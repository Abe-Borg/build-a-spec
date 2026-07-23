"""Acceptance tests for the raw-record-preserving DOCX ZIP clone.

The assertions in this module deliberately use a small, independent binary
ZIP oracle.  Reading the output only through :mod:`zipfile` would prove that
the archive remained semantically valid, but would miss recompression or
metadata drift in members that Build-a-Spec does not own.
"""
from __future__ import annotations

import binascii
import copy
import io
import struct
import warnings
import zipfile
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.spec_doc import raw_zip
from backend.spec_doc.raw_zip import (
    RawZipError,
    parse_raw_zip_archive,
    replace_document_xml_raw,
    replace_raw_zip_member,
)
from tests.docx_fidelity_helpers import (
    DOCX_MEDIA_TYPE,
    make_fidelity_master,
)


_DOCUMENT_PART = "word/document.xml"
_LOCAL = struct.Struct("<4s5H3I2H")
_CENTRAL = struct.Struct("<4s6H3I5H2I")
_EOCD = struct.Struct("<4s4H2IH")
_LOCAL_SIGNATURE = b"PK\x03\x04"
_CENTRAL_SIGNATURE = b"PK\x01\x02"
_EOCD_SIGNATURE = b"PK\x05\x06"
_DESCRIPTOR_SIGNATURE = b"PK\x07\x08"
_ARCHIVE_EXTRA_DATA_SIGNATURE = b"PK\x06\x08"
_UTF8_FLAG = 0x0800
_DESCRIPTOR_FLAG = 0x0008
_UNICODE_PATH_EXTRA_ID = 0x7075


@dataclass(frozen=True)
class _EntrySpec:
    name: str
    data: bytes
    method: int = zipfile.ZIP_DEFLATED
    level: int | None = None
    date_time: tuple[int, int, int, int, int, int] = (
        2024,
        5,
        6,
        7,
        8,
        10,
    )
    extra: bytes = b""
    comment: bytes = b""
    internal_attr: int = 0
    external_attr: int = 0o100640 << 16


@dataclass(frozen=True)
class _OracleEntry:
    name: str
    flags: int
    method: int
    stored_local_offset: int
    local_start: int
    local_header_end: int
    compressed_data_end: int
    local_record_end: int
    central_start: int
    central_end: int


@dataclass(frozen=True)
class _OracleArchive:
    entries: tuple[_OracleEntry, ...]
    offset_base: int
    central_start: int
    eocd_start: int
    comment: bytes
    trailing: bytes

    def entry(self, name: str) -> _OracleEntry:
        matches = [entry for entry in self.entries if entry.name == name]
        assert len(matches) == 1, f"Expected exactly one ZIP member {name!r}"
        return matches[0]


class _NonSeekableSink:
    """Minimal output stream that makes ``zipfile`` emit descriptors."""

    def __init__(self) -> None:
        self._buffer = io.BytesIO()

    def write(self, data: bytes) -> int:
        return self._buffer.write(data)

    def tell(self) -> int:
        return self._buffer.tell()

    def seek(self, *_args, **_kwargs):
        raise OSError("fixture output is intentionally non-seekable")

    def flush(self) -> None:
        return None

    def seekable(self) -> bool:
        return False

    def getvalue(self) -> bytes:
        return self._buffer.getvalue()


def _zip_extra(field_id: int, payload: bytes) -> bytes:
    return struct.pack("<HH", field_id, len(payload)) + payload


def _unicode_path_extra(raw_name: bytes, unicode_name: str) -> bytes:
    payload = (
        b"\x01"
        + struct.pack("<I", binascii.crc32(raw_name) & 0xFFFFFFFF)
        + unicode_name.encode("utf-8")
    )
    return _zip_extra(_UNICODE_PATH_EXTRA_ID, payload)


def _zip_info(spec: _EntrySpec) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(spec.name, date_time=spec.date_time)
    info.compress_type = spec.method
    info.create_system = 3
    info.external_attr = spec.external_attr
    info.internal_attr = spec.internal_attr
    info.extra = spec.extra
    info.comment = spec.comment
    return info


def _write_zip(
    specs: tuple[_EntrySpec, ...],
    *,
    archive_comment: bytes = b"",
    descriptors: bool = False,
    force_zip64: frozenset[str] = frozenset(),
) -> bytes:
    sink: io.BytesIO | _NonSeekableSink
    sink = _NonSeekableSink() if descriptors else io.BytesIO()
    with zipfile.ZipFile(sink, "w", allowZip64=True) as archive:
        archive.comment = archive_comment
        for spec in specs:
            info = _zip_info(spec)
            if spec.name in force_zip64:
                # ``force_zip64`` produces a genuine ZIP64 local record even
                # though this deliberately tiny fixture does not need one.
                with archive.open(info, "w", force_zip64=True) as member:
                    member.write(spec.data)
            else:
                archive.writestr(
                    info,
                    spec.data,
                    compress_type=spec.method,
                    compresslevel=spec.level,
                )
    return sink.getvalue()


def _decode_name(raw_name: bytes, flags: int) -> str:
    return raw_name.decode("utf-8" if flags & _UTF8_FLAG else "cp437")


def _oracle(data: bytes) -> _OracleArchive:
    """Parse the classic ZIP records needed by the raw-fidelity assertions."""

    eocd_start = data.rfind(_EOCD_SIGNATURE)
    assert eocd_start >= 0
    eocd = _EOCD.unpack_from(data, eocd_start)
    assert eocd[0] == _EOCD_SIGNATURE
    entry_count = eocd[4]
    central_size = eocd[5]
    stored_central_offset = eocd[6]
    comment_length = eocd[7]
    comment_start = eocd_start + _EOCD.size
    comment_end = comment_start + comment_length
    assert comment_end <= len(data)
    central_start = eocd_start - central_size
    offset_base = central_start - stored_central_offset
    assert offset_base >= 0

    central_rows: list[dict[str, int | str]] = []
    position = central_start
    for _ in range(entry_count):
        fields = _CENTRAL.unpack_from(data, position)
        assert fields[0] == _CENTRAL_SIGNATURE
        name_length, extra_length, entry_comment_length = fields[10:13]
        record_end = (
            position
            + _CENTRAL.size
            + name_length
            + extra_length
            + entry_comment_length
        )
        raw_name = data[
            position + _CENTRAL.size : position + _CENTRAL.size + name_length
        ]
        central_rows.append(
            {
                "name": _decode_name(raw_name, fields[3]),
                "flags": fields[3],
                "method": fields[4],
                "compressed_size": fields[8],
                "stored_local_offset": fields[16],
                "central_start": position,
                "central_end": record_end,
            }
        )
        position = record_end
    assert position == eocd_start

    actual_offsets = sorted(
        offset_base + int(row["stored_local_offset"])
        for row in central_rows
    )
    assert len(actual_offsets) == len(set(actual_offsets))
    boundaries = {
        offset: (
            actual_offsets[index + 1]
            if index + 1 < len(actual_offsets)
            else central_start
        )
        for index, offset in enumerate(actual_offsets)
    }

    entries: list[_OracleEntry] = []
    for row in central_rows:
        local_start = offset_base + int(row["stored_local_offset"])
        local = _LOCAL.unpack_from(data, local_start)
        assert local[0] == _LOCAL_SIGNATURE
        name_length, extra_length = local[9:11]
        local_header_end = (
            local_start + _LOCAL.size + name_length + extra_length
        )
        compressed_data_end = local_header_end + int(row["compressed_size"])
        local_record_end = compressed_data_end
        if int(row["flags"]) & _DESCRIPTOR_FLAG:
            descriptor_size = (
                16
                if data.startswith(_DESCRIPTOR_SIGNATURE, compressed_data_end)
                else 12
            )
            local_record_end += descriptor_size
        assert local_record_end <= boundaries[local_start]
        entries.append(
            _OracleEntry(
                name=str(row["name"]),
                flags=int(row["flags"]),
                method=int(row["method"]),
                stored_local_offset=int(row["stored_local_offset"]),
                local_start=local_start,
                local_header_end=local_header_end,
                compressed_data_end=compressed_data_end,
                local_record_end=local_record_end,
                central_start=int(row["central_start"]),
                central_end=int(row["central_end"]),
            )
        )
    return _OracleArchive(
        entries=tuple(entries),
        offset_base=offset_base,
        central_start=central_start,
        eocd_start=eocd_start,
        comment=data[comment_start:comment_end],
        trailing=data[comment_end:],
    )


def _insert_before_central_directory(data: bytes, payload: bytes) -> bytes:
    before = _oracle(data)
    output = bytearray(
        data[: before.central_start] + payload + data[before.central_start :]
    )
    new_eocd_start = before.eocd_start + len(payload)
    stored_central_offset = _EOCD.unpack_from(output, new_eocd_start)[6]
    struct.pack_into(
        "<I",
        output,
        new_eocd_start + 16,
        stored_central_offset + len(payload),
    )
    return bytes(output)


def _with_zip64_descriptor_prefix(
    data: bytes,
    *,
    name: str,
    signed: bool,
) -> bytes:
    before = _oracle(data)
    entry = before.entry(name)
    assert entry.flags & _DESCRIPTOR_FLAG
    assert data.startswith(_DESCRIPTOR_SIGNATURE, entry.compressed_data_end)
    assert entry.compressed_data_end + 16 == entry.local_record_end
    descriptor = (
        struct.pack("<4sI2Q", _DESCRIPTOR_SIGNATURE, 0, 0, 0)
        if signed
        else struct.pack("<I2Q", 0, 0, 0)
    )
    old_descriptor_end = entry.compressed_data_end + 16
    delta = len(descriptor) - 16
    output = bytearray(
        data[: entry.compressed_data_end]
        + descriptor
        + data[old_descriptor_end:]
    )
    new_eocd_start = before.eocd_start + delta
    stored_central_offset = _EOCD.unpack_from(output, new_eocd_start)[6]
    struct.pack_into(
        "<I",
        output,
        new_eocd_start + 16,
        stored_central_offset + delta,
    )
    return bytes(output)


def _local_record(data: bytes, entry: _OracleEntry) -> bytes:
    return data[entry.local_start : entry.local_record_end]


def _central_record(data: bytes, entry: _OracleEntry) -> bytes:
    return data[entry.central_start : entry.central_end]


def _compressed_data(data: bytes, entry: _OracleEntry) -> bytes:
    return data[entry.local_header_end : entry.compressed_data_end]


def _masked_central(record: bytes, *, mutable: bool) -> bytes:
    masked = bytearray(record)
    masked[42:46] = b"\x00" * 4
    if mutable:
        masked[16:28] = b"\x00" * 12
    return bytes(masked)


def _masked_local_header(data: bytes, entry: _OracleEntry) -> bytes:
    header = bytearray(data[entry.local_start : entry.local_header_end])
    header[14:26] = b"\x00" * 12
    return bytes(header)


def _rich_specs(document_method: int) -> tuple[_EntrySpec, ...]:
    compressible = (
        bytes(range(256)) * 32
        + b"A" * 12_000
        + b"build-a-spec-raw-zip" * 600
    )
    document_level = None if document_method == zipfile.ZIP_STORED else 6
    return (
        _EntrySpec(
            "assets/",
            b"",
            method=zipfile.ZIP_STORED,
            date_time=(1982, 2, 4, 6, 8, 10),
            comment=b"directory-entry-comment",
            external_attr=(0o40775 << 16) | 0x10,
        ),
        _EntrySpec(
            "stored.bin",
            b"STORED-OPAQUE-PAYLOAD\x00\xff",
            method=zipfile.ZIP_STORED,
            date_time=(1994, 8, 12, 14, 16, 18),
            extra=_zip_extra(0xCAFE, b"CENTR"),
            comment=b"stored-entry-comment\x00\xff",
            internal_attr=1,
            external_attr=0o100444 << 16,
        ),
        _EntrySpec(
            _DOCUMENT_PART,
            b"<document><body>original</body></document>",
            method=document_method,
            level=document_level,
            date_time=(2002, 3, 6, 9, 12, 14),
            extra=_zip_extra(0xBEEF, b"DOCMETA"),
            comment=b"document-entry-comment",
            external_attr=0o100600 << 16,
        ),
        _EntrySpec(
            "assets/deflate-fast.bin",
            compressible,
            method=zipfile.ZIP_DEFLATED,
            level=1,
            date_time=(2010, 4, 8, 12, 16, 20),
            comment=b"fast-deflate",
            external_attr=0o100640 << 16,
        ),
        _EntrySpec(
            "assets/deflate-best.bin",
            compressible,
            method=zipfile.ZIP_DEFLATED,
            level=9,
            date_time=(2022, 10, 20, 22, 24, 26),
            comment=b"best-deflate",
            external_attr=0o100604 << 16,
        ),
    )


def _with_distinct_local_extra(data: bytes, name: str = "stored.bin") -> bytes:
    oracle = _oracle(data)
    entry = oracle.entry(name)
    local = _LOCAL.unpack_from(data, entry.local_start)
    name_length, extra_length = local[9:11]
    extra_start = entry.local_start + _LOCAL.size + name_length
    extra = data[extra_start : extra_start + extra_length]
    marker = b"CENTR"
    marker_at = extra.find(marker)
    assert marker_at >= 0
    mutated = bytearray(data)
    start = extra_start + marker_at
    mutated[start : start + len(marker)] = b"LOCAL"
    return bytes(mutated)


def _assert_raw_clone(
    source: bytes,
    output: bytes,
    *,
    replacement: bytes,
) -> None:
    before = _oracle(source)
    after = _oracle(output)
    assert [entry.name for entry in after.entries] == [
        entry.name for entry in before.entries
    ]
    assert len({entry.name for entry in after.entries}) == len(after.entries)
    assert after.offset_base == before.offset_base
    assert source[: min(entry.local_start for entry in before.entries)] == output[
        : min(entry.local_start for entry in after.entries)
    ]
    assert after.comment == before.comment
    assert after.trailing == before.trailing

    after_by_name = {entry.name: entry for entry in after.entries}
    for old in before.entries:
        new = after_by_name[old.name]
        if old.name == _DOCUMENT_PART:
            assert _masked_central(
                _central_record(source, old), mutable=True
            ) == _masked_central(_central_record(output, new), mutable=True)
            assert _masked_local_header(source, old) == _masked_local_header(
                output, new
            )
        else:
            assert _local_record(source, old) == _local_record(output, new)
            assert _masked_central(
                _central_record(source, old), mutable=False
            ) == _masked_central(_central_record(output, new), mutable=False)

    with zipfile.ZipFile(io.BytesIO(output), "r") as archive:
        assert archive.testzip() is None
        assert archive.read(_DOCUMENT_PART) == replacement
        assert [info.filename for info in archive.infolist()] == [
            entry.name for entry in before.entries
        ]


@pytest.mark.parametrize(
    "document_method", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED]
)
def test_raw_clone_preserves_stored_and_deflated_member_records(
    document_method,
):
    source = _with_distinct_local_extra(
        _write_zip(
            _rich_specs(document_method),
            archive_comment=b"raw-clone archive comment\x00\xff",
        )
    )
    before = _oracle(source)
    fast = before.entry("assets/deflate-fast.bin")
    best = before.entry("assets/deflate-best.bin")
    assert _compressed_data(source, fast) != _compressed_data(source, best)

    replacement = b"<document><body>changed &amp; valid</body></document>"
    output = replace_document_xml_raw(source, replacement)
    _assert_raw_clone(source, output, replacement=replacement)
    assert _local_record(
        source, before.entry(_DOCUMENT_PART)
    ) != _local_record(output, _oracle(output).entry(_DOCUMENT_PART))


def test_rich_metadata_extras_comments_attributes_and_directory_survive():
    source = _with_distinct_local_extra(
        _write_zip(
            _rich_specs(zipfile.ZIP_DEFLATED),
            archive_comment=b"client archive metadata",
        )
    )
    replacement = b"<document><body>metadata-preserving edit</body></document>"
    output = replace_raw_zip_member(
        source,
        filename=_DOCUMENT_PART,
        payload=replacement,
    )
    _assert_raw_clone(source, output, replacement=replacement)

    before_oracle = _oracle(source)
    stored = before_oracle.entry("stored.bin")
    local_header = source[stored.local_start : stored.local_header_end]
    central = _central_record(source, stored)
    assert b"LOCAL" in local_header
    assert b"CENTR" not in local_header
    assert b"CENTR" in central
    assert b"LOCAL" not in central

    with zipfile.ZipFile(io.BytesIO(source), "r") as before, zipfile.ZipFile(
        io.BytesIO(output), "r"
    ) as after:
        assert before.comment == after.comment == b"client archive metadata"
        for old, new in zip(before.infolist(), after.infolist()):
            assert new.filename == old.filename
            assert new.date_time == old.date_time
            assert new.comment == old.comment
            assert new.extra == old.extra
            assert new.create_system == old.create_system
            assert new.internal_attr == old.internal_attr
            assert new.external_attr == old.external_attr
        assert before.getinfo("assets/").is_dir()
        assert after.getinfo("assets/").is_dir()


def test_signed_data_descriptors_are_preserved_and_rebuilt():
    source = _write_zip(
        _rich_specs(zipfile.ZIP_DEFLATED),
        descriptors=True,
        archive_comment=b"descriptor archive",
    )
    before = _oracle(source)
    assert all(entry.flags & _DESCRIPTOR_FLAG for entry in before.entries)
    for entry in before.entries:
        assert source.startswith(_DESCRIPTOR_SIGNATURE, entry.compressed_data_end)

    replacement = b"<document><body>descriptor edit</body></document>"
    output = replace_document_xml_raw(source, replacement)
    _assert_raw_clone(source, output, replacement=replacement)
    changed = _oracle(output).entry(_DOCUMENT_PART)
    assert changed.flags & _DESCRIPTOR_FLAG
    assert output.startswith(_DESCRIPTOR_SIGNATURE, changed.compressed_data_end)


@pytest.mark.parametrize("signed", [False, True], ids=["unsigned", "signed"])
def test_zip64_descriptor_that_has_a_classic_prefix_is_rejected(signed):
    source = _write_zip(
        (
            _EntrySpec("before.bin", b"before", method=zipfile.ZIP_STORED),
            _EntrySpec(_DOCUMENT_PART, b"", method=zipfile.ZIP_STORED),
        ),
        descriptors=True,
    )
    source = _with_zip64_descriptor_prefix(
        source,
        name=_DOCUMENT_PART,
        signed=signed,
    )
    # The central directory still makes this readable to consumers that do
    # not inspect the descriptor width.  The raw mutation path must not infer
    # a classic descriptor merely because its prefix happens to match.
    with zipfile.ZipFile(io.BytesIO(source), "r") as archive:
        assert archive.read(_DOCUMENT_PART) == b""
    with pytest.raises(RawZipError, match="ZIP64 data descriptor"):
        parse_raw_zip_archive(source)


def test_preamble_archive_comment_and_eocd_trailing_bytes_are_exact():
    archive = _write_zip(
        _rich_specs(zipfile.ZIP_DEFLATED),
        archive_comment=b"declared EOCD comment",
    )
    preamble = b"MZ\x90\x00BUILD-A-SPEC-PREAMBLE\x00"
    trailing = b"BUILD-A-SPEC-TRAILING-DATA\x00\xff"
    source = preamble + archive + trailing
    parsed = parse_raw_zip_archive(source)
    assert parsed.preamble == preamble
    assert parsed.archive_comment == b"declared EOCD comment"
    assert parsed.trailing_bytes == trailing

    replacement = b"<document><body>envelope edit</body></document>"
    output = replace_document_xml_raw(source, replacement)
    _assert_raw_clone(source, output, replacement=replacement)
    assert output.startswith(preamble)
    assert output.endswith(trailing)


def test_raw_clone_is_deterministic_and_does_not_shadow_document_member():
    source = _write_zip(
        _rich_specs(zipfile.ZIP_DEFLATED),
        archive_comment=b"deterministic",
    )
    replacement = b"<document><body>same deterministic edit</body></document>"
    first = replace_document_xml_raw(source, replacement)
    second = replace_document_xml_raw(source, replacement)
    assert second == first
    with zipfile.ZipFile(io.BytesIO(first), "r") as archive:
        names = archive.namelist()
        assert names.count(_DOCUMENT_PART) == 1
        assert archive.read(_DOCUMENT_PART) == replacement


def test_cached_source_archive_skips_source_reparse_but_still_audits_output(
    monkeypatch,
):
    source = _write_zip(
        _rich_specs(zipfile.ZIP_DEFLATED),
        archive_comment=b"cached source index",
    )
    replacement = b"<document><body>cached archive edit</body></document>"
    real_parse = raw_zip.parse_raw_zip_archive
    parsed_payloads: list[bytes] = []

    def tracked_parse(data: bytes, *, mutable_member: str = _DOCUMENT_PART):
        parsed_payloads.append(data)
        return real_parse(data, mutable_member=mutable_member)

    monkeypatch.setattr(raw_zip, "parse_raw_zip_archive", tracked_parse)

    source_archive = raw_zip.parse_raw_zip_archive(source)
    output = raw_zip.replace_document_xml_raw(
        source,
        replacement,
        source_archive=source_archive,
    )
    assert parsed_payloads == [source, output]

    raw_zip.audit_raw_zip_replacement(
        source,
        output,
        filename=_DOCUMENT_PART,
        expected_payload=replacement,
        source_archive=source_archive,
    )
    assert parsed_payloads == [source, output, output]


def test_cached_source_archive_must_match_exact_source_bytes():
    source = _write_zip(
        _rich_specs(zipfile.ZIP_DEFLATED),
        archive_comment=b"original source",
    )
    stale_archive = parse_raw_zip_archive(source)
    different_source = _write_zip(
        _rich_specs(zipfile.ZIP_DEFLATED),
        archive_comment=b"different source",
    )

    with pytest.raises(RawZipError, match="does not match the source bytes"):
        replace_document_xml_raw(
            different_source,
            b"<document><body>must not apply</body></document>",
            source_archive=stale_archive,
        )


def test_cached_source_archive_rechecks_the_selected_mutable_member():
    source = _write_zip(
        (
            _EntrySpec(
                _DOCUMENT_PART,
                b"<document><body>source</body></document>",
                method=zipfile.ZIP_DEFLATED,
            ),
            _EntrySpec(
                "assets/unsupported.bin",
                b"unrelated bzip payload",
                method=zipfile.ZIP_BZIP2,
                level=9,
            ),
        )
    )
    source_archive = parse_raw_zip_archive(source)

    with pytest.raises(RawZipError, match="unsupported ZIP compression method"):
        replace_raw_zip_member(
            source,
            filename="assets/unsupported.bin",
            payload=b"must not apply",
            source_archive=source_archive,
        )


def test_malformed_local_extra_field_is_rejected():
    source = _write_zip(_rich_specs(zipfile.ZIP_DEFLATED))
    oracle = _oracle(source)
    stored = oracle.entry("stored.bin")
    local = _LOCAL.unpack_from(source, stored.local_start)
    extra_start = stored.local_start + _LOCAL.size + local[9]
    field_id, size = struct.unpack_from("<HH", source, extra_start)
    assert field_id == 0xCAFE and size == len(b"CENTR")
    malformed = bytearray(source)
    struct.pack_into("<H", malformed, extra_start + 2, size + 1)
    with pytest.raises(RawZipError, match="extra field is malformed"):
        parse_raw_zip_archive(bytes(malformed))


def test_matching_unicode_path_extra_is_supported():
    raw_name = _DOCUMENT_PART.encode("ascii")
    source = _write_zip(
        (
            _EntrySpec(
                _DOCUMENT_PART,
                b"<document/>",
                method=zipfile.ZIP_STORED,
                extra=_unicode_path_extra(raw_name, _DOCUMENT_PART),
            ),
        )
    )
    parsed = parse_raw_zip_archive(source)
    assert parsed.entry(_DOCUMENT_PART).filename == _DOCUMENT_PART
    replacement = b"<document><changed/></document>"
    output = replace_document_xml_raw(source, replacement)
    with zipfile.ZipFile(io.BytesIO(output), "r") as archive:
        assert archive.read(_DOCUMENT_PART) == replacement


@pytest.mark.parametrize(
    "alternate_name",
    ["../shadow.xml", "evil/document.xml"],
)
def test_conflicting_unicode_path_alternate_is_rejected(alternate_name):
    raw_name = _DOCUMENT_PART.encode("ascii")
    source = _write_zip(
        (
            _EntrySpec(
                _DOCUMENT_PART,
                b"<document/>",
                method=zipfile.ZIP_STORED,
                extra=_unicode_path_extra(raw_name, alternate_name),
            ),
        )
    )
    with pytest.raises(RawZipError, match="Unicode Path.*conflicts|unsafe path"):
        parse_raw_zip_archive(source)


def test_duplicate_unicode_path_extra_is_rejected():
    raw_name = _DOCUMENT_PART.encode("ascii")
    field = _unicode_path_extra(raw_name, _DOCUMENT_PART)
    source = _write_zip(
        (
            _EntrySpec(
                _DOCUMENT_PART,
                b"<document/>",
                method=zipfile.ZIP_STORED,
                extra=field + field,
            ),
        )
    )
    with pytest.raises(RawZipError, match="duplicate Unicode Path"):
        parse_raw_zip_archive(source)


def test_local_and_central_unicode_path_metadata_must_agree():
    raw_name = _DOCUMENT_PART.encode("ascii")
    source = _write_zip(
        (
            _EntrySpec(
                _DOCUMENT_PART,
                b"<document/>",
                method=zipfile.ZIP_STORED,
                extra=_unicode_path_extra(raw_name, _DOCUMENT_PART),
            ),
        )
    )
    entry = _oracle(source).entry(_DOCUMENT_PART)
    local = _LOCAL.unpack_from(source, entry.local_start)
    local_extra_start = entry.local_start + _LOCAL.size + local[9]
    mutated = bytearray(source)
    struct.pack_into("<H", mutated, local_extra_start, 0xCAFE)
    with pytest.raises(RawZipError, match="local and central Unicode Path"):
        parse_raw_zip_archive(bytes(mutated))


@pytest.mark.parametrize("field_id", range(0x0014, 0x001A))
def test_known_crypto_extra_fields_are_rejected(field_id):
    source = _write_zip(
        (
            _EntrySpec(
                _DOCUMENT_PART,
                b"<document/>",
                method=zipfile.ZIP_STORED,
                extra=_zip_extra(field_id, b"opaque-crypto-metadata"),
            ),
        )
    )
    with pytest.raises(RawZipError, match="unsupported encryption data"):
        parse_raw_zip_archive(source)


@pytest.mark.parametrize(
    "unsafe_name",
    ["../escape.bin", "/absolute.bin", "C:/drive.bin"],
)
def test_public_raw_parser_rejects_unsafe_member_names(unsafe_name):
    source = _write_zip(
        (
            _EntrySpec(unsafe_name, b"unsafe", method=zipfile.ZIP_STORED),
            _EntrySpec(_DOCUMENT_PART, b"<document/>", method=zipfile.ZIP_STORED),
        )
    )
    with pytest.raises(RawZipError, match="unsafe"):
        parse_raw_zip_archive(source)


def test_public_raw_parser_rejects_raw_backslash_member_name():
    safe_name = "folder/escape.bin"
    source = _write_zip(
        (
            _EntrySpec(safe_name, b"unsafe", method=zipfile.ZIP_STORED),
            _EntrySpec(_DOCUMENT_PART, b"<document/>", method=zipfile.ZIP_STORED),
        )
    )
    entry = _oracle(source).entry(safe_name)
    slash_index = safe_name.index("/")
    mutated = bytearray(source)
    mutated[entry.local_start + _LOCAL.size + slash_index] = ord("\\")
    mutated[entry.central_start + _CENTRAL.size + slash_index] = ord("\\")
    with pytest.raises(RawZipError, match="unsafe"):
        parse_raw_zip_archive(bytes(mutated))


def test_archive_extra_data_record_in_gap_is_rejected():
    source = _write_zip(_rich_specs(zipfile.ZIP_DEFLATED))
    source = _insert_before_central_directory(
        source,
        _ARCHIVE_EXTRA_DATA_SIGNATURE + struct.pack("<I", 0),
    )
    with zipfile.ZipFile(io.BytesIO(source), "r") as archive:
        assert archive.read(_DOCUMENT_PART)
    with pytest.raises(RawZipError, match="orphan record"):
        parse_raw_zip_archive(source)


def test_multi_disk_eocd_is_rejected():
    source = _write_zip(_rich_specs(zipfile.ZIP_DEFLATED))
    eocd_start = _oracle(source).eocd_start
    malformed = bytearray(source)
    struct.pack_into("<H", malformed, eocd_start + 4, 1)
    with pytest.raises(RawZipError, match="multi-disk"):
        parse_raw_zip_archive(bytes(malformed))


def test_overlapping_local_header_offsets_are_rejected():
    source = _write_zip(_rich_specs(zipfile.ZIP_DEFLATED))
    oracle = _oracle(source)
    first, second = oracle.entries[:2]
    malformed = bytearray(source)
    struct.pack_into(
        "<I",
        malformed,
        second.central_start + 42,
        first.stored_local_offset,
    )
    with pytest.raises(RawZipError, match="share a local-header offset"):
        parse_raw_zip_archive(bytes(malformed))


def test_duplicate_central_names_are_rejected():
    specs = (
        _EntrySpec("duplicate.bin", b"first", method=zipfile.ZIP_STORED),
        _EntrySpec("duplicate.bin", b"second", method=zipfile.ZIP_STORED),
        _EntrySpec(_DOCUMENT_PART, b"<document/>", method=zipfile.ZIP_STORED),
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        source = _write_zip(specs)
    with pytest.raises(
        RawZipError,
        match="duplicate names|exactly one|metadata disagree|share",
    ):
        parse_raw_zip_archive(source)


def test_zip64_document_layout_is_rejected():
    source = _write_zip(
        _rich_specs(zipfile.ZIP_DEFLATED),
        force_zip64=frozenset({_DOCUMENT_PART}),
    )
    with pytest.raises(RawZipError, match="ZIP64|unsupported ZIP features"):
        parse_raw_zip_archive(source)


def test_replacement_size_equal_to_zip64_sentinel_is_rejected(monkeypatch):
    source = _write_zip(_rich_specs(zipfile.ZIP_STORED))
    entry = parse_raw_zip_archive(source).entry(_DOCUMENT_PART)

    class _SentinelSizedCompressedData:
        def __len__(self) -> int:
            return 0xFFFFFFFF

    monkeypatch.setattr(
        raw_zip,
        "_compressed_replacement",
        lambda _entry, _payload: _SentinelSizedCompressedData(),
    )
    with pytest.raises(RawZipError, match="ZIP64 sizes"):
        raw_zip._rebuilt_local_record(source, entry, b"replacement")


@pytest.mark.parametrize(
    "method, level",
    [
        (zipfile.ZIP_BZIP2, 9),
        (zipfile.ZIP_LZMA, None),
    ],
)
def test_unsupported_document_compression_is_rejected(method, level):
    specs = tuple(
        _EntrySpec(
            spec.name,
            spec.data,
            method=method if spec.name == _DOCUMENT_PART else spec.method,
            level=level if spec.name == _DOCUMENT_PART else spec.level,
            date_time=spec.date_time,
            extra=spec.extra,
            comment=spec.comment,
            internal_attr=spec.internal_attr,
            external_attr=spec.external_attr,
        )
        for spec in _rich_specs(zipfile.ZIP_DEFLATED)
    )
    source = _write_zip(specs)
    with pytest.raises(RawZipError, match="unsupported ZIP compression method"):
        parse_raw_zip_archive(source)


def _repack_document_compression(source: bytes, method: int) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source), "r") as before, zipfile.ZipFile(
        output, "w", allowZip64=True
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


def test_unsupported_document_compression_is_pass_through_only_and_atomic(
    tmp_path,
):
    source = _repack_document_compression(
        make_fidelity_master(tmp_path), zipfile.ZIP_BZIP2
    )
    client = TestClient(create_app())
    imported = client.post(
        "/api/import/master",
        files={
            "file": (
                "bzip2-document.docx",
                source,
                DOCX_MEDIA_TYPE,
            )
        },
    )
    assert imported.status_code == 200, imported.text
    preservation = imported.json()["source_preservation"]
    assert preservation["status"] == "pass_through_only"
    assert preservation["exact_original_available"] is True
    assert {
        item["blocker"] for item in preservation["blockers"]
    } == {"unsupported_raw_zip_layout"}
    assert client.get("/api/export/docx", params={"mode": "source"}).content == source
    assert client.get("/api/import/original").content == source

    before = client.get("/api/doc").json()["doc"]
    rejected = client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "replace",
                    "target_id": "pt1.a1.p1",
                    "text": "Install system per NFPA 13-2022.",
                    "status": "confirmed",
                }
            ]
        },
    )
    assert rejected.status_code == 400
    assert "[unsupported_raw_zip_layout]" in rejected.json()["error"]
    assert client.get("/api/doc").json()["doc"] == before
    assert client.get("/api/export/docx", params={"mode": "source"}).content == source


def test_rebuild_preflight_failure_rejects_edit_but_keeps_exact_original(
    tmp_path,
    monkeypatch,
):
    source = make_fidelity_master(tmp_path)
    client = TestClient(create_app())
    imported = client.post(
        "/api/import/master",
        files={"file": ("rebuild-failure.docx", source, DOCX_MEDIA_TYPE)},
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["source_preservation"]["status"] == "ready"

    # A no-op source download must bypass mutation/rebuild work entirely.
    calls: list[bytes] = []

    def fail_rebuild(
        source_bytes: bytes,
        document_xml: bytes,
        *,
        source_archive=None,
    ) -> bytes:
        assert source_bytes == source
        assert source_archive is not None
        assert b"Transactional raw ZIP preflight failure." in document_xml
        calls.append(document_xml)
        raise RawZipError("synthetic rebuilt-output audit failure")

    monkeypatch.setattr(
        "backend.spec_doc.source_patch.replace_document_xml_raw",
        fail_rebuild,
    )
    assert client.get("/api/export/docx", params={"mode": "source"}).content == source
    assert client.get("/api/import/original").content == source
    assert calls == []

    before = client.get("/api/doc").json()["doc"]
    rejected = client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "replace",
                    "target_id": "pt1.a1.p1",
                    "text": "Transactional raw ZIP preflight failure.",
                    "status": "confirmed",
                }
            ]
        },
    )
    assert rejected.status_code == 400
    assert "[output_validation_failed]" in rejected.json()["error"]
    assert "synthetic rebuilt-output audit failure" in rejected.json()["error"]
    assert len(calls) == 1
    assert client.get("/api/doc").json()["doc"] == before

    # The rejected transaction cannot make the recovery artifact unavailable.
    assert client.get("/api/export/docx", params={"mode": "source"}).content == source
    assert client.get("/api/import/original").content == source
    assert len(calls) == 1
