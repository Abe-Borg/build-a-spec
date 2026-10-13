"""A provision is confirmed only when a person approved it.

Owner decision (2026-10-13, option A of step 4 of the prompt-review
follow-up). "Confirmed" meant two things: on a provision, the user stated or
approved it; on a project fact, the user stated it OR a grounded source
(research, an attached document, Final QC) established it. And the prompt said
"a provision drafted from a confirmed fact may be stamped confirmed", so a
research finding could become a confirmed provision no person had looked at —
one that never reaches the Review drawer, and that readiness's
``no_assumed_left`` never holds back. Now:

1. a provision is confirmed only on the user's word: a research item, an
   attached document, a Final QC finding, or a fact one of them established
   (or an assumed fact) is a source to cite, never an approval;
2. a fact's "confirmed" keeps its meaning (established), and the prompt says
   so beside the provision rule;
3. an assumed fact is a working default, not settled input: the old "do not
   re-ask or re-derive" covered every fact, assumed ones included.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend import sessions
from backend.app import create_app
from backend.llm.prompts import (
    ADAPT_IMPORTED_DIRECTIVE,
    FULL_DRAFT_DIRECTIVE,
    render_system_prompt,
)
from backend.project_facts import ProjectFactStore
from backend.spec_doc.model import APPLY_SPEC_EDITS_TOOL
from backend.spec_modules.generic import GENERIC
from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE
from tests.fakes import FakeClient, text_turn


def _flat(text: str) -> str:
    return " ".join(text.split())


@pytest.fixture(params=[GENERIC, HYPERSCALE_FIRE], ids=lambda m: m.module_id)
def prompt(request) -> str:
    return _flat(render_system_prompt(request.param))


def test_only_the_user_confirms_a_provision(prompt):
    assert (
        "confirmed — the user stated it, or explicitly approved your proposal. "
        "Nothing else confirms a provision." in prompt
    )
    assert (
        "A research item, an attached document, a Final QC finding, and a "
        "project fact that one of them established (or that is itself "
        "assumed) are sources to cite, not approvals: a provision drafted "
        "from one is assumed, with that source as source_item_id, until the "
        "user approves it." in prompt
    )
    assert "A project fact the user stated (source: user, confirmed) counts as their statement." in prompt


def test_a_fact_no_longer_confirms_the_provision_drafted_from_it(prompt):
    assert "may be stamped confirmed" not in prompt
    assert (
        "A fact's \"confirmed\" means established; a provision's means a "
        "person approved it." in prompt
    )
    assert (
        "stamp the provision confirmed only when the user stated that fact "
        "(source: user, confirmed) or approved the provision, and assumed when "
        "the fact rests on research, an attached document or Final QC, or is "
        "itself assumed" in prompt
    )
    # The research policy's citation line no longer reads as a confirmation.
    assert "when a provision follows a confirmed fact" not in prompt
    assert (
        "A citation shows where a provision came from; it never confirms it"
        in prompt
    )


def test_an_assumed_fact_is_not_settled_input(prompt):
    assert "Treat the ESTABLISHED PROJECT FACTS block as settled input" not in prompt
    assert (
        "A confirmed fact is settled input: do not re-ask what it states, and "
        "do not re-derive it." in prompt
    )
    assert (
        "An assumed fact is a working default, not a decision: draft to it, "
        "stamp what rests on it assumed, and raise it with the user once, in "
        "one line, when a provision you draft depends on it — when they "
        "agree, supersede it with the same statement recorded as confirmed."
        in prompt
    )
    # A panel edit is the user's word; "treat what it shows as settled" would
    # have settled an assumed fact the panel shows too.
    assert "treat what it shows as settled" not in prompt
    assert "a change made there is the user's own word" in prompt


def test_an_attached_document_alone_confirms_nothing(prompt):
    assert "confirmed when the user directed it, assumed when you inferred it" not in prompt
    assert (
        "confirmed only when the user directed that requirement or approved "
        "the provision, assumed when you drew it from the document yourself — "
        "the document alone confirms nothing." in prompt
    )


def test_the_whole_section_passes_say_the_same():
    full = _flat(FULL_DRAFT_DIRECTIVE)
    assert "a confirmed project fact" not in full
    assert "or an established project fact, tag it" in full
    assert (
        "assumed for whatever you drew from research, an attached document or "
        "a project fact I did not state myself" in full
    )
    adapt = _flat(ADAPT_IMPORTED_DIRECTIVE)
    assert "where I've established it" not in adapt
    assert "set_status to confirmed where I've stated or approved it" in adapt


def test_the_edit_tools_status_property_says_it_at_the_point_of_use():
    item = APPLY_SPEC_EDITS_TOOL["input_schema"]["properties"]["edits"]["items"]
    description = item["properties"]["status"]["description"]
    assert (
        "confirmed only for what the user stated or approved — a source you "
        "cite is not an approval." in description
    )
    # Final QC echoes only the tool's top-level description, which did not
    # change, so retained QC reports stay current.
    assert "a source you cite is not an approval" not in APPLY_SPEC_EDITS_TOOL["description"]


def _store() -> ProjectFactStore:
    store = ProjectFactStore()
    store.record(
        {
            "statement": "The owner requires a 1-hour fire barrier at the battery room.",
            "status": "confirmed",
            "source_kind": "user",
        },
        recorded_in="21 13 13",
        recorded_at="2026-10-13",
    )
    store.record(
        {
            "statement": "The AHJ requires a 24-hour hydrostatic test notice.",
            "status": "confirmed",
            "source_kind": "research",
            "source_ref": "r-1a2b3c4d5e6f",
        },
        recorded_in="21 13 13",
        recorded_at="2026-10-13",
    )
    store.record(
        {"statement": "Municipal supply with a fire pump.", "status": "assumed"},
        recorded_in="21 13 13",
        recorded_at="2026-10-13",
    )
    return store


def test_the_facts_block_tells_the_two_kinds_apart():
    block = _flat(_store().context_block(current_section="21 13 13"))
    # Each fact's status and source still render, so the model can tell a
    # user's statement from a research-established fact.
    assert "[project, confirmed] The owner requires" in block and "source: user" in block
    assert "source: research r-1a2b3c4d5e6f" in block
    assert "[project, assumed] Municipal supply" in block
    assert "Do not re-ask or re-derive these." not in block
    assert "A provision drafted from a confirmed fact may cite" not in block
    assert (
        "Do not re-ask or re-derive the confirmed ones. An [assumed] fact is a "
        "working default: raise it with the user once, in one line, when a "
        "provision you draft rests on it." in block
    )
    assert (
        "A provision drafted from a fact cites its id as source_item_id, and "
        "is stamped confirmed only when the user stated that fact (source: "
        "user, confirmed) or approved the provision." in block
    )


def _parse_sse(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: ") :])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def test_a_chat_request_carries_the_rule_in_all_three_places(monkeypatch):
    """The stable prompt, the edit tool's status property, and this turn's
    facts block all reach the model on an ordinary turn."""
    session = sessions.get_session()
    session.facts = _store()
    fake = FakeClient([text_turn(["Which edition has the City adopted?"])])
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)

    events = _parse_sse(TestClient(create_app()).post("/api/chat", json={"message": "go"}).text)
    assert events[-1]["type"] == "turn_complete"
    request = fake.messages.last_request
    system = _flat(" ".join(block["text"] for block in request["system"]))
    assert "Nothing else confirms a provision." in system
    edit_tool = next(t for t in request["tools"] if t.get("name") == "apply_spec_edits")
    status = edit_tool["input_schema"]["properties"]["edits"]["items"]["properties"]["status"]
    assert "a source you cite is not an approval" in status["description"]
    newest = request["messages"][-1]["content"]
    context = _flat(" ".join(b.get("text", "") for b in newest if isinstance(b, dict)))
    assert "Do not re-ask or re-derive the confirmed ones." in context
