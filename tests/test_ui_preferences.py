"""The panel tray's remembered layout (``backend/ui_preferences.py``).

The document panel's panels can be folded away and chosen one by one, and
the choice has to outlive the launch. The packaged app's WebView keeps no
browser storage between launches (pywebview's private mode, and a fresh port
every time), so the choice lives in a small file in the config directory.
Every test here points that file at ``tmp_path``: nothing touches the real
config directory.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import app_paths, sessions, ui_preferences
from backend.app import DesktopSecurityConfig, create_app


@pytest.fixture
def prefs_path(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "config" / ui_preferences.PREFERENCES_FILENAME
    monkeypatch.setattr(ui_preferences, "default_preferences_path", lambda: path)
    return path


def _client() -> TestClient:
    return TestClient(create_app(_record_start_event=False))


def test_the_file_lives_in_the_app_config_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(app_paths, "app_config_dir", lambda: tmp_path)
    assert ui_preferences.default_preferences_path() == (
        tmp_path / "ui_preferences.json"
    )


def test_nothing_saved_reads_as_every_panel_shown(prefs_path):
    response = _client().get("/api/ui/preferences")
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "panels_folded": False,
        "hidden_panels": [],
    }
    # A read never creates the file.
    assert not prefs_path.exists()


def test_a_saved_layout_is_what_the_next_launch_reads(prefs_path):
    saved = _client().put(
        "/api/ui/preferences",
        json={"panels_folded": True, "hidden_panels": ["standards", "documents"]},
    )
    assert saved.status_code == 200
    assert saved.json() == {
        "ok": True,
        "panels_folded": True,
        "hidden_panels": ["standards", "documents"],
    }
    # A new app is what a relaunch is: nothing carried in memory.
    reread = _client().get("/api/ui/preferences").json()
    assert reread["panels_folded"] is True
    assert reread["hidden_panels"] == ["standards", "documents"]
    on_disk = json.loads(prefs_path.read_text(encoding="utf-8"))
    assert on_disk == {
        "version": ui_preferences.FORMAT_VERSION,
        "panels_folded": True,
        "hidden_panels": ["standards", "documents"],
    }


def test_the_layout_is_not_session_state(prefs_path):
    """New session and Open project replace the SESSION; the layout is the
    user's, so it survives both — and a streaming turn does not hold it up."""
    client = _client()
    client.put(
        "/api/ui/preferences",
        json={"panels_folded": True, "hidden_panels": ["review"]},
    )
    assert client.post("/api/session/reset").status_code == 200
    sessions.get_session().turn_active = True
    try:
        assert client.get("/api/ui/preferences").json()["hidden_panels"] == [
            "review"
        ]
        changed = client.put(
            "/api/ui/preferences",
            json={"panels_folded": False, "hidden_panels": []},
        )
        assert changed.status_code == 200
    finally:
        sessions.get_session().turn_active = False
    assert client.get("/api/ui/preferences").json()["panels_folded"] is False


def test_the_same_panel_twice_is_stored_once(prefs_path):
    saved = _client().put(
        "/api/ui/preferences",
        json={"panels_folded": False, "hidden_panels": ["qc", "qc", "issues"]},
    )
    assert saved.json()["hidden_panels"] == ["qc", "issues"]


@pytest.mark.parametrize(
    "body",
    [
        # A string that reads as true would fold every panel over a typo.
        {"panels_folded": "true", "hidden_panels": []},
        {"panels_folded": 1, "hidden_panels": []},
        {"panels_folded": False, "hidden_panels": ["Not An Id"]},
        {"panels_folded": False, "hidden_panels": ["../../escape"]},
        {"panels_folded": False, "hidden_panels": [7]},
        {"panels_folded": False, "hidden_panels": "review"},
        {
            "panels_folded": False,
            "hidden_panels": [
                f"panel-{index}"
                for index in range(ui_preferences.MAX_HIDDEN_PANELS + 1)
            ],
        },
    ],
)
def test_a_malformed_layout_is_refused_and_changes_nothing(prefs_path, body):
    client = _client()
    client.put(
        "/api/ui/preferences",
        json={"panels_folded": True, "hidden_panels": ["research"]},
    )
    before = prefs_path.read_bytes()
    refused = client.put("/api/ui/preferences", json=body)
    assert refused.status_code == 422
    assert refused.json()["ok"] is False
    assert refused.json()["code"] == "validation_error"
    assert prefs_path.read_bytes() == before


def test_a_failed_write_says_so_and_leaves_the_old_file_whole(
    prefs_path, monkeypatch
):
    client = _client()
    client.put(
        "/api/ui/preferences",
        json={"panels_folded": False, "hidden_panels": ["research"]},
    )
    before = prefs_path.read_bytes()

    def refuse(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", refuse)
    failed = client.put(
        "/api/ui/preferences",
        json={"panels_folded": True, "hidden_panels": []},
    )
    assert failed.status_code == 500
    assert failed.json()["ok"] is False
    assert failed.json()["code"] == "write_failed"
    assert prefs_path.read_bytes() == before
    # The temporary file never outlives the failure.
    assert sorted(path.name for path in prefs_path.parent.iterdir()) == [
        prefs_path.name
    ]


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"not json at all", ui_preferences.UiPreferences()),
        (b"[1, 2, 3]", ui_preferences.UiPreferences()),
        # Only a real boolean folds the tray.
        (
            b'{"panels_folded": "true", "hidden_panels": ["qc"]}',
            ui_preferences.UiPreferences(False, ("qc",)),
        ),
        (
            b'{"panels_folded": true, "hidden_panels": "qc"}',
            ui_preferences.UiPreferences(True, ()),
        ),
        # Ids that are not ids are dropped; the rest survive, once each.
        (
            b'{"panels_folded": true, "hidden_panels": '
            b'["qc", "QC", "../x", 3, null, "qc", "open-items"]}',
            ui_preferences.UiPreferences(True, ("qc", "open-items")),
        ),
        # An older Notepad saves UTF-8 with a byte-order mark.
        (
            b'\xef\xbb\xbf{"panels_folded": true, "hidden_panels": []}',
            ui_preferences.UiPreferences(True, ()),
        ),
        # Written by a newer build: unknown keys are ignored, not fatal.
        (
            b'{"version": 9, "panels_folded": true, "hidden_panels": [],'
            b' "something_new": {"a": 1}}',
            ui_preferences.UiPreferences(True, ()),
        ),
    ],
)
def test_the_reader_forgives_a_file_the_app_did_not_write(
    prefs_path, content, expected
):
    prefs_path.parent.mkdir(parents=True, exist_ok=True)
    prefs_path.write_bytes(content)
    assert ui_preferences.load_preferences() == expected
    response = _client().get("/api/ui/preferences").json()
    assert response["panels_folded"] is expected.panels_folded
    assert response["hidden_panels"] == list(expected.hidden_panels)


def test_the_reader_bounds_what_it_keeps_and_what_it_reads(prefs_path):
    prefs_path.parent.mkdir(parents=True, exist_ok=True)
    many = [f"panel-{index}" for index in range(ui_preferences.MAX_HIDDEN_PANELS * 2)]
    prefs_path.write_text(
        json.dumps({"panels_folded": True, "hidden_panels": many}),
        encoding="utf-8",
    )
    kept = ui_preferences.load_preferences()
    assert kept.hidden_panels == tuple(many[: ui_preferences.MAX_HIDDEN_PANELS])

    # A file far larger than the app ever writes is not read at all.
    prefs_path.write_text(
        json.dumps({"panels_folded": True, "hidden_panels": ["qc"]})
        + " " * ui_preferences.MAX_FILE_BYTES,
        encoding="utf-8",
    )
    assert ui_preferences.load_preferences() == ui_preferences.UiPreferences()


def test_the_layout_sits_behind_the_launch_token_like_every_route(prefs_path):
    host, port = "127.0.0.1", 43124
    origin = f"http://{host}:{port}"
    token = "test-token-" + ("y" * 64)
    client = TestClient(
        create_app(
            desktop_security=DesktopSecurityConfig(
                boot_nonce="boot-nonce-for-test-only",
                api_token=token,
                bound_host=host,
                bound_port=port,
                allowed_hosts=(f"{host}:{port}",),
                allowed_origins=(origin,),
            ),
            _record_start_event=False,
        ),
        base_url=origin,
    )
    assert client.get("/api/ui/preferences").status_code == 401
    refused = client.put(
        "/api/ui/preferences",
        json={"panels_folded": True, "hidden_panels": []},
    )
    assert refused.status_code == 401
    assert not prefs_path.exists()
    headers = {"X-BuildASpec-Token": token}
    assert client.get("/api/ui/preferences", headers=headers).status_code == 200
    assert (
        client.put(
            "/api/ui/preferences",
            json={"panels_folded": True, "hidden_panels": []},
            headers=headers,
        ).status_code
        == 200
    )
    assert json.loads(prefs_path.read_text(encoding="utf-8"))["panels_folded"]
