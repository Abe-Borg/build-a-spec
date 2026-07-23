"""Reusable immutable context coverage for source-preserving DOCX gates.

The context is deliberately process-local derived state.  These tests keep
the public preservation functions honest: once a source is indexed, callers
can reuse that exact context without rescanning immutable package state, while
stale or forged cache objects still fail closed.
"""
from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError, fields, replace
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from lxml import etree

from backend import sessions
from backend.app import create_app
import backend.spec_doc.source_patch as source_patch_module
from backend.spec_doc.importer import parse_master_docx
from backend.spec_doc.model import SpecSection, apply_edits
from backend.spec_doc.source_patch import (
    SourcePatchError,
    build_source_patch_context,
    build_source_preserving_docx,
    source_patch_readiness,
    validate_source_map_identity,
    validate_source_transition,
)
from tests.docx_fidelity_helpers import (
    DOCX_MEDIA_TYPE,
    TARGET_EDITED_TEXT,
    make_fidelity_master,
    package_manifest,
    rewrite_zip_members,
    sha256,
)


@pytest.fixture
def source_inputs(tmp_path):
    filename = "source-context-cache-master.docx"
    source = make_fidelity_master(tmp_path, filename=filename)
    imported = parse_master_docx(tmp_path / filename)
    assert imported.source_map is not None
    baseline = SpecSection.from_dict(imported.section.to_dict())
    return source, imported.source_map, baseline


def _edited_candidate(baseline: SpecSection) -> SpecSection:
    candidate, _applied = apply_edits(
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
    return candidate


def _client() -> TestClient:
    return TestClient(create_app())


def _import_source(client: TestClient, source: bytes) -> None:
    response = client.post(
        "/api/import/master",
        files={
            "file": (
                "source-context-cache-master.docx",
                source,
                DOCX_MEDIA_TYPE,
            )
        },
    )
    assert response.status_code == 200, response.text


def _repeat_valid_retained_versions(package: bytes, *, count: int) -> bytes:
    with zipfile.ZipFile(io.BytesIO(package), "r") as archive:
        project = json.loads(archive.read("project.json"))
    baseline_index = project["doc"]["baseline_index"]
    assert isinstance(baseline_index, int)
    baseline = project["doc"]["versions"][baseline_index]
    project["doc"]["versions"] = (
        project["doc"]["versions"][:baseline_index]
        + [copy.deepcopy(baseline) for _index in range(count)]
    )
    project["doc"]["index"] = len(project["doc"]["versions"]) - 1

    project_bytes = json.dumps(
        project,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    manifest = package_manifest(package)
    manifest["project"] = {
        "path": "project.json",
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
            "manifest.json": manifest_bytes,
            "project.json": project_bytes,
        },
    )


def test_source_patch_context_is_deeply_immutable_and_has_no_lxml_tree(
    source_inputs,
):
    source, source_map, baseline = source_inputs
    context = build_source_patch_context(
        source_bytes=source,
        source_map=source_map,
        baseline=baseline,
    )

    with pytest.raises(FrozenInstanceError):
        context.source_sha256 = "0" * 64
    with pytest.raises(TypeError):
        context.numbering_usage_counts["999"] = 1
    with pytest.raises(TypeError):
        context.paragraph_templates[999] = next(
            iter(context.paragraph_templates.values())
        )

    assert context.body_inventory
    with pytest.raises(FrozenInstanceError):
        context.body_inventory[0].expanded_name = "forged"
    assert context.xml_index is not None
    with pytest.raises(FrozenInstanceError):
        context.xml_index.encoding = "UTF-16"
    assert context.raw_zip_archive is not None
    with pytest.raises(FrozenInstanceError):
        context.raw_zip_archive.offset_base = 99

    # A cached source context may retain immutable bytes and frozen records,
    # but never a mutable lxml tree or element shared across validations.
    for descriptor in fields(context):
        value = getattr(context, descriptor.name)
        assert not isinstance(value, (etree._Element, etree._ElementTree))


def test_stale_context_hash_fails_closed_without_silent_reindex(
    source_inputs, monkeypatch
):
    source, source_map, baseline = source_inputs
    context = build_source_patch_context(
        source_bytes=source,
        source_map=source_map,
        baseline=baseline,
    )
    stale = replace(context, source_sha256="0" * 64)
    baseline_before = baseline.to_dict()

    def unexpected_rebuild(**_kwargs):
        pytest.fail("a supplied stale context must be rejected, not rebuilt")

    monkeypatch.setattr(
        source_patch_module,
        "build_source_patch_context",
        unexpected_rebuild,
    )

    readiness = source_patch_readiness(
        source_bytes=source,
        source_map=source_map,
        baseline=baseline,
        current=baseline,
        context=stale,
    )
    assert readiness.ready is False
    assert readiness.blockers[0].blocker == "source_hash_mismatch"

    for gate in (validate_source_map_identity, validate_source_transition):
        kwargs = {
            "source_bytes": source,
            "source_map": source_map,
            "baseline": baseline,
            "context": stale,
        }
        if gate is validate_source_transition:
            kwargs["current"] = baseline
        with pytest.raises(SourcePatchError) as caught:
            gate(**kwargs)
        assert caught.value.blocker == "source_hash_mismatch"

    with pytest.raises(SourcePatchError) as caught:
        build_source_preserving_docx(
            source_bytes=source,
            source_map=source_map,
            baseline=baseline,
            current=baseline,
            context=stale,
        )
    assert caught.value.blocker == "source_hash_mismatch"
    assert baseline.to_dict() == baseline_before


def test_repeated_cached_export_is_deterministic_and_does_not_mutate_context(
    source_inputs,
):
    source, source_map, baseline = source_inputs
    context = build_source_patch_context(
        source_bytes=source,
        source_map=source_map,
        baseline=baseline,
    )
    candidate = _edited_candidate(baseline)
    xml_index = context.xml_index
    raw_archive = context.raw_zip_archive
    inventory = context.body_inventory
    numbering_counts = dict(context.numbering_usage_counts)

    first = build_source_preserving_docx(
        source_bytes=source,
        source_map=source_map,
        baseline=baseline,
        current=candidate,
        context=context,
    )
    second = build_source_preserving_docx(
        source_bytes=source,
        source_map=source_map,
        baseline=baseline,
        current=candidate,
        context=context,
    )

    assert first != source
    assert second == first
    assert context.source_bytes is source
    assert context.xml_index is xml_index
    assert context.raw_zip_archive is raw_archive
    assert context.body_inventory is inventory
    assert dict(context.numbering_usage_counts) == numbering_counts


def test_repeated_public_gates_do_not_rescan_the_cached_source(
    source_inputs, monkeypatch
):
    source, source_map, baseline = source_inputs
    context = build_source_patch_context(
        source_bytes=source,
        source_map=source_map,
        baseline=baseline,
    )
    candidate = _edited_candidate(baseline)

    real_xml_index = source_patch_module.build_source_xml_index
    real_inspect = source_patch_module.inspect_docx_package
    source_rescans: list[str] = []
    context_rebuilds = 0

    def context_builder_spy(**_kwargs):
        nonlocal context_rebuilds
        context_rebuilds += 1
        pytest.fail("public gates rebuilt an explicitly supplied source context")

    def xml_index_spy(payload, *args, **kwargs):
        if payload == context.document_xml:
            source_rescans.append("document_xml")
        return real_xml_index(payload, *args, **kwargs)

    def package_inspection_spy(payload, *args, **kwargs):
        if payload == source:
            source_rescans.append("source_package")
        return real_inspect(payload, *args, **kwargs)

    def immutable_source_scan(name):
        def fail(*_args, **_kwargs):
            source_rescans.append(name)
            pytest.fail(f"cached source state was rescanned through {name}")

        return fail

    monkeypatch.setattr(
        source_patch_module,
        "build_source_patch_context",
        context_builder_spy,
    )
    monkeypatch.setattr(
        source_patch_module,
        "build_source_xml_index",
        xml_index_spy,
    )
    monkeypatch.setattr(
        source_patch_module,
        "inspect_docx_package",
        package_inspection_spy,
    )
    for name in (
        "_read_document_xml_and_inventory",
        "detect_global_source_blockers",
        "_numbering_context",
        "parse_raw_zip_archive",
    ):
        monkeypatch.setattr(
            source_patch_module,
            name,
            immutable_source_scan(name),
        )

    validate_source_map_identity(
        source_bytes=source,
        source_map=source_map,
        baseline=baseline,
        context=context,
    )
    assert source_patch_readiness(
        source_bytes=source,
        source_map=source_map,
        baseline=baseline,
        current=baseline,
        context=context,
    ).ready
    validate_source_transition(
        source_bytes=source,
        source_map=source_map,
        baseline=baseline,
        current=baseline,
        context=context,
    )
    assert (
        build_source_preserving_docx(
            source_bytes=source,
            source_map=source_map,
            baseline=baseline,
            current=baseline,
            context=context,
        )
        == source
    )

    for _index in range(2):
        assert source_patch_readiness(
            source_bytes=source,
            source_map=source_map,
            baseline=baseline,
            current=candidate,
            context=context,
        ).ready
        validate_source_transition(
            source_bytes=source,
            source_map=source_map,
            baseline=baseline,
            current=candidate,
            context=context,
        )
        exported = build_source_preserving_docx(
            source_bytes=source,
            source_map=source_map,
            baseline=baseline,
            current=candidate,
            context=context,
        )
        assert exported != source

    assert context_rebuilds == 0
    assert source_rescans == []


def test_missing_in_memory_context_is_built_lazily_once_and_reused(
    tmp_path, monkeypatch
):
    source = make_fidelity_master(
        tmp_path,
        filename="source-context-cache-master.docx",
    )
    client = _client()
    _import_source(client, source)
    session = sessions.get_session()
    assert session.source_patch_context is not None

    # Simulate a source-backed SessionState created before the process-local
    # cache field existed. The first ordinary read rebuilds it; every later
    # read/edit/export must share that one object.
    session.source_patch_context = None
    real_builder = source_patch_module.build_source_patch_context
    builds = 0

    def builder_spy(**kwargs):
        nonlocal builds
        builds += 1
        return real_builder(**kwargs)

    monkeypatch.setattr(
        source_patch_module,
        "build_source_patch_context",
        builder_spy,
    )

    snapshot = client.get("/api/doc")
    assert snapshot.status_code == 200
    rebuilt = session.source_patch_context
    assert rebuilt is not None
    assert builds == 1

    assert client.get("/api/readiness").status_code == 200
    edited = client.post(
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
    assert edited.status_code == 200, edited.text
    exported = client.get("/api/export/docx", params={"mode": "source"})
    assert exported.status_code == 200, exported.text
    assert exported.content != source
    assert builds == 1
    assert session.source_patch_context is rebuilt

    assert client.post("/api/session/reset").status_code == 200
    assert session.source_patch_context is None


def test_import_builds_context_once_through_the_public_builder_seam(
    tmp_path,
    monkeypatch,
):
    source = make_fidelity_master(
        tmp_path,
        filename="source-context-cache-master.docx",
    )
    real_builder = source_patch_module.build_source_patch_context
    builds = 0

    def builder_spy(**kwargs):
        nonlocal builds
        builds += 1
        return real_builder(**kwargs)

    monkeypatch.setattr(
        source_patch_module,
        "build_source_patch_context",
        builder_spy,
    )
    client = _client()
    _import_source(client, source)

    assert builds == 1
    assert sessions.get_session().source_patch_context is not None


def test_context_is_not_serialized_and_is_rebuilt_once_after_project_load(
    tmp_path, monkeypatch
):
    source = make_fidelity_master(
        tmp_path,
        filename="source-context-cache-master.docx",
    )
    client = _client()
    _import_source(client, source)
    session = sessions.get_session()
    original_context = session.source_patch_context
    assert original_context is not None

    package = client.get("/api/project/save")
    assert package.status_code == 200, package.text
    with zipfile.ZipFile(io.BytesIO(package.content), "r") as archive:
        assert set(archive.namelist()) == {
            "manifest.json",
            "project.json",
            "source/original.docx",
        }
        project_bytes = archive.read("project.json")
    project = json.loads(project_bytes)
    serialized = json.dumps(project, sort_keys=True)
    assert "source_patch_context" not in serialized
    assert "xml_index" not in serialized
    assert "raw_zip_archive" not in serialized

    assert client.post("/api/session/reset").status_code == 200
    assert session.source_patch_context is None

    real_builder = source_patch_module.build_source_patch_context
    builds = 0

    def builder_spy(**kwargs):
        nonlocal builds
        builds += 1
        return real_builder(**kwargs)

    monkeypatch.setattr(
        source_patch_module,
        "build_source_patch_context",
        builder_spy,
    )
    loaded = client.post(
        "/api/project/load-file",
        files={
            "file": (
                "source-context-cache.baspec",
                package.content,
                "application/octet-stream",
            )
        },
    )
    assert loaded.status_code == 200, loaded.text
    restored_context = session.source_patch_context
    assert restored_context is not None
    assert restored_context is not original_context
    assert restored_context.source_sha256 == original_context.source_sha256
    assert restored_context.document_xml_sha256 == (
        original_context.document_xml_sha256
    )
    assert builds == 1


def test_one_context_validates_one_hundred_retained_history_versions(
    tmp_path, monkeypatch
):
    source = make_fidelity_master(
        tmp_path,
        filename="source-context-cache-master.docx",
    )
    client = _client()
    _import_source(client, source)
    saved = client.get("/api/project/save")
    assert saved.status_code == 200, saved.text
    package = _repeat_valid_retained_versions(saved.content, count=100)
    assert client.post("/api/session/reset").status_code == 200

    real_builder = source_patch_module.build_source_patch_context
    builds = 0

    def builder_spy(**kwargs):
        nonlocal builds
        builds += 1
        return real_builder(**kwargs)

    monkeypatch.setattr(
        source_patch_module,
        "build_source_patch_context",
        builder_spy,
    )
    loaded = client.post(
        "/api/project/load-file",
        files={
            "file": (
                "source-context-cache-100-versions.baspec",
                package,
                "application/octet-stream",
            )
        },
    )
    assert loaded.status_code == 200, loaded.text
    session = sessions.get_session()
    assert len(session.doc.versions) == 101
    assert session.doc.baseline_index == 1
    assert session.source_patch_context is not None
    assert builds == 1

    # Payload generation and export after restoration must keep using the
    # same context that validated all retained undo/redo states.
    assert client.get("/api/doc").status_code == 200
    assert client.get("/api/readiness").status_code == 200
    exported = client.get("/api/export/docx", params={"mode": "source"})
    assert exported.status_code == 200, exported.text
    assert exported.content == source
    assert builds == 1


def test_lazy_context_failure_makes_project_save_fail_closed_without_mutation(
    tmp_path,
):
    source = make_fidelity_master(
        tmp_path,
        filename="source-context-cache-master.docx",
    )
    client = _client()
    _import_source(client, source)
    session = sessions.get_session()
    before_doc = session.doc.snapshot()
    retained_map = session.source_docx_map

    # Simulate a corrupted legacy/in-memory attachment whose derived cache is
    # absent. The persistence boundary must translate construction failure to
    # a normal project-package rejection, never an HTTP 500 or partial reset.
    corrupt_source = b"not-a-docx"
    session.source_docx_bytes = corrupt_source
    session.source_patch_context = None
    rejected = client.get("/api/project/save")

    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["ok"] is False
    assert session.doc.snapshot() == before_doc
    assert session.source_docx_bytes is corrupt_source
    assert session.source_docx_map is retained_map
    assert session.source_patch_context is None
