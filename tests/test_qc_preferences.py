"""Machine-local transport, environment locks, and per-run snapshots."""
from __future__ import annotations

import json
import os
import threading

import pytest
from fastapi.testclient import TestClient

from backend import app_paths, onboarding_state, qc_preferences, sessions, settings, ui_preferences
from backend.app import create_app
from backend.qc.engine import QCResult, build_qc_input_manifest, qc_input_fingerprint, qc_version_fingerprint
from backend.qc.runner import QCRunner
from backend.spec_doc.model import DocumentStore
from backend.spec_modules import DEFAULT_MODULE

ENV = "BUILD_A_SPEC_QC_BATCH_VERIFICATION"


@pytest.fixture
def prefs_path(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.setattr(app_paths, "app_config_dir", lambda: tmp_path)
    # create_app samples the GUI regime; restore the setting after each test.
    monkeypatch.setattr(
        settings, "QC_BATCH_VERIFICATION", settings.QC_BATCH_VERIFICATION_DEFAULT
    )
    return qc_preferences.default_preferences_path()


def client():
    return TestClient(create_app(_record_start_event=False))


def draft():
    store = DocumentStore()
    store.begin_turn()
    store.apply_edits([
        {"action": "replace", "target_id": "sec", "text": "WET-PIPE SPRINKLER SYSTEMS", "numbering": "21 13 13"},
        {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
    ])
    store.commit_turn()
    return store


def test_default_is_streamed_without_creating_a_file(prefs_path):
    """Streamed since the cost program's streamed stagger (docs/as-built.md,
    "Final QC streams its verifier seats, leaders first"); batched before."""
    assert settings.QC_BATCH_VERIFICATION_DEFAULT is False
    assert client().get("/api/ui/qc-preferences").json() == {
        "ok": True, "batch_verification": False, "locked": False,
    }
    assert not prefs_path.exists()
    assert prefs_path.parent == onboarding_state.default_onboarding_path().parent
    assert prefs_path != ui_preferences.default_preferences_path()


@pytest.mark.parametrize("batch", [True, False])
def test_unset_environment_uses_saved_choice_across_launches(prefs_path, batch):
    assert client().put("/api/ui/qc-preferences", json={"batch_verification": batch}).status_code == 200
    assert client().get("/api/ui/qc-preferences").json() == {
        "ok": True, "batch_verification": batch, "locked": False,
    }
    assert settings.QC_BATCH_VERIFICATION is batch


@pytest.mark.parametrize("value,batch", [
    ("0", False), ("false", False), ("no", False), ("off", False),
    (" OFF ", False), ("1", True), ("true", True), ("", True),
])
def test_present_environment_locks_and_does_not_overwrite_saved_choice(prefs_path, monkeypatch, value, batch):
    qc_preferences.save_batch_verification(not batch)
    before = prefs_path.read_bytes()
    monkeypatch.setenv(ENV, value)
    c = client()
    expected = {"ok": True, "batch_verification": batch, "locked": True}
    assert c.get("/api/ui/qc-preferences").json() == expected
    assert c.put("/api/ui/qc-preferences", json={"batch_verification": not batch}).json() == expected
    assert qc_preferences.resolve_batch_verification(not batch) is batch
    assert prefs_path.read_bytes() == before


@pytest.mark.parametrize("raw", ['oops', '[]', '{}', '{"batch_verification":"false"}', '{"batch_verification":0}', 'x' * (qc_preferences.MAX_FILE_BYTES + 1)])
def test_bad_files_read_as_the_shipped_default(prefs_path, raw):
    prefs_path.write_text(raw)
    assert qc_preferences.load_batch_verification() is settings.QC_BATCH_VERIFICATION_DEFAULT
    assert qc_preferences.load_batch_verification() is False


def test_a_choice_saved_before_the_default_flipped_is_kept(prefs_path):
    """The default moved from Batch to Stream; a saved Batch is the user's."""
    prefs_path.write_text('{"version": 1, "batch_verification": true}')
    assert qc_preferences.load_batch_verification() is True
    assert client().get("/api/ui/qc-preferences").json()["batch_verification"] is True


def test_bom_is_read_and_layout_and_onboarding_saves_are_independent(prefs_path):
    prefs_path.write_text('\ufeff{"batch_verification":false}', encoding="utf-8")
    c = client()
    c.put("/api/ui/preferences", json={"panels_folded": True})
    c.put("/api/ui/onboarding", json={"completed_version": 2})
    assert c.get("/api/ui/qc-preferences").json()["batch_verification"] is False
    c.put("/api/ui/qc-preferences", json={"batch_verification": True})
    assert c.get("/api/ui/preferences").json()["panels_folded"] is True
    assert c.get("/api/ui/onboarding").json()["completed_version"] == 2
    assert "batch_verification" not in json.loads(ui_preferences.default_preferences_path().read_text())


@pytest.mark.parametrize("body", [{}, {"batch_verification": None}, {"batch_verification": "false"}, {"batch_verification": 0}])
def test_writes_are_strict(prefs_path, body):
    qc_preferences.save_batch_verification(False)
    before = prefs_path.read_bytes()
    assert client().put("/api/ui/qc-preferences", json=body).status_code == 422
    assert prefs_path.read_bytes() == before


def test_failed_atomic_write_preserves_choice(prefs_path, monkeypatch):
    qc_preferences.save_batch_verification(False)
    c = client()
    before = prefs_path.read_bytes()
    def fail(*_args, **_kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", fail)
    assert c.put("/api/ui/qc-preferences", json={"batch_verification": True}).status_code == 500
    assert prefs_path.read_bytes() == before
    assert settings.QC_BATCH_VERIFICATION is False
    assert list(prefs_path.parent.glob(".buildaspec-qc-preferences-*")) == []


@pytest.mark.parametrize("env,saved,requested,expected", [
    (None, False, None, False), (None, True, False, False),
    (None, False, True, True), ("true", False, False, True),
    ("off", True, True, False), (None, True, None, True),
])
def test_start_resolves_transport_for_this_run_only(prefs_path, monkeypatch, env, saved, requested, expected):
    qc_preferences.save_batch_verification(saved)
    if env is not None:
        monkeypatch.setenv(ENV, env)
    c = client()
    session = sessions.get_session()
    session.doc = draft()
    captured = {}
    def start(**kwargs):
        captured.update(kwargs)
        return True
    monkeypatch.setattr(session.qc, "start", start)
    monkeypatch.setattr("backend.app.get_client", lambda: object())
    body = {} if requested is None else {"batch_verification": requested}
    assert c.post("/api/qc/start", json=body).status_code == 200
    assert captured["batch_verification"] is expected
    assert qc_preferences.load_batch_verification() is saved


def test_settings_change_uses_existing_manifest_staleness(prefs_path):
    c = client()
    store = draft()
    manifest = build_qc_input_manifest(store.doc, None, DEFAULT_MODULE, version_index=store.index, batch_verification=settings.QC_BATCH_VERIFICATION, consolidation_enabled=settings.QC_CONSOLIDATION, model=settings.QC_MODEL, max_tokens=settings.QC_MAX_TOKENS)
    result = QCResult(version_index=store.index, version_fingerprint=qc_version_fingerprint(store.doc), input_manifest=manifest, input_fingerprint=qc_input_fingerprint(manifest))
    assert result.matches_inputs(store.index, store.doc, None, DEFAULT_MODULE)
    c.put("/api/ui/qc-preferences", json={"batch_verification": not settings.QC_BATCH_VERIFICATION_DEFAULT})
    assert not result.matches_inputs(store.index, store.doc, None, DEFAULT_MODULE)


def test_running_worker_keeps_its_start_transport(prefs_path, monkeypatch):
    c = client()
    entered = threading.Event()
    release = threading.Event()
    captured = []
    def run(*_args, batch_verification, **_kwargs):
        entered.set()
        assert release.wait(5)
        captured.append(batch_verification)
        return QCResult(execution_status="complete")
    monkeypatch.setattr("backend.qc.runner.run_final_qc", run)
    store = draft()
    runner = QCRunner()
    assert runner.start(section=store.doc, profile=None, module=DEFAULT_MODULE, client=object(), model=settings.QC_MODEL, max_tokens=settings.QC_MAX_TOKENS, version_index=store.index, batch_verification=True)
    try:
        assert entered.wait(5)
        c.put("/api/ui/qc-preferences", json={"batch_verification": False})
    finally:
        release.set()
        runner._thread.join(5)
    assert captured == [True]
