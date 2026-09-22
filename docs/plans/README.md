# Implementation plans

Owner: Abraham.

## Active

**[Project workspace](project-workspace/README.md)** — the live program
(decisions ratified 2026-09-22; Phase 1 shipped in v1.20.0, PR #174; Phase 2
is in review, PR #176; one release for Phases 2–6 at the end). Carry a project's work
(research, location, system and project facts, references, editions)
across its spec sections as a first-class project: a project folder with a
Project panel, an append-only write-back merge, a harvest pass, a measured
relevance trim, an optional client library, and the release closeout. The
folder's README carries the handoff prompt, the implementation record, the
release policy, the binding decisions and the program rules; one spec file
per phase sits beside it. The assessment and the original one-file plan
are [`PROJECT_WORKSPACE_2026-09-22.md`](PROJECT_WORKSPACE_2026-09-22.md).
A fresh session starts from the folder README's "How to hand this off"
section, not here.

**[Deep-dive remediation](deep-dive-remediation/README.md)** — the other live
program. Six phases, 24 chunks; **Phase 1 is complete** (direct server-tool
callers; continuation containers in research, QC and chat; server-tool
pairing plus legacy history repair) and **Phase 2 is nearly done** — 2.2 (QC
settling semantics, pulled forward), 2.1 (real-shaped server-tool activity
events) and 2.3 (research follower reconnect) have landed; only 2.4 remains,
plus Phases 3–6. Its README carries the handoff prompt, the
frozen decisions, the finding-coverage matrix, the dependency edges and the
phase gates; start there, not here.

**[Redline on your original](REDLINE_ON_ORIGINAL_2026-09-22.md)**: proposed
2026-09-22 and awaiting the owner's decisions; nothing is built yet. It adds a
tracked-changes copy of the Word file you imported: every non-body part stays
byte-identical, Reject All gives back the upload, and Accept All gives
*Export Word (keeps your formatting)*. The export checks both halves itself and
refuses when either check fails. Phase 0 fixes four bugs found in today's
formatted export along the way: relettered provisions lose their tab and bold,
a section break is dropped or duplicated, and a "(Not used.)" line goes stale.

## Retired

The batch plans for v0.7.0–v1.0.0 (Batches 2–5) and the batch kickoff prompt
`AGENT_PROMPT.md` were deleted on 2026-07-29. All four batches shipped, and
their as-built design record is restated in `CLAUDE.md` — the file those plans
themselves named as the source of truth for conventions, invariants and frozen
decisions. `AGENT_PROMPT.md` selected the next batch from `VERSION` (topping
out at 0.9.0 → Batch 5) and pointed at a `ROADMAP.md` deleted long before it,
so it could not route work in this codebase any more.

Two things in those plans were **not** already covered elsewhere and were
relocated before the files were removed, not dropped:

- Batch 4's "audit-grade reporting amendment", the one plan section still
  claiming to be a live maintenance contract → `CLAUDE.md` → *Audit-grade
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

Deliberately not kept as an in-tree `archive/` folder: repo-wide searches by
coding agents would still surface it, which is the exact clutter the removal
was for.
