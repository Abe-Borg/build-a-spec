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
    validate_suppressed_shape,
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
        _BASIS,
        {
            "NFPA 70": {
                "edition": "2023",
                "basis": "state electrical",
                "title": "National Electrical Code",
            }
        },
    )
    assert [e.name for e in effs] == ["NFPA 13", "NFPA 25", "NFPA 70"]
    added = effs[-1]
    # A standard the module does not pin is marked is_added and carries its
    # own title (pins carry theirs; added entries would otherwise be bare).
    assert added.is_override and added.is_added and added.edition == "2023"
    assert added.title == "National Electrical Code"
    assert added.reference_line == "NFPA 70 - National Electrical Code (2023 edition)"


def test_effective_editions_skips_suppressed():
    # A suppressed pin drops out entirely.
    effs = effective_editions(_BASIS, None, {"NFPA 25": "not applicable"})
    assert [e.name for e in effs] == ["NFPA 13"]

    # Suppression wins over a recorded override, and is non-destructive: the
    # override re-applies once the suppression is lifted.
    overrides = {"NFPA 13": {"edition": "2019", "basis": "2021 VCC"}}
    hidden = effective_editions(_BASIS, overrides, {"NFPA 13": ""})
    assert [e.name for e in hidden] == ["NFPA 25"]
    restored = effective_editions(_BASIS, overrides, None)
    assert restored[0].name == "NFPA 13" and restored[0].edition == "2019"

    # Suppressing an added standard removes it from the appended tail too.
    added = {"NFPA 70": {"edition": "2023", "basis": "x"}}
    assert effective_editions(_BASIS, added, {"NFPA 70": ""}) == effective_editions(
        _BASIS, None, None
    )


def test_standards_context_block_names_override_basis():
    block = standards_context_block(
        _BASIS, {"NFPA 13": {"edition": "2019", "basis": "2021 VCC"}}
    )
    assert "- NFPA 13: 2019 — jurisdiction-adopted override (basis: 2021 VCC)" in block
    assert "- NFPA 25: 2026 — module default" in block
    assert "set_standard_edition" in block


def test_standards_context_block_marks_added_and_excluded():
    block = standards_context_block(
        _BASIS,
        {"NFPA 30": {"edition": "2024", "basis": "on-site flammable storage"}},
        {"NFPA 25": "no water-based ITM in scope"},
    )
    # An added standard is labelled as such, not as a jurisdiction adoption.
    assert (
        "- NFPA 30: 2024 — added for this project "
        "(basis: on-site flammable storage)" in block
    )
    # The suppressed pin is absent from the in-effect list …
    assert "- NFPA 25:" not in block
    # … and named in the exclusion advisory so the model won't reintroduce it.
    assert (
        "Intentionally excluded from this project (do not reintroduce into "
        "REFERENCES): NFPA 25 — no water-based ITM in scope." in block
    )


def test_references_article_lines_orders_and_filters():
    lines = references_article_lines(_BASIS, None, names=["nfpa 25", "NFPA 13"])
    assert lines[0].startswith("NFPA 25 - ")
    assert lines[1].startswith("NFPA 13 - ")
    # Unknown names are skipped, not invented.
    assert references_article_lines(_BASIS, None, names=["NFPA 99"]) == []
    # No filter: everything in pin order.
    assert len(references_article_lines(_BASIS, None)) == 2


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


def test_validate_overrides_shape_accepts_optional_title():
    clean = validate_overrides_shape(
        {"NFPA 30": {"edition": "2024", "basis": "owner", "title": " Flammable Liquids Code "}}
    )
    assert clean == {
        "NFPA 30": {"edition": "2024", "basis": "owner", "title": "Flammable Liquids Code"}
    }
    # An empty/absent title is simply omitted (not stored as "").
    assert "title" not in validate_overrides_shape(
        {"NFPA 30": {"edition": "2024", "basis": "owner", "title": "  "}}
    )["NFPA 30"]


def test_validate_suppressed_shape():
    assert validate_suppressed_shape(None) == {}
    assert validate_suppressed_shape({}) == {}
    # Names are canonicalized; reasons are stripped; an empty reason is allowed
    # (excluding a standard is a scope decision, not an edition change).
    clean = validate_suppressed_shape(
        {"nfpa  2001": " no clean-agent system ", "NFPA 76": ""}
    )
    assert clean == {"NFPA 2001": "no clean-agent system", "NFPA 76": ""}
    for bad in (
        "not a dict",
        {"NFPA 2001": 3},  # non-string reason
        {"NFPA 2001": None},  # non-string reason
        {"": "reason"},  # empty name
    ):
        with pytest.raises(ValueError):
            validate_suppressed_shape(bad)
