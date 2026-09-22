# Phase 7 — Release closeout: the one release for Phases 2–6

**Status:** not started. **Depends on:** every phase Abraham wants in the
release, merged (Phases 2, 3, 4 and 5A at minimum; 5B and 6 only if built).
This is the LAST phase, and the only one that touches a version number.

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
