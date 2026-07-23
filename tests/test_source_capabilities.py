"""Per-element source-preserving edit capability contract coverage.

The report is advisory UX/model guidance.  These tests deliberately build it
from the same immutable source context and final-state planner used by the
authoritative edit gate, then prove that computing guidance cannot mutate any
of those inputs.
"""
from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError, dataclass
import io
from pathlib import Path
import zipfile

import pytest
from fastapi.testclient import TestClient

from backend import sessions
from backend.app import _qc_source_guard, create_app
from backend.llm.conversation import _source_editing_boundary_block
from backend.qc.engine import (
    QCFinding,
    QCResult,
    _lens_user_message,
    _validate_ops,
)
from backend.qc.schema import QC_LENSES
import backend.spec_doc.source_patch as source_patch_module
from backend.spec_doc.importer import parse_master_docx
from backend.spec_doc.model import SpecSection, apply_edits
from backend.spec_doc.source_mapping import SourceBodyMap
from backend.spec_doc.source_patch import (
    SourceCapabilityReport,
    SourcePatchContext,
    build_source_patch_context,
    source_capability_summary,
    source_edit_capabilities,
)
from tests.docx_fidelity_helpers import (
    DOCX_MEDIA_TYPE,
    add_document_protection,
    add_active_content_marker,
    add_signature_origin_marker,
    add_tracked_change,
    document_xml,
    make_fidelity_master,
    make_numbered_island_master,
    make_table_projection_master,
    rewrite_zip_members,
)


_DOCUMENT_PART = "word/document.xml"


@dataclass(frozen=True)
class _SourceInputs:
    source: bytes
    source_map: SourceBodyMap
    baseline: SpecSection
    current: SpecSection
    context: SourcePatchContext


def _source_inputs(
    tmp_path: Path,
    source: bytes,
    *,
    filename: str,
) -> _SourceInputs:
    path = tmp_path / filename
    path.write_bytes(source)
    parsed = parse_master_docx(path)
    assert parsed.source_map is not None
    baseline = SpecSection.from_dict(parsed.section.to_dict())
    current = SpecSection.from_dict(parsed.section.to_dict())
    context = build_source_patch_context(
        source_bytes=source,
        source_map=parsed.source_map,
        baseline=baseline,
    )
    return _SourceInputs(
        source=source,
        source_map=parsed.source_map,
        baseline=baseline,
        current=current,
        context=context,
    )


def _report(inputs: _SourceInputs, current: SpecSection | None = None):
    return source_edit_capabilities(
        context=inputs.context,
        source_map=inputs.source_map,
        baseline=inputs.baseline,
        current=current or inputs.current,
    )


def _operation(
    report: SourceCapabilityReport,
    uid: str,
    operation: str,
):
    return report.elements[uid].operations[operation]


def _repack_document_compression(source: bytes, method: int) -> bytes:
    """Give document.xml a valid ZIP method unsupported by the raw cloner."""
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source), "r") as before, zipfile.ZipFile(
        output,
        "w",
        allowZip64=True,
    ) as after:
        after.comment = before.comment
        for old in before.infolist():
            info = copy.copy(old)
            compression = method if old.filename == _DOCUMENT_PART else old.compress_type
            info.compress_type = compression
            after.writestr(
                info,
                before.read(old),
                compress_type=compression,
                compresslevel=9 if compression == zipfile.ZIP_BZIP2 else None,
            )
    return output.getvalue()


def _with_utf16_document_xml(source: bytes) -> bytes:
    xml_text = document_xml(source).decode("utf-8-sig")
    xml_text = xml_text.replace("encoding='UTF-8'", "encoding='UTF-16'", 1)
    return rewrite_zip_members(
        source,
        replacements={"word/document.xml": xml_text.encode("utf-16")},
    )


@pytest.fixture
def api_client():
    sessions.reset_session()
    with TestClient(create_app()) as client:
        yield client
    sessions.reset_session()


def _import_api(
    client: TestClient,
    source: bytes,
    *,
    filename: str = "capability-master.docx",
) -> dict:
    response = client.post(
        "/api/import/master",
        files={"file": (filename, source, DOCX_MEDIA_TYPE)},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_report_is_deeply_immutable_deterministic_and_does_not_mutate_inputs(
    tmp_path,
):
    inputs = _source_inputs(
        tmp_path,
        make_fidelity_master(tmp_path),
        filename="capability-immutability.docx",
    )
    baseline_before = copy.deepcopy(inputs.baseline.to_dict())
    current_before = copy.deepcopy(inputs.current.to_dict())
    source_map_before = copy.deepcopy(inputs.source_map.to_dict())
    context_before = (
        inputs.context.source_bytes,
        inputs.context.source_sha256,
        inputs.context.document_xml,
        inputs.context.document_xml_sha256,
        inputs.context.body_inventory,
        tuple(inputs.context.numbering_usage_counts.items()),
        tuple(inputs.context.paragraph_templates.items()),
        inputs.context.raw_zip_archive,
    )

    first = _report(inputs)
    second = _report(inputs)

    assert first.to_dict() == second.to_dict()
    assert inputs.baseline.to_dict() == baseline_before
    assert inputs.current.to_dict() == current_before
    assert inputs.source_map.to_dict() == source_map_before
    assert (
        inputs.context.source_bytes,
        inputs.context.source_sha256,
        inputs.context.document_xml,
        inputs.context.document_xml_sha256,
        inputs.context.body_inventory,
        tuple(inputs.context.numbering_usage_counts.items()),
        tuple(inputs.context.paragraph_templates.items()),
        inputs.context.raw_zip_archive,
    ) == context_before
    assert inputs.source == inputs.context.source_bytes

    with pytest.raises(TypeError):
        first.elements["forged"] = first.elements["sec"]
    with pytest.raises(TypeError):
        first.elements["pt1.a1.p1"].operations["forged"] = _operation(
            first,
            "pt1.a1.p1",
            "replace_text",
        )
    with pytest.raises(FrozenInstanceError):
        first.status = "blocked"
    with pytest.raises(FrozenInstanceError):
        _operation(first, "pt1.a1.p1", "replace_text").allowed = False


def test_manual_label_simple_text_headings_and_complex_content(tmp_path):
    inputs = _source_inputs(
        tmp_path,
        make_fidelity_master(tmp_path),
        filename="capability-manual-label.docx",
    )
    report = _report(inputs)

    assert report.status == "ready"
    for uid in ("sec", "pt1", "pt1.a1"):
        heading = _operation(report, uid, "replace_text")
        assert heading.allowed is False
        assert heading.blocker == "heading_change"

    manual = report.elements["pt1.a1.p1"].operations
    assert manual["replace_text"].allowed is True
    assert manual["delete"].allowed is False
    assert manual["delete"].blocker == "manual_label_structural_change"
    assert manual["move"].allowed is False
    assert manual["set_status"].allowed is True
    assert manual["set_provenance"].allowed is True

    hyperlink = report.elements["pt1.a1.p3"].operations
    assert hyperlink["replace_text"].allowed is False
    assert hyperlink["replace_text"].blocker == "complex_paragraph_markup"
    assert hyperlink["set_status"].allowed is True


def test_table_projection_is_body_read_only_but_metadata_editable(tmp_path):
    inputs = _source_inputs(
        tmp_path,
        make_table_projection_master(tmp_path),
        filename="capability-table.docx",
    )
    report = _report(inputs)
    table_row = report.elements["pt2.a1.p1"].operations

    assert table_row["replace_text"].allowed is False
    assert table_row["replace_text"].blocker == "table_projection"
    assert table_row["delete"].allowed is False
    assert table_row["move"].allowed is False
    assert table_row["add_paragraph"].allowed is False
    assert table_row["set_status"].allowed is True
    assert table_row["set_provenance"].allowed is True


def test_numbered_island_reports_exact_add_delete_and_move_positions(tmp_path):
    inputs = _source_inputs(
        tmp_path,
        make_numbered_island_master(tmp_path),
        filename="capability-numbered.docx",
    )
    report = _report(inputs)

    add = _operation(report, "pt1.a1", "add_paragraph")
    assert add.allowed is True
    assert add.island_key == "pt1.a1.p1"
    assert add.allowed_positions == (0, 1, 2, 3)
    assert len(add.placements) == 1
    assert add.placements[0].island_key == "pt1.a1.p1"
    assert add.placements[0].allowed_positions == (0, 1, 2, 3)

    expected_moves = {
        "pt1.a1.p1": (0, (1, 2)),
        "pt1.a1.p2": (1, (0, 2)),
        "pt1.a1.p3": (2, (0, 1)),
    }
    for uid, (current_position, allowed_positions) in expected_moves.items():
        operations = report.elements[uid].operations
        assert operations["replace_text"].allowed is True
        assert operations["delete"].allowed is True
        assert operations["delete"].island_key == "pt1.a1.p1"
        assert operations["move"].allowed is True
        assert operations["move"].island_key == "pt1.a1.p1"
        assert operations["move"].current_position == current_position
        assert operations["move"].allowed_positions == allowed_positions
        assert current_position not in operations["move"].allowed_positions


def test_capabilities_recompute_after_prior_add_delete_and_move(tmp_path):
    inputs = _source_inputs(
        tmp_path,
        make_numbered_island_master(tmp_path),
        filename="capability-recompute.docx",
    )
    after_add, applied = apply_edits(
        inputs.current,
        [
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "position": 1,
                "text": "A provision added before capability recomputation.",
            }
        ],
    )
    added_uid = applied[0]["id"]
    assert added_uid == "pt1.a1.p4"
    added_report = _report(inputs, after_add)
    added = added_report.elements[added_uid].operations
    assert added["replace_text"].allowed is True
    assert added["delete"].allowed is True
    assert added["delete"].island_key == "pt1.a1.p1"
    assert added["move"].allowed is True
    assert added["move"].current_position == 1
    assert added["move"].allowed_positions == (0, 2, 3)
    assert _operation(
        added_report,
        "pt1.a1",
        "add_paragraph",
    ).allowed_positions == (0, 1, 2, 3, 4)

    after_delete, _applied = apply_edits(
        after_add,
        [{"action": "delete", "target_id": "pt1.a1.p2"}],
    )
    deleted_report = _report(inputs, after_delete)
    assert "pt1.a1.p2" not in deleted_report.elements
    assert _operation(
        deleted_report,
        "pt1.a1",
        "add_paragraph",
    ).allowed_positions == (0, 1, 2, 3)
    assert _operation(
        deleted_report,
        added_uid,
        "move",
    ).allowed_positions == (0, 2)

    after_move, _applied = apply_edits(
        after_delete,
        [{"action": "move", "target_id": "pt1.a1.p3", "position": 0}],
    )
    moved_report = _report(inputs, after_move)
    moved = _operation(moved_report, "pt1.a1.p3", "move")
    assert moved.current_position == 0
    assert moved.allowed_positions == (1, 2)
    assert _operation(moved_report, added_uid, "move").current_position == 2


@pytest.mark.parametrize(
    ("mutate", "expected_blocker", "filename"),
    [
        pytest.param(
            add_document_protection,
            "document_protection",
            "capability-protected.docx",
            id="global-document-protection",
        ),
        pytest.param(
            lambda source: _repack_document_compression(source, zipfile.ZIP_BZIP2),
            "unsupported_raw_zip_layout",
            "capability-unsupported-raw-zip.docx",
            id="runtime-raw-zip-layout",
        ),
        pytest.param(
            add_signature_origin_marker,
            "signed_package",
            "capability-signed.docx",
            id="global-signed-package",
        ),
        pytest.param(
            add_tracked_change,
            "tracked_changes",
            "capability-revision.docx",
            id="global-tracked-change",
        ),
        pytest.param(
            add_active_content_marker,
            "active_content",
            "capability-active-content.docx",
            id="global-active-content",
        ),
        pytest.param(
            _with_utf16_document_xml,
            "unsupported_source_xml_encoding",
            "capability-utf16.docx",
            id="runtime-unsupported-xml-encoding",
        ),
    ],
)
def test_global_and_runtime_blockers_deny_body_but_allow_metadata(
    tmp_path,
    mutate,
    expected_blocker,
    filename,
):
    source = mutate(make_numbered_island_master(tmp_path))
    inputs = _source_inputs(tmp_path, source, filename=filename)
    report = _report(inputs)

    assert report.status == "pass_through_only"
    for uid, operation_name in (
        ("sec", "replace_text"),
        ("pt1", "replace_text"),
        ("pt1.a1", "replace_text"),
        ("pt1.a1", "add_paragraph"),
        ("pt1.a1.p1", "replace_text"),
        ("pt1.a1.p1", "delete"),
        ("pt1.a1.p1", "move"),
        ("pt1.a1.p1", "add_paragraph"),
    ):
        capability = _operation(report, uid, operation_name)
        assert capability.allowed is False
        assert capability.blocker == expected_blocker
        assert capability.message

    assert _operation(report, "sec", "set_project_profile").allowed is True
    assert _operation(report, "sec", "set_standard_edition").allowed is True
    assert _operation(report, "sec", "set_standard_suppressed").allowed is True
    assert _operation(report, "pt1.a1.p1", "set_status").allowed is True
    assert _operation(report, "pt1.a1.p1", "set_provenance").allowed is True


@pytest.mark.parametrize("complex_middle", ["field", "hyperlink"])
def test_field_and_hyperlink_paragraphs_are_independently_read_only(
    tmp_path,
    complex_middle,
):
    inputs = _source_inputs(
        tmp_path,
        make_numbered_island_master(
            tmp_path,
            complex_middle=complex_middle,
        ),
        filename=f"capability-{complex_middle}.docx",
    )
    operations = _report(inputs).elements["pt1.a1.p2"].operations

    assert operations["replace_text"].allowed is False
    assert operations["replace_text"].blocker in {
        "complex_paragraph_markup",
        "complex_run_markup",
    }
    assert operations["delete"].allowed is False
    assert operations["move"].allowed is False
    assert operations["set_status"].allowed is True
    assert operations["set_provenance"].allowed is True


def test_split_complex_run_is_read_only_without_freezing_metadata(tmp_path):
    inputs = _source_inputs(
        tmp_path,
        make_fidelity_master(
            tmp_path,
            split_target_runs=True,
            filename="capability-complex-run.docx",
        ),
        filename="capability-complex-run.docx",
    )
    operations = _report(inputs).elements["pt1.a1.p1"].operations

    assert operations["replace_text"].allowed is False
    assert operations["replace_text"].blocker in {
        "complex_paragraph_markup",
        "complex_run_markup",
    }
    assert operations["set_status"].allowed is True
    assert operations["set_provenance"].allowed is True


def test_nested_paragraphs_deny_structural_changes_but_keep_metadata(tmp_path):
    inputs = _source_inputs(
        tmp_path,
        make_numbered_island_master(tmp_path, ilvls=(0, 1, 0)),
        filename="capability-nested.docx",
    )
    report = _report(inputs)

    for uid in ("pt1.a1.p1", "pt1.a1.p1.p1"):
        operations = report.elements[uid].operations
        assert operations["delete"].allowed is False
        assert operations["move"].allowed is False
        assert operations["add_paragraph"].allowed is False
        assert operations["set_status"].allowed is True
        assert operations["set_provenance"].allowed is True


def test_ambiguous_add_template_does_not_disable_whole_element_moves(
    tmp_path,
):
    inputs = _source_inputs(
        tmp_path,
        make_numbered_island_master(tmp_path, inconsistent_format=True),
        filename="capability-ambiguous-template.docx",
    )
    report = _report(inputs)

    add = _operation(report, "pt1.a1", "add_paragraph")
    assert add.allowed is False
    assert add.blocker == "ambiguous_structural_template"
    assert _operation(report, "pt1.a1.p1", "delete").allowed is True
    assert _operation(report, "pt1.a1.p1", "move").allowed is True


@pytest.mark.parametrize(
    ("fixture_kwargs", "filename"),
    [
        pytest.param(
            {"separator": "empty_after_second"},
            "capability-leaked-num-id.docx",
            id="leaked-numbering-instance",
        ),
        pytest.param(
            {"mixed_num_id": True},
            "capability-shared-num-id.docx",
            id="mixed-numbering-instance",
        ),
        pytest.param(
            {"separator": "sdt"},
            "capability-content-control-boundary.docx",
            id="content-control-boundary",
        ),
    ],
)
def test_numbering_leaks_and_opaque_content_controls_fail_closed(
    tmp_path,
    fixture_kwargs,
    filename,
):
    inputs = _source_inputs(
        tmp_path,
        make_numbered_island_master(tmp_path, **fixture_kwargs),
        filename=filename,
    )
    report = _report(inputs)

    assert _operation(report, "pt1.a1", "add_paragraph").allowed is False
    assert not any(
        _operation(report, uid, "delete").allowed
        for uid in ("pt1.a1.p1", "pt1.a1.p2", "pt1.a1.p3")
    )
    assert _operation(report, "pt1.a1.p1", "set_status").allowed is True


def test_explicit_context_is_reused_for_every_capability_probe(tmp_path, monkeypatch):
    inputs = _source_inputs(
        tmp_path,
        make_numbered_island_master(tmp_path),
        filename="capability-context-reuse.docx",
    )

    def unexpected_rebuild(**_kwargs):
        pytest.fail("capability probes rebuilt the supplied immutable context")

    monkeypatch.setattr(
        source_patch_module,
        "build_source_patch_context",
        unexpected_rebuild,
    )
    first = _report(inputs)
    second = _report(inputs)

    assert first.to_dict() == second.to_dict()
    assert first.status == "ready"


def test_compact_summary_contains_ids_and_islands_without_package_details(tmp_path):
    inputs = _source_inputs(
        tmp_path,
        make_numbered_island_master(tmp_path),
        filename="capability-summary.docx",
    )
    summary = source_capability_summary(_report(inputs), inputs.current)

    assert "Source-preserving body permissions:" in summary
    assert "Text-editable IDs: pt1.a1.p1, pt1.a1.p2, pt1.a1.p3" in summary
    assert "Structural island pt1.a1.p1: pt1.a1.p1, pt1.a1.p2, pt1.a1.p3" in summary
    assert "Add positions for pt1.a1 in island pt1.a1.p1: 0, 1, 2, 3" in summary
    assert "Status, research provenance, and project metadata may still be changed." in summary
    assert "<w:" not in summary
    assert "word/document.xml" not in summary
    assert "heading_change" not in summary
    assert "ZIP" not in summary
    assert len(summary) < 1_000


def test_api_capabilities_refresh_across_history_project_load_and_qc_apply(
    tmp_path,
    api_client,
):
    fresh = api_client.get("/api/doc")
    assert fresh.status_code == 200
    assert fresh.json()["source_capabilities"] is None

    source = make_numbered_island_master(
        tmp_path,
        filename="capability-api-lifecycle.docx",
    )
    imported = _import_api(
        api_client,
        source,
        filename="capability-api-lifecycle.docx",
    )
    assert imported["source_capabilities"]["status"] == "ready"
    assert imported["source_capabilities"]["elements"]["pt1.a1.p1"][
        "replace_text"
    ]["allowed"] is True
    assert imported["source_capabilities"]["elements"]["pt1.a1"][
        "replace_text"
    ]["blocker"] == "heading_change"

    pre_import = api_client.post("/api/doc/undo")
    assert pre_import.status_code == 200
    assert pre_import.json()["source_capabilities"] is None
    restored_import = api_client.post("/api/doc/redo")
    assert restored_import.status_code == 200
    assert restored_import.json()["source_capabilities"]["status"] == "ready"

    added = api_client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "add_paragraph",
                    "target_id": "pt1.a1",
                    "position": 1,
                    "text": "An earlier safe capability addition.",
                }
            ]
        },
    )
    assert added.status_code == 200, added.text
    added_uid = added.json()["applied"][0]["id"]
    assert added_uid == "pt1.a1.p4"
    assert added_uid in added.json()["source_capabilities"]["elements"]

    undone = api_client.post("/api/doc/undo")
    assert undone.status_code == 200
    assert added_uid not in undone.json()["source_capabilities"]["elements"]
    redone = api_client.post("/api/doc/redo")
    assert redone.status_code == 200
    assert added_uid in redone.json()["source_capabilities"]["elements"]

    saved = api_client.get("/api/project/save")
    assert saved.status_code == 200, saved.text
    assert api_client.post("/api/session/reset").status_code == 200
    loaded = api_client.post(
        "/api/project/load-file",
        files={
            "file": (
                "capability-api-lifecycle.baspec",
                saved.content,
                "application/octet-stream",
            )
        },
    )
    assert loaded.status_code == 200, loaded.text
    assert added_uid in loaded.json()["source_capabilities"]["elements"]

    finding = QCFinding(
        finding_id="qc-capability-delete",
        lens_id="internal_consistency",
        severity="critical",
        element_id="pt1.a1.p2",
        title="Remove a redundant requirement",
        issue="The requirement is redundant.",
        rationale="Exercise capability refresh after the authoritative QC gate.",
        proposed_ops=[{"action": "delete", "target_id": "pt1.a1.p2"}],
        ops_valid=True,
    )
    session = sessions.get_session()
    session.qc.restore(
        QCResult(
            findings=[finding],
            version_index=session.doc.index,
        )
    )
    applied = api_client.post(
        "/api/qc/apply",
        json={"finding_ids": [finding.finding_id]},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["outcomes"][finding.finding_id] == "applied"
    refreshed = applied.json()["source_capabilities"]
    assert "pt1.a1.p2" not in refreshed["elements"]
    assert refreshed["elements"]["pt1.a1"]["add_paragraph"][
        "allowed_positions"
    ] == [0, 1, 2, 3]


def test_pass_through_api_keeps_metadata_editable_and_rejects_forged_body_edit(
    tmp_path,
    api_client,
):
    source = add_signature_origin_marker(
        make_numbered_island_master(
            tmp_path,
            filename="capability-api-signed.docx",
        )
    )
    imported = _import_api(
        api_client,
        source,
        filename="capability-api-signed.docx",
    )
    capabilities = imported["source_capabilities"]
    paragraph = capabilities["elements"]["pt1.a1.p1"]
    assert capabilities["status"] == "pass_through_only"
    assert paragraph["replace_text"]["allowed"] is False
    assert paragraph["replace_text"]["blocker"] == "signed_package"
    assert paragraph["set_status"]["allowed"] is True
    assert paragraph["set_provenance"]["allowed"] is True

    metadata = api_client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "replace",
                    "target_id": "pt1.a1.p1",
                    "status": "confirmed",
                    "source_item_id": "r-capability-proof",
                }
            ]
        },
    )
    assert metadata.status_code == 200, metadata.text
    assert metadata.json()["source_capabilities"]["elements"]["pt1.a1.p1"][
        "set_status"
    ]["allowed"] is True
    before = metadata.json()["doc"]

    forged = api_client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "replace",
                    "target_id": "pt1.a1.p1",
                    "text": "A forged client-side body edit.",
                }
            ]
        },
    )
    assert forged.status_code == 400
    assert "[signed_package]" in forged.json()["error"]
    assert api_client.get("/api/doc").json()["doc"] == before
    assert api_client.get("/api/import/original").content == source


def test_legacy_source_less_project_has_no_imported_body_capability_lock(
    tmp_path,
    api_client,
):
    source = make_fidelity_master(
        tmp_path,
        filename="capability-legacy-source-less.docx",
    )
    _import_api(
        api_client,
        source,
        filename="capability-legacy-source-less.docx",
    )
    legacy = sessions.project_payload(sessions.get_session())
    for key in ("source_map", "source_body_map", "source_docx"):
        legacy.pop(key, None)

    assert api_client.post("/api/session/reset").status_code == 200
    loaded = api_client.post("/api/project/load", json=legacy)
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["baseline_index"] is not None
    assert loaded.json()["source_available"] is False
    assert loaded.json()["source_capabilities"] is None
    assert _source_editing_boundary_block(sessions.get_session()) is None
    assert _qc_source_guard(sessions.get_session()) is None

    # Legacy source-less projects have always used the ordinary semantic edit
    # path; the advisory contract must not freeze them merely because their
    # historical import baseline remains in the document store.
    edited = api_client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "replace",
                    "target_id": "pt1.a1.p1",
                    "text": "A normalized legacy-project edit.",
                }
            ]
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["source_capabilities"] is None


def test_new_branch_before_import_baseline_remains_source_unconstrained(
    tmp_path,
    api_client,
):
    source = make_fidelity_master(
        tmp_path,
        filename="capability-abandoned-import-branch.docx",
    )
    _import_api(
        api_client,
        source,
        filename="capability-abandoned-import-branch.docx",
    )
    undone = api_client.post("/api/doc/undo")
    assert undone.status_code == 200
    assert undone.json()["source_capabilities"] is None

    first = api_client.post(
        "/api/doc/edit",
        json={
            "ops": [
                    {
                        "action": "add_article",
                        "target_id": "pt1",
                        "text": "FRESH BRANCH ARTICLE",
                    }
            ]
        },
    )
    assert first.status_code == 200, first.text
    article_uid = first.json()["applied"][0]["id"]
    assert first.json()["baseline_index"] is None
    assert first.json()["source_available"] is True
    assert first.json()["source_capabilities"] is None

    second = api_client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "replace",
                    "target_id": article_uid,
                    "text": "FRESH BRANCH ARTICLE REVISED",
                }
            ]
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["source_capabilities"] is None


@pytest.mark.parametrize(
    ("corrupt", "expected_blocker"),
    [
        pytest.param(
            lambda session: setattr(session, "source_docx_map", None),
            "source_unavailable",
            id="missing-source-map",
        ),
        pytest.param(
            lambda session: setattr(
                session,
                "source_docx_bytes",
                session.source_docx_bytes + b"stale-source-identity",
            ),
            "source_hash_mismatch",
            id="stale-source-context",
        ),
    ],
)
def test_incomplete_source_scope_denies_body_but_allows_metadata_everywhere(
    tmp_path,
    api_client,
    corrupt,
    expected_blocker,
):
    source = make_numbered_island_master(
        tmp_path,
        filename=f"capability-{expected_blocker}.docx",
    )
    _import_api(
        api_client,
        source,
        filename=f"capability-{expected_blocker}.docx",
    )
    session = sessions.get_session()
    corrupt(session)

    blocked = api_client.get("/api/doc")
    assert blocked.status_code == 200
    operations = blocked.json()["source_capabilities"]["elements"][
        "pt1.a1.p1"
    ]
    assert operations["replace_text"]["allowed"] is False
    assert operations["replace_text"]["blocker"] == expected_blocker
    assert operations["set_status"]["allowed"] is True
    assert operations["set_provenance"]["allowed"] is True

    metadata = api_client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "replace",
                    "target_id": "pt1.a1.p1",
                    "status": "confirmed",
                    "source_item_id": "r-incomplete-source-metadata",
                }
            ]
        },
    )
    assert metadata.status_code == 200, metadata.text
    before = metadata.json()["doc"]

    forged = api_client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "replace",
                    "target_id": "pt1.a1.p1",
                    "text": "This incomplete-source body edit must fail.",
                }
            ]
        },
    )
    assert forged.status_code == 400
    assert f"[{expected_blocker}]" in forged.json()["error"]
    assert api_client.get("/api/doc").json()["doc"] == before

    guard = _qc_source_guard(session)
    assert guard is not None and guard.required is True
    metadata_finding = QCFinding(
        finding_id="qc-incomplete-source-metadata",
        lens_id="internal_consistency",
        severity="medium",
        element_id="pt1.a1.p1",
        title="Confirm reviewed status",
        issue="The imported requirement was reviewed.",
        rationale="Metadata remains independent from body XML.",
        proposed_ops=[
            {
                "action": "set_status",
                "target_id": "pt1.a1.p1",
                "status": "imported",
            }
        ],
    )
    _validate_ops(metadata_finding, session.doc.doc, guard)
    assert metadata_finding.ops_valid is True

    body_finding = QCFinding(
        finding_id="qc-incomplete-source-body",
        lens_id="internal_consistency",
        severity="medium",
        element_id="pt1.a1.p1",
        title="Rewrite source body",
        issue="Try a body rewrite with incomplete source state.",
        rationale="The source final-state gate must reject it.",
        proposed_ops=[
            {
                "action": "replace",
                "target_id": "pt1.a1.p1",
                "text": "A QC body edit that must stay advisory.",
            }
        ],
    )
    _validate_ops(body_finding, session.doc.doc, guard)
    assert body_finding.ops_valid is False


def test_session_llm_and_qc_use_compact_capability_summary_without_mutation(
    tmp_path,
    api_client,
):
    source = make_numbered_island_master(
        tmp_path,
        filename="capability-prompt-summary.docx",
    )
    _import_api(
        api_client,
        source,
        filename="capability-prompt-summary.docx",
    )
    session = sessions.get_session()
    before_history = copy.deepcopy(session.doc.snapshot())
    before_source = session.source_docx_bytes

    llm_block = _source_editing_boundary_block(session)
    guard = _qc_source_guard(session)
    assert llm_block is not None
    assert guard is not None
    assert guard.capability_summary
    qc_message = _lens_user_message(
        QC_LENSES[0],
        session.doc.doc,
        session.module,
        None,
        source_capability_summary=guard.capability_summary,
    )

    for rendered in (llm_block, guard.capability_summary, qc_message):
        assert "Text-editable IDs: pt1.a1.p1, pt1.a1.p2, pt1.a1.p3" in rendered
        assert "Structural island pt1.a1.p1" in rendered
        assert "<w:" not in rendered
        assert "word/document.xml" not in rendered
        assert "allowed_positions" not in rendered
        assert "ZIP" not in rendered
    assert "<source_preserving_body_permissions>" in qc_message
    assert session.doc.snapshot() == before_history
    assert session.source_docx_bytes == before_source == source
