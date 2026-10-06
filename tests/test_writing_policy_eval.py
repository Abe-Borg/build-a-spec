"""The writing-policy fixtures and assessor, run hermetically.

Each reviewed case is the brief's "realistic fixture with a reviewed
expected outcome": its seed is the "before", its reviewed edit the "after",
and its lossy edits the failures the assessor must catch. The owner-run
evaluation scores live model output with the same assessor, so these tests
are what make its numbers mean something.
"""
from __future__ import annotations

import json

import pytest

from backend.spec_doc.linting import lint_document
from backend.spec_doc.spec_voice import drafted_edit_problems
from backend.spec_modules import get_module
from tools import writing_policy_eval as evaluator

CASES = evaluator.load_cases()


def test_the_fixture_set_covers_every_case_the_brief_names():
    ids = {case.case_id for case in CASES}
    assert ids == {
        "v1_mixed_paragraph",
        "factory_vs_field_testing",
        "test_without_report",
        "schedules_by_function",
        "onsite_fabrication",
        "template_exception",
        "division01_administrative",
        "missing_acceptance_criteria",
        "preserve_qualifiers",
    }
    for case in CASES:
        assert case.raw["instruction"].strip()
        assert case.expected["obligations"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_the_reviewed_outcome_passes_and_writes_specification_text(case):
    before = evaluator.seed_section(case)
    after = evaluator.reviewed_section(case)
    outcome = evaluator.assess(case, before, after)
    assert outcome["passed"], outcome
    # The reviewed edit is something the model is allowed to write…
    assert drafted_edit_problems(case.raw.get("reviewed_edit") or []) == []
    # …and leaves nothing for the lint to report.
    assert lint_document(after, get_module(None)) == []


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_every_lossy_edit_is_caught_for_the_reason_recorded(case):
    before = evaluator.seed_section(case)
    assert case.raw.get("lossy_edits"), case.case_id
    for lossy in case.raw["lossy_edits"]:
        after = evaluator.apply_ops(before, evaluator.lossy_ops(case, lossy))
        outcome = evaluator.assess(case, before, after)
        assert not outcome["passed"], lossy["id"]
        assert lossy["expect"] in outcome["failures"], (lossy["id"], outcome["failures"])


def test_the_before_documents_show_the_misplacements_the_cases_correct():
    misplaced = {
        case.case_id: evaluator.assess(
            case, evaluator.seed_section(case), evaluator.seed_section(case)
        )["failures"]
        for case in CASES
    }
    assert misplaced["v1_mixed_paragraph"] == [
        "obligation:product_data_submittal",
        "obligation:instruction_submittal",
        "obligation:locations",
        "obligation:operator_access",
        "obligation:install_per_instructions",
        "obligation:field_test",
    ]
    assert misplaced["factory_vs_field_testing"] == [
        "obligation:factory_test",
        "obligation:curve_submittal",
        "obligation:field_report_submittal",
    ]
    assert misplaced["onsite_fabrication"] == ["obligation:fabrication"]
    # The cases whose correct answer is "leave it" start out correct.
    for case_id in (
        "test_without_report",
        "template_exception",
        "division01_administrative",
        "missing_acceptance_criteria",
        "preserve_qualifiers",
    ):
        assert misplaced[case_id] == [], case_id


def test_the_offline_report_sends_nothing_and_compares_with_the_baseline(monkeypatch):
    import backend.llm.conversation as conversation

    def refuse():
        raise AssertionError("the offline report must not build a client")

    monkeypatch.setattr(conversation, "get_client", refuse)
    report = evaluator.offline_report()
    assert report["baseline"]["commit"] == "86b6c1f"
    assert report["unmeasured"] == evaluator.UNMEASURED
    current = report["current"]
    for module_id, prompt in current["drafting_system_prompt"].items():
        assert prompt["policy_copies"] == 1, module_id
        assert report["char_deltas"][f"drafting:{module_id}"] > 0
    for module_id, prompts in current["qc_system_prompts"].items():
        assert prompts["lens"]["policy_copies"] == 1
        assert prompts["verifier"]["policy_copies"] == 1
        assert prompts["consolidation"]["policy_copies"] == 0
        assert report["char_deltas"][f"consolidation:{module_id}"] == 0
    for fixture in report["fixtures"]:
        assert fixture["after"] == [], fixture
        assert all(lossy["caught"] for lossy in fixture["lossy"]), fixture
    assert all(counts == {} for counts in report["template_lint"].values())
    json.dumps(report)


def test_the_cli_prints_the_offline_report(capsys):
    assert evaluator.main([]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["current"]["policy"]["label"] == "spec-writing/1"
