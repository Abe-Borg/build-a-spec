"""Step 7 of the prompt-review follow-up: the text the outside review never saw.

The reviewer read the system prompt. The model also reads the tool
descriptions, the instructions inside each turn's PROJECT CONTEXT blocks, and
the output-tool fields of research, Final QC and the fact harvest. Steps 1-6
changed rules there only where a step touched the text directly, so wording
elsewhere still stated the old rule. Each test pins one place now reading the
current rule, and that the old wording is gone:

1. move "content cannot be reparented" — false since relocate (step 5).
2. set_status "use it to confirm an assumed block" — only the user's approval
   confirms (step 4).
3. "never by an id" beside a set_standard_edition basis that must cite a
   research item by its id (step 3).
4. The LINT REPORT and the lint policy called lint advisory, the readiness
   checklist said "advisory lint issue(s)": every issue holds readiness.
5. "until each is confirmed, adapted, or deleted": an adapted block is
   assumed, and assumed holds readiness too.
6. The pinned standards block named only the user's statement as a basis for a
   differing adopted edition; grounded research is the other (step 3).
7. The lint policy, the lint message and the research policy asked for a
   "directive" (step 6: requirements, never commentary), and the research
   policy said never explain "where it applies" (step 6: scope stays).
8. "The export schedules every block still stamped imported": the review
   report does, since the specification export ends at END OF SECTION.
9. record_project_facts and the harvest named editions "confirmed by the user
   or the AHJ" (step 3: the user's statement or a grounded source).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.harvest import HARVEST_SYSTEM_PROMPT
from backend.llm.prompts import render_system_prompt
from backend.project_facts import RECORD_PROJECT_FACTS_TOOL
from backend.qc.engine import _op_vocabulary
from backend.spec_doc.model import APPLY_SPEC_EDITS_TOOL
from backend.spec_modules.generic import GENERIC
from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE
from backend.standards import standards_context_block
from tests.fakes import FakeClient, request_context_text, text_turn


def _flat(text: str) -> str:
    return " ".join(text.split())


@pytest.fixture(params=[GENERIC, HYPERSCALE_FIRE], ids=lambda m: m.module_id)
def prompt(request) -> str:
    return _flat(render_system_prompt(request.param))


def _edit_description() -> str:
    return _flat(APPLY_SPEC_EDITS_TOOL["description"])


def _edit_property(name: str) -> str:
    items = APPLY_SPEC_EDITS_TOOL["input_schema"]["properties"]["edits"]["items"]
    return _flat(items["properties"][name]["description"])


# ---------------------------------------------------------------------------
# 1-3. The edit tool
# ---------------------------------------------------------------------------


def test_move_no_longer_says_content_cannot_be_reparented():
    text = _edit_description()
    assert "content cannot be reparented" not in text
    assert "move never changes an element's parent" in text
    # Final QC echoes the same text as its op vocabulary, and has no
    # relocate: the line is true for both readers.
    assert _flat(_op_vocabulary()) == text
    assert "relocate" in _edit_property("parent_id")


def test_set_status_confirms_only_what_the_user_approved():
    text = _edit_description()
    assert "use it to confirm an assumed block" not in text
    assert (
        "Changes only the provision's status, without retyping its text — "
        "confirmed once the user has approved the provision, or assumed for "
        "an imported block you keep as fitting this project (gap-and-adapt)."
        in text
    )


def test_the_reason_rule_names_the_edition_basis_exception(prompt):
    reason = _edit_property("reason")
    for text in (reason, prompt):
        assert "never by an id" in text or "never by its id" in text
        assert "there a research item is cited by its id" in text
        assert "an edition's basis never reaches Word" in text
    # The operation the exception points at does ask for the id.
    assert "cite the item id in the basis" in _edit_description()


# ---------------------------------------------------------------------------
# 4. Lint holds readiness
# ---------------------------------------------------------------------------


def test_the_lint_policy_says_lint_holds_readiness(prompt):
    assert "Lint is advisory:" not in prompt
    assert (
        "Lint never blocks an edit, but every issue it reports holds the "
        "section's issue readiness until fixed" in prompt
    )


def test_the_lint_report_and_the_checklist_say_so_too(monkeypatch):
    client = TestClient(create_app())
    edited = client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
                {
                    "action": "add_paragraph",
                    "target_id": "pt1.a1",
                    "text": "The Contractor should provide sprinkler heads.",
                },
            ]
        },
    )
    assert edited.status_code == 200, edited.text
    lint = edited.json()["lint"]
    assert lint

    readiness = client.get("/api/readiness").json()
    check = next(c for c in readiness["checks"] if c["id"] == "lint_clean")
    assert check["ok"] is False and check["advisory"] is False
    assert check["detail"] == f"{len(lint)} lint issue(s) to fix."

    fake = FakeClient([text_turn(["Noted."])])
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 200
    context = _flat(request_context_text(fake.messages.requests[0]))
    assert "LINT REPORT (deterministic, advisory" not in context
    assert (
        "LINT REPORT (deterministic; it never blocks an edit, but each issue "
        "holds issue readiness until fixed" in context
    )


# ---------------------------------------------------------------------------
# 6. Editions: the user or grounded research
# ---------------------------------------------------------------------------


def test_the_pinned_standards_block_names_both_bases():
    block = _flat(standards_context_block(HYPERSCALE_FIRE.basis, {}, {}))
    assert "If the user states a jurisdiction-adopted edition" not in block
    assert (
        "If the user states, or a grounded research item establishes, a "
        "jurisdiction-adopted edition that differs, record it with a "
        "set_standard_edition operation (adoption basis required: the user's "
        "statement or the research item id) before drafting to it." in block
    )


# ---------------------------------------------------------------------------
# 7. Requirements, never commentary; scope stays
# ---------------------------------------------------------------------------


def test_the_lint_policy_asks_for_the_requirement_not_a_directive(prompt):
    assert "rewrite the provision as a directive" not in prompt
    assert (
        "rewrite the provision as the requirement alone, without the "
        "explanation" in prompt
    )


def test_the_research_policy_keeps_scope_qualifiers(prompt):
    assert "or explain where it applies." not in prompt
    assert "as a directive in specification voice" not in prompt
    assert (
        "Draft the requirement it imposes in specification voice; never copy "
        "the item's wording, name the jurisdiction or authority behind it, or "
        "explain why it applies (a qualifier that defines where the "
        "requirement applies is scope, and stays)." in prompt
    )


# ---------------------------------------------------------------------------
# 8. The review report, not the export
# ---------------------------------------------------------------------------


def test_gap_and_adapt_names_the_review_report(prompt):
    assert "The export schedules every block still stamped imported" not in prompt
    assert (
        "The review report schedules every block still stamped imported, and "
        "issue readiness holds while one remains" in prompt
    )


# ---------------------------------------------------------------------------
# 9. Facts: an edition's source is the user or grounded research
# ---------------------------------------------------------------------------


def test_record_project_facts_names_the_edition_sources():
    text = _flat(RECORD_PROJECT_FACTS_TOOL["description"])
    assert "or the AHJ" not in text
    assert (
        "adopted codes, editions and amendments the user states or a grounded "
        "research item establishes (never from your own knowledge)" in text
    )


def test_the_harvest_names_the_edition_sources():
    text = _flat(HARVEST_SYSTEM_PROMPT)
    assert "as confirmed by the user or the authority having jurisdiction" not in text
    assert (
        "adopted codes, editions and amendments the user stated (including "
        "what they report the authority having jurisdiction confirmed), or "
        "that an attached document or a research finding marked grounded "
        "establishes;" in text
    )
