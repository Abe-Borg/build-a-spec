"""Build-critical packaging invariants.

These guard the Windows release pipeline against changes that only fail on
a Windows build machine (or, worse, silently ship a broken installer):
the app icon must exist and be wired into the PyInstaller spec and the
Inno Setup installer, and the installer's stable AppId must never change
(it is what makes upgrades install in place). Hermetic — pure file reads,
no build tools required.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PKG = REPO_ROOT / "packaging" / "windows"
ICON = PKG / "assets" / "BuildASpec.ico"

# The frozen Build-a-Spec AppId — must be stable across every release so an
# install upgrades in place. NEVER change this (CLAUDE.md / installer.iss).
FROZEN_APP_ID = "{{89E58C42-A4F6-49F8-8FCB-1147CB0186DB}"


def test_app_icon_exists_and_is_a_valid_multi_size_ico():
    assert ICON.is_file(), f"missing app icon: {ICON}"
    data = ICON.read_bytes()
    # ICO header: reserved(0) + type(1 = icon) little-endian, then image count.
    assert data[:4] == b"\x00\x00\x01\x00", "not a valid .ico (bad header)"
    image_count = int.from_bytes(data[4:6], "little")
    # A single-size icon means make_icon.py silently dropped the larger
    # resolutions (the classic "save from a small base image" bug); require
    # a real multi-resolution set including the 256px frame.
    assert image_count >= 5, f"icon should embed several sizes, got {image_count}"
    # Each ICONDIRENTRY is 16 bytes after the 6-byte header; byte 0 is the
    # width (0 encodes 256).
    widths = {data[6 + i * 16] for i in range(image_count)}
    assert 0 in widths or 256 in widths, "icon is missing the 256px frame"
    assert 16 in widths, "icon is missing the 16px frame"


def test_pyinstaller_spec_embeds_the_icon():
    spec = (PKG / "build-a-spec.spec").read_text(encoding="utf-8")
    assert "BuildASpec.ico" in spec, "the PyInstaller spec must set the exe icon"
    assert "icon=None" not in spec, "the exe icon is still unset (icon=None)"


def test_installer_references_the_icon():
    iss = (PKG / "installer.iss").read_text(encoding="utf-8")
    assert "SetupIconFile=assets\\BuildASpec.ico" in iss


def test_installer_appid_is_frozen():
    iss = (PKG / "installer.iss").read_text(encoding="utf-8")
    assert f"AppId={FROZEN_APP_ID}" in iss, (
        "the installer AppId changed — this breaks in-place upgrades and "
        "must never happen"
    )


def test_installer_gates_webview2_on_the_bootstrapper_being_present():
    """The WebView2 bundling is preprocessor-guarded so a manual build
    without the (gitignored) bootstrapper still compiles."""
    iss = (PKG / "installer.iss").read_text(encoding="utf-8")
    assert "MicrosoftEdgeWebview2Setup.exe" in iss
    assert "#ifdef HaveWebView2" in iss
    assert "IsWebView2RuntimeInstalled" in iss


def test_release_and_ci_workflows_exist():
    workflows = REPO_ROOT / ".github" / "workflows"
    assert (workflows / "release.yml").is_file()
    assert (workflows / "ci.yml").is_file()


def test_windowed_startup_survives_none_std_streams(monkeypatch):
    """A windowed PyInstaller build has sys.stdout/stderr == None; uvicorn's
    log formatter calls sys.stdout.isatty() and crashed the shipped app on
    launch. _ensure_std_streams must make uvicorn.Config construct cleanly.
    Regression guard for the None-stdout startup crash."""
    import uvicorn

    import main

    monkeypatch.setattr(sys, "stdout", None, raising=False)
    monkeypatch.setattr(sys, "stderr", None, raising=False)

    main._ensure_std_streams()

    assert sys.stdout is not None and sys.stderr is not None
    assert sys.stdout.isatty() is False and sys.stderr.isatty() is False

    # The exact path that used to raise: Config -> configure_logging ->
    # ColourizedFormatter.__init__ -> sys.stdout.isatty().
    uvicorn.Config("backend.app:app", host="127.0.0.1", port=8756, log_level="warning")
