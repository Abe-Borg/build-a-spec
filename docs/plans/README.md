# Implementation plans

Owner: Abraham.

Nothing is in flight. Every program that was planned here has shipped, and
its as-built record lives in `docs/as-built.md`. `CLAUDE.md` is the file
each plan named as the source of truth for conventions, invariants and
frozen decisions. A future
plan goes in this folder beside this index, and the index is where a session
looks first.

## Retired

### 2026-09-30 — the finished programs

Deleted on 2026-09-30, once every program below had closed and every
user-visible item it still owed had been lifted into the unreleased 1.21.0
entry of `backend/release_notes.py`. Each row's last column names the
`docs/as-built.md` sections that hold its as-built record.

| Program | Files removed | Closed | As-built record |
|---|---|---|---|
| Deep-dive remediation (v1.8.0) | `deep-dive-remediation/` (8 files) | 2026-07-29, Chunk 6.5 | the "Deep-dive remediation Chunk N.N" sections |
| Chat history compaction | `CHAT_HISTORY_COMPACTION_2026-09-22.md` | 2026-09-23; Phases 1–3 shipped, Phases 4 and 5 dropped (D4, D6) | "Stale outlines stay out of saved history", "Fetched page text stays out of saved history", "A long conversation is condensed, never deleted", "Routine condensing is on by default", "The compaction plan's last two pieces are dropped" |
| Project workspace | `PROJECT_WORKSPACE_2026-09-22.md`, `project-workspace/` (7 files) | 2026-09-23, Phase 7 (PR #188); Phase 5 Part B and Phase 6 were never started (below) | "Next section in one click", "The project has a home", "The brief is a living file", "Nothing settled is left in the transcript", "What each turn carries is measured", "The project workspace program, as shipped" |
| Redline on your original | `REDLINE_ON_ORIGINAL_2026-09-22.md` | 2026-09-23, Phase 3 and every follow-up | the "Redline on your original" sections; the contract is `docs/DOCX_FIDELITY.md` |
| Research and Final QC cost, Tier 1 | `RESEARCH_QC_COST_TIER1_2026-09-23.md`, `RESEARCH_QC_COST_TIER1_PROGRESS.md` | 2026-09-24, Chunk 6 | "Research and Final QC cost, Tier 1, as shipped" and one section per chunk |
| Tier 1 finish | `tier1-finish/` (3 files) | 2026-09-25, FIN-1 | "The two shelved savings are on, and watch themselves" |
| The 5.5 prompting upgrade | `prompt55/` (2 files) | 2026-09-30, P55-8 | "The 5.5 prompting upgrade, as shipped" and one section per session |
| Revision-2 review, execution record | `../review-results/2026-09-09/EXECUTION_RECORD.md` | steps 1, 3 and 5 shipped; steps 2 and 4 were owner-run measurements never taken, and its two deferred ideas were since built by Tier 1 Chunks 2 and 3 | "The revision-2 review plan, as executed", "Where the revision-2 plan went" |

Removed with them: `tests/test_tier1_finish_tracker.py` and
`tests/test_prompt55_tracker.py`, whose only job was to police the shape of
the two trackers, and `tools/lint_block_profile.py`, the execution record's
step-4 instrument, which had no tests, was never run, and carried a copy of
the LINT REPORT renderer that had to be kept in step with `conversation.py`.

Three things were not already recorded elsewhere and were relocated, not
dropped:

- the release-note drafts the plans still owed (compaction Phases 2 and 3,
  the Tier 1 plan's §7, and the redline program's seven) → the 1.21.0 entry
  of `backend/release_notes.py`;
- the deep-dive program's nine-item owner-owed live and manual QA gate
  (Chunk 6.5) → `docs/RELEASE_WINDOWS.md` → **Pre-release manual QA** →
  "Deep-dive remediation live gate";
- the execution record's step-2 decision rule → it was already what
  `tools/qc_export_cost_profile.py` applies; its docstring now says so.

Two specs were never built, and nothing is waiting on them: project
workspace Phase 5 Part B (relevance-first research rendering, gated on a
measurement of a real second section) and Phase 6 (a client library, gated
on a second project for one client, decision D5). If either is picked up,
its spec is in git history at the last commit that held these files:

```
git show 7d4c0db:docs/plans/project-workspace/05_RELEVANCE_TRIM.md
git show 7d4c0db:docs/plans/project-workspace/06_CLIENT_LIBRARY.md
```

Every other file above is there too, under its own path.

Deliberately not kept as an in-tree `archive/` folder: repo-wide searches by
coding agents would still surface it, which is the exact clutter the removal
was for. The same reasoning retired the batch plans below.

### 2026-07-29 — the batch plans

The batch plans for v0.7.0–v1.0.0 (Batches 2–5) and the batch kickoff prompt
`AGENT_PROMPT.md` were deleted on 2026-07-29. All four batches shipped, and
their as-built design record is restated in `docs/as-built.md`. `CLAUDE.md`
is the file those plans themselves named as the source of truth for
conventions, invariants and frozen decisions. `AGENT_PROMPT.md` selected the next batch from `VERSION` (topping
out at 0.9.0 → Batch 5) and pointed at a `ROADMAP.md` deleted long before it,
so it could not route work in this codebase any more.

Two things in those plans were **not** already covered elsewhere and were
relocated before the files were removed, not dropped:

- Batch 4's "audit-grade reporting amendment", the one plan section still
  claiming to be a live maintenance contract → `docs/as-built.md` → *Audit-grade
  Final QC report extension* → **The reporting contract**.
- The manual QA that Batches 2, 3, 4 and 5 each recorded as **still owed**
  (real-Word redline round-trips, packaged-app QC report downloads, partial-QC
  coverage behavior, key flows, streaming feel) → `docs/RELEASE_WINDOWS.md` →
  **Pre-release manual QA**. Nothing recorded those checks as completed, so
  they are carried forward as outstanding.

They remain in git history at `c991c4c` if you need the original design
reasoning:

```
git show c991c4c:docs/plans/BATCH_4_FINAL_QC_ON_FABLE.md
```
