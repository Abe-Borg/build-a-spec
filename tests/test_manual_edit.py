"""WI2: direct manual editing via POST /api/doc/edit, and the set_status op."""
from __future__ import annotations

import io
import json

from docx import Document
from fastapi.testclient import TestClient

from backend.app import create_app
from backend import sessions
from backend.spec_doc.model import APPLY_SPEC_EDITS_TOOL
from tests.fakes import FakeClient, text_turn, tool_turn


def _client() -> TestClient:
    return TestClient(create_app())


def _parse_sse(body: str) -> list[dict]:
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
        {
            "action": "add_paragraph",
            "target_id": "pt1.a1",
            "text": "Section includes wet-pipe systems per NFPA 13-2025.",
            "status": "assumed",
        },
    ]
}


def _seed(client: TestClient, monkeypatch) -> None:
    fake = FakeClient([tool_turn(["Drafting."], _SEED_EDITS), text_turn(["Done."])])
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    resp = client.post("/api/chat", json={"message": "Start 21 13 13"})
    assert _parse_sse(resp.text)[-1]["type"] == "turn_complete"


def test_set_status_in_tool_schema_enum():
    action = APPLY_SPEC_EDITS_TOOL["input_schema"]["properties"]["edits"][
        "items"
    ]["properties"]["action"]
    assert "set_status" in action["enum"]


def test_manual_replace_edits_transactionally(monkeypatch):
    client = _client()
    _seed(client, monkeypatch)

    resp = client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "replace",
                    "target_id": "pt1.a1.p1",
                    "text": "Section includes wet-pipe systems per NFPA 13.",
                    "status": "confirmed",
                }
            ]
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    para = data["doc"]["parts"][0]["articles"][0]["paragraphs"][0]
    assert para["text"] == "Section includes wet-pipe systems per NFPA 13."
    assert para["status"] == "confirmed"
    # One new version committed (seed was v1; this is v2).
    assert data["doc"]["version"] == {"index": 2, "count": 3}


def test_manual_set_status_confirms_block(monkeypatch):
    client = _client()
    _seed(client, monkeypatch)

    resp = client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "set_status",
                    "target_id": "pt1.a1.p1",
                    "status": "confirmed",
                }
            ]
        },
    )
    assert resp.status_code == 200
    (applied,) = resp.json()["applied"]
    assert applied == {"action": "set_status", "id": "pt1.a1.p1", "status": "confirmed"}
    para = resp.json()["doc"]["parts"][0]["articles"][0]["paragraphs"][0]
    # Text untouched, status flipped.
    assert para["text"].startswith("Section includes wet-pipe")
    assert para["status"] == "confirmed"


def test_manual_delete_edit(monkeypatch):
    client = _client()
    _seed(client, monkeypatch)
    resp = client.post(
        "/api/doc/edit",
        json={"ops": [{"action": "delete", "target_id": "pt1.a1.p1"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["doc"]["parts"][0]["articles"][0]["paragraphs"] == []


def test_invalid_manual_batch_400_and_unchanged(monkeypatch):
    client = _client()
    _seed(client, monkeypatch)
    before = client.get("/api/doc").json()["doc"]

    resp = client.post(
        "/api/doc/edit",
        json={"ops": [{"action": "delete", "target_id": "does-not-exist"}]},
    )
    assert resp.status_code == 400
    assert "no element" in resp.json()["error"]
    # Document (and version count) unchanged.
    assert client.get("/api/doc").json()["doc"] == before


def test_manual_edit_rejected_while_turn_active(monkeypatch):
    from backend.llm.conversation import stream_user_turn

    fake = FakeClient([tool_turn(["Working. "], _SEED_EDITS), text_turn(["Done."])])
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    session = sessions.get_session()
    client = _client()

    gen = stream_user_turn(session, "Start 21 13 13")
    # Advance to mid-turn (turn_active is set right after begin_turn).
    for event in gen:
        if event["type"] == "doc_patch":
            break
    assert session.turn_active is True

    resp = client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {"action": "replace", "target_id": "sec", "text": "HANDS OFF"}
            ]
        },
    )
    assert resp.status_code == 409
    assert "streaming" in resp.json()["error"]

    # Drain the turn; the guard clears and a manual edit now succeeds.
    for _ in gen:
        pass
    assert session.turn_active is False
    ok = client.post(
        "/api/doc/edit",
        json={"ops": [{"action": "set_status", "target_id": "pt1.a1.p1", "status": "confirmed"}]},
    )
    assert ok.status_code == 200


def test_manual_edit_is_undoable(monkeypatch):
    client = _client()
    _seed(client, monkeypatch)
    client.post(
        "/api/doc/edit",
        json={"ops": [{"action": "set_status", "target_id": "pt1.a1.p1", "status": "confirmed"}]},
    )
    undone = client.post("/api/doc/undo")
    assert undone.status_code == 200
    para = undone.json()["doc"]["parts"][0]["articles"][0]["paragraphs"][0]
    assert para["status"] == "assumed"  # back to the seeded value


def test_confirming_removes_block_from_assumptions_schedule(monkeypatch):
    client = _client()
    _seed(client, monkeypatch)

    # Before: the assumed block is scheduled.
    doc = Document(io.BytesIO(client.get("/api/export/docx").content))
    assert "ASSUMPTIONS SCHEDULE" in [p.text for p in doc.paragraphs]
    rows_before = [
        cell.text for t in doc.tables for row in t.rows for cell in row.cells
    ]
    assert any("wet-pipe systems" in c for c in rows_before)

    # Confirm it, then re-export: it drops off the assumptions schedule.
    client.post(
        "/api/doc/edit",
        json={"ops": [{"action": "set_status", "target_id": "pt1.a1.p1", "status": "confirmed"}]},
    )
    doc2 = Document(io.BytesIO(client.get("/api/export/docx").content))
    rows_after = [
        cell.text for t in doc2.tables for row in t.rows for cell in row.cells
    ]
    assert not any("wet-pipe systems" in c for c in rows_after)
