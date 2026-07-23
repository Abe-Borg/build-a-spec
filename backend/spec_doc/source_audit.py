"""Memory-bounded package preservation checks for source DOCX exports.

This module audits the decompressed package contract.  Raw ZIP-record fidelity
is intentionally audited separately by :mod:`backend.spec_doc.raw_zip`.
Keeping the two checks separate makes this helper useful even when callers are
working with ordinary ``zipfile`` readers while ensuring unrelated package
members are never materialized in memory.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass
from io import BytesIO
from typing import BinaryIO

DEFAULT_AUDIT_CHUNK_BYTES = 1024 * 1024
DEFAULT_DOCUMENT_PART = "word/document.xml"


@dataclass(frozen=True, slots=True)
class SourceAuditError(ValueError):
    """A package failed one fail-closed preservation invariant."""

    blocker: str
    detail: str

    def __post_init__(self) -> None:
        ValueError.__init__(self, self.detail)


def _validated_chunk_size(chunk_size: int) -> int:
    if (
        isinstance(chunk_size, bool)
        or not isinstance(chunk_size, int)
        or chunk_size < 1
    ):
        raise ValueError("chunk_size must be a positive integer")
    return chunk_size


def _read_chunk(stream: BinaryIO, chunk_size: int) -> bytes:
    """Read one explicitly bounded chunk and normalize bytes-like results."""
    chunk = stream.read(chunk_size)
    if not isinstance(chunk, (bytes, bytearray, memoryview)):
        raise TypeError("package member streams must return bytes")
    if len(chunk) > chunk_size:
        raise ValueError("package member stream exceeded the requested read size")
    return bytes(chunk)


def streams_equal(
    left: BinaryIO,
    right: BinaryIO,
    *,
    chunk_size: int = DEFAULT_AUDIT_CHUNK_BYTES,
) -> bool:
    """Compare two binary streams with bounded reads and bounded buffering.

    Readers are permitted to return short chunks before EOF.  At most one
    requested chunk from each reader is retained, and ``read()`` is always
    passed an explicit positive size.
    """
    chunk_size = _validated_chunk_size(chunk_size)
    left_pending = b""
    right_pending = b""
    left_offset = 0
    right_offset = 0
    left_eof = False
    right_eof = False

    while True:
        if left_offset == len(left_pending) and not left_eof:
            left_pending = _read_chunk(left, chunk_size)
            left_offset = 0
            left_eof = not left_pending
        if right_offset == len(right_pending) and not right_eof:
            right_pending = _read_chunk(right, chunk_size)
            right_offset = 0
            right_eof = not right_pending

        left_remaining = len(left_pending) - left_offset
        right_remaining = len(right_pending) - right_offset
        common = min(left_remaining, right_remaining)
        if common:
            if (
                memoryview(left_pending)[left_offset : left_offset + common]
                != memoryview(right_pending)[right_offset : right_offset + common]
            ):
                return False
            left_offset += common
            right_offset += common
            continue

        if left_eof or right_eof:
            return (
                left_eof
                and right_eof
                and left_offset == len(left_pending)
                and right_offset == len(right_pending)
            )


def _ordered_inventory(archive: zipfile.ZipFile) -> tuple[zipfile.ZipInfo, ...]:
    return tuple(archive.infolist())


def _document_entry(
    inventory: tuple[zipfile.ZipInfo, ...],
    document_part: str,
) -> zipfile.ZipInfo:
    matches = tuple(info for info in inventory if info.filename == document_part)
    if len(matches) != 1:
        raise SourceAuditError(
            "output_validation_failed",
            f"the package must contain exactly one {document_part!r} member",
        )
    return matches[0]


def audit_package_preservation_streaming(
    source_bytes: bytes,
    output_bytes: bytes,
    *,
    expected_document_xml: bytes,
    document_part: str = DEFAULT_DOCUMENT_PART,
    chunk_size: int = DEFAULT_AUDIT_CHUNK_BYTES,
) -> None:
    """Audit decompressed package preservation without materializing members.

    The ordered member-name inventory must remain exact.  Every member except
    ``document_part`` is compared directly between the source and output using
    :func:`streams_equal`.  The output document part is compared directly to
    the approved XML bytes.  Callers remain responsible for validating the
    source package and for the separate raw-record fidelity audit.
    """
    if not isinstance(source_bytes, bytes):
        raise TypeError("source_bytes must be bytes")
    if not isinstance(output_bytes, bytes):
        raise TypeError("output_bytes must be bytes")
    if not isinstance(expected_document_xml, bytes):
        raise TypeError("expected_document_xml must be bytes")
    if not isinstance(document_part, str) or not document_part:
        raise ValueError("document_part must be a non-empty string")
    chunk_size = _validated_chunk_size(chunk_size)

    try:
        with zipfile.ZipFile(BytesIO(source_bytes), "r") as source, zipfile.ZipFile(
            BytesIO(output_bytes), "r"
        ) as output:
            source_inventory = _ordered_inventory(source)
            output_inventory = _ordered_inventory(output)
            source_names = tuple(info.filename for info in source_inventory)
            output_names = tuple(info.filename for info in output_inventory)
            if source_names != output_names:
                raise SourceAuditError(
                    "part_inventory_changed",
                    "the patched package member inventory changed",
                )

            # Require a unique document member even when two malformed
            # inventories happen to match exactly.  Opening by ZipInfo below
            # also prevents duplicate-name shadowing.
            _document_entry(source_inventory, document_part)
            output_document = _document_entry(output_inventory, document_part)

            for source_info, output_info in zip(
                source_inventory, output_inventory
            ):
                if source_info.filename == document_part:
                    continue
                with source.open(source_info, "r") as source_member, output.open(
                    output_info, "r"
                ) as output_member:
                    if not streams_equal(
                        source_member,
                        output_member,
                        chunk_size=chunk_size,
                    ):
                        raise SourceAuditError(
                            "out_of_scope_part_changed",
                            f"out-of-scope part {source_info.filename!r} changed",
                        )

            with output.open(output_document, "r") as output_member:
                if not streams_equal(
                    BytesIO(expected_document_xml),
                    output_member,
                    chunk_size=chunk_size,
                ):
                    raise SourceAuditError(
                        "unexpected_document_xml",
                        "the cloned package does not contain the approved "
                        "lexical XML result",
                    )
    except SourceAuditError:
        raise
    except (
        KeyError,
        NotImplementedError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
    ) as exc:
        raise SourceAuditError(
            "output_validation_failed",
            "the patched DOCX failed streaming package validation",
        ) from exc


__all__ = [
    "DEFAULT_AUDIT_CHUNK_BYTES",
    "DEFAULT_DOCUMENT_PART",
    "SourceAuditError",
    "audit_package_preservation_streaming",
    "streams_equal",
]
