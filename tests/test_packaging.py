"""Build-critical packaging invariants.

These guard the Windows release pipeline against changes that only fail on
a Windows build machine (or, worse, silently ship a broken installer):
the app icon must exist and be wired into the PyInstaller spec and the
Inno Setup installer, and the installer's stable AppId must never change
(it is what makes upgrades install in place). Hermetic — pure file reads,
no build tools required.
"""
from __future__ import annotations

import json
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


def test_pyinstaller_spec_bundles_the_license():
    """The license notice must travel with every installed copy, not just
    the git checkout — installer.iss bundles dist/BuildASpec wholesale, so
    getting the LICENSE file into the PyInstaller output is what actually
    ships it. Under PolyForm Shield this is not merely a courtesy: the
    Notices section obliges anyone passing on any part of the software to
    pass on these terms, and the Noncompete term only binds a recipient who
    received them."""
    assert (REPO_ROOT / "LICENSE").is_file(), "repo root LICENSE is missing"
    spec = (PKG / "build-a-spec.spec").read_text(encoding="utf-8")
    assert '"LICENSE"' in spec, (
        "the PyInstaller spec must bundle the root LICENSE file into the "
        "frozen app"
    )



def test_every_surface_states_the_same_license():
    """The license claim lives in six places and they must not drift.

    Relicensed MIT -> PolyForm Shield 1.0.0 on 2026-08-28. The one that is
    easiest to miss is HelpModal's About footer, because it is the only copy
    the *user* ever reads. A stale surface here is a false license claim, so
    this pins all of them rather than trusting a future grep."""
    license_text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")

    assert license_text.startswith("# PolyForm Shield License 1.0.0"), (
        "LICENSE is no longer PolyForm Shield 1.0.0 — if that is deliberate, "
        "update every surface asserted below in the same change"
    )

    # The two notice lines the license itself references must each be ONE
    # physical line: the Notices section obliges redistributors to carry
    # "plain-text lines beginning with `Required Notice:`", and a wrapped
    # continuation line does not begin with that prefix.
    for prefix in ("Required Notice:", "Licensor Line of Business:"):
        matches = [ln for ln in license_text.splitlines() if ln.startswith(prefix)]
        assert len(matches) == 1, f"expected exactly one {prefix!r} line"
        assert "Abraham Borg" in matches[0] or "Build-a-Spec" in matches[0]

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "PolyForm Shield License 1.0.0" in readme
    assert "MIT License" not in readme

    # The About footer in the shipped UI.
    help_modal = (
        REPO_ROOT / "frontend" / "src" / "components" / "HelpModal.tsx"
    ).read_text(encoding="utf-8")
    assert "PolyForm Shield License 1.0.0" in help_modal, (
        "the in-app About footer still claims a different license than LICENSE"
    )
    assert "MIT License" not in help_modal

    # package.json / package-lock.json root entries. Dependency entries in the
    # lockfile carry THEIR OWN licenses and must never be rewritten.
    pkg = json.loads(
        (REPO_ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    )
    lock = json.loads(
        (REPO_ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8")
    )
    assert pkg["license"] == "SEE LICENSE IN LICENSE"
    assert lock["packages"][""]["license"] == pkg["license"], (
        "the lockfile root entry drifted from package.json"
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
    # isatty() must be callable without raising (the crash was AttributeError
    # on None). Its bool value is platform-dependent — Windows' 'nul' device
    # reports isatty() == True — and irrelevant here: it only toggles ANSI
    # colours, which are discarded. What matters is no crash.
    assert isinstance(sys.stdout.isatty(), bool)
    assert isinstance(sys.stderr.isatty(), bool)
    sys.stdout.write("")  # writable, no crash
    sys.stderr.write("")

    # The exact path that used to raise: Config -> configure_logging ->
    # ColourizedFormatter.__init__ -> sys.stdout.isatty(). Must not raise.
    uvicorn.Config("backend.app:app", host="127.0.0.1", port=8756, log_level="warning")
