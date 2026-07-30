"""Focused regressions for complete Final-QC input identity."""
from __future__ import annotations

import inspect
from dataclasses import replace
from types import SimpleNamespace

from backend import settings
from backend.qc.engine import (
    QCSourceGuard,
    _lens_request_suffix,
    _lens_shared_prefix,
    _render_profile,
    build_qc_input_manifest,
    qc_input_fingerprint,
)
from backend.qc.schema import QC_LENSES
from backend.research.engine import DimensionStatus, RequirementsProfile
from backend.spec_doc.model import SpecSection
from backend.spec_doc.source_mapping import SourceBodyMap
from backend.spec_modules import DEFAULT_MODULE


def _section(title: str = "WET-PIPE SPRINKLER SYSTEMS") -> SpecSection:
    section = SpecSection.empty()
    section.number = "21 13 13"
    section.title = title
    return section


def _source_guard(section: SpecSection) -> QCSourceGuard:
    source_map = SourceBodyMap(
        source_sha256="1" * 64,
        document_xml_sha256="2" * 64,
        baseline_projection_sha256="3" * 64,
        body_child_count=0,
        body_blocks=(),
        bindings={},
    )
    context = SimpleNamespace(
        source_sha256="1" * 64,
        baseline_projection_sha256="3" * 64,
        document_xml_sha256="2" * 64,
        global_blockers=(),
        runtime_mutation_issues=(),
        numbering_levels=frozenset({("1", "0")}),
        document_tag="{wordprocessingml}document",
        non_body_c14n_sha256=("4" * 64,),
    )
    return QCSourceGuard(
        required=True,
        source_bytes=b"source-package-a",
        source_map=source_map,
        baseline=section,
        context=context,  # type: ignore[arg-type] - minimal immutable test record
        capability_summary="Mapped source-backed body edits are available.",
    )


def _manifest(section: SpecSection, guard: QCSourceGuard) -> dict:
    return build_qc_input_manifest(
        section,
        None,
        DEFAULT_MODULE,
        version_index=0,
        discipline="Fire Protection",
        source_guard=guard,
        model=settings.QC_MODEL,
        max_tokens=settings.QC_MAX_TOKENS,
    )


def test_each_source_preservation_constituent_changes_full_input_identity() -> None:
    section = _section()
    guard = _source_guard(section)
    baseline_manifest = _manifest(section, guard)
    baseline_identity = qc_input_fingerprint(baseline_manifest)
    source = baseline_manifest["source_preservation"]

    assert source["source_bytes_fingerprint"]
    assert source["source_map_fingerprint"]
    assert source["baseline_fingerprint"]
    assert source["patch_context_fingerprint"]

    changed_baseline = _section("ALTERED SOURCE BASELINE")
    changed_map = replace(
        guard.source_map,
        global_blockers=("changed source-map policy",),
    )
    changed_context = SimpleNamespace(
        **{
            **vars(guard.context),
            "document_tag": "{wordprocessingml}changed-document",
        }
    )
    variants = {
        "source bytes": replace(guard, source_bytes=b"source-package-b"),
        "source map": replace(guard, source_map=changed_map),
        "source baseline": replace(guard, baseline=changed_baseline),
        "source patch context": replace(guard, context=changed_context),
    }

    for label, variant in variants.items():
        manifest = _manifest(section, variant)
        assert qc_input_fingerprint(manifest) != baseline_identity, label



# ---------------------------------------------------------------------------
# Captured research facts (Chunk 3.1)
# ---------------------------------------------------------------------------


def _research_profile(*statuses: tuple[str, str, str]) -> RequirementsProfile:
    return RequirementsProfile(
        items=[],
        dimension_statuses=[
            DimensionStatus(dimension_id=did, status=status, title=title)
            for did, status, title in statuses
        ],
        research_date="2026-07-21",
        project={"city": "Ashburn", "state": "VA", "country": "USA"},
    )


def test_the_manifest_names_which_coverage_failed_not_only_how_many() -> None:
    """A report is an audit of the run's input snapshot.

    Counts cannot answer "which two of the four?", and the live profile is
    not available to a report opened later (it may have gained a round), so
    the names have to be captured here.
    """
    profile = _research_profile(
        ("governing_codes", "completed", "Governing codes"),
        ("ahj_requirements", "failed", "AHJ requirements"),
        ("client_standards", "completed", "Client and insurer standards"),
        ("site_environment", "failed", "Site and environment"),
    )
    research = build_qc_input_manifest(
        _section(),
        profile,
        DEFAULT_MODULE,
        version_index=3,
        model=settings.QC_MODEL,
        max_tokens=settings.QC_MAX_TOKENS,
    )["requirements_research"]

    assert research["present"] is True
    assert research["dimension_count"] == 4
    assert research["completed_dimensions"] == 2
    assert research["failed_dimensions"] == 2
    # Module declaration order, both lists, so a rendered limitation reads
    # the same way every time.
    assert research["completed_dimension_ids"] == [
        "governing_codes",
        "client_standards",
    ]
    assert research["failed_dimension_ids"] == [
        "ahj_requirements",
        "site_environment",
    ]
    assert research["dimension_titles"]["ahj_requirements"] == "AHJ requirements"
    # The sets a later chunk validates as disjoint really are.
    assert not set(research["completed_dimension_ids"]) & set(
        research["failed_dimension_ids"]
    )


def test_an_absent_profile_records_empty_coverage_rather_than_omitting_it() -> None:
    research = build_qc_input_manifest(
        _section(),
        None,
        DEFAULT_MODULE,
        version_index=0,
        model=settings.QC_MODEL,
        max_tokens=settings.QC_MAX_TOKENS,
    )["requirements_research"]
    assert research["present"] is False
    assert research["dimension_count"] == 0
    assert research["completed_dimension_ids"] == []
    assert research["failed_dimension_ids"] == []
    # No statuses were recorded, but the module still DECLARES coverage, and
    # naming it is what lets a report say what was never researched at all
    # rather than only that a profile was absent (Chunk 3.2).
    assert research["declared_dimension_count"] == 4
    assert sorted(research["dimension_titles"]) == sorted(
        research["required_dimension_ids"]
    )
    assert research["incomplete_required_dimension_ids"] == [
        d.dimension_id for d in DEFAULT_MODULE.research_dimensions
    ]


def test_partial_coverage_changes_the_full_input_identity() -> None:
    """Two runs that saw different coverage are not the same review."""
    complete = _research_profile(
        ("governing_codes", "completed", "Governing codes"),
        ("ahj_requirements", "completed", "AHJ requirements"),
    )
    partial = _research_profile(
        ("governing_codes", "completed", "Governing codes"),
        ("ahj_requirements", "failed", "AHJ requirements"),
    )

    def identity(profile: RequirementsProfile) -> str:
        return qc_input_fingerprint(
            build_qc_input_manifest(
                _section(),
                profile,
                DEFAULT_MODULE,
                version_index=0,
                model=settings.QC_MODEL,
                max_tokens=settings.QC_MAX_TOKENS,
            )
        )

    assert identity(complete) != identity(partial)


def test_every_lens_is_told_which_coverage_is_absent() -> None:
    """The warning rides the SHARED cached prefix, so all five lenses see it.

    Asserting it on the prefix rather than looping over per-lens messages is
    the stronger claim: the prefix is byte-identical for every lens by
    construction (that is the v1.8.0 caching invariant), and the per-lens
    suffix carries no profile content at all — so no lens can be built
    without this.
    """
    profile = _research_profile(
        ("governing_codes", "completed", "Governing codes"),
        ("ahj_requirements", "failed", "AHJ requirements"),
    )
    rendered = _render_profile(profile)
    assert "INCOMPLETE COVERAGE: research for AHJ requirements never" in rendered
    assert "ABSENT, not verified-empty" in rendered

    prefix = _lens_shared_prefix(
        _section(),
        DEFAULT_MODULE,
        profile,
        "Fire Protection",
        "",
        "Current date: 2026-07-21",
    )
    assert "INCOMPLETE COVERAGE" in prefix
    # And it cannot be lens-specific: `_lens_shared_prefix` takes no lens
    # argument, so the profile physically cannot vary between them, and no
    # per-lens suffix carries coverage text of its own.
    assert "lens" not in inspect.signature(_lens_shared_prefix).parameters
    for lens in QC_LENSES:
        assert "INCOMPLETE COVERAGE" not in _lens_request_suffix(lens), lens.lens_id
