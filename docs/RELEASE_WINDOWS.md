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
   `frontend/package.json` to the same value, then run
   `npm install --package-lock-only` inside `frontend` to refresh the lock.
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

**A release covers every version since the last one, not just its own.** The
version bump and the release are separate acts, and they come apart: 1.14.0,
1.16.0 and 1.18.0 were each bumped and merged, then superseded by the next
bump without ever being tagged. Their work ships in the following installer,
so the two surfaces above have to name it — they are what a user reads
*before* deciding to update, and a page describing less than the build
contains is the failure here. (The in-app modal already spanned the gap on
its own: `resolve_pending` announces everything newer than the user's
`last_seen_version`, whatever was tagged in between.)

The workflow works the bound out itself — it asks the API which releases
have actually **published** and passes the list as `--released`, and the
renderer takes the greatest one below the version being cut — so cutting a
release is still just a tag push. It asks for releases rather than tags on
purpose: a tag build that fails after the tag is pushed leaves a tag behind
with no release page, and the Windows-only steps here (freeze, smoke test,
installer) are never exercised by CI, so that is a real way to get one.
Treating it as the bound would skip that version's notes — the gap this
exists to close. An unusable bound falls back to describing the tagged
version alone rather than emptying the whole changelog onto one page.
Rendering by hand, where `--since` is the explicit override:

```bash
python packaging/windows/render_release_notes.py \
    --version 1.19.0 --since v1.17.0 \
    --notes-out release-notes.txt --body-out release-body.md
```

It prints the versions it covered; `--since` is optional and omitting it
renders the single entry, exactly as it did before.

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
& '.\.venv\Scripts\python.exe' -m ruff check .
& '.\.venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider
Push-Location .\frontend
npm test
npm run build
Pop-Location
& '.\.venv\Scripts\python.exe' -m tests.docx_corpus .\artifacts\docx-corpus
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

- [ ] **The install gate** (v1.17.0), on that same previous-version machine:
      with a reply streaming, the header pill and Help → About's Install are
      disabled and their tooltip says why; with unsaved work and nothing
      running, Install opens the Save / Install without saving / Cancel
      prompt, Cancel keeps the session, and either other choice launches the
      installer; two rapid clicks start one download, not two.

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

### Final QC (v0.9.0; model updated v1.8.0 and v1.20.0, adjudication v1.9.0)

- [ ] A live run on the configured QC model (`settings.QC_MODEL`, currently
      Opus 5.5). The Review Room's three stages read honestly — specialists,
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
- [ ] **Staggered first stage** (cost Tier 1, Chunk 2). Press Run Final QC:
      the code-compliance card and one other lens card start at once, and the
      other three lens cards stay queued for a few seconds until the first
      one begins answering. The review completes as usual. In the report's
      per-lens usage, three of the four lenses without web tools show cache
      reads and no cache write, and
      `.\.venv\Scripts\python tools\qc_export_cost_profile.py "<the JSON export>"`
      says 3 read the shared prefix and 1 wrote one. With
      `$env:BUILD_A_SPEC_QC_WARM_WAIT_SECONDS = "0"` (Command Prompt:
      `set BUILD_A_SPEC_QC_WARM_WAIT_SECONDS=0`) all five start at once, as
      before. Press Stop while the three are queued: they are recorded as
      cancelled, and the activity log shows one "share a cached prefix" line
      ending `(stopped)`.
- [ ] **Streamed lead seat** (cost Tier 1, Chunk 3 — **off by default**, so
      this row runs only when trialling it). From a source checkout, set
      `$env:BUILD_A_SPEC_QC_BATCH_WARM_LEAD = "1"` (Command Prompt:
      `set BUILD_A_SPEC_QC_BATCH_WARM_LEAD=1`) and run Final QC on a section
      big enough that one kind of finding faces at least 20 verifier seats
      (ten or more medium findings from lenses without web tools, say). In
      the Review Room, one seat of that group shows live activity while the
      rest wait on the batch; the batch line counts it when it finishes. In
      the report, that seat's usage is priced at list (the JSON export shows
      its `cost_multiplier` as 1.0, every batched seat's as 0.5), the
      methodology lists **Streamed lead seat**, and
      `.\.venv\Scripts\python tools\qc_export_cost_profile.py "<the JSON export>"`
      shows a `seat:list-price:<group>` row. With the switch off (the
      default) none of that appears and the methodology does not mention a
      lead.

### Redline export (v1.0.0)

- [ ] Open a redline in **real Word**: the reviewing pane shows
      "Build-a-Spec" as author; **Accept All** yields the current document;
      **Reject All** yields the master; word-level edits read cleanly; deleted
      paragraphs collapse on accept.

### Imported specs (v1.9.0, reworked v1.14.0–v1.15.0)

- [ ] Import a real office master: it lands **detached and editable at
      once** — edit a heading and add an article with no dialog, no
      waiting, and no "pending" strip. *Export Word (keeps your formatting)*
      leads the Export menu and round-trips the master's fonts, headers and
      footers with the edits in place; *Open in Word* opens that export;
      *Download original upload* still returns the upload **byte-identical**;
      *Redline vs master* still works. Save, reopen the `.baspec`, and
      confirm it loads still editable.
- [ ] A master that used to freeze — tracked changes, macros, an embedded
      object, or Restrict Editing — imports **editable** rather than
      read-only. Tracked changes is the one to try: the import shows the
      accepted text and warns that it did.
- [ ] A master whose numbering **starts one level in**: sibling articles
      arrive as siblings, with no invented `IMPORTED CONTENT` article.
      An **auto-numbered** master (Word multilevel list — the article
      headings' visible text is just the title) arrives with its PARTs and
      articles recognized as real structure, `spec_shape_detected` true,
      and no "not a spec section" banner.
- [ ] A `.baspec` saved **before v1.14.0** still opens under the byte-exact
      contract it was saved under: the panel shows **Edit freely** with its
      one-way dialog; confirming it unlocks heading edits and add-article
      with the original still downloadable byte-identical and *Redline vs
      master* still working. Save, reopen, and confirm the decision stuck.
- [ ] A **combined multi-section** file: only the first SECTION imports,
      and the import notes name the next section and the dropped block
      count instead of discarding them silently.
- [ ] A large master (1,000+ paragraphs) imports without freezing the app
      and is editable the moment it lands — nothing runs after the import
      any more, so nothing can be waited on.

### Export Word (keeps your formatting) keeps more of it (redline program, Phase 0)

On a **real office master**, export with *Export Word (keeps your
formatting)* and open the file in **real Word** after each edit below — the
fixtures in the suite are python-docx- and LibreOffice-built, and only Word
proves Word's layout:

- [ ] **Add a provision near the top of an article** in a master with typed
      letters ("A.⇥…"): every provision below it is relettered and keeps the
      **tab** after its letter and any **bold** or italic phrase; the new
      provision has the master's tab after its letter, not a space.
- [ ] **Delete the provision just above a landscape schedule page**: the page
      stays **landscape** (the section break stays with the content above
      it), and the document has as many sections as before.
- [ ] **Add an article in a Word-numbered master** (the article headings'
      visible text is only the title): the new heading looks like the other
      headings, and carries Word's number only — no typed number beside it.
- [ ] **An export with no edits** keeps the master's article numbers as
      written — `1.01 SUMMARY` stays `1.01`, not `1.1`.

### Redline on your original (redline program, Phase 1)

On a **real office master**, in **real Word**, from the **packaged** app. The
suite proves Accept All and Reject All with its own XML resolver on every
export; only Word proves Word agrees.

- [ ] Import the master and make a mix of edits: reword a provision, add one
      near the top of an article, delete one, move one. Export →
      **Redline on your original (tracked changes)**. The file is named
      `<your upload's name> - REDLINE.docx`, opens with **no repair prompt**,
      and the Reviewing Pane lists the changes with **Build-a-Spec** as the
      author. In a master with typed letters, the provisions below the added
      one show their letter change (`A.` → `B.`) with the tab after it kept.
      (The judge below checks that Word reads every change as Build-a-Spec's;
      the repair prompt and the pane itself are this row's.)
- [ ] **Accept All and Reject All, judged by real Word**: run the judge
      (*Real Word as the judge*, below). It replaces comparing the two by eye:
      Word resolves every targeted markup shape and every corpus master under
      the corpus sweep's edit mixes, both ways, and each result must match
      Word's own save of the formatted export or of the upload.
- [ ] **Replace your master with it, end to end.** In Word, accept or reject
      every change, save, and close. Import the saved file into a New session:
      it imports with no tracked-changes warning and reads the way you left
      it in Word. That file is now the master.
- [ ] **Open redline in Word** (in the Export menu, desktop app only) opens
      the same redline from a temporary folder straight into Word. With Word
      still holding it open, click it again: a second copy opens, no error.
- [ ] **A master that already carries tracked changes** (saved in Word with
      changes neither accepted nor rejected): after import, *Redline on your
      original* is greyed out, and hovering it shows the server's reason with
      the fix (accept or reject them in Word, save, import again). *Open
      redline in Word* is not offered, and *Redline of extracted provisions*
      still downloads.

### Links and deeper provisions (redline program, Phase 1 follow-up)

On a **real office master** whose provisions hold hyperlinks (a link to a
referenced standard or to another section), in **real Word**. The suite
proves the XML; only Word proves how a link and a numbering level look and
behave.

- [ ] **Edit a provision that holds a link**: reword a word outside the
      link, and in another provision a word of the link's own text. Export
      with *Export Word (keeps your formatting)*: each link is still a link —
      clicking it goes where it went before — its text changed only where
      you changed it, and the words around it keep their bold or italic. In
      a master with typed letters, add a provision above one holding a link:
      it is relettered and keeps its link and the tab after its letter.
- [ ] **Add a word right after a link's text** (and, in another provision,
      right before it): the new word is not blue or underlined and is not
      part of the link — hovering it shows no link, and the link itself
      still goes to the same place.
- [ ] **Delete every word of a link**: nothing is left behind — no empty
      link, nothing clickable, no stray underline.
- [ ] **Redline on your original** of the same edits: Word shows a changed
      word of a link's text as deleted and inserted inside the link. (That
      **Accept All** leaves every surviving link in place and **Reject All**
      gives back the original links and text is the judge's: its
      `targeted/links` and `targeted/link-with-bookmark` groups.) Click a
      surviving link after Accept All: it still goes where it went.
- [ ] **Add the first sub-provision** under a provision of a Word-numbered
      master — the first "1." under an "A." in a master that has none yet.
      In the formatted export, and after Accept All in the redline, it
      prints at its own level (indented under the "A.", in the master's
      "1." format), not as the next letter beside its parent. Import the
      export again: it comes back under its parent, not as its sibling.
- [ ] **In a master whose numbering rides its paragraph styles** (PR1, PR2,
      … — most office masters), do the same, and note where Word draws the
      new sub-provision: at the level's own indent, or at its neighbour's
      (the export keeps the neighbour's paragraph style and sets the level
      on the paragraph; its number is right either way). Record what Word
      does — it decides whether the export should also take the level's own
      style.

### Real Word as the judge (redline program, Phase 2)

The suite proves the redline on your original with the app's own resolver;
this has real Word resolve the same files. On Windows with Microsoft Word,
from the repo (setup: `docs/DOCX_RENDERER_WINDOWS.md` → *Resolve mode*):

```powershell
$env:BUILD_A_SPEC_WORD_JUDGE = "1"
.\.venv\Scripts\python -m pytest -q tests\test_redline_word_judge.py
```

- [ ] Every group passes. `artifacts\word-judge\report.json` shows no `fail`
      or `error` case and names the Word version and build that judged it;
      record them. A failure names the group, the case, the resolution and
      the body child, with both sides rendered in the report — it is a real
      disagreement between Word and the app's own resolver, never something
      to tolerate away.
- [ ] Once the corpus has Word's own tracked-move sample
      (`actual_word_16_tracked_move`), the sample test passes too: Word's
      Accept All and Reject All of the moves Word itself wrote match the
      app's resolver. Record where the report says the moved bookmark
      landed each way.

### Word's own "Moved" marks (redline program, Phase 2 PR B)

Built before anyone ran the judge above on Windows (the owner waived that
gate on 2026-09-23), so these rows, and the judge's `targeted/native-move*`
groups, are the first time Word sees them. On a **Word-numbered** master
(Word draws its own numbers, so a move there changes no text), in **real
Word**, from the **packaged** app. The switch is
`BUILD_A_SPEC_REDLINE_NATIVE_MOVES`, on by default.

- [ ] **A moved provision shows as Moved.** Move one provision up past two
      others (nothing else), and in another article move a provision that
      has sub-provisions. Export → *Redline on your original*. The file
      opens with **no repair prompt**; the Reviewing Pane lists each move
      as a **Moved** change by Build-a-Spec — one entry per move, the
      provision and its sub-provisions as ONE move — and the page shows it
      green (double strikethrough where it was, double underline where it
      is, with a "Moved" balloon in the margin on a Word that shows them).
      Nothing is shown as an ordinary deletion or insertion.
- [ ] **Accept All and Reject All** of that file (Review → Accept → Accept
      All Changes, then undo and Reject All Changes): Accept All gives
      exactly what *Export Word (keeps your formatting)* gives for the same
      edits, with no empty paragraph where a provision moved from; Reject
      All gives your original back, with no empty paragraph where one moved
      to. (The judge's `targeted/native-moves` group checks the same with
      Word's own save; this row is the eye check that the pane and the page
      agree with it.)
- [ ] **In a master with typed letters**, move a provision: it shows as a
      deletion and an insertion (its letter changed with its position), and
      a provision's sub-provisions moved with it show as Moved.
- [ ] **With the switch off** the same move shows as a deletion where it was
      and an insertion where it is, exactly as in Phase 1. Quit the app,
      then start it from a terminal with the switch set (here from the build
      folder; the installed app's own path works the same) — in PowerShell:

      ```powershell
      $env:BUILD_A_SPEC_REDLINE_NATIVE_MOVES = "0"
      .\dist\BuildASpec\BuildASpec.exe
      ```

      in Command Prompt:

      ```bat
      set BUILD_A_SPEC_REDLINE_NATIVE_MOVES=0
      .\dist\BuildASpec\BuildASpec.exe
      ```

      Export the redline again: no Moved marks. Accept All and Reject All
      give the same two files as above.

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

### Next section in one click (v1.20.0)

- [ ] **Next section → on a rich section.** With a profile, an override, a
      research round, an attached reference and a recorded fact in the
      session, press *Next section →*: the save prompt appears; *Save, then
      start* writes the `.baspec` and opens a new page named from the pick,
      with the references and facts in their panels, the standards strip
      showing the carried edition, and readiness passing research as
      carried. The chat marker names what was carried.
- [ ] **Leave it unnamed, then import.** Choose *Leave it unnamed* and
      import an office master into the new section: the import is accepted
      (a named page would have been refused) and the project setup survives
      the import.
- [ ] **In a tour, the button is absent**; with a turn streaming, it is
      disabled.

### Project workspace (v1.21.0)

The project-workspace program's rows, one subsection per phase. Phase 2's
Project panel was already in the 1.20.0 build, but 1.21.0 is the first
release to announce it, so its rows belong to this release. Phase 1 (Next
section →) is under v1.20.0 above.

#### The project has a home (Phase 2)

- [ ] **Save beside a brief and see the panel.** In a section that has
      exported a project brief (Export → *Export project brief*), save the
      section into the same folder as the `.basproject`: the **Project**
      panel appears above Project facts, opens itself, names the folder and
      the brief with its "brief last updated" date, and marks the section
      *current*. Now the other order: in a fresh section, save first and
      then export the brief into that same folder. The panel shows the
      project with no folder and says to save once more; press Save and it
      names the folder. Close the app and reopen that file: the folder is
      found again (the save wrote the project into the file).
- [ ] **Open a sibling by number.** From that section press *Next section →*,
      pick the next section, and save it: the first Save dialog opens in the
      project folder. Export the brief again, then press *Open* on the first
      section's row: the save prompt appears (*Save, then open*), and the
      first section opens from its file with its conversation, document and
      panels, the Project panel still showing the folder. A row whose file
      was deleted says "file not found beside the brief" and has no *Open*;
      a stray `.baspec` in the folder is listed under "Not in the registry".
- [ ] **A moved folder still resolves.** Close the app, move (or rename) the
      whole project folder, reopen the app, and *Open* a section file from
      its new location: the Project panel names the new folder and *Open*
      on a sibling works. Neither the `.baspec` nor the `.basproject` names
      any folder (open one in a text editor / zip viewer to confirm).
- [ ] **A browser session shows no panel.** In dev mode opened in a plain
      browser (no shell bridge), a linked section shows no Project panel;
      Open and New session → *New section in an existing project* still work
      as before.
- [ ] **In the tour** the paper chapter's Project panel step shows two
      sections of a practice project with no folder, no *Open* and no
      *Next section →*.

#### The brief is a living file (Phase 3)

- [ ] **Save twice from two sections, then read the brief.** With two
      sections of one project in their folder (the Phase 2 rows above), open
      the first, record a project fact in Project facts, and Save. Open the
      second, record a different fact, attach a reference document, and
      Save. Open the `.basproject` in a text editor: BOTH facts and the new
      document are in it, and nothing that was there before is gone. Save the
      second section again without changing anything: the file's bytes (and
      its `updated_at`) do not change.
- [ ] **Update project brief.** In the Project panel press *Update project
      brief*: it says what it added (or that the brief already had
      everything). Rename the `.basproject` away and press it again: an error
      in the panel, and a Save still succeeds with a notice saying the brief
      was not updated.
- [ ] **Export onto the existing file.** Export → *Export project brief* and
      pick the project's existing `.basproject`. Windows' Save dialog first
      asks whether to replace it; answer *Yes*. The export then merges
      instead of replacing: the other section's facts are still in the file
      afterwards. Export onto a `.basproject` of a DIFFERENT project: after
      Windows' question, the app asks "Replace the file?"; *No* leaves that
      file byte-identical, *Yes* replaces it. Export onto a text file renamed
      `.basproject`: the same two questions.
- [ ] **Pull into the older section.** After the second section saved its
      fact and document, open the first: the Project panel header says
      "changes to pull" and the panel offers *Pull project changes* with what
      it would bring. Pull: the second section's fact and document appear
      (the document with the next free id; a fact citing it follows it), a
      chat marker says what came in, and the offer disappears. If the second
      section recorded a different NFPA 13 edition, the notice strip lists
      the disagreement and an "assumed" project fact reads "Sections
      disagree on the NFPA 13 edition … Resolve before issue." — the first
      section's own edition and profile are unchanged. A retained Final QC
      report now reads stale. With a turn streaming, *Pull* is disabled.
- [ ] **A hand-edited brief with a bad reference.** Edit the `.basproject`
      so a fact cites `ref-99` (a document it does not hold) and Pull: the
      fact arrives citing the project brief and the section that recorded
      it, and the notice strip says a carried fact's source could not be
      found. Edit it again so two documents share one `rid`: Pull and Save
      both refuse to merge it, and the file is left exactly as it was.

#### Harvest: nothing settled is left in the transcript (Phase 4)

- [ ] **A real harvest on a real section (paid, owner-run).** In a section
      with a conversation that settled things the assistant never recorded
      (ask it to "just agree and carry on" without recording facts), open
      the Project facts panel: it reads "N replies since facts were last
      harvested". Press *Harvest facts…*: the dialog says what it will read
      and that it is one paid call, and NOTHING is spent until *Run the
      harvest*. Run it: the sheet lists proposals, each with its source and
      the line it rests on; a quote the app could not find in what was read
      is flagged. Untick one, edit another's statement, and *Record*: only
      the ticked ones land in Project facts; Settings → Usage shows a
      "Fact harvest" line; the panel's hint drops to nothing. Run it again at
      once: it reads no new replies.
- [ ] **A proposal with a bad source is refused.** On the sheet, edit a
      proposal's source to `ref-99` (no such document), tick it and
      *Record*: nothing is recorded, the row says why, and the sheet stays
      open — correct the source (or clear it and set the kind to "you said
      it") and *Record* succeeds without running the call again. In the
      panel, *+ Add a fact* is unaffected; in chat, ask the assistant to
      record a fact citing a research finding that does not exist: it is told
      the source names nothing and records it correctly instead.
- [ ] **Commit, then the Final QC stale marker.** With a retained Final QC
      report showing current, harvest and record one fact: the Final QC
      drawer and the readiness card now read stale (facts are one of its
      inputs). Record none on the next harvest (*Record none — mark these
      replies read*): nothing changes except the hint.
- [ ] **The nudges never run it.** With replies waiting, open *Next
      section →*: a warning line says how many replies are unharvested with
      *Harvest first* — press it, the harvest dialog opens over Next section,
      Escape closes only the harvest, and Next section is still there. Open
      the Export menu: the same line sits under *Export project brief* and
      opens the same dialog. Neither spends anything until *Run*.
- [ ] **Stale while reviewing.** Run a harvest, leave the sheet open, and in
      the panel add a fact by hand (or have the assistant change the
      document): *Record* is refused as stale and nothing is recorded. A
      reply that changes neither the document nor the facts does not make it
      stale. Leave a sheet open for more
      than 15 minutes: *Record* says the preview expired.
- [ ] **A fact whose source is gone is flagged, not changed.** Record a fact
      citing an attached document, then delete that document: the fact shows
      "⚠ source not found" with its original source in the tooltip, still
      editable and retirable. Attaching the same file again does not clear
      it — a re-attached document gets a new id, and the fact keeps exactly
      the source it was recorded with; retire it, or record it again
      against the new id.
- [ ] **A fact tied to a forgotten reply stays flagged.** Attach a document,
      ask a question that makes the assistant read it (reply 2), then settle
      something in one more exchange (reply 3). Harvest, and accept a
      proposal whose source is `turn:3`: it shows no flag in the panel.
      Delete the document: the conversation forgets replies 2 and 3, and the
      fact shows "⚠ source not found". Ask two new questions: the new replies
      take the numbers 2 and 3, and the fact STILL shows "⚠ source not
      found" — it names the reply it was recorded against, not whatever holds
      the number now. A harvested fact citing reply 1 stays unflagged
      throughout.
- [ ] **A draft with no conversation can still be harvested.** Import a
      master (or open a project whose chat is empty) and edit a provision by
      hand, without sending a message: the Project facts panel is there with
      *Harvest facts…* enabled, and the dialog says no replies are waiting
      but the draft will be read. A brand-new empty session shows no panel
      at all; if facts exist but there is nothing to read, *Harvest facts…*
      is disabled and its tooltip says why.
- [ ] **In the tour** the Project facts step never shows *Harvest facts…*.

#### See what each turn carries (Phase 5, part A)

- [ ] **The Context makeup row after one turn.** Before any message,
      Settings → *Developer tools* → Session state shows *Context makeup*
      as "not measured". Open a section with a research profile, send one
      message, and press *Refresh*: the row now starts with the total and
      the research share ("no research profile" on a section without one),
      then the document and the other blocks largest first, and it sits
      beside *Context gauge*. *Recent activity* filtered to `prompt_refs`
      shows the same numbers under `context_sizes`, one event per turn.
      *New session* and *Open project* each put the row back to "not
      measured". The measurement that decides whether part B is built is
      owner-run and is described in
      `docs/plans/project-workspace/05_RELEVANCE_TRIM.md` (the gate), not
      here — it is not a release check.

### Chat history compaction (Phases 1–2; the page-text trim on by default)

- [ ] **Stale outlines leave the saved conversation** (Phase 1). In a
      section of a few articles, press *Draft full section*, then send one
      more message. Settings → Developer tools → Session state → **History
      makeup** mentions no stale outlines. Save, copy the `.baspec` to a
      `.zip` and open its `project.json`: each edit result says its outline
      was omitted instead of repeating the document.
- [ ] **The fetch elision canary passes**:
      `.\.venv\Scripts\python tools\fetch_elision_canary.py --run` (one
      request, about two cents at most). It sends a saved conversation whose
      fetched page text was trimmed to a note that carries the passage the
      reply quoted, with the reply's citation into the old text removed,
      and must report that the provider accepted it. Its first run
      (2026-09-23) was refused on the older shape, which kept that
      citation; its second run, the same day, passed on this one, and the
      page-text trim has been on by default since. If it reports a refusal,
      switch the trim off (`BUILD_A_SPEC_ELIDE_FETCHED_PAGES=0`), run it
      again with `--control`, and record both outputs in the compaction
      plan's Phase 2 section.
- [ ] **With the default, a fetched page leaves the saved conversation.**
      Ask the assistant to read a public web page and quote something from
      it, then send one more message. **History makeup** does not mention
      fetched pages carrying their text, and the next message still gets an
      answer (the assistant may read the page again). Save, copy the
      `.baspec` to a `.zip` and open its `project.json`: the page's address
      and title are there and its body text is not. The quoted passage is
      still there, inside the page's note, and the reply no longer carries
      a citation into the removed text.
- [ ] **With the trim switched off, a fetched page stays in the saved
      conversation.** Launch with `BUILD_A_SPEC_ELIDE_FETCHED_PAGES=0`
      (PowerShell: `$env:BUILD_A_SPEC_ELIDE_FETCHED_PAGES = "0"`) and repeat
      the web-page check. After the reply, **History makeup** reports the
      page ("1 fetched pages carrying their text"), and the saved
      `project.json` still holds the page's text.

### Chat history compaction (Phase 3; routine condensing on by default)

These rows spend a little: each summary is one model call. Routine
condensing is on by default, so these rows only lower the point where it
starts. Set the knobs in the shell you launch the app from, and clear them
when done.

- [ ] **Routine condensing.** Launch with
      `BUILD_A_SPEC_CHAT_COMPACTION_THRESHOLD=10000` and
      `BUILD_A_SPEC_CHAT_COMPACTION_KEEP_TURNS=1` (nothing needs switching
      on). Hold a conversation of six or so turns, stating one exact value
      early ("use 42 gpm for the riser"). Soon after the reply that passes
      the threshold — without sending another message — a divider appears
      above the last turn: *Above: turns 1–N, condensed for the model ·
      View summary*. The transcript above it is all still there.
- [ ] **View summary** opens a sheet with the summary under its section
      headings, and says how many turns it stands in for and the token
      estimate before and after. Settings shows a **Conversation
      condensing** line in the usage table; Developer tools → Session state
      → **Condensed conversation** shows the record's sizes, never its text.
- [ ] **Recall.** Ask "what flow did we settle on for the riser, exactly?"
      The answer gives 42 gpm (the assistant may look the turn up first; the
      status line shows it working).
- [ ] **It survives a save.** Save, close and reopen the project: the
      divider is back in the same place, and no new summary is written on
      opening (the usage table's condensing line does not move).
- [ ] **New session** clears the divider and the Developer tools row.
- [ ] **Removing a document the condensed turns read.** Early in the
      conversation, attach a small reference document and ask the
      assistant to read it. Once the divider appears, remove that document
      from the panel: the divider goes with it at once (**View summary** is
      gone rather than failing).
- [ ] **Condensing after web lookups.** With the same two knobs set,
      and the page-text trim switched off
      (`BUILD_A_SPEC_ELIDE_FETCHED_PAGES=0`, so the saved replies keep
      their citations into the pages), ask the assistant to read a public
      web page and quote it; in a later turn, ask it to read a second page
      and quote that. Keep chatting until the divider moves past the first
      page's turn. The next message still gets an answer. Before the
      citation repair, a condensed conversation whose early turns read
      pages could be refused from then on, because its later citations
      pointed at the wrong page. With the trim on (the default), the saved
      replies carry no citations into the pages, so this row needs it off.
- [ ] **Switching it off.** With the same two knobs set, also launch with
      `BUILD_A_SPEC_CHAT_COMPACTION=0` (PowerShell:
      `$env:BUILD_A_SPEC_CHAT_COMPACTION = "0"`) and hold the same
      conversation: no divider appears, and the usage table gains no
      condensing line.
- [ ] **The backstop.** Clear the two knobs and launch with
      `BUILD_A_SPEC_CHAT_COMPACTION=0` and
      `BUILD_A_SPEC_CONTEXT_WINDOW=60000`, so the backstop is the only
      thing that condenses. Keep chatting: once a message would not fit,
      the status line reads *Condensing earlier conversation…* before the
      reply starts, the reply still arrives, the divider appears, and the
      sheet adds *Condensed at the moment a message would not have fit*.
- [ ] **Never in the tour.** Relaunch with only the two routine knobs set,
      take the guided tour's conversation chapter and chat in a practice
      copy: no divider appears, and the usage table gains no condensing
      line.

### State that must recover (v1.17.0)

None of these have a DOM harness; the source-level pins in
`frontend/tests/strandedState.test.ts` and `downloads.test.ts` guard the
code shape, and these rows guard the behavior. Stop the backend process
(leave the window open) for each.

- [ ] **Settings → Test key / Save** with the backend stopped: the buttons
      come back (no permanent "Working…") and the message reads *Could not
      test the key: …*, not *Key rejected*. Restart the backend and Test
      again — it works without reopening Settings.
- [ ] **Review walk, PART hold**: press-and-hold the *confirm this part*
      button on a PART with outstanding blocks in two articles and, mid-hold,
      press New session. Nothing lands in the new session and nothing errors
      — the timer used to fire a bulk confirm into the document you had just
      replaced.
- [ ] **Dropped polls**: with spend on the meter and a readiness card
      showing, stop the backend and click around the panel. The header's
      spend figure, the context gauge and the Issue-readiness card keep their
      last values (they used to blank to "—", vanish, and read "not yet
      reviewed"); restart the backend and they refresh on the next poll.
- [ ] **Export menu** with the backend stopped: the trigger reads
      *Preparing…* and then an *Export failed —* strip names the reason,
      dismissible with ✕. With it running, every entry still downloads under
      the server's filename and *Download exact original DOCX* is
      byte-identical to the upload.
- [ ] **Settings → What's new** with the backend stopped: a *What's new
      could not be loaded* notice appears in the document panel instead of
      nothing happening.
- [ ] **One Escape, one dialog** (v1.17.0, the dialog stack): open Help →
      *Why trust it?* → *I'm not convinced*, press Escape — only the dossier
      closes and Help stays; open Settings → *Developer tools*, press Escape —
      only Developer tools closes. Escape in Settings alone closes it (it had
      no keyboard handling before), and Tab stays inside every open dialog.
      In the tour's between-chapters card press Escape, then Escape again on
      the *End the guided tour?* confirmation — the confirmation closes and
      the tour continues. In New session → *Start from a template*, Escape
      inside a template preview returns to the list first; a second Escape
      closes the dialog.
- [ ] **A long reply stays smooth**: ask for something that streams for a
      minute and scroll up mid-stream — the view must not yank back to the
      bottom while you read, and scrolling back within ~80 px of the bottom
      re-pins it. The chat no longer re-measures itself every animation
      frame, so an idle stream should show no steady CPU draw in Task
      Manager beyond the streaming text itself.

---

## Manual release (on a Windows machine)

The commands below run as written in PowerShell (the Windows default
terminal) and in Command Prompt. Run them from the repo root.

### 0. One-time setup

- Python 3.11+ and Node 22+ installed (`npm test` needs Node's type stripping; 20 cannot parse the `.ts` tests).
- [Inno Setup 6](https://jrsoftware.org/isinfo.php) installed (`ISCC.exe`
  on PATH, or use its full path).
- `pip install pyinstaller` in the build venv (build-time only — it is
  deliberately NOT in `requirements.txt`).
- On Windows, `pip install pythonnet` too if a plain
  `pip install -r requirements.txt` didn't pull it — it is what loads
  pywebview's Edge WebView2 backend, and PyInstaller can only bundle what
  is installed.
- In PowerShell, `.\.venv\Scripts\activate` runs `Activate.ps1`. If
  PowerShell refuses because running scripts is disabled on this system,
  allow local scripts for your account once with
  `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

### 1. Version bump + consistency gate

1. Bump `VERSION` in `backend/settings.py` **and** `version` in
   `frontend/package.json` (then `npm install --package-lock-only`) **and**
   the `**vX.Y.Z**` headline on `README.md`'s first prose line — the gate
   below checks all three.
2. Gate:

   ```
   python packaging\windows\check_release_version.py --tag vX.Y.Z
   ```

### 2. Build

```
python -m venv .venv
.\.venv\Scripts\activate
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

```
.\dist\BuildASpec\BuildASpec.exe --version
.\dist\BuildASpec\BuildASpec.exe --selfcheck
.\dist\BuildASpec\BuildASpec.exe --boot-check
```

`--selfcheck` imports the FastAPI surface, the research engine, the
compliance checker, the updater, the docx importer, and pywebview, and
verifies the bundled frontend is present — exit 0 required. `--boot-check`
then starts the backend headless exactly the way the app does and waits for
`/api/health` — the check a pure import cannot make, which is what catches a
windowed-mode boot crash (set the environment variable
`BUILD_A_SPEC_DISABLE_UPDATE_CHECK` to `1` first, as the workflow does).
(The build is windowed, so set `BUILD_A_SPEC_SELFCHECK_OUT` to
`selfcheck.txt` to capture output to a file if the console shows nothing.)
PowerShell sets an environment variable with `$env:NAME = "value"`, for
example `$env:BUILD_A_SPEC_SELFCHECK_OUT = "selfcheck.txt"`; Command Prompt
uses `set NAME=value`. The `set` form does not carry over: in PowerShell it
creates a PowerShell variable the app never sees. Then launch it plainly
once and click through: chat turn, import, export.

### 4. Installer

Optionally fetch the WebView2 bootstrapper first so the installer bundles
it (the app already falls back to a browser window without it):

```
curl.exe -L -o packaging\windows\MicrosoftEdgeWebview2Setup.exe "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
```

Then compile:

```
ISCC /DMyAppVersion=X.Y.Z packaging\windows\installer.iss
```

Output: `dist\installer\BuildASpecSetup.exe`. Install it on a clean
profile and launch once. **Do not change the AppId GUID in
`installer.iss` — ever.** It is what makes upgrades install in place.

### 5. Manifest

```
python packaging\windows\render_release_notes.py --version X.Y.Z --notes-out release-notes.txt --body-out release-body.md

python packaging\windows\make_manifest.py --version X.Y.Z --installer dist\installer\BuildASpecSetup.exe --url https://github.com/Abe-Borg/build-a-spec/releases/download/vX.Y.Z/BuildASpecSetup.exe --out latest.json --notes-file release-notes.txt --published-at YYYY-MM-DD
```

Each command is one line on purpose: `^` continues a line only in Command
Prompt, and PowerShell reads it as an argument.

`--notes` still takes a literal string, but prefer `--notes-file` so the
manifest, the release page, and the app's own What's-new modal all come from
the same `backend/release_notes.py` entry. `release-body.md` is the release
body to paste in step 6.

The `--url` must be the final release-asset URL (tag path shown), and it
must be https — `parse_manifest` refuses anything else.

### 6. GitHub Release

1. Tag: `git tag vX.Y.Z`, then `git push --tags`.
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
