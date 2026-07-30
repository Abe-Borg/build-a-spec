# Phase 4 — Cache and metering accuracy

- Status: in progress (4.1-4.3 landed; 4.4 planned)
- Prerequisites: Phases 1-3 complete
- Risk: high financial/audit impact; follow the chunk order exactly

## Goal

Make interview caching incremental across user turns, keep entries alive for
the app's observed turn pacing, and price/meter the resulting traffic honestly.
Also retain spend when every research dimension fails and provide a disclosed
best-effort output estimate when the user stops before the provider's terminal
usage delta.

## Required order

Chunk 4.1 must land before 4.2. Once chat emits one-hour cache writes, the
ledger must already understand their provider subtotal and price. Shipping the
TTL change first would deliberately expand the known undercount.

## Chunk 4.1 — Per-TTL cache usage and pricing

### Data contract

Use the plain usage key `cache_creation_1h_input_tokens` for the provider's
`usage.cache_creation.ephemeral_1h_input_tokens` subtotal. The existing
`cache_creation_input_tokens` remains the total across TTL classes.

Cost math must be:

```text
one_hour = cache_creation_1h_input_tokens
five_minute = cache_creation_input_tokens - one_hour
cache cost = five_minute * cache_write + one_hour * cache_write_1h
```

Never charge the one-hour subtotal a second time on top of the total.

### Implementation

1. Add `cache_write_1h` at `2.0 * input` to every model record in
   `backend/settings.py::PRICING`; keep `cache_write` as the 5-minute `1.25x`
   rate. Update the pricing comment.
2. Extend `backend/usage_ledger.py::usage_to_dict` to read nested
   `cache_creation.ephemeral_1h_input_tokens` from both SDK objects and dict
   fakes.
3. Extend `backend/llm/conversation.py::_merge_usage` with the same nested
   subtotal because chat aggregates usage without calling `usage_to_dict`.
   Prefer a small local accessor consistent with the module's existing fake
   support; do not import a private QC helper.
4. Update `estimate_usage_cost` using the non-double-counting formula. Guard
   malformed live values by clamping the 1h subtotal into `[0, total]`; persisted
   audit validation below must reject impossible values rather than silently
   clamp them.
5. `usage_pricing_snapshot` should include `cache_write_1h` in
   `rates_per_token` and state in its explanatory text that the provider's
   one-hour subtotal is priced separately.
6. In `backend/qc/engine.py`:
   - let `_persisted_cost_basis` accept both the legacy four-rate shape and the
     new five-rate shape;
   - preserve the shape read from an old report rather than rewriting its
     claimed immutable basis;
   - use `rates.get("cache_write_1h", rates["cache_write"])` for old records;
   - apply the same non-double-counting formula in
     `_estimated_cost_from_basis`; and
   - make current/new report accounting inconsistent when the persisted 1h
     subtotal exceeds total cache creation.
7. Old reports with neither the subtotal nor the new rate must deserialize and
   reproduce their saved estimate. New reports must carry both.
8. Update report cost-basis wording if the generic Word renderer does not make
   the two rates clear enough.
9. Add golden tests for pure 5m, pure 1h, mixed, zero, malformed nested input,
   old cost-basis load, new cost-basis round trip, aggregate lens/verifier
   reconciliation, and the exact configured rates for every model.

### Files

- `backend/settings.py`
- `backend/usage_ledger.py`
- `backend/llm/conversation.py`
- `backend/qc/engine.py`
- `backend/spec_doc/docx_export.py` if wording changes
- `tests/fakes.py` usage objects
- `tests/test_usage.py`
- `tests/test_qc_audit_report.py`
- `tests/test_qc_runner_audit_integrity.py`
- `README.md`
- `CLAUDE.md`

### Focused verification

```powershell
venv\Scripts\python -m pytest -q tests/test_usage.py tests/test_qc_audit_report.py tests/test_qc_runner_audit_integrity.py tests/test_qc.py
```

### Acceptance criteria

- One million Opus 5 one-hour cache-write tokens estimate at the configured
  2x-input rate, not the 5-minute rate.
- Mixed totals are not double-counted.
- Old v3 reports without the new field remain readable and self-consistent.
- New QC reports reconcile per-record and aggregate usage under the saved
  five-rate basis.

### Implementation record

- Status: **complete**
- Commit/PR: see the branch `claude/deep-dive-remediation-4-1-thvbrv`
- Tests: `tests/test_usage.py` (11 new — the per-model rate matrix, pure 5m,
  pure 1h at 2x input, mixed-not-double-counted, zero, clamped malformed
  subtotal, ledger accrual, nested read plus absent-key identity, malformed
  nested object, snapshot rate + wording, and the subtotal through a real
  `/api/chat` turn); `tests/test_qc_audit_report.py` (5 new — a verifier
  seat's subtotal captured/priced/round-tripped, legacy basis loads and
  reproduces its estimate, legacy basis plus a subtotal prices
  conservatively, impossible subtotal rejected per-record and in aggregate,
  unknown rate/field still refused); `tests/test_qc_manifest_integrity.py`
  (1 new — pricing is not part of reviewed input identity). Focused command
  green, then the full suite: **1239 passed, 9 skipped**. Each new mechanism
  was reverted in place to prove it load-bearing (the impossible-subtotal
  guard → 1 red; the split formula → 1 red).
- Deviations:
  - **Item 5's "explanatory text" became a new `cache_write_treatment`
    field** rather than an edit to `thinking_token_treatment`/`authority`.
    It is the exact sibling of `thinking_token_treatment` — both explain an
    "already inside that number, do not charge it twice" decision — and
    both report renderers are generic (`docx_export` iterates `cost_basis`
    items, `QCReportModal` prints a JSON block), so it reaches Word and the
    modal with no renderer change. That is also why **item 8 needed no
    work**: the new field is what makes the two rates clear.
  - **`_persisted_cost_basis` had to accept two TOP-LEVEL shapes**, not just
    two `rates_per_token` shapes, because the new field is a top-level key
    and the validator asserts exact set equality. Legacy nine-key/four-rate
    and new ten-key/five-rate are both accepted and echoed back verbatim;
    an unknown rate key or unknown top-level field is still refused (pinned).
    **Review follow-up (PR #99, Codex):** the two sets must be validated as
    a PAIR. Validated independently they admit two hybrids, and one is the
    forged claim this validator exists to refuse — a basis keeping
    `cache_write_treatment` while dropping `cache_write_1h` prices 1M
    one-hour tokens at $6.25 instead of $10.00 while its own text promises
    per-TTL pricing. Fixed with `_COST_BASIS_SHAPES` and pinned by
    `test_a_half_migrated_cost_basis_is_refused`. The same review pass
    exposed a test-side flaw: `to_dict` shallow-copies `cost_basis`, so
    in-place mutation of `rates_per_token` leaks across cases — every case
    now starts from one `copy.deepcopy(baseline)`.
  - **Two split helpers, deliberately not one.**
    `usage_ledger.cache_write_split` clamps (live provider data should skew
    an estimate, never invert it); `qc.engine._cache_write_tokens_by_ttl`
    does not, so an impossible persisted subtotal reaches
    `_audit_accounting_consistent` and fails there. This is the plan's
    "clamp live, reject persisted" rule made structural.
  - **No frontend change.** The Settings usage table shows the
    cache-creation TOTAL, which the subtotal is part of, so it stays
    correct; only the dollar figure moved. Breaking the TTL split out in the
    UI belongs with Chunk 4.4's disclosure work. `npm test` / `npm run
    build` therefore not run (no `frontend/` file touched).
  - **Retained QC results do not go stale**, verified against
    `build_qc_input_manifest`: `cost_basis` is absent from the hashed
    manifest and `configuration` carries no rate. Pinned by a new test so
    the claim cannot rot.
- Manual QA owed: the phase-level item — compare a verifier-heavy run's
  recorded one-hour subtotal and rate against the provider usage/billing
  export. Needs a paid live QC run and owner approval; the app remains a
  list-price estimate, not an invoice.

## Chunk 4.2 — Rolling committed-history breakpoint and one-hour TTL

### Cache layout

Each chat request should contain copy-on-write breakpoints at:

1. the stable system block (which also closes the stable tools+system prefix);
2. the last eligible content block of committed `session.history`, when history
   exists; and
3. the tail of the full current request, including the fresh PROJECT CONTEXT
   and continuation messages.

Breakpoints 1 and 2 use `BUILD_A_SPEC_CHAT_CACHE_TTL` (default `"1h"`), so
the policy can be re-tuned against real session economics without a code
change. Breakpoint 3 — the tail — is pinned to the **shortest** supported
TTL and is not configurable: it is keyed on the fresh PROJECT CONTEXT, which
commit strips, so no later turn can read it and a long lifetime there is
paid for and never used.

The request is therefore **non-increasing in TTL** rather than uniform.
Mixed TTLs impose a provider ordering constraint — longer-lived entries must
precede shorter-lived ones — whose violation is a nonretryable 400, the
exact failure mode PR #82's review caught. Pinning the tail to the shortest
TTL and refusing to expose it as a knob makes that violation unbuildable at
any setting, which is a stronger guarantee than uniformity gave. Stored
history never carries `cache_control`.

### Implementation

1. Copy-adapt QC's `_cache_control` shape into
   `backend/llm/conversation.py`; do not import a QC-private helper.
2. Make `_stable_system_blocks` and `_with_tail_cache_breakpoint` accept/use one
   cache TTL source, read from the new setting (default one hour). Validate the
   setting to the provider-supported values and fall back loudly to the
   default on an invalid value.
3. Add a copy-on-write committed-history helper or replace the tail helper with
   a clearly named multi-breakpoint builder. It must receive the committed
   history boundary explicitly rather than trying to infer it from roles.
4. Request construction should snapshot `history = list(session.history)`,
   sanitize `history + new_messages`, place a breakpoint at the last content
   block belonging to that history prefix, then place the tail breakpoint.
   If history is empty, only the tail is added. If both boundaries ever resolve
   to the same block, attach one annotation.
5. Ensure PDF/reference/figure elision does not change message counts used to
   locate the boundary. If it can, carry an explicit marker/index through the
   sanitizer rather than guessing.
6. Keep the request under the provider's breakpoint limit. Do not add an
   independent tool breakpoint unless inspection of the rendered API request
   proves it is necessary; the system breakpoint already covers preceding
   tools.
7. Rewrite the module and helper docstrings to describe the actual rolling
   boundary, strip-at-commit divergence, one-hour economics, and residual
   provider lookback limitation for unusually block-heavy turns.
8. Update the existing exact-dict tests in `tests/test_app.py` and
   `tests/test_session_modules.py` from bare ephemeral to one hour.
9. Add regression tests across at least three user turns:
   - request N writes a tail entry;
   - request N+1 contains a request-only breakpoint on the committed-history
     boundary plus its new tail;
   - the shared committed bytes are exactly the previous history prefix;
   - every cache-control dict in a request uses the same TTL;
   - continuation rounds retain the tail behavior; and
   - `session.history` and a saved project contain no `cache_control`.

### Files

- `backend/llm/conversation.py`
- `backend/settings.py` (`BUILD_A_SPEC_CHAT_CACHE_TTL`)
- `tests/test_app.py`
- `tests/test_session_modules.py`
- `tests/test_project_package.py`
- `CLAUDE.md`
- `README.md` if cache policy is user-facing there

### Focused verification

```powershell
venv\Scripts\python -m pytest -q tests/test_app.py tests/test_session_modules.py tests/test_project_package.py tests/test_usage.py
```

### Acceptance criteria

- A second/third turn has a committed-history breakpoint before the fresh
  context and a tail breakpoint after it.
- Chat breakpoints are non-increasing in TTL at every setting: system and
  the committed-history boundary carry the configured TTL (default one
  hour), the tail carries the shortest supported one, and no setting can
  build an out-of-order (short-before-long) request.
- Stored/saved history remains annotation-free.
- Context refresh, thinking preservation within a turn, PDF elision, and turn
  atomicity remain unchanged.

### Implementation record

- Status: **complete**
- Commit/PR: branch `claude/deep-dive-remediation-4-1-thvbrv` (restarted from
  master after the 4.1 PR merged)
- Tests: `tests/test_app.py` (8 new — the rolling layout across three turns
  with exact marked-message indexes and last-block-only placement; the
  byte-prefix cache-read condition asserted directly; uniform TTL;
  continuation rounds; nothing surviving into history or a saved project;
  the TTL setting's validation + loud fallback; the boundary helper's
  fail-safe; and the sanitizer message-count invariant). Five existing
  exact-dict assertions updated from bare `ephemeral` to
  `{"type": "ephemeral", "ttl": "1h"}` across `test_app.py`,
  `test_runtime_date.py` and `test_session_modules.py`. Full suite:
  **1249 passed, 9 skipped**. Both mechanisms reverted in place to prove
  them load-bearing (tail-only → 2 red; mixed TTL → 5 red).
- Deviations:
  - **Item 5 needed no marker threading.** The sanitizer provably cannot
    change message count — the pairing pass replaces entries positionally
    and refills an emptied assistant message with a placeholder rather than
    removing it, and PDF elision rebuilds only affected messages. So the
    boundary is plain index arithmetic. It is still *checked*
    (`_committed_history_boundary` compares raw vs sanitized length and
    returns `-1` on any mismatch), and the invariant it rests on has its
    own test, so a future sanitizer change degrades to the old tail-only
    behavior instead of annotating the wrong message.
  - **Item 6 confirmed by inspection, no tool breakpoint added.** A rendered
    request carries exactly three breakpoints (system + boundary + tail),
    inside the limit of four; tools render ahead of system, so the system
    breakpoint already closes them. Asserted, not assumed.
  - **`test_project_package.py` needed no change** — the "no `cache_control`
    in a saved project" claim is covered end-to-end through
    `GET /api/project/save` in the new `test_app.py` case, which exercises
    the real save path rather than the serializer in isolation.
  - **The tail TTL split (PR #100 review, Codex).** Frozen decision 7 called
    for one uniform TTL; the review pointed out that the tail is keyed on
    bytes commit strips, so its entry can only ever be read by continuation
    rounds inside the same turn — a one-hour lifetime there is bought and
    never used, at 2.0x input to write against 1.25x, on a block the size of
    the whole document (~$0.02-0.11/turn depending on document size). The
    provider's constraint is only SHORT-before-LONG, so `1h/1h/5m` is legal.
    Owner decision: take the saving. Implemented so the safety property
    survives — the tail is pinned to the SHORTEST supported TTL and
    deliberately not env-overridable, so no setting can build an
    out-of-order request. Decision 7 and the acceptance criterion are
    amended above; `test_no_setting_can_build_an_out_of_order_request`
    sweeps every supported setting and is stronger than the uniformity
    check it replaces (reverting the pin turns it red).
  - **Docstring corrections were part of the fix, not cosmetic.** The module
    docstring and the CLAUDE.md context-architecture bullet both asserted
    that the tail breakpoint made history "cache incrementally". That was
    the false claim the review found; both now describe the actual rolling
    boundary, the strip-at-commit divergence that defeats a tail-only
    layout, the one-hour economics, and the 20-block lookback residual.
- Manual QA owed: the phase-level item — capture two ordinary chat turns
  more than five minutes apart and confirm diagnostics show the large
  committed prefix moving from cache creation to cache read while only the
  incremental suffix writes. Needs a real API key and owner approval.

## Chunk 4.3 — Meter total research failure and cancellation

### Implementation

1. Extend `ResearchFanoutError` with `usage_totals: dict[str, int]`, matching
   the useful parts of `QCFanoutError`'s design.
2. At the all-dimensions-failed raise site, aggregate the already-recorded
   `DimensionStatus` usage keys using the same fields as
   `RequirementsProfile.usage_total`. Include retries and server-tool request
   counts already folded into each status.
3. In `ResearchRunner`'s `except ResearchFanoutError` branch, call
   `usage_sink(exc.usage_totals)` before resolving the runner. Meter even if a
   concurrent stop has already won: the spend belongs to the session unless a
   generation/workspace guard at the app sink rejects it.
4. Preserve auth-error classification and failure text.
5. Add tests for:
   - four failed dimensions with nonzero distinct usage;
   - retries included once;
   - all dimensions cancelled through stop;
   - no usage/no-key failures remaining no-ops; and
   - earlier accumulated research rounds not being billed again.

### Files

- `backend/research/engine.py`
- `backend/research/runner.py`
- `tests/test_research_engine.py`
- `tests/test_research_rounds.py`
- `tests/test_usage.py`
- `tests/test_stop.py`
- `CLAUDE.md`

### Focused verification

```powershell
venv\Scripts\python -m pytest -q tests/test_research_engine.py tests/test_research_rounds.py tests/test_usage.py tests/test_stop.py
```

### Acceptance criteria

- An all-failed or all-cancelled round contributes its exact captured usage
  once to the research category.
- Earlier successful rounds are not re-metered.
- Runner/profile resolution semantics remain unchanged.

### Implementation record

- Status: **complete**
- Commit/PR: branch `claude/deep-dive-phase-4-cont-o2up2c`
- Tests: 9 new. `tests/test_research_engine.py` (5 — four failed dimensions
  with distinct usage asserted as an exact dict; a retried attempt counted
  exactly once; a failure that never reached the provider carrying no bill;
  the key-tuple guard against a future `DimensionStatus` usage field; and
  both callers proven to be one computation).
  `tests/test_research_rounds.py` (2 — a totally failed round metered, and
  success → total failure → success billing 100 · 28 · 40 while the
  profile's cumulative total stays 140).
  `tests/test_stop.py` (1 — the pre-CAS ordering, via a barrier client that
  holds all four dimensions at their first call so the stop lands after
  each has banked a billed paused response).
  `tests/test_usage.py` (1 — the spend reaching `/api/usage`'s research
  category end to end). Focused command green, then the full suite:
  **1259 passed, 9 skipped**. Both mechanisms reverted in place to prove
  them load-bearing: dropping `usage_totals` from the raise → 6 red;
  gating the meter on winning the CAS → 1 red (the stop test, and only it).
- Deviations:
  - **Item 2 became a shared helper rather than an aggregation written at
    the raise site.** The plan says "using the same fields as
    `RequirementsProfile.usage_total`"; writing a second loop over the same
    tuple satisfies that on the day and drifts later, and the failing path
    is the one nobody watches. `dimension_usage_total(statuses)` is now the
    single definition and `usage_total()` delegates to it, so "the same
    fields" is structural. `test_every_recorded_usage_field_reaches_the_
    meter` additionally compares `_DIMENSION_USAGE_KEYS` against the
    dataclass's own `*_tokens`/`*_requests` fields, so a usage field added
    to `DimensionStatus` without wiring is a red test rather than a silent
    undercount.
  - **`cache_creation_1h_input_tokens` is deliberately not in the key
    tuple.** Research writes no one-hour cache entries (only Final QC's
    verifier seats do), so Chunk 4.1's per-TTL split has nothing to
    separate here and `DimensionStatus` has never recorded the subtotal.
    Noted in a comment so a future reader does not read the omission as an
    oversight.
  - **Item 1's `QCFanoutError` parity stops at `usage_totals`.** No
    `result` field: QC preserves a terminal partial record, research stop
    is lossy by design and there is no `RequirementsProfile` to retain
    (the plan's own acceptance criteria keep resolution semantics
    unchanged).
  - **The empty case is a guard, not a zero row.** The runner meters only
    `if usage_sink is not None and exc.usage_totals`, matching QC — a
    module with no declared dimensions raises before any request, and a
    zero-valued ledger entry would show up as a fake turn in the meter.
  - **The generic `except Exception` branch still does not meter**, having
    no usage to report. The plan does not ask it to.
  - **`tests/test_usage.py` gained two small helpers**
    (`_seed_research_session`, `_wait_research`) extracted from the
    existing `test_research_run_rolls_up_into_ledger` so the new
    end-to-end case does not duplicate ~20 lines of profile setup and a
    poll loop. The existing test now uses both. Its one behavior change is
    that `_wait_research` RAISES when the run never reaches the expected
    status, where the inlined loop fell through and failed later on a
    confusing usage assertion — strictly better diagnostics, same pass/fail.
  - **No frontend change.** `/api/usage` already reports the research
    category and the header ticker/Settings table already read it, so the
    newly-landing spend surfaces with no UI work. `npm test` / `npm run
    build` therefore not run (no `frontend/` file touched). Worth noting
    that `TrustDeepDiveModal`'s Money section already promised this —
    "Money spent on work that failed or was stopped is still real, and the
    meter records it rather than quietly writing it off" — so the chunk
    makes a shipped user-facing claim true rather than requiring new copy.
- Manual QA owed: with owner approval, stop a real research round mid-flight
  (or let one fail against a revoked key after some spend) and confirm the
  header ticker and the Settings usage table move by roughly the abandoned
  round's cost. The hermetic tests prove the usage reaches the ledger and
  the arithmetic, not that the provider billed what the statuses recorded.

## Chunk 4.4 — Disclosed stopped-turn output estimate

### Constraint

When a stream is closed immediately, the SDK snapshot usually lacks the final
`message_delta` output count. Exact provider output usage is unavailable. The
fix must improve the estimate without presenting it as exact.

### Separation rule (binding)

Provider-reported token counts and heuristic estimates never share a field.
`output_tokens` always holds exactly what the provider reported — on a stopped
turn that is the snapshot placeholder, and it stays that way. The estimate
lands in a separate `estimated_output_tokens` counter with an explicit
disclosure flag, and every aggregate that includes it says so. Blending
`ceil(chars / 4)` into a provider total would corrupt the one number that can
be reconciled against provider records.

### Implementation

1. Add a deterministic private estimator for the serialized assistant content
   accumulated in `current_message_snapshot`. Include text, thinking, and
   model-generated tool/server-tool input; do not include user/tool-result
   input. A conservative `ceil(character_count / 4)` heuristic is acceptable
   unless the installed SDK exposes a reliable running output count.
2. On `stopped_mid_stream` only:
   - record `estimated_output_tokens = max(0, estimate - reported)` beside the
     untouched provider-reported usage — never overwrite or inflate
     `output_tokens`;
   - set `usage_estimated: true` on the round/turn usage record;
   - have the ledger carry the estimated component as its own labeled bucket:
     priced at the same output rate but reported as an estimated addition in
     `/api/usage`, the header ticker, and the Settings usage table (which
     already label totals as estimates); and
   - use reported-plus-estimated for the last-round context gauge, documented
     as an upper estimate.
3. Propagate the disclosure end to end: `turn_complete.usage`, the round trace
   event, and any aggregate that includes an estimated component must carry
   the flag/counter so downstream consumers can separate exact from estimated
   spend.
4. Do not estimate on normal terminal responses; exact provider usage wins and
   the estimated counter stays absent/zero.
5. Add tests with a long stopped snapshot, a tiny provider placeholder, a
   provider count larger than the heuristic, thinking/tool input, and a normal
   end turn. Assert that `output_tokens` always equals the provider-reported
   value, that the estimated counter is separate and disclosed, and
   monotonicity — not tokenizer-level exactness.
6. Update ledger/docs wording so session cost remains explicitly an estimate
   and names the estimated-output component.

### Files

- `backend/llm/conversation.py`
- `backend/tracing/capture.py` if its typed turn event needs the disclosure
- `tests/test_stop.py`
- `tests/test_usage.py`
- `tests/test_tracing.py`
- `README.md` and `CLAUDE.md`

### Focused verification and phase gate

```powershell
venv\Scripts\python -m pytest -q tests/test_stop.py tests/test_usage.py tests/test_tracing.py
```

Then run the full standard verification commands from the master plan.

### Acceptance criteria

- Stopping after substantial output no longer under-reports spend: the
  estimated component is visible in usage and cost surfaces.
- `output_tokens` always equals the provider-reported value; the estimate
  lives only in `estimated_output_tokens`.
- Normal completed responses use exact usage unchanged.
- Every estimated record is explicitly labeled estimated.
- Estimated output is counted once in cost and context metrics.

## Phase 4 manual QA

- With owner approval, capture two otherwise ordinary chat turns more than five
  minutes apart. Confirm diagnostics show the large committed prefix moving
  from cache creation to cache read while only the incremental suffix writes.
- Compare a verifier-heavy run's recorded one-hour cache subtotal/rate against
  the provider usage/billing export. The app remains a list-price estimate, not
  an invoice.
- Stop a long output and confirm the cost ticker increments plausibly and the
  trace marks the output usage as estimated.
