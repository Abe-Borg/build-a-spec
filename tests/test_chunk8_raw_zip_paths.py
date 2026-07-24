"""Chunk 8 adversarial coverage for raw ZIP spans and OPC member paths.

The fixtures are deterministic classic ZIP32 archives.  Binary mutations use
the independent record oracle from ``test_raw_zip_clone`` so the cases do not
depend on ``zipfile`` agreeing with the production raw-record parser.
"""
from __future__ import annotations

import io
import struct
import warnings
import zipfile

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.spec_doc.raw_zip import RawZipError, parse_raw_zip_archive
from backend.spec_doc.source_package import (
    SourcePackageError,
    inspect_docx_package,
)
from tests.docx_fidelity_helpers import (
    DOCX_MEDIA_TYPE,
    make_fidelity_master,
    rewrite_zip_members,
)
from tests.test_raw_zip_clone import _CENTRAL, _oracle


_CONTENT_TYPES = b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Override PartName="/word/document.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
_ROOT_RELS = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
    Target="word/document.xml"/>
</Relationships>"""
_DOCUMENT = b"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>Deterministic ZIP fixture.</w:t></w:r></w:p></w:body>
</w:document>"""
_FIXED_TIME = (2024, 5, 6, 7, 8, 10)


def _package(*additions: tuple[str, bytes]) -> bytes:
    """Return a minimal, valid OPC Word package with fixed ZIP metadata."""
    entries = (
        ("[Content_Types].xml", _CONTENT_TYPES),
        ("_rels/.rels", _ROOT_RELS),
        ("word/document.xml", _DOCUMENT),
        *additions,
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", allowZip64=False) as archive:
        for name, payload in entries:
            info = zipfile.ZipInfo(name, date_time=_FIXED_TIME)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100640 << 16
            archive.writestr(info, payload)
    return output.getvalue()


def _central_name_shadow(
    source: bytes,
    *,
    original: str,
    shadow: str,
) -> bytes:
    """Change only one central-directory filename to another equal-size name."""
    original_bytes = original.encode("utf-8")
    shadow_bytes = shadow.encode("utf-8")
    assert len(original_bytes) == len(shadow_bytes)
    entry = _oracle(source).entry(original)
    output = bytearray(source)
    name_start = entry.central_start + _CENTRAL.size
    assert output[name_start : name_start + len(original_bytes)] == original_bytes
    output[name_start : name_start + len(shadow_bytes)] = shadow_bytes
    return bytes(output)


def _partially_overlap_local_records(
    source: bytes,
    *,
    first_name: str | None = None,
    second_name: str | None = None,
) -> bytes:
    """Point one central record into the preceding member's stored data."""
    archive = _oracle(source)
    if first_name is None and second_name is None:
        first, second = archive.entries[:2]
    else:
        assert first_name is not None and second_name is not None
        first = archive.entry(first_name)
        second = archive.entry(second_name)
    assert first.local_header_end + 1 < first.compressed_data_end
    overlapping_offset = first.local_header_end + 1 - archive.offset_base
    output = bytearray(source)
    struct.pack_into("<I", output, second.central_start + 42, overlapping_offset)
    return bytes(output)


def _with_consistently_wrong_crc(source: bytes, *, member_name: str) -> bytes:
    """Forge matching local/central CRC fields that disagree with payload."""
    entry = _oracle(source).entry(member_name)
    assert entry.flags & 0x0008 == 0
    local_crc = struct.unpack_from("<I", source, entry.local_start + 14)[0]
    central_crc = struct.unpack_from("<I", source, entry.central_start + 16)[0]
    assert local_crc == central_crc
    forged_crc = local_crc ^ 0xFFFFFFFF
    output = bytearray(source)
    struct.pack_into("<I", output, entry.local_start + 14, forged_crc)
    struct.pack_into("<I", output, entry.central_start + 16, forged_crc)
    return bytes(output)


@pytest.mark.parametrize(
    "unsafe_name",
    [
        pytest.param("word/%2e%2e/escape.xml", id="encoded-dot-dot-lower"),
        pytest.param("word/%2E%2E/escape.xml", id="encoded-dot-dot-upper"),
        pytest.param("word%5cescape.xml", id="encoded-backslash-lower"),
        pytest.param("word%5Cescape.xml", id="encoded-backslash-upper"),
        pytest.param("word%2fescape.xml", id="encoded-forward-slash"),
        pytest.param("C%3a/escape.xml", id="encoded-drive-colon"),
        pytest.param("word/control%00.xml", id="encoded-nul"),
        pytest.param("word/incomplete%.xml", id="truncated-percent"),
        pytest.param("word/incomplete%2.xml", id="short-percent"),
        pytest.param("word/invalid%GG.xml", id="non-hex-percent"),
    ],
)
def test_encoded_or_malformed_member_paths_fail_both_package_boundaries(
    unsafe_name,
):
    source = _package((unsafe_name, b"adversarial member"))

    with pytest.raises(SourcePackageError, match="unsafe member"):
        inspect_docx_package(source)
    with pytest.raises(RawZipError, match="unsafe"):
        parse_raw_zip_archive(source)


@pytest.mark.parametrize(
    "aliases",
    [
        pytest.param(
            (("word/%64ocument.xml", b"encoded shadow"),),
            id="percent-encoded-unreserved-shadow",
        ),
        pytest.param(
            (("WORD/document.xml", b"case shadow"),),
            id="case-folded-shadow",
        ),
        pytest.param(
            (
                ("customXml/caf\N{LATIN SMALL LETTER E WITH ACUTE}.xml", b"nfc"),
                ("customxml/cafe\N{COMBINING ACUTE ACCENT}.xml", b"nfd"),
            ),
            id="unicode-normalization-shadow",
        ),
        pytest.param(
            (
                ("customXml/%C3%A9.xml", b"encoded utf8"),
                ("customxml/\N{LATIN SMALL LETTER E WITH ACUTE}.xml", b"literal utf8"),
            ),
            id="percent-encoded-utf8-shadow",
        ),
    ],
)
def test_normalized_member_name_aliases_are_duplicate_names(aliases):
    source = _package(*aliases)

    package_info = inspect_docx_package(source)
    assert package_info.member_count == 3 + len(aliases)
    with pytest.raises(RawZipError, match="duplicate names"):
        parse_raw_zip_archive(source)


def test_valid_percent_encoded_utf8_member_name_is_not_over_rejected():
    source = _package(("customXml/%C3%A9vidence.xml", b"safe opaque payload"))

    package_info = inspect_docx_package(source)
    raw = parse_raw_zip_archive(source)

    assert package_info.member_count == 4
    assert raw.entry("customXml/%C3%A9vidence.xml").uncompressed_size == len(
        b"safe opaque payload"
    )


def test_central_directory_only_document_shadow_is_rejected_before_local_use():
    source = _package(("word/documenx.xml", b"central-only shadow"))
    source = _central_name_shadow(
        source,
        original="word/documenx.xml",
        shadow="word/document.xml",
    )
    with zipfile.ZipFile(io.BytesIO(source), "r") as archive:
        assert archive.namelist().count("word/document.xml") == 2

    with pytest.raises(SourcePackageError, match="malformed required Word XML"):
        inspect_docx_package(source)
    with pytest.raises(RawZipError, match="duplicate names"):
        parse_raw_zip_archive(source)


def test_partial_local_record_overlap_is_rejected_by_both_boundaries():
    source = _partially_overlap_local_records(_package())

    with pytest.raises(SourcePackageError, match="malformed required Word XML"):
        inspect_docx_package(source)
    with pytest.raises(RawZipError, match="overlaps the next archive record"):
        parse_raw_zip_archive(source)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            _package(("word/%2e%2e/escape.xml", b"unsafe")),
            id="encoded-traversal",
        ),
        pytest.param(
            _central_name_shadow(
                _package(("word/documenx.xml", b"shadow")),
                original="word/documenx.xml",
                shadow="word/document.xml",
            ),
            id="duplicate-central-name",
        ),
        pytest.param(
            _partially_overlap_local_records(_package()),
            id="overlapping-local-records",
        ),
    ],
)
def test_unsafe_zip_packages_are_rejected_without_retaining_source(source):
    client = TestClient(create_app())

    rejected = client.post(
        "/api/import/master",
        files={"file": ("unsafe.docx", source, DOCX_MEDIA_TYPE)},
    )

    assert rejected.status_code == 400
    assert client.get("/api/doc").json()["source_available"] is False
    assert client.get("/api/import/original").status_code == 404


def test_raw_layout_ambiguity_is_pass_through_only_with_exact_noop(tmp_path):
    # Harmless trailing bytes are supported, but an orphan ZIP record marker
    # makes mutation ambiguous.  Package/OPC inspection may retain this source;
    # the raw mutation boundary must narrow it to exact-original only.
    source = make_fidelity_master(tmp_path) + b"PK\x03\x04ORPHAN-LOCAL-RECORD"
    inspect_docx_package(source)
    client = TestClient(create_app())

    imported = client.post(
        "/api/import/master",
        files={"file": ("ambiguous-raw-layout.docx", source, DOCX_MEDIA_TYPE)},
    )

    assert imported.status_code == 200, imported.text
    preservation = imported.json()["source_preservation"]
    assert preservation["status"] == "pass_through_only"
    assert preservation["exact_original_available"] is True
    assert {item["blocker"] for item in preservation["blockers"]} == {
        "unsupported_raw_zip_layout"
    }
    assert client.get("/api/import/original").content == source
    assert client.get("/api/export/docx", params={"mode": "source"}).content == source

    before = client.get("/api/doc").json()["doc"]
    rejected = client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "replace",
                    "target_id": "pt1.a1.p1",
                    "text": "This mutation must remain unavailable.",
                }
            ]
        },
    )
    assert rejected.status_code == 400
    assert "[unsupported_raw_zip_layout]" in rejected.json()["error"]
    assert client.get("/api/doc").json()["doc"] == before
    assert client.get("/api/export/docx", params={"mode": "source"}).content == source


@pytest.mark.parametrize("layout", ["duplicate", "overlap", "bad-crc"])
def test_raw_zip_ambiguity_retains_exact_source_pass_through_only(
    tmp_path,
    layout,
):
    master = make_fidelity_master(tmp_path)
    first_name = "customXml/chunk8-first.bin"
    second_name = "customXml/chunk8-second.bin"
    if layout == "duplicate":
        duplicate_name = "customXml/chunk8-duplicate.bin"
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Duplicate name:.*",
                category=UserWarning,
            )
            source = rewrite_zip_members(
                master,
                additions=[
                    (duplicate_name, b"first duplicate payload"),
                    (duplicate_name, b"second duplicate payload"),
                ],
            )
    elif layout == "overlap":
        source = rewrite_zip_members(
            master,
            additions=[
                (first_name, bytes(range(256)) * 4),
                (second_name, b"overlapped extra member"),
            ],
        )
        source = _partially_overlap_local_records(
            source,
            first_name=first_name,
            second_name=second_name,
        )
    else:
        source = rewrite_zip_members(
            master,
            additions=[(first_name, b"opaque member with forged CRC")],
        )
        source = _with_consistently_wrong_crc(
            source,
            member_name=first_name,
        )

    # Package/OPC inspection keeps the bounded recovery artifact, while the
    # strict raw span index refuses to expose any writable surface.
    package_info = inspect_docx_package(source)
    if layout == "bad-crc":
        # Decompressed-integrity proof and structural raw indexing are
        # deliberately separate.  The package proof carries its ambiguity
        # into source context so the structurally consistent CRC forgery can
        # never expose a writable surface.
        assert package_info.integrity_ambiguous is True
        parse_raw_zip_archive(source)
    else:
        assert package_info.integrity_ambiguous is (layout == "overlap")
        with pytest.raises(RawZipError) as raw_error:
            parse_raw_zip_archive(source)
        assert raw_error.value.blocker == "unsupported_raw_zip_layout"

    client = TestClient(create_app())
    imported = client.post(
        "/api/import/master",
        files={"file": (f"{layout}-raw-layout.docx", source, DOCX_MEDIA_TYPE)},
    )

    assert imported.status_code == 200, imported.text
    preservation = imported.json()["source_preservation"]
    assert preservation["status"] == "pass_through_only"
    assert preservation["exact_original_available"] is True
    assert {item["blocker"] for item in preservation["blockers"]} == {
        "unsupported_raw_zip_layout"
    }
    assert client.get("/api/import/original").content == source
    assert client.get("/api/export/docx", params={"mode": "source"}).content == source

    before = client.get("/api/doc").json()["doc"]
    rejected = client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "replace",
                    "target_id": "pt1.a1.p1",
                    "text": "Raw ambiguity must never gain a writable surface.",
                }
            ]
        },
    )
    assert rejected.status_code == 400
    assert "[unsupported_raw_zip_layout]" in rejected.json()["error"]
    assert client.get("/api/doc").json()["doc"] == before
    assert client.get("/api/import/original").content == source
    assert client.get("/api/export/docx", params={"mode": "source"}).content == source
