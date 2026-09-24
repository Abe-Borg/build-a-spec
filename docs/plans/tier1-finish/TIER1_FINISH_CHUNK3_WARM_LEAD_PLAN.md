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

*Not started.*

When you build this session, append here:
- what was built;
- every deviation from this spec, and why;
- every knowing change to an existing test;
- the revert matrix (mechanism → tests red);
- **For FIN-1:** the CLAUDE.md implemented-notes material (the why and the
  traps, in CLAUDE.md's own style), the Layout entries to add or change,
  any erratum an earlier CLAUDE.md section now needs, and anything the root
  README must say.

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

*Not started.*

When you build this session, append here what WL-1's As built lists, plus
both flip-readiness runs' counts.

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
