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

**Actions → Release (Windows) → Run workflow** (`workflow_dispatch`), with a
**branch** selected. There is no `dry_run` input — the ref is the switch:
the publish step is `if: startsWith(github.ref, 'refs/tags/')`, so a branch
dispatch runs the whole pipeline (version gate, backend suite, frontend
build, PyInstaller freeze, `--version`/`--selfcheck` smoke test, WebView2
bootstrapper, Inno installer, `latest.json`) and uploads
`BuildASpecSetup.exe` + `latest.json` as a downloadable **run artifact**,
but creates no Release. Download the artifact and install it to verify a
clean-machine experience before tagging.

Two differences from a tag build, both deliberate: the version gate runs
without `--tag` (there is no tag to agree with, so it only checks
`settings.py` against `package.json`), and `latest.json`'s `url` points at
the release asset path for the version being built, which does not exist
until you actually tag. That manifest is for inspection, not for pointing a
real updater at.

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
packaged build, real eyes on motion, or a paid live model run. Everything a
test *can* cover is already covered by `pytest` and `npm test`, which CI runs
on every PR; nothing below duplicates them.

**Status, stated honestly:** no evidence exists that any item here has ever
been performed, through v1.9.0. The list was frozen at v1.0.0 for eight
releases and had drifted out of agreement with the app (one item asserted a
readiness rule that Chunk 5.4 reversed), which is a good part of why it never
got run. It was resynced against the shipped behaviour on 2026-08-18. Note in
each release which items you ran and on what — Word version, packaged vs. dev
build — and treat an unrecorded item as not done.

### Minimum before any release

Four items, all performable from the dry-run artifact before you tag. If you
do nothing else, do these — they are the paths where a regression is both
invisible to CI and expensive to the user.

- [ ] **The manifest describes the installer beside it.** From the dry-run
      artifact, hash `BuildASpecSetup.exe` and compare it to `latest.json`'s
      `sha256` (`make_manifest.py` computes it from the file it was handed,
      so a mismatch means the manifest step got the wrong input):

      ```powershell
      (Get-FileHash .\BuildASpecSetup.exe -Algorithm SHA256).Hash.ToLower()
      (Get-Content .\latest.json | ConvertFrom-Json).sha256
      ```

      The updater SHA-256-verifies before it launches anything, so a
      mismatch here means every user is offered an update that downloads and
      then refuses to install. Its `url` will point at an asset that does not
      exist until you tag — expected on a branch build, see "Test the build
      without releasing" above.
- [ ] **Launch the packaged build and do one of each.** A chat turn, an
      import, an export. `--selfcheck` proves the modules import; it does not
      prove the window works.
- [ ] **Open an exported `.docx` in real Word.** Both a clean export and a
      redline. python-docx will happily write a package Word then declines to
      open.
- [ ] **Two copies at once** (v1.9.0). Launch the packaged app twice: both
      windows work, and a download from each lands. Each launch takes its own
      port and credential, and the download cookie's name is derived per
      launch precisely so the second instance cannot clobber the first's.

### Immediately after publishing

- [ ] **The live update path.** On a machine with the *previous* version
      installed: the header offers the new version, the install closes the
      app and relaunches on it, and the What's-new modal opens **once** and
      not again. See "Verify the update path" above.

      This one **cannot** be done before the tag, which is why it is not in
      the minimum above: `updates.py` resolves
      `releases/latest/download/latest.json`, and until the Release is
      published that path still serves the *previous* version — so an old
      install rehearsing against a branch build is simply told it is up to
      date. The run artifact is not a substitute either; it is a zip behind
      GitHub auth, not a URL the updater can fetch.

      To rehearse it earlier, serve the built `latest.json` and `.exe` from
      any reachable HTTPS location and launch the old app with
      `BUILD_A_SPEC_UPDATE_URL` pointing at that manifest. `http://` will not
      do — the updater is https-only and guards against a redirect
      downgrade. Failing that, publish and check immediately: the manifest
      hash check in the minimum above is what stands between you and the
      expensive version of this going wrong.

### Streaming and chat feel (v0.7.0)

- [ ] A long drafting turn, a thinking-heavy turn, and a search turn — text
      arrives smoothly, the status strip never goes dead, chips appear live
      and name the actual query (v1.9.0 fixed the unlabelled "Searching the
      web…").
- [ ] Reduced-motion enabled: no typewriter, no shimmer, no pulse, no
      breathing agent dot, no chip rise-in.
- [ ] Scrolling up mid-stream hands off follow; returning to the bottom
      re-pins.
- [ ] The header spend ticker and the Settings usage table show believable
      dollar figures for the turns just run. Stop a long reply mid-stream and
      confirm the estimated-output disclosure appears rather than being
      folded into the reported number.

### Keys and manual editing (v0.7.0)

- [ ] Test / replace / remove key flows, including an env-supplied key
      rendering read-only.
- [ ] A `.docx` round-trip after a manual inline edit.

### Review queue (v0.8.0)

- [ ] Keyboard walk end-to-end on a **real office master**: keep / edit /
      delete / ask / skip, hold-to-confirm an article, busy lockout while a
      turn streams, undo after a delete.
- [ ] "Ask model" round-trips and the queue recomputes on turn completion.

### Final QC (v0.9.0; model updated v1.8.0, adjudication v1.9.0)

- [ ] A live run on the configured QC model (`settings.QC_MODEL`, currently
      Opus 5). The Review Room's three stages read honestly — specialists,
      then candidate panels, then local fix validation — findings are real,
      and a refusal fails its lens clean without taking the others down.
      `tools/qc_verifier_canary.py --run` is the cheap standing check that the
      provider still accepts the strict verifier schema; it is one low-token
      request and is **not** a substitute for a full run.
- [ ] Accept-fix `.docx` round-trip; the base QC `.docx` opens in Word.
- [ ] Hold-to-apply-criticals applies them as **one** undo step. Note that
      readiness does **not** go green here: since v1.9.0 `no_open_qc_findings`
      requires *every* surviving finding applied or dismissed-with-reason and
      every dispute adjudicated, not just the criticals. Green needs the whole
      queue dispositioned. (The old wording of this item asserted the
      opposite and would have read as a bug.)
- [ ] A **disputed** candidate (v1.9.0): a complete panel that split. It
      appears in its own warn-toned group, is never auto-applied, blocks
      readiness, and can be cleared by dismissing it with a reason.
- [ ] A large report in `QCReportModal`: no truncation, unsafe source strings
      inert rather than clickable.
- [ ] Word **and** JSON downloads from the **packaged** app, **after applying
      a fix** — that is the exact state where they used to hang for minutes
      (fixed in v1.9.0), and an `<a download>` failing in the shell looks
      identical to nothing happening.
- [ ] Legacy-result limitations and stale input identity render correctly.
- [ ] Partial runs from one failed lens and, independently, one failed
      verifier seat: each must block `qc_execution_complete`; a failed latest
      attempt must also block `qc_current`; all three report surfaces must
      identify the same run.

### Redline export (v1.0.0)

- [ ] Open a redline in **real Word**: the reviewing pane shows
      "Build-a-Spec" as author; **Accept All** yields the current document;
      **Reject All** yields the master; word-level edits read cleanly; deleted
      paragraphs collapse on accept.

### Imported specs and Edit freely (v1.9.0)

- [ ] **Edit freely** on a real office master: import, confirm the one-way
      dialog, then edit a heading and add an article — the things source mode
      refuses. Export is a normal Build-a-Spec `.docx`; *Download original
      upload* still returns the upload **byte-identical**; *Redline vs master*
      still works. Save, reopen the `.baspec`, and confirm it loads with the
      decision intact.
- [ ] A **frozen** package — tracked changes, macros, an embedded object, or
      Restrict Editing — names its cause and remedy in the panel rather than
      going silently read-only. Tracked changes is the one to try: the import
      shows the accepted text, so it looks clean while still being locked.
- [ ] A master whose numbering **starts one level in**: sibling articles
      arrive as siblings, with no invented `IMPORTED CONTENT` article.
- [ ] A large master (1,000+ paragraphs): the app stays responsive while the
      permission sweep runs, and the panel says "pending" rather than
      "read-only". The sweep is still quadratic by design decision — this
      confirms nothing *waits* on it.

### Attachments, figures and templates (v1.1.0–v1.4.0)

- [ ] Attach one of each reference type — `.docx`, `.pdf`, `.txt`, `.xml`,
      `.csv` — and have the model read one. A PDF with no text layer must be
      refused with the reason, not attached empty.
- [ ] A mermaid figure, a hand-authored SVG, and a table render in chat; the
      SVG/PNG/CSV downloads work from the **packaged** app. Rendering is
      sandboxed at three layers, so this is the one place a rendering
      regression would be invisible to the suite.
- [ ] Create a template both ways (Exact and AI-Generalize), approve the
      generalize diff, then instantiate it into a blank session.

### The guided tour (v1.1.0, reworked v1.9.0)

- [ ] Run it start to finish with **no API key configured** — it is bundled
      and must cost nothing and require nothing. Spotlights land on the right
      controls, every screen offers a way out, and ending returns your real
      project untouched.
- [ ] Open a modal on top of the tour (help, or the template studio) and press
      Escape once: the modal closes and the tour does **not** end.

### Diagnostics (v1.6.0, bounded v1.9.0)

- [ ] Download a diagnostics bundle from the packaged app and open it: it
      contains the snapshot, this launch's logs and the current trace, states
      what it truncated, and contains **no** key material.
- [ ] The trace viewer opens from Developer tools and renders with no network.

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
