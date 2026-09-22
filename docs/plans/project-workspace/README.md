# Project workspace — the program

Owner: Abraham. Opened 2026-09-22. This folder is the program's **handoff**:
a fresh session reads this README, reconciles the implementation record
against `master`, picks the next phase, reads that phase's file, builds it,
and records where it stands here before stopping. Nothing about where the
program stands lives anywhere else — not in a chat, not in a PR description.

The assessment that started it and the original one-file plan are in
[`../PROJECT_WORKSPACE_2026-09-22.md`](../PROJECT_WORKSPACE_2026-09-22.md).
That file is the design RECORD (Part 1: what the app already did; Part 2:
the six-phase plan as reviewed and ratified). The phase files here are the
SPECS: they restate each phase at the level a session can build from — the
files and functions to touch, the data shapes, the routes, the tests by
name, the docs — and they win where the two differ, with the difference
recorded under the phase file's "Deviations from the plan" heading.

## The ask, in one paragraph

Finish the common work on the fire-sprinkler section (21 13 13) — the
client and jurisdiction research, the location facts, the system facts, the
project facts — then swap in the fire-pump section (21 30 00) and have all
of that carry over, without dragging the previous session's context along.
v1.17.0's project brief already carries the assets; this program makes the
project a first-class thing: one living project file, sections that hang
off it, switching between them in one click, everything project-level
captured automatically and flowing in every direction.

## Implementation record

Three states, and who sets each:

- **not started** — nothing pushed.
- **in review** — set by the phase's own implementation PR, which cannot
  know whether it will merge: the row carries the PR number and the commit.
  A later session never treats `in review` as done.
- **complete** — set AFTER the merge, by the first later session to touch
  this file (the reconcile step in the handoff prompt below), or by Abraham
  by hand. The proof is on `master`.

A phase's status update therefore rides the NEXT PR, not its own. `master`
is the source of truth; an `in review` row names the PR to check; a PR
closed without merging is reconciled back to `not started` with a note.

| Phase | File | Status | Commit/PR | Notes |
|---|---|---|---|---|
| plan | `../PROJECT_WORKSPACE_2026-09-22.md` | **complete** | PR #173 | assessment + plan; four Codex findings folded into Phases 3–4 |
| 1 | (in the plan file) | **complete** | `b8bac2f` + `28bad83` (PR #174) | Next section →; **cut as v1.20.0**, tagged by Abraham at `0a744ab` on 2026-09-22 — a tree that also holds Phase 2 (see "Release policy") |
| handoff | this folder | **complete** | `aa119fa` (PR #175, merged `5b25df9`) | the program folder, the specs, the release policy |
| 2 | `02_PROJECT_HOME.md` | **complete** | `7cf3893` + `3da1e64` (PR #176, merged `c6b7803`) | project folder + Project panel; desktop shell only, the browser keeps the file relay; 13 as-built deviations in the phase file |
| 3 | `03_WRITE_BACK_MERGE.md` | **complete** | `4176a56` + `5cd6a8c` (PR #179, merged `5cdc83a`) | the brief is a living file: save-time refresh, Update project brief, export-onto-existing merge, Pull project changes; 21 as-built deviations in the phase file (19 as built + 2 from the Codex review) |
| 4 | `04_HARVEST.md` | **in review** | `54d2437` (PR #185) | the fact harvest: one opt-in paid call, a review sheet, one-batch commit; a fact's source must resolve (the recording tool, the panel, the commit), older ones flagged, never rewritten; 23 as-built deviations in the phase file |
| 5 | `05_RELEVANCE_TRIM.md` | not started | | part A (measure) any time; part B gated on the measurement |
| 6 | `06_CLIENT_LIBRARY.md` | not started | | optional (D5); after 3; only when a second project for one client exists |
| 7 | `07_RELEASE_CLOSEOUT.md` | not started | | **last.** The one release for Phases 2–6; the tag is Abraham's |

## Release policy

**One release at the very end** (Abraham, 2026-09-22), cut by Phase 7.
Phases 2–6 land as ordinary PRs with **no version bump and no release-notes
entry**: `settings.VERSION` stays 1.20.0 until Phase 7, and the suite's rule
"a version has an entry" stays satisfied by the 1.20.0 entry already in
`backend/release_notes.py`. Each phase instead writes what a user can now
do into the **"Release-note draft"** section of its own phase file; Phase 7
writes the real entry from those drafts.

v1.20.0 is already cut and merged (Phase 1). It does not need its own tag:
the release renderer (`packaging/windows/render_release_notes.py`) describes
every version since the last PUBLISHED release, so the closeout's tag build
will cover 1.20.0's notes alongside the closeout's own. Tagging v1.20.0 now
is harmless and simply ships the Next-section button early; skipping it
loses nothing.

**What happened (recorded 2026-09-22, during Phase 3).** Abraham tagged
v1.20.0 at `0a744ab`, the PR #178 merge. Phase 2 had already merged
(`c6b7803`), so the v1.20.0 build holds more than the Next-section button: it
also carries Phase 2's project folder and Project panel, plus PRs #177 and
#178. Its release notes describe the button, the Export-brief change and the
Opus 5.5 move, but not Phase 2. The tag build was still running when this
was written. For Phase 7 this means:

- If v1.20.0 publishes, its entry is frozen (CLAUDE.md, "A released
  version's entry is frozen"). The closeout ships as a new version with its
  own entry.
- The closeout entry is the only place Phase 2's draft notes can reach
  users. It should say the Project panel arrived in 1.20.0, not call it new.

**Confirmed (2026-09-22, during Phase 4's reconcile):** the v1.20.0 tag
build published — the GitHub Releases API lists *Build-a-Spec 1.20.0*,
published 2026-09-22 20:31 UTC. The first bullet above is therefore the
case: the 1.20.0 entry is frozen, and the closeout ships as a new version
(1.21.0 in `07_RELEASE_CLOSEOUT.md`). Unlike Phase 2, none of Phase 3 is in
the 1.20.0 build (PR #179 merged after the tag), so the closeout entry
announces Phase 3's work as new.

## How to hand this off

Give a fresh coding agent this prompt from the repository root:

```text
Read these files completely before touching code:

1. CLAUDE.md
2. docs/plans/project-workspace/README.md
3. The phase file for the phase you will build (chosen below)

FIRST, reconcile the implementation record in the README against master.
For every row marked "in review", check whether its PR merged (the GitHub
API, or `git log origin/master --oneline` for its commit): merged -> mark it
"complete" in your PR; closed without merging -> mark it "not started" with
a one-line note; still open -> leave it, and do not start a phase that
depends on it (continue that PR if it is yours to drive, else pick an
independent phase). Never treat "in review" as done.

THEN implement exactly ONE phase: the first phase whose Status is "not
started" and whose "Depends on" line (in its phase file) is satisfied. Its
phase file is the spec. Decisions D1-D5 in the plan file are ratified and
binding. Record every as-built deviation under the phase file's "Deviations
from the plan" heading rather than rewriting the spec text. If current code
makes part of the spec unsafe, stop and explain the conflict with concrete
code evidence.

Keep tests hermetic: no network and no real API key. Use tests/fakes.py for
provider behavior. A phase ships as ONE pull request. Phases 2-6 bump NO
version and add NO release-notes entry: write the user-facing notes into the
phase file's "Release-note draft" section instead (Phase 7 cuts the release
from them). Every phase DOES update README.md (a subsection under
"## Project workspace (in progress)" — Phase 7 retitles it), CLAUDE.md (an
implemented-notes section before "Source-of-truth pointers", plus the Layout
entries it touches), and docs/RELEASE_WINDOWS.md (manual QA rows). A new
user-facing control is a three-place capability edit (frontend/src/lib/
capabilities.ts, the control's data-capability, a tour step) or npm test
fails; a new SessionState field is declared in tests/test_session_wipe.py or
that suite fails.

Before pushing, from the repository root: ruff check ., the full pytest
suite, (cd frontend && npm test && npm run build) -- the only package.json is
frontend/, so the npm commands fail with ENOENT anywhere else. Push, open the
PR, reply to every Codex thread with the fixing commit, and resolve them.

Before stopping, mark the phase "in review" in the README's implementation
record with the commit and PR -- not "complete": the PR has not merged, and
the next session's reconcile step promotes it once it has. Never push a git
tag: the owner tags after Phase 7 merges.
```

One phase is one reviewable pull request and one session. Phases 2 → 3 are
a chain; Phase 4 is independent of both; Phase 5's measurement half is
independent and its trim half is gated; Phase 6 is optional; Phase 7 is
last and depends on every phase the owner wants in the release.

## Decisions (ratified as recommended, 2026-09-22 — binding)

| # | Decision | Ruling |
|---|---|---|
| D1 | Where a project lives | **A folder.** `<Project>.basproject` beside one `.baspec` per section. Discovered by co-location plus a matching `project_id`; no paths persisted in any file. Browser/dev sessions keep the file relay. |
| D2 | When the brief is refreshed | **On every project Save**, silently, when a co-located brief exists (an append-only merge, never an overwrite); plus an explicit *Update project brief* action. |
| D3 | Harvest is a paid call | **Opt-in, one call, preview-then-commit.** Offered, never forced. Never fires on its own. |
| D4 | Conflicting project setup between sections | **Newest export wins for the profile; edition-override disagreements are recorded as a warning AND a project fact.** |
| D5 | Client library (Phase 6) | **Defer** until Phases 1–4 have been used on a real multi-section project. |

## Program rules

- `CLAUDE.md` is binding — turn atomicity across every store,
  strip-at-commit, snapshot-before-expensive-work, the event-loop rule (an
  `async def` handler never does seconds of CPU inline), the never-rewrite
  rule for implemented notes (append; correct in an errata bullet).
- **The transcript and the document never travel** between sections, in
  any form. A model-written summary of a session is not a carry-over asset.
- **Every carried asset keeps its provenance.** A brief carries records; a
  merged fact keeps who recorded it and where; a harvested fact resolves to
  something real or is refused.
- **Nothing is deleted by a merge.** Supersede with a reason; re-mint an id
  that collides; name what was dropped at a cap.
- **Paid work is opt-in and previewed.** The harvest pass never fires on
  its own and commits nothing the user did not accept.
- **The import gate stays fail-closed.** A named page counts as content
  (`SpecSection.has_body_content`); the choice to leave a page unnamed for
  an office-master import belongs in the dialog (Phase 1 precedent).
- **Paths the shell knows never reach the frontend as inputs and are never
  persisted into a `.baspec` or `.basproject`.** The shell resolves; the
  frontend asks by name (a section number, a token); the server validates
  that a resolved path stays inside the project folder.
- Windows is the primary target: paths are `os.path` joins, atomic writes go
  through the shell's `_atomic_write_target` / the catalog's `_atomic_write`
  idiom, and nothing relies on symlinks.

## Dependency edges

```
1 (done) ──► 2 ──► 3 ──► 6 (optional)
                  │
4 ────────────────┼──► 7 (closeout: the release)
5A (measure) ─► 5B (trim, gated) ─┘
```

## Standard verification commands

```
.venv/bin/python -m ruff check .                    # Windows: .venv\Scripts\python
.venv/bin/python -m pytest -q
cd frontend && npm test && npm run build && cd ..
```

Phase 7 adds `python packaging/windows/check_release_version.py --tag vX.Y.Z`
and `python packaging/windows/render_release_notes.py --version X.Y.Z
--notes-out <scratch>/notes.txt --body-out <scratch>/body.md`.
