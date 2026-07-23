"""Unit tests for the standards machinery: pins, overrides, rendering."""
from __future__ import annotations

import pytest

from backend.standards import (
    BaseCode,
    StandardEdition,
    StandardsBasis,
    effective_editions,
    normalize_standard_name,
    references_article_lines,
    standards_context_block,
    validate_overrides_shape,
)

_BASIS = StandardsBasis(
    label="test-basis",
    base_codes=(BaseCode("ibc", "IBC", "2024", source="test"),),
    standards=(
        StandardEdition(
            "NFPA 13", "2025",
            title="Standard for the Installation of Sprinkler Systems",
            source="verified: test",
        ),
        StandardEdition(
            "NFPA 25", "2026",
            title="ITM of Water-Based Fire Protection Systems",
            source="UNVERIFIED: check me",
        ),
    ),
)


def test_normalize_standard_name():
    assert normalize_standard_name("  nfpa   13 ") == "NFPA 13"
    assert normalize_standard_name("") == ""


def test_edition_phrase_description_and_reference_line():
    std = _BASIS.standards[0]
    assert std.edition_phrase == "2025"
    assert std.description == "NFPA 13 2025"
    assert std.reference_line == (
        "NFPA 13 - Standard for the Installation of Sprinkler Systems "
        "(2025 edition)"
    )
    noted = StandardEdition("NFPA 25", "2013", note="California Edition")
    assert noted.edition_phrase == "2013 (California Edition)"


def test_is_verified_flags_unverified_sources():
    assert _BASIS.standards[0].is_verified
    assert not _BASIS.standards[1].is_verified
    assert not StandardEdition("X", "2020").is_verified  # empty source
    assert _BASIS.unverified_standards() == (_BASIS.standards[1],)


def test_basis_lookup_is_case_insensitive():
    assert _BASIS.standard("nfpa 13").edition == "2025"
    assert _BASIS.standard("NFPA 99") is None


def test_effective_editions_defaults_and_override():
    effs = effective_editions(_BASIS, None)
    assert [(e.name, e.edition, e.is_override) for e in effs] == [
        ("NFPA 13", "2025", False),
        ("NFPA 25", "2026", False),
    ]

    overridden = effective_editions(
        _BASIS, {"NFPA 13": {"edition": "2019", "basis": "2021 VCC"}}
    )
    nfpa13 = overridden[0]
    assert (nfpa13.edition, nfpa13.is_override, nfpa13.basis) == (
        "2019", True, "2021 VCC"
    )
    # Pin metadata (title) survives the override.
    assert "Sprinkler Systems" in nfpa13.title
    # Untouched pins stay defaults.
    assert overridden[1].is_override is False


def test_effective_editions_unpinned_override_appends():
    effs = effective_editions(
        _BASIS, {"NFPA 70": {"edition": "2023", "basis": "state electrical"}}
    )
    assert [e.name for e in effs] == ["NFPA 13", "NFPA 25", "NFPA 70"]
    assert effs[-1].is_override and effs[-1].edition == "2023"


def test_standards_context_block_names_override_basis():
    block = standards_context_block(
        _BASIS, {"NFPA 13": {"edition": "2019", "basis": "2021 VCC"}}
    )
    assert "- NFPA 13: 2019 — jurisdiction-adopted override (basis: 2021 VCC)" in block
    assert "- NFPA 25: 2026 — module default" in block
    assert "set_standard_edition" in block


def test_references_article_lines_orders_and_filters():
    lines = references_article_lines(_BASIS, None, names=["nfpa 25", "NFPA 13"])
    assert lines[0].startswith("NFPA 25 - ")
    assert lines[1].startswith("NFPA 13 - ")
    # Unknown names are skipped, not invented.
    assert references_article_lines(_BASIS, None, names=["NFPA 99"]) == []
    # No filter: everything in pin order.
    assert len(references_article_lines(_BASIS, None)) == 2


# ---------------------------------------------------------------------------
# Batch 10: the unpinned basis rendering
# ---------------------------------------------------------------------------

_UNPINNED = StandardsBasis(label="test-unpinned", unpinned=True)


def test_unpinned_context_block_states_the_mandatory_basis_posture():
    block = standards_context_block(_UNPINNED, None)
    assert "pins NO default editions" in block
    assert "- (none recorded yet)" in block
    # The decision-1 policy, verbatim territory: stated basis, honest
    # model proposals, year-free until recorded.
    assert "set_standard_edition" in block
    assert "model-proposed, unverified" in block
    assert "without an edition year" in block
    # Nothing renders as a module default.
    assert "module default" not in block


def test_unpinned_context_block_lists_recorded_overrides():
    block = standards_context_block(
        _UNPINNED,
        {"CSA Z662": {"edition": "2023", "basis": "user: AB adoption"}},
    )
    assert "- CSA Z662: 2023 — recorded (basis: user: AB adoption)" in block
    assert "(none recorded yet)" not in block
    # Effective editions with zero pins are exactly the overrides.
    effs = effective_editions(
        _UNPINNED, {"CSA Z662": {"edition": "2023", "basis": "x"}}
    )
    assert [(e.name, e.edition, e.is_override) for e in effs] == [
        ("CSA Z662", "2023", True)
    ]


def test_pinned_context_block_unchanged_by_the_unpinned_branch():
    # The pinned rendering is byte-stable across the Batch 10 change.
    block = standards_context_block(_BASIS, None)
    assert block.startswith("Standards editions in effect for this project:")
    assert "- NFPA 13: 2025 — module default (current published edition)" in block
    assert "pins NO default editions" not in block


def test_validate_overrides_shape_rejects_malformed():
    assert validate_overrides_shape(None) == {}
    clean = validate_overrides_shape(
        {"nfpa  13": {"edition": " 2019 ", "basis": " 2021 VCC "}}
    )
    assert clean == {"NFPA 13": {"edition": "2019", "basis": "2021 VCC"}}
    for bad in (
        "not a dict",
        {"NFPA 13": "not a dict"},
        {"NFPA 13": {"edition": "2019"}},  # missing basis
        {"NFPA 13": {"edition": "", "basis": "x"}},  # empty edition
        {"": {"edition": "2019", "basis": "x"}},  # empty name
    ):
        with pytest.raises(ValueError):
            validate_overrides_shape(bad)
