"""Specification voice, advisory half: explanatory prose and REFERENCES shape.

Owner rule (2026-10-06): a provision directs the Contractor and never
explains, and a REFERENCES entry is one standard's designation, title and
edition. ``spec_voice`` blocks placeholders outright (tests/test_spec_voice.py);
explanation is a matter of degree, so the lint reports it instead — these
tests pin the owner's phrase list, the phrases deliberately left alone, and
the owner's own examples both ways.
"""
from __future__ import annotations

import pytest

from backend.llm.prompts import render_system_prompt
from backend.qc.schema import QC_LENS_BY_ID
from backend.spec_doc.linting import (
    RULE_EXPLANATORY_PROSE,
    RULE_REFERENCE_ENTRY,
    lint_document,
)
from backend.spec_doc.model import SpecSection, apply_edits
from backend.spec_doc.spec_voice import (
    REFERENCE_ENTRY_MAX_CHARS,
    explanatory_prose_hits,
    reference_entry_problems,
)
from backend.spec_modules.generic import GENERIC
from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE

_VOICE_RULES = {RULE_EXPLANATORY_PROSE, RULE_REFERENCE_ENTRY}

# The owner's three drafts, and the corrections they gave.
_WATERFLOW_EXPLAINS = (
    "Virginia amends IBC 903.4.2 (Alarms) to require an approved audible "
    "device connected to each automatic sprinkler system, actuated by water "
    "flow equivalent to a single sprinkler of the smallest orifice size "
    "installed in the system, located on the exterior of the building in an "
    "approved location. Where a fire alarm system is installed, actuation of "
    "the automatic sprinkler system shall also actuate the building fire "
    "alarm system. This amendment applies generally and governs the "
    "waterflow alarm and fire-alarm actuation linkage for each of this "
    "project's sprinkler riser rooms; refer to Section 21 10 00 for the "
    "waterflow alarm device and Section 28 31 00 for fire alarm system "
    "actuation."
)
_WATERFLOW_DIRECTS = (
    "Provide an approved audible device connected to each automatic "
    "sprinkler system, actuated by water flow equivalent to a single "
    "sprinkler of the smallest orifice size installed in the system, located "
    "on the exterior of the building in an approved location. Actuation of "
    "the automatic sprinkler system shall also actuate the building fire "
    "alarm system; refer to Section 21 10 00 for the waterflow alarm device "
    "and Section 28 31 00 for fire alarm system actuation."
)
_NFPA25_EXPLAINS = (
    "NFPA 25, Standard for the Inspection, Testing, and Maintenance of "
    "Water-Based Fire Protection Systems, 2020 edition. The 2021 Virginia "
    "Statewide Fire Prevention Code (SFPC) incorporates its referenced "
    "standards via its own Chapter 80, distinct from the VCC/IBC Chapter 35 "
    "table used for construction-code standards. Because the SFPC "
    "incorporates the 2021 International Fire Code (IFC) by reference, and "
    "the 2021 IFC and 2021 IBC share the same ICC code-development cycle "
    "referencing NFPA 25 at the edition available at time of drafting "
    "(consistent with the confirmed 2019 edition of NFPA 13 shared by both "
    "codes), the 2021 IFC's referenced-standards table is understood to cite "
    "NFPA 25 at the 2020 edition, corroborated by a jurisdiction's published "
    "ITM enforcement summary confirming the 2021 IFC references NFPA 25-2020 "
    "directly."
)
_NFPA25_DIRECTS = (
    "NFPA 25, Standard for the Inspection, Testing, and Maintenance of "
    "Water-Based Fire Protection Systems, 2020 edition."
)
_FM532_EXPLAINS = (
    "FM Global Property Loss Prevention Data Sheet 5-32, Data Centers and "
    "Related Facilities (edition recorded for this Project: January 2026, "
    "Interim Revision July 2026) — the primary insurer-specific loss "
    "prevention standard for this occupancy, covering construction, "
    "detection, and suppression system selection. FM DS 5-32 ranks preaction "
    "sprinkler valve configurations (non-interlock, single-interlock, and "
    "double-interlock) in order of preference based on water-delay and "
    "reliability tradeoffs; double-interlock pre-action is specified for "
    "this Project's critical spaces per the Owner's documented design "
    "baseline. Also referenced: FM Global Property Loss Prevention Data "
    "Sheet 2-0, Installation Guidelines for Automatic Sprinklers, for general "
    "sprinkler installation guidance."
)
_FM532_DIRECTS = (
    "FM Global Property Loss Prevention Data Sheet 5-32, Data Centers and "
    "Related Facilities, January 2026 (Interim Revision July 2026)."
)


def _section(article_title: str, *texts: str) -> SpecSection:
    ops = [
        {"action": "replace", "target_id": "sec", "text": "WET-PIPE SPRINKLER SYSTEMS", "numbering": "21 13 13"},
        {"action": "add_article", "target_id": "pt1", "text": article_title},
    ]
    ops += [
        {"action": "add_paragraph", "target_id": "pt1.a1", "text": text}
        for text in texts
    ]
    section, _ = apply_edits(SpecSection.empty(), ops)
    return section


def _voice_issues(section: SpecSection, **kwargs) -> list[dict]:
    return [
        issue
        for issue in lint_document(section, HYPERSCALE_FIRE, **kwargs)
        if issue["rule"] in _VOICE_RULES
    ]


# ---------------------------------------------------------------------------
# The owner's examples, both ways
# ---------------------------------------------------------------------------


def test_the_waterflow_draft_is_flagged_and_its_correction_is_clean():
    (issue,) = _voice_issues(_section("SYSTEM DESCRIPTION", _WATERFLOW_EXPLAINS))
    assert issue["rule"] == RULE_EXPLANATORY_PROSE
    for phrase in ("amends", "This amendment", "applies generally", "governs the"):
        assert f"'{phrase}'" in issue["message"]
    assert "directive to the Contractor" in issue["message"]
    assert _voice_issues(_section("SYSTEM DESCRIPTION", _WATERFLOW_DIRECTS)) == []


def test_the_references_drafts_are_flagged_and_their_corrections_are_clean():
    issues = _voice_issues(_section("REFERENCES", _NFPA25_EXPLAINS, _FM532_EXPLAINS))
    by_rule = {(i["rule"], i["element_id"]) for i in issues}
    assert by_rule == {
        (RULE_EXPLANATORY_PROSE, "pt1.a1.p1"),
        (RULE_REFERENCE_ENTRY, "pt1.a1.p1"),
        (RULE_EXPLANATORY_PROSE, "pt1.a1.p2"),
        (RULE_REFERENCE_ENTRY, "pt1.a1.p2"),
    }
    nfpa25 = reference_entry_problems(_NFPA25_EXPLAINS)
    assert "more than one sentence" in nfpa25
    assert f"over {REFERENCE_ENTRY_MAX_CHARS} characters" in nfpa25
    fm = " ".join(reference_entry_problems(_FM532_EXPLAINS))
    for reason in (
        "names a second standard ('Also referenced')",
        "an em-dash description",
        "'primary'",
        "'specified for'",
        "'recorded'",
    ):
        assert reason in fm
    assert _voice_issues(_section("REFERENCES", _NFPA25_DIRECTS, _FM532_DIRECTS)) == []


# ---------------------------------------------------------------------------
# The phrase list
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, phrase",
    [
        ("The State has adopted the 2021 IFC.", "has adopted"),
        ("The jurisdiction adopts NFPA 13.", "adopts"),
        ("The SFPC incorporates the IFC by reference.", "incorporates the IFC"),
        ("The 2020 edition is understood to apply.", "is understood to"),
        ("Provide valves because the insurer requires them.", "because"),
        ("Provide drains in order to empty the system.", "in order to"),
        ("The intent is a fully supervised system.", "The intent"),
        ("Signs are intended to aid responders.", "are intended to"),
        ("The purpose of the test is to prove tightness.", "The purpose of"),
        ("Provide supervision; this ensures readiness.", "this ensures"),
        ("This requirement applies to riser rooms.", "This requirement applies"),
        ("Supervision applies to this Project.", "applies to this Project"),
        ("Provide hangers as described in this provision.", "this provision"),
        ("Edition basis: Owner standard.", "basis:"),
        ("Provide switches per research item r-1.", "research item"),
        ("Use the edition in the project profile.", "project profile"),
        ("Resolve the open items first.", "open items"),
        ("Provide an unverified rating.", "unverified"),
        ("Coordinate with the design team.", "the design team"),
        ("The specifier selects the finish.", "The specifier"),
        ("It is recommended to provide spares.", "It is recommended"),
        ("Valves should be supervised.", "should"),
        ("Pumps will likely be electric.", "likely"),
        ("Pipe may need to be galvanized.", "may need to"),
        ("Size the main pending a flow test.", "pending"),
    ],
)
def test_each_explanatory_phrase_is_flagged(text, phrase):
    assert phrase in [hit["match"] for hit in explanatory_prose_hits(text)]


@pytest.mark.parametrize(
    "text",
    [
        "Work of this Section includes wet-pipe sprinkler systems.",
        "Comply with IBC §903.4.2 (Alarms).",
        "Install sprinkler systems in accordance with NFPA 13.",
        "Refer to Section 28 31 00 for fire alarm system actuation.",
        "Coordinate riser locations with the Owner.",
        "Confirm final locations with the Owner before installation.",
        "Install per the manufacturer's written recommendations.",
        "Slope piping so that it drains to the main drain.",
        "Provide sprinklers where indicated on the Drawings.",
        "Provide signage where required by authorities having jurisdiction.",
        "Comply with the IBC, as amended by the Virginia Construction Code.",
        "Hangers are generally spaced as scheduled.",
        "Branch lines typically run parallel to the joists.",
        "Where requirements conflict, the more stringent requirement governs.",
        "The fire pump controller shall incorporate a supervisory switch.",
        "Provide finishes consistent with the existing building.",
        "For the purpose of this Section, a riser room is any room housing a riser.",
        "Valves shall be supervised.",
    ],
)
def test_ordinary_specification_language_is_left_alone(text):
    assert explanatory_prose_hits(text) == []


def test_the_owners_borderline_calls_carry_their_own_advice():
    (should,) = explanatory_prose_hits("Valves should be supervised.")
    assert "'shall'" in should["label"]
    (reason,) = explanatory_prose_hits("Provide drains in order to empty it.")
    assert "'in order to'" in reason["label"]
    (pending,) = explanatory_prose_hits("Size the main pending a flow test.")
    assert "real condition of the work" in pending["label"]


def test_one_issue_per_provision_names_every_phrase():
    section = _section(
        "SYSTEM DESCRIPTION",
        "Valves should be supervised because the insurer requires it.",
    )
    (issue,) = _voice_issues(section)
    # In reading order, whatever order the patterns are listed in.
    assert issue["message"].index("'should'") < issue["message"].index("'because'")
    assert issue["match"] == "should"


# ---------------------------------------------------------------------------
# The REFERENCES shape rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entry",
    [
        _NFPA25_DIRECTS,
        _FM532_DIRECTS,
        "ASTM A795/A795M, Standard Specification for Black and Hot-Dipped "
        "Zinc-Coated (Galvanized) Welded and Seamless Steel Pipe for Fire "
        "Protection Use, 2021 edition.",
        "UL 199, Automatic Sprinklers for Fire-Protection Service, Ed. 12.",
        "ANSI/UL 199, Automatic Sprinklers for Fire-Protection Service, Ed. 12.",
        "NFPA 25, Inspection, Testing, and Maintenance; Water-Based Systems, "
        "2020 edition.",
        "U.S. Department of Labor, OSHA 29 CFR 1910, Occupational Safety and "
        "Health Standards.",
        "NFPA 13 — Standard for the Installation of Sprinkler Systems, 2025 "
        "edition.",
        "ASTM E84, Standard Test Method for Surface Burning Characteristics "
        "of Building Materials (Wall Coverings), 2024 edition.",
    ],
)
def test_well_formed_reference_entries_pass(entry):
    assert reference_entry_problems(entry) == []


def test_two_standards_in_one_entry_are_flagged_without_cue_words():
    """PR #276 review: "and" joins two standards as surely as "Also referenced"."""
    two = (
        "NFPA 13, Standard for the Installation of Sprinkler Systems, 2022 "
        "edition and NFPA 14, Standard for Standpipes, 2019 edition."
    )
    assert reference_entry_problems(two) == ["more than one standard (NFPA 13, NFPA 14)"]
    assert reference_entry_problems(
        "Standard for Sprinkler Systems, 2022 edition, and Standpipes, 2019 edition."
    ) == ["more than one edition"]


def test_a_second_sentence_is_counted_however_it_starts():
    """PR #276 review: a digit can open a sentence; a semicolon ends none."""
    assert reference_entry_problems(
        "NFPA 25, Standard, 2020 edition. 2021 IFC references it."
    ) == ["more than one sentence"]
    assert reference_entry_problems(
        "NFPA 25, Inspection, Testing, and Maintenance; Water-Based Systems, "
        "2020 edition."
    ) == []


def test_only_leaf_entries_of_a_references_article_are_shape_checked():
    lead_in = (
        "The publications listed below form a part of this Specification to "
        "the extent referenced. The publications are referred to in the text "
        "by basic designation only."
    )
    section, _ = apply_edits(
        SpecSection.empty(),
        [
            {"action": "add_article", "target_id": "pt1", "text": "REFERENCE STANDARDS"},
            {"action": "add_paragraph", "target_id": "pt1.a1", "text": lead_in},
            {"action": "add_paragraph", "target_id": "pt1.a1.p1", "text": _NFPA25_DIRECTS},
            {"action": "add_paragraph", "target_id": "pt1.a1.p1", "text": _FM532_EXPLAINS},
            {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
            {"action": "add_paragraph", "target_id": "pt1.a2", "text": _FM532_DIRECTS + " " + _NFPA25_DIRECTS},
        ],
    )
    shape = [i for i in _voice_issues(section) if i["rule"] == RULE_REFERENCE_ENTRY]
    assert [i["element_id"] for i in shape] == ["pt1.a1.p1.p2"]


# ---------------------------------------------------------------------------
# What the rules never read
# ---------------------------------------------------------------------------


def test_preserved_blocks_and_non_spec_imports_are_not_voice_checked():
    section = _section("REFERENCES", _NFPA25_EXPLAINS)
    assert _voice_issues(section)
    assert _voice_issues(section, unstructured_import=True) == []
    section.parts[0].articles[0].paragraphs[0].locked = "table"
    assert _voice_issues(section) == []


# ---------------------------------------------------------------------------
# What the model and Final QC are told
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", [HYPERSCALE_FIRE, GENERIC], ids=lambda m: m.module_id)
def test_the_prompt_names_the_owners_calls_and_the_new_rules(module):
    prompt = render_system_prompt(module)
    # PR #276 review: "pending" is not a refused placeholder (the guard never
    # refused it), and the voice section says when it may stay.
    assert '"to be determined", or "pending". The app refuses' not in prompt
    assert '"Pending" stays only where it is a real condition of the work' in prompt
    assert 'Never "should"' in prompt
    assert 'never "in order to"' in prompt
    assert "explanatory_prose and reference_entry_shape" in prompt


def test_final_qc_reviews_specification_voice():
    brief = QC_LENS_BY_ID["enforceability_language"].brief
    for needle in (
        "never explains",
        "'in order to'",
        "'should'",
        "IBC §903.4.2 (Alarms)",
        "REFERENCES entry",
        "designation, full title, and edition",
    ):
        assert needle in brief
