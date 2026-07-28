"""Runner/API replay coverage for the expanded Final QC SSE vocabulary."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend import sessions, settings
from backend.app import create_app
from backend.qc.engine import QCResult
from backend.qc.runner import QCRunner
from backend.spec_doc.model import DocumentStore
from backend.spec_modules import DEFAULT_MODULE


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
        ]
    )
    store.commit_turn()
    return store


def _start(runner: QCRunner) -> None:
    store = _store()
    assert runner.start(
        section=store.doc,
        profile=None,
        module=DEFAULT_MODULE,
        client=object(),
        model=settings.QC_MODEL,
        max_tokens=settings.QC_MAX_TOKENS,
        version_index=store.index,
    )
    assert runner._thread is not None
    runner._thread.join(timeout=5)
    assert not runner._thread.is_alive()


def _parse_sse(body: str) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def _expanded_events() -> list[dict]:
    return [
        {"type": "qc_started", "run_id": "engine-overwrites-this"},
        {"type": "lens_started", "lens_id": "completeness"},
        {
            "type": "lens_activity",
            "lens_id": "completeness",
            "kind": "thinking",
        },
        {
            "type": "lens_search",
            "lens_id": "completeness",
            "query": "query",
        },
        {
            "type": "lens_fetch",
            "lens_id": "completeness",
            "url": "https://example.test/source",
        },
        {
            "type": "lens_retry",
            "lens_id": "completeness",
            "attempt": 1,
        },
        {"type": "lens_complete", "lens_id": "completeness"},
        {
            "type": "verification_started",
            "candidates": [{"candidate_id": "candidate-1"}],
        },
        {"type": "verify_progress", "done": 0, "total": 1},
        {
            "type": "verifier_started",
            "candidate_id": "candidate-1",
            "reviewer_index": 1,
        },
        {
            "type": "verifier_activity",
            "candidate_id": "candidate-1",
            "reviewer_index": 1,
            "kind": "thinking",
        },
        {
            "type": "verifier_search",
            "candidate_id": "candidate-1",
            "reviewer_index": 1,
            "query": "check",
        },
        {
            "type": "verifier_fetch",
            "candidate_id": "candidate-1",
            "reviewer_index": 1,
            "url": "https://example.test/check",
        },
        {
            "type": "verifier_retry",
            "candidate_id": "candidate-1",
            "reviewer_index": 1,
            "attempt": 1,
        },
        {
            "type": "verifier_complete",
            "candidate_id": "candidate-1",
            "reviewer_index": 1,
            "status": "completed",
            "upholds": True,
        },
        {
            "type": "candidate_complete",
            "candidate_id": "candidate-1",
            "outcome": "upheld",
        },
        {"type": "verify_progress", "done": 1, "total": 1},
        {"type": "verification_complete", "upheld": 1},
        {"type": "validation_started", "total": 1},
        {
            "type": "validation_progress",
            "candidate_id": "candidate-1",
            "outcome": "manual",
        },
        {"type": "validation_complete", "total": 1, "done": 1},
    ]


def test_runner_and_api_replay_every_live_event_then_exact_stream_end(
    monkeypatch,
) -> None:
    emitted = _expanded_events()

    def fake_run(*_args, run_id: str, event_sink, **_kwargs) -> QCResult:
        for event in emitted:
            event_sink(event)
        return QCResult(run_id=run_id, execution_status="complete")

    monkeypatch.setattr("backend.qc.runner.run_final_qc", fake_run)
    runner = QCRunner()
    _start(runner)
    assert runner.status == "complete"

    replay = list(runner.sse_events(poll_interval=0, timeout_s=1))
    expected_types = [event["type"] for event in emitted] + ["qc_complete"]
    assert [event["type"] for event in replay[:-1]] == expected_types
    assert [event["seq"] for event in replay[:-1]] == list(
        range(len(replay) - 1)
    )
    assert replay[-1] == {"type": "stream_end", "status": "complete"}

    # The FastAPI endpoint relays the same append-only log unchanged.
    sessions.get_session().qc = runner
    response = TestClient(create_app()).get("/api/qc/stream")
    assert response.status_code == 200
    api_replay = _parse_sse(response.text)
    assert api_replay == replay
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"


def test_bound_stream_is_superseded_without_leaking_replacement_events(
    monkeypatch,
) -> None:
    calls = 0

    def fake_run(*_args, run_id: str, event_sink, **_kwargs) -> QCResult:
        nonlocal calls
        calls += 1
        event_sink({"type": "old_only" if calls == 1 else "new_only"})
        return QCResult(run_id=run_id, execution_status="complete")

    monkeypatch.setattr("backend.qc.runner.run_final_qc", fake_run)
    runner = QCRunner()
    _start(runner)
    old_run_id = runner.latest_attempt_run_id
    old_stream = runner.sse_events(poll_interval=0, timeout_s=1)

    _start(runner)
    assert runner.latest_attempt_run_id != old_run_id
    assert any(event["type"] == "new_only" for event in runner.events)

    # Ownership was captured when sse_events() was called, before replacement.
    # The old response gets one sentinel and no frame from the new run.
    assert list(old_stream) == [
        {
            "type": "stream_end",
            "status": "superseded",
            "run_id": old_run_id,
        }
    ]
    fresh = list(runner.sse_events(poll_interval=0, timeout_s=1))
    assert [event["type"] for event in fresh] == [
        "new_only",
        "qc_complete",
        "stream_end",
    ]
