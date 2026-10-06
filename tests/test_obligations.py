"""Obligation anchors and the relocation check behind Final QC's safe fixes."""
from __future__ import annotations

from backend.spec_doc.model import SpecSection, apply_edits
from backend.spec_doc.obligations import (
    _normalized,
    anchor_present,
    obligation_anchors,
    relocation_problems,
)


def test_anchors_are_values_with_units_tags_designations_and_sections():
    anchors = obligation_anchors(
        "Provide Type V1 ball valves rated 175 psi, NPS 2 and smaller, per "
        "NFPA 13-2025 and UL 1091; hydrostatically test at 200 psi for 2 "
        "hours; refer to Section 21 13 13, Article 2.3; provide a 1-1/2 inch "
        "FDC-1 connection with 10 percent spare."
    )
    for expected in (
        "v1",
        "175 psi",
        "nps 2",
        "nfpa 13",
        "2025",
        "ul 1091",
        "200 psi",
        "2 hours",
        "21 13 13",
        "1-1/2 inch",
        "fdc-1",
        "10 percent",
    ):
        assert expected in anchors, expected
    # A cross-reference may be renumbered by an authorized relocation.
    assert "2.3" not in anchors
    assert len(anchors) == len(set(anchors))


def test_prose_numbers_and_ambiguous_units_are_not_anchors():
    assert obligation_anchors("Install 1 in each room.") == ()
    # "13 and" is not a quantity: only the designation is anchored, so a
    # split of the clause cannot be flagged for it.
    assert obligation_anchors(
        "Install valves per NFPA 13 and the manufacturer's instructions."
    ) == ("nfpa 13",)


def test_presence_is_token_bounded_and_spelling_tolerant():
    haystack = _normalized("Test at 175-psi per NFPA 130 and NFPA 13-2025.")
    assert anchor_present("175 psi", haystack)
    assert anchor_present("nfpa 13", haystack)
    assert not anchor_present("nfpa 1", haystack)
    assert not anchor_present("75 psi", haystack)


def _section(ops):
    section, _ = apply_edits(SpecSection.empty(), ops)
    return section


_BASE = [
    {"action": "add_article", "target_id": "pt2", "text": "VALVES"},
    {"action": "add_article", "target_id": "pt3", "text": "INSTALLATION"},
    {
        "action": "add_paragraph",
        "target_id": "pt3.a1",
        "text": "Provide Type V1 ball valves rated 175 psi in accordance with UL 1091.",
    },
]


def _problems(ops, base=_BASE):
    before = _section(base)
    after, _ = apply_edits(before, ops)
    return relocation_problems(before, after, ops)


def test_only_a_fix_that_deletes_and_adds_is_checked():
    # A pure retype or a pure deletion is the verifiers' to judge.
    assert _problems([{"action": "replace", "target_id": "pt3.a1.p1", "text": "Provide ball valves."}]) == []
    assert _problems([{"action": "delete", "target_id": "pt3.a1.p1"}]) == []


def test_a_split_that_carries_every_anchor_passes():
    ops = [
        {"action": "add_paragraph", "target_id": "pt2.a1", "text": "Provide Type V1 ball valves rated 175 psi."},
        {"action": "add_paragraph", "target_id": "pt2.a1", "text": "Ball valves shall comply with UL 1091."},
        {"action": "delete", "target_id": "pt3.a1.p1"},
    ]
    assert _problems(ops) == []


def test_a_split_that_drops_a_value_names_it():
    ops = [
        {"action": "add_paragraph", "target_id": "pt2.a1", "text": "Provide Type V1 ball valves in accordance with UL 1091."},
        {"action": "delete", "target_id": "pt3.a1.p1"},
    ]
    problems = _problems(ops)
    assert problems == ["3.1.A loses '175 psi'"]


def test_deleting_a_whole_article_checks_each_carried_provision():
    ops = [
        {"action": "add_paragraph", "target_id": "pt2.a1", "text": "Provide Type V1 ball valves in accordance with UL 1091."},
        {"action": "delete", "target_id": "pt3.a1"},
    ]
    assert _problems(ops) == ["3.1.A loses '175 psi'"]


def test_a_retype_elsewhere_in_the_same_fix_counts_as_new_text():
    base = _BASE + [
        {"action": "add_paragraph", "target_id": "pt2.a1", "text": "Provide valves."},
    ]
    ops = [
        {"action": "replace", "target_id": "pt2.a1.p1", "text": "Provide Type V1 ball valves in accordance with UL 1091."},
        {"action": "delete", "target_id": "pt3.a1.p1"},
    ]
    assert _problems(ops, base) == ["3.1.A loses '175 psi'"]


def test_an_unrelated_removal_beside_an_addition_is_not_a_relocation():
    ops = [
        {"action": "add_paragraph", "target_id": "pt3.a1", "text": "Refer to Section 21 05 23 for general-duty valves."},
        {"action": "delete", "target_id": "pt3.a1.p1"},
    ]
    assert _problems(ops) == []


def test_malformed_input_is_never_a_crash():
    section = _section(_BASE)
    assert relocation_problems(section, section, None) == []
    assert relocation_problems(section, section, ["delete"]) == []
    assert relocation_problems(
        section, section, [{"action": "delete", "target_id": "missing"}, {"action": "add_paragraph", "text": "x"}]
    ) == []
