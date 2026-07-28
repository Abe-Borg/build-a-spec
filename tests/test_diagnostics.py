"""Developer-tools diagnostics: logging, crash capture, endpoints, bundle.

Hermetic like everything else: ``conftest.py`` sets ``BUILD_A_SPEC_LOG=0``
and ``BUILD_A_SPEC_TRACE=0`` for the suite; these tests opt back in with
tmp directories through the two fixtures below and tear the process-global
logging state down again (``diagnostics.reset_for_tests``).
"""
from __future__ import annotations

import json
import logging
import os
import time
import zipfile
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from backend import diagnostics, sessions
from backend.app import create_app
from backend.tracing import capture, config as trace_config
from backend.tracing import recorder as recorder_module
from backend.tracing.recorder import set_recorder
from backend.tracing.redaction import scrub_data


@pytest.fixture
def log_env(monkeypatch, tmp_path):
    """Opt logging back in against a tmp dir; fully torn down after."""
    monkeypatch.setenv(diagnostics.ENV_LOG, "1")
    monkeypatch.delenv(diagnostics.ENV_LOG_LEVEL, raising=False)
    log_dir = tmp_path / "logs"
    monkeypatch.setenv(diagnostics.ENV_LOG_DIR, str(log_dir))
    yield log_dir
    diagnostics.reset_for_tests()


@pytest.fixture
def trace_env(monkeypatch, tmp_path):
    """Opt tracing back in against a tmp dir (the test_tracing pattern)."""
    monkeypatch.setenv(trace_config.ENV_TRACE, "1")
    trace_dir = tmp_path / "traces"
    monkeypatch.setenv(trace_config.ENV_TRACE_DIR, str(trace_dir))
    set_recorder(None)
    yield trace_dir
    rec = recorder_module.get_recorder()
    if rec is not None:
        rec.stop()
    set_recorder(None)


def _wait_events(predicate, timeout=3.0):
    """Poll the current recorder's events.jsonl until predicate holds.

    Per-line flush makes the file readable while the writer thread is
    live, but the queue drains asynchronously — hence the poll.
    """
    rec = recorder_module.get_recorder()
    assert rec is not None, "expected a live recorder"
    path = rec.trace_dir / "events.jsonl"
    deadline = time.monotonic() + timeout
    events: list[dict] = []
    while time.monotonic() < deadline:
        if path.exists():
            events = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if predicate(events):
                return events
        time.sleep(0.02)
    return events


# --- logging bootstrap ------------------------------------------------------


def test_init_logging_writes_to_the_configured_dir_and_is_idempotent(log_env):
    path = diagnostics.init_logging(force=True)
    assert path is not None and path.parent == log_env
    logging.getLogger("buildaspec.test").info("hello file")
    assert path.exists()
    assert "hello file" in path.read_text(encoding="utf-8")

    handler_count = sum(
        1
        for h in logging.getLogger().handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    )
    assert diagnostics.init_logging() == path  # idempotent
    assert (
        sum(
            1
            for h in logging.getLogger().handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        )
        == handler_count
    )


def test_log_env_off_and_level_knobs(monkeypatch, tmp_path):
    try:
        monkeypatch.setenv(diagnostics.ENV_LOG, "0")
        assert diagnostics.init_logging(force=True) is None
        assert diagnostics.current_log_file() is None
        assert not (tmp_path / "logs").exists()

        monkeypatch.setenv(diagnostics.ENV_LOG, "1")
        monkeypatch.setenv(diagnostics.ENV_LOG_DIR, str(tmp_path / "logs"))
        monkeypatch.setenv(diagnostics.ENV_LOG_LEVEL, "INFO")
        assert diagnostics.configured_level() == logging.INFO
        monkeypatch.setenv(diagnostics.ENV_LOG_LEVEL, "garbage")
        assert diagnostics.configured_level() == logging.DEBUG
    finally:
        diagnostics.reset_for_tests()


def test_unclean_shutdown_marker_round_trip(log_env, caplog):
    diagnostics.init_logging(force=True)
    marker_path = log_env / diagnostics.RUN_MARKER_FILENAME
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["clean"] is False and marker["pid"] == os.getpid()

    diagnostics.mark_clean_shutdown()
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["clean"] is True and "ended_at" in marker

    # Simulate a crashed previous run: a marker left with clean=False.
    marker_path.write_text(
        json.dumps({"clean": False, "pid": 4242, "started_at": 1000.0}),
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="buildaspec"):
        diagnostics.init_logging(force=True)
    assert "did not shut down cleanly" in caplog.text
    assert "4242" in caplog.text


def test_usage_count_keys_survive_the_scrub():
    """The ported bare-``token`` key pattern redacted every usage count out
    of every span — counts are not secrets; auth tokens still are."""
    scrubbed = scrub_data(
        {
            "usage": {"input_tokens": 10, "output_tokens": 5, "thinking_tokens": 2},
            "auth_token": "abc",
            "api_key": "whatever",
        }
    )
    assert scrubbed["usage"] == {
        "input_tokens": 10,
        "output_tokens": 5,
        "thinking_tokens": 2,
    }
    assert scrubbed["auth_token"] == "<redacted>"
    assert scrubbed["api_key"] == "<redacted>"


# --- request middleware + exception handlers --------------------------------


def test_request_middleware_logs_and_traces_requests(trace_env, caplog):
    client = TestClient(create_app())
    with caplog.at_level(logging.DEBUG, logger="buildaspec.api"):
        client.get("/api/doc")
        client.get("/api/health")
    assert "GET /api/doc -> 200" in caplog.text

    events = _wait_events(
        lambda evs: any(
            e["type"] == "api_request" and e["path"] == "/api/doc"
            for e in evs
        )
    )
    doc_requests = [
        e
        for e in events
        if e["type"] == "api_request" and e["path"] == "/api/doc"
    ]
    assert doc_requests and doc_requests[0]["status"] == 200
    assert isinstance(doc_requests[0]["ms"], int)
    # Poll endpoints stay out of the trace (quiet list).
    assert not any(
        e["type"] == "api_request" and e["path"] == "/api/health"
        for e in events
    )
    # Boot itself is recorded.
    assert any(e["type"] == "server_started" for e in events)


def test_catch_all_handler_returns_the_error_idiom_and_logs(
    monkeypatch, caplog
):
    # Flag-gated so the conftest teardown (which also calls get_session
    # while this patch is still active) is unaffected after the test body.
    state = {"boom": True}
    real_get_session = sessions.get_session

    def maybe_boom():
        if state["boom"]:
            raise RuntimeError("kaboom")
        return real_get_session()

    monkeypatch.setattr("backend.sessions.get_session", maybe_boom)
    client = TestClient(create_app(), raise_server_exceptions=False)
    with caplog.at_level(logging.WARNING, logger="buildaspec.api"):
        resp = client.get("/api/doc")
    state["boom"] = False
    assert resp.status_code == 500
    data = resp.json()
    assert data["ok"] is False
    assert data["code"] == "internal_error"
    assert "kaboom" in data["error"]
    # The traceback is in the log, not just the response.
    assert "Unhandled error on GET /api/doc" in caplog.text
    assert "RuntimeError: kaboom" in caplog.text


def test_422_translates_to_the_error_idiom_without_echoing_input(caplog):
    client = TestClient(create_app())
    with caplog.at_level(logging.DEBUG, logger="buildaspec.api"):
        resp = client.post(
            "/api/key",
            json={"api_key": {"v": "sk-ant-fake-abcdef1234567890"}},
        )
    assert resp.status_code == 422
    data = resp.json()
    assert data["ok"] is False and data["code"] == "validation_error"
    # pydantic v2 errors() carry the submitted input — the handler must
    # build its message from loc/msg only, or /api/key echoes the key.
    assert "sk-ant-fake" not in resp.text
    assert "sk-ant-fake" not in caplog.text


# --- diagnostics endpoints --------------------------------------------------


def test_diagnostics_snapshot_shape_and_no_key_material():
    client = TestClient(create_app())
    resp = client.get("/api/diagnostics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    for block in ("app", "tracing", "logging", "key", "workspace", "session", "usage"):
        assert block in data
    assert data["app"]["name"] and data["app"]["version"]
    assert data["key"]["present"] is True and data["key"]["source"] == "env"
    assert data["key"]["masked"].startswith("…")
    assert "test-key-hermetic" not in resp.text
    assert data["workspace"]["scope"] == "original"
    assert data["session"]["history_len"] == 0
    assert data["session"]["doc_empty"] is True
    assert data["usage"]["turns"] == 0

    # Suite defaults: logging and tracing are off — grace, not errors.
    assert client.get("/api/diagnostics/log").json()["enabled"] is False
    assert client.get("/api/diagnostics/activity").json()["enabled"] is False


def test_log_tail_bounds_and_missing_file_grace(log_env, monkeypatch):
    diagnostics.init_logging(force=True)
    log = logging.getLogger("buildaspec.test")
    for i in range(20):
        log.info("line %d", i)

    out = diagnostics.tail_log(5)
    assert out["enabled"] is True and len(out["lines"]) == 5
    assert out["lines"][-1].endswith("line 19")
    assert out["size_bytes"] > 0

    assert len(diagnostics.tail_log(0)["lines"]) == 1  # clamps low
    assert len(diagnostics.tail_log(10**9)["lines"]) >= 20  # clamps high
    assert diagnostics.tail_log("garbage")["lines"]  # type-lenient

    # Missing file: an empty log dir (delay=True — no emit, no file).
    monkeypatch.setenv(diagnostics.ENV_LOG_DIR, str(log_env.parent / "empty"))
    out = diagnostics.tail_log(5)
    assert out["enabled"] is True
    assert out["lines"] == [] and out["size_bytes"] == 0


def test_traces_list_newest_first_with_current_flag(trace_env):
    older = trace_env / "session-aaaa1111-1000"
    older.mkdir(parents=True)
    (older / "run.json").write_text(
        json.dumps({"run_id": older.name, "started_at": 1000.0, "ended_at": 1500.0})
    )
    (older / "events.jsonl").write_text('{"ts": 1000.0}\n')
    newer = trace_env / "session-bbbb2222-2000"
    newer.mkdir()
    (newer / "run.json").write_text(
        json.dumps({"run_id": newer.name, "started_at": 2000.0, "ended_at": None})
    )
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))

    out = diagnostics.list_trace_runs()
    assert out["root"] == str(trace_env)
    assert [r["run_id"] for r in out["runs"]] == [newer.name, older.name]
    assert out["runs"][1]["files"]["events.jsonl"] > 0
    assert out["runs"][1]["started_at"] == 1000.0
    assert not any(r["current"] for r in out["runs"])

    # A live recorder marks its own run as current.
    capture.app_event("server_started")
    out = diagnostics.list_trace_runs()
    current = [r for r in out["runs"] if r["current"]]
    assert len(current) == 1
    assert current[0]["run_id"] == recorder_module.get_recorder().run_id


def test_activity_endpoint_returns_recent_events_and_open_spans(trace_env):
    capture.app_event("session_reset", module_id="generic", had_content=False)
    handle = capture.turn_start(model="claude-sonnet-5", history_len=0)
    try:
        _wait_events(
            lambda evs: any(e["type"] == "session_reset" for e in evs)
        )
        out = diagnostics.read_recent_trace_events(tail=50)
        assert out["enabled"] is True
        assert any(e["type"] == "session_reset" for e in out["events"])
        assert any(s["kind"] == "turn" for s in out["spans"])
    finally:
        capture.turn_end(handle)


def test_bundle_zip_contains_snapshot_log_and_trace_and_never_the_key(
    log_env, trace_env
):
    diagnostics.init_logging(force=True)
    client = TestClient(create_app())
    client.get("/api/doc")  # an api_request event + log lines

    resp = client.get("/api/diagnostics/bundle")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert "buildaspec-diagnostics-" in resp.headers.get(
        "content-disposition", ""
    )
    assert resp.headers.get("cache-control") == "no-store"

    zf = zipfile.ZipFile(BytesIO(resp.content))
    names = zf.namelist()
    assert "snapshot.json" in names
    assert f"logs/{diagnostics.LOG_FILENAME}" in names
    assert any(
        n.startswith("traces/") and n.endswith("events.jsonl") for n in names
    )
    # The hermetic fake key must not appear in ANY member, decompressed.
    for name in names:
        assert b"test-key-hermetic" not in zf.read(name), name
    snapshot = json.loads(zf.read("snapshot.json"))
    assert snapshot["app"]["version"]
    assert snapshot["key"]["masked"].startswith("…")


def test_client_event_collector_logs_traces_and_bounds(
    log_env, trace_env, caplog
):
    diagnostics.init_logging(force=True)
    client = TestClient(create_app())

    with caplog.at_level(logging.ERROR, logger="buildaspec.client"):
        resp = client.post(
            "/api/diagnostics/client-event",
            json={
                "kind": "error",
                "message": "TypeError: x is undefined",
                "stack": "at App.tsx:1:1",
                "source": "index.js:10",
            },
        )
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert "TypeError: x is undefined" in caplog.text
    events = _wait_events(
        lambda evs: any(e["type"] == "client_error" for e in evs)
    )
    client_events = [e for e in events if e["type"] == "client_error"]
    assert client_events and client_events[0]["kind"] == "error"

    # Oversized → 400 in the error idiom.
    resp = client.post(
        "/api/diagnostics/client-event",
        json={"kind": "error", "message": "x" * 33_000},
    )
    assert resp.status_code == 400 and resp.json()["ok"] is False

    # Unknown kind coerces rather than rejects (a reporter must not fail).
    resp = client.post(
        "/api/diagnostics/client-event",
        json={"kind": "weird", "message": "m"},
    )
    assert resp.status_code == 200

    # A malformed payload gets the translated 422 idiom.
    resp = client.post("/api/diagnostics/client-event", json={"kind": "error"})
    assert resp.status_code == 422 and resp.json()["code"] == "validation_error"


# --- capture call sites -----------------------------------------------------


def test_capture_sites_leave_events(trace_env):
    client = TestClient(create_app())
    resp = client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {"action": "add_article", "target_id": "pt1", "text": "SCOPE"}
            ]
        },
    )
    assert resp.status_code == 200
    client.post("/api/doc/undo")
    client.get("/api/project/save")
    client.post("/api/session/reset")

    events = _wait_events(
        lambda evs: {
            "doc_edit",
            "doc_history",
            "project_save",
            "session_reset",
        }
        <= {e["type"] for e in evs}
    )
    types = {e["type"] for e in events}
    assert {"doc_edit", "doc_history", "project_save", "session_reset"} <= types

    edit = next(e for e in events if e["type"] == "doc_edit")
    assert edit["ops"] == 1 and edit["ok"] is True
    assert edit["actions"] == ["add_article"]
    undo = next(e for e in events if e["type"] == "doc_history")
    assert undo["action"] == "undo" and undo["ok"] is True
    save = next(e for e in events if e["type"] == "project_save")
    assert save["bytes"] > 0 and save["filename"].startswith("buildaspec-")
    reset = next(e for e in events if e["type"] == "session_reset")
    assert reset["had_content"] is False  # the undo returned it to empty


def test_round_end_and_prompt_refs_are_recorded_per_turn(
    monkeypatch, trace_env
):
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
            text_turn(["Second turn."]),
        ]
    )
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    client = TestClient(create_app())
    client.post("/api/chat", json={"message": "go"})
    client.post("/api/chat", json={"message": "again"})

    events = _wait_events(
        lambda evs: sum(1 for e in evs if e["type"] == "round_end") >= 3
        and sum(1 for e in evs if e["type"] == "prompt_refs") >= 2
    )
    rounds = [e for e in events if e["type"] == "round_end"]
    # Turn 1 = two rounds (tool_use then end_turn); turn 2 = one round.
    assert len(rounds) == 3
    assert rounds[0]["round"] == 0 and rounds[0]["stop_reason"] == "tool_use"
    assert rounds[0]["tool_uses"] == 1
    assert rounds[1]["round"] == 1 and rounds[1]["stop_reason"] == "end_turn"
    assert isinstance(rounds[0]["ms"], int)

    prompt_events = [e for e in events if e["type"] == "prompt_refs"]
    assert len(prompt_events) == 2
    assert "ref" in prompt_events[0]["system"]

    rec = recorder_module.get_recorder()
    prompts = [
        json.loads(line)
        for line in (rec.trace_dir / "prompts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    kinds = [p["kind"] for p in prompts]
    assert {"system", "project_context", "user"} <= set(kinds)
    # The stable system block is hash-deduped: one entry across two turns.
    assert kinds.count("system") == 1


def test_workspace_conflict_event_from_the_lease_middleware(
    monkeypatch, trace_env
):
    client = TestClient(create_app())

    def raiser(expected_workspace_id=None):
        raise sessions.WorkspaceConflictError("the workspace moved")

    monkeypatch.setattr("backend.sessions.active_write", raiser)
    resp = client.post(
        "/api/qc/dismiss", json={"finding_id": "x", "reason": "r"}
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "stale_workspace"
    events = _wait_events(
        lambda evs: any(e["type"] == "workspace_conflict" for e in evs)
    )
    conflict = next(e for e in events if e["type"] == "workspace_conflict")
    assert conflict["path"] == "/api/qc/dismiss"
