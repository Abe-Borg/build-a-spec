"""Scripted fake Anthropic streaming client (hermetic tests).

Grown from the Phase 1 fake (which spoke only text) to script whole
multi-round turns: each entry is a "turn" the next ``stream()`` call
replays — text chunks, final content blocks (text and/or tool_use), and a
stop reason. An entry that is an Exception instance is raised instead,
for failure-path tests. Mirrors the fake-client convention of Spec
Critic's suite.
"""
from __future__ import annotations

import itertools
import json
import re
from types import SimpleNamespace
from typing import Any


def audit_grade_qc_result(session: Any, findings: list[Any]):
    """Build a current, complete QC result for non-QC endpoint tests.

    A few concurrency/source-preservation tests need a trusted QC result only
    to reach the later guard they actually exercise.  Keep those fixtures on
    the same v3 identity, lens, verifier, and pricing contract as production
    instead of weakening the endpoint's audit-completeness gate.
    """
    import uuid

    from backend import settings
    from backend.app import _qc_source_guard
    from backend.qc.engine import (
        CONSOLIDATION_OPS_ORIGINAL,
        CONSOLIDATION_STATUS_SKIPPED,
        QC_PROTOCOL_VERSION,
        QC_REPORT_SCHEMA_VERSION,
        VERIFICATION_RULE_V4,
        QCCandidateOrigin,
        QCConsolidation,
        QCConsolidationGroup,
        QCLensStatus,
        QCResult,
        QCReviewedCheck,
        QCVerdict,
        _mint_origin_id,
        build_qc_input_manifest,
        panel_outcome,
        qc_input_fingerprint,
        qc_version_fingerprint,
    )
    from backend.qc.schema import QC_LENSES
    from backend.usage_ledger import usage_pricing_snapshot

    profile = session.research.profile_result
    # ``block=True`` mirrors what ``POST /api/qc/start`` does: a real run
    # settles the background permission sweep before it records its input
    # manifest, so a fixture built from a still-``pending`` capability
    # summary would look stale to every later freshness check.
    source_guard = _qc_source_guard(session, block=True)
    manifest = build_qc_input_manifest(
        session.doc.doc,
        profile,
        session.module,
        version_index=session.doc.index,
        discipline=session.discipline,
        source_guard=source_guard,
        model=settings.QC_MODEL,
        max_tokens=settings.QC_MAX_TOKENS,
        # The live regime, matching what `matches_inputs` will rebuild — a
        # fixture pinned to the other one would read as stale to every
        # freshness check and never reach the guard it exists to exercise.
        consolidation_enabled=settings.QC_CONSOLIDATION,
    )
    lens_ids = {lens.lens_id for lens in QC_LENSES}
    default_lens_id = "coordination_consistency"
    for finding in findings:
        if finding.lens_id not in lens_ids:
            finding.lens_id = default_lens_id
        original_severity = finding.original_severity or finding.severity
        finding.original_severity = original_severity
        panel_size = (
            settings.QC_VERIFIERS_CRITICAL
            if original_severity in {"critical", "high"}
            else settings.QC_VERIFIERS_STANDARD
        )
        panel_size = max(1, panel_size)
        finding.verification_panel_size = panel_size
        # Current-schema fixture: v4 upholds only on a unanimous panel, and
        # carries the rule identity rather than an integer bar. Derived
        # through the engine's own helper below so a future rule change
        # breaks this fixture loudly instead of silently minting a record
        # the reload check would reject.
        finding.verification_threshold = panel_size
        finding.verification_rule = VERIFICATION_RULE_V4
        finding.dispute_reason = ""
        has_ops = bool(finding.proposed_ops)
        finding.ops_semantic_status = "approved" if has_ops else "not_proposed"
        finding.ops_semantic_reason = (
            f"All {panel_size} verifier seat(s) approved the operations."
            if has_ops
            else "No proposed operations were supplied."
        )
        if not has_ops:
            finding.ops_valid = False
        finding.verdicts = [
            QCVerdict(
                upholds=True,
                note="Audit-grade endpoint test fixture upheld the finding.",
                ops_adequate=has_ops,
                ops_note=(
                    "The complete operation set safely fixes the finding."
                    if has_ops
                    else "No operations were proposed."
                ),
                reviewer_index=index,
            )
            for index in range(1, panel_size + 1)
        ]
        finding.verification_outcome, finding.dispute_reason = panel_outcome(
            original_severity, finding.verdicts, expected_seats=panel_size
        )

    # Every fixture candidate stands for exactly its own lens claim — the
    # identity partition. Built through the engine's own id helper so the
    # record satisfies the same reload partition check production writes.
    origins = [
        QCCandidateOrigin(
            origin_id=_mint_origin_id(
                finding.lens_id,
                {
                    "element_id": finding.element_id,
                    "title": finding.title,
                    "issue": finding.issue,
                    "rationale": finding.rationale,
                    "severity": finding.original_severity,
                    "source_urls": finding.source_urls,
                    "proposed_ops": finding.proposed_ops,
                },
            ),
            candidate_index=index,
            candidate_id=f"raw-{index + 1}",
            lens_id=finding.lens_id,
            severity=finding.original_severity,
            element_id=finding.element_id,
            title=finding.title,
            issue=finding.issue,
            rationale=finding.rationale,
            source_urls=list(finding.source_urls),
            accepted_sources=list(finding.accepted_sources),
            grounded=finding.grounded,
            proposed_ops=[dict(op) for op in finding.proposed_ops],
        )
        for index, finding in enumerate(findings)
    ]
    for finding, origin in zip(findings, origins):
        finding.candidate_origins = [origin.origin_id]
        finding.ops_source = CONSOLIDATION_OPS_ORIGINAL
    consolidation = QCConsolidation(
        status=CONSOLIDATION_STATUS_SKIPPED,
        fallback_reason="Test fixture: candidates were not grouped.",
        origins=origins,
        groups=[
            QCConsolidationGroup(
                group_index=index,
                candidate_id=f"candidate-{index + 1}",
                origin_ids=[origin.origin_id],
                element_id=origin.element_id,
                severity=origin.severity,
                ops_source=CONSOLIDATION_OPS_ORIGINAL,
                proposed_ops=[dict(op) for op in origin.proposed_ops],
            )
            for index, origin in enumerate(origins)
        ],
    )

    lens_statuses = []
    for lens in QC_LENSES:
        lens_findings = [f for f in findings if f.lens_id == lens.lens_id]
        lens_statuses.append(
            QCLensStatus(
                lens_id=lens.lens_id,
                title=lens.title,
                brief=lens.brief,
                status="completed",
                finding_count=len(lens_findings),
                grounded_count=sum(1 for f in lens_findings if f.grounded),
                reviewed_checks=[
                    QCReviewedCheck(
                        check=f"Test coverage for {lens.title}",
                        outcome="finding" if lens_findings else "passed",
                        element_ids=[
                            f.element_id for f in lens_findings if f.element_id
                        ],
                    )
                ],
            )
        )

    return QCResult(
        schema_version=QC_REPORT_SCHEMA_VERSION,
        protocol_version=QC_PROTOCOL_VERSION,
        run_id=f"qc-test-{uuid.uuid4().hex}",
        execution_status="complete",
        summary="Audit-grade endpoint test fixture.",
        findings=findings,
        lens_statuses=lens_statuses,
        consolidation=consolidation if settings.QC_CONSOLIDATION else None,
        started_at="2026-07-24T10:00:00+00:00",
        finished_at="2026-07-24T10:00:01+00:00",
        version_index=session.doc.index,
        version_fingerprint=qc_version_fingerprint(session.doc.doc),
        input_fingerprint=qc_input_fingerprint(manifest),
        input_manifest=manifest,
        model=settings.QC_MODEL,
        effort=settings.QC_EFFORT,
        max_tokens=settings.QC_MAX_TOKENS,
        cost_basis=usage_pricing_snapshot(settings.QC_MODEL),
        research_profile_present=profile is not None,
    )


_SRVTOOLU_COUNTER = itertools.count(1)


def text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def tool_use_block(
    tool_id: str, name: str, tool_input: dict[str, Any]
) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=tool_input)


def token_usage(
    *,
    input: int = 0,
    output: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
    cache_write_1h: int | None = None,
    thinking: int = 0,
    searches: int = 0,
    fetches: int = 0,
) -> SimpleNamespace:
    """A billed-usage object shaped for the ledger (WI4 cost meter).

    ``cache_write`` is the provider's TOTAL cache creation across TTL
    classes, exactly as the API reports it. ``cache_write_1h`` is the
    one-hour subtotal WITHIN that total; passing it adds the nested
    ``cache_creation`` object the SDK sends. Omitting it leaves the
    attribute absent entirely, so every pre-existing fixture keeps
    exercising the no-nested-object path.
    """
    usage = SimpleNamespace(
        input_tokens=input,
        output_tokens=output,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
        output_tokens_details=SimpleNamespace(thinking_tokens=thinking),
        server_tool_use=SimpleNamespace(
            web_search_requests=searches, web_fetch_requests=fetches
        ),
    )
    if cache_write_1h is not None:
        usage.cache_creation = SimpleNamespace(
            ephemeral_5m_input_tokens=max(0, cache_write - cache_write_1h),
            ephemeral_1h_input_tokens=cache_write_1h,
        )
    return usage


def text_turn(
    chunks: list[str],
    stop_reason: str = "end_turn",
    *,
    usage: SimpleNamespace | None = None,
) -> SimpleNamespace:
    """A response that streams ``chunks`` and ends the turn."""
    return SimpleNamespace(
        chunks=list(chunks),
        content=[text_block("".join(chunks))],
        stop_reason=stop_reason,
        usage=usage,
    )


def tool_turn(
    chunks: list[str],
    tool_input: dict[str, Any],
    *,
    tool_id: str = "toolu_fake_1",
    name: str = "apply_spec_edits",
    stop_reason: str = "tool_use",
    usage: SimpleNamespace | None = None,
) -> SimpleNamespace:
    """A response that streams ``chunks`` then requests a tool call.

    ``stop_reason`` other than ``tool_use`` (e.g. ``max_tokens``) simulates
    a response truncated mid-tool-call.
    """
    content: list[SimpleNamespace] = []
    text = "".join(chunks)
    if text:
        content.append(text_block(text))
    content.append(tool_use_block(tool_id, name, tool_input))
    return SimpleNamespace(
        chunks=list(chunks), content=content, stop_reason=stop_reason, usage=usage
    )


def thinking_block(thinking: str = "", signature: str = "sig-fake") -> SimpleNamespace:
    """An adaptive-thinking block (Sonnet 5 display "omitted" → empty text)."""
    return SimpleNamespace(type="thinking", thinking=thinking, signature=signature)


def chat_search_blocks(
    query: str, urls: list[str], *, tool_use_id: str = ""
) -> list[SimpleNamespace]:
    """A ``server_tool_use``(web_search) + result pair for the chat loop."""
    use_id = tool_use_id or _srvtoolu_id("chat")
    return [
        SimpleNamespace(
            type="server_tool_use",
            id=use_id,
            name="web_search",
            input={"query": query},
        ),
        search_result_block(urls, tool_use_id=use_id),
    ]


# ---------------------------------------------------------------------------
# Raw stream events (WI1: the chat loop now iterates SDK events, not
# text_stream). Builders mirror the anthropic SDK's raw-event shapes the
# engine consumes; ``_synthesize_events`` derives a plausible default
# sequence from a scripted turn's content so existing tool/text/thinking
# turns stream correctly with no per-test wiring.
# ---------------------------------------------------------------------------


def block_start_event(
    index: int,
    block_type: str,
    name: str = "",
    *,
    input: dict | None = None,
) -> SimpleNamespace:
    """A ``content_block_start`` frame.

    ``input`` is the code-execution caller's shape: that caller hands the
    whole tool input over on the start frame and streams no
    ``input_json_delta`` after it. Omitted — the direct-caller default both
    web tools pin — the started block carries no ``input`` attribute at
    all, exactly as the wire does, and the deltas are what carry the
    payload.
    """
    block = SimpleNamespace(type=block_type, name=name)
    if input is not None:
        block.input = input
    return SimpleNamespace(
        type="content_block_start", index=index, content_block=block
    )


def text_delta_event(index: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="content_block_delta",
        index=index,
        delta=SimpleNamespace(type="text_delta", text=text),
    )


def thinking_delta_event(index: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="content_block_delta",
        index=index,
        delta=SimpleNamespace(type="thinking_delta", thinking=text),
    )


def input_json_delta_event(index: int, partial: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="content_block_delta",
        index=index,
        delta=SimpleNamespace(type="input_json_delta", partial_json=partial),
    )


def block_stop_event(index: int) -> SimpleNamespace:
    return SimpleNamespace(type="content_block_stop", index=index)


def code_execution_tool_events(
    index: int, name: str, tool_input: dict
) -> list[SimpleNamespace]:
    """A server-tool block in the CODE-EXECUTION caller's streaming shape.

    Start frame carrying the complete input, then straight to stop — zero
    ``input_json_delta`` frames in between. That is what
    ``allowed_callers: ["code_execution_20260120"]`` (the ``_20260209``
    tools' provider default, which this app deliberately does not use)
    produces, and it is the shape that used to leave the chat chip, the
    research agent board, and the QC Review Room with no query or URL to
    show. ``_synthesize_events`` cannot produce it — it always emits a
    delta — so a fixture wanting this shape must build it here and pass it
    through ``events=``.

    The direct-caller counterpart is ``block_start_event`` (no ``input``)
    plus ``input_json_delta_event`` plus ``block_stop_event``, which is what
    ``_synthesize_events`` already builds for every scripted turn.
    """
    return [
        block_start_event(index, "server_tool_use", name, input=dict(tool_input)),
        block_stop_event(index),
    ]


_STREAMED_BLOCK_TYPES = ("text", "thinking", "tool_use", "server_tool_use")


def _synthesize_events(
    content: list[SimpleNamespace], chunks: list[str]
) -> list[SimpleNamespace]:
    """Build a plausible raw-event sequence for a scripted turn's content.

    Each streamable block gets start → delta(s) → stop; the turn's text
    ``chunks`` stream as the first text block's deltas (so existing tests
    that assert on streamed text keep passing), other text blocks stream
    their full text, thinking blocks their (possibly empty) thinking, and
    tool blocks their JSON input. Result blocks carry no stream events.
    """
    events: list[SimpleNamespace] = []
    chunks = list(chunks or [])
    used_chunks = False
    idx = 0
    for block in content:
        btype = getattr(block, "type", None)
        if btype not in _STREAMED_BLOCK_TYPES:
            continue
        name = getattr(block, "name", "") or ""
        events.append(block_start_event(idx, btype, name))
        if btype == "text":
            if chunks and not used_chunks:
                events.extend(text_delta_event(idx, c) for c in chunks)
                used_chunks = True
            else:
                text = getattr(block, "text", "") or ""
                if text:
                    events.append(text_delta_event(idx, text))
        elif btype == "thinking":
            thinking = getattr(block, "thinking", "") or ""
            if thinking:
                events.append(thinking_delta_event(idx, thinking))
        else:  # tool_use / server_tool_use
            tool_input = getattr(block, "input", None) or {}
            events.append(input_json_delta_event(idx, json.dumps(tool_input)))
        events.append(block_stop_event(idx))
        idx += 1
    return events


def _container(container: str | None) -> dict:
    """The optional ``container=`` kwarg, as response attributes.

    A provider response carries ``container.id`` only when a server tool ran
    through the code-execution caller; the engines must echo it back on a
    pause_turn continuation. Absent — the normal case, since both web tools
    pin ``allowed_callers: ["direct"]`` — the attribute is simply not set,
    so every existing fixture stays byte-for-byte what it was.
    """
    if container is None:
        return {}
    return {"container": SimpleNamespace(id=container)}


def _container_of(turn: SimpleNamespace) -> dict:
    """Carry a scripted turn's container onto a rebuilt final message."""
    container = getattr(turn, "container", None)
    return {} if container is None else {"container": container}


def raw_turn(
    content: list[SimpleNamespace],
    *,
    stop_reason: str,
    chunks: list[str] | None = None,
    events: list[SimpleNamespace] | None = None,
    container: str | None = None,
    usage: SimpleNamespace | None = None,
    snapshot_usage: SimpleNamespace | None = None,
) -> SimpleNamespace:
    """A scripted response with arbitrary content blocks (thinking,
    server tools, pause_turn shapes) for the chat loop's fake client.

    ``events`` overrides the synthesized raw-event stream when a test needs
    a precise ordering (e.g. thinking → text → tool). ``container`` scripts
    a provider continuation container id (see :func:`_container`).
    ``snapshot_usage`` scripts what ``current_message_snapshot`` reports
    when a turn is stopped mid-stream, which is normally a much smaller
    placeholder than the final message's count."""
    turn = SimpleNamespace(
        chunks=list(chunks or []),
        content=list(content),
        stop_reason=stop_reason,
        events=events,
        **_container(container),
    )
    # Attached only when supplied, like ``container``: ``SequencedFakeClient``
    # routes on ``hasattr(turn, "usage")`` to pick its stream context, so an
    # unconditional ``usage=None`` would silently change which context a
    # raw_turn gets if one were ever scripted through that client.
    if usage is not None:
        turn.usage = usage
    if snapshot_usage is not None:
        turn.snapshot_usage = snapshot_usage
    return turn


def request_context_text(request: dict) -> str:
    """The PROJECT CONTEXT block of a captured chat request.

    The context is the FIRST text block of the turn's user message (the
    user's own text follows it) — the Sonnet-unleashed context placement.
    Returns "" when the request has no such block.
    """
    for message in request.get("messages", []):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and first.get("type") == "text":
                text = first.get("text", "")
                if "PROJECT CONTEXT" in text:
                    return text
    return ""


class _FakeStreamCtx:
    def __init__(self, turn: SimpleNamespace):
        self._turn = turn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        """Yield the turn's raw stream events (explicit or synthesized)."""
        events = getattr(self._turn, "events", None)
        if events is None:
            events = _synthesize_events(
                self._turn.content, getattr(self._turn, "chunks", [])
            )
        yield from events

    @property
    def text_stream(self):
        yield from self._turn.chunks

    def get_final_message(self):
        # ``container`` is threaded through rather than dropped: the chat
        # loop reads it off the final message to carry a paused turn's
        # provider container onto the continuation request.
        return SimpleNamespace(
            content=self._turn.content,
            stop_reason=self._turn.stop_reason,
            usage=getattr(self._turn, "usage", None),
            **_container_of(self._turn),
        )

    @property
    def current_message_snapshot(self):
        """Stand-in for the real SDK's incrementally-accumulated snapshot.

        The real ``MessageStream`` updates this on every raw event, so a
        consumer that stops mid-iteration (see ``stream_user_turn``'s
        stop-request check) sees exactly what streamed so far, no more. This
        fake replays a fixed script rather than truly accumulating event by
        event, so it returns the same (full) content ``get_final_message``
        would — good enough to exercise the "read the snapshot instead of
        draining the stream" code path without duplicating the SDK's
        accumulation logic. ``stop_reason`` is ``None``, matching the real
        API (only set once the message is fully complete).

        ``snapshot_usage`` on the scripted turn overrides the usage here.
        That models the one way the snapshot genuinely differs from a final
        message: the authoritative output count rides the closing
        ``message_delta``, which a stopped stream never receives, so the
        snapshot reports only the small placeholder ``message_start``
        announced. Absent, the turn's own usage is returned and every
        pre-existing fixture behaves exactly as before.
        """
        snapshot_usage = getattr(self._turn, "snapshot_usage", None)
        return SimpleNamespace(
            content=self._turn.content,
            stop_reason=None,
            usage=(
                snapshot_usage
                if snapshot_usage is not None
                else getattr(self._turn, "usage", None)
            ),
        )


class _FakeMessages:
    def __init__(self, turns: list[Any]):
        self._turns = list(turns)
        self.requests: list[dict[str, Any]] = []

    def stream(self, **request):
        self.requests.append(request)
        if not self._turns:
            raise AssertionError("Fake client got more requests than scripted turns.")
        turn = self._turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return _FakeStreamCtx(turn)

    @property
    def last_request(self) -> dict[str, Any] | None:
        return self.requests[-1] if self.requests else None


class FakeClient:
    """``FakeClient([...turns...])`` — turns from :func:`text_turn` /
    :func:`tool_turn`, or Exception instances to raise on that round."""

    def __init__(self, turns: list[Any]):
        self.messages = _FakeMessages(turns)


def bad_request(message: str) -> Any:
    """A real ``anthropic.BadRequestError`` (status 400) for scripting the
    thinking.display capability degrade."""
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(400, request=request)
    return anthropic.BadRequestError(message, response=response, body=None)


def auth_error(
    message: str = (
        '{"type": "error", "error": {"type": "authentication_error", '
        '"message": "invalid x-api-key"}}'
    ),
) -> Any:
    """A real ``anthropic.AuthenticationError`` (401) for scripting an
    invalid/expired stored key across chat, research, and QC tests."""
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(401, request=request)
    return anthropic.AuthenticationError(message, response=response, body=None)


# ---------------------------------------------------------------------------
# Phase 4: research-shaped responses (web server tools + usage telemetry)
# ---------------------------------------------------------------------------


def _srvtoolu_id(prefix: str) -> str:
    """A unique ``srvtoolu_``-style id.

    Unique per call on purpose: a shared placeholder id would make two
    independent server-tool calls in one turn look like one, which the
    pairing invariant reads as a completed pair when it is nothing of the
    kind.
    """
    return f"srvtoolu_{prefix}_{next(_SRVTOOLU_COUNTER)}"


def search_result_block(
    urls: list[str], *, tool_use_id: str = ""
) -> SimpleNamespace:
    """A ``web_search_tool_result`` block whose results carry ``urls``.

    ``tool_use_id`` names the ``server_tool_use`` this answers — the real
    wire shape, and what makes the pair survive the server-tool pairing
    invariant (``backend/llm/server_tool_pairing.py``). Omitted only where a
    test deliberately scripts an orphaned result.
    """
    block = SimpleNamespace(
        type="web_search_tool_result",
        content=[
            SimpleNamespace(type="web_search_result", url=url, title=f"t:{url}")
            for url in urls
        ],
    )
    if tool_use_id:
        block.tool_use_id = tool_use_id
    return block


def fetch_blocks(url: str, *, tool_use_id: str = "") -> list[SimpleNamespace]:
    """A ``server_tool_use``(web_fetch) + result pair for ``url``."""
    use_id = tool_use_id or _srvtoolu_id("fetch")
    return [
        SimpleNamespace(
            type="server_tool_use",
            id=use_id,
            name="web_fetch",
            input={"url": url},
        ),
        SimpleNamespace(
            type="web_fetch_tool_result",
            tool_use_id=use_id,
            content={"type": "web_fetch_result", "url": url},
        ),
    ]


def usage(
    searches: int = 0,
    fetches: int = 0,
    *,
    input: int = 0,
    output: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
    cache_write_1h: int | None = None,
) -> SimpleNamespace:
    """Research/QC billed usage. ``cache_write`` is the TTL-wide total.

    ``cache_write_1h`` is the one-hour subtotal within it; supplying it
    attaches the nested ``cache_creation`` object the SDK sends on a
    request that carries a one-hour breakpoint (Final QC's verifier seats).
    """
    record = SimpleNamespace(
        input_tokens=input,
        output_tokens=output,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
        server_tool_use=SimpleNamespace(
            web_search_requests=searches, web_fetch_requests=fetches
        ),
    )
    if cache_write_1h is not None:
        record.cache_creation = SimpleNamespace(
            ephemeral_5m_input_tokens=max(0, cache_write - cache_write_1h),
            ephemeral_1h_input_tokens=cache_write_1h,
        )
    return record


def research_response(
    *,
    items: list[dict] | None = None,
    searched_urls: list[str] | None = None,
    queries: list[str] | None = None,
    extra_blocks: list[SimpleNamespace] | None = None,
    stop_reason: str = "tool_use",
    searches: int | None = None,
    fetches: int = 0,
    tokens: dict[str, int] | None = None,
    tool_name: str = "submit_requirements_research",
    container: str | None = None,
) -> SimpleNamespace:
    """A terminal research response: search results + the output tool call.

    ``items`` are raw payload item dicts (the engine normalizes them).
    ``searched_urls`` become one web_search_tool_result block. ``searches``
    defaults to len(searched_urls) so the usage telemetry stays coherent.
    ``queries`` prepend one ``server_tool_use``(web_search) block each — the
    shape the engine's live-activity relay detects (result blocks alone
    synthesize no stream events, so a fixture without them emits nothing).
    """
    content: list[SimpleNamespace] = []
    query_list = list(queries or [])
    for i, query in enumerate(query_list):
        # Use then its own result, the order and pairing the API produces.
        # The retrieved URLs ride the FIRST search; any later ones return an
        # empty result, which is a real outcome and leaves evidence counts
        # exactly as they were before ids existed here.
        use_id = _srvtoolu_id("research")
        content.append(
            SimpleNamespace(
                type="server_tool_use",
                id=use_id,
                name="web_search",
                input={"query": query},
            )
        )
        content.append(
            search_result_block(
                searched_urls if i == 0 else [], tool_use_id=use_id
            )
        )
    if searched_urls and not query_list:
        # No scripted queries: the bare result block the grounding tests use.
        content.append(search_result_block(searched_urls))
    content.extend(extra_blocks or [])
    if items is not None:
        content.append(
            tool_use_block(
                "toolu_research",
                tool_name,
                {"summary": "", "items": items},
            )
        )
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=usage(
            searches if searches is not None else len(searched_urls or []),
            fetches,
            **(tokens or {}),
        ),
        **_container(container),
    )


def pause_response(
    *,
    searched_urls: list[str] | None = None,
    searches: int | None = None,
    container: str | None = None,
    pending_query: str = "",
) -> SimpleNamespace:
    """A ``pause_turn`` response mid-research (server tools still running).

    ``pending_query`` appends a trailing ``server_tool_use`` with NO result
    — the reason a turn pauses at all, and the block the provider resumes
    from. Fixtures without it model a pause whose searches all came back,
    which is the easier half.
    """
    content: list[SimpleNamespace] = []
    if searched_urls:
        content.append(search_result_block(searched_urls))
    if pending_query:
        content.append(
            SimpleNamespace(
                type="server_tool_use",
                id=_srvtoolu_id("pending"),
                name="web_search",
                input={"query": pending_query},
            )
        )
    return SimpleNamespace(
        content=content,
        stop_reason="pause_turn",
        usage=usage(searches if searches is not None else len(searched_urls or [])),
        **_container(container),
    )


def qc_findings_response(
    lens: str,
    *,
    findings: list[dict] | None = None,
    summary: str = "",
    reviewed_checks: list[dict] | None = None,
    searched_urls: list[str] | None = None,
    stop_reason: str = "tool_use",
    searches: int | None = None,
    fetches: int = 0,
    tokens: dict[str, int] | None = None,
    container: str | None = None,
) -> SimpleNamespace:
    """A terminal Final-QC lens response: search results + submit_qc_findings.

    ``findings`` are raw payload finding dicts (the engine normalizes them);
    ``lens`` is only for readability in the test. ``findings=None`` produces a
    response with NO tool call (a parse-failure case)."""
    content: list[SimpleNamespace] = []
    if searched_urls:
        content.append(search_result_block(searched_urls))
    if findings is not None:
        checks = reviewed_checks
        if checks is None:
            checks = [
                {
                    "check": f"{lens} full-scope review",
                    "outcome": "finding" if findings else "passed",
                    "notes": (
                        f"Reviewed the {lens} scope and recorded "
                        f"{len(findings)} candidate finding(s)."
                    ),
                    "element_ids": [],
                    "source_urls": [],
                }
            ]
        content.append(
            tool_use_block(
                "toolu_qc_findings",
                "submit_qc_findings",
                {
                    "summary": summary,
                    "reviewed_checks": checks,
                    "findings": findings,
                },
            )
        )
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=usage(
            searches if searches is not None else len(searched_urls or []),
            fetches,
            **(tokens or {}),
        ),
        **_container(container),
    )


def qc_verdict_response(
    upholds: bool,
    *,
    severity: str | None = None,
    note: str = "",
    ops_adequate: bool | None = None,
    ops_note: str = "",
    evidence: list[dict] | None = None,
    searched_urls: list[str] | None = None,
    stop_reason: str = "tool_use",
    tokens: dict[str, int] | None = None,
    container: str | None = None,
) -> SimpleNamespace:
    """A Final-QC verifier response: a submit_qc_verdict tool call.

    ``evidence`` scripts ``refutation_evidence`` entries verbatim (each
    ``{"type": "source", "url": ...}`` or
    ``{"type": "document_ref", "reference": ...}``) — the v4 gate on a
    critical/high refutation. ``searched_urls`` additionally attaches a
    web-search result block, which is what makes a cited source count as
    RETRIEVED by this seat: scripting evidence without it models the
    citation-without-retrieval case the gate must reject.
    """
    content: list[SimpleNamespace] = []
    if searched_urls:
        content.append(search_result_block(searched_urls))
    content.append(
        tool_use_block(
            "toolu_qc_verdict",
            "submit_qc_verdict",
            {
                "upholds": upholds,
                "revised_severity": severity,
                "note": note,
                "ops_adequate": (
                    upholds if ops_adequate is None else ops_adequate
                ),
                "ops_note": ops_note,
                **({"refutation_evidence": evidence} if evidence else {}),
            },
        )
    )
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=usage(**(tokens or {})),
        **_container(container),
    )


def qc_consolidation_response(
    groups: list[dict],
    *,
    stop_reason: str = "tool_use",
    tokens: dict[str, int] | None = None,
    container: str | None = None,
) -> SimpleNamespace:
    """A Final-QC consolidation response: a submit_qc_consolidation call.

    ``groups`` are raw payload group dicts (the engine normalizes and then
    strictly validates them), so a test can script an invalid partition —
    a missing index, an unknown one, a merge with no canonical wording —
    and assert the deterministic singleton fallback.

    ``groups=None`` produces a response with NO tool call (the parse-failure
    case), mirroring ``qc_findings_response``.
    """
    content: list[SimpleNamespace] = []
    if groups is not None:
        content.append(
            tool_use_block(
                "toolu_qc_consolidation",
                "submit_qc_consolidation",
                {"groups": groups},
            )
        )
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=usage(**(tokens or {})),
        **_container(container),
    )


_CONSOLIDATION_MARKER = "[[QC-CONSOLIDATE:"
_CANDIDATE_INDEX_RE = re.compile(r"<candidate index=\"(\d+)\">")


def singleton_consolidation_for(request_text: str) -> SimpleNamespace:
    """The default answer to an unscripted grouping call: group nothing.

    Every QC fixture predates cross-lens consolidation and asserts against
    one panel per lens candidate, so the fake's default has to be the
    identity partition — that keeps those expectations meaning what they
    always meant while still running the real grouping code path.

    Indexes are read back out of the prompt the engine actually built, so a
    fixture can never answer for a candidate set it was not asked about; a
    test that wants real grouping scripts it with
    :func:`qc_consolidation_response`.
    """
    return qc_consolidation_response(
        [
            {
                "member_indexes": [int(index)],
                "canonical_title": None,
                "canonical_issue": None,
                "canonical_rationale": None,
                "grouping_rationale": None,
                "reconciled_ops": None,
            }
            for index in _CANDIDATE_INDEX_RE.findall(request_text)
        ]
    )


def _user_text(messages: list) -> str:
    """The first user turn's text, whether it is a string or content blocks.

    QC caches its shared document/standards/profile prefix by splitting the
    user turn into two text blocks (``backend/qc/engine.py``), so routing on
    ``content`` alone would see a list and match nothing. Every text block is
    joined in order, which keeps the routing keys (``[[QC-LENS:...]]``,
    ``[[QC-VERIFY:...]]``, finding titles) findable wherever they land.
    """
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n\n".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        return ""
    return ""


class SequencedFakeClient:
    """Fake client whose scripted turns are keyed by dimension.

    The research fan-out runs dimensions on parallel threads, so a single
    shared pop-in-order queue (``FakeClient``) would interleave
    nondeterministically. This client inspects the request's first user
    message and pops from the matching dimension's own queue (matched by
    ``key`` substring). Thread-safe.

    Captured requests SNAPSHOT their ``messages`` list. Both engines append
    to one list across a pause_turn continuation, so capturing it by
    reference makes every request in an attempt show that attempt's *final*
    conversation — a per-request assertion ("this continuation re-sent
    exactly the paused content") would then quietly be asserting something
    else. The message dicts themselves are never mutated in place (the
    resend sanitizer builds new ones), so a shallow copy is enough.
    """

    def __init__(self, scripts: dict[str, list]):
        import threading

        self._scripts = {k: list(v) for k, v in scripts.items()}
        self._lock = threading.Lock()
        self.requests: list[dict] = []
        self.messages = self  # client.messages.stream(...)
        # client.messages.batches.{create,retrieve,results,cancel} — the
        # batched phase-2 transport routes through the SAME scripts, so an
        # existing QC fixture proves parity between the two paths instead of
        # needing a parallel set of batch fixtures.
        self.batches = _FakeBatches(self)

    def pop_turn(self, request: dict):
        """Resolve one request against the scripts. Raises a scripted error.

        Shared by the streaming and batch fakes so both consume the same
        queues in the same way; the batch fake catches what this raises and
        turns it into an errored result line, which is what the real API
        does with a per-request failure inside a batch.
        """
        with self._lock:
            captured = dict(request)
            if isinstance(captured.get("messages"), list):
                captured["messages"] = list(captured["messages"])
            self.requests.append(captured)
            first_user = _user_text(request.get("messages", []))
            return self._match_locked(first_user)

    def stream(self, **request):
        with self._lock:
            captured = dict(request)
            if isinstance(captured.get("messages"), list):
                captured["messages"] = list(captured["messages"])
            self.requests.append(captured)
            first_user = _user_text(request.get("messages", []))
            turn = self._match_locked(first_user)
        if isinstance(turn, Exception):
            raise turn
        return _FakeStreamCtx(
            SimpleNamespace(
                chunks=[],
                content=turn.content,
                stop_reason=turn.stop_reason,
                **_container_of(turn),
            )
        ) if not hasattr(turn, "usage") else _FakeResearchStreamCtx(turn)

    def _match_locked(self, first_user: str):
        # A grouping call quotes every candidate's title verbatim, so
        # ordinary title-keyed scripts all match it. Routing it here —
        # before the substring sweep, and only against keys that carry
        # the marker themselves — stops it consuming a verdict scripted
        # for one of the findings it is merely quoting, which would
        # desynchronize that finding's whole panel.
        consolidating = _CONSOLIDATION_MARKER in first_user
        candidates = (
            {
                key: queue
                for key, queue in self._scripts.items()
                if _CONSOLIDATION_MARKER in key
            }
            if consolidating
            else {
                key: queue
                for key, queue in self._scripts.items()
                if _CONSOLIDATION_MARKER not in key
            }
        )
        for key, queue in candidates.items():
            if key in first_user:
                if not queue:
                    raise AssertionError(
                        f"Fake research client: no scripted turns left "
                        f"for {key!r}."
                    )
                turn = queue.pop(0)
                break
        else:
            if consolidating:
                turn = singleton_consolidation_for(first_user)
            else:
                raise AssertionError(
                    "Fake research client: no script matches the request "
                    f"({first_user[:80]!r})."
                )
        return turn



class _FakeBatches:
    """In-memory Message Batches API over a SequencedFakeClient's scripts.

    Deliberately synchronous: ``create`` resolves every request immediately
    and ``retrieve`` reports ``ended`` on the first poll, so a test never
    sleeps. That models the parts of the contract the engine actually
    depends on — custom_id round-tripping, per-request success/error result
    lines, request_counts, pause_turn results carried into a second round,
    and cancellation — without pretending to model provider queue latency,
    which the engine treats as opaque anyway.

    A scripted turn that is an Exception becomes an ERRORED result line
    rather than a raised call, because that is where the real API puts a
    per-request failure inside an otherwise healthy batch. A scripted
    ``AssertionError`` from an exhausted or unmatched script is re-raised:
    that is a broken fixture, not a modelled provider failure, and swallowing
    it into an errored seat would turn a test bug into a passing partial run.
    """

    def __init__(self, client: "SequencedFakeClient"):
        self._client = client
        self._batches: dict[str, dict] = {}
        self._counter = 0
        self.created: list[list[dict]] = []
        self.cancelled: list[str] = []

    def create(self, *, requests):
        self._counter += 1
        batch_id = f"msgbatch_fake_{self._counter}"
        self.created.append([dict(r) for r in requests])
        results = []
        counts = {
            "processing": 0,
            "succeeded": 0,
            "errored": 0,
            "canceled": 0,
            "expired": 0,
        }
        for request in requests:
            custom_id = request["custom_id"]
            params = dict(request["params"])
            try:
                turn = self._client.pop_turn(params)
            except AssertionError:
                raise
            if isinstance(turn, Exception):
                counts["errored"] += 1
                results.append(
                    SimpleNamespace(
                        custom_id=custom_id,
                        result=SimpleNamespace(
                            type="errored",
                            error=_fake_batch_error(turn),
                        ),
                    )
                )
                continue
            counts["succeeded"] += 1
            message = SimpleNamespace(
                content=turn.content,
                stop_reason=turn.stop_reason,
                usage=getattr(turn, "usage", None),
                **_container_of(turn),
            )
            results.append(
                SimpleNamespace(
                    custom_id=custom_id,
                    result=SimpleNamespace(type="succeeded", message=message),
                )
            )
        self._batches[batch_id] = {"results": results, "counts": counts}
        return SimpleNamespace(
            id=batch_id,
            processing_status="ended",
            request_counts=SimpleNamespace(**counts),
        )

    def retrieve(self, batch_id):
        record = self._batches[batch_id]
        return SimpleNamespace(
            id=batch_id,
            processing_status="ended",
            request_counts=SimpleNamespace(**record["counts"]),
        )

    def results(self, batch_id):
        return list(self._batches[batch_id]["results"])

    def cancel(self, batch_id):
        self.cancelled.append(batch_id)
        return SimpleNamespace(id=batch_id, processing_status="canceling")


def _fake_batch_error(exc: Exception) -> SimpleNamespace:
    """Wrap a scripted exception in the batch error envelope.

    The type string is what the engine classifies on, so it has to be the
    wire name rather than the Python class name.
    """
    name = type(exc).__name__.lower()
    if "auth" in name:
        wire = "authentication_error"
    elif "ratelimit" in name or "rate_limit" in name:
        wire = "rate_limit_error"
    elif "badrequest" in name or "invalid" in name:
        wire = "invalid_request_error"
    elif "connection" in name or "timeout" in name:
        wire = "timeout_error"
    else:
        wire = "api_error"
    return SimpleNamespace(
        type="error",
        error=SimpleNamespace(type=wire, message=str(exc)),
    )


class _FakeResearchStreamCtx:
    """Stream context that returns the scripted response object as-is
    (preserving ``usage`` — ``_FakeStreamCtx`` rebuilds and drops it)."""

    def __init__(self, response: SimpleNamespace):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        """Yield the response's raw stream events (explicit or synthesized).

        The research engine iterates the stream for live activity before
        calling ``get_final_message`` (mirroring the chat loop). An explicit
        ``events`` attribute on the scripted response overrides synthesis —
        the malformed-frame injection hook, same as ``raw_turn``."""
        events = getattr(self._response, "events", None)
        if events is None:
            events = _synthesize_events(self._response.content, [])
        yield from events

    @property
    def text_stream(self):
        yield from ()

    def get_final_message(self):
        return self._response
