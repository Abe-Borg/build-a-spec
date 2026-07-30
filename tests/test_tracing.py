"""Tracing tests: recorder on/off, JSONL output, redaction, capture hooks."""
from __future__ import annotations

import json

from backend.tracing import capture, config, recorder as recorder_module
from backend.tracing.recorder import TraceRecorder, set_recorder
from backend.tracing.redaction import scrub_data


def _read_jsonl(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_recorder_writes_spans_events_and_run_meta(tmp_path):
    rec = TraceRecorder(
        run_id="run-test",
        trace_dir=tmp_path / "run-test",
        capture_level="default",
        app_version="0.5.0",
    )
    rec.start(model="claude-sonnet-5")
    with rec.span("turn", "turn #1", inputs={"model": "claude-sonnet-5"}) as handle:
        rec.add_event(handle, "tool_dispatch", ops=3, ok=True)
        ref = rec.prompt_ref("system", "the stable prompt text")
        assert "ref" in ref
        # Dedup: same text returns the same hash, writes once.
        assert rec.prompt_ref("system", "the stable prompt text") == ref
    rec.stop()

    run_dir = tmp_path / "run-test"
    spans = _read_jsonl(run_dir / "spans.jsonl")
    events = _read_jsonl(run_dir / "events.jsonl")
    prompts = _read_jsonl(run_dir / "prompts.jsonl")
    meta = json.loads((run_dir / "run.json").read_text())

    assert spans[0]["kind"] == "turn" and spans[0]["status"] == "ok"
    assert events[0]["type"] == "tool_dispatch" and events[0]["ops"] == 3
    assert events[0]["span_id"] == spans[0]["span_id"]
    assert len(prompts) == 1
    assert meta["run_id"] == "run-test" and meta["ended_at"] is not None


def test_nested_spans_inherit_parent_and_errors_close_spans(tmp_path):
    rec = TraceRecorder(
        run_id="run-nest", trace_dir=tmp_path / "n", capture_level="default"
    )
    rec.start()
    try:
        with rec.span("session", "outer"):
            with rec.span("turn", "inner"):
                raise RuntimeError("boom")
    except RuntimeError:
        pass
    rec.stop()
    spans = {s["name"]: s for s in _read_jsonl(tmp_path / "n" / "spans.jsonl")}
    assert spans["inner"]["parent_span_id"] == spans["outer"]["span_id"]
    assert spans["inner"]["status"] == "error"
    assert "boom" in spans["inner"]["error"]
    # The outer span closed as error too (exception propagated through it).
    assert spans["outer"]["status"] == "error"


def test_redaction_scrubs_credentials_not_content():
    data = {
        "api_key": "sk-ant-abc123def456ghi",
        "text": "Comply with NFPA 13-2025 throughout.",
        "nested": {"authorization": "whatever", "note": "Bearer abc123def456ghij"},
    }
    scrubbed = scrub_data(data)
    assert scrubbed["api_key"] == "<redacted>"
    assert scrubbed["nested"]["authorization"] == "<redacted>"
    assert scrubbed["nested"]["note"] == "<redacted>"
    # Draft content passes through untouched.
    assert scrubbed["text"] == "Comply with NFPA 13-2025 throughout."


def test_capture_hooks_are_noops_when_disabled(monkeypatch):
    monkeypatch.setenv(config.ENV_TRACE, "0")
    set_recorder(None)
    handle = capture.turn_start(model="m", history_len=0)
    assert handle is None
    capture.turn_end(handle)  # must not raise
    capture.tool_dispatch(None, ops=1, ok=True)
    capture.import_event(blocks=1, warnings=0, tracked_changes=False)
    assert recorder_module.get_recorder() is None


def test_capture_hooks_record_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv(config.ENV_TRACE, "1")
    monkeypatch.setenv(config.ENV_TRACE_DIR, str(tmp_path))
    set_recorder(None)
    try:
        handle = capture.turn_start(model="claude-sonnet-5", history_len=0)
        assert handle is not None
        capture.tool_dispatch(handle, ops=2, ok=True)
        capture.turn_end(handle, stop_reason="end_turn", doc_changed=True)

        rec = recorder_module.get_recorder()
        assert rec is not None
        rec.stop()

        run_dirs = list(tmp_path.iterdir())
        assert len(run_dirs) == 1
        spans = _read_jsonl(run_dirs[0] / "spans.jsonl")
        events = _read_jsonl(run_dirs[0] / "events.jsonl")
        assert spans[0]["kind"] == "turn"
        assert spans[0]["outputs"]["doc_changed"] is True
        assert events[0]["type"] == "tool_dispatch"
    finally:
        set_recorder(None)


def test_research_and_qc_progress_events_reach_the_trace(monkeypatch, tmp_path):
    """The sink event's "type" key must not collide with add_event's.

    Every research/QC sink event carries a "type" key; passing the dict as
    **kwargs to ``add_event(handle, type, ...)`` raised a TypeError that the
    never-raise hooks swallowed — so no progress event ever reached a trace.
    The hooks now rename it to ``event_type``.
    """
    monkeypatch.setenv(config.ENV_TRACE, "1")
    monkeypatch.setenv(config.ENV_TRACE_DIR, str(tmp_path))
    set_recorder(None)
    try:
        research_handle = capture.research_start(project="Ashburn DC", dimensions=4)
        assert research_handle is not None
        capture.research_event(
            research_handle,
            {
                "type": "dimension_search",
                "dimension_id": "governing_codes",
                "query": "virginia statewide fire prevention code edition",
            },
        )
        capture.research_end(research_handle, status="complete", items=1)

        qc_handle = capture.qc_start(lenses=5)
        assert qc_handle is not None
        capture.qc_event(qc_handle, {"type": "lens_complete", "lens_id": "completeness"})
        capture.qc_end(qc_handle, status="complete", findings=0)

        rec = recorder_module.get_recorder()
        assert rec is not None
        rec.stop()

        run_dirs = list(tmp_path.iterdir())
        assert len(run_dirs) == 1
        events = _read_jsonl(run_dirs[0] / "events.jsonl")
        research_events = [e for e in events if e["type"] == "research_progress"]
        assert len(research_events) == 1
        assert research_events[0]["event_type"] == "dimension_search"
        assert research_events[0]["dimension_id"] == "governing_codes"
        assert "fire prevention" in research_events[0]["query"]
        qc_events = [e for e in events if e["type"] == "qc_progress"]
        assert len(qc_events) == 1
        assert qc_events[0]["event_type"] == "lens_complete"
        assert qc_events[0]["lens_id"] == "completeness"
    finally:
        set_recorder(None)


def test_the_research_span_records_which_coverage_never_completed(
    monkeypatch, tmp_path
):
    """A partial run closes its span with ``status="complete"``.

    So without the incomplete list, a trace could not answer "which coverage
    failed" — the exact question a support bundle exists for. Sanitized kinds
    only: the dimension's own error message can carry provider exception text.
    """
    monkeypatch.setenv(config.ENV_TRACE, "1")
    monkeypatch.setenv(config.ENV_TRACE_DIR, str(tmp_path))
    set_recorder(None)
    try:
        handle = capture.research_start(project="Ashburn DC", dimensions=4)
        capture.research_end(
            handle,
            status="complete",
            items=7,
            incomplete_dimensions=[
                {
                    "dimension_id": "ahj_requirements",
                    "title": "AHJ requirements",
                    "error_kind": "rate_limit",
                }
            ],
        )
        rec = recorder_module.get_recorder()
        assert rec is not None
        rec.stop()

        run_dirs = list(tmp_path.iterdir())
        spans = _read_jsonl(run_dirs[0] / "spans.jsonl")
        research = [s for s in spans if s.get("kind") == "research"]
        assert len(research) == 1
        outputs = research[0]["outputs"]
        assert outputs["status"] == "complete" and outputs["items"] == 7
        assert outputs["incomplete_dimensions"] == [
            {
                "dimension_id": "ahj_requirements",
                "title": "AHJ requirements",
                "error_kind": "rate_limit",
            }
        ]

        # A fully complete run says nothing extra — the key is absent, not
        # an empty list, so a complete span stays byte-identical to before.
        set_recorder(None)
        clean = capture.research_start(project="Ashburn DC", dimensions=4)
        capture.research_end(clean, status="complete", items=7)
        rec = recorder_module.get_recorder()
        assert rec is not None
        rec.stop()
        newest = max(tmp_path.iterdir(), key=lambda p: p.stat().st_mtime)
        spans = _read_jsonl(newest / "spans.jsonl")
        research = [s for s in spans if s.get("kind") == "research"]
        assert "incomplete_dimensions" not in research[0]["outputs"]
    finally:
        set_recorder(None)


def test_turns_trace_end_to_end_through_the_engine(monkeypatch, tmp_path):
    """A real (fake-client) chat turn produces a turn span + tool event."""
    monkeypatch.setenv(config.ENV_TRACE, "1")
    monkeypatch.setenv(config.ENV_TRACE_DIR, str(tmp_path))
    set_recorder(None)
    try:
        from fastapi.testclient import TestClient

        from backend.app import create_app
        from tests.fakes import FakeClient, text_turn, tool_turn

        fake = FakeClient(
            [
                tool_turn(
                    ["Drafting."],
                    {
                        "edits": [
                            {
                                "action": "add_article",
                                "target_id": "pt1",
                                "text": "SUMMARY",
                            }
                        ]
                    },
                ),
                text_turn(["Done."]),
            ]
        )
        monkeypatch.setattr(
            "backend.llm.conversation.get_client", lambda: fake
        )
        client = TestClient(create_app())
        client.post("/api/chat", json={"message": "go"})

        rec = recorder_module.get_recorder()
        assert rec is not None
        rec.stop()
        run_dir = next(tmp_path.iterdir())
        spans = _read_jsonl(run_dir / "spans.jsonl")
        events = _read_jsonl(run_dir / "events.jsonl")
        turn_spans = [s for s in spans if s["kind"] == "turn"]
        assert turn_spans and turn_spans[0]["status"] == "ok"
        assert turn_spans[0]["outputs"]["doc_changed"] is True
        assert any(e["type"] == "tool_dispatch" and e["ok"] for e in events)
    finally:
        set_recorder(None)


def test_viewer_endpoint_serves_the_bundled_html():
    from fastapi.testclient import TestClient

    from backend.app import create_app

    resp = TestClient(create_app()).get("/api/trace/viewer")
    assert resp.status_code == 200
    assert "html" in resp.headers["content-type"]
    assert len(resp.content) > 1000


def test_events_are_readable_before_stop_thanks_to_per_line_flush(tmp_path):
    """Per-line flush: a hard crash must not lose already-recorded events.

    The pre-fix writer only flushed at stop(), so anything short of a clean
    exit lost up to a buffer of trace data — the exact scenario traces
    exist to explain. Poll-read the file while the recorder is still live.
    """
    import time as _time

    rec = TraceRecorder(
        run_id="run-flush", trace_dir=tmp_path / "f", capture_level="default"
    )
    rec.start()
    try:
        rec.add_event(None, "api_request", method="GET", path="/api/doc")
        deadline = _time.monotonic() + 2.0
        events = []
        while _time.monotonic() < deadline:
            path = tmp_path / "f" / "events.jsonl"
            if path.exists():
                events = _read_jsonl(path)
                if events:
                    break
            _time.sleep(0.02)
        assert events and events[0]["type"] == "api_request"
    finally:
        rec.stop()


def test_cross_thread_close_leaves_no_stale_parent(tmp_path):
    """A span closed on another thread must not parent later spans.

    The ported thread-local span stack pushed on the opening thread and
    could only pop on the closing thread — research/QC/audit spans close on
    daemon threads, so the opener's stack kept a dead handle and the next
    span opened on that (reused) thread inherited it as parent. The stack
    is gone; only explicit parents and the span() ContextVar remain.
    """
    import threading

    rec = TraceRecorder(
        run_id="run-x", trace_dir=tmp_path / "x", capture_level="default"
    )
    rec.start()
    try:
        first = rec.open_span("research", "closed elsewhere")
        closer = threading.Thread(target=lambda: rec.close_span(first))
        closer.start()
        closer.join()
        second = rec.open_span("turn", "after the cross-thread close")
        rec.close_span(second)
    finally:
        rec.stop()
    spans = {s["name"]: s for s in _read_jsonl(tmp_path / "x" / "spans.jsonl")}
    assert spans["after the cross-thread close"]["parent_span_id"] is None


def test_prompt_ref_redacts_pasted_credentials_but_keeps_the_prompt(tmp_path):
    """A key pasted into chat must not reach prompts.jsonl (P1 review find).

    prompt_ref is the one write path scrub_data does not cover — and must
    not: whole-string scrubbing would erase the entire prompt over one
    pasted key. Substring redaction keeps the prompt useful.
    """
    rec = TraceRecorder(
        run_id="run-p", trace_dir=tmp_path / "p", capture_level="default"
    )
    rec.start()
    ref = rec.prompt_ref(
        "user", "please use sk-ant-abc123def456ghi789 as the key, thanks"
    )
    assert "ref" in ref
    rec.stop()
    prompts = _read_jsonl(tmp_path / "p" / "prompts.jsonl")
    assert len(prompts) == 1
    assert "sk-ant-" not in prompts[0]["text"]
    assert "<redacted>" in prompts[0]["text"]
    assert prompts[0]["text"].startswith("please use ")

    # Deep mode inlines — same redaction at the same choke point.
    deep = TraceRecorder(
        run_id="run-pd", trace_dir=tmp_path / "pd", capture_level="deep"
    )
    deep.start()
    inline = deep.prompt_ref("user", "key sk-ant-abc123def456ghi789 end")
    deep.stop()
    assert inline == {"inline": "key <redacted> end"}


def test_flush_barrier_makes_enqueued_events_immediately_readable(tmp_path):
    """flush() returns only once prior records are on disk — no polling."""
    rec = TraceRecorder(
        run_id="run-fb", trace_dir=tmp_path / "fb", capture_level="default"
    )
    rec.start()
    try:
        for i in range(25):
            rec.add_event(None, "api_request", path=f"/x/{i}")
        assert rec.flush(timeout=5.0) is True
        events = _read_jsonl(tmp_path / "fb" / "events.jsonl")
        assert len(events) == 25
    finally:
        rec.stop()
    # After stop, flush degrades gracefully instead of hanging.
    assert rec.flush(timeout=0.1) is True


def test_run_meta_records_the_environment(tmp_path):
    rec = TraceRecorder(
        run_id="run-env", trace_dir=tmp_path / "e", capture_level="default"
    )
    rec.start(
        model="claude-sonnet-5",
        environment={"platform": "TestOS-1.0", "python": "3.11.0", "pid": 42},
    )
    rec.stop()
    meta = json.loads((tmp_path / "e" / "run.json").read_text())
    assert meta["environment"]["platform"] == "TestOS-1.0"
    assert meta["environment"]["pid"] == 42

    # A resume against the same dir keeps (and may refresh) the block.
    again = TraceRecorder(
        run_id="run-env", trace_dir=tmp_path / "e", capture_level="default"
    )
    again.start(environment={"platform": "TestOS-2.0"})
    again.stop()
    meta = json.loads((tmp_path / "e" / "run.json").read_text())
    assert meta["environment"]["platform"] == "TestOS-2.0"
    assert len(meta["resumed_at"]) == 1
