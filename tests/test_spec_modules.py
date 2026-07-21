"""Registry validation tests: a bad module definition must fail at import
time (registration), never mid-session. Mirrors Spec Critic's
``test_module_registry.py`` posture."""
from __future__ import annotations

from dataclasses import replace

import pytest

from backend.spec_modules import (
    AVAILABLE_MODULES,
    DEFAULT_MODULE,
    get_module,
    validate_module_registry,
)
from backend.spec_modules.base import (
    InterviewTopic,
    ResearchDimension,
    SectionDef,
)
from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE
from backend.standards import StandardEdition, StandardsBasis


def _valid() -> object:
    return HYPERSCALE_FIRE


def test_registry_resolves_and_defaults():
    assert "hyperscale_fire" in AVAILABLE_MODULES
    assert get_module("hyperscale_fire") is HYPERSCALE_FIRE
    assert get_module(None) is DEFAULT_MODULE
    assert get_module("no-such-module") is DEFAULT_MODULE
    validate_module_registry(AVAILABLE_MODULES.values())


def test_shipping_module_is_coherent():
    module = _valid()
    # Lead section carries the full playbook.
    assert module.lead_section().number == "21 13 13"
    assert module.lead_section().playbook_depth == "full"
    # NFPA 13 pinned at 2025 — the frozen project decision.
    assert module.basis.standard("NFPA 13").edition == "2025"
    # Every pin documents provenance.
    assert all(std.source.strip() for std in module.basis.standards)
    # Non-defaultable minimum exists and is small.
    hard = [t for t in module.interview_playbook if t.non_defaultable]
    assert 1 <= len(hard) <= 4
    # Dormant research dimensions are present for Phase 4.
    assert {d.dimension_id for d in module.research_dimensions} >= {
        "governing_codes",
        "ahj_requirements",
        "client_standards",
        "site_environment",
    }


def test_duplicate_module_id_rejected():
    with pytest.raises(ValueError, match="Duplicate module_id"):
        validate_module_registry([_valid(), _valid()])


def test_duplicate_basis_label_rejected():
    other = replace(_valid(), module_id="other_module")
    with pytest.raises(ValueError, match="Duplicate basis label"):
        validate_module_registry([_valid(), other])


def test_empty_prompt_slot_rejected():
    bad = replace(_valid(), drafting_persona="   ")
    with pytest.raises(ValueError, match="empty prompt slot"):
        validate_module_registry([bad])


def test_pin_without_provenance_rejected():
    basis = _valid().basis
    bad_basis = replace(
        basis,
        standards=basis.standards + (StandardEdition("NFPA 99", "2024"),),
    )
    bad = replace(_valid(), basis=bad_basis)
    with pytest.raises(ValueError, match="provenance source"):
        validate_module_registry([bad])


def test_duplicate_pin_rejected():
    basis = _valid().basis
    bad_basis = replace(
        basis,
        standards=basis.standards
        + (StandardEdition("nfpa 13", "2022", source="dupe"),),
    )
    bad = replace(_valid(), basis=bad_basis)
    with pytest.raises(ValueError, match="duplicate pinned standard"):
        validate_module_registry([bad])


def test_basis_without_standards_rejected():
    bad_basis = StandardsBasis(label="empty", base_codes=_valid().basis.base_codes)
    bad = replace(_valid(), basis=bad_basis)
    with pytest.raises(ValueError, match="pins no standards"):
        validate_module_registry([bad])


def test_malformed_section_number_rejected():
    bad = replace(
        _valid(),
        section_catalog=_valid().section_catalog
        + (SectionDef("21-13-13", "Bad Number"),),
    )
    with pytest.raises(ValueError, match="not CSI-formatted"):
        validate_module_registry([bad])


def test_duplicate_catalog_section_rejected():
    bad = replace(
        _valid(),
        section_catalog=_valid().section_catalog
        + (SectionDef("21 13 13", "Duplicate"),),
    )
    with pytest.raises(ValueError, match="duplicate catalog section"):
        validate_module_registry([bad])


def test_defaultable_topic_without_default_rejected():
    bad = replace(
        _valid(),
        interview_playbook=_valid().interview_playbook
        + (InterviewTopic("no_default", "Topic", "Guidance."),),
    )
    with pytest.raises(ValueError, match="must carry its recommended default"):
        validate_module_registry([bad])


def test_non_defaultable_topic_with_default_rejected():
    bad = replace(
        _valid(),
        interview_playbook=_valid().interview_playbook
        + (
            InterviewTopic(
                "both", "Topic", "Guidance.",
                default="a default", non_defaultable=True,
            ),
        ),
    )
    with pytest.raises(ValueError, match="must not carry a default"):
        validate_module_registry([bad])


def test_duplicate_topic_id_rejected():
    first = _valid().interview_playbook[0]
    bad = replace(
        _valid(),
        interview_playbook=_valid().interview_playbook + (first,),
    )
    with pytest.raises(ValueError, match="duplicate playbook topic"):
        validate_module_registry([bad])


def test_bad_lint_pattern_rejected():
    bad = replace(_valid(), lint_extra_marker_patterns=("([unclosed",))
    with pytest.raises(ValueError, match="does not compile"):
        validate_module_registry([bad])


def test_research_template_placeholder_typo_rejected():
    bad_dim = ResearchDimension(
        "typo_dim", "Typo", "Research {no_such_placeholder} today."
    )
    bad = replace(
        _valid(), research_dimensions=_valid().research_dimensions + (bad_dim,)
    )
    with pytest.raises(ValueError, match="does not format"):
        validate_module_registry([bad])


def test_domain_conventions_placeholder_typo_rejected():
    bad = replace(_valid(), domain_conventions="Uses {bogus_placeholder}.")
    with pytest.raises(ValueError, match="does not format"):
        validate_module_registry([bad])


def test_rendered_prompt_is_stable_and_complete():
    from backend.llm.prompts import render_system_prompt

    prompt = render_system_prompt(_valid())
    assert prompt == render_system_prompt(_valid())  # deterministic
    # Module content made it in.
    assert "21 13 13 Wet-Pipe Sprinkler Systems" in prompt
    assert "(must ask)" in prompt
    assert "set_standard_edition" in prompt
    # Every domain-conventions placeholder resolved.
    assert "{ibc}" not in prompt and "{ifc}" not in prompt
    # Editions in effect do NOT render here (they are dynamic-block data —
    # the stable prompt must stay cacheable across override changes).
    assert "Standards editions in effect" not in prompt
