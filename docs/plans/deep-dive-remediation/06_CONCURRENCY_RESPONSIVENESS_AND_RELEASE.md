# Phase 6 — Concurrency, responsiveness, and release

- Status: **complete** (6.1-6.5 landed; owner-authorized live/manual QA still owed)
- Prerequisites: Chunk 6.5 requires Phases 1-5 complete. Chunks 6.1-6.4 depend
  only on Phase 1 (6.1 additionally interacts with Chunk 4.3's metering seam —
  coordinate, don't serialize). Pulling 6.1 forward early is encouraged: the
  runner-settlement races it fixes get more load-bearing as Phase 2 multiplies
  event volume.
- Risk: medium; race fixes are low-frequency but touch ownership and snapshot
  boundaries. Use deterministic barriers in tests, never probabilistic sleeps.

## Goal

Finish the remediation by making runner and workspace transitions atomic,
moving blocking work off the event loop and out of the turn-state lock,
returning coherent document payloads, closing stopped-run trace spans, and
executing the complete automated/manual release gate.

## Chunk 6.1 — Atomic research settlement and stopped-run traces

### Research runner transaction

Refactor `ResearchRunner` so a terminal transition publishes one coherent state
under one lock:

1. verify `status == running` and the optional run token;
2. compute/adopt the merged profile while status is still running and the lock
   excludes readers;
3. assign error/error-kind;
4. set the winning run's captured cancel event when the transition is a user
   stop;
5. append the terminal event to the current event log with its sequence and
   round under the same lock;
6. clear the run token; and
7. publish the terminal status last.

No fresh `start()` can clear the log or install a new cancel event between
those steps.

### Implementation

1. Add `_append_event_locked` to `backend/research/runner.py` and make `_emit`
   delegate to it after token validation.
2. Extend/replace `_try_resolve` with explicit inputs for:
   - terminal event;
   - round number;
   - optional adopt callback; and
   - optional cancel-event identity for user stop.
   It should return the adopted profile/terminal record needed by the caller,
   not force callers to reread mutable fields after release.
3. Move every worker `research_complete`/`research_failed` append inside this
   transaction. Delete the token-free post-CAS `_emit` calls.
4. Make `stop()` capture and use the exact active round, cancel event, message,
   and token under the same lock. Never reread `self.error` or
   `self._cancel_event` after a successor can start.
5. Adopt a successful merged profile before publishing `status=complete`.
   Lock-free legacy readers then see either running+old profile or
   complete+new profile, never complete+old profile. Prefer coherent `snapshot`
   reads in app code when touching both fields.
6. Preserve event sequence density, round tagging, cumulative counts, and the
   ability to start the next round immediately after the stop response.
7. Add deterministic barrier tests for:
   - stop versus immediate restart;
   - worker success/failure versus successor start;
   - SSE drained check versus terminal append; and
   - profile adoption versus a simultaneous readiness/QC snapshot.
   Coordinate threads with `threading.Event`/barriers at injected seams; do not
   rely on microsecond timing.

### Trace scope correction

Normal worker completion already calls `research_end`/`qc_end` and records the
outcome in `spans.jsonl`. Do not duplicate those terminal frames into
`events.jsonl` merely to satisfy the earlier broad claim.

The actual fix is explicit stop:

1. Track the active research/QC trace handle with its run identity long enough
   for `stop()` or the stopped worker's finalizer to close it exactly once.
2. A research stop may close immediately as failed/cancelled because the round
   result is discarded; usage can still arrive in the ledger through Phase 4.3.
3. A QC stop may close at final attempt settlement if that is the point where
   its preserved partial report/counts become final. Whichever point is chosen,
   encode it once and test it; never let both stop and worker close the span.
4. Include the terminal status/error and available item/finding counts in the
   span close. Keep the existing `stop_requested` app event.
5. `restore()` emits an SSE compatibility event but does not represent a new
   provider run and should not fabricate a run span.

### Files

- `backend/research/runner.py`
- `backend/qc/runner.py`
- `backend/tracing/capture.py` if an idempotent close helper is useful
- `tests/test_research_rounds.py`
- `tests/test_stop.py`
- `tests/test_qc_runner_audit_integrity.py`
- `tests/test_tracing.py`
- `CLAUDE.md`

### Focused verification

```powershell
venv\Scripts\python -m pytest -q tests/test_research_rounds.py tests/test_stop.py tests/test_qc_runner_audit_integrity.py tests/test_tracing.py
```

### Acceptance criteria

- A successor run cannot receive an old terminal event or have its cancel event
  set by the prior stop.
- Terminal status and its profile/error/event are snapshot-coherent.
- Stopped research and QC runs leave no unclosed trace span.
- Normal terminal outcomes remain represented once as span closes, not
  duplicated as progress events.

### Implementation record

- Status: complete
- Commit/PR: branch `claude/phase-6-concurrency-responsiveness-xncqik`
- Tests: 8 new, all reverted in place to confirm they go red.
  `tests/test_research_rounds.py` (4): stop publishes everything before the
  next round can start; a finished round's terminal event never lands in the
  next round's log; the terminal status and its event are never visible
  apart; a lock-free reader never sees `complete` beside the old profile.
  `tests/test_stop.py` (1): a stop cannot cancel the round that replaces it.
  `tests/test_tracing.py` (3): a stopped research run closes its span at the
  stop; a stopped QC run closes its span when the attempt settles; a stop's
  span close never precedes an event the log already accepted. Full suite
  1363 passed / 9 skipped (no new skips); `npm test` 193; `npm run build`
  clean.
- Deviations:
  - **`_try_resolve` returns a `_Resolution`, not a bool.** The plan asked
    for explicit inputs (terminal event, round, adopt, cancel identity) and
    for the winner not to reread mutable fields. Round number and cancel
    event are *read inside the transaction* rather than passed in: while
    `status == running` — which the transaction has just verified — both are
    provably still this run's, so passing them would only add a way for a
    caller to pass the wrong one.
  - **The terminal event is a factory, not a dict**
    (`Callable[[_Resolution], dict]`). The success event's counts come from
    the merged profile, which only exists once `adopt` has run inside the
    lock. `_research_failed_event` is the one shared failure shape.
  - **`_failure_message` → `_failure_message_locked`**, folded into the
    transaction (it reads `profile_result`, which the same critical section
    is writing).
  - **The trace fix needed no `capture.py` helper.** Exactly-once is
    structural instead: each runner holds the open handle and the terminal
    transition CLAIMS it, so the loser has nothing to close. Research closes
    at the stop; QC closes at settlement (`_finalize_attempt`, whoever won
    the status race) and never at `stop()` — encoded once each and tested.
  - **A span is opened only after the compare-and-set**, so a refused
    double-start never fabricates one. That leaves a window in which a stop
    can resolve first, so adoption is token-checked and an orphan closes on
    the spot, reading the runner's terminal state only while it is still
    terminal (a successor started in the same window owns it by then).
  - **The QC span now records `latest_attempt_status`**
    (`complete|partial|failed|cancelled`) and its error rather than a flat
    `complete`/`failed` — the plan's "terminal status/error and available
    counts".
  - **`restore()` appends its compatibility event inside its own lock** for
    the same reason as the transaction; it still opens no span.
  - **Review finding (Codex, PR #107), fixed in the same branch:** closing
    the span made event ORDER matter, so research's trace mirror moved
    inside `_emit`'s lock. A dimension thread preempted between "the log
    accepted my event" and "mirror it" let a concurrent stop close the span
    in the gap, and `recorder.add_event` does not check whether a span is
    open — so the event landed stamped past its own `ended_at`. Harmless
    before this chunk (a stop closed nothing); incoherent after it. QC needs
    no equivalent and deliberately does not have one: its span closes in
    `_finalize_attempt`, which the worker reaches only after its own pools
    have joined.
  - The deterministic seam is `tests/test_research_rounds.py::_ReleaseHook`,
    a lock wrapper with `while_locked` / `on_release` hooks. No sleeps, no
    timing assertions. `on_release` fires after the release rather than
    during it, which is what lets the ordering test drive a stop at the
    exact seam under both the old and fixed arrangements instead of
    deadlocking against the fix.
- Manual QA owed: none specific to this chunk beyond the Phase 6.5 gate
  items already listed (research transport recovery, QC live state and
  container). Worth watching in the 6.5 live runs: a stopped research or QC
  run should now appear as a CLOSED span in the trace viewer.

## Chunk 6.2 — Owned tutorial transition tokens

### Design

Replace the shared `_transitioning` boolean with an owner token. Only the
operation that installed a token may clear it. A truthy property/helper can
preserve existing busy checks, but raw unconditional `False` assignments must
disappear.

### Implementation

1. Add helpers under `SessionManager._lock`, for example:
   - `_begin_transition_locked() -> object`;
   - `_finish_transition_locked(owner)`; and
   - `_transition_active_locked()`.
2. Update `begin_tutorial` and `push_scenario` to capture an owner before their
   unlocked clone/build and clear only that owner in success, conflict, and
   exception paths.
3. Make `finish_tutorial` refuse with `WorkspaceBusyError` while a transition
   token exists. This prevents it from discarding a paid scenario build before
   that build can merge usage normally.
4. Make `force_restore_original` refuse/veto while a transition token exists,
   rather than clearing someone else's reservation. Update native-close/reset
   callers to surface the busy result safely; do not block a UI thread waiting
   an unbounded model call.
5. Do not add a special `pop_scenario` mid-build guard: its scope precondition
   already makes that path unreachable, as the source report's verifier noted.
6. Preserve `_active_writes` and `_busy_reasons` checks. The refuted paid-run
   start claim needs no additional route guard.
7. Add barrier-controlled tests:
   - a paid scenario build held outside the lock makes finish/force restore
     refuse;
   - after the owner completes/fails, the guard clears;
   - an old losing build cannot clear a newer transition's token; and
   - usage from the completed scenario is merged once.

### Files

- `backend/sessions.py`
- app/native-close call sites that handle restore outcomes
- `tests/test_tutorial.py`
- `tests/test_close_prompt.py`
- `CLAUDE.md`

### Focused verification

```powershell
venv\Scripts\python -m pytest -q tests/test_tutorial.py tests/test_close_prompt.py
```

### Acceptance criteria

- Only a transition owner can clear its reservation.
- Finish/new-session/native-close cannot orphan an in-flight scenario build.
- Existing active-write and paid-run guards remain the only run-start policy.

### Implementation record

- Status: complete
- Commit/PR: branch `claude/phase-6-concurrency-responsiveness-xncqik`
- Tests: 6 new. `tests/test_tutorial.py` (5): finish / forced restore /
  tutorial repair all refuse while a build holds the slot, and the guard
  lifts when it completes; a failed build releases its own slot; an
  abandoned build cannot release a newer transition's reservation; a
  refused finish lets the build's spend reach the original exactly once;
  a hard reset cannot be overwritten by the tutorial setup it abandoned.
  `tests/test_close_prompt.py` (1): native close vetoes a scenario build
  instead of waiting on it. Four go red against the old code; the other
  two pin behavior that already held and must keep holding under
  ownership (regression guards, stated as such rather than claimed as
  fixes). Full suite 1369 passed / 9 skipped (no new skips); `npm test`
  193; `npm run build` clean.
- Deviations:
  - **`replace_tutorial` got the same guard**, which the plan did not
    enumerate. It is the same class of bug: the enrichment repair swaps
    the very tutorial session an in-flight scenario build is holding and
    will merge its usage onto, so leaving it out would ship the chunk with
    a hole in its own stated invariant.
  - **`begin_tutorial` gained a transition check it never had.** Its scope
    guard cannot stand in, because activation happens at the end of the
    method — during a build the scope is still `original`, so two
    overlapping starts both cloned and the loser only found out at the
    commit re-check.
  - **Callers check `_transition_active_locked()` before reserving**, and
    `_begin_transition_locked()` also raises. The duplication is
    deliberate: the explicit check preserves the existing precedence of
    "another tutorial transition" among the other busy reasons, and the
    helper's raise is the backstop that keeps the slot un-double-bookable.
  - **`force_restore_original` keeps an `abandon_transition=True` escape**
    used only by `reset_session()`, the hard-reset primitive the autouse
    test fixture calls around every test. Refusing unconditionally there
    would let one leaked reservation cascade into every later test.
    Ownership is what makes abandoning safe — the abandoned build then
    owns nothing and clears nothing.
  - **Native-close callers needed no change.**
    `restore_original_for_native_close` already refused on the flag and
    `main._CloseController` already turns that into the `tutorial-busy`
    prompt, so the "surface the busy result safely / do not block a UI
    thread" requirement was already met; it is now covered by a test.
  - **Review finding (Codex, PR #108), fixed in the same branch:**
    `abandon_transition` was ignored whenever the scope was still
    `original` — which is exactly the in-flight `begin_tutorial` case,
    since activation happens at the end of the method — and even once
    revoked, the builders' commit re-check compared only workspace id and
    session identity, so the abandoned setup still committed a tutorial
    over the just-reset session. Both halves fixed and independently
    proven load-bearing: abandonment is handled before the scope early
    return, and both builders check `self._transition_owner is not owner`
    first. Ownership is the authority — losing it is losing the right to
    commit, the same shape as the run token in Chunk 6.1.
  - `pop_scenario` left unguarded, as the plan directs.
- Manual QA owed: none specific to this chunk. Worth exercising in the
  6.5 pass: enter Chapter 6 (live figure generation) and try to end the
  tour while it is preparing — expect the busy refusal, then a normal
  finish once it lands, with the attempt's spend visible in the meter.

## Chunk 6.3 — Event-loop offload and coherent short endpoint snapshots

### Template import

1. In `backend/app.py::template_import`, retain the awaited bounded upload read,
   then call `get_template_catalog().import_bytes(data)` through
   `run_in_threadpool`.
2. Preserve all `TemplateError`, status-code, trace-event, size-limit, atomic
   write, and catalog-lock behavior.
3. Add a concurrency test modeled on the existing import/reference/project
   responsiveness tests: block `import_bytes` in the worker and prove a health
   request/SSE poll completes on the event loop.

### Document diff

1. Resolve the session, version count, current index, baseline index, and the
   two version dictionaries inside `session.session_state_guard()`.
2. Copy/detach the two dictionaries under the guard, release it, then construct
   `SpecSection` objects and run `diff_sections` outside the lock.
3. Preserve the existing 400 errors for out-of-range/equal indexes. A concurrent
   redo-tail truncation can no longer raise `IndexError`.

### `/api/doc` and QC apply response

1. Wrap `_doc_payload` in `/api/doc` with `session_state_guard` so its document,
   lint, open questions, figures, suggestions, and version pair come from one
   coherent state.
2. In both final QC-apply branches, build the successful `_doc_payload` inside
   the same final guard that commits/records outcomes, save it to a local, and
   return that frozen payload after release.
3. Do not extend the guard over trace logging or JSON serialization if a plain
   detached dict has already been captured.
4. Add concurrency tests that inject a new turn/mutation immediately after the
   final QC-apply guard and prove the response still describes the applied
   version, plus a cross-field `/api/doc` snapshot test.

### Files

- `backend/app.py`
- `tests/test_import_responsiveness.py`
- `tests/test_templates.py`
- `tests/test_diffing.py`
- `tests/test_qc_audit_report.py`
- `tests/test_qc_apply_history.py`
- `tests/test_app.py`
- `CLAUDE.md`

### Focused verification

```powershell
venv\Scripts\python -m pytest -q tests/test_import_responsiveness.py tests/test_templates.py tests/test_diffing.py tests/test_qc_audit_report.py tests/test_qc_apply_history.py tests/test_app.py
```

### Acceptance criteria

- A maximum-size template import cannot stall unrelated async endpoints.
- Diff either returns a coherent comparison or the existing 400, never a TOCTOU
  500.
- QC apply's response describes exactly the version it committed.
- `/api/doc` fields come from one guarded state.

### Implementation record

- Status: complete
- Commit/PR: branch `claude/phase-6-concurrency-responsiveness-xncqik`
- Tests: 5 new, each reverted in place — against `HEAD~1`, not a stash, since
  a committed chunk is not reverted by stashing — to confirm it goes red.
  `tests/test_import_responsiveness.py`: a blocked template import does not
  stall `/api/health` (the file's established blocked-worker pattern).
  `tests/test_redline_export.py`: a concurrent redo-tail truncation cannot
  turn a diff into a 500 — it reproduces the real `IndexError` on the
  unguarded route. `tests/test_qc_apply_history.py`: apply replies with the
  version it committed, not a later one. `tests/test_app.py` (2): `/api/doc`
  reads one state, not a mixture of two, and the workspace is never looked
  up while the session guard is held. Full suite 1374 passed / 9 skipped
  (no new skips); `npm test` 193; `npm run build` clean.
- Deviations:
  - **The diff binds version REFERENCES under the guard rather than
    deepcopying.** A version record is immutable history — QC apply's
    staleness check turns on that identity — so the reference is as safe as
    a copy against the failure the plan names (the LIST being truncated),
    and a deepcopy of two whole documents per compare-view poll is real
    cost for none.
  - **The diff TOCTOU test lives in `tests/test_redline_export.py`**, not
    `tests/test_diffing.py` as listed: `test_diffing.py` is pure-unit with
    no TestClient, while the existing `/api/doc/diff` route tests are
    already in the redline file. Keeping route tests together beat adding
    HTTP infrastructure to a unit module.
  - **Review finding (Codex, PR #109), fixed in the same branch:**
    guarding `/api/doc` introduced an AB/BA deadlock — `_doc_payload` ends
    with `sessions.get_workspace()` (manager lock), while a tutorial
    transition holds the manager lock and calls `invalidate_model_turn()`
    (session lock). `_doc_payload` now takes an optional `workspace`, and
    `/api/doc` and QC apply capture the lease before the guard. Undo/redo/
    edit were already safe and stay unchanged: they hold an `active_write`
    lease, which is mutually exclusive with every transition under the
    manager lock. A second finding asked for a persistent TestClient portal
    in the diff race test; the stronger answer was to remove the
    concurrency entirely and drive the truncation from inside the seam on
    the request's own thread, so the test no longer depends on client
    threading semantics at all.
  - `tests/test_diagnostics.py`'s catch-all test used `/api/doc` as its
    victim and patched `get_session`, which that route no longer calls. It
    now uses `/api/figures` — deliberately trivial, so the test stays about
    the handler rather than about how a busier route acquires its state.
  - No `tests/test_templates.py` change was needed: the import route had no
    existing route-level test, and the new one belongs with the other
    event-loop guarantees.
- Manual QA owed: none specific to this chunk. Covered incidentally by the
  6.5 responsiveness item (import a large template while chat streams).

## Chunk 6.4 — Snapshot heavy DOCX/request work outside locks

### Part A: DOCX export

Refactor export into capture and render phases. The export is a coherent
snapshot taken at click time; it does not need to block later edits while ZIP,
XML, source validation, or `python-docx` work runs.

1. Introduce a private detached export-input structure in `backend/app.py` (or
   a nearby export module). Under `session.session_state_guard`, validate mode
   and capture everything rendering needs:
   - a detached current `SpecSection`;
   - only the version dictionaries needed for a selected redline;
   - baseline section/index;
   - immutable source bytes and a detached source map;
   - a safe source patch context snapshot when already cached;
   - detached audit result and one coherent QC audit record;
   - captured readiness/export-current-state facts; and
   - the filename/identity belonging to that document snapshot.
2. Release the guard before `diff_sections`, source plan validation,
   `build_source_preserving_docx`, `build_docx`, and raw-ZIP reconstruction.
3. If source patch context is absent, expose/refactor a pure builder that can
   create it from captured source inputs outside the session lock. Do not write
   a newly built cache back unless generation/source identity is revalidated
   under the guard; caching is optional for export correctness.
4. Preserve fail-closed source-mode behavior and every existing HTTP status and
   warning. Never silently fall back to normalized export.
5. Cover both source and normalized branches. The report finding applies to the
   normalized `python-docx` render too.
6. Add a barrier inside the heavy renderer and prove a concurrent chat claim or
   stop request is not blocked. Also mutate the live document after capture and
   prove the returned filename/content/QC closing all belong to the captured
   snapshot, not a mixed state.

### Part B: Chat resend sanitization

1. Inside `owned_model_turn_guard`, verify ownership and snapshot only the
   minimum mutable inputs: `list(session.history)`, current new-message copies,
   and stable module/config references needed to build the request.
2. Release the guard before `sanitize_messages_for_resend`, PDF base64 decode,
   `PdfReader` page counting, cache-breakpoint copies, and final kwargs
   assembly.
3. Re-enter/check ownership and generation immediately before opening the
   provider stream. If reset/load won during build, discard the request. If the
   user stop flag was set during build, take the safe between-round stop path
   and do not start another paid request.
4. Keep the Phase 1 container key and Phase 4 cache-boundary/TTL logic in the
   detached request builder.
5. Add a deterministic test that blocks the sanitizer while another thread
   calls the stop endpoint; the stop must complete before the sanitizer is
   released. Add a reset-during-build test proving no request is sent.

### Files

- `backend/app.py`
- `backend/llm/conversation.py`
- `backend/research/resend_sanitizer.py` only if a pure seam is needed
- source-export helpers as required by the existing architecture
- `tests/test_source_preserving_export.py`
- `tests/test_redline_export.py`
- `tests/test_docx_corpus.py` or focused source fidelity tests
- `tests/test_import_responsiveness.py`
- `tests/test_stop.py`
- `tests/test_app.py`
- `CLAUDE.md`

### Focused verification

```powershell
venv\Scripts\python -m pytest -q tests/test_source_preserving_export.py tests/test_redline_export.py tests/test_import_responsiveness.py tests/test_stop.py tests/test_app.py
```

Also run the relevant DOCX fidelity/corpus tests selected by the current
`docs/DOCX_FIDELITY.md` contract. Do not regenerate approved fixture artifacts
unless output semantics intentionally changed and the owner approves.

### Acceptance criteria

- No DOCX ZIP/render build runs while `_turn_state_lock` is held.
- Export bytes, filename, readiness, and QC closing all describe one captured
  snapshot even if the live session changes during rendering.
- PDF resend sanitization does not block chat stop or session-state endpoints.
- A reset/stop during detached request construction prevents an obsolete paid
  request.

### Implementation record

- Status: **complete** (Part A and Part B both landed)
- Commit/PR: branch `claude/phase-6-concurrency-responsiveness-xncqik`
  (Part A, PR #110); branch `claude/phase-6-4b-chat-resend-ogk9an`
  (Part B). Split deliberately: Part A touches the source-preserving export
  gate — per CLAUDE.md the most safety-critical subsystem in the repo, with
  its own contract in `docs/DOCX_FIDELITY.md` — so it gets an undiluted
  review and its own revert boundary. Part B (chat resend sanitization) is
  a separate change against the chat request builder.
- Tests (Part A): 3 new, each reverted in place to confirm it goes red.
  `tests/test_import_responsiveness.py` (2): a blocked `build_docx` and a
  blocked `build_source_preserving_docx` each leave `/api/doc` — which takes
  exactly the turn-state lock a chat claim needs — answering promptly, so
  both branches are covered. `tests/test_redline_export.py` (1): an export
  renders the snapshot it captured, not a later document. Full suite 1377
  passed / 9 skipped (no new skips); `tests/test_docx_corpus.py` +
  `tests/test_source_preserving_export.py` green with no fixture
  regeneration; `npm test` 193; `npm run build` clean.
- Deviations (Part A):
  - **The capture keeps detached `SpecSection` objects, not version
    dictionaries**, for the current tree and the redline base. Building
    them under the guard is far cheaper than the render it replaces, and it
    preserves `_source_baseline`'s `from_dict` try/except — which is what
    turns a malformed persisted baseline into the existing 409 rather than
    a 500 escaping from the unguarded renderer.
  - **No pure context builder was extracted.**
    `build_source_preserving_docx` already accepts `context=None` and
    builds one internally, so the cold path builds outside the lock with no
    new seam. Nothing is written back to the cache, as the plan permits.
  - **Fixed while here:** the filename was computed from a SECOND live read
    of `store.doc` after the render. Under the old always-locked route that
    was invisible; releasing the guard would have made it a mixed-state
    reply, so it is now read from the captured snapshot.
- Manual QA owed (Part A): the 6.5 responsiveness item covers it — export a
  large source DOCX while chat streams and confirm both the stream and the
  export behave, and that the file opens in Microsoft Word.
- Tests (Part B): 3 new. Reverted in place three ways — the whole change
  (3 red), then each mechanism on its own, to prove each test pins the
  mechanism it claims to.
  `tests/test_import_responsiveness.py` (1): a blocked
  `sanitize_messages_for_resend` leaves `POST /api/chat/stop` answering
  promptly. Building back under the guard turns only this one red, at
  5.00s — the exact diagnostic it was written for. It asserts an ordering
  (the stop returned while the build was still blocked) on top of the
  file's established timing bound, and the block is bounded so a
  regression fails rather than hangs.
  `tests/test_stop.py` (1): a stop landing during request construction
  never sends the request, still commits a normal turn, keeps history
  alternating, and leaves the session able to take another turn. Removing
  the stop-before-send check turns only this one red.
  `tests/test_chunk8_stress_concurrency.py` (1): a reset during request
  construction discards the turn and sends nothing. Removing the post-build
  ownership re-check turns only this one red.
  Full suite 1380 passed / 9 skipped (no new skips); `npm test` 193;
  `npm run build` clean.
- Deviations (Part B):
  - **The capture is a turn-local closure; the BUILDER is module level.**
    `_ChatRequestInputs` + `_build_chat_request` sit beside
    `_with_cache_breakpoints` rather than inside `stream_user_turn`, which
    is what makes the detachment checkable — a pure function that takes no
    session cannot read one by accident. The capture stays a closure
    because it legitimately needs `session` and the turn's `new_messages`.
  - **`_stable_system_blocks` now takes the module, not the session.** It
    renders into the cached system block, so it must not see session state;
    taking a frozen `SpecModule` makes that mechanical instead of
    documented, and lets the render move out of the lock with it. Two call
    sites, both updated; output is byte-identical.
  - **Shallow list copies, not deep copies** — the same argument Part A
    made for version records. History and `new_messages` are only appended
    to, truncated, or replaced wholesale, and the sanitizer rebuilds rather
    than mutates, so a per-round deep copy of the conversation would be
    real cost for no additional guarantee.
  - **Ownership and the stop flag are re-read in ONE critical section**
    rather than the plan's two steps. "Is this turn still ours, and is it
    still wanted" is one decision, and splitting it would let the answers
    come from different moments.
  - **The between-round stop path was extracted to
    `close_for_between_round_stop()` rather than reached with `continue`.**
    The round loop is a `for ... else` whose `else` raises on tool-round
    exhaustion, so a `continue` from the final iteration would convert a
    user stop into a failed, rolled-back turn — exactly what Batch 7's stop
    semantics exist to prevent. One shared implementation, two call sites.
  - **The reset-during-build test lives in
    `tests/test_chunk8_stress_concurrency.py`**, not `tests/test_app.py` as
    listed: its exact sibling
    (`test_reset_during_turn_context_capture_discards_startup_atomically`)
    is already there, and that is where a reader looking for "reset during
    a turn" will look.
  - **No `backend/research/resend_sanitizer.py` change was needed.** The
    plan allowed one "only if a pure seam is needed"; the function was
    already pure, so the seam was on the caller's side.
  - The residual window between the re-check and `_enter_stream` is not
    closed and is not pretended away: a stop landing there is still caught
    by the existing after-every-event check. What changed is that the
    window is microseconds of dict assembly instead of seconds of PDF
    parsing.
- Manual QA owed (Part B): covered by the 6.5 responsiveness item and the
  chat-stop recovery item. Worth doing deliberately in that pass: have the
  model `web_fetch` a large PDF, then press Stop on the following round —
  the button should respond immediately, and the turn should commit what
  had already arrived.

## Chunk 6.5 — Full release validation and documentation closeout

This chunk makes no speculative feature changes. It proves and records the
program's result.

### Automated gate

1. Run the complete suites from a clean worktree:

```powershell
venv\Scripts\python -m pytest -q
Push-Location frontend
npm test
npm run build
Pop-Location
```

2. Run packaging/version consistency tests included in pytest. If the owner has
   selected a release version, update both `backend/settings.py::VERSION` and
   `frontend/package.json`; otherwise leave product versions unchanged.
3. Search for stale patterns and review every intentional remaining match:

```powershell
rg -n "pause_turn|messages\.stream|server_tool_use|allowed_callers|cache_control|settling|\(size // 2\) \+ 1|_panel_outcome|_transitioning|session_state_guard" backend frontend/src tests
```

4. Confirm:
   - both web tools declare `allowed_callers: ["direct"]` in every consumer,
     and no ZDR claim outlives a configuration that breaks it;
   - every provider continuation site carries a turn/attempt-local container
     (defense-in-depth even in direct mode);
   - no stop/truncation path filters only `tool_use` while retaining dangling
     server tools, and the load/resend repair boundaries are active;
   - interview cache TTLs are non-increasing across the request — the
     system and committed-history breakpoints carry the configured TTL
     (default one hour) and the tail is pinned to the shortest supported one,
     so no setting can build an out-of-order request (frozen decision 7 as
     amended during 4.2; the original "all breakpoints share one TTL" wording
     is superseded and must not be re-imposed);
   - old four-rate and new five-rate audit bases have tests, and
     provider-reported usage is never blended with estimates;
   - v3/v4 outcome handling (including disputed and the evidence rule) is
     explicit;
   - issue readiness, masthead, and sign-off derive from one helper;
   - no normal terminal event was redundantly mirrored solely to satisfy the
     refuted trace claim; and
   - no change weakened `finding_id` or duplicated paid-run start guards.
5. Review new concurrency tests for barrier determinism and remove any
   timing-only assertions.

### Documentation gate

1. Update `CLAUDE.md` sections for:
   - provider continuation/container and dangling-server-tool invariants;
   - live event payload extraction and follower reconnect;
   - research required-dimension/readiness policy;
   - one-hour cache layout and per-TTL pricing;
   - Final QC v4 threshold policy;
   - runner/workspace transition ownership; and
   - snapshot-before-heavy-work rules.
2. Update `README.md` user-facing descriptions of research readiness, QC
   limitations, estimated usage/cost, and any release version.
3. Update release notes if a release is being cut. Do not claim a live provider
   validation that was not actually run.
4. Mark every chunk complete in the master implementation record and add its
   commit/PR, tests, deviations, and still-owed manual QA.

### Owner-authorized live/manual gate

These tests can incur substantial spend. Obtain explicit approval before
starting them and capture diagnostics for comparison.

The provider cannot be forced to return `pause_turn` on demand, so the
pause-continuation contract is proven by the hermetic fakes (forced pause,
continuation-shape assertions, invalid-continuation rejection). The live
canary asserts what is deterministic — direct-mode completion, streamed
query/URL deltas, no container error — and verifies a natural pause's
continuation opportunistically when one occurs. This class of failure came
from provider-side behavior shifting under a pinned tool version; rerun the
research canary after any future change to the web-tool definitions.

1. **Research continuation and activity (direct-mode canary)**
   - Run all dimensions on a realistic project.
   - Confirm no container-id 400, all expected dimensions complete or fail for
     a substantive reason, and query/URL labels plus trace counts populate.
   - If a natural `pause_turn` occurs, confirm its continuation succeeds.
2. **Chat stop recovery**
   - Trigger web search, stop while searching, send a follow-up, save the
     project, reopen it, and send another follow-up.
   - Confirm no poisoned-history 400 and the stopped output usage is labeled
     estimated.
3. **Research transport recovery**
   - Interrupt only the SSE transport while the server run continues.
   - Confirm automatic reconnect, no board regression, and a correct terminal
     profile.
4. **QC live state and container**
   - Run normal Final QC and confirm no stop banner.
   - In a separate authorized run, stop mid-flight and confirm genuine settling
     until the partial report attaches.
5. **Truthful partial research report**
   - Use a controlled partial fixture/session with a required dimension's
     coverage missing.
   - Confirm model context, readiness, modal Limitations, JSON manifest, Word
     identity/Limitations/readiness all agree.
6. **Cache economics**
   - Send at least three realistic turns, including a gap longer than five
     minutes.
   - Confirm diagnostics show a growing cache read at the committed-history
     boundary and only incremental creation; verify the one-hour subtotal and
     list-price estimate against provider records.
7. **QC v4**
   - Confirm current reports say `final-qc/4`, outcomes follow the v4 table
     (including a disputed candidate blocking readiness until dispositioned),
     the masthead and sign-off agree, and a saved v3 report is
     historical/readable but requests a rerun for current readiness.
8. **Responsiveness**
   - While chat streams, import a large template, export a large source DOCX,
     and request stop in separate trials.
   - Confirm SSE/health remain responsive and each export opens correctly in
     Microsoft Word.
9. **Report visual QA**
   - Inspect a large Word report for version labels, partial-research
     limitations, per-TTL cost basis, request-count composition, page breaks,
     and readable tables.

### Final acceptance criteria

- All automated gates are green with no new skips/xfails.
- Every R01-R26 and R28-R35 row in the master coverage matrix has a landed
  change and test.
- R27's refuted/non-goal behavior remains unchanged.
- Manual tests are either checked off with diagnostic evidence or explicitly
  listed as owner-owed; none are silently assumed.
- The handoff summary reports changes, deviations, remaining risks, and the
  selected release/version state.

### Implementation record

- Status: **complete** (automated + documentation gates; the live/manual gate
  is owner-owed by explicit instruction, not skipped silently)
- Commit/PR: branch `claude/phase-6-5-release-closeout`
- Full pytest result: **1380 passed, 9 skipped** — the same 9 skips the
  program started with, no new skips, no xfails, no network.
- Frontend test result: `npm test` **193 passed**
- Frontend build result: `npm run build` clean (tsc + vite)
- Version consistency gate: `check_release_version.py` → ok, 1.8.0 (also run
  inside pytest as `test_version_consistency_gate`)

### Stale-pattern sweep — every match reviewed

| Pattern | Matches | Verdict |
|---|---:|---|
| `_transitioning` | 0 | 6.2 removed the shared boolean outright |
| `(size // 2) + 1` | 2 | both comments explaining the RETIRED v3 rule, in `qc/engine.py` and `test_qc.py` |
| `_panel_outcome` | 0 | the helper is public `panel_outcome` |
| `allowed_callers` | 12 | one declaration (`WEB_TOOL_ALLOWED_CALLERS`), two builders, the rest documentation |
| `pause_turn` / `messages.stream` / `server_tool_use` / `cache_control` / `session_state_guard` / `settling` | 72 / 7 / 141 / 58 / 47 / 102 | all live usage; spot-checked against the confirmations below |

### The ten confirmations

1. **Direct callers.** Five consumers — chat `_chat_tools`, research
   `_run_dimension`, and two QC sites — all go through
   `build_web_search_tool`/`build_web_fetch_tool`; **zero hand-rolled web
   tool dicts anywhere in `backend/`**, so nothing can silently take the
   provider default. `WEB_TOOL_ALLOWED_CALLERS = ("direct",)`, spread as a
   fresh list per request. ZDR claims in `TrustDeepDiveModal` and CLAUDE.md
   are coupled to both halves (model + caller mode).
2. **Containers.** All three continuation sites refresh
   `container_id = response_container_id(...) or container_id` and add a
   top-level `container` key only when nonblank: research and QC keep it
   attempt-local (reset inside the retry loop, outside the continuation
   loop), chat keeps it turn-local and passes it as a parameter.
3. **Dangling server tools.** Five boundaries active — mid-stream
   truncation, between-round stop, `_committed_messages`,
   `sanitize_messages_for_resend` (outgoing, `protect_trailing_assistant`),
   and `project.py` load repair. The single `tool_use`-only content filter
   is immediately followed by the turn-wide pairing scrub, so no path
   filters one kind and retains the other.
4. **Cache TTLs.** Non-increasing, not uniform — see the amendment recorded
   against this checklist item. `SUPPORTED_CACHE_TTLS` is shortest-first
   and that order is load-bearing; `CHAT_TAIL_CACHE_TTL` is pinned to
   `[0]` and deliberately not env-overridable;
   `test_no_setting_can_build_an_out_of_order_request` sweeps every
   supported setting.
5. **Cost bases and estimates.** `_COST_BASIS_SHAPES` validates the outer
   field set and the rate map **as a pair**, so neither four-rate nor
   five-rate hybrids validate; both shapes have tests and a legacy basis
   reproduces its saved estimate exactly. `estimated_output_tokens` /
   `usage_estimated` are disjoint from `output_tokens`, which always holds
   the provider's figure.
6. **v3/v4 outcomes.** Schema 4 / `final-qc/4`; `panel_outcome` is the one
   decision point; `VERIFICATION_RULE_V4` is persisted per finding;
   `DISPUTE_REASON_INSUFFICIENT_EVIDENCE` implements the severity gate; and
   `_structural_verification_outcome` branches on schema version so a v3
   report is re-checked under v3's rule and never re-adjudicated.
7. **One sign-off helper.** `qc_signoff_state` + the shared
   `QC_SIGNOFF_CLEAR` constant drive the masthead, the sign-off, and the
   executive layer; `issue_ready` is equality against that one constant.
   The frontend deliberately does not re-derive the recommendation.
8. **No redundant terminal mirroring.** Neither runner calls `add_event`
   with a terminal frame; normal outcomes remain span closes, per frozen
   decision 9. The only trace change was closing STOPPED spans.
9. **`finding_id` not weakened, guards not duplicated.** The hash still
   covers lens, element, title, issue, rationale, severity, source URLs,
   proposed ops, reviewed text, final severity, verification outcome, the
   full panel projection, grounding decisions and accepted sources — and
   5.2 **added** `origin_ids`. `_transition_active_locked` remains the one
   paid-run start guard, checked at 8 sites.
10. **Concurrency test determinism reviewed; nothing removed.** Ordering is
    carried by `threading.Event` barriers and by seams driven on the
    request's own thread (6.3's diff race, 6.4's export and request-build
    races) — never by sleeps. The three `time.sleep` calls in the suite are
    all inside deadline-bounded *condition* polls waiting for a daemon
    runner to go terminal, and the assertion is on the condition, not on
    elapsed time. The `elapsed < _RESPONSIVE_SECONDS` assertions in
    `test_import_responsiveness.py` are retained deliberately: for a
    responsiveness test the elapsed time IS the property under test, and
    removing it removes the test. Each is a bounded 5s block against a 1.5s
    threshold (3.3x margin) and 6.4B additionally asserts the structural
    ordering.

### Coverage matrix

Every R01-R26 and R28-R35 row maps to a chunk that is now complete, with
tests recorded in that chunk's own implementation record. R27 (the refuted
scenario busy-guard claim, broad terminal-event duplication, and
`finding_id` simplification) is unchanged, as intended.

### Documentation gate

1. CLAUDE.md carries a section for all seven required topics — provider
   caller mode/containers and the dangling-server-tool invariants, live
   event extraction plus follower reconnect and the replay index, required
   research coverage, per-TTL pricing and the rolling breakpoint, v4 panel
   outcomes, runner and transition ownership, and the two
   snapshot-before-heavy-work sections.
2. `README.md`: the three topics the gate enumerates — research readiness,
   QC limitations (three-state research coverage), and estimated usage/cost
   (per-TTL cache writes, failed/stopped work metered, the separate
   stopped-reply estimate) — were each already current, kept so by the chunk
   that changed them. **One claim outside that list was stale and is fixed**:
   the Final QC report-identity bullet still advertised schema `3` /
   `final-qc/3` and described only schema-2 as non-actionable. Chunk 5.1
   shipped schema 4, and the actionability guard is
   `schema_version >= QC_REPORT_SCHEMA_VERSION`, so v3 is non-actionable
   too. Found while checking an unrelated review finding, which is the
   honest provenance: enumerating the gate's three topics is not the same
   as reading the document, and a version constant is exactly the kind of
   claim that goes stale silently.
3. Release notes: the 1.8.0 entry was extended — see the deviation below.
4. The master implementation record now carries every chunk's commit and PR.

- Manual QA completed: **none.** The owner elected to defer the entire
  live/manual gate until implementation was finished. Nothing in this record
  claims a live provider validation.
- Manual QA owed: the full nine-item owner-authorized list above, unchanged,
  plus the per-chunk items each phase file records. Worth running first,
  because they are the ones with a behavior change behind them: (5) partial
  research agreeing across model context, readiness, modal, JSON and Word;
  (7) v4 outcomes including a disputed candidate blocking readiness; (6)
  cache economics across a >5-minute gap; (1) the direct-mode research
  canary. **Rerun the research canary after any future change to the
  web-tool definitions.**
- Deviations:
  - **Product version deliberately NOT bumped.** The plan says to bump only
    if the owner has selected a release version; none was selected, so
    `backend/settings.py::VERSION` and `frontend/package.json` stay at
    1.8.0 and no release was cut.
  - **The release-note obligation was still discharged, without a bump.**
    Chunk 5.1 parked the v3→v4 `finding_id`/dismiss-memory break on the
    grounds that "a notes entry for a version that does not exist would
    fail the version-consistency gate". That constraint turned out not to
    bind: **1.8.0 already exists as the shipped version and already has a
    notes entry, and it was never released** (GitHub Releases holds only
    v1.0.0 and v1.7.0). Everything from `e62dd61` onward — 37 merges,
    including this entire program — is unreleased work that 1.8.0 will
    carry. Its entry therefore had to describe that work or a user
    upgrading from 1.7.0 would get notes covering a fraction of what
    changed. The entry gained "Findings you dismissed will come back once"
    plus three sections (Research, What things cost, The window stops
    freezing) and a rewritten headline/summary. If the release is
    renumbered later, the whole entry moves as a unit.
  - **A stale claim was found and fixed in those unreleased notes.** The
    1.8.0 item "Nothing about the review got smaller" asserted "a tie still
    going to the refuters" — the v3 rule that Chunk 5.1 replaced. Under v4
    a tie is `disputed`. Shipping it would have told users the opposite of
    what the app does.
  - **Two plan documents still carried the pre-amendment TTL wording** and
    were reconciled: this chunk's own confirmation item 4 and the matrix row
    R11 both said "uniform", which frozen decision 7 was explicitly amended
    away from during 4.2 (PR #100). Taken literally, the checklist item
    would have failed against correct code. The amended criterion — non-
    increasing, tail pinned shortest, no setting able to build an
    out-of-order request — is strictly stronger.
  - **No timing-only assertions were removed**, contrary to the literal
    instruction in step 5 of the automated gate; see confirmation 10 for the
    reasoning. Removing them would delete the only assertion that tests
    responsiveness at all.
