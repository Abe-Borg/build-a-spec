"""Relationship-aware mutation blockers for source-preserving DOCX export."""
from __future__ import annotations

import io
import zipfile
from collections.abc import Callable

import pytest
from lxml import etree
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.spec_doc.importer import parse_master_docx
from backend.spec_doc.model import apply_edits
from backend.spec_doc.source_mapping import detect_global_source_blockers
from backend.spec_doc.source_patch import SourcePatchError, build_source_preserving_docx
from tests.docx_fidelity_helpers import (
    DOCX_MEDIA_TYPE,
    make_numbered_island_master,
    rewrite_zip_members,
)


_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_SETTINGS_REL_TRANSITIONAL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings"
)
_SETTINGS_REL_STRICT = (
    "http://purl.oclc.org/ooxml/officeDocument/relationships/settings"
)
_SIGNATURE_ORIGIN_REL = (
    "http://schemas.openxmlformats.org/package/2006/relationships/"
    "digital-signature/origin"
)
_OLE_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject"
)
_SETTINGS_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"
)


def _serialize(element) -> bytes:
    return etree.tostring(
        element,
        encoding="UTF-8",
        xml_declaration=True,
        standalone=True,
    )


def _xml_part(payload: bytes, name: str):
    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        return etree.fromstring(archive.read(name))


def _add_relationship(root, rel_id: str, rel_type: str, target: str) -> None:
    relationship = etree.SubElement(root, f"{{{_REL_NS}}}Relationship")
    relationship.set("Id", rel_id)
    relationship.set("Type", rel_type)
    relationship.set("Target", target)


def _add_override(root, part_name: str, content_type: str) -> None:
    override = etree.SubElement(root, f"{{{_CT_NS}}}Override")
    override.set("PartName", part_name)
    override.set("ContentType", content_type)


def _with_track_revisions(payload: bytes, value: str | None) -> bytes:
    settings = _xml_part(payload, "word/settings.xml")
    track = etree.Element(f"{{{_W_NS}}}trackRevisions")
    if value is not None:
        track.set(f"{{{_W_NS}}}val", value)
    settings.insert(0, track)
    return rewrite_zip_members(
        payload,
        replacements={"word/settings.xml": _serialize(settings)},
    )


def _with_relocated_protected_settings(payload: bytes, rel_type: str) -> bytes:
    settings = _xml_part(payload, "word/settings.xml")
    protection = etree.Element(f"{{{_W_NS}}}documentProtection")
    protection.set(f"{{{_W_NS}}}edit", "readOnly")
    protection.set(f"{{{_W_NS}}}enforcement", "1")
    settings.insert(0, protection)

    relationships = _xml_part(payload, "word/_rels/document.xml.rels")
    settings_relationships = [
        child
        for child in relationships
        if child.get("Type") in {
            _SETTINGS_REL_TRANSITIONAL,
            _SETTINGS_REL_STRICT,
        }
    ]
    assert len(settings_relationships) == 1
    settings_relationships[0].set("Type", rel_type)
    settings_relationships[0].set("Target", "../client-data/review-settings.xml")

    content_types = _xml_part(payload, "[Content_Types].xml")
    settings_overrides = [
        child
        for child in content_types
        if child.get("PartName") == "/word/settings.xml"
    ]
    assert len(settings_overrides) == 1
    settings_overrides[0].set("PartName", "/client-data/review-settings.xml")
    settings_overrides[0].set("ContentType", _SETTINGS_CONTENT_TYPE)

    return rewrite_zip_members(
        payload,
        replacements={
            "word/_rels/document.xml.rels": _serialize(relationships),
            "[Content_Types].xml": _serialize(content_types),
        },
        omit={"word/settings.xml"},
        additions=[("client-data/review-settings.xml", _serialize(settings))],
    )


def _with_relocated_signature(payload: bytes) -> bytes:
    relationships = _xml_part(payload, "_rels/.rels")
    content_types = _xml_part(payload, "[Content_Types].xml")
    _add_relationship(
        relationships,
        "rIdRelocatedSignature",
        _SIGNATURE_ORIGIN_REL,
        "/security/client-proof.bin",
    )
    _add_override(
        content_types,
        "/security/client-proof.bin",
        "application/vnd.openxmlformats-package.digital-signature-origin",
    )
    return rewrite_zip_members(
        payload,
        replacements={
            "_rels/.rels": _serialize(relationships),
            "[Content_Types].xml": _serialize(content_types),
        },
        additions=[("security/client-proof.bin", b"")],
    )


def _with_relocated_ole(payload: bytes) -> bytes:
    relationships = _xml_part(payload, "word/_rels/document.xml.rels")
    content_types = _xml_part(payload, "[Content_Types].xml")
    _add_relationship(
        relationships,
        "rIdRelocatedOle",
        _OLE_REL,
        "../client-data/client-object.dat",
    )
    # ISO permits any content type for an embedded object. The relationship
    # type, not a familiar filename or MIME type, must be authoritative.
    _add_override(
        content_types,
        "/client-data/client-object.dat",
        "application/octet-stream",
    )
    return rewrite_zip_members(
        payload,
        replacements={
            "word/_rels/document.xml.rels": _serialize(relationships),
            "[Content_Types].xml": _serialize(content_types),
        },
        additions=[("client-data/client-object.dat", b"OPAQUE-OLE")],
    )


def _with_relocated_revision_header(
    payload: bytes,
    *,
    new_part: str = "client-data/review-header.xml",
    content_type: str | None = None,
) -> bytes:
    relationships = _xml_part(payload, "word/_rels/document.xml.rels")
    header_relationships = [
        child
        for child in relationships
        if child.get("Type", "").endswith("/header")
    ]
    assert header_relationships
    relationship = header_relationships[0]
    old_target = relationship.get("Target")
    assert old_target and not old_target.startswith("/")
    old_part = f"word/{old_target}"
    relationship.set("Target", f"../{new_part}")

    header = _xml_part(payload, old_part)
    paragraph = header.find(f".//{{{_W_NS}}}p")
    assert paragraph is not None
    insertion = etree.SubElement(paragraph, f"{{{_W_NS}}}ins")
    insertion.set(f"{{{_W_NS}}}id", "9001")
    insertion.set(f"{{{_W_NS}}}author", "Fixture Reviewer")
    run = etree.SubElement(insertion, f"{{{_W_NS}}}r")
    text = etree.SubElement(run, f"{{{_W_NS}}}t")
    text.text = "Pending relocated header revision"

    content_types = _xml_part(payload, "[Content_Types].xml")
    header_overrides = [
        child
        for child in content_types
        if child.get("PartName") == f"/{old_part}"
    ]
    assert len(header_overrides) == 1
    header_overrides[0].set("PartName", f"/{new_part}")
    if content_type is not None:
        header_overrides[0].set("ContentType", content_type)

    return rewrite_zip_members(
        payload,
        replacements={
            "word/_rels/document.xml.rels": _serialize(relationships),
            "[Content_Types].xml": _serialize(content_types),
        },
        omit={old_part},
        additions=[(new_part, _serialize(header))],
    )


def _with_header_revision_marker(
    payload: bytes,
    namespace: str,
    local_name: str,
) -> bytes:
    header = _xml_part(payload, "word/header1.xml")
    paragraph = header.find(f".//{{{_W_NS}}}p")
    assert paragraph is not None
    marker = etree.SubElement(paragraph, f"{{{namespace}}}{local_name}")
    marker.set(f"{{{_W_NS}}}id", "8128")
    marker.set(f"{{{_W_NS}}}author", "Fixture Reviewer")
    return rewrite_zip_members(
        payload,
        replacements={"word/header1.xml": _serialize(header)},
    )


def _with_macro_enabled_content_type(payload: bytes) -> bytes:
    content_types = _xml_part(payload, "[Content_Types].xml")
    document_overrides = [
        child
        for child in content_types
        if child.get("PartName") == "/word/document.xml"
    ]
    assert len(document_overrides) == 1
    document_overrides[0].set(
        "ContentType",
        "application/vnd.ms-word.document.macroEnabled.main+xml",
    )
    return rewrite_zip_members(
        payload,
        replacements={"[Content_Types].xml": _serialize(content_types)},
    )


def test_normal_package_has_no_global_mutation_blocker(tmp_path):
    source = make_numbered_island_master(tmp_path)
    assert detect_global_source_blockers(source) == ()


@pytest.mark.parametrize("value", [None, "1", "true", "on"])
def test_enabled_track_revisions_is_pass_through_only(tmp_path, value):
    source = _with_track_revisions(make_numbered_island_master(tmp_path), value)
    assert "tracked_changes" in detect_global_source_blockers(source)


@pytest.mark.parametrize("value", ["0", "false", "off"])
def test_disabled_track_revisions_does_not_create_a_false_blocker(tmp_path, value):
    source = _with_track_revisions(make_numbered_island_master(tmp_path), value)
    assert "tracked_changes" not in detect_global_source_blockers(source)


@pytest.mark.parametrize(
    "rel_type",
    [_SETTINGS_REL_TRANSITIONAL, _SETTINGS_REL_STRICT],
)
def test_relocated_related_settings_protection_is_detected(tmp_path, rel_type):
    source = _with_relocated_protected_settings(
        make_numbered_island_master(tmp_path),
        rel_type,
    )
    assert "document_protection" in detect_global_source_blockers(source)


def test_noncanonical_signature_part_is_found_from_opc_metadata(tmp_path):
    source = _with_relocated_signature(make_numbered_island_master(tmp_path))
    assert "signed_package" in detect_global_source_blockers(source)


def test_noncanonical_ole_part_is_found_from_relationship_type(tmp_path):
    source = _with_relocated_ole(make_numbered_island_master(tmp_path))
    assert "active_content" in detect_global_source_blockers(source)


def test_revision_in_relocated_related_header_is_detected(tmp_path):
    source = _with_relocated_revision_header(make_numbered_island_master(tmp_path))
    assert "tracked_changes" in detect_global_source_blockers(source)


def test_revision_in_correctly_typed_non_xml_header_is_detected(tmp_path):
    source = _with_relocated_revision_header(
        make_numbered_island_master(tmp_path),
        new_part="client-data/review-header.dat",
    )
    assert "tracked_changes" in detect_global_source_blockers(source)


def test_mistyped_revision_bearing_relationship_fails_closed(tmp_path):
    source = _with_relocated_revision_header(
        make_numbered_island_master(tmp_path),
        content_type="application/xml",
    )
    blockers = detect_global_source_blockers(source)
    assert "unsafe_revision_scan" in blockers
    assert "tracked_changes" in blockers


@pytest.mark.parametrize(
    ("namespace", "local_name"),
    [
        (_W_NS, "customXmlPrChange"),
        (_W_NS, "tblPrExChange"),
        (_W14_NS, "conflictIns"),
        (_W14_NS, "conflictDel"),
        (_W14_NS, "customXmlConflictInsRangeStart"),
        (_W14_NS, "customXmlConflictInsRangeEnd"),
        (_W14_NS, "customXmlConflictDelRangeStart"),
        (_W14_NS, "customXmlConflictDelRangeEnd"),
    ],
)
def test_complete_revision_vocabulary_is_pass_through_only(
    tmp_path,
    namespace,
    local_name,
):
    source = _with_header_revision_marker(
        make_numbered_island_master(tmp_path),
        namespace,
        local_name,
    )
    assert "tracked_changes" in detect_global_source_blockers(source)


def test_macro_enabled_main_content_type_is_active_content(tmp_path):
    source = _with_macro_enabled_content_type(make_numbered_island_master(tmp_path))
    assert "active_content" in detect_global_source_blockers(source)


def _import_master_api(client: TestClient, source: bytes):
    response = client.post(
        "/api/import/master",
        files={"file": ("client-master.docx", source, DOCX_MEDIA_TYPE)},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_api_reports_ordinary_import_as_bounded_body_ready(tmp_path):
    client = TestClient(create_app())
    imported = _import_master_api(client, make_numbered_island_master(tmp_path))

    assert imported["preservation_ready"] is True
    assert imported["source_preservation"] == {
        "status": "ready",
        "source_export_ready": True,
        "exact_original_available": True,
        "body_editing": "bounded",
        "no_op": True,
        "changed_uids": [],
        "blockers": [],
    }


@pytest.mark.parametrize(
    ("mutator", "expected_blocker"),
    [
        (lambda payload: _with_track_revisions(payload, None), "tracked_changes"),
        (
            lambda payload: _with_relocated_protected_settings(
                payload, _SETTINGS_REL_TRANSITIONAL
            ),
            "document_protection",
        ),
        (_with_relocated_signature, "signed_package"),
        (_with_relocated_ole, "active_content"),
        (_with_relocated_revision_header, "tracked_changes"),
    ],
    ids=[
        "track-revisions",
        "protection",
        "signature",
        "active-content",
        "revision-markup",
    ],
)
def test_api_labels_global_blocker_imports_pass_through_only(
    tmp_path,
    mutator: Callable[[bytes], bytes],
    expected_blocker: str,
):
    client = TestClient(create_app())
    source = mutator(make_numbered_island_master(tmp_path))
    imported = _import_master_api(client, source)

    # Compatibility: source mode is ready because the current document is an
    # exact no-op. The detailed contract makes clear that mutation is not.
    assert imported["preservation_ready"] is True
    state = imported["source_preservation"]
    assert state["status"] == "pass_through_only"
    assert state["source_export_ready"] is True
    assert state["exact_original_available"] is True
    assert state["body_editing"] == "disabled"
    assert state["no_op"] is True
    assert expected_blocker in {
        blocker["blocker"] for blocker in state["blockers"]
    }

    preserved = client.get("/api/export/docx", params={"mode": "source"})
    assert preserved.status_code == 200
    assert preserved.content == source
    assert client.get("/api/import/original").content == source

    rejected = client.post(
        "/api/doc/edit",
        json={"ops": [{"action": "delete", "target_id": "pt1.a1.p2"}]},
    )
    assert rejected.status_code == 400
    assert f"[{expected_blocker}]" in rejected.json()["error"]
    assert (
        client.get("/api/export/docx", params={"mode": "source"}).content
        == source
    )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: _with_track_revisions(payload, None),
        lambda payload: _with_relocated_protected_settings(
            payload, _SETTINGS_REL_TRANSITIONAL
        ),
        _with_relocated_signature,
        _with_relocated_ole,
        _with_relocated_revision_header,
        lambda payload: _with_relocated_revision_header(
            payload,
            new_part="client-data/review-header.dat",
        ),
        lambda payload: _with_header_revision_marker(
            payload,
            _W_NS,
            "tblPrExChange",
        ),
        lambda payload: _with_header_revision_marker(
            payload,
            _W14_NS,
            "conflictIns",
        ),
    ],
    ids=[
        "track-revisions",
        "relocated-protection",
        "signature",
        "ole",
        "relocated-revision",
        "non-xml-relocated-revision",
        "table-property-exception-revision",
        "office-2010-conflict-revision",
    ],
)
def test_global_blockers_still_allow_exact_noop_source_export(
    tmp_path,
    mutator: Callable[[bytes], bytes],
):
    source = mutator(make_numbered_island_master(tmp_path))
    path = tmp_path / "blocked-source.docx"
    path.write_bytes(source)
    imported = parse_master_docx(path)
    assert imported.source_map is not None

    output = build_source_preserving_docx(
        source_bytes=source,
        source_map=imported.source_map,
        baseline=imported.section,
        current=imported.section,
    )
    assert output == source


@pytest.mark.parametrize(
    ("mutator", "expected_blocker"),
    [
        (lambda payload: _with_track_revisions(payload, None), "tracked_changes"),
        (
            lambda payload: _with_relocated_protected_settings(
                payload, _SETTINGS_REL_TRANSITIONAL
            ),
            "document_protection",
        ),
        (_with_relocated_signature, "signed_package"),
        (_with_relocated_ole, "active_content"),
        (_with_relocated_revision_header, "tracked_changes"),
        (
            lambda payload: _with_relocated_revision_header(
                payload,
                new_part="client-data/review-header.dat",
            ),
            "tracked_changes",
        ),
        (
            lambda payload: _with_header_revision_marker(
                payload,
                _W_NS,
                "customXmlPrChange",
            ),
            "tracked_changes",
        ),
        (
            lambda payload: _with_header_revision_marker(
                payload,
                _W14_NS,
                "conflictDel",
            ),
            "tracked_changes",
        ),
    ],
    ids=[
        "track-revisions",
        "relocated-protection",
        "signature",
        "ole",
        "relocated-revision",
        "non-xml-relocated-revision",
        "custom-xml-property-revision",
        "office-2010-conflict-revision",
    ],
)
def test_global_blockers_reject_real_source_mutation(
    tmp_path,
    mutator: Callable[[bytes], bytes],
    expected_blocker: str,
):
    source = mutator(make_numbered_island_master(tmp_path))
    path = tmp_path / "blocked-mutation.docx"
    path.write_bytes(source)
    imported = parse_master_docx(path)
    assert imported.source_map is not None
    current, _ = apply_edits(
        imported.section,
        [{"action": "delete", "target_id": "pt1.a1.p2"}],
    )

    with pytest.raises(SourcePatchError) as error:
        build_source_preserving_docx(
            source_bytes=source,
            source_map=imported.source_map,
            baseline=imported.section,
            current=current,
        )
    assert error.value.blocker == expected_blocker
