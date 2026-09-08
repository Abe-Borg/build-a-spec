# Build-a-Spec: implementation plan following independent review

Date: 2026-09-08 (revision 2, same day)  
Reviewed baseline: `ce0249a7144677074fddea09b334d793907ee536` on `master`  
Deliverable status: **implementation specification; no application changes or test executions performed while preparing it.** Revision 2 was written after a second, independent read of the same tree confirmed every source finding in section 2.1 and disagreed with parts of the design built on them. The disagreements and the author's responses are recorded in section 2.4; the packages below are the reconciled position.

## 0. What changed in revision 2

Revision 1 proposed six packages (A–F) plus conditional cache work (G), a versioned per-seat financial-capture contract, a three-agent implementation team, and a lint-compaction package ranked as a recommended improvement. A second review agreed with every finding and disputed the design. The author accepted the substance of that review. Revision 2 therefore:

1. **Recovers uncollected batch usage instead of only disclosing it.** The runner already acknowledges Stop synchronously and lets the worker unwind in a documented settling state, so a bounded post-cancel result read belongs in that window. Disclosure becomes the fallback when the bound expires.
2. **Shrinks the disclosure to one per-seat count, one run-level status, and one derived session-meter flag** that mirrors an existing precedent (`includes_estimated_output`).
3. **Moves reading real QC exports to the front**, before any tooling or lint work, and corrects revision 1's batch-economics framing (section 2.4).
4. **Gates lint compaction on a measurement from real documents.** It is no longer a recommended improvement by default.
5. **Fixes the copyable commands** to the repository's `.venv` convention.
6. **Replaces three implementation agents plus an integrator with one implementer and one reviewer**, and three pull requests.

Everything revision 1 said about compatibility and ownership safeguards stands: no schema or protocol bump, nothing new in `input_manifest`, no background reconciliation service, run-token isolation, session-generation guards, lock ordering, and the retained-success versus latest-attempt distinction.

## 1. Purpose and scope

Implement the corrections supported by two independent reviews, preserve the application's existing authoring and review behavior, and establish real measurements before changing expensive model-request paths.

### 1.1 Outcomes, in order

1. The QC drawer's session-cost line includes both streaming and batched QC charges.
2. The batching cost question is answered from existing QC exports before any further cost work is planned.
3. Provider usage already obtained from batch results is preserved when a later read fails, and results that completed before a Stop or a deadline are collected within a bounded settlement window. When that fails, the gap is disclosed, never silently written off.
4. Lint compaction proceeds only if a measurement on real documents shows it earns its place.
5. Documentation describes the code that ships; the suite, the lint gate, the frontend tests and the build are green.

### 1.2 Decisions deliberately retained

- Full-document chat context, current model routing, effort levels, output allowances, verifier counts, adjudication rules.
- The batched-verification default and the automatic-debrief default.
- Per-occurrence lint records, issue navigation, and readiness semantics.
- Current source-preserving DOCX and legacy source-patching behavior.
- Existing input framing, server-tool pairing, transaction rollback, and session/run ownership guards.
- The current frontend testing approach (`node --test` over the sources; no new framework).
- Existing dependencies. No new dependency is expected.

Do not split large modules merely because they are large. Extract a helper only when it isolates the changed behavior or supports meaningful verification.

### 1.3 Work order and pull-request boundaries

| Step | Work | PR | Dependency |
|---|---|---|---|
| 1 | QC drawer sums `qc` + `qc_batched` | PR 1 (one small change) | None |
| 2 | Read existing QC exports; answer the batching question; record it | No code required (a read-only script under `tools/` is optional) | None |
| 3 | Incremental result consumption, bounded post-cancel settlement, minimal disclosure | PR 2 | Step 2 informs nothing here; it can run in parallel |
| 4 | Measure real lint repetition; implement compaction only if it earns it | PR 4, conditional | Measurement first |
| 5 | Documentation, release metadata, integrated gates | PR 3, or folded into PR 2 | 1–3 |

Cache experiments (section 10) remain deferred and conditional.

## 2. Evidence the implementer must understand

Line numbers refer to the reviewed commit. Search by symbol and test name if the checkout has moved.

### 2.1 Confirmed source findings (verified twice, independently)

| Finding | Source anchor | Practical implication |
|---|---|---|
| Stop and timeout return before reading the active batch's results | `backend/qc/engine.py`, `_run_batch_calls`, ≈5255 (Stop) and ≈5278 (deadline): `_cancel_batch`, `settle_all(unsettled(), …)`, `return results()` | Seats that completed inside the provider before the cancel landed are billed and never read. **Prior-round usage does survive**: `_BatchSeatState.settle()` builds the `_CallResult` from `billed` plus `all_responses`. Only the current round's completed-but-unread results are lost. |
| Results are fully materialized before any item is processed | `engine.py` ≈5286: `items = list(client.messages.batches.results(batch_id))` | One iterator failure discards every row already yielded — their **verdicts** as well as their usage. Every seat then settles failed and the run reads partial. |
| The runner meters returned records once | `backend/qc/runner.py` ≈250–263: `usage_by_meter_category()` → `usage_sink(category, bucket)` | Nothing reconciles missing batch responses later. |
| The QC drawer reads only the `qc` category | `frontend/src/components/QCDrawer.tsx` ≈970: `usage?.estimated_cost_usd.by_category.qc` | `qc_batched` exists in the ledger and in the Settings label map; the drawer's "This session's QC" line omits the phase that is roughly nine calls in ten. |
| Edition lint is per physical occurrence with stable ids | `backend/spec_doc/linting.py`, `lint_document` (`id = f"{rule}:{element_id}:{n}"`) | Replacing raw issues with grouped issues would change a deliberate contract. |
| Chat renders one line per lint occurrence | `backend/llm/conversation.py`, `_turn_context_text` ≈2020–2027 | The message text is templated on standard name, cited year, expected edition and basis, so identical citations across elements produce byte-identical messages. Grouping by exact message would collapse exactly the repeats and nothing else. |
| QC context carries freshness and dispositions | `backend/qc/context.py` (STALE/CURRENT, applied/dismissed rollup) | QC context is not stable solely because its run id is unchanged. |
| Compliance verifiers carry web tools; other verifiers do not | `engine.py`, `_verifier_tools` ≈4656 (`if lens.web:`) | Two verifier cache lineages, not one. This corrects the v1.8.0 note in `CLAUDE.md` that "every verifier seat shares another" prefix. |

Also verified: `test_docs_consistency.py` scans a fixed list of documents for an undotted venv path, so revision 1's commands did not fail the suite; they were still wrong (section 2.4).

### 2.2 Corrections to the original review brief

Unchanged from revision 1; retained because they still bind the measurement work in step 2 and step 4.

1. **Character counts are inconsistent.** The reported outline is 67,703 characters, lint 96,024, full context 120,911. The first two sum to 163,727, so they cannot describe the components of one assembled string. Determine what was measured; do not manufacture a fixture to force agreement.
2. **The 24k lint-token estimate cannot be attributed solely to edition findings.** The cited population contains 120 synthetic `duplicate_provision` findings from a repeated-text fixture. Separate rules and measure the actual model-facing text on real documents.
3. **The phase-one example confuses added cost with missed savings.** Four hypothetical 60k-token five-minute writes cost $1.50; uncached input $1.20; ideal one-write/three-read $0.465. The write penalty is $0.30; the improvement available relative to the ideal schedule is $1.035.
4. **The batch no-hit example omits the one-hour write premium.** Thirty-five 60k-token one-hour writes at $10/M, batch-discounted, cost $10.50. The brief's $5.25 priced uncached input, not cache creation. (Section 2.4 corrects the example's input size and adds the output term.)
5. **Ideal streaming is not the current streaming baseline.** The verifier pool launches up to eight calls concurrently and requests are partitioned by tools. In a single-prefix illustration, eight writes plus twenty-seven reads cost $5.61 against $1.62 ideal. Neither is an observed run.
6. **A shared document string is not a shared complete cache prefix.** Tools and request options matter (finding 8 above).
7. **Many lint occurrences are not many acknowledgement tasks.** `backend/app.py` uses one `lint_clean` boolean; one `set_standard_edition` operation resolves every matching unrecorded citation.
8. **Automatic debrief is conditional and deduplicated.** `research_failed` and `qc_failed` never debrief; the queue dedupes by fired token and holds during turns.
9. **Untouched DOCX preservation has a tracked-changes exception.** `backend/spec_doc/source_render.py`: a revision-bearing paragraph is rewritten to the Accept-All view, never cloned.
10. **Baseline descriptions need qualification.** The frontend `package.json` lists 27 test files at this commit. Establish skip reasons and build results rather than treating a pass count as a certificate.

### 2.3 Provider and SDK references

Recheck before implementing provider-facing changes; record the verification date.

- **Batch cancellation.** `batches.cancel` moves the batch to `canceling`, then `ended`; requests already processing may complete, and their results are retrievable once the batch has ended. Results arrive in any order; key by `custom_id`, never by position. Result types: `succeeded` / `errored` / `canceled` / `expired`.
- **Per-request SDK overrides.** The Python SDK supports `client.with_options(timeout=…, max_retries=…)` for one call without mutating the client. The application client (`backend/llm/client.py`) is built with `settings.SDK_MAX_RETRIES` (2) and an `anthropic.Timeout` whose read timeout is `settings.API_TIMEOUT_SECONDS` (600). **A single results read under those defaults can therefore run for three attempts of ten minutes each.** Any bounded window must override both for the calls inside it (section 7.3).
- **Pricing.** Opus 5 base input $5/M, five-minute cache write $6.25/M, one-hour write $10/M, cache read $0.50/M, output $25/M; batch multiplier 0.5 applies to every token class including cache writes and output. `settings.PRICING` and `settings.BATCH_COST_MULTIPLIER` are the in-repo authority; do not reprice historical records.
- **Prompt caching.** Prefix identity, cache availability after response commencement, expiration, breakpoint placement. Thinking blocks cannot receive arbitrary explicit markers (the standing "no messages-tail breakpoint" decision for QC).
- **Stop reasons.** Preserve paused assistant content and the server-tool continuation contract.

Environment note from revision 1: the SDK inspected during the original review was 0.117.0, while `requirements.txt` requires `anthropic>=1.0,<2`. Resolve the reviewer's environment in step 0; do not downgrade the requirement.

### 2.4 Corrections to revision 1 of this plan

Recorded here rather than silently rewritten, so a reader of the earlier revision can see what moved and why.

1. **Stop acknowledgement and worker termination are different requirements, and revision 1 conflated them.** `QCRunner.stop()` (`runner.py` ≈411) flips status under the lock and returns; the worker keeps unwinding and the run stays in the documented settling state until `_finalize_attempt`. The streamed transport already lets up to eight in-flight seats run to completion inside that interval, bounded only by the 600 s API timeout. A bounded post-cancel read is therefore better behaved than the Stop the app already ships, and it recovers the money. Revision 1's "no new blocking provider dependency on the Stop path" prohibited the one mechanism that helps, on a premise the architecture already handles. The user-visible cost that remains is real and stated in section 7: a replacement QC run, apply, dismiss and export stay locked while the worker settles.
2. **The financial-capture contract was overdesigned.** Per-seat capture status, request registries, anomaly lists, a versioned metadata block, a run-id-keyed disclosure set on the session ledger with merge semantics, and eighteen separately specified tests, for a defect whose whole shape is "the current round's results were not read". Section 7.5 is the replacement.
3. **The batch-economics framing overstated the risk, and the correction cuts against both reviews.** Revision 1's examples and the second review's table used a 60k-token input-only request. The repository's own measurements say something different: the v1.8.0 notes put a verifier request at roughly 12,600 tokens, and the per-phase effort notes reconcile **output as roughly 85 percent of a run's cost** (thinking bills as output). The batch discount halves output too. On those numbers, with an assumed 6k output tokens per seat (the export supplies the real figure):

   | 35 verifier seats, Opus 5 rates | Input side | Output side | Total |
   |---|---|---|---|
   | Streaming, 8 concurrent cache misses | $1.18 | $5.25 | $6.43 |
   | Batched, zero cache hits | $2.21 | $2.63 | $4.84 |
   | Batched, perfect cache hits | $0.17 | $2.63 | $2.80 |

   The batched default is safe regardless of hit rate. What the export inspection decides is the roughly two-dollar gap between the two batched rows, which is worth knowing and not worth a redesign. **Batched verification shipped in v1.12.0**, so any Final QC export saved between v1.8.0 and v1.11.0 carries measured streaming cache behavior for phase 2; if any exist, both sides of the comparison are measured and no paid experiment is needed.
4. **Incremental result consumption preserves review evidence, not only accounting.** A later download failure currently discards the verdicts of seats whose rows were already yielded. Revision 1 filed this under accounting; it strengthens the case for the change. Recovered verdict records still obey the existing partial and cancelled-attempt rules.
5. **The copyable commands invoked an undotted venv path.** Setup creates `.venv`, and Batch 8 retired the undotted form from every runbook. The commands in this revision follow the repository convention.
6. **Three implementation agents plus an integrator was disproportionate.** With the scope narrowed, this is three pull requests and one owner, reviewed independently.
7. **"No continuations, no retries, no promotion after Stop" is mostly existing behavior.** `_apply_batch_item` already settles a `canceled` result row; the runner installs a result into the retained slot only when `execution_status == "complete"`; a stopped run has already lost the compare-and-set. Recovery needs one flag (section 7.4), not a second guard.
8. **The session-level warning has an exact precedent.** The ledger already derives `includes_estimated_output` from a counter in `snapshot()` rather than storing a boolean. The uncollected-charges warning follows the same shape (section 7.5), which inherits reset, load, merge and the generation guard for free.

## 3. Non-negotiable invariants

### 3.1 Correctness and ownership

- A stopped or timed-out review cannot become actionable because a provider request later finishes. Recovered seats are recorded on the cancelled or partial attempt; the retained-success slot is untouched.
- Preserve run-token isolation and session-generation guards. Usage and disclosure belong to the originating session and run, never a successor.
- Preserve the distinction between a retained successful QC result and a newer partial, failed, cancelled or settling attempt.
- No network work or expensive rendering while holding session or runner locks. The settlement window runs on the worker thread with no lock held.
- Preserve lock ordering; review `SessionManager` and the per-session turn guards before adding callbacks.
- Retain every provider usage figure already obtained: refusals, failed parses, retries, continuation rounds.
- Never invent token counts for an unread batch result, and never infer spend from the number of submitted seats.

### 3.2 Three concepts that stay separate

1. **Arithmetic consistency**: recorded totals equal the sum of recorded underlying usage and prices. Unchanged; `_audit_accounting_consistent` still gates every load.
2. **Batch usage capture**: every submitted request in the recorded scope has a known outcome and its reported usage has been captured. A run can be arithmetically consistent and still incomplete here.
3. **Review completeness and validity**: the required lenses and panels completed under the adjudication rules. A complete capture does not make an incomplete review valid. A report written before this change carries no capture status and must not be read as complete.

### 3.3 Accounting scope

Bounded settlement plus explicit disclosure, in this release. **No background reconciliation service**: no durable queue, worker, recovery UI, or continued provider polling after the worker settles. If the bounded window cannot collect a result, the record says so and the app does not promise to reconstruct final provider charges later.

### 3.4 Quality effects

Accounting and display corrections must not change model inputs or verdicts. Lint compaction, if it proceeds, changes presentation to the model and is treated as a model-facing change with structural and adversarial-context tests. Moving current state ahead of history remains deferred.

## 4. Step 0 — baseline

Record before changing anything, in `docs/review-results/<date>/baseline.md`:

- Commit, branch, working-tree status. Do not discard unrelated changes.
- Python version, SDK version, installed dependency versions and whether they satisfy `requirements.txt`. Resolve the 0.117.0 discrepancy from the original review environment; do not change the requirement.
- Node and npm versions. CI pins Node 22; `npm test` depends on type stripping and cannot run on Node 20.
- Commands, exit codes, test counts, durations, explicit skip reasons. Whether DOCX visual verification ran and with which renderer.
- No secrets, no private document text, no machine-wide environment dump.

```powershell
# Repository root.
git --no-optional-locks status --short
git rev-parse HEAD
& '.\.venv\Scripts\python.exe' --version
& '.\.venv\Scripts\python.exe' -m pip list --format=json
node --version
npm --version

& '.\.venv\Scripts\python.exe' -m pytest -q -ra
& '.\.venv\Scripts\python.exe' -m ruff check .

# From frontend/.
npm test
npm run build
```

Use the supported Windows workflow. Do not install a new environment because the original brief showed Unix commands.

## 5. Step 1 — the drawer sums both QC categories (PR 1)

In `frontend/src/components/QCDrawer.tsx`, compute the session's QC spend as `qc` plus `qc_batched` from `usage.estimated_cost_usd.by_category`, treating an absent category as zero and never rendering `NaN`. Include neither interview nor research spend. Use the corrected value in both the description line and the confirmation text. Keep the retained report's run-specific cost separate from the session's cumulative spend.

A small pure helper in an existing frontend library module is fine if it makes the behavior testable; do not add a new state owner or duplicate the sum in two controls.

Tests (source-based, `frontend/tests/`, registered in the explicit `package.json` test list): streaming-only, batched-only, both present with unrelated interview and research spend excluded, absent usage, valid zero. Mutation check: restore the single-category read; the batched-only and mixed cases go red.

This is independently valuable and should ship first, alone.

## 6. Step 2 — read the real exports before building anything else

No application code is required. A read-only script under `tools/` is acceptable if it makes the reading repeatable; it must not import the client factory, initialize a session, make model requests, or emit document text, prompts, reference-document contents or credentials.

### 6.1 Inputs

Owner-designated Final QC JSON exports (`GET /api/qc/export.json`) and `.baspec` project files. Every persisted `QCVerdict` and `QCLensStatus` carries `usage_totals` with `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens` and, since Chunk 4.1, `cache_creation_1h_input_tokens`; each verdict carries `cost_multiplier` (0.5 for a batched seat, 1.0 otherwise). The export's `report` and `current_state` are separate; count a run once even if it was exported twice.

### 6.2 What to extract

Per run, split lens records from verifier records, and verifier records by `cost_multiplier` and by whether the seat's lens carried web tools:

- Token totals by class: uncached input, five-minute writes (`cache_creation_input_tokens − cache_creation_1h_input_tokens`), one-hour writes, cache reads, output.
- Cache-read share `R / (I + W + R)`, labelled as a token-weighted share of the input side and not a request hit rate.
- Output as a share of the run's estimated cost, using the persisted `cost_basis`.
- Phase 1: how many of the four web-toolless lenses wrote versus read the shared prefix (this decides the staggered-launch idea in section 10 on its own).
- Capture completeness is **unknown** for every existing record; do not infer it.

### 6.3 Decision rule

- Batched seats show cache reads on most seats: the batched default stands and the batch-cache-reuse investigation (section 10.2) is closed.
- Batched seats show near-zero reads: the default still stands on the output term (section 2.4.3), the measured input-side gap is recorded, and section 10.2 becomes a concrete, budgeted proposal for the owner rather than a deferred idea.
- Exports from v1.8.0 through v1.11.0 exist: report the measured streaming phase-2 cache behavior beside the batched figure and retire the $5.61 hypothetical.

Write the result to `docs/review-results/<date>/measurements.md` with artifact provenance and privacy-preserving summaries only.

## 7. Step 3 — incremental consumption, bounded settlement, minimal disclosure (PR 2)

### 7.1 Files

- `backend/qc/engine.py`: `_run_batch_calls`, `_apply_batch_item`, `_BatchSeatState`, `_CallResult`, `_verifier_outcome`, `QCVerdict`, `QCResult` (serialization, `usage_by_meter_category`, accounting validation).
- `backend/qc/runner.py`: metering and `_finalize_attempt` only if the sink signature changes (it should not).
- `backend/usage_ledger.py`: one counter key and one derived snapshot flag.
- `backend/settings.py` and `README.md`: one knob and its row.
- `backend/spec_doc/docx_export.py`: `_qc_render_usage_and_cost` and the executive cost line.
- `frontend/src/types.ts`, `frontend/src/lib/qcReport.ts`, `QCDrawer.tsx`, `QCReportModal.tsx`, `Header.tsx`, `SettingsPanel.tsx`: read the new fields; text only, no new control.
- `tests/fakes.py`: `with_options` on the fake client and batches; controllable result iterators.

### 7.2 Consume results incrementally

Replace `items = list(client.messages.batches.results(batch_id))` with item-by-item processing:

1. Read one row. Validate its `custom_id` against this round's submitted set; an unknown id is skipped and counted, never assigned to a seat.
2. Fold it through `_apply_batch_item` exactly as today (settle, continue, or retry).
3. On a later iterator exception, keep every row already folded, settle the remaining pending seats with the transport error under the existing failure class, and record the number of submitted requests whose result was never read (section 7.5). If every expected row was already read when the iterator's final step fails, the transport diagnostic is kept and no gap is recorded.

A duplicate row for a seat already settled in this round is ignored. A retried or continued seat legitimately reuses its `custom_id` in a later round; identity is `(batch round, custom_id)`, which the per-round `states`/`answered` bookkeeping already gives.

### 7.3 Bounded settlement after Stop and after the deadline

On `should_stop()` inside the poll loop, and on the wall-clock ceiling, after `_cancel_batch`:

1. Enter a settlement window bounded by `settings.QC_BATCH_SETTLE_SECONDS` (new; default 120; `_int_env(..., minimum=1)`; README row; the knob-inventory floor in `tests/test_settings.py` moves with it). The deadline is a single `time.monotonic()` value checked **between every operation**: after the cancel call, before each `retrieve`, before the results read, and between result rows.
2. Poll `retrieve` until `processing_status == "ended"` or the deadline passes, sleeping `QC_BATCH_POLL_SECONDS` in one-second slices. Do not consult `should_stop()` here; the run is already stopped.
3. If ended, read results through the incremental path of 7.2 in **recovery mode** (7.4).
4. Every call inside the window goes through `client.with_options(max_retries=0, timeout=<short read timeout, e.g. 30 s>)`. Without this the SDK's own two retries at a 600 s read timeout make the bound fiction. The fake client gains a `with_options` that returns itself.
5. When the deadline passes at any point, settle the remaining pending seats as cancelled (Stop) or with the ceiling message (deadline), exactly as today, and record the uncollected count.

Emit the existing `verification_batch` frames: `polling` with counts during the window, then `cancelled` or `timeout` carrying `uncollected: N`. No new status value, so `qcLive.ts` needs no fold change; the drawer may render the count on the terminal line.

The user-visible cost: a replacement Final QC run, apply, dismiss and export stay locked while the attempt settles, for at most the bound plus one short read. That is already true of the streamed transport's Stop, for longer. State it in the Stop confirmation copy if the copy currently implies an instant end.

### 7.4 Recovery mode in `_apply_batch_item`

A `recovering: bool = False` keyword. When true:

- `succeeded` with a complete stop reason: `settle_parsed()` as normal. The verdict and its usage are recorded on the cancelled or partial attempt.
- `succeeded` with `pause_turn`: settle as cancelled **after** appending the response to `all_responses`, so its usage is captured; no continuation is queued.
- `errored` and retryable: settle failed; no retry is queued.
- `canceled`, `expired`, refusal, incomplete: unchanged.

Nothing else changes. Promotion is impossible by construction: the runner installs a result into the retained slot only when `execution_status == "complete"`, and a stopped run's `_try_resolve` has already lost.

### 7.5 The disclosure: one count, one status, one derived flag

- `QCVerdict.uncollected_requests: int = 0`: the number of submitted batch requests for this seat whose result was never read. Serialized like every other verdict field; loads as 0 when absent.
- `QCResult.batch_usage_capture: str = ""`: `"complete"`, `"incomplete"`, or `""` for a report written before this change. Set at build time from the seats; `from_dict` refuses a value that disagrees with the seat counts, the same way outcome labels are checked against their seats. Empty is never promoted to complete.
- `usage_ledger`: a counter key (`uncollected_batch_requests`) added into the `qc_batched` bucket by `usage_by_meter_category()` and delivered through the existing `usage_sink(category, bucket)`, so the runner's metering code and the session-generation guard are unchanged. `snapshot()` derives `includes_uncollected_charges` from the counter, exactly as `includes_estimated_output` is derived. The pricing helpers read named token keys, so the counter is never priced. Reset, project load, `load_snapshot` and `merge_delta` need no change.
- Not in `input_manifest`, not in `input_fingerprint`, not in `cost_basis`. A retained result does not go stale because the app can now disclose capture. Test this explicitly.

### 7.6 Surfaces (text only, no new control)

- QC drawer session line and QC confirmation text: append "Some batch charges from a stopped run could not be collected; the real figure may be higher." when the ledger flag is set.
- `QCReportModal` cost line and `_qc_render_usage_and_cost` plus the executive cost line in the Word memo: per report, "Recorded cost estimate: $X. N batch requests were submitted whose results were not collected; the final cost may be higher." For `batch_usage_capture == ""`: "Cost-capture completeness was not recorded by this version."
- Header spend pill tooltip and the Settings usage paragraph: one sentence, beside the existing estimated-output disclosure, kept distinct from it (estimated token content and unread provider results are different limitations).
- JSON export: automatic through `to_dict`.
- `TrustDeepDiveModal`: inspect the Money section and the Final QC and Stop runtime cards for claims this changes; correct only those.

### 7.7 Tests

Parameterize over shared fixtures; the behaviors are mandatory, the count is not.

| Scenario | Essential assertion |
|---|---|
| Iterator raises after one successful row | That seat's verdict and usage survive; remaining seats fail with the transport error; `uncollected_requests` equals the unread count; the run is partial |
| Iterator raises after every expected row | No gap recorded; transport diagnostic kept |
| Stop after submission; batch ends within the bound | Completed seats are recovered with verdicts and usage; paused seats settle cancelled with their usage; nothing is continued or retried; `batches.create` is not called again; the attempt is cancelled and the retained result untouched |
| Stop after submission; batch never ends within the bound | Every pending seat cancelled; count recorded; no `retrieve` after the deadline; total calls inside the window bounded |
| Deadline (wall-clock ceiling) case | Same as Stop, with the ceiling message |
| Stop before submission | No batch, no gap, no warning |
| Duplicate row and unknown `custom_id` | Neither double-counts nor is assigned; unknown ids counted |
| Same `custom_id` in two rounds | Both rounds' usage counted |
| Ledger | The counter reaches the `qc_batched` bucket; `includes_uncollected_charges` is derived; empty known usage still discloses; a later successful run does not clear it; reset clears it; a stale-generation delivery is rejected |
| Persistence | `uncollected_requests` and `batch_usage_capture` survive project and JSON round trips; a disagreeing pair is refused; a pre-change report loads with `""` and renders the not-recorded wording; the input fingerprint of a retained result is unchanged |
| SDK options | Every call inside the window goes through `with_options(max_retries=0, timeout=…)`; a `retrieve` that raises does not escape the window |

Existing tests that must remain meaningful, by name: `test_stopping_cancels_the_batch_and_settles_every_open_seat`, `test_a_stop_before_submission_spends_nothing_on_phase_two`, `test_a_paused_seat_continues_in_a_second_round`, `test_a_retryable_seat_failure_restarts_on_a_fresh_conversation`, `test_a_seat_with_no_result_line_is_recorded_failed_not_dropped`, `test_the_run_total_is_the_sum_of_its_records_when_any_was_discounted`, `test_the_session_meter_prices_the_batched_phase_separately`, `test_a_stop_during_the_submission_backoff_returns_promptly` (all `tests/test_qc_batch_verification.py`); `test_cancelled_worker_preserves_paid_partial_without_replacing_success` (`test_qc_runner_audit_integrity.py`); `test_stopped_worker_cannot_resolve_or_emit_into_newer_run`, `test_current_schema_pricing_and_aggregate_accounting_are_reconciled`, `test_a_legacy_v3_report_keeps_its_historical_rendering` (`test_qc_audit_report.py`); `test_mixed_ttl_cache_writes_are_not_double_counted`, `test_ledger_reset_clears` (`test_usage.py`). The existing cancellation test does not detect missing spend; do not cite it as proof of the new behavior.

Mutation evidence, each reverted in place and restored: whole-iterator materialization restored → the first two rows red; the settlement window removed → the two Stop-recovery rows red; the `with_options` override dropped → the SDK-options row red; the counter not delivered → the ledger row red; the run-level status not validated → the disagreeing-pair case red.

### 7.8 Acceptance and rollback

Observed usage survives partial retrieval; completed results are collected within the bound; unresolved charges are disclosed; Stop acknowledgement is unchanged; run and session ownership hold; old reports load and render the not-recorded wording; no fingerprint changes. Exact final provider spend after a cancellation is **not** promised.

Rollback: the retrieval and settlement changes revert independently of the readers. A rollback must not relabel an incomplete report as complete; keep the readers.

## 8. Step 4 — measure lint repetition, then decide (PR 4, conditional)

### 8.1 Measurement first

On the owner's real project files, offline, with the injected date frozen: run `lint_document` and the chat context builder, and record per document the occurrence count by rule, the characters of the LINT REPORT block, and the share of that block that exact-`(rule, severity, message)` grouping would remove. Label a chars-to-tokens conversion as an estimate with its rule. The brief's 96k-character figure was a repeated-text fixture inflated by `duplicate_provision`; it decides nothing.

Working threshold, a judgment call to be stated as such: proceed when the block on a representative real document is more than about 5k characters and grouping would remove more than half of it; otherwise defer and record the measurement. The dollar case is small either way (the block is in the uncached tail every turn, but forty identical lines are roughly 2,500 tokens at Sonnet 5 rates); the justification, if any, is keeping the report readable for the model in the twenty-plus occurrence regime.

### 8.2 Contract if it proceeds

Change only the lint text inserted into `_turn_context_text`; not `lint_document`, raw ids, frontend rows, the lint SSE payload, readiness, or exports. Start with `stale_edition` and `unrecorded_edition`. A small pure helper beside the context builder; never in the lint engine.

Grouping key: exact `(rule, severity, message)` equality, no normalization, first-seen order, every element id and reference preserved, per-element occurrence counts preserved, no cap on the location list, unrecognized or malformed issues rendered exactly as today, input records never mutated, the advisory framing and the delimiter neutralization preserved. Use grouping for a group only when its rendering is strictly shorter than the legacy lines; otherwise keep the legacy lines. Singleton rendering stays byte-identical. Do not change the QC input manifest.

Illustrative rendering (formats are the existing ones, not a new numbering scheme):

```text
- [unrecorded_edition] <exact existing message>
  Affected citations (3): 1.1.A (element pt1.a1.p1); 1.1.B (element pt1.a1.p2); 1.2.A (element pt1.a2.p1).
```

### 8.3 Tests if it proceeds

Many identical edition messages across elements; the same standard at two cited years; same standard with differing severity or message; two physical citations in one paragraph; mixed edition and non-edition issues; singleton-only; empty; missing optional fields; determinism and no mutation; short messages where grouping is longer; hostile delimiter text; a continuation turn reusing the frozen context. At least one fake-client integration test must assert on the final emitted request text, on the unchanged raw lint payload, on readiness reporting the same population, and on committed history not acquiring the block. Existing pins to retain, by name: `test_stale_edition_detected_in_three_citation_shapes`, `test_unrecorded_edition_fires_on_the_engine_citation_shapes`, `test_recording_the_edition_silences_the_rule`, `test_overlapping_designation_forms_are_not_double_reported`, `test_three_identical_siblings_report_two_findings_not_three` (`test_linting.py`); `test_chat_turn_emits_lint_event_and_payloads_carry_standards`, `test_context_block_never_fossilizes_into_history`, `test_a_turns_cached_prefix_is_a_byte_prefix_of_the_next_request`, `test_no_breakpoint_survives_into_history_or_a_saved_project` (`test_app.py`); `test_document_text_cannot_forge_the_context_boundary` (`test_reference_docs.py`).

Rollback is the renderer call site; raw records are untouched.

## 9. Step 5 — documentation, integration, delivery (PR 3)

1. Append a `CLAUDE.md` section covering incremental batch consumption, the bounded settlement window and its SDK-option requirement, the one-count/one-status/one-derived-flag disclosure, why no background reconciliation exists, and the recovery-mode flag. Append an errata line for the v1.8.0 "every verifier seat shares" wording (two lineages), pointing at finding 8. Append the lint contract only if step 4 ships.
2. README: the QC drawer cost line, the new knob row, the cost-capture disclosure, the export-based measurement note. Do not trim; add.
3. Release notes: the current unreleased entry gets the drawer fix and the capture work. Check whether the current `settings.VERSION` is already tagged through the GitHub Releases API, never local tags; a tagged entry is frozen, so a new entry and a four-place version bump are required in that case.
4. `docs/review-results/<date>/`: baseline, measurements, and this plan's execution record, kept separate from the original brief. Do not edit the brief in Downloads.
5. `requirements.txt`: no change expected; say so.
6. No model identifier strings in commit messages, PR bodies, comments or new documentation.

Focused checks after each PR, then the integrated gates once:

```powershell
# Repository root.
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_qc_batch_verification.py tests/test_qc_audit_report.py tests/test_qc_runner_audit_integrity.py tests/test_usage.py tests/test_settings.py tests/test_docs_consistency.py
& '.\.venv\Scripts\python.exe' -m pytest -q -ra
& '.\.venv\Scripts\python.exe' -m ruff check .

# From frontend/.
npm test
npm run build
```

If the version changes, also run `packaging/windows/check_release_version.py`. Visual verification is targeted: the cost disclosures at normal and narrow widths, and a complete, a cancelled-with-gap, and a pre-change Word memo through the repository's renderer workflow. Report renderer unavailability rather than declaring the check passed.

## 10. Deferred cache investigations (conditional; not production defaults)

Nothing here is required to finish steps 1–5.

### 10.1 Staggered phase-one launch

Decided by the phase-1 telemetry in step 2: if the four web-toolless lenses routinely all write the shared prefix, an experiment may start one and release the rest on an observed response-start event. Include failure-to-start, cancellation, timeout and the web-toolled lens, and measure wall-clock as well as cost. Retain the current scheduler unless the measured benefit justifies the coordination.

### 10.2 Batch cache reuse

Decided by the batched-seat cache-read share in step 2 (section 6.3). If it is near zero, a concrete proposal states request count, synthetic payload, output allowance, timeout, maximum spend and cleanup before the owner authorizes any paid comparison. Compare total cost for equivalent work including output, retries and every cache-write TTL. `tools/qc_verifier_canary.py` is a schema-acceptance check, not cache evidence; do not expand its paid scope.

### 10.3 Client continuation caching and 10.4 slow-changing assets before history

Unchanged from revision 1: both remain deferred, both need real usage evidence, and neither may attach cache metadata to thinking blocks, memoize QC context by run id alone, or truncate the document or location lists under the label of a cache change.

## 11. Ownership

One implementer owns steps 1–5 and the three pull requests. One independent reviewer reads each PR against this checklist:

1. Financial metadata never treated as token counts; the counter is never priced.
2. Pre-change reports never declared financially complete.
3. No double counting after incremental capture (rows, retries, runner totals, workspace merges).
4. Stop acknowledgement unchanged; the settlement window bounded across cancel, polls, reads and SDK retries.
5. Recovered findings never installed as actionable.
6. Reset, project load and tutorial transitions neither lose nor misattribute the warning.
7. No fingerprint change from metadata.
8. If step 4 ships: no lost location, no merged remedy, no changed raw record.
9. Synthetic fixtures never cited as production savings.
10. Aggregate cache metrics never presented as per-request causal evidence.

Copyable execution prompt for the implementer:

```text
Implement steps 1 through 5 of docs/REVIEW_IMPLEMENTATION_PLAN_2026-09-08.md
(revision 2) against the current repository, revalidating the cited symbols
first. Ship the QC drawer category sum alone as PR 1. Read the owner's existing
Final QC exports and record the batching measurement before any further cost
work. Then deliver incremental batch-result consumption, a bounded post-cancel
settlement window with per-request SDK overrides, and the one-count/one-status/
one-derived-flag disclosure as PR 2, with the tests and mutation evidence the
plan names. Measure real lint repetition and implement compaction only if the
plan's threshold is met. Finish with documentation, release metadata, and the
integrated gates as PR 3. Do not change model routing, effort, panel sizes,
full-document context, raw lint records, adjudication, cache defaults, the QC
schema or protocol version, or the input manifest. Do not add background
reconciliation or run paid experiments. Use the .venv interpreter.
```

## 12. Definition of done

- [ ] Baseline recorded; the SDK environment discrepancy resolved or isolated.
- [ ] QC drawer includes streaming and batched categories (PR 1 merged).
- [ ] Batching question answered from real exports and recorded, or the absence of exports recorded.
- [ ] Partial batch result retrieval preserves observed verdicts and usage.
- [ ] Results completed before a Stop or deadline are collected within the bound; every call inside the window carries the SDK overrides.
- [ ] Uncollected requests are counted per seat, summarized per run, and disclosed on every cost surface; empty known usage still discloses.
- [ ] No tokens, costs or request identities fabricated; no duplicate charges.
- [ ] Stop acknowledgement, partial-attempt retention and successor isolation hold.
- [ ] Pre-change reports load and render the not-recorded wording; fingerprints and adjudication unchanged.
- [ ] Lint compaction either shipped against a recorded measurement or deferred with the measurement recorded.
- [ ] Documentation describes the shipped code; the `.venv` convention holds in every runbook command.
- [ ] Focused tests, integrated gates and targeted visual checks reported accurately, with the mutation evidence.
- [ ] No unapproved paid experiments, releases, unrelated refactors or new dependencies.
