# Implementation plans

Owner: Abraham.

## Active

**[Project workspace](project-workspace/README.md)** — the live program
(decisions ratified 2026-09-22; Phase 1 shipped in v1.20.0, PR #174; where
the later phases stand is kept ONLY in the folder README's implementation
record — this line went stale once already; one release for Phases 2–6 at
the end). Carry a project's work
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

**[Chat history compaction](CHAT_HISTORY_COMPACTION_2026-09-22.md)** —
opened 2026-09-22. Keep the chat history the model re-reads every turn
bounded without losing what only the conversation holds: stop saving data
stored elsewhere (Phase 1, stale edit outlines — complete, PR #182; Phase 2,
fetched page text — merged in PR #183 and switched off; its live canary's
first run on 2026-09-23 was refused, and the trim was reworked in PR
#192, so it stays off until the canary passes on the new shape), then
condense the conversation rarely between turns with the full transcript kept
and recallable (Phase 3 — complete, PR #189; decisions D1–D4 made
2026-09-22; routine condensing stays off by default until a paid recall
check; PR #192 also repairs citations that a condensed view had left
pointing at the wrong page). The plan's
implementation record is the only authority on where each phase stands; this
line is a summary of it. The file is the plan, the owner decisions (D1–D4)
and the implementation record; its
"promote before prune" phase is handed to the project workspace's harvest.
Phases 1–2 ship in the project workspace's release, 1.21.0 (same release
policy).

**[Deep-dive remediation](deep-dive-remediation/README.md)** — **complete**.
Six phases, 24 chunks (1.1–6.5), all landed: direct server-tool callers,
continuation containers, server-tool pairing and legacy history repair, live
stream resilience, named research coverage, per-TTL cache pricing, the v4
Final QC panel outcomes, consolidation and sign-off consistency, and the
locking and snapshot rules. The one item still outstanding is the owner-run
live and manual QA in Chunk 6.5, which spends real money. Its README carries
the handoff prompt, the frozen decisions, the finding-coverage matrix, the
dependency edges and the phase gates; start there, not here.

**[Redline on your original](REDLINE_ON_ORIGINAL_2026-09-22.md)**: opened
2026-09-22; all seven decisions ratified the same day (the seventh settled
Phase 0's open question: a provision added after a section's last paragraph
keeps landing at the top of the next section). **Phase 0 (PR #184) ships in
1.21.0** (the project-workspace closeout). **Phase 1 is built:** its backend
(PR #187) is in 1.21.0, and its UI (PR #190 — the menu item, *Open redline in
Word*, the capability, the copy) is on `master`. No release entry announces
the redline yet; the owner picks the release, and the real-Word QA rows are
still to run. A follow-up (PR #193) fixed the two losses Phase 1 recorded in
the formatted export: a provision holding a hyperlink is spliced instead of
rebuilt from its first run (every fallback in the corpus sweep had been a
link — none are now), and a new sub-provision in a Word-numbered master takes
its own numbering level. **Phase 2 lands as two PRs** (a deviation: the plan
sized it as one). PR A makes real Word the redline's judge — an optional
Windows suite where a hidden Word accepts and rejects every change and the
result must match Word's own save of the formatted export or the upload —
plus a corpus recipe that records Word's own tracked moves as evidence;
its as-built note is "Phase 2 (PR A) — as built". PR B, native Word "Moved"
marks, starts after PR A merges and the owner has run the judge on Windows.
The program adds a
tracked-changes copy of the Word file you imported: every non-body part stays
byte-identical, Reject All gives back the upload, and Accept All gives
*Export Word (keeps your formatting)*. The export checks both halves itself
and refuses when either check fails. Phase 0 fixed the four bugs found in
today's formatted export along the way — relettered provisions lost their tab
and bold, a section break was dropped or duplicated, and a "(Not used.)" line
went stale — plus five more of the same export's defects it turned up.

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
