# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-folder build for the Build-a-Spec Windows desktop app.

Cloned from Claude-Spec-Critic ``packaging/windows/spec-critic.spec`` and
repointed at this app's stack (FastAPI + pywebview instead of
customtkinter; the built React frontend bundled as data).

Build (on Windows, from the repo root):

    python -m venv .venv && .venv\\Scripts\\activate
    pip install -r requirements.txt
    pip install pyinstaller
    cd frontend && npm install && npm run build && cd ..
    pyinstaller packaging/windows/build-a-spec.spec --noconfirm --clean

Output: ``dist/BuildASpec/`` (folder with ``BuildASpec.exe`` + bundled
interpreter and deps). ``packaging/windows/installer.iss`` wraps it into
``BuildASpecSetup.exe``.

One-folder (not one-file) is deliberate: starts faster, updates more
reliably, and trips antivirus far less than a self-extracting one-file exe
— the Inno Setup installer makes it a normal double-click install anyway.
"""
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

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
