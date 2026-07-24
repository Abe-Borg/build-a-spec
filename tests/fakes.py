"""Scripted fake Anthropic streaming client (hermetic tests).

Grown from the Phase 1 fake (which spoke only text) to script whole
multi-round turns: each entry is a "turn" the next ``stream()`` call
replays — text chunks, final content blocks (text and/or tool_use), and a
stop reason. An entry that is an Exception instance is raised instead,
for failure-path tests. Mirrors the fake-client convention of Spec
Critic's suite.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any


def audit_grade_qc_result(session: Any, findings: list[Any]):
    """Build a current, complete QC result for non-QC endpoint tests.

    A few concurrency/source-preservation tests need a trusted QC result only
    to reach the later guard they actually exercise.  Keep those fixtures on
    the same v2 identity, lens, verifier, and pricing contract as production
    instead of weakening the endpoint's audit-completeness gate.
    """
    import uuid

    from backend import settings
    from backend.app import _qc_source_guard
    from backend.qc.engine import (
        QC_PROTOCOL_VERSION,
        QC_REPORT_SCHEMA_VERSION,
        QCLensStatus,
        QCResult,
        QCReviewedCheck,
        QCVerdict,
        build_qc_input_manifest,
        qc_input_fingerprint,
        qc_version_fingerprint,
    )
    from backend.qc.schema import QC_LENSES
    from backend.usage_ledger import usage_pricing_snapshot

    profile = session.research.profile_result
    source_guard = _qc_source_guard(session)
    manifest = build_qc_input_manifest(
        session.doc.doc,
        profile,
        session.module,
        version_index=session.doc.index,
        discipline=session.discipline,
        source_guard=source_guard,
        model=settings.QC_MODEL,
        max_tokens=settings.QC_MAX_TOKENS,
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
        finding.verification_threshold = (panel_size // 2) + 1
        finding.verification_outcome = "upheld"
        finding.verdicts = [
            QCVerdict(
                upholds=True,
                note="Audit-grade endpoint test fixture upheld the finding.",
                reviewer_index=index,
            )
            for index in range(1, panel_size + 1)
        ]

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
    thinking: int = 0,
    searches: int = 0,
    fetches: int = 0,
) -> SimpleNamespace:
    """A billed-usage object shaped for the ledger (WI4 cost meter)."""
    return SimpleNamespace(
        input_tokens=input,
        output_tokens=output,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
        output_tokens_details=SimpleNamespace(thinking_tokens=thinking),
        server_tool_use=SimpleNamespace(
            web_search_requests=searches, web_fetch_requests=fetches
        ),
    )


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


def chat_search_blocks(query: str, urls: list[str]) -> list[SimpleNamespace]:
    """A ``server_tool_use``(web_search) + result pair for the chat loop."""
    return [
        SimpleNamespace(
            type="server_tool_use",
            id="srvtoolu_fake",
            name="web_search",
            input={"query": query},
        ),
        search_result_block(urls),
    ]


# ---------------------------------------------------------------------------
# Raw stream events (WI1: the chat loop now iterates SDK events, not
# text_stream). Builders mirror the anthropic SDK's raw-event shapes the
# engine consumes; ``_synthesize_events`` derives a plausible default
# sequence from a scripted turn's content so existing tool/text/thinking
# turns stream correctly with no per-test wiring.
# ---------------------------------------------------------------------------


def block_start_event(index: int, block_type: str, name: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        type="content_block_start",
        index=index,
        content_block=SimpleNamespace(type=block_type, name=name),
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


def raw_turn(
    content: list[SimpleNamespace],
    *,
    stop_reason: str,
    chunks: list[str] | None = None,
    events: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    """A scripted response with arbitrary content blocks (thinking,
    server tools, pause_turn shapes) for the chat loop's fake client.

    ``events`` overrides the synthesized raw-event stream when a test needs
    a precise ordering (e.g. thinking → text → tool)."""
    return SimpleNamespace(
        chunks=list(chunks or []),
        content=list(content),
        stop_reason=stop_reason,
        events=events,
    )


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
        return SimpleNamespace(
            content=self._turn.content,
            stop_reason=self._turn.stop_reason,
            usage=getattr(self._turn, "usage", None),
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
        """
        return SimpleNamespace(
            content=self._turn.content,
            stop_reason=None,
            usage=getattr(self._turn, "usage", None),
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


# ---------------------------------------------------------------------------
# Phase 4: research-shaped responses (web server tools + usage telemetry)
# ---------------------------------------------------------------------------


def search_result_block(urls: list[str]) -> SimpleNamespace:
    """A ``web_search_tool_result`` block whose results carry ``urls``."""
    return SimpleNamespace(
        type="web_search_tool_result",
        content=[
            SimpleNamespace(type="web_search_result", url=url, title=f"t:{url}")
            for url in urls
        ],
    )


def fetch_blocks(url: str) -> list[SimpleNamespace]:
    """A ``server_tool_use``(web_fetch) + result pair for ``url``."""
    return [
        SimpleNamespace(type="server_tool_use", name="web_fetch", input={"url": url}),
        SimpleNamespace(
            type="web_fetch_tool_result",
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
) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=input,
        output_tokens=output,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
        server_tool_use=SimpleNamespace(
            web_search_requests=searches, web_fetch_requests=fetches
        ),
    )


def research_response(
    *,
    items: list[dict] | None = None,
    searched_urls: list[str] | None = None,
    extra_blocks: list[SimpleNamespace] | None = None,
    stop_reason: str = "tool_use",
    searches: int | None = None,
    fetches: int = 0,
    tokens: dict[str, int] | None = None,
    tool_name: str = "submit_requirements_research",
) -> SimpleNamespace:
    """A terminal research response: search results + the output tool call.

    ``items`` are raw payload item dicts (the engine normalizes them).
    ``searched_urls`` become one web_search_tool_result block. ``searches``
    defaults to len(searched_urls) so the usage telemetry stays coherent.
    """
    content: list[SimpleNamespace] = []
    if searched_urls:
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
    )


def pause_response(
    *, searched_urls: list[str] | None = None, searches: int | None = None
) -> SimpleNamespace:
    """A ``pause_turn`` response mid-research (server tools still running)."""
    content: list[SimpleNamespace] = []
    if searched_urls:
        content.append(search_result_block(searched_urls))
    return SimpleNamespace(
        content=content,
        stop_reason="pause_turn",
        usage=usage(searches if searches is not None else len(searched_urls or [])),
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
    )


def qc_verdict_response(
    upholds: bool,
    *,
    severity: str | None = None,
    note: str = "",
    stop_reason: str = "tool_use",
    tokens: dict[str, int] | None = None,
) -> SimpleNamespace:
    """A Final-QC verifier response: a submit_qc_verdict tool call."""
    return SimpleNamespace(
        content=[
            tool_use_block(
                "toolu_qc_verdict",
                "submit_qc_verdict",
                {"upholds": upholds, "revised_severity": severity, "note": note},
            )
        ],
        stop_reason=stop_reason,
        usage=usage(**(tokens or {})),
    )


class SequencedFakeClient:
    """Fake client whose scripted turns are keyed by dimension.

    The research fan-out runs dimensions on parallel threads, so a single
    shared pop-in-order queue (``FakeClient``) would interleave
    nondeterministically. This client inspects the request's first user
    message and pops from the matching dimension's own queue (matched by
    ``key`` substring). Thread-safe.
    """

    def __init__(self, scripts: dict[str, list]):
        import threading

        self._scripts = {k: list(v) for k, v in scripts.items()}
        self._lock = threading.Lock()
        self.requests: list[dict] = []
        self.messages = self  # client.messages.stream(...)

    def stream(self, **request):
        with self._lock:
            self.requests.append(request)
            first_user = ""
            for message in request.get("messages", []):
                if message.get("role") == "user":
                    content = message.get("content")
                    first_user = content if isinstance(content, str) else ""
                    break
            for key, queue in self._scripts.items():
                if key in first_user:
                    if not queue:
                        raise AssertionError(
                            f"Fake research client: no scripted turns left "
                            f"for {key!r}."
                        )
                    turn = queue.pop(0)
                    break
            else:
                raise AssertionError(
                    "Fake research client: no script matches the request "
                    f"({first_user[:80]!r})."
                )
        if isinstance(turn, Exception):
            raise turn
        return _FakeStreamCtx(
            SimpleNamespace(chunks=[], content=turn.content, stop_reason=turn.stop_reason)
        ) if not hasattr(turn, "usage") else _FakeResearchStreamCtx(turn)


class _FakeResearchStreamCtx:
    """Stream context that returns the scripted response object as-is
    (preserving ``usage`` — ``_FakeStreamCtx`` rebuilds and drops it)."""

    def __init__(self, response: SimpleNamespace):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        yield from ()

    def get_final_message(self):
        return self._response
