# Revision-2 review plan — execution record

Plan: `docs/REVIEW_IMPLEMENTATION_PLAN_2026-09-08.md` (revision 2), **deleted
2026-09-09 at the owner's instruction once every step that could be executed
without his files had shipped.** This record supersedes it and is the surviving
statement of the work — the three `CLAUDE.md` implemented-notes sections that
cite the plan by name (steps 1, 3 and 5) are frozen history and still name it as
the provenance it was; this file is where that reference now leads.
Baseline reviewed by the plan: `ce0249a7144677074fddea09b334d793907ee536`.
Executed: 2026-09-08 → 2026-09-09, shipped in **v1.18.0**.

This file records what each step delivered and what it deliberately did not.
Because the plan is gone, the two steps that were never run carry their full
acceptance criteria below rather than a pointer — see *What is still open*.
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

Both steps need documents that exist only on the owner's machine. Neither was
guessed at. Because the plan file is deleted, each carries its full acceptance
criteria here — enough to run it from this record alone.

### Step 2 — answer the batching question from a real export

The plan put this ahead of any further cost work deliberately: a saved
`/api/qc/export.json` settles what the review architecture actually costs,
and nothing in the repository can stand in for one. Most informative would be
an export from a run between v1.8.0 and v1.11.0, which predates batching and
would show what a streaming verifier seat cost per seat.

**No application code is required.** A read-only script under `tools/` is
acceptable if it makes the reading repeatable. It must not import the client
factory, initialize a session, make model requests, or emit document text,
prompts, reference-document contents or credentials.

*Inputs.* Owner-designated Final QC JSON exports and `.baspec` project files.
Every persisted `QCVerdict` and `QCLensStatus` carries `usage_totals` with
`input_tokens`, `output_tokens`, `cache_read_input_tokens`,
`cache_creation_input_tokens` and — since Chunk 4.1 —
`cache_creation_1h_input_tokens`; each verdict carries `cost_multiplier`
(0.5 for a batched seat, 1.0 otherwise). The export's `report` and
`current_state` are separate; count a run once even if it was exported twice.

*What to extract.* Per run, split lens records from verifier records, and
verifier records by `cost_multiplier` and by whether the seat's lens carried
web tools:

- Token totals by class: uncached input, five-minute writes
  (`cache_creation_input_tokens − cache_creation_1h_input_tokens`), one-hour
  writes, cache reads, output.
- Cache-read share `R / (I + W + R)`, labelled as a token-weighted share of the
  input side and **not** a request hit rate.
- Output as a share of the run's estimated cost, using the persisted
  `cost_basis`.
- Phase 1: how many of the four web-toolless lenses wrote versus read the
  shared prefix. This decides the staggered-launch idea below on its own.
- Capture completeness is **unknown** for every record written before v1.18.0;
  do not infer it.

*Decision rule.*

- Batched seats show cache reads on most seats → the batched default stands and
  the batch-cache-reuse investigation is closed.
- Batched seats show near-zero reads → the default still stands on the output
  term, the measured input-side gap is recorded, and batch cache reuse becomes
  a concrete, budgeted proposal for the owner rather than a deferred idea.
- Exports from v1.8.0 through v1.11.0 exist → report the measured streaming
  phase-2 cache behavior beside the batched figure and retire the $5.61
  hypothetical.

Write the result to `docs/review-results/<date>/measurements.md`, with artifact
provenance and privacy-preserving summaries only.

**Note the erratum recorded below:** phase 2 has *two* verifier cache lineages,
not one, so seats verifying a `code_compliance` finding must be counted
separately or the cache-read share will read low for a reason that is
structural rather than a defect.

### Step 4 — measure the lint block, then decide

Conditional by design, and the measurement comes first. On the owner's real
project files, offline, with the injected date frozen: run `lint_document` and
the chat context builder, and record per document the occurrence count by rule,
the characters of the LINT REPORT block, and the share of that block that exact
`(rule, severity, message)` grouping would remove. Label any chars-to-tokens
conversion as an estimate and state its rule.

*Working threshold, a judgment call and stated as such:* proceed when the block
on a representative real document is more than about 5k characters **and**
grouping would remove more than half of it; otherwise defer and record the
measurement. The original brief's 96k-character figure came from a
repeated-text fixture inflated by `duplicate_provision` and decides nothing.
The dollar case is small either way — the block sits in the uncached tail every
turn, but forty identical lines are roughly 2,500 tokens at Sonnet 5 rates — so
the justification, if any, is keeping the report readable for the model in the
twenty-plus occurrence regime. No compaction was written, because writing it
before the measurement would be building to a number nobody has.

*Contract if it proceeds.* Change only the lint text inserted into
`_turn_context_text` — not `lint_document`, raw ids, frontend rows, the lint
SSE payload, readiness, or exports. Start with `stale_edition` and
`unrecorded_edition`. A small pure helper beside the context builder; never in
the lint engine. Grouping key is exact `(rule, severity, message)` equality:
no normalization, first-seen order, every element id and reference preserved,
per-element occurrence counts preserved, no cap on the location list,
unrecognized or malformed issues rendered exactly as today, input records never
mutated, the advisory framing and the delimiter neutralization preserved. Use
grouping for a group only when its rendering is strictly shorter than the
legacy lines; otherwise keep the legacy lines, and singleton rendering stays
byte-identical. Do not change the QC input manifest. Rollback is the renderer
call site; raw records are untouched.

Illustrative rendering, using the existing formats rather than a new numbering
scheme:

```text
- [unrecorded_edition] <exact existing message>
  Affected citations (3): 1.1.A (element pt1.a1.p1); 1.1.B (element pt1.a1.p2); 1.2.A (element pt1.a2.p1).
```

*Tests if it proceeds.* Many identical edition messages across elements; the
same standard at two cited years; the same standard with differing severity or
message; two physical citations in one paragraph; mixed edition and
non-edition issues; singleton-only; empty; missing optional fields; determinism
and no mutation; short messages where grouping is longer; hostile delimiter
text; a continuation turn reusing the frozen context. At least one fake-client
integration test must assert on the final emitted request text, on the
unchanged raw lint payload, on readiness reporting the same population, and on
committed history not acquiring the block. Existing pins to retain, by name:
`test_stale_edition_detected_in_three_citation_shapes`,
`test_unrecorded_edition_fires_on_the_engine_citation_shapes`,
`test_recording_the_edition_silences_the_rule`,
`test_overlapping_designation_forms_are_not_double_reported`,
`test_three_identical_siblings_report_two_findings_not_three`
(`test_linting.py`); `test_chat_turn_emits_lint_event_and_payloads_carry_standards`,
`test_context_block_never_fossilizes_into_history`,
`test_a_turns_cached_prefix_is_a_byte_prefix_of_the_next_request`,
`test_no_breakpoint_survives_into_history_or_a_saved_project`
(`test_app.py`); `test_document_text_cannot_forge_the_context_boundary`
(`test_reference_docs.py`).

### The cheapest way to close either

A read-only script under `tools/` that the owner runs locally and pastes the
output of. The plan sanctioned exactly that for step 2. It was offered and not
taken up.

## Deferred, unchanged from the plan

None of this was required to finish steps 1–5, and none of it is a production
default. Recorded here because the plan that held it is gone.

**Staggered phase-one launch.** Decided by the phase-1 telemetry in step 2: if
the four web-toolless lenses routinely *all* write the shared prefix, an
experiment may start one and release the rest on an observed response-start
event. It would have to cover failure-to-start, cancellation, timeout and the
web-toolled lens, and measure wall-clock as well as cost. Retain the current
scheduler unless the measured benefit justifies the coordination.

**Batch cache reuse.** Decided by the batched-seat cache-read share in step 2.
If it is near zero, a concrete proposal states request count, synthetic
payload, output allowance, timeout, maximum spend and cleanup **before** the
owner authorizes any paid comparison. Compare total cost for equivalent work
including output, retries and every cache-write TTL.
`tools/qc_verifier_canary.py` is a schema-acceptance check, not cache
evidence — do not expand its paid scope.

**Client continuation caching, and slow-changing assets before history.** Both
remain deferred and both need real usage evidence. Neither may attach cache
metadata to thinking blocks, memoize QC context by run id alone, or truncate
the document or location lists under the label of a cache change.

**Not re-baselined.** `QC_VERIFIER_EFFORT` was documented, not changed. No
background reconciliation service exists or was proposed: the settlement window
is bounded and synchronous, and its gap is disclosed rather than queued for
later repair.

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
