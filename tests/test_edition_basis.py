"""An edition enters on the user's word or grounded research, never a guess.

Owner decision (2026-10-12, option A of the prompt-review follow-up). The
generic module used to let the model record a "model-proposed, unverified"
edition from its own training. Once recorded, lint flags other citations
against it and Final QC checks the specification against it — a guess
treated as verified. Now:

1. the generic module's playbook and conventions, and the per-turn standards
   block, allow only the user's statement or a grounded research item, and
   name an earlier session's model-proposed entry as unverified;
2. ``spec_voice.check_edition_bases`` refuses a model batch that records an
   edition on a model-proposed basis (saved histories carry the old wording,
   and a model imitates its history);
3. Final QC's code-compliance lens checks the edition in effect for the
   project, not the newest; a newer edition is a separate observation;
4. research's governing-codes area asks which adoption governs THIS project
   (permit application or vesting date, transition period, existing-building
   code for alterations).
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend import sessions
from backend.app import create_app
from backend.llm.prompts import render_system_prompt
from backend.project_profile import ProjectProfile
from backend.qc.schema import QC_LENSES
from backend.research.engine import build_dimension_user_message
from backend.spec_doc.model import SpecEditError
from backend.spec_doc.spec_voice import check_edition_bases, edition_basis_problems
from backend.spec_modules.generic import GENERIC
from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE
from backend.standards import StandardsBasis, standards_context_block
from tests.fakes import FakeClient, text_turn, tool_turn

OLD_BASIS = "model-proposed, unverified — current published edition per training data"


def _edition(basis: str, *, edition: str = "2024", standard: str = "ASHRAE 90.1"):
    return {
        "action": "set_standard_edition",
        "target_id": "sec",
        "standard": standard,
        "edition": edition,
        "basis": basis,
        "title": "Energy Standard for Sites and Buildings",
    }


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "basis",
    [
        OLD_BASIS,
        "Model proposed edition",
        "model proposal: latest edition",
        "current edition per my training data",
        # Codex review on PR #308: other ways of saying the same thing.
        "current published edition per my knowledge",
        "my best estimate of the current edition",
        "the model\u2019s proposed edition",
        "the model's proposed edition",
        "latest edition from the assistant's own recollection",
    ],
)
def test_a_model_proposed_basis_is_refused(basis):
    problems = edition_basis_problems([_edition(basis)])
    assert len(problems) == 1 and "model proposal" in problems[0]
    with pytest.raises(SpecEditError) as excinfo:
        check_edition_bases([_edition(basis)])
    message = str(excinfo.value)
    assert "the user's statement or a grounded research item id" in message
    assert "without an edition year" in message


@pytest.mark.parametrize(
    "basis",
    [
        "User stated: the City adopted ASHRAE 90.1-2022 with the 2024 IECC",
        "research r-1a2b3c4d5e6f: 2021 VCC, Loudoun County VA",
    ],
)
def test_a_users_statement_or_a_research_item_passes(basis):
    assert edition_basis_problems([_edition(basis)]) == []
    check_edition_bases([_edition(basis)])


def test_a_research_citation_must_name_an_item_the_profile_holds():
    """An invented research id would dress a guess up as research. Checkable,
    so checked; a user's statement cannot be (a "yes" carries no year), so
    the phrase guard is a backstop for the prompt's rule, not its proof."""
    basis = "research r-1a2b3c4d5e6f: 2021 VCC, Loudoun County VA"
    known = frozenset({"r-1a2b3c4d5e6f"})
    assert edition_basis_problems([_edition(basis)], research_ids=known) == []
    problems = edition_basis_problems([_edition(basis)], research_ids=frozenset())
    assert len(problems) == 1
    assert "cites r-1a2b3c4d5e6f, which the research profile does not hold" in problems[0]
    # No research cited, nothing to check.
    assert (
        edition_basis_problems(
            [_edition("User stated the 2022 edition was adopted")],
            research_ids=frozenset(),
        )
        == []
    )


def test_a_chat_batch_citing_research_that_does_not_exist_is_refused(monkeypatch):
    bad = {"edits": [_edition("research r-0123456789ab: adopted 2022 edition")]}
    fake = FakeClient(
        [
            tool_turn([], bad, tool_id="toolu_bad"),
            text_turn(["Which edition has the City adopted?"]),
        ]
    )
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    events = _parse_sse(_client().post("/api/chat", json={"message": "go"}).text)
    assert events[-1]["type"] == "turn_complete"
    refusal = sessions.get_session().history[2]["content"][0]
    assert refusal["is_error"] is True
    assert "which the research profile does not hold" in refusal["content"]
    assert sessions.get_session().doc.doc.edition_overrides == {}


def test_removing_an_override_or_another_op_is_never_refused():
    removal = _edition(OLD_BASIS)
    del removal["edition"]
    blank = _edition(OLD_BASIS, edition="  ")
    other = {"action": "add_article", "target_id": "pt1", "text": OLD_BASIS}
    assert edition_basis_problems([removal, blank, other, "junk", None]) == []
    assert edition_basis_problems("not a list") == []


def _client() -> TestClient:
    return TestClient(create_app())


def _parse_sse(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: ") :])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def test_a_chat_batch_recording_a_guess_is_refused_whole(monkeypatch):
    """The whole batch is refused (edits are all or nothing); the model reads
    why and corrects itself; nothing is recorded."""
    bad = {
        "edits": [
            {"action": "add_article", "target_id": "pt1", "text": "REFERENCES"},
            _edition(OLD_BASIS),
        ]
    }
    good = {
        "edits": [
            {"action": "add_article", "target_id": "pt1", "text": "REFERENCES"},
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "text": "ASHRAE 90.1, Energy Standard for Sites and Buildings.",
                "status": "assumed",
            },
        ]
    }
    fake = FakeClient(
        [
            tool_turn([], bad, tool_id="toolu_bad"),
            tool_turn([], good, tool_id="toolu_good"),
            text_turn(["Which edition of ASHRAE 90.1 has the City adopted?"]),
        ]
    )
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)

    events = _parse_sse(_client().post("/api/chat", json={"message": "go"}).text)
    assert events[-1]["type"] == "turn_complete"
    history = sessions.get_session().history
    refusal = history[2]["content"][0]
    assert refusal["is_error"] is True
    assert "would record an edition nobody has established" in refusal["content"]

    doc = sessions.get_session().doc.doc
    assert doc.edition_overrides == {}
    assert [a.title for a in doc.parts[0].articles] == ["REFERENCES"]


# ---------------------------------------------------------------------------
# What the model is told
# ---------------------------------------------------------------------------


def test_the_generic_prompt_allows_only_the_user_or_research():
    prompt = " ".join(render_system_prompt(GENERIC).split())
    assert "model-proposed, unverified — current published edition" not in prompt
    assert (
        "the user's statement or a grounded research item id — never an "
        "edition you propose from your own knowledge" in prompt
    )
    assert "something to tell the user and have them confirm" in prompt
    # The playbook default for the editions topic.
    assert "never record an edition from your own knowledge" in prompt
    # An earlier session's guess is named for what it is.
    assert 'An entry recorded in an earlier session with a "model-proposed" basis is unverified' in (
        prompt
    )


def test_the_standards_block_names_an_earlier_guess_unverified():
    unpinned = StandardsBasis(label="test-unpinned", unpinned=True)
    overrides = {
        "ASHRAE 90.1": {"edition": "2022", "basis": OLD_BASIS, "title": "Energy"}
    }
    block = standards_context_block(unpinned, overrides)
    # The legacy entry still renders with its basis, so the model can see it…
    assert "ASHRAE 90.1: 2022" in block and OLD_BASIS in block
    # …and is told what to do with it.
    assert "whose basis says it was model-proposed is unverified" in block
    assert "honestly-labeled model proposal" not in block


def test_the_curated_module_block_is_unchanged_by_the_decision():
    """The hyperscale module pins current editions with receipts; only the
    unpinned rendering carried the model-proposal allowance."""
    block = standards_context_block(HYPERSCALE_FIRE.basis, None)
    assert "model-proposed" not in block
    assert "module default (current published edition)" in block


def test_final_qc_checks_the_edition_in_effect_not_the_newest():
    brief = next(lens for lens in QC_LENSES if lens.lens_id == "code_compliance").brief
    assert "ACTUAL current content" not in brief
    assert "the actual text of the edition in effect for this project" in brief
    assert "with any amendments its adoption makes" in brief
    assert "not the newest edition" in brief
    assert (
        "A newer published edition is a separate observation, never a defect "
        "in a citation that matches the edition in effect" in brief
    )


@pytest.mark.parametrize("module", [GENERIC, HYPERSCALE_FIRE])
def test_research_asks_which_adoption_governs_this_project(module):
    dimension = next(
        d for d in module.research_dimensions if d.dimension_id == "governing_codes"
    )
    _shared, task = build_dimension_user_message(
        module,
        ProjectProfile(
            city="Ashburn",
            state_or_province="VA",
            country="US",
            client_name="Example Owner",
        ),
        dimension,
        "Fire suppression",
    )
    text = " ".join(task.split())
    assert "the rules that decide which adoption governs a given project" in text
    assert "when the permit application is filed" in text
    assert "transition period" in text
    assert "existing-building code" in text
    # Research does not know the permit date or whether the work is an
    # alteration (Codex review on PR #308): it reports the rules, and applies
    # one only where established project facts supply the answer.
    assert "Report these as conditional rules" in text
    assert "only where established project facts supply its permit or vesting date" in text
    assert "never treat the edition currently in force as governing merely because it is current" in text
