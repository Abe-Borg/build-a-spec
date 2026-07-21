"""Backend API tests: health, key handling, SSE chat + tool loop, document
endpoints, export, and project save/resume — all against the scripted fake
streaming client in ``tests/fakes.py``."""
from __future__ import annotations

import io
import json

from docx import Document
from fastapi.testclient import TestClient

from backend.app import create_app
from backend import sessions
from tests.fakes import FakeClient, text_turn, tool_turn


def _client() -> TestClient:
    return TestClient(create_app())


def _parse_sse(body: str) -> list[dict]:
    events = []
    for line in body.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


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
        {
            "action": "add_paragraph",
            "target_id": "pt1.a1",
            "text": "Section includes wet-pipe systems per NFPA 13-2025.",
            "status": "assumed",
        },
        {
            "action": "add_paragraph",
            "target_id": "pt1.a1",
            "text": "Design density: [TBD: density] over remote area.",
            "status": "needs_input",
        },
    ]
}


def _seed_doc_via_chat(client: TestClient, monkeypatch) -> None:
    fake = FakeClient(
        [tool_turn(["Drafting."], _SEED_EDITS), text_turn(["Done."])]
    )
    _patch_client(monkeypatch, fake)
    resp = client.post("/api/chat", json={"message": "Start 21 13 13"})
    assert _parse_sse(resp.text)[-1]["type"] == "turn_complete"


# ---------------------------------------------------------------------------
# Phase 1 surface
# ---------------------------------------------------------------------------


def test_health_reports_model_and_key(monkeypatch):
    resp = _client().get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["model"]
    assert data["api_key_present"] is True  # conftest injects the env key


def test_chat_streams_deltas_and_updates_history(monkeypatch):
    fake = FakeClient([text_turn(["PART 1 ", "- GENERAL"])])
    _patch_client(monkeypatch, fake)

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

    # The request carried the cached system prompt, the live document
    # outline block after it, and the document tool.
    request = fake.messages.last_request
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "document" in request["system"][1]["text"]
    assert [t["name"] for t in request["tools"]] == ["apply_spec_edits"]


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
    fake = FakeClient([text_turn(["never used"])])
    _patch_client(monkeypatch, fake)
    resp = _client().post("/api/chat", json={"message": "   "})
    events = _parse_sse(resp.text)
    assert events[0]["type"] == "error"
    assert fake.messages.last_request is None


def test_session_reset_clears_history_and_document(monkeypatch):
    client = _client()
    _seed_doc_via_chat(client, monkeypatch)
    assert sessions.get_session().history
    assert not sessions.get_session().doc.doc.is_empty()

    resp = client.post("/api/session/reset")
    assert resp.json() == {"ok": True}
    assert sessions.get_session().history == []
    assert sessions.get_session().doc.doc.is_empty()
    assert len(sessions.get_session().doc.versions) == 1


# ---------------------------------------------------------------------------
# Tool-use continuation loop
# ---------------------------------------------------------------------------


def test_tool_turn_patches_document_and_continues(monkeypatch):
    fake = FakeClient(
        [
            tool_turn(["Drafting the summary. "], _SEED_EDITS),
            text_turn(["Two questions next."]),
        ]
    )
    _patch_client(monkeypatch, fake)

    resp = _client().post("/api/chat", json={"message": "Start 21 13 13"})
    events = _parse_sse(resp.text)

    # Text from both rounds streamed out.
    text = "".join(e["text"] for e in events if e["type"] == "text_delta")
    assert text == "Drafting the summary. Two questions next."

    patches = [e for e in events if e["type"] == "doc_patch"]
    assert len(patches) == 1
    applied_ids = [op["id"] for op in patches[0]["ops"]]
    assert applied_ids == ["sec", "pt1.a1", "pt1.a1.p1", "pt1.a1.p2"]
    assert patches[0]["doc"]["section"]["number"] == "21 13 13"

    # Mid-turn patches carry the pre-commit version pointer; the committed
    # snapshot after the turn carries the real one.
    assert patches[0]["doc"]["version"] == {"index": 0, "count": 1}
    (snapshot_evt,) = [e for e in events if e["type"] == "doc_snapshot"]
    assert snapshot_evt["doc"]["version"] == {"index": 1, "count": 2}

    (open_evt,) = [e for e in events if e["type"] == "open_questions"]
    kinds = {i["kind"] for i in open_evt["items"]}
    assert kinds == {"tbd", "needs_input"}

    assert events[-1] == {"type": "turn_complete", "stop_reason": "end_turn"}

    # History: user, assistant(tool_use), user(tool_result), assistant.
    history = sessions.get_session().history
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
    tool_use = history[1]["content"][-1]
    assert tool_use["type"] == "tool_use" and tool_use["name"] == "apply_spec_edits"
    tool_result = history[2]["content"][0]
    assert tool_result["tool_use_id"] == tool_use["id"]
    assert "outline" in tool_result["content"]

    # The continuation request carried the tool result back.
    second_request = fake.messages.requests[1]
    assert second_request["messages"][-1]["content"][0]["type"] == "tool_result"

    # One committed version for the turn.
    store = sessions.get_session().doc
    assert len(store.versions) == 2 and store.index == 1


def test_invalid_edit_batch_becomes_tool_error_not_turn_failure(monkeypatch):
    fake = FakeClient(
        [
            tool_turn([], {"edits": [{"action": "delete", "target_id": "zzz"}]}),
            text_turn(["Let me fix that."]),
        ]
    )
    _patch_client(monkeypatch, fake)

    resp = _client().post("/api/chat", json={"message": "go"})
    events = _parse_sse(resp.text)

    assert [e for e in events if e["type"] == "doc_patch"] == []
    assert [e for e in events if e["type"] == "open_questions"] == []
    assert events[-1]["type"] == "turn_complete"

    history = sessions.get_session().history
    tool_result = history[2]["content"][0]
    assert tool_result["is_error"] is True
    assert "rejected" in tool_result["content"]

    store = sessions.get_session().doc
    assert store.doc.is_empty() and len(store.versions) == 1


def test_failure_mid_continuation_rolls_everything_back(monkeypatch):
    fake = FakeClient(
        [
            tool_turn(["Working. "], _SEED_EDITS),
            RuntimeError("kaput"),
        ]
    )
    _patch_client(monkeypatch, fake)

    resp = _client().post("/api/chat", json={"message": "go"})
    events = _parse_sse(resp.text)

    # The doc_patch streamed optimistically, but the turn failed…
    assert any(e["type"] == "doc_patch" for e in events)
    assert events[-1] == {"type": "error", "message": "Unexpected error: kaput"}

    # …so nothing stuck: history untouched, document rolled back.
    assert sessions.get_session().history == []
    store = sessions.get_session().doc
    assert store.doc.is_empty()
    assert len(store.versions) == 1 and store.index == 0


def test_client_disconnect_mid_turn_rolls_back(monkeypatch):
    from backend.llm.conversation import stream_user_turn

    fake = FakeClient(
        [tool_turn(["Working. "], _SEED_EDITS), text_turn(["never reached"])]
    )
    _patch_client(monkeypatch, fake)
    session = sessions.get_session()

    gen = stream_user_turn(session, "Start 21 13 13")
    saw_patch = False
    for event in gen:
        if event["type"] == "doc_patch":
            saw_patch = True
            break
    assert saw_patch
    # The SSE consumer goes away (browser reload / fetch abort): the
    # generator is closed at the yield, which except-clauses cannot see.
    gen.close()

    assert session.history == []
    assert session.doc.doc.is_empty()
    assert len(session.doc.versions) == 1
    # A fresh turn starts from clean state (no orphaned backup adopted).
    assert session.doc._turn_backup is None


def test_session_reset_mid_turn_discards_zombie_turn(monkeypatch):
    from backend.llm.conversation import stream_user_turn

    fake = FakeClient(
        [tool_turn(["Round one. "], _SEED_EDITS), text_turn(["Round two."])]
    )
    _patch_client(monkeypatch, fake)
    session = sessions.get_session()

    events = []
    gen = stream_user_turn(session, "Start 21 13 13")
    for event in gen:
        events.append(event)
        if event["type"] == "doc_patch":
            # "New session" lands between continuation rounds.
            sessions.reset_session()
    assert events[-1]["type"] == "error"
    assert "reset" in events[-1]["message"]

    # The fresh session stayed exactly fresh.
    assert session.history == []
    assert session.doc.doc.is_empty()
    assert len(session.doc.versions) == 1 and session.doc.index == 0


def test_max_tokens_mid_tool_use_does_not_wedge_history(monkeypatch):
    fake = FakeClient(
        [tool_turn(["Partial draft"], _SEED_EDITS, stop_reason="max_tokens")]
    )
    _patch_client(monkeypatch, fake)
    client = _client()

    resp = client.post("/api/chat", json={"message": "go"})
    events = _parse_sse(resp.text)
    assert events[-1] == {"type": "turn_complete", "stop_reason": "max_tokens"}
    # The unexecuted tool call never touched the doc and is not in history
    # (a dangling tool_use would invalidate every later request).
    assert sessions.get_session().doc.doc.is_empty()
    history = sessions.get_session().history
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert all(b["type"] == "text" for b in history[1]["content"])

    # The next turn goes through cleanly on the committed history.
    fake2 = FakeClient([text_turn(["Continuing."])])
    _patch_client(monkeypatch, fake2)
    resp = client.post("/api/chat", json={"message": "continue"})
    assert _parse_sse(resp.text)[-1]["type"] == "turn_complete"


def test_tool_round_exhaustion_is_a_safe_failure(monkeypatch):
    from backend.llm.conversation import MAX_TOOL_ROUNDS

    turns = [
        tool_turn(
            [f"round {i} "],
            {"edits": [{"action": "add_article", "target_id": "pt1", "text": f"A{i}"}]},
            tool_id=f"toolu_round_{i}",
        )
        for i in range(MAX_TOOL_ROUNDS)
    ]
    fake = FakeClient(turns)
    _patch_client(monkeypatch, fake)

    resp = _client().post("/api/chat", json={"message": "go"})
    events = _parse_sse(resp.text)

    # Every round patched optimistically, then the turn failed as a unit.
    assert len([e for e in events if e["type"] == "doc_patch"]) == MAX_TOOL_ROUNDS
    assert events[-1]["type"] == "error"
    assert "tool rounds" in events[-1]["message"]
    assert sessions.get_session().history == []
    store = sessions.get_session().doc
    assert store.doc.is_empty() and len(store.versions) == 1


# ---------------------------------------------------------------------------
# Document endpoints
# ---------------------------------------------------------------------------


def test_doc_snapshot_undo_redo_endpoints(monkeypatch):
    client = _client()

    empty = client.get("/api/doc").json()
    assert empty["doc"]["version"] == {"index": 0, "count": 1}
    assert empty["open_questions"] == []

    _seed_doc_via_chat(client, monkeypatch)
    payload = client.get("/api/doc").json()
    assert payload["doc"]["section"]["number"] == "21 13 13"
    assert payload["doc"]["version"] == {"index": 1, "count": 2}
    assert len(payload["open_questions"]) == 2

    undone = client.post("/api/doc/undo")
    assert undone.status_code == 200
    assert undone.json()["doc"]["section"]["number"] == ""
    assert undone.json()["open_questions"] == []

    assert client.post("/api/doc/undo").status_code == 409

    redone = client.post("/api/doc/redo")
    assert redone.status_code == 200
    assert redone.json()["doc"]["section"]["number"] == "21 13 13"
    assert client.post("/api/doc/redo").status_code == 409


def test_docx_export_smoke(monkeypatch):
    client = _client()
    _seed_doc_via_chat(client, monkeypatch)

    resp = client.get("/api/export/docx")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml"
    )
    assert "SECTION 21 13 13" in resp.headers["content-disposition"]

    document = Document(io.BytesIO(resp.content))
    texts = [p.text for p in document.paragraphs]
    assert "SECTION 21 13 13" in texts
    assert "WET-PIPE SPRINKLER SYSTEMS" in texts
    assert any(t.startswith("A.\t") for t in texts)
    assert "ASSUMPTIONS SCHEDULE" in texts

    # The assumed block is scheduled with its numbering; the TBD is an
    # open item.
    tables = document.tables
    assert len(tables) == 2
    assumed_rows = [
        (row.cells[0].text, row.cells[1].text) for row in tables[0].rows[1:]
    ]
    assert assumed_rows == [
        ("1.1.A", "Section includes wet-pipe systems per NFPA 13-2025.")
    ]
    open_rows = [row.cells[1].text for row in tables[1].rows[1:]]
    assert any("density" in t for t in open_rows)


# ---------------------------------------------------------------------------
# Project save / resume
# ---------------------------------------------------------------------------


def test_project_save_and_resume_round_trip(monkeypatch):
    client = _client()
    _seed_doc_via_chat(client, monkeypatch)

    saved = client.get("/api/project/save")
    assert saved.status_code == 200
    assert "attachment" in saved.headers["content-disposition"]
    project = json.loads(saved.content)
    assert project["kind"] == "buildaspec-project"

    client.post("/api/session/reset")
    assert sessions.get_session().doc.doc.is_empty()

    loaded = client.post("/api/project/load", json=project)
    assert loaded.status_code == 200
    data = loaded.json()
    assert data["doc"]["section"]["number"] == "21 13 13"
    assert data["doc"]["version"] == {"index": 1, "count": 2}
    assert len(data["open_questions"]) == 2
    # The transcript shows only text turns (no tool plumbing).
    assert [m["role"] for m in data["chat"]] == ["user", "assistant"]
    assert data["chat"][1]["text"] == "Drafting.\n\nDone."

    # Undo still works across the resume (full version history restored).
    assert client.post("/api/doc/undo").status_code == 200

    # History resumed in API shape (tool_use/tool_result intact).
    history = sessions.get_session().history
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]


def test_project_load_rejects_garbage(monkeypatch):
    client = _client()
    resp = client.post("/api/project/load", json={"kind": "not-a-project"})
    assert resp.status_code == 400
    assert "project file" in resp.json()["error"]
    # Session untouched.
    assert sessions.get_session().history == []
