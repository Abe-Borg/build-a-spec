"""The guided tour's remembered completion (``backend/onboarding_state.py``).

The empty chat's tutorial chip reads "Take the full interactive tutorial
again", without its pulse, once the tour has been finished. The packaged
app's WebView keeps no browser storage between launches (pywebview's private
mode, and a fresh port every time), so the answer lives in a small file in
the config directory — its own file, never a key in the panel layout's,
which ``PUT /api/ui/preferences`` replaces whole. Every test here points both
files at ``tmp_path``: nothing touches the real config directory.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import app_paths, onboarding_state, sessions, ui_preferences
from backend.app import DesktopSecurityConfig, create_app


@pytest.fixture
def state_path(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "config" / onboarding_state.ONBOARDING_FILENAME
    monkeypatch.setattr(onboarding_state, "default_onboarding_path", lambda: path)
    return path


@pytest.fixture
def prefs_path(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "config" / ui_preferences.PREFERENCES_FILENAME
    monkeypatch.setattr(ui_preferences, "default_preferences_path", lambda: path)
    return path


def _client() -> TestClient:
    return TestClient(create_app(_record_start_event=False))


def test_the_file_lives_in_the_app_config_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(app_paths, "app_config_dir", lambda: tmp_path)
    assert onboarding_state.default_onboarding_path() == (
        tmp_path / "onboarding_state.json"
    )
    # Its own file, never the panel layout's.
    assert onboarding_state.default_onboarding_path() != (
        ui_preferences.default_preferences_path()
    )


def test_nothing_saved_reads_as_never_completed(state_path):
    response = _client().get("/api/ui/onboarding")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "completed_version": None}
    # A read never creates the file.
    assert not state_path.exists()


def test_a_finished_tour_is_what_the_next_launch_reads(state_path):
    saved = _client().put("/api/ui/onboarding", json={"completed_version": 2})
    assert saved.status_code == 200
    assert saved.json() == {"ok": True, "completed_version": 2}
    # A new app is what a relaunch is: nothing carried in memory.
    assert _client().get("/api/ui/onboarding").json() == {
        "ok": True,
        "completed_version": 2,
    }
    assert json.loads(state_path.read_text(encoding="utf-8")) == {
        "version": onboarding_state.FORMAT_VERSION,
        "completed_version": 2,
    }


def test_completion_is_not_session_state(state_path):
    """New session and Open project replace the SESSION; finishing the tour
    is the user's, so it survives both — and a streaming turn does not hold
    it up."""
    client = _client()
    client.put("/api/ui/onboarding", json={"completed_version": 2})
    assert client.post("/api/session/reset").status_code == 200
    sessions.get_session().turn_active = True
    try:
        assert client.get("/api/ui/onboarding").json()["completed_version"] == 2
        assert (
            client.put(
                "/api/ui/onboarding", json={"completed_version": 3}
            ).status_code
            == 200
        )
    finally:
        sessions.get_session().turn_active = False
    assert client.get("/api/ui/onboarding").json()["completed_version"] == 3


def test_the_panel_layout_and_the_completion_never_erase_each_other(
    state_path, prefs_path
):
    """The layout's PUT is a full replacement. The two live in two files, so
    a layout save cannot drop the completion and a completion write cannot
    drop the layout — in either order."""
    client = _client()
    client.put("/api/ui/onboarding", json={"completed_version": 2})
    client.put(
        "/api/ui/preferences",
        json={"panels_folded": True, "hidden_panels": ["standards"]},
    )
    assert client.get("/api/ui/onboarding").json()["completed_version"] == 2
    client.put("/api/ui/onboarding", json={"completed_version": 3})
    layout = client.get("/api/ui/preferences").json()
    assert layout["panels_folded"] is True
    assert layout["hidden_panels"] == ["standards"]
    client.put(
        "/api/ui/preferences", json={"panels_folded": False, "hidden_panels": []}
    )
    assert _client().get("/api/ui/onboarding").json()["completed_version"] == 3
    assert "completed_version" not in json.loads(
        prefs_path.read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"completed_version": None},
        {"completed_version": "2"},
        # True is an int in Python; it is not a version.
        {"completed_version": True},
        {"completed_version": 2.5},
        {"completed_version": 0},
        {"completed_version": -1},
        {"completed_version": onboarding_state.MAX_COMPLETION_VERSION + 1},
    ],
)
def test_a_malformed_completion_is_refused_and_changes_nothing(state_path, body):
    client = _client()
    client.put("/api/ui/onboarding", json={"completed_version": 2})
    before = state_path.read_bytes()
    refused = client.put("/api/ui/onboarding", json=body)
    assert refused.status_code == 422
    assert refused.json()["ok"] is False
    assert refused.json()["code"] == "validation_error"
    assert state_path.read_bytes() == before


def test_a_failed_write_says_so_and_leaves_the_old_file_whole(
    state_path, monkeypatch
):
    client = _client()
    client.put("/api/ui/onboarding", json={"completed_version": 2})
    before = state_path.read_bytes()

    def refuse(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", refuse)
    failed = client.put("/api/ui/onboarding", json={"completed_version": 3})
    assert failed.status_code == 500
    assert failed.json()["ok"] is False
    assert failed.json()["code"] == "write_failed"
    assert state_path.read_bytes() == before
    # The temporary file never outlives the failure.
    assert sorted(path.name for path in state_path.parent.iterdir()) == [
        state_path.name
    ]


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"not json at all", None),
        (b"[2]", None),
        (b"2", None),
        (b'{"completed_version": "2"}', None),
        (b'{"completed_version": true}', None),
        (b'{"completed_version": 2.0}', None),
        (b'{"completed_version": 0}', None),
        (b'{"completed_version": null}', None),
        (b"{}", None),
        (b'{"completed_version": 2}', 2),
        # An older Notepad saves UTF-8 with a byte-order mark.
        (b'\xef\xbb\xbf{"completed_version": 2}', 2),
        # Written by a newer build: unknown keys are ignored, not fatal.
        (b'{"version": 9, "completed_version": 5, "something_new": [1]}', 5),
    ],
)
def test_the_reader_forgives_a_file_the_app_did_not_write(
    state_path, content, expected
):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_bytes(content)
    assert onboarding_state.load_onboarding_state() == (
        onboarding_state.OnboardingState(completed_version=expected)
    )
    assert _client().get("/api/ui/onboarding").json() == {
        "ok": True,
        "completed_version": expected,
    }


def test_a_file_far_larger_than_the_app_writes_is_not_read(state_path):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"completed_version": 2}) + " " * onboarding_state.MAX_FILE_BYTES,
        encoding="utf-8",
    )
    assert onboarding_state.load_onboarding_state() == (
        onboarding_state.OnboardingState()
    )


def test_the_completion_sits_behind_the_launch_token_like_every_route(
    state_path,
):
    host, port = "127.0.0.1", 43125
    origin = f"http://{host}:{port}"
    token = "test-token-" + ("z" * 64)
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
    assert client.get("/api/ui/onboarding").status_code == 401
    refused = client.put("/api/ui/onboarding", json={"completed_version": 2})
    assert refused.status_code == 401
    assert not state_path.exists()
    headers = {"X-BuildASpec-Token": token}
    assert client.get("/api/ui/onboarding", headers=headers).status_code == 200
    assert (
        client.put(
            "/api/ui/onboarding", json={"completed_version": 2}, headers=headers
        ).status_code
        == 200
    )
    assert json.loads(state_path.read_text(encoding="utf-8"))[
        "completed_version"
    ] == 2
