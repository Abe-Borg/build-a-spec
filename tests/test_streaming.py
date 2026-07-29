"""WI1 streaming-UX backend tests: the chat loop now iterates raw SDK
stream events and emits a richer, live SSE vocabulary (status hints,
thinking summaries, live web activity). All against the scripted fake
streaming client in ``tests/fakes.py``."""
from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app import create_app
from backend import sessions
from tests.fakes import (
    FakeClient,
    bad_request,
    block_start_event,
    block_stop_event,
    chat_search_blocks,
    code_execution_tool_events,
    fetch_blocks,
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


def test_start_input_only_web_activity_emits_the_real_query_and_url(monkeypatch):
    """A server tool whose whole input arrives on the START frame — the
    code-execution caller's shape, which streams no ``input_json_delta`` —
    still fills its chat chip.

    Direct callers (``allowed_callers: ["direct"]``, Chunk 1.1) make that
    shape unexpected, not impossible; this is the fallback that keeps the
    chip from reading "Searching the web…" with nothing in it.
    """
    search_round = raw_turn(
        [
            *chat_search_blocks("NFPA 13 2025 remote area", ["https://nfpa.org"]),
            *fetch_blocks("https://nfpa.org/13"),
        ],
        stop_reason="pause_turn",
        events=[
            *code_execution_tool_events(
                0, "web_search", {"query": "NFPA 13 2025 remote area"}
            ),
            *code_execution_tool_events(
                1, "web_fetch", {"url": "https://nfpa.org/13"}
            ),
        ],
    )
    fake = FakeClient([search_round, text_turn(["Done."])])
    _patch_client(monkeypatch, fake)

    frames = _parse_sse(
        _client().post("/api/chat", json={"message": "check the remote area rule"}).text
    )
    (search,) = [e for e in frames if e["type"] == "web_search"]
    assert search["query"] == "NFPA 13 2025 remote area"
    (fetch,) = [e for e in frames if e["type"] == "web_fetch"]
    assert fetch["url"] == "https://nfpa.org/13"
    assert frames[-1]["type"] == "turn_complete"


def test_a_malformed_stream_frame_never_fails_a_chat_turn(monkeypatch):
    """Garbage raw frames are skipped, not fatal — the same posture the
    research and QC relays adapted from this one have always had, and one
    non-string ``partial_json`` away from being untrue here.

    A server tool that supplied no input either way still emits its chip
    with empty text: the round DID search, and chat says so rather than
    going silent (the deliberate contrast with research/QC, which skip)."""
    events = [
        # A start frame with no content_block at all.
        SimpleNamespace(type="content_block_start", index=0, content_block=None),
        # A delta with no delta payload.
        SimpleNamespace(type="content_block_delta", index=0, delta=None),
        # A non-string partial_json for a block that never started: string
        # concatenation raises, and used to take the whole turn with it.
        SimpleNamespace(
            type="content_block_delta",
            index=1,
            delta=SimpleNamespace(type="input_json_delta", partial_json=7),
        ),
        SimpleNamespace(type="mystery_frame"),
        # A search with neither deltas nor a start input.
        block_start_event(2, "server_tool_use", "web_search"),
        block_stop_event(2),
        # A start input that is not a mapping at all. Rejected by the reader,
        # NOT by the frame's try/except — the block still announces itself
        # and still gets its (unlabelled) chip.
        SimpleNamespace(
            type="content_block_start",
            index=3,
            content_block=SimpleNamespace(
                type="server_tool_use", name="web_fetch", input=42
            ),
        ),
        block_stop_event(3),
        block_start_event(4, "text"),
        text_delta_event(4, "Done."),
        block_stop_event(4),
    ]
    fake = FakeClient(
        [raw_turn([text_block("Done.")], stop_reason="end_turn", events=events)]
    )
    _patch_client(monkeypatch, fake)

    frames = _parse_sse(_client().post("/api/chat", json={"message": "look it up"}).text)
    assert frames[-1]["type"] == "turn_complete"
    assert not [e for e in frames if e["type"] == "error"]
    (search,) = [e for e in frames if e["type"] == "web_search"]
    assert search["query"] == ""
    (fetch,) = [e for e in frames if e["type"] == "web_fetch"]
    assert fetch["url"] == ""
    assert [e["kind"] for e in frames if e["type"] == "status"] == [
        "working",
        "searching",
        "fetching",
        "writing",
    ]
    assert "".join(e["text"] for e in frames if e["type"] == "text_delta") == "Done."


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
