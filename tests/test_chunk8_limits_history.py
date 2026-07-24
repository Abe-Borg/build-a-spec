"""Chunk 8 stress coverage for bounded packages and retained histories.

The large DOCX fixture is generated as a highly-compressible ZIP stream, so
the test reaches the production uncompressed ceilings without checking a
large binary into the repository or holding the expanded payload in memory.
"""
from __future__ import annotations

import asyncio
import copy
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from backend import sessions
from backend.app import create_app
from backend.spec_doc.model import SpecSection, apply_edits
from backend.spec_doc.source_package import (
    MAX_UPLOAD_BYTES,
    MAX_ZIP_MEMBERS,
    MAX_ZIP_MEMBER_BYTES,
    MAX_ZIP_UNCOMPRESSED_BYTES,
    SourcePackageError,
    UploadTooLargeError,
    inspect_docx_package,
    read_upload_bounded,
)
from tests.docx_fidelity_helpers import (
    DOCX_MEDIA_TYPE,
    make_fidelity_master,
    package_manifest,
    rewrite_zip_members,
    sha256,
)


_PROJECT_ENTRY = "project.json"
_MANIFEST_ENTRY = "manifest.json"
_TARGET_UID = "pt1.a1.p1"
_SAFE_HISTORY_COUNT = 64
_STREAM_CHUNK_BYTES = 1024 * 1024


class _GeneratedUpload:
    """Async upload that materializes only the bytes each read requests."""

    def __init__(self, size: int) -> None:
        self.remaining = size
        self.max_requested = 0

    async def read(self, size: int) -> bytes:
        self.max_requested = max(self.max_requested, size)
        emitted = min(size, self.remaining)
        self.remaining -= emitted
        return b"x" * emitted


def _near_limit_docx(source: bytes) -> bytes:
    """Return a valid container whose declared total equals the hard limit."""
    with zipfile.ZipFile(io.BytesIO(source), "r") as original:
        original_infos = original.infolist()
        original_total = sum(info.file_size for info in original_infos)
        remaining = MAX_ZIP_UNCOMPRESSED_BYTES - original_total
        assert remaining > 2 * MAX_ZIP_MEMBER_BYTES
        streamed_sizes = (
            MAX_ZIP_MEMBER_BYTES,
            MAX_ZIP_MEMBER_BYTES,
            remaining - 2 * MAX_ZIP_MEMBER_BYTES,
        )
        assert 0 < streamed_sizes[-1] <= MAX_ZIP_MEMBER_BYTES

        output = io.BytesIO()
        with zipfile.ZipFile(
            output,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as rebuilt:
            for info in original_infos:
                rebuilt.writestr(info, original.read(info.filename))
            zero_chunk = b"\x00" * _STREAM_CHUNK_BYTES
            for suffix, member_size in zip("abc", streamed_sizes):
                info = zipfile.ZipInfo(
                    f"word/media/near-limit-{suffix}.png",
                    date_time=(2024, 1, 1, 0, 0, 0),
                )
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                with rebuilt.open(info, "w") as member:
                    full_chunks, tail = divmod(member_size, len(zero_chunk))
                    for _index in range(full_chunks):
                        member.write(zero_chunk)
                    if tail:
                        member.write(zero_chunk[:tail])
            padding_members = MAX_ZIP_MEMBERS - len(original_infos) - len(
                streamed_sizes
            )
            assert padding_members > 0
            for index in range(padding_members):
                info = zipfile.ZipInfo(
                    f"customXml/near-limit-pad-{index:04d}.bin",
                    date_time=(2024, 1, 1, 0, 0, 0),
                )
                info.compress_type = zipfile.ZIP_STORED
                info.external_attr = 0o600 << 16
                rebuilt.writestr(info, b"")
    return output.getvalue()


def _client() -> TestClient:
    return TestClient(create_app())


def _import_master(client: TestClient, source: bytes) -> None:
    response = client.post(
        "/api/import/master",
        files={"file": ("history-master.docx", source, DOCX_MEDIA_TYPE)},
    )
    assert response.status_code == 200, response.text


def _rewrite_project(package: bytes, project: dict) -> bytes:
    project_bytes = json.dumps(
        project,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    manifest = package_manifest(package)
    manifest["project"] = {
        "path": _PROJECT_ENTRY,
        "size_bytes": len(project_bytes),
        "sha256": sha256(project_bytes),
    }
    manifest_bytes = json.dumps(
        manifest,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return rewrite_zip_members(
        package,
        replacements={
            _MANIFEST_ENTRY: manifest_bytes,
            _PROJECT_ENTRY: project_bytes,
        },
    )


def _many_version_package(package: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(package), "r") as archive:
        project = json.loads(archive.read(_PROJECT_ENTRY))
    baseline_index = project["doc"]["baseline_index"]
    assert isinstance(baseline_index, int)
    baseline_dict = project["doc"]["versions"][baseline_index]
    baseline = SpecSection.from_dict(baseline_dict)

    safe_versions: list[dict] = []
    for index in range(_SAFE_HISTORY_COUNT):
        candidate, applied = apply_edits(
            baseline,
            [
                {
                    "action": "replace",
                    "target_id": _TARGET_UID,
                    "text": (
                        "Install system per NFPA 13-2022; retained history "
                        f"state {index:03d}."
                    ),
                    "status": "confirmed",
                }
            ],
        )
        assert applied
        safe_versions.append(candidate.to_dict())

    # End on an exact baseline copy. The deep edited history remains reachable
    # by undo, while a source export at the active no-op state must be byte-for-
    # byte identical to the retained master.
    project["doc"]["versions"] = (
        project["doc"]["versions"][: baseline_index + 1]
        + safe_versions
        + [copy.deepcopy(baseline_dict)]
    )
    project["doc"]["index"] = len(project["doc"]["versions"]) - 1
    return _rewrite_project(package, project)


def _append_unsafe_tail(package: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(package), "r") as archive:
        project = json.loads(archive.read(_PROJECT_ENTRY))
    baseline_index = project["doc"]["baseline_index"]
    assert isinstance(baseline_index, int)
    unsafe = copy.deepcopy(project["doc"]["versions"][baseline_index])
    unsafe["section"]["title"] = "UNSUPPORTED LATE HISTORY HEADING"
    project["doc"]["versions"].append(unsafe)
    # Leave the active index on the valid no-op state so the bad snapshot is a
    # dormant redo tail. Loading must still reject the whole package.
    return _rewrite_project(package, project)


def test_production_upload_boundary_accepts_exact_and_rejects_next_byte():
    exact_upload = _GeneratedUpload(MAX_UPLOAD_BYTES)
    exact = asyncio.run(read_upload_bounded(exact_upload))
    assert len(exact) == MAX_UPLOAD_BYTES
    assert exact[:1] == b"x" and exact[-1:] == b"x"
    assert exact_upload.max_requested <= _STREAM_CHUNK_BYTES

    over_upload = _GeneratedUpload(MAX_UPLOAD_BYTES + 1)
    with pytest.raises(UploadTooLargeError):
        asyncio.run(read_upload_bounded(over_upload))
    assert over_upload.remaining == 0
    assert over_upload.max_requested <= _STREAM_CHUNK_BYTES


def test_docx_accepts_exact_production_uncompressed_boundaries(tmp_path):
    source = _near_limit_docx(make_fidelity_master(tmp_path))
    # Compression keeps this runtime fixture inside the upload ceiling while
    # its expanded package reaches both production ZIP limits exactly.
    assert len(source) < MAX_UPLOAD_BYTES
    with zipfile.ZipFile(io.BytesIO(source), "r") as archive:
        infos = archive.infolist()
        member_count = len(infos)
        expanded_total = sum(info.file_size for info in infos)
        largest_member = max(info.file_size for info in infos)
    assert expanded_total == MAX_ZIP_UNCOMPRESSED_BYTES
    assert largest_member == MAX_ZIP_MEMBER_BYTES
    assert member_count == MAX_ZIP_MEMBERS

    info = inspect_docx_package(source, max_members=member_count)
    assert info.member_count == member_count
    assert info.uncompressed_bytes == MAX_ZIP_UNCOMPRESSED_BYTES

    # All three counters are inclusive. Reducing any exact ceiling by one
    # must reject from declared metadata before decompression broadens access.
    with pytest.raises(SourcePackageError):
        inspect_docx_package(source, max_members=member_count - 1)
    with pytest.raises(SourcePackageError):
        inspect_docx_package(
            source,
            max_total_uncompressed=MAX_ZIP_UNCOMPRESSED_BYTES - 1,
        )
    with pytest.raises(SourcePackageError):
        inspect_docx_package(
            source,
            max_member_uncompressed=MAX_ZIP_MEMBER_BYTES - 1,
        )


def test_many_source_versions_restore_exactly_and_reject_late_unsafe_tail(
    tmp_path,
):
    client = _client()
    source = make_fidelity_master(tmp_path)
    _import_master(client, source)
    saved = client.get("/api/project/save")
    assert saved.status_code == 200, saved.text

    many_versions = _many_version_package(saved.content)
    assert client.post("/api/session/reset").status_code == 200
    loaded = client.post(
        "/api/project/load-file",
        files={
            "file": (
                "many-safe-versions.baspec",
                many_versions,
                "application/octet-stream",
            )
        },
    )
    assert loaded.status_code == 200, loaded.text
    session = sessions.get_session()
    expected_count = 2 + _SAFE_HISTORY_COUNT + 1
    assert len(session.doc.versions) == expected_count
    assert session.doc.index == expected_count - 1

    first_export = client.get("/api/export/docx", params={"mode": "source"})
    second_export = client.get("/api/export/docx", params={"mode": "source"})
    assert first_export.status_code == 200, first_export.text
    assert second_export.status_code == 200, second_export.text
    assert first_export.content == second_export.content == source

    assert client.post("/api/doc/undo").status_code == 200
    edited_export = client.get("/api/export/docx", params={"mode": "source"})
    assert edited_export.status_code == 200, edited_export.text
    assert edited_export.content != source
    assert client.post("/api/doc/redo").status_code == 200
    assert client.get("/api/export/docx", params={"mode": "source"}).content == source

    before_doc = client.get("/api/doc").json()
    before_versions = copy.deepcopy(session.doc.versions)
    before_index = session.doc.index
    forged = _append_unsafe_tail(many_versions)
    rejected = client.post(
        "/api/project/load-file",
        files={
            "file": (
                "late-unsafe-version.baspec",
                forged,
                "application/octet-stream",
            )
        },
    )
    assert rejected.status_code == 400
    assert "retained version" in rejected.json()["error"].lower()
    assert client.get("/api/doc").json() == before_doc
    assert session.doc.versions == before_versions
    assert session.doc.index == before_index
    original = client.get("/api/import/original")
    assert original.status_code == 200
    assert original.content == source
    final_export = client.get("/api/export/docx", params={"mode": "source"})
    assert final_export.status_code == 200, final_export.text
    assert final_export.content == source
