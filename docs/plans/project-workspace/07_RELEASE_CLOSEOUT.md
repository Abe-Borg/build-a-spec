# Phase 7 — Release closeout: the one release for Phases 2–6

**Status:** in review (`c526a38`–`7143b48`, PR #188) — cut as **v1.21.0**. **Depends on:**
every phase Abraham wants in the release, merged (Phases 2, 3, 4 and 5A at
minimum; 5B and 6 only if built). This is the LAST phase, and the only one
that touches a version number.

## Goal

One release that describes everything the program shipped since v1.20.0 —
which is itself unreleased-but-cut (see the README's "Release policy") —
so users get one update, one What's-new modal, and one release page.

## Steps (in order; each is a checkbox for the session that runs it)

1. **Reconcile the record.** Every phase in the release reads `complete`
   with its merged PR; anything still `in review` is either finished first
   or left out of the release deliberately (say which, in the record).
2. **Choose the version.** Minor bump from 1.20.0 → **1.21.0** (features
   shipped; the repo's precedent is minor for a feature batch, patch for a
   fix-only release). Check the published releases through the GitHub API
   (`git tag -l` is empty in a fresh clone) so the bound the renderer will
   use is known: it describes every version since the last PUBLISHED one,
   so if v1.20.0 was never tagged the page covers 1.20.0 and 1.21.0
   together — the intended outcome.
3. **Write the release-notes entry** at the top of `RELEASE_NOTES` in
   `backend/release_notes.py`: headline, a summary paragraph in the user's
   words, and one `ReleaseSection` per theme built from each phase file's
   "Release-note draft" (Projects, Research, Facts…). Every item says what
   the user can now DO. A behaviour change (Phase 4's source-ref check on
   the recording tool) is stated plainly.
   Also collect the "Release-note draft" sections of
   `../CHAT_HISTORY_COMPACTION_2026-09-22.md` for every phase of that plan
   merged since v1.20.0 — it follows this program's release policy and
   rides this release rather than cutting its own.
4. **Bump the five version sites**: `backend/settings.py` `VERSION`,
   `frontend/package.json` `version`, BOTH root `version` fields of
   `frontend/package-lock.json` (the top-level one and `packages[""]`), and
   the README headline `**vX.Y.Z**`. `check_release_version.py --tag
   v1.21.0` and `npm ci --dry-run` (from `frontend/`) prove it.
5. **README.** Retitle "## Project workspace (in progress)" to "## Shipped
   in v1.21.0 (…)" and read it top to bottom as one section; the
   Architecture route list names every route the program added; the
   Configuration table has a row for every `BUILD_A_SPEC_*` knob the code
   reads (`tests/test_docs_consistency.py` enforces it).
6. **CLAUDE.md.** One closeout section ("The project workspace program, as
   shipped") with an errata bullet for anything the phase sections got
   wrong along the way; the Layout block current for every module the
   program added.
7. **Runbook.** `docs/RELEASE_WINDOWS.md`: the program's QA rows
   consolidated under one "Project workspace (v1.21.0)" heading; the
   "Minimum before any release" list unchanged.
8. **The trust dossier.** `TrustDeepDiveModal.tsx`'s runtime cards and any
   number it quotes re-verified against the code (a harvest card exists,
   the brief card says the file is written on save, "no model runs on its
   own" is still scoped correctly).
9. **Full gate.** `ruff check .`, the full pytest suite, `(cd frontend &&
   npm test && npm run build)`, `check_release_version.py --tag v1.21.0`,
   `render_release_notes.py --version 1.21.0 --notes-out … --body-out …`
   and READ the rendered body: it must describe 1.20.0's Next-section
   button too if v1.20.0 was never published.
10. **PR**, Codex, merge. Then mark Phase 7 `in review` in the record with
    the PR; the next session (or Abraham) marks it `complete` and the
    program done.

## The owner's part, after the merge

```
git checkout master && git pull
git tag v1.21.0 && git push --tags
```

The tag build runs the version gate, the suite on Windows, the freeze, the
smoke checks, the installer, and publishes the GitHub Release with
`BuildASpecSetup.exe` and `latest.json`. Then the "Immediately after
publishing" rows in `docs/RELEASE_WINDOWS.md` (the live update path from a
previous-version machine).

## Deviations from the plan

(Record here what was left out of the release and why, and any step that
had to differ.)

As built, 2026-09-23 (v1.21.0):

1. **Compaction Phase 2 ships switched off** (steps 1 and 3). PR #183
   merged (`7fc6e24`) without the live canary its own plan made the merge
   gate, and the canary was still unrun at the closeout. It needs the
   owner's API key and one paid request, and none was available to this
   session. The owner handed the call to this session rather than run it.
   A history the provider refused would fail every later message in the
   project, and the page text is gone once the project is saved. So the
   release does not bet on it. `BUILD_A_SPEC_ELIDE_FETCHED_PAGES` (default
   `0`) gates both the commit-time and the load-time trim, and the canary
   forces the trim on for its own request. The code, tests and canary all
   stay on `master`. The compaction plan's Phase 2 → Canary result says
   what turns it on. Its release-note draft is withheld from this entry.
2. **Phase 5 Part B and Phase 6 are left out** (step 1). Part B waits on
   the owner's gate measurement (a real 21 30 00 sitting seeded from a
   researched 21 13 13), and Phase 6 is deferred by D5. Neither was
   started, so there is nothing to finish first. Each can ship in a later
   release of its own.
3. **The redline plan's Phase 0 draft is used too** (step 3). Step 3 names
   only the compaction plan's drafts. Redline Phase 0 (PR #184) also merged
   since v1.20.0, and its plan left the release to the owner. 1.21.0 is
   that release, so the Phase 0 draft under "Release-note drafts" in
   `../REDLINE_ON_ORIGINAL_2026-09-22.md` is its "Word export" section.
4. **Phase 2 is announced, not introduced** (steps 2, 3 and 9). The v1.20.0
   tag was cut after PR #176 merged, so the Project panel shipped in the
   1.20.0 build without a release note. The 1.21.0 item says it arrived in
   1.20.0. v1.20.0 was published (2026-09-22), so the rendered page covers
   1.21.0 alone. Step 9's case of the page also having to describe the
   Next-section button does not arise.
5. **The runbook gained a compaction Phase 1 row; redline Phase 0's rows
   came from PR #187** (step 7). The compaction section had rows only for
   Phase 2. It now has one for Phase 1, and its Phase 2 rows are rewritten
   for the switch. Master had no redline Phase 0 rows, and PR #187 was
   adding them at the same place, so this PR wrote none. #187 then merged
   during review (deviation 8), which brought them into the release.
6. **The trust dossier needed no edit** (step 8). Every number it quotes
   was re-checked against the code, it has a harvest card, the brief card
   says the file is written on save, and "no model runs that you did not
   start" still names the harvest as never automatic.
7. **Step 5's route list needed no edit.** The Architecture block already
   named every route Phases 2–4 added (each phase added its own), and the
   Configuration table gained one row: the new switch.
8. **Redline Phase 1's backend rides this release too.** PR #187 merged
   (`0aa2e98`) while this PR was in review, so the 1.21.0 build carries the
   redline on your original. It is reachable only through the API
   (`?redline=master&mode=preserved`), because its menu item arrives with
   Phase 1's UI PR. So the release entry does not announce it, and its draft
   waits for the release that carries the UI. The Export menu's existing
   items name their mode explicitly, so what they download is unchanged.
   Merging master into this branch resolved three docs conflicts, all
   recorded in CLAUDE.md's closeout section.
