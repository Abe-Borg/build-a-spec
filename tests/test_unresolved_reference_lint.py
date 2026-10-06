"""The unresolved_reference lint: internal references that point nowhere.

The writing policy says an authorized edit updates every reference it
affects. Numbering follows position here, so adding, deleting or relocating
content can leave "Article 2.3" naming nothing. The rule is narrow on
purpose: every lint issue blocks readiness and none can be dismissed, so a
reference that could belong to another document is never flagged.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.spec_doc.linting import RULE_UNRESOLVED_REFERENCE, lint_document
from backend.spec_doc.model import SpecSection, apply_edits
from backend.spec_modules import get_module
from tools import writing_policy_eval as evaluator

MODULE = get_module("hyperscale_fire")
_ROOT = Path(__file__).resolve().parents[1]


def _section(*texts: str, number: str = "21 13 13") -> SpecSection:
    ops = [
        {"action": "replace", "target_id": "sec", "numbering": number, "text": "WET-PIPE SPRINKLER SYSTEMS"},
        {"action": "add_article", "target_id": "pt2", "text": "PIPE AND FITTINGS"},
        {"action": "add_paragraph", "target_id": "pt2.a1", "text": "Provide Schedule 10 steel pipe."},
        {"action": "add_paragraph", "target_id": "pt2.a1.p1", "text": "Roll-grooved ends."},
        {"action": "add_article", "target_id": "pt3", "text": "INSTALLATION"},
    ]
    ops += [
        {"action": "add_paragraph", "target_id": "pt3.a1", "text": text}
        for text in texts
    ]
    section, _ = apply_edits(SpecSection.empty(), ops)
    return section


def _hits(section: SpecSection, **kwargs) -> list[dict]:
    return [
        issue
        for issue in lint_document(section, MODULE, **kwargs)
        if issue["rule"] == RULE_UNRESOLVED_REFERENCE
    ]


@pytest.mark.parametrize(
    "text, missing",
    [
        ("Install pipe specified in Article 2.2.", ["2.2"]),
        ("Install pipe specified in Articles 2.1 and 2.4.", ["2.4"]),
        ("Install fittings as specified in Paragraph 2.1.B.", ["2.1.B"]),
        ("Install fittings as specified in Paragraph 2.1.A.2.", ["2.1.A.2"]),
        ("Comply with Articles 2.1 through 2.6.", ["2.6"]),
    ],
)
def test_a_reference_this_section_does_not_have_is_flagged(text, missing):
    hits = _hits(_section(text))
    assert [hit["match"] for hit in hits] == missing
    assert hits[0]["element_id"] == "pt3.a1.p1"
    assert hits[0]["ref"] == "3.1.A"
    assert "this section does not have" in hits[0]["message"]


@pytest.mark.parametrize(
    "text",
    [
        "Install pipe specified in Article 2.1.",
        "Install pipe specified in Article 2.01.",
        "Install ends as specified in Paragraph 2.1.A.1.",
        "Install ends as specified in paragraph 2.1.a.1.",
        # Another document's articles.
        "Comply with Section 21 05 29, Article 2.5.",
        "Comply with Section \"Hangers and Supports,\" Article 2.5.",
        "Comply with Article 2.5 of NFPA 13.",
        "Comply with Article 3.4 in the General Conditions.",
        "Comply with NFPA 13 and Article 2.5 requirements.",
        "Comply with IBC §903.4.2 (Alarms) and Article 3.4.",
        "Comply with Division 01 requirements and Article 1.6.",
        "Comply with Article 1.6 of the Contract.",
        # Not a PART 1-3 article number.
        "Comply with NFPA 70, Article 695.",
        "Comply with Article 5.2.",
    ],
)
def test_references_that_resolve_or_belong_elsewhere_are_never_flagged(text):
    assert _hits(_section(text)) == []


def test_a_relocation_that_renumbers_a_reference_is_caught():
    section = _section("Install pipe specified in Article 2.2.")
    section, _ = apply_edits(
        section,
        [
            {"action": "add_article", "target_id": "pt2", "text": "VALVES"},
            {"action": "add_paragraph", "target_id": "pt2.a2", "text": "Provide ball valves."},
        ],
    )
    assert _hits(section) == []
    section, _ = apply_edits(section, [{"action": "delete", "target_id": "pt2.a2"}])
    assert [hit["match"] for hit in _hits(section)] == ["2.2"]


def test_locked_blocks_unstructured_imports_and_division_00_are_skipped():
    section = _section("Install pipe specified in Article 2.2.")
    assert _hits(section, unstructured_import=True) == []
    section.parts[2].articles[0].paragraphs[0].locked = "table"
    assert _hits(section) == []
    assert _hits(_section("Comply with Article 2.2.", number="00 72 00")) == []


def test_no_false_positives_across_the_fixtures_and_curated_templates():
    for case in evaluator.load_cases():
        for section in (evaluator.seed_section(case), evaluator.reviewed_section(case)):
            assert _hits(section) == [], case.case_id
    for path in sorted((_ROOT / "backend" / "templates" / "curated").glob("*.bastemplate")):
        data = json.loads(path.read_text(encoding="utf-8"))
        section = SpecSection.from_dict(data["document"])
        module = get_module(data.get("module_id"))
        assert [
            issue
            for issue in lint_document(section, module)
            if issue["rule"] == RULE_UNRESOLVED_REFERENCE
        ] == [], path.name
