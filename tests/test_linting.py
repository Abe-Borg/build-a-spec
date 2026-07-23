"""Lint engine tests: every rule, the negation suppression, and the
override interplay — pure document-model exercises, no API."""
from __future__ import annotations

from backend.spec_doc import DocumentStore, lint_document
from backend.spec_modules import DEFAULT_MODULE


def _doc_with(edits: list[dict]):
    store = DocumentStore()
    store.begin_turn()
    store.apply_edits(edits)
    store.commit_turn()
    return store.doc


def _para(text: str, target: str = "pt1.a1") -> dict:
    return {"action": "add_paragraph", "target_id": target, "text": text}


def _base(*paragraph_texts: str) -> list[dict]:
    edits = [
        {
            "action": "replace",
            "target_id": "sec",
            "text": "WET-PIPE SPRINKLER SYSTEMS",
            "numbering": "21 13 13",
        },
        {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
    ]
    edits.extend(_para(t) for t in paragraph_texts)
    return edits


def _rules(issues):
    return [(i["rule"], i["element_id"]) for i in issues]


# ---------------------------------------------------------------------------
# Stale-edition rule
# ---------------------------------------------------------------------------


def test_stale_edition_detected_in_three_citation_shapes():
    doc = _doc_with(
        _base(
            "Comply with NFPA 13-2019 throughout.",
            "Install per NFPA 13, 2019 edition.",
            "Test per the 2019 edition of NFPA 13.",
        )
    )
    issues = [
        i for i in lint_document(doc, DEFAULT_MODULE)
        if i["rule"] == "stale_edition"
    ]
    assert len(issues) == 3
    assert all("edition in effect is 2025" in i["message"] for i in issues)
    # Each issue anchors to its paragraph and carries the matched text.
    assert issues[0]["element_id"] == "pt1.a1.p1"
    assert "2019" in issues[0]["match"]


def test_matching_edition_and_bare_reference_do_not_flag():
    doc = _doc_with(
        _base(
            "Comply with NFPA 13-2025 throughout.",
            "Sprinklers per NFPA 13 and the approved working plans.",
        )
    )
    assert [
        i for i in lint_document(doc, DEFAULT_MODULE)
        if i["rule"] == "stale_edition"
    ] == []


def test_sibling_designations_do_not_cross_match():
    # NFPA 13 pinned at 2025 must not fire on NFPA 13R/13D/130 citations.
    doc = _doc_with(
        _base(
            "Residential occupancies are outside this section per NFPA 13R-2019.",
            "Transit facilities follow NFPA 130-2020.",
        )
    )
    issues = [
        i for i in lint_document(doc, DEFAULT_MODULE)
        if i["rule"] == "stale_edition"
    ]
    assert issues == []


def test_negation_suppresses_historical_citations():
    doc = _doc_with(
        _base(
            "NFPA 13-2019 is superseded and shall not be used.",
            "Previously designed under NFPA 13, 2016 edition.",
        )
    )
    assert [
        i for i in lint_document(doc, DEFAULT_MODULE)
        if i["rule"] == "stale_edition"
    ] == []


def test_override_retargets_the_stale_check():
    doc = _doc_with(
        _base("Comply with NFPA 13-2025 throughout.")
        + [
            {
                "action": "set_standard_edition",
                "target_id": "sec",
                "standard": "NFPA 13",
                "edition": "2019",
                "basis": "2021 VCC per user",
            }
        ]
    )
    issues = [
        i for i in lint_document(doc, DEFAULT_MODULE)
        if i["rule"] == "stale_edition"
    ]
    # The module-default 2025 citation now contradicts the jurisdiction's
    # 2019 — and the message names the recorded basis.
    assert len(issues) == 1
    assert "edition in effect is 2019" in issues[0]["message"]
    assert "2021 VCC per user" in issues[0]["message"]

    # And a 2019 citation is clean under the override.
    doc2 = _doc_with(
        _base("Comply with NFPA 13-2019 throughout.")
        + [
            {
                "action": "set_standard_edition",
                "target_id": "sec",
                "standard": "NFPA 13",
                "edition": "2019",
                "basis": "2021 VCC per user",
            }
        ]
    )
    assert [
        i for i in lint_document(doc2, DEFAULT_MODULE)
        if i["rule"] == "stale_edition"
    ] == []


# ---------------------------------------------------------------------------
# Placeholder / template markers
# ---------------------------------------------------------------------------


def test_placeholder_and_template_markers():
    doc = _doc_with(
        _base(
            "Provide [INSERT MANUFACTURER] sprinklers.",
            "Riser location ___ to be confirmed.",
            "TODO: verify pump room layout.",
            "Lorem ipsum dolor sit amet.",
        )
    )
    issues = lint_document(doc, DEFAULT_MODULE)
    rules = [i["rule"] for i in issues]
    assert rules.count("placeholder_marker") == 2
    assert rules.count("template_marker") == 2


def test_tbd_is_not_double_reported_by_lint():
    # [TBD: ...] is first-class open-item tracking, not lint.
    doc = _doc_with(_base("Design density [TBD: value] applies."))
    assert [
        i for i in lint_document(doc, DEFAULT_MODULE)
        if "TBD" in i.get("match", "")
    ] == []


def test_module_extra_marker_patterns_apply():
    doc = _doc_with(_base("Project: [PROJECT NAME] campus, phase XXXX."))
    issues = [
        i for i in lint_document(doc, DEFAULT_MODULE)
        if i["rule"] == "template_marker"
    ]
    assert len(issues) == 2  # [PROJECT NAME] + XXXX from module extras


# ---------------------------------------------------------------------------
# Structural rules
# ---------------------------------------------------------------------------


def test_empty_article_and_duplicate_title():
    doc = _doc_with(
        [
            {
                "action": "replace",
                "target_id": "sec",
                "text": "T",
                "numbering": "21 13 13",
            },
            {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
            {"action": "add_article", "target_id": "pt1", "text": "summary"},
            {"action": "add_paragraph", "target_id": "pt1.a2", "text": "Body."},
        ]
    )
    issues = lint_document(doc, DEFAULT_MODULE)
    rules = _rules(issues)
    assert ("empty_article", "pt1.a1") in rules
    assert ("duplicate_article_title", "pt1.a2") in rules


def test_missing_section_header_is_info_level():
    doc = _doc_with(
        [
            {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
            {"action": "add_paragraph", "target_id": "pt1.a1", "text": "Body."},
        ]
    )
    issues = [
        i for i in lint_document(doc, DEFAULT_MODULE)
        if i["rule"] == "missing_section_header"
    ]
    assert len(issues) == 1
    assert issues[0]["severity"] == "info"
    assert issues[0]["element_id"] == "sec"


def test_clean_document_produces_no_issues():
    doc = _doc_with(
        _base(
            "Section includes wet-pipe sprinkler systems per NFPA 13.",
            "Hydrostatically test at 200 psi for 2 hours.",
        )
    )
    assert lint_document(doc, DEFAULT_MODULE) == []


def test_issue_ids_are_stable_per_tree():
    doc = _doc_with(_base("TODO: one. TODO: two."))
    first = lint_document(doc, DEFAULT_MODULE)
    second = lint_document(doc, DEFAULT_MODULE)
    assert [i["id"] for i in first] == [i["id"] for i in second]
    assert len({i["id"] for i in first}) == len(first)


def test_references_article_line_shape_is_checked():
    # The REFERENCES-article shape: designation, full title, "(year edition)".
    doc = _doc_with(
        _base(
            "NFPA 13 - Standard for the Installation of Sprinkler Systems "
            "(2019 edition).",
        )
    )
    issues = [
        i for i in lint_document(doc, DEFAULT_MODULE)
        if i["rule"] == "stale_edition"
    ]
    assert len(issues) == 1 and "2019" in issues[0]["match"]

    # Matching edition: clean.
    doc2 = _doc_with(
        _base(
            "NFPA 13 - Standard for the Installation of Sprinkler Systems "
            "(2025 edition).",
        )
    )
    assert [
        i for i in lint_document(doc2, DEFAULT_MODULE)
        if i["rule"] == "stale_edition"
    ] == []

    # Designation lists don't cross-attribute the year: NFPA 20 pinned 2025,
    # and the 2024 belongs to NFPA 14 (its correct pin) — no false flag on
    # NFPA 20 because the gap contains digits ("14").
    doc3 = _doc_with(_base("Pumps per NFPA 20 and NFPA 14 (2024 edition)."))
    assert [
        i for i in lint_document(doc3, DEFAULT_MODULE)
        if i["rule"] == "stale_edition"
    ] == []


# ---------------------------------------------------------------------------
# Batch 9: unrecorded_edition (unpinned-basis modules only)
# ---------------------------------------------------------------------------


def _generic():
    from backend.spec_modules.generic import GENERIC

    return GENERIC


def _record(standard: str, edition: str) -> dict:
    return {
        "action": "set_standard_edition",
        "target_id": "sec",
        "standard": standard,
        "edition": edition,
        "basis": "user stated",
    }


def _unrecorded(issues):
    return [i for i in issues if i["rule"] == "unrecorded_edition"]


def test_unrecorded_edition_fires_on_the_engine_citation_shapes():
    doc = _doc_with(
        _base(
            "Comply with NFPA 13-2019 throughout.",
            "Install per NFPA 13, 2019 edition.",
            "Test per the 2019 edition of NFPA 13.",
            "NFPA 13 - Standard for the Installation of Sprinkler Systems "
            "(2019 edition).",
        )
    )
    issues = _unrecorded(lint_document(doc, _generic()))
    assert len(issues) == 4
    assert all("no edition is recorded" in i["message"] for i in issues)
    assert all("set_standard_edition" in i["message"] for i in issues)
    assert issues[0]["severity"] == "warn"
    # No stale_edition double-fire: nothing is in the effective set.
    assert [
        i for i in lint_document(doc, _generic())
        if i["rule"] == "stale_edition"
    ] == []


def test_unrecorded_edition_covers_publisher_grammar_designations():
    doc = _doc_with(
        _base(
            "Fire tests per CAN/ULC-S524-2019.",
            "Energy performance per ASHRAE 90.1-2022.",
        )
    )
    issues = _unrecorded(lint_document(doc, _generic()))
    assert {i["match"] for i in issues} == {
        "CAN/ULC-S524-2019",
        "ASHRAE 90.1-2022",
    }


def test_year_free_designations_are_silent():
    doc = _doc_with(
        _base(
            "Pipe per ASTM A53 and ASTM A795.",
            "Sprinklers listed per UL 199.",
        )
    )
    assert _unrecorded(lint_document(doc, _generic())) == []


def test_recording_the_edition_silences_the_rule():
    doc = _doc_with(
        _base("Comply with NFPA 13-2019 throughout.")
        + [_record("NFPA 13", "2019")]
    )
    issues = lint_document(doc, _generic())
    assert _unrecorded(issues) == []
    # Year matches the recorded edition — stale is silent too.
    assert [i for i in issues if i["rule"] == "stale_edition"] == []


def test_recorded_but_wrong_year_is_stale_not_unrecorded():
    doc = _doc_with(
        _base("Comply with NFPA 13-2019 throughout.")
        + [_record("NFPA 13", "2022")]
    )
    issues = lint_document(doc, _generic())
    assert _unrecorded(issues) == []
    stale = [i for i in issues if i["rule"] == "stale_edition"]
    assert len(stale) == 1 and "edition in effect is 2022" in stale[0]["message"]


def test_hyphen_and_space_designation_forms_match_the_record():
    # Text writes "CAN/ULC-S524", the override was recorded as "CAN/ULC S524"
    # — the same standard; the rule must treat it as recorded.
    doc = _doc_with(
        _base("Fire alarm per CAN/ULC-S524-2019.")
        + [_record("CAN/ULC S524", "2019")]
    )
    assert _unrecorded(lint_document(doc, _generic())) == []


def test_unrecorded_edition_respects_negation_suppression():
    doc = _doc_with(
        _base(
            "NFPA 13-2016 is superseded and shall not be used.",
            "Previously designed under ASHRAE 90.1-2016.",
        )
    )
    assert _unrecorded(lint_document(doc, _generic())) == []


def test_mixed_recorded_and_unrecorded_designations():
    doc = _doc_with(
        _base("Comply with NFPA 13-2019 and NFPA 72-2019.")
        + [_record("NFPA 13", "2019")]
    )
    issues = _unrecorded(lint_document(doc, _generic()))
    assert len(issues) == 1 and "NFPA 72" in issues[0]["message"]


def test_pinned_module_never_runs_the_unrecorded_rule():
    # ASTM E84 is not among the hyperscale pins; a year citation on it
    # produces NOTHING for a pinned module (the rule is scoped off), and
    # the full lint output is exactly the pre-Batch-8 expectation.
    doc = _doc_with(
        _base(
            "Surface burning per ASTM E84 (2021 edition).",
            "Comply with NFPA 13-2019 throughout.",
        )
    )
    issues = lint_document(doc, DEFAULT_MODULE)
    assert _unrecorded(issues) == []
    assert [(i["rule"], i["element_id"]) for i in issues] == [
        ("stale_edition", "pt1.a1.p2")
    ]


# ---------------------------------------------------------------------------
# Batch 9 remediation: the two review-workflow findings on the new rule.
# ---------------------------------------------------------------------------


def test_wrong_year_in_other_punctuation_form_of_recorded_standard_is_stale():
    # Recorded as the SPACE form; the document cites the HYPHEN form at a
    # wrong year. The stale scan is punctuation-tolerant for unpinned
    # modules, so this fires stale_edition (not silence, not unrecorded).
    doc = _doc_with(
        _base("Fire alarm wiring per CAN/ULC-S524-2019.")
        + [_record("CAN/ULC S524", "2022")]
    )
    issues = lint_document(doc, _generic())
    stale = [i for i in issues if i["rule"] == "stale_edition"]
    assert len(stale) == 1 and "edition in effect is 2022" in stale[0]["message"]
    # And it is NOT double-counted as unrecorded (the standard IS recorded).
    assert _unrecorded(issues) == []


def test_overlapping_designation_forms_are_not_double_reported():
    # "ULC-S524" also matches inside "CAN/ULC-S524-2019"; longest-first
    # binding drops that inner match, so two physical citations → two issues.
    doc = _doc_with(
        _base("Tested to ULC-S524-2019 and CAN/ULC-S524-2019 throughout.")
    )
    issues = _unrecorded(lint_document(doc, _generic()))
    assert len(issues) == 2
    matches = sorted(i["match"] for i in issues)
    assert matches == ["CAN/ULC-S524-2019", "ULC-S524-2019"]
