"""Focused regressions for complete Final-QC input identity."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from backend import settings
from backend.qc.engine import (
    QCSourceGuard,
    build_qc_input_manifest,
    qc_input_fingerprint,
)
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

