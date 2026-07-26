"""Reusable semantic-template isolation, validation, and persistence tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend import sessions
from backend.app import _ai_generalized_template_document, create_app
from backend.llm.conversation import SessionState
from backend.spec_doc.model import SpecSection, iter_paragraphs
from backend.spec_doc.project import load_project
from backend.templates import (
    MAX_TEMPLATE_BYTES,
    TEMPLATE_EXTENSION,
    TemplateCatalog,
    TemplateError,
    TemplateImmutableError,
    template_bytes,
)


CURATED_ROOT = Path(__file__).resolve().parents[1] / "backend" / "templates" / "curated"


def _catalog(tmp_path: Path) -> TemplateCatalog:
    return TemplateCatalog(personal_root=tmp_path / "personal", curated_root=CURATED_ROOT)


def _starter(catalog: TemplateCatalog) -> SpecSection:
    template, source = catalog.get("curated:complete-section-starter")
    assert source == "curated"
    return SpecSection.from_dict(template["document"])


def test_exact_preview_strips_project_state_and_rebases_external_starter_statuses(tmp_path):
    catalog = _catalog(tmp_path)
    section = _starter(catalog)
    section.project_profile = {
        "city": "Seattle",
        "state_or_province": "Washington",
        "country": "United States",
        "client_name": "Private client",
    }
    section.edition_overrides = {"NFPA 13": {"edition": "2022", "basis": "Local adoption"}}
    section.suppressed_standards = {"NFPA 72": "Not in project scope"}
    paragraphs = [item[2] for item in iter_paragraphs(section)]
    paragraphs[0].status = "confirmed"
    paragraphs[0].source_item_id = "research-provenance-that-must-not-transfer"
    needs_input_ids = {p.uid for p in paragraphs if p.status == "needs_input"}

    token, preview = catalog.preview(
        section,
        name="Exact reusable starter",
        description="A semantic copy, not a project clone.",
        module_id="generic",
        binding={"workspace_id": 7, "generation": 3, "doc_version": 2},
    )

    assert token
    restored = SpecSection.from_dict(preview["document"])
    assert restored.project_profile == {}
    assert restored.edition_overrides == {}
    assert restored.suppressed_standards == {}
    for _part, _article, paragraph, _depth, _reference in iter_paragraphs(restored):
        assert paragraph.source_item_id == ""
        assert paragraph.status == (
            "needs_input" if paragraph.uid in needs_input_ids else "imported"
        )


def test_preview_token_is_version_bound_and_consumed_on_stale_commit(tmp_path):
    catalog = _catalog(tmp_path)
    binding = {"workspace_id": 11, "generation": 4, "doc_version": 1}
    token, _preview = catalog.preview(
        _starter(catalog),
        name="Bound preview",
        module_id="generic",
        binding=binding,
    )

    with pytest.raises(TemplateError, match="project changed"):
        catalog.commit_preview(token, binding={**binding, "doc_version": 2})
    assert list((tmp_path / "personal").glob(f"*{TEMPLATE_EXTENSION}")) == []
    with pytest.raises(TemplateError, match="preview expired"):
        catalog.commit_preview(token, binding=binding)


def test_preview_api_streams_progress_and_result_with_json_compatibility(
    tmp_path, monkeypatch
):
    catalog = _catalog(tmp_path)
    monkeypatch.setattr("backend.templates._CATALOG", catalog)
    sessions.get_session().doc.seed_template(_starter(catalog))
    client = TestClient(create_app())
    body = {"name": "Streamed starter", "description": "Preview", "mode": "exact"}

    streamed = client.post(
        "/api/templates/preview",
        json=body,
        headers={"Accept": "text/event-stream"},
    )
    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("text/event-stream")
    events = [
        json.loads(line.removeprefix("data: "))
        for line in streamed.text.splitlines()
        if line.startswith("data: ")
    ]
    assert events[0] == {"type": "template_status", "stage": "preparing"}
    assert events[-1]["type"] == "template_preview"
    assert events[-1]["preview"]["preview_token"]
    assert events[-1]["preview"]["template"]["name"] == "Streamed starter"

    compatible = client.post("/api/templates/preview", json=body)
    assert compatible.status_code == 200
    assert compatible.json()["preview_token"]


def test_portable_import_is_strict_and_always_assigns_a_fresh_personal_id(tmp_path):
    catalog = _catalog(tmp_path)
    curated, _source = catalog.get("curated:complete-section-starter")

    first = catalog.import_bytes(template_bytes(curated))
    second = catalog.import_bytes(template_bytes(curated))
    assert first["id"].startswith("personal:")
    assert second["id"].startswith("personal:")
    assert first["id"] != second["id"] != curated["id"]

    malformed = dict(curated)
    malformed["project"] = {"history": ["must not be accepted"]}
    with pytest.raises(TemplateError, match="unknown fields"):
        catalog.import_bytes(json.dumps(malformed).encode("utf-8"))

    encoded = template_bytes(curated).decode("utf-8")
    duplicate = encoded.replace(
        '  "kind": "buildaspec-spec-template",',
        '  "kind": "buildaspec-spec-template",\n  "kind": "buildaspec-spec-template",',
        1,
    )
    with pytest.raises(TemplateError, match="Duplicate JSON field"):
        catalog.import_bytes(duplicate.encode("utf-8"))


def test_future_dated_portable_import_is_rebased_and_immediately_readable(tmp_path):
    catalog = _catalog(tmp_path)
    portable, _source = catalog.get("curated:complete-section-starter")
    portable["created_at"] = "2099-01-01T00:00:00+00:00"
    portable["updated_at"] = "2099-01-01T00:00:00+00:00"

    created = catalog.import_bytes(template_bytes(portable))
    loaded, source = catalog.get(created["id"])
    assert source == "personal"
    assert loaded["created_at"] == loaded["updated_at"]
    assert loaded["created_at"] != portable["created_at"]
    assert created["id"] in {item["id"] for item in catalog.list().templates}
    exported, _filename = catalog.export(created["id"])
    assert json.loads(exported)["id"] == created["id"]


def test_personal_crud_and_curated_immutability(tmp_path):
    catalog = _catalog(tmp_path)
    token, _preview = catalog.preview(
        _starter(catalog), name="My starter", module_id="generic"
    )
    created = catalog.commit_preview(token)
    template_id = created["id"]

    renamed = catalog.update(
        template_id, name="Renamed starter", description="Updated description"
    )
    assert renamed["name"] == "Renamed starter"
    exported, filename = catalog.export(template_id)
    assert filename.endswith(TEMPLATE_EXTENSION)
    assert json.loads(exported)["id"] == template_id

    with pytest.raises(TemplateImmutableError):
        catalog.update(
            "curated:complete-section-starter", name="No", description="No"
        )
    with pytest.raises(TemplateImmutableError):
        catalog.delete("curated:complete-section-starter")

    catalog.delete(template_id)
    assert all(item["id"] != template_id for item in catalog.list().templates)


def test_unknown_module_falls_back_visibly_and_template_is_independent_version_zero(tmp_path):
    catalog = _catalog(tmp_path)
    token, preview = catalog.preview(
        _starter(catalog),
        name="Future-module starter",
        module_id="future_module_v9",
    )
    session = SessionState()
    result = catalog.instantiate_preview(token, session)

    assert "not installed" in result["warning"]
    assert session.module.module_id == "generic"
    assert session.doc.index == 0
    assert len(session.doc.versions) == 1
    assert session.doc.can_undo() is False
    assert session.doc.baseline_index is None
    assert session.template_origin == {
        "template_id": preview["id"],
        "name": "Future-module starter",
        "seed_block_ids": result["template_origin"]["seed_block_ids"],
    }

    first = next(iter(iter_paragraphs(session.doc.doc)))[2]
    original_text = first.text
    session.doc.begin_turn()
    session.doc.apply_edits(
        [{"action": "replace", "target_id": first.uid, "text": original_text + " Edited."}]
    )
    assert session.doc.commit_turn() is True
    assert session.doc.can_undo() is True
    assert session.doc.undo() is True
    restored_first = next(iter(iter_paragraphs(session.doc.doc)))[2]
    assert restored_first.text == original_text


def test_template_origin_round_trips_in_baspec_semantic_payload(tmp_path):
    catalog = _catalog(tmp_path)
    source = SessionState()
    catalog.instantiate("curated:complete-section-starter", source)
    payload = sessions.project_payload(source)

    restored = SessionState()
    load_project(payload, restored)
    assert restored.template_origin == source.template_origin
    assert restored.doc.to_dict() == source.doc.to_dict()
    assert restored.source_docx_bytes is None
    assert restored.doc.baseline_index is None


def test_ai_generalization_is_usage_metered_and_must_preserve_structure(
    tmp_path, monkeypatch
):
    catalog = _catalog(tmp_path)
    session = SessionState()
    session.doc.seed_template(_starter(catalog))
    generalized = SpecSection.from_dict(session.doc.doc.to_dict())
    first = next(iter(iter_paragraphs(generalized)))[2]
    first.text = first.text.replace("this Section", "the applicable work")

    class _Messages:
        def create(self, **_kwargs):
            return SimpleNamespace(
                content=[{"text": json.dumps({"document": generalized.to_dict()})}],
                usage={"input_tokens": 9, "output_tokens": 4},
            )

    monkeypatch.setattr(
        "backend.app.get_client", lambda: SimpleNamespace(messages=_Messages())
    )
    result = _ai_generalized_template_document(session)
    assert SpecSection.from_dict(result).parts[0].articles[0].uid == generalized.parts[0].articles[0].uid
    assert session.usage.snapshot()["categories"]["template"] == {
        "input_tokens": 9,
        "output_tokens": 4,
    }

    # Removing even one article turns an AI preview into an unauthorized
    # redraft, so it must fail without yielding a confirmable preview.
    generalized.parts[0].articles.pop()
    with pytest.raises(TemplateError, match="changed structure"):
        _ai_generalized_template_document(session)


def test_size_count_path_and_corrupt_library_limits_fail_closed(tmp_path, monkeypatch):
    catalog = _catalog(tmp_path)
    with pytest.raises(TemplateError, match="16 MiB"):
        catalog.import_bytes(b"x" * (MAX_TEMPLATE_BYTES + 1))
    with pytest.raises(TemplateError, match="not found"):
        catalog.get("personal:../../outside")

    personal = tmp_path / "personal"
    (personal / ("f" * 32 + TEMPLATE_EXTENSION)).write_text("{broken", encoding="utf-8")
    listing = catalog.list()
    assert listing.invalid_personal_count == 1
    assert all(item["id"] != "personal:" + "f" * 32 for item in listing.templates)

    monkeypatch.setattr("backend.templates.MAX_PERSONAL_TEMPLATES", 1)
    token, _preview = catalog.preview(
        _starter(catalog), name="Count bound", module_id="generic"
    )
    with pytest.raises(TemplateError, match="limited to 1"):
        catalog.commit_preview(token)


def test_atomic_write_failure_leaves_no_partial_template(tmp_path, monkeypatch):
    catalog = _catalog(tmp_path)
    token, preview = catalog.preview(
        _starter(catalog), name="Atomic starter", module_id="generic"
    )

    def fail_replace(_source, _target):
        raise OSError("simulated full disk")

    monkeypatch.setattr("backend.templates.os.replace", fail_replace)
    with pytest.raises(TemplateError, match="saved atomically"):
        catalog.commit_preview(token)
    expected = tmp_path / "personal" / (
        preview["id"].split(":", 1)[1] + TEMPLATE_EXTENSION
    )
    assert not expected.exists()
    assert list((tmp_path / "personal").glob("*.tmp")) == []


def test_personal_symlink_is_quarantined_when_platform_allows_it(tmp_path):
    catalog = _catalog(tmp_path)
    personal = tmp_path / "personal"
    outside = tmp_path / "outside.bastemplate"
    curated, _source = catalog.get("curated:complete-section-starter")
    outside.write_bytes(template_bytes(curated))
    link = personal / ("e" * 32 + TEMPLATE_EXTENSION)
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("This Windows account cannot create symlinks.")
    listing = catalog.list()
    assert listing.invalid_personal_count == 1
    with pytest.raises(TemplateError, match="Symbolic links"):
        catalog.get("personal:" + "e" * 32)
