"""Phase 1 backend tests: health, key handling, SSE chat with a fake stream."""
from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app import create_app
from backend import sessions


def _client() -> TestClient:
    return TestClient(create_app())


# ---------------------------------------------------------------------------
# Fake Anthropic streaming client (mirrors tests/fixtures/fake_anthropic.py
# in Spec Critic, trimmed to the streaming surface Phase 1 uses).
# ---------------------------------------------------------------------------


class _FakeStream:
    def __init__(self, chunks: list[str], stop_reason: str = "end_turn"):
        self._chunks = chunks
        self._stop_reason = stop_reason

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        yield from self._chunks

    def get_final_message(self):
        text_block = SimpleNamespace(type="text", text="".join(self._chunks))
        return SimpleNamespace(
            content=[text_block], stop_reason=self._stop_reason
        )


class _FakeMessages:
    def __init__(self, chunks: list[str]):
        self._chunks = chunks
        self.last_request: dict | None = None

    def stream(self, **request):
        self.last_request = request
        return _FakeStream(self._chunks)


class _FakeClient:
    def __init__(self, chunks: list[str]):
        self.messages = _FakeMessages(chunks)


def _parse_sse(body: str) -> list[dict]:
    events = []
    for line in body.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_health_reports_model_and_key(monkeypatch):
    resp = _client().get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["model"]
    assert data["api_key_present"] is True  # conftest injects the env key


def test_chat_streams_deltas_and_updates_history(monkeypatch):
    fake = _FakeClient(["PART 1 ", "- GENERAL"])
    monkeypatch.setattr(
        "backend.llm.conversation.get_client", lambda: fake
    )

    resp = _client().post("/api/chat", json={"message": "Start 21 13 13"})
    assert resp.status_code == 200
    events = _parse_sse(resp.text)

    deltas = [e["text"] for e in events if e["type"] == "text_delta"]
    assert "".join(deltas) == "PART 1 - GENERAL"
    assert events[-1]["type"] == "turn_complete"
    assert events[-1]["stop_reason"] == "end_turn"

    history = sessions.get_session().history
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"
    assert history[1]["content"][0]["text"] == "PART 1 - GENERAL"

    # The request carried the system prompt with a cache anchor.
    system = fake.messages.last_request["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}


def test_chat_error_leaves_history_clean(monkeypatch):
    def _boom():
        raise RuntimeError("kaput")

    monkeypatch.setattr("backend.llm.conversation.get_client", _boom)

    resp = _client().post("/api/chat", json={"message": "hello"})
    events = _parse_sse(resp.text)
    assert events == [
        {"type": "error", "message": "Unexpected error: kaput"}
    ]
    assert sessions.get_session().history == []


def test_empty_message_is_rejected(monkeypatch):
    fake = _FakeClient(["never used"])
    monkeypatch.setattr(
        "backend.llm.conversation.get_client", lambda: fake
    )
    resp = _client().post("/api/chat", json={"message": "   "})
    events = _parse_sse(resp.text)
    assert events[0]["type"] == "error"
    assert fake.messages.last_request is None


def test_session_reset(monkeypatch):
    fake = _FakeClient(["hi"])
    monkeypatch.setattr(
        "backend.llm.conversation.get_client", lambda: fake
    )
    client = _client()
    client.post("/api/chat", json={"message": "hello"})
    assert sessions.get_session().history

    resp = client.post("/api/session/reset")
    assert resp.json() == {"ok": True}
    assert sessions.get_session().history == []
