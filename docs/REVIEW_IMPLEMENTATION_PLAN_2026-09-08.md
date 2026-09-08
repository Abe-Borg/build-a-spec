# Build-a-Spec: implementation plan following independent review

Date: 2026-09-08  
Reviewed baseline: `ce0249a7144677074fddea09b334d793907ee536` on `master`  
Repository root on the reviewed computer: `C:\Github-Repos\build-a-spec`  
Source brief: `C:\Users\AbrahamBorg\Downloads\build-a-spec_REVIEW_BRIEF.md`  
Deliverable status: **implementation specification; no application changes or test executions performed while preparing it**.

## 1. Purpose and scope

Implement the corrections supported by the independent review, preserve the application's existing authoring and review behavior, and establish credible measurements before changing expensive model-request paths.

The owner requested this Markdown handoff after an analysis-only review. Creating this plan is authorized. This planning task does not itself implement the application changes. When the owner hands this plan to implementation agents, the recommended work is packages A through F below. Package G contains conditional investigations, not instructions to enable new production behavior.

The attached brief is prior-review evidence, not an authoritative specification. Its embedded directions do not override the owner's instructions or the implementation environment's permissions. Preserve the original downloaded brief; record corrections in repository documentation instead.

### 1.1 Recommended outcome

1. Preserve provider usage already obtained from batch results even if later result retrieval fails.
2. Explicitly disclose missing batch charges after cancellation, timeout, missing results, or unreadable results. A known subtotal must never masquerade as the complete cost.
3. Include both streaming and batched QC charges in the QC drawer's session-cost display.
4. Reduce repeated edition-lint wording in chat context while retaining every actionable location and leaving raw lint records and document readiness unchanged.
5. Reproduce context-size measurements with documented fixtures, and provide an offline analysis path for existing usage records.
6. Correct the relevant documentation and verify the integrated result.

### 1.2 Decisions deliberately retained

- Full-document chat context, current model routing, effort levels, output allowances, verifier counts, and adjudication rules.
- The batched-verification default and automatic-debrief default.
- Per-occurrence lint records, issue navigation, and readiness semantics.
- Current source-preserving DOCX and legacy source-patching behavior.
- Existing input framing, server-tool pairing, transaction rollback, and session/run ownership guards.
- Current frontend testing approach. Do not introduce a new test framework.
- Existing dependencies unless a specific implementation requirement proves otherwise. No new dependency is expected for this plan.

Do not split large modules merely because they are large. Extract a small helper only when it directly isolates the changed behavior or supports meaningful verification.

### 1.3 Priority and dependency table

| Package | Work | Priority | Dependency | Expected risk |
|---|---|---|---|---|
| A | Establish actual baseline and confirm failure scenarios | First | None | Low; test/environment work |
| B | Preserve observed batch usage and represent incomplete financial capture | Highest correctness value | A | Medium; persistence and audit contracts |
| C | Correct and disclose QC/session cost presentation | High | B metadata contract; cost-sum fix can start independently | Low to medium |
| D | Compact edition-lint wording in chat only | Recommended improvement | A; before/after fixture from E | Medium; model-facing formatting |
| E | Reproducible context and offline usage measurements | Recommended evidence work | A; compare D before/after | Low if kept outside production paths |
| F | Documentation, integration, and release-ready verification | Required | B, C, D, E | Low to medium |
| G | Conditional cache experiments | Deferred | E and explicit experiment authorization where required | Medium to high |

Packages B/C and D/E can proceed in parallel after A. One integrator owns shared documentation, release metadata, and the final full-suite run.

## 2. Evidence the implementers must understand

All line numbers below refer to the reviewed commit. Search by symbol and test name if the checkout has moved.

### 2.1 Confirmed source findings

| Finding | Source anchor | Practical implication |
|---|---|---|
| Stop and timeout return before collecting the active batch's results | `backend/qc/engine.py`, `_run_batch_calls`, approximately 5255 and 5278 | Successful requests completed before cancellation can be absent from recorded spend |
| Results are fully materialized before any item is processed | `backend/qc/engine.py`, approximately 5286 | An iterator failure can discard usage from rows already yielded |
| The runner meters returned records once | `backend/qc/runner.py`, approximately 250-263 | Missing batch responses are not later reconciled by the runner |
| QC drawer reads only the `qc` category | `frontend/src/components/QCDrawer.tsx`, approximately 970 | Session QC cost omits `qc_batched`, although the ledger has that category |
| Edition lint is per physical occurrence, with stable IDs | `backend/spec_doc/linting.py`, `lint_document`; `tests/test_linting.py` | Replacing raw issues with grouped issues would change a deliberate contract |
| Chat renders every lint occurrence | `backend/llm/conversation.py`, `_turn_context_text`, approximately 2005-2030 | Repeated explanatory wording is a bounded optimization target |
| QC context includes freshness and finding dispositions | `backend/qc/context.py`, `qc_review_context_block` | QC context is not stable solely because its run ID is unchanged |
| Compliance verifiers have different tools from other verifiers | `backend/qc/engine.py`, `_verifier_tools`, approximately 4656 | There are distinct cache-compatible prefixes, not one universal verifier prefix |

These are static findings, not claims that a new regression test has already reproduced them. Package A/B supplies that proof.

### 2.2 Corrections to the review brief

1. **Character counts are inconsistent.** The reported outline is 67,703 characters, lint is 96,024, and full context is 120,911. The first two sum to 163,727. They cannot describe the stated components of the same assembled string under the same conditions. Determine what was actually measured; do not manufacture a fixture to force the numbers to agree.
2. **The 24k lint-token estimate cannot be attributed solely to edition findings.** The cited issue population also contains 120 synthetic duplicate-provision findings. Separate rules and measure the actual model-facing text.
3. **The phase-one example confuses added cost with missed savings.** Four hypothetical 60k-token five-minute writes cost $1.50; uncached input costs $1.20; ideal one-write/three-read scheduling costs $0.465. The write penalty is $0.30. The potential improvement relative to the ideal schedule is $1.035.
4. **The batch no-hit example omits the one-hour write premium.** Thirty-five hypothetical 60k-token one-hour writes at $10/million, discounted by 0.5, cost $10.50. The brief's $5.25 describes uncached input at $5/million with the discount, not one-hour cache creation.
5. **Ideal streaming is not the current streaming baseline.** The current verifier pool can launch eight calls concurrently, and requests are partitioned by tools. Actual cache misses need observation. In a simplified single-prefix illustration, eight writes plus twenty-seven reads cost $5.61, versus $1.62 for ideal one-write/thirty-four-read streaming. Neither is an observed run.
6. **A shared document string is not a shared complete cache prefix.** Tools and request options matter. Priming one verifier is insufficient when both web-enabled and other verifier requests exist.
7. **Many lint occurrences are not many independent acknowledgement tasks.** `backend/app.py` uses one `lint_clean` boolean. One edition-setting operation can resolve multiple matching unrecorded citations.
8. **Automatic debrief is conditional and deduplicated.** Inspect the queue and completion events. Do not say every stopped or failed run triggers a billed debrief, and do not claim it is the most expensive turn without data.
9. **Untouched DOCX preservation has a tracked-changes exception.** Revision-bearing paragraphs are rewritten to the imported Accept-All view. Parsed-element equality and lexical XML-byte equality are different contracts.
10. **Baseline descriptions need qualification.** The brief reports earlier test results; this review did not rerun them. The frontend script lists 27 test files at this commit, not 12. Establish skip reasons and build results instead of treating a large pass count as a correctness certificate.

### 2.3 Current provider references

Recheck these official sources before implementing provider-facing changes; record the verification date. This plan's numerical examples use the reviewed configuration and published rates, not a guarantee of future pricing.

- [Batch processing](https://platform.claude.com/docs/en/build-with-claude/batch-processing): cache and batch discounts can combine; sibling cache hits are best effort; cancellation can leave completed results. Retrieve actual request outcomes to distinguish completed work from requests canceled before processing.
- [Pricing](https://platform.claude.com/docs/en/about-claude/pricing): the current example uses $5/million base QC input, $6.25/million five-minute writes, $10/million one-hour writes, $0.50/million reads, and the 0.5 batch multiplier. Output and applicable tool charges remain separate cost components.
- [Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching): complete prefix identity, cache availability after response commencement, expiration, breakpoint placement, and request-level automatic caching are relevant. Assistant thinking blocks cannot simply receive arbitrary explicit markers.
- [Tool use with prompt caching](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-use-with-prompt-caching): server-tool results receive automatic cache treatment within the server's agentic loop when caching is enabled. Missing local assistant markers alone does not measure the cost of client continuation boundaries.
- [Stop reasons](https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons): preserve paused assistant content and the server-tool continuation contract.

The installed SDK inspected during review was version 0.117.0, while `requirements.txt` currently requests `anthropic>=1.0,<2`. That is an environment discrepancy to resolve and document in A, not a reason to silently downgrade the repository requirement. The inspected SDK already exposes request-level `cache_control`; verify the actual implementation environment separately.

## 3. Non-negotiable implementation invariants

### 3.1 Correctness and ownership

- A stopped or timed-out review cannot become actionable because a provider request later finishes.
- Preserve run-token isolation and session-generation guards. Paid usage and disclosure metadata must belong to the originating session/run, never a successor.
- Preserve the distinction between retained successful QC and a newer partial, failed, canceled, or settling attempt.
- Do not do network work or expensive rendering while holding session or runner locks.
- Preserve lock ordering. Review `SessionManager` and per-session turn guards before adding callbacks.
- Retain all provider usage already obtained, including usage on refusals, failed parses, retries, and continuation rounds where the provider returned counts.
- Never invent token counts for an unread batch result, and never infer spend from the number of submitted seats alone.

### 3.2 Three concepts that must remain separate

1. **Arithmetic consistency:** recorded totals equal the sum of recorded underlying usage and prices.
2. **Batch financial capture completeness:** all submitted batch requests in the recorded scope have known billable/nonbillable outcomes and their reported billable usage has been captured. This is not a guarantee of complete provider invoicing or coverage of unrelated streaming failures.
3. **Review completeness and validity:** the required lenses and panels completed under the applicable QC adjudication rules.

A record may be arithmetically consistent while missing provider charges. A complete financial record does not make an incomplete review valid. A legacy report without capture metadata must not be silently promoted to financially complete.

### 3.3 Accounting scope decision

**Implement explicit incomplete-accounting disclosure in this release. Do not build a background reconciliation service as part of this plan.**

The bounded fix preserves every observed charge and states when others are unavailable. It does not promise to reconstruct final provider charges after cancellation. Keep enough non-content request identity to support later reconciliation if needed, but do not add a durable queue, worker service, new recovery UI, or hidden continued provider polling now.

This is an intentional acceptance boundary, not a claim that complete billing can be known immediately after Stop. If automatic reconciliation is later requested, it needs its own ownership, persistence, credential, retry, shutdown, and exactly-once design.

### 3.4 Scope of quality effects

Accounting and display corrections must not change model inputs or verdicts. Lint compaction preserves the information provided but changes its presentation to the model; treat it as a model-facing change requiring structural and adversarial-context tests. Moving current state ahead of older history is a larger behavioral change and remains deferred.

## 4. Package A — baseline, reproduction, and coordination

### A1. Establish the actual starting point

1. Read applicable repository instructions and the relevant current sections and later errata in `CLAUDE.md`.
2. Record commit, branch, and working-tree status. Do not discard unrelated changes.
3. Inspect the configured Python environment before running tests. Record Python version, SDK version, installed dependency versions, and whether they satisfy `requirements.txt`.
4. Record Node and npm versions and frontend lockfile identity. Use the repository-required Node version; the existing tests use type stripping.
5. Record relevant non-secret execution settings, DOCX renderer availability, and whether visual checks will run. Never dump the full environment or credential configuration.
6. If environment repair is required, use the host's permission workflow. Do not bypass it or silently change global installations. Do not change project dependency policy simply to make a stale environment pass.

Recommended PowerShell command shapes, issued from the stated directory and adapted only to the verified environment:

```powershell
# Repository root; read-only inventories.
git --no-optional-locks status --short
git rev-parse HEAD
& .\venv\Scripts\python.exe --version
& .\venv\Scripts\python.exe -m pip list --format=json
node --version
npm --version

# Baseline gates; these can create normal test/build artifacts.
& .\venv\Scripts\python.exe -m pytest -q -ra
& .\venv\Scripts\python.exe -m ruff check .

# Run these from frontend/.
npm test
npm run build
```

Do not install a new environment merely because the original brief shows Unix commands. Use the supported Windows workflow. Respect any existing restrictions on local writes when this plan is executed in a different task.

### A2. Baseline record

Create a concise execution record under the implementation branch's review documentation, for example `docs/review-results/2026-09-08/baseline.md`. Include:

- Exact commit and runtime versions; resolved dependencies or a link to the local recorded inventory.
- Commands, exit codes, test counts, durations, and explicit skip reasons.
- Whether DOCX visual verification ran, and with which renderer.
- Any existing failures, with a reproduction and scope attribution.
- No secrets, private document text, machine-wide environment dump, or unsupported claim of complete audit coverage.

Use a later date if implementation occurs later. Do not commit private runtime artifacts unnecessarily.

### A3. Add proof before the accounting fix

The B owner first writes focused regression scenarios for:

- One successful, billable seat exists before a user stop.
- One successful, billable seat exists before the phase deadline.
- The results iterator yields a successful row and then raises.
- The QC drawer receives only `qc_batched` spending.

These should expose missing accounting/disclosure or display behavior on the baseline. Use provider fakes; no paid calls are needed.

Do not assert these tests already fail until executed. If the source has changed and a scenario passes, investigate before changing working code.

### A acceptance

- The starting environment and actual baseline are identifiable.
- Confirmed defects have meaningful test scenarios or a documented reason a scenario cannot yet execute.
- The owners and shared-file boundaries below are agreed.

## 5. Package B — trustworthy batch accounting

### B1. Files and interfaces to inspect

Primary ownership:

- `backend/qc/engine.py`: `_run_batch_calls`, `_apply_batch_item`, `_BatchSeatState`, `_CallResult`, `_verifier_outcome`, `QCVerdict`, `QCResult`, aggregate/accounting validation and serialization.
- `backend/qc/runner.py`: completion, canceled-attempt preservation, metering, snapshots and restoring saved attempts.
- `backend/usage_ledger.py`: separate disclosure state, snapshots, reset, clone restoration and delta merge.
- `backend/llm/conversation.py`: `SessionState.add_usage_if_current` and any analogous disclosure update.
- `backend/app.py`: QC start callback capture and usage/status/export plumbing.
- `backend/spec_doc/project.py` and `backend/spec_doc/project_package.py`: only if the existing generic QC serialization does not already carry the new metadata safely.

Likely test ownership:

- `tests/test_qc_batch_verification.py`
- `tests/test_qc_audit_report.py`
- `tests/test_qc_runner_audit_integrity.py`
- `tests/test_usage.py`
- Relevant existing session/tutorial/project-load tests discovered by symbol search.

### B2. Define the additive financial-capture contract first

Before coding the frontend, share the exact serialized shape with C. The following is the recommended conceptual shape; adapt naming to existing conventions without weakening its semantics:

```json
{
  "batch_accounting": {
    "version": 1,
    "capture_status": "incomplete",
    "unresolved_requests": [
      {
        "batch_id": "provider-batch-id",
        "custom_id": "request-id-within-batch",
        "round_index": 0,
        "reason": "cancelled_before_results_collected"
      }
    ]
  }
}
```

Contract requirements:

1. Use a separate typed structure, preferably per verifier seat with a derived run summary. Do not mix strings, flags, counts of missing requests, or request IDs into `usage_totals`.
2. Distinguish `complete`, `incomplete`, and `not_recorded` capture. Missing metadata in an older batched record means `not_recorded`, not a retroactively verified `complete`. Streaming-only work is not applicable to this batch-specific extension; derive that from recorded transport facts instead of attaching a misleading completeness claim.
3. Record every unresolved submitted request identity the app actually knows. Represent missing batch ID, ambiguous submission, or unmatched returned identity honestly; do not invent an ID or a request count.
4. Capture reason codes separately from concise display text. Expected reasons include stop, deadline, unreadable results, missing result row, and an ambiguous provider submission outcome where relevant.
5. Known successful provider results contribute their reported usage even when the review itself is canceled. Known nonbillable terminal results can resolve capture without contributing tokens; verify current provider semantics.
6. Aggregate summaries derive from underlying seat/request metadata plus a bounded run-level accounting-anomaly list for unassignable or conflicting provider results. If a summary is persisted too, validate agreement with that evidence. An unknown custom ID has no invented seat owner; a batch-wide anomaly must still prevent an unsupported completeness claim even if every expected seat is otherwise resolved.
7. Complete capture requires the expected request population to be accounted for. A zero-token subtotal does not prove a no-cost run.
8. Keep the existing `cost_basis` shape and token-price arithmetic intact. Financial completeness is not a new pricing rate.
9. Validate field types and supported metadata versions. Never accept contradictory metadata as complete. Because this is optional financial metadata, preserve an otherwise valid paid review and disclose unknown capture when that metadata is malformed or unsupported; do not weaken strict existing numerical usage/pricing validation. Keep semantic verdict integrity independent of this financial-field parsing policy.
10. Bound metadata by the already bounded submitted request population. Do not save request bodies, returned document text, credentials, or arbitrary provider exception payloads in this structure.

Use an independently versioned additive accounting field. **Do not blindly bump `QC_REPORT_SCHEMA_VERSION` or protocol version:** those values participate in historical adjudication and integrity logic. A financial-disclosure addition must not accidentally reclassify existing schema-4 reports as a weaker legacy format or re-adjudicate schema-3 reports.

If the implementation reveals a real need to change the main schema, document the specific incompatibility and implement explicit version-specific readers and tests. Do not take a global version bump as a shortcut.

### B3. Consume results incrementally

Replace whole-iterator materialization with controlled item-by-item processing:

1. Read one provider result row.
2. Validate its batch/request identity against this submission.
3. Capture reported usage and record the financial outcome through one accounting path.
4. Apply the existing parsing/retry/continuation logic without duplicating usage.
5. Continue until end of stream or failure.

On a later iterator exception, preserve already processed usage and mark only unresolved capture as unavailable. Settle unfinished seats according to existing partial/failure rules. Do not return an empty accounting record merely because the whole results stream did not complete. If every expected request result was already captured before the iterator's final read fails, retain the transport diagnostic without inventing a missing-charge gap.

Maintain the existing distinction between `all_responses` for the active attempt and billed prior attempts. Be especially careful when a retry resets messages: it must not erase financial evidence.

### B4. Duplicate and anomalous results

- Identify a result by at least `(batch_id, custom_id)` within a run, not `custom_id` alone: a continued/retried seat may use the same custom ID in a different batch round.
- An identical repeated row must not double-count usage or schedule another continuation.
- Conflicting duplicate rows must not silently replace trusted data. Report an integrity/capture anomaly and keep verdict eligibility conservative.
- Unknown custom IDs must not be assigned to an arbitrary seat. Mark the anomaly without corrupting known aggregates.
- Missing expected rows remain represented as failed/inconclusive review work and incomplete financial capture when their billing outcome cannot be established.

Do not introduce a process-global deduplication registry. Ownership belongs to the execution/request records.

### B5. Stop and deadline behavior

Preserve prompt user cancellation:

1. Once the execution path observes Stop, it submits no further model work for that run. A request already crossing the provider boundary may still be accepted; account for it or disclose its unresolved outcome instead of claiming local cancellation revoked it.
2. Request provider cancellation using the existing supported path.
3. Preserve observed usage from earlier completed rounds and any current-round rows already captured.
4. Mark outstanding submitted work as financially incomplete when its outcome is unavailable.
5. Finalize the review as canceled/partial under the existing runner rules.

Do not wait for the provider's cancellation to reach `ended`, add lengthy final polling, or keep the runner in a new indefinite “settling” state merely to chase exact charges. A read of already available results may be reused if the existing flow has them, but no new blocking provider dependency belongs on the Stop path.

If cancellation itself fails or is ambiguous, retain the disclosure; do not claim the provider stopped charging. The UI should explain missing accounting, not expose infrastructure exception details.

### B6. Runner and session-ledger propagation

1. Keep one-time aggregate metering of known usage; do not meter each streamed result and then meter the whole result again. Publish originating run ID, category buckets, and capture disclosure as one logical update under the existing session-generation guard and ledger lock. A concurrent snapshot must not see newly recorded spending without its already-known missing-charge warning. Prefer a QC run-report operation for this purpose; any alternative must prove the same atomic publication and duplicate-delivery protections.
2. Pass incomplete-capture notices through the same originating-session generation check as usage.
3. A notice must propagate even when known usage is empty. `UsageLedger.add` being a no-op for empty counts must not suppress a real missing-charge warning.
4. Store disclosure metadata outside category token buckets, such as a deduplicated set/map keyed by originating QC run identity.
5. Preserve disclosure in `snapshot`, `load_snapshot`, and detached-workspace `merge_delta` behavior. Merge identities idempotently, not by subtracting boolean or integer flags.
6. `reset` and project load clear the current-session ledger and its disclosure according to existing session-meter policy. A loaded saved QC report still carries its own historical capture status.
7. A newer complete run does not erase an earlier incomplete-charge notice from the same session's cumulative meter.
8. Superseded sessions cannot receive late notices or charges. Test the generation guard with notices as well as token counts.
9. Preserve the saved partial attempt without replacing the last successful retained review.

With no background reconciler, the financial record is finalized with its known subtotal and explicit gap. There is no late mutation of a saved report, no late installation of a verdict, and no automatic provider call after this work completes.

### B7. API, persistence, and freshness policy

- Carry metadata through the QC status/report snapshot, JSON export, saved project/package, and reload.
- Derive overall financial capture without changing panel adjudication or document readiness rules.
- Arithmetic integrity checks still reconcile known per-record totals; do not weaken them because capture is incomplete.
- Financial disclosure is not part of the model input. Do not add it to `input_manifest` or change `input_fingerprint` solely to describe accounting.
- Existing successful QC results should not become stale merely because the app can now disclose capture completeness. Test this explicitly.
- Older financial records retain their historical cost basis. Do not reprice them with today's settings.

### B8. Required new regression scenarios

The names below are proposed new tests, not existing executed tests. Names may follow local style, but each behavior is mandatory.

| Proposed test/scenario | Essential assertion |
|---|---|
| `test_batch_result_stream_failure_preserves_preceding_usage` | First yielded billable result remains priced after the iterator raises; remaining request capture is incomplete |
| `test_stop_after_submission_discloses_uncollected_batch_charges` | Stop returns under existing responsiveness bounds; canceled seats do not claim financial completeness, even with zero observed usage |
| `test_timeout_preserves_prior_round_usage_and_marks_current_round_incomplete` | Earlier continuation/retry usage survives; unknown current-round usage is not fabricated |
| `test_stop_before_submission_has_complete_zero_phase_two_capture` | No batch submitted means no missing-charge warning for that phase |
| `test_missing_batch_result_keeps_known_rows_and_marks_capture_gap` | Known records survive; missing identity is represented |
| `test_identical_batch_result_row_is_accounted_once` | No duplicate tokens, request counts, or continuations |
| `test_same_custom_id_in_distinct_rounds_is_not_deduplicated` | Legitimate continuation/retry work is counted in both rounds |
| `test_conflicting_duplicate_and_unknown_result_ids_fail_conservatively` | Anomalies cannot produce a clean complete capture or an actionable invalid verdict |
| `test_batch_capture_metadata_survives_project_and_json_round_trip` | Seat and run summaries retain their meaning and agree |
| `test_legacy_capture_status_is_not_recorded` | Missing old metadata is readable but not falsely complete |
| `test_invalid_optional_capture_metadata_is_unknown_without_rewriting_verdicts` | Unsupported/contradictory metadata is never complete; otherwise valid review evidence remains readable and numerical accounting validation remains strict |
| `test_all_expected_rows_received_before_iterator_failure_has_no_fake_gap` | An error after the complete expected result population does not invent missing financial capture |
| `test_incomplete_capture_notice_reaches_ledger_without_tokens` | Empty known subtotal still discloses missing costs |
| `test_session_generation_rejects_stale_capture_notices` | Reset/load/successor isolation applies to notices |
| `test_usage_snapshot_cannot_observe_new_run_spend_without_its_capture_notice` | Token buckets and disclosure publish atomically |
| `test_batch_wide_anomaly_prevents_false_complete_capture` | Unknown/conflicting provider identity remains disclosed even when expected seats are resolved |
| `test_capture_notices_clone_merge_and_reset_without_duplication` | Tutorial/scenario or detached snapshot operations preserve semantics |
| `test_accounting_metadata_does_not_change_qc_input_fingerprint` | Financial metadata does not invalidate unrelated paid review |

Use fakes with exact reported token counts, mixed TTL usage, different batch rounds, and controlled iterator failures. Test arithmetic through the production pricing helpers; do not assert the implementation's private statement order.

### B9. Existing tests that must remain meaningful

- `tests/test_qc_batch_verification.py::test_stopping_cancels_the_batch_and_settles_every_open_seat`
- `tests/test_qc_batch_verification.py::test_a_stop_before_submission_spends_nothing_on_phase_two`
- `tests/test_qc_batch_verification.py::test_a_paused_seat_continues_in_a_second_round`
- `tests/test_qc_batch_verification.py::test_a_retryable_seat_failure_restarts_on_a_fresh_conversation`
- `tests/test_qc_batch_verification.py::test_a_seat_with_no_result_line_is_recorded_failed_not_dropped`
- `tests/test_qc_batch_verification.py::test_the_run_total_is_the_sum_of_its_records_when_any_was_discounted`
- `tests/test_qc_batch_verification.py::test_the_session_meter_prices_the_batched_phase_separately`
- `tests/test_qc_runner_audit_integrity.py::test_cancelled_worker_preserves_paid_partial_without_replacing_success`
- `tests/test_qc_audit_report.py::test_stopped_worker_cannot_resolve_or_emit_into_newer_run`
- `tests/test_qc_audit_report.py::test_current_schema_pricing_and_aggregate_accounting_are_reconciled`
- `tests/test_qc_audit_report.py::test_a_legacy_v3_report_keeps_its_historical_rendering`
- `tests/test_usage.py::test_mixed_ttl_cache_writes_are_not_double_counted`
- `tests/test_usage.py::test_ledger_reset_clears`

The existing cancellation test does not by itself detect missing provider spending. Do not claim it proves the new accounting behavior. Record exactly which new tests fail when the new mechanism is removed.

### B10. Acceptance and rollback

Acceptance requires observed charges to survive partial retrieval, unresolved charges to be disclosed, preserved Stop behavior, correct run/session ownership, and backward-compatible report handling. It does **not** require exact final provider spending to be known after cancellation.

Keep this package independently reviewable. If rollback is necessary, prefer disabling the changed retrieval behavior while retaining readers and truthful display of accounting metadata already written. A code rollback must not silently discard new financial-gap information or relabel incomplete reports as complete. Verify old/new report fixtures before deciding whether a full rollback is compatible.

## 6. Package C — correct and honest cost presentation

### C1. Correct the QC category sum

In `frontend/src/components/QCDrawer.tsx`, calculate session QC spending from `qc` plus `qc_batched`. Include neither interview nor research spending. Use the existing validated numeric conventions; handle absent fields as absent/zero consistently with the current UI and avoid rendering `NaN`.

Use the corrected value for both the drawer's description and the confirmation text. Keep the retained report's run-specific cost separate from the session's cumulative spending.

Prefer a small pure helper in an appropriate existing frontend library module if that makes behavior testable. Do not introduce a new state owner or duplicate the calculation in multiple controls.

### C2. Display missing-charge disclosure where cost is asserted

Coordinate with B's final API contract. Inspect at least:

- `frontend/src/types.ts` and `frontend/src/lib/qcReport.ts`
- `frontend/src/components/QCDrawer.tsx`
- `frontend/src/components/QCReportModal.tsx`
- `frontend/src/components/Header.tsx`
- `frontend/src/components/SettingsPanel.tsx`
- `frontend/src/components/DeveloperToolsModal.tsx`
- `backend/spec_doc/docx_export.py`: `_qc_render_usage_and_cost`, executive cost text, and per-record telemetry labels
- QC JSON export and any diagnostics snapshot that describes the same totals

Recommended user-facing wording, adapted to the available space:

> Recorded cost estimate: $X. Some batch charges could not be collected, so the final cost may be higher.

For a historical report without the metadata:

> Cost-capture completeness was not recorded by this version.

Do not label unavailable counts as zero, present a range without a basis, or imply an invoice-exact amount. Keep this warning distinct from the existing stopped-chat estimated-output disclosure: estimated token content and missing provider results are different limitations.

A capture warning should not replace the existing partial-review explanation or change what the user can apply. No new user action/control is required for this package.

### C3. Tests and verification

Add behavior tests for:

1. Only streaming QC cost present.
2. Only batched QC cost present.
3. Both categories present, plus unrelated interview/research cost that must be excluded.
4. Missing usage data and valid zero totals.
5. Incomplete capture with a positive known subtotal.
6. Incomplete capture with no captured tokens.
7. Legacy report without capture metadata.
8. Complete known capture without the missing-charge warning.
9. Report JSON, UI, and Word memo agree about whether costs may be missing.
10. A loaded historical QC report is not treated as spending in the new session's reset meter.

Use `frontend/tests/qcReport.test.ts` and related existing source-based frontend test patterns. If a new test file is necessary, add it to the explicit `frontend/package.json` test command; do not assume automatic discovery.

For Word memo changes, add structural assertions in the existing QC report tests and visually inspect a representative complete, canceled/incomplete, and legacy memo using the repository's renderer workflow. Report renderer unavailability rather than declaring visual verification passed.

This package changes text, not controls. If an implementer nevertheless introduces a control, the capability vocabulary, production `data-capability`, tour entries, and set-equality tests must remain aligned. Prefer avoiding that scope expansion.

### C4. Acceptance and rollback

- The QC drawer includes the entire recorded QC session spend.
- Every prominent cost assertion affected by unavailable batch charges has an appropriate disclosure.
- The same unknown amount is never portrayed as known on another surface.
- Old records remain readable; saved review validity and freshness are unchanged.

Rollback can restore the former presentation only if it does not conceal metadata written by B. Keep the small category-sum fix separate if needed; it is independently valuable.

## 7. Package D — compact edition-lint wording without losing locations

### D1. Exact scope

Change only the lint text inserted into `_turn_context_text`. Do not change `lint_document`, raw issue IDs, frontend issue rows, lint SSE payloads, readiness checks, or lint export/API records.

Start with the repeated edition rules (`stale_edition` and `unrecorded_edition`). Leave unrelated lint rules unchanged unless separate evidence justifies expansion. The synthetic duplicate-provision issue count must not drive a production change to duplicate detection.

Recommended implementation location: a small pure helper near the chat context builder or a tightly scoped chat-context module. Avoid embedding model-presentation policy in the deterministic lint engine.

### D2. Grouping contract

1. Use exact `(rule, severity, message)` equality for the current issue schema. Require expected string fields for grouping. Do not normalize whitespace, punctuation, capitalization, or wording. If a future field changes remedy meaning without changing those fields, conservatively extend the key or leave that issue ungrouped.
2. Do not group by standard name alone. Different cited years, expected years, adoption bases, and remedies must remain distinct.
3. Preserve first-seen group order and first-seen occurrence order. Repeated calls with the same input must produce identical output.
4. Preserve every affected element ID and its original human-readable reference where available.
5. Preserve separate physical occurrences in one element. If listing an element once, retain an explicit occurrence count and mapping so repeated citations are not collapsed into one issue.
6. Keep raw stable issue IDs unchanged in their original records. The current chat renderer does not print those issue IDs; do not add redundant opaque IDs to every prompt merely for a new internal convenience. If the chosen compact representation uses them, justify the token tradeoff and retain every one.
7. Do not cap the location list to the first N locations, truncate remedies, or hide low-severity findings.
8. For malformed/unrecognized input or non-eligible rules, preserve the existing singleton rendering/fallback behavior. Do not drop an issue because it cannot be grouped.
9. Preserve the advisory framing and current source-content delimiter neutralization.
10. Avoid mutating any input issue dictionary/list. The same records also serve the UI and API.

An illustrative grouped rendering is:

```text
- [unrecorded_edition] <exact existing explanation and remedy>
  Affected citations (3): 1.1.A (element pt1.a1.p1); 1.1.B (element pt1.a1.p2); 1.2.A (element pt1.a2.p1).
```

Use the actual reference and ID formats; the example is not a new numbering specification. Keep singleton rendering byte-for-byte identical where practical. For each eligible repeated group, compare the candidate with its legacy individual lines and use grouping only if it is strictly shorter in characters; otherwise retain the legacy lines. This is a deterministic no-growth guard, not a provider-token measurement. Do not include `match` in the key: equivalent citation spellings with the same remedy can group, while their original `match` fields remain untouched in raw records. Confirm the compact representation is shorter on the targeted repeated-message fixtures before enabling it.

### D3. Integration constraints

- Continue building current context once per user turn and reusing it through that turn's continuations.
- Preserve stripping of transient context at commit.
- Preserve the stable system prompt and current committed-history/tail cache breakpoints.
- Do not move context ahead of history, insert synthetic roles, or add a new cache breakpoint in this package.
- Do not change the QC input manifest: this is chat-only rendering, not a change to what Final QC receives.
- Retain the model's authority framing: the current document and current findings still arrive in the current user-turn context.

### D4. Proposed new tests

| Scenario | Required proof |
|---|---|
| Many identical edition messages across elements | Wording is grouped, every original location is represented, and output is shorter |
| Same standard with two cited years | Separate remedy groups survive |
| Same standard/rule with differing message or severity | No incorrect merge |
| Two physical citations in one paragraph | Occurrence multiplicity survives |
| Mixed edition and non-edition issues | Non-eligible rules retain their existing rendering and no issue disappears |
| Singleton-only input | Existing presentation is unchanged where promised |
| Empty input | No spurious lint block |
| Missing optional fields | Existing fallback is preserved; no crash or silent omission |
| Repeated call and input snapshot | Deterministic output; no mutation of raw records |
| Eligible short messages where grouping overhead is larger | Fallback preserves the shorter legacy representation |
| Hostile delimiter text in message/reference | Whole context still neutralizes closing/injected framing |
| User turn with continuation | Same frozen grouped context through the turn; no duplication into history |

Helper tests may inspect structured intermediate data, but at least one fake-client integration test must inspect the final emitted model-request text. Verify every source location and physical-occurrence multiplicity survives the final rendering, the raw API lint payload is unchanged, readiness still reports the same occurrence population, and committed history does not acquire the snapshot. Complete intermediate data is insufficient if the renderer later drops locations. Do not merely assert a hard-coded count of newline characters.

### D5. Existing regression pins to retain

- `tests/test_linting.py::test_stale_edition_detected_in_three_citation_shapes`
- `tests/test_linting.py::test_unrecorded_edition_fires_on_the_engine_citation_shapes`
- `tests/test_linting.py::test_recording_the_edition_silences_the_rule`
- `tests/test_linting.py::test_overlapping_designation_forms_are_not_double_reported`
- `tests/test_linting.py::test_three_identical_siblings_report_two_findings_not_three`
- `tests/test_app.py::test_chat_turn_emits_lint_event_and_payloads_carry_standards`
- `tests/test_app.py::test_context_block_never_fossilizes_into_history`
- `tests/test_app.py::test_a_turns_cached_prefix_is_a_byte_prefix_of_the_next_request`
- `tests/test_app.py::test_the_tail_is_written_at_the_short_ttl_the_boundary_at_the_long_one`
- `tests/test_app.py::test_continuation_rounds_keep_their_own_tail_breakpoint`
- `tests/test_app.py::test_the_history_boundary_fails_safe_if_sanitizing_ever_moves_messages`
- `tests/test_app.py::test_sanitizing_a_request_never_adds_or_drops_a_message`
- `tests/test_app.py::test_no_breakpoint_survives_into_history_or_a_saved_project`
- `tests/test_reference_docs.py::test_document_text_cannot_forge_the_context_boundary`
- `tests/test_qc_context.py::test_an_edit_after_the_review_flips_the_block_stale`

Existing raw-lint tests should still pass if prompt compaction is removed, because their contract is intentionally unchanged. New rendering tests must detect removal of compaction or loss of locations. State this distinction in the execution report.

### D6. Acceptance and rollback

- On documented repeated-edition fixtures, exact prompt characters decrease while actionable information remains complete.
- No raw lint/API/UI/readiness behavior changes.
- No prompt/context-history/cache-layout invariant regresses.
- Report actual savings in characters and an explicitly labeled token estimate. Do not claim an observed reduction in provider spending without provider usage.

Rollback is the renderer call-site change; the original raw lint records remain intact. No project migration or data repair should be necessary.

## 8. Package E — reproducible evidence and offline usage analysis

### E1. Deliverables and scope

Provide a small developer-facing measurement utility, preferably under `tools/`, and a written findings report under `docs/review-results/`. Suggested filenames are `tools/review_cost_analysis.py` and `docs/review-results/<date>/measurements.md`; they are proposed additions, not existing tools.

Default execution must be offline and must not call `get_client`, initialize a live application/session server, make model requests, open private documents beyond explicit input paths, or scan the user's profile for data. Printing to stdout is the default; writing output requires an explicit output path.

Use existing domain constructors and renderers. Keep fixtures deterministic and in memory. No new runtime dependency or production telemetry service is needed.

### E2. Context measurement fixtures

At minimum provide:

1. A documented 3-part, 8-article-per-part, 6-provision-per-article repeated-text fixture, resembling the brief's stated shape.
2. The same size with distinct provision text and repeated edition citations, eliminating synthetic duplicate-provision inflation.
3. Small and larger versions to show scaling without claiming representativeness.
4. Generic/unpinned and pinned-module cases.
5. Single and mixed cited-edition cases.
6. Selected combinations with research, retained QC, project facts, and project sections populated through valid production data shapes.

The original brief did not supply its exact generator. If it cannot be recovered from explicitly available artifacts, label the new fixture a documented reconstruction, not an exact reproduction. The contradictory arithmetic remains a correction even if the original measurements cannot be reconstructed. Freeze the injected current date/time and all project identity/state used in measurements so before/after differences are attributable to the intended change.

For each fixture record:

- Document element/provision counts and citation occurrence counts.
- Raw lint counts by rule.
- Full-outline characters.
- Actual model-facing lint characters, before and after D.
- Assembled current-context characters.
- Other context components and any joining/framing/neutralization overhead or clearly labeled residual.
- UTF-8 bytes separately if useful; do not confuse bytes, Unicode characters, and provider tokens.
- A clearly labeled character-based token estimate, with the exact conversion rule.
- Timing only as an informational measurement with runtime/hardware details, not a flaky CI performance threshold.

Reconcile the component accounting with the final rendered string. If framing transforms lengths, measure the transformed material or state the exact overhead; do not compare different serialization layers. Measure raw issue JSON separately only when clearly labeled.

Do not refactor the entire context builder merely to obtain a benchmark. Reuse the new lint helper and existing component renderers; keep any additional measurement-only decomposition small and verified.

### E3. Existing real usage records

Accept owner-designated QC JSON exports, project files, or trace files as explicit inputs. No real usage artifact was found in the reviewed repository enumeration. Missing real data is not a failure of the implementation packages and must not cause agents to invent usage or run paid QC.

Handle available records defensively:

- Prefer existing safe project/export parsers for their supported formats.
- Extract accounting metadata without executing embedded content.
- Avoid emitting document text, prompts, reference-document contents, or API credentials.
- Distinguish the session meter from persisted run records: the former resets and is not a lifetime project cost.
- Separate retained successful QC from newer attempts so the same run is not counted twice.
- Detect whether detailed request/attempt information is available; aggregate seats do not necessarily permit request-by-request causal analysis.
- Respect the new incomplete-capture metadata; old records have unknown coverage unless independent records establish it.

### E4. Cost model

Let `I` be uncached input, `W` total cache creation, `W1` its one-hour subtotal, `R` cache reads, and `O` provider output. Then five-minute creation is `W5 = W - W1`, after the existing validation policy.

Use the production pricing helpers and each persisted QC cost basis where available:

```text
token subtotal = I × input_rate
               + W5 × write_5m_rate
               + W1 × write_1h_rate
               + R × read_rate
               + O × output_rate

priced total = applicable transport-adjusted token/tool charges
```

Match the repository's actual multiplier policy and verify tool pricing against the current provider. Do not silently change production pricing while writing the analyzer. If a separate pricing defect is found, report it with evidence as a new issue.

Rules:

- `W1` is contained in `W`; never add both as separate populations of new input.
- Thinking is already included in provider output; do not charge it again.
- Batch and streaming records may have different multipliers within one run.
- Use historical rate snapshots for historical records. If unavailable, label any current-rate estimate explicitly.
- Separate observed totals from hypothetical scheduling alternatives.
- Include output, tool fees, retries, and priming work when comparing full-run costs.
- Label `cache_saved_usd` as the existing gross saving from reads relative to base input. It does not subtract the cache-write premium and is not proof that the whole cache policy is net beneficial.

Reconcile report totals to the underlying known records within existing rounding tolerance. Report mismatches; do not overwrite the source artifact to make it consistent.

### E5. Cache-compatible request grouping

When the necessary request metadata exists, group by effective model/configuration and actual compatible prefix, including tools, system, shared context, TTL placement, effort/thinking/tool-choice options, relevant provider headers, and timing.

At minimum distinguish web-enabled compliance verification from other verification. A module's textual document prefix alone is not sufficient identity.

Do not claim a full exact-prefix hash when the export does not contain all request inputs. Label such groupings as inferred/partial. Never obtain missing identity by reading a credential store or enabling deep prompt capture without a task need.

Report useful observed aggregates:

- Known cost by phase, transport, and available request group.
- Cache-read, five-minute-write, one-hour-write, uncached-input, and output token totals.
- Captured request and continuation counts, with their scope.
- Capture completeness and missing-record reasons.
- Latency where real start/end timestamps exist.

Token-weighted cache-read share can be reported as `R / (I + W + R)`, but label its scope. It is not automatically the percentage of requests that hit or the shared-prefix hit rate; suffix tokens and server-loop aggregation can affect it.

### E6. Meaningful utility tests

- Mixed TTL pricing agrees with `cache_write_split` and persisted rate snapshots.
- Mixed streaming/batch records reconcile without double discounting or double counting.
- Repeated exports of the same identified run are not counted twice.
- Unknown historical coverage remains unknown.
- Malformed files, missing optional counters, and incomplete records yield explicit errors/limitations rather than fabricated zeros.
- The synthetic unique-provision fixture does not accidentally create the repeated-sibling population it is intended to exclude.
- Fixture component lengths reconcile with the actual assembled representation.
- Offline mode cannot invoke a provider client, and output excludes source document text unless an explicit future diagnostic mode is deliberately added.

### E7. Acceptance and rollback

Deliver measurements, the exact fixture definitions/seed, limitations, and a proceed/defer conclusion for each cache idea. No specific dollar-saving target is required or assumed.

If real data is absent, deliver the working offline analyzer and synthetic scaling report, and explicitly leave real cache behavior undecided. Removal of a developer-only analyzer must not affect runtime behavior or saved projects.

## 9. Package F — documentation, integration, and delivery

### F1. Documentation changes

The integrator owns edits to shared documents. Each implementation owner supplies a concise factual entry with actual test evidence.

1. Append a new `CLAUDE.md` section covering incomplete batch accounting, partial-result preservation, metadata compatibility, ledger ownership, and why no background reconciliation was added.
2. Append the lint-rendering contract: raw per-occurrence records remain authoritative; exact repeated wording may be compacted for chat with complete location coverage.
3. Append errata for the earlier universal-verifier-cache-prefix claim, historical research-context cap wording, and cost examples where they actually occur. Link the superseded sections rather than rewriting history.
4. Update current README descriptions of cost reporting and any affected user-visible behavior.
5. Qualify DOCX preservation claims only on surfaces that actually overstate the guarantee. Preserve the tracked-changes/Accept-All exception and the distinction between parsed-element preservation and legacy lexical patching. Do not alter DOCX algorithms in this package.
6. Correct test-file counts only where currently published and worth retaining; prefer avoiding brittle prose counts when they add no useful contract.
7. Record measured results separately from the original brief. Do not edit the file in Downloads.
8. Inspect `TrustDeepDiveModal.tsx` only for claims changed by this work. Correct affected assertions; do not rewrite the entire trust document.
9. Do not modify `requirements.txt` just to show activity if dependencies did not change. State that no dependency update was required.
10. Do not edit an already released `ReleaseNote` entry. If packaging a new release, append a new entry and update all existing version surfaces together using the repository's release convention.

Avoid model identifier strings in new commit messages, PR descriptions, comments, and documentation added by this work. Refer to configured interview/research/QC roles where an identifier is unnecessary. Preserve existing executable configuration constants as required by the application.

### F2. Verification sequence

Run focused tests after each package. Once integrated, run the complete backend suite, lint gate, frontend tests, and frontend build once more. Repeat broad checks only when a subsequent change or unresolved failure justifies it.

Suggested focused backend set for B/C:

```powershell
& .\venv\Scripts\python.exe -m pytest -q tests/test_qc_batch_verification.py tests/test_qc_audit_report.py tests/test_qc_runner_audit_integrity.py tests/test_usage.py
```

Suggested focused backend set for D:

```powershell
& .\venv\Scripts\python.exe -m pytest -q tests/test_linting.py tests/test_app.py tests/test_qc_context.py
```

Add relevant existing session, tutorial, persistence, tracing/redaction, and report tests based on the exact final touched paths. Run new utility tests for E. Do not treat the suggested sets as permission to omit a directly affected existing contract.

Final gates:

```powershell
# Repository root.
& .\venv\Scripts\python.exe -m pytest -q -ra
& .\venv\Scripts\python.exe -m ruff check .

# frontend/.
npm test
npm run build
```

Use the actual verified Python interpreter if its directory is different. If a release version is changed, also run `packaging/windows/check_release_version.py` with that interpreter.

Visual verification is targeted: cost disclosures at normal and narrow UI widths, and changed Word memo layouts for complete/incomplete/legacy records. If a relevant skill is available in the execution environment, use its artifact-rendering workflow. This plan itself creates no Word artifact.

### F3. Mutation evidence

The repository asks which tests go red if a mechanism is reverted. Supply actual evidence for the correctness mechanisms, not an invented count:

- Temporarily restore whole-iterator materialization in an isolated change: the partial-results preservation test should fail.
- Temporarily suppress missing-charge metadata/propagation: the zero-known-usage warning and persistence tests should fail.
- Temporarily restore the single QC category display: the batch-only or mixed-category test should fail.
- Temporarily omit a grouped lint location or merge differing remedies: the losslessness tests should fail.

Use a safe temporary patch in an isolated checkout or carefully restore only the owned change. Never use a destructive reset of the user's working tree. After restoring the intended implementation, rerun affected tests. Record exact test names and outcomes; do not require a broad mutation campaign unrelated to these changes.

### F4. Release/merge boundaries

Prepare reviewable commits or a draft PR according to the owner task's authorization. This plan does not request publishing a release, pushing private data, installing a packaged application, or running paid experiments.

Keep independently reversible logical changes:

1. Financial-capture contract, incremental retrieval, and backend regression tests.
2. Cost presentation, memo compatibility, and frontend tests.
3. Lint rendering and regression tests.
4. Offline measurements and corrected documentation.

Some dependencies may require a combined integration commit; do not split a backend/UI contract into a deployable broken intermediate state. Do not commit failing tests as the final delivered state.

### F5. Final handoff from implementation agents

Deliver:

- The concrete behavior before and after each change.
- Changed files and the reason for each change.
- Actual test results and skip reasons, including the restored-green mutation checks.
- Synthetic measurements with fixtures and estimate labels.
- Any real usage conclusions, with artifact provenance and privacy-preserving summaries.
- Compatibility decisions for saved reports, session reset, schema/protocol, cost basis, and input fingerprint.
- Known limitations, especially unavailable final charges after cancellation and absent real cache telemetry.
- Explicit deferred work. Do not call deferred cache experiments implemented.
- A clean account of remaining workspace changes and rollback considerations.

## 10. Package G — conditional cache investigations, not production defaults

No item below is required to complete packages A-F. Do not let an interesting cache redesign delay the confirmed accounting and presentation fixes.

### G1. Staggered phase-one launch

Consider only if real measurements show meaningful simultaneous cache writes among otherwise compatible requests.

An experiment would start one request and release compatible siblings after an observed provider response-start event, not an assumed two-second delay. Include failure-to-start, cancellation, timeout, and non-compatible tool groups. Measure wall-clock delay as well as total cost, including the priming request.

Do not serialize complete lens responses or wait indefinitely for the first text token. An event identifying response commencement is distinct from user-visible text, especially with thinking/tool work.

Go/no-go: retain the existing scheduler unless measured benefit justifies the additional coordination and its tested failure handling.

### G2. Batch cache reuse

First inspect real per-seat usage and distinguish compatible request groups. Any paid comparison needs a written request count, synthetic payload, output allowance, timeout, maximum spend, and cancellation/cleanup plan before the required owner authorization.

Compare actual total cost for equivalent semantic work, including output, retries, priming, and all cache-write TTLs. Account for queue delay and expiration. One priming request or a split batch does not guarantee a later batch executes while its prefix is warm.

Do not use the existing `tools/qc_verifier_canary.py` as evidence of cache behavior. It is a bounded schema-acceptance check. Do not silently expand its paid scope.

### G3. Client continuation caching

The research and QC engines append paused assistant content and sanitize it before resending. A cache experiment must distinguish server-internal cached iterations from client continuation requests.

Request-level automatic caching may avoid altering the assistant block objects, but support alone does not prove a useful hit. Verify SDK compatibility, eligible trailing blocks, TTL ordering, breakpoint limits/lookback, stable prefix identity, container handling, pairing protections, and PDF sanitization.

A proposed implementation must preserve paused content and test actual request shapes. Do not attach cache metadata to thinking blocks indiscriminately. Do not call this saving confirmed without usage evidence.

### G4. Slow-changing assets before history

Remain deferred unless measurements show a worthwhile repeated cost after lint compaction.

If pursued later, the design must account for:

- Research/fact/standard edits, QC freshness and dispositions, and latest-attempt notes.
- Rewriting the cached history suffix whenever leading assets change.
- Authority conflicts between current state moved earlier and older dialogue that follows it.
- Role alternation, synthetic prefix offsets, committed-history boundary arithmetic, and context stripping.
- Input framing and delimiter neutralization for every new data frame.
- Cache expiration and the four-breakpoint constraint.

Never memoize QC context solely by run ID. Never truncate the full document or location lists under the label of a cache-only change.

### G5. Evidence threshold and paid-work boundary

An experiment proposal is concrete only when it states inputs, comparisons, expected observations, error cases, budget, and stopping conditions. The owner should be able to approve the experiment itself, not a vague request to “test caching.” Existing local source/fake tests may proceed within implementation authorization; new billed provider work needs the applicable explicit authorization.

If evidence is unavailable or benefits are marginal, document the conclusion and keep the existing production behavior.

## 11. Agent assignments and handoff instructions

### Agent 1 — accounting backend owner

Implement B after A. Own engine/runner/accounting changes and related backend tests. Establish the additive serialized contract before C begins. Preserve verdict and schema semantics. Coordinate any `conversation.py` edits with Agent 3; its generation-guard method is separate from chat context rendering.

Required report: baseline failure scenarios, exact financial-capture meaning, persistence/ledger propagation, tests, mutation evidence, fingerprint decision, and remaining inability to know uncollected provider charges.

### Agent 2 — presentation owner

Implement C. The category-sum correction can begin immediately after baseline. Wait for B's metadata shape before finalizing disclosure. Own frontend types, report helper, cost surfaces, and frontend tests. Coordinate changes to `backend/spec_doc/docx_export.py` and its tests with Agent 1.

Required report: complete list of affected cost assertions, complete/incomplete/legacy examples, frontend validation, and Word memo visual verification or a clearly stated renderer limitation.

### Agent 3 — lint and measurement owner

Implement D and E. Own the chat lint renderer and offline measurements. Preserve raw lint records and prompt/history invariants. Coordinate with Agent 1 before touching the shared conversation module. Do not implement G as an opportunistic extension.

Required report: losslessness contract, mixed-edition and same-paragraph occurrence tests, actual before/after fixture measurements, offline analyzer behavior, and a data-based proceed/defer assessment.

### Integrator/reviewer

Own A/F, shared docs, final API-contract review, compatibility fixtures, full test/build gates, and final handoff. Keep the three agents' implementation scopes independent. If using worktrees, follow repository branch conventions and merge without overwriting unrelated work.

Review these failure modes specifically:

1. Financial metadata silently treated as token counts.
2. Old reports silently declared financially complete.
3. Whole-result aggregates double-counted after incremental capture.
4. Stop delayed by result reconciliation.
5. Canceled findings installed as actionable after late completion.
6. Session reset or tutorial merge losing/misattributing warnings.
7. Metadata-only changes invalidating paid QC through fingerprints.
8. Lint grouping losing a location or merging different edition remedies.
9. Synthetic duplicate findings inflating claimed production savings.
10. Provider aggregate cache metrics mistaken for exact per-request causal evidence.

### Copyable execution prompt for the lead implementation agent

```text
Implement the recommended packages A-F in this plan against the current repository,
revalidating the cited baseline and symbols first. Preserve unrelated work. Start
with meaningful reproductions of the accounting and display defects, then deliver
the bounded accounting/disclosure fix, correct cost presentation, lossless chat-only
edition-lint compaction, offline measurements, and documentation. Delegate bounded
independent packages and coordinate shared files. Do not change model routing,
effort, panel sizes, full-document context, raw lint records, adjudication, or cache
defaults. Do not add background batch reconciliation or run paid cache experiments.
Maintain legacy reports, current schema rules, session/run isolation, and existing
QC freshness semantics. Use actual test and measurement evidence; explicitly name
unavailable real usage data and visual checks. Finish with integrated passing
gates, reviewable changes, a compatibility/rollback account, and deferred work.
```

## 12. Definition of done

- [ ] Actual baseline recorded; environment discrepancy resolved or explicitly isolated.
- [ ] Partial batch result retrieval preserves observed usage.
- [ ] Stop, timeout, missing rows, and unreadable results disclose unavailable charges.
- [ ] Empty known usage cannot suppress the disclosure.
- [ ] No tokens, costs, or request identities are fabricated.
- [ ] Known costs reconcile exactly under the existing pricing/rounding contract.
- [ ] No duplicate charges from results, retries, runner totals, or workspace merges.
- [ ] Stop responsiveness, partial-attempt retention, and successor isolation hold.
- [ ] Saved report/project and legacy readers preserve accounting meaning.
- [ ] Metadata-only changes do not alter QC input fingerprints or adjudication.
- [ ] QC drawer includes streaming and batched categories.
- [ ] Relevant cost surfaces agree about missing financial capture.
- [ ] Edition-lint prompt text is shorter on documented repeated-message fixtures.
- [ ] Every actionable lint occurrence/location remains represented.
- [ ] Raw lint records, navigation, readiness, and full-document context remain intact.
- [ ] Offline measurements distinguish actual strings, estimates, and real usage.
- [ ] Corrected documentation preserves historical entries and names limitations.
- [ ] Focused tests, integrated full gates, and required visual checks are reported accurately.
- [ ] Actual mutation evidence identifies the tests protecting the new mechanisms.
- [ ] No unapproved paid experiments, releases, unrelated refactors, or new dependencies.
- [ ] Final report distinguishes completed fixes from deferred cache investigations.
