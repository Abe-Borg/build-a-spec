# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-folder build for the Build-a-Spec Windows desktop app.

Cloned from Claude-Spec-Critic ``packaging/windows/spec-critic.spec`` and
repointed at this app's stack (FastAPI + pywebview instead of
customtkinter; the built React frontend bundled as data).

Build (on Windows, from the repo root — the same steps as the Build section
of ``docs/RELEASE_WINDOWS.md``). One command per line, so they run as
written in PowerShell and in Command Prompt; Windows PowerShell 5.1 has no
``&&``, and PowerShell needs the leading ``.\\`` to run the activation
script:

    python -m venv .venv
    .\\.venv\\Scripts\\activate
    pip install -r requirements.txt
    pip install -r packaging/windows/build-requirements.txt
    cd frontend
    npm ci
    npm run build
    cd ..
    pyinstaller packaging/windows/build-a-spec.spec --noconfirm --clean

Output: ``dist/BuildASpec/`` (folder with ``BuildASpec.exe`` + bundled
interpreter and deps) and ``dist/THIRD-PARTY-NOTICES.txt`` (the license text
of every bundled component; see ``third_party_notices.py`` — the build fails
when one is missing, and it reads ``frontend/node_modules``, so ``npm ci``
must run first). ``packaging/windows/installer.iss`` wraps both into
``BuildASpecSetup.exe``.

One-folder (not one-file) is deliberate: starts faster, updates more
reliably, and trips antivirus far less than a self-extracting one-file exe
— the Inno Setup installer makes it a normal double-click install anyway.
"""
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata
from PyInstaller.utils.hooks.tcl_tk import tcltk_info

# packaging/windows helper (importable + unit-tested: tests/test_third_party_notices.py).
if SPECPATH not in sys.path:
    sys.path.insert(0, SPECPATH)
from third_party_notices import NOTICES_FILENAME, write_third_party_notices  # noqa: E402

datas = []
binaries = []
hiddenimports = []

# pywebview resolves its Windows backend (Edge WebView2 via pythonnet/clr)
# dynamically — collect everything so the frozen app finds it at runtime.
for _pkg in ("webview", "clr_loader", "pythonnet"):
    try:
        _d, _b, _h = collect_all(_pkg)
        datas += _d
        binaries += _b
        hiddenimports += _h
    except Exception:
        # pythonnet/clr_loader may be absent on a non-Windows dev box; the
        # Windows release build has them via pywebview's install extras.
        pass

# FastAPI/uvicorn dynamic bits.
hiddenimports += collect_submodules("uvicorn")

# pythonnet's import module is ``clr`` (a compiled extension loaded at
# runtime, invisible to PyInstaller's static analysis). PyInstaller's
# bundled hooks (pyinstaller-hooks-contrib: clr / clr_loader / webview)
# collect Python.Runtime.dll et al.; naming ``clr`` explicitly is cheap
# insurance against the classic frozen "cannot load Python.Runtime" crash.
hiddenimports += ["clr"]

# keyring resolves its backend (Windows Credential Manager) dynamically;
# optional dependency — a build without it still works via the key file.
try:
    hiddenimports += collect_submodules("keyring.backends")
    hiddenimports += ["keyring.backends.Windows"]
except Exception:
    pass

# The Excel reader for reference attachments imports openpyxl lazily, and
# openpyxl picks up defusedxml only if it can import it — the reader refuses
# workbooks without it. Static analysis finds both; naming them is cheap
# insurance against shipping a build that refuses every .xlsx.
hiddenimports += ["openpyxl", "defusedxml", "defusedxml.ElementTree"]

# Distribution metadata read at runtime.
for _dist in ("anthropic", "keyring", "fastapi", "pywebview", "pypdf"):
    try:
        datas += copy_metadata(_dist)
    except Exception:
        pass

# The app package itself.
_repo_root = os.path.dirname(os.path.dirname(SPECPATH))
_d, _b, _h = collect_all("backend")
datas += _d
binaries += _b
hiddenimports += _h

# The built React frontend — served by the backend from
# ``<bundle>/frontend/dist`` (see backend.settings._resolve_frontend_dist).
datas += [(os.path.join(_repo_root, "frontend", "dist"), os.path.join("frontend", "dist"))]

# The bundled HTML trace viewer (backend/tracing/viewer/trace_viewer.html)
# is collected by collect_all("backend"); the explicit entry keeps the
# build correct if collection heuristics change (PyInstaller dedupes).
datas += [(
    os.path.join(_repo_root, "backend", "tracing", "viewer", "trace_viewer.html"),
    os.path.join("backend", "tracing", "viewer"),
)]

# Curated semantic spec starters used by New Session and the offline tutorial.
datas += [(
    os.path.join(_repo_root, "backend", "templates", "curated"),
    os.path.join("backend", "templates", "curated"),
)]

# The PolyForm Shield license text — every copy must carry it (the Notices
# section requires that anyone receiving any part of the software also
# receives these terms and the `Required Notice:` line, and the Noncompete term
# only binds a recipient who has them). PyInstaller 6 puts data files in the
# contents directory, so this copy lands at dist/BuildASpec/_internal/LICENSE;
# it keeps the terms inside the one-folder build if that folder is passed on
# by itself. installer.iss also installs LICENSE as {app}\LICENSE.txt, beside
# the exe, where a user finds it.
datas += [(os.path.join(_repo_root, "LICENSE"), ".")]

a = Analysis(
    [os.path.join(SPECPATH, "app_entry.py")],
    pathex=[_repo_root],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "_pytest", "playwright"],
    noarchive=False,
)

# Third-party license notices. A bundled binary must carry the license text of
# everything it bundles, and copy_metadata above reaches only a few packages.
# This writes dist/THIRD-PARTY-NOTICES.txt — beside the one-folder build, not
# inside it — and installer.iss installs it next to BuildASpec.exe. It lists
# every distribution that owns a file the analysis collected (plus
# PyInstaller, whose bootloader is the exe), the interpreter's LICENSE.txt,
# Microsoft's WebView2 SDK terms (vendored by pywebview), and every production
# npm package the frontend is built from. It FAILS THE BUILD when any of those
# texts cannot be found, so an installer missing a license can never ship
# silently. Placed before PYZ/EXE/COLLECT so a gap stops the build early.
# Tcl/Tk terms are required only if the analysis ever collects tkinter.
write_third_party_notices(
    os.path.join(DISTPATH, NOTICES_FILENAME),
    [a.pure, a.scripts, a.binaries, a.datas],
    tcl_data_dir=tcltk_info.tcl_data_dir,
    tk_data_dir=tcltk_info.tk_data_dir,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BuildASpec",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed app — no console behind the pywebview window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Multi-resolution app icon (packaging/windows/make_icon.py generates it);
    # embedded in BuildASpec.exe so the taskbar/desktop show a real icon.
    icon=os.path.join(SPECPATH, "assets", "BuildASpec.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="BuildASpec",
)
