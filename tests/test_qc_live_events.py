"""Focused contract tests for Final QC's live Review Room event stream."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import anthropic
import httpx

from backend import settings
from backend.qc.engine import VERIFICATION_RULE_V4, run_final_qc
from backend.qc.schema import QC_LENSES
from backend.spec_doc.model import DocumentStore
from backend.spec_modules import DEFAULT_MODULE
from tests.fakes import (
    SequencedFakeClient,
    _synthesize_events,
    bad_request,
    block_start_event,
    block_stop_event,
    code_execution_tool_events,
    input_json_delta_event,
    qc_findings_response,
    qc_verdict_response,
)


_LENS_KEYS = {
    lens.lens_id: f"[[QC-LENS:{lens.lens_id}]]" for lens in QC_LENSES
}


def _store() -> DocumentStore:
    store = DocumentStore()
    store.begin_turn()
    store.apply_edits(
        [
            {
                "action": "replace",
                "target_id": "sec",
                "text": "WET-PIPE SPRINKLER SYSTEMS",
                "numbering": "21 13 13",
            },
            {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "text": "Comply with NFPA 13-2019 throughout.",
                "status": "assumed",
            },
        ]
    )
    store.commit_turn()
    return store


def _finding(
    title: str,
    *,
    severity: str = "medium",
    source_urls: list[str] | None = None,
    proposed_ops: list[dict] | None = None,
) -> dict:
    return {
        "title": title,
        "severity": severity,
        "element_id": "pt1.a1.p1",
        "issue": f"Issue for {title}.",
        "rationale": f"Rationale for {title}.",
        "source_urls": source_urls or [],
        "proposed_ops": proposed_ops,
    }


def _scripts(**per_lens: list[object]) -> dict[str, list[object]]:
    return {
        key: list(
            per_lens.get(
                lens_id,
                [qc_findings_response(lens_id, findings=[])],
            )
        )
        for lens_id, key in _LENS_KEYS.items()
    }


def _run(
    scripts: dict[str, list[object]],
    events: list[dict],
    *,
    should_stop=lambda: False,
) -> object:
    return _run_client(
        SequencedFakeClient(scripts),
        events,
        should_stop=should_stop,
    )


def _run_client(
    client: object,
    events: list[dict],
    *,
    event_sink=None,
    should_stop=lambda: False,
) -> object:
    store = _store()
    return run_final_qc(
        store.doc,
        None,
        DEFAULT_MODULE,
        client,
        model=settings.QC_MODEL,
        max_tokens=4096,
        version_index=store.index,
        started_at="2026-07-28T10:00:00-07:00",
        finished_at="2026-07-28T10:01:00-07:00",
        run_id="qc-live-test",
        event_sink=event_sink or events.append,
        should_stop=should_stop,
    )


def _events_for(events: list[dict], **fields: object) -> list[dict]:
    return [
        event
        for event in events
        if all(event.get(key) == value for key, value in fields.items())
    ]


def test_zero_candidates_emits_truthful_empty_phase_transitions() -> None:
    events: list[dict] = []
    result = _run(_scripts(), events)

    assert result.findings == []
    assert len(_events_for(events, type="lens_started")) == len(QC_LENSES)
    assert len(_events_for(events, type="lens_complete")) == len(QC_LENSES)
    started = _events_for(events, type="verification_started")
    assert started == [
        {
            "type": "verification_started",
            "candidates": [],
            "total_candidates": 0,
            "total_seats": 0,
            "max_workers": max(1, settings.QC_MAX_WORKERS),
        }
    ]
    assert _events_for(events, type="verify_progress") == [
        {"type": "verify_progress", "done": 0, "total": 0}
    ]
    assert _events_for(events, type="verification_complete") == [
        {
            "type": "verification_complete",
            "total_candidates": 0,
            "total_seats": 0,
            "completed_seats": 0,
            "upheld": 0,
            "refuted": 0,
            "disputed": 0,
            "inconclusive": 0,
        }
    ]
    assert _events_for(events, type="validation_started") == [
        {"type": "validation_started", "total": 0}
    ]
    assert _events_for(events, type="validation_complete") == [
        {
            "type": "validation_complete",
            "total": 0,
            "done": 0,
            "safe_fix_count": 0,
            "advisory_count": 0,
            "manual_count": 0,
        }
    ]


def test_live_lens_panel_and_validation_events_use_observable_payloads() -> None:
    title = "Replace the stale edition"
    ops = [
        {
            "action": "replace",
            "target_id": "pt1.a1.p1",
            "text": "Comply with NFPA 13-2025 throughout.",
            "status": "confirmed",
        }
    ]
    lens_response = qc_findings_response(
        "code_compliance",
        findings=[
            _finding(
                title,
                source_urls=["https://codes.example/rule"],
                proposed_ops=ops,
            )
        ],
        searched_urls=["https://codes.example/rule"],
        searches=2,
        fetches=1,
    )
    lens_response.events = [
        block_start_event(0, "thinking"),
        block_stop_event(0),
        block_start_event(1, "server_tool_use", "web_search"),
        input_json_delta_event(1, '{"query":"first code query"}'),
        block_stop_event(1),
        # Consecutive searches emit two query chips but only one activity change.
        block_start_event(2, "server_tool_use", "web_search"),
        input_json_delta_event(2, '{"query":"second code query"}'),
        block_stop_event(2),
        block_start_event(3, "server_tool_use", "web_fetch"),
        input_json_delta_event(3, '{"url":"https://codes.example/rule"}'),
        block_stop_event(3),
        block_start_event(4, "tool_use", "submit_qc_findings"),
        # Output-tool JSON is intentionally not streamed or buffered.
        input_json_delta_event(4, '{"private":"must not leak"}'),
        block_stop_event(4),
    ]
    verdict_one = qc_verdict_response(
        True,
        severity="low",
        note="private verifier reasoning",
        ops_adequate=True,
        ops_note="private fix reasoning",
    )
    verdict_one.events = [
        block_start_event(0, "thinking"),
        block_stop_event(0),
        block_start_event(1, "server_tool_use", "web_search"),
        input_json_delta_event(1, '{"query":"independent check"}'),
        block_stop_event(1),
        block_start_event(2, "server_tool_use", "web_fetch"),
        input_json_delta_event(2, '{"url":"https://codes.example/review"}'),
        block_stop_event(2),
        *_synthesize_events(verdict_one.content, []),
    ]
    scripts = _scripts(code_compliance=[lens_response])
    scripts[title] = [verdict_one, qc_verdict_response(True, ops_adequate=True)]
    events: list[dict] = []
    result = _run(scripts, events)

    assert len(result.findings) == 1
    lens_events = _events_for(events, lens_id="code_compliance")
    lens_types = [event["type"] for event in lens_events]
    assert lens_types[0] == "lens_started"
    assert lens_types[-1] == "lens_complete"
    assert [
        event["kind"]
        for event in lens_events
        if event["type"] == "lens_activity"
    ] == ["thinking", "searching", "fetching", "writing"]
    assert [
        event["query"]
        for event in lens_events
        if event["type"] == "lens_search"
    ] == ["first code query", "second code query"]
    terminal = lens_events[-1]
    assert terminal["reviewed_check_count"] == 1
    assert terminal["candidate_count"] == 1
    assert terminal["grounded_count"] == 1
    assert terminal["search_count"] == 2
    assert terminal["fetch_count"] == 1
    assert terminal["request_count"] == 1

    verification_started = _events_for(events, type="verification_started")[0]
    assert verification_started["candidates"] == [
        {
            "candidate_id": "candidate-1",
            "title": title,
            "original_severity": "medium",
            "lens_id": "code_compliance",
            # One lens claim behind this candidate — nothing was grouped.
            "origin_count": 1,
            "panel_size": 2,
            "uphold_requires": 2,
            "rule": VERIFICATION_RULE_V4,
            "evidence_gated": False,
            "outcomes": ["upheld", "disputed", "refuted", "inconclusive"],
        }
    ]
    assert verification_started["total_seats"] == 2

    seat_one = _events_for(
        events,
        candidate_id="candidate-1",
        reviewer_index=1,
    )
    seat_one_types = [event["type"] for event in seat_one]
    assert seat_one_types[0] == "verifier_started"
    assert seat_one_types[-1] == "verifier_complete"
    assert "verifier_search" in seat_one_types
    assert "verifier_fetch" in seat_one_types
    completed = seat_one[-1]
    assert completed["status"] == "completed"
    assert completed["upholds"] is True
    assert completed["revised_severity"] == "low"
    assert completed["ops_adequate"] is True
    assert "note" not in completed
    assert "ops_note" not in completed
    assert "private" not in str(events)

    candidate = _events_for(events, type="candidate_complete")[0]
    assert candidate == {
        "type": "candidate_complete",
        "candidate_id": "candidate-1",
        "outcome": "upheld",
        "dispute_reason": "",
        "panel_size": 2,
        "uphold_requires": 2,
        "completed_seats": 2,
        "upholds": 2,
    }
    validation = _events_for(events, type="validation_progress")
    assert len(validation) == 1
    assert validation[0]["outcome"] == "safe_fix"
    assert validation[0]["ops_valid"] is True
    assert events.index(_events_for(events, type="verification_complete")[0]) < (
        events.index(validation[0])
    )


def test_malformed_frames_are_ignored_and_stream_failure_retries(monkeypatch) -> None:
    import backend.qc.engine as engine

    monkeypatch.setattr(engine.time, "sleep", lambda _seconds: None)
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    retryable = anthropic.APIConnectionError(message="reset", request=request)

    class RaisingEvents:
        def __iter__(self):
            yield SimpleNamespace(type="content_block_start", index=0)
            raise retryable

    failed_stream = qc_findings_response("completeness", findings=[])
    failed_stream.events = RaisingEvents()
    recovered = qc_findings_response("completeness", findings=[])
    recovered.events = [
        SimpleNamespace(type="content_block_start", index=0, content_block=None),
        SimpleNamespace(type="content_block_delta", index=0, delta=None),
        block_start_event(1, "server_tool_use", "web_search"),
        input_json_delta_event(1, "{not json"),
        block_stop_event(1),
        SimpleNamespace(type="mystery_frame"),
        *_synthesize_events(recovered.content, []),
    ]
    events: list[dict] = []
    _run(_scripts(completeness=[failed_stream, recovered]), events)

    retries = _events_for(events, type="lens_retry", lens_id="completeness")
    assert retries == [
        {
            "type": "lens_retry",
            "lens_id": "completeness",
            "attempt": 1,
            "max_attempts": 3,
            "reason": "connection",
            "backoff_s": 5.0,
        }
    ]
    terminal = _events_for(events, type="lens_complete", lens_id="completeness")
    assert len(terminal) == 1
    assert not _events_for(events, type="lens_search", lens_id="completeness")


def test_start_input_only_frames_emit_real_lens_and_seat_activity(
    monkeypatch,
) -> None:
    """A server tool whose whole input arrives on the START frame — the
    code-execution caller's shape, which streams no ``input_json_delta`` —
    still names its query and URL in both Review Room phases.

    Direct callers (Chunk 1.1) make that shape unexpected, not impossible;
    this is the fallback that keeps a specialist card and a verifier seat
    from showing a nameless search.
    """
    monkeypatch.setattr(settings, "QC_MAX_WORKERS", 1)
    title = "Start-input candidate"
    lens_response = qc_findings_response(
        "code_compliance", findings=[_finding(title)]
    )
    lens_response.events = [
        *code_execution_tool_events(
            0, "web_search", {"query": "nfpa 13 2025 remote area reduction"}
        ),
        *code_execution_tool_events(
            1, "web_fetch", {"url": "https://example.test/nfpa-13"}
        ),
        *_synthesize_events(lens_response.content, []),
    ]
    first_seat = qc_verdict_response(True)
    first_seat.events = [
        *code_execution_tool_events(
            0, "web_search", {"query": "seat start-input query"}
        ),
        *code_execution_tool_events(
            1, "web_fetch", {"url": "https://example.test/seat"}
        ),
        *_synthesize_events(first_seat.content, []),
    ]
    scripts = _scripts(code_compliance=[lens_response])
    scripts[title] = [first_seat, qc_verdict_response(True)]
    events: list[dict] = []
    _run(scripts, events)

    assert _events_for(events, type="lens_search", lens_id="code_compliance") == [
        {
            "type": "lens_search",
            "lens_id": "code_compliance",
            "query": "nfpa 13 2025 remote area reduction",
        }
    ]
    assert _events_for(events, type="lens_fetch", lens_id="code_compliance") == [
        {
            "type": "lens_fetch",
            "lens_id": "code_compliance",
            "url": "https://example.test/nfpa-13",
        }
    ]
    assert _events_for(events, type="verifier_search") == [
        {
            "type": "verifier_search",
            "candidate_id": "candidate-1",
            "reviewer_index": 1,
            "query": "seat start-input query",
        }
    ]
    assert _events_for(events, type="verifier_fetch") == [
        {
            "type": "verifier_fetch",
            "candidate_id": "candidate-1",
            "reviewer_index": 1,
            "url": "https://example.test/seat",
        }
    ]
    # Adjudication is untouched — this chunk changes labels, not verdicts.
    assert _events_for(events, type="candidate_complete")[0]["outcome"] == "upheld"


def test_qc_streamed_deltas_win_and_an_absent_input_says_nothing() -> None:
    """Precedence and absence for a lens stream: deltas beat the start copy,
    a block with neither shape emits nothing, and a non-mapping start input
    is garbage rather than a lens failure."""
    lens_response = qc_findings_response("completeness", findings=[])
    lens_response.events = [
        # Both shapes on one block: the streamed deltas win.
        block_start_event(
            0, "server_tool_use", "web_search", input={"query": "start copy"}
        ),
        input_json_delta_event(0, '{"query": "streamed delta"}'),
        block_stop_event(0),
        # Neither shape: nothing truthful to say, so nothing is said.
        block_start_event(1, "server_tool_use", "web_fetch"),
        block_stop_event(1),
        # A start input that is not a mapping at all.
        SimpleNamespace(
            type="content_block_start",
            index=2,
            content_block=SimpleNamespace(
                type="server_tool_use", name="web_search", input=["not", "a", "map"]
            ),
        ),
        block_stop_event(2),
        # A repeat stop for a block already retired: every tracking dict
        # dropped index 0 the first time, so there is nothing left to replay.
        block_stop_event(0),
        *_synthesize_events(lens_response.content, []),
    ]
    events: list[dict] = []
    _run(_scripts(completeness=[lens_response]), events)

    assert [
        event["query"]
        for event in _events_for(events, type="lens_search", lens_id="completeness")
    ] == ["streamed delta"]
    assert not _events_for(events, type="lens_fetch", lens_id="completeness")
    # All three blocks announced their activity — the non-mapping input was
    # rejected by the reader, not by the frame's try/except (which would have
    # swallowed the start frame whole and lost the third "searching").
    assert [
        event["kind"]
        for event in _events_for(events, type="lens_activity", lens_id="completeness")
    ] == ["searching", "fetching", "searching", "writing"]
    terminal = _events_for(events, type="lens_complete", lens_id="completeness")
    assert len(terminal) == 1


def _lens_requests(client: SequencedFakeClient, lens_id: str) -> list[dict]:
    """Captured requests belonging to one lens, in order."""
    key = _LENS_KEYS[lens_id]
    return [
        request
        for request in client.requests
        if key
        in "\n\n".join(
            block.get("text", "")
            for block in request["messages"][0]["content"]
            if isinstance(block, dict)
        )
    ]


def test_qc_pause_continuation_echoes_the_container_and_a_retry_drops_it(
    monkeypatch,
) -> None:
    """QC's continuation obeys the same container contract as research.

    A code-execution-called server tool can only be resumed inside the
    container it started in. With direct callers none is expected, so this
    is the defense-in-depth half — and the reset boundary is the part worth
    pinning: a retry abandons the conversation, so it must not inherit the
    failed attempt's container.
    """
    import backend.qc.engine as engine

    monkeypatch.setattr(engine.time, "sleep", lambda _seconds: None)
    http_request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    retryable = anthropic.APIConnectionError(message="reset", request=http_request)

    first_pause = qc_findings_response(
        "code_compliance",
        findings=[],
        stop_reason="pause_turn",
        container="cont_qc_1",
    )
    client = SequencedFakeClient(
        _scripts(
            code_compliance=[
                first_pause,
                # No container field — not a revocation.
                qc_findings_response(
                    "code_compliance", findings=[], stop_reason="pause_turn"
                ),
                retryable,
                qc_findings_response("code_compliance", findings=[]),
            ]
        )
    )
    events: list[dict] = []
    _run_client(client, events)

    requests = _lens_requests(client, "code_compliance")
    assert len(requests) == 4
    assert "container" not in requests[0]
    assert requests[1]["container"] == "cont_qc_1"
    assert requests[2]["container"] == "cont_qc_1"
    assert "container" not in requests[3]

    # The pause contract is unchanged, and the container never enters the
    # cached prefix or the conversation.
    assert [m["role"] for m in requests[1]["messages"]] == ["user", "assistant"]
    assert requests[1]["messages"][1]["content"] == first_pause.content
    for request in requests:
        assert "cont_qc_1" not in str(request["messages"])
        assert "cont_qc_1" not in str(request["system"])
        assert "cont_qc_1" not in str(request["tools"])

    # Every other lens ran container-free, as they always have.
    for lens in QC_LENSES:
        if lens.lens_id == "code_compliance":
            continue
        assert all(
            "container" not in request
            for request in _lens_requests(client, lens.lens_id)
        )


def test_failed_verifier_makes_panel_inconclusive_without_fix_validation() -> None:
    title = "Candidate with a broken seat"
    scripts = _scripts(
        coordination_consistency=[
            qc_findings_response(
                "coordination_consistency",
                findings=[_finding(title)],
            )
        ]
    )
    scripts[title] = [qc_verdict_response(True), RuntimeError("seat unavailable")]
    events: list[dict] = []
    result = _run(scripts, events)

    assert result.findings == []
    assert len(result.inconclusive) == 1
    failed = [
        event
        for event in _events_for(events, type="verifier_complete")
        if event["status"] == "failed"
    ]
    assert len(failed) == 1
    assert "upholds" not in failed[0]
    assert "revised_severity" not in failed[0]
    assert "ops_adequate" not in failed[0]
    assert _events_for(events, type="candidate_complete") == [
        {
            "type": "candidate_complete",
            "candidate_id": "candidate-1",
            "outcome": "inconclusive",
            "dispute_reason": "",
            "panel_size": 2,
            "uphold_requires": 2,
            "completed_seats": 1,
            "upholds": 1,
        }
    ]
    assert _events_for(events, type="verification_complete")[0][
        "inconclusive"
    ] == 1
    assert _events_for(events, type="validation_progress") == []
    assert _events_for(events, type="validation_complete")[0]["total"] == 0


def test_verifier_retry_then_relays_tool_activity_for_the_same_seat(
    monkeypatch,
) -> None:
    """Retry narration and tool chips retain candidate + seat identity."""
    import backend.qc.engine as engine

    monkeypatch.setattr(engine.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(settings, "QC_MAX_WORKERS", 1)
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    retryable = anthropic.APIConnectionError(message="reset", request=request)
    title = "Verifier retry candidate"
    enriched = qc_verdict_response(True)
    enriched.events = [
        block_start_event(0, "server_tool_use", "web_search"),
        input_json_delta_event(0, '{"query":"seat retry query"}'),
        block_stop_event(0),
        block_start_event(1, "server_tool_use", "web_fetch"),
        input_json_delta_event(1, '{"url":"https://example.test/seat"}'),
        block_stop_event(1),
        *_synthesize_events(enriched.content, []),
    ]
    scripts = _scripts(
        code_compliance=[
            qc_findings_response(
                "code_compliance",
                findings=[_finding(title)],
            )
        ]
    )
    scripts[title] = [retryable, enriched, qc_verdict_response(True)]
    events: list[dict] = []
    _run(scripts, events)

    seat = _events_for(
        events,
        candidate_id="candidate-1",
        reviewer_index=1,
    )
    types = [event["type"] for event in seat]
    assert types[0] == "verifier_started"
    assert types.index("verifier_retry") < types.index("verifier_search")
    assert types.index("verifier_search") < types.index("verifier_fetch")
    assert types[-1] == "verifier_complete"
    assert _events_for(
        events,
        type="verifier_retry",
        candidate_id="candidate-1",
        reviewer_index=1,
    ) == [
        {
            "type": "verifier_retry",
            "candidate_id": "candidate-1",
            "reviewer_index": 1,
            "attempt": 1,
            "max_attempts": 3,
            "reason": "connection",
            "backoff_s": 5.0,
        }
    ]
    assert next(event for event in seat if event["type"] == "verifier_search")[
        "query"
    ] == "seat retry query"
    assert next(event for event in seat if event["type"] == "verifier_fetch")[
        "url"
    ] == "https://example.test/seat"


def test_parallel_lens_activity_interleaves_without_breaking_worker_order() -> None:
    """Workers may interleave globally, but each worker keeps its subsequence."""
    first_seen = threading.Event()
    second_seen = threading.Event()

    class FirstEvents:
        def __iter__(self):
            yield block_start_event(0, "thinking")
            first_seen.set()
            second_seen.wait(timeout=2)
            yield block_start_event(1, "tool_use", "submit_qc_findings")

    class SecondEvents:
        def __iter__(self):
            first_seen.wait(timeout=2)
            yield block_start_event(0, "thinking")
            second_seen.set()
            yield block_start_event(1, "tool_use", "submit_qc_findings")

    first = qc_findings_response("completeness", findings=[])
    first.events = FirstEvents()
    second = qc_findings_response("coordination_consistency", findings=[])
    second.events = SecondEvents()
    events: list[dict] = []
    _run(
        _scripts(
            completeness=[first],
            coordination_consistency=[second],
        ),
        events,
    )

    first_activity = _events_for(
        events, type="lens_activity", lens_id="completeness", kind="thinking"
    )[0]
    second_activity = _events_for(
        events,
        type="lens_activity",
        lens_id="coordination_consistency",
        kind="thinking",
    )[0]
    first_terminal = _events_for(
        events, type="lens_complete", lens_id="completeness"
    )[0]
    second_terminal = _events_for(
        events, type="lens_complete", lens_id="coordination_consistency"
    )[0]
    assert events.index(first_activity) < events.index(second_activity)
    assert events.index(second_activity) < events.index(first_terminal)
    assert events.index(second_activity) < events.index(second_terminal)
    for lens_id in ("completeness", "coordination_consistency"):
        worker = _events_for(events, lens_id=lens_id)
        types = [event["type"] for event in worker]
        assert types[0] == "lens_started"
        assert types[-1] == "lens_complete"


def test_shared_invalid_request_accounts_for_every_unstarted_verifier_seat(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "QC_MAX_WORKERS", 1)
    findings = [_finding(f"Shared failure {index}") for index in range(3)]
    scripts = _scripts(
        coordination_consistency=[
            qc_findings_response(
                "coordination_consistency",
                findings=findings,
            )
        ]
    )
    # Only the first seat reaches the provider. The breaker synthesizes every
    # queued seat after the pre-response invalid request.
    scripts[findings[0]["title"]] = [bad_request("invalid verifier request")]
    events: list[dict] = []
    result = _run(scripts, events)

    assert result.execution_status == "partial"
    assert len(result.inconclusive) == 3
    assert len(_events_for(events, type="verifier_started")) == 1
    completions = _events_for(events, type="verifier_complete")
    assert len(completions) == 6
    assert all(event["status"] == "failed" for event in completions)
    assert all("upholds" not in event for event in completions)
    candidate_events = _events_for(events, type="candidate_complete")
    assert len(candidate_events) == 3
    assert all(event["outcome"] == "inconclusive" for event in candidate_events)
    terminal = _events_for(events, type="verification_complete")[0]
    assert terminal == {
        "type": "verification_complete",
        "total_candidates": 3,
        "total_seats": 6,
        "completed_seats": 0,
        "upheld": 0,
        "refuted": 0,
        "disputed": 0,
        "inconclusive": 3,
    }


def test_stop_after_roster_cancels_seats_and_keeps_panels_inconclusive(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "QC_MAX_WORKERS", 1)
    title = "Cancelled panel"
    scripts = _scripts(
        completeness=[
            qc_findings_response("completeness", findings=[_finding(title)])
        ]
    )
    stop = threading.Event()
    events: list[dict] = []

    def sink(event: dict) -> None:
        events.append(event)
        if event["type"] == "verification_started":
            stop.set()

    result = _run_client(
        SequencedFakeClient(scripts),
        events,
        event_sink=sink,
        should_stop=stop.is_set,
    )

    assert result.execution_status == "partial"
    assert len(result.inconclusive) == 1
    completions = _events_for(events, type="verifier_complete")
    assert len(completions) == 2
    assert all(event["status"] == "cancelled" for event in completions)
    assert _events_for(events, type="candidate_complete")[0][
        "outcome"
    ] == "inconclusive"
    assert _events_for(events, type="verification_complete")[0][
        "completed_seats"
    ] == 0
    assert _events_for(events, type="validation_progress") == []
