"""Tracing tests: recorder on/off, JSONL output, redaction, capture hooks."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
import threading
import time

from backend.tracing import (
    capture,
    config,
    recorder as recorder_module,
    retention as retention_module,
)
from backend.tracing.config import TraceRetentionPolicy
from backend.tracing.recorder import TraceRecorder, set_recorder
from backend.tracing.redaction import scrub_data
from backend.tracing.retention import prune_trace_runs


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
        assert ref["stored"] is True
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


def test_redaction_depth_limit_never_serializes_untrusted_repr_or_secret(tmp_path):
    class _SecretRepr:
        def __repr__(self) -> str:
            return "password=plain-text-secret"

    nested: object = {
        "password": "plain-text-secret",
        "object": _SecretRepr(),
    }
    for _ in range(9):
        nested = {"child": nested}

    scrubbed = scrub_data(nested)
    encoded = json.dumps(scrubbed)
    assert "plain-text-secret" not in encoded
    assert "password=" not in encoded
    assert "<max-depth>" in encoded

    # The writer's fallback must not reopen the same repr leak for an
    # unsupported value at a shallower level.
    rec = TraceRecorder(
        run_id="run-unsupported",
        trace_dir=tmp_path / "unsupported",
        capture_level="default",
    )
    rec.start()
    rec.add_event(None, "note", value=_SecretRepr())
    rec.stop()
    raw = (tmp_path / "unsupported" / "events.jsonl").read_text()
    assert "plain-text-secret" not in raw
    assert "<unsupported-value>" in raw


def test_redaction_normalizes_dataclasses_sets_and_pathlike_values():
    from dataclasses import dataclass
    from pathlib import Path

    class _PathLike(os.PathLike):
        def __fspath__(self) -> str:
            return "sk-ant-custompathsecret123"

    @dataclass
    class _Payload:
        api_key: str
        paths: set[Path]
        labels: frozenset[str]
        custom_path: os.PathLike

    scrubbed = scrub_data(
        {
            "payload": _Payload(
                api_key="sk-ant-dataclasssecret123",
                paths={Path("reports"), Path("sk-ant-pathsecret123")},
                labels=frozenset(
                    {"safe label", "Bearer abcdefghijklmnop"}
                ),
                custom_path=_PathLike(),
            )
        }
    )

    payload = scrubbed["payload"]
    assert payload["api_key"] == "<redacted>"
    assert set(payload["paths"]) == {"reports", "<redacted>"}
    assert set(payload["labels"]) == {"safe label", "<redacted>"}
    assert payload["custom_path"] == "<redacted>"
    encoded = json.dumps(scrubbed, allow_nan=False)
    assert "dataclasssecret" not in encoded
    assert "pathsecret" not in encoded
    assert "abcdefghijklmnop" not in encoded


def test_secret_named_credential_key_is_redacted_without_key_collision():
    import hashlib

    # Keep the credential-shaped fixture intact at runtime without presenting
    # repository scanners with what looks like a hardcoded bearer token.
    test_token = "abcdefghijklmnop"
    credential_key = f"Authorization: Bearer {test_token}"
    digest = hashlib.sha256(
        credential_key.encode("utf-8", errors="surrogatepass")
    ).hexdigest()[:12]
    literal_placeholder = f"<redacted-key:{digest}>"

    scrubbed = scrub_data(
        {
            credential_key: "credential-bearing key value",
            literal_placeholder: "literal placeholder value",
        }
    )

    assert credential_key not in scrubbed
    assert scrubbed[literal_placeholder] == "literal placeholder value"
    generated = [
        key
        for key in scrubbed
        if isinstance(key, str)
        and key.startswith(f"<redacted-key:{digest}:")
    ]
    assert len(generated) == 1
    assert scrubbed[generated[0]] == "<redacted>"


def test_redaction_normalizes_non_finite_numbers_for_strict_json():
    scrubbed = scrub_data(
        {
            "values": [float("nan"), float("inf"), float("-inf")],
            float("nan"): "non-finite key",
        }
    )

    assert scrubbed == {
        "values": [
            "<non-finite:NaN>",
            "<non-finite:+Infinity>",
            "<non-finite:-Infinity>",
        ],
        "<non-finite:NaN>": "non-finite key",
    }
    json.dumps(scrubbed, allow_nan=False)


def test_capture_hooks_are_noops_when_disabled(monkeypatch):
    monkeypatch.setenv(config.ENV_TRACE, "0")
    set_recorder(None)
    handle = capture.turn_start(model="m", history_len=0)
    assert handle is None
    capture.turn_end(handle)  # must not raise
    capture.tool_dispatch(None, ops=1, ok=True)
    capture.import_event(blocks=1, warnings=0, tracked_changes=False)
    assert recorder_module.get_recorder() is None


def test_trace_startup_failure_state_is_bounded_and_log_is_redacted(
    monkeypatch, caplog
):
    monkeypatch.setenv(config.ENV_TRACE, "1")
    monkeypatch.setattr(capture, "_STARTUP_FAILURE_STATE", None)
    monkeypatch.setattr(capture, "_MAX_STARTUP_FAILURE_COUNT", 2)
    set_recorder(None)

    secret = "abcdefghijklmnop"

    def fail_trace_dir(_run_id):
        raise RuntimeError(f"provider rejected Bearer {secret}")

    monkeypatch.setattr(capture, "trace_dir_for_run", fail_trace_dir)
    started_at = time.time()
    try:
        with caplog.at_level("ERROR", logger="backend.tracing.capture"):
            capture.app_event("first")
            capture.app_event("second")
            capture.app_event("third")

        state = capture.trace_startup_failure_state()
        assert state is not None
        assert state["exception_type"] == "RuntimeError"
        assert state["last_failed_at"] >= started_at
        assert state["count"] == 2
        assert secret not in caplog.text
        assert "Bearer <redacted>" not in caplog.text
        assert "<redacted>" in caplog.text

        # Callers receive a copy rather than the shared diagnostic state.
        state["count"] = 0
        assert capture.trace_startup_failure_state()["count"] == 2
    finally:
        set_recorder(None)


def test_capture_hooks_record_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv(config.ENV_TRACE, "1")
    monkeypatch.setenv(config.ENV_TRACE_DIR, str(tmp_path))
    monkeypatch.setenv(config.ENV_TRACE_MAX_MIB, "3")
    set_recorder(None)
    try:
        handle = capture.turn_start(model="claude-sonnet-5", history_len=0)
        assert handle is not None
        capture.tool_dispatch(handle, ops=2, ok=True)
        capture.turn_end(handle, stop_reason="end_turn", doc_changed=True)

        rec = recorder_module.get_recorder()
        assert rec is not None
        assert rec._max_run_bytes == 3 * 1024 * 1024
        rec.stop()

        run_dirs = list(tmp_path.iterdir())
        assert len(run_dirs) == 1
        spans = _read_jsonl(run_dirs[0] / "spans.jsonl")
        events = _read_jsonl(run_dirs[0] / "events.jsonl")
        assert spans[0]["kind"] == "turn"
        assert spans[0]["outputs"]["doc_changed"] is True
        assert any(event["type"] == "tool_dispatch" for event in events)
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


class _BlockedProviderClient:
    """Every streaming call waits for ``release``, then fails fast."""

    def __init__(self, release: threading.Event) -> None:
        self._release = release
        self.messages = self

    def stream(self, **_request):
        self._release.wait(timeout=10)
        raise RuntimeError("aborted")


def test_a_stopped_research_run_closes_its_span_at_the_stop(
    monkeypatch, tmp_path
):
    """A stopped run used to leave its span open for the rest of the app's life.

    The worker closed the span only when it WON the compare-and-set, and a
    stop is precisely the case where it loses — so every stopped research
    run left an unclosed span, which the viewer renders as a crash node and
    a support bundle cannot tell apart from a hang.

    Research closes at the stop rather than at the worker's finalizer
    because the round's result is discarded outright: there is nothing left
    to wait for. (Final QC is the opposite call — see the next test.)
    """
    monkeypatch.setenv(config.ENV_TRACE, "1")
    monkeypatch.setenv(config.ENV_TRACE_DIR, str(tmp_path))
    set_recorder(None)
    try:
        from backend.project_profile import ProjectProfile
        from backend.research.runner import ResearchRunner
        from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE

        release = threading.Event()
        settled = threading.Event()
        runner = ResearchRunner()
        assert runner.start(
            module=HYPERSCALE_FIRE,
            project_profile=ProjectProfile("Ashburn", "VA", "USA", "ExampleCo"),
            client=_BlockedProviderClient(release),
            model="claude-sonnet-5",
            max_tokens=4096,
            on_settled=settled.set,
        )
        rec = recorder_module.get_recorder()
        assert rec is not None
        assert [s["kind"] for s in rec.open_span_summaries()] == ["research"]

        assert runner.stop() is True
        assert rec.open_span_summaries() == [], "the stop closed the span"

        # The abandoned worker unwinds; it lost the race, so it must not
        # close a second time (or reopen anything).
        release.set()
        assert settled.wait(timeout=10)
        assert rec.open_span_summaries() == []
        rec.stop()

        run_dirs = list(tmp_path.iterdir())
        spans = _read_jsonl(run_dirs[0] / "spans.jsonl")
        research = [s for s in spans if s.get("kind") == "research"]
        assert len(research) == 1, "closed exactly once"
        assert research[0]["outputs"]["status"] == "failed"
        assert "stopped" in (research[0]["error"] or "").lower()
    finally:
        set_recorder(None)


def test_a_stops_span_close_never_precedes_an_event_it_already_accepted(
    monkeypatch, tmp_path
):
    """Closing the span made ordering matter, so the mirror moved under the lock.

    A dimension thread preempted between "the log accepted my event" and
    "mirror it to the trace" would let a concurrent stop close the span in
    the gap — and ``add_event`` does not check whether a span is still
    open, so the event landed timestamped past its own span's ``ended_at``.
    Harmless before this chunk (a stop closed nothing), and exactly the
    kind of incoherent timeline the chunk exists to remove.

    Reported by Codex on PR #107.
    """
    monkeypatch.setenv(config.ENV_TRACE, "1")
    monkeypatch.setenv(config.ENV_TRACE_DIR, str(tmp_path))
    set_recorder(None)
    try:
        from backend.project_profile import ProjectProfile
        from backend.research.runner import ResearchRunner
        from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE
        from tests.test_research_rounds import _ReleaseHook

        release = threading.Event()
        settled = threading.Event()
        runner = ResearchRunner()
        runner._lock = _ReleaseHook(runner._lock)

        def _stop_in_the_gap() -> None:
            # The seam is the release AFTER the first accepted event: with
            # the mirror outside the lock that is precisely the window;
            # with it inside, the mirror is already enqueued. Running after
            # the release (never during) is what lets stop() take the lock
            # here under either arrangement instead of deadlocking.
            if runner.status != "running" or not runner.events:
                return
            runner._lock.on_release = None
            runner.stop()

        runner._lock.on_release = _stop_in_the_gap
        assert runner.start(
            module=HYPERSCALE_FIRE,
            project_profile=ProjectProfile("Ashburn", "VA", "USA", "ExampleCo"),
            client=_BlockedProviderClient(release),
            model="claude-sonnet-5",
            max_tokens=4096,
            on_settled=settled.set,
        )
        release.set()
        assert settled.wait(timeout=10)
        runner._lock.on_release = None
        assert runner.status == "failed"

        rec = recorder_module.get_recorder()
        assert rec is not None
        rec.stop()

        run_dirs = list(tmp_path.iterdir())
        spans = _read_jsonl(run_dirs[0] / "spans.jsonl")
        events = _read_jsonl(run_dirs[0] / "events.jsonl")
        research = [s for s in spans if s.get("kind") == "research"]
        assert len(research) == 1
        span_id, ended_at = research[0]["span_id"], research[0]["ended_at"]
        assert ended_at is not None

        mirrored = [
            e
            for e in events
            if e.get("span_id") == span_id and e.get("type") == "research_progress"
        ]
        assert mirrored, "the run really did mirror progress before the stop"
        late = [e for e in mirrored if e["ts"] > ended_at]
        assert late == [], "an accepted event was recorded after its span closed"
    finally:
        set_recorder(None)


def test_a_stopped_qc_run_closes_its_span_when_the_attempt_settles(
    monkeypatch, tmp_path
):
    """Final QC closes at settlement, not at the stop — and only there.

    A stopped QC attempt keeps assembling the partial report it already paid
    for, and that report (and its finding counts) is what the span should
    describe. So the close belongs at the one point both outcomes pass
    through, ``_finalize_attempt``; stopping never closes it, which is what
    makes "exactly once" structural rather than a convention.
    """
    monkeypatch.setenv(config.ENV_TRACE, "1")
    monkeypatch.setenv(config.ENV_TRACE_DIR, str(tmp_path))
    set_recorder(None)
    try:
        from fastapi.testclient import TestClient

        from backend import sessions
        from backend.app import create_app
        from tests.test_qc import _seed_doc

        client = TestClient(create_app())
        _seed_doc(client, monkeypatch)

        release = threading.Event()
        monkeypatch.setattr(
            "backend.app.get_client",
            lambda: _BlockedProviderClient(release),
        )
        assert client.post("/api/qc/start").json()["ok"] is True
        rec = recorder_module.get_recorder()
        assert rec is not None
        assert [s["kind"] for s in rec.open_span_summaries()] == ["qc"]

        assert client.post("/api/qc/stop").json()["ok"] is True
        # Still open: the paid worker has not attached its report yet, and
        # that attachment is what the span's counts describe.
        assert [s["kind"] for s in rec.open_span_summaries()] == ["qc"]

        release.set()
        sessions.get_session().qc._thread.join(timeout=10)
        assert rec.open_span_summaries() == [], "settlement closed the span"
        rec.stop()

        run_dirs = list(tmp_path.iterdir())
        spans = _read_jsonl(run_dirs[0] / "spans.jsonl")
        qc_spans = [s for s in spans if s.get("kind") == "qc"]
        assert len(qc_spans) == 1, "closed exactly once"
        assert qc_spans[0]["outputs"]["status"] == "cancelled"
        assert "stopped" in (qc_spans[0]["error"] or "").lower()
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


def test_jsonl_preserves_lone_surrogates_without_dropping_records(tmp_path):
    rec = TraceRecorder(
        run_id="run-surrogate",
        trace_dir=tmp_path / "surrogate",
        capture_level="deep",
    )
    rec.start()
    try:
        hostile = "before\ud800after"
        rec.add_event(None, "client_error", message=hostile)
        prompt = rec.prompt_ref("turn", hostile)
        assert prompt["inline"] == hostile
        assert rec.flush(timeout=2.0) is True

        events = _read_jsonl(tmp_path / "surrogate" / "events.jsonl")
        assert events[0]["message"] == hostile
        meta = json.loads((tmp_path / "surrogate" / "run.json").read_text())
        assert meta["recorder_health"]["dropped_records"] == 0
        assert meta["recorder_health"]["write_failures"] == 0
    finally:
        rec.stop()


def test_non_finite_numbers_are_preserved_as_strict_json_markers(tmp_path):
    def reject_nonstandard_constant(value: str):
        raise AssertionError(f"non-standard JSON constant: {value}")

    rec = TraceRecorder(
        run_id="run-non-finite",
        trace_dir=tmp_path / "non-finite",
        capture_level="default",
    )
    rec.start(environment={"startup_metric": float("nan")})
    rec.add_event(
        None,
        "metrics",
        values={
            "nan": float("nan"),
            "positive": float("inf"),
            "negative": float("-inf"),
        },
        keyed={float("nan"): "non-finite key"},
    )
    rec.stop()

    event_line = (tmp_path / "non-finite" / "events.jsonl").read_text(
        encoding="utf-8"
    ).strip()
    event = json.loads(event_line, parse_constant=reject_nonstandard_constant)
    assert event["values"] == {
        "nan": "<non-finite:NaN>",
        "positive": "<non-finite:+Infinity>",
        "negative": "<non-finite:-Infinity>",
    }
    assert event["keyed"] == {"<non-finite:NaN>": "non-finite key"}
    meta = json.loads(
        (tmp_path / "non-finite" / "run.json").read_text(encoding="utf-8"),
        parse_constant=reject_nonstandard_constant,
    )
    assert meta["environment"]["startup_metric"] == "<non-finite:NaN>"
    assert meta["recorder_health"]["write_failures"] == 0
    assert meta["recorder_health"]["dropped_records"] == 0


def test_live_run_summary_is_checkpointed_without_a_bundle_flush(
    monkeypatch, tmp_path
):
    """A hard crash leaves run.json at most one short checkpoint behind."""
    import time as _time

    monkeypatch.setattr(recorder_module, "_META_CHECKPOINT_SECONDS", 0.02)
    rec = TraceRecorder(
        run_id="run-checkpoint",
        trace_dir=tmp_path / "checkpoint",
        capture_level="default",
    )
    rec.start()
    try:
        rec.add_event(None, "api_request", status=500, error="RuntimeError")
        deadline = _time.monotonic() + 2.0
        summary = {}
        while _time.monotonic() < deadline:
            meta = json.loads(
                (tmp_path / "checkpoint" / "run.json").read_text()
            )
            summary = meta.get("summary") or {}
            if summary.get("events_total") == 1:
                break
            _time.sleep(0.01)
        assert summary["events_total"] == 1
        assert summary["failed_events"] == 1
    finally:
        rec.stop()


def test_idle_checkpoint_persists_enqueue_drop_health(monkeypatch, tmp_path):
    """A pure producer-side drop must reach run.json without another event."""
    monkeypatch.setattr(recorder_module, "_META_CHECKPOINT_SECONDS", 0.02)
    rec = TraceRecorder(
        run_id="run-idle-drop",
        trace_dir=tmp_path / "idle-drop",
        capture_level="default",
        queue_max_bytes=300,
    )
    rec.start()
    try:
        rec.add_event(None, "note", payload="x" * 2_000)
        deadline = time.monotonic() + 2.0
        health = {}
        while time.monotonic() < deadline:
            meta = json.loads(
                (tmp_path / "idle-drop" / "run.json").read_text()
            )
            health = meta.get("recorder_health") or {}
            if health.get("queue_byte_drops") == 1:
                break
            time.sleep(0.01)

        assert health["dropped_records"] == 1
        assert health["queue_byte_drops"] == 1
        assert health["last_drop_reason"] == "queue_byte_limit"
        assert health["metadata_dirty"] is False
        assert (
            health["metadata_checkpointed_revision"]
            == health["metadata_revision"]
        )
    finally:
        rec.stop()


def test_stop_preserves_writer_crash_as_terminal_failure(tmp_path):
    rec = TraceRecorder(
        run_id="run-writer-crash",
        trace_dir=tmp_path / "writer-crash",
        capture_level="default",
    )

    @contextmanager
    def crash_open_writers():
        raise OSError("simulated writer crash")
        yield {}  # pragma: no cover - makes this a context manager generator

    rec._open_writers = crash_open_writers
    rec.start()
    assert rec._writer_thread is not None
    rec._writer_thread.join(timeout=2.0)
    assert rec._writer_thread.is_alive() is False

    before_stop = json.loads(
        (tmp_path / "writer-crash" / "run.json").read_text()
    )
    assert before_stop["recorder_health"]["state"] == "failed"
    assert before_stop["recorder_health"]["fatal_error"] == "OSError"

    rec.stop()
    after_stop = json.loads(
        (tmp_path / "writer-crash" / "run.json").read_text()
    )
    assert after_stop["ended_at"] is not None
    assert after_stop["recorder_health"]["state"] == "failed"
    assert after_stop["recorder_health"]["fatal_error"] == "OSError"
    assert rec.flush(timeout=0.01) is False


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


def test_default_prompt_hash_distinguishes_surrogate_from_question_mark(tmp_path):
    rec = TraceRecorder(
        run_id="run-prompt-surrogate",
        trace_dir=tmp_path / "prompt-surrogate",
        capture_level="default",
    )
    rec.start()
    surrogate_text = "before\ud800after"
    literal_question = "before?after"
    surrogate_ref = rec.prompt_ref("user", surrogate_text)
    question_ref = rec.prompt_ref("user", literal_question)
    rec.stop()

    assert surrogate_ref["ref"] != question_ref["ref"]
    prompts = _read_jsonl(tmp_path / "prompt-surrogate" / "prompts.jsonl")
    assert len(prompts) == 2
    assert {prompt["text"] for prompt in prompts} == {
        surrogate_text,
        literal_question,
    }


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


def test_bounded_queue_drops_by_count_without_blocking_flush_control(tmp_path):
    gate = threading.Event()
    rec = TraceRecorder(
        run_id="run-queue-count",
        trace_dir=tmp_path / "queue-count",
        capture_level="default",
        queue_max_records=2,
        queue_max_bytes=1024 * 1024,
    )
    real_writer_loop = rec._writer_loop

    def blocked_writer() -> None:
        gate.wait(timeout=2.0)
        real_writer_loop()

    rec._writer_loop = blocked_writer
    rec.start()
    try:
        rec.add_event(None, "note", index=1)
        rec.add_event(None, "note", index=2)
        rec.add_event(None, "note", index=3)

        result: dict[str, bool] = {}
        flush_thread = threading.Thread(
            target=lambda: result.setdefault("ok", rec.flush(timeout=2.0))
        )
        flush_thread.start()
        deadline = time.monotonic() + 1.0
        while rec._outstanding_barrier is None and time.monotonic() < deadline:
            time.sleep(0.005)
        assert rec._outstanding_barrier is not None
        gate.set()
        flush_thread.join(timeout=2.0)
        assert result == {"ok": True}

        events = _read_jsonl(tmp_path / "queue-count" / "events.jsonl")
        assert [event["index"] for event in events] == [1, 2]
        meta = json.loads(
            (tmp_path / "queue-count" / "run.json").read_text()
        )
        health = meta["recorder_health"]
        assert health["dropped_records"] == 1
        assert health["queue_count_drops"] == 1
        assert health["queue_byte_drops"] == 0
        assert health["queue_high_watermark"] == 2
        assert health["queue_depth"] == 0
    finally:
        gate.set()
        rec.stop()


def test_queue_payload_byte_cap_rejects_one_oversized_record(tmp_path):
    rec = TraceRecorder(
        run_id="run-queue-bytes",
        trace_dir=tmp_path / "queue-bytes",
        capture_level="default",
        queue_max_records=10,
        queue_max_bytes=400,
    )
    rec.start()
    try:
        rec.add_event(None, "note", payload="x" * 2_000)
        rec.add_event(None, "note", payload="small")
        assert rec.flush(timeout=2.0) is True

        events = _read_jsonl(tmp_path / "queue-bytes" / "events.jsonl")
        assert [event["payload"] for event in events] == ["small"]
        meta = json.loads(
            (tmp_path / "queue-bytes" / "run.json").read_text()
        )
        health = meta["recorder_health"]
        assert health["queue_byte_drops"] == 1
        assert health["queue_count_drops"] == 0
        assert health["last_drop_reason"] == "queue_byte_limit"
        assert health["queue_payload_high_watermark"] <= 400
    finally:
        rec.stop()


def test_prompt_dedup_retries_after_backpressure_rejects_storage(tmp_path):
    gate = threading.Event()
    rec = TraceRecorder(
        run_id="run-prompt-retry",
        trace_dir=tmp_path / "prompt-retry",
        capture_level="default",
        queue_max_records=1,
        queue_max_bytes=1024 * 1024,
    )
    real_writer_loop = rec._writer_loop

    def blocked_writer() -> None:
        gate.wait(timeout=2.0)
        real_writer_loop()

    rec._writer_loop = blocked_writer
    rec.start()
    try:
        rec.add_event(None, "note", phase="fills-queue")
        first_ref = rec.prompt_ref("user", "retry this prompt")
        assert first_ref["stored"] is False
        assert rec.writer_health()["queue_count_drops"] == 1

        gate.set()
        assert rec.flush(timeout=2.0) is True
        stored_ref = rec.prompt_ref("user", "retry this prompt")
        assert stored_ref == {
            "ref": first_ref["ref"],
            "kind": "user",
            "stored": True,
        }
        assert rec.prompt_ref("user", "retry this prompt") == stored_ref
        assert rec.flush(timeout=2.0) is True

        prompts = _read_jsonl(tmp_path / "prompt-retry" / "prompts.jsonl")
        assert [prompt["text"] for prompt in prompts] == ["retry this prompt"]
    finally:
        gate.set()
        rec.stop()


def test_active_run_byte_cap_drops_records_but_barriers_still_progress(tmp_path):
    rec = TraceRecorder(
        run_id="run-byte-cap",
        trace_dir=tmp_path / "byte-cap",
        capture_level="default",
        max_run_bytes=1,
    )
    rec.start()
    try:
        rec.add_event(None, "note", payload="first")
        rec.add_event(None, "note", payload="second")
        assert rec.flush(timeout=2.0) is True

        meta = json.loads((tmp_path / "byte-cap" / "run.json").read_text())
        health = meta["recorder_health"]
        assert health["storage_limit_reached"] is True
        assert health["max_run_bytes"] == 1
        assert health["storage_limit_scope"] == "jsonl_payload_bytes"
        assert health["run_byte_limit_drops"] == 2
        assert health["dropped_records"] == 2
        assert health["last_drop_reason"] == "run_byte_limit"
        assert health["data_bytes_written"] == 0
        assert meta["summary"]["records_enqueued"] == 0
    finally:
        rec.stop()


def test_flush_metadata_summary_stops_exactly_at_its_barrier(tmp_path):
    gate = threading.Event()
    rec = TraceRecorder(
        run_id="run-barrier-summary",
        trace_dir=tmp_path / "barrier-summary",
        capture_level="default",
    )
    real_writer_loop = rec._writer_loop

    def blocked_writer() -> None:
        gate.wait(timeout=2.0)
        real_writer_loop()

    rec._writer_loop = blocked_writer
    rec.start()
    try:
        rec.add_event(None, "note", phase="before")
        result: dict[str, bool] = {}
        flush_thread = threading.Thread(
            target=lambda: result.setdefault("ok", rec.flush(timeout=2.0))
        )
        flush_thread.start()
        deadline = time.monotonic() + 1.0
        while rec._outstanding_barrier is None and time.monotonic() < deadline:
            time.sleep(0.005)
        assert rec._outstanding_barrier is not None
        rec.add_event(None, "note", phase="after")
        gate.set()
        flush_thread.join(timeout=2.0)
        assert result == {"ok": True}

        first_meta = json.loads(
            (tmp_path / "barrier-summary" / "run.json").read_text()
        )
        assert first_meta["summary"]["records_enqueued"] == 1
        assert first_meta["summary"]["events_total"] == 1
        first_health = first_meta["recorder_health"]
        assert first_health["metadata_dirty"] is True
        assert (
            first_health["metadata_checkpointed_revision"]
            < first_health["metadata_revision"]
        )

        assert rec.flush(timeout=2.0) is True
        second_meta = json.loads(
            (tmp_path / "barrier-summary" / "run.json").read_text()
        )
        assert second_meta["summary"]["records_enqueued"] == 2
        assert second_meta["summary"]["events_total"] == 2
        second_health = second_meta["recorder_health"]
        assert second_health["metadata_dirty"] is False
        assert (
            second_health["metadata_checkpointed_revision"]
            == second_health["metadata_revision"]
        )
    finally:
        gate.set()
        rec.stop()


def test_failed_event_summary_uses_closed_failure_shapes_and_counts(tmp_path):
    rec = TraceRecorder(
        run_id="run-failure-classifier",
        trace_dir=tmp_path / "failure-classifier",
        capture_level="default",
    )
    rec.start()
    try:
        rec.add_event(None, "client_error", message_fingerprint="abc")
        rec.add_event(None, "workspace_conflict", generation=4)
        rec.add_event(None, "trace_retention", failures=2)
        rec.add_event(None, "trace_retention", failures=0)
        rec.add_event(None, "note", unrelated_failures=99)
        assert rec.flush(timeout=2.0) is True

        meta = json.loads(
            (tmp_path / "failure-classifier" / "run.json").read_text()
        )
        summary = meta["summary"]
        assert summary["failed_events"] == 3
        assert summary["failed_events_by_type"] == {
            "client_error": 1,
            "trace_retention": 1,
            "workspace_conflict": 1,
        }
    finally:
        rec.stop()


def test_stop_timeout_later_converges_and_flush_requires_terminal_state(
    tmp_path,
):
    gate = threading.Event()
    rec = TraceRecorder(
        run_id="run-stop-converges",
        trace_dir=tmp_path / "stop-converges",
        capture_level="default",
    )
    real_writer_loop = rec._writer_loop

    def blocked_writer() -> None:
        gate.wait(timeout=2.0)
        real_writer_loop()

    rec._writer_loop = blocked_writer
    rec.start()
    rec.add_event(None, "note", phase="queued")
    try:
        rec.stop(flush_timeout=0.01)
        timed_out = json.loads(
            (tmp_path / "stop-converges" / "run.json").read_text()
        )
        assert timed_out["recorder_health"]["state"] == "drain_timeout"
        assert timed_out["recorder_health"]["drain_timed_out"] is True
        assert rec.flush(timeout=0.01) is False

        gate.set()
        assert rec._writer_thread is not None
        rec._writer_thread.join(timeout=2.0)
        final_meta = json.loads(
            (tmp_path / "stop-converges" / "run.json").read_text()
        )
        final_health = final_meta["recorder_health"]
        assert final_health["state"] == "stopped"
        assert final_health["thread_alive"] is False
        assert final_health["drain_timed_out"] is True
        assert rec.flush(timeout=0.1) is True
    finally:
        gate.set()
        rec.stop()


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
    assert meta["trace_schema_version"] == 2
    assert len(meta["process_instances"]) == 2
    assert all(item["ended_at"] is not None for item in meta["process_instances"])


def test_records_are_correlated_sequenced_and_summarized_at_flush(tmp_path):
    rec = TraceRecorder(
        run_id="run-summary",
        trace_dir=tmp_path / "summary",
        capture_level="default",
    )
    rec.start(environment={"pid": os.getpid()})
    handle = rec.open_span("turn", "turn #1")
    rec.add_event(
        handle,
        "api_request",
        status=409,
        error="stale_workspace",
        outcome_code="stale_workspace",
    )
    rec.prompt_ref("user", "explain this trace")
    rec.close_span(
        handle,
        outputs={
            "usage": {
                "input_tokens": 12,
                "output_tokens": 34,
                "cache_read_input_tokens": 56,
                "usage_estimated": True,
                # Booleans and unknown fields are never miscounted as tokens.
                "unexpected": 999,
            }
        },
    )
    assert rec.flush(timeout=5.0) is True

    run_dir = tmp_path / "summary"
    records = (
        _read_jsonl(run_dir / "events.jsonl")
        + _read_jsonl(run_dir / "prompts.jsonl")
        + _read_jsonl(run_dir / "spans.jsonl")
    )
    process_ids = {record["process_instance_id"] for record in records}
    assert len(process_ids) == 1
    assert all(record["trace_run_id"] == "run-summary" for record in records)
    assert sorted(record["record_seq"] for record in records) == [1, 2, 3]

    live_meta = json.loads((run_dir / "run.json").read_text())
    summary = live_meta["summary"]
    assert summary["records_enqueued"] == 3
    assert summary["events_by_type"] == {"api_request": 1}
    assert summary["event_status_counts"] == {"409": 1}
    assert summary["request_outcome_counts"] == {"stale_workspace": 1}
    assert summary["failed_events"] == 1
    assert summary["failed_events_by_type"] == {"api_request": 1}
    assert summary["spans_by_kind"] == {"turn": 1}
    assert summary["spans_by_status"] == {"ok": 1}
    assert summary["prompts_stored"] == 1
    assert summary["turn_usage_totals"] == {
        "input_tokens": 12,
        "output_tokens": 34,
        "cache_read_input_tokens": 56,
    }
    assert summary["includes_estimated_turn_usage"] is True
    health = live_meta["recorder_health"]
    assert health["state"] == "running"
    assert health["records_written"] == 3
    assert health["dropped_records"] == 0
    assert health["queue_depth"] == 0
    assert health["open_spans"] == 0

    rec.stop()
    final_meta = json.loads((run_dir / "run.json").read_text())
    assert final_meta["recorder_health"]["state"] == "stopped"
    assert final_meta["process_instances"][-1]["ended_at"] is not None


def test_trace_retention_configuration_is_bounded_and_invalid_values_fall_back(
    monkeypatch,
):
    monkeypatch.setenv(config.ENV_TRACE_MAX_RUNS, "7")
    monkeypatch.setenv(config.ENV_TRACE_MAX_AGE_DAYS, "14")
    monkeypatch.setenv(config.ENV_TRACE_MAX_MIB, "64")
    policy = config.trace_retention_policy()
    assert policy == TraceRetentionPolicy(
        max_runs=7,
        max_age_days=14,
        max_bytes=64 * 1024 * 1024,
    )

    monkeypatch.setenv(config.ENV_TRACE_MAX_RUNS, "not-a-number")
    monkeypatch.setenv(config.ENV_TRACE_MAX_AGE_DAYS, "-1")
    monkeypatch.setenv(config.ENV_TRACE_MAX_MIB, "")
    policy = config.trace_retention_policy()
    assert policy.max_runs == config.DEFAULT_TRACE_MAX_RUNS
    assert policy.max_age_days == config.DEFAULT_TRACE_MAX_AGE_DAYS
    assert policy.max_bytes == config.DEFAULT_TRACE_MAX_MIB * 1024 * 1024


def test_trace_retention_prunes_only_recognized_oldest_inactive_runs(tmp_path):
    root = tmp_path / "traces"
    root.mkdir()

    def add_run(run_id: str, *, started: float, ended: float | None) -> None:
        run_dir = root / run_id
        run_dir.mkdir()
        (run_dir / "run.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "started_at": started,
                    "ended_at": ended,
                }
            ),
            encoding="utf-8",
        )
        (run_dir / "events.jsonl").write_text("x" * 128, encoding="utf-8")

    add_run("old", started=100.0, ended=200.0)
    add_run("new", started=300.0, ended=400.0)
    add_run("current", started=500.0, ended=None)
    unrelated = root / "do-not-delete"
    unrelated.mkdir()
    (unrelated / "notes.txt").write_text("mine", encoding="utf-8")
    mismatched = root / "mismatched"
    mismatched.mkdir()
    (mismatched / "run.json").write_text(
        json.dumps({"run_id": "somewhere-else", "ended_at": 1}),
        encoding="utf-8",
    )

    result = prune_trace_runs(
        root,
        current_run_id="current",
        policy=TraceRetentionPolicy(max_runs=2, max_age_days=0, max_bytes=0),
        now=600.0,
    )
    assert result["removed_runs"] == 1
    assert result["removed_by_reason"] == {"count": 1}
    assert not (root / "old").exists()
    assert (root / "new").exists()
    assert (root / "current").exists()
    assert unrelated.exists()
    assert mismatched.exists()
    assert result["ignored_entries"] == 2
    assert result["limits_satisfied"] is True


def test_trace_retention_never_prunes_an_ended_run_owned_by_a_live_pid(tmp_path):
    root = tmp_path / "traces"
    root.mkdir()

    def add_run(run_id: str, pid: int | None) -> None:
        run_dir = root / run_id
        run_dir.mkdir()
        meta = {
            "run_id": run_id,
            "started_at": 100.0,
            "ended_at": 200.0,
        }
        if pid is not None:
            meta["environment"] = {"pid": pid}
        (run_dir / "run.json").write_text(json.dumps(meta), encoding="utf-8")

    add_run("live-draining", os.getpid())
    add_run("inactive", None)
    result = prune_trace_runs(
        root,
        current_run_id="another-run",
        policy=TraceRetentionPolicy(max_runs=1, max_age_days=0, max_bytes=0),
        now=300.0,
    )

    assert (root / "live-draining").exists()
    assert not (root / "inactive").exists()
    assert result["protected_runs"] == 1


def test_trace_retention_applies_age_and_aggregate_byte_ceilings(tmp_path):
    root = tmp_path / "traces"
    root.mkdir()

    def add_run(run_id: str, *, ended: float, payload_bytes: int) -> None:
        run_dir = root / run_id
        run_dir.mkdir()
        (run_dir / "run.json").write_text(
            json.dumps(
                {"run_id": run_id, "started_at": ended - 10, "ended_at": ended}
            ),
            encoding="utf-8",
        )
        (run_dir / "events.jsonl").write_bytes(b"x" * payload_bytes)

    add_run("expired", ended=100.0, payload_bytes=900)
    add_run("large-old", ended=99_900.0, payload_bytes=900)
    add_run("large-new", ended=99_950.0, payload_bytes=900)

    result = prune_trace_runs(
        root,
        current_run_id="none",
        policy=TraceRetentionPolicy(
            max_runs=0,
            max_age_days=1,
            max_bytes=1_100,
        ),
        now=100_000.0,
    )
    assert result["removed_by_reason"] == {"age": 1, "bytes": 1}
    assert result["remaining_runs"] == 1
    assert (root / "large-new").exists()
    assert result["limits_satisfied"] is True


def test_trace_retention_reports_unsatisfied_age_limit_after_delete_failure(
    tmp_path, monkeypatch
):
    root = tmp_path / "traces"
    run_dir = root / "expired"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps({"run_id": "expired", "started_at": 1.0, "ended_at": 2.0}),
        encoding="utf-8",
    )

    def fail_remove(_path):
        raise OSError("locked")

    monkeypatch.setattr(retention_module.shutil, "rmtree", fail_remove)
    result = prune_trace_runs(
        root,
        current_run_id="none",
        policy=TraceRetentionPolicy(max_runs=0, max_age_days=1, max_bytes=0),
        now=200_000.0,
    )

    assert result["failures"] == 1
    assert result["limits_satisfied"] is False
