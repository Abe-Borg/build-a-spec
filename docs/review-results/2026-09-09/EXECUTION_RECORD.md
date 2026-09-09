# Revision-2 review plan — execution record

Plan: `docs/REVIEW_IMPLEMENTATION_PLAN_2026-09-08.md` (revision 2).
Baseline reviewed by the plan: `ce0249a7144677074fddea09b334d793907ee536`.
Executed: 2026-09-08 → 2026-09-09, shipped in **v1.18.0**.

This file records what each step delivered and what it deliberately did not.
The original review brief is not edited — this is kept separate from it, per
the plan's step 5.

## Status by step

| Step | Work | Outcome |
|---|---|---|
| 1 | QC drawer sums `qc` + `qc_batched` | Shipped — PR #162 |
| 2 | Read real QC exports; answer the batching question | **Not run** — needs a file only the owner has |
| 3 | Incremental result consumption, bounded post-cancel settlement, minimal disclosure | Shipped — PR #163 |
| 4 | Measure real lint repetition, compact only if it earns it | **Not run** — needs a file only the owner has; conditional by design |
| 5 | Documentation, release metadata, integrated gates | This change |

## What shipped

**Step 1 (PR #162).** `qcReport.qcSessionCost` sums both QC meter buckets.
The ledger splits Final QC spend by the rate it was billed at — `qc` at list
price, `qc_batched` at `BATCH_COST_MULTIPLIER` — because one bucket can only
carry one rate. The drawer's "This session's QC" line and the launch
confirmation read only the first, so a batched review (the default since
v1.12.0, and roughly nine calls in ten) reported a fraction of its cost, or
`$0.00` when every call was batched.

**Step 3 (PR #163).** Four mechanisms, one theme — the provider bills a batch
request whether or not the app reads its result:

- the results stream is consumed one row at a time, so a mid-read failure
  costs only the remainder rather than discarding every verdict already read;
- Stop and the wall-clock ceiling open a bounded settlement window that
  cancels, polls to `ended`, and reads back what completed, in a recovery mode
  that starts no new work;
- every call in that window is bounded by the time the window has left, not a
  fixed ceiling, with the connect timeout bounded down to the same budget;
- what cannot be collected is disclosed — two counts, one status, one derived
  meter flag — and a stopped run is recorded `partial`, never complete.

Two review findings from Codex on PR #163 were real and fixed before merge:
the per-call bound ignoring the configured window, and a streamed run being
filed under the capture state reserved for pre-disclosure reports.

**Step 5 (this change).** The release entry, the version bump, the README
additions, one erratum, and this record. `requirements.txt` needed no change
and none was made — nothing in steps 1–3 added a dependency.

## Release metadata

`settings.VERSION` was `1.17.0`, and **v1.17.0 was already tagged and
published** (2026-09-08 19:11 UTC, confirmed through the GitHub Releases API;
`git tag -l` is empty in a fresh clone, which is how a past overwrite of a
released entry survived review). Its `ReleaseNote` is therefore frozen. Steps
1 and 3 are the only user-visible work that landed after that tag, so this
change appends a **new 1.18.0 entry** and bumps the version in five places:
`backend/settings.py`, `frontend/package.json`, both root `version` fields in
`frontend/package-lock.json`, and the README headline.

## Erratum recorded

The v1.8.0 note in `CLAUDE.md` ("Final QC cost + speed") says the four
web-toolless lenses share a cached prefix and "every verifier seat shares
another". The second half is wrong: `_verifier_tools` appends the web search
and fetch tools when `lens.web`, which is `code_compliance` alone, so seats
verifying a compliance finding carry a different `tools` array — and tools
render ahead of system and messages, which is the same reason that section
already gives for `code_compliance` not joining the lens lineage. There are
two verifier lineages. Nothing is broken by it; each caches correctly within
itself. The correction is recorded in a new section rather than by editing
the frozen v1.8.0 one.

## Review findings on PR #164

Two P2 findings from Codex on `ee3b779`, both real, both in copy this change
itself wrote:

- **A dropped result read does not reduce the bill.** The release note said an
  interrupted read "now costs only what had not been read yet". The provider
  ran and billed those requests when the batch processed — which is the whole
  premise of the settlement window, and stated as such two files away. What
  incremental reading saves is the verdicts and the recorded usage already
  folded, not the charge. Rewritten to say that.
- **Verification is not one submission.** `messages.batches.create` sits inside
  `_run_batch_calls`'s round loop, so a phase submits one batch per round and
  a round is added whenever a seat pauses or retries. The README line and the
  release summary both claimed one batch; both corrected, along with the
  `CLAUDE.md` Layout index entry that seeded the wording. The frozen v1.12.0
  section is left alone and the correction is recorded as errata.

Neither changes behavior; both were claims a reader could have checked and
found false, which is the class of defect step 5 exists to remove.

## What is still open, and why

**Step 2 — answer the batching question from a real export.** The plan moved
this ahead of any further cost work deliberately: `/api/qc/export.json`
carries per-lens and per-seat token counts, request counts, the pricing basis
and the batch multiplier, so a saved export settles what the review
architecture actually costs. Nothing in the repository can stand in for one.
Most informative would be an export from a run between v1.8.0 and v1.11.0,
which predates batching and would show what the streaming phase cost per seat.

**Step 4 — measure the lint block, then decide.** Conditional by design. The
plan's threshold is a LINT REPORT block over roughly 5k characters on a
representative real document, of which exact `(rule, severity, message)`
grouping would remove more than half. The original brief's 96k-character
figure came from a repeated-text fixture inflated by `duplicate_provision`
and decides nothing. No compaction was written, because writing it before the
measurement would be building to a number nobody has.

Both need documents that exist only on the owner's machine. The cheapest way
to close them is a read-only script under `tools/` that the owner runs locally
and pastes the output of — the plan sanctions exactly that for step 2. It was
offered and not yet taken up.

## Deferred, unchanged from the plan

Section 10's cache investigations (staggered phase-one launch, batch cache
reuse, client continuation caching, slow-changing assets before history) remain
conditional and were not started. `QC_VERIFIER_EFFORT` was documented, not
re-baselined. No background reconciliation service exists or was proposed: the
settlement window is bounded and synchronous, and its gap is disclosed rather
than queued for later repair.

## What pins the version bump

Reverting each half in place, to show which check catches it:

| Mutation | Red |
|---|---|
| Version bumped with no 1.18.0 release entry | `test_release_notes.py` ×2, `test_packaging.py` ×1 |
| README headline left at v1.17.0 | `test_updates.py::test_version_consistency_gate` |
| Either `package-lock.json` root `version` left behind | **no test** — `check_release_version.py` reads neither field, so `npm ci` is the only thing that catches it (it passes here) |

That last row is a known gap, already recorded in `CLAUDE.md`; it is stated
rather than papered over.

## Gates at the time of writing

```
pytest -q          1950 passed, 9 skipped
ruff check .       All checks passed
npm test           298 passed
npm run build      clean
check_release_version.py   version consistency ok: 1.18.0
```
