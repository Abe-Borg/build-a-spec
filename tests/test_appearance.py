"""The window's remembered colour scheme (``backend/appearance.py``).

Settings -> Appearance offers Spec Surgeon's five colour schemes, and the
choice has to outlive the launch. The packaged app's WebView keeps no browser
storage between launches (pywebview's private mode, and a fresh port every
time), so the choice lives in a small file in the config directory, read by
``main.py`` before the window exists and by the page once it is up. Every test
here points that file at ``tmp_path``: nothing touches the real config
directory.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from backend import app_paths, appearance, ui_preferences
from backend.app import DesktopSecurityConfig, create_app

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


@pytest.fixture
def appearance_path(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "config" / appearance.APPEARANCE_FILENAME
    monkeypatch.setattr(appearance, "default_appearance_path", lambda: path)
    return path


def _client() -> TestClient:
    return TestClient(create_app(_record_start_event=False))


def test_the_file_lives_in_the_app_config_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(app_paths, "app_config_dir", lambda: tmp_path)
    assert appearance.default_appearance_path() == tmp_path / "appearance.json"
    # Never the panel layout's file: a layout save replaces that one whole.
    assert appearance.APPEARANCE_FILENAME != ui_preferences.PREFERENCES_FILENAME


def test_nothing_saved_reads_as_warm_clay(appearance_path):
    response = _client().get("/api/ui/appearance")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "theme": "clay"}
    assert appearance.DEFAULT_THEME == "clay"
    # A read never creates the file.
    assert not appearance_path.exists()


@pytest.mark.parametrize("theme", appearance.THEMES)
def test_a_saved_scheme_is_what_the_next_launch_reads(appearance_path, theme):
    saved = _client().put("/api/ui/appearance", json={"theme": theme})
    assert saved.status_code == 200
    assert saved.json() == {"ok": True, "theme": theme}
    assert json.loads(appearance_path.read_text(encoding="utf-8")) == {
        "version": appearance.FORMAT_VERSION,
        "theme": theme,
    }
    # What main.py reads at launch, and what the page reads once it is up.
    assert appearance.load_appearance().theme == theme
    assert _client().get("/api/ui/appearance").json() == {"ok": True, "theme": theme}


@pytest.mark.parametrize(
    "raw",
    [
        {"theme": "dark"},  # not a scheme of this build
        {"theme": "Linen"},  # ids are exact
        {"theme": ""},
        {"theme": 3},
        {"theme": None},
        {"theme": ["linen"]},
        {},
        ["linen"],
        "linen",
        None,
    ],
)
def test_anything_unreadable_as_a_scheme_is_the_default(appearance_path, raw):
    appearance_path.parent.mkdir(parents=True)
    appearance_path.write_text(json.dumps(raw), encoding="utf-8")
    assert appearance.load_appearance().theme == "clay"


def test_a_broken_or_oversized_file_is_the_default_never_an_error(appearance_path):
    appearance_path.parent.mkdir(parents=True)
    appearance_path.write_text("{not json", encoding="utf-8")
    assert appearance.load_appearance().theme == "clay"
    appearance_path.write_bytes(b"\xff\xfe\x00garbage")
    assert appearance.load_appearance().theme == "clay"
    padding = " " * (appearance.MAX_FILE_BYTES + 1)
    appearance_path.write_text('{"theme": "linen"}' + padding, encoding="utf-8")
    assert appearance.load_appearance().theme == "clay"
    assert _client().get("/api/ui/appearance").json() == {"ok": True, "theme": "clay"}


def test_a_file_saved_by_an_older_notepad_still_reads(appearance_path):
    appearance_path.parent.mkdir(parents=True)
    appearance_path.write_text('{"theme": "carbon"}', encoding="utf-8-sig")
    assert appearance.load_appearance().theme == "carbon"


@pytest.mark.parametrize("body", [{"theme": "dark"}, {"theme": 3}, {"theme": None}, {}])
def test_the_route_refuses_a_scheme_it_does_not_know_and_keeps_the_file(
    appearance_path, body
):
    client = _client()
    assert client.put("/api/ui/appearance", json={"theme": "linen"}).status_code == 200
    before = appearance_path.read_bytes()
    refused = client.put("/api/ui/appearance", json=body)
    assert refused.status_code == 422
    assert appearance_path.read_bytes() == before


def test_save_refuses_an_unknown_scheme(appearance_path):
    with pytest.raises(ValueError):
        appearance.save_appearance(appearance.Appearance(theme="dark"))
    assert not appearance_path.exists()


def test_a_failed_write_says_so(appearance_path, monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(appearance, "save_appearance", fail)
    response = _client().put("/api/ui/appearance", json={"theme": "linen"})
    assert response.status_code == 500
    payload = response.json()
    assert payload["ok"] is False
    assert payload["code"] == "write_failed"
    assert "color scheme could not be saved" in payload["error"]


def test_the_write_is_atomic_and_leaves_no_temp_file(appearance_path):
    appearance.save_appearance(appearance.Appearance(theme="graphite"))
    appearance.save_appearance(appearance.Appearance(theme="blueprint"))
    assert sorted(os.listdir(appearance_path.parent)) == ["appearance.json"]
    assert appearance.load_appearance().theme == "blueprint"


def test_the_scheme_and_the_panel_layout_never_erase_each_other(
    appearance_path, monkeypatch
):
    layout_path = appearance_path.parent / ui_preferences.PREFERENCES_FILENAME
    monkeypatch.setattr(ui_preferences, "default_preferences_path", lambda: layout_path)
    client = _client()
    assert client.put("/api/ui/appearance", json={"theme": "linen"}).status_code == 200
    layout = {"panels_folded": True, "hidden_panels": ["standards"]}
    assert client.put("/api/ui/preferences", json=layout).status_code == 200
    assert client.get("/api/ui/appearance").json()["theme"] == "linen"
    assert client.put("/api/ui/appearance", json={"theme": "carbon"}).status_code == 200
    saved_layout = client.get("/api/ui/preferences").json()
    assert saved_layout["panels_folded"] is True
    assert saved_layout["hidden_panels"] == ["standards"]


def test_the_scheme_sits_behind_the_launch_token_like_every_route(appearance_path):
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
    assert client.get("/api/ui/appearance").status_code == 401
    assert (
        client.put("/api/ui/appearance", json={"theme": "linen"}).status_code == 401
    )
    assert not appearance_path.exists()
    headers = {"X-BuildASpec-Token": token, "Origin": origin}
    assert (
        client.put(
            "/api/ui/appearance", json={"theme": "linen"}, headers=headers
        ).status_code
        == 200
    )
    assert client.get("/api/ui/appearance", headers=headers).json()["theme"] == "linen"


# --- The page, the stylesheet and the shell know the same schemes ----------


def test_the_page_and_the_backend_know_the_same_schemes():
    theme_ts = (FRONTEND / "src" / "lib" / "theme.ts").read_text(encoding="utf-8")
    ids = tuple(re.findall(r'^\s*id: "([a-z]+)",$', theme_ts, re.M))
    assert ids == appearance.THEMES


def test_each_window_background_is_its_schemes_own_background():
    css = (FRONTEND / "src" / "index.css").read_text(encoding="utf-8")
    base = re.search(r"@theme \{(.*?)\n\}", css, re.S)
    assert base is not None
    backgrounds = {
        "clay": re.search(r"--color-bg:\s*(#[0-9a-f]{6});", base.group(1)).group(1)
    }
    for theme, body in re.findall(r':root\[data-theme="([a-z]+)"\] \{(.*?)\}', css, re.S):
        backgrounds[theme] = re.search(r"--color-bg:\s*(#[0-9a-f]{6});", body).group(1)
    assert backgrounds == appearance.BACKGROUNDS
    assert tuple(appearance.BACKGROUNDS) == appearance.THEMES


def test_the_window_opens_on_the_saved_schemes_page_and_background():
    url = main._url_with_boot_fragment(
        main._url_with_theme("http://127.0.0.1:9999/", "linen"), "nonce-for-test"
    )
    parsed = urllib.parse.urlsplit(url)
    assert urllib.parse.parse_qs(parsed.query) == {"theme": ["linen"]}
    # The boot nonce still rides the fragment, never the query.
    assert urllib.parse.parse_qs(parsed.fragment) == {"buildaspec_boot": ["nonce-for-test"]}
    # Another query parameter is kept; an old theme is replaced, not doubled.
    kept = main._url_with_theme("http://127.0.0.1:5173/?x=1&theme=clay", "carbon")
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(kept).query) == {
        "x": ["1"],
        "theme": ["carbon"],
    }
    # An id this build does not know opens the default.
    unknown = main._url_with_theme("http://127.0.0.1:9999/", "dark")
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(unknown).query) == {
        "theme": ["clay"]
    }
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "theme = appearance.load_appearance().theme" in source
    assert "_url_with_theme(frontend_base_url, theme)" in source
    assert "background_color=appearance.BACKGROUNDS[theme]" in source
