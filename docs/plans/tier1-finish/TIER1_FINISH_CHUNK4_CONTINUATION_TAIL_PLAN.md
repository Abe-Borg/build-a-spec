# Tier 1 finish — Chunk 4: make the continuation tail safe, measure it, turn it on

Owner: Abraham. Opened 2026-09-24.

**Where this program stands lives in
[`TIER1_FINISH_TRACKER.md`](TIER1_FINISH_TRACKER.md) and nowhere else.**
The tracker also holds the session procedure, the rules (R1–R10), the
decision log (FD1, FD2, …), the handoff prompt and the closeout session
(FIN-1). This file is the spec for three sessions: **CT-1, CT-2 and CT-3**.
Its sibling, [`TIER1_FINISH_CHUNK3_WARM_LEAD_PLAN.md`](TIER1_FINISH_CHUNK3_WARM_LEAD_PLAN.md),
is the spec for WL-1 and WL-2. The sessions run in the tracker's order —
CT-1, CT-2, CT-3, WL-1, WL-2, FIN-1 — one session per chat, one pull request
per session.

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
4. [Shared infrastructure: `backend/cost_checks.py`](#4-shared-infrastructure-backendcost_checkspy)
5. Sessions — [CT-1](#ct-1--survive-a-rejected-continuation-tail) ·
   [CT-2](#ct-2--measure-what-the-continuation-tail-saves) ·
   [CT-3](#ct-3--turn-the-continuation-tail-on)
6. [Appendix A: the value check's arithmetic](#appendix-a-the-value-checks-arithmetic)

---

## 1. What this plan finishes

- Tier 1 Chunk 4 (PR #215, merge `f3aaf88`) added the **continuation
  tail**: a top-level `cache_control: {"type": "ephemeral"}` on every
  streamed research and Final QC request that resumes a `pause_turn`. The
  resumed request then reads the cache entries the provider already wrote,
  instead of paying full input price for the conversation it re-sends. The
  spec and its As built are in
  `docs/plans/RESEARCH_QC_COST_TIER1_2026-09-23.md`, Chunk 4.
- It ships **off**: `BUILD_A_SPEC_CONTINUATION_CACHE` →
  `settings.CONTINUATION_CACHE`, default `False`. Its flip waited on M3, a
  measured trial Abraham was to run.
- Abraham will not run M3, or any other measurement (the Tier 1 progress
  file's O6). Decision FD1 in the tracker replaces the M3 gate with two
  runtime self-checks. They watch the runs the app makes anyway and can only
  switch the tail **off**. Then the switch turns on.

The end state after CT-3:
- The switch defaults on.
- A provider that refuses the tail costs one extra request, once per app
  session, instead of failing every paused research area.
- A tail that measurably costs more than it saves switches itself off.
- Settings → Developer tools shows which of those happened.

---

## 2. The risks, and what removes each

| Risk | How bad | Removed by |
|---|---|---|
| The provider rejects a continuation that carries the tail (a 400). | Every paused research area, paused compliance lens and paused streamed verifier seat or warm lead fails, on every run, until someone switches the tail off by hand. A research round is the app's most expensive action. | **CT-1.** The rejected request is sent again without the tail, and the tail switches off for the rest of the app session. |
| The provider accepts it, but its entries do not match what the continuation re-sends. | Each continuation pays the 5-minute write premium on the turn it re-sends (+25% on that part) instead of reading it at a tenth of the input price or less. Bounded, and small next to a continuation's output. | **CT-2.** Where the reported usage can prove it (Appendix A), a tail that loses money switches itself off. Where it cannot, the bounded worst case stands. |
| Nothing is wrong. | — | **CT-3** turns the switch on. |

**What is documented** (checked 2026-09-23; re-check before building — the
claude-api skill's `shared/prompt-caching.md` and Anthropic's
prompt-caching docs):
- A top-level `cache_control` places one automatic breakpoint on the
  request's last cacheable block. If that block is ineligible, it walks
  back to the nearest eligible one.
- A breakpoint looks back at most 20 blocks for an earlier entry.
- Web search and web fetch write a 5-minute entry after their tool results
  when the request already uses caching. Every request the two engines send
  does (explicit markers on the last tool, the system block and user
  block 0).
- TTLs must not increase through a request. A 5-minute tail after 1-hour
  explicit markers is allowed; the reverse is a 400.
- Automatic caching has two documented 400s: all four slots already taken
  by explicit markers, and an explicit marker on the last block with a
  different TTL. Tier 1 Chunk 4 proved neither can arise
  (`test_no_request_exceeds_four_breakpoints`), and a scratch run of its
  guard over the whole suite found no violation.

**What is not documented**, and so what M3 was for:
- Whether the provider accepts the tail on every continuation shape the
  app sends: thinking blocks, a trailing pending `server_tool_use`, a
  container. CT-1 makes a "no" harmless.
- Whether the re-sent paused content matches the provider's own
  after-tool-result entries byte for byte. CT-2 measures it where it can.

---

## 3. The code today

**Research — `backend/research/engine.py`:**
- `_CONTINUATION_CACHE_CONTROL` (a read-only `MappingProxyType`) and
  `_is_continuation(messages)`: true when the conversation ends on the
  assistant.
- `_run_dimension(..., continuation_cache: bool = False, ...)`:
  - The request loop, `while len(all_responses) <= RESEARCH_MAX_CONTINUATIONS:`,
    builds `stream_kwargs = dict(request_kwargs)`, adds `container`, and
    adds `stream_kwargs["cache_control"] = dict(_CONTINUATION_CACHE_CONTROL)`
    when `continuation_cache and _is_continuation(messages)`.
  - Then `in_request = True`, and
    `with client.messages.stream(messages=messages, **stream_kwargs) as stream:`
    relays the live events and takes `stream.get_final_message()`.
  - An exception goes to `classify_exception` (`research/retry_policy.py`).
    Any 4xx `APIStatusError`, a `BadRequestError` included, is
    `invalid_request`, which is not retryable: the dimension fails at once,
    with the provider's message.
- `run_requirements_research(..., continuation_cache=None)` pins the setting
  once per round.

**Final QC — `backend/qc/engine.py`:**
- Its own copies of `_CONTINUATION_CACHE_CONTROL` and `_is_continuation`
  (copy-don't-import).
- `_run_streaming_call(..., first_output=None, continuation_cache=False)`:
  - The same loop, with `api_request_count += 1` per request.
  - A `try/finally` sets `first_output` when each request ends.
  - Every streamed QC call goes through it: the five lenses, the
    consolidation grouping calls, streamed verifier seats
    (`QC_BATCH_VERIFICATION=0`) and Chunk 3's warm leads.
- The batched transport never carries the tail, by design (Tier 1 Chunk 4,
  point 5; the comment in `_run_batch_calls`' request builder). Nothing in
  this plan changes that.
- `run_final_qc(..., continuation_cache=None)` pins the setting once per
  run.

**The precedent for "a 400 on an optional feature → send it again without
it, and remember that for the process":** `backend/llm/conversation.py`,
`_enter_stream` with `reset_thinking_display_probe` (the thinking-display
degrade). Note its `_PROMPT_TOO_LONG` exclusion: a prompt that is too long
is not a rejected feature. Note also that it latches BEFORE its resend;
CT-1 latches only on proof (its design, point 4).

**Tests and fakes:**
- `tests/test_continuation_cache.py`: the breakpoint guard, and
  `test_continuation_cache_ships_switched_off`, which reads the default
  from the source with `ast`.
- The per-engine pairs in `tests/test_research_engine.py` and
  `tests/test_qc_live_events.py`, and `tests/test_retry_resume.py`.
- `tests/fakes.py`:
  - `bad_request(message)` returns a real `anthropic.BadRequestError`.
  - A scripted exception raises from `SequencedFakeClient.stream(...)`.
    The real SDK sends the request when the stream context is ENTERED, so
    a guard has to cover both.
  - `SequencedFakeClient.requests` snapshots every request.
  - `usage(...)` builds a usage record, and attaches optional parts only
    when they are supplied (`cache_write_1h=`). New optional parts follow
    the same rule, so every existing fixture stays byte-identical.

---

## 4. Shared infrastructure: `backend/cost_checks.py`

CT-1 creates it, and CT-2 and WL-1 extend it. It is one module so that the
diagnostics block, the Developer tools row and the test reset each have one
place to read.

- **A leaf.** It imports only the standard library, `backend.settings`,
  `backend.usage_ledger` and `anthropic` (R8). Both engines import it. It is
  the one exception to copy-don't-import besides the ledger, because the
  state it holds is one per process, not one per engine copy.
- **Thread-safe.** Research runs four dimension threads and Final QC up to
  eight workers, so every read and write takes one module lock.
- **OFF-only, process-local and in memory** (R6).
  - A latch, once set, stays set until the process ends. An app restart
    re-arms it.
  - Nothing is persisted. Nothing reaches a request, a record, a usage
    total, a project file, a project brief or the QC input manifest.
- **It never raises into a request path.** Every entry point an engine
  calls catches its own exceptions and logs them at DEBUG.
- **Logger `buildaspec.cost_checks`** (the activity log). One WARNING the
  first time a latch is set, naming the behavior, the engine and the
  reason. Nothing is logged per request.

Suggested interface. A session may reshape it; record the change under As
built.

```python
# engine: "research" | "qc"
def continuation_tail_enabled(engine: str) -> bool
def disable_continuation_tail(engine: str, *, reason: str, detail: str = "") -> None
def is_tail_rejection(exc: BaseException) -> bool                   # CT-1
def first_iteration_usage(response: Any) -> dict[str, int] | None   # CT-2
def observe_continuation(
    engine: str, *, model: str, opening: Any, response: Any
) -> None                                                            # CT-2
def warm_lead_enabled() -> bool                                      # WL-1
def record_warm_lead_lineage(...) -> None                            # WL-1
def snapshot() -> dict[str, Any]
def reset_for_tests() -> None
```

`reason` is a closed vocabulary: `rejected` (CT-1), `unprofitable` (CT-2
and WL-1) and `not_read` (WL-1). The frontend pins it (CT-2).

---

## CT-1 — Survive a rejected continuation tail

### Goal

Suppose a continuation carries the tail, and the provider rejects it with
a 400 when its stream opens. The request is then sent again, once, without
the tail. The call carries on as if the tail had never been there, and
that engine's tail switches off for the rest of the app session. **The
switch still ships off** at the end of this session.

### Why

It is the only failure mode of the tail that is not bounded (§2). Today a
rejection is `invalid_request`, which is not retryable, so it fails the
dimension or the call outright, on every paused conversation, every time.
With this guard, the worst a refusal can cost is one extra request per
engine per app session.

### Design

1. **Where.** The stream open in `_run_dimension` and in
   `_run_streaming_call`. Each engine gets its own small helper
   (copy-don't-import; the shared module holds only the latch). For
   example, a context manager that opens the stream and yields it together
   with whether the request that opened carried the tail — CT-2 needs that
   flag, so shape the helper to give it. The fakes raise from
   `messages.stream(...)`, and the real SDK raises when the stream context
   is entered, so guard both. `contextlib.ExitStack.enter_context(client.messages.stream(...))`
   covers both in one place.
2. **What counts as a rejection** (`cost_checks.is_tail_rejection`): an
   `anthropic.BadRequestError`, raised while opening a stream whose kwargs
   carry `cache_control`, whose text does not match `prompt is too long`
   (case-insensitive). The `_enter_stream` precedent excludes that text for
   a reason that applies here too: a continuation that is too long fails
   the same way with or without the tail. Nothing else counts:
   - not a 400 on a request without the tail;
   - not a 401, 403, 404, 413, 429 or 5xx;
   - not an error raised after the stream opened, while relaying events or
     in `get_final_message()`. That is not a verdict on the request's shape.
3. **The resend.** The same call, with the same `messages` and every other
   keyword unchanged (`container` included), minus `cache_control`.
   - Send it immediately, with no backoff. It is the same request without
     an optional feature, not a retry of a transient failure.
   - Final QC counts it: `api_request_count += 1`, because "client API
     requests" includes retries (the Chunk 5.3 label). Research counts no
     requests.
   - Neither engine appends anything for the rejected request. A request
     rejected with a 400 returns no response, and is not billed.
4. **Latch on proof.** Switch the engine's tail off
   (`disable_continuation_tail(engine, reason="rejected", detail=...)`)
   unless the tail-free resend is itself rejected with a `BadRequestError`.
   A 400 that survives removing the tail was not the tail's, and latching
   then would switch off a saving for nothing.
   - If the resend opens: latch, and carry on with that stream.
   - If the resend raises anything other than a `BadRequestError` (a 429, a
     dropped connection): latch, then let the exception take the ordinary
     retry path. The 400 went away when the tail did.
   - If the resend raises a `BadRequestError`: no latch. Let it take the
     ordinary path, which fails the call exactly as it failed before this
     session.
   - `detail` is at most 200 characters: the error type and the start of
     its message. It reaches diagnostics (which scrub it) and never a
     record.
5. **Read per request, after the switch.** The condition that adds the tail
   becomes `continuation_cache and cost_checks.continuation_tail_enabled(engine)
   and _is_continuation(messages)`.
   - The switch is still pinned once per round or run.
   - The latch is a safety override that can only REMOVE the tail, in every
     thread, from the next request on. That is a deliberate exception to
     Tier 1 Chunk 4's "one round, one answer" rule; record it under As
     built.
6. **Engine names: `"research"` and `"qc"`.** They are separate latches,
   because the two engines send different models (Sonnet 5 for research,
   Opus 5.5 for Final QC by default), and a refusal is a property of a
   request shape on one model.
7. **Everything else is unchanged.**
   - `in_request` stays `True` across the open and the resend, so a
     retryable failure of the resend resumes as Chunk 5 intends.
   - `first_output`'s `finally` still fires when the request ends.
   - The live relay starts on whichever stream opened.
   - `_CallResult` and every record are built as before.
8. **Diagnostics.** `backend/diagnostics.py`'s `snapshot()` gains a
   top-level `cost_checks` key, filled from `cost_checks.snapshot()`: for
   each engine, `{setting_on, enabled, reason, detail, since}`.
   - It is top level, not under `session`: the latches belong to the
     process, not to a workspace.
   - Every key must survive `backend.tracing.redaction.scrub_data`. The
     key pattern redacts any key containing `token` not followed by `s`
     (`token(?!s)`), so `read_tokens` is fine and `token_count` is not.
     Test it.
   - It is read without the session guard. It is not session state, and
     `cost_checks` has its own lock.
9. **Tests reset it.** `tests/conftest.py`'s autouse `_fresh_session`
   calls `cost_checks.reset_for_tests()` before and after every test,
   beside `reset_thinking_display_probe()`.

### Files

- new `backend/cost_checks.py`
- `backend/research/engine.py` and `backend/qc/engine.py`
- `backend/diagnostics.py`
- `tests/conftest.py`
- new `tests/test_cost_checks_tail_rejection.py`
- this plan's As built, and the tracker's CT-1 row and checklist

### Tests (`tests/test_cost_checks_tail_rejection.py`)

Every test passes `continuation_cache=` explicitly, so that CT-3's flip
changes none of them.

**Research:**
- A dimension pauses; its continuation is rejected
  (`bad_request("... cache_control ...")`), then succeeds.
  - Three requests. The second carries `cache_control`. The third equals
    the second minus that key: the same messages and the same container.
  - The dimension completes.
  - The research latch is set, and the QC latch is not.
- After the latch, a later continuation carries no tail. Use a second
  round, or a second dimension ordered after the first by the script.
  Keep the order deterministic.
- The resend is also rejected: two 400s, no latch. The dimension fails with
  the resend's message and kind `invalid_request`.
- The resend hits a rate limit: the latch is set, and the ordinary retry
  path (a resume) sends the continuation again without the tail. Patch
  the backoff sleep.
- "prompt is too long": no resend, and no latch.
- A 400 on an opening request, which carries no tail: no resend, no latch,
  and today's failure.
- An error raised after the stream opened is not intercepted. Build the
  fixture: the fake stream contexts replay events, and one can raise
  partway through.

**Final QC:**
- A paused `code_compliance` lens whose continuation is rejected:
  - it is sent again, and the lens completes;
  - its record's `api_request_count` counts both requests;
  - the QC latch is set.
- A streamed web-tooled verifier seat (the `batch_verification=False` path),
  in the same shape.
- F3: a retained Final QC result stays current (`matches_inputs`) after the
  latch.

**Shared:**
- Two dimensions rejected at once set the latch once, log one WARNING, and
  both complete.
- After a latch, `diagnostics.snapshot()["cost_checks"]` reports it, and
  its keys and values survive `scrub_data` unchanged.
- A test that latches does not leak into the next one. Use a pair of tests,
  or assert the reset directly.
- `test_continuation_cache_ships_switched_off` is untouched, and green.

### Acceptance

- **CT-1.1** `backend/cost_checks.py` exists as a thread-safe leaf module (standard library, `backend.settings`, `backend.usage_ledger` and `anthropic` only), holding one continuation-tail latch per engine, OFF-only and process-local, with the `buildaspec.cost_checks` logger and `reset_for_tests()`.
- **CT-1.2** Research: a continuation that carries the tail and is rejected with a 400 when its stream opens is sent again once without the tail, with the same messages and every other keyword unchanged, and the dimension carries on.
- **CT-1.3** Final QC: the same guard in `_run_streaming_call`, so it covers lenses, grouping calls, streamed seats and warm leads, and `api_request_count` counts both requests.
- **CT-1.4** The latch is set unless the tail-free resend is itself rejected with a 400. A resend that fails any other way sets the latch, then takes the ordinary retry path.
- **CT-1.5** Only a tail-bearing stream open is guarded. A "prompt is too long" 400, a 400 on a request without the tail, and any error raised after the stream opened take today's path unchanged.
- **CT-1.6** The latch is read on every request, after the switch, in every thread. Once it is set, no request of that engine carries the tail for the rest of the process. Nothing about it reaches a request's other fields, a record, a usage total or the QC input manifest; the F3 test proves a retained result stays current.
- **CT-1.7** `diagnostics.snapshot()` carries a top-level `cost_checks` block, with each engine's setting, state, reason, detail and time, and it survives `scrub_data` (a test).
- **CT-1.8** `tests/conftest.py` resets the latches before and after every test.
- **CT-1.9** `tests/test_cost_checks_tail_rejection.py` covers CT-1.2 to CT-1.8, and every test in it passes `continuation_cache=` explicitly.
- **CT-1.10** `BUILD_A_SPEC_CONTINUATION_CACHE` still defaults off, and `test_continuation_cache_ships_switched_off` is unchanged and green.
- **CT-1.11** Verified: ruff, the full pytest suite, `npm test` and `npm run build` are all clean.
- **CT-1.12** Every mechanism above was reverted in place, one at a time, and its own test went red. The matrix is recorded under As built.
- **CT-1.13** As built is written, including its For FIN-1 list, and the root `CLAUDE.md` and `README.md` are untouched.

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

#### CT-1 as built (2026-09-24)

Built from `master` at `a82abc3`, on branch
`claude/trusting-thompson-ltr3tk`. The switch still ships off:
`backend/settings.py` is untouched, and
`test_continuation_cache_ships_switched_off` is unchanged and green.

**What was built**

- **`backend/cost_checks.py`** (new): the leaf that holds the self-checks'
  state. For CT-1, one continuation-tail latch per engine
  (`ENGINE_RESEARCH = "research"`, `ENGINE_QC = "qc"`), each a `_Latch`
  of `reason` / `detail` / `since`, under one module lock.
  - `continuation_tail_enabled(engine)` is the read.
  - `disable_continuation_tail(engine, *, reason, detail="")` is the
    write: the first latch wins, one WARNING on `buildaspec.cost_checks`.
  - `is_tail_rejection(exc)` is the classifier: an
    `anthropic.BadRequestError` whose text is not "prompt is too long".
  - `exception_detail(exc)` formats the detail.
  - `snapshot()` is the diagnostics block; `reset_for_tests()` clears it.
  - Every entry point an engine calls catches its own exceptions and logs
    them at DEBUG; a failed read answers `True`, which leaves the tail as
    the switch set it.
- **The guard** — `_open_stream`, one copy in each engine, placed beside
  `_CONTINUATION_CACHE_CONTROL` and `_is_continuation`.
  - A `contextlib.contextmanager` over one `ExitStack`: the stream is
    opened with `stack.enter_context(client.messages.stream(...))` inside
    one `try`, which covers the SDK (the request is sent when the stream
    context is ENTERED) and the fakes (which raise from `stream(...)`).
  - On a tail rejection it opens the same request again with every
    argument but `cache_control`, at once. It latches unless that resend
    is itself a `BadRequestError`. A resend that fails any other way
    latches, then raises into the ordinary retry path.
  - It yields `(stream, carried)`: `carried` is whether the request that
    OPENED carried the tail, so CT-2 never credits the tail with a
    resend.
  - The `yield` sits outside the `try`, so anything raised after the open
    reaches the caller untouched.
- **The call sites.** `_run_dimension` and `_run_streaming_call` open every
  request through the helper. The tail condition is now
  `continuation_cache and cost_checks.continuation_tail_enabled(engine) and _is_continuation(messages)`.
  Final QC's copy takes a `count_request` callback, a `nonlocal` counter
  in `_run_streaming_call` called just before the resend is sent. So the
  resend is counted even when it raises, and the loop's own increment
  still counts the first request.
- **Diagnostics.** `diagnostics.snapshot()` gains a top-level
  `cost_checks` block, filled from `cost_checks.snapshot()`. It is read
  without the session guard, and it reaches `/api/diagnostics` and the
  support bundle's `snapshot.json` through the same `scrub_data` as the
  rest of the payload.
- **`tests/conftest.py`**: `_fresh_session` calls
  `cost_checks.reset_for_tests()` before and after the `yield`, beside
  `reset_thinking_display_probe()`.
- **`tests/test_cost_checks_tail_rejection.py`** (new, 38 tests; below).

**Deviations from the spec, and why**

1. **The interface.** Beside the suggested functions:
   - `exception_detail(exc)`, so the detail's format and its
     200-character, one-line clip live in one place that both engines
     call;
   - the constants `ENGINE_RESEARCH`, `ENGINE_QC`, `TAIL_ENGINES`,
     `REASON_REJECTED` and `DETAIL_MAX_CHARS`.

   The reason vocabulary is closed per behavior: the tail accepts only
   `rejected` for now (`_TAIL_REASONS`). CT-2 adds `unprofitable`; WL-1
   adds its own for the warm lead. Anything else is logged at DEBUG and
   ignored, and switches nothing off.
2. **The snapshot is grouped by behavior**:
   `{"continuation_tail": {"research": {...}, "qc": {...}}}`, not the
   spec's flat per-engine map. WL-1's warm-lead block then sits beside
   `continuation_tail` rather than among the engine names. Each entry
   holds exactly the spec's five keys:
   - `setting_on`: the live switch;
   - `enabled`: the checks' verdict alone, `False` once latched. The tail
     is sent only when both are true;
   - `reason`, and `detail`;
   - `since`: seconds since the epoch, or `None`.
3. **It imports less than the leaf rule allows**: the standard library,
   `anthropic` and `settings`. CT-1 needs nothing from `usage_ledger`;
   CT-2 may add it for the rates.
4. **Only `Exception` latches.** A `KeyboardInterrupt` or `SystemExit`
   raised by the resend propagates without latching, as the engines
   re-raise those anyway.
5. **The latch is consulted on every request, opening ones included.**
   The condition is the spec's order — switch, latch, is-continuation — so
   the latch is read on every request after the switch, not only on
   continuations. It costs one lock acquisition per request, and pins
   CT-1.6 literally.
6. **The detail never reaches a record.** It lives only in the latch
   (diagnostics) and the one WARNING. A call that recovered records
   nothing of the refusal, and the refused request's error text is
   absent from the profile and the `QCResult` alike (pinned).
7. **The stale "M3" comments were left for CT-3.** These are the "MEASURED
   BY M3" comments above `_CONTINUATION_CACHE_CONTROL` in both engines,
   and `settings.py`'s "flips only on a recorded M3 pass". They describe
   the gate FD1 replaced, so they went stale when the plans merged, not
   with this session. CT-3's steps 2 and 4 rewrite them at the flip, when
   CT-2's check exists to name. The comments this session touched — the
   per-round and per-run pins in both engines — now name the latch as the
   one deliberate exception to "one round, one answer".
8. **Tests beyond the spec.**
   - The resend tests run over BOTH engines as one assertion set (Tier 1
     Chunk 5's reason: the engines keep separate copies of the guard).
   - The latch is pinned as read on every request after the switch (a
     spy).
   - One latch reaches a request another thread builds next.
   - A lens record with a resend in it still reconciles and reloads
     (`QCResult.from_dict`).
   - The helper is tested directly: `carried`, the SDK's
     refuse-on-`__enter__` shape, and a failure inside the stream passing
     through untouched.
   - Every read and write takes the one lock (a counting lock: a race
     cannot be forced deterministically, but the lock can be counted).
   - Sixteen threads latch once.
   - The leaf rule is checked by parsing the module's imports.
   - The conftest reset is pinned from the source, and by a pair of tests.

**Knowing changes to existing tests:** none. No existing assertion changed;
`tests/conftest.py` gains the reset (a fixture, not a test).

**The tests** (`tests/test_cost_checks_tail_rejection.py`). Every test that
runs an engine passes `continuation_cache=`; the run helpers take it as a
required keyword, so the rule is structural.

- Both engines (parametrized over research's governing-codes dimension and
  Final QC's compliance lens):
  - `test_a_refused_continuation_is_sent_again_once_without_the_tail`:
    three requests, the resend equal to the refused one minus the tail
    (container kept), no retry and no sleep, only the two responses
    billed, Final QC counting (3, 2), this engine latched and not the
    other, the detail, and nothing of the refusal in the record.
  - `test_a_resend_refused_too_fails_as_before_and_latches_nothing`
  - `test_a_resend_that_fails_another_way_latches_then_resumes_without_the_tail`:
    a 429 on the resend; one retry, mode `resume`; the resumed request
    equal to the resend; Final QC counting (4, 2).
  - `test_prompt_too_long_is_not_the_tails_refusal`
  - `test_a_400_on_a_request_without_the_tail_takes_todays_path`: the
    opening request, and the switch off.
  - `test_an_error_after_the_stream_opened_is_not_intercepted`: a 400
    raised while relaying.
  - `test_the_latch_is_read_on_every_request_after_the_switch`
  - `test_after_the_latch_a_later_call_sends_exactly_a_switch_off_calls_requests`
    (the spec's "second round"; for Final QC, a second run).
- Research:
  - `test_a_latch_reaches_a_request_another_thread_builds_next`: one
    dimension's opening is held on an event until another's refusal
    latches.
  - `test_two_dimensions_refused_at_once_latch_once_and_both_complete`: a
    barrier holds both tail-bearing continuations until both are built;
    two asks, one latch, one WARNING.
- Final QC:
  - `test_a_refused_lens_record_still_reconciles_and_reloads`
  - `test_a_refused_streamed_verifier_seat_is_sent_again`: verdict counts
    (1, 1) and (3, 2).
  - `test_a_retained_result_stays_current_after_the_latch` (F3): the
    fingerprint equals a switch-off run's, the manifest names none of it,
    and `matches_inputs` holds with the latch set and after it is
    cleared.
- Diagnostics: `test_diagnostics_report_the_latch_and_survive_the_scrub`.
  It checks the clear state, the latched state, that
  `scrub_data(raw) == raw == diagnostics.snapshot()["cost_checks"]`, that
  `/api/diagnostics` carries the block, and that no key matches the
  redaction key pattern.
- `cost_checks` itself:
  - `test_only_a_400_other_than_too_long_is_a_tail_rejection`
  - `test_the_first_latch_wins_and_logs_one_warning`
  - `test_the_detail_is_one_line_and_clipped`
  - `test_a_malformed_call_never_raises_and_switches_nothing_off`
  - `test_many_threads_latch_once`
  - `test_every_read_and_write_takes_the_one_lock`
  - `test_cost_checks_is_a_leaf_both_engines_share`
- The helper, on both engines:
  - `test_the_helper_says_whether_the_request_that_opened_carried_the_tail`
  - `test_the_helper_guards_the_sdks_shape_a_refusal_on_entering_the_stream`
  - `test_the_helper_lets_a_failure_inside_the_stream_through_untouched`
- The reset:
  - `test_the_conftest_resets_the_latches_before_and_after_every_test`
  - the pair `test_a_latch_left_set_on_purpose` /
    `test_the_next_test_starts_with_both_latches_clear`.

**Verification** (Linux container, from the repository root)

- `.venv/bin/python -m ruff check .`: all checks passed.
- `.venv/bin/python -m pytest -q` on the finished code: 2912 passed,
  64 skipped (8 min 39 s). An earlier run, before
  `test_every_read_and_write_takes_the_one_lock` was added: 2911 passed,
  64 skipped. The new file also passed eight runs in a row, and the
  revert matrix's baseline run passed 208 of 208 across its seven suites.
- `npm test` (in `frontend/`): 421 passed, 0 failed.
- `npm run build`: built; the only warning is the existing chunk-size one.
- `tests/test_tier1_finish_tracker.py` and `tests/test_docs_consistency.py`
  pass with this As built and the ticks in place.
- `git diff --name-only origin/master...HEAD -- CLAUDE.md README.md`
  prints nothing.

**Revert matrix.** Each mechanism was reverted in place, one at a time, in
the working tree. The runs used the new file plus the suites that exercise
the same code: `test_continuation_cache.py`, `test_research_engine.py`,
`test_qc_live_events.py`, `test_retry_resume.py`,
`test_qc_batch_warm_lead.py` and `test_diagnostics.py`. After each run the
exact text read was restored, and the file checked byte-identical. "Red"
counts failing tests, and names the new file's where it helps.

48 rows, 48 red.

| Mechanism reverted | Red |
|---|---|
| `cost_checks`: the latch never read (always enabled) | 16 |
| a later latch overwrites the first | 2 |
| any reason accepted | 1 |
| the detail not clipped | 1 |
| "prompt is too long" counted as the tail's | 3 |
| any error counted as a 400 | 3 (two are Chunk 5's `test_a_resumed_continuation_carries_the_tail_the_failed_one_did`, which pins that a connection error on a tail-bearing continuation is never the tail's) |
| no WARNING | 2 |
| no time recorded | 1 |
| the snapshot's `enabled` always `True` | 1 |
| the snapshot's `setting_on` not the live switch | 1 |
| the error type dropped from the detail | 3 |
| a read without the lock | 1 |
| a write without the lock | 1 |
| the snapshot without the lock | 1 |
| reset without the lock | 1 |
| reset clears nothing | 41 (latches leak into six files) |
| research: no guard (the stream opened as before CT-1) | 7 |
| research: the latch not read | 4 |
| research: the latch read before the switch | 1 |
| research: the latch read on continuations only | 1 |
| research: the resend keeps the tail | 5 |
| research: the resend drops the container | 1 |
| research: no latch when the resend opens | 7 |
| research: a latch though the resend is refused with a 400 too | 1 |
| research: no latch when the resend fails another way | 1 |
| research: `carried` stays `True` after the resend | 2 |
| research: only the `stream(...)` call guarded, not entering it | 1 |
| research: errors after the open intercepted too | 2 |
| research: a request without the tail resent too | 1 |
| QC: no guard | 7 |
| QC: the latch not read | 3 |
| QC: the latch read before the switch | 1 |
| QC: the latch read on continuations only | 1 |
| QC: the resend keeps the tail | 5 |
| QC: the resend drops the container | 1 |
| QC: no latch when the resend opens | 6 |
| QC: a latch though the resend is refused with a 400 too | 1 |
| QC: no latch when the resend fails another way | 1 |
| QC: `carried` stays `True` after the resend | 2 |
| QC: only the `stream(...)` call guarded, not entering it | 1 |
| QC: errors after the open intercepted too | 2 |
| QC: a request without the tail resent too | 3 (two are existing tests: `test_a_lead_that_fails_before_streaming_still_releases_the_batch` and `test_shared_invalid_request_accounts_for_every_unstarted_verifier_seat`) |
| QC: the resend not counted | 6 |
| QC: the resend counted only once it opens | 2 |
| `diagnostics.snapshot()` without the `cost_checks` block | 1 |
| conftest: no reset before the `yield` | 1 |
| conftest: no reset after the `yield` | 1 |
| conftest: no reset at all | 38 (the pair, the source pin, and leaks into five more files) |

The two "no guard" rows leave `_open_stream` defined but unused, so the
helper's own tests stay green there: the call sites are proven separately
from the helper.

**For FIN-1**

- **CLAUDE.md implemented notes** — a section "A refused continuation
  tail costs one request — implemented notes (Tier 1 finish, CT-1)", in
  CLAUDE.md's own style. The why and the traps:
  - *Why a guard, before any measurement.* A refused tail was the tail's
    one unbounded failure. `invalid_request` is not retryable, so every
    paused research area and compliance review would fail, on every run.
    With the guard, the worst a refusal costs is one extra request per
    engine per app session.
  - *The SDK sends at `__enter__`, the fakes raise at `stream(...)`.* One
    `try` around `stack.enter_context(client.messages.stream(...))`
    covers both. The `yield` must stay OUTSIDE that `try`: inside it, a
    failure thrown into the generator would be caught and "resent", and
    `contextmanager` then raises "generator didn't stop after throw()".
  - *The resend is not a retry.* It goes at once, with no backoff, with
    the same messages and every other argument, the container included.
    Nothing is appended for the refused request, which returned no
    response and billed nothing. Final QC counts it as a request ("client
    API requests" includes retries), counted before it is sent so a
    resend that raises is still counted. Research counts no requests.
  - *Latch on proof.* A 400 that survives removing the tail was not the
    tail's, so no latch. A resend that fails any other way does latch
    (the 400 went with the tail); then the ordinary retry path resumes the
    conversation (Tier 1 Chunk 5), and the resumed continuation is built
    after the latch, so it goes without the tail.
  - *"prompt is too long" is never the tail's*: the same pattern and the
    same `str(exc)` as the chat engine's thinking-display degrade and its
    too-long retry.
  - *Read on every request, after the switch.* This is the one deliberate
    exception to Tier 1 Chunk 4's "one round, one answer": the latch can
    only REMOVE the tail, in every thread, from the next request on.
    Separate latches per engine, because a refusal is a property of a
    request shape on one model.
  - *Where the state lives.* In memory, per process, OFF-only. It is
    never in a request, a record, a usage total, a project file, a brief
    or the QC input manifest (F3 pinned). Only in the one WARNING and the
    diagnostics block.
  - *Test traps.*
    - "Two refused at once" needs both tail-bearing continuations built
      before either is refused: a `threading.Barrier(2)` in the fake's
      `stream`, before it pops the scripted refusal.
    - "Every thread" holds one dimension's opening request on an event set
      by the latch.
    - A race cannot be forced deterministically, so the lock is proven by
      counting it.
    - The F3 comparison has to pin `QC_BATCH_VERIFICATION` to the
      transport the runs used, because the staleness check rebuilds the
      manifest with the live setting.
- **Layout** — add:
  - `backend/cost_checks.py`: "the cost self-checks' state: one
    continuation-tail latch per engine (`research`, `qc`), OFF-only,
    in-memory, per process; `continuation_tail_enabled` /
    `disable_continuation_tail` / `is_tail_rejection` /
    `exception_detail` / `snapshot` / `reset_for_tests`; a leaf both
    engines import; one WARNING on `buildaspec.cost_checks` when a latch
    is set".
  - `tests/test_cost_checks_tail_rejection.py`.

  Change:
  - `research/engine.py` and `qc/engine.py` gain `_open_stream` (copied,
    `(stream, carried)`, QC's with `count_request`) and the latch read in
    the tail condition;
  - `diagnostics.py`'s snapshot gains the top-level `cost_checks` block;
  - `tests/conftest.py` resets the latches around every test;
  - the `settings.py` Layout entry's `CONTINUATION_CACHE` note, "pinned
    per round/run", gains "(the self-check latch can only remove the
    tail)".
- **Errata** for earlier CLAUDE.md sections:
  - "A paused call reads its own cache (Research/QC cost Tier 1, Chunk
    4)", and the Layout entries for both engines, say the switch is
    pinned once per round and per run: "one round, one answer". The CT-1
    latch is the one exception.
  - "Research and Final QC cost, Tier 1, as shipped (closeout)" says that
    if the provider refused the tail, "every paused research area and
    compliance review would fail with a 400". Since CT-1, a refusal costs
    one resend per engine per app session.
- **README** — in the continuation-tail material and the
  `BUILD_A_SPEC_CONTINUATION_CACHE` Configuration row: a continuation the
  provider refuses because of the tail is sent again without it, and the
  tail switches off for that engine until the app restarts. It is logged
  once to the activity log and shown in Settings → Developer tools (the
  diagnostics `cost_checks` block; CT-2 adds the Developer tools line).

---

## CT-2 — Measure what the continuation tail saves

### Goal

Every response to a continuation that carried the tail is measured, where
the usage the provider reports makes that possible. If, after enough
observations, the tail has cost more than it saved even on the most
generous reading, that engine's tail switches off (`unprofitable`).
Developer tools shows what was measured. **The switch still ships off.**

### Why

This is M3's economic half, run on the requests the app makes anyway. The
tail's bad case (§2) is bounded, but it is a loss on every continuation for
as long as nobody looks. A default-on switch should notice its own loss.

### Design

1. **The measurement point.** In both engines, right after a response is
   appended to `all_responses`, if the request that produced it carried the
   tail — as the CT-1 helper reports, so a tail-free resend is never
   measured — call:

   ```python
   cost_checks.observe_continuation(
       engine, model=model, opening=all_responses[0], response=response
   )
   ```

   `all_responses[0]` is the current conversation's opening response. A
   continuation always has one. A Chunk 5 resume keeps it, and a restart
   starts a new conversation whose first request carries no tail. Take it
   before the append, or read index 0 after; it is the same object.
2. **The first iteration** (`cost_checks.first_iteration_usage(response)`),
   in order:
   - **Exact.** `response.usage.iterations` is present: use its first
     entry whose `type` is `message`.
     - In SDK 1.8.0 that field is typed only on the beta usage type
       (`BetaUsage.iterations`). The GA `Usage` model accepts extra fields,
       so if a GA response carries it, it arrives as a list of plain
       dicts. Read both shapes, duck-typed.
     - Re-check the SDK and the API reference when you build this, and
       record what you found under As built. Never make a request to find
       out (R5).
   - **Single iteration.** No iterations, but the response made no
     server-tool request: no `server_tool_use` block in its `content`, and
     no web search or fetch requests in its usage. Its top-level usage is
     then its only iteration.
   - **Otherwise,** `None`. The top-level usage of a request that ran a
     server-side tool loop sums every iteration, and every later iteration
     re-reads the whole prefix, so it cannot say what the tail did.
3. **One observation** (Appendix A has the derivation):
   - **exact**: the continuation and its opening response both have an
     exact first iteration. S = R·(u − r) − W·(w − u).
   - **bound**: the continuation has a single iteration, and its read is
     above zero. S_max = read·(u − r) − write·(w − u), an upper bound on S.
   - **unmeasured**: anything else. Counted, never guessed.
   - u, r and w are the input, cache-read and 5-minute cache-write rates of
     the engine's model. Read them through a new public accessor in
     `backend/usage_ledger.py` (for example `model_rates(model)`, returning
     what `_rates` returns). Do not copy the rate table or its
     unknown-model fallback.
4. **The latch rule.** Per engine, sum S and S_max over every measured
   observation since the process started. Once there are at least
   `_TAIL_MIN_OBSERVATIONS` (6) measured observations and the sum is below
   zero, latch (`disable_continuation_tail(engine, reason="unprofitable",
   detail=...)`).
   - Every term is exact or an upper bound, so the sum is an upper bound on
     what the tail saved. The rule latches only on a proven loss, which is
     the right direction for a switch that defaults on.
   - Six observations: one odd request cannot decide it, and a losing tail
     is caught within about one research round (four areas, each pausing a
     few times).
   - Keep observing after a latch, for diagnostics. Nothing can switch the
     tail back on.
5. **It changes nothing else.**
   - `observe_continuation` never raises: it catches everything and logs at
     DEBUG.
   - It reads the response objects; it never mutates them.
   - It changes no request, no record, no usage total, no meter bucket and
     no manifest (F3, F4, R6).
6. **Diagnostics.** The `cost_checks` block gains, per engine: `measured`,
   `exact`, `bound`, `unmeasured`, `saving_usd` (the signed sum, rounded to
   six decimals) and `last_observed_at`.
7. **Developer tools.** Add a "Cost self-checks" row to
   `DeveloperToolsModal`'s Environment section. It is rendered by a new
   pure helper, `frontend/src/lib/costChecks.ts` (the `contextSizes.ts`
   idiom), from a typed `cost_checks` field on `DiagnosticsSnapshot` in
   `frontend/src/types.ts`. One line per engine and behavior, in plain
   words. For example:
   - "Continuation tail, research: on · 7 measured (2 exact, 5 bound),
     est. saving $0.31 · 3 not measurable"
   - "Continuation tail, Final QC: off for this session — the provider
     rejected it"
   - "Continuation tail: switched off in settings"

   The frontend test pins the helper's reason vocabulary against
   `backend/cost_checks.py`'s, read from the file (the
   `contextSizes.test.ts` idiom), so a reason added on one side cannot drop
   out of the other. The row needs no new capability id:
   `session.developer-tools` covers the modal.
8. **Fakes.** Give `tests/fakes.py`'s `usage(...)` a keyword-only
   `iterations=` that attaches `usage.iterations` only when supplied. Build
   entries both as dicts and as objects, since tests must cover both
   shapes.

### Files

- `backend/cost_checks.py`, `backend/usage_ledger.py`
- `backend/research/engine.py`, `backend/qc/engine.py`
- `frontend/src/lib/costChecks.ts` (new), `frontend/src/types.ts` and
  `frontend/src/components/DeveloperToolsModal.tsx`
- `frontend/package.json` (register the new test in the explicit
  `node --test` list)
- `tests/fakes.py`
- new `tests/test_cost_checks_tail_value.py` and
  `frontend/tests/costChecks.test.ts`
- this plan's As built, and the tracker's CT-2 row and checklist

### Tests

`tests/test_cost_checks_tail_value.py` (every engine test passes
`continuation_cache=True` explicitly):
- Unit tests of the arithmetic (Appendix A):
  - an exact observation, with a hand-computed saving;
  - an exact observation whose opening entry had expired (the `missed` term);
  - a bound observation;
  - a multi-iteration response without iterations counts as unmeasured;
  - a bound observation with zero read is skipped;
  - iterations given as dicts and as objects.
- The latch:
  - five losing observations do not latch;
  - the sixth does, and logs one WARNING;
  - a sum at or above zero never latches;
  - the latch persists however many winning observations follow.
- The hooks, end to end:
  - a paused research dimension with scripted usage produces exactly the
    observations it should;
  - a paused compliance lens and a streamed web-tooled seat do the same in
    Final QC;
  - a tail-free resend (CT-1) is never observed;
  - an opening response is never observed as a continuation.
- A malformed usage (non-numeric, missing fields) raises nothing and
  changes nothing.
- The measurement is invisible: every record, usage total and manifest is
  identical with `observe_continuation` patched to a no-op (F3, F4).
- Diagnostics report the counts and survive `scrub_data`.
- `test_continuation_cache_ships_switched_off` is untouched, and green.

`frontend/tests/costChecks.test.ts`:
- Each state renders its line: setting off, on and unmeasured, on and
  measured, `rejected`, and `unprofitable`.
- The reason vocabulary is pinned against the backend file.
- A missing or older snapshot without `cost_checks` renders "not reported",
  never throws.
- The modal uses the helper, a source-level pin (the `chatPerf.test.ts`
  idiom).

### Acceptance

- **CT-2.1** `backend/usage_ledger.py` has a public rate accessor that reuses `_rates` (no copied rate table or fallback), and `cost_checks.observe_continuation` never raises.
- **CT-2.2** `cost_checks.first_iteration_usage` reads the first `message` entry of `usage.iterations` whether it is a dict or an object, falls back to the top-level usage only when the response made no server-tool request, and otherwise returns `None`.
- **CT-2.3** Observations follow Appendix A: an exact saving from both first iterations, a bound saving that is an upper bound when there is a single iteration with a read above zero, and anything else counted as unmeasured, never guessed.
- **CT-2.4** An engine's tail latches `unprofitable` once it has at least six measured observations whose summed saving is below zero, and never otherwise.
- **CT-2.5** Both engines observe every response to a request that actually carried the tail, with the conversation's opening response, and never a tail-free resend or an opening response.
- **CT-2.6** The measurement changes no request, record, usage total, meter bucket or manifest (a test with it patched out), and raises nothing on malformed usage.
- **CT-2.7** The `cost_checks` diagnostics block reports each engine's measured, exact, bound and unmeasured counts and its estimated saving, and Developer tools shows a "Cost self-checks" row rendered by `frontend/src/lib/costChecks.ts`.
- **CT-2.8** `tests/test_cost_checks_tail_value.py` and `frontend/tests/costChecks.test.ts` (registered in `frontend/package.json`) cover CT-2.1 to CT-2.7.
- **CT-2.9** `BUILD_A_SPEC_CONTINUATION_CACHE` still defaults off, and its ships-off pin is unchanged and green.
- **CT-2.10** Verified: ruff, the full pytest suite, `npm test` and `npm run build` are all clean.
- **CT-2.11** Every mechanism above was reverted in place, one at a time, and its own test went red. The matrix is recorded under As built.
- **CT-2.12** As built is written, including its For FIN-1 list and what the SDK and API reference say about `usage.iterations` today, and the root `CLAUDE.md` and `README.md` are untouched.

### As built

*Built on 2026-09-24; the record follows the paragraph below.*

When you build this session, append here what CT-1's As built lists, plus
what you found about `usage.iterations` (the SDK version, the GA and beta
types, and the API reference, with the date).

#### CT-2 as built (2026-09-24)

Built from `master` at `cd3ce86` (CT-1's merge), on branch
`claude/vigilant-euler-xs9aab`. The switch still ships off:
`backend/settings.py` is untouched, and
`test_continuation_cache_ships_switched_off` is unchanged and green.

**What was built**

- **`backend/usage_ledger.py`**: `model_rates(model)`, the public rate
  accessor. It returns `dict(_rates(model))`: the ledger's own lookup,
  unknown-model fallback included, as a copy, so a caller cannot edit
  `settings.PRICING`.
- **`backend/cost_checks.py`**, the value check:
  - `REASON_UNPROFITABLE = "unprofitable"` joins `_TAIL_REASONS`, and
    `_TAIL_MIN_OBSERVATIONS = 6`.
  - `first_iteration_usage(response)` returns the first model iteration's
    `input_tokens`, `output_tokens`, `cache_read_input_tokens` and
    `cache_creation_input_tokens`, or `None`. It tries, in order:
    - the first `message` entry of `usage.iterations`, read whether it is
      a dict or an object;
    - the top-level usage, but only when the response provably ran one
      model iteration (`_single_iteration`);
    - otherwise `None`.
  - `observe_continuation(engine, *, model, opening, response)` classifies
    one observation (`_tail_saving`, below) and records it. It keeps a
    per-engine `_TailValue`: `exact`, `bound`, `unmeasured`, a `Decimal`
    `saving` and `last_observed_at`. After recording, if the engine has at
    least six measured observations summing below zero, it latches
    `unprofitable` with one WARNING. It reads the responses and never
    changes them. It never raises: a failure is logged at DEBUG, and
    records nothing.
  - `_tail_saving(model, opening, response)` is Appendix A, with the
    refinements the deviations below describe.
    - **exact**: the continuation ran one model iteration, and the opening
      response's first iteration is known. S = R·(u − r) − W·(w − u), with
      R, `missed` and W exactly as in A.3.
    - **bound**: the continuation ran one model iteration and read
      something, but the opening's first iteration is unknown.
      S_max = read·(u − r) − write·(w − u).
    - **unmeasured**: anything else.
  - `disable_continuation_tail` and `observe_continuation` share
    `_latch_locked` (set the latch under the lock the caller holds; the
    first latch wins) and `_warn_latched` (the one WARNING, logged outside
    the lock).
  - `snapshot()`: each engine's entry gains `measured` (`exact` +
    `bound`), `exact`, `bound`, `unmeasured`, `saving_usd` (the signed
    sum, rounded to six places away from zero, never `-0.0`) and
    `last_observed_at`.
  - `reset_for_tests()` clears the observations with the latches, in the
    same lock acquisition.
- **The hooks.** In `_run_dimension` and `_run_streaming_call`, right after
  `all_responses.append(response)`, a response to a request that carried
  the tail is observed:
  `if carried_tail: cost_checks.observe_continuation(_TAIL_ENGINE, model=model, opening=all_responses[0], response=response)`.
  `carried_tail` is what CT-1's `_open_stream` yields: `False` after a
  resend without the tail, so the resend is never observed. An opening
  request never carries the tail, so its response is never observed.
- **Developer tools.**
  - `frontend/src/types.ts` gains `ContinuationTailCheck` and
    `CostChecksSnapshot`, and `DiagnosticsSnapshot.cost_checks`.
  - `frontend/src/lib/costChecks.ts` (new) has `costCheckLines(checks)`,
    `CHECK_REASON_TEXT` and `TAIL_ENGINE_LABELS`.
  - `DeveloperToolsModal`'s Environment section renders one `Row` per
    line after "Models". The first row is named "Cost self-checks".
- **`tests/fakes.py`**: `usage(..., iterations=)` attaches
  `usage.iterations` only when supplied, entries as given (dicts or
  objects). Every existing fixture stays byte-identical.

**Deviations from the spec, and why**

1. **An exact observation needs ONE model iteration in the continuation.**
   The spec's exact path uses the first `message` iteration of any
   continuation that reports iterations. A continuation that ran two or
   more iterations (a server tool ran inside it) is now unmeasured,
   because its first iteration does not bound what the tail saved on it.

   The provider puts an automatic breakpoint after each server tool's
   result, and a later iteration reads the entry the tail wrote. Take P0
   as the explicit prefix. T follows it: R of T is readable from earlier
   entries, and W is not. O1 is the first iteration's output, and X1 the
   first tool result.
   - Without the tail: iteration 1 reads P0 and pays T at the input rate.
     Iteration 2's breakpoint at X1 reads P0 + R, and writes W + O1 + X1.
   - With the tail: iteration 1 reads P0 + R and writes W. Iteration 2
     reads P0 + T, and writes O1 + X1.

   So the tail saves R·(u − r) − W·(w − u) on iteration 1, the Appendix A
   term, and W·(w − r) more on iteration 2. The total is
   (R + W)·(u − r) ≥ 0, and iterations after the second are the same
   either way. The first-iteration figure understates such a request by
   W·(w − r), so it is not the upper bound A.5 needs. Summing it could
   latch a tail that was saving money, which is the one outcome the rule
   exists to avoid.

   `_one_model_iteration` requires, with `usage.iterations` reported,
   exactly one entry, a `message`. Without it, the response must provably
   be a single iteration.
2. **A continuation that answers a pending server tool call is unmeasured.**
   The provider runs the pending tool first, so the first iteration's input
   is the request plus that tool's result, behind the provider's own
   automatic breakpoint. The tail is redundant there, and the write the
   usage reports includes the new result: a 30,000-token fetched page
   would be charged to the tail as a loss it never caused.

   `_answers_pending_tool` detects it from the response itself: a
   `*_tool_result` block whose `tool_use_id` names no `server_tool_use`
   or `mcp_tool_use` block earlier in the same response. The server tools
   page says a resumed response begins with such a block. Unreadable
   content counts as pending: never measure what cannot be read.
3. **The single-iteration fallback is an allowlist.** The spec says: no
   `server_tool_use` block, and no web search or fetch requests in the
   usage. The code requires:
   - content made only of `text`, `thinking`, `redacted_thinking` and
     `tool_use` blocks;
   - no `*_requests` count above zero in `usage.server_tool_use`. That
     means the two web tools, plus any other such count the record
     carries, whether a declared field or a pydantic extra from a newer
     provider.

   A pending resume (a result block with no use block) and any block type
   this code has never seen are then excluded too. The direction is safe:
   more unmeasured, never a false single iteration.
4. **The arithmetic is in `Decimal`, with rates to twelve significant
   digits** (`_rate`: `Decimal(format(float(rate), ".12g"))`).
   `settings.PRICING` divides per-million prices by a million in floats,
   so Sonnet 5's and Opus 5.5's cache read is `2.0000000000000002e-07`.
   With that noise kept, an observation that exactly breaks even (read 5,
   write 18 on Sonnet 5) sums to a hair below zero, and twelve of them
   latch. Twelve digits strip the noise and touch no real price (checked
   for every row of `PRICING`). Pinned by
   `test_a_sum_at_or_above_zero_never_latches`.

   The dollar figures the check reports (`saving_usd`, and the latch's
   detail) round to six places **away from zero** (`_usd`), not half-even.
   The rule reads the exact sum, so a sum a tenth of a millionth of a
   dollar below zero latches. Rounded half-even it would then read as a
   saving of `0.0` beside the reason `unprofitable`. Pinned by
   `test_a_loss_under_a_millionth_of_a_dollar_still_reads_as_a_loss`.
5. **What counts as a count.** `input_tokens` and `output_tokens` must be
   non-negative ints (a bool is not one: `isinstance(True, int)` is true).
   A cache count may be `None`, which the GA `Usage` types allow and which
   means zero. Anything else makes the iteration unreadable, so the
   observation is unmeasured. A record that is not a usage record never
   reads as a zero observation that would count toward the six.
6. **One lock acquisition per observation, latch included.** A reader
   never sees a count without the latch it earned. The WARNING is logged
   outside the lock. The first latch wins, whatever its reason: a tail
   refused (CT-1) and later measured at a loss stays `rejected`.

   Observing carries on after a latch, as step 4 asks. In practice only
   requests already in flight when it latched still report back, since no
   later request carries the tail. So the running sum can move a little
   after the latch, even back above zero.
7. **`last_observed_at` covers every observation**, measured or not. It
   says when a response to a tail-bearing request was last seen.
8. **The frontend.**
   - The row is one `Row` per line: the first named "Cost self-checks",
     the rest with an empty name. `Row` renders a single value line.
   - The switch off in settings is said once, for both engines.
   - Money is shown to four places ("est. saving $0.0860", "est. loss
     $0.2892"), not the example's two: a continuation saves fractions of a
     cent.
   - The latch's detail stays out of the row, which is plain words. It is
     in Copy snapshot JSON and in the activity log's WARNING.
   - The `unprofitable` reason reads "it had cost more than it saved",
     past tense, because the counts beside it keep running (deviation 6).
     The frontend test pins that line beside a later saving.
   - Beyond the spec's reason pin, the test also pins the engines, in
     `TAIL_ENGINES` order.
   - A CT-1-era backend (no counts) renders "measurement not reported". An
     unknown reason renders "switched off (<reason>)". An unknown engine
     renders under its own id.
9. **Comments made true (R9).**
   - The tail condition's comment in both engines said a refusal was "the
     one way it can change mid-round" (mid-run for Final QC). It now names
     both ways: a refusal (`_open_stream`), or a proven loss
     (`cost_checks.observe_continuation`).
   - `cost_checks`'s module docstring said "The one check so far is the
     continuation tail's guard". It now says "The first check is", and
     describes the second.

**Knowing changes to existing tests**

- `test_diagnostics_report_the_latch_and_survive_the_scrub`: the clear
  per-engine entry gains the six new keys, at zero or `None`.
- `test_a_malformed_call_never_raises_and_switches_nothing_off` used
  `reason="unprofitable"` as a reason outside the vocabulary. CT-2 made it
  a real reason, so the test now uses `"not_a_reason"`, which never will
  be. WL-1's `not_read` would have broken it again.
- Fixtures: `tests/fakes.py`'s `usage(iterations=)`, attached only when
  supplied.

**The tests** — `tests/test_cost_checks_tail_value.py`, 29 tests (32
counting parametrized cases). Every engine run passes
`continuation_cache=True`; the runners take it as a required keyword. An
autouse fixture first checks the rates the hand-computed savings assume
(Sonnet 5 2.00/0.20/2.50 and Opus 5.5 4.00/0.20/5.00 per million), so a
price change fails there, under its own name.

- The arithmetic:
  - `test_an_exact_observation_measures_what_the_tail_read_and_wrote`:
    $0.068.
  - `test_an_expired_opening_entry_is_not_charged_to_the_tail`: the
    `missed` term, −$0.015 two ways.
  - `test_a_bound_credits_every_read_and_charges_every_write`: $0.086, and
    bound − exact = P0·(u − r).
  - `test_final_qc_is_priced_on_its_own_model`: Opus 5.5, $0.182.
  - `test_a_response_that_ran_server_tools_without_iterations_is_unmeasured`
  - `test_a_bound_with_nothing_read_is_skipped`
  - `test_iterations_are_read_as_dicts_and_as_objects`: a leading
    `compaction` entry skipped, and exact end to end from object entries.
  - `test_a_continuation_that_ran_more_than_one_iteration_is_unmeasured`
    (deviation 1)
  - `test_a_continuation_that_answers_a_pending_call_is_unmeasured`
    (deviation 2), with a control that is measured.
  - `test_the_top_level_usage_stands_in_only_when_no_server_tool_ran`
    (deviation 3)
  - `test_malformed_usage_raises_nothing_and_measures_nothing`: fourteen
    malformed shapes, each against three openings, seven times; the
    responses unchanged; a failure inside the check logged at DEBUG,
    recording nothing; an unknown engine.
- The latch:
  - `test_five_losing_observations_do_not_latch_and_the_sixth_does`: the
    exact detail, one WARNING, the other engine untouched.
  - `test_unmeasured_observations_never_count_toward_the_six`
  - `test_a_sum_at_or_above_zero_never_latches`: twelve exact break-evens
    (deviation 4), and a win that outweighs ten losses.
  - `test_a_loss_under_a_millionth_of_a_dollar_still_reads_as_a_loss`:
    −$0.0000001 latches and reads as −$0.000001 (deviation 4).
  - `test_the_latch_persists_however_many_winning_observations_follow`
  - `test_a_refused_tail_is_never_relabelled_unprofitable`
  - `test_an_observation_takes_the_one_lock_once`: CT-1's counting lock.
  - `test_many_threads_observe_without_losing_a_count`
- The hooks, end to end:
  - `test_a_paused_research_dimension_observes_each_continuation`: an
    opening, a continuation that searched (unmeasured) and a closing one
    (bound). Only the two tail-bearing requests' responses are observed,
    each with the opening response, by identity (`is`: the fake hands
    back the scripted object, and SimpleNamespace compares by value).
  - `test_reported_iterations_make_a_research_observation_exact`
  - `test_a_paused_compliance_lens_observes_its_continuations`
  - `test_a_streamed_web_tooled_seat_observes_its_continuation`
  - `test_a_tail_free_resend_is_never_observed` (both engines)
  - `test_an_opening_response_is_never_observed` (both engines): a call
    that completes on its first request, and a paused call with the switch
    off.
  - `test_an_unprofitable_latch_takes_the_tail_off_the_next_request` (both
    engines)
- `test_the_measurement_is_invisible` (F3, F4, R6). Both engines run twice,
  measuring and with `observe_continuation` a no-op. Everything is
  identical: requests, records, `usage_total()`,
  `usage_by_meter_category()`, the input manifest and the fingerprint.
  The only exceptions are the fields no two runs share, `round_id` and
  QC's wall-clock `duration_ms`. The manifest names none of the check.
- `test_diagnostics_report_the_counts_and_survive_the_scrub`
- `test_the_rates_come_from_the_ledger`: `model_rates` equals `_rates` for
  every priced model and an unknown one, and returns a copy. The check's
  source reads no price table. Doubling what `_rates` returns doubles the
  saving.

`frontend/tests/costChecks.test.ts` (9 tests, registered in
`frontend/package.json`):
- each state's line: switched off in settings, on with nothing measured,
  on and measured, rejected, unprofitable (with its loss, and beside a
  later saving);
- the reason vocabulary and the engines, pinned against
  `backend/cost_checks.py`;
- a missing, older or malformed snapshot renders "not reported", or a
  per-engine line, and never throws;
- the modal renders the row through the helper.

**What the SDK and the API reference say about `usage.iterations`**
(checked 2026-09-24; no request was made, R5)

- **The SDK.** `anthropic` 1.8.0 is installed; `requirements.txt` allows
  `>=1.0,<2`.
  - The GA `anthropic.types.Usage` has no `iterations` field. Its fields
    are `cache_creation`, `cache_creation_input_tokens`,
    `cache_read_input_tokens`, `inference_geo`, `input_tokens`,
    `output_tokens`, `output_tokens_details`, `server_tool_use` and
    `service_tier`. It allows extra fields: a GA response carrying
    `iterations` keeps it as a list of plain dicts (checked locally with
    `construct_type(Message, …)`).
  - The GA `ServerToolUsage` has `web_fetch_requests` and
    `web_search_requests`.
  - The beta `BetaUsage.iterations` is `Optional[BetaIterationsUsage]`: a
    list discriminated on `type`. Its members are
    `BetaMessageIterationUsage` (`type: "message"`, with `input_tokens`,
    `output_tokens`, `cache_read_input_tokens` and
    `cache_creation_input_tokens` as required ints, plus optional
    `cache_creation` and `model`), `BetaCompactionIterationUsage`,
    `BetaAdvisorMessageIterationUsage` and
    `BetaFallbackMessageIterationUsage`. Its docstring calls message
    entries "model sampling iterations, such as the turns of a server-side
    tool use loop", for understanding "token accumulation across
    server-side tool use loops".
- **The API reference.**
  - The Messages API reference (GA and beta) did not render its response
    schema when fetched, so it says nothing either way.
  - The web search tool page's usage examples, for the GA endpoint, carry
    no `iterations`.
  - The tool-use documentation says that with prompt caching on, "the API
    automatically places a cache breakpoint on the server tool result
    before running the next iteration of the agentic loop", with the
    default 5-minute TTL, and only when the request already has a
    `cache_control` marker. That is the entry a later iteration reads in
    deviation 1.
  - The server tools page says a paused turn's pending tool runs at the
    start of the next request, and that the next response begins with that
    tool's result block. That is deviation 2.
  - The only other documentation found that names `usage.iterations` is
    for server-side fallbacks (beta), where top-level usage covers only the
    attempt that produced the message.
  - No page says the GA endpoint returns `usage.iterations` for a
    server-tool loop.
- **What that means.** The exact path is ready, and pinned, for a response
  that carries `iterations` in either shape. On the GA endpoint the app
  calls today, it should rarely if ever fire, so the bound path carries
  the check.

**What the check cannot see — for CT-3, and for Abraham**

On the GA endpoint, a continuation whose request ran a server tool reports
no per-iteration usage, so it is unmeasured. The measured continuations
are then only the ones that ran no server tool: in practice, each paused
conversation's closing request (research's findings, a lens's findings, a
seat's verdict). Deviation 1 shows those are the only continuations on
which the tail can lose; one that ran a server tool never loses under the
documented cache model. So the latch's sum is a proven loss on the requests
it measures, exactly as A.5 says, but not across all continuations.

In §2's bad case (entries that do not match what is re-sent), a paused
area's closing continuation loses T·(w − u), while each earlier continuation
that searched gains T·(u − r). On Sonnet 5 that is 3.6 times as much per
token. So the check could switch off a tail that is saving money overall.
That is the safe direction for spend, because a latch can only remove a
saving, and it is what the spec specifies. It is not a CT-2 decision, but
CT-3 and Abraham should know it before the flip.

For the same reason, §2's row "each continuation pays the 5-minute write
premium on the turn it re-sends" holds only for a continuation that runs no
server tool. The spec text stays as written; this is the correction.

**Verification** (Linux container, from the repository root)

- `.venv/bin/python -m ruff check .`: all checks passed.
- `.venv/bin/python -m pytest -q` on the finished code: 2944 passed,
  64 skipped (7 min 37 s). CT-1 ended at 2912; the 32 new are this file's
  cases. An earlier run, before the rounding change and its test: 2943
  passed, 64 skipped. The revert matrix's baseline passed 250 of 250
  across its eight suites.
- `npm test` (in `frontend/`): 430 passed, 0 failed (CT-1: 421).
- `npm run build`: built; the only warning is the existing chunk-size one.
- `tests/test_tier1_finish_tracker.py` and `tests/test_docs_consistency.py`
  pass with this As built and the ticks in place.
- `git diff --name-only origin/master...HEAD -- CLAUDE.md README.md`
  prints nothing.

**Revert matrix.** Each mechanism was reverted in place, one at a time, in
the working tree, by a script: it replaced one exact snippet, ran the
suites, restored the exact text read, and checked it byte-identical. The
backend rows ran the new file plus `test_cost_checks_tail_rejection.py`,
`test_continuation_cache.py`, `test_research_engine.py`,
`test_qc_live_events.py`, `test_retry_resume.py`, `test_usage.py` and
`test_diagnostics.py`. The frontend rows ran `frontend/tests/costChecks.test.ts`.
The whole tree's hash was unchanged at the end. "Red" counts failing tests.

49 rows, 49 red.

| Mechanism reverted | Red |
|---|---|
| `observe_continuation` records nothing | 24 |
| no six-observation floor (a first loss latches) | 2 |
| a sum of exactly zero latches (`<= 0`) | 1 |
| unmeasured observations count toward the six | 1 |
| a later latch overwrites the first | 2 |
| an unprofitable latch writes no WARNING | 2 |
| the latch taken in a second lock acquisition | 1 |
| the observation's time not recorded | 2 |
| exact: the `missed` term dropped (every write charged) | 1 |
| exact: every read credited (the prefix not subtracted) | 5 |
| exact: the write charged at w − r, not w − u | 5 |
| exact path gone (a known opening measured as a bound) | 7 |
| bound path gone (an unknown opening unmeasured) | 18 |
| bound with nothing read not skipped | 1 |
| rates read as their float repr (the noise kept) | 5 |
| the snapshot's saving can read `-0.0` | 1 |
| dollars rounded half-even (a tiny loss reads as a saving of 0.0) | 1 |
| a multi-iteration continuation measured from its first iteration | 1 |
| a continuation answering a pending call measured | 1 |
| single iteration: only a `server_tool_use` block excluded (the spec's rule) | 1 |
| single iteration: the usage's request counts ignored | 2 |
| single iteration: only the two web-tool counts read | 1 |
| reported iterations never read | 9 |
| iterations read only as objects (a dict entry missed) | 8 |
| iterations read only as dicts (an object entry missed) | 26 (the fakes' responses and usage records are objects too, so nothing is read) |
| the first entry read whatever its type | 1 |
| a bool accepted as a count | 1 |
| a negative count accepted | 1 |
| a missing cache count malformed (not zero) | 1 |
| a missing required count read as zero | 1 |
| the check reads the price table itself | 2 |
| `model_rates` hands out the table itself (no copy) | 7 |
| the snapshot without the counts | 25 |
| reset clears the latches but not the observations | 28 (observations carry into every later test) |
| research: no hook | 4 |
| research: every response observed (not only tail-bearing ones) | 7 |
| research: the previous response passed as the opening | 2 |
| QC: no hook | 3 |
| QC: every response observed (not only tail-bearing ones) | 6 |
| QC: the previous response passed as the opening | 1 |
| fakes: `iterations` never attached | 8 |
| frontend: no words for unprofitable | 2 |
| frontend: the unprofitable reason in the present tense | 1 |
| frontend: the engines in another order | 6 |
| frontend: the switch off said per engine | 1 |
| frontend: a loss shown as a saving | 1 |
| frontend: an older backend's missing counts read as zero | 1 |
| frontend: a missing snapshot renders nothing | 1 |
| frontend: the modal renders its own text, not the helper's | 1 |

The first run found one row green: "unmeasured observations count toward
the six". Its test observed five losses and then twenty unmeasured
observations, and the rule is consulted only when an observation is
measured, so a rule that counted the unmeasured ones never got to act. The
test now observes the unmeasured ones first; that row went red, and the
whole matrix was run again on the final code, with the result above.

**For FIN-1**

- **CLAUDE.md implemented notes** — a section "The continuation tail
  measures what it saves — implemented notes (Tier 1 finish, CT-2)", in
  CLAUDE.md's own style. The why and the traps:
  - *Why measure.* The tail's bad case (§2) is bounded but is a loss on
    every continuation for as long as nobody looks, and a default-on switch
    should notice its own loss. The check reads only responses the app
    already receives (R5).
  - *What is measured* (Appendix A): exact from both first iterations,
    bound on a single iteration with a read, and anything else counted,
    never guessed. It latches `unprofitable` on six or more measured
    observations summing below zero, and keeps observing afterwards.
  - *What the plan's formula cannot see* (deviations 1 and 2). A
    continuation that ran a server tool saves at least (R + W)·(u − r),
    because a later iteration reads the tail's entry. A continuation that
    resumes a pending tool has its first iteration behind the provider's
    own breakpoint. Both are unmeasured.
  - *GA reports no per-iteration usage today*, so the closing continuation
    carries the check. The latch sums only the continuations that can
    lose (the section above).
  - *Traps.*
    - `PRICING`'s float division (`0.20 / 1_000_000` is
      `2.0000000000000002e-07`) makes a break-even observation a hair
      negative. The fix is `Decimal` at twelve significant digits.
    - The dollar figures round away from zero, so a proven loss a
      fraction of a millionth of a dollar never reads as a saving of 0.0.
    - A bool is an int.
    - The hook reads CT-1's `carried_tail`, never
      `"cache_control" in stream_kwargs`: the resend drops the tail.
    - A test pins "the conversation's opening response" by identity,
      because the fake returns the scripted object and SimpleNamespace
      compares by value.
    - Comparing two runs' records needs `round_id` (uuid4) and QC's
      `duration_ms` set aside, and the same scripted objects reused,
      because a fake mints its ids per build.
    - One lock acquisition per observation, latch included.
- **Layout.** Change `backend/cost_checks.py`'s entry, as CT-1's For FIN-1
  worded it, to add:
  - "the continuation tail's value check: `first_iteration_usage` and
    `observe_continuation` (exact, bound or unmeasured per Appendix A;
    `unprofitable` after six measured observations summing below zero),
    the snapshot's counts and saving".

  Add:
  - `usage_ledger.py`: `model_rates(model)`, the public accessor that
    `cost_checks` reads.
  - `frontend/src/lib/costChecks.ts`: Developer tools' "Cost self-checks"
    row, with the reason and engine vocabularies pinned against
    `backend/cost_checks.py`.
  - `tests/test_cost_checks_tail_value.py` and
    `frontend/tests/costChecks.test.ts`.

  Change:
  - the two engines' entries: the observation hook after the append,
    when `carried_tail`;
  - the DeveloperToolsModal component entry: the Cost self-checks row;
  - `tests/fakes.py`'s `usage(iterations=)`.
- **Errata** for earlier CLAUDE.md sections:
  - "A paused call reads its own cache (Research/QC cost Tier 1, Chunk 4)"
    says the worst case "costs each continuation the 5-minute write
    premium on its re-sent turn". That holds only for a continuation that
    runs no server tool; one that does gains even then (deviation 1).
  - The same section says "M3 would show cache writes rising while reads
    do not, and the flip rule catches exactly that". Since CT-2, the
    runtime check catches it on the continuations it can measure, with no
    M3.
  - CT-1's For FIN-1 README note, "CT-2 adds the Developer tools line", is
    done.
- **README** — in the continuation-tail material and the
  `BUILD_A_SPEC_CONTINUATION_CACHE` Configuration row: the app measures
  what the tail saves wherever the provider's usage shows it. After six
  measured continuations that together cost more than they saved, the tail
  switches off for that engine until the app restarts. Settings →
  Developer tools → "Cost self-checks" shows what was measured, and the
  estimated saving.

---

## CT-3 — Turn the continuation tail on

### Goal

`BUILD_A_SPEC_CONTINUATION_CACHE` defaults on. Everything that described it
as off is true again, except the root `README.md` and `CLAUDE.md` (R2:
FIN-1 updates them).

### Why

FD1. With CT-1 in place, the provider refusing the shape costs one request
per engine per app session. With CT-2 in place, a tail that loses money
switches itself off wherever that can be proven, and elsewhere the worst
case is bounded (+25% on the re-sent turn content). That is what F5 asks of
a default-on switch, as FD1 amends it.

### Design

1. **Flip readiness, before the flip.** Run the whole backend suite with
   the switch on from the environment:

   ```powershell
   $env:BUILD_A_SPEC_CONTINUATION_CACHE = "1"
   .\.venv\Scripts\python -m pytest -q
   Remove-Item Env:\BUILD_A_SPEC_CONTINUATION_CACHE
   ```

   On Linux: `BUILD_A_SPEC_CONTINUATION_CACHE=1 .venv/bin/python -m pytest -q`.
   Only `test_continuation_cache_ships_switched_off` may fail; it reads the
   source. Anything else that fails is a test that depended on the default.
   Fix it by passing the switch explicitly, and name it under As built.
   Record the counts.
2. **The flip.** In `backend/settings.py`, `CONTINUATION_CACHE` defaults to
   `True`. Rewrite its comment: what the tail does; that it is on because
   of FD1; CT-1's guard; CT-2's check; the bounded worst case; and that `0`
   switches it off. The comments that say it "flips only on a recorded M3
   pass" go.
3. **The pin.** Replace `test_continuation_cache_ships_switched_off` with
   `test_continuation_cache_ships_switched_on`. It still reads the default
   from the source with `ast`, so no developer's environment can make it
   pass or fail.
4. **Copy that has to stay true (R9).** Grep for `CONTINUATION_CACHE`,
   `continuation tail`, `off by default` and `M3` across `backend/`,
   `frontend/src/`, `docs/` and `tools/`, and fix every claim this made
   false. At least:
   - `docs/RELEASE_WINDOWS.md`, the section "A paused call reads its own
     cache (cost Tier 1, Chunk 4 — off by default)". Its rows become
     ordinary release QA with the default on. Add a row for switching it
     off (`$env:BUILD_A_SPEC_CONTINUATION_CACHE = "0"`) and a row for the
     Developer tools "Cost self-checks" line.
   - The trust dossier, `frontend/src/components/TrustDeepDiveModal.tsx`.
     Re-read the Research card and the Final QC card against the new
     default. Tier 1 Chunk 4's As built (item 13) said to do this at the
     flip.
   - Engine docstrings keep "off, the default for a direct caller": that
     describes the function parameter's default, which does not change.
   - Do NOT touch the root `README.md` or `CLAUDE.md` (R2). List what they
     need under For FIN-1.
5. **The release-note draft.** In `docs/plans/RESEARCH_QC_COST_TIER1_2026-09-23.md`
   §7, "Release-note draft (Tier 1)", make item 4 unconditional: drop its
   conditional marker, and update the "Which items" bullet. You may add one
   plain sentence to item 4's body saying the app switches the saving off
   by itself if the service ever refuses it. No version bump, no
   `backend/release_notes.py` entry, no tag (F7).
6. **Tell the owner what merging does.** The PR body says, near the top,
   that merging it turns the continuation tail on for everyone running from
   `master`, and how to switch it off.

### Files

- `backend/settings.py`
- `tests/test_continuation_cache.py`, plus any test that depended on the
  default (named under As built)
- `docs/RELEASE_WINDOWS.md`
- `frontend/src/components/TrustDeepDiveModal.tsx`, if its cards need it
- `docs/plans/RESEARCH_QC_COST_TIER1_2026-09-23.md` §7
- this plan's As built, and the tracker's CT-3 row and checklist

### Acceptance

- **CT-3.1** Before the flip, the whole backend suite was run with `BUILD_A_SPEC_CONTINUATION_CACHE=1`; only the ships-off pin failed, or every other failure was fixed and named. The counts are recorded under As built.
- **CT-3.2** `settings.CONTINUATION_CACHE` defaults to `True`, and its comment says why (FD1, CT-1, CT-2, the bounded worst case) and how to switch it off.
- **CT-3.3** `test_continuation_cache_ships_switched_on` replaces `test_continuation_cache_ships_switched_off`, and still reads the default from the source with `ast`.
- **CT-3.4** Every claim outside the root `README.md` and `CLAUDE.md` that the tail is off by default, or waits on M3, is true again: `docs/RELEASE_WINDOWS.md`'s Chunk 4 rows (with an off-switch row and a self-check row), the trust dossier's Research and Final QC cards, and any comment or docstring the grep found.
- **CT-3.5** The Tier 1 plan's §7 lists item 4 as always included, with no version bump, `backend/release_notes.py` entry or tag.
- **CT-3.6** Verified: ruff, the full pytest suite, `npm test` and `npm run build` are all clean.
- **CT-3.7** Reverting the default makes the new pin go red, and restoring it makes it green again.
- **CT-3.8** As built is written, including its For FIN-1 list (the root README rows and text, and the CLAUDE.md errata for every section that says the tail ships off), and the root `CLAUDE.md` and `README.md` are untouched.

### As built

*Built on 2026-09-24; the record follows the paragraph below.*

When you build this session, append here what CT-1's As built lists, plus
the flip-readiness counts.

#### CT-3 as built (2026-09-24)

Built from `master` at `66c10d8` (CT-2's merge), on branch
`claude/relaxed-pasteur-o7k36u`. **Merging it turns the continuation tail
on for everyone running from `master`.** `BUILD_A_SPEC_CONTINUATION_CACHE=0`
switches it off (PowerShell: `$env:BUILD_A_SPEC_CONTINUATION_CACHE = "0"`;
Command Prompt: `set BUILD_A_SPEC_CONTINUATION_CACHE=0`). The reconcile step
filled in CT-2's merge commit, `66c10d8`.

**Flip readiness (CT-3.1).** Before the flip, the whole backend suite ran on
`66c10d8` (plus only the reconcile edit) with
`BUILD_A_SPEC_CONTINUATION_CACHE=1` in the environment:
**1 failed, 2943 passed, 64 skipped** (410.78 s).

- `test_continuation_cache_ships_switched_off` **passed**. The spec allowed
  for it to fail, but it reads the default from the source with `ast`, so
  no environment can reach it, in either direction.
- The one failure was a test that depended on the default:
  `tests/test_qc_verifier_v3.py::test_output_and_ordinary_call_failures_do_not_trip_shared_breaker`.
  It predates this program. It scripts one verifier seat as a pause, then a
  400 on the continuation, then the verdict meant for the finding's second
  seat. With the tail on, the continuation carried it, so CT-1's guard sent
  the same request again without it. The resend took the second seat's
  verdict, succeeded, and latched Final QC's tail `rejected`: 10 verifier
  requests instead of the 9 the test asserts, and the latch set. No other
  test depended on the default.
- **The fix is the spec's**: the file's `_run` helper takes
  `continuation_cache: bool = False` and passes it to `run_final_qc`
  explicitly (it used to leave it `None`, which reads the setting). With
  the tail off the test pins exactly what it always pinned, and its
  assertions are unchanged.
- The same suite was not re-run with the environment variable after the
  fix, because the default is now on: the "Verification" run below is the
  same thing, and it passes.

**What was built**

- **`backend/settings.py`**: `CONTINUATION_CACHE = _bool_env("BUILD_A_SPEC_CONTINUATION_CACHE", True)`.
  The comment's first paragraph (what the tail does) is unchanged. The
  paragraph that said "OFF by default, deliberately … The default flips
  only on a recorded M3 pass" is replaced. It now says:
  - the tail is on since CT-3, under FD1, without the measured trial the
    flip once waited on, and the self-checks in `backend/cost_checks.py`
    can only switch it off, one engine at a time, until the app restarts;
  - a refused tail costs one resend and switches that engine's tail off
    (CT-1): one extra request per engine per app session, never a failed
    research area or review;
  - six or more measured continuations that together cost more than they
    saved switch it off too (CT-2);
  - what the checks cannot see is bounded (+25% on the re-sent turn);
  - `0` switches it off;
  - it stays out of the QC input manifest (F3).

  The warm lead's comment just above it is WL-2's, and unchanged.
- **`tests/test_continuation_cache.py`**: `test_continuation_cache_ships_switched_on`
  replaces `test_continuation_cache_ships_switched_off`. It is the same
  `ast` walk over `backend/settings.py`, now expecting
  `["BUILD_A_SPEC_CONTINUATION_CACHE", True]`, and its docstring says why
  the default moved and how an operator switches the tail off.
- **`tests/test_qc_verifier_v3.py`**: `_run(client, *, continuation_cache=False)`,
  passed explicitly, with a comment saying why it never reads the setting;
  and one new test (below).
- **Copy (R9)**, everything outside the root files that the flip made
  false:
  - `docs/RELEASE_WINDOWS.md`: the section is now "A paused call reads its
    own cache (cost Tier 1, Chunk 4 — on by default)". Its rows run on an
    ordinary build. The research row says what a refusal looks like (the
    step is sent once more, and one `Cost self-check: the continuation
    tail is switched off for research` WARNING appears); the comparisons
    are made against a run with the switch off. Two rows are new: **The
    Cost self-checks row** (both engines `on`, what was measured, and never
    `off for this session` without the WARNING that switched it off), and
    **Switching it off** (`"0"` before start; the row then reads
    `Continuation tail: switched off in settings`, and nothing carries the
    breakpoint). "Off is unchanged" went; the new last row covers it.
  - The trust dossier (`frontend/src/components/TrustDeepDiveModal.tsx`):
    the Research card's "AI involved" row says that when the API pauses a
    conversation, the next step reads what it re-sends from the cache; that
    a refusal is sent once more without it; and that after a refusal, or
    once the reuse has measurably cost more than it saved, the app stops
    until a restart, with Developer tools → Cost self-checks showing what
    it measured. The Final QC card's "AI involved" row says the compliance
    lens does the same under the same self-checks, and why batched seats
    never do. The warm lead's "off by default" sentence is WL-2's, and
    unchanged.
  - Both engines' tail comments (`research/engine.py` and `qc/engine.py`,
    above `_CONTINUATION_CACHE_CONTROL`): "MEASURED BY M3 … M3's flip rule
    catches it" is replaced by what now watches the tail (CT-1's resend and
    CT-2's value check), and CT-2's correction that a continuation that ran
    a server tool gains even when the entries do not match.
  - The two latch notes CT-2 left naming only a refusal:
    `run_requirements_research`'s docstring and `run_final_qc`'s pin
    comment now say the latch also fires once the tail "has provably cost
    more than it saved".
  - `backend/cost_checks.py`'s module docstring: the savings "shipped off",
    and with the checks in place the tail defaults on (CT-3).
  - Test comments: `tests/test_qc_live_events.py`'s `_run_client` and
    `tests/test_cost_checks_tail_rejection.py`'s module docstring no longer
    call the default off.
  - This program's own tense: the tracker's "built two savings that still
    ship **off**" and the plans index's "that still ship off" now say
    "shipped off" (true of when the program opened, and after it).
  - The Tier 1 progress file gains a dated note at its top: Chunk 4's
    switch is on since this PR, so where that file says two switches ship
    off, only Chunk 3's still does, until WL-2. Its closeout text is left
    as the record it is.
  - The engine docstrings that say "off, the default for a direct caller"
    are unchanged, as the spec says: they describe the function
    parameter's default, which did not move.
- **The release-note draft** (`docs/plans/RESEARCH_QC_COST_TIER1_2026-09-23.md`
  §7, CT-3.5). Item 4 lost its conditional marker and gained one sentence:
  "If the service ever refuses this, or it turns out to cost more than it
  saves, the app stops doing it by itself until you restart it." The
  "Which items" bullet says items 1, 2 and 4 always ship and item 3 is
  conditional on `BUILD_A_SPEC_QC_BATCH_WARM_LEAD` until WL-2. No version
  bump, no `backend/release_notes.py` entry, no tag (F7).

**Deviations from the spec, and why**

1. **The ships-off pin passed the flip-readiness run.** The spec allowed it
   to fail ("Only `test_continuation_cache_ships_switched_off` may fail").
   It passed, as an `ast` pin must: the environment cannot reach it.
   Nothing to fix, and the flip replaced it anyway.
2. **A tail-on twin beside the fixed test.** The spec's fix is only to pass
   the switch explicitly. Pinning the tail off alone would leave the
   breaker's behaviour under the new default untested, so
   `test_a_400_that_outlives_the_tail_still_spares_the_shared_breaker` runs
   the same post-response 400 with the tail on. It pins that CT-1 sends the
   refused continuation once more without the tail, that the resend's 400
   latches nothing (it was not the tail's), that the seat fails as before
   one counted request later (`api_request_count` 3, one response), and
   that the shared-failure breaker stays closed (the seat had a response,
   so it is no shared failure) and the later candidate is verified. It
   runs with one worker: the fake routes scripts by title, so a finding's
   two seats share one queue.
3. **The copy sweep went past the spec's list.** The spec names the
   release checklist, the dossier and "any comment or docstring the grep
   found". The grep also found the engines' M3 comments, the two latch
   notes CT-2 left incomplete, `cost_checks.py`'s docstring, two test
   comments, and "still ship off" in the tracker, the plans index and the
   Tier 1 progress file. Each is a claim R9 says must be true at this
   merge; every change is listed above.
4. **§7 changed in three more places than item 4 and its bullet.** The
   summary sentence now covers three items, because item 4 now always
   ships and a summary that mentions the program should say so. The
   block's intro, which said the wording is "the chunks' own, unchanged",
   names what CT-3 added. "Where it goes" says "a conditional item", since
   only one is left.
5. **The dossier now describes a resume's cost.** Tier 1 Chunk 4's As
   built (item 13) left it unchanged because it described the shipped
   defaults and "makes no claim about what a resume costs". With the tail
   on by default, a research conversation and the compliance lens behave
   differently on every pause, so each card names it, with the self-checks
   that can switch it off. That is the re-read item 13 asked the flip
   session for.

**Knowing changes to existing tests**

- `test_continuation_cache_ships_switched_off` → `test_continuation_cache_ships_switched_on`
  (the spec's CT-3.3).
- `tests/test_qc_verifier_v3.py`'s `_run` passes `continuation_cache`
  explicitly (default `False`). Every test in the file that runs the engine
  goes through it; only
  `test_output_and_ordinary_call_failures_do_not_trip_shared_breaker` was
  affected, and its assertions are unchanged.
- Comment and docstring only: `tests/test_qc_live_events.py` and
  `tests/test_cost_checks_tail_rejection.py`.

**The tests.** One new test:
`tests/test_qc_verifier_v3.py::test_a_400_that_outlives_the_tail_still_spares_the_shared_breaker`
(deviation 2). It passes `continuation_cache=True`, per R7.

**Verification** (Linux container, from the repository root)

- `.venv/bin/python -m ruff check .`: all checks passed.
- `.venv/bin/python -m pytest -q` with the new default (no environment
  variable): 2945 passed, 64 skipped (6 min 46 s). CT-2 ended at 2944;
  the one new test is the twin.
- The same suite with `BUILD_A_SPEC_CONTINUATION_CACHE=0`, to show no test
  depends on the tail being on: 2945 passed, 64 skipped (6 min 36 s).
  The same count, because every test of the tail passes the switch
  explicitly (R7), and the pin reads the source.
- `npm test` (in `frontend/`): 430 passed, 0 failed.
- `npm run build`: built; the only warning is the existing chunk-size one.
- `tests/test_tier1_finish_tracker.py` and `tests/test_docs_consistency.py`
  pass with this As built and the ticks in place (18 passed).
- `git diff --name-only origin/master...HEAD -- CLAUDE.md README.md`
  prints nothing.

**Revert matrix.** Each mechanism was reverted in place, one at a time, by
a script: it replaced one exact snippet, ran the suites, restored the exact
text read, and checked the file byte-identical. Every row ran
`tests/test_continuation_cache.py`, `tests/test_qc_verifier_v3.py` and
`tests/test_cost_checks_tail_rejection.py`. "Red" counts failing tests.

5 rows, 5 red. The three suites' baseline passed 56 of 56, and after the
last row the pin passed again on its own.

| Mechanism reverted | Red |
|---|---|
| the default back to `False` | 1 (`test_continuation_cache_ships_switched_on`) |
| `test_qc_verifier_v3.py`'s `_run` leaves the switch to the setting | 1 (`test_output_and_ordinary_call_failures_do_not_trip_shared_breaker`) |
| `_run` ignores its argument (the tail always off) | 1 (the twin) |
| QC: a resend refused too still latches (CT-1's rule reverted) | 2 (the twin, and CT-1's `test_a_resend_refused_too_fails_as_before_and_latches_nothing[qc]`) |
| QC: the tail never sent | 16 (the twin, 6 in `test_continuation_cache.py` and 9 in `test_cost_checks_tail_rejection.py`) |

The second row is the flip-readiness failure itself: with the default on,
the helper must pass the switch, or the older test meets CT-1's guard
again. The last two show that the twin really runs with the tail on and
through CT-1's guard.

**For FIN-1**

CT-1's and CT-2's For FIN-1 lists still stand; this adds what the flip
itself changes.

- **README** (`README.md`), in "## Research and Final QC cost (Tier 1)":
  - The paragraph "Chunks 3 and 4 are built but ship switched off …"
    (about line 1076): Chunk 4 has been on by default since CT-3, without
    the measured run, under the Tier 1 finish program's self-checks. Only
    Chunk 3 waits, until WL-2. "Neither is planned … so both stay off" is
    no longer true.
  - The heading "### A paused call reads its own cache (Chunk 4, switched
    off)" (about line 1214): "on by default".
  - Its bullet "**Off by default, deliberately.** … `=1` switches it on for
    a trial" (about lines 1224–1232): on by default; a refusal is sent once
    more without the tail and switches that engine's tail off until a
    restart (CT-1); a proven loss does the same (CT-2); `=0` switches it
    off, with the PowerShell and Command Prompt forms of `"0"`.
  - Its bullet "What it saves", "which is exactly what the trial checks
    for" (about line 1251): what CT-2's value check measures, on the
    continuations it can measure.
  - Its bullet "Measured, not modelled" (about lines 1252–1257): "after a
    Research round with the switch on … than a round without it" becomes a
    comparison with a round made with the switch off. "There is no
    activity-log line for it" is false since CT-1: a latch writes one
    WARNING, and Developer tools has the "Cost self-checks" row (CT-2).
  - The Configuration row for `BUILD_A_SPEC_CONTINUATION_CACHE` (about
    line 2820): default `1`; drop "**off** until a measured run shows the
    provider accepts it and it pays"; say what `0` does; the examples set
    `"0"`.
- **CLAUDE.md implemented notes**: a section "The continuation tail is on
  by default — implemented notes (Tier 1 finish, CT-3)", in CLAUDE.md's
  own style (FIN-1 may fold it into its one consolidated section). The why
  and the traps:
  - *Why it could flip without M3* (FD1): CT-1 removed the one unbounded
    failure (a refusal is one resend per engine per app session), CT-2
    switches the tail off on a proven loss where the usage shows it, and
    the rest is bounded at +25% on the re-sent turn. Merging this PR was
    Abraham's approval.
  - *The pin reads the source.* The flip-readiness run with `=1` passed
    the old ships-off pin, because an `ast` pin cannot be reached by the
    environment. That is the point of it, and why the new pin is the same
    walk.
  - *A test that scripts a 400 right after a pause must say which regime
    it pins.* With the tail on, CT-1's guard meets any 400 on a
    tail-bearing continuation: the resend takes the next scripted item. A
    script meant for "the call fails" then succeeds and latches the tail
    instead. `test_qc_verifier_v3.py` pins the switch off for that
    contract, and a twin pins the tail-on behaviour.
  - *Fakes route by title.* `SequencedFakeClient` gives a finding's seats
    one shared queue, so a test that scripts one seat's resend runs with
    `QC_MAX_WORKERS=1`.
- **Layout**: the `settings.py` entry's
  "CONTINUATION_CACHE (BUILD_A_SPEC_CONTINUATION_CACHE, default OFF — cost
  Tier 1 Chunk 4's continuation tail, research and QC alike; flips on only
  on a recorded M3 pass; …)" becomes "default ON since the Tier 1 finish
  program's CT-3; the cost self-checks can only remove it, per engine,
  until a restart; 0 switches it off", beside CT-1's "(the self-check
  latch can only remove the tail)" edit.
- **Errata** for earlier CLAUDE.md sections:
  - "A paused call reads its own cache — implemented notes (Research/QC
    cost Tier 1, Chunk 4)" says "**It ships switched off.**" It has been
    on by default since CT-3. (CT-2's For FIN-1 already corrects the same
    section's M3 bullet.)
  - "Final QC phase 2 is batched" says `tests/test_qc_verifier_v3.py` pins
    `batch_verification=False`. It now also pins `continuation_cache=False`
    for its post-response-400 contract, with a tail-on twin.
  - "Research and Final QC cost, Tier 1, as shipped — implemented notes
    (closeout)":
    - "Chunk 4 … It ships off (`BUILD_A_SPEC_CONTINUATION_CACHE`)": on by
      default since CT-3.
    - "**Two switches ship off, for different reasons.**" and "Each
      default flips only on an M3 pass": only Chunk 3's is off, until
      WL-2, and neither flip waits on M3 any more (FD1).
    - "Everything else passes either way, so a flip is still one commit
      per chunk": stopped holding at CT-1, whose guard met one older
      test's scripted 400. CT-3 made that test pass the switch explicitly.
    - "items 3 and 4 only if their switch defaults on in the release":
      item 4 always ships since CT-3.

---

## Appendix A: the value check's arithmetic

**A.1 Rates.** u is the input rate, r the cache-read rate and w the
5-minute cache-write rate, of the engine's model, from `settings.PRICING`
through `usage_ledger`. Per million tokens today:

| Model | Engine | u | r | w |
|---|---|---:|---:|---:|
| Claude Sonnet 5 | research | $2.00 | $0.20 | $2.50 |
| Claude Opus 5.5 | Final QC (default) | $4.00 | $0.20 | $5.00 |

The tail's own entries are 5-minute entries, so w is the 5-minute rate even
on a verifier seat whose explicit markers are 1-hour.

**A.2 What the tail changes on one continuation.** Look at the request's
first model iteration: its input is exactly the request as sent. Let P0 be
the explicit prefix (tools, system and user block 0), and T everything
after it (block 1 and the re-sent turn content).
- **Without the tail,** the first iteration reads P0 from the cache and
  pays full input price for T.
- **With the tail,** the provider reads the longest cached prefix it can
  reach, P0 plus R tokens of T, and writes the rest, W tokens, as a new
  5-minute entry. So T = R + W, plus anything after the last cacheable
  block, normally nothing.

The tail's saving on that request is:

> S = R·(u − r) − W·(w − u)

It pays when R / (R + W) is above (w − u) / ((u − r) + (w − u)). That is
21.7% on Sonnet 5 and 20.8% on Opus 5.5: a low bar.

**A.3 Exact observations.** Use the first `message` iteration of the
continuation and of its conversation's opening response. On the opening
request, block 1 comes after the last breakpoint and is uncached, so:

- base = opening.read + opening.write (that is, P0)
- R = max(0, cont.read − base)
- missed = max(0, base − cont.read)
- W = max(0, cont.write − missed)

`missed` is the explicit prefix, written again because its entry had
expired. That happens with or without the tail, so it is not charged to
the tail.

**A.4 Bound observations.** The continuation made no server-tool request,
so its top-level usage is its only iteration, but P0 is unknown. Credit
every read to the tail, and charge every write to it:

> S_max = read·(u − r) − write·(w − u)

While the explicit prefix's entry is alive (read = P0 + R and write = W),
S_max = S + P0·(u − r) ≥ S. It is alive on every continuation the app
sends:
- Research's 5-minute markers are refreshed by the paused request's own
  iterations, and a continuation follows its pause at once. A Chunk 5
  backoff between them is at most tens of seconds.
- Final QC's seat markers are 1-hour, and its lenses' 5-minute markers are
  refreshed the same way as research's.

A bound observation with a read of zero is skipped: that is the case where
the assumption would not hold.

**A.5 The latch.** The sum over an engine's measured observations of S
(exact) and S_max (bound) is an upper bound on what the tail saved on those
requests. If it is below zero after at least six observations, the tail
cost money even on the most generous reading, and it switches off.

**A.6 What it cannot see.** Suppose the provider reports no per-iteration
usage, and every continuation runs server tools. Then CT-2 measures nothing
(Developer tools says so), and the tail stays on under FD1: CT-1's guard
removes the unbounded failure, and the rest is bounded at +25% on the
re-sent turn content. Example: a governing-codes continuation that re-sends
60k tokens on Sonnet 5 pays $0.12 of input today. At worst it pays $0.15
with the tail, and at best $0.012.
