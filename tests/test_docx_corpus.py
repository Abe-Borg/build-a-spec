"""Structural acceptance tests for the provenance-explicit DOCX corpus."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import io
import json
import os
import re
import struct
import zipfile
import zlib

import pytest
from lxml import etree

from backend.spec_doc.importer import parse_master_docx
from backend.spec_doc.model import apply_edits
from backend.spec_doc.raw_zip import (
    audit_raw_zip_replacement,
    parse_raw_zip_archive,
)
from backend.spec_doc.source_patch import (
    build_source_patch_context,
    build_source_preserving_docx,
    source_edit_capabilities,
    source_patch_readiness,
)
import tests.docx_corpus as docx_corpus_module
from tests.docx_corpus import (
    build_case,
    corpus_cases,
    load_manifest,
    materialize_corpus,
)
from tests.docx_fidelity_helpers import (
    assert_untouched_parts_identical,
    assert_valid_docx_package,
)


_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_EP_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
)
_PRIVATE_EXTRA = struct.pack("<HH4s", 0xCAFE, 4, b"BASP")
_ARCHIVE_COMMENT = b"Build-a-Spec sanitized synthetic corpus ZIP comment"
_RELOCATED_NUMBERING = "word/corpus/numbering-relocated.xml"
_LOCAL_PATH_RE = re.compile(
    r"(?i)(?:[a-z]:[\\/](?:users|documents and settings)[\\/]|/(?:users|home)/)"
)
_FORBIDDEN_TEXT = (
    "AbrahamBorg",
    "C:\\Github-Repos",
    "C:/Github-Repos",
    "client name",
    "real consultant",
)
_REQUIRED_CASE_CATEGORIES = {
    "word_like_rich": {
        "microsoft-word-like",
        "multipage",
        "default-first-even-headers",
        "page-field",
        "header-image",
        "table-heavy",
        "custom-styles",
    },
    "libreoffice_like_metadata": {"libreoffice-like", "producer-metadata"},
    "older_conversion_like": {
        "older-doc-conversion-like",
        "compatibility-mode",
    },
    "consultant_template": {"consultant-template-like", "custom-properties"},
    "mixed_section_layout": {"multiple-sections", "mixed-orientation"},
    "custom_numbering": {"custom-numbering", "numbered-island"},
    "relocated_numbering_opc": {
        "relocated-opc-part",
        "relationship-resolution",
    },
    "unusual_utf8_declaration": {
        "unusual-xml-declaration",
        "manual-ooxml",
    },
    "utf16_pass_through": {"utf16", "pass-through-only"},
    "large_media": {"large-media", "bounded-streaming"},
    "zip_comment_and_extra": {"zip-comment", "zip-extra-field"},
    "manual_comments_ooxml": {"comments", "content-types"},
    "manual_notes_ooxml": {
        "footnotes",
        "endnotes",
        "content-types",
        "referential-integrity",
    },
    "actual_word_16_rich": {"actual-microsoft-word", "producer-saved"},
    "actual_word_16_consultant_template": {
        "actual-microsoft-word",
        "consultant-template-like",
        "producer-saved",
    },
    "actual_word_16_legacy_doc_conversion": {
        "actual-microsoft-word",
        "actual-legacy-doc-conversion",
        "word-97-2003-binary-roundtrip",
    },
    "actual_libreoffice_26_rich": {
        "actual-libreoffice",
        "odt-docx-conversion",
        "producer-saved",
    },
    "tracked_move_source": {"tracked-move-source", "bookmarks"},
}
#: Every identity a corpus package may name, anywhere a producer records a
#: person: a change's or comment's author, its initials, people.xml, and the
#: core properties. A positive list, so a real name fails however it is
#: spelled — no denylist can know every one.
_PLACEHOLDER_IDENTITIES = {
    "Build-a-Spec Synthetic Corpus",
    "Build-a-Spec Sanitized Corpus",
    "Build-a-Spec Corpus",
    "BASC",
}
_IDENTITY_FIELDS = (
    re.compile(r'\bw(?:15)?:author="([^"]*)"'),
    re.compile(r'\bw:initials="([^"]*)"'),
    re.compile(r'\bw15:userId="([^"]*)"'),
    re.compile(r"<dc:creator>([^<]*)</dc:creator>"),
    re.compile(r"<cp:lastModifiedBy>([^<]*)</cp:lastModifiedBy>"),
)
_EXTERNAL_PRODUCER_FIELDS = {
    "product",
    "version",
    "platform",
    "production_method",
}
_EXTERNAL_SANITIZATION_FIELDS = {
    "tool",
    "procedure",
    "privacy_review",
    "modified_parts",
}


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    workspace = tmp_path_factory.mktemp("docx-corpus")
    return {
        case.case_id: (case, build_case(case, workspace))
        for case in corpus_cases()
    }


def _xml_part(payload: bytes, name: str):
    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        return etree.fromstring(archive.read(name))


def _application(payload: bytes) -> tuple[str, str]:
    root = _xml_part(payload, "docProps/app.xml")
    application = root.find(f"{{{_EP_NS}}}Application")
    version = root.find(f"{{{_EP_NS}}}AppVersion")
    assert application is not None and version is not None
    return application.text or "", version.text or ""


def _package_surfaces(payload: bytes):
    """Yield every ZIP surface that can carry privacy-sensitive bytes."""
    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        yield "archive comment", archive.comment
        for info in archive.infolist():
            yield f"member name {info.filename}", info.filename.encode("utf-8")
            yield f"member comment {info.filename}", info.comment
            yield f"member extra {info.filename}", info.extra
            yield f"member data {info.filename}", archive.read(info)

    # zipfile exposes central-directory metadata, but local-only extras,
    # preambles, inter-member gaps, and bytes after EOCD can differ.  Scan the
    # exact retained raw spans as well so privacy review covers the envelope
    # that the source-preserving path promises to keep byte-for-byte.
    raw = parse_raw_zip_archive(payload)
    yield "raw ZIP preamble", raw.preamble
    yield "raw ZIP EOCD", raw.eocd
    yield "raw ZIP trailing bytes", raw.trailing_bytes
    for entry in raw.entries:
        yield (
            f"raw local header {entry.filename}",
            payload[entry.local_header_span.start : entry.local_header_span.end],
        )
        yield (
            f"raw inter-member gap {entry.filename}",
            payload[entry.gap_after_span.start : entry.gap_after_span.end],
        )
        yield f"raw central record {entry.filename}", entry.central_record


def _decoded_text_views(payload: bytes) -> tuple[str, ...]:
    views = [payload.decode("utf-8", errors="ignore")]
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        views.append(payload.decode("utf-16", errors="ignore"))
    return tuple(views)


def _assert_package_is_sanitized(case_id: str, payload: bytes) -> None:
    for surface, content in _package_surfaces(payload):
        text_views = _decoded_text_views(content)
        for forbidden in _FORBIDDEN_TEXT:
            assert all(
                forbidden.casefold() not in text.casefold() for text in text_views
            ), f"{case_id}: privacy token in {surface}: {forbidden}"
        assert all(
            _LOCAL_PATH_RE.search(text) is None for text in text_views
        ), f"{case_id}: local user path in {surface}"


def test_privacy_scan_includes_raw_zip_preamble_and_trailing_bytes(corpus):
    clean = corpus["word_like_rich"][1]
    forbidden = b"AbrahamBorg"

    for surface, payload in (
        ("raw ZIP preamble", forbidden + clean),
        ("raw ZIP trailing bytes", clean + forbidden),
    ):
        with pytest.raises(AssertionError, match=surface):
            _assert_package_is_sanitized("raw-surface-probe", payload)


def _iter_model_paragraphs(section):
    for part in section.parts:
        for article in part.articles:
            pending = list(article.paragraphs)
            while pending:
                paragraph = pending.pop(0)
                yield paragraph
                pending[0:0] = paragraph.children


def _zip_envelope(payload: bytes):
    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        return archive.comment, {
            info.filename: (
                info.comment,
                info.extra,
                info.date_time,
                info.compress_type,
                info.external_attr,
            )
            for info in archive.infolist()
        }


def _assert_png_chunks_are_valid(payload: bytes) -> None:
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    cursor = 8
    chunk_kinds: list[bytes] = []
    while cursor < len(payload):
        assert cursor + 12 <= len(payload)
        length = struct.unpack_from(">I", payload, cursor)[0]
        kind = payload[cursor + 4 : cursor + 8]
        data_start = cursor + 8
        data_end = data_start + length
        assert data_end + 4 <= len(payload)
        expected_crc = struct.unpack_from(">I", payload, data_end)[0]
        actual_crc = zlib.crc32(kind)
        actual_crc = zlib.crc32(payload[data_start:data_end], actual_crc) & 0xFFFFFFFF
        assert actual_crc == expected_crc
        chunk_kinds.append(kind)
        cursor = data_end + 4
        if kind == b"IEND":
            break
    assert cursor == len(payload)
    assert chunk_kinds[0] == b"IHDR"
    assert b"IDAT" in chunk_kinds
    assert chunk_kinds[-1] == b"IEND"


def test_manifest_has_explicit_provenance_and_complete_local_coverage():
    manifest = load_manifest()
    cases = corpus_cases()
    assert manifest["schema_version"] == 1
    assert manifest["corpus_kind"] == "provenance_explicit"
    assert len(cases) >= 12
    assert len({case.case_id for case in cases}) == len(cases)
    assert len({case.filename.casefold() for case in cases}) == len(cases)
    assert all(case.filename.endswith(".docx") for case in cases)

    by_id = {case.case_id: case for case in cases}
    assert _REQUIRED_CASE_CATEGORIES.keys() <= by_id.keys()
    for case_id, categories in _REQUIRED_CASE_CATEGORIES.items():
        assert categories <= set(by_id[case_id].categories)

    for case in cases:
        assert case.provenance["kind"] in {"synthetic", "external_sanitized"}
        assert case.provenance["sanitized"] is True
        assert case.provenance["statement"]
        if case.provenance["kind"] == "synthetic":
            assert case.provenance["actual_producer"] is None
            assert case.recipe and case.fixture is None
        else:
            producer = case.provenance["actual_producer"]
            sanitization = case.provenance["sanitization"]
            assert _EXTERNAL_PRODUCER_FIELDS <= producer.keys()
            assert _EXTERNAL_SANITIZATION_FIELDS <= sanitization.keys()
            assert all(producer[field].strip() for field in _EXTERNAL_PRODUCER_FIELDS)
            assert all(
                sanitization[field].strip()
                for field in _EXTERNAL_SANITIZATION_FIELDS - {"modified_parts"}
            )
            assert isinstance(sanitization["modified_parts"], list)
            assert case.fixture and case.recipe is None
            assert case.expected_sha256
        assert case.expectations["importable"] is True
        assert case.expectations["exact_noop"] is True


@pytest.mark.parametrize(
    "unsafe_filename",
    [
        "../escape.docx",
        r"..\escape.docx",
        "/tmp/escape.docx",
        r"C:\temp\escape.docx",
        "nested/escape.docx",
        "nested\\escape.docx",
        "CON.docx",
        "LPT1.fixture.docx",
    ],
)
def test_manifest_rejects_non_leaf_output_paths(monkeypatch, unsafe_filename):
    manifest = deepcopy(load_manifest())
    manifest["cases"] = [manifest["cases"][0]]
    manifest["cases"][0]["filename"] = unsafe_filename
    monkeypatch.setattr(docx_corpus_module, "load_manifest", lambda: manifest)
    with pytest.raises(ValueError, match="leaf-only"):
        docx_corpus_module.corpus_cases()


@pytest.mark.parametrize(
    "unsafe_case_id",
    [
        "../escape",
        r"..\escape",
        "/tmp/escape",
        r"C:\temp\escape",
        "nested/id",
        "CON",
        "LPT1.fixture",
        "trailing.",
        "",
    ],
)
def test_manifest_rejects_path_like_case_ids(monkeypatch, unsafe_case_id):
    manifest = deepcopy(load_manifest())
    manifest["cases"] = [manifest["cases"][0]]
    manifest["cases"][0]["id"] = unsafe_case_id
    monkeypatch.setattr(docx_corpus_module, "load_manifest", lambda: manifest)
    with pytest.raises(ValueError, match="portable identifier"):
        docx_corpus_module.corpus_cases()


def test_build_case_rejects_unvalidated_path_like_id_before_writing(tmp_path):
    case = next(item for item in corpus_cases() if item.recipe is not None)
    unsafe = replace(case, case_id=r"..\escape")

    with pytest.raises(ValueError, match="portable identifier"):
        build_case(unsafe, tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_manifest_rejects_case_insensitive_output_collisions(monkeypatch):
    manifest = deepcopy(load_manifest())
    manifest["cases"] = manifest["cases"][:2]
    manifest["cases"][1]["filename"] = manifest["cases"][0][
        "filename"
    ].upper()
    monkeypatch.setattr(docx_corpus_module, "load_manifest", lambda: manifest)
    with pytest.raises(ValueError, match="filenames must be unique"):
        docx_corpus_module.corpus_cases()


def test_external_fixture_schema_requires_structured_provenance(monkeypatch):
    manifest = deepcopy(load_manifest())
    external = manifest["cases"][0]
    external["id"] = "external_schema_probe"
    external.pop("recipe")
    external["fixture"] = "external-schema-probe.docx"
    external["sha256"] = "a" * 64
    external["provenance"] = {
        "kind": "external_sanitized",
        "actual_producer": {
            "product": "Fixture Producer",
            "version": "1.2.3",
            "platform": "Fixture OS",
            "production_method": "Native placeholder-only Save As DOCX",
        },
        "sanitization": {
            "tool": "Fixture sanitizer 1.0",
            "procedure": "Placeholder-only construction; metadata reviewed.",
            "modified_parts": [],
            "privacy_review": "All ZIP members and metadata reviewed.",
        },
        "sanitized": True,
        "statement": "Schema-only external fixture probe.",
    }
    manifest["cases"] = [external]
    monkeypatch.setattr(docx_corpus_module, "load_manifest", lambda: manifest)
    assert docx_corpus_module.corpus_cases()[0].case_id == "external_schema_probe"

    external["provenance"]["actual_producer"]["version"] = ""
    with pytest.raises(ValueError, match="structured producer"):
        docx_corpus_module.corpus_cases()
    external["provenance"]["actual_producer"]["version"] = "1.2.3"
    external["provenance"]["sanitization"].pop("procedure")
    with pytest.raises(ValueError, match="structured sanitization"):
        docx_corpus_module.corpus_cases()


def test_every_corpus_recipe_is_byte_deterministic(corpus, tmp_path):
    second_workspace = tmp_path / "second-build"
    for case, first in corpus.values():
        second = build_case(case, second_workspace)
        assert second == first, f"non-deterministic corpus recipe: {case.case_id}"
        if case.provenance["kind"] == "synthetic":
            with zipfile.ZipFile(io.BytesIO(first), "r") as archive:
                assert all(
                    info.date_time == (2026, 1, 1, 0, 0, 0)
                    for info in archive.infolist()
                )


def test_corpus_packages_are_valid_sanitized_and_exact_noop(corpus, tmp_path):
    for case, payload in corpus.values():
        assert_valid_docx_package(payload)
        _assert_package_is_sanitized(case.case_id, payload)

        path = tmp_path / case.filename
        path.write_bytes(payload)
        imported = parse_master_docx(path)
        assert imported.source_map is not None
        context = build_source_patch_context(
            source_bytes=payload,
            source_map=imported.source_map,
            baseline=imported.section,
        )
        readiness = source_patch_readiness(
            source_bytes=payload,
            source_map=imported.source_map,
            baseline=imported.section,
            current=imported.section,
            context=context,
        )
        assert readiness.ready is True
        assert readiness.no_op is True
        blockers = {
            *imported.source_map.global_blockers,
            *(issue.blocker for issue in context.runtime_mutation_issues),
        }
        assert blockers == set(case.expectations["mutation_blockers"])
        actual_mode = "pass_through_only" if blockers else "ready"
        assert actual_mode == case.expectations["source_mode"]
        assert (
            build_source_preserving_docx(
                source_bytes=payload,
                source_map=imported.source_map,
                baseline=imported.section,
                current=imported.section,
                context=context,
            )
            == payload
        )


def test_ready_cases_support_surgical_text_mutation(corpus, tmp_path):
    """Probe the authoritative capability and patch paths, not only no-op."""
    for case, payload in corpus.values():
        if case.expectations["source_mode"] != "ready":
            continue

        path = tmp_path / case.filename
        path.write_bytes(payload)
        imported = parse_master_docx(path)
        assert imported.source_map is not None
        context = build_source_patch_context(
            source_bytes=payload,
            source_map=imported.source_map,
            baseline=imported.section,
        )
        capabilities = source_edit_capabilities(
            context=context,
            source_map=imported.source_map,
            baseline=imported.section,
            current=imported.section,
        )
        candidates = []
        for paragraph in _iter_model_paragraphs(imported.section):
            element = capabilities.elements.get(paragraph.uid)
            operation = element.operations.get("replace_text") if element else None
            if operation is not None and operation.allowed:
                candidates.append(paragraph)
        assert candidates, f"{case.case_id} declares ready but has no replace target"

        target = candidates[0]
        sentinel = f"Corpus mutation probe for {case.case_id}."
        replacement = f"{target.text} {sentinel}"
        current, applied = apply_edits(
            imported.section,
            [
                {
                    "action": "replace",
                    "target_id": target.uid,
                    "text": replacement,
                }
            ],
        )
        assert applied
        readiness = source_patch_readiness(
            source_bytes=payload,
            source_map=imported.source_map,
            baseline=imported.section,
            current=current,
            context=context,
        )
        assert readiness.ready is True, (
            case.case_id,
            [(issue.blocker, issue.message) for issue in readiness.blockers],
        )
        edited = build_source_preserving_docx(
            source_bytes=payload,
            source_map=imported.source_map,
            baseline=imported.section,
            current=current,
            context=context,
        )
        assert edited != payload
        assert sentinel in "".join(
            _xml_part(edited, "word/document.xml").itertext()
        )
        assert_untouched_parts_identical(payload, edited)
        assert _zip_envelope(edited) == _zip_envelope(payload)
        with zipfile.ZipFile(io.BytesIO(edited), "r") as archive:
            edited_document_xml = archive.read("word/document.xml")
        audit_raw_zip_replacement(
            payload,
            edited,
            filename="word/document.xml",
            expected_payload=edited_document_xml,
            source_archive=context.raw_zip_archive,
        )
        assert_valid_docx_package(edited)


def test_producer_profiles_are_metadata_only_and_honestly_distinct(corpus):
    word = corpus["word_like_rich"][1]
    libre = corpus["libreoffice_like_metadata"][1]
    legacy = corpus["older_conversion_like"][1]
    consultant = corpus["consultant_template"][1]
    assert _application(word) == ("Microsoft Office Word", "16.0000")
    assert _application(libre)[0].startswith("LibreOffice/24.2")
    assert _application(legacy) == ("Microsoft Office Word", "12.0000")
    assert _application(consultant)[0] == (
        "Build-a-Spec Synthetic Consultant Template"
    )

    settings = _xml_part(legacy, "word/settings.xml")
    compatibility = settings.xpath(
        ".//w:compatSetting[@w:name='compatibilityMode']",
        namespaces={"w": _W_NS},
    )
    assert len(compatibility) == 1
    assert compatibility[0].get(f"{{{_W_NS}}}val") == "12"


def test_external_producer_claims_match_package_metadata(corpus):
    word_ids = (
        "actual_word_16_rich",
        "actual_word_16_consultant_template",
        "actual_word_16_legacy_doc_conversion",
    )
    for case_id in word_ids:
        case, payload = corpus[case_id]
        assert _application(payload) == ("Microsoft Office Word", "16.0000")
        assert case.provenance["actual_producer"]["product"] == (
            "Microsoft Office Word"
        )
        assert case.provenance["sanitization"]["modified_parts"] == [
            "docProps/core.xml"
        ]

    libre_case, libre = corpus["actual_libreoffice_26_rich"]
    libre_application, libre_app_version = _application(libre)
    assert libre_application.startswith("LibreOffice/26.2.4.2$Windows_X86_64")
    assert libre_app_version == "15.0000"
    assert libre_case.provenance["actual_producer"]["product"] == "LibreOffice"
    assert libre_case.provenance["sanitization"]["modified_parts"] == []

    legacy = corpus["actual_word_16_legacy_doc_conversion"][1]
    legacy_settings = _xml_part(legacy, "word/settings.xml")
    assert legacy_settings.xpath(
        ".//w:compatSetting[@w:name='compatibilityMode'][@w:val='11']",
        namespaces={"w": _W_NS},
    )

    for case_id in (
        "actual_word_16_rich",
        "actual_word_16_consultant_template",
    ):
        payload = corpus[case_id][1]
        with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
            names = set(archive.namelist())
            document = etree.fromstring(archive.read("word/document.xml"))
            relationships = etree.fromstring(
                archive.read("word/_rels/document.xml.rels")
            )
        assert any(
            name.startswith("customXml/") and name.endswith(".xml")
            for name in names
        )
        assert "docProps/custom.xml" in names
        assert any(
            item.get("Type", "").endswith("/customXml")
            for item in relationships.findall(f"{{{_REL_NS}}}Relationship")
        )
        assert document.xpath(".//w:sdt", namespaces={"w": _W_NS})
        assert document.xpath(".//w:tbl", namespaces={"w": _W_NS})


def test_word_like_rich_categories_are_backed_by_ooxml(corpus):
    payload = corpus["word_like_rich"][1]
    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        names = set(archive.namelist())
        document = etree.fromstring(archive.read("word/document.xml"))
        settings = etree.fromstring(archive.read("word/settings.xml"))
        styles = etree.fromstring(archive.read("word/styles.xml"))
        header_parts = sorted(
            name
            for name in names
            if name.startswith("word/header") and name.endswith(".xml")
        )
        footer_parts = sorted(
            name
            for name in names
            if name.startswith("word/footer") and name.endswith(".xml")
        )
        header_roots = [etree.fromstring(archive.read(name)) for name in header_parts]
        footer_roots = [etree.fromstring(archive.read(name)) for name in footer_parts]

    assert "customXml/clientFixture.xml" in names
    assert "docProps/custom.xml" in names
    assert len(header_parts) == 3
    assert len(footer_parts) == 3
    assert document.xpath(".//w:sdt", namespaces={"w": _W_NS})
    assert document.xpath(".//w:tbl", namespaces={"w": _W_NS})
    assert document.xpath(".//w:drawing", namespaces={"w": _W_NS})
    assert document.xpath(".//w:br[@w:type='page']", namespaces={"w": _W_NS})
    assert document.xpath(".//w:sectPr/w:titlePg", namespaces={"w": _W_NS})
    assert settings.xpath("./w:evenAndOddHeaders", namespaces={"w": _W_NS})
    assert styles.xpath(
        ".//w:style[w:name[@w:val='Client Provision']]",
        namespaces={"w": _W_NS},
    )
    header_text = ["".join(root.itertext()) for root in header_roots]
    footer_text = ["".join(root.itertext()) for root in footer_roots]
    assert any("CLIENT MASTER HEADER" in text for text in header_text)
    assert any("CLIENT FIRST-PAGE HEADER" in text for text in header_text)
    assert any("CLIENT EVEN-PAGE HEADER" in text for text in header_text)
    assert any("CLIENT FIRST-PAGE FOOTER" in text for text in footer_text)
    assert any("CLIENT EVEN-PAGE FOOTER" in text for text in footer_text)
    assert any(
        "PAGE" in "".join(root.xpath(".//w:instrText/text()", namespaces={"w": _W_NS}))
        for root in footer_roots
    )


def test_layout_numbering_and_relocated_opc_shapes(corpus):
    mixed = corpus["mixed_section_layout"][1]
    mixed_root = _xml_part(mixed, "word/document.xml")
    section_properties = mixed_root.xpath(".//w:sectPr", namespaces={"w": _W_NS})
    assert len(section_properties) == 2
    orientations = {
        node.get(f"{{{_W_NS}}}orient", "portrait")
        for section in section_properties
        for node in section.findall(f"{{{_W_NS}}}pgSz")
    }
    assert orientations == {"portrait", "landscape"}
    margin_signatures = {
        tuple(sorted(node.attrib.items()))
        for section in section_properties
        for node in section.findall(f"{{{_W_NS}}}pgMar")
    }
    assert len(margin_signatures) == 2

    custom = corpus["custom_numbering"][1]
    custom_root = _xml_part(custom, "word/document.xml")
    custom_num_ids = {
        node.get(f"{{{_W_NS}}}val")
        for node in custom_root.xpath(
            ".//w:pPr/w:numPr/w:numId", namespaces={"w": _W_NS}
        )
    }
    custom_numbering = _xml_part(custom, "word/numbering.xml")
    custom_defined_ids = {
        node.get(f"{{{_W_NS}}}numId")
        for node in custom_numbering.findall(f"{{{_W_NS}}}num")
    }
    assert custom_num_ids
    assert custom_num_ids <= custom_defined_ids

    relocated = corpus["relocated_numbering_opc"][1]
    with zipfile.ZipFile(io.BytesIO(relocated), "r") as archive:
        names = set(archive.namelist())
        rels = etree.fromstring(archive.read("word/_rels/document.xml.rels"))
        content_types = etree.fromstring(archive.read("[Content_Types].xml"))
    assert "word/numbering.xml" not in names
    assert _RELOCATED_NUMBERING in names
    numbering_targets = [
        item.get("Target")
        for item in rels.findall(f"{{{_REL_NS}}}Relationship")
        if item.get("Type", "").endswith("/numbering")
    ]
    assert numbering_targets == ["corpus/numbering-relocated.xml"]
    assert any(
        item.get("PartName") == f"/{_RELOCATED_NUMBERING}"
        for item in content_types.findall(f"{{{_CT_NS}}}Override")
    )
    relocated_root = _xml_part(relocated, _RELOCATED_NUMBERING)
    relocated_defined_ids = {
        node.get(f"{{{_W_NS}}}numId")
        for node in relocated_root.findall(f"{{{_W_NS}}}num")
    }
    relocated_document = _xml_part(relocated, "word/document.xml")
    relocated_used_ids = {
        node.get(f"{{{_W_NS}}}val")
        for node in relocated_document.xpath(
            ".//w:pPr/w:numPr/w:numId", namespaces={"w": _W_NS}
        )
    }
    assert relocated_used_ids
    assert relocated_used_ids <= relocated_defined_ids


def test_manual_footnote_and_endnote_references_are_integral(corpus):
    payload = corpus["manual_notes_ooxml"][1]
    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        names = set(archive.namelist())
        document = etree.fromstring(archive.read("word/document.xml"))
        footnotes = etree.fromstring(archive.read("word/footnotes.xml"))
        endnotes = etree.fromstring(archive.read("word/endnotes.xml"))
        relationships = etree.fromstring(
            archive.read("word/_rels/document.xml.rels")
        )
        content_types = etree.fromstring(archive.read("[Content_Types].xml"))

    assert {"word/footnotes.xml", "word/endnotes.xml"} <= names
    footnote_references = {
        node.get(f"{{{_W_NS}}}id")
        for node in document.xpath(".//w:footnoteReference", namespaces={"w": _W_NS})
    }
    endnote_references = {
        node.get(f"{{{_W_NS}}}id")
        for node in document.xpath(".//w:endnoteReference", namespaces={"w": _W_NS})
    }
    defined_footnotes = {
        node.get(f"{{{_W_NS}}}id")
        for node in footnotes.xpath("./w:footnote[not(@w:type)]", namespaces={"w": _W_NS})
    }
    defined_endnotes = {
        node.get(f"{{{_W_NS}}}id")
        for node in endnotes.xpath("./w:endnote[not(@w:type)]", namespaces={"w": _W_NS})
    }
    assert footnote_references == defined_footnotes == {"1"}
    assert endnote_references == defined_endnotes == {"1"}

    for collection, note_name in (
        (footnotes, "footnote"),
        (endnotes, "endnote"),
    ):
        special = {
            (
                node.get(f"{{{_W_NS}}}id"),
                node.get(f"{{{_W_NS}}}type"),
            )
            for node in collection.xpath(
                f"./w:{note_name}[@w:type]", namespaces={"w": _W_NS}
            )
        }
        assert special == {
            ("-1", "separator"),
            ("0", "continuationSeparator"),
        }

    relationship_targets = {
        item.get("Type", "").rsplit("/", 1)[-1]: item.get("Target")
        for item in relationships.findall(f"{{{_REL_NS}}}Relationship")
        if item.get("Type", "").endswith(("/footnotes", "/endnotes"))
    }
    assert relationship_targets == {
        "footnotes": "footnotes.xml",
        "endnotes": "endnotes.xml",
    }
    overrides = {
        item.get("PartName"): item.get("ContentType")
        for item in content_types.findall(f"{{{_CT_NS}}}Override")
    }
    assert overrides["/word/footnotes.xml"].endswith(".footnotes+xml")
    assert overrides["/word/endnotes.xml"].endswith(".endnotes+xml")
    assert "Sanitized synthetic footnote content." in "".join(
        footnotes.itertext()
    )
    assert "Sanitized synthetic endnote content." in "".join(
        endnotes.itertext()
    )


def test_lexical_media_zip_and_manual_ooxml_shapes(corpus):
    unusual = corpus["unusual_utf8_declaration"][1]
    with zipfile.ZipFile(io.BytesIO(unusual), "r") as archive:
        unusual_xml = archive.read("word/document.xml")
    assert unusual_xml.startswith(
        b"\xef\xbb\xbf<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\r\n"
    )
    assert b"<!-- Build-a-Spec sanitized lexical corpus marker -->" in unusual_xml
    assert b"<?build-a-spec-corpus unusual-utf8?>" in unusual_xml

    utf16 = corpus["utf16_pass_through"][1]
    with zipfile.ZipFile(io.BytesIO(utf16), "r") as archive:
        utf16_xml = archive.read("word/document.xml")
    assert utf16_xml.startswith((b"\xff\xfe", b"\xfe\xff"))
    etree.fromstring(utf16_xml)

    large = corpus["large_media"][1]
    with zipfile.ZipFile(io.BytesIO(large), "r") as archive:
        media = [
            archive.read(name)
            for name in archive.namelist()
            if name.startswith("word/media/")
        ]
    assert max(map(len, media)) > 2 * 1024 * 1024
    png_media = [item for item in media if item.startswith(b"\x89PNG\r\n\x1a\n")]
    assert png_media
    for image in png_media:
        _assert_png_chunks_are_valid(image)

    zip_case = corpus["zip_comment_and_extra"][1]
    with zipfile.ZipFile(io.BytesIO(zip_case), "r") as archive:
        assert archive.comment == _ARCHIVE_COMMENT
        document_info = archive.getinfo("word/document.xml")
        assert _PRIVATE_EXTRA in document_info.extra

    manual = corpus["manual_comments_ooxml"][1]
    with zipfile.ZipFile(io.BytesIO(manual), "r") as archive:
        assert "word/comments.xml" in archive.namelist()
        document = etree.fromstring(archive.read("word/document.xml"))
        comments = etree.fromstring(archive.read("word/comments.xml"))
        rels = etree.fromstring(archive.read("word/_rels/document.xml.rels"))
        content_types = etree.fromstring(archive.read("[Content_Types].xml"))
    starts = {
        node.get(f"{{{_W_NS}}}id")
        for node in document.xpath(".//w:commentRangeStart", namespaces={"w": _W_NS})
    }
    ends = {
        node.get(f"{{{_W_NS}}}id")
        for node in document.xpath(".//w:commentRangeEnd", namespaces={"w": _W_NS})
    }
    references = {
        node.get(f"{{{_W_NS}}}id")
        for node in document.xpath(".//w:commentReference", namespaces={"w": _W_NS})
    }
    defined_comments = {
        node.get(f"{{{_W_NS}}}id")
        for node in comments.xpath("./w:comment", namespaces={"w": _W_NS})
    }
    assert starts == ends == references == defined_comments == {"0"}
    assert any(
        item.get("Type", "").endswith("/comments")
        for item in rels.findall(f"{{{_REL_NS}}}Relationship")
    )
    assert any(
        item.get("PartName") == "/word/comments.xml"
        and item.get("ContentType", "").endswith(".comments+xml")
        for item in content_types.findall(f"{{{_CT_NS}}}Override")
    )


def test_materializer_emits_checksummed_resolved_manifest(tmp_path):
    output = tmp_path / "materialized"
    resolved = materialize_corpus(output)
    on_disk = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk == resolved
    assert len(resolved["cases"]) == len(corpus_cases())
    assert set(resolved["generator_toolchain"]) == {
        "python",
        "python_docx",
        "lxml",
        "zlib",
    }
    assert all(resolved["generator_toolchain"].values())
    for metadata in resolved["cases"]:
        materialized = (output / metadata["filename"]).resolve()
        assert materialized.parent == output.resolve()
        payload = materialized.read_bytes()
        assert metadata["size_bytes"] == len(payload)
        assert metadata["sha256"] == hashlib.sha256(payload).hexdigest()


def _identities(payload: bytes) -> set[str]:
    found: set[str] = set()
    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        for name in archive.namelist():
            if not name.endswith((".xml", ".rels")):
                continue
            text = archive.read(name).decode("utf-8", errors="ignore")
            for field in _IDENTITY_FIELDS:
                found.update(field.findall(text))
    return found


def test_every_recorded_identity_is_a_placeholder(corpus):
    """A producer writes a person wherever a change, a comment or a save is
    attributed. Every corpus package names only placeholders there — which
    is what keeps a Word user name out of a fixture's word/document.xml,
    the part sanitize_external_fixtures.py never touches."""
    for case_id, (_case, payload) in corpus.items():
        assert _identities(payload) <= _PLACEHOLDER_IDENTITIES, case_id
    leaked = _package_with(
        corpus["tracked_move_source"][1],
        {"word/people.xml": _people_xml("Pat Example", "None", "Pat Example")},
    )
    assert _identities(leaked) - _PLACEHOLDER_IDENTITIES == {"Pat Example"}


# ---------------------------------------------------------------------------
# The Word producer's TrackedMove recipe (Redline on your original, Phase 2)
# ---------------------------------------------------------------------------

_PRODUCER = docx_corpus_module._MANIFEST_DIR / "generate_word_fixtures.ps1"
_W15_NS = "http://schemas.microsoft.com/office/word/2012/wordml"


def _producer_text() -> str:
    return _PRODUCER.read_text(encoding="utf-8")


def _package_with(payload: bytes, members: dict[str, bytes]) -> bytes:
    """``payload`` with ``members`` replaced, or added when absent."""
    from tests.docx_fidelity_helpers import rewrite_zip_members

    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        names = set(archive.namelist())
    return rewrite_zip_members(
        payload,
        replacements={name: data for name, data in members.items() if name in names},
        additions=[(name, data) for name, data in members.items() if name not in names],
    )


def _people_xml(author: str, provider: str, user: str) -> bytes:
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w15:people xmlns:w15="{_W15_NS}"><w15:person w15:author="{author}">'
        f'<w15:presenceInfo w15:providerId="{provider}" w15:userId="{user}"/>'
        "</w15:person></w15:people>"
    ).encode("utf-8")


def _word_shaped_tracked_moves(source: bytes, *, author: str) -> bytes:
    """What Word saves after the recipe's two moves, hand-built: each moved
    paragraph a moveFrom/moveTo pair with range markers and a moved mark,
    the bookmark on the moved-to copy, Track Changes still on."""
    stamp = f'w:author="{author}" w:date="2026-09-23T10:00:00Z"'

    def plain(text: str) -> str:
        return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"

    def moved(kind: str, text: str, ids: int, name: str, bookmark: bool = False) -> str:
        opened = (
            f'<w:bookmarkStart w:id="0" w:name="{docx_corpus_module.TRACKED_MOVE_BOOKMARK}"/>'
            if bookmark
            else ""
        )
        closed = '<w:bookmarkEnd w:id="0"/>' if bookmark else ""
        return (
            f'<w:p><w:pPr><w:rPr><w:{kind} w:id="{ids}" {stamp}/></w:rPr></w:pPr>'
            f'<w:{kind}RangeStart w:id="{ids + 1}" {stamp} w:name="{name}"/>'
            f'<w:{kind} w:id="{ids + 2}" {stamp}>{opened}<w:r><w:t>{text}</w:t></w:r>'
            f"{closed}</w:{kind}><w:{kind}RangeEnd w:id=\"{ids + 1}\"/></w:p>"
        )

    (alpha, charlie), (bravo, delta) = docx_corpus_module.TRACKED_MOVES
    with zipfile.ZipFile(io.BytesIO(source), "r") as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
        settings = etree.fromstring(archive.read("word/settings.xml"))
    body = root.find(f"{{{_W_NS}}}body")
    section_properties = body.find(f"{{{_W_NS}}}sectPr")
    moved_body = etree.fromstring(
        f'<w:body xmlns:w="{_W_NS}">'
        + "".join(
            [
                plain("SECTION 21 13 19"),
                plain("PLACEHOLDER TRACKED MOVE SOURCE"),
                plain("PART 1 - GENERAL"),
                plain("1.1 SUMMARY"),
                moved("moveFrom", f"A. {alpha}", 10, "move1"),
                moved("moveFrom", f"B. {bravo}", 20, "move2"),
                plain(f"C. {charlie}"),
                moved("moveTo", f"A. {alpha}", 30, "move1"),
                plain(f"D. {delta}"),
                moved("moveTo", f"B. {bravo}", 40, "move2", bookmark=True),
                plain("1.2 QUALITY ASSURANCE"),
                plain("A. Placeholder provision echo."),
                plain("END OF SECTION 21 13 19"),
            ]
        )
        + "</w:body>"
    )
    moved_body.append(section_properties)
    root.replace(body, moved_body)
    settings.insert(0, etree.Element(f"{{{_W_NS}}}trackRevisions"))

    def serialize(element) -> bytes:
        return etree.tostring(
            element, encoding="UTF-8", xml_declaration=True, standalone=True
        )

    return _package_with(
        source,
        {
            "word/document.xml": serialize(root),
            "word/settings.xml": serialize(settings),
            "word/people.xml": _people_xml(author, "None", author),
        },
    )


def test_the_tracked_move_source_is_what_the_word_recipe_looks_for(corpus, tmp_path):
    """The recipe finds each paragraph it moves, and each it lands after, by
    text — so every one appears exactly once — and the moved bookmark wraps
    the second paragraph it moves. The script names the same text, bookmark
    and placeholder author as the Python side."""
    from tests.word_judge import TRACKED_MOVE_AUTHOR, TRACKED_MOVE_BOOKMARK

    payload = corpus["tracked_move_source"][1]
    document = _xml_part(payload, "word/document.xml")
    paragraphs = document.findall(f".//{{{_W_NS}}}p")
    texts = ["".join(p.itertext()) for p in paragraphs]
    for move, after in docx_corpus_module.TRACKED_MOVES:
        for needle in (move, after):
            assert sum(needle in text for text in texts) == 1, needle
    (bookmarked,) = [
        p
        for p in paragraphs
        if p.find(f"{{{_W_NS}}}bookmarkStart") is not None
    ]
    assert docx_corpus_module.TRACKED_MOVES[1][0] in "".join(bookmarked.itertext())
    assert (
        bookmarked.find(f"{{{_W_NS}}}bookmarkStart").get(f"{{{_W_NS}}}name")
        == docx_corpus_module.TRACKED_MOVE_BOOKMARK
        == TRACKED_MOVE_BOOKMARK
    )

    script = _producer_text()
    moves = re.findall(r'@\{ Move = "([^"]+)"; After = "([^"]+)" \}', script)
    assert tuple(moves) == docx_corpus_module.TRACKED_MOVES
    assert f'$trackedMoveBookmark = "{TRACKED_MOVE_BOOKMARK}"' in script
    assert f'$trackedMoveAuthor = "{TRACKED_MOVE_AUTHOR}"' in script
    assert re.search(r'\$trackedMoveInitials = "([^"]+)"', script).group(1) in (
        _PLACEHOLDER_IDENTITIES
    )
    assert '"tracked-move-source.docx"' in script
    assert '"microsoft-word-16-tracked-move.docx"' in script

    # The source imports as an ordinary spec: one article, four provisions.
    path = tmp_path / "tracked-move-source.docx"
    path.write_bytes(payload)
    summary = parse_master_docx(path).section.parts[0].articles[0]
    assert [p.text for p in summary.paragraphs] == [
        "Placeholder provision alpha.",
        "Placeholder provision bravo.",
        "Placeholder provision charlie.",
        "Placeholder provision delta.",
    ]


def test_the_recipe_records_moves_under_a_placeholder_and_restores_the_user(corpus):
    """The recipe never runs here (it needs Word), so its text is pinned:
    the placeholder identity is set before the source is opened and put back
    in a finally block; Track Changes and Track Moves are on; what Word
    recorded is checked BEFORE anything is saved; and the user's own
    identity never reaches a message."""
    script = _producer_text()
    recipe = script[script.index("function Invoke-TrackedMoveRecipe") :]
    recipe = recipe[: recipe.index("\n$sourcePath =")]
    opened = recipe.index("$Word.Documents.Open(")
    assert recipe.index("$Word.UserName = $trackedMoveAuthor") < opened
    assert recipe.index("$Word.UserInitials = $trackedMoveInitials") < opened
    assert recipe.index("$Word.Options.UseLocalUserInfo = $true") < opened
    restore = recipe[recipe.index("    finally {") :]
    for line in (
        "$Word.UserName = $originalUserName",
        "$Word.UserInitials = $originalUserInitials",
        "$Word.Options.UseLocalUserInfo = $originalUseLocal",
    ):
        assert line in restore, line
    for line in (
        "[ref]$readOnly",
        "[ref]$addToRecentFiles",
        "$document.TrackRevisions = $true",
        "$document.TrackMoves = $true",
        "$saveFormat = 16",
        "[ref]$saveAddToRecentFiles",
    ):
        assert line in recipe, line
    assert "$readOnly = $true" in recipe and "$addToRecentFiles = $false" in recipe
    saved = recipe.index("$document.SaveAs2(")
    for check in (
        "$revision.Author -cne $trackedMoveAuthor",
        "14 { $movedFrom++ }",
        "15 { $movedTo++ }",
        "$document.Bookmarks.Exists($trackedMoveBookmark)",
    ):
        assert recipe.index(check) < saved, check
    # After the save: the package scan, and the file deleted on any finding.
    scanned = recipe.index("Get-TrackedMovePackageProblem -Path $OutputPath")
    assert saved < scanned < recipe.index("Remove-Item -LiteralPath $OutputPath")
    # The user's identity is compared and restored, never printed: no line
    # that names it builds a string, writes output or throws.
    for line in script.splitlines():
        if "$originalUserName" in line or "$originalUserInitials" in line:
            assert '"' not in line and "Write-" not in line and "throw" not in line, line
    assert "Get-Process -Name WINWORD" in script  # never another Word


def _run_package_scan(tmp_path, cases: dict[str, tuple[bytes, list[str]]]) -> dict:
    """Run the recipe's own ``Get-TrackedMovePackageProblem`` — lifted out
    of the script by its syntax tree — over each package."""
    import shutil
    import subprocess

    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell 7 (pwsh) is not installed; CI's Linux runner has it")
    listing = []
    for name, (payload, identities) in cases.items():
        path = tmp_path / f"{name}.docx"
        path.write_bytes(payload)
        listing.append({"name": name, "path": str(path), "identities": identities})
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps({"cases": listing}), encoding="utf-8")
    harness = r"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:BUILD_A_SPEC_PS_SCRIPT, [ref]$tokens, [ref]$errors
)
$function = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq "Get-TrackedMovePackageProblem"
}, $true)
$author = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and
        $node.Left.Extent.Text -eq '$trackedMoveAuthor'
}, $false)
. ([scriptblock]::Create($author.Extent.Text + "`n" + $function.Extent.Text))
$listing = Get-Content -Raw -LiteralPath $env:BUILD_A_SPEC_PS_CASES | ConvertFrom-Json
$results = [ordered]@{}
foreach ($case in $listing.cases) {
    $identities = [string[]]@($case.identities)
    $results[$case.name] = Get-TrackedMovePackageProblem -Path $case.path -Identities $identities
}
ConvertTo-Json -Compress -InputObject $results
"""
    completed = subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", harness],
        env={
            **os.environ,
            "BUILD_A_SPEC_PS_SCRIPT": str(_PRODUCER),
            "BUILD_A_SPEC_PS_CASES": str(cases_path),
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_the_recipe_refuses_a_package_that_names_a_real_person(corpus, tmp_path):
    """The recipe's last line of defence, run for real: after Word saves,
    any local identity, user folder path, other author or signed-in
    presence deletes the file. A short user name must not match inside an
    attribute name (w15:userId)."""
    author = "Build-a-Spec Corpus"
    clean = _word_shaped_tracked_moves(corpus["tracked_move_source"][1], author=author)
    other_author = _word_shaped_tracked_moves(
        corpus["tracked_move_source"][1], author="Pat Example"
    )
    signed_in = _package_with(clean, {"word/people.xml": _people_xml(author, "AD", "S::pat@example.invalid")})
    stranger_in_people = _package_with(
        clean, {"word/people.xml": _people_xml("Pat Example", "None", "Pat Example")}
    )
    with zipfile.ZipFile(io.BytesIO(clean), "r") as archive:
        core = archive.read("docProps/core.xml").decode("utf-8")
        rels = archive.read("word/_rels/document.xml.rels").decode("utf-8")
    named_in_core = _package_with(
        clean,
        {
            "docProps/core.xml": re.sub(
                r"<cp:lastModifiedBy>[^<]*</cp:lastModifiedBy>",
                "<cp:lastModifiedBy>Pat Example</cp:lastModifiedBy>",
                core,
            ).encode("utf-8")
        },
    )
    local_path = _package_with(
        clean,
        {
            "word/_rels/document.xml.rels": rels.replace(
                "</Relationships>",
                '<Relationship Id="rId99" Type="http://schemas.openxmlformats.org/'
                'officeDocument/2006/relationships/attachedTemplate" Target="file:///'
                'C:\\Users\\pat\\AppData\\Roaming\\Microsoft\\Templates\\Normal.dotm" '
                'TargetMode="External"/></Relationships>',
            ).encode("utf-8")
        },
    )
    results = _run_package_scan(
        tmp_path,
        {
            "clean": (clean, ["Pat Example", "pexample"]),
            "clean-short-user-name": (clean, ["user"]),
            "other-author": (other_author, []),
            "signed-in": (signed_in, []),
            "stranger-in-people": (stranger_in_people, []),
            "named-in-core": (named_in_core, ["Pat Example"]),
            "local-path": (local_path, []),
        },
    )
    assert results == {
        "clean": "",
        "clean-short-user-name": "",
        "other-author": (
            "a change in word/document.xml is attributed to someone other than "
            "the placeholder author"
        ),
        "signed-in": "word/people.xml carries presence information for a signed-in account",
        "stranger-in-people": (
            "a change in word/people.xml is attributed to someone other than the "
            "placeholder author"
        ),
        "named-in-core": "the local Office or Windows identity appears in docProps/core.xml",
        "local-path": "a local user folder path appears in word/_rels/document.xml.rels",
    }


#: What the Word-saved sample's manifest entry will promise, once the fixture
#: is produced and pinned (docs/DOCX_FIDELITY_CORPUS.md).
_TRACKED_MOVE_EXPECTATIONS = {
    "importable": True,
    "exact_noop": True,
    "source_mode": "pass_through_only",
    "mutation_blockers": ["tracked_changes"],
}


def test_a_word_shaped_tracked_move_sample_meets_its_future_manifest_entry(
    corpus, tmp_path
):
    """Pre-validates the entry the real fixture lands with: a package holding
    Word's own pending moves imports as the Accept-All view, is exact-original
    only (the pending changes are its one blocker), keeps its exact no-op,
    and the redline on your original refuses it by name."""
    from backend.spec_doc.source_render import (
        REDLINE_PENDING_REVISIONS,
        SourceRedlineError,
        render_preserving_redline,
    )

    sample = _word_shaped_tracked_moves(
        corpus["tracked_move_source"][1], author="Build-a-Spec Corpus"
    )
    assert _identities(sample) <= _PLACEHOLDER_IDENTITIES
    path = tmp_path / "sample.docx"
    path.write_bytes(sample)
    imported = parse_master_docx(path)
    summary = imported.section.parts[0].articles[0]
    assert [p.text for p in summary.paragraphs] == [
        "Placeholder provision charlie.",
        "Placeholder provision alpha.",
        "Placeholder provision delta.",
        "Placeholder provision bravo.",
    ]
    context = build_source_patch_context(
        source_bytes=sample, source_map=imported.source_map, baseline=imported.section
    )
    blockers = {
        *imported.source_map.global_blockers,
        *(issue.blocker for issue in context.runtime_mutation_issues),
    }
    assert sorted(blockers) == _TRACKED_MOVE_EXPECTATIONS["mutation_blockers"]
    assert (
        build_source_preserving_docx(
            source_bytes=sample,
            source_map=imported.source_map,
            baseline=imported.section,
            current=imported.section,
            context=context,
        )
        == sample
    )
    edited, _ = apply_edits(
        imported.section,
        [{"action": "replace", "target_id": summary.paragraphs[0].uid, "text": "Changed."}],
    )
    with pytest.raises(SourceRedlineError) as refused:
        render_preserving_redline(
            source_bytes=sample,
            format_map=imported.format_map,
            baseline=imported.section,
            current=edited,
            author="Build-a-Spec",
            date="2026-09-23T12:00:00Z",
        )
    assert refused.value.reason == REDLINE_PENDING_REVISIONS
