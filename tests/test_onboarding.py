"""Batch 6: the guided-tour demo pass.

Onboarding has no drafting pipeline of its own — ``POST /api/onboarding/demo``
only hands the frontend a canned, discipline-parameterized directive, which
the frontend then sends through the ordinary ``/api/chat`` path (the Batch 3
full-draft pattern). These tests cover the endpoint's guards (including the
blank-document gate the full-draft endpoint doesn't have), the directive's
obligations (prompt snapshot), discipline sanitization, the stable-prompt
policy, and one end-to-end small demo turn.
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


# The demo turn as a script: one small tool round per PART (so patches
# stream three times), then the short closing text round. Round 2 plants
# the [TBD: ...] marker and round 3 the needs_input block — the live
# material the tour's open-items and review steps point at.
_DEMO_ROUND_1 = {
    "edits": [
        {
            "action": "replace",
            "target_id": "sec",
            "text": "HYDRONIC PIPING (DEMO)",
            "numbering": "23 21 13",
        },
        {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
        {
            "action": "add_paragraph",
            "target_id": "pt1.a1",
            "text": "Section includes demonstration hydronic piping provisions.",
            "status": "assumed",
        },
    ]
}
_DEMO_ROUND_2 = {
    "edits": [
        {"action": "add_article", "target_id": "pt2", "text": "PIPE MATERIALS"},
        {
            "action": "add_paragraph",
            "target_id": "pt2.a1",
            "text": "Provide Type L copper tube; working pressure "
            "[TBD: system working pressure].",
            "status": "assumed",
        },
    ]
}
_DEMO_ROUND_3 = {
    "edits": [
        {"action": "add_article", "target_id": "pt3", "text": "INSTALLATION"},
        {
            "action": "add_paragraph",
            "target_id": "pt3.a1",
            "text": "Slope piping to accessible drain points.",
            "status": "assumed",
        },
        {
            "action": "add_paragraph",
            "target_id": "pt3.a1",
            "text": "Coordinate routing with structure and other trades.",
            "status": "needs_input",
        },
    ]
}


# ---------------------------------------------------------------------------
# Endpoint guards + payload
# ---------------------------------------------------------------------------


def test_onboarding_demo_returns_discipline_directive():
    resp = _client().post("/api/onboarding/demo", json={"discipline": "Plumbing"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "DEMO pass" in data["message"]
    assert "Plumbing" in data["message"]


def test_onboarding_demo_409_while_turn_active():
    sessions.get_session().turn_active = True
    try:
        resp = _client().post(
            "/api/onboarding/demo", json={"discipline": "Electrical"}
        )
        assert resp.status_code == 409
        assert "streaming" in resp.json()["error"]
    finally:
        sessions.get_session().turn_active = False


def test_onboarding_demo_409_while_research_running():
    sessions.get_session().research.status = "running"
    resp = _client().post("/api/onboarding/demo", json={"discipline": "Electrical"})
    assert resp.status_code == 409
    assert "research" in resp.json()["error"].lower()


def test_onboarding_demo_409_when_doc_not_empty():
    client = _client()
    seeded = client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"}
            ]
        },
    )
    assert seeded.status_code == 200

    resp = client.post("/api/onboarding/demo", json={"discipline": "Plumbing"})
    assert resp.status_code == 409
    assert "blank" in resp.json()["error"].lower()


def test_onboarding_demo_409_when_only_profile_set():
    # A recorded project profile alone makes the document non-empty
    # (SpecSection.is_empty) — the demo gate must track that definition,
    # not "has articles".
    client = _client()
    seeded = client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "set_project_profile",
                    "target_id": "sec",
                    "city": "Phoenix",
                    "state": "Arizona",
                    "country": "USA",
                    "client": "Demo Client",
                }
            ]
        },
    )
    assert seeded.status_code == 200

    resp = client.post("/api/onboarding/demo", json={"discipline": "Plumbing"})
    assert resp.status_code == 409
    assert "blank" in resp.json()["error"].lower()


# ---------------------------------------------------------------------------
# Directive obligations (prompt snapshot) + sanitization + stable policy
# ---------------------------------------------------------------------------


def test_directive_carries_the_demo_obligations():
    from backend.llm.prompts import onboarding_demo_directive

    text = onboarding_demo_directive("Mechanical (HVAC)")
    # Announces itself so the stable-prompt policy can key on it.
    assert "guided-tour DEMO pass" in text
    # Deliberately small: one brief article per PART.
    assert "SMALL" in text
    assert "ONE brief article per PART" in text
    # Sets the section header itself (discipline-appropriate, free-form).
    assert 'replace on "sec"' in text
    # The discipline lands in the drafting instructions.
    assert "Mechanical (HVAC)" in text
    # Provenance discipline + the planted open-item material.
    assert "assumed" in text
    assert "[TBD" in text
    assert "needs_input" in text
    # Later tour steps teach the profile — the demo must not set it.
    assert "Do NOT set the project profile" in text
    # Batching-for-live-streaming obligation.
    assert "apply_spec_edits" in text
    # The tour drives what happens next, not follow-up questions.
    assert "NO follow-up questions" in text


def test_directive_sanitizes_discipline():
    from backend.llm.prompts import onboarding_demo_directive

    # Internal whitespace (including newlines) collapses to single spaces —
    # free text can't break the directive's bullet structure.
    text = onboarding_demo_directive("  Mechanical\n(HVAC)  ")
    assert "Mechanical (HVAC)" in text
    assert "Mechanical\n(HVAC)" not in text

    # Over-long input is capped, not passed through.
    text = onboarding_demo_directive("x" * 500)
    assert "x" * 81 not in text
    assert "x" * 80 in text

    # Empty / whitespace-only falls back to the default discipline.
    for empty in ("", "   ", "\n\t"):
        text = onboarding_demo_directive(empty)
        assert "Fire Protection & Suppression" in text


def test_stable_prompt_carries_onboarding_policy():
    from backend.llm.prompts import render_system_prompt
    from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE

    prompt = render_system_prompt(HYPERSCALE_FIRE)
    assert "Guided-tour demo pass" in prompt
    assert "outside this module's specialty" in prompt
    # It is stable content — no session-varying data leaked in.
    assert "Standards editions in effect" not in prompt


# ---------------------------------------------------------------------------
# End-to-end: the directive drives an ordinary small streaming turn
# ---------------------------------------------------------------------------


def test_demo_directive_drives_a_small_streaming_turn(monkeypatch):
    client = _client()

    # The tour fetches the directive for the chosen discipline…
    message = client.post(
        "/api/onboarding/demo", json={"discipline": "Mechanical (HVAC)"}
    ).json()["message"]

    # …then sends it through the normal chat path.
    fake = FakeClient(
        [
            tool_turn(["Setting up PART 1. "], _DEMO_ROUND_1, tool_id="toolu_ob1"),
            tool_turn(["PART 2. "], _DEMO_ROUND_2, tool_id="toolu_ob2"),
            tool_turn(["PART 3. "], _DEMO_ROUND_3, tool_id="toolu_ob3"),
            text_turn(["Demo section ready — it's a teaching prop."]),
        ]
    )
    _patch_client(monkeypatch, fake)

    resp = client.post("/api/chat", json={"message": message})
    events = _parse_sse(resp.text)

    # Patches streamed one PART at a time, and the turn completed.
    patches = [e for e in events if e["type"] == "doc_patch"]
    assert len(patches) == 3
    assert events[-1]["type"] == "turn_complete"

    # The directive itself is the user turn recorded in history.
    history = sessions.get_session().history
    assert history[0]["content"] == [{"type": "text", "text": message}]

    # One committed version for the whole demo (a single undo step).
    store = sessions.get_session().doc
    assert len(store.versions) == 2 and store.index == 1

    # The planted open-item material is live: one TBD + one needs_input.
    open_events = [e for e in events if e["type"] == "open_questions"]
    assert open_events, "a doc-changing turn must emit open_questions"
    kinds = sorted(item["kind"] for item in open_events[-1]["items"])
    assert kinds == ["needs_input", "tbd"]

    # The demo header the script set survived verbatim.
    doc = client.get("/api/doc").json()["doc"]
    assert doc["section"]["number"] == "23 21 13"
    assert doc["section"]["title"] == "HYDRONIC PIPING (DEMO)"

    # One undo returns to the blank page the demo started from.
    undone = client.post("/api/doc/undo")
    assert undone.status_code == 200
    assert undone.json()["doc"]["section"]["number"] == ""


# ---------------------------------------------------------------------------
# Batch 9: the demo aligns an open-catalog session's discipline with the
# tour's chosen discipline (so PROJECT CONTEXT can't name a stale one).
# ---------------------------------------------------------------------------


def test_demo_sets_discipline_on_a_generic_session():
    client = _client()
    # Start a generic session already carrying a different discipline.
    client.post(
        "/api/session/reset",
        json={"module_id": "generic", "discipline": "Electrical"},
    )
    resp = client.post("/api/onboarding/demo", json={"discipline": "Plumbing"})
    assert resp.status_code == 200
    # The session discipline now matches the demo the tour is about to draft.
    assert sessions.get_session().discipline == "Plumbing"


def test_demo_leaves_a_curated_session_discipline_empty():
    client = _client()
    # Default (curated) module — the invariant keeps discipline "".
    resp = client.post(
        "/api/onboarding/demo", json={"discipline": "Mechanical (HVAC)"}
    )
    assert resp.status_code == 200
    assert sessions.get_session().discipline == ""
