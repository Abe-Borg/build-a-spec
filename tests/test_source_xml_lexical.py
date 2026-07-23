"""Lexical-fidelity acceptance tests for source-backed text replacement.

The source map intentionally stores decoded character offsets.  These tests
exercise the independently rebuilt byte index and prove that the exporter
copies every byte outside an authorized ``w:t`` semantic slice verbatim.
Structural edits remain covered by the P1b suite and are intentionally out of
scope here.
"""
from __future__ import annotations

import io
import zipfile

import pytest
from docx import Document
from fastapi.testclient import TestClient
from lxml import etree

from backend.app import create_app
import backend.spec_doc.source_patch as source_patch_module
from backend.spec_doc.xml_lexical import (
    XmlLexicalError,
    XmlPatch,
    apply_xml_patches,
    build_source_xml_index,
    decoded_slice_byte_span,
    detect_xml_encoding,
    encode_word_text,
)
from tests.docx_fidelity_helpers import (
    DOCX_MEDIA_TYPE,
    make_fidelity_master,
    rewrite_zip_members,
)


_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_UTF8_BOM = b"\xef\xbb\xbf"


def _document_xml(payload: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        return archive.read("word/document.xml")


def _with_document_xml(payload: bytes, document_xml: bytes) -> bytes:
    return rewrite_zip_members(
        payload,
        replacements={"word/document.xml": document_xml},
    )


def _client() -> TestClient:
    return TestClient(create_app())


def _import_master(client: TestClient, source: bytes) -> dict:
    response = client.post(
        "/api/import/master",
        files={
            "file": (
                "lexical-fidelity.docx",
                source,
                DOCX_MEDIA_TYPE,
            )
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _source_export(client: TestClient):
    return client.get("/api/export/docx", params={"mode": "source"})


def _replace(client: TestClient, target_id: str, text: str):
    return client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "replace",
                    "target_id": target_id,
                    "text": text,
                    "status": "confirmed",
                }
            ]
        },
    )


def _minimal_index_xml(
    *,
    bom: bytes = b"",
    declaration: bytes,
    newline: bytes,
    prefix: bytes,
) -> bytes:
    return b"".join(
        (
            bom,
            declaration,
            newline,
            b"<",
            prefix,
            b':document data-last=\'z\' xmlns:unused="urn:unused" xmlns:',
            prefix,
            b"='",
            _W_NS.encode("ascii"),
            b"' data-first=\"a\">",
            newline,
            b"<",
            prefix,
            b":body>",
            newline,
            b"<!-- before-first --><?gap keep?>",
            newline,
            b"<",
            prefix,
            b":p data-z='9' data-a=\"1\"><",
            prefix,
            b":r><",
            prefix,
            b":t>A&#46; AT&amp;T&#32;</",
            prefix,
            b":t></",
            prefix,
            b":r></",
            prefix,
            b":p>",
            newline,
            b"<",
            prefix,
            b":bookmarkStart ",
            prefix,
            b":id='0'/>",
            newline,
            b"<?second-gap value='unchanged'?><!-- keep CRLF and comments -->",
            newline,
            b"<",
            prefix,
            b":p><",
            prefix,
            b":r><",
            prefix,
            b":t>Z&#x2E; Omega&#x20;</",
            prefix,
            b":t></",
            prefix,
            b":r></",
            prefix,
            b":p>",
            newline,
            b"</",
            prefix,
            b":body>",
            newline,
            b"</",
            prefix,
            b":document>",
            newline,
            b"<?after-root untouched?>",
        )
    )


def _api_document_xml(*, cdata: bool = False) -> bytes:
    """A schema-light Word main part with deliberately odd lexical choices."""
    first_text = (
        b"A&#x2E; <![CDATA[Install AT&T equipment from 2019.]]>&#32;"
        if cdata
        else b"A&#x2E; Install AT&amp;T equipment from 2019.&#32;"
    )
    return b"".join(
        (
            _UTF8_BOM,
            b"<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\r\n",
            b"<?client-source quote=\"single-and-double\"?>\r\n",
            b"<quill:document data-z='last' xmlns:unused=\"urn:unused\" ",
            b"xmlns:quill='",
            _W_NS.encode("ascii"),
            b"' data-a=\"first\">\r\n",
            b"<quill:body data-two='2' xmlns:local=\"urn:local\" ",
            b"data-one=\"1\">\r\n",
            b"  <!-- heading gap must remain byte-exact -->\r\n",
            b"<quill:p data-z='9' data-a=\"1\"><quill:r><quill:t>",
            b"SECTION 21 13 13",
            b"</quill:t></quill:r></quill:p>\r\n",
            b"<?between-headings preserve='yes'?>\r\n",
            b"<quill:p><quill:r><quill:t>LEXICAL FIDELITY</quill:t>",
            b"</quill:r></quill:p>\r\n",
            b"<quill:p><quill:r><quill:t>PART 1 - GENERAL</quill:t>",
            b"</quill:r></quill:p>\r\n",
            b"<quill:p><quill:r><quill:t>1.1 SOURCE TEXT</quill:t>",
            b"</quill:r></quill:p>\r\n",
            b"<!-- immediately before first target -->\r\n",
            b"<quill:p data-last='z' data-first=\"a\"><quill:r><quill:t>",
            first_text,
            b"</quill:t></quill:r></quill:p>\r\n",
            b"<?between-targets exact?>\r\n",
            b"<quill:p><quill:r><quill:t>",
            b"B&#46; Retain 20&#50;0 note.&#x20;",
            b"</quill:t></quill:r></quill:p>\r\n",
            b"<!-- immediately after last target -->\r\n",
            b"<quill:p><quill:r><quill:t>END OF SECTION 21 13 13</quill:t>",
            b"</quill:r></quill:p>\r\n",
            b"<quill:sectPr custom-last='true'/>\r\n",
            b"</quill:body>\r\n",
            b"</quill:document>\r\n",
            b"<?after-document preserve=\"exactly\"?>",
        )
    )


@pytest.mark.parametrize(
    ("bom", "declaration", "newline", "prefix"),
    [
        pytest.param(
            b"",
            b'<?xml version="1.0" encoding="UTF-8"?>',
            b"\n",
            b"word",
            id="utf8-double-quotes-no-standalone",
        ),
        pytest.param(
            _UTF8_BOM,
            b"<?xml version='1.0' encoding='utf-8' standalone='no'?>",
            b"\r\n",
            b"unusualWordPrefix",
            id="utf8-bom-single-quotes-standalone-crlf",
        ),
    ],
)
def test_index_and_multiple_patches_preserve_adversarial_lexical_bytes(
    bom: bytes,
    declaration: bytes,
    newline: bytes,
    prefix: bytes,
):
    source = _minimal_index_xml(
        bom=bom,
        declaration=declaration,
        newline=newline,
        prefix=prefix,
    )

    index = build_source_xml_index(source)
    assert index.encoding == "utf-8"
    assert index.bom == bom
    assert index.xml_declaration_span is not None
    assert (
        source[
            index.xml_declaration_span.start : index.xml_declaration_span.end
        ]
        == declaration
    )
    assert [child.body_child_index for child in index.body_children] == [0, 1, 2]
    assert index.word_text(0, 0).lexical_name == prefix + b":t"
    assert index.word_text(2, 0).lexical_name == prefix + b":t"
    assert dict(index.body_namespace_bindings)[prefix.decode()] == _W_NS

    gap_bytes = [source[span.start : span.end] for span in index.body_gaps]
    assert b"<!-- before-first --><?gap keep?>" in gap_bytes[0]
    assert (
        b"<?second-gap value='unchanged'?><!-- keep CRLF and comments -->"
        in gap_bytes[2]
    )

    first = index.word_text(0, 0)
    last = index.word_text(2, 0)
    assert first.decoded_text == "A. AT&T "
    assert last.decoded_text == "Z. Omega "
    first_span = decoded_slice_byte_span(source, first, 3, 7)
    last_span = decoded_slice_byte_span(source, last, 3, 8)
    assert source[first_span.start : first_span.end] == b"AT&amp;T"
    assert source[last_span.start : last_span.end] == b"Omega"

    patched = apply_xml_patches(
        source,
        (
            XmlPatch(
                first_span.start,
                first_span.end,
                b"R&amp;D",
                "first",
                "replace_text",
            ),
            XmlPatch(
                last_span.start,
                last_span.end,
                b"Sigma",
                "last",
                "replace_text",
            ),
        ),
    )
    expected = source.replace(b"AT&amp;T", b"R&amp;D", 1).replace(
        b"Omega", b"Sigma", 1
    )
    assert patched == expected
    assert patched.startswith(bom + declaration + newline)
    assert b"A&#46; R&amp;D&#32;" in patched
    assert b"Z&#x2E; Sigma&#x20;" in patched
    etree.fromstring(patched)


@pytest.mark.parametrize(
    ("raw_text", "decoded", "raw_semantic"),
    [
        pytest.param(
            b"A&#46; AT&amp;T&#32;",
            "A. AT&T ",
            b"AT&amp;T",
            id="named-and-decimal-references",
        ),
        pytest.param(
            b"A&#x2E; AT&#x26;T&#x20;",
            "A. AT&T ",
            b"AT&#x26;T",
            id="hex-references",
        ),
    ],
)
def test_decoded_offsets_map_across_entities_without_rewriting_prefix_or_suffix(
    raw_text: bytes,
    decoded: str,
    raw_semantic: bytes,
):
    source = b"".join(
        (
            b"<w:document xmlns:w='",
            _W_NS.encode("ascii"),
            b"'><w:body><w:p><w:r><w:t>",
            raw_text,
            b"</w:t></w:r></w:p></w:body></w:document>",
        )
    )
    node = build_source_xml_index(source).word_text(0, 0)
    assert node.decoded_text == decoded
    span = decoded_slice_byte_span(source, node, 3, 7)
    assert source[span.start : span.end] == raw_semantic

    patched = apply_xml_patches(
        source,
        [XmlPatch(span.start, span.end, b"R&amp;D", "p1", "replace_text")],
    )
    expected = source[: span.start] + b"R&amp;D" + source[span.end :]
    assert patched == expected
    assert patched[: span.start] == source[: span.start]
    suffix_start = span.start + len(b"R&amp;D")
    assert patched[suffix_start:] == source[span.end :]


def test_overlapping_patch_manifest_is_rejected():
    with pytest.raises(XmlLexicalError) as exc_info:
        apply_xml_patches(
            b"0123456789",
            (
                XmlPatch(2, 6, b"first", "p1", "replace_text"),
                XmlPatch(5, 8, b"second", "p2", "replace_text"),
            ),
        )
    assert exc_info.value.blocker == "overlapping_xml_patches"


def test_replacement_cannot_form_cdata_close_across_patch_boundaries():
    assert encode_word_text(">value", raw_prefix=b"]]") == b"&gt;value"
    assert encode_word_text("value]]", raw_suffix=b">") == b"value]&#93;"


def test_cdata_text_is_indexed_but_never_exposed_as_a_mutable_byte_slice():
    source = b"".join(
        (
            b"<w:document xmlns:w='",
            _W_NS.encode("ascii"),
            b"'><w:body><w:p><w:r><w:t>",
            b"<![CDATA[A. Install & retain equipment.]]>",
            b"</w:t></w:r></w:p></w:body></w:document>",
        )
    )
    node = build_source_xml_index(source).word_text(0, 0)
    assert node.decoded_text == "A. Install & retain equipment."
    assert node.mutable_content is False
    with pytest.raises(XmlLexicalError) as exc_info:
        decoded_slice_byte_span(source, node, 3, len(node.decoded_text))
    assert exc_info.value.blocker == "unsupported_source_text_lexical_form"


def test_text_only_api_export_splices_two_semantic_slices_and_bypasses_serializer(
    tmp_path,
    monkeypatch,
):
    original_package = make_fidelity_master(tmp_path)
    source_xml = _api_document_xml()
    source = _with_document_xml(original_package, source_xml)
    client = _client()
    imported = _import_master(client, source)
    assert imported["source_preservation"]["status"] == "ready"
    assert [
        paragraph["id"]
        for paragraph in imported["doc"]["parts"][0]["articles"][0][
            "paragraphs"
        ]
    ] == ["pt1.a1.p1", "pt1.a1.p2"]

    def serializer_must_not_run(*_args, **_kwargs):
        raise AssertionError("text-only source export used the tree serializer")

    monkeypatch.setattr(
        source_patch_module,
        "_serialize_tree",
        serializer_must_not_run,
    )
    edited = client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "replace",
                    "target_id": "pt1.a1.p1",
                    "text": "Provide <listed> AT&T equipment for 2026.",
                    "status": "confirmed",
                },
                {
                    "action": "replace",
                    "target_id": "pt1.a1.p2",
                    "text": "Retain R&D closeout records.",
                    "status": "confirmed",
                },
            ]
        },
    )
    assert edited.status_code == 200, edited.text

    first_export = _source_export(client)
    assert first_export.status_code == 200, first_export.text
    output_xml = _document_xml(first_export.content)
    expected_xml = source_xml.replace(
        b"Install AT&amp;T equipment from 2019.",
        b"Provide &lt;listed> AT&amp;T equipment for 2026.",
        1,
    ).replace(
        b"Retain 20&#50;0 note.",
        b"Retain R&amp;D closeout records.",
        1,
    )
    assert output_xml == expected_xml
    assert b"A&#x2E; Provide &lt;listed>" in output_xml
    assert b"2026.&#32;" in output_xml
    assert b"B&#46; Retain R&amp;D" in output_xml
    assert b"records.&#x20;" in output_xml
    assert etree.fromstring(output_xml).tag == f"{{{_W_NS}}}document"
    assert Document(io.BytesIO(first_export.content)).paragraphs

    # Repeated export is deterministic. Undo reaches the immutable package;
    # redo regenerates the same byte-spliced main part.
    assert _source_export(client).content == first_export.content
    undone = client.post("/api/doc/undo")
    assert undone.status_code == 200
    assert _source_export(client).content == source
    redone = client.post("/api/doc/redo")
    assert redone.status_code == 200
    assert _source_export(client).content == first_export.content
    assert client.get("/api/import/original").content == source


def test_cdata_source_text_edit_fails_closed_and_keeps_session_atomic(tmp_path):
    source = _with_document_xml(
        make_fidelity_master(tmp_path),
        _api_document_xml(cdata=True),
    )
    client = _client()
    imported = _import_master(client, source)
    assert imported["source_preservation"]["status"] == "ready"
    before = client.get("/api/doc").json()["doc"]

    rejected = _replace(client, "pt1.a1.p1", "Replace the CDATA provision.")
    assert rejected.status_code == 400
    assert "[unsupported_source_text_lexical_form]" in rejected.json()["error"]
    assert client.get("/api/doc").json()["doc"] == before
    assert _source_export(client).content == source


def test_utf16_source_is_pass_through_only_and_edit_rejection_is_atomic(tmp_path):
    utf8_xml = _api_document_xml()
    xml_text = utf8_xml.decode("utf-8-sig").replace(
        "encoding='UTF-8'", "encoding='UTF-16'", 1
    )
    utf16_xml = xml_text.encode("utf-16")
    source = _with_document_xml(make_fidelity_master(tmp_path), utf16_xml)
    client = _client()
    imported = _import_master(client, source)

    preservation = imported["source_preservation"]
    assert preservation["status"] == "pass_through_only"
    assert preservation["source_export_ready"] is True
    assert preservation["exact_original_available"] is True
    assert preservation["body_editing"] == "disabled"
    assert {
        blocker["blocker"] for blocker in preservation["blockers"]
    } == {"unsupported_source_xml_encoding"}
    assert _source_export(client).content == source

    before = client.get("/api/doc").json()["doc"]
    rejected = _replace(client, "pt1.a1.p1", "An unsafe transcoding attempt.")
    assert rejected.status_code == 400
    assert "[unsupported_source_xml_encoding]" in rejected.json()["error"]
    assert client.get("/api/doc").json()["doc"] == before
    assert _source_export(client).content == source
    assert client.get("/api/import/original").content == source


@pytest.mark.parametrize("codec", ["utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"])
def test_bomless_interleaved_unicode_encodings_are_precise_blockers(codec):
    source = (
        f"<w:document xmlns:w='{_W_NS}'><w:body/></w:document>"
    ).encode(codec)
    with pytest.raises(XmlLexicalError) as exc_info:
        detect_xml_encoding(source)
    assert exc_info.value.blocker == "unsupported_source_xml_encoding"
