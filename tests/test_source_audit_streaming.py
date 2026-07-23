from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable, Mapping

import pytest

from backend.spec_doc.importer import parse_master_docx
from backend.spec_doc.model import SpecSection, apply_edits
from backend.spec_doc.source_audit import (
    SourceAuditError,
    audit_package_preservation_streaming,
    streams_equal,
)
from backend.spec_doc.source_patch import (
    build_source_patch_context,
    build_source_preserving_docx,
)
from tests.docx_fidelity_helpers import TARGET_EDITED_TEXT, make_fidelity_master

_DOCUMENT_PART = "word/document.xml"


class _StrictReader:
    """A binary reader that fails if a caller requests an unbounded read."""

    def __init__(
        self,
        payload: bytes,
        *,
        maximum_return: int | None = None,
    ) -> None:
        self._stream = io.BytesIO(payload)
        self.maximum_return = maximum_return
        self.requests: list[int] = []

    def read(self, size: int = -1) -> bytes:
        assert size >= 0, "unbounded read() is forbidden"
        self.requests.append(size)
        if self.maximum_return is not None:
            size = min(size, self.maximum_return)
        return self._stream.read(size)


class _StrictZipReader:
    """Context-manager wrapper around ZipExtFile with bounded-read checks."""

    def __init__(self, wrapped, *, name: str, requests: list[tuple[str, int]]):
        self._wrapped = wrapped
        self._name = name
        self._requests = requests

    def read(self, size: int = -1) -> bytes:
        assert size >= 0, f"unbounded read() used for {self._name}"
        self._requests.append((self._name, size))
        return self._wrapped.read(size)

    def close(self) -> None:
        self._wrapped.close()

    def __enter__(self):
        self._wrapped.__enter__()
        return self

    def __exit__(self, exc_type, exc, traceback):
        return self._wrapped.__exit__(exc_type, exc, traceback)


def _write_zip(
    entries: Mapping[str, bytes] | Iterable[tuple[str, bytes]],
    *,
    compression: int = zipfile.ZIP_DEFLATED,
) -> bytes:
    items = entries.items() if isinstance(entries, Mapping) else entries
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name, payload in items:
            archive.writestr(name, payload)
    return output.getvalue()


def _package_entries(
    *,
    document_xml: bytes,
    media: bytes = b"media",
) -> tuple[tuple[str, bytes], ...]:
    return (
        ("[Content_Types].xml", b"types"),
        ("_rels/.rels", b"relationships"),
        (_DOCUMENT_PART, document_xml),
        ("word/header1.xml", b"immutable header"),
        ("word/media/image.bin", media),
    )


def test_streams_equal_uses_bounded_reads_and_accepts_short_chunks():
    payload = (b"bounded-stream-contents" * 37) + b"end"
    left = _StrictReader(payload, maximum_return=7)
    right = _StrictReader(payload, maximum_return=13)

    assert streams_equal(left, right, chunk_size=64) is True
    assert left.requests and right.requests
    assert set(left.requests) == {64}
    assert set(right.requests) == {64}


def test_streams_equal_detects_content_and_length_differences():
    assert streams_equal(
        _StrictReader(b"same-prefix-left"),
        _StrictReader(b"same-prefix-right"),
        chunk_size=4,
    ) is False
    assert streams_equal(
        _StrictReader(b"prefix"),
        _StrictReader(b"prefix-extra"),
        chunk_size=3,
    ) is False


@pytest.mark.parametrize("chunk_size", [0, -1, True, 1.5])
def test_streams_equal_rejects_invalid_chunk_size(chunk_size):
    with pytest.raises(ValueError, match="positive integer"):
        streams_equal(io.BytesIO(), io.BytesIO(), chunk_size=chunk_size)


def test_package_audit_streams_large_unrelated_members_with_bounded_reads(
    monkeypatch,
):
    chunk_size = 64 * 1024
    media = bytes(range(251)) * 2_100
    source_xml = b"<document><body>before</body></document>"
    expected_xml = b"<document><body>after</body></document>"
    source = _write_zip(_package_entries(document_xml=source_xml, media=media))
    output = _write_zip(_package_entries(document_xml=expected_xml, media=media))

    original_open = zipfile.ZipFile.open
    requests: list[tuple[str, int]] = []

    def strict_open(self, name, mode="r", pwd=None, *, force_zip64=False):
        wrapped = original_open(
            self,
            name,
            mode=mode,
            pwd=pwd,
            force_zip64=force_zip64,
        )
        member_name = name.filename if isinstance(name, zipfile.ZipInfo) else name
        return _StrictZipReader(
            wrapped,
            name=member_name,
            requests=requests,
        )

    monkeypatch.setattr(zipfile.ZipFile, "open", strict_open)

    audit_package_preservation_streaming(
        source,
        output,
        expected_document_xml=expected_xml,
        chunk_size=chunk_size,
    )

    assert requests
    assert all(0 < size <= chunk_size for _, size in requests)
    media_requests = [
        size for name, size in requests if name == "word/media/image.bin"
    ]
    assert len(media_requests) > 2
    assert set(media_requests) == {chunk_size}


def test_package_audit_requires_exact_ordered_member_inventory():
    source_entries = _package_entries(document_xml=b"<document/>")
    output_entries = source_entries[:2] + tuple(reversed(source_entries[2:]))

    with pytest.raises(SourceAuditError) as exc_info:
        audit_package_preservation_streaming(
            _write_zip(source_entries),
            _write_zip(output_entries),
            expected_document_xml=b"<document/>",
        )

    assert exc_info.value.blocker == "part_inventory_changed"


def test_package_audit_rejects_changed_unrelated_member():
    source_entries = _package_entries(document_xml=b"<document/>")
    output_entries = tuple(
        (name, b"changed header" if name == "word/header1.xml" else payload)
        for name, payload in source_entries
    )

    with pytest.raises(SourceAuditError) as exc_info:
        audit_package_preservation_streaming(
            _write_zip(source_entries),
            _write_zip(output_entries),
            expected_document_xml=b"<document/>",
            chunk_size=5,
        )

    assert exc_info.value.blocker == "out_of_scope_part_changed"
    assert "word/header1.xml" in exc_info.value.detail


def test_package_audit_compares_document_directly_to_expected_bytes():
    actual_xml = b"<document><actual/></document>"
    source = _write_zip(_package_entries(document_xml=b"<document/>"))
    output = _write_zip(_package_entries(document_xml=actual_xml))

    with pytest.raises(SourceAuditError) as exc_info:
        audit_package_preservation_streaming(
            source,
            output,
            expected_document_xml=b"<document><expected/></document>",
            chunk_size=8,
        )

    assert exc_info.value.blocker == "unexpected_document_xml"


def test_package_audit_rejects_duplicate_document_members():
    entries = (
        (_DOCUMENT_PART, b"<first/>"),
        (_DOCUMENT_PART, b"<second/>"),
    )
    with pytest.warns(UserWarning, match="Duplicate name"):
        package = _write_zip(entries)

    with pytest.raises(SourceAuditError) as exc_info:
        audit_package_preservation_streaming(
            package,
            package,
            expected_document_xml=b"<second/>",
        )

    assert exc_info.value.blocker == "output_validation_failed"


def test_package_audit_wraps_malformed_zip_errors():
    with pytest.raises(SourceAuditError) as exc_info:
        audit_package_preservation_streaming(
            _write_zip(((_DOCUMENT_PART, b"<document/>"),)),
            b"not a zip",
            expected_document_xml=b"<document/>",
        )

    assert exc_info.value.blocker == "output_validation_failed"


def test_package_audit_validates_argument_types():
    package = _write_zip(((_DOCUMENT_PART, b"<document/>"),))

    with pytest.raises(TypeError, match="expected_document_xml"):
        audit_package_preservation_streaming(
            package,
            package,
            expected_document_xml=bytearray(b"<document/>"),
        )


def test_integrated_source_export_audit_never_uses_unbounded_member_reads(
    tmp_path,
    monkeypatch,
):
    source = make_fidelity_master(
        tmp_path,
        filename="bounded-integrated-audit.docx",
    )
    imported = parse_master_docx(tmp_path / "bounded-integrated-audit.docx")
    assert imported.source_map is not None
    baseline = SpecSection.from_dict(imported.section.to_dict())
    current, _applied = apply_edits(
        baseline,
        [
            {
                "action": "replace",
                "target_id": "pt1.a1.p1",
                "text": TARGET_EDITED_TEXT,
                "status": "confirmed",
            }
        ],
    )
    context = build_source_patch_context(
        source_bytes=source,
        source_map=imported.source_map,
        baseline=baseline,
    )

    original_open = zipfile.ZipFile.open
    requests: list[tuple[str, int]] = []

    def strict_open(self, name, mode="r", pwd=None, *, force_zip64=False):
        wrapped = original_open(
            self,
            name,
            mode=mode,
            pwd=pwd,
            force_zip64=force_zip64,
        )
        member_name = name.filename if isinstance(name, zipfile.ZipInfo) else name
        return _StrictZipReader(
            wrapped,
            name=member_name,
            requests=requests,
        )

    monkeypatch.setattr(zipfile.ZipFile, "open", strict_open)
    output = build_source_preserving_docx(
        source_bytes=source,
        source_map=imported.source_map,
        baseline=baseline,
        current=current,
        context=context,
    )

    assert output != source
    assert requests
    assert all(0 < size <= 1024 * 1024 for _, size in requests)
