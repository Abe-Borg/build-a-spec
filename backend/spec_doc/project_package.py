"""Portable Build-a-Spec project containers.

The semantic project remains JSON, but an imported Word master must travel as
the exact bytes that were validated at import time.  A ``.baspec`` file is a
small, versioned ZIP envelope with fixed member names::

    manifest.json
    project.json
    source/original.docx       (optional)

Nothing is extracted to disk.  Parsing is deliberately side-effect free so an
API caller can validate the complete envelope, source package, semantic
baseline, and source map before replacing the live session.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import struct
import unicodedata
import zipfile
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .project import validate_project_data
from .source_package import (
    MAX_UPLOAD_BYTES as MAX_SOURCE_DOCX_BYTES,
    sanitize_import_report,
    sanitize_source_filename,
)

if TYPE_CHECKING:
    from .source_patch import SourcePatchContext

PACKAGE_KIND = "buildaspec-project-package"
PACKAGE_FORMAT = 1
PACKAGE_MEDIA_TYPE = "application/vnd.buildaspec.project+zip"
PACKAGE_EXTENSION = ".baspec"

MANIFEST_PATH = "manifest.json"
PROJECT_PATH = "project.json"
SOURCE_DOCX_PATH = "source/original.docx"

# A normal section is far smaller than these ceilings.  The limits are high
# enough for long version/chat histories while bounding hostile compression.
MAX_PACKAGE_BYTES = 96 * 1024 * 1024
MAX_PACKAGE_MEMBERS = 3
MAX_MANIFEST_BYTES = 64 * 1024
MAX_PROJECT_JSON_BYTES = 64 * 1024 * 1024
MAX_PACKAGE_UNCOMPRESSED_BYTES = 96 * 1024 * 1024

_READ_CHUNK_BYTES = 1024 * 1024
_LOCAL_FILE_HEADER_SIZE = 30
_LOCAL_FILE_HEADER_SIGNATURE = b"PK\x03\x04"
_ENCRYPTED_FLAG = 0x1
_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
_FIXED_MEMBERS = frozenset({MANIFEST_PATH, PROJECT_PATH, SOURCE_DOCX_PATH})


class ProjectPackageError(ValueError):
    """The bytes are not a safe, internally consistent project file."""


class ProjectPackageTooLargeError(ProjectPackageError):
    """The outer project upload exceeded its compressed-size limit."""


@dataclass(frozen=True)
class ParsedProjectPackage:
    """Validated project data staged without touching the live session."""

    project: dict[str, Any]
    source_docx_bytes: bytes | None = None
    source_docx_filename: str = ""
    source_map: dict[str, Any] | None = None
    legacy_json: bool = False
    # Derived immutable state is process-local only.  It never enters the
    # manifest or project JSON and is excluded from value equality/repr so the
    # portable package contract remains exactly format 1.
    source_patch_context: SourcePatchContext | None = field(
        default=None,
        repr=False,
        compare=False,
    )


async def read_project_upload_bounded(
    upload,
    *,
    max_bytes: int = MAX_PACKAGE_BYTES,
    chunk_bytes: int = _READ_CHUNK_BYTES,
) -> bytes:
    """Read an uploaded project with an exact compressed-byte ceiling."""
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
            raise ProjectPackageError("The uploaded project could not be read.")
        payload.extend(chunk)
        if len(payload) > max_bytes:
            break
    raise ProjectPackageTooLargeError(
        f"The project file is too large (maximum {max_bytes // (1024 * 1024)} MiB)."
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: Any, *, pretty: bool = True) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ProjectPackageError("The project contains non-JSON data.") from exc


def _reject_json_constant(value: str):
    raise ValueError(f"invalid JSON constant {value}")


def _decode_json(data: bytes, *, label: str) -> Any:
    try:
        text = data.decode("utf-8-sig")
        return json.loads(text, parse_constant=_reject_json_constant)
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ProjectPackageError(f"The {label} is not valid UTF-8 JSON.") from exc


def _safe_member_key(name: str) -> str:
    if not name or any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise ProjectPackageError("The project package has an unsafe member name.")
    if "\\" in name or name.startswith("/") or _DRIVE_PREFIX_RE.match(name):
        raise ProjectPackageError("The project package has an unsafe member path.")
    path = name[:-1] if name.endswith("/") else name
    parts = path.split("/")
    if not path or any(part in {"", ".", ".."} for part in parts):
        raise ProjectPackageError("The project package has an unsafe member path.")
    return unicodedata.normalize("NFC", name).casefold()


def _local_header_flags(data: bytes, info: zipfile.ZipInfo) -> int:
    offset = info.header_offset
    end = offset + _LOCAL_FILE_HEADER_SIZE
    if offset < 0 or end > len(data):
        raise ProjectPackageError("The project package has a malformed ZIP header.")
    header = data[offset:end]
    if header[:4] != _LOCAL_FILE_HEADER_SIGNATURE:
        raise ProjectPackageError("The project package has a malformed ZIP header.")
    return struct.unpack_from("<H", header, 6)[0]


def _descriptor(
    value: Any,
    *,
    expected_path: str,
    maximum_size: int,
    include_filename: bool,
) -> tuple[int, str, str]:
    expected_keys = {"path", "size_bytes", "sha256"}
    if include_filename:
        expected_keys.add("filename")
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ProjectPackageError("The project manifest has an invalid descriptor.")
    if value.get("path") != expected_path:
        raise ProjectPackageError("The project manifest references an invalid path.")
    size = value.get("size_bytes")
    digest = value.get("sha256")
    if (
        isinstance(size, bool)
        or not isinstance(size, int)
        or size < 0
        or size > maximum_size
        or not isinstance(digest, str)
        or not _SHA256_RE.fullmatch(digest)
    ):
        raise ProjectPackageError("The project manifest has invalid integrity data.")
    filename = ""
    if include_filename:
        raw_filename = value.get("filename")
        if not isinstance(raw_filename, str):
            raise ProjectPackageError("The project manifest has an invalid filename.")
        filename = sanitize_source_filename(raw_filename)
        if filename != raw_filename:
            raise ProjectPackageError("The project manifest has an unsafe filename.")
    return size, digest, filename


def _assert_source_matches_project(
    project: dict[str, Any], source: bytes, filename: str
) -> None:
    report = sanitize_import_report(project.get("import_report"))
    if report is None:
        raise ProjectPackageError(
            "A project with an attached source DOCX needs a valid import report."
        )
    if report["size_bytes"] != len(source) or report["sha256"] != _sha256(source):
        raise ProjectPackageError(
            "The attached source DOCX does not match the project's import report."
        )
    if report["filename"] != filename:
        raise ProjectPackageError(
            "The attached source DOCX filename does not match the import report."
        )


def _validated_project(project: Any):
    try:
        return validate_project_data(project)
    except ValueError as exc:
        raise ProjectPackageError(str(exc)) from exc


def _assert_source_binding(
    staged_project,
    source: bytes,
    *,
    context: SourcePatchContext | None = None,
) -> SourcePatchContext:
    """Return one validated transient context for the source and baseline."""
    if staged_project.source_map is None:
        raise ProjectPackageError(
            "A project with an attached source DOCX needs a valid source map."
        )
    doc_data = staged_project.doc_data
    baseline_index = doc_data.get("baseline_index")
    versions = doc_data.get("versions")
    if (
        isinstance(baseline_index, bool)
        or not isinstance(baseline_index, int)
        or not isinstance(versions, list)
        or not 0 <= baseline_index < len(versions)
    ):
        raise ProjectPackageError(
            "A source-backed project needs its imported semantic baseline."
        )

    from .model import SpecSection
    from .source_mapping import SourceBodyMap
    from .source_patch import (
        build_source_patch_context,
        validate_source_map_identity,
    )

    try:
        baseline = SpecSection.from_dict(versions[baseline_index])
        source_map = SourceBodyMap.from_dict(staged_project.source_map)
        if context is None:
            return build_source_patch_context(
                source_bytes=source,
                source_map=source_map,
                baseline=baseline,
            )
        validate_source_map_identity(
            source_bytes=source,
            source_map=source_map,
            baseline=baseline,
            context=context,
        )
        return context
    except (TypeError, ValueError) as exc:
        raise ProjectPackageError(
            f"The source DOCX, source map, and imported baseline disagree: {exc}"
        ) from exc


def _zip_info(path: str, *, compression: int) -> zipfile.ZipInfo:
    # Stable metadata keeps repeated saves reproducible apart from saved_at.
    info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = compression
    info.create_system = 3
    info.external_attr = 0o600 << 16
    return info


def build_project_package(
    project: dict[str, Any],
    *,
    source_docx_bytes: bytes | None = None,
    source_docx_filename: str = "",
    source_patch_context: SourcePatchContext | None = None,
) -> bytes:
    """Serialize semantic JSON plus an optional exact source into ``.baspec``.

    The source is validated again at the persistence boundary.  This avoids
    writing a container whose manifest is self-consistent but whose attached
    file is not a safe DOCX or no longer matches the import report.  A supplied
    source context is identity-checked and reused, but is never serialized.
    """
    staged_project = _validated_project(project)
    project_bytes = _json_bytes(project)
    if len(project_bytes) > MAX_PROJECT_JSON_BYTES:
        raise ProjectPackageTooLargeError(
            "The project history is too large to save in one project file."
        )

    source: bytes | None
    source_descriptor: dict[str, Any] | None
    if source_docx_bytes is None:
        if source_patch_context is not None:
            raise ProjectPackageError(
                "A project package cannot use a source context without its "
                "source DOCX."
            )
        if staged_project.source_map is not None:
            raise ProjectPackageError(
                "A project package cannot carry a source map without its "
                "source DOCX."
            )
        source = None
        source_descriptor = None
    else:
        if not isinstance(source_docx_bytes, bytes):
            raise TypeError("source_docx_bytes must be bytes or None")
        source = source_docx_bytes
        if len(source) > MAX_SOURCE_DOCX_BYTES:
            raise ProjectPackageTooLargeError(
                "The attached source DOCX exceeds the project source limit."
            )
        filename = sanitize_source_filename(source_docx_filename)
        _assert_source_matches_project(project, source, filename)
        _assert_source_binding(
            staged_project,
            source,
            context=source_patch_context,
        )
        source_descriptor = {
            "path": SOURCE_DOCX_PATH,
            "filename": filename,
            "size_bytes": len(source),
            "sha256": _sha256(source),
        }

    manifest = {
        "kind": PACKAGE_KIND,
        "format": PACKAGE_FORMAT,
        "project": {
            "path": PROJECT_PATH,
            "size_bytes": len(project_bytes),
            "sha256": _sha256(project_bytes),
        },
        "source_docx": source_descriptor,
    }
    manifest_bytes = _json_bytes(manifest)
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:  # pragma: no cover - invariant
        raise ProjectPackageError("The project manifest is unexpectedly large.")

    output = io.BytesIO()
    try:
        with zipfile.ZipFile(
            output, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=False
        ) as archive:
            archive.writestr(
                _zip_info(MANIFEST_PATH, compression=zipfile.ZIP_DEFLATED),
                manifest_bytes,
            )
            archive.writestr(
                _zip_info(PROJECT_PATH, compression=zipfile.ZIP_DEFLATED),
                project_bytes,
            )
            if source is not None:
                # DOCX is already compressed. ZIP_STORED avoids pointless
                # recompression and preserves a predictable resource bound.
                archive.writestr(
                    _zip_info(SOURCE_DOCX_PATH, compression=zipfile.ZIP_STORED),
                    source,
                )
    except (OSError, RuntimeError, zipfile.LargeZipFile) as exc:
        raise ProjectPackageError("Could not create the project package.") from exc

    payload = output.getvalue()
    if len(payload) > MAX_PACKAGE_BYTES:
        raise ProjectPackageTooLargeError(
            "The completed project package exceeds the file-size limit."
        )
    return payload


def _read_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    maximum: int,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    try:
        with archive.open(info, "r") as member:
            while True:
                chunk = member.read(min(_READ_CHUNK_BYTES, maximum + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > maximum:
                    raise ProjectPackageTooLargeError(
                        f"The {info.filename} member exceeds its size limit."
                    )
    except ProjectPackageError:
        raise
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise ProjectPackageError(
            "The project package failed ZIP integrity validation."
        ) from exc
    if total != info.file_size:
        raise ProjectPackageError("The project package has inconsistent member sizes.")
    return b"".join(chunks)


def parse_project_package(
    data: bytes,
    *,
    source_patch_context: SourcePatchContext | None = None,
) -> ParsedProjectPackage:
    """Validate and stage a ``.baspec`` plus a transient source context."""
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    if len(data) > MAX_PACKAGE_BYTES:
        raise ProjectPackageTooLargeError(
            "The project file is too large "
            f"(maximum {MAX_PACKAGE_BYTES // (1024 * 1024)} MiB)."
        )
    try:
        archive = zipfile.ZipFile(io.BytesIO(data), "r")
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise ProjectPackageError(
            "That file is not a readable project package."
        ) from exc

    try:
        infos = archive.infolist()
        if len(infos) not in (2, 3) or len(infos) > MAX_PACKAGE_MEMBERS:
            raise ProjectPackageError(
                "A project package must contain only its fixed project members."
            )
        by_name: dict[str, zipfile.ZipInfo] = {}
        collision_keys: set[str] = set()
        declared_total = 0
        for info in infos:
            key = _safe_member_key(info.filename)
            if key in collision_keys:
                raise ProjectPackageError(
                    "The project package contains duplicate member names."
                )
            collision_keys.add(key)
            if info.filename not in _FIXED_MEMBERS or info.is_dir():
                raise ProjectPackageError(
                    "The project package contains an unexpected member."
                )
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if unix_mode and stat.S_ISLNK(unix_mode):
                raise ProjectPackageError(
                    "The project package contains an unsafe symbolic link."
                )
            if (
                info.flag_bits & _ENCRYPTED_FLAG
                or _local_header_flags(data, info) & _ENCRYPTED_FLAG
            ):
                raise ProjectPackageError("Encrypted project members are unsupported.")
            if info.compress_type not in _ALLOWED_COMPRESSION:
                raise ProjectPackageError(
                    "The project package uses an unsupported compression method."
                )
            member_limit = {
                MANIFEST_PATH: MAX_MANIFEST_BYTES,
                PROJECT_PATH: MAX_PROJECT_JSON_BYTES,
                SOURCE_DOCX_PATH: MAX_SOURCE_DOCX_BYTES,
            }[info.filename]
            if info.file_size < 0 or info.file_size > member_limit:
                raise ProjectPackageTooLargeError(
                    f"The {info.filename} member exceeds its size limit."
                )
            declared_total += info.file_size
            if declared_total > MAX_PACKAGE_UNCOMPRESSED_BYTES:
                raise ProjectPackageTooLargeError(
                    "The project package exceeds its uncompressed-size limit."
                )
            by_name[info.filename] = info

        if MANIFEST_PATH not in by_name or PROJECT_PATH not in by_name:
            raise ProjectPackageError(
                "The project package is missing its manifest or project data."
            )

        manifest_bytes = _read_member(
            archive, by_name[MANIFEST_PATH], maximum=MAX_MANIFEST_BYTES
        )
        project_bytes = _read_member(
            archive, by_name[PROJECT_PATH], maximum=MAX_PROJECT_JSON_BYTES
        )
        manifest = _decode_json(manifest_bytes, label="project manifest")
        if (
            not isinstance(manifest, dict)
            or set(manifest) != {"kind", "format", "project", "source_docx"}
            or manifest.get("kind") != PACKAGE_KIND
            or manifest.get("format") != PACKAGE_FORMAT
        ):
            raise ProjectPackageError("Unsupported or malformed project manifest.")

        project_size, project_digest, _ = _descriptor(
            manifest.get("project"),
            expected_path=PROJECT_PATH,
            maximum_size=MAX_PROJECT_JSON_BYTES,
            include_filename=False,
        )
        if (
            project_size != len(project_bytes)
            or project_digest != _sha256(project_bytes)
        ):
            raise ProjectPackageError(
                "The project data does not match its manifest integrity record."
            )

        project = _decode_json(project_bytes, label="project data")
        staged_project = _validated_project(project)
        source_map = staged_project.source_map

        source_descriptor = manifest.get("source_docx")
        source_info = by_name.get(SOURCE_DOCX_PATH)
        if source_descriptor is None:
            if source_patch_context is not None:
                raise ProjectPackageError(
                    "A source context cannot be used without an attached "
                    "source DOCX."
                )
            if source_info is not None:
                raise ProjectPackageError(
                    "The project contains an undeclared source DOCX."
                )
            if source_map is not None:
                raise ProjectPackageError(
                    "The project contains a source map without its source DOCX."
                )
            return ParsedProjectPackage(project=project, source_map=source_map)

        if source_info is None:
            raise ProjectPackageError(
                "The project manifest declares a missing source DOCX."
            )
        source_size, source_digest, source_filename = _descriptor(
            source_descriptor,
            expected_path=SOURCE_DOCX_PATH,
            maximum_size=MAX_SOURCE_DOCX_BYTES,
            include_filename=True,
        )
        source = _read_member(
            archive, source_info, maximum=MAX_SOURCE_DOCX_BYTES
        )
        if source_size != len(source) or source_digest != _sha256(source):
            raise ProjectPackageError(
                "The source DOCX does not match its manifest integrity record."
            )
        _assert_source_matches_project(project, source, source_filename)
        validated_context = _assert_source_binding(
            staged_project,
            source,
            context=source_patch_context,
        )
        return ParsedProjectPackage(
            project=project,
            source_docx_bytes=source,
            source_docx_filename=source_filename,
            source_map=source_map,
            source_patch_context=validated_context,
        )
    except ProjectPackageError:
        raise
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, struct.error) as exc:
        raise ProjectPackageError(
            "The project package failed ZIP integrity validation."
        ) from exc
    finally:
        archive.close()


def parse_project_file(
    data: bytes,
    *,
    source_patch_context: SourcePatchContext | None = None,
) -> ParsedProjectPackage:
    """Parse either a portable package or a legacy format-1 JSON project."""
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    if len(data) > MAX_PACKAGE_BYTES:
        raise ProjectPackageTooLargeError(
            "The project file is too large "
            f"(maximum {MAX_PACKAGE_BYTES // (1024 * 1024)} MiB)."
        )
    if data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return parse_project_package(
            data,
            source_patch_context=source_patch_context,
        )
    if len(data) > MAX_PROJECT_JSON_BYTES:
        raise ProjectPackageTooLargeError(
            "The legacy JSON project exceeds its size limit."
        )
    project = _decode_json(data, label="project data")
    staged_project = _validated_project(project)
    if source_patch_context is not None:
        raise ProjectPackageError(
            "A source context cannot be used with a legacy JSON project."
        )
    return ParsedProjectPackage(
        project=project,
        source_map=staged_project.source_map,
        legacy_json=True,
    )
