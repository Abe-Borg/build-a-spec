"""Bounded upload handling and defensive inspection for imported DOCX files.

The validated package becomes an immutable source artifact for recovery,
native project persistence, and fail-closed clone-and-patch export. This module
is the single boundary in front of OOXML consumers so malformed or hostile ZIP
containers are rejected before a parser sees them.
"""
from __future__ import annotations

import hashlib
import re
import stat
import struct
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Callable

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
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_OFFICE_DOCUMENT_REL_TYPES = frozenset(
    {
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
        "http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument",
    }
)
_OPC_RELATIONSHIP_NAMESPACES = frozenset(
    {
        "http://schemas.openxmlformats.org/package/2006/relationships",
    }
)
_OPC_CONTENT_TYPE_NAMESPACES = frozenset(
    {
        "http://schemas.openxmlformats.org/package/2006/content-types",
    }
)
_WORDPROCESSINGML_NAMESPACES = frozenset(
    {
        "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "http://purl.oclc.org/ooxml/wordprocessingml/main",
    }
)

FIDELITY_NOTICE = (
    "Build-a-Spec retains the exact source package and extracts supported body "
    "content into its semantic model. Preserved export clones the source and "
    "patches verified simple body-paragraph text. Bounded add, delete, and "
    "reorder are available only inside proven flat body islands with isolated "
    "direct Word list bindings; all other structural or complex-format edits "
    "are refused. Headers, footers, and general Word formatting remain outside "
    "the edit surface. Normalized DOCX remains a separate, explicit export mode."
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
    # True when a bounded non-required member could not be decompressed or
    # CRC-verified.  The exact artifact remains recoverable, but callers must
    # not expose a source-backed mutation surface for it.
    integrity_ambiguous: bool = False


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
    # OPC part names are URI-like even though ZIP exposes them as decoded
    # strings.  Validate and decode percent escapes before applying path
    # rules so encoded traversal, separators, drive prefixes, and equivalent
    # shadow names cannot bypass the package boundary.  Encoded separators
    # are rejected outright because consumers disagree on whether they are a
    # path delimiter or data inside one segment.
    canonical_bytes = bytearray()
    index = 0
    try:
        while index < len(name):
            character = name[index]
            if character != "%":
                canonical_bytes.extend(character.encode("utf-8"))
                index += 1
                continue
            if (
                index + 2 >= len(name)
                or name[index + 1] not in _HEX_DIGITS
                or name[index + 2] not in _HEX_DIGITS
            ):
                raise SourcePackageError(
                    "The DOCX package contains an unsafe member path."
                )
            value = int(name[index + 1 : index + 3], 16)
            if value in {ord("/"), ord("\\")}:
                raise SourcePackageError(
                    "The DOCX package contains an unsafe member path."
                )
            canonical_bytes.append(value)
            index += 3
        canonical_name = canonical_bytes.decode("utf-8")
    except UnicodeError as exc:
        raise SourcePackageError(
            "The DOCX package contains an unsafe member name."
        ) from exc

    if any(ord(ch) < 32 or ord(ch) == 127 for ch in canonical_name):
        raise SourcePackageError(
            "The DOCX package contains an unsafe member name."
        )
    if (
        "\\" in canonical_name
        or canonical_name.startswith("/")
        or _DRIVE_PREFIX_RE.match(canonical_name)
    ):
        raise SourcePackageError(
            "The DOCX package contains an unsafe member path."
        )

    is_directory = canonical_name.endswith("/")
    path = canonical_name[:-1] if is_directory else canonical_name
    parts = path.split("/")
    if not path or any(part in {"", ".", ".."} for part in parts):
        raise SourcePackageError(
            "The DOCX package contains an unsafe member path."
        )

    # OPC part names are URI-like.  Normalizing for the duplicate check also
    # prevents two names that collide on common case-insensitive filesystems.
    normalized = unicodedata.normalize("NFC", canonical_name).casefold()
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


def _xml_namespace_name(tag: str) -> str:
    if not tag.startswith("{") or "}" not in tag:
        return ""
    return tag[1:].split("}", 1)[0]


class _RequiredOpcXmlTarget:
    """Collect only the required facts while Expat streams one OPC part."""

    __slots__ = (
        "_child_matcher",
        "_depth",
        "_expected_root_local_name",
        "_expected_root_namespaces",
        "has_required_child",
        "root_is_valid",
    )

    def __init__(
        self,
        *,
        expected_root_local_name: str,
        expected_root_namespaces: frozenset[str],
        child_matcher: Callable[[str, dict[str, str]], bool] | None = None,
    ) -> None:
        self._expected_root_local_name = expected_root_local_name
        self._expected_root_namespaces = expected_root_namespaces
        self._child_matcher = child_matcher
        self._depth = 0
        self.root_is_valid = False
        self.has_required_child = child_matcher is None

    def start(self, tag: str, attributes: dict[str, str]) -> None:
        if self._depth == 0:
            self.root_is_valid = (
                _xml_local_name(tag) == self._expected_root_local_name
                and _xml_namespace_name(tag) in self._expected_root_namespaces
            )
        elif (
            self._depth == 1
            and self._child_matcher is not None
            and self._child_matcher(tag, attributes)
        ):
            self.has_required_child = True
        self._depth += 1

    def end(self, _tag: str) -> None:
        self._depth -= 1

    def data(self, _data: str) -> None:
        # Required OPC wiring is expressed entirely by element/attribute data.
        # Ignoring character data here prevents large irrelevant text nodes
        # from accumulating in an ElementTree.
        return None

    def close(self) -> "_RequiredOpcXmlTarget":
        return self


def _stream_required_opc_xml(
    archive: zipfile.ZipFile,
    filename: str,
    *,
    expected_root_local_name: str,
    expected_root_namespaces: frozenset[str],
    child_matcher: Callable[[str, dict[str, str]], bool] | None = None,
) -> _RequiredOpcXmlTarget:
    """Parse one required XML part through explicitly bounded member reads."""
    target = _RequiredOpcXmlTarget(
        expected_root_local_name=expected_root_local_name,
        expected_root_namespaces=expected_root_namespaces,
        child_matcher=child_matcher,
    )
    parser = ET.XMLParser(target=target)
    with archive.open(filename, "r") as member:
        while True:
            chunk = member.read(_ZIP_READ_CHUNK_BYTES)
            if not chunk:
                break
            parser.feed(chunk)
    return parser.close()


def _declares_main_document(tag: str, attributes: dict[str, str]) -> bool:
    return (
        _xml_local_name(tag) == "Override"
        and _xml_namespace_name(tag) in _OPC_CONTENT_TYPE_NAMESPACES
        and attributes.get("PartName", "").lstrip("/") == "word/document.xml"
    )


def _relates_to_main_document(tag: str, attributes: dict[str, str]) -> bool:
    return (
        _xml_local_name(tag) == "Relationship"
        and _xml_namespace_name(tag) in _OPC_RELATIONSHIP_NAMESPACES
        and attributes.get("Type") in _OFFICE_DOCUMENT_REL_TYPES
        and attributes.get("Target", "").lstrip("/") == "word/document.xml"
        and attributes.get("TargetMode", "Internal") != "External"
    )


def _validate_required_opc_xml(archive: zipfile.ZipFile) -> None:
    """Check required OPC XML is well-formed and wired to the main part."""
    try:
        content_types = _stream_required_opc_xml(
            archive,
            "[Content_Types].xml",
            expected_root_local_name="Types",
            expected_root_namespaces=_OPC_CONTENT_TYPE_NAMESPACES,
            child_matcher=_declares_main_document,
        )
        relationships = _stream_required_opc_xml(
            archive,
            "_rels/.rels",
            expected_root_local_name="Relationships",
            expected_root_namespaces=_OPC_RELATIONSHIP_NAMESPACES,
            child_matcher=_relates_to_main_document,
        )
        document = _stream_required_opc_xml(
            archive,
            "word/document.xml",
            expected_root_local_name="document",
            expected_root_namespaces=_WORDPROCESSINGML_NAMESPACES,
        )
    except (
        ET.ParseError,
        UnicodeError,
        zipfile.BadZipFile,
        RuntimeError,
        NotImplementedError,
        zlib.error,
        EOFError,
    ) as exc:
        raise SourcePackageError(
            "The DOCX package contains malformed required Word XML."
        ) from exc

    if not content_types.root_is_valid:
        raise SourcePackageError(
            "The DOCX package has an invalid [Content_Types].xml part."
        )
    if not content_types.has_required_child:
        raise SourcePackageError(
            "The DOCX package does not declare its main Word document part."
        )

    if not relationships.root_is_valid:
        raise SourcePackageError(
            "The DOCX package has an invalid root relationships part."
        )
    if not relationships.has_required_child:
        raise SourcePackageError(
            "The DOCX package has no valid relationship to word/document.xml."
        )

    if not document.root_is_valid:
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
    """Validate ZIP/OPC safety limits and readable required Word parts.

    The compressed bytes have already passed :func:`read_upload_bounded`.
    Declared sizes are checked before decompression; streamed actual sizes are
    checked again while readable members are consumed to EOF, which also makes
    ``zipfile`` verify their CRCs.  A non-required member whose raw ZIP layout
    is ambiguous may be retained under its already-bounded declared size; the
    stricter raw index will then narrow preservation to exact-original
    pass-through.  Required OPC parts still must be readable and valid below.
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

        exact_names: set[str] = set()
        declared_total = 0
        for info in infos:
            # Validate every spelling, including duplicate/normalized aliases.
            # Collisions are raw-layout ambiguity rather than an extraction
            # path here: no member is materialized onto a filesystem, and the
            # raw-preserving mutation boundary rejects the ambiguity later.
            _safe_member_key(info.filename)
            exact_names.add(info.filename)

            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if unix_mode and stat.S_ISLNK(unix_mode):
                raise SourcePackageError(
                    "The DOCX package contains an unsafe symbolic-link member."
                )
            try:
                local_flags = _local_header_flags(data, info)
            except SourcePackageError:
                # A central entry may point at an unsupported/overlapping raw
                # record.  Keep inspecting through zipfile so readable OPC can
                # still be imported as exact-original-only.  If this is a
                # required part, _validate_required_opc_xml will reject it.
                local_flags = 0
            if info.flag_bits & _ENCRYPTED_FLAG or local_flags & _ENCRYPTED_FLAG:
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
        integrity_ambiguous = False
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
            except (
                zipfile.BadZipFile,
                RuntimeError,
                NotImplementedError,
                zlib.error,
                EOFError,
            ) as exc:
                # Preserve an unreadable non-required raw record only by its
                # bounded central-directory declaration.  Required Word parts
                # are independently reopened and parsed below, so they remain
                # hard failures.  The strict raw index records this ambiguity
                # as unsupported_raw_zip_layout and disables every mutation.
                if member_total > info.file_size:
                    raise SourcePackageError(
                        "The DOCX package contains inconsistent member sizes."
                    ) from exc
                actual_total += info.file_size - member_total
                member_total = info.file_size
                integrity_ambiguous = True
            if member_total != info.file_size:
                raise SourcePackageError(
                    "The DOCX package contains inconsistent member sizes."
                )

        _validate_required_opc_xml(archive)

        return DocxPackageInfo(
            member_count=len(infos),
            uncompressed_bytes=actual_total,
            integrity_ambiguous=integrity_ambiguous,
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
    """Build the whitelist-only report persisted with a project."""
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
