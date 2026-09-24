# Tier 1 finish — Chunk 3: check the warm lead against real runs, turn it on

Owner: Abraham. Opened 2026-09-24.

**Where this program stands lives in
[`TIER1_FINISH_TRACKER.md`](TIER1_FINISH_TRACKER.md) and nowhere else.**
The tracker also holds the session procedure, the rules (R1–R10), the
decision log (FD1, FD2, …), the handoff prompt and the closeout session
(FIN-1). This file is the spec for two sessions: **WL-1 and WL-2**. Its
sibling, [`TIER1_FINISH_CHUNK4_CONTINUATION_TAIL_PLAN.md`](TIER1_FINISH_CHUNK4_CONTINUATION_TAIL_PLAN.md),
is the spec for CT-1, CT-2 and CT-3. Those run FIRST: the sessions run in
the tracker's order — CT-1, CT-2, CT-3, WL-1, WL-2, FIN-1 — one session per
chat, one pull request per session. WL-1 builds on the module CT-1 creates
and the Developer tools row CT-2 adds.

> **Do not edit the root `CLAUDE.md` or the root `README.md` in any session
> of this plan** (rule R2, decision FD2). That overrides CLAUDE.md's "keep
> README and CLAUDE.md current" ground rule for as long as this program
> runs. What those two files would have received goes into your session's
> **As built** below, under **For FIN-1**, and FIN-1 folds it in once every
> session in both plans is done.

Written for coding agents that can reason. Each session states its goal,
the evidence behind its design, the code it touches, its tests and its
acceptance criteria. Where the code has moved since this was written, the
code wins: adapt, and record the difference under that session's **As
built**. Where the current code makes a design unsafe, stop and report it
with code evidence (the tracker's `blocked` state).

## Contents

1. [What this plan finishes](#1-what-this-plan-finishes)
2. [The risks, and what removes each](#2-the-risks-and-what-removes-each)
3. [The code today](#3-the-code-today)
4. Sessions — [WL-1](#wl-1--check-that-the-batch-reads-the-warm-lead) ·
   [WL-2](#wl-2--turn-the-warm-lead-on)
5. [Appendix B: the warm-lead check's arithmetic](#appendix-b-the-warm-lead-checks-arithmetic)

---

## 1. What this plan finishes

- Tier 1 Chunk 3 (PR #213, merge `d6f2c32`) added the **warm lead**. When
  one cache lineage of Final QC's batched verifier seats reaches its minimum
  (20 seats, never below 8), one seat of it is streamed FIRST, at list
  price. The batch goes out only after that seat's first output, when its
  1-hour cache entry becomes readable, so the rest of the lineage can read
  the entry instead of each seat writing its own. The spec and its As built
  are in `docs/plans/RESEARCH_QC_COST_TIER1_2026-09-23.md`, Chunk 3; the
  break-even arithmetic is its §10.3.
- It ships **off**: `BUILD_A_SPEC_QC_BATCH_WARM_LEAD` →
  `settings.QC_BATCH_WARM_LEAD`, default `False`. Its flip waited on M3, a
  measured trial Abraham was to run, because one thing is undocumented:
  **whether a batch request can read a cache entry that a separately
  streamed request wrote.**
- Abraham will not run M3, or any other measurement (the Tier 1 progress
  file's O6). Decision FD1 in the tracker replaces the M3 gate with a
  runtime self-check. It reads the usage the batch already reports, after
  every Final QC that sent a lead, and can only switch the lead **off**.
  Then the switch turns on.

The end state after WL-2:
- The switch defaults on.
- A lead that the batch visibly does not read, or that cost more than it
  could possibly have saved, switches itself off for the rest of the app
  session.
- Settings → Developer tools shows the last check's numbers.

---

## 2. The risks, and what removes each

| Risk | How bad | Removed by |
|---|---|---|
| The batch cannot read the entry the streamed lead wrote. | The lead costs its own batch discount (about half of one seat's cost, per large lineage, per run) and saves nothing. The batch also waits for the lead's first output, bounded by `QC_WARM_WAIT_SECONDS` (45 s). Bounded, and small next to the phase. | **WL-1**, the `not_read` rule. |
| The lead is expensive (a web-tooled lead that searched a lot) and the lineage barely reads. | The lead can cost more than any saving it could buy. Bounded the same way. | **WL-1**, the `unprofitable` rule. |
| The lead fails. | It is an ordinary streamed seat on the same retry policy, so a failure is an ordinary failed seat. That makes its candidate inconclusive and the run partial, exactly as a failed batched seat does. No new failure mode. | nothing to remove |
| A new request shape the provider refuses. | None. A lead sends the same request a batched seat sends (`_qc_request_kwargs`, the one builder), streamed. Its continuations carry the continuation tail only if Chunk 4's switch is on, and CT-1 guards that. | CT-1 (already done by then) |
| Nothing is wrong. | — | **WL-2** turns the switch on. |

**What WL-1 can and cannot see.** It sees h₁: how much of the lineage's
shared prefix the batched seats read, with the lead in place. It cannot see
h₀: what they would have read without a lead. Batch cache hits are
best-effort, and Anthropic quotes 30–98% as typical. So:
- A lead the batch does not read is caught.
- A lead that cost more than it could possibly have saved is caught.
- A lead that is read, but was not needed (the batch would have read the
  prefix anyway), is kept. That costs its discount, about $0.20 to $0.35
  per large lineage per run at typical sizes (Appendix B.6), and it is the
  price of not running M3.

---

## 3. The code today

All in `backend/qc/engine.py`:
- Under "A streamed lead seat warms the batch's cache (cost Tier 1,
  Chunk 3)":
  - `LINEAGE_WEB_TOOLED` / `LINEAGE_NO_WEB`;
  - `_WARM_LEAD_MIN_SEATS_WEB` / `_NO_WEB` (20 each) and
    `_WARM_LEAD_SEAT_FLOOR` (8), enforced by `_warm_lead_minimum`;
  - `_WarmLead(key, kind, lineage_size)`, `_spec_lineage_key`,
    `_spec_lineage_kind` and `_pick_warm_leads(specs)`.
- `_run_batch_calls(..., warm_leads=False, warm_wait_seconds=0.0,
  continuation_cache=False)`:
  - `leads = _pick_warm_leads(specs) if warm_leads and warm_wait_seconds > 0 else []`.
  - Each lead streams through `_run_streaming_call` on a `qc-lead` pool,
    with `first_output=released`, and `_await_leaders` waits for it.
  - The round loop batches only `batch_pending()`, which is never a lead.
  - `fold_leads(wait=)` folds a finished lead's `_CallResult` into
    `states[key].settled`.
  - `finish(status, ...)` joins the leads FIRST, then emits the terminal
    frame, then builds the outcome.
  - `results()` builds `_BatchPhaseOutcome` with `streamed_keys`: the leads
    that sent at least one request.
- **The paths that end a phase:**
  - a Stop or the wall-clock ceiling at the top of a round (`finish("cancelled" | "timeout", terminated_early=True)`);
  - a refused or id-less submission, or an unreadable results stream (`finish("failed", ...)`);
  - the settlement window (`settle_open_batch` → `finish(..., terminated_early=True)`);
  - and the normal end, `return finish("ended")` after the round loop. The
    round ceiling ends there too, having settled its unfinished seats as
    failures.
- `run_final_qc(..., batch_warm_lead=None, ...)` pins the switch once per
  run. The batch branch prices a key in `batch_phase.streamed_keys` at
  `cost_multiplier` 1.0 and every other seat at
  `settings.BATCH_COST_MULTIPLIER`.
- A seat's `_CallResult.billed` holds every billed response in order.
  `billed[0]` is its first response: for a batched seat, its request in
  the first round, which was submitted after the lead's release.
- `_sum_billed(responses)` totals usage, and
  `usage_ledger.estimate_usage_cost(model, usage, multiplier=)` prices it.

**Tests** — `tests/test_qc_batch_warm_lead.py`:
- `test_warm_lead_ships_switched_off` reads the default from the source
  with `ast`.
- Reusable helpers: `_titles`, `_lineage_scripts`,
  `_minimums_at_the_floor`, `_LeadClient`, `_watch_lead_pool`,
  `_LeapClock` and `_held_lead_client`.
- `tests/test_qc_batch_verification.py`'s `_run` pins
  `batch_warm_lead=False`, so that file describes the plain batch whatever
  the default is.

**Fakes:** `tests/fakes.py`'s `qc_verdict_response(..., tokens=...)` and
`qc_findings_response(..., tokens=...)` pass `tokens` into `usage(...)`
(`input`, `output`, `cache_read`, `cache_write`, `cache_write_1h`). The
batch fake resolves batched requests through the same scripts as the
streamed ones.

**The profiler:** `tools/qc_export_cost_profile.py` shows a lead as a
`seat:list-price:<lineage>` row.

---

## WL-1 — Check that the batch reads the warm lead

### Goal

After a batched verifier phase that ended normally and sent a lead, the
batch's own reported usage is read for each lineage that had a lead:
- how much of the shared prefix the batched seats read (h₁);
- what the lead cost (C);
- the break-even baseline (h₀*): the lead paid for itself if the batch,
  without it, would have read less than h₀*.

If the batch visibly did not read the prefix, or the lead cost more than it
could possibly have saved, the lead switches off for the rest of the app
session. Developer tools shows the last check. **The switch still ships
off.**

### Why

It is M3's Chunk 3 half, run on every Final QC the app makes anyway (§2).

### Design

1. **The latch** lives in `backend/cost_checks.py`, which CT-1 created
   (the Chunk 4 plan's §4). Add:
   - `warm_lead_enabled() -> bool`;
   - `disable_warm_lead(*, reason, detail="")`, with reason `not_read` or
     `unprofitable`;
   - a recording entry point (for example `record_warm_lead_lineage(...)`)
     that keeps the last check for diagnostics and applies the rules below.

   The same posture as CT-1's latch: thread-safe, OFF-only, process-local,
   in memory, never raises into the engine, and one WARNING when it latches
   (R6).
2. **The gate.** `_run_batch_calls` picks leads only while the latch is
   clear:
   `leads = _pick_warm_leads(specs) if warm_leads and warm_wait_seconds > 0 and cost_checks.warm_lead_enabled() else []`.
   Nothing else about the lead changes.
3. **When to measure.** Only on the normal end, the final
   `return finish("ended")`, and only after the leads are joined (call
   `fold_leads(wait=True)` first; `finish()` joining again is harmless).
   - Never after a Stop, the wall-clock ceiling, a refused or id-less
     submission, a failed results read or the settlement window. Their
     seats are an incomplete sample of a phase nobody finished.
   - Measure only lineages whose lead is in `streamed_keys` (it sent a
     request).
   - Batched seats that the round ceiling settled as failures are still
     measured, if they have a first response.
4. **Which seats belong to a lineage.** Group the phase's keys by
   `_spec_lineage_key(states[key].spec)`, or give `_WarmLead` its member
   keys when it is picked. The batched seats are the members minus the
   lead.
5. **Per batched seat** (Appendix B.2):
   - take the seat's first billed response, `settled.billed[0]`;
   - read its first iteration with `cost_checks.first_iteration_usage`
     (CT-2): `usage.iterations` if present, or the top-level usage if the
     response made no server-tool request;
   - s = read / (read + write) on that first iteration;
   - the seat **read the prefix** when s ≥ `_WARM_LEAD_SEAT_READ_SHARE`
     (0.95).

   A seat with no billed response, no readable first iteration, or
   read + write = 0 is **unmeasured**: counted, never guessed.
   - No-web seats never run a server tool, so their top-level usage is
     always their only iteration, and every no-web seat with a response is
     measurable.
   - A web-tooled seat is measurable only when the provider reports
     iterations, or when its first response did not search.
6. **Per lineage**, once at least `_WARM_LEAD_MIN_MEASURED` (8) seats are
   measured:
   - h₁ = the share of measured seats that read the prefix;
   - p = the median of read + write over the measured seats' first
     iterations (the prefix, in tokens);
   - C = the lead's list-price cost:
     `estimate_usage_cost(spec.model, _sum_billed(lead.billed))`,
     multiplier 1.0;
   - n = the lineage size, counting the lead;
   - h₀*, as in Appendix B.4.

   Log one INFO line per lineage on `buildaspec.cost_checks`: the lineage
   kind, n, measured, unmeasured, h₁, h₀*, C and the verdict. Numbers
   only; no finding text.
7. **The rules**, in order, applied to each measured lineage:
   - `not_read`, when h₁ < `_WARM_LEAD_MIN_READ_SHARE` (0.5). A lead
     exists to lift the batch's read share close to 1: every seat submitted
     after its first output could read its entry. If fewer than half the
     measured seats read the prefix, the batch is not reading the lead's
     copy, and the lead only costs its discount.
   - `unprofitable`, when h₀* ≤ 0. Even if the batch alone would have read
     nothing, the lead cost more than it saved.
   - otherwise, keep it.

   One run can latch; the next run then sends no lead. There is no rule on
   h₀* > 0, because h₀ is unknown (§2). Write that reasoning into the
   module docstring.
8. **It changes nothing else.** No request, record, `cost_multiplier`,
   usage total, meter bucket, SSE payload or manifest changes (F3, F4, R4,
   R6). The check is arithmetic on objects the phase already holds, run
   once per phase, and it never raises. A malformed usage makes a seat
   unmeasured.
9. **Diagnostics.** The `cost_checks` block gains `warm_lead`:
   `{setting_on, enabled, reason, detail, since, last_check}`, where
   `last_check` holds `at` and a per-lineage list of `kind`, `seats`,
   `measured`, `unmeasured`, `read_share`, `prefix_tokens`,
   `lead_cost_usd`, `break_even_read_share` and `verdict`. Every key must
   survive `scrub_data`: no `token` without an `s` after it.
10. **Developer tools.** Extend CT-2's "Cost self-checks" row
    (`frontend/src/lib/costChecks.ts`) with the warm lead. For example:
    - "Warm lead (Final QC): on · last check: 24 no-web seats, 22 measured,
      100% read the shared copy; the lead pays when the batch alone would
      read under 71%"
    - "Warm lead: off for this session — the batch did not read the lead's
      copy (12%)"
    - "Warm lead: switched off in settings"

    Extend the frontend test, including the reason vocabulary pin.

### Files

- `backend/cost_checks.py` and `backend/qc/engine.py`
- `frontend/src/lib/costChecks.ts`, `frontend/src/types.ts` and, if
  needed, `frontend/src/components/DeveloperToolsModal.tsx`
- `tests/fakes.py`, only if a helper is missing (keyword-only, attached
  only when supplied)
- new `tests/test_cost_checks_warm_lead.py`, and `frontend/tests/costChecks.test.ts`
- this plan's As built, and the tracker's WL-1 row and checklist

### Tests (`tests/test_cost_checks_warm_lead.py`)

Every test passes `batch_warm_lead=True` (and a nonzero wait) explicitly,
so WL-2's flip changes none of them. Reuse `_minimums_at_the_floor` and
`_lineage_scripts` from `tests/test_qc_batch_warm_lead.py` rather than
copying them.

- **Unit tests of the arithmetic** (Appendix B), each with hand-computed
  numbers:
  - h₁, p (the median) and h₀*;
  - the `not_read` threshold on both sides;
  - `unprofitable` for an expensive lead;
  - fewer than 8 measured seats → no verdict.
- **End to end, a no-web lineage at the floor:**
  - every batched seat reads the prefix → kept, and diagnostics record
    h₁ = 1;
  - every batched seat writes it → `not_read`, and the NEXT run streams
    no lead: no request precedes `batches.create`;
  - an expensive lead with a low read share → `unprofitable`.
- **A web-tooled lineage:**
  - its seats searched, with no iterations → unmeasured, and no verdict;
  - with iterations scripted → measured.
- **Paths that are never measured:**
  - a Stop, the wall-clock ceiling and a refused submission;
  - a lead stopped before its first request (not in `streamed_keys`).
- **Invisibility:** with the check patched to a no-op, every record,
  `cost_multiplier`, meter bucket and the input manifest are identical
  (F3, F4).
- A malformed seat usage makes that seat unmeasured and raises nothing.
- Diagnostics report the last check and survive `scrub_data`.
- `test_warm_lead_ships_switched_off` is untouched, and green.

### Acceptance

- **WL-1.1** `backend/cost_checks.py` holds a warm-lead latch, with the same posture as the continuation-tail latches (thread-safe, OFF-only, process-local, one WARNING), and `_run_batch_calls` picks leads only while it is clear.
- **WL-1.2** The check runs only when a phase reaches its normal end, after the leads are joined, and only for lineages whose lead sent a request. It never runs after a Stop, the wall-clock ceiling, a refused or id-less submission, a failed results read or the settlement window.
- **WL-1.3** Each batched seat is measured from its first billed response's first iteration: it read the prefix when its read share is at least 0.95, and seats without a readable first iteration are counted as unmeasured, never guessed.
- **WL-1.4** With at least 8 measured seats, the check computes h₁, p, C and h₀* as Appendix B says, and logs one INFO line per lineage with numbers only.
- **WL-1.5** The lead latches `not_read` when h₁ is below 0.5, and `unprofitable` when h₀* is at most 0. Otherwise it is kept.
- **WL-1.6** The check changes no request, record, multiplier, usage total, meter bucket, SSE payload or manifest (a test with it patched out), and raises nothing on malformed usage.
- **WL-1.7** The `cost_checks` diagnostics block reports the warm lead's state and last check and survives `scrub_data`, and the Developer tools "Cost self-checks" row shows it.
- **WL-1.8** `tests/test_cost_checks_warm_lead.py` and the extended `frontend/tests/costChecks.test.ts` cover WL-1.1 to WL-1.7.
- **WL-1.9** `BUILD_A_SPEC_QC_BATCH_WARM_LEAD` still defaults off, and `test_warm_lead_ships_switched_off` is unchanged and green.
- **WL-1.10** Verified: ruff, the full pytest suite, `npm test` and `npm run build` are all clean.
- **WL-1.11** Every mechanism above was reverted in place, one at a time, and its own test went red. The matrix is recorded under As built.
- **WL-1.12** As built is written, including its For FIN-1 list, and the root `CLAUDE.md` and `README.md` are untouched.

### As built

*Built on 2026-09-24; the record follows the list below.*

When you build this session, append here:
- what was built;
- every deviation from this spec, and why;
- every knowing change to an existing test;
- the revert matrix (mechanism → tests red);
- **For FIN-1:** the CLAUDE.md implemented-notes material (the why and the
  traps, in CLAUDE.md's own style), the Layout entries to add or change,
  any erratum an earlier CLAUDE.md section now needs, and anything the root
  README must say.

#### WL-1 as built (2026-09-24)

Built from `master` at `14c1185` (CT-3's merge), on branch
`claude/focused-meitner-477gz0`. The switch still ships off:
`settings.QC_BATCH_WARM_LEAD` still defaults to `False`, and
`test_warm_lead_ships_switched_off` is unchanged and green.

**What was built**

- **`backend/cost_checks.py`**, the warm lead's check:
  - `REASON_NOT_READ = "not_read"` joins the closed reason vocabulary, and
    `_WARM_LEAD_REASONS = {not_read, unprofitable}` is what the warm lead
    may latch on.
  - One latch (`_WarmLeadState`: a `_Latch` and the last check), with the
    tail latches' posture: thread-safe, OFF-only, process-local, in memory,
    one WARNING when it latches. `warm_lead_enabled()` never raises (it
    answers `True` if the check itself fails); `disable_warm_lead(*, reason,
    detail="")` takes only a warm-lead reason, the first latch wins, and a
    malformed call is logged at DEBUG. `_set_locked(latch, …)` is now the
    one "the first latch wins" rule; `_latch_locked(engine, …)` delegates
    to it.
  - `WarmLeadLineage` (frozen): `kind`, `seats` (n, the lead among them),
    `model`, `lead_usage` (every billed response of the lead, summed),
    `batched_first` (each other seat's reply to the first batch it rode,
    or `None`; deviation 12) and `warm` (deviation 2).
  - `check_warm_leads(lineages)`, called once per phase. Each lineage is
    judged by `_judge_lineage` (Appendix B, below) and logged in one INFO
    line; the phase's judgments replace `last_check`; the first lineage
    judged `not_read` or `unprofitable` latches, with one WARNING.
    Recording and latching are one lock acquisition; the INFO lines and
    the WARNING are logged outside it. It never raises: a failure is logged
    at DEBUG and records nothing.
  - `_judge_lineage`: per batched seat, `first_iteration_usage` (CT-2) on
    its reply to the first batch it rode; a seat with no such reply, no
    readable first iteration or read + write = 0 is unmeasured. It read
    the prefix when read ≥ 0.95·(read + write). With at least 8 measured
    seats: h₁ = reads / measured, p = the exact median of read + write,
    C = `usage_ledger.estimate_usage_cost(model, lead_usage)` (list price),
    Δ = the 1-hour cache-write rate − the cache-read rate (both through
    `usage_ledger.model_rates`, as `Decimal` at twelve significant digits,
    CT-2's `_rate`), b = `settings.BATCH_COST_MULTIPLIER`, and the
    numerator N = (n − 1)·b·h₁·Δ·p − (1 − b)·C over D = n·b·Δ·p. The
    verdicts, in order: `not_warm`, `too_few` (below 8 measured),
    `not_read` (h₁ < 0.5), `unprofitable` (N ≤ 0), `kept`.
  - The module docstring describes the third check, and says why there is
    no rule on h₀* > 0 (h₀ is never observed; any rule would guess it).
  - `snapshot()` gains `"warm_lead": {setting_on, enabled, reason, detail,
    since, last_check}` beside `continuation_tail`, read in the same one
    lock acquisition. `last_check` is `None`, then `{at, lineages}`, each
    lineage `{kind, seats, measured, unmeasured, read_share, prefix_tokens,
    lead_cost_usd, break_even_read_share, verdict}`, copied on the way
    out. `reset_for_tests()` clears it in the same one acquisition.
- **`backend/qc/engine.py`**:
  - The gate: `_run_batch_calls` picks leads only while
    `cost_checks.warm_lead_enabled()`, after the switch and the wait.
  - `_WarmLead.members`: every key of the lineage in specs order, the lead
    first, filled by `_pick_warm_leads` (the spec's second option).
  - `release_outcomes`: how each lead's `_await_leaders` wait ended
    (`warm`, `timeout` or `stopped`), recorded by `release()`.
  - `streamed_leads()`: "a lead that sent a request", extracted from
    `results()` so the pricing and the check read one rule.
  - `_BatchSeatState.first_round` and `first_reply` (deviation 12): the
    round of the first batch a seat rode, marked when the provider accepts
    that batch (a refused submission ran nothing, so it is not one), and
    the reply that batch brought back, recorded when the round is read
    (`None` for an errored, expired or missing item). Never part of the
    record.
  - `check_leads()`: builds one `WarmLeadLineage` per streamed lead
    (`_sum_billed` of the lead's billed responses; each other member's
    `first_reply`, failed or not) and hands the phase's lineages to
    `cost_checks.check_warm_leads`. Its own `try` keeps a gathering failure
    out of the phase (logged at DEBUG on `buildaspec.qc`).
  - The normal end is now `settle_all(...)`, `fold_leads(wait=True)`,
    `check_leads()`, `return finish("ended")`. No other exit calls it.
- **Developer tools.** `frontend/src/types.ts` gains `WarmLeadCheck`,
  `WarmLeadLineageCheck` and `CostChecksSnapshot.warm_lead`.
  `frontend/src/lib/costChecks.ts` appends the warm lead's line after the
  continuation tail's (deviation 9). `DeveloperToolsModal.tsx` needed no
  change: it renders every line the helper returns.

**Deviations from the spec, and why**

1. **One call per phase: `check_warm_leads(lineages)`**, not a per-lineage
   `record_warm_lead_lineage(...)`. `last_check` then describes one phase,
   and recording and latching are one lock acquisition, so a reader never
   sees a check without the latch it earned.
2. **A lineage is judged only when the batch went out after the lead's
   copy was ready — the `not_warm` verdict, beyond the spec.** The spec
   measures every lead in `streamed_keys`. But a lead can be streamed and
   still leave the batch nothing to read:
   - its wait timed out (no first output within `QC_WARM_WAIT_SECONDS`);
   - its first request failed fast: `_run_streaming_call`'s `finally`
     releases the wait when a request ends, raised or not, so the wait's
     outcome still reads `warm`, and the lead's retry makes it a streamed
     lead.

   Judged, either would latch `not_read` whenever the batch alone reads
   little: a false latch, exactly when a lead is worth the most, costing
   the saving for the rest of the session. So `warm` is `release_outcomes
   [lead] == "warm"` **and** `api_request_count == len(billed)` (none of
   the lead's requests failed). The second half is conservative: a failure
   later in the lead's conversation, after its first output, also reads
   `not_warm`. That only skips a judgment; it can never cause a latch.
   The numbers are still computed and reported for a `not_warm` lineage
   with 8 or more measured seats (h₁ there is close to what the batch reads
   alone).
3. **The verdict vocabulary**: `kept`, `too_few`, `not_warm`, `not_read`,
   `unprofitable` (`WARM_LEAD_VERDICTS`), pinned against the frontend.
   Below 8 measured seats (`too_few`), h₁, p and h₀* are reported as
   `None`; C always is.
4. **`unprofitable` reads the numerator, N ≤ 0.** That is h₀* ≤ 0 whenever
   D > 0, which it is for every priced model. If the rates ever left
   D ≤ 0 (reading no cheaper than writing), N ≤ 0 still says the lead cannot
   pay, and h₀* is reported as `None` instead of a meaningless ratio.
5. **Rounding.** `read_share` rounds half-even to four places.
   `break_even_read_share` rounds to four places **away from zero** (CT-2's
   `_usd` posture), so a break-even a hair above zero never reads as `0.0`
   beside `kept`, nor one a hair below as a positive. `prefix_tokens` is an
   int, or a float when an even count's median ends in .5.
   `lead_cost_usd` is `_usd(C)`; C itself is `estimate_usage_cost`'s value,
   rounded to six places.
6. **Δ uses the 1-hour write rate**, falling back to the 5-minute one for a
   model that has none (none today). The seats' own writes are 1-hour.
7. **A lineage at the floor of 8 seats is never judged.** It has 7 batched
   seats, one short of 8 measured, so it always reads `too_few`. At the
   shipped minimums (20) every lineage has at least 19 batched seats. The
   tests use 10-seat lineages. WL-2's floor-8 flip-readiness run will see
   `too_few` for its 8-seat lineages; that is expected.
8. **The engine gathers under its own `try`**, and `_sum_billed` runs there
   (it is engine code; the check stays a leaf). A gathering failure is
   logged at DEBUG on `buildaspec.qc` and records nothing.
9. **The frontend line.**
   - The latch: "Warm lead (Final QC): off for this session — the batch
     did not read the lead's copy (12%)", the share read from the
     last-check lineage whose verdict is the latch's reason; for
     `unprofitable`, "(lead cost $1.4563)". A latch with no such lineage
     (set directly) shows the reason alone.
   - On: "… on · nothing checked yet", or "… on · last check: " and one
     phrase per lineage, joined by " · ". `kept` reads "24 no-web seats,
     22 measured, 100% read the shared copy; the lead pays when the batch
     alone would read under 71%"; `too_few` "…, 0 measured — too few to
     judge"; `not_warm` "… — not judged: the batch went out before the
     lead's copy was ready".
   - "Warm lead: switched off in settings" when the setting is off; "Warm
     lead: not reported" for a malformed block. A backend with no warm-lead
     block (CT-2's) renders no warm-lead line, so the existing tests are
     unchanged.
   - `CHECK_REASON_TEXT` gains `not_read`; `WARM_LEAD_VERDICT_TEXT` has the
     three verdicts that are not reasons. The test pins both against
     `backend/cost_checks.py`.
10. **Comments and QA rows made true (R9)**, in files the spec did not
    list:
    - `backend/settings.py`: `QC_BATCH_WARM_LEAD`'s comment said "The
      default flips only on a recorded M3 pass". FD1 made that false, and
      WL-1 builds its replacement, so it now says no trial will settle it
      and describes the check. The default is untouched (WL-2's).
    - `backend/qc/engine.py`: the block comment above `LINEAGE_WEB_TOOLED`
      said the same; it now names the check, `check_leads` and the gate.
    - `docs/RELEASE_WINDOWS.md`: the "Streamed lead seat" row now says what
      the Cost self-checks row shows after that Final QC, and the "Cost
      self-checks row" row mentions the warm lead's last line. WL-2 adds
      its own rows.
11. **The invisibility test's baseline patches out the hand-off too.**
    The spec's baseline patches the check to a no-op. The engine's
    `check_leads` still ran in both arms then, so a hand-off that wrote
    anything back would have shown up in both and passed (the revert
    matrix's first run found exactly that). The baseline now also makes
    `cost_checks.WarmLeadLineage` raise, so nothing after the phase's own
    reads runs in it.
12. **Each seat is read from its reply to the FIRST batch it rode, not
    from `settled.billed[0]`** (found by Codex's review of PR #227). The
    spec says `billed[0]` is a batched seat's "request in the first round,
    which was submitted after the lead's release". That is not true of a
    seat whose item in its first batch errored (an overloaded or server
    error, a rate limit, a timeout). Such an item brings back no reply, so
    the seat is retried in a later round, and its `billed[0]` is that
    retry's reply. The retry runs after the first round has ended, when
    it can read a copy an EARLIER batched seat stored rather than the
    lead's. Counted, such retries raise h₁ and could keep a lead the first
    round did not read. In the test, 8 seats write the prefix in round 1
    and 9 retries read it in round 2: read from the retries, h₁ would be
    9/17 and the lead kept; read from the first batch, h₁ is 0 of 8
    measured and the lead switches off (`not_read`).
    - So the engine records, per seat, the round of the first batch the
      provider accepted (`_BatchSeatState.first_round`) and the reply that
      batch brought back (`first_reply`, `None` when it brought none). The
      check reads only that. A seat whose first batch brought no reply is
      unmeasured.
    - A refused submission ran nothing, so it is no seat's first batch:
      after a retried 429 on `batches.create`, the seats are read from the
      batch the provider accepted next.

**Knowing changes to existing tests**

None. `frontend/tests/costChecks.test.ts` gains eight tests after its
nine; the nine are unchanged.

**The tests** — `tests/test_cost_checks_warm_lead.py`, 43 tests (45
counting parametrized cases). Every engine run passes
`batch_warm_lead=True` and a nonzero wait, on `claude-opus-5-5`; an autouse
fixture first checks the rates the hand-computed numbers assume (Opus 5.5:
$4.00 input, $20.00 output, $0.20 cache read, $8.00 1-hour write per
million) and b = 0.5.

- The arithmetic, by hand:
  - `test_h1_p_c_and_the_break_even_by_hand`: n = 10, p = 40,000,
    C = $0.40028, h₀* = 1.20386 / 1.56, shown as 0.7718.
  - `test_p_is_the_median_of_the_measured_prefixes`: an even count's mean
    of the middle two (35,000), and an odd count's middle.
  - `test_a_seat_read_the_prefix_at_95_percent_and_not_below`: 38,000/2,000
    reads, 37,600/2,400 does not; h₁ = 0.5 exactly is kept.
  - `test_not_read_below_half_and_kept_at_half`
  - `test_an_expensive_lead_is_unprofitable`: C = $1.82028,
    h₀* = −0.28614 / 1.404.
  - `test_a_break_even_of_exactly_zero_is_unprofitable`: C = $1.248, N = 0;
    one output token fewer is kept, shown as 0.0001.
  - `test_fewer_than_eight_measured_seats_give_no_verdict`
  - `test_a_lead_the_batch_did_not_wait_for_is_not_judged`
  - `test_what_cannot_be_read_is_unmeasured_and_nothing_raises`: no
    response, a searched seat, nothing read or written, a bool, a negative,
    a missing count, no usage, a string.
  - `test_a_searched_seat_is_measured_from_its_reported_first_iteration`
  - `test_a_failure_inside_the_check_records_nothing`
  - `test_the_rates_come_from_the_ledger`: doubling what `_rates` returns
    doubles C and Δ.
- The latch: `test_a_losing_lineage_latches_once_with_one_warning`,
  `test_one_info_line_per_lineage_with_numbers_only`,
  `test_the_first_losing_lineage_sets_the_reason`,
  `test_disable_takes_only_a_warm_lead_reason`,
  `test_the_warm_lead_and_the_tails_latch_apart`,
  `test_every_read_and_write_takes_the_one_lock_once` (CT-1's counting
  lock), `test_many_threads_check_and_one_latches`,
  `test_an_empty_check_records_nothing`, and the pair
  `test_a_latch_left_set_on_purpose` /
  `test_the_next_test_starts_with_the_warm_lead_clear`.
- End to end:
  - `test_a_lineage_the_batch_reads_is_kept_and_diagnostics_record_it`:
    ten no-web seats, h₁ = 1, h₀* = 0.91186 / 1.17 (0.7794), through
    `diagnostics.snapshot()`.
  - `test_a_batch_that_writes_the_prefix_switches_the_lead_off_for_the_next_run`:
    `not_read`, one WARNING; the next run streams nothing before the batch
    and batches all ten seats.
  - `test_an_expensive_lead_on_a_lightly_read_lineage_is_unprofitable`:
    h₁ = 5/9, C = $1.45628, h₀* = −0.1224.
  - `test_a_web_tooled_lineage_that_searched_without_iterations_is_unmeasured`
    and `test_a_web_tooled_lineage_with_iterations_is_measured`.
  - `test_a_seat_the_round_ceiling_failed_is_still_measured` and
    `test_a_paused_seat_is_measured_from_its_first_response`.
  - `test_a_retried_seat_is_measured_only_by_its_first_batch` and
    `test_a_refused_submission_is_not_a_seats_first_batch` (deviation 12).
    The first: 18 no-web seats, 8 that wrote the prefix in round 1 and 9
    whose round-1 items errored and whose retries read it in round 2 give
    8 measured, 9 unmeasured, h₁ = 0 and `not_read`. The second: a 429 on
    the first `batches.create`, and the nine seats are read from the batch
    accepted next.
  - `test_a_lead_whose_first_request_failed_is_not_judged` and
    `test_a_lead_whose_wait_timed_out_is_not_judged` (deviation 2). The
    second uses a 50 ms wait that can only expire: the lead is held on an
    event until the batch's results are read.
  - `test_the_check_reads_the_lead_only_after_it_is_joined`: the lead is
    held after its first output until its future's `result()` is asked,
    which only the join does.
- Never measured: `test_a_phase_nobody_finished_is_never_measured` (a Stop
  after submission, the settlement window; a refused submission that ends
  the phase; a failed results read),
  `test_an_id_less_submission_is_never_measured`,
  `test_the_wall_clock_ceiling_is_never_measured`,
  `test_a_stop_during_the_lead_wait_is_never_measured`, and
  `test_a_lead_that_sent_nothing_is_not_measured_on_a_normal_end`.
- `test_the_latch_stops_the_next_phase_picking_a_lead` and
  `test_gathering_for_the_check_never_fails_the_phase`.
- `test_the_check_changes_no_request_record_multiplier_meter_event_or_manifest`
  (F3, F4, R4, R6; deviation 11). Requests, batches, the whole record,
  the multipliers, the meter buckets, the manifest, the fingerprint and
  every event are identical. Set aside: the record's wall-clock
  `duration_ms`, and phase 1's `done` counter, which counts lenses in the
  order their threads finish.
- `test_diagnostics_report_the_last_check_and_survive_the_scrub`: the
  block, unchanged by `scrub_data`, identical in `diagnostics.snapshot()`
  and `/api/diagnostics`, no key matching the credential pattern, and a
  copy handed out.

`frontend/tests/costChecks.test.ts` gains 8 tests (17 in all): each
warm-lead state's line, the latch's evidence, malformed and older
snapshots, and the verdict vocabulary pinned against the backend.

**What the check cannot see — for WL-2 and Abraham**

- **h₀**, as §2 says: a lead that is read but was not needed is kept.
- **Most web-tooled lineages.** A web-tooled seat that searched in its
  first response reports summed usage and no per-iteration usage on the GA
  endpoint (CT-2's findings), so it is unmeasured. Where most seats search
  first, the lineage reads `too_few` and its lead is kept, judged by
  nothing. A no-web seat is measurable whenever its first batch brings a
  reply.
- **A seat whose first batch brought no reply** (deviation 12). Its retry
  is not evidence about the lead, so it is unmeasured. A phase where many
  first items fail can read `too_few`.
- **A lineage of exactly 8 seats** (deviation 7).

**Verification** (Linux container, from the repository root)

- `.venv/bin/python -m ruff check .`: all checks passed.
- `.venv/bin/python -m pytest -q`, on the final code: 2990 passed, 64
  skipped (6 min 44 s). CT-3 ended at 2945; the 45 new cases are
  `tests/test_cost_checks_warm_lead.py`'s. Before the fix for Codex's
  review it was 2988 passed, 64 skipped.
- `npm test` (in `frontend/`): 438 passed, 0 failed (430 before).
- `npm run build`: built; the only warning is the existing chunk-size one.
- `tests/test_tier1_finish_tracker.py` and `tests/test_docs_consistency.py`
  pass with this As built and the ticks in place.
- `git diff --name-only origin/master...HEAD -- CLAUDE.md README.md`
  prints nothing.

**Revert matrix.** Each mechanism was reverted in place, one at a time, by
a script: it replaced one exact snippet, ran the suites, restored the
exact text it read, and checked it byte-identical; the working tree's diff
was byte-identical at the end. The backend rows ran the new file,
`test_qc_batch_warm_lead.py`, `test_cost_checks_tail_rejection.py` and
`test_cost_checks_tail_value.py` (140 tests); the frontend rows ran
`frontend/tests/costChecks.test.ts`. "Red" counts failing tests. The
table is the run on the final code, after the fix for Codex's review
(deviation 12).

51 rows, 51 red.

| Mechanism reverted | Red |
|---|---|
| gate: a lead picked whatever the latch says | 2 |
| the latch read always clear | 6 |
| the check never latches | 10 |
| a later latch overwrites the first | 5 (two of them the tail's) |
| a latch writes no WARNING | 4 |
| `disable_warm_lead` takes any reason | 1 |
| the check records and latches in two lock acquisitions | 1 |
| reset leaves the warm lead | 47 (a latch carries into every later test) |
| the check runs before the leads are joined | 2 |
| the check runs on every terminal path (inside `finish`) | 7 |
| the check never runs | 15 |
| a lead that sent nothing is measured | 1 |
| the wait's outcome not recorded | 11 |
| `warm` ignores the wait's outcome | 1 |
| `warm` ignores a failed request | 1 |
| no `not_warm` verdict | 3 |
| a seat the round ceiling failed is skipped | 1 |
| gathering raises into the phase | 2 |
| the seat threshold 0.95 → 0.9 | 1 |
| the seat threshold strict (> 0.95) | 1 |
| read + write = 0 counted as measured | 2 |
| a seat read from its last reply, updated every round | 2 |
| the check reads `billed[0]` again (the code Codex flagged) | 1 |
| the first reply taken from any round, not the first batch's | 1 |
| a refused submission counted as a seat's first batch | 1 |
| no first reply recorded | 12 |
| the minimum measured 8 → 7 | 2 |
| p is the mean, not the median | 1 |
| C at the batch rate | 13 |
| Δ uses the 5-minute write rate | 10 |
| n does not count the lead | 11 |
| no INFO line | 2 |
| the break-even rounds half-even | 5 |
| `not_read` at or below one half | 6 |
| no `not_read` rule | 8 |
| `unprofitable` only below zero | 3 |
| no `unprofitable` rule | 5 |
| the check raises | 1 |
| sabotage: the check changes a response it reads | 2 |
| sabotage: the hand-off emits an event | 1 |
| sabotage: the hand-off edits a record | 3 |
| the snapshot has no warm-lead block | 26 |
| the snapshot hands out the module's record | 1 |
| `setting_on` reads the tail's switch | 1 |
| a key that trips the redaction pattern (`prefix_token`) | 7 |
| frontend: no words for `not_read` | 4 |
| frontend: no words for `not_warm` | 2 |
| frontend: no warm-lead line | 7 |
| frontend: the setting off not said | 1 |
| frontend: the latch's evidence dropped | 1 |
| frontend: the break-even clause dropped | 1 |

The first run, before the review, found two rows green:
- "A seat read from its last response" passed because no test had a
  batched seat with two billed responses.
  `test_a_paused_seat_is_measured_from_its_first_response` now has one.
- The hand-off sabotage passed because the invisibility test's baseline
  still ran the hand-off (deviation 11).

Deviation 12 then replaced `billed[0]` with `first_reply`. So "a seat read
from its last response" became "a seat read from its last reply, updated
every round". Four rows cover the fix, and "a seat the round ceiling
failed is skipped" is now expressed on `first_reply`. One mutation was
left out because it cannot change anything: taking the LAST reply instead
of the first at the moment the first batch is read. A seat has at most
one reply then, so the two are the same.

**For FIN-1**

- **CLAUDE.md implemented notes** — a section "The warm lead checks that
  the batch reads its copy — implemented notes (Tier 1 finish, WL-1)", in
  CLAUDE.md's own style. The why and the traps:
  - *Why.* Whether a batch request can read an entry a streamed request
    wrote is undocumented, and no trial will settle it (FD1). So after
    every batched phase that ends normally and sent a lead, the app reads
    the usage the batch already reported: nothing is sent (R5).
  - *What is measured* (Appendix B): per batched seat, the first iteration
    of its reply to the first batch it rode (read ≥ 95% of read + write);
    per lineage with 8 or more measured seats, h₁, p, C and h₀*.
    `not_read` below h₁ = 0.5, `unprofitable` at h₀* ≤ 0; one run can
    latch, and the next phase picks no lead.
  - *No rule on h₀* > 0*: h₀ is never observed. A read-but-unneeded lead is
    kept, at the price of its discount.
  - *`not_warm`* (deviation 2): a lead whose wait timed out, or whose first
    request failed (its `finally` releases the wait), left the batch
    nothing to read; judging it would latch falsely exactly when a lead is
    worth the most.
  - *Traps.*
    - A lineage of exactly 8 seats has 7 batched seats and can never be
      judged.
    - A web-tooled seat that searched first is unmeasured on the GA
      endpoint; web-tooled lineages mostly read `too_few`.
    - The check reads each seat's reply to the FIRST batch it rode
      (`_BatchSeatState.first_reply`), never `billed[0]` (Codex, PR #227).
      A seat whose first item errored is retried in a later round, where it
      can read a copy an earlier batched seat stored: its `billed[0]` is
      that retry, and counting it keeps a lead the first round never read.
      A refused submission ran nothing, so it is no seat's first batch.
      And a paused seat's continuation is not its read of the prefix.
    - The check runs only after `fold_leads(wait=True)`: an unjoined lead
      has no record, so it is not "streamed" and would be skipped.
    - Comparing two runs needs QC's `duration_ms` and phase 1's `done`
      counter set aside; and an invisibility baseline must patch out the
      engine's hand-off, not only the check, or a hand-off that writes
      back passes in both arms.
    - A lineage's values sit six levels down in the diagnostics payload,
      the scrub's bound, so they must be scalars.
    - `break_even_read_share` rounds away from zero, like `_usd`.
- **Layout.**
  - `backend/cost_checks.py`: add "the warm lead's check (WL-1):
    `warm_lead_enabled`, `disable_warm_lead` and `check_warm_leads`
    (`WarmLeadLineage`; Appendix B's h₁, p, C and h₀*; `not_read` below
    h₁ = 0.5, `unprofitable` at h₀* ≤ 0; `not_warm` and `too_few` never
    latch), and the snapshot's `warm_lead` block".
  - `backend/qc/engine.py` (the Chunk 3 part of its entry): the gate reads
    `cost_checks.warm_lead_enabled()`; `_WarmLead.members`;
    `_BatchSeatState.first_round` / `first_reply`; `release_outcomes`;
    `streamed_leads()`; `check_leads()` at the normal end only, after
    `fold_leads(wait=True)`.
  - `frontend/src/lib/costChecks.ts`: the warm lead's line and
    `WARM_LEAD_VERDICT_TEXT`; `frontend/src/types.ts`: `WarmLeadCheck`.
  - Add `tests/test_cost_checks_warm_lead.py`, and extend the
    `frontend/tests/costChecks.test.ts` entry.
- **Errata** for earlier CLAUDE.md sections:
  - The Layout's `settings.py` entry says `QC_BATCH_WARM_LEAD` "flips on
    only on a recorded M3 pass". Since FD1 its flip waits on WL-1's check
    and WL-2's merge, not M3.
  - "Final QC's batched phase can stream a lead seat first (Research/QC
    cost Tier 1, Chunk 3)" says the default "waits for an M3 pass". Same
    correction.
  - "Research and Final QC cost, Tier 1, as shipped (closeout)" says
    Chunk 3's default "flips only on an M3 pass of that chunk's own test".
    Same correction.
- **README** — in the warm-lead subsection (which says it "stays off until
  a measured run shows it does (the plan's M3)") and the
  `BUILD_A_SPEC_QC_BATCH_WARM_LEAD` Configuration row ("**off** until a
  measured run shows it pays"): after each Final QC that sent a lead, the
  app reads how many of the batched seats read the lead's copy. If fewer
  than half did, or the lead cost more than it could have saved, the lead
  switches off until the app restarts. Settings → Developer tools → "Cost
  self-checks" shows the last check. (WL-2 flips the default; FIN-1 writes
  both.)

---

## WL-2 — Turn the warm lead on

### Goal

`BUILD_A_SPEC_QC_BATCH_WARM_LEAD` defaults on. Everything that described it
as off is true again, except the root `README.md` and `CLAUDE.md` (R2:
FIN-1 updates them).

### Why

FD1. With WL-1 in place, a lead the batch does not read, or one that cost
more than it could have saved, switches itself off, and the rest is bounded
(§2). That is what F5 asks of a default-on switch, as FD1 amends it.

### Design

1. **Flip readiness, before the flip.** Two runs, both recorded under As
   built:
   - The whole backend suite with the switch on from the environment:

     ```powershell
     $env:BUILD_A_SPEC_QC_BATCH_WARM_LEAD = "1"
     .\.venv\Scripts\python -m pytest -q
     Remove-Item Env:\BUILD_A_SPEC_QC_BATCH_WARM_LEAD
     ```

     On Linux: `BUILD_A_SPEC_QC_BATCH_WARM_LEAD=1 .venv/bin/python -m pytest -q`.
     Only `test_warm_lead_ships_switched_off` may fail.
   - The Final QC tests with both lineage minimums at the floor of 8, so
     the lead path really runs across the existing suite. This is Tier 1
     Chunk 3's own flip-readiness method (its As built, item 13). Use a
     scratch pytest plugin in a directory outside the repository, never
     committed. On Linux (any scratch directory will do):

     ```bash
     mkdir -p /tmp/floor8 && cat > /tmp/floor8/floor8.py <<'EOF'
     import pytest

     @pytest.fixture(autouse=True)
     def _floor8(monkeypatch):
         import backend.qc.engine as engine
         monkeypatch.setattr(engine, "_WARM_LEAD_MIN_SEATS_WEB", 8)
         monkeypatch.setattr(engine, "_WARM_LEAD_MIN_SEATS_NO_WEB", 8)
     EOF
     BUILD_A_SPEC_QC_BATCH_WARM_LEAD=1 PYTHONPATH=/tmp/floor8 .venv/bin/python -m pytest -q -p floor8 -k "qc or QC or final"
     ```

     Also count how many batch phases picked a lead, so the run is known to
     have exercised it: add `-o log_cli=true --log-cli-level=INFO` and count
     the `buildaspec.qc` lines containing `one streamed first` (the Chunk 3
     INFO line). Anything that fails, other than the pin,
     is a test that depended on the default: fix it by passing the switch
     explicitly, and name it.
2. **The flip.** In `backend/settings.py`, `QC_BATCH_WARM_LEAD` defaults to
   `True`. Rewrite its comment: what the lead does; that it is on because
   of FD1; WL-1's check; the bounded cost; the wait of up to
   `QC_WARM_WAIT_SECONDS`; and that `0` switches it off. Rewrite the block
   comment above `LINEAGE_WEB_TOOLED` in `backend/qc/engine.py` ("ships off
   and flips only on a measured pass") to say the same.
3. **The pin.** Replace `test_warm_lead_ships_switched_off` with
   `test_warm_lead_ships_switched_on`. It still reads the default from the
   source with `ast`.
4. **Copy that has to stay true (R9).** Grep for `WARM_LEAD`, `warm lead`,
   `streamed first`, `off by default` and `M3` across `backend/`,
   `frontend/src/`, `docs/` and `tools/`, and fix every claim this made
   false. At least:
   - The trust dossier's Final QC card
     (`frontend/src/components/TrustDeepDiveModal.tsx`, the paragraph that
     begins "An optional setting, off by default, sends one seat of a large
     group first"). Say it is on, when it happens, that the report prices
     that seat at full price, and that the app stops doing it for the rest
     of the session if the batch does not read that seat's copy.
   - `docs/RELEASE_WINDOWS.md`, the "Streamed lead seat" row. It becomes
     ordinary release QA with the default on. Add a row for switching it
     off (`$env:BUILD_A_SPEC_QC_BATCH_WARM_LEAD = "0"`) and a row for the
     Developer tools "Cost self-checks" warm-lead line.
   - Check that the report methodology note (`QC_WARM_LEAD_METHODOLOGY_NOTE`,
     the same literal in `docx_export` and `frontend/src/lib/qcReport.ts`)
     is still rendered only for a run that sent a lead. It should be, and
     it needs no change.
   - Do NOT touch the root `README.md` or `CLAUDE.md` (R2). List what they
     need under For FIN-1.
5. **The release-note draft.** In
   `docs/plans/RESEARCH_QC_COST_TIER1_2026-09-23.md` §7, "Release-note
   draft (Tier 1)", make item 3 unconditional: drop its conditional marker,
   and update the "Which items" bullet. You may add one plain sentence to
   item 3's body: the verification may start a few seconds later, and the
   app stops doing it by itself if it does not help. No version bump, no
   `backend/release_notes.py` entry, no tag (F7).
6. **Tell the owner what merging does.** The PR body says, near the top,
   that merging it turns the warm lead on for everyone running from
   `master`, and how to switch it off.

### Files

- `backend/settings.py` and `backend/qc/engine.py` (the comments)
- `tests/test_qc_batch_warm_lead.py`, plus any test that depended on the
  default (named under As built)
- `frontend/src/components/TrustDeepDiveModal.tsx`
- `docs/RELEASE_WINDOWS.md`
- `docs/plans/RESEARCH_QC_COST_TIER1_2026-09-23.md` §7
- this plan's As built, and the tracker's WL-2 row and checklist

### Acceptance

- **WL-2.1** Before the flip, the whole backend suite was run with `BUILD_A_SPEC_QC_BATCH_WARM_LEAD=1`, and the Final QC tests were run with both minimums at the floor of 8, with the number of phases that picked a lead counted. Only the ships-off pin failed, or every other failure was fixed and named. The counts are recorded under As built.
- **WL-2.2** `settings.QC_BATCH_WARM_LEAD` defaults to `True`, and its comment and the engine's block comment say why (FD1, WL-1, the bounded cost, the wait) and how to switch it off.
- **WL-2.3** `test_warm_lead_ships_switched_on` replaces `test_warm_lead_ships_switched_off`, and still reads the default from the source with `ast`.
- **WL-2.4** Every claim outside the root `README.md` and `CLAUDE.md` that the lead is off by default, or waits on M3, is true again: the trust dossier's Final QC card, `docs/RELEASE_WINDOWS.md`'s lead rows (with an off-switch row and a self-check row), and any comment the grep found. The methodology note is still rendered only for a run that sent a lead.
- **WL-2.5** The Tier 1 plan's §7 lists item 3 as always included, with no version bump, `backend/release_notes.py` entry or tag.
- **WL-2.6** Verified: ruff, the full pytest suite, `npm test` and `npm run build` are all clean.
- **WL-2.7** Reverting the default makes the new pin go red, and restoring it makes it green again.
- **WL-2.8** As built is written, including its For FIN-1 list (the root README rows and text, and the CLAUDE.md errata for every section that says the lead ships off), and the root `CLAUDE.md` and `README.md` are untouched.

### As built

*Built on 2026-09-24; the record follows the paragraph below.*

When you build this session, append here what WL-1's As built lists, plus
both flip-readiness runs' counts.

#### WL-2 as built (2026-09-24)

Built from `master` at `4eb1d70` (WL-1's merge), on branch
`claude/sharp-archimedes-qvlx9k`. **Merging it turns the warm lead on for
everyone running from `master`.** `BUILD_A_SPEC_QC_BATCH_WARM_LEAD=0`
switches it off (PowerShell: `$env:BUILD_A_SPEC_QC_BATCH_WARM_LEAD = "0"`;
Command Prompt: `set BUILD_A_SPEC_QC_BATCH_WARM_LEAD=0`). The reconcile step
filled in WL-1's merge commit, `4eb1d70`.

**Flip readiness (WL-2.1).** Both runs were made before the flip, on
`4eb1d70` plus only the reconcile edit.

- **The whole backend suite with `BUILD_A_SPEC_QC_BATCH_WARM_LEAD=1`** in
  the environment: **2990 passed, 64 skipped, none failed** (415.63 s).
  `test_warm_lead_ships_switched_off` passed (deviation 1). No other test
  failed, and the same suite with the switch off passes too (Verification,
  below), so no test's outcome depends on the default.
- **The Final QC tests with both lineage minimums at the floor of 8**, with
  the spec's scratch plugin (outside the repository, never committed) and
  `-o log_cli=true --log-cli-level=INFO`. `-k "qc or QC or final"` selected
  444 tests: **442 passed, 2 failed** (2610 deselected; 45.1 s).
  - **Phases that picked a lead**, counted two ways that agree:
    - The live log holds 25 `buildaspec.qc` lines containing
      `one streamed first`. (The output file holds 26, because pytest
      repeats one of them in a failure report.) A line is written per
      lead, not per phase.
    - So the plugin also wrapped the engine's `_run_batch_calls` and
      `_pick_warm_leads`, passing every argument and result through
      untouched, to count phases. It counted **206 batched verification
      phases. The gate was open in 158 of them, and 23 picked a lead,
      25 leads in all**: two phases had two lineages large enough.
    - `test_the_lineage_minimums_are_never_below_eight` also calls
      `_pick_warm_leads` directly, twice. Those two calls are not phases,
      and are left out of the counts.
  - By file, `tests/test_qc_batch_warm_lead.py` ran 26 phases, 20 of them
    with a lead (22 leads). `tests/test_qc_warm_launch.py` ran 6, 3 of them
    with one lead each. **Outside the lead's own test file, 419 tests ran
    and none failed; 3 of its 180 phases picked a lead.** The Tier 1
    closeout's run of the same method counted 3 of 138.
  - WL-1's check judged 20 lineages: 18 read `too_few` and 2 `not_warm`.
    None latched: the run logged no `the warm lead is switched off`
    WARNING. At the floor, a lineage of 8 seats has 7 batched seats, one
    short of 8 measured, so it always reads `too_few` (WL-1's deviation 7).
  - The two failures (deviation 2) are both in
    `tests/test_qc_batch_warm_lead.py`, and both pin the shipped minimum of
    20 that the plugin lowers to 8:
    - `test_a_lineage_below_the_minimum_has_no_lead` asserts
      `_warm_lead_minimum(...) == 20` directly.
    - `test_the_switch_off_is_todays_batch_exactly` runs an 8-seat lineage
      "below the shipped minimum of 20" with the switch on, and asserts
      that every seat is batched.

    Both pass the switch explicitly, and both passed in the first run.
  - `tests/test_cost_checks_warm_lead.py` (WL-1's) matches none of the
    `-k` terms, so it did not run here. It ran in the first run, and every
    engine run in it passes `batch_warm_lead=True`.

**What was built**

- **`backend/settings.py`**:
  `QC_BATCH_WARM_LEAD = _bool_env("BUILD_A_SPEC_QC_BATCH_WARM_LEAD", True)`.
  The comment's first paragraph (what a lead does, the minimums and the
  wait) is unchanged. The paragraph that began "OFF by default,
  deliberately" is replaced. It now says:
  - the lead has been on since WL-2, without the measured trial the flip
    once waited on;
  - a lead pays only if a batch request can read the entry a streamed
    request wrote, and no document says whether it can;
  - FD1 replaced the trial with WL-1's self-check, which can only switch
    the lead off, until the app restarts;
  - the check reads only the usage the batch already reported, and nothing
    is sent to test the provider;
  - once at least 8 batched seats are measured, the check switches the lead
    off when fewer than half of them read the lead's copy, or when the lead
    cost more than it could have saved even if the batch alone would have
    read nothing;
  - what the check cannot see is bounded: about the lead's own batch
    discount per large lineage per run ($0.20–$0.35 at typical sizes,
    B.6), and a wait of at most `QC_WARM_WAIT_SECONDS`;
  - a lead that fails is an ordinary failed seat;
  - `0` switches it off;
  - it stays out of the QC input manifest (F3).
- **`backend/qc/engine.py`**: only the block comment above
  `LINEAGE_WEB_TOOLED` changed. It said the switch ships off, and now says:
  - the lead has been on by default since WL-2, because WL-1's self-check
    watches every run that sends one;
  - what the check cannot see is bounded: the lead's discount, a failed
    seat, and the wait;
  - `=0` switches it off.

  No engine code changed. The gate, `run_final_qc`'s pin and the check
  are Chunk 3's and WL-1's.
- **`tests/test_qc_batch_warm_lead.py`**: `test_warm_lead_ships_switched_on`
  replaces `test_warm_lead_ships_switched_off`. It is the same `ast` walk
  over `backend/settings.py`, now expecting
  `["BUILD_A_SPEC_QC_BATCH_WARM_LEAD", True]`, and its docstring says why
  the default moved and how an operator switches the lead off. The module
  docstring's "with the switch off (its shipped default)" now says the
  switch shipped off until WL-2.
- **Copy (R9)**: everything outside the root files that the flip made
  false.
  - **The trust dossier's Final QC card**
    (`frontend/src/components/TrustDeepDiveModal.tsx`). The sentence that
    began "An optional setting, off by default, sends one seat of a large
    group first" is replaced. It now says:
    - when twenty or more seats read the same copy of the document, one of
      them is sent first, at full price;
    - the batch goes out once that seat begins answering, after at most
      45 seconds;
    - that seat shows its activity, and the report prices it at full
      price;
    - after each such review the app reads the batch's own usage report;
      if the batch did not read that copy, or the seat cost more than it
      could have saved, the app stops doing this until a restart;
    - Settings → Developer tools → Cost self-checks shows what it found.
  - **`docs/RELEASE_WINDOWS.md`**, in the Final QC section:
    - **Streamed lead seat** is now ordinary release QA ("on by default
      since the Tier 1 finish program's WL-2"). It sets no environment
      variable, and says the batch goes out once the lead begins answering
      (at most 45 seconds later). It adds that on a section too small for
      any group to reach 20 seats, no seat streams ahead of the batch and
      the methodology does not mention a lead.
    - A new row, **The warm lead's Cost self-checks line**, takes the
      checks that used to sit at the end of the Streamed lead seat row. The
      line reads `on · nothing checked yet` before any lead has been sent,
      and `on · last check: …` after one. It reads `off for this session`
      only beside the WARNING that switched it off, and then the next
      Final QC streams no lead. A restart switches it back on.
    - A new row, **Switching the warm lead off**: `"0"` set before start,
      in both shells. Then no seat streams ahead of the batch, every
      `cost_multiplier` is 0.5, the methodology has no lead step, the
      profiler has no `seat:list-price` row, and the Cost self-checks
      row's last line reads `Warm lead: switched off in settings`.
    - In the Chunk 4 section, the Cost self-checks row's clause about its
      last line now reads `Warm lead (Final QC): on · …`, the default since
      WL-2.
  - **`backend/cost_checks.py`'s module docstring**: with the checks in
    place, both savings default on, the tail since CT-3 and the warm lead
    since WL-2.
  - **The Tier 1 progress file** gains a dated note under CT-3's. Chunk 3's
    switch is on too since this PR, so neither switch the file says ships
    off still does, and neither flip waited on M3. Its closeout text is
    left as the record it is.
  - **The Tier 1 plan** gains two appended notes. The text they qualify is
    unchanged.
    - After §8's M3, a dated *M3 superseded* note: the trial was never
      run. Its procedure still works, and its two `= "1"` lines now set
      the default.
    - After §10.3, a *Superseded* note. §10.3 ends "That is why the chunk
      ships switched off, and its default flips only on an M3 pass".
- **The release-note draft** (WL-2.5), in
  `docs/plans/RESEARCH_QC_COST_TIER1_2026-09-23.md` §7:
  - Item 3 lost its conditional marker and gained one sentence:
    "Verification may start a few seconds later, and if this turns out not
    to help, the app stops doing it by itself until you restart it."
  - The "Which items" bullet says all four items go in any such release.
  - No version bump, no `backend/release_notes.py` entry, no tag (F7).
- **The methodology note, checked, unchanged (WL-2.4).** `docx_export`
  adds the "Streamed lead seat" step only
  `if qc_streamed_lead_seats(qc_result)`. The report modal adds it only when
  `qcStreamedLeadSeats(report) > 0`. Both read the records, never the
  setting. They are pinned by
  `test_the_methodology_sentence_is_the_same_in_both_projections` and by
  `frontend/tests/qcReport.test.ts`'s `qcStreamedLeadSeats` cases.

**Deviations from the spec, and why**

1. **The ships-off pin passed the first flip-readiness run.** The spec
   allowed it to fail ("Only `test_warm_lead_ships_switched_off` may
   fail"). It passed, as an `ast` pin must: the environment cannot reach
   it. CT-3's run showed the same.
2. **The floor-8 run failed two tests, and neither depended on the
   default.**
   - The spec reads any failure other than the pin as a test that depended
     on the default, fixed by passing the switch explicitly. These two
     already pass it, and both passed in the first run. What they pin is
     the shipped minimum of 20, which the plugin lowers.
   - Passing the switch would change nothing, and weakening them would
     unpin the minimum. So neither changed.
   - The Tier 1 closeout's run of this method left
     `tests/test_qc_batch_warm_lead.py` out for this reason (it ran "the
     QC tests outside the lead seat's test file"). The spec's command
     keeps the file in, so its two minimum pins fail by design. Outside
     that file, nothing failed.
3. **Phases were counted by wrapping two engine functions, beside the INFO
   lines.** The spec counts the `one streamed first` lines, but those count
   leads, not phases: a phase with two large lineages writes two. So the
   plugin also wrapped `_run_batch_calls` and `_pick_warm_leads`, to count
   phases, open gates and leads. Its lead count agrees with the lines (25).
4. **The copy sweep went past the spec's list.** The spec names the
   dossier, the release checklist's lead rows, and "any comment the grep
   found". The grep also found four more claims, and R9 says each must be
   true at this merge (every change is listed above):
   - `backend/cost_checks.py`'s module docstring;
   - the lead test file's module docstring;
   - the Tier 1 progress file, which said Chunk 3's switch still ships
     off;
   - the Tier 1 plan's §8 and §10.3, which make M3 the gate.

   The plans index (`docs/plans/README.md`) was left alone. Its "Chunks 3
   and 4 shipped off" is past tense, and its "Chunks 3 and 4 only if
   flipped by then" stays true, since both are. FIN-1 closes out that
   entry.
5. **§7 changed in three more places than item 3 and its bullet**, as
   CT-3's did for item 4:
   - the block's intro names what WL-2 added;
   - "Where it goes" drops "so leaving out a conditional item leaves a
     clean list", since no conditional item is left;
   - the summary sentence covers all four items.
6. **The dossier says a little more than the spec asks.** The spec lists
   what to say: that the lead is on, when it happens, that the report
   prices the seat at full price, and that the app stops if the batch does
   not read the seat's copy. The new sentence also says:
   - the second reason the app stops: the seat cost more than it could
     have saved;
   - where to see the result (Settings → Developer tools → Cost
     self-checks);
   - two numbers, "twenty or more seats" and "at most 45 seconds". These
     are the shipped lineage minimum (`_WARM_LEAD_MIN_SEATS_*`) and
     `QC_WARM_WAIT_SECONDS`'s default. The dossier's contract is that
     every number in it is real, so a change to either default must change
     this sentence too.

**Knowing changes to existing tests**

- `test_warm_lead_ships_switched_off` → `test_warm_lead_ships_switched_on`
  (WL-2.3).
- `tests/test_qc_batch_warm_lead.py`'s module docstring, its "off means
  off" bullet, now says the switch shipped off until WL-2. The tests under
  it are unchanged, since each sets the switch it runs itself.
- Nothing else. The flip-readiness runs found no test that depended on the
  default.

**The tests.** No new test. The pin is WL-2.3's replacement. Everything the
flip turns on was already tested with the switch passed explicitly (R7) by
Chunk 3 and WL-1: the lead, its pricing, every exit from the phase, the
check and its latch.

**Verification** (Linux container, from the repository root)

- `.venv/bin/python -m ruff check .`: all checks passed.
- `.venv/bin/python -m pytest -q` with the new default (no environment
  variable): 2990 passed, 64 skipped (406.73 s). WL-1 also ended at 2990:
  the pin was replaced, not added.
- The same suite with `BUILD_A_SPEC_QC_BATCH_WARM_LEAD=0`, to show that no
  test depends on the lead being on: 2990 passed, 64 skipped
  (406.37 s). The same count, because every test that runs a lead sets
  the switch itself (R7), and the pin reads the source.
- A third run checked that claim: the whole suite with the new default and
  the shipped minimums, counted by the same wrappers (no minimum lowered).
  Leads ran only in tests that set the switch themselves: 46 of 249
  batched phases picked one, 21 in `tests/test_qc_batch_warm_lead.py`, 23
  in `tests/test_cost_checks_warm_lead.py` and 2 in
  `tests/test_continuation_cache.py`. That run's one failure,
  `tests/test_preserved_chrome_lint.py::test_readiness_and_the_model_read_the_same_chrome`,
  came from `npm run build` rebuilding `frontend/dist` at the same moment
  (the app mounts `frontend/dist/assets` when it starts). The test passes
  on its own, and it passed in both full runs above.
- `npm test` (in `frontend/`): 438 passed, 0 failed.
- `npm run build`: built; the only warning is the existing chunk-size one.
- `tests/test_tier1_finish_tracker.py` and `tests/test_docs_consistency.py`
  pass with this As built and the ticks in place (18 passed).
- `git diff --name-only origin/master...HEAD -- CLAUDE.md README.md`
  prints nothing.

**Revert matrix.** Each mechanism was reverted in place, one at a time, by
a script: it replaced one exact snippet, ran the suites, restored the exact
text it read, and checked the file byte-identical. The working tree's diff
was byte-identical at the end. Every row ran
`tests/test_qc_batch_warm_lead.py`, `tests/test_cost_checks_warm_lead.py`,
`tests/test_qc_batch_verification.py` and `tests/test_qc_warm_launch.py`
(131 tests). "Red" counts failing tests.

5 rows, 5 red. The baseline passed 131 of 131, and after the last row the
suites passed 131 of 131 again, the new pin among them (WL-2.7).

| Mechanism reverted | Red |
|---|---|
| the default back to `False` | 1 (`test_warm_lead_ships_switched_on`) |
| `run_final_qc` reads an unset switch as off, not from the setting | 1 (`test_the_setting_reaches_the_run`) |
| the gate ignores the self-check's latch (WL-1) | 2 (`test_a_batch_that_writes_the_prefix_switches_the_lead_off_for_the_next_run`, `test_the_latch_stops_the_next_phase_picking_a_lead`) |
| the self-check never latches (WL-1) | 10 |
| no lead is ever picked | 40 (19 in the lead's own test file, 21 in WL-1's) |

The first two rows are the flip itself: the default, and `run_final_qc`
reading it when a caller passes nothing. The third and fourth show that
the default now switched on still runs through WL-1's gate and latch: take
either away, and a lead the batch does not read can no longer switch
itself off. The last shows that 40 of these tests really run a lead.

**For FIN-1**

WL-1's For FIN-1 list still stands, as do CT-1's, CT-2's and CT-3's. This
adds what the flip itself changes.

- **README** (`README.md`), in "## Research and Final QC cost (Tier 1)"
  unless another section is named:
  - **The paragraph "Chunks 3 and 4 are built but ship switched off …"**
    (about line 1076). CT-3's list rewrites it for Chunk 4. After WL-2 both
    are on by default, without the measured run, under the Tier 1 finish
    program's self-checks.
    - Its second sentence stays true: each rests on provider behaviour
      that only a real run can confirm. That is now why the self-checks
      watch real runs.
    - Its first sentence is false, and so are "Each turns on only when a
      measured run … until then changes nothing" and "so both stay off".
    - O6 still stands: no measured run is planned.
  - **The heading "### Final QC's batched review can warm its own copy
    first (Chunk 3, switched off)"** (about line 1160) becomes "on by
    default". In its intro, "With this switch on, when one of those
    groups…" becomes "When one of those groups…".
  - **Its bullet "Off by default, deliberately. … `=1` switches it on for
    a trial"** (about lines 1173–1178). It should say:
    - the lead has been on by default since WL-2;
    - after each Final QC that sent a lead, the app reads how many of the
      batched seats read the lead's copy, and if fewer than half did, or
      the lead cost more than it could have saved, the lead switches off
      until the app restarts (WL-1);
    - `=0` switches it off, in both the PowerShell and the Command Prompt
      forms of `"0"`.

    WL-1's For FIN-1 README item says the same about the check; fold the
    two into this one bullet.
  - **"What changes when it is on"** (about line 1179) becomes "What
    changes".
  - **"Measured, not modelled"** (about lines 1202–1207): "on a Final QC
    made with the switch on" becomes "on a Final QC that sent a lead". Add
    that Developer tools → Cost self-checks shows the last check.
  - **"Where to see it"** (about lines 1208–1211): beside the lead's own
    line, add the check's one INFO line per judged lineage, and the one
    WARNING it writes when it switches the lead off.
  - **"(and so does a streamed lead seat, when
    `BUILD_A_SPEC_QC_BATCH_WARM_LEAD` is on)"** (about line 2118, in
    "## Shipped in v0.9.0 (Batch 4: Final QC) and still current"): the lead
    is on by default, so "(and so does a streamed lead seat)".
  - **The Configuration rows**, in "## Configuration":
    - `BUILD_A_SPEC_QC_BATCH_VERIFICATION` (about line 2829): "except a
      streamed lead seat's when `BUILD_A_SPEC_QC_BATCH_WARM_LEAD` is on"
      becomes "except a streamed lead seat's
      (`BUILD_A_SPEC_QC_BATCH_WARM_LEAD`)".
    - `BUILD_A_SPEC_QC_BATCH_WARM_LEAD` (about line 2830): the default is
      `1`, and "**off** until a measured run shows it pays" goes. Say that
      WL-1's check switches the lead off for the rest of the session when
      the batch does not read its copy or it cost more than it could have
      saved, and what `0` does.
- **CLAUDE.md implemented notes**: a section "The warm lead is on by
  default — implemented notes (Tier 1 finish, WL-2)", in CLAUDE.md's own
  style. FIN-1 may fold it into its one consolidated section. The why and
  the traps:
  - *Why it could flip without M3* (FD1). WL-1's check switches the lead
    off when the batch does not read its copy (h₁ < 0.5) or when the lead
    cost more than it could have saved (h₀* ≤ 0). It reads only usage the
    batch already reported. What it cannot see is bounded: a lead that is
    read but was not needed costs about its own discount ($0.20–$0.35 per
    large lineage per run); the batch waits at most
    `QC_WARM_WAIT_SECONDS`; and a failed lead is an ordinary failed seat.
    Merging this PR was Abraham's approval.
  - *The pin reads the source.* The flip-readiness run with `=1` passed
    the old ships-off pin, as CT-3's run did. The new pin is the same
    `ast` walk.
  - *A floor-8 run must leave the lead's own test file out, or expect two
    failures.* `test_a_lineage_below_the_minimum_has_no_lead` and
    `test_the_switch_off_is_todays_batch_exactly` pin the shipped minimum
    of 20. At the floor they fail by design. The closeout's method left
    the file out; WL-2's spec command kept it in.
  - *Count phases, not INFO lines.* `one streamed first` is written once
    per lead, and one phase can send two. And
    `test_the_lineage_minimums_are_never_below_eight` calls
    `_pick_warm_leads` directly, so a wrapper around it also counts two
    calls that are not phases.
  - *At the floor, the check never judges an 8-seat lineage* (7 batched
    seats; WL-1's deviation 7). So a floor-8 run exercises the lead, not
    the latch.
- **Layout.**
  - `backend/settings.py` (the entry's lines 143–147): `QC_BATCH_WARM_LEAD`
    "(BUILD_A_SPEC_QC_BATCH_WARM_LEAD, default OFF — cost Tier 1 Chunk 3's
    streamed lead seat; flips on only on a recorded M3 pass; …)" becomes
    "default ON since the Tier 1 finish program's WL-2; WL-1's warm-lead
    check can only remove it, until a restart; 0 switches it off". WL-1's
    For FIN-1 corrects the same entry's M3 clause; the two edits are one.
  - `tests/test_qc_batch_warm_lead.py`: "and the default read from the
    source" becomes "and the default (on) read from the source".
- **Errata** for earlier CLAUDE.md sections:
  - "Final QC's batched phase can stream a lead seat first — implemented
    notes (Research/QC cost Tier 1, Chunk 3)":
    - "**It ships switched off.**" It has been on by default since WL-2.
      WL-1's For FIN-1 already corrects the same section's "waits for an
      M3 pass".
    - "Flip readiness, measured once" says every QC test outside the new
      file (369) passed at the floor. WL-2's re-run: 419 outside the file,
      none failed.
    - Its erratum 1 says "with `QC_BATCH_WARM_LEAD` on, one seat per large
      lineage IS streamed". That is now the default.
  - "Research and Final QC cost, Tier 1, as shipped — implemented notes
    (closeout)":
    - "Chunk 3 … It ships off (`BUILD_A_SPEC_QC_BATCH_WARM_LEAD`)": on by
      default since WL-2.
    - "**Two switches ship off, for different reasons.**" and "Each
      default flips only on an M3 pass": both are on (CT-3 and WL-2), and
      neither flip waited on M3 (FD1). CT-3's list says only Chunk 3's is
      off until WL-2; after WL-2, neither is.
    - "items 3 and 4 only if their switch defaults on in the release":
      all four items ship since WL-2.
    - "3 of 138 batch phases picked a lead": WL-2's re-run of the same
      method counted 3 of 180 outside the lead's own file (more tests
      since).
- **The plans index and the Tier 1 progress file.** FIN-1's own step 5
  closes these out: `docs/plans/README.md`'s Tier 1 entry ("Chunks 3 and 4
  only if flipped by then": both are) and the progress file's "After the
  program" table. WL-2 added only the progress file's dated note.

---

## Appendix B: the warm-lead check's arithmetic

**B.1 Rates.** w₁ is the QC model's 1-hour cache-write rate and r its
cache-read rate (`settings.PRICING` through `usage_ledger`; on Claude
Opus 5.5, $8.00 and $0.20 per million), and b is
`settings.BATCH_COST_MULTIPLIER` (0.5). Δ = w₁ − r.

**B.2 One seat.** A verifier seat's request is tools, system, user block 0
(the shared prefix, with the last explicit breakpoint on it) and block 1
(the seat's own finding, uncached). Its first model iteration therefore
reads or writes exactly the lineage's shared prefix: read + write = p.
- It read the prefix when read / (read + write) ≥ 0.95. The 5% slack
  covers a small part written because an inner breakpoint (tools, system)
  missed while block 0 hit.
- It cannot say whose copy it read: the lead's, another seat's, or an
  entry from an earlier run within the hour. B.6 says why that is enough.

**B.3 One lineage.**
- h₁ = the share of measured batched seats that read the prefix.
- p = the median of read + write over the measured seats.
- C = the lead's list-price cost (every billed response, multiplier 1.0).
- n = the lineage's seats, counting the lead.

**B.4 Break-even.** Tier 1 §10.3's M3 test says the lead paid when

> (n − 1)·b·(h₁ − h₀)·Δ·p > (1 − b)·C + b·h₀·Δ·p

where h₀ is what the batch would have read without the lead. The right
side is the lead's extra cost: its lost discount on everything it did, plus
the discounted read a batched seat in its place would have had. Solving for
h₀, the lead paid if and only if h₀ < h₀*, where

> h₀* = [(n − 1)·b·h₁·Δ·p − (1 − b)·C] / (n·b·Δ·p)

**B.5 The rules.**
- h₀* ≤ 0 means the lead lost money even if the batch alone would have read
  nothing: `unprofitable`.
- h₁ < 0.5 means the batch is not reading the lead's copy. If a streamed
  entry were readable by the batch, every seat submitted after the lead's
  first output could read it, so h₁ would sit near 1: `not_read`. That
  rule is a heuristic, and it errs cheaply. A false latch loses the lead's
  saving until the app restarts; a missed one costs one lead's discount per
  run.

**B.6 What it cannot see, and what that costs.** h₀ is unknown. A lead that
is read (high h₁) but unnecessary (the batch would have read the prefix
anyway, high h₀) is kept. Its cost is (1 − b)·C + b·h₀·Δ·p. At p = 40k
input tokens, o = 4k output tokens and h₀ = 0.9 on Opus 5.5, that is:
- C ≈ 40k × $8/M + 4k × $20/M = $0.40;
- (1 − b)·C = $0.20;
- b·h₀·Δ·p = 0.5 × 0.9 × $7.80/M × 40k ≈ $0.14;
- so about $0.34 per large lineage per run.

When the batch would otherwise read little, the lead saves much more. At
h₀ = 0.3, a 20-seat lineage whose batch then reads everything (h₁ = 1)
saves (n − 1)·b·(h₁ − h₀)·Δ·p = 19 × 0.5 × 0.7 × $0.312 ≈ $2.07, less that
extra cost.
