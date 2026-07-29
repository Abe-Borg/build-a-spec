# Phase 4 — Cache and metering accuracy

- Status: planned
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

- Status: planned
- Commit/PR:
- Tests:
- Deviations:
- Manual QA owed:

## Chunk 4.2 — Rolling committed-history breakpoint and one-hour TTL

### Cache layout

Each chat request should contain copy-on-write breakpoints at:

1. the stable system block (which also closes the stable tools+system prefix);
2. the last eligible content block of committed `session.history`, when history
   exists; and
3. the tail of the full current request, including the fresh PROJECT CONTEXT
   and continuation messages.

All three use the same TTL, sourced from one setting
(`BUILD_A_SPEC_CHAT_CACHE_TTL`, default `"1h"`), so the policy can be
re-tuned against real session economics without a code change. The TTL is
**uniform within every request** regardless of the configured value — mixed
TTLs impose a provider ordering constraint (longer-lived entries must precede
shorter-lived ones) whose violation is a nonretryable 400, the exact failure
mode PR #82's review caught. Stored history never carries `cache_control`.

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
- All chat breakpoints share the configured TTL (default one hour); no
  mixed-TTL request can be built at any setting.
- Stored/saved history remains annotation-free.
- Context refresh, thinking preservation within a turn, PDF elision, and turn
  atomicity remain unchanged.

### Implementation record

- Status: planned
- Commit/PR:
- Tests:
- Deviations:
- Manual QA owed:

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

- Status: planned
- Commit/PR:
- Tests:
- Deviations:
- Manual QA owed:

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
