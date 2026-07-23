"""Source-backed history, concurrency, and fixed-tree integrity guards."""
from __future__ import annotations

import copy
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from backend import sessions
from backend.app import create_app
from backend.spec_doc.model import DocumentStore, SpecSection
from tests.docx_fidelity_helpers import (
    DOCX_MEDIA_TYPE,
    make_fidelity_master,
    package_manifest,
    rewrite_zip_members,
    sha256,
)

_MANIFEST_ENTRY = "manifest.json"
_PROJECT_ENTRY = "project.json"


def _client() -> TestClient:
    return TestClient(create_app())


def _import_master(client: TestClient, source: bytes) -> None:
    response = client.post(
        "/api/import/master",
        files={"file": ("history-master.docx", source, DOCX_MEDIA_TYPE)},
    )
    assert response.status_code == 200, response.text


def _load_package(client: TestClient, payload: bytes):
    return client.post(
        "/api/project/load-file",
        files={"file": ("forged-history.baspec", payload, "application/octet-stream")},
    )


def _append_unsafe_redo_version(package: bytes) -> bytes:
    """Add a valid semantic snapshot that exceeds the source patch boundary."""
    with zipfile.ZipFile(io.BytesIO(package), "r") as archive:
        project = json.loads(archive.read(_PROJECT_ENTRY))

    baseline_index = project["doc"]["baseline_index"]
    assert isinstance(baseline_index, int)
    forged = copy.deepcopy(project["doc"]["versions"][baseline_index])
    forged["section"]["title"] = "FORGED UNSAFE REDO HEADING"
    project["doc"]["versions"].append(forged)
    # Keep the active state at the valid imported baseline. The forged state
    # is dormant in the redo tail and was missed by the former active-only
    # package validation.
    project["doc"]["index"] = baseline_index

    project_bytes = json.dumps(
        project, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    manifest = package_manifest(package)
    manifest["project"] = {
        "path": _PROJECT_ENTRY,
        "size_bytes": len(project_bytes),
        "sha256": sha256(project_bytes),
    }
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return rewrite_zip_members(
        package,
        replacements={
            _MANIFEST_ENTRY: manifest_bytes,
            _PROJECT_ENTRY: project_bytes,
        },
    )


def test_load_rejects_unsafe_retained_source_version_atomically(tmp_path):
    client = _client()
    source = make_fidelity_master(tmp_path)
    _import_master(client, source)
    before = client.get("/api/doc").json()
    original_before = client.get("/api/import/original")
    assert original_before.status_code == 200
    assert original_before.content == source

    package = client.get("/api/project/save")
    assert package.status_code == 200
    forged = _append_unsafe_redo_version(package.content)

    rejected = _load_package(client, forged)
    assert rejected.status_code == 400
    assert rejected.json()["ok"] is False
    assert "retained version" in rejected.json()["error"].lower()
    assert client.get("/api/doc").json() == before
    original_after = client.get("/api/import/original")
    assert original_after.status_code == 200
    assert original_after.content == source


def test_undo_and_redo_reject_while_model_turn_is_active():
    client = _client()
    seeded = client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
                {
                    "action": "add_paragraph",
                    "target_id": "pt1.a1",
                    "text": "Seed one undoable version.",
                    "status": "confirmed",
                },
            ]
        },
    )
    assert seeded.status_code == 200, seeded.text
    session = sessions.get_session()

    try:
        before_undo = client.get("/api/doc").json()
        session.turn_active = True
        undo = client.post("/api/doc/undo")
        assert undo.status_code == 409
        assert "model turn is streaming" in undo.json()["error"].lower()
        assert client.get("/api/doc").json() == before_undo

        session.turn_active = False
        assert client.post("/api/doc/undo").status_code == 200
        before_redo = client.get("/api/doc").json()
        session.turn_active = True
        redo = client.post("/api/doc/redo")
        assert redo.status_code == 409
        assert "model turn is streaming" in redo.json()["error"].lower()
        assert client.get("/api/doc").json() == before_redo
    finally:
        session.turn_active = False


@pytest.mark.parametrize(
    "numbers",
    ([2, 1, 3], [1, 2, 2], [0, 2, 3]),
)
def test_document_load_rejects_forged_fixed_part_numbers(numbers):
    snapshot = SpecSection.empty().to_dict()
    for part, number in zip(snapshot["parts"], numbers):
        part["number"] = number

    store = DocumentStore()
    with pytest.raises(ValueError, match="numbered 1/2/3"):
        store.load({"versions": [snapshot], "index": 0})
    assert store.doc.is_empty()
    assert store.index == 0
    assert len(store.versions) == 1

