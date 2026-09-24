# Tier 1 finish — tracker

<!-- TIER1-FINISH-STATUS: IN PROGRESS -->

**Next session:** WL-2 — Turn the warm lead on

Owner: Abraham. Opened 2026-09-24.

**This file is the only record of where the "Tier 1 finish" program
stands.** A chat, a pull request description or a commit message is not.
The two specs are:

- [`TIER1_FINISH_CHUNK4_CONTINUATION_TAIL_PLAN.md`](TIER1_FINISH_CHUNK4_CONTINUATION_TAIL_PLAN.md):
  sessions CT-1, CT-2 and CT-3 (Tier 1 Chunk 4, the continuation tail).
- [`TIER1_FINISH_CHUNK3_WARM_LEAD_PLAN.md`](TIER1_FINISH_CHUNK3_WARM_LEAD_PLAN.md):
  sessions WL-1 and WL-2 (Tier 1 Chunk 3, the warm lead).

The closeout, FIN-1, is specified here, at the end of this file.
`tests/test_tier1_finish_tracker.py` checks this file's shape on every pull
request, so a malformed edit fails CI rather than misleading the next
session.

A new session starts from [The handoff prompt](#the-handoff-prompt), and
then follows the [Session procedure](#session-procedure) step by step.

## What this program is

Tier 1 of the research and Final QC cost program
(`docs/plans/RESEARCH_QC_COST_TIER1_2026-09-23.md`) built two savings that
shipped **off**:
- Chunk 4, the continuation tail (`BUILD_A_SPEC_CONTINUATION_CACHE`);
- Chunk 3, the warm lead (`BUILD_A_SPEC_QC_BATCH_WARM_LEAD`).

Each was to flip on only after a measured trial (M3) that Abraham will not
run (the Tier 1 progress file's O6). This program finishes both without
that trial (FD1). It adds runtime self-checks that watch the runs the app
makes anyway and can only switch a saving **off**, and then turns each
saving on, one pull request per session:

1. **CT-1** makes a provider refusal of the tail harmless.
2. **CT-2** measures what the tail saves, and switches it off on a proven
   loss.
3. **CT-3** turns the tail on.
4. **WL-1** checks that the batch reads the warm lead, and switches the
   lead off when it does not, or when the lead lost money.
5. **WL-2** turns the warm lead on.
6. **FIN-1** updates the root `CLAUDE.md` and `README.md`, which no earlier
   session touches (FD2), and declares the program complete.

## Status

| ID | Plan | Title | Status | PR | Merge commit |
|---|---|---|---|---|---|
| CT-1 | Chunk 4 | Survive a rejected continuation tail | done | PR #224 | `cd3ce86` |
| CT-2 | Chunk 4 | Measure what the continuation tail saves | done | PR #225 | `66c10d8` |
| CT-3 | Chunk 4 | Turn the continuation tail on | done | PR #226 | `14c1185` |
| WL-1 | Chunk 3 | Check that the batch reads the warm lead | done | PR #227 | `4eb1d70` |
| WL-2 | Chunk 3 | Turn the warm lead on | not started | — | — |
| FIN-1 | both | Close out: root docs and the finish line | not started | — | — |

### What each status means

- **not started**: nothing for this session has merged.
- **done**: the session's work is on `master`.
  - The session's own pull request sets it, as that PR's last change, with
    the PR number in the PR column.
  - The row reaches `master` only when the PR merges, so on `master`
    "done" always means merged.
  - The next session fills in the merge commit in its reconcile step,
    before it ticks an item or marks itself blocked. FIN-1's own merge
    commit stays blank, because no session comes after it.
- **blocked**: the session cannot be built safely as specified.
  - The row names the pull request that records why, with code evidence.
  - Abraham decides what happens next. A later session never works around
    a blocked one.

Rows change in order: every `done` row comes before any other row, at most
one row is `blocked`, and everything after the first row that is not done
is `not started`. An open pull request is not a status: the reconcile step
finds it on GitHub.

## Checklists

Each item mirrors the acceptance criterion with the same ID in its plan
(FIN-1's are in this file). The test checks that the two lists match.
- Tick an item (`- [x]`) only when it is true on your branch, and append
  `— evidence:` with where it is proven: a test name, a file, a command's
  output, or an As built item.
- A `done` session has every item ticked, with evidence.
- Only the session in progress may have ticks while its row is not done.

### CT-1 — Survive a rejected continuation tail

- [x] CT-1.1 — the `cost_checks` leaf module with one tail latch per engine — evidence: `backend/cost_checks.py`; `test_cost_checks_is_a_leaf_both_engines_share`, `test_every_read_and_write_takes_the_one_lock`, `test_the_first_latch_wins_and_logs_one_warning`
- [x] CT-1.2 — research re-sends a rejected tail-bearing continuation once, without the tail — evidence: `test_a_refused_continuation_is_sent_again_once_without_the_tail[research]`
- [x] CT-1.3 — the same guard in Final QC's `_run_streaming_call`, counting both requests — evidence: `test_a_refused_continuation_is_sent_again_once_without_the_tail[qc]`, `test_a_refused_streamed_verifier_seat_is_sent_again`, `test_a_refused_lens_record_still_reconciles_and_reloads`
- [x] CT-1.4 — latch unless the tail-free resend is itself rejected with a 400 — evidence: `test_a_resend_refused_too_fails_as_before_and_latches_nothing`, `test_a_resend_that_fails_another_way_latches_then_resumes_without_the_tail` (both engines)
- [x] CT-1.5 — only a tail-bearing stream open is guarded — evidence: `test_prompt_too_long_is_not_the_tails_refusal`, `test_a_400_on_a_request_without_the_tail_takes_todays_path`, `test_an_error_after_the_stream_opened_is_not_intercepted` (both engines); `test_the_helper_lets_a_failure_inside_the_stream_through_untouched`
- [x] CT-1.6 — the latch is read on every request and touches nothing else (F3 test) — evidence: `test_the_latch_is_read_on_every_request_after_the_switch`, `test_after_the_latch_a_later_call_sends_exactly_a_switch_off_calls_requests`, `test_a_latch_reaches_a_request_another_thread_builds_next`, `test_a_retained_result_stays_current_after_the_latch`
- [x] CT-1.7 — the `cost_checks` diagnostics block, which survives `scrub_data` — evidence: `test_diagnostics_report_the_latch_and_survive_the_scrub`
- [x] CT-1.8 — the conftest resets the latches around every test — evidence: `tests/conftest.py`; `test_the_conftest_resets_the_latches_before_and_after_every_test`, and the pair `test_a_latch_left_set_on_purpose` / `test_the_next_test_starts_with_both_latches_clear`
- [x] CT-1.9 — `tests/test_cost_checks_tail_rejection.py`, with the switch explicit in every test — evidence: the file's 38 tests; its run helpers take `continuation_cache` as a required keyword
- [x] CT-1.10 — the switch still ships off — evidence: `backend/settings.py` untouched; `test_continuation_cache_ships_switched_off` unchanged and green
- [x] CT-1.11 — verified: ruff, pytest, npm test, npm run build — evidence: the plan's CT-1 As built, "Verification"
- [x] CT-1.12 — revert matrix recorded — evidence: the plan's CT-1 As built, "Revert matrix"
- [x] CT-1.13 — As built with For FIN-1; root CLAUDE.md and README.md untouched — evidence: the plan's CT-1 As built and its For FIN-1 list; `git diff --name-only origin/master...HEAD -- CLAUDE.md README.md` prints nothing

### CT-2 — Measure what the continuation tail saves

- [x] CT-2.1 — a public rate accessor in the ledger, and an observer that never raises — evidence: `usage_ledger.model_rates`; `test_the_rates_come_from_the_ledger`, `test_malformed_usage_raises_nothing_and_measures_nothing`
- [x] CT-2.2 — first-iteration usage: `usage.iterations`, a single iteration, or none — evidence: `test_iterations_are_read_as_dicts_and_as_objects`, `test_the_top_level_usage_stands_in_only_when_no_server_tool_ran`, `test_a_response_that_ran_server_tools_without_iterations_is_unmeasured`
- [x] CT-2.3 — exact, bound and unmeasured observations as in Appendix A — evidence: `test_an_exact_observation_measures_what_the_tail_read_and_wrote`, `test_an_expired_opening_entry_is_not_charged_to_the_tail`, `test_a_bound_credits_every_read_and_charges_every_write`, `test_final_qc_is_priced_on_its_own_model`, `test_a_bound_with_nothing_read_is_skipped`; the plan's CT-2 As built, deviations 1 to 5
- [x] CT-2.4 — latch `unprofitable` after six or more observations summing below zero — evidence: `test_five_losing_observations_do_not_latch_and_the_sixth_does`, `test_unmeasured_observations_never_count_toward_the_six`, `test_a_sum_at_or_above_zero_never_latches`, `test_the_latch_persists_however_many_winning_observations_follow`
- [x] CT-2.5 — both engines observe exactly the responses to tail-bearing requests — evidence: `test_a_paused_research_dimension_observes_each_continuation`, `test_a_paused_compliance_lens_observes_its_continuations`, `test_a_streamed_web_tooled_seat_observes_its_continuation`, `test_a_tail_free_resend_is_never_observed` and `test_an_opening_response_is_never_observed` (both engines)
- [x] CT-2.6 — the measurement changes nothing and raises nothing — evidence: `test_the_measurement_is_invisible`, `test_malformed_usage_raises_nothing_and_measures_nothing`
- [x] CT-2.7 — diagnostics counts, and the Developer tools "Cost self-checks" row — evidence: `test_diagnostics_report_the_counts_and_survive_the_scrub`; `frontend/src/lib/costChecks.ts` and `frontend/tests/costChecks.test.ts`
- [x] CT-2.8 — the backend and frontend tests, registered — evidence: `tests/test_cost_checks_tail_value.py` (29 tests, 32 with parametrized cases); `frontend/tests/costChecks.test.ts` (9 tests), in `frontend/package.json`'s test script
- [x] CT-2.9 — the switch still ships off — evidence: `backend/settings.py` untouched; `test_continuation_cache_ships_switched_off` unchanged and green
- [x] CT-2.10 — verified: ruff, pytest, npm test, npm run build — evidence: the plan's CT-2 As built, "Verification"
- [x] CT-2.11 — revert matrix recorded — evidence: the plan's CT-2 As built, "Revert matrix"
- [x] CT-2.12 — As built with For FIN-1 and the `usage.iterations` findings; root files untouched — evidence: the plan's CT-2 As built, its `usage.iterations` findings and its For FIN-1 list; `git diff --name-only origin/master...HEAD -- CLAUDE.md README.md` prints nothing

### CT-3 — Turn the continuation tail on

- [x] CT-3.1 — the flip-readiness run, with the switch on from the environment — evidence: the plan's CT-3 As built, "Flip readiness": 1 failed, 2943 passed, 64 skipped; the one failure fixed and named (`test_qc_verifier_v3.py`'s `_run` passes the switch explicitly)
- [x] CT-3.2 — `CONTINUATION_CACHE` defaults to `True`, and its comment says why — evidence: `backend/settings.py`; the plan's CT-3 As built, "What was built"
- [x] CT-3.3 — `test_continuation_cache_ships_switched_on` replaces the ships-off pin — evidence: `tests/test_continuation_cache.py::test_continuation_cache_ships_switched_on` (the same `ast` walk over the source)
- [x] CT-3.4 — every claim outside the root files that the tail is off is true again — evidence: `docs/RELEASE_WINDOWS.md` (with the Cost self-checks and Switching it off rows), `TrustDeepDiveModal.tsx`'s Research and Final QC cards, both engines' comments, `backend/cost_checks.py`; the plan's CT-3 As built, "Copy (R9)" and deviation 3
- [x] CT-3.5 — the Tier 1 plan's §7 item 4 is always included — evidence: `docs/plans/RESEARCH_QC_COST_TIER1_2026-09-23.md` §7, "Which items" and item 4; no version bump, `release_notes.py` entry or tag
- [x] CT-3.6 — verified: ruff, pytest, npm test, npm run build — evidence: the plan's CT-3 As built, "Verification"
- [x] CT-3.7 — reverting the default turns the new pin red — evidence: the plan's CT-3 As built, "Revert matrix", first row
- [x] CT-3.8 — As built with For FIN-1; root CLAUDE.md and README.md untouched — evidence: the plan's CT-3 As built and its For FIN-1 list; `git diff --name-only origin/master...HEAD -- CLAUDE.md README.md` prints nothing

### WL-1 — Check that the batch reads the warm lead

- [x] WL-1.1 — the warm-lead latch, and leads picked only while it is clear — evidence: `backend/cost_checks.py` (`warm_lead_enabled`, `disable_warm_lead`, `check_warm_leads`) and the gate in `_run_batch_calls`; `test_a_losing_lineage_latches_once_with_one_warning`, `test_the_first_losing_lineage_sets_the_reason`, `test_every_read_and_write_takes_the_one_lock_once`, `test_many_threads_check_and_one_latches`, `test_the_latch_stops_the_next_phase_picking_a_lead`
- [x] WL-1.2 — the check runs only at a phase's normal end, for leads that sent a request — evidence: `test_the_check_reads_the_lead_only_after_it_is_joined`, `test_a_phase_nobody_finished_is_never_measured`, `test_an_id_less_submission_is_never_measured`, `test_the_wall_clock_ceiling_is_never_measured`, `test_a_stop_during_the_lead_wait_is_never_measured`, `test_a_lead_that_sent_nothing_is_not_measured_on_a_normal_end`; the plan's WL-1 As built, deviation 2
- [x] WL-1.3 — per-seat first-iteration read share; unmeasured seats counted — evidence: `test_a_seat_read_the_prefix_at_95_percent_and_not_below`, `test_what_cannot_be_read_is_unmeasured_and_nothing_raises`, `test_a_searched_seat_is_measured_from_its_reported_first_iteration`, `test_a_paused_seat_is_measured_from_its_first_response`, `test_a_retried_seat_is_measured_only_by_its_first_batch`, `test_a_refused_submission_is_not_a_seats_first_batch`, `test_a_web_tooled_lineage_that_searched_without_iterations_is_unmeasured`; the plan's WL-1 As built, deviation 12
- [x] WL-1.4 — h₁, p, C and h₀* with at least 8 measured seats; one INFO line per lineage — evidence: `test_h1_p_c_and_the_break_even_by_hand`, `test_p_is_the_median_of_the_measured_prefixes`, `test_fewer_than_eight_measured_seats_give_no_verdict`, `test_the_rates_come_from_the_ledger`, `test_one_info_line_per_lineage_with_numbers_only`
- [x] WL-1.5 — `not_read` below 0.5, `unprofitable` at h₀* ≤ 0 — evidence: `test_not_read_below_half_and_kept_at_half`, `test_an_expensive_lead_is_unprofitable`, `test_a_break_even_of_exactly_zero_is_unprofitable`, `test_a_batch_that_writes_the_prefix_switches_the_lead_off_for_the_next_run`, `test_an_expensive_lead_on_a_lightly_read_lineage_is_unprofitable`
- [x] WL-1.6 — the check changes nothing and raises nothing — evidence: `test_the_check_changes_no_request_record_multiplier_meter_event_or_manifest`, `test_a_failure_inside_the_check_records_nothing`, `test_gathering_for_the_check_never_fails_the_phase`, `test_what_cannot_be_read_is_unmeasured_and_nothing_raises`
- [x] WL-1.7 — diagnostics and the Developer tools row — evidence: `test_diagnostics_report_the_last_check_and_survive_the_scrub`, `test_a_lineage_the_batch_reads_is_kept_and_diagnostics_record_it`; `frontend/src/lib/costChecks.ts` and its eight new tests in `frontend/tests/costChecks.test.ts`
- [x] WL-1.8 — `tests/test_cost_checks_warm_lead.py` and the frontend cases — evidence: `tests/test_cost_checks_warm_lead.py` (43 tests, 45 with parametrized cases); `frontend/tests/costChecks.test.ts` (17 tests, 8 of them new), already in `frontend/package.json`'s test script
- [x] WL-1.9 — the switch still ships off — evidence: `QC_BATCH_WARM_LEAD` still defaults to `False` in `backend/settings.py` (only its comment changed); `test_warm_lead_ships_switched_off` unchanged and green
- [x] WL-1.10 — verified: ruff, pytest, npm test, npm run build — evidence: the plan's WL-1 As built, "Verification"
- [x] WL-1.11 — revert matrix recorded — evidence: the plan's WL-1 As built, "Revert matrix" (51 rows, 51 red)
- [x] WL-1.12 — As built with For FIN-1; root CLAUDE.md and README.md untouched — evidence: the plan's WL-1 As built and its For FIN-1 list; `git diff --name-only origin/master...HEAD -- CLAUDE.md README.md` prints nothing

### WL-2 — Turn the warm lead on

- [ ] WL-2.1 — both flip-readiness runs, with the number of phases that picked a lead
- [ ] WL-2.2 — `QC_BATCH_WARM_LEAD` defaults to `True`, and both comments say why
- [ ] WL-2.3 — `test_warm_lead_ships_switched_on` replaces the ships-off pin
- [ ] WL-2.4 — every claim outside the root files that the lead is off is true again
- [ ] WL-2.5 — the Tier 1 plan's §7 item 3 is always included
- [ ] WL-2.6 — verified: ruff, pytest, npm test, npm run build
- [ ] WL-2.7 — reverting the default turns the new pin red
- [ ] WL-2.8 — As built with For FIN-1; root CLAUDE.md and README.md untouched

### FIN-1 — Close out: root docs and the finish line

- [ ] FIN-1.1 — every merge commit but FIN-1's own is filled in
- [ ] FIN-1.2 — every session's For FIN-1 list is done, or its exceptions are explained
- [ ] FIN-1.3 — the root `README.md` is updated
- [ ] FIN-1.4 — the root `CLAUDE.md` is updated
- [ ] FIN-1.5 — the Tier 1 plan, the Tier 1 progress file and `docs/plans/README.md` are updated
- [ ] FIN-1.6 — verified: ruff, pytest, npm test, npm run build
- [ ] FIN-1.7 — this tracker is COMPLETE, with the banner at the top

## Rules

These bind every session. Only Abraham changes one, and the change is
recorded in the [Decision log](#decision-log).

- **R1 — Tier 1's frozen decisions still bind.** They are in the Tier 1
  plan's §3:
  - F1: no quality change.
  - F2: one request shape; anything new rides beside the request.
  - F3: a retained Final QC result stays current. Nothing new enters the
    QC input manifest, and there is no schema or protocol bump.
  - F4: the accounting reconciles.
  - F6: tests are hermetic, and a paid check is Abraham's to run.
  - F7: no version bump, no `backend/release_notes.py` entry and no git
    tag.
  - F9: one session, one pull request.

  FD1 amends F5 for these two switches. F8 (measure, don't model) is kept:
  the self-checks measure real runs, and nothing is decided from a model
  of what they would show.
- **R2 — The root `CLAUDE.md` and `README.md` are frozen until FIN-1**
  (FD2).
  - This overrides CLAUDE.md's ground rule to keep them current, and the
    docs step of the Tier 1 session procedure.
  - What they would have received goes into your session's As built, under
    For FIN-1.
  - Before every push, `git diff --name-only origin/master...HEAD -- CLAUDE.md README.md`
    prints nothing, except in FIN-1.
  - Every other document is live, and must stay true (R9).
- **R3 — No new `BUILD_A_SPEC_*` environment variable.** A new knob needs
  a README Configuration row (`tests/test_docs_consistency.py` enforces
  it), which R2 forbids. Thresholds are module constants in
  `backend/cost_checks.py`. The two existing switches remain the
  operator's off switches.
- **R4 — No new SSE event type, and no new key on a pinned payload**
  (`verification_started` and `stream_end` are pinned by exact-dict tests).
  Self-check telemetry goes to the `buildaspec.cost_checks` log, the
  diagnostics snapshot and Developer tools.
- **R5 — No paid probes.** The self-checks read only responses to requests
  the app was already sending; nothing sends a request to test the
  provider. No session makes a real API call.
- **R6 — Self-check state is process-local, in memory, and can only turn
  a saving off.**
  - It is never persisted, and never written to a project file, a project
    brief, the QC input manifest, a `QCResult`, a usage record or the
    meter.
  - An app restart re-arms it.
- **R7 — Tests are hermetic and Windows-safe.** Use events or stepped
  clocks, never real sleeps. The conftest reset clears every latch around
  every test. A new test passes the switch it exercises explicitly, so a
  flip changes none of them.
- **R8 — `backend/cost_checks.py` is a leaf** (standard library,
  `backend.settings`, `backend.usage_ledger` and `anthropic` only),
  imported by both engines. It is the one exception to copy-don't-import
  besides the ledger. Engine-side helpers, such as the stream-open guard,
  are still copied, not shared.
- **R9 — Copy is true at every merge.** This covers Help, the trust
  dossier, Developer tools, `docs/RELEASE_WINDOWS.md`, code comments and
  the Tier 1 plan's §7 drafts. The one accepted exception is R2's: between
  CT-3 or WL-2 and FIN-1, the root README may still call a switch off. The
  flip session lists that under For FIN-1.
- **R10 — Diagnostics field names survive redaction.** No key may contain
  `token` unless an `s` follows it (`backend/tracing/redaction.py`'s
  `token(?!s)`), and a test proves the fields come through `scrub_data`
  unchanged.

## Decision log

| # | Date | Decision |
|---|---|---|
| FD1 | 2026-09-24 | **Finish Chunks 3 and 4 without an owner-run measurement.** Abraham asked for implementation plans for Tier 1 Chunks 3 and 4, carried out by agent sessions one pull request at a time, and will do no measurement himself (the Tier 1 progress file's O6 stands). So this program replaces the M3 gate those chunks' flips waited on with runtime self-checks. They watch the runs the app makes anyway, and can only switch a saving off. Then each switch turns on (CT-3, WL-2). This amends Tier 1's F5 for these two switches: a switch may default on when a self-check removes its unbounded failure mode and switches it off on a measured loss, and what the check cannot measure is bounded. Abraham merges every pull request, so merging CT-3's and WL-2's is his approval of each flip, and not merging one stops it. Recorded in the Tier 1 progress file as O7. |
| FD2 | 2026-09-24 | **The root `CLAUDE.md` and `README.md` wait for the end.** Abraham: no session updates them until all work across both plans is complete. Each session writes what they would have received into its As built's For FIN-1 list, and FIN-1 folds it all in. |

## Session procedure

1. **Read** CLAUDE.md (binding), this tracker and both plans, in full. For
   background, also read the Tier 1 plan's Chunk 3 and Chunk 4 sections,
   and their As built.
2. **Reconcile.**
   1. Run `git fetch origin master`, and read this file as it is on
      `origin/master`, not as it is on an unmerged branch.
   2. List the open pull requests in `Abe-Borg/build-a-spec` whose title
      starts with `Tier 1 finish`. An open one means a session is in
      flight.
      - If your prompt asks you to finish that PR, drive it to merge.
      - Otherwise, stop and tell Abraham which PR is open and what it is
        waiting on.
   3. Fill in the merge commit of every `done` row whose cell is blank,
      from the GitHub API or `git log origin/master`. This rides your PR.
      Do it first: from the moment your session ticks an item or is marked
      blocked, the tracker test requires it.
3. **Pick the session.** Take the first row that is not `done`.
   - If it is `blocked`, stop and tell Abraham. Never work around a
     blocked session.
   - If every row is done, the program is complete: tell Abraham, and
     change nothing.
   - Your prompt names the session it expects. The tracker decides, not
     the prompt. If they disagree, say so in your first message.
4. **Build it** on the branch your session was given.
   - The plan's section for that session is the spec. Tick checklist items
     here, with evidence, as each one becomes true.
   - Record every deviation, every knowing test change and the revert
     matrix under the session's **As built** in its plan, including its
     **For FIN-1** list. Append to the spec text; never rewrite it.
   - **If the current code makes the spec unsafe, stop.**
     - Mark the row `blocked`, with the code evidence, in a PR that carries
       only that record and step 2.3's merge commits, titled
       `Tier 1 finish — <ID>: blocked`.
     - Set the Next-session line to `none — <ID> is blocked; Abraham decides`.
     - Tell Abraham.
5. **Verify** from the repository root, and fix everything before you push.
   On Windows (PowerShell):

   ```powershell
   .\.venv\Scripts\python -m ruff check .
   .\.venv\Scripts\python -m pytest -q
   cd frontend
   npm test
   npm run build
   cd ..
   ```

   On Linux or in a cloud container, use CLAUDE.md's Commands section:
   `.venv/bin/python -m ruff check .`, `.venv/bin/python -m pytest -q`,
   and `npm test` and `npm run build` in `frontend/`. If the container has
   no virtual environment, create one from `requirements.txt` first.
6. **Docs.** Change only what the session's spec lists, plus its As built.
   Never the root `CLAUDE.md` or `README.md` (R2). Unless this is FIN-1,
   check that
   `git diff --name-only origin/master...HEAD -- CLAUDE.md README.md`
   prints nothing.
7. **Commit and open the pull request.**
   - Commit in the house style: sassy where warranted, never obnoxious
     (CLAUDE.md, ground rules).
   - Push, and open a ready-for-review PR titled
     `Tier 1 finish — <ID>: <title>`.
   - Its body lists what changed, the new tests, every knowing test change
     and a summary of the revert matrix.
   - For CT-3 and WL-2, the body says near the top what merging turns on,
     for everyone running from `master`, and how to switch it off.
   - Subscribe to the PR's activity. Schedule a check-in, if your session
     can.
8. **Mark the session done, as the PR's last commit.**
   - Set its row to `done`, with `PR #<number>`. Leave its merge commit
     blank (`—`).
   - Check that every checklist item is ticked, with evidence.
   - Set the Next-session line to the next row's `<ID> — <title>`, or to
     `none — the program is complete` for FIN-1.
   - Run `tests/test_tier1_finish_tracker.py`, and push.
9. **Drive the PR to merge.**
   - Handle CI failures, review threads and merge conflicts under your
     session's standing rules.
   - Never merge it yourself: Abraham merges.
   - Never bump the version, add a `backend/release_notes.py` entry or push
     a git tag (F7).
10. **After it merges,** send the handoff (next section). For FIN-1, send
    the completion message instead
    ([When every session is done](#when-every-session-is-done)).

## After the pull request merges: the handoff

**Do this only after the merge.** You will learn of it from the PR
subscription or from a scheduled check-in. On each check-in, re-check the
PR, and re-arm the check-in until the PR is merged or closed.

Then send Abraham one message with three parts:

1. **What merged**, in one line: the session, the PR link and the merge
   commit.
2. **What comes next**, in one line. If the next session is CT-3 or WL-2,
   say plainly that merging ITS pull request turns a saving on for everyone
   running from `master`; that merge is his approval, and he can decline by
   not merging it.
3. **The prompt for the next session**, in a fenced `text` block: the
   template below, with the next session's ID and title filled in.

Three exceptions:
- **The PR closed without merging.** Say so, and give him the prompt to
  redo the same session.
- **Your session cannot learn of the merge** (it has no subscription or
  check-in tools). End your final message with the prompt anyway, under
  the heading "Next session — start only after PR #N is merged".
- **FIN-1.** There is no next session. Send the completion message
  instead.

Abraham has nothing to do between sessions except merge.

## The handoff prompt

The first session's prompt is this template, with this filled in:

`CT-1 — Survive a rejected continuation tail`

Every later prompt is the same template, with the next session filled in.

```text
Continue the "Tier 1 finish" program in this repository (Research and Final QC cost, Tier 1: finishing Chunks 3 and 4).

Read these files completely before touching code:
1. CLAUDE.md (binding; it is loaded as your project instructions)
2. docs/plans/tier1-finish/TIER1_FINISH_TRACKER.md
3. docs/plans/tier1-finish/TIER1_FINISH_CHUNK4_CONTINUATION_TAIL_PLAN.md
4. docs/plans/tier1-finish/TIER1_FINISH_CHUNK3_WARM_LEAD_PLAN.md

Then follow the tracker's "Session procedure" step by step. In short: reconcile the tracker against master and GitHub; do exactly ONE session, the one the tracker names; tick its checklist with evidence, and mark its row done as the pull request's last change; do not edit the root CLAUDE.md or README.md unless the session is FIN-1; drive the PR to merge (I merge); and only after it has merged, give me the prompt for the next session.

I expect this session to be: <ID> — <title>. (The tracker decides, not this line.)
```

## FIN-1 — Close out: root docs and the finish line

**Depends on:** every other session `done`.

**Goal.** Put everything the program built into the root `README.md` and
`CLAUDE.md`, which no earlier session touched (FD2); leave an honest record
in the Tier 1 files and the plans index; and mark this program complete, in
big letters.

1. **Reconcile.** Fill in every blank merge commit, WL-2's included. Only
   FIN-1's own stays blank.
2. **Read every session's As built**, in both plans, and work through each
   **For FIN-1** list. Do every item, or say under this section's As built
   why one is not needed.
3. **The root `README.md`.** Never shrink it (owner preference); add and
   correct.
   - In "## Research and Final QC cost (Tier 1)": the two subsections about
     the warm lead and the continuation tail say they are on by default,
     what they save, and how to switch each off (`0`). Their "to try it"
     instructions become "to switch it off".
   - Add a subsection on the cost self-checks:
     - what each one watches;
     - what switches a saving off, and that it stays off only until the
       app restarts;
     - where to see it: Settings → Developer tools → Cost self-checks;
     - that nothing is sent to test the provider.
   - The Configuration rows for `BUILD_A_SPEC_CONTINUATION_CACHE` and
     `BUILD_A_SPEC_QC_BATCH_WARM_LEAD` show the default `1`, and say what
     `0` does.
   - Then grep the README for `CONTINUATION_CACHE`, `WARM_LEAD`,
     `switched off`, `off by default` and `M3`, and fix every sentence the
     flips made false (for example, the `BUILD_A_SPEC_QC_BATCH_VERIFICATION`
     row and the Final QC transport paragraphs).
4. **The root `CLAUDE.md`.**
   - **One implemented-notes section,** placed before
     "## Source-of-truth pointers into Claude-Spec-Critic" and titled in
     the file's style (for example, "The two shelved savings are on, and
     watch themselves — implemented notes (Tier 1 finish)"). It
     consolidates the five sessions' As built material, in CLAUDE.md's
     voice:
     - why the M3 gate was replaced (FD1);
     - `backend/cost_checks.py` and its rules (R5, R6, R8, R10);
     - CT-1's guard;
     - CT-2's arithmetic and its limits;
     - WL-1's checks and their limits;
     - the two flips;
     - each session's revert matrix.
   - **The Layout** (maintained current, so edit in place):
     - new entries for `backend/cost_checks.py`,
       `frontend/src/lib/costChecks.ts` and every new test file;
     - changed entries for `backend/settings.py` (the two defaults),
       `backend/research/engine.py` and `backend/qc/engine.py` (the guard,
       the measurement hooks and the lead gate), `backend/diagnostics.py`
       (the `cost_checks` block), `backend/usage_ledger.py` (the public
       rate accessor), `frontend/src/components/DeveloperToolsModal.tsx`
       (the row) and `tests/conftest.py` (the reset).
   - **Errata.** Implemented notes are append-only, so these go in the new
     section. There is one erratum for every earlier section that says
     either switch ships off, or flips only on M3. At least:
     - "Final QC's batched phase can stream a lead seat first";
     - "A paused call reads its own cache";
     - "Research and Final QC cost, Tier 1, as shipped — implemented notes
       (closeout)".
5. **The other files.**
   - In the Tier 1 plan (`docs/plans/RESEARCH_QC_COST_TIER1_2026-09-23.md`),
     make sure §7's "Which items" includes all four items. CT-3 and WL-2
     should already have done this.
   - In the Tier 1 progress file, add a dated note under its top pointer
     saying both flips are done, with CT-3's and WL-2's PRs. In its "After
     the program" table, mark the two flip rows done by this program. Do
     not rewrite its history rows.
   - In `docs/plans/README.md`, mark this program's entry **complete**, and
     update the Tier 1 entry's sentence about the shelved flips.
   - Still no version bump, no `backend/release_notes.py` entry and no tag
     (F7). The Tier 1 release notes (all four items now) stay owed to
     whichever release next ships from `master`.
6. **Verify**, as the Session procedure's step 5 says.
7. **Mark the program complete, as the PR's last commit.** In this file:
   - Set FIN-1's row to `done` with its PR number, and leave its merge
     commit blank.
   - Tick every FIN-1 item, with evidence.
   - Change the status line from `<!-- TIER1-FINISH-STATUS: IN PROGRESS -->`
     to `<!-- TIER1-FINISH-STATUS: COMPLETE -->`.
   - Directly under the status line, add these, in this order, each on its
     own line. Blank lines may separate them; nothing else may.
     1. The opening comment `<!-- TIER1-FINISH-BANNER -->`.
     2. The completion banner below, exactly as given, in a `text` code
        block (three backticks and `text`, the banner's seven lines, three
        backticks), with no blank line inside it.
     3. The closing comment `<!-- /TIER1-FINISH-BANNER -->`.
     4. `**ALL WORK IN BOTH TIER 1 FINISH PLANS IS COMPLETE.**`
   - The Next-session line comes right after them. Set it to
     `none — the program is complete`.
   - The top of this file then reads: the title, the status line, the
     banner block, the completion line, the Next-session line.
   - Run `tests/test_tier1_finish_tracker.py`. It checks all of this.
8. **Open the PR**, and drive it to merge. Its title:

   `Tier 1 finish — FIN-1: Close out: root docs and the finish line`

   After it merges, send the completion message
   ([When every session is done](#when-every-session-is-done)).

### Acceptance

- **FIN-1.1** Every row's merge commit is filled in except FIN-1's own.
- **FIN-1.2** Every For FIN-1 item in every session's As built is done, or FIN-1's As built says why it is not needed.
- **FIN-1.3** The root `README.md` describes both savings as on by default, with how to switch each off, adds a subsection on the cost self-checks, shows `1` in both Configuration rows, and has no sentence left that the flips made false. Nothing in it was removed that is still true.
- **FIN-1.4** The root `CLAUDE.md` has one consolidated implemented-notes section for this program, with errata for every earlier section that says a switch ships off, and Layout entries for every file the program added or changed.
- **FIN-1.5** The Tier 1 plan's §7 includes all four items. The Tier 1 progress file records both flips as done, and `docs/plans/README.md` marks this program complete. There is no version bump, no `backend/release_notes.py` entry and no tag.
- **FIN-1.6** Verified: ruff, the full pytest suite, `npm test` and `npm run build` are all clean.
- **FIN-1.7** This tracker's status line is COMPLETE; directly under it sit the banner block, then the bold completion line, then the Next-session line, which reads `none — the program is complete`; and `tests/test_tier1_finish_tracker.py` passes.

### As built

*Not started.*

## When every session is done

**The completion banner** — copy it exactly:

```text
   ###    ##       ##          ########   #######  ##    ## ########
  ## ##   ##       ##          ##     ## ##     ## ###   ## ##
 ##   ##  ##       ##          ##     ## ##     ## ####  ## ##
##     ## ##       ##          ##     ## ##     ## ## ## ## ######
######### ##       ##          ##     ## ##     ## ##  #### ##
##     ## ##       ##          ##     ## ##     ## ##   ### ##
##     ## ######## ########    ########   #######  ##    ## ########
```

**The completion message.** After FIN-1's pull request merges, the
session's final message to Abraham has these parts, in order, and nothing
before them:

1. The completion banner above, in a `text` code block.
2. The heading line `# ALL WORK IN BOTH TIER 1 FINISH PLANS IS COMPLETE`.
3. **Merged:** FIN-1, with the PR link and its merge commit.
4. **Now on by default:**
   - the continuation tail (`BUILD_A_SPEC_CONTINUATION_CACHE`);
   - the warm lead (`BUILD_A_SPEC_QC_BATCH_WARM_LEAD`).

   Each is watched by its self-check (Settings → Developer tools → Cost
   self-checks), and `0` switches either one off.
5. **Still owed:** the Tier 1 release notes (the Tier 1 plan's §7, all four
   items) to whichever release next ships from `master`. Nothing else.
6. **There is no next session.**
