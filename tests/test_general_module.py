"""The domain-neutral general module: it is the default, its prompt carries no
fixed-catalog cage or fire steering, it drafts an arbitrary section, the
per-turn AUTHORING anchor tracks the chosen section, and its research
dimensions target the section being authored."""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend import sessions
from backend.app import create_app
from backend.llm.prompts import render_system_prompt
from backend.project_profile import ProjectProfile
from backend.research.engine import build_dimension_user_message
from backend.spec_modules import DEFAULT_MODULE, GENERAL, HYPERSCALE_FIRE
from tests.fakes import FakeClient, request_context_text, text_turn, tool_turn


def _client() -> TestClient:
    return TestClient(create_app())


def _patch_client(monkeypatch, fake) -> None:
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)


def test_general_is_the_default_and_prompt_is_open():
    assert DEFAULT_MODULE is GENERAL
    prompt = render_system_prompt(GENERAL)
    # No fixed-catalog cage and no fire content.
    assert "steer toward the first" not in prompt
    assert "21 13 13" not in prompt
    assert "Wet-Pipe" not in prompt
    # It explicitly invites any section, in any discipline.
    assert "not tied to a fixed list of sections" in prompt
    assert "author whatever CSI SectionFormat section the user names" in prompt
    # Stable / deterministic (it carries cache_control in the request).
    assert prompt == render_system_prompt(GENERAL)


_DRAFT_TAB = {
    "edits": [
        {
            "action": "replace",
            "target_id": "sec",
            "text": "TESTING, ADJUSTING, AND BALANCING FOR HVAC",
            "numbering": "23 05 93",
        },
        {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
        {
            "action": "add_paragraph",
            "target_id": "pt1.a1",
            "text": "Provide testing, adjusting, and balancing of HVAC systems.",
            "status": "assumed",
        },
    ]
}


def test_general_drafts_an_arbitrary_section_and_anchors_it(monkeypatch):
    client = _client()

    # With a blank document the AUTHORING anchor says no section is chosen.
    blank = FakeClient([text_turn(["Hello."])])
    _patch_client(monkeypatch, blank)
    client.post("/api/chat", json={"message": "hi"})
    assert "no section chosen yet" in request_context_text(
        blank.messages.last_request
    )

    # Nothing constrains the header to the fire catalog — draft Division 23.
    draft = FakeClient(
        [tool_turn(["Drafting TAB."], _DRAFT_TAB), text_turn(["Done."])]
    )
    _patch_client(monkeypatch, draft)
    client.post("/api/chat", json={"message": "Let's write 23 05 93."})
    doc = sessions.get_session().doc.doc
    assert doc.number == "23 05 93"
    assert "TESTING, ADJUSTING" in doc.title

    # The next turn's PROJECT CONTEXT anchors on the drafted section, and the
    # empty basis surfaces intentionally (no fire pins forced in).
    nxt = FakeClient([text_turn(["Continuing."])])
    _patch_client(monkeypatch, nxt)
    client.post("/api/chat", json={"message": "continue"})
    context = request_context_text(nxt.messages.last_request)
    assert "AUTHORING: Section 23 05 93" in context
    assert "none pinned by default" in context
    assert "NFPA 13" not in context


def test_general_research_message_targets_the_section():
    profile = ProjectProfile("Phoenix", "AZ", "USA", "AcmeCo")
    dim = next(
        d
        for d in GENERAL.research_dimensions
        if d.dimension_id == "governing_codes"
    )
    msg = build_dimension_user_message(
        GENERAL,
        profile,
        dim,
        section_number="23 05 93",
        section_title="Testing, Adjusting, and Balancing for HVAC",
    )
    assert "23 05 93" in msg
    assert "Testing, Adjusting, and Balancing for HVAC" in msg
    assert "Phoenix" in msg
    # Fire's templates don't reference the section placeholders, but formatting
    # with the extra kwargs still succeeds (str.format ignores unused keys).
    fire_dim = next(
        d
        for d in HYPERSCALE_FIRE.research_dimensions
        if d.dimension_id == "governing_codes"
    )
    fire_msg = build_dimension_user_message(
        HYPERSCALE_FIRE,
        profile,
        fire_dim,
        section_number="23 05 93",
        section_title="irrelevant to fire template",
    )
    assert "governing building and fire codes" in fire_msg


def test_switch_module_endpoint_gates_and_switches(monkeypatch):
    client = _client()
    # Default is general.
    assert client.get("/api/health").json()["module_id"] == "general"

    # Switch to fire on a blank document.
    resp = client.post("/api/module", json={"module_id": "hyperscale_fire"})
    assert resp.status_code == 200 and resp.json()["module_id"] == "hyperscale_fire"
    assert sessions.get_session().module is HYPERSCALE_FIRE

    # Unknown module → 400 (never silently degrades).
    assert client.post("/api/module", json={"module_id": "nope"}).status_code == 400

    # With content on the page, switching is refused (409).
    edits = {
        "ops": [
            {
                "action": "replace",
                "target_id": "sec",
                "text": "WET-PIPE SPRINKLER SYSTEMS",
                "numbering": "21 13 13",
            }
        ]
    }
    assert client.post("/api/doc/edit", json=edits).status_code == 200
    assert not sessions.get_session().doc.doc.is_empty()
    blocked = client.post("/api/module", json={"module_id": "general"})
    assert blocked.status_code == 409
