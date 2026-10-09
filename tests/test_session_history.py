"""A session's history across app launches (``backend/session_history``).

Owner request (Abraham, 2026-10-09): a section worked on over days or weeks
spans many app launches, and the diagnostics bundle carried only the current
launch in full. These tests pin the four parts of the fix: the session's
identity, the journal its ``.baspec`` carries, the launch index (run marker
and ``run.json`` tags) with retention reading it, and the bundle collecting
every launch of the open session still on disk.

Hermetic like the rest of the suite: logging and tracing opt back in against
tmp directories through the fixtures below (the ``test_diagnostics`` shape).
"""
from __future__ import annotations

import json
import logging
import os
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from backend import diagnostics, session_history, sessions
from backend.app import create_app
from backend.llm.conversation import SessionState
from backend.session_history import (
    JOURNAL_KEY,
    MAX_JOURNAL_VISITS,
    SessionIdentity,
    sanitize_journal,
    valid_session_uid,
)
from backend.spec_doc.project import load_project
from backend.tracing import capture, config as trace_config
from backend.tracing import recorder as recorder_module
from backend.tracing.config import TraceRetentionPolicy
from backend.tracing.recorder import set_recorder
from backend.tracing.retention import prune_trace_runs

DAY = 24 * 60 * 60


@pytest.fixture
def log_env(monkeypatch, tmp_path):
    monkeypatch.setenv(diagnostics.ENV_LOG, "1")
    monkeypatch.delenv(diagnostics.ENV_LOG_LEVEL, raising=False)
    log_dir = tmp_path / "logs"
    monkeypatch.setenv(diagnostics.ENV_LOG_DIR, str(log_dir))
    yield log_dir
    diagnostics.reset_for_tests()


@pytest.fixture
def trace_env(monkeypatch, tmp_path):
    monkeypatch.setenv(trace_config.ENV_TRACE, "1")
    trace_dir = tmp_path / "traces"
    monkeypatch.setenv(trace_config.ENV_TRACE_DIR, str(trace_dir))
    set_recorder(None)
    yield trace_dir
    rec = recorder_module.get_recorder()
    if rec is not None:
        rec.stop()
    set_recorder(None)


def _journal(session: SessionState) -> dict:
    return sessions.project_payload(session)[JOURNAL_KEY]


def _uid(token: str) -> str:
    return token * 32


def _trace_run(
    root,
    run_id: str,
    *,
    ended: float,
    session_uids=(),
    pid: int | None = None,
    payload: str = "",
):
    run_dir = root / run_id
    run_dir.mkdir(parents=True)
    meta = {"run_id": run_id, "started_at": ended - 60, "ended_at": ended}
    if session_uids:
        meta["session_uids"] = list(session_uids)
    if pid is not None:
        meta["environment"] = {"pid": pid}
    (run_dir / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    event = {"ts": ended, "type": "api_request", "path": "/api/x", "status": 500}
    (run_dir / "events.jsonl").write_text(
        json.dumps(event) + "\n" + payload, encoding="utf-8"
    )
    (run_dir / "spans.jsonl").write_text(
        json.dumps({"kind": "turn", "status": "ok"}) + "\n", encoding="utf-8"
    )
    (run_dir / "prompts.jsonl").write_text(
        json.dumps({"ref": "p1", "text": "the drafted prompt"}) + "\n",
        encoding="utf-8",
    )
    os.utime(run_dir, (ended, ended))
    return run_dir


def _log_run(root, token: str, *, ended: float, session_uids=()):
    # No pid: a made-up one can belong to a live process on the machine
    # running the suite, which would (rightly) make the launch "live".
    run_id = "process-" + token * 32
    run_dir = root / run_id
    run_dir.mkdir(parents=True)
    marker = {
        "run_id": run_id,
        "started_at": ended - 60,
        "updated_at": ended,
        "ended_at": ended,
        "clean": True,
        "state": "clean_shutdown",
    }
    if session_uids:
        marker["session_uids"] = list(session_uids)
    (run_dir / diagnostics.RUN_MARKER_FILENAME).write_text(
        json.dumps(marker), encoding="utf-8"
    )
    (run_dir / diagnostics.LOG_FILENAME).write_text(
        "2026-10-01 09:00:00.000 ERROR    buildaspec.test [pid=1] boom "
        f"{token}\n",
        encoding="utf-8",
    )
    for path in (run_dir / diagnostics.LOG_FILENAME, run_dir):
        os.utime(path, (ended, ended))
    return run_dir


# --- identity ---------------------------------------------------------------


def test_a_session_has_a_uid_and_reset_mints_a_new_one():
    session = SessionState()
    first = session.identity.session_uid
    assert valid_session_uid(first)
    assert session.identity.began == "new"
    assert session.identity.created_at is not None

    session.reset()

    assert valid_session_uid(session.identity.session_uid)
    assert session.identity.session_uid != first
    assert session.identity.visits == ()


def test_the_saved_file_carries_the_uid_and_this_visit():
    session = SessionState()
    session.usage.add(
        "interview",
        {"input_tokens": 1200, "output_tokens": 300},
        count_turn=True,
    )

    journal = _journal(session)

    assert journal["schema"] == 1
    assert journal["session_uid"] == session.identity.session_uid
    [visit] = journal["visits"]
    assert visit["visit_id"] == session.identity.visit_id
    assert visit["began"] == "new"
    assert visit["process_run_id"] == diagnostics.process_run_id()
    assert visit["turns"] == 1
    assert visit["tokens"] == {"input_tokens": 1200, "output_tokens": 300}
    assert visit["estimated_cost_usd"] > 0
    assert isinstance(visit["last_saved_at"], float)
    # Ids, times and counts only — the shape is closed.
    assert set(visit) == {
        "visit_id", "began", "started_at", "last_saved_at", "process_run_id",
        "trace_run_id", "app_version", "turns", "estimated_cost_usd", "tokens",
    }


def test_saving_one_visit_twice_keeps_one_entry():
    session = SessionState()
    first = _journal(session)
    session.usage.add("interview", {"input_tokens": 5}, count_turn=True)
    second = _journal(session)
    assert len(second["visits"]) == 1
    assert second["visits"][0]["visit_id"] == first["visits"][0]["visit_id"]
    assert second["visits"][0]["turns"] == 1


def test_reopening_the_file_is_a_new_visit_of_the_same_session():
    original = SessionState()
    original.usage.add("interview", {"input_tokens": 7}, count_turn=True)
    saved = sessions.project_payload(original)

    reopened = SessionState()
    load_project(saved, reopened)

    assert reopened.identity.session_uid == original.identity.session_uid
    assert reopened.identity.created_at == original.identity.created_at
    assert reopened.identity.began == "opened"
    assert reopened.identity.visit_id != original.identity.visit_id
    journal = _journal(reopened)
    assert [v["visit_id"] for v in journal["visits"]] == [
        original.identity.visit_id,
        reopened.identity.visit_id,
    ]
    # The earlier visit is carried exactly as it was saved.
    assert journal["visits"][0] == saved[JOURNAL_KEY]["visits"][0]
    # And this visit's meter starts at zero, as it always has on load.
    assert journal["visits"][1]["turns"] == 0


def test_a_file_from_before_session_history_gets_a_uid_at_load():
    payload = sessions.project_payload(SessionState())
    payload.pop(JOURNAL_KEY)

    first, second = SessionState(), SessionState()
    load_project(payload, first)
    load_project(payload, second)

    assert valid_session_uid(first.identity.session_uid)
    assert first.identity.created_at is None
    assert first.identity.visits == ()
    # Nothing was recorded under the first until a save: two opens without
    # one are two uids. The documented trade.
    assert first.identity.session_uid != second.identity.session_uid
    assert _journal(first)["session_uid"] == first.identity.session_uid


def test_loading_over_a_live_session_never_keeps_its_identity():
    live = SessionState()
    live.identity = SessionIdentity(
        visits=({"visit_id": _uid("a"), "started_at": 1.0},)
    )
    other = SessionState()
    load_project(sessions.project_payload(other), live)
    assert live.identity.session_uid == other.identity.session_uid
    assert [v["visit_id"] for v in live.identity.visits] == [
        other.identity.visit_id
    ]


def test_a_shared_files_journal_is_rebuilt_from_known_fields_only():
    uid = _uid("c")
    restored = sanitize_journal(
        {
            "session_uid": uid,
            "created_at": 100.0,
            "dropped_visits": 3,
            "visits": [
                {
                    "visit_id": _uid("d"),
                    "started_at": 200.0,
                    "began": "hacked",
                    "process_run_id": "../../etc",
                    "trace_run_id": "session-0123abcd-1791000000",
                    "app_version": "1.25.1",
                    "turns": -4,
                    "estimated_cost_usd": float("inf"),
                    "tokens": {"input_tokens": 10, "prompt": "text", "x": 5},
                    "note": "the draft text must never ride along",
                },
                {"visit_id": "not-hex", "started_at": 1.0},
                {"visit_id": _uid("d"), "started_at": 300.0},  # duplicate
                "garbage",
            ],
        }
    )
    assert restored is not None
    assert restored.session_uid == uid
    assert restored.dropped_visits == 3
    [visit] = restored.visits
    assert visit["began"] == "opened"
    assert visit["process_run_id"] == ""
    assert visit["trace_run_id"] == "session-0123abcd-1791000000"
    assert visit["turns"] == 0
    assert visit["estimated_cost_usd"] == 0.0
    assert visit["tokens"] == {"input_tokens": 10}
    assert "note" not in visit

    assert sanitize_journal({"session_uid": "nope", "visits": []}) is None
    assert sanitize_journal("nope") is None


def test_numbers_too_large_for_a_float_are_unusable_not_an_error():
    """A JSON integer past the float range (``float(10**400)`` raises) makes
    a field unusable, never the load (Codex review on PR #303)."""
    huge = 10**400
    restored = sanitize_journal(
        {
            "session_uid": _uid("c"),
            "created_at": huge,
            "dropped_visits": huge,
            "visits": [
                {"visit_id": _uid("d"), "started_at": huge},
                {
                    "visit_id": _uid("e"),
                    "started_at": 5.0,
                    "turns": huge,
                    "estimated_cost_usd": huge,
                    "tokens": {"input_tokens": huge},
                },
            ],
        }
    )
    assert restored.created_at is None
    assert restored.dropped_visits == 0
    [visit] = restored.visits
    assert visit["turns"] == 0
    assert visit["estimated_cost_usd"] == 0.0
    assert visit["tokens"] == {}


def test_a_bad_journal_never_leaves_a_half_loaded_session(monkeypatch):
    """Identity is staged before the live session changes: if building it
    fails, the session is exactly what it was."""
    live = SessionState()
    live.history.append({"role": "user", "content": [{"type": "text", "text": "mine"}]})
    payload = sessions.project_payload(SessionState())

    def boom(_project):
        raise ValueError("unreadable journal")

    monkeypatch.setattr(SessionIdentity, "from_project", staticmethod(boom))
    with pytest.raises(ValueError):
        load_project(payload, live)
    assert live.history[0]["content"][0]["text"] == "mine"


def test_the_journal_keeps_the_newest_visits_and_counts_the_rest():
    visits = [
        {"visit_id": f"{i:032x}", "started_at": float(i)}
        for i in range(MAX_JOURNAL_VISITS + 5)
    ]
    restored = sanitize_journal(
        {"session_uid": _uid("e"), "visits": visits, "dropped_visits": 1}
    )
    assert len(restored.visits) == MAX_JOURNAL_VISITS
    assert restored.dropped_visits == 6
    assert restored.visits[-1]["visit_id"] == visits[-1]["visit_id"]

    identity = SessionIdentity(
        session_uid=_uid("e"),
        visits=restored.visits,
        dropped_visits=restored.dropped_visits,
    )
    journal = identity.journal(
        usage_snapshot=None,
        process_run_id="",
        trace_run_id="",
        app_version="",
        now=1.0,
    )
    assert len(journal["visits"]) == MAX_JOURNAL_VISITS
    assert journal["dropped_visits"] == 7
    assert journal["visits"][-1]["visit_id"] == identity.visit_id


def test_a_tutorial_clone_is_a_different_session():
    original = sessions.get_session()
    before = original.identity
    manager = sessions.workspace_manager()

    tutorial = manager.begin_tutorial(request_id="session-history-clone")
    try:
        clone = tutorial.session
        assert clone.identity.session_uid != before.session_uid
        assert clone.identity.began == "tutorial"
        assert clone.identity.visits == ()
        # The active workspace's uid is what log lines and records carry.
        assert sessions.active_session_uid() == clone.identity.session_uid
    finally:
        manager.force_restore_original()
    assert original.identity is before
    assert sessions.active_session_uid() == before.session_uid


# --- stamping ---------------------------------------------------------------


def test_log_lines_carry_the_active_sessions_uid(log_env):
    diagnostics.init_logging(force=True)
    uid = sessions.get_session().identity.session_uid
    logging.getLogger("buildaspec.test").warning("hello from a section")
    written = diagnostics.current_log_file().read_text(encoding="utf-8")
    line = next(
        line for line in written.splitlines() if "hello from a section" in line
    )
    assert f"session={uid}" in line


def test_the_active_uid_is_empty_without_a_provider(monkeypatch):
    monkeypatch.setattr(session_history, "_ACTIVE_PROVIDER", None)
    assert session_history.active_session_uid() == ""

    def broken() -> str:
        raise RuntimeError("never let observability raise")

    monkeypatch.setattr(session_history, "_ACTIVE_PROVIDER", broken)
    assert session_history.active_session_uid() == ""


def test_trace_records_carry_the_active_sessions_uid(trace_env):
    uid = sessions.get_session().identity.session_uid
    capture.app_event("probe", ok=True)
    recorder = recorder_module.get_recorder()
    assert recorder.flush(timeout=2.0)
    records = [
        json.loads(line)
        for line in (recorder.trace_dir / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    probe = next(record for record in records if record.get("type") == "probe")
    assert probe["session_uid"] == uid


# --- the launch index -------------------------------------------------------


def _run_meta() -> dict:
    recorder = recorder_module.get_recorder()
    return json.loads((recorder.trace_dir / "run.json").read_text("utf-8"))


def _marker() -> dict:
    path = diagnostics.current_log_dir() / diagnostics.RUN_MARKER_FILENAME
    return json.loads(path.read_text(encoding="utf-8"))


def test_a_blank_session_never_tags_the_launch(log_env, trace_env):
    diagnostics.init_logging(force=True)
    client = TestClient(create_app())
    client.get("/api/doc")
    assert "session_uids" not in _marker()
    assert "session_uids" not in _run_meta()


def test_opening_a_project_tags_the_launch_at_once(log_env, trace_env):
    diagnostics.init_logging(force=True)
    client = TestClient(create_app())
    saved = sessions.project_payload(SessionState())
    uid = saved[JOURNAL_KEY]["session_uid"]

    resp = client.post("/api/project/load", json=saved)

    assert resp.status_code == 200, resp.text
    assert sessions.get_session().identity.session_uid == uid
    # Written through, not at the next checkpoint: a launch that crashes
    # after the open is still findable by the session it was working on.
    assert _marker()["session_uids"] == [uid]
    assert _run_meta()["session_uids"] == [uid]


def test_saving_tags_the_launch_and_load_file_restores_the_uid(
    log_env, trace_env
):
    diagnostics.init_logging(force=True)
    client = TestClient(create_app())
    uid = sessions.get_session().identity.session_uid

    saved = client.get("/api/project/save")

    assert saved.status_code == 200
    assert _marker()["session_uids"] == [uid]
    assert _run_meta()["session_uids"] == [uid]

    assert client.post("/api/session/reset").json()["ok"]
    assert sessions.get_session().identity.session_uid != uid
    loaded = client.post(
        "/api/project/load-file",
        files={"file": ("p.baspec", saved.content, "application/octet-stream")},
    )
    assert loaded.status_code == 200, loaded.text
    assert sessions.get_session().identity.session_uid == uid
    # Tagged once, however often it is saved or opened in one launch.
    assert _run_meta()["session_uids"] == [uid]


@pytest.mark.parametrize("route", ["legacy", "baspec"])
def test_the_launch_is_tagged_with_the_session_the_load_committed(
    route, log_env, trace_env, monkeypatch
):
    """The uid is read under the commit's guard (Codex review on PR #303):
    a reset landing between the guard and the tag must not be what gets
    tagged. The reset is simulated in that exact window — the project_load
    trace event, which both routes emit after the guard and before tagging."""
    diagnostics.init_logging(force=True)
    client = TestClient(create_app())
    other = SessionState()
    uid = other.identity.session_uid
    real_event = capture.app_event

    def reset_after_load(event_type, **fields):
        if event_type == "project_load" and fields.get("ok"):
            sessions.get_session().reset()
        real_event(event_type, **fields)

    monkeypatch.setattr(capture, "app_event", reset_after_load)
    if route == "legacy":
        resp = client.post("/api/project/load", json=sessions.project_payload(other))
    else:
        package = sessions.render_project_package(
            sessions.capture_project_package_inputs(other)
        )
        resp = client.post(
            "/api/project/load-file",
            files={"file": ("p.baspec", package, "application/octet-stream")},
        )
    assert resp.status_code == 200, resp.text
    assert sessions.get_session().identity.session_uid != uid  # the reset won
    assert _marker()["session_uids"] == [uid]


def test_opening_a_baspec_tags_the_launch(log_env, trace_env):
    diagnostics.init_logging(force=True)
    client = TestClient(create_app())
    other = SessionState()
    # Packaged without the save path, so nothing tagged it on the way.
    package = sessions.render_project_package(
        sessions.capture_project_package_inputs(other)
    )
    uid = other.identity.session_uid

    loaded = client.post(
        "/api/project/load-file",
        files={"file": ("p.baspec", package, "application/octet-stream")},
    )

    assert loaded.status_code == 200, loaded.text
    assert sessions.get_session().identity.session_uid == uid
    assert _marker()["session_uids"] == [uid]
    assert _run_meta()["session_uids"] == [uid]


# --- retention --------------------------------------------------------------


def test_trace_retention_keeps_a_live_sessions_launches(tmp_path, monkeypatch):
    monkeypatch.delenv(session_history.ENV_SESSION_HISTORY_DAYS, raising=False)
    root = tmp_path / "traces"
    now = 1_800_000_000.0
    live, stale = _uid("a"), _uid("b")
    _trace_run(root, "session-old-live", ended=now - 60 * DAY, session_uids=[live])
    _trace_run(root, "session-new-live", ended=now - 2 * DAY, session_uids=[live])
    _trace_run(root, "session-old-other", ended=now - 60 * DAY)
    # A session last worked on longer ago than the window is not live.
    _trace_run(
        root, "session-old-stale", ended=now - 120 * DAY, session_uids=[stale]
    )

    result = prune_trace_runs(
        root,
        current_run_id="session-current",
        policy=TraceRetentionPolicy(max_runs=0, max_age_days=30, max_bytes=0),
        now=now,
    )

    assert (root / "session-old-live").exists()
    assert (root / "session-new-live").exists()
    assert not (root / "session-old-other").exists()
    assert not (root / "session-old-stale").exists()
    assert result["session_kept_runs"] == 2
    assert result["session_history_days"] == 90
    # Kept by design, so not a ceiling the pass failed to meet.
    assert result["limits_satisfied"] is True


def test_trace_retention_count_spares_a_live_session(tmp_path):
    root = tmp_path / "traces"
    now = 1_800_000_000.0
    uid = _uid("a")
    for index in range(4):
        _trace_run(
            root,
            f"session-live-{index}",
            ended=now - (10 - index) * 3600,
            session_uids=[uid],
        )
    _trace_run(root, "session-other-old", ended=now - 20 * 3600)
    _trace_run(root, "session-other-new", ended=now - 3600)

    prune_trace_runs(
        root,
        current_run_id="session-current",
        policy=TraceRetentionPolicy(max_runs=3, max_age_days=0, max_bytes=0),
        now=now,
    )

    assert all((root / f"session-live-{i}").exists() for i in range(4))
    assert not (root / "session-other-old").exists()
    assert not (root / "session-other-new").exists()


def test_trace_retention_bytes_take_other_launches_first(tmp_path):
    root = tmp_path / "traces"
    now = 1_800_000_000.0
    uid = _uid("a")
    padding = "x" * 4000
    _trace_run(root, "session-live-old", ended=now - 9000, session_uids=[uid], payload=padding)
    _trace_run(root, "session-live-new", ended=now - 100, session_uids=[uid], payload=padding)
    _trace_run(root, "session-other", ended=now - 50, payload=padding)

    def size(name: str) -> int:
        return sum(p.stat().st_size for p in (root / name).iterdir())

    # Room for the two newest; the byte ceiling takes the other launch even
    # though it is the newest, then the session's oldest.
    limit = size("session-live-new") + size("session-other") - 1
    prune_trace_runs(
        root,
        current_run_id="session-current",
        policy=TraceRetentionPolicy(max_runs=0, max_age_days=0, max_bytes=limit),
        now=now,
    )
    assert not (root / "session-other").exists()
    assert not (root / "session-live-old").exists()
    assert (root / "session-live-new").exists()


def test_session_history_days_zero_turns_the_protection_off(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(session_history.ENV_SESSION_HISTORY_DAYS, "0")
    root = tmp_path / "traces"
    now = 1_800_000_000.0
    _trace_run(root, "session-old-live", ended=now - 60 * DAY, session_uids=[_uid("a")])
    _trace_run(root, "session-new-live", ended=now - DAY, session_uids=[_uid("a")])
    prune_trace_runs(
        root,
        current_run_id="session-current",
        policy=TraceRetentionPolicy(max_runs=0, max_age_days=30, max_bytes=0),
        now=now,
    )
    assert not (root / "session-old-live").exists()

    monkeypatch.setenv(session_history.ENV_SESSION_HISTORY_DAYS, "junk")
    assert session_history.session_history_days() == 90


def test_log_retention_keeps_a_live_sessions_launches(tmp_path, monkeypatch):
    monkeypatch.delenv(session_history.ENV_SESSION_HISTORY_DAYS, raising=False)
    monkeypatch.setattr(diagnostics, "_process_is_alive", lambda pid: False)
    root = tmp_path / "logs"
    now = time.time()
    uid = _uid("a")
    kept = _log_run(root, "1", ended=now - 60 * DAY, session_uids=[uid])
    recent = _log_run(root, "2", ended=now - DAY, session_uids=[uid])
    other = _log_run(root, "3", ended=now - 60 * DAY)

    result = diagnostics.prune_log_runs(
        root,
        current_run_id=diagnostics.process_run_id(),
        policy=diagnostics.LogRetentionPolicy(
            max_runs=0, max_age_days=30, max_bytes=0
        ),
        now=now,
    )

    assert kept.exists() and recent.exists()
    assert not other.exists()
    assert result["session_kept_runs"] == 2
    assert result["limits_satisfied"] is True


# --- the bundle -------------------------------------------------------------


def _bundle(**kwargs) -> tuple[set[str], dict, zipfile.ZipFile]:
    path, _name = diagnostics.build_bundle(**kwargs)
    try:
        data = path.read_bytes()
    finally:
        diagnostics.unlink_quietly(path)
    import io

    zf = zipfile.ZipFile(io.BytesIO(data))
    return set(zf.namelist()), json.loads(zf.read("bundle-manifest.json")), zf


def _open_session_with_history(trace_env, log_env):
    """An open session whose journal remembers three earlier launches: two
    still on disk (one trace run, one log run) and one retention took."""
    now = time.time()
    payload = sessions.project_payload(SessionState())
    uid = payload[JOURNAL_KEY]["session_uid"]
    gone_trace = "session-0000dead-1790000000"
    gone_log = "process-" + "9" * 32
    payload[JOURNAL_KEY]["visits"].insert(
        0,
        {
            "visit_id": _uid("f"),
            "started_at": now - 30 * DAY,
            "process_run_id": gone_log,
            "trace_run_id": gone_trace,
        },
    )
    load_project(payload, sessions.get_session())
    _trace_run(trace_env, "session-earlier-1", ended=now - 5 * DAY, session_uids=[uid])
    _trace_run(trace_env, "session-earlier-2", ended=now - 2 * DAY, session_uids=[uid])
    _trace_run(trace_env, "session-unrelated", ended=now - DAY)
    log = _log_run(log_env, "1", ended=now - 2 * DAY, session_uids=[uid])
    _log_run(log_env, "2", ended=now - DAY)
    return uid, gone_trace, gone_log, log


def test_the_bundle_carries_every_earlier_launch_of_the_open_session(
    log_env, trace_env
):
    diagnostics.init_logging(force=True)
    capture.app_event("server_started")
    uid, gone_trace, gone_log, log = _open_session_with_history(
        trace_env, log_env
    )

    names, manifest, zf = _bundle()

    for run_id in ("session-earlier-1", "session-earlier-2"):
        assert f"traces/{run_id}/events.jsonl" in names
        assert f"traces/{run_id}/spans.jsonl" in names
        assert f"traces/{run_id}/run.json" in names
        # Prompt text is the large part, and only on request.
        assert f"traces/{run_id}/prompts.jsonl" not in names
    assert f"logs/{log.name}/{diagnostics.LOG_FILENAME}" in names
    assert f"logs/{log.name}/{diagnostics.RUN_MARKER_FILENAME}" in names
    # Another section's launch is not part of this session's history, and
    # the unrelated log run is not copied at all.
    assert "traces/session-unrelated/events.jsonl" not in names
    assert not any(n.startswith("logs/process-2222") for n in names)
    # The session's runs are not duplicated as prior-run tails.
    assert "traces/session-earlier-2/events-tail.jsonl" not in names

    scope = manifest["scope"]["session_history"]
    assert manifest["session_uid"] == uid
    assert scope["session_uid"] == uid
    assert scope["include_prompts"] is False
    assert [run["run_id"] for run in scope["trace_runs"]] == [
        "session-earlier-2",
        "session-earlier-1",
    ]
    assert all(run["coverage"] == "full" for run in scope["trace_runs"])
    assert all(
        run["prompts"] == "omitted_not_requested" for run in scope["trace_runs"]
    )
    assert [run["run_id"] for run in scope["log_runs"]] == [log.name]
    assert scope["journal_runs_not_on_disk"] == {
        "trace": [gone_trace],
        "log": [gone_log],
    }
    assert "session-earlier-1" in manifest["included_run_ids"]

    journal = json.loads(zf.read("session/journal.json"))
    assert journal["session_uid"] == uid
    assert len(journal["visits"]) == 3  # the gone one, the saved, this one
    assert journal["visits"][-1]["last_saved_at"] is None
    assert scope["visits_recorded"] == 3

    incidents = json.loads(zf.read("incident-index.json"))
    assert incidents["session_uid"] == uid
    assert any(
        item["run_id"] == log.name for item in incidents["session_log_errors"]
    )
    assert any(
        item.get("run_id") == "session-earlier-1"
        for item in incidents["recent_trace_incidents"]
    )


def test_the_bundle_adds_earlier_prompts_only_when_asked(log_env, trace_env):
    diagnostics.init_logging(force=True)
    capture.app_event("server_started")
    _open_session_with_history(trace_env, log_env)

    names, manifest, zf = _bundle(include_session_prompts=True)

    assert "traces/session-earlier-1/prompts.jsonl" in names
    assert b"the drafted prompt" in zf.read("traces/session-earlier-1/prompts.jsonl")
    assert manifest["scope"]["session_history"]["include_prompts"] is True


def test_an_over_budget_launch_contributes_a_bounded_tail(
    log_env, trace_env, monkeypatch
):
    diagnostics.init_logging(force=True)
    capture.app_event("server_started")
    _open_session_with_history(trace_env, log_env)
    # Room for exactly the newest launch, its prompt text included.
    newest = sum(
        p.stat().st_size for p in (trace_env / "session-earlier-2").iterdir()
    )
    monkeypatch.setattr(diagnostics, "_SESSION_TRACE_BYTE_BUDGET", newest)
    monkeypatch.setattr(diagnostics, "_SESSION_LOG_BYTE_BUDGET", 0)

    names, manifest, _zf = _bundle(include_session_prompts=True)

    scope = manifest["scope"]["session_history"]
    coverage = {run["run_id"]: run for run in scope["trace_runs"]}
    assert coverage["session-earlier-2"]["coverage"] == "full"
    assert coverage["session-earlier-1"]["coverage"] == "bounded_tail_over_budget"
    assert coverage["session-earlier-1"]["prompts"] == "omitted_over_budget"
    assert "traces/session-earlier-1/events-tail.jsonl" in names
    assert "traces/session-earlier-1/events.jsonl" not in names
    assert "traces/session-earlier-1/prompts.jsonl" not in names
    [log_scope] = scope["log_runs"]
    assert log_scope["coverage"] == "bounded_tail_over_budget"


def test_a_journal_cannot_pull_in_a_launch_that_is_not_tagged(
    log_env, trace_env
):
    """A ``.baspec`` is untrusted input: a run id its journal names selects
    nothing on its own (Codex review on PR #303). Only the run's own tag
    puts it in the bundle; an untagged run the journal names is listed,
    never copied, and only a launch with no folder reads as removed."""
    diagnostics.init_logging(force=True)
    capture.app_event("server_started")
    now = time.time()
    payload = sessions.project_payload(SessionState())
    untagged_trace = "session-0123abcd-1790000001"
    untagged_log = "process-" + "7" * 32
    payload[JOURNAL_KEY]["visits"].insert(
        0,
        {
            "visit_id": _uid("e"),
            "started_at": now - 3 * DAY,
            "process_run_id": untagged_log,
            "trace_run_id": untagged_trace,
        },
    )
    load_project(payload, sessions.get_session())
    _trace_run(trace_env, untagged_trace, ended=now - 3 * DAY)
    _log_run(log_env, "7", ended=now - 3 * DAY)

    names, manifest, _zf = _bundle(include_session_prompts=True)

    # Not copied as session history (it may still appear as one of the
    # three "other recent runs" tails, which never consult the journal).
    assert f"traces/{untagged_trace}/events.jsonl" not in names
    assert f"traces/{untagged_trace}/prompts.jsonl" not in names
    assert not any(n.startswith(f"logs/{untagged_log}/") for n in names)
    scope = manifest["scope"]["session_history"]
    assert scope["trace_runs"] == [] and scope["log_runs"] == []
    assert scope["journal_runs_untagged"] == {
        "trace": [untagged_trace],
        "log": [untagged_log],
    }
    assert scope["journal_runs_not_on_disk"] == {"trace": [], "log": []}
    # And the snapshot does not count them as this section's either.
    facts = diagnostics.snapshot()["session"]["session_history"]
    assert facts["earlier_trace_runs_on_disk"] == 0
    assert facts["earlier_log_runs_on_disk"] == 0


def test_packaging_the_tours_practice_copy_tags_nothing(log_env, trace_env):
    diagnostics.init_logging(force=True)
    capture.app_event("server_started")
    manager = sessions.workspace_manager()
    tutorial = manager.begin_tutorial(request_id="session-history-package")
    try:
        sessions.project_package(tutorial.session)
    finally:
        manager.force_restore_original()
    assert "session_uids" not in _marker()
    assert "session_uids" not in _run_meta()


def test_a_launch_owned_by_a_live_process_is_named_not_copied(
    log_env, trace_env
):
    diagnostics.init_logging(force=True)
    capture.app_event("server_started")
    uid, *_rest = _open_session_with_history(trace_env, log_env)
    _trace_run(
        trace_env,
        "session-sibling-window",
        ended=time.time(),
        session_uids=[uid],
        pid=os.getpid(),
    )

    names, manifest, _zf = _bundle()

    assert not any(n.startswith("traces/session-sibling-window/") for n in names)
    scope = manifest["scope"]["session_history"]
    assert "session-sibling-window" in scope["excluded_live_run_ids"]


def test_the_snapshot_says_how_much_of_the_session_is_on_disk(
    log_env, trace_env
):
    diagnostics.init_logging(force=True)
    capture.app_event("server_started")
    uid, *_rest = _open_session_with_history(trace_env, log_env)

    facts = diagnostics.snapshot()["session"]["session_history"]

    assert facts["session_uid"] == uid
    assert facts["visits_recorded"] == 3
    assert facts["visit_began"] == "opened"
    assert facts["earlier_trace_runs_on_disk"] == 2
    assert facts["earlier_log_runs_on_disk"] == 1
    assert facts["history_days"] == 90
    # The load here bypassed the route, so this launch is not tagged yet.
    assert facts["current_launch_tagged"] is False
    diagnostics.note_session(uid)
    assert diagnostics.snapshot()["session"]["session_history"][
        "current_launch_tagged"
    ] is True
