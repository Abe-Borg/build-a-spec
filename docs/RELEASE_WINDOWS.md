# Windows release runbook — Build-a-Spec

Adapted from Claude-Spec-Critic's `docs/RELEASE_WINDOWS.md` (same
pipeline: PyInstaller one-folder → Inno Setup installer → GitHub Release
with a SHA-256 `latest.json` manifest the in-app updater consumes). Run
on a Windows machine.

## 0. One-time setup

- Python 3.11+ and Node 20+ installed.
- [Inno Setup 6](https://jrsoftware.org/isinfo.php) installed (`ISCC.exe`
  on PATH, or use its full path).
- `pip install pyinstaller` in the build venv (build-time only — it is
  deliberately NOT in `requirements.txt`).

## 1. Version bump + consistency gate

1. Bump `VERSION` in `backend/settings.py` **and** `version` in
   `frontend/package.json` (then `npm install --package-lock-only`).
2. Gate:

   ```bat
   python packaging\windows\check_release_version.py --tag v0.5.0
   ```

   A mismatch here would ship an app that permanently sees itself as
   out of date. `tests/test_updates.py::test_version_consistency_gate`
   enforces the same in CI/pytest.

## 2. Build

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller

cd frontend
npm install
npm run build
cd ..

pyinstaller packaging\windows\build-a-spec.spec --noconfirm --clean
```

Output: `dist\BuildASpec\` (one-folder app).

## 3. Smoke-test the frozen app

```bat
dist\BuildASpec\BuildASpec.exe --version
dist\BuildASpec\BuildASpec.exe --selfcheck
```

`--selfcheck` imports the FastAPI surface, the research engine, the
compliance checker, the updater, the docx importer, and pywebview, and
verifies the bundled frontend is present — exit 0 required. (The build is
windowed, so set `BUILD_A_SPEC_SELFCHECK_OUT=selfcheck.txt` to capture
output to a file if the console shows nothing.) Then launch it plainly
once and click through: chat turn, import, export.

## 4. Installer

```bat
ISCC /DMyAppVersion=0.5.0 packaging\windows\installer.iss
```

Output: `dist\installer\BuildASpecSetup.exe`. Install it on a clean
profile and launch once. **Do not change the AppId GUID in
`installer.iss` — ever.** It is what makes upgrades install in place.

## 5. Manifest

```bat
python packaging\windows\make_manifest.py ^
    --version 0.5.0 ^
    --installer dist\installer\BuildASpecSetup.exe ^
    --url https://github.com/Abe-Borg/build-a-spec/releases/download/v0.5.0/BuildASpecSetup.exe ^
    --out latest.json ^
    --notes "Short release notes." ^
    --published-at 2026-07-21
```

The `--url` must be the final release-asset URL (tag path shown), and it
must be https — `parse_manifest` refuses anything else.

## 6. GitHub Release

1. Tag: `git tag v0.5.0 && git push --tags`.
2. Create the release for the tag; upload **both**
   `BuildASpecSetup.exe` and `latest.json` as assets.
3. Publish (not a draft, not a pre-release) — the updater reads
   `releases/latest/download/latest.json`, which only serves published,
   non-prerelease releases.

## 7. Verify the update path

On a machine with the *previous* version installed: open the app →
header shows "vX.Y.Z available — install" (or force with the
`/api/update/check?force=true` endpoint) → install → app closes,
installer runs, new version launches. The download is SHA-256-verified
against the manifest before it ever executes; a tampered or truncated
download refuses to run.

## SmartScreen expectations

The app is not code-signed (no paid certificate), so the first run of a
downloaded installer shows Windows SmartScreen's "Windows protected your
PC" — More info → Run anyway. This is expected; the integrity story is
the https-only manifest + SHA-256 gate, not an Authenticode signature.
Document this in release notes for new users.

## Troubleshooting

- **Frozen app can't find the frontend**: the spec bundles
  `frontend/dist` → `<bundle>/frontend/dist`; `backend.settings`
  resolves it via `sys._MEIPASS`. Make sure `npm run build` ran before
  PyInstaller.
- **pywebview backend errors**: the spec collects `webview`,
  `clr_loader`, and `pythonnet`; WebView2 runtime is preinstalled on
  current Windows — on older images install the Evergreen WebView2
  runtime.
- **Updater says up to date after release**: the release must be
  published and non-prerelease; `latest.json` must be an asset of the
  *latest* release.
