"""Strict raw-record cloning for source-preserving DOCX mutation.

The ordinary :mod:`zipfile` writer recompresses every member and rebuilds the
whole archive.  This module instead indexes the immutable source bytes,
copies every unchanged local record verbatim, and rebuilds only one supported
member plus central-directory offsets.  Ambiguous or advanced ZIP layouts are
runtime mutation blockers; callers must still retain and return the exact
original bytes for no-op export.
"""
from __future__ import annotations

import binascii
import re
import struct
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass
from io import BytesIO

_LOCAL_SIGNATURE = b"PK\x03\x04"
_CENTRAL_SIGNATURE = b"PK\x01\x02"
_EOCD_SIGNATURE = b"PK\x05\x06"
_DESCRIPTOR_SIGNATURE = b"PK\x07\x08"
_ZIP64_EOCD_SIGNATURE = b"PK\x06\x06"
_ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_ARCHIVE_EXTRA_DATA_SIGNATURE = b"PK\x06\x08"
_DIGITAL_SIGNATURE = b"PK\x05\x05"

_LOCAL_HEADER = struct.Struct("<4s5H3I2H")
_CENTRAL_HEADER = struct.Struct("<4s6H3I5H2I")
_EOCD = struct.Struct("<4s4H2IH")
_DESCRIPTOR = struct.Struct("<3I")
_SIGNED_DESCRIPTOR = struct.Struct("<4s3I")
_ZIP64_DESCRIPTOR = struct.Struct("<I2Q")
_SIGNED_ZIP64_DESCRIPTOR = struct.Struct("<4sI2Q")

_ZIP64_EXTRA_ID = 0x0001
_AES_EXTRA_ID = 0x9901
_UNICODE_PATH_EXTRA_ID = 0x7075
_CRYPTO_EXTRA_IDS = frozenset(range(0x0014, 0x001A)) | frozenset(
    {_AES_EXTRA_ID}
)
_ZIP64_U16 = 0xFFFF
_ZIP64_U32 = 0xFFFFFFFF
_ENCRYPTION_FLAGS = 0x0001 | 0x0040 | 0x2000
_DATA_DESCRIPTOR_FLAG = 0x0008
_UTF8_NAME_FLAG = 0x0800
_DEFLATE_OPTION_FLAGS = 0x0006
_SUPPORTED_DOCUMENT_FLAGS = (
    _DATA_DESCRIPTOR_FLAG | _UTF8_NAME_FLAG | _DEFLATE_OPTION_FLAGS
)
_DOCUMENT_PART = "word/document.xml"
_STRUCTURAL_SIGNATURES = (
    _LOCAL_SIGNATURE,
    _CENTRAL_SIGNATURE,
    _EOCD_SIGNATURE,
    _ZIP64_EOCD_SIGNATURE,
    _ZIP64_LOCATOR_SIGNATURE,
    _ARCHIVE_EXTRA_DATA_SIGNATURE,
    _DIGITAL_SIGNATURE,
    _DESCRIPTOR_SIGNATURE,
)
_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")


class RawZipError(ValueError):
    """The archive layout cannot be mutated without raw-fidelity risk."""

    blocker = "unsupported_raw_zip_layout"

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class ByteSpan:
    start: int
    end: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.start, bool)
            or isinstance(self.end, bool)
            or not isinstance(self.start, int)
            or not isinstance(self.end, int)
            or self.start < 0
            or self.end < self.start
        ):
            raise ValueError("Invalid ZIP byte span.")


@dataclass(frozen=True, slots=True)
class RawZipEntry:
    filename: str
    raw_filename: bytes
    central_index: int
    local_order_index: int
    local_record_span: ByteSpan
    local_header_span: ByteSpan
    compressed_data_span: ByteSpan
    descriptor_span: ByteSpan | None
    gap_after_span: ByteSpan
    central_record_span: ByteSpan
    central_record: bytes
    local_header_offset: int
    stored_local_header_offset: int
    version_needed: int
    compression_method: int
    flags: int
    crc32: int
    compressed_size: int
    uncompressed_size: int
    local_crc32: int
    local_compressed_size: int
    local_uncompressed_size: int
    descriptor_has_signature: bool


@dataclass(frozen=True, slots=True)
class RawZipArchive:
    source_bytes: bytes
    offset_base: int
    preamble: bytes
    entries: tuple[RawZipEntry, ...]
    local_order: tuple[int, ...]
    central_directory_span: ByteSpan
    eocd_span: ByteSpan
    eocd: bytes
    archive_comment: bytes
    trailing_bytes: bytes

    def entry(self, filename: str) -> RawZipEntry:
        matches = [entry for entry in self.entries if entry.filename == filename]
        if len(matches) != 1:
            raise RawZipError(
                f"the archive does not contain exactly one {filename!r} member"
            )
        return matches[0]


@dataclass(frozen=True, slots=True)
class _CentralRecord:
    filename: str
    raw_filename: bytes
    record_span: ByteSpan
    record: bytes
    version_made: int
    version_needed: int
    flags: int
    method: int
    modified_time: int
    modified_date: int
    crc32: int
    compressed_size: int
    uncompressed_size: int
    disk_start: int
    internal_attr: int
    external_attr: int
    stored_local_offset: int
    extra: bytes
    comment: bytes
    unicode_path: str | None


def _unsupported(detail: str) -> RawZipError:
    return RawZipError(detail)


def _decode_filename(raw: bytes, flags: int) -> str:
    try:
        decoded = raw.decode("utf-8" if flags & _UTF8_NAME_FLAG else "cp437")
    except UnicodeDecodeError as exc:
        raise _unsupported("a ZIP member name cannot be decoded safely") from exc
    if not decoded or "\x00" in decoded:
        raise _unsupported("a ZIP member name is empty or contains NUL")
    return decoded


def _safe_member_key(name: str) -> str:
    """Validate one decoded member name and return its collision key."""
    if not name or any(
        ord(character) < 32 or ord(character) == 127
        for character in name
    ):
        raise _unsupported("a ZIP member has an unsafe name")
    if "\\" in name or name.startswith("/") or _DRIVE_PREFIX_RE.match(name):
        raise _unsupported("a ZIP member has an unsafe path")
    path = name[:-1] if name.endswith("/") else name
    parts = path.split("/")
    if not path or any(part in {"", ".", ".."} for part in parts):
        raise _unsupported("a ZIP member has an unsafe path")
    return unicodedata.normalize("NFC", name).casefold()


def _validate_extra(
    extra: bytes,
    *,
    label: str,
    raw_filename: bytes,
    decoded_filename: str,
) -> str | None:
    position = 0
    unicode_path: str | None = None
    while position < len(extra):
        if position + 4 > len(extra):
            raise _unsupported(f"the {label} ZIP extra field is malformed")
        field_id, size = struct.unpack_from("<HH", extra, position)
        position += 4
        if position + size > len(extra):
            raise _unsupported(f"the {label} ZIP extra field is malformed")
        payload = extra[position : position + size]
        if field_id == _ZIP64_EXTRA_ID:
            raise _unsupported(
                f"the {label} ZIP extra field uses unsupported ZIP64 data"
            )
        if field_id in _CRYPTO_EXTRA_IDS:
            raise _unsupported(
                f"the {label} ZIP extra field uses unsupported encryption data"
            )
        if field_id == _UNICODE_PATH_EXTRA_ID:
            if unicode_path is not None:
                raise _unsupported(
                    f"the {label} ZIP extra field contains duplicate Unicode Path data"
                )
            if len(payload) < 6 or payload[0] != 1:
                raise _unsupported(
                    f"the {label} Unicode Path extra field is malformed"
                )
            expected_crc = binascii.crc32(raw_filename) & 0xFFFFFFFF
            if struct.unpack_from("<I", payload, 1)[0] != expected_crc:
                raise _unsupported(
                    f"the {label} Unicode Path extra field has an invalid name CRC"
                )
            try:
                unicode_path = payload[5:].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise _unsupported(
                    f"the {label} Unicode Path extra field is not valid UTF-8"
                ) from exc
            _safe_member_key(unicode_path)
            if unicode_path != decoded_filename:
                raise _unsupported(
                    f"the {label} Unicode Path extra field conflicts with the member name"
                )
        position += size
    return unicode_path


def _contains_structural_signature(payload: bytes) -> bool:
    return any(signature in payload for signature in _STRUCTURAL_SIGNATURES)


def _parse_eocd(
    data: bytes,
    *,
    zip_start_dir: int,
    zip_comment: bytes,
    zip_entry_count: int,
) -> tuple[int, tuple[int, ...], int, bytes, bytes]:
    candidates: list[tuple[int, tuple[int, ...], int, bytes, bytes]] = []
    cursor = len(data)
    while cursor:
        position = data.rfind(_EOCD_SIGNATURE, 0, cursor)
        if position < 0:
            break
        cursor = position
        if position + _EOCD.size > len(data):
            continue
        fields = _EOCD.unpack_from(data, position)
        comment_length = fields[7]
        end = position + _EOCD.size + comment_length
        if end > len(data):
            continue
        disk_number, central_disk, disk_entries, total_entries = fields[1:5]
        central_size, stored_central_offset = fields[5:7]
        comment = data[position + _EOCD.size : end]
        if (
            position != zip_start_dir + central_size
            or comment != zip_comment
            or total_entries != zip_entry_count
        ):
            continue
        if (
            disk_number != 0
            or central_disk != 0
            or disk_entries != total_entries
        ):
            raise _unsupported("multi-disk ZIP archives are not mutable")
        if (
            disk_entries == _ZIP64_U16
            or total_entries == _ZIP64_U16
            or central_size == _ZIP64_U32
            or stored_central_offset == _ZIP64_U32
        ):
            raise _unsupported("ZIP64 archives are not mutable")
        offset_base = zip_start_dir - stored_central_offset
        if offset_base < 0:
            raise _unsupported("the ZIP central-directory offset is inconsistent")
        candidates.append(
            (
                position,
                fields,
                offset_base,
                comment,
                data[end:],
            )
        )
    if len(candidates) != 1:
        raise _unsupported("the ZIP end-of-central-directory record is ambiguous")
    return candidates[0]


def _parse_central_records(
    data: bytes,
    *,
    start: int,
    end: int,
    count: int,
) -> tuple[_CentralRecord, ...]:
    records: list[_CentralRecord] = []
    position = start
    for _index in range(count):
        if position + _CENTRAL_HEADER.size > end:
            raise _unsupported("the ZIP central directory is truncated")
        fields = _CENTRAL_HEADER.unpack_from(data, position)
        if fields[0] != _CENTRAL_SIGNATURE:
            raise _unsupported("the ZIP central directory contains an unknown record")
        (
            _signature,
            version_made,
            version_needed,
            flags,
            method,
            modified_time,
            modified_date,
            crc32,
            compressed_size,
            uncompressed_size,
            name_length,
            extra_length,
            comment_length,
            disk_start,
            internal_attr,
            external_attr,
            stored_local_offset,
        ) = fields
        record_end = (
            position
            + _CENTRAL_HEADER.size
            + name_length
            + extra_length
            + comment_length
        )
        if record_end > end:
            raise _unsupported("a ZIP central-directory record is truncated")
        if (
            compressed_size == _ZIP64_U32
            or uncompressed_size == _ZIP64_U32
            or stored_local_offset == _ZIP64_U32
            or disk_start == _ZIP64_U16
        ):
            raise _unsupported("ZIP64 member records are not mutable")
        if disk_start != 0:
            raise _unsupported("multi-disk ZIP member records are not mutable")
        if flags & _ENCRYPTION_FLAGS or method == 99:
            raise _unsupported("encrypted ZIP members are not mutable")
        variable = position + _CENTRAL_HEADER.size
        raw_filename = data[variable : variable + name_length]
        filename = _decode_filename(raw_filename, flags)
        _safe_member_key(filename)
        extra_start = variable + name_length
        extra = data[extra_start : extra_start + extra_length]
        comment = data[extra_start + extra_length : record_end]
        unicode_path = _validate_extra(
            extra,
            label="central-directory",
            raw_filename=raw_filename,
            decoded_filename=filename,
        )
        records.append(
            _CentralRecord(
                filename=filename,
                raw_filename=raw_filename,
                record_span=ByteSpan(position, record_end),
                record=data[position:record_end],
                version_made=version_made,
                version_needed=version_needed,
                flags=flags,
                method=method,
                modified_time=modified_time,
                modified_date=modified_date,
                crc32=crc32,
                compressed_size=compressed_size,
                uncompressed_size=uncompressed_size,
                disk_start=disk_start,
                internal_attr=internal_attr,
                external_attr=external_attr,
                stored_local_offset=stored_local_offset,
                extra=extra,
                comment=comment,
                unicode_path=unicode_path,
            )
        )
        position = record_end
    if position != end:
        raise _unsupported("the ZIP central directory has unclaimed records")
    return tuple(records)


def _descriptor_span(
    data: bytes,
    *,
    data_end: int,
    boundary: int,
    central: _CentralRecord,
) -> tuple[ByteSpan, bool]:
    # A ZIP64 descriptor can share an exact classic-descriptor prefix (most
    # notably for an empty member).  Check the wider forms first so their
    # remaining size words cannot be misclassified as an inert archive gap.
    if data_end + _ZIP64_DESCRIPTOR.size <= boundary:
        values = _ZIP64_DESCRIPTOR.unpack_from(data, data_end)
        if values == (
            central.crc32,
            central.compressed_size,
            central.uncompressed_size,
        ):
            raise _unsupported("a ZIP64 data descriptor is not mutable")
    if data_end + _SIGNED_ZIP64_DESCRIPTOR.size <= boundary:
        values = _SIGNED_ZIP64_DESCRIPTOR.unpack_from(data, data_end)
        if values == (
            _DESCRIPTOR_SIGNATURE,
            central.crc32,
            central.compressed_size,
            central.uncompressed_size,
        ):
            raise _unsupported("a ZIP64 data descriptor is not mutable")

    matches: list[tuple[int, bool]] = []
    if data_end + _DESCRIPTOR.size <= boundary:
        values = _DESCRIPTOR.unpack_from(data, data_end)
        if values == (
            central.crc32,
            central.compressed_size,
            central.uncompressed_size,
        ):
            matches.append((_DESCRIPTOR.size, False))
    if data_end + _SIGNED_DESCRIPTOR.size <= boundary:
        values = _SIGNED_DESCRIPTOR.unpack_from(data, data_end)
        if values == (
            _DESCRIPTOR_SIGNATURE,
            central.crc32,
            central.compressed_size,
            central.uncompressed_size,
        ):
            matches.append((_SIGNED_DESCRIPTOR.size, True))
    if len(matches) != 1:
        raise _unsupported("a ZIP data descriptor is missing or ambiguous")
    size, has_signature = matches[0]
    return ByteSpan(data_end, data_end + size), has_signature


def _parse_local_entry(
    data: bytes,
    *,
    central: _CentralRecord,
    central_index: int,
    local_order_index: int,
    offset_base: int,
    boundary: int,
) -> RawZipEntry:
    local_offset = offset_base + central.stored_local_offset
    if local_offset < 0 or local_offset + _LOCAL_HEADER.size > boundary:
        raise _unsupported("a ZIP local-header offset is out of range")
    fields = _LOCAL_HEADER.unpack_from(data, local_offset)
    if fields[0] != _LOCAL_SIGNATURE:
        raise _unsupported("a ZIP member does not begin with a local header")
    (
        _signature,
        version_needed,
        flags,
        method,
        modified_time,
        modified_date,
        local_crc32,
        local_compressed_size,
        local_uncompressed_size,
        name_length,
        extra_length,
    ) = fields
    header_end = local_offset + _LOCAL_HEADER.size + name_length + extra_length
    if header_end > boundary:
        raise _unsupported("a ZIP local header is truncated")
    raw_filename = data[
        local_offset + _LOCAL_HEADER.size :
        local_offset + _LOCAL_HEADER.size + name_length
    ]
    extra = data[
        local_offset + _LOCAL_HEADER.size + name_length : header_end
    ]
    local_unicode_path = _validate_extra(
        extra,
        label="local-header",
        raw_filename=raw_filename,
        decoded_filename=central.filename,
    )
    if (
        version_needed != central.version_needed
        or flags != central.flags
        or method != central.method
        or modified_time != central.modified_time
        or modified_date != central.modified_date
        or raw_filename != central.raw_filename
    ):
        raise _unsupported("local and central ZIP member metadata disagree")
    if local_unicode_path != central.unicode_path:
        raise _unsupported("local and central Unicode Path metadata disagree")
    if flags & _ENCRYPTION_FLAGS or method == 99:
        raise _unsupported("encrypted ZIP members are not mutable")

    data_end = header_end + central.compressed_size
    if data_end > boundary:
        raise _unsupported("ZIP member data overlaps the next archive record")
    descriptor_span: ByteSpan | None = None
    descriptor_has_signature = False
    if flags & _DATA_DESCRIPTOR_FLAG:
        if (
            local_crc32,
            local_compressed_size,
            local_uncompressed_size,
        ) not in {
            (0, 0, 0),
            (
                central.crc32,
                central.compressed_size,
                central.uncompressed_size,
            ),
        }:
            raise _unsupported("ZIP descriptor and local size fields disagree")
        descriptor_span, descriptor_has_signature = _descriptor_span(
            data,
            data_end=data_end,
            boundary=boundary,
            central=central,
        )
        record_end = descriptor_span.end
    else:
        if (
            local_crc32,
            local_compressed_size,
            local_uncompressed_size,
        ) != (
            central.crc32,
            central.compressed_size,
            central.uncompressed_size,
        ):
            raise _unsupported("local and central ZIP sizes or CRC disagree")
        record_end = data_end

    gap = data[record_end:boundary]
    if _contains_structural_signature(gap):
        raise _unsupported("an inter-member ZIP gap contains an orphan record")
    return RawZipEntry(
        filename=central.filename,
        raw_filename=central.raw_filename,
        central_index=central_index,
        local_order_index=local_order_index,
        local_record_span=ByteSpan(local_offset, record_end),
        local_header_span=ByteSpan(local_offset, header_end),
        compressed_data_span=ByteSpan(header_end, data_end),
        descriptor_span=descriptor_span,
        gap_after_span=ByteSpan(record_end, boundary),
        central_record_span=central.record_span,
        central_record=central.record,
        local_header_offset=local_offset,
        stored_local_header_offset=central.stored_local_offset,
        version_needed=central.version_needed,
        compression_method=central.method,
        flags=central.flags,
        crc32=central.crc32,
        compressed_size=central.compressed_size,
        uncompressed_size=central.uncompressed_size,
        local_crc32=local_crc32,
        local_compressed_size=local_compressed_size,
        local_uncompressed_size=local_uncompressed_size,
        descriptor_has_signature=descriptor_has_signature,
    )


def _cross_check_zipfile(
    archive: zipfile.ZipFile,
    infos: list[zipfile.ZipInfo],
    entries: tuple[RawZipEntry, ...],
    *,
    central_start: int,
) -> None:
    if archive.start_dir != central_start or len(infos) != len(entries):
        raise _unsupported("zipfile and the raw ZIP index disagree")
    for info, entry in zip(infos, entries):
        central_fields = _CENTRAL_HEADER.unpack_from(entry.central_record)
        name_length = central_fields[10]
        extra_length = central_fields[11]
        comment_length = central_fields[12]
        extra_start = _CENTRAL_HEADER.size + name_length
        central_extra = entry.central_record[
            extra_start : extra_start + extra_length
        ]
        central_comment = entry.central_record[
            extra_start + extra_length :
            extra_start + extra_length + comment_length
        ]
        version_made = central_fields[1]
        version_needed = central_fields[2]
        if (
            info.filename != entry.filename
            or info.header_offset != entry.local_header_offset
            or info.flag_bits != entry.flags
            or info.compress_type != entry.compression_method
            or info.CRC != entry.crc32
            or info.compress_size != entry.compressed_size
            or info.file_size != entry.uncompressed_size
            or info.extra != central_extra
            or info.comment != central_comment
            or (info.create_version | (info.create_system << 8)) != version_made
            or (info.extract_version | (info.reserved << 8)) != version_needed
            or info.volume != central_fields[13]
            or info.internal_attr != central_fields[14]
            or info.external_attr != central_fields[15]
        ):
            raise _unsupported("zipfile and raw ZIP member metadata disagree")


def parse_raw_zip_archive(
    data: bytes,
    *,
    mutable_member: str = _DOCUMENT_PART,
) -> RawZipArchive:
    """Return a strict immutable index for a raw-preserving mutation."""
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    try:
        archive = zipfile.ZipFile(BytesIO(data), "r")
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise _unsupported("the source is not a readable ZIP archive") from exc
    try:
        infos = archive.infolist()
        eocd_start, eocd_fields, offset_base, comment, trailing = _parse_eocd(
            data,
            zip_start_dir=archive.start_dir,
            zip_comment=archive.comment,
            zip_entry_count=len(infos),
        )
        central_size = eocd_fields[5]
        central_start = eocd_start - central_size
        central_records = _parse_central_records(
            data,
            start=central_start,
            end=eocd_start,
            count=eocd_fields[4],
        )
        if len(central_records) != len(infos):
            raise _unsupported("the ZIP member inventory is inconsistent")
        normalized_names = [
            _safe_member_key(record.filename) for record in central_records
        ]
        if len(set(normalized_names)) != len(normalized_names):
            raise _unsupported("the ZIP central directory contains duplicate names")

        physical = sorted(
            range(len(central_records)),
            key=lambda index: offset_base
            + central_records[index].stored_local_offset,
        )
        physical_offsets = [
            offset_base + central_records[index].stored_local_offset
            for index in physical
        ]
        if len(set(physical_offsets)) != len(physical_offsets):
            raise _unsupported("ZIP members share a local-header offset")
        first_local_offset = physical_offsets[0] if physical_offsets else central_start
        if offset_base not in {0, first_local_offset}:
            raise _unsupported("the ZIP preamble offset convention is ambiguous")
        if not 0 <= first_local_offset <= central_start:
            raise _unsupported("the ZIP preamble or local records are out of range")
        preamble = data[:first_local_offset]
        if _contains_structural_signature(preamble):
            raise _unsupported("the ZIP preamble contains an orphan archive record")

        by_central: list[RawZipEntry | None] = [None] * len(central_records)
        for local_order_index, central_index in enumerate(physical):
            boundary = (
                physical_offsets[local_order_index + 1]
                if local_order_index + 1 < len(physical_offsets)
                else central_start
            )
            by_central[central_index] = _parse_local_entry(
                data,
                central=central_records[central_index],
                central_index=central_index,
                local_order_index=local_order_index,
                offset_base=offset_base,
                boundary=boundary,
            )
        if any(entry is None for entry in by_central):  # pragma: no cover
            raise _unsupported("a ZIP member has no local record")
        entries = tuple(entry for entry in by_central if entry is not None)
        _cross_check_zipfile(
            archive,
            infos,
            entries,
            central_start=central_start,
        )
    except (IndexError, OverflowError, struct.error) as exc:
        raise _unsupported("the raw ZIP layout is malformed") from exc
    finally:
        archive.close()

    if _contains_structural_signature(trailing):
        raise _unsupported("trailing ZIP data contains an orphan archive record")
    result = RawZipArchive(
        source_bytes=data,
        offset_base=offset_base,
        preamble=preamble,
        entries=entries,
        local_order=tuple(physical),
        central_directory_span=ByteSpan(central_start, eocd_start),
        eocd_span=ByteSpan(eocd_start, eocd_start + _EOCD.size + len(comment)),
        eocd=data[eocd_start : eocd_start + _EOCD.size],
        archive_comment=comment,
        trailing_bytes=trailing,
    )
    target = result.entry(mutable_member)
    if target.compression_method not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
        raise _unsupported(
            f"{mutable_member} uses an unsupported ZIP compression method"
        )
    if target.flags & ~_SUPPORTED_DOCUMENT_FLAGS:
        raise _unsupported(f"{mutable_member} uses unsupported ZIP flags")
    if (
        target.compression_method == zipfile.ZIP_STORED
        and target.flags & _DEFLATE_OPTION_FLAGS
    ):
        raise _unsupported(f"a stored {mutable_member} has deflate-only flags")
    if target.version_needed > 20:
        raise _unsupported(f"{mutable_member} requires unsupported ZIP features")
    return result


def _compressed_replacement(entry: RawZipEntry, payload: bytes) -> bytes:
    if entry.compression_method == zipfile.ZIP_STORED:
        return payload
    option = entry.flags & _DEFLATE_OPTION_FLAGS
    level = {0x0002: 9, 0x0004: 3, 0x0006: 1}.get(
        option, zlib.Z_DEFAULT_COMPRESSION
    )
    compressor = zlib.compressobj(level, zlib.DEFLATED, -15)
    return compressor.compress(payload) + compressor.flush()


def _rebuilt_local_record(
    source: bytes,
    entry: RawZipEntry,
    payload: bytes,
) -> tuple[bytes, int, int]:
    compressed = _compressed_replacement(entry, payload)
    crc32 = binascii.crc32(payload) & 0xFFFFFFFF
    if len(payload) >= _ZIP64_U32 or len(compressed) >= _ZIP64_U32:
        raise _unsupported("the replacement would require ZIP64 sizes")
    header = bytearray(source[entry.local_header_span.start : entry.local_header_span.end])
    if entry.flags & _DATA_DESCRIPTOR_FLAG:
        if (
            entry.local_crc32,
            entry.local_compressed_size,
            entry.local_uncompressed_size,
        ) == (0, 0, 0):
            struct.pack_into("<3I", header, 14, 0, 0, 0)
        else:
            struct.pack_into(
                "<3I", header, 14, crc32, len(compressed), len(payload)
            )
        descriptor = (
            _SIGNED_DESCRIPTOR.pack(
                _DESCRIPTOR_SIGNATURE, crc32, len(compressed), len(payload)
            )
            if entry.descriptor_has_signature
            else _DESCRIPTOR.pack(crc32, len(compressed), len(payload))
        )
    else:
        struct.pack_into("<3I", header, 14, crc32, len(compressed), len(payload))
        descriptor = b""
    return bytes(header) + compressed + descriptor, crc32, len(compressed)


def _masked_record(record: bytes, spans: tuple[tuple[int, int], ...]) -> bytes:
    result = bytearray(record)
    for start, end in spans:
        result[start:end] = b"\x00" * (end - start)
    return bytes(result)


def _audit_raw_rebuild(
    source: RawZipArchive,
    output_bytes: bytes,
    *,
    mutable_member: str,
    expected_payload: bytes,
) -> None:
    output = parse_raw_zip_archive(output_bytes, mutable_member=mutable_member)
    if (
        [entry.filename for entry in output.entries]
        != [entry.filename for entry in source.entries]
        or output.local_order != source.local_order
        or output.preamble != source.preamble
        or output.archive_comment != source.archive_comment
        or output.trailing_bytes != source.trailing_bytes
        or output.offset_base != source.offset_base
        or _masked_record(output.eocd, ((12, 20),))
        != _masked_record(source.eocd, ((12, 20),))
    ):
        raise _unsupported("the rebuilt ZIP archive layout changed unexpectedly")

    output_by_name = {entry.filename: entry for entry in output.entries}
    for before in source.entries:
        after = output_by_name[before.filename]
        before_gap = source.source_bytes[
            before.gap_after_span.start : before.gap_after_span.end
        ]
        after_gap = output_bytes[after.gap_after_span.start : after.gap_after_span.end]
        if before_gap != after_gap:
            raise _unsupported("an inter-member ZIP gap changed")
        if before.filename != mutable_member:
            before_local = source.source_bytes[
                before.local_record_span.start : before.local_record_span.end
            ]
            after_local = output_bytes[
                after.local_record_span.start : after.local_record_span.end
            ]
            if before_local != after_local:
                raise _unsupported("an unchanged ZIP local record changed")
            central_mask = ((42, 46),)
        else:
            central_mask = ((16, 28), (42, 46))
            before_header = source.source_bytes[
                before.local_header_span.start : before.local_header_span.end
            ]
            after_header = output_bytes[
                after.local_header_span.start : after.local_header_span.end
            ]
            if (
                _masked_record(before_header, ((14, 26),))
                != _masked_record(after_header, ((14, 26),))
                or before.descriptor_has_signature
                != after.descriptor_has_signature
                or (before.descriptor_span is None)
                != (after.descriptor_span is None)
            ):
                raise _unsupported("the rebuilt member header shape changed")
        if _masked_record(before.central_record, central_mask) != _masked_record(
            after.central_record, central_mask
        ):
            raise _unsupported("ZIP central-directory metadata changed unexpectedly")
    try:
        with zipfile.ZipFile(BytesIO(output_bytes), "r") as archive:
            if archive.read(mutable_member) != expected_payload:
                raise _unsupported("the rebuilt ZIP member payload is incorrect")
    except (KeyError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as exc:
        raise _unsupported("the rebuilt ZIP archive failed validation") from exc


def replace_raw_zip_member(
    source_bytes: bytes,
    *,
    filename: str,
    payload: bytes,
) -> bytes:
    """Replace one supported member while preserving every other raw record."""
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    source = parse_raw_zip_archive(source_bytes, mutable_member=filename)
    target = source.entry(filename)
    rebuilt_target, target_crc, target_compressed_size = _rebuilt_local_record(
        source_bytes, target, payload
    )

    output = bytearray(source.preamble)
    new_offsets: dict[int, int] = {}
    for central_index in source.local_order:
        entry = source.entries[central_index]
        new_offsets[central_index] = len(output)
        if entry.filename == filename:
            output.extend(rebuilt_target)
        else:
            output.extend(
                source_bytes[entry.local_record_span.start : entry.local_record_span.end]
            )
        output.extend(
            source_bytes[entry.gap_after_span.start : entry.gap_after_span.end]
        )

    central_start = len(output)
    for entry in source.entries:
        record = bytearray(entry.central_record)
        relative_offset = new_offsets[entry.central_index] - source.offset_base
        if not 0 <= relative_offset <= _ZIP64_U32 - 1:
            raise _unsupported("the rebuilt ZIP offsets would require ZIP64")
        struct.pack_into("<I", record, 42, relative_offset)
        if entry.filename == filename:
            struct.pack_into(
                "<3I",
                record,
                16,
                target_crc,
                target_compressed_size,
                len(payload),
            )
        output.extend(record)
    central_size = len(output) - central_start
    stored_central_offset = central_start - source.offset_base
    if (
        central_size > _ZIP64_U32 - 1
        or not 0 <= stored_central_offset <= _ZIP64_U32 - 1
    ):
        raise _unsupported("the rebuilt central directory would require ZIP64")

    eocd = bytearray(source.eocd)
    struct.pack_into("<II", eocd, 12, central_size, stored_central_offset)
    output.extend(eocd)
    output.extend(source.archive_comment)
    output.extend(source.trailing_bytes)
    result = bytes(output)
    _audit_raw_rebuild(
        source,
        result,
        mutable_member=filename,
        expected_payload=payload,
    )
    return result


def audit_raw_zip_replacement(
    source_bytes: bytes,
    output_bytes: bytes,
    *,
    filename: str,
    expected_payload: bytes,
) -> None:
    """Independently prove one rebuilt member is the only raw ZIP change."""
    source = parse_raw_zip_archive(source_bytes, mutable_member=filename)
    _audit_raw_rebuild(
        source,
        output_bytes,
        mutable_member=filename,
        expected_payload=expected_payload,
    )


def replace_document_xml_raw(source_bytes: bytes, document_xml: bytes) -> bytes:
    return replace_raw_zip_member(
        source_bytes,
        filename=_DOCUMENT_PART,
        payload=document_xml,
    )


__all__ = [
    "audit_raw_zip_replacement",
    "ByteSpan",
    "RawZipArchive",
    "RawZipEntry",
    "RawZipError",
    "parse_raw_zip_archive",
    "replace_document_xml_raw",
    "replace_raw_zip_member",
]
