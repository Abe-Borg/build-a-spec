"""Batch 3, WI1: the full-section draft pass.

The pass has no dedicated drafting pipeline — ``POST /api/draft/full`` only
hands the frontend a canned directive, which the frontend then sends through
the ordinary ``/api/chat`` path. These tests cover the endpoint's guards and
payload, the directive's obligations (prompt snapshot), the stable-prompt
policy, and one end-to-end multi-round turn driven by the directive.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend.app import create_app
from backend import sessions
from tests.fakes import FakeClient, text_turn, tool_turn


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


# The full-draft turn as a script: two tool rounds (so patches stream twice)
# then a closing text round — each round carries distinct provenance.
_DRAFT_ROUND_1 = {
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
            "text": "Section includes wet-pipe automatic sprinkler systems.",
            "status": "assumed",
        },
    ]
}
_DRAFT_ROUND_2 = {
    "edits": [
        {"action": "add_article", "target_id": "pt2", "text": "SPRINKLERS"},
        {
            "action": "add_paragraph",
            "target_id": "pt2.a1",
            "text": "Provide UL-listed automatic sprinklers.",
            "status": "confirmed",
        },
        {
            "action": "add_paragraph",
            "target_id": "pt2.a1",
            "text": "Temperature rating [TBD: rating].",
            "status": "needs_input",
        },
    ]
}


# ---------------------------------------------------------------------------
# Endpoint guards + payload
# ---------------------------------------------------------------------------


def test_draft_full_returns_the_directive():
    resp = _client().post("/api/draft/full")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["message"].startswith("Draft the COMPLETE section")


def test_draft_full_409_while_turn_active():
    sessions.get_session().turn_active = True
    try:
        resp = _client().post("/api/draft/full")
        assert resp.status_code == 409
        assert "streaming" in resp.json()["error"]
    finally:
        sessions.get_session().turn_active = False


def test_draft_full_409_while_research_running():
    sessions.get_session().research.status = "running"
    resp = _client().post("/api/draft/full")
    assert resp.status_code == 409
    assert "research" in resp.json()["error"].lower()


# ---------------------------------------------------------------------------
# Directive obligations (prompt snapshot) + stable-prompt policy
# ---------------------------------------------------------------------------


def test_directive_carries_the_provenance_and_batching_obligations():
    from backend.llm.prompts import FULL_DRAFT_DIRECTIVE

    text = FULL_DRAFT_DIRECTIVE
    # Draft-the-whole-thing obligation.
    assert "COMPLETE section" in text
    assert "every PART" in text and "every article" in text
    # Provenance discipline: all three stamps + the TBD marker.
    assert "confirmed" in text
    assert "assumed" in text
    assert "needs_input" in text
    assert "[TBD" in text
    # Use established facts + research provenance link.
    assert "project profile" in text
    assert "source_item_id" in text
    # Batching-for-live-streaming obligation.
    assert "apply_spec_edits" in text
    # Close with a summary + follow-up questions.
    assert "follow-up question" in text


def test_stable_prompt_carries_the_full_draft_policy():
    from backend.llm.prompts import render_system_prompt
    from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE

    prompt = render_system_prompt(HYPERSCALE_FIRE)
    assert "Full-section draft pass" in prompt
    assert "breadth-first" in prompt
    # It is stable content — no session-varying data leaked in.
    assert "Standards editions in effect" not in prompt


# ---------------------------------------------------------------------------
# End-to-end: the directive drives an ordinary multi-round turn
# ---------------------------------------------------------------------------


def test_directive_drives_a_streaming_multi_round_draft(monkeypatch):
    client = _client()

    # The frontend fetches the directive…
    message = client.post("/api/draft/full").json()["message"]

    # …then sends it through the normal chat path.
    fake = FakeClient(
        [
            tool_turn(["Laying down the skeleton. "], _DRAFT_ROUND_1, tool_id="toolu_d1"),
            tool_turn(["Now the products. "], _DRAFT_ROUND_2, tool_id="toolu_d2"),
            text_turn(["Complete first pass. Two questions remain."]),
        ]
    )
    _patch_client(monkeypatch, fake)

    resp = client.post("/api/chat", json={"message": message})
    events = _parse_sse(resp.text)

    # Patches streamed article-by-article, not one mega-batch at the end.
    patches = [e for e in events if e["type"] == "doc_patch"]
    assert len(patches) == 2
    assert events[-1]["type"] == "turn_complete"

    # The directive itself is the user turn recorded in history.
    history = sessions.get_session().history
    assert history[0]["content"] == [{"type": "text", "text": message}]

    # One committed version for the whole pass (a single undo step).
    store = sessions.get_session().doc
    assert len(store.versions) == 2 and store.index == 1

    # Provenance from the script survived verbatim.
    doc = client.get("/api/doc").json()["doc"]
    assert doc["section"]["number"] == "21 13 13"
    pt1_p1 = doc["parts"][0]["articles"][0]["paragraphs"][0]
    assert pt1_p1["status"] == "assumed"
    pt2 = doc["parts"][1]["articles"][0]["paragraphs"]
    assert pt2[0]["status"] == "confirmed"
    assert pt2[1]["status"] == "needs_input"

    # One undo returns to the blank page (import/draft symmetry).
    undone = client.post("/api/doc/undo")
    assert undone.status_code == 200
    assert undone.json()["doc"]["section"]["number"] == ""
