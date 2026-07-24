"""Chunk 8 adversarial coverage for lexical source-preserving XML edits.

These cases concentrate on byte-level shapes that semantic XML parsers tend
to normalize: body comments/PIs, direct character data, encoding changes,
entity spellings, and very large text nodes.  Exact no-ops must always return
the immutable package, safe edits may change only their approved text slice,
and ambiguous sources must reject a mutation without changing session state.
"""
from __future__ import annotations

import io
import random
import zipfile

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.spec_doc.xml_lexical import (
    XmlLexicalError,
    XmlPatch,
    apply_xml_patches,
    build_source_xml_index,
    decoded_slice_byte_span,
    encode_word_text,
)
from tests.docx_fidelity_helpers import (
    DOCX_MEDIA_TYPE,
    make_fidelity_master,
    rewrite_zip_members,
)


_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _document_xml(payload: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        return archive.read("word/document.xml")


def _package_with_document_xml(tmp_path, document_xml: bytes, *, stem: str) -> bytes:
    base = make_fidelity_master(tmp_path, filename=f"{stem}-base.docx")
    return rewrite_zip_members(
        base,
        replacements={"word/document.xml": document_xml},
    )


def _body_xml_bytes(
    *,
    target: bytes = b"A&#46; Install AT&amp;T equipment from 2019.",
    target_gap: bytes = b"\r\n",
    declaration: bytes = b"<?xml version='1.0' encoding='UTF-8'?>\r\n",
) -> bytes:
    """Return a small importable Word main part with one mapped provision."""
    return b"".join(
        (
            declaration,
            b"<w:document xmlns:w='",
            _W_NS.encode("ascii"),
            b"'><w:body>\r\n",
            b"<w:p><w:r><w:t>SECTION 21 13 13</w:t></w:r></w:p>\r\n",
            b"<w:p><w:r><w:t>LEXICAL ADVERSARIAL</w:t></w:r></w:p>\r\n",
            b"<w:p><w:r><w:t>PART 1 - GENERAL</w:t></w:r></w:p>\r\n",
            b"<w:p><w:r><w:t>1.1 SOURCE TEXT</w:t></w:r></w:p>\r\n",
            b"<w:p><w:r><w:t>",
            target,
            b"</w:t></w:r></w:p>",
            target_gap,
            b"<w:p><w:r><w:t>B. Retain the adjacent provision.</w:t>",
            b"</w:r></w:p>\r\n",
            b"<w:p><w:r><w:t>END OF SECTION 21 13 13</w:t></w:r></w:p>\r\n",
            b"<w:sectPr/>\r\n",
            b"</w:body></w:document>",
        )
    )


def _body_xml_text(*, encoding: str, target: str) -> str:
    return "".join(
        (
            f"<?xml version='1.0' encoding='{encoding}'?>\n",
            f"<w:document xmlns:w='{_W_NS}'><w:body>\n",
            "<w:p><w:r><w:t>SECTION 21 13 13</w:t></w:r></w:p>\n",
            "<w:p><w:r><w:t>LEXICAL ADVERSARIAL</w:t></w:r></w:p>\n",
            "<w:p><w:r><w:t>PART 1 - GENERAL</w:t></w:r></w:p>\n",
            "<w:p><w:r><w:t>1.1 SOURCE TEXT</w:t></w:r></w:p>\n",
            f"<w:p><w:r><w:t>{target}</w:t></w:r></w:p>\n",
            "<w:p><w:r><w:t>B. Retain the adjacent provision.</w:t></w:r></w:p>\n",
            "<w:p><w:r><w:t>END OF SECTION 21 13 13</w:t></w:r></w:p>\n",
            "<w:sectPr/>\n</w:body></w:document>",
        )
    )


def _client() -> TestClient:
    return TestClient(create_app())


def _import(client: TestClient, source: bytes, *, filename: str = "source.docx") -> dict:
    response = client.post(
        "/api/import/master",
        files={"file": (filename, source, DOCX_MEDIA_TYPE)},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _source_export(client: TestClient):
    return client.get("/api/export/docx", params={"mode": "source"})


def _replace_first(client: TestClient, text: str):
    return client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "replace",
                    "target_id": "pt1.a1.p1",
                    "text": text,
                    "status": "confirmed",
                }
            ]
        },
    )


def _assert_atomic_rejection(
    client: TestClient,
    source: bytes,
    *,
    blocker: str,
) -> None:
    before = client.get("/api/doc").json()["doc"]
    assert _source_export(client).content == source
    rejected = _replace_first(client, "A mutation that must not commit.")
    assert rejected.status_code == 400, rejected.text
    assert f"[{blocker}]" in rejected.json()["error"]
    assert client.get("/api/doc").json()["doc"] == before
    assert _source_export(client).content == source
    assert client.get("/api/import/original").content == source


def _minimal_text_xml(raw_text: bytes) -> bytes:
    return b"".join(
        (
            b"<w:document xmlns:w='",
            _W_NS.encode("ascii"),
            b"'><w:body><w:p><w:r><w:t>",
            raw_text,
            b"</w:t></w:r></w:p></w:body></w:document>",
        )
    )


def test_comments_pis_and_xml_s_references_survive_safe_text_splice(tmp_path):
    gap = (
        b"&#x20;&#10;<!-- body-gap comment: keep raw -->\r\n"
        b"<?body-gap preserve='yes'?>&#9;"
    )
    source_xml = _body_xml_bytes(target_gap=gap)
    source = _package_with_document_xml(tmp_path, source_xml, stem="markup-gap")
    client = _client()
    imported = _import(client, source)

    assert imported["source_preservation"]["status"] == "ready"
    assert _source_export(client).content == source

    old_slice = b"Install AT&amp;T equipment from 2019."
    new_slice = b"Provide R&amp;D equipment for 2026."
    old_start = source_xml.index(old_slice)
    old_end = old_start + len(old_slice)
    edited = _replace_first(client, "Provide R&D equipment for 2026.")
    assert edited.status_code == 200, edited.text

    output_xml = _document_xml(_source_export(client).content)
    assert output_xml == source_xml[:old_start] + new_slice + source_xml[old_end:]
    assert output_xml[:old_start] == source_xml[:old_start]
    assert output_xml[old_start + len(new_slice) :] == source_xml[old_end:]
    assert gap in output_xml


@pytest.mark.parametrize(
    "direct_character_data",
    [
        pytest.param(b"UNMAPPED-BODY-TEXT", id="ascii-text"),
        pytest.param(b"&#160;", id="numeric-nonbreaking-space"),
        pytest.param("\u2003".encode("utf-8"), id="literal-em-space"),
    ],
)
def test_non_xml_s_direct_body_data_is_pass_through_only_and_atomic(
    tmp_path,
    direct_character_data: bytes,
):
    source_xml = _body_xml_bytes(
        target_gap=b"\r\n" + direct_character_data + b"\r\n"
    )
    source = _package_with_document_xml(
        tmp_path,
        source_xml,
        stem="direct-body-data",
    )
    client = _client()
    imported = _import(client, source)

    preservation = imported["source_preservation"]
    assert preservation["status"] == "pass_through_only"
    assert {item["blocker"] for item in preservation["blockers"]} == {
        "unsafe_document_xml"
    }
    _assert_atomic_rejection(client, source, blocker="unsafe_document_xml")


@pytest.mark.parametrize(
    ("declaration_encoding", "codec", "target"),
    [
        pytest.param(
            "UTF-16",
            "utf-16",
            "A. Install UTF-16 equipment.",
            id="utf16-with-bom",
        ),
        pytest.param(
            "UTF-16BE",
            "utf-16-be",
            "A. Install UTF-16BE equipment.",
            id="utf16be-without-bom",
        ),
        pytest.param(
            "ISO-8859-1",
            "iso-8859-1",
            "A. Install caf\u00e9 equipment.",
            id="latin1",
        ),
        pytest.param(
            "US-ASCII",
            "ascii",
            "A. Install ASCII equipment.",
            id="ascii-declaration",
        ),
    ],
)
def test_unsupported_declared_encodings_keep_exact_noop_and_reject_atomically(
    tmp_path,
    declaration_encoding: str,
    codec: str,
    target: str,
):
    source_xml = _body_xml_text(
        encoding=declaration_encoding,
        target=target,
    ).encode(codec)
    source = _package_with_document_xml(
        tmp_path,
        source_xml,
        stem=f"unsupported-{codec}",
    )
    client = _client()
    imported = _import(client, source, filename=f"unsupported-{codec}.docx")

    preservation = imported["source_preservation"]
    assert preservation["status"] == "pass_through_only"
    assert {item["blocker"] for item in preservation["blockers"]} == {
        "unsupported_source_xml_encoding"
    }
    _assert_atomic_rejection(
        client,
        source,
        blocker="unsupported_source_xml_encoding",
    )


def _entity_spelling(character: str, rng: random.Random, *, force_reference: bool) -> bytes:
    value = ord(character)
    choices = [f"&#{value};".encode("ascii"), f"&#x{value:X};".encode("ascii")]
    if character == "&":
        choices.append(b"&amp;")
    elif character == "<":
        choices.append(b"&lt;")
    elif character == ">":
        choices.extend((b"&gt;", b">"))
    else:
        choices.append(character.encode("utf-8"))
    if force_reference:
        choices = [choice for choice in choices if choice.startswith(b"&")]
    return rng.choice(choices)


def _entity_lexicalize(
    text: str,
    rng: random.Random,
    *,
    force_first_reference: bool = False,
    force_last_reference: bool = False,
) -> bytes:
    return b"".join(
        _entity_spelling(
            character,
            rng,
            force_reference=(
                (index == 0 and force_first_reference)
                or (index == len(text) - 1 and force_last_reference)
            ),
        )
        for index, character in enumerate(text)
    )


def test_deterministic_entity_boundary_fuzz_preserves_every_raw_neighbor():
    replacement = "R&D <new> \u03a9"
    for case in range(64):
        rng = random.Random(0xB17E + case)
        decoded_prefix = "A. pre\u03a9 "
        decoded_target = "&target>"
        decoded_suffix = " post<end"
        raw_prefix = _entity_lexicalize(decoded_prefix, rng)
        raw_target = _entity_lexicalize(
            decoded_target,
            rng,
            force_first_reference=True,
            force_last_reference=case % 2 == 0,
        )
        raw_suffix = _entity_lexicalize(
            decoded_suffix,
            rng,
            force_first_reference=case % 3 == 0,
        )
        raw_text = raw_prefix + raw_target + raw_suffix
        source = _minimal_text_xml(raw_text)
        node = build_source_xml_index(source).word_text(0, 0)
        assert node.decoded_text == decoded_prefix + decoded_target + decoded_suffix

        span = decoded_slice_byte_span(
            source,
            node,
            len(decoded_prefix),
            len(decoded_prefix) + len(decoded_target),
        )
        assert source[span.start : span.end] == raw_target
        encoded = encode_word_text(
            replacement,
            raw_prefix=source[node.content_span.start : span.start],
            raw_suffix=source[span.end : node.content_span.end],
        )
        output = apply_xml_patches(
            source,
            [XmlPatch(span.start, span.end, encoded, "target", "replace_text")],
        )
        assert output[: span.start] == source[: span.start]
        assert output[span.start + len(encoded) :] == source[span.end :]
        assert (
            build_source_xml_index(output).word_text(0, 0).decoded_text
            == decoded_prefix + replacement + decoded_suffix
        )


@pytest.mark.parametrize(
    "raw_text",
    [
        pytest.param(b"A &undefined; value", id="undefined-named-entity"),
        pytest.param(b"A &#x110000; value", id="out-of-range-character-reference"),
        pytest.param(b"A &#; value", id="empty-character-reference"),
        pytest.param(b"A &amp value", id="unterminated-reference"),
    ],
)
def test_ambiguous_or_illegal_entity_forms_fail_closed(raw_text: bytes):
    with pytest.raises(XmlLexicalError) as exc_info:
        build_source_xml_index(_minimal_text_xml(raw_text))
    assert exc_info.value.blocker == "unsafe_document_xml"


def test_extremely_long_text_node_maps_only_requested_slice_and_preserves_rest():
    repetitions = 200_000
    raw_unit = b"A&amp;B&#x20;"
    raw_text = raw_unit * repetitions
    source = _minimal_text_xml(raw_text)
    node = build_source_xml_index(source).word_text(0, 0)

    assert len(node.decoded_text) == repetitions * 4
    decoded_start = (repetitions - 1) * 4 + 1
    span = decoded_slice_byte_span(
        source,
        node,
        decoded_start,
        decoded_start + 2,
    )
    assert source[span.start : span.end] == b"&amp;B"

    replacement = b"R&amp;D"
    output = apply_xml_patches(
        source,
        [XmlPatch(span.start, span.end, replacement, "tail", "replace_text")],
    )
    assert output[: span.start] == source[: span.start]
    assert output[span.start + len(replacement) :] == source[span.end :]
    assert build_source_xml_index(output).word_text(0, 0).decoded_text.endswith(
        "AR&D "
    )
