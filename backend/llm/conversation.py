"""Streaming conversation engine with document tool-use.

One synchronous generator per user turn: yields UI-ready event dicts that
the FastAPI layer serializes as Server-Sent Events. History and the
document store live on a :class:`SessionState` owned by the caller
(``backend.sessions``).

Context architecture (the "Sonnet unleashed" restructure, 2026-07-21)
---------------------------------------------------------------------
The system prompt is ONLY the stable module-rendered block, carrying
``cache_control`` — byte-identical across the whole session. Everything
session-varying (standards editions in effect, the research profile, the
FULL document text, the lint report, open items) rides a PROJECT CONTEXT
block spliced into the newest user message instead. That ordering is what
makes the conversation history a stable, cacheable prefix: a second cache
breakpoint rides the tail of the request's messages, so the growing
interview hits the prompt cache incrementally instead of re-billing every
token every turn. At commit the spliced context (and the turn's thinking
blocks, plus any fetched-PDF payloads) are stripped from the stored
history — each request carries exactly one, current, state block.

The model sees the ENTIRE document every turn — full paragraph text, ids,
statuses, provenance chips — never a truncated outline. Tool results still
carry the compact outline as an id map for mid-turn orientation.

Tool loop
---------
``apply_spec_edits`` plus the ``web_search``/``web_fetch`` server tools
(static config — byte-stable so the cached prefix never busts). A turn is
a continuation loop: stream a response; on ``tool_use``, apply the edits
transactionally (an invalid batch becomes an ``is_error`` tool result the
model can correct), emit a ``doc_patch`` event, send the tool results
back, and stream again; on ``pause_turn`` (long server-tool work), re-send
the assistant content per the pause contract and continue. Adaptive
thinking is stated explicitly (Sonnet 5 runs it by default) with the
effort level from settings; thinking blocks are preserved verbatim across
continuation rounds — the API requires them during tool use — and dropped
only at commit.

Turn atomicity is unchanged from Phase 2: history mutates and the document
turn commits (one undo snapshot per changed turn) only after a fully
successful turn. Every failure path yields one ``error`` event, rolls the
document back to its pre-turn state, and leaves history unchanged, so a
resend never duplicates anything.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import anthropic

from .. import settings
from ..spec_doc import (
    APPLY_SPEC_EDITS_TOOL,
    DocumentStore,
    SpecEditError,
    lint_document,
    open_questions,
    outline,
)
from ..compliance import AuditRunner
from ..research import ResearchRunner, research_context_block
from ..research.resend_sanitizer import (
    elide_all_pdf_sources,
    sanitize_messages_for_resend,
)
from ..research.schema import build_web_fetch_tool, build_web_search_tool
from ..tracing import capture as _trace
from ..spec_modules import SpecModule, get_module
from ..standards import standards_context_block
from ..usage_ledger import UsageLedger
from .client import MissingApiKeyError, get_client
from .prompts import render_system_prompt

# Ceiling on continuation rounds (tool dispatches + pause_turn resumes)
# within one user turn. This is a runaway circuit breaker, not a quality
# limit: each round can carry an arbitrarily large edit batch and a fresh
# web-tool allowance, so no legitimate turn gets anywhere near it — the
# failure mode it guards is a model resubmitting the same broken batch
# forever. Hitting it is treated as a failed (retry-safe) turn.
MAX_TOOL_ROUNDS = 50


def _chat_tools() -> list[dict[str, Any]]:
    """The interview tool list: document edits + live web lookups.

    Static configuration on purpose — tools precede the system prompt in
    the cached prefix, so anything per-turn here (e.g. a profile-derived
    ``user_location``) would bust the prompt cache for the whole session.
    The model steers search locale through its query text instead.
    """
    return [
        APPLY_SPEC_EDITS_TOOL,
        build_web_search_tool(max_uses=settings.CHAT_MAX_SEARCHES),
        build_web_fetch_tool(max_uses=settings.CHAT_MAX_FETCHES),
    ]


@dataclass
class SessionState:
    """One conversation's accumulated state: history + document + module.

    ``generation`` increments whenever the session is replaced out from
    under a possibly-streaming turn (reset, project load); an in-flight
    turn compares it before touching the store or committing, so a zombie
    turn can never pollute the fresh session. ``module`` is the active
    :class:`SpecModule` — reset keeps it (a fresh session in the same
    discipline), project load resolves it from the file's ``module_id``.
    """

    history: list[dict[str, Any]] = field(default_factory=list)
    doc: DocumentStore = field(default_factory=DocumentStore)
    generation: int = 0
    module: SpecModule = field(default_factory=lambda: get_module(None))
    research: ResearchRunner = field(default_factory=ResearchRunner)
    audit: AuditRunner = field(default_factory=AuditRunner)
    # Session-scoped billed-usage meter (WI4). Reset/load clear it.
    usage: UsageLedger = field(default_factory=UsageLedger)
    # True while a model turn owns the document store (WI2). Manual edits are
    # rejected in this window — a mid-turn manual edit would be swept into the
    # streaming turn's commit or rollback.
    turn_active: bool = False

    def reset(self) -> None:
        self.history.clear()
        self.doc.reset()
        # Fresh runners: work still running against the old session
        # finishes into the abandoned objects (the zombie-turn pattern).
        self.research = ResearchRunner()
        self.audit = AuditRunner()
        # The meter answers "what has THIS session spent" — a fresh session
        # starts at zero (the trace remains the permanent record).
        self.usage.reset()
        self.generation += 1


class _SessionInvalidated(RuntimeError):
    """The session was reset/replaced while this turn was still streaming."""


def _stable_system_blocks(session: SessionState) -> list[dict[str, Any]]:
    """The system prompt: ONLY the stable module block, cached.

    Nothing session-varying may render here (pinned by
    ``test_stable_system_prompt_is_cached_and_module_rendered``); the live
    state travels in the PROJECT CONTEXT block of the newest user message
    (:func:`_turn_context_text`), after the cacheable history prefix.
    """
    return [
        {
            "type": "text",
            "text": render_system_prompt(session.module),
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _turn_context_text(session: SessionState) -> str:
    """The PROJECT CONTEXT block: everything live, rendered at turn start.

    Standards editions in effect, the research profile (when one exists),
    the FULL document text with ids/statuses/provenance, the lint report,
    and the open-item list. Spliced ahead of the user's text in the newest
    user message and stripped again at commit — each request carries
    exactly one, current, state block, never a stale one.
    """
    doc = session.doc.doc
    parts = [
        standards_context_block(session.module.basis, doc.edition_overrides)
    ]
    research_profile = getattr(session.research, "profile_result", None)
    if research_profile is not None:
        block, _dropped = research_context_block(research_profile)
        parts.append(block)
    parts.append(
        "Current specification document (full text; element ids in "
        "[id: …], provenance chips as ◆item-id):\n"
        + outline(doc, max_text=None)
    )
    lint_items = lint_document(doc, session.module)
    if lint_items:
        lines = [
            "LINT REPORT (deterministic, advisory — stale-edition findings "
            "are drafting errors to fix):"
        ]
        for issue in lint_items:
            where = issue.get("ref") or issue.get("element_id") or ""
            lines.append(
                f"- [{issue.get('rule')}] {where}: {issue.get('message')} "
                f"(element {issue.get('element_id')})"
            )
        parts.append("\n".join(lines))
    open_items = open_questions(doc)
    if open_items:
        lines = ["OPEN ITEMS (resolve as answers arrive):"]
        for item in open_items:
            lines.append(
                f"- {item.get('ref')} [{item.get('kind')}] "
                f"{item.get('label')} (element {item.get('element_id')})"
            )
        parts.append("\n".join(lines))
    return (
        "=== PROJECT CONTEXT (current state — supersedes anything "
        "remembered from earlier turns) ===\n\n"
        + "\n\n".join(parts)
        + "\n\n=== END PROJECT CONTEXT ==="
    )


def _serialize(node: Any) -> Any:
    """Deep-serialize SDK content into plain JSON-able structures.

    Preserves EVERY block type verbatim — text (with citations), tool_use,
    thinking/redacted_thinking (empty ``thinking`` fields included, per the
    adaptive-thinking contract), server_tool_use, and the web tool result
    blocks — so continuation rounds can re-send exactly what the API
    returned. Pydantic models dump via ``model_dump``; test fakes
    (SimpleNamespace) fall back to ``vars()``.
    """
    if isinstance(node, dict):
        return {k: _serialize(v) for k, v in node.items()}
    if isinstance(node, (list, tuple)):
        return [_serialize(v) for v in node]
    dump = getattr(node, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json", exclude_none=True)
        except Exception:  # noqa: BLE001 — fall through to attribute dump
            pass
    if hasattr(node, "__dict__") and not isinstance(node, type):
        return {k: _serialize(v) for k, v in vars(node).items()}
    return node


def _content_blocks_to_dicts(content: Any) -> list[dict[str, Any]]:
    """Serialize SDK content blocks into plain history dicts, verbatim."""
    blocks: list[dict[str, Any]] = []
    for block in content or []:
        serialized = _serialize(block)
        if isinstance(serialized, dict) and serialized.get("type"):
            blocks.append(serialized)
    return blocks


_TRANSIENT_BLOCK_TYPES = frozenset({"thinking", "redacted_thinking"})


def _committed_messages(
    new_messages: list[dict[str, Any]], user_text: str
) -> list[dict[str, Any]]:
    """The turn's messages as history stores them: lean and current-free.

    - The first user message keeps ONLY the user's text (the PROJECT
      CONTEXT block would otherwise fossilize a stale document snapshot
      into every later request).
    - Thinking blocks drop — the adaptive-thinking contract only requires
      them within the turn that produced them.
    - Fetched-PDF payloads are elided wholesale (see
      :func:`elide_all_pdf_sources`); search results and citations stay.
    """
    committed: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": user_text}]}
    ]
    for message in new_messages[1:]:
        if message.get("role") != "assistant":
            committed.append(message)
            continue
        content = [
            b
            for b in (message.get("content") or [])
            if b.get("type") not in _TRANSIENT_BLOCK_TYPES
        ]
        if not content:
            content = [{"type": "text", "text": "[Model reasoning omitted.]"}]
        committed.append({"role": "assistant", "content": content})
    return elide_all_pdf_sources(committed)


def _with_tail_cache_breakpoint(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Copy-on-write a cache breakpoint onto the request's last block.

    Marks the incremental prefix (tools + system are covered by the stable
    block's own breakpoint): each continuation round extends the previous
    round's cache; each new turn re-writes only the previous turn's
    exchange (the strip at commit shifts those bytes). Stored history is
    never mutated — the breakpoint rides a per-request copy.
    """
    if not messages:
        return messages
    last = messages[-1]
    content = last.get("content")
    if not isinstance(content, list) or not content:
        return messages
    tail = content[-1]
    if not isinstance(tail, dict) or tail.get("type") in _TRANSIENT_BLOCK_TYPES:
        return messages
    new_tail = dict(tail)
    new_tail["cache_control"] = {"type": "ephemeral"}
    return [
        *messages[:-1],
        {**last, "content": [*content[:-1], new_tail]},
    ]


def _merge_usage(totals: dict[str, int], usage: Any) -> None:
    """Accumulate one response's billed usage into the turn totals."""
    if usage is None:
        return
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    ):
        value = getattr(usage, key, None)
        if isinstance(value, (int, float)) and value:
            totals[key] = totals.get(key, 0) + int(value)
    details = getattr(usage, "output_tokens_details", None)
    thinking = getattr(details, "thinking_tokens", None) if details else None
    if isinstance(thinking, (int, float)) and thinking:
        totals["thinking_tokens"] = totals.get("thinking_tokens", 0) + int(
            thinking
        )
    server = getattr(usage, "server_tool_use", None)
    for key in ("web_search_requests", "web_fetch_requests"):
        value = getattr(server, key, None) if server else None
        if isinstance(value, (int, float)) and value:
            totals[key] = totals.get(key, 0) + int(value)


# --- Streaming-event translation (WI1: buttery-smooth streaming UX) ----------
#
# The interview streams raw SDK events instead of the text-only
# ``text_stream``, so the UI sees everything the model is doing the moment it
# happens: adaptive-thinking summaries, drafting progress on a long edit
# batch, and web lookups the instant they fire — never a silent pause, never
# a post-hoc "🔍 Searched…" chip that lands after the search is over. ``status``
# frames are transient UI hints (not persisted to history/traces/project
# files); ``text_delta``/``thinking_delta`` clear the strip.

# Runaway guard on drafting-progress frames: at most one per this interval,
# so a 40-op batch streams a handful of "drafting… 2.4k" pulses, not a flood.
_DRAFT_PROGRESS_INTERVAL_S = 0.25


def _safe_json(text: str) -> dict[str, Any]:
    """Parse an accumulated tool-input JSON fragment; ``{}`` on garbage."""
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _stream_events(stream: Any) -> Iterator[dict[str, Any]]:
    """Translate one round's raw stream events into UI event dicts.

    Yields ``status`` hints on block starts (thinking/writing/drafting/
    searching/fetching), ``text_delta``/``thinking_delta`` on content deltas,
    throttled drafting ``progress_chars`` as an edit batch's JSON streams,
    and LIVE ``web_search``/``web_fetch`` events the instant a server-tool
    block's input finishes (``content_block_stop``) — not derived after the
    round from the final message. Empty thinking deltas (``display:
    omitted``) are dropped so they don't prematurely clear the status strip.
    """
    json_buffers: dict[int, str] = {}
    block_kinds: dict[int, tuple[str, str]] = {}
    last_progress = time.monotonic()
    for event in stream:
        etype = getattr(event, "type", None)
        if etype == "content_block_start":
            block = getattr(event, "content_block", None)
            index = getattr(event, "index", 0)
            btype = getattr(block, "type", None) or ""
            bname = getattr(block, "name", "") or ""
            block_kinds[index] = (btype, bname)
            json_buffers[index] = ""
            if btype == "thinking":
                yield {"type": "status", "kind": "thinking"}
            elif btype == "text":
                yield {"type": "status", "kind": "writing"}
            elif btype == "tool_use" and bname == "apply_spec_edits":
                yield {"type": "status", "kind": "drafting", "progress_chars": 0}
            elif btype == "server_tool_use" and bname == "web_search":
                yield {"type": "status", "kind": "searching"}
            elif btype == "server_tool_use" and bname == "web_fetch":
                yield {"type": "status", "kind": "fetching"}
        elif etype == "content_block_delta":
            delta = getattr(event, "delta", None)
            dtype = getattr(delta, "type", None)
            index = getattr(event, "index", 0)
            if dtype == "text_delta":
                text = getattr(delta, "text", "") or ""
                if text:
                    yield {"type": "text_delta", "text": text}
            elif dtype == "thinking_delta":
                text = getattr(delta, "thinking", "") or ""
                if text:
                    yield {"type": "thinking_delta", "text": text}
            elif dtype == "input_json_delta":
                json_buffers[index] = json_buffers.get(index, "") + (
                    getattr(delta, "partial_json", "") or ""
                )
                if block_kinds.get(index) == ("tool_use", "apply_spec_edits"):
                    now = time.monotonic()
                    if now - last_progress >= _DRAFT_PROGRESS_INTERVAL_S:
                        last_progress = now
                        yield {
                            "type": "status",
                            "kind": "drafting",
                            "progress_chars": len(json_buffers[index]),
                        }
        elif etype == "content_block_stop":
            index = getattr(event, "index", 0)
            btype, bname = block_kinds.get(index, ("", ""))
            if btype != "server_tool_use":
                continue
            payload = _safe_json(json_buffers.get(index, ""))
            if bname == "web_search":
                yield {"type": "web_search", "query": str(payload.get("query", ""))}
            elif bname == "web_fetch":
                yield {"type": "web_fetch", "url": str(payload.get("url", ""))}


# thinking.display capability probe. Sonnet 5 accepts ``summarized``; a model
# or endpoint that rejects the ``display`` key 400s once, after which the
# whole process degrades to ``omitted`` (remembered, never re-probed). Reset
# between hermetic tests via :func:`reset_thinking_display_probe`.
_display_probe_disabled = False


def reset_thinking_display_probe() -> None:
    """Re-arm the thinking.display probe (tests; a fresh process)."""
    global _display_probe_disabled
    _display_probe_disabled = False


def _thinking_param() -> dict[str, Any]:
    """The adaptive-thinking request param, with display when supported."""
    thinking: dict[str, Any] = {"type": "adaptive"}
    if not _display_probe_disabled and settings.THINKING_DISPLAY == "summarized":
        thinking["display"] = "summarized"
    return thinking


def _enter_stream(
    client: Any, kwargs: dict[str, Any], trace_handle: Any = None
) -> tuple[Any, Any]:
    """Open + enter a message stream, degrading thinking.display once on 400.

    The request fires when the stream context is entered, so a rejected
    ``display`` key surfaces here; we retry the same round without it,
    remember the degrade for the process, and note it in the trace.
    """
    global _display_probe_disabled
    try:
        manager = client.messages.stream(**kwargs)
        return manager, manager.__enter__()
    except anthropic.BadRequestError:
        thinking = kwargs.get("thinking") or {}
        if _display_probe_disabled or "display" not in thinking:
            raise
        _display_probe_disabled = True
        _trace.note(
            trace_handle,
            "thinking.display rejected; degraded to omitted for this session",
        )
        kwargs = {**kwargs, "thinking": {"type": "adaptive"}}
        manager = client.messages.stream(**kwargs)
        return manager, manager.__enter__()


def _run_tool(
    session: SessionState, block: dict[str, Any], trace_handle: Any = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Execute one (serialized) tool_use block.

    Returns ``(tool_result_block, ui_events)``. Tool failures become
    ``is_error`` results for the model to correct — they never abort the
    turn.
    """
    if block.get("name") != "apply_spec_edits":
        return (
            {
                "type": "tool_result",
                "tool_use_id": block.get("id"),
                "content": f"Unknown tool: {block.get('name')}",
                "is_error": True,
            },
            [],
        )
    edits = (block.get("input") or {}).get("edits")
    try:
        applied = session.doc.apply_edits(edits)
    except SpecEditError as exc:
        _trace.tool_dispatch(
            trace_handle,
            ops=len(edits) if isinstance(edits, list) else 0,
            ok=False,
            error=str(exc),
        )
        return (
            {
                "type": "tool_result",
                "tool_use_id": block.get("id"),
                "content": (
                    f"Edit batch rejected (nothing was applied): {exc}\n\n"
                    "Current specification document:\n"
                    + outline(session.doc.doc)
                ),
                "is_error": True,
            },
            [],
        )
    _trace.tool_dispatch(trace_handle, ops=len(applied), ok=True)
    result = {
        "type": "tool_result",
        "tool_use_id": block.get("id"),
        "content": json.dumps(
            {"applied": applied, "outline": outline(session.doc.doc)},
            ensure_ascii=False,
        ),
    }
    patch = {
        "type": "doc_patch",
        "ops": applied,
        "doc": session.doc.snapshot(),
    }
    return result, [patch]


def stream_user_turn(
    session: SessionState,
    user_text: str,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Run one user turn against the model, yielding UI event dicts.

    Event order: transient ``status`` hints (working/thinking/writing/
    drafting/searching/fetching) and ``thinking_delta`` summaries interleave
    with ``text_delta`` chunks across every continuation round; live
    ``web_search``/``web_fetch`` events fire the instant a server-tool call
    completes; ``doc_patch`` follows each applied edit batch. Then — on
    success — ``open_questions`` and ``lint`` (if the document changed) and
    ``turn_complete``, which carries the turn's aggregated billed usage.
    ``status`` frames are transient UI hints, never persisted. Any failure
    yields a single ``error`` event, rolls the document back, and leaves
    history unchanged.
    """
    user_text = (user_text or "").strip()
    if not user_text:
        yield {"type": "error", "message": "Empty message."}
        return

    # The PROJECT CONTEXT renders once, at turn start: mid-turn document
    # changes reach the model through tool results, and a frozen block
    # keeps the request prefix byte-stable across continuation rounds.
    context_text = _turn_context_text(session)
    new_messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": context_text},
                {"type": "text", "text": user_text},
            ],
        }
    ]
    session.doc.begin_turn()
    session.turn_active = True
    generation = session.generation
    trace_handle = _trace.turn_start(
        model=model or settings.INTERVIEW_MODEL,
        history_len=len(session.history),
    )

    def check_session() -> None:
        if session.generation != generation:
            raise _SessionInvalidated(
                "The session was reset while this turn was streaming; "
                "the turn was discarded."
            )

    def request_kwargs() -> dict[str, Any]:
        messages = sanitize_messages_for_resend(
            list(session.history) + new_messages
        )
        return {
            "model": model or settings.INTERVIEW_MODEL,
            "max_tokens": max_tokens or settings.INTERVIEW_MAX_TOKENS,
            "system": _stable_system_blocks(session),
            "messages": _with_tail_cache_breakpoint(messages),
            "tools": _chat_tools(),
            "thinking": _thinking_param(),
            "output_config": {"effort": settings.INTERVIEW_EFFORT},
        }

    stop_reason: str | None = None
    doc_changed = False
    committed = False
    usage_totals: dict[str, int] = {}
    try:
        client = get_client()
        resumed_from_pause = False
        for _round in range(MAX_TOOL_ROUNDS):
            check_session()
            # Never dead air between rounds: from send to first token there is
            # always a live status. A pause_turn resume keeps server work
            # visible as "searching" rather than a generic "working".
            yield {
                "type": "status",
                "kind": "searching" if resumed_from_pause else "working",
                "round": _round,
            }
            resumed_from_pause = False
            manager, stream = _enter_stream(
                client, request_kwargs(), trace_handle
            )
            try:
                yield from _stream_events(stream)
                final = stream.get_final_message()
            finally:
                manager.__exit__(None, None, None)

            _merge_usage(usage_totals, getattr(final, "usage", None))
            content = _content_blocks_to_dicts(final.content)
            stop_reason = final.stop_reason

            if stop_reason == "pause_turn":
                # Long server-tool work paused server-side: re-send the
                # assistant content verbatim (per the pause_turn contract,
                # thinking blocks included) and stream again — no
                # synthetic user turn, no tool results.
                new_messages.append({"role": "assistant", "content": content})
                resumed_from_pause = True
                continue
            if stop_reason != "tool_use":
                # A truncated response (e.g. max_tokens) can still carry
                # tool_use blocks; committing one without a tool_result
                # would make every later request invalid. Keep the text.
                content = [b for b in content if b["type"] != "tool_use"] or [
                    {"type": "text", "text": "[Response was cut off before completion.]"}
                ]
                new_messages.append({"role": "assistant", "content": content})
                break
            new_messages.append({"role": "assistant", "content": content})

            tool_results: list[dict[str, Any]] = []
            for block in content:
                if block.get("type") != "tool_use":
                    continue
                check_session()
                result, ui_events = _run_tool(session, block, trace_handle)
                tool_results.append(result)
                for event in ui_events:
                    yield event
            new_messages.append({"role": "user", "content": tool_results})
        else:
            raise RuntimeError(
                f"Turn exceeded {MAX_TOOL_ROUNDS} tool rounds; aborted."
            )
    except MissingApiKeyError as exc:
        yield {"type": "error", "message": str(exc)}
        return
    except _SessionInvalidated as exc:
        # The fresh/loaded session must stay exactly as the user made it —
        # nothing was applied after the generation change.
        yield {"type": "error", "message": str(exc)}
        return
    except anthropic.APIStatusError as exc:
        yield {
            "type": "error",
            "message": f"Anthropic API error ({exc.status_code}): {exc.message}",
        }
        return
    except anthropic.APIConnectionError:
        yield {
            "type": "error",
            "message": "Could not reach the Anthropic API. Check your connection and try again.",
        }
        return
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI, never raised
        yield {"type": "error", "message": f"Unexpected error: {exc}"}
        return
    else:
        if session.generation != generation:
            # Reset/load won the race after the last round: leave the
            # fresh session untouched and discard this turn.
            yield {
                "type": "error",
                "message": "The session was reset while this turn was "
                "streaming; the turn was discarded.",
            }
            return
        session.history.extend(_committed_messages(new_messages, user_text))
        doc_changed = session.doc.commit_turn()
        committed = True
    finally:
        # Runs on every exit — including GeneratorExit when the SSE client
        # disconnects mid-stream, which no except clause above can see.
        # Anything short of a committed turn rolls the document back.
        session.turn_active = False
        # The spend is real even on a failed turn — record it (unless a
        # reset/load raced in, whose fresh ledger must not inherit it).
        if session.generation == generation:
            session.usage.add("interview", usage_totals, count_turn=True)
        if not committed:
            session.doc.rollback_turn()
            _trace.turn_end(
                trace_handle,
                stop_reason=stop_reason,
                doc_changed=False,
                usage=usage_totals,
                error="turn did not commit (failure or disconnect)",
            )
        else:
            _trace.turn_end(
                trace_handle,
                stop_reason=stop_reason,
                doc_changed=doc_changed,
                usage=usage_totals,
            )

    if doc_changed:
        # doc_patch snapshots stream mid-turn, before the version commit;
        # this snapshot carries the committed version pointer.
        yield {"type": "doc_snapshot", "doc": session.doc.snapshot()}
        yield {
            "type": "open_questions",
            "items": open_questions(session.doc.doc),
        }
        yield {
            "type": "lint",
            "items": lint_document(session.doc.doc, session.module),
            "standards": standards_payload(session),
        }
    yield {
        "type": "turn_complete",
        "stop_reason": stop_reason,
        "usage": usage_totals,
    }


def standards_payload(session: SessionState) -> list[dict[str, Any]]:
    """UI-shaped list of the editions in effect (pins + overrides)."""
    from ..standards import effective_editions

    return [
        {
            "name": eff.name,
            "edition": eff.edition,
            "title": eff.title,
            "is_override": eff.is_override,
            "basis": eff.basis,
        }
        for eff in effective_editions(
            session.module.basis, session.doc.doc.edition_overrides
        )
    ]
