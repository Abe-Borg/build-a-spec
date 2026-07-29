# Windows release runbook — Build-a-Spec

Same pipeline as Claude-Spec-Critic: PyInstaller one-folder → Inno Setup
installer → GitHub Release with a SHA-256 `latest.json` manifest the
in-app updater consumes. There are two ways to cut a release:

- **Automated (recommended)** — push a version tag; GitHub Actions builds
  the installer on a Windows runner and publishes the Release. No Windows
  machine needed.
- **Manual** — run the steps yourself on a Windows box (below). Useful for
  debugging the build.

---

## Automated release (GitHub Actions)

The workflow is `.github/workflows/release.yml` (job runs on
`windows-latest`). It builds the frontend, runs the version gate and the
backend test suite, freezes the app with PyInstaller, smoke-tests the
frozen `.exe` (`--version` + `--selfcheck`), bundles the Edge WebView2
bootstrapper, compiles the Inno Setup installer, generates `latest.json`,
and — on a tag build — publishes a GitHub Release with both assets.

### Cut a release

1. **Write the release notes** — add a `ReleaseNote` entry for the new
   version at the top of `RELEASE_NOTES` in `backend/release_notes.py`.
   This is not optional: `tests/test_release_notes.py::
   test_the_shipped_notes_describe_the_shipped_version` fails a version
   with no entry, and the workflow's render step exits non-zero, because a
   release with no notes puts an empty "What's new" modal in front of every
   user who updates. Write for a spec author, not a developer — what they
   can now *do*, not the subsystem that changed.
2. Bump `VERSION` in `backend/settings.py` **and** `version` in
   `frontend/package.json` to the same value, then
   `cd frontend && npm install --package-lock-only` to refresh the lock.
   (`tests/test_updates.py::test_version_consistency_gate` enforces the
   match; a mismatch would ship an app that permanently sees itself as out
   of date.)
3. Commit to `master` (through a PR — CI runs the tests and the gate).
4. Tag and push:

   ```bash
   git tag v0.9.0
   git push origin v0.9.0
   ```

5. Watch **Actions → Release (Windows)**. On success it creates the
   Release for the tag with `BuildASpecSetup.exe` + `latest.json` attached.
   The release body is your `release_notes.py` entry, followed by the
   install/SmartScreen instructions
   (`packaging/windows/release_install_notes.md`), followed by GitHub's
   auto-generated commit changelog.

### Where release notes end up

One entry in `backend/release_notes.py` feeds three surfaces, so they can
never disagree:

| Surface | Rendered by | Seen by |
|---|---|---|
| The app's **What's new** modal | the bundled module itself | a user who just updated (opens once), or anyone via Settings → What's new |
| `latest.json`'s `notes` field | `manifest_summary()` | a user who has **not** updated yet — the update pill's tooltip |
| The GitHub Release body | `markdown_notes()` | anyone on the releases page |

`packaging/windows/render_release_notes.py` produces the last two at build
time. The app never fetches its own notes — they ship inside the build, so a
freshly-updated app can show them with no network at all.

The "has this user seen it" marker is `last_seen_version` in the update
state file (`update_check.json`, beside the API key). A fresh install is
deliberately shown nothing; the app distinguishes it from an upgrade by
sampling whether that file existed at boot.

The workflow needs no secrets — the built-in `GITHUB_TOKEN` (with
`contents: write`, declared in the workflow) creates the Release.

### Test the build without releasing

**Actions → Release (Windows) → Run workflow** (`workflow_dispatch`). With
`dry_run` left on, it runs the whole pipeline and uploads
`BuildASpecSetup.exe` + `latest.json` as a downloadable **run artifact**,
but does **not** create a Release. Download the artifact and install it to
verify a clean-machine experience before tagging.

### Verify the update path

On a machine with the *previous* version installed: open the app → the
header shows "vX.Y.Z available — install" (or force it with the
`/api/update/check?force=true` endpoint) → install → the app closes, the
installer runs, and the new version launches. The download is
SHA-256-verified against the manifest before it ever executes; a tampered
or truncated download refuses to run.

## DOCX fidelity release gate

Before tagging, verify the contract in
[DOCX_FIDELITY.md](DOCX_FIDELITY.md), not only that a DOCX opens. At minimum:

```powershell
& '.\venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider
Push-Location .\frontend
npm test
npm run build
Pop-Location
& '.\venv\Scripts\python.exe' -m tests.docx_corpus .\artifacts\docx-corpus
```

The backend gate includes exact-original/no-op, source patch locality,
pass-through-only blockers, project compatibility, adversarial OPC/ZIP/XML,
limits/history, and concurrency. The frontend test covers source capability and
output-guidance behavior. Corpus materialization verifies fixture checksums and
provenance metadata.

Run the optional renderer-backed suite with Microsoft Word and/or LibreOffice
when those applications are available; follow
[DOCX_RENDERER_WINDOWS.md](DOCX_RENDERER_WINDOWS.md). Record the exact
renderer/version used. A package-only pass must not be reported as a Word or
LibreOffice visual pass.

Review any new external fixture using the privacy process in
[DOCX_FIDELITY_CORPUS.md](DOCX_FIDELITY_CORPUS.md). Do not attach local trace
directories to a release: traces can contain document text and prompts. Any
optional aggregate fidelity diagnostic may contain coarse blocker codes/counts
only, never document text, raw OOXML, source bytes, filenames, paths, or free
form exception details.

---

## Pre-release manual QA

These checks cannot be made hermetic — they need a real Word install, a real
packaged build, real eyes on motion, or a paid live model run. They were
recorded as **still owed** in the v0.7.0–v1.0.0 batch plans; when those plans
were retired on 2026-07-29 the obligations moved here rather than lapsing. No
evidence exists that any of them was ever performed, so treat the whole list as
outstanding until a release records otherwise. Note in the release which ones
you ran and on what (Word version, packaged vs. dev build).

**Streaming and chat feel** (v0.7.0)

- [ ] A long drafting turn, a thinking-heavy turn, and a search turn — text
      arrives smoothly, the status strip never goes dead, chips appear live.
- [ ] Reduced-motion enabled: no typewriter, no shimmer, no pulse.
- [ ] Scrolling up mid-stream hands off follow; returning to the bottom
      re-pins.
- [ ] The header spend ticker and the Settings usage table show believable
      dollar figures for the turns just run.

**Keys and manual editing** (v0.7.0)

- [ ] Test / replace / remove key flows, including an env-supplied key
      rendering read-only.
- [ ] A `.docx` round-trip after a manual inline edit.

**Review queue** (v0.8.0)

- [ ] Keyboard walk end-to-end on a **real office master**: keep / edit /
      delete / ask / skip, hold-to-confirm an article, busy lockout while a
      turn streams, undo after a delete.
- [ ] "Ask model" round-trips and the queue recomputes on turn completion.

**Final QC** (v0.9.0; model updated v1.8.0)

- [ ] A live run on the configured QC model (`settings.QC_MODEL`, currently
      Opus 5 — the plan text said Fable 5, superseded): lens → verify progress
      reads honestly, findings are real, a refusal fails the lens clean.
- [ ] Accept-fix `.docx` round-trip; the base QC `.docx` opens in Word.
- [ ] Hold-to-apply-criticals, and readiness going green.
- [ ] A large report in `QCReportModal`: no truncation, unsafe source strings
      inert rather than clickable.
- [ ] Word **and** JSON downloads from the **packaged** app.
- [ ] Legacy-result limitations and stale input identity render correctly.
- [ ] Partial runs from one failed lens and, independently, one failed
      verifier seat: each must block `qc_audit_complete`; a failed latest
      attempt must also block `qc_current`; all three report surfaces must
      identify the same run.

**Redline export** (v1.0.0)

- [ ] Open a redline in **real Word**: the reviewing pane shows
      "Build-a-Spec" as author; **Accept All** yields the current document;
      **Reject All** yields the master; word-level edits read cleanly; deleted
      paragraphs collapse on accept.

---

## Manual release (on a Windows machine)

### 0. One-time setup

- Python 3.11+ and Node 20+ installed.
- [Inno Setup 6](https://jrsoftware.org/isinfo.php) installed (`ISCC.exe`
  on PATH, or use its full path).
- `pip install pyinstaller` in the build venv (build-time only — it is
  deliberately NOT in `requirements.txt`).
- On Windows, `pip install pythonnet` too if a plain
  `pip install -r requirements.txt` didn't pull it — it is what loads
  pywebview's Edge WebView2 backend, and PyInstaller can only bundle what
  is installed.

### 1. Version bump + consistency gate

1. Bump `VERSION` in `backend/settings.py` **and** `version` in
   `frontend/package.json` (then `npm install --package-lock-only`).
2. Gate:

   ```bat
   python packaging\windows\check_release_version.py --tag v0.9.0
   ```

### 2. Build

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install pythonnet pyinstaller

cd frontend
npm ci
npm run build
cd ..

pyinstaller packaging\windows\build-a-spec.spec --noconfirm --clean
```

Output: `dist\BuildASpec\` (one-folder app).

### 3. Smoke-test the frozen app

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

### 4. Installer

Optionally fetch the WebView2 bootstrapper first so the installer bundles
it (the app already falls back to a browser window without it):

```bat
curl -L -o packaging\windows\MicrosoftEdgeWebview2Setup.exe "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
```

Then compile:

```bat
ISCC /DMyAppVersion=0.9.0 packaging\windows\installer.iss
```

Output: `dist\installer\BuildASpecSetup.exe`. Install it on a clean
profile and launch once. **Do not change the AppId GUID in
`installer.iss` — ever.** It is what makes upgrades install in place.

### 5. Manifest

```bat
python packaging\windows\render_release_notes.py ^
    --version 0.9.0 ^
    --notes-out release-notes.txt ^
    --body-out release-body.md

python packaging\windows\make_manifest.py ^
    --version 0.9.0 ^
    --installer dist\installer\BuildASpecSetup.exe ^
    --url https://github.com/Abe-Borg/build-a-spec/releases/download/v0.9.0/BuildASpecSetup.exe ^
    --out latest.json ^
    --notes-file release-notes.txt ^
    --published-at 2026-07-21
```

`--notes` still takes a literal string, but prefer `--notes-file` so the
manifest, the release page, and the app's own What's-new modal all come from
the same `backend/release_notes.py` entry. `release-body.md` is the release
body to paste in step 6.

The `--url` must be the final release-asset URL (tag path shown), and it
must be https — `parse_manifest` refuses anything else.

### 6. GitHub Release

1. Tag: `git tag v0.9.0 && git push --tags`.
2. Create the release for the tag; upload **both**
   `BuildASpecSetup.exe` and `latest.json` as assets.
3. Publish (not a draft, not a pre-release) — the updater reads
   `releases/latest/download/latest.json`, which only serves published,
   non-prerelease releases.

---

## The app icon

`packaging/windows/assets/BuildASpec.ico` is embedded in the `.exe` (via
the PyInstaller spec) and used as the installer icon. It is generated,
reproducibly, by `packaging/windows/make_icon.py` (`pip install Pillow`,
then run it). Regenerate and commit the `.ico`/`.png` if the mark changes.

## SmartScreen expectations

The app is not code-signed (no paid certificate), so the first run of a
downloaded installer shows Windows SmartScreen's "Windows protected your
PC" — More info → Run anyway. This is expected; the integrity story is
the https-only manifest + SHA-256 gate, not an Authenticode signature.
Document this in release notes for new users (the automated release does
this for you).

## Troubleshooting

- **Frozen app can't find the frontend**: the spec bundles
  `frontend/dist` → `<bundle>/frontend/dist`; `backend.settings`
  resolves it via `sys._MEIPASS`. Make sure `npm run build` ran before
  PyInstaller.
- **pywebview backend errors / app opens in a browser instead of a
  window**: the native window needs the Edge WebView2 runtime. The
  installer bundles the bootstrapper and installs it if missing; on older
  images or offline installs, install the Evergreen WebView2 runtime
  manually. The spec collects `webview`, `clr_loader`, and `pythonnet` —
  make sure `pythonnet` was installed before freezing.
- **Updater says up to date after release**: the release must be
  published and non-prerelease; `latest.json` must be an asset of the
  *latest* release.
