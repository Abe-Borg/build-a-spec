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
