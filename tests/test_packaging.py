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
import re
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



def _iss_section(name: str) -> list[str]:
    """The directive lines of one ``[Section]`` of installer.iss: comments,
    preprocessor lines and blanks dropped, surrounding whitespace stripped."""
    lines: list[str] = []
    current = None
    for raw in (PKG / "installer.iss").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        header = re.fullmatch(r"\[([A-Za-z]+)\]", line)
        if header:
            current = header.group(1)
            continue
        if current == name and line and not line.startswith((";", "#")):
            lines.append(line)
    return lines


def _iss_directive(section: str, key: str) -> list[str]:
    prefix = f"{key}="
    return [ln[len(prefix):] for ln in _iss_section(section) if ln.startswith(prefix)]


def test_installer_requires_accepting_the_license():
    """The installer shows the license and will not install until the user
    selects "I accept the agreement".

    That is what Inno Setup's ``LicenseFile`` directive does: it adds the
    License Agreement page, with "I do not accept" selected by default and
    Next disabled until "I accept" is chosen. The page reads the repo's own
    LICENSE, never a copy, so it cannot show terms other than the ones that
    govern. Inno Setup cannot run here, so this pins the wiring; compiling
    the installer (the release workflow, or its branch dry run) and the
    pre-release QA row in docs/RELEASE_WINDOWS.md check the page itself."""
    values = _iss_directive("Setup", "LicenseFile")
    assert values, "installer.iss has no LicenseFile, so Setup shows no license page"
    assert len(values) == 1, f"LicenseFile is set more than once: {values}"
    # Inno resolves a relative path against the script's own folder (the
    # default SourceDir), as it does for OutputDir and every [Files] Source.
    target = (PKG / values[0].replace("\\", "/")).resolve()
    assert target == (REPO_ROOT / "LICENSE").resolve(), (
        f"the license page must show the root LICENSE, not {target}"
    )
    text = target.read_bytes()
    # Inno Setup reads a text license file as ANSI, or as UTF-8 (versions
    # before 6.3 only with a BOM). ASCII reads the same in all of them. A
    # leading "{\rtf" would make Setup load the file as rich text instead.
    assert text.isascii(), (
        "LICENSE holds a non-ASCII character; older Inno Setup compilers "
        "would show it garbled on the license page"
    )
    assert not text.lstrip().startswith(b"{\\rtf"), "LICENSE must stay plain text"
    # The choices keep Inno Setup's own wording ("I accept the agreement" /
    # "I do not accept the agreement"), which is what the QA row tells a
    # tester to look for, and no [Code] may skip the page (ShouldSkipPage on
    # wpLicense would install without asking).
    for key in ("LicenseAccepted", "LicenseNotAccepted"):
        assert not _iss_directive("Messages", key), (
            f"installer.iss overrides {key}; the choices must keep Inno "
            "Setup's own wording"
        )
    iss = (PKG / "installer.iss").read_text(encoding="utf-8")
    assert "wpLicense" not in iss, "installer.iss code refers to the license page"


def test_installer_license_page_names_the_license_in_plain_text():
    """The page's lead-in (``LicenseLabel3``) names the license and says
    the agreement below it governs. It is read on a Windows dialog, so it
    stays ASCII — the script carries no encoding marker — and ONE line: a
    [Messages] entry does not continue onto the next line."""
    values = _iss_directive("Messages", "LicenseLabel3")
    assert len(values) == 1, "installer.iss must set LicenseLabel3 exactly once"
    label = values[0]
    assert label.isascii(), "the license page's lead-in must be ASCII"
    assert "PolyForm Shield License 1.0.0" in label
    assert "the agreement below governs" in label
    assert "accept" in label


def test_every_surface_states_the_same_license():
    """Seven of the eight license surfaces, pinned so they cannot drift.

    The eighth — the bundled copy in the PyInstaller output — is pinned by
    ``test_pyinstaller_spec_bundles_the_license`` above, so between the two
    tests every surface CLAUDE.md lists is asserted. The installer's license
    page is the newest: its lead-in names the license here, and
    ``test_installer_requires_accepting_the_license`` pins that the terms it
    shows are LICENSE itself.

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

    # The installer's license page: its lead-in names the license, and the
    # terms under it are LICENSE itself.
    (label,) = _iss_directive("Messages", "LicenseLabel3")
    assert "PolyForm Shield License 1.0.0" in label, (
        "the installer's license page names a different license than LICENSE"
    )
    iss = (PKG / "installer.iss").read_text(encoding="utf-8")
    assert "MIT License" not in iss

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

    # `SEE LICENSE IN <filename>` resolves relative to the PACKAGE root, so
    # npm tooling and license scanners treating frontend/ as the package look
    # for frontend/LICENSE, not the repo root's. It must therefore exist and
    # must not drift from the canonical copy. A bare `PolyForm-Shield-1.0.0`
    # would avoid the duplicate, but that id is NOT in the SPDX list (only
    # PolyForm-Noncommercial-1.0.0 and PolyForm-Small-Business-1.0.0 are), so
    # npm warns on it. A checked-in copy is used rather than a symlink because
    # Windows is the primary platform and symlinks do not survive a default
    # Windows checkout.
    frontend_license = REPO_ROOT / "frontend" / "LICENSE"
    assert frontend_license.is_file(), (
        "frontend/package.json says `SEE LICENSE IN LICENSE`, so frontend/"
        "LICENSE must exist or the reference dangles"
    )
    assert frontend_license.read_bytes() == (REPO_ROOT / "LICENSE").read_bytes(), (
        "frontend/LICENSE drifted from the root LICENSE — they are the same "
        "license and must stay byte-identical"
    )


def test_installer_gates_webview2_on_the_bootstrapper_being_present():
    """The WebView2 bundling is preprocessor-guarded so a manual build
    without the (gitignored) bootstrapper still compiles."""
    iss = (PKG / "installer.iss").read_text(encoding="utf-8")
    assert "MicrosoftEdgeWebview2Setup.exe" in iss
    assert "#ifdef HaveWebView2" in iss
    assert "IsWebView2RuntimeInstalled" in iss


def test_app_entry_documents_every_headless_flag():
    """The frozen exe's headless flags are how the release workflow
    smoke-tests a build it cannot open a window on. `--boot-check` landed
    in `main()` and in release.yml but not in the module docstring, which
    kept saying "two flags" — so a reader of app_entry.py would not know the
    boot check existed, let alone that it is the one that catches
    windowed-mode crashes. Every flag `main()` handles must be documented
    where the flags are explained AND exercised by the workflow."""
    import ast

    source = (PKG / "app_entry.py").read_text(encoding="utf-8")
    flags = sorted(set(re.findall(r'"(--[a-z-]+)" in args', source)))
    assert flags, "app_entry.main() no longer dispatches on any headless flag"
    docstring = ast.get_docstring(ast.parse(source)) or ""
    release = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    for flag in flags:
        assert flag in docstring, f"app_entry.py's docstring does not describe {flag}"
        assert f"'{flag}'" in release, f"release.yml never runs {flag}"


def test_ci_lints_before_it_tests():
    """The lint gate is a CI step, and it runs BEFORE pytest — a dangling
    import should fail in seconds, not after the whole suite. The step and
    the config it reads are both pinned: a `ruff.toml` that stops selecting
    the bug-catching families is a gate that lets everything through."""
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    lint = ci.find("ruff check")
    tests = ci.find("python -m pytest")
    assert lint >= 0, "ci.yml has no ruff step"
    assert tests >= 0, "ci.yml has no pytest step"
    assert lint < tests, "the ruff step must run before pytest"
    config = (REPO_ROOT / "ruff.toml").read_text(encoding="utf-8")
    selected = re.search(r"^select\s*=\s*\[([^\]]*)\]", config, re.M)
    assert selected, "ruff.toml declares no rule selection"
    families = set(re.findall(r'"([A-Z0-9]+)"', selected.group(1)))
    assert {"F", "B", "E9"} <= families, families
    assert "ruff==" in (REPO_ROOT / "requirements.txt").read_text(
        encoding="utf-8"
    ), "ruff is not pinned in requirements.txt, so CI cannot run the gate"


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
