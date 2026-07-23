"""P1 project-container acceptance tests.

Source-backed projects are binary ``.baspec`` OPC-like containers, not JSON
with an unbounded base64 field. The inner DOCX remains byte-exact and every
load validates the outer package, its manifest, the source package, and the
source-to-semantic binding before mutating the live session.
"""
from __future__ import annotations

import io
import json
import warnings
import zipfile

from fastapi.testclient import TestClient

from backend import sessions
from backend.app import create_app
from backend.spec_doc.source_package import inspect_docx_package
from tests.docx_fidelity_helpers import (
    DOCX_MEDIA_TYPE,
    TARGET_EDITED_TEXT,
    assert_only_target_text_changed,
    assert_untouched_parts_identical,
    assert_valid_docx_package,
    make_fidelity_master,
    make_numbered_island_master,
    mark_zip_members_encrypted,
    package_manifest,
    rewrite_zip_members,
    sha256,
)


PROJECT_PACKAGE_KIND = "buildaspec-project-package"
PROJECT_PACKAGE_FORMAT = 1
PROJECT_ENTRY = "project.json"
SOURCE_ENTRY = "source/original.docx"
MANIFEST_ENTRY = "manifest.json"


def _client() -> TestClient:
    return TestClient(create_app())


def _import_master(client: TestClient, source: bytes):
    response = client.post(
        "/api/import/master",
        files={
            "file": (
                "client-fidelity-master.docx",
                source,
                DOCX_MEDIA_TYPE,
            )
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _load_file(
    client: TestClient,
    payload: bytes,
    *,
    filename: str = "fixture.baspec",
    media_type: str = "application/octet-stream",
):
    return client.post(
        "/api/project/load-file",
        files={"file": (filename, payload, media_type)},
    )


def _outer_parts(payload: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


def _replace_target(client: TestClient):
    return client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "replace",
                    "target_id": "pt1.a1.p1",
                    "text": TARGET_EDITED_TEXT,
                    "status": "confirmed",
                }
            ]
        },
    )


def _rebuild_consistent_outer(
    package: bytes,
    *,
    source: bytes | None = None,
    project: dict | None = None,
) -> bytes:
    """Recompute the manifest around changed payloads.

    This models a deliberate tamperer who can update ordinary hashes. Such a
    package must still fail if its source does not match the semantic baseline
    and source map; SHA-256 here is integrity metadata, not authentication.
    """

    parts = _outer_parts(package)
    manifest = json.loads(parts[MANIFEST_ENTRY])
    if source is None:
        source = parts[SOURCE_ENTRY]
    if project is None:
        project = json.loads(parts[PROJECT_ENTRY])
    project_bytes = json.dumps(
        project, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    manifest["project"].update(
        {
            "path": PROJECT_ENTRY,
            "size_bytes": len(project_bytes),
            "sha256": sha256(project_bytes),
        }
    )
    manifest["source_docx"].update(
        {
            "path": SOURCE_ENTRY,
            "size_bytes": len(source),
            "sha256": sha256(source),
        }
    )
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return rewrite_zip_members(
        package,
        replacements={
            MANIFEST_ENTRY: manifest_bytes,
            PROJECT_ENTRY: project_bytes,
            SOURCE_ENTRY: source,
        },
    )


def _source_with_changed_baseline(source: bytes) -> bytes:
    parts = _outer_parts(source)
    document_xml = parts["word/document.xml"]
    changed = document_xml.replace(b"NFPA 13-2019", b"NFPA 13-2020")
    assert changed != document_xml
    payload = rewrite_zip_members(
        source, replacements={"word/document.xml": changed}
    )
    inspect_docx_package(payload)
    return payload


def _update_project_report_for_source(project: dict, source: bytes) -> dict:
    updated = json.loads(json.dumps(project))
    report = updated["import_report"]
    report["sha256"] = sha256(source)
    report["size_bytes"] = len(source)
    return updated


def test_saved_baspec_has_bounded_manifest_and_stores_exact_source(tmp_path):
    client = _client()
    source = make_fidelity_master(tmp_path)
    imported = _import_master(client, source)

    saved = client.get("/api/project/save")
    assert saved.status_code == 200
    assert ".baspec" in saved.headers["content-disposition"].lower()

    with zipfile.ZipFile(io.BytesIO(saved.content), "r") as archive:
        assert [info.filename for info in archive.infolist()] == [
            MANIFEST_ENTRY,
            PROJECT_ENTRY,
            SOURCE_ENTRY,
        ]
        assert archive.testzip() is None
        assert archive.getinfo(SOURCE_ENTRY).compress_type == zipfile.ZIP_STORED
        assert archive.read(SOURCE_ENTRY) == source
        project_bytes = archive.read(PROJECT_ENTRY)

    manifest = package_manifest(saved.content)
    assert manifest["kind"] == PROJECT_PACKAGE_KIND
    assert manifest["format"] == PROJECT_PACKAGE_FORMAT
    assert manifest["project"] == {
        "path": PROJECT_ENTRY,
        "size_bytes": len(project_bytes),
        "sha256": sha256(project_bytes),
    }
    source_meta = manifest["source_docx"]
    assert source_meta["path"] == SOURCE_ENTRY
    assert source_meta["filename"] == imported["import_report"]["filename"]
    assert source_meta["size_bytes"] == len(source)
    assert source_meta["sha256"] == sha256(source)


def test_project_resume_restores_source_patch_state_and_undo(tmp_path):
    client = _client()
    source = make_fidelity_master(tmp_path)
    _import_master(client, source)

    baseline_package = client.get("/api/project/save").content
    assert client.post("/api/session/reset").status_code == 200
    loaded_baseline = _load_file(client, baseline_package)
    assert loaded_baseline.status_code == 200, loaded_baseline.text
    assert loaded_baseline.json()["source_available"] is True
    assert client.get("/api/import/original").content == source
    assert client.get(
        "/api/export/docx", params={"mode": "source"}
    ).content == source

    assert _replace_target(client).status_code == 200
    edited = client.get("/api/export/docx", params={"mode": "source"}).content
    assert_untouched_parts_identical(source, edited)
    assert_only_target_text_changed(source, edited)

    edited_package = client.get("/api/project/save").content
    assert client.post("/api/session/reset").status_code == 200
    loaded_edited = _load_file(client, edited_package)
    assert loaded_edited.status_code == 200, loaded_edited.text
    assert loaded_edited.json()["source_available"] is True
    assert client.get("/api/import/original").content == source
    assert client.get(
        "/api/export/docx", params={"mode": "source"}
    ).content == edited

    assert client.post("/api/doc/undo").status_code == 200
    assert client.get(
        "/api/export/docx", params={"mode": "source"}
    ).content == source


def test_project_resume_restores_pending_structural_patch_plan(tmp_path):
    """A .baspec keeps semantic structure edits, never a rewritten source."""

    client = _client()
    source = make_numbered_island_master(tmp_path)
    _import_master(client, source)

    edited = client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "add_paragraph",
                    "target_id": "pt1.a1",
                    "position": 1,
                    "text": "Provide tamper switches at all supervised valves.",
                    "status": "confirmed",
                },
                {
                    "action": "move",
                    "target_id": "pt1.a1.p3",
                    "position": 0,
                },
            ]
        },
    )
    assert edited.status_code == 200, edited.text
    expected_docx = client.get(
        "/api/export/docx", params={"mode": "source"}
    ).content
    assert expected_docx != source
    assert_untouched_parts_identical(source, expected_docx)
    assert_valid_docx_package(expected_docx)

    package = client.get("/api/project/save")
    assert package.status_code == 200
    with zipfile.ZipFile(io.BytesIO(package.content), "r") as archive:
        # Persistence always retains the immutable source, not the generated
        # structural export; the edit plan lives in semantic history.
        assert archive.read(SOURCE_ENTRY) == source

    assert client.post("/api/session/reset").status_code == 200
    loaded = _load_file(client, package.content)
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["source_available"] is True
    assert loaded.json()["preservation_ready"] is True
    assert client.get("/api/import/original").content == source
    assert client.get(
        "/api/export/docx", params={"mode": "source"}
    ).content == expected_docx

    assert client.post("/api/doc/undo").status_code == 200
    assert client.get(
        "/api/export/docx", params={"mode": "source"}
    ).content == source
    assert client.post("/api/doc/redo").status_code == 200
    assert client.get(
        "/api/export/docx", params={"mode": "source"}
    ).content == expected_docx


def test_save_before_import_baseline_retains_source_for_redo(tmp_path):
    client = _client()
    source = make_fidelity_master(tmp_path)
    _import_master(client, source)
    assert client.post("/api/doc/undo").status_code == 200
    prebaseline = client.get("/api/doc").json()["doc"]
    assert prebaseline["version"] == {"index": 0, "count": 2}

    package = client.get("/api/project/save")
    assert package.status_code == 200
    assert package_manifest(package.content)["source_docx"] is not None

    assert client.post("/api/session/reset").status_code == 200
    loaded = _load_file(client, package.content)
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["source_available"] is True
    assert loaded.json()["doc"]["version"] == {"index": 0, "count": 2}
    assert client.get("/api/import/original").content == source

    assert client.post("/api/doc/redo").status_code == 200
    assert client.get(
        "/api/export/docx", params={"mode": "source"}
    ).content == source


def test_new_branch_before_import_baseline_saves_without_stale_source_map(tmp_path):
    client = _client()
    source = make_fidelity_master(tmp_path)
    imported = _import_master(client, source)
    report = imported["import_report"]
    assert client.post("/api/doc/undo").status_code == 200

    fresh_branch = client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "replace",
                    "target_id": "sec",
                    "text": "FRESH BRANCH",
                    "numbering": "00 00 01",
                },
                {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
                {
                    "action": "add_paragraph",
                    "target_id": "pt1.a1",
                    "text": "This draft deliberately abandoned the imported branch.",
                    "status": "confirmed",
                },
            ]
        },
    )
    assert fresh_branch.status_code == 200, fresh_branch.text
    assert fresh_branch.json()["baseline_index"] is None

    package = client.get("/api/project/save")
    assert package.status_code == 200
    assert package_manifest(package.content)["source_docx"] is None
    parts = _outer_parts(package.content)
    project = json.loads(parts[PROJECT_ENTRY])
    assert "source_map" not in project
    assert project["import_report"] == report

    assert client.post("/api/session/reset").status_code == 200
    loaded = _load_file(client, package.content)
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["source_available"] is False
    assert loaded.json()["preservation_ready"] is False
    assert loaded.json()["import_report"] == report
    assert loaded.json()["doc"]["section"] == {
        "number": "00 00 01",
        "title": "FRESH BRANCH",
    }
    assert client.get("/api/import/original").status_code == 409


def test_legacy_format_one_json_loads_without_claiming_source_fidelity(tmp_path):
    client = _client()
    source = make_fidelity_master(tmp_path)
    _import_master(client, source)

    # The inner payload remains the existing format-1 project schema. Remove
    # P1-only source mapping metadata to model a genuine P0 JSON save.
    legacy = json.loads(json.dumps(sessions.project_payload(sessions.get_session())))
    for key in ("source_map", "source_body_map", "source_docx"):
        legacy.pop(key, None)
    legacy_bytes = json.dumps(legacy, ensure_ascii=False).encode("utf-8")

    assert client.post("/api/session/reset").status_code == 200
    loaded = _load_file(
        client,
        legacy_bytes,
        filename="legacy-buildaspec-project.json",
        media_type="application/json",
    )
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["source_available"] is False
    assert loaded.json()["preservation_ready"] is False
    assert client.get("/api/import/original").status_code == 409
    assert client.get(
        "/api/export/docx", params={"mode": "source"}
    ).status_code == 409
    normalized = client.get(
        "/api/export/docx", params={"mode": "normalized"}
    )
    assert normalized.status_code == 200
    assert_valid_docx_package(normalized.content)


def _assert_rejected_load_is_atomic(client: TestClient, payload: bytes, source: bytes):
    before = client.get("/api/doc").json()
    original_before = client.get("/api/import/original")
    assert original_before.status_code == 200
    assert original_before.content == source

    rejected = _load_file(client, payload)
    assert rejected.status_code == 400
    assert rejected.json()["ok"] is False
    assert isinstance(rejected.json().get("error"), str)
    assert client.get("/api/doc").json() == before
    original_after = client.get("/api/import/original")
    assert original_after.status_code == 200
    assert original_after.content == source


def test_project_load_rejects_source_hash_tamper_atomically(tmp_path):
    client = _client()
    source = make_fidelity_master(tmp_path)
    _import_master(client, source)
    package = client.get("/api/project/save").content
    tampered_source = bytearray(source)
    tampered_source[len(tampered_source) // 2] ^= 0x01
    tampered = rewrite_zip_members(
        package, replacements={SOURCE_ENTRY: bytes(tampered_source)}
    )
    _assert_rejected_load_is_atomic(client, tampered, source)


def test_project_load_rejects_unsafe_outer_member_atomically(tmp_path):
    client = _client()
    source = make_fidelity_master(tmp_path)
    _import_master(client, source)
    package = client.get("/api/project/save").content
    tampered = rewrite_zip_members(
        package, additions=[("../outside-project.txt", b"unsafe")]
    )
    _assert_rejected_load_is_atomic(client, tampered, source)


def test_project_load_rejects_duplicate_outer_member_atomically(tmp_path):
    client = _client()
    source = make_fidelity_master(tmp_path)
    _import_master(client, source)
    package = client.get("/api/project/save").content
    project_bytes = _outer_parts(package)[PROJECT_ENTRY]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        tampered = rewrite_zip_members(
            package, additions=[(PROJECT_ENTRY, project_bytes)]
        )
    _assert_rejected_load_is_atomic(client, tampered, source)


def test_project_load_rejects_encrypted_outer_members_atomically(tmp_path):
    client = _client()
    source = make_fidelity_master(tmp_path)
    _import_master(client, source)
    package = client.get("/api/project/save").content
    _assert_rejected_load_is_atomic(
        client, mark_zip_members_encrypted(package), source
    )


def test_project_load_rejects_missing_declared_source_atomically(tmp_path):
    client = _client()
    source = make_fidelity_master(tmp_path)
    _import_master(client, source)
    package = client.get("/api/project/save").content
    tampered = rewrite_zip_members(package, omit={SOURCE_ENTRY})
    _assert_rejected_load_is_atomic(client, tampered, source)


def test_recomputed_hashes_cannot_pair_a_different_source_with_the_project(
    tmp_path,
):
    client = _client()
    source = make_fidelity_master(tmp_path)
    _import_master(client, source)
    package = client.get("/api/project/save").content
    parts = _outer_parts(package)
    different_source = _source_with_changed_baseline(source)
    project = _update_project_report_for_source(
        json.loads(parts[PROJECT_ENTRY]), different_source
    )
    tampered = _rebuild_consistent_outer(
        package, source=different_source, project=project
    )
    _assert_rejected_load_is_atomic(client, tampered, source)


def test_recomputed_outer_hashes_do_not_make_a_forged_source_map_trusted(tmp_path):
    client = _client()
    source = make_fidelity_master(tmp_path)
    _import_master(client, source)
    package = client.get("/api/project/save").content
    project = json.loads(_outer_parts(package)[PROJECT_ENTRY])
    binding = project["source_map"]["bindings"]["pt1.a1.p1"]
    binding["body_child_index"] += 1
    tampered = _rebuild_consistent_outer(package, project=project)
    _assert_rejected_load_is_atomic(client, tampered, source)


def test_recomputed_hashes_do_not_bypass_inner_docx_safety(tmp_path):
    client = _client()
    source = make_fidelity_master(tmp_path)
    imported = _import_master(client, source)
    package = client.get("/api/project/save").content
    parts = _outer_parts(package)
    unsafe_source = rewrite_zip_members(
        source, additions=[("../inner-escape.xml", b"unsafe")]
    )
    project = _update_project_report_for_source(
        json.loads(parts[PROJECT_ENTRY]), unsafe_source
    )
    project["import_report"]["zip_member_count"] = (
        imported["import_report"]["zip_member_count"] + 1
    )
    project["import_report"]["zip_uncompressed_bytes"] = (
        imported["import_report"]["zip_uncompressed_bytes"] + len(b"unsafe")
    )
    tampered = _rebuild_consistent_outer(
        package, source=unsafe_source, project=project
    )
    _assert_rejected_load_is_atomic(client, tampered, source)
