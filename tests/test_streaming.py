"""WI1 streaming-UX backend tests: the chat loop now iterates raw SDK
stream events and emits a richer, live SSE vocabulary (status hints,
thinking summaries, live web activity). All against the scripted fake
streaming client in ``tests/fakes.py``."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend.app import create_app
from backend import sessions
from tests.fakes import (
    FakeClient,
    bad_request,
    block_start_event,
    block_stop_event,
    chat_search_blocks,
    raw_turn,
    text_block,
    text_delta_event,
    text_turn,
    thinking_block,
    thinking_delta_event,
    tool_turn,
)


def _client() -> TestClient:
    return TestClient(create_app())


def _parse_sse(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def _patch_client(monkeypatch, fake: FakeClient) -> None:
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)


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


def test_status_thinking_then_thinking_delta_then_text_order(monkeypatch):
    """A reasoning-heavy turn streams: working → thinking → thinking_delta →
    writing → text_delta, exactly in that order."""
    events = [
        block_start_event(0, "thinking"),
        thinking_delta_event(0, "Weighing the density basis…"),
        block_stop_event(0),
        block_start_event(1, "text"),
        text_delta_event(1, "Here's my recommendation."),
        block_stop_event(1),
    ]
    fake = FakeClient(
        [
            raw_turn(
                [thinking_block(), text_block("Here's my recommendation.")],
                stop_reason="end_turn",
                events=events,
            )
        ]
    )
    _patch_client(monkeypatch, fake)

    resp = _client().post("/api/chat", json={"message": "recommend a density"})
    frames = _parse_sse(resp.text)

    # The transient status/thinking/text spine, in order.
    spine = [
        (e["type"], e.get("kind"))
        for e in frames
        if e["type"] in ("status", "thinking_delta", "text_delta")
    ]
    assert spine == [
        ("status", "working"),
        ("status", "thinking"),
        ("thinking_delta", None),
        ("status", "writing"),
        ("text_delta", None),
    ]
    assert frames[-1]["type"] == "turn_complete"

    # The working status carries the round index (0 for the first round).
    (working,) = [e for e in frames if e.get("kind") == "working"]
    assert working["round"] == 0


def test_live_web_search_fires_before_doc_patch(monkeypatch):
    """The live web_search event (emitted on the server-tool block's stop)
    arrives before the round's doc_patch — a real liveness signal, not a
    post-hoc chip."""
    search_round = raw_turn(
        [*chat_search_blocks("NFPA 13 2025 remote area", ["https://nfpa.org"])],
        stop_reason="pause_turn",
    )
    edit_round = tool_turn(["Recording."], _SEED_EDITS)
    fake = FakeClient([search_round, edit_round, text_turn(["Done."])])
    _patch_client(monkeypatch, fake)

    resp = _client().post("/api/chat", json={"message": "check then draft"})
    frames = _parse_sse(resp.text)
    types = [e["type"] for e in frames]

    assert "web_search" in types and "doc_patch" in types
    assert types.index("web_search") < types.index("doc_patch")
    (search_evt,) = [e for e in frames if e["type"] == "web_search"]
    assert search_evt["query"] == "NFPA 13 2025 remote area"

    # A "searching" status precedes the web_search event, and the pause
    # resume re-announces "searching" rather than a generic "working".
    searching = [i for i, e in enumerate(frames) if e.get("kind") == "searching"]
    assert len(searching) >= 2
    assert searching[0] < types.index("web_search")


def test_drafting_status_on_tool_block(monkeypatch):
    """A tool_use(apply_spec_edits) block announces a 'drafting' status."""
    fake = FakeClient([tool_turn(["Drafting."], _SEED_EDITS), text_turn(["Done."])])
    _patch_client(monkeypatch, fake)

    resp = _client().post("/api/chat", json={"message": "draft it"})
    frames = _parse_sse(resp.text)
    assert any(e.get("kind") == "drafting" for e in frames)


def test_status_frames_never_persist_to_history(monkeypatch):
    """Transient status/thinking_delta frames never reach committed history
    or the saved project file."""
    fake = FakeClient([tool_turn(["Drafting."], _SEED_EDITS), text_turn(["Done."])])
    _patch_client(monkeypatch, fake)
    client = _client()

    resp = client.post("/api/chat", json={"message": "draft it"})
    frames = _parse_sse(resp.text)
    assert any(e["type"] == "status" for e in frames)  # they DID stream

    dumped = json.dumps(sessions.get_session().history)
    assert '"status"' not in dumped
    assert "thinking_delta" not in dumped

    project = json.loads(json.dumps(sessions.project_payload(sessions.get_session())))
    project_dump = json.dumps(project)
    assert "thinking_delta" not in project_dump
    # No bare {"type": "status"} UI frame leaked into the project payload.
    assert '"type": "status"' not in project_dump


def test_thinking_display_degrades_to_omitted_on_400(monkeypatch):
    """When the endpoint rejects thinking.display with a 400, the engine
    retries the same round without it and the turn succeeds. The degrade is
    remembered: the retried request carries plain adaptive thinking."""
    fake = FakeClient([bad_request("thinking.display: unsupported"), text_turn(["ok"])])
    _patch_client(monkeypatch, fake)

    resp = _client().post("/api/chat", json={"message": "hello"})
    frames = _parse_sse(resp.text)
    assert frames[-1]["type"] == "turn_complete"

    # Two requests were made: the rejected one (with display) and the retry.
    assert len(fake.messages.requests) == 2
    assert fake.messages.requests[0]["thinking"] == {
        "type": "adaptive",
        "display": "summarized",
    }
    assert fake.messages.requests[1]["thinking"] == {"type": "adaptive"}

    # Degrade is remembered: a fresh turn skips display entirely (one request).
    fake2 = FakeClient([text_turn(["again"])])
    _patch_client(monkeypatch, fake2)
    _client().post("/api/chat", json={"message": "again"})
    assert fake2.messages.last_request["thinking"] == {"type": "adaptive"}
