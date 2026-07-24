"""Deterministic OPC and revision adversarial cases for source fidelity.

These cases exercise the metadata discovery boundary rather than raw ZIP
preservation.  Ambiguous but readable packages must become pass-through-only;
their exact original bytes remain available for a no-op source export.
"""
from __future__ import annotations

import io
import zipfile
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from lxml import etree

from backend.app import create_app
from backend.spec_doc.importer import parse_master_docx
from backend.spec_doc.model import apply_edits
from backend.spec_doc.source_mapping import detect_global_source_blockers
from backend.spec_doc.source_package import SourcePackageError, inspect_docx_package
from backend.spec_doc.source_patch import SourcePatchError, build_source_preserving_docx
from tests.docx_fidelity_helpers import (
    DOCX_MEDIA_TYPE,
    make_numbered_island_master,
    rewrite_zip_members,
)


_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_STRICT_W_NS = "http://purl.oclc.org/ooxml/wordprocessingml/main"
_W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_INVALID_PURL_REL_NS = "http://purl.oclc.org/ooxml/package/relationships"
_INVALID_PURL_CT_NS = "http://purl.oclc.org/ooxml/package/content-types"
_OFFICE_DOCUMENT_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    "officeDocument"
)
_STRICT_OFFICE_DOCUMENT_REL = (
    "http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument"
)
_HEADER_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header"
)
_HEADER_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"
)
_MACRO_DOCUMENT_CONTENT_TYPE = (
    "application/vnd.ms-word.document.macroenabled.main+xml"
)
_FOREIGN_NS = "urn:build-a-spec:foreign-opc-impostor"


def _serialize(root) -> bytes:
    return etree.tostring(
        root,
        encoding="UTF-8",
        xml_declaration=True,
        standalone=True,
    )


def _xml_part(payload: bytes, name: str):
    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        return etree.fromstring(archive.read(name))


def _local_name(tag: str) -> str:
    return etree.QName(tag).localname


def _namespace(tag: str) -> str | None:
    return etree.QName(tag).namespace


def _retag_namespace(root, old_namespace: str, new_namespace: str) -> None:
    for element in root.iter():
        if isinstance(element.tag, str) and _namespace(element.tag) == old_namespace:
            element.tag = f"{{{new_namespace}}}{_local_name(element.tag)}"


def _relationship_xml(rows: list[tuple[str, str, str, str | None]]) -> bytes:
    root = etree.Element(f"{{{_REL_NS}}}Relationships", nsmap={None: _REL_NS})
    for rel_id, rel_type, target, target_mode in rows:
        relationship = etree.SubElement(root, f"{{{_REL_NS}}}Relationship")
        relationship.set("Id", rel_id)
        relationship.set("Type", rel_type)
        relationship.set("Target", target)
        if target_mode is not None:
            relationship.set("TargetMode", target_mode)
    return _serialize(root)


def _with_orphan_relationship_part(
    payload: bytes,
    rows: list[tuple[str, str, str, str | None]],
    *,
    targets: tuple[str, ...] = (),
) -> bytes:
    additions = [
        ("client-data/container.bin", b"relationship source"),
        (
            "client-data/_rels/container.bin.rels",
            _relationship_xml(rows),
        ),
    ]
    additions.extend(
        (f"client-data/{name}", b"relationship target") for name in targets
    )
    return rewrite_zip_members(payload, additions=additions)


def _with_duplicate_relationship_ids(
    payload: bytes,
    second_id: str = "rIdDuplicate",
) -> bytes:
    return _with_orphan_relationship_part(
        payload,
        [
            ("rIdDuplicate", "urn:example:first", "first.bin", None),
            (second_id, "urn:example:second", "second.bin", None),
        ],
        targets=("first.bin", "second.bin"),
    )


def _with_malformed_percent_target(payload: bytes, target: str = "bad%GZ.bin") -> bytes:
    return _with_orphan_relationship_part(
        payload,
        [("rIdMalformedPercent", "urn:example:data", target, None)],
    )


def _with_external_revision_relationship(payload: bytes) -> bytes:
    return _with_orphan_relationship_part(
        payload,
        [
            (
                "rIdExternalHeader",
                _HEADER_REL,
                "https://example.invalid/review-header.xml",
                "External",
            )
        ],
    )


def _with_malformed_external_target(payload: bytes, target: str) -> bytes:
    return _with_orphan_relationship_part(
        payload,
        [("rIdExternalData", "urn:example:data", target, "External")],
    )


def _with_duplicate_content_type_override(payload: bytes) -> bytes:
    content_types = _xml_part(payload, "[Content_Types].xml")
    document_overrides = [
        child
        for child in content_types
        if isinstance(child.tag, str)
        and _namespace(child.tag) == _CT_NS
        and _local_name(child.tag) == "Override"
        and child.get("PartName") == "/word/document.xml"
    ]
    assert len(document_overrides) == 1
    duplicate = etree.fromstring(_serialize(document_overrides[0]))
    content_types.append(duplicate)
    return rewrite_zip_members(
        payload,
        replacements={"[Content_Types].xml": _serialize(content_types)},
    )


def _with_duplicate_content_type_default(payload: bytes) -> bytes:
    content_types = _xml_part(payload, "[Content_Types].xml")
    defaults = [
        child
        for child in content_types
        if isinstance(child.tag, str)
        and _namespace(child.tag) == _CT_NS
        and _local_name(child.tag) == "Default"
    ]
    assert defaults
    duplicate = etree.fromstring(_serialize(defaults[0]))
    # Extension matching is ASCII case-insensitive in the discovery index.
    duplicate.set("Extension", duplicate.get("Extension", "").swapcase())
    content_types.append(duplicate)
    return rewrite_zip_members(
        payload,
        replacements={"[Content_Types].xml": _serialize(content_types)},
    )


def _append_revision_marker(root, namespace: str, local_name: str) -> None:
    paragraphs = [
        element
        for element in root.iter()
        if isinstance(element.tag, str) and _local_name(element.tag) == "p"
    ]
    assert paragraphs
    marker = etree.SubElement(paragraphs[0], f"{{{namespace}}}{local_name}")
    marker.set(f"{{{_W_NS}}}id", "8200")
    marker.set(f"{{{_W_NS}}}author", "Adversarial Fixture")


def _with_header_marker(payload: bytes, namespace: str, local_name: str) -> bytes:
    header = _xml_part(payload, "word/header1.xml")
    _append_revision_marker(header, namespace, local_name)
    return rewrite_zip_members(
        payload,
        replacements={"word/header1.xml": _serialize(header)},
    )


def _with_relocated_header_revision(
    payload: bytes,
    *,
    content_type: str = _HEADER_CONTENT_TYPE,
) -> bytes:
    relationships = _xml_part(payload, "word/_rels/document.xml.rels")
    content_types = _xml_part(payload, "[Content_Types].xml")
    header_relationships = [
        child
        for child in relationships
        if child.get("Type", "").casefold().endswith("/header")
    ]
    assert header_relationships
    relationship = header_relationships[0]
    old_target = relationship.get("Target")
    assert old_target
    old_part = f"word/{old_target}"
    new_part = "client-data/revision-header.payload"
    relationship.set("Target", f"../{new_part}")

    overrides = [
        child
        for child in content_types
        if child.get("PartName") == f"/{old_part}"
    ]
    assert len(overrides) == 1
    overrides[0].set("PartName", f"/{new_part}")
    overrides[0].set("ContentType", content_type)

    header = _xml_part(payload, old_part)
    _append_revision_marker(header, _W_NS, "ins")
    return rewrite_zip_members(
        payload,
        replacements={
            "word/_rels/document.xml.rels": _serialize(relationships),
            "[Content_Types].xml": _serialize(content_types),
        },
        omit={old_part},
        additions=[(new_part, _serialize(header))],
    )


def _with_mixed_case_macro_content_type(payload: bytes) -> bytes:
    content_types = _xml_part(payload, "[Content_Types].xml")
    document_overrides = [
        child
        for child in content_types
        if child.get("PartName") == "/word/document.xml"
    ]
    assert len(document_overrides) == 1
    mixed_case = "".join(
        character.upper() if index % 2 else character.lower()
        for index, character in enumerate(_MACRO_DOCUMENT_CONTENT_TYPE)
    )
    document_overrides[0].set("ContentType", mixed_case)
    return rewrite_zip_members(
        payload,
        replacements={"[Content_Types].xml": _serialize(content_types)},
    )


def _with_strict_required_namespaces(payload: bytes) -> bytes:
    content_types = _xml_part(payload, "[Content_Types].xml")
    relationships = _xml_part(payload, "_rels/.rels")
    document = _xml_part(payload, "word/document.xml")
    _retag_namespace(document, _W_NS, _STRICT_W_NS)
    for relationship in relationships:
        if relationship.get("Type") == _OFFICE_DOCUMENT_REL:
            relationship.set("Type", _STRICT_OFFICE_DOCUMENT_REL)
    return rewrite_zip_members(
        payload,
        replacements={
            "[Content_Types].xml": _serialize(content_types),
            "_rels/.rels": _serialize(relationships),
            "word/document.xml": _serialize(document),
        },
    )


def _with_foreign_required_root(payload: bytes, part_name: str) -> bytes:
    root = _xml_part(payload, part_name)
    root.tag = f"{{{_FOREIGN_NS}}}{_local_name(root.tag)}"
    return rewrite_zip_members(
        payload,
        replacements={part_name: _serialize(root)},
    )


def _with_foreign_required_child(payload: bytes, part_name: str) -> bytes:
    root = _xml_part(payload, part_name)
    if part_name == "[Content_Types].xml":
        matches = [
            child
            for child in root
            if child.get("PartName") == "/word/document.xml"
        ]
    else:
        matches = [
            child
            for child in root
            if child.get("Type") in {
                _OFFICE_DOCUMENT_REL,
                _STRICT_OFFICE_DOCUMENT_REL,
            }
        ]
    assert len(matches) == 1
    matches[0].tag = f"{{{_FOREIGN_NS}}}{_local_name(matches[0].tag)}"
    return rewrite_zip_members(
        payload,
        replacements={part_name: _serialize(root)},
    )


def _with_strict_header_revision(payload: bytes) -> bytes:
    header = _xml_part(payload, "word/header1.xml")
    _retag_namespace(header, _W_NS, _STRICT_W_NS)
    _append_revision_marker(header, _STRICT_W_NS, "ins")
    return rewrite_zip_members(
        payload,
        replacements={"word/header1.xml": _serialize(header)},
    )


def _with_settings_marker(
    payload: bytes,
    namespace: str,
    local_name: str,
) -> bytes:
    settings = _xml_part(payload, "word/settings.xml")
    marker = etree.Element(f"{{{namespace}}}{local_name}")
    settings.insert(0, marker)
    return rewrite_zip_members(
        payload,
        replacements={"word/settings.xml": _serialize(settings)},
    )


def _with_strict_track_revisions(
    payload: bytes,
    *,
    mismatched_val_namespace: bool = False,
) -> bytes:
    settings = _xml_part(payload, "word/settings.xml")
    _retag_namespace(settings, _W_NS, _STRICT_W_NS)
    marker = etree.Element(f"{{{_STRICT_W_NS}}}trackRevisions")
    if mismatched_val_namespace:
        marker.set(f"{{{_W_NS}}}val", "false")
    settings.insert(0, marker)
    return rewrite_zip_members(
        payload,
        replacements={"word/settings.xml": _serialize(settings)},
    )


def _assert_pass_through_only_noop(
    tmp_path,
    source: bytes,
    expected_blocker: str,
) -> None:
    # Ambiguity is a mutation blocker, not a reason to discard readable source
    # bytes. The original remains recoverable and byte-identical.
    inspect_docx_package(source)
    path = tmp_path / "ambiguous-source.docx"
    path.write_bytes(source)
    imported = parse_master_docx(path)
    assert imported.source_map is not None
    assert expected_blocker in imported.source_map.global_blockers

    preserved = build_source_preserving_docx(
        source_bytes=source,
        source_map=imported.source_map,
        baseline=imported.section,
        current=imported.section,
    )
    assert preserved == source

    current, _changed = apply_edits(
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


@pytest.mark.parametrize(
    "target",
    [
        "bad%.bin",
        "bad%0.bin",
        "bad%GZ.bin",
        "bad%0A.bin",
        "C%3A/escape.xml",
        "%2e%2e/escape.xml",
        "safe/.%2E/escape.xml",
        "bad%2Fchild.bin",
        "bad%5Cchild.bin",
    ],
)
def test_unsafe_percent_encoding_fails_relationship_discovery(tmp_path, target):
    source = _with_malformed_percent_target(
        make_numbered_island_master(tmp_path),
        target,
    )
    _assert_pass_through_only_noop(tmp_path, source, "unsafe_relationship_scan")


@pytest.mark.parametrize(
    "target",
    [
        "https://example.invalid/bad%GZ",
        "https://example.invalid/bad%0Avalue",
        "https://example.invalid/bad\\path",
    ],
)
def test_malformed_external_target_fails_relationship_discovery(
    tmp_path,
    target,
):
    source = _with_malformed_external_target(
        make_numbered_island_master(tmp_path),
        target,
    )
    _assert_pass_through_only_noop(tmp_path, source, "unsafe_relationship_scan")


@pytest.mark.parametrize(
    ("mutator", "expected_blocker"),
    [
        (_with_duplicate_relationship_ids, "unsafe_relationship_scan"),
        (
            lambda payload: _with_duplicate_relationship_ids(
                payload,
                " rIdDuplicate ",
            ),
            "unsafe_relationship_scan",
        ),
        (_with_duplicate_content_type_override, "unsafe_relationship_scan"),
        (_with_duplicate_content_type_default, "unsafe_relationship_scan"),
        (_with_external_revision_relationship, "unsafe_revision_scan"),
    ],
    ids=[
        "duplicate-relationship-id",
        "whitespace-equivalent-relationship-id",
        "duplicate-content-type-override",
        "duplicate-content-type-default",
        "external-revision-relationship",
    ],
)
def test_opc_ambiguity_is_pass_through_only_with_exact_noop(
    tmp_path,
    mutator: Callable[[bytes], bytes],
    expected_blocker: str,
):
    source = mutator(make_numbered_island_master(tmp_path))
    _assert_pass_through_only_noop(tmp_path, source, expected_blocker)


def test_mixed_case_word_mime_type_still_exposes_revision_part(tmp_path):
    mixed_case_header_type = "".join(
        character.upper() if index % 2 else character.lower()
        for index, character in enumerate(_HEADER_CONTENT_TYPE)
    )
    source = _with_relocated_header_revision(
        make_numbered_island_master(tmp_path),
        content_type=mixed_case_header_type,
    )
    blockers = detect_global_source_blockers(source)
    assert "tracked_changes" in blockers
    assert "unsafe_relationship_scan" not in blockers
    assert "unsafe_revision_scan" not in blockers


def test_mixed_case_macro_mime_type_remains_active_content(tmp_path):
    source = _with_mixed_case_macro_content_type(
        make_numbered_island_master(tmp_path)
    )
    assert "active_content" in detect_global_source_blockers(source)


def test_revision_scan_uses_opc_type_for_non_xml_word_part_name(tmp_path):
    source = _with_relocated_header_revision(make_numbered_island_master(tmp_path))
    assert "tracked_changes" in detect_global_source_blockers(source)


def test_required_opc_validation_accepts_strict_namespaces(tmp_path):
    source = _with_strict_required_namespaces(
        make_numbered_island_master(tmp_path)
    )
    info = inspect_docx_package(source)
    assert info.member_count > 0


def test_fully_strict_main_is_rejected_atomically_without_source_state(tmp_path):
    source = _with_strict_required_namespaces(
        make_numbered_island_master(tmp_path)
    )
    client = TestClient(create_app())

    rejected = client.post(
        "/api/import/master",
        files={"file": ("strict-main.docx", source, DOCX_MEDIA_TYPE)},
    )

    assert rejected.status_code == 400
    assert client.get("/api/doc").json()["source_available"] is False
    assert client.get("/api/import/original").status_code == 404


@pytest.mark.parametrize(
    ("part_name", "invalid_namespace"),
    [
        ("[Content_Types].xml", _INVALID_PURL_CT_NS),
        ("_rels/.rels", _INVALID_PURL_REL_NS),
    ],
)
def test_strict_ooxml_does_not_change_fixed_opc_package_namespaces(
    tmp_path,
    part_name,
    invalid_namespace,
):
    source = make_numbered_island_master(tmp_path)
    root = _xml_part(source, part_name)
    _retag_namespace(root, _namespace(root.tag), invalid_namespace)
    source = rewrite_zip_members(
        source,
        replacements={part_name: _serialize(root)},
    )
    with pytest.raises(SourcePackageError):
        inspect_docx_package(source)


@pytest.mark.parametrize(
    "part_name",
    ["[Content_Types].xml", "_rels/.rels", "word/document.xml"],
)
def test_required_opc_validation_rejects_foreign_namespace_impostors(
    tmp_path,
    part_name,
):
    source = _with_foreign_required_root(
        make_numbered_island_master(tmp_path),
        part_name,
    )
    with pytest.raises(SourcePackageError):
        inspect_docx_package(source)


@pytest.mark.parametrize("part_name", ["[Content_Types].xml", "_rels/.rels"])
def test_foreign_namespace_child_cannot_supply_required_opc_wiring(
    tmp_path,
    part_name,
):
    source = _with_foreign_required_child(
        make_numbered_island_master(tmp_path),
        part_name,
    )
    with pytest.raises(SourcePackageError):
        inspect_docx_package(source)


def test_strict_wordprocessing_namespace_revision_is_detected(tmp_path):
    source = _with_strict_header_revision(make_numbered_island_master(tmp_path))
    assert "tracked_changes" in detect_global_source_blockers(source)


def test_strict_settings_namespace_track_revisions_is_detected(tmp_path):
    source = _with_strict_track_revisions(make_numbered_island_master(tmp_path))
    assert "tracked_changes" in detect_global_source_blockers(source)


def test_strict_settings_rejects_transitional_val_attribute(tmp_path):
    source = _with_strict_track_revisions(
        make_numbered_island_master(tmp_path),
        mismatched_val_namespace=True,
    )
    blockers = detect_global_source_blockers(source)
    assert "unsafe_settings_xml" in blockers
    assert "tracked_changes" not in blockers


@pytest.mark.parametrize("local_name", ["ins", "del", "conflictIns"])
def test_foreign_revision_like_local_names_do_not_false_positive(
    tmp_path,
    local_name,
):
    source = _with_header_marker(
        make_numbered_island_master(tmp_path),
        _FOREIGN_NS,
        local_name,
    )
    assert "tracked_changes" not in detect_global_source_blockers(source)


def test_foreign_track_revisions_element_does_not_false_positive(tmp_path):
    source = _with_settings_marker(
        make_numbered_island_master(tmp_path),
        _FOREIGN_NS,
        "trackRevisions",
    )
    blockers = detect_global_source_blockers(source)
    assert "tracked_changes" not in blockers
    assert "unsafe_settings_xml" not in blockers


@pytest.mark.parametrize(
    "local_name",
    [
        "conflictIns",
        "conflictDel",
        "customXmlConflictInsRangeStart",
        "customXmlConflictDelRangeEnd",
    ],
)
def test_office_conflict_revision_vocabulary_is_detected(tmp_path, local_name):
    source = _with_header_marker(
        make_numbered_island_master(tmp_path),
        _W14_NS,
        local_name,
    )
    assert "tracked_changes" in detect_global_source_blockers(source)
