"""Bounded upload handling and defensive inspection for imported DOCX files.

P0 deliberately keeps the imported package only as an active-session recovery
artifact; it does not yet use that package as the export base.  This module is
the single boundary in front of ``python-docx`` so malformed or hostile ZIP
containers are rejected before the OOXML parser sees them.
"""
from __future__ import annotations

import hashlib
import re
import stat
import struct
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from io import BytesIO
from typing import Any

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_ZIP_MEMBERS = 5_000
MAX_ZIP_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
MAX_ZIP_MEMBER_BYTES = 100 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024

_ZIP_READ_CHUNK_BYTES = 1024 * 1024
_LOCAL_FILE_HEADER_SIZE = 30
_LOCAL_FILE_HEADER_SIGNATURE = b"PK\x03\x04"
_ENCRYPTED_FLAG = 0x1
_REQUIRED_OPC_PARTS = frozenset(
    {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
)
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")
_OFFICE_DOCUMENT_REL_TYPES = frozenset(
    {
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
        "http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument",
    }
)

FIDELITY_NOTICE = (
    "Build-a-Spec currently imports normalized body content, not the original "
    "Word document's formatting. Exports are newly generated DOCX files and "
    "do not yet preserve existing headers, footers, styles, numbering, table "
    "structure, or other unsupported Word features. Keep the original master "
    "for reference."
)


class SourcePackageError(ValueError):
    """The upload is not a safe, structurally usable DOCX package."""


class UploadTooLargeError(SourcePackageError):
    """The compressed upload exceeded the bounded-read limit."""


@dataclass(frozen=True)
class DocxPackageInfo:
    """Safe package metrics recorded in the import report."""

    member_count: int
    uncompressed_bytes: int


async def read_upload_bounded(
    upload,
    *,
    max_bytes: int = MAX_UPLOAD_BYTES,
    chunk_bytes: int = UPLOAD_CHUNK_BYTES,
) -> bytes:
    """Read an async upload without ever accepting more than ``max_bytes``.

    One extra byte is requested after an exactly-at-limit upload so an
    oversized stream cannot masquerade as a valid prefix.  The keyword limits
    are injectable to keep boundary behavior cheap to test.
    """
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    if (
        isinstance(chunk_bytes, bool)
        or not isinstance(chunk_bytes, int)
        or chunk_bytes < 1
    ):
        raise ValueError("chunk_bytes must be a positive integer")

    payload = bytearray()
    while len(payload) <= max_bytes:
        request_size = min(chunk_bytes, max_bytes + 1 - len(payload))
        chunk = await upload.read(request_size)
        if not chunk:
            return bytes(payload)
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise SourcePackageError("The uploaded file could not be read.")
        payload.extend(chunk)
        if len(payload) > max_bytes:
            break
    raise UploadTooLargeError(
        f"The DOCX upload is too large (maximum {max_bytes // (1024 * 1024)} MiB)."
    )


def sanitize_source_filename(value: Any) -> str:
    """Return a path-free, header-safe display filename for retained bytes."""
    if not isinstance(value, str):
        value = ""
    # Browsers normally send only a basename, but older clients can send a
    # full Windows or POSIX path.  Treat both separators identically.
    name = value.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch >= " " and ch != "\x7f").strip()
    name = name.strip(". ")
    if not name:
        return "imported-master.docx"
    # Keep Unicode names for the RFC 5987 download header, but bound metadata
    # stored in memory/project JSON.  Preserve the extension when truncating.
    if len(name) > 240:
        suffix = ".docx" if name.lower().endswith(".docx") else ""
        name = name[: 240 - len(suffix)].rstrip(". ") + suffix
    if not name.lower().endswith(".docx"):
        name += ".docx"
    return name


def _validated_limit(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _safe_member_key(name: str) -> str:
    """Validate one archive name and return its collision-detection key."""
    if not name:
        raise SourcePackageError("The DOCX package contains an empty member name.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise SourcePackageError(
            "The DOCX package contains an unsafe member name."
        )
    if "\\" in name or name.startswith("/") or _DRIVE_PREFIX_RE.match(name):
        raise SourcePackageError(
            "The DOCX package contains an unsafe member path."
        )

    is_directory = name.endswith("/")
    path = name[:-1] if is_directory else name
    parts = path.split("/")
    if not path or any(part in {"", ".", ".."} for part in parts):
        raise SourcePackageError(
            "The DOCX package contains an unsafe member path."
        )

    # OPC part names are URI-like.  Normalizing for the duplicate check also
    # prevents two names that collide on common case-insensitive filesystems.
    normalized = unicodedata.normalize("NFC", name).casefold()
    return normalized


def _local_header_flags(data: bytes, info: zipfile.ZipInfo) -> int:
    """Read the local-header flags (ZipInfo exposes central-directory flags)."""
    offset = info.header_offset
    end = offset + _LOCAL_FILE_HEADER_SIZE
    if offset < 0 or end > len(data):
        raise SourcePackageError("The DOCX package has a malformed ZIP header.")
    header = data[offset:end]
    if header[:4] != _LOCAL_FILE_HEADER_SIGNATURE:
        raise SourcePackageError("The DOCX package has a malformed ZIP header.")
    return struct.unpack_from("<H", header, 6)[0]


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _validate_required_opc_xml(archive: zipfile.ZipFile) -> None:
    """Check required OPC XML is well-formed and wired to the main part."""
    try:
        content_types = ET.fromstring(archive.read("[Content_Types].xml"))
        relationships = ET.fromstring(archive.read("_rels/.rels"))
        document = ET.fromstring(archive.read("word/document.xml"))
    except (ET.ParseError, UnicodeError, zipfile.BadZipFile, RuntimeError) as exc:
        raise SourcePackageError(
            "The DOCX package contains malformed required Word XML."
        ) from exc

    if _xml_local_name(content_types.tag) != "Types":
        raise SourcePackageError(
            "The DOCX package has an invalid [Content_Types].xml part."
        )
    has_document_override = any(
        _xml_local_name(child.tag) == "Override"
        and child.attrib.get("PartName", "").lstrip("/") == "word/document.xml"
        for child in content_types
    )
    if not has_document_override:
        raise SourcePackageError(
            "The DOCX package does not declare its main Word document part."
        )

    if _xml_local_name(relationships.tag) != "Relationships":
        raise SourcePackageError(
            "The DOCX package has an invalid root relationships part."
        )
    has_main_relationship = any(
        _xml_local_name(child.tag) == "Relationship"
        and child.attrib.get("Type") in _OFFICE_DOCUMENT_REL_TYPES
        and child.attrib.get("Target", "").lstrip("/") == "word/document.xml"
        and child.attrib.get("TargetMode", "Internal") != "External"
        for child in relationships
    )
    if not has_main_relationship:
        raise SourcePackageError(
            "The DOCX package has no valid relationship to word/document.xml."
        )

    if _xml_local_name(document.tag) != "document":
        raise SourcePackageError(
            "The DOCX package has an invalid main Word document part."
        )


def inspect_docx_package(
    data: bytes,
    *,
    max_members: int = MAX_ZIP_MEMBERS,
    max_total_uncompressed: int = MAX_ZIP_UNCOMPRESSED_BYTES,
    max_member_uncompressed: int = MAX_ZIP_MEMBER_BYTES,
) -> DocxPackageInfo:
    """Validate ZIP/OPC safety limits and force a CRC check of every member.

    The compressed bytes have already passed :func:`read_upload_bounded`.
    Declared sizes are checked before decompression; streamed actual sizes are
    checked again while every member is read to EOF, which also makes
    ``zipfile`` verify each member CRC.
    """
    max_members = _validated_limit(max_members, "max_members")
    max_total_uncompressed = _validated_limit(
        max_total_uncompressed, "max_total_uncompressed"
    )
    max_member_uncompressed = _validated_limit(
        max_member_uncompressed, "max_member_uncompressed"
    )
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")

    try:
        archive = zipfile.ZipFile(BytesIO(data), "r")
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise SourcePackageError(
            "That file is not a readable .docx document."
        ) from exc

    try:
        infos = archive.infolist()
        if len(infos) > max_members:
            raise SourcePackageError(
                f"The DOCX package has too many ZIP members (maximum {max_members})."
            )

        names: set[str] = set()
        exact_names: set[str] = set()
        declared_total = 0
        for info in infos:
            key = _safe_member_key(info.filename)
            if key in names:
                raise SourcePackageError(
                    "The DOCX package contains duplicate member names."
                )
            names.add(key)
            exact_names.add(info.filename)

            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if unix_mode and stat.S_ISLNK(unix_mode):
                raise SourcePackageError(
                    "The DOCX package contains an unsafe symbolic-link member."
                )
            if (
                info.flag_bits & _ENCRYPTED_FLAG
                or _local_header_flags(data, info) & _ENCRYPTED_FLAG
            ):
                raise SourcePackageError(
                    "Encrypted DOCX package members are not supported."
                )
            if info.file_size < 0 or info.file_size > max_member_uncompressed:
                raise SourcePackageError(
                    "A DOCX package member exceeds the uncompressed size limit "
                    f"of {max_member_uncompressed // (1024 * 1024)} MiB."
                )
            declared_total += info.file_size
            if declared_total > max_total_uncompressed:
                raise SourcePackageError(
                    "The DOCX package exceeds the total uncompressed size limit "
                    f"of {max_total_uncompressed // (1024 * 1024)} MiB."
                )

        missing = sorted(_REQUIRED_OPC_PARTS - exact_names)
        if missing:
            raise SourcePackageError(
                "The DOCX package is missing required Word parts: "
                + ", ".join(missing)
                + "."
            )

        actual_total = 0
        for info in infos:
            member_total = 0
            try:
                with archive.open(info, "r") as member:
                    while True:
                        chunk = member.read(_ZIP_READ_CHUNK_BYTES)
                        if not chunk:
                            break
                        member_total += len(chunk)
                        actual_total += len(chunk)
                        if member_total > max_member_uncompressed:
                            raise SourcePackageError(
                                "A DOCX package member exceeds the uncompressed "
                                "size limit."
                            )
                        if actual_total > max_total_uncompressed:
                            raise SourcePackageError(
                                "The DOCX package exceeds the total uncompressed "
                                "size limit."
                            )
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
                raise SourcePackageError(
                    "The DOCX package failed ZIP integrity validation."
                ) from exc
            if member_total != info.file_size:
                raise SourcePackageError(
                    "The DOCX package contains inconsistent member sizes."
                )

        _validate_required_opc_xml(archive)

        return DocxPackageInfo(
            member_count=len(infos), uncompressed_bytes=actual_total
        )
    finally:
        archive.close()


def build_import_report(
    *,
    filename: str,
    source_bytes: bytes,
    package_info: DocxPackageInfo,
    imported_block_count: int,
    skipped_empty_count: int,
    warnings: list[str],
    tracked_changes_detected: bool,
) -> dict[str, Any]:
    """Build the whitelist-only report persisted with a P0 project."""
    report = {
        "filename": sanitize_source_filename(filename),
        "sha256": hashlib.sha256(source_bytes).hexdigest(),
        "size_bytes": len(source_bytes),
        "zip_member_count": package_info.member_count,
        "zip_uncompressed_bytes": package_info.uncompressed_bytes,
        "imported_block_count": imported_block_count,
        "skipped_empty_count": skipped_empty_count,
        "warnings": list(warnings),
        "tracked_changes_detected": bool(tracked_changes_detected),
        "fidelity_notice": FIDELITY_NOTICE,
    }
    # Route application-created metadata through the same caps/whitelist as
    # project-loaded metadata.  Warnings originate in document content and
    # should not become an unbounded JSON/API surface.
    sanitized = sanitize_import_report(report)
    if sanitized is None:  # pragma: no cover - internal invariant
        raise ValueError("Could not build a valid import report.")
    return sanitized


def _report_int(value: Any, *, maximum: int) -> int | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > maximum
    ):
        return None
    return value


def sanitize_import_report(value: Any) -> dict[str, Any] | None:
    """Restore only trusted-shaped import metadata from project JSON.

    Optional metadata must never make an otherwise valid legacy project fail
    to load.  A malformed report therefore degrades to ``None``; unknown keys
    (including forged source bytes or ``source_available``) are dropped.
    """
    if not isinstance(value, dict):
        return None
    sha256 = value.get("sha256")
    if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
        return None

    size_bytes = _report_int(value.get("size_bytes"), maximum=MAX_UPLOAD_BYTES)
    member_count = _report_int(
        value.get("zip_member_count"), maximum=MAX_ZIP_MEMBERS
    )
    uncompressed = _report_int(
        value.get("zip_uncompressed_bytes"),
        maximum=MAX_ZIP_UNCOMPRESSED_BYTES,
    )
    imported = _report_int(value.get("imported_block_count"), maximum=10_000_000)
    skipped = _report_int(value.get("skipped_empty_count"), maximum=10_000_000)
    tracked = value.get("tracked_changes_detected")
    warnings_value = value.get("warnings")
    if (
        size_bytes is None
        or member_count is None
        or uncompressed is None
        or imported is None
        or skipped is None
        or not isinstance(tracked, bool)
        or not isinstance(warnings_value, list)
    ):
        return None

    warnings: list[str] = []
    for item in warnings_value[:1_000]:
        if isinstance(item, str):
            cleaned = item.strip()
            if cleaned:
                warnings.append(cleaned[:4_000])

    return {
        "filename": sanitize_source_filename(value.get("filename")),
        "sha256": sha256.lower(),
        "size_bytes": size_bytes,
        "zip_member_count": member_count,
        "zip_uncompressed_bytes": uncompressed,
        "imported_block_count": imported,
        "skipped_empty_count": skipped,
        "warnings": warnings,
        "tracked_changes_detected": tracked,
        # Never trust persisted prose to overstate fidelity; this build's
        # canonical notice describes what its current exporter actually does.
        "fidelity_notice": FIDELITY_NOTICE,
    }
