"""Stop generation: chat (graceful, commits partial progress like Claude.ai),
requirements research, and Final QC (both lossy — cooperative cancellation
that discards whatever the run had found)."""
from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from backend import sessions
from backend.app import create_app
from backend.llm.conversation import stream_user_turn
from tests.fakes import (
    FakeClient,
    SequencedFakeClient,
    block_start_event,
    block_stop_event,
    text_delta_event,
    text_turn,
    tool_turn,
    raw_turn,
    text_block,
)
from tests.test_qc import _finding, _qc_scripts, _seed_doc  # noqa: F401 (reuse)
from tests.test_research_engine import _scripts  # noqa: F401 (reuse)


def _client() -> TestClient:
    return TestClient(create_app())


def _parse_sse(body: str) -> list[dict]:
    import json

    return [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


_SEED_EDITS = {
    "edits": [
        {
            "action": "replace",
            "target_id": "sec",
            "text": "WET-PIPE SPRINKLER SYSTEMS",
            "numbering": "21 13 13",
        },
        {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
    ]
}


# ---------------------------------------------------------------------------
# Chat stop
# ---------------------------------------------------------------------------


def test_chat_stop_endpoint_409s_when_idle_and_signals_an_active_turn():
    client = _client()
    resp = client.post("/api/chat/stop")
    assert resp.status_code == 409

    session = sessions.get_session()
    session.turn_active = True
    try:
        resp = client.post("/api/chat/stop")
        assert resp.json()["ok"] is True
        assert session.stop_requested.is_set()
    finally:
        session.turn_active = False
        session.stop_requested.clear()


def test_chat_stop_mid_stream_truncates_the_live_events_but_commits():
    """Stopping partway through a text stream ends the SSE stream early (the
    caller never sees the rest of the scripted text) yet still commits a
    normal turn — no error, no rollback — mirroring Claude.ai's stop button."""
    events = [
        block_start_event(0, "text"),
        text_delta_event(0, "Hello"),
        text_delta_event(0, " world"),
        text_delta_event(0, "!"),
        block_stop_event(0),
    ]
    fake = FakeClient(
        [raw_turn([text_block("Hello world!")], stop_reason="end_turn", events=events)]
    )

    session = sessions.get_session()
    import backend.llm.conversation as conv

    orig_get_client = conv.get_client
    conv.get_client = lambda: fake  # type: ignore[assignment]
    try:
        seen_deltas: list[str] = []
        saw_turn_complete = False
        for event in stream_user_turn(session, "say hi"):
            if event["type"] == "text_delta":
                seen_deltas.append(event["text"])
                if len(seen_deltas) == 1:
                    session.stop_requested.set()
            elif event["type"] == "turn_complete":
                saw_turn_complete = True
            assert event["type"] != "error"
    finally:
        conv.get_client = orig_get_client

    # Only the first delta was seen — the stream stopped, it didn't drain.
    assert seen_deltas == ["Hello"]
    assert saw_turn_complete
    assert session.turn_active is False

    # The turn committed (not rolled back): one new user/assistant pair.
    assert len(session.history) == 2
    assert session.history[0]["role"] == "user"
    assert session.history[1]["role"] == "assistant"

    # The session is still healthy — a follow-up turn streams normally.
    fake2 = FakeClient([text_turn(["Continuing fine."])])
    conv.get_client = lambda: fake2  # type: ignore[assignment]
    try:
        events2 = list(stream_user_turn(session, "and then?"))
    finally:
        conv.get_client = orig_get_client
    assert events2[-1]["type"] == "turn_complete"
    assert len(session.history) == 4


def test_chat_stop_between_rounds_after_tool_dispatch_keeps_history_alternating():
    """Stopping right after a tool_use round (before the model gets to react
    to the tool_result) must not leave a dangling tool_result as the last
    history message — the next turn's user message would collide with it."""
    fake = FakeClient(
        [tool_turn(["Drafting."], _SEED_EDITS), text_turn(["Should not be reached."])]
    )
    session = sessions.get_session()
    import backend.llm.conversation as conv

    orig_get_client = conv.get_client
    conv.get_client = lambda: fake  # type: ignore[assignment]
    try:
        for event in stream_user_turn(session, "draft it"):
            if event["type"] == "doc_patch":
                session.stop_requested.set()
    finally:
        conv.get_client = orig_get_client

    # Round 1 never actually streamed — only one request was made.
    assert len(fake.messages.requests) == 1

    # The doc edit from the completed tool round IS kept (committed, not
    # rolled back).
    assert session.doc.doc.parts[0].articles[0].title == "SUMMARY"

    # History ends on an assistant turn, not the dangling tool_result.
    assert session.history[-1]["role"] == "assistant"
    assert session.history[-1]["content"] == [
        {"type": "text", "text": "[Generation stopped by user.]"}
    ]
    assert session.history[-2]["role"] == "user"  # the tool_result message

    # A follow-up turn still streams fine (roles kept alternating).
    fake2 = FakeClient([text_turn(["ok"])])
    conv.get_client = lambda: fake2  # type: ignore[assignment]
    try:
        events2 = list(stream_user_turn(session, "keep going"))
    finally:
        conv.get_client = orig_get_client
    assert events2[-1]["type"] == "turn_complete"


# ---------------------------------------------------------------------------
# Research stop
# ---------------------------------------------------------------------------


def _record_profile(client: TestClient, monkeypatch) -> None:
    fake = FakeClient(
        [
            tool_turn(
                ["Recorded."],
                {
                    "edits": [
                        {
                            "action": "set_project_profile",
                            "target_id": "sec",
                            "city": "Ashburn",
                            "state": "Virginia",
                            "country": "USA",
                            "client": "ExampleCo",
                        }
                    ]
                },
            ),
            text_turn(["Done."]),
        ]
    )
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    resp = client.post("/api/chat", json={"message": "Ashburn VA, client ExampleCo"})
    assert _parse_sse(resp.text)[-1]["type"] == "turn_complete"


def _wait_terminal(client: TestClient, path: str, timeout_s: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        snapshot = client.get(path).json()
        if snapshot["status"] in ("complete", "failed"):
            return snapshot
        time.sleep(0.05)
    raise AssertionError(f"{path} did not settle in time")


class _Blocking:
    """A client whose every streaming call blocks until released, then fails
    with a non-retryable error (fast, deterministic once unblocked)."""

    def __init__(self, release: threading.Event):
        self._release = release
        self.messages = self

    def stream(self, **_request):
        self._release.wait(timeout=5)
        raise RuntimeError("aborted")


def test_research_stop_discards_running_work_and_allows_immediate_restart(
    monkeypatch,
):
    client = _client()
    _record_profile(client, monkeypatch)

    release = threading.Event()
    monkeypatch.setattr("backend.app.get_client", lambda: _Blocking(release))
    assert client.post("/api/research/start").json()["ok"] is True
    assert client.get("/api/research/status").json()["status"] == "running"

    old_runner = sessions.get_session().research
    assert client.post("/api/research/stop").json()["ok"] is True
    snapshot = client.get("/api/research/status").json()
    assert snapshot["status"] == "failed"
    assert "stopped" in snapshot["error"].lower()
    assert "profile" not in snapshot

    # Nothing is running any more — a second stop is a no-op.
    assert client.post("/api/research/stop").status_code == 409

    # Let the abandoned thread's blocked calls fail and unwind; its (also
    # failed) outcome must NOT clobber the already-resolved status/message.
    release.set()
    old_runner._thread.join(timeout=5)
    assert not old_runner._thread.is_alive()
    assert old_runner.status == "failed"
    assert "stopped" in old_runner.error.lower()
    assert old_runner.profile_result is None
    assert client.get("/api/research/status").json()["status"] == "failed"

    # A fresh run starts immediately and completes normally.
    monkeypatch.setattr(
        "backend.app.get_client", lambda: SequencedFakeClient(_scripts())
    )
    assert client.post("/api/research/start").json()["ok"] is True
    assert _wait_terminal(client, "/api/research/status")["status"] == "complete"


# ---------------------------------------------------------------------------
# QC stop
# ---------------------------------------------------------------------------


def test_qc_stop_discards_running_work_and_allows_immediate_restart(monkeypatch):
    client = _client()
    _seed_doc(client, monkeypatch)

    release = threading.Event()
    monkeypatch.setattr("backend.app.get_client", lambda: _Blocking(release))
    assert client.post("/api/qc/start").json()["ok"] is True
    assert client.get("/api/qc/status").json()["status"] == "running"

    old_runner = sessions.get_session().qc
    assert client.post("/api/qc/stop").json()["ok"] is True
    snapshot = client.get("/api/qc/status").json()
    assert snapshot["status"] == "failed"
    assert "stopped" in snapshot["error"].lower()
    assert "result" not in snapshot

    assert client.post("/api/qc/stop").status_code == 409

    release.set()
    old_runner._thread.join(timeout=5)
    assert not old_runner._thread.is_alive()
    assert old_runner.status == "failed"
    assert "stopped" in old_runner.error.lower()
    assert old_runner.result is None
    assert client.get("/api/qc/status").json()["status"] == "failed"

    monkeypatch.setattr(
        "backend.app.get_client", lambda: SequencedFakeClient(_qc_scripts())
    )
    assert client.post("/api/qc/start").json()["ok"] is True
    assert _wait_terminal(client, "/api/qc/status")["status"] == "complete"
