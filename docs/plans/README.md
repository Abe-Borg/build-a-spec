# Implementation plans

Owner: Abraham.

## Active

**[Deep-dive remediation](deep-dive-remediation/README.md)** — the only live
program. Six phases, 24 chunks; **Chunk 1.1 (direct server-tool callers) is
complete**, the rest are planned. Its README carries the handoff prompt, the
frozen decisions, the finding-coverage matrix, the dependency edges and the
phase gates; start there, not here.

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
