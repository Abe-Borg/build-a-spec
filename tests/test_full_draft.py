"""Batch 3, WI1: the full-section draft pass.

The pass has no dedicated drafting pipeline — ``POST /api/draft/full`` only
hands the frontend a canned directive, which the frontend then sends through
the ordinary ``/api/chat`` path. These tests cover the endpoint's guards and
payload, the directive's obligations (prompt snapshot), the stable-prompt
policy, and one end-to-end multi-round turn driven by the directive.

They also cover the prerequisite gate: a whole-section draft anchors on the
section, the project type, and the country, so with any of the three unknown
the endpoint returns a directive that COLLECTS them instead of one that
drafts blind.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend.app import create_app
from backend import sessions
from tests.fakes import FakeClient, text_turn, tool_turn


def _client() -> TestClient:
    return TestClient(create_app())


# The three facts a full draft anchors on, as the ops that record them —
# the same vocabulary the model's tool and the panel's controls use.
_SECTION_OP = {
    "action": "replace",
    "target_id": "sec",
    "text": "WET-PIPE SPRINKLER SYSTEMS",
    "numbering": "21 13 13",
}
_PROJECT_TYPE_OP = {
    "action": "set_project_identity",
    "target_id": "sec",
    "project_type": "Hyperscale Data Center",
}
_COUNTRY_OP = {
    "action": "set_project_profile",
    "target_id": "sec",
    "country": "United States",
}


def _establish(client: TestClient, *ops: dict) -> None:
    """Record prerequisite facts through the real manual-edit path."""
    resp = client.post("/api/doc/edit", json={"ops": list(ops)})
    assert resp.status_code == 200, resp.text


def _establish_all(client: TestClient) -> None:
    _establish(client, _SECTION_OP, _PROJECT_TYPE_OP, _COUNTRY_OP)


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
    client = _client()
    _establish_all(client)
    resp = client.post("/api/draft/full")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["ready"] is True
    assert data["missing"] == []
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
# The prerequisite gate: section + project type + country
# ---------------------------------------------------------------------------


def test_prerequisites_derive_from_the_three_identity_facts():
    from backend.llm.prompts import draft_prerequisites

    blank = draft_prerequisites()
    assert blank.ready is False
    # Fixed order, so every surface lists the same gaps the same way.
    assert blank.missing == ("section", "project_type", "country")

    full = draft_prerequisites(
        section_number="21 13 13",
        section_title="WET-PIPE SPRINKLER SYSTEMS",
        project_type="Hyperscale Data Center",
        country="United States",
    )
    assert full.ready is True and full.missing == ()
    assert full.section == "21 13 13 WET-PIPE SPRINKLER SYSTEMS"
    # The country is resolved to the app's display form, not echoed raw.
    assert full.country == "USA"


def test_a_half_set_section_header_is_not_a_section():
    """Number and title are written as a pair; half of one is not an anchor."""
    from backend.llm.prompts import draft_prerequisites

    kwargs = {"project_type": "Hospital", "country": "CA"}
    assert draft_prerequisites(section_number="21 13 13", **kwargs).missing == (
        "section",
    )
    assert draft_prerequisites(section_title="SPRINKLERS", **kwargs).missing == (
        "section",
    )
    assert draft_prerequisites(
        section_number="  ", section_title="\t", **kwargs
    ).missing == ("section",)
    assert draft_prerequisites(
        section_number="21 13 13", section_title="SPRINKLERS", **kwargs
    ).ready


def test_an_unsupported_country_reads_as_missing_not_as_known():
    """A hand-edited project file must not draft against a code family the
    app cannot support — set_project_profile refuses these, but a legacy or
    edited file can still carry free text."""
    from backend.llm.prompts import draft_prerequisites

    prereqs = draft_prerequisites(
        section_number="21 13 13",
        section_title="SPRINKLERS",
        project_type="Data Center",
        country="Mexico",
    )
    assert prereqs.missing == ("country",)
    assert prereqs.country == ""


def test_draft_full_collects_the_missing_facts_instead_of_drafting_blind():
    client = _client()
    resp = client.post("/api/draft/full")

    # Not an error: the click succeeded, and the payload says what happens.
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["ready"] is False
    assert data["missing"] == ["section", "project_type", "country"]

    message = data["message"]
    assert not message.startswith("Draft the COMPLETE section")
    # It asks for exactly the three facts, by the ops that record them.
    assert "CSI section" in message
    assert "set_project_identity" in message
    assert "set_project_profile" in message
    # …and forbids drafting on this turn.
    assert "Do NOT draft the section in this turn" in message


def test_the_collection_directive_only_asks_for_what_is_missing():
    client = _client()
    _establish(client, _SECTION_OP, _COUNTRY_OP)

    data = client.post("/api/draft/full").json()
    assert data["ready"] is False
    assert data["missing"] == ["project_type"]

    message = data["message"]
    # The one gap is asked for…
    assert "set_project_identity" in message
    # …and the settled facts are named as settled, so the turn is not spent
    # re-asking them.
    assert "do not re-ask" in message.lower()
    assert "- SECTION: 21 13 13 WET-PIPE SPRINKLER SYSTEMS" in message
    assert "- COUNTRY: USA" in message
    assert "set_project_profile" not in message
    assert "one more thing" in message


def test_the_draft_directive_anchors_on_the_established_facts():
    client = _client()
    _establish_all(client)

    message = client.post("/api/draft/full").json()["message"]
    # The obligations are unchanged — the anchor is appended, not a rewrite.
    from backend.llm.prompts import FULL_DRAFT_DIRECTIVE

    assert message.startswith(FULL_DRAFT_DIRECTIVE)
    assert "- SECTION: 21 13 13 WET-PIPE SPRINKLER SYSTEMS" in message
    assert "- PROJECT TYPE (facility/use): Hyperscale Data Center" in message
    assert "- COUNTRY: USA" in message


def test_the_doc_payload_reports_the_gate_for_the_panel():
    """One server-side derivation feeds both the tooltip and the endpoint."""
    client = _client()

    report = client.get("/api/doc").json()["draft_prerequisites"]
    assert report["ready"] is False
    assert report["missing"] == ["section", "project_type", "country"]
    # Records, not parallel arrays — label and value cannot misalign.
    assert [r["id"] for r in report["requirements"]] == [
        "section",
        "project_type",
        "country",
    ]
    assert all(r["satisfied"] is False for r in report["requirements"])
    assert all(r["label"] for r in report["requirements"])

    _establish_all(client)
    report = client.get("/api/doc").json()["draft_prerequisites"]
    assert report["ready"] is True and report["missing"] == []
    values = {r["id"]: r["value"] for r in report["requirements"]}
    assert values["section"] == "21 13 13 WET-PIPE SPRINKLER SYSTEMS"
    assert values["project_type"] == "Hyperscale Data Center"
    assert values["country"] == "USA"


def test_the_panel_report_and_the_endpoint_never_disagree():
    """The payload is guidance; the endpoint decides. They share one
    derivation, so a click can never draft when the panel said it would
    ask (or ask when the panel said it would draft)."""
    client = _client()
    for ops in (
        (),
        (_SECTION_OP,),
        (_PROJECT_TYPE_OP,),
        (_COUNTRY_OP,),
        (_SECTION_OP, _COUNTRY_OP),
        (_SECTION_OP, _PROJECT_TYPE_OP, _COUNTRY_OP),
    ):
        client.post("/api/session/reset")
        if ops:
            _establish(client, *ops)
        report = client.get("/api/doc").json()["draft_prerequisites"]
        plan = client.post("/api/draft/full").json()
        assert report["ready"] == plan["ready"]
        assert report["missing"] == plan["missing"]


def test_undoing_the_prerequisites_reopens_the_gate():
    """The gate reads live document state — it never latches once satisfied."""
    client = _client()
    _establish_all(client)
    assert client.post("/api/draft/full").json()["ready"] is True

    client.post("/api/doc/undo")
    plan = client.post("/api/draft/full").json()
    assert plan["ready"] is False
    assert plan["missing"] == ["section", "project_type", "country"]


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

    # The prerequisites are known, so the click buys a draft, not questions.
    _establish_all(client)
    plan = client.post("/api/draft/full").json()
    assert plan["ready"] is True
    message = plan["message"]

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

    # One committed version for the whole pass (a single undo step) — on top
    # of the empty snapshot and the one the prerequisite setup committed.
    store = sessions.get_session().doc
    assert len(store.versions) == 3 and store.index == 2

    # Provenance from the script survived verbatim.
    doc = client.get("/api/doc").json()["doc"]
    assert doc["section"]["number"] == "21 13 13"
    pt1_p1 = doc["parts"][0]["articles"][0]["paragraphs"][0]
    assert pt1_p1["status"] == "assumed"
    pt2 = doc["parts"][1]["articles"][0]["paragraphs"]
    assert pt2[0]["status"] == "confirmed"
    assert pt2[1]["status"] == "needs_input"

    # One undo unwinds the entire multi-round pass — back to the pre-draft
    # page, which still holds the prerequisites the draft was anchored on.
    undone = client.post("/api/doc/undo")
    assert undone.status_code == 200
    reverted = undone.json()["doc"]
    assert all(not part["articles"] for part in reverted["parts"])
    assert reverted["section"]["number"] == "21 13 13"
