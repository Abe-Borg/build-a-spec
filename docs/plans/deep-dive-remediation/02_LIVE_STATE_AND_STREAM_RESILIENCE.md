# Phase 2 — Live state and stream resilience

- Status: in progress (2.1, 2.2 and 2.3 complete; 2.4 planned)
- Prerequisite: Phase 1 complete
- Risk: high user-visible impact; backend run results must remain unchanged

## Goal

Make live research, chat web activity, and the Final QC Review Room tell the
truth and recover from ordinary transport behavior. This phase fixes missing
query/URL labels, the always-settling QC state, the non-reconnecting research
follower, stale research snapshot regression, and quadratic event replay.

No chunk in this phase may change research findings, QC adjudication, request
budgets, or billed provider usage.

## Chunk 2.1 — Real-shaped server-tool activity events

### Root cause

The current relays reconstruct tool input only from `input_json_delta`. With
the code-execution caller previously used by the `_20260209` tools, the full
input can be present on `content_block_start.content_block.input` and no JSON
deltas follow. Research and QC then suppress empty payloads; chat emits a
blank chip.

After Chunk 1.1 switches the tools to `allowed_callers: ["direct"]`, deltas
are again the documented streaming shape, so this chunk is defense-in-depth:
it keeps the labels truthful for any future code-execution-called tool, any
provider-side shape drift, and any re-enable of dynamic filtering. Implement
it fully; do not skip it because direct mode restored the deltas.

### Implementation

1. Extend the raw fake-event helpers in `tests/fakes.py`:
   - allow `block_start_event` to carry an optional `input` mapping;
   - retain the existing delta-producing shape for direct caller tests; and
   - add an explicit code-execution-shaped event option: populated start input,
     zero `input_json_delta` frames, then `content_block_stop`.
2. In all three relays, keep `start_inputs: dict[int, dict]` alongside the JSON
   buffers and block-kind map:
   - `backend/research/engine.py::_relay_stream_activity`;
   - `backend/qc/engine.py::_relay_stream_activity`; and
   - `backend/llm/conversation.py::_stream_events`.
3. At a server-tool start, copy a nonempty mapping from `block.input`. Do not
   retain an SDK object by reference.
4. At block stop, parse the delta buffer first and use the copied start input
   only when the parsed mapping is empty. Delta data wins when both exist.
5. Pop the index from all tracking dictionaries at stop so a long stream does
   not retain completed input payloads.
6. Preserve each relay's existing absence behavior:
   - research/QC may skip a genuinely missing query/URL;
   - chat may still emit its current placeholder event if no payload exists;
   - malformed frames remain nonfatal.
7. Add tests for web search and fetch in both caller shapes, plus malformed and
   missing input. Assert exact event types and payload text.
8. Update the live-research and Review Room event-contract notes in
   `CLAUDE.md`.

### Files

- `tests/fakes.py`
- `backend/research/engine.py`
- `backend/qc/engine.py`
- `backend/llm/conversation.py`
- `tests/test_research_engine.py`
- `tests/test_qc_live_events.py`
- `tests/test_streaming.py`
- `CLAUDE.md`

### Focused verification

```powershell
venv\Scripts\python -m pytest -q tests/test_research_engine.py tests/test_qc_live_events.py tests/test_streaming.py
```

### Acceptance criteria

- A start-input-only web search emits its real query in all three channels.
- A start-input-only web fetch emits its real URL in all three channels.
- Existing delta-driven streams behave identically.
- No malformed frame can fail a chat turn, dimension, lens, or verifier.

### Implementation record

- Status: **complete** (2026-07-29)
- Commit/PR: `0cfad61` — PR #93
- Tests: seven new, four of which fail against the pre-fix code (the other
  three are precedence/absence guards that pin the new reader against a
  future inversion — proven load-bearing by deleting the `isinstance`
  guard and watching them go red).
  `tests/test_research_engine.py` gains
  `test_start_input_only_frames_still_emit_the_real_query_and_url` (exact
  `dimension_search`/`dimension_fetch` dicts from a start-input-only
  stream, plus the activity spine and the untouched findings),
  `test_streamed_deltas_win_and_an_absent_input_says_nothing`
  (precedence, a block with neither shape, and a non-mapping start input
  that must still announce its activity — i.e. rejected by the reader,
  not swallowed by the frame's `try/except`), and
  `test_a_finished_block_is_forgotten_at_stop` (a repeat
  `content_block_stop` has nothing left to replay — the observable proof
  that every tracking dict pops).
  `tests/test_qc_live_events.py` gains
  `test_start_input_only_frames_emit_real_lens_and_seat_activity` (both
  `event_prefix` values in one run — a lens card and a verifier seat —
  plus the unchanged `upheld` adjudication) and
  `test_qc_streamed_deltas_win_and_an_absent_input_says_nothing`.
  `tests/test_streaming.py` gains
  `test_start_input_only_web_activity_emits_the_real_query_and_url`,
  `test_malformed_frames_the_relay_decodes_never_fail_a_chat_turn`, and
  `test_a_real_sdk_start_frame_with_a_non_mapping_input_is_survivable`
  (added in review — see the last deviation).
  Focused run green (`test_research_engine` / `test_qc_live_events` /
  `test_streaming` — 40 passed); full gate green: `pytest -q` 1178
  passed, 9 skipped; `npm test` 96 passed; `npm run build` clean (no
  frontend change — run as a regression check, not because this chunk
  touched the UI).
- Deviations:
  - **The chat relay was not already malformed-frame-safe, so it is
    now.** Plan step 6 says "malformed frames remain nonfatal" as a
    preservation item and acceptance criterion 4 names the chat turn —
    but `_stream_events` had no per-event guard, and
    `json_buffers.get(index, "") + (getattr(delta, "partial_json", "")
    or "")` raises `TypeError` on a non-string `partial_json`, taking the
    whole turn with it. The new test proves that: against the pre-fix
    code it fails with `'error' == 'turn_complete'`. The loop body is now
    wrapped in the same `except Exception: continue` the research and QC
    relays (adapted *from this one*) have always had. Stream ITERATION
    errors still escape, which is the property that matters.
  - **`_start_input` is a local helper in each of the three relay
    modules**, not shared through `research/grounding.py` the way Chunk
    1.2's `response_container_id` is. Grounding reads facts off finalized
    *messages*; this reads a raw *stream frame*, a different provider
    surface. More to the point, the three relays are deliberately
    triplicated (CLAUDE.md: "copy-adapted from
    `conversation._stream_events`") and already carry three deliberately
    different `_safe_json`-family parsers — QC's tolerates `TypeError`
    and accepts a `q` alias, research's does not. Adding one shared
    import beside three divergent local ones would have been the
    inconsistent choice. QC's is named `_start_block_input` because
    `_start_input` would read like "the input that starts a QC run".
  - **`block_kinds` is popped at stop too**, not just the payload dicts.
    The plan says "pop the index from all tracking dictionaries"; doing
    it for real is what makes `test_a_finished_block_is_forgotten_at_stop`
    a genuine test rather than a tautology — with `start_inputs` in play,
    a surviving `block_kinds` entry would let a repeat stop re-emit the
    start copy. The pops happen **before** the
    `if btype != "server_tool_use": continue`, so a text/thinking block
    releases its index as well. Chat's pop also frees a completed
    `apply_spec_edits` batch's JSON, which is the largest buffer in the
    turn.
  - **`tests/fakes.py` gained `code_execution_tool_events`**, a standalone
    builder rather than an option on `_synthesize_events`. Synthesis is
    driven by a scripted turn's *content*, which cannot express "this
    block streamed no deltas" — every existing fixture would have had to
    grow a flag. The builder pairs with the existing `events=` override
    idiom that the malformed-frame tests already use, and its docstring
    names the direct-caller counterpart so the two shapes are documented
    together. `block_start_event` took a keyword-only `input=`; absent
    (every existing call site) the started block carries no `input`
    attribute at all, exactly as the direct-caller wire does.
  - Precedence is `streamed or started`, per plan step 4 — a stream that
    somehow supplied both is taken at its deltas. Absence behavior stays
    deliberately non-uniform: research and QC skip, chat still emits its
    chip with empty text.
  - **Acceptance criterion 4 is scoped to frames the relay decodes, and
    that scoping is deliberate** — raised in review on PR #93, where an
    automated reviewer read "No malformed frame can fail a chat turn,
    dimension, lens, or verifier" literally and asked for the stream
    ITERATION to be guarded too. It must not be. The SDK accumulates and
    snapshots every raw frame inside `next(stream)` and only then yields
    it (`anthropic/lib/streaming/_messages.py::__stream__` calls
    `accumulate_event` before `yield`, verified against 0.120.2), so a
    frame that breaks the SDK's own accumulator raises during iteration —
    which is a **failed request**, and all three relays document that
    those escape into the retry classifier on purpose. Catching them
    would silently kill the retry pinned by
    `test_malformed_frames_are_ignored_and_stream_failure_retries`, and
    would change billed retry behavior, which this phase forbids.
    The reviewer's second point was fair and was fixed: the chat test was
    named `..._a_malformed_stream_frame_never_fails_a_chat_turn`, which
    over-claimed, and its `partial_json=7` frame is one today's SDK would
    raise on first. It is now
    `test_malformed_frames_the_relay_decodes_never_fail_a_chat_turn`, with
    the boundary written into the docstring, and a **new** test builds a
    genuine `RawContentBlockStartEvent`/`ServerToolUseBlock` rather than a
    `SimpleNamespace`. That one matters: `ServerToolUseBlock.input` is
    declared `Dict[str, object]` but raw events are built with
    `construct_type_unchecked` (no validation), so a non-mapping `input`
    reaches the app on a real SDK object with nothing upstream rejecting
    it — `_start_input`'s `isinstance(..., Mapping)` is the only guard,
    and deleting it turns that test red. The three helper docstrings now
    say so.
- Manual QA owed: Phase 2's list — start research and confirm visible
  query and URL labels populate. Note this is the *direct-caller* path
  (Chunk 1.1), which the live canary in Chunk 6.5 already covers; the
  code-execution path this chunk adds cannot be exercised without
  re-enabling dynamic filtering, which is an owner decision and out of
  scope. It is proven hermetically, which is the point of the fixture.

## Chunk 2.2 — Correct QC settling semantics

### Urgency

This chunk is independent of 2.1 and may land immediately after Chunk 1.1 —
do not hold it behind the rest of this phase. The wrong semantics already ship
today on two backend surfaces (the double-start 409 copy and the readiness
`qc_current` detail claim a stop was requested during every normal run), and
the Review Room frontend turns the same bit into a run-long "Stop requested —
finishing already-paid in-flight work" banner the first time it runs.

### State contract

Use these meanings everywhere:

| Runner state | `status` | worker settled | `settling` |
|---|---|---:|---:|
| Normal active run | `running` | no | false |
| Normal completed/failed run | terminal | yes | false |
| User stop won, worker unwinding paid work | terminal | no | true |
| Stopped worker fully attached/discarded | terminal | yes | false |

`settling` is not a synonym for “a worker thread exists.” Callers that need to
block both normal running and stop settlement already check
`status == "running" or is_settling` and should continue doing so.

### Implementation

1. In `backend/qc/runner.py`, define the stop-settling predicate once under the
   runner lock: `status in _TERMINAL and not _worker_settled`.
2. Use it in both `QCRunner.is_settling` and
   `audit_record_snapshot()["runner"]["settling"]`. Avoid two expressions that
   can drift.
3. Audit `backend/app.py` readiness, double-start, apply, dismiss, preview, and
   export guards. Preserve their separate `status == "running"` checks; only
   their text selection should now distinguish ordinary running from stopped
   settlement correctly.
4. Add a backend test that holds a fake normal QC worker mid-run and asserts:
   - `/api/qc/status` reports `status="running", settling=false`;
   - readiness says the run is running, not that a stop was requested; and
   - a second start returns “already running,” not the stopped-settling copy.
5. In the frontend, add/use one predicate for stop settlement:
   `snapshot.status !== "running" && snapshot.settling === true`.
   Apply it in `foldQcLiveState`, `isQcActiveSnapshot`, and `QCDrawer` labels,
   warning banner, button state, and aria-live message.
6. In `reconcileQcSnapshotUpdate`, a fetched running snapshot with
   `settling:false` must clear an erroneous prior settling bit. Keep settlement
   sticky only for a terminal stopped attempt until `qc_attempt_settled`.
7. Add frontend tests for each row in the state table and for stale
   reconciliation.

### Files

- `backend/qc/runner.py`
- `backend/app.py` only if message/gate cleanup is required
- `tests/test_qc_runner_audit_integrity.py`
- `tests/test_qc_audit_report.py`
- `tests/test_qc.py`
- `frontend/src/lib/qcLive.ts`
- `frontend/src/components/QCDrawer.tsx`
- `frontend/tests/qcLive.test.ts`
- `CLAUDE.md`

### Focused verification

```powershell
venv\Scripts\python -m pytest -q tests/test_qc_runner_audit_integrity.py tests/test_qc_audit_report.py tests/test_qc.py
Push-Location frontend
npm test
npm run build
Pop-Location
```

### Acceptance criteria

- A normal run never displays “Stop requested,” “Preserving report,” or
  “finishing already-paid in-flight work.”
- A stopped, unwinding attempt still displays those states until its settlement
  event.
- Start/apply/dismiss/readiness behavior remains blocked during both active run
  and genuine settlement, with accurate copy.

### Implementation record

- Status: **complete** (2026-07-29)
- Commit/PR: `66abd43` — PR #92
- Tests: `tests/test_qc_runner_audit_integrity.py` gains
  `test_an_ordinary_running_attempt_is_not_settling` (running with a real
  in-flight worker reports `settling: false` on both surfaces, a second
  start is still refused, and a normal completion stays not-settling).
  `tests/test_stop.py` gains
  `test_a_normal_qc_run_never_reports_a_stop_it_did_not_get`, the API-level
  version the plan asks for: a blocked worker held mid-run, then
  `/api/qc/status` → `running` + `settling: false`, the readiness
  `qc_current` detail containing neither "stop" nor "settl", and a second
  `POST /api/qc/start` returning exactly "Final QC is already running."
  `frontend/tests/qcLive.test.ts` gains three: every row of the state
  table through `isQcStopSettling`/`isQcActiveSnapshot`, a normal running
  fold, and a running snapshot clearing an erroneous prior settling bit.
  Focused run green (stop / runner integrity / audit report / qc — 90
  passed); full gate green: `pytest -q` 1170 passed, 9 skipped;
  `npm test` 95 passed; `npm run build` clean.
- Deviations:
  - **One `backend/app.py` copy change**, which the plan allowed ("only if
    message/gate cleanup is required"). The readiness running branch read
    "Final QC is running and has not settled." — accurate English, but
    `settling` is a term of art in this subsystem for a stopped attempt
    unwinding, so the sentence invited exactly the confusion the chunk
    exists to remove. Now "Final QC is running; no completed report is
    available yet." The test asserts the detail contains neither "stop" nor
    "settl", which keeps the overloaded vocabulary from creeping back.
  - No other `app.py` change was needed: every guard already had the shape
    the plan wanted (`status == "running" or is_settling` for the gate,
    `is_settling` for the copy), so fixing the predicate fixed all of them
    at once.
  - `tests/test_qc_audit_report.py` untouched — it exercises report
    projections, and the runner-integrity file already owned the state
    table.
  - **The reconcile carries settlement on `isQcStopSettling(previous)`, not
    `previous.settling`** — a correction made in review on PR #92. Gating
    on the *reconciled* status re-admitted the stale bit the moment a run
    went terminal: a normal completion emits no `qc_attempt_settled`, so
    the bit would then latch on forever and leave the drawer in stop
    language, and active, after the run ended. The stale input this
    defends against is specifically a running-and-settling snapshot, so
    the test has to be on the previous snapshot's own state.
- Manual QA owed: Phase 2's list — run a normal Final QC and confirm no
  stop/settling language anywhere, then stop one and confirm the real
  settlement language persists until paid in-flight work attaches.

## Chunk 2.3 — Research follower reconnect and stale-fetch rejection

### Implementation

1. Move the pure research live-log helpers currently embedded in
   `frontend/src/App.tsx` into a focused module such as
   `frontend/src/lib/researchLive.ts`:
   - `mergeResearchEvent`;
   - max-sequence/round identity helpers; and
   - `reconcileResearchSnapshotUpdate`, returning `{snapshot, accepted}` like
     the QC equivalent.
2. Add a research refresh-generation ref. Increment it when a new round starts
   or a streamed `research_started` belongs to a different round. Reject a
   response from an older generation.
3. For a same-round fetch, compare max sequence rather than only array length.
   If the fetched watermark is behind the local log, reject the entire fetched
   snapshot so stale status, error, and profile cannot replace a terminal local
   state. For a different round, replace wholesale.
4. Rewrite `followResearch` with the proven `followQc` loop shape:
   - catch stream iteration failures;
   - record terminal/superseded `stream_end` status;
   - on nonterminal close, call `getResearchStatus`;
   - reconnect after a short delay while the authoritative/local state is still
     `running` and the workspace epoch is unchanged;
   - on status-probe failure, reconnect if the local snapshot is active; and
   - keep the existing final refresh of research and usage.
5. Give the follower an identity and an abort path: each `followResearch`
   invocation owns an `AbortController` (or equivalent epoch token), a newer
   follower or a workspace-epoch change aborts the old stream instead of
   letting two readers race, and a synchronous snapshot ref (the QC
   `researchSnapshotRef` pattern) backs every decision the loop makes between
   renders so it never reads stale React state.
6. Type `stream_end` as a proper discriminated union in `frontend/src/types.ts`
   (terminal vs `running` timeout vs `superseded`) so classification is
   compiler-checked rather than string-matched inline.
7. Handle equal-watermark upgrades explicitly: when the fetched and local
   watermarks match, adopt the response only if it does not regress lifecycle
   state — a terminal local status or an adopted profile must never be replaced
   by a running/older payload with the same sequence.
8. Make `refreshResearch` generation-aware and do not drive auth-modal or other
   side effects from a rejected stale response.
9. Add `frontend/tests/researchLive.test.ts` to the explicit `npm test` command
   in `frontend/package.json`.
10. Test terminal-over-running races for round 1 and later rounds, a different
    round replacement, generation rejection, abort-on-epoch-change, stream-end
    status classification, equal-watermark non-regression, and profile
    preservation.

### Files

- `frontend/src/App.tsx`
- `frontend/src/lib/researchLive.ts` (new)
- `frontend/tests/researchLive.test.ts` (new)
- `frontend/package.json`
- optionally `frontend/src/types.ts` for shared narrowed types
- `CLAUDE.md`

### Focused verification

```powershell
Push-Location frontend
npm test
npm run build
Pop-Location
```

### Acceptance criteria

- A stream close while server status is running reconnects without a page or
  session reset.
- A terminal or superseded sentinel does not reconnect.
- A transient stream/status failure does not create an unhandled rejection or
  strand an active board.
- A late pre-terminal fetch cannot regress terminal status or remove the newly
  adopted profile.

### Implementation record

- Status: **complete** (2026-07-29)
- Commit/PR: `a42fc76` — PR #94
- Tests: 25 new, in two new files, both registered in `package.json`'s
  explicit `node --test` list.
  `frontend/tests/researchLive.test.ts` (22): merge ordering/dedupe, merge
  never writing status/error/profile, a replayed `research_started` NOT
  blanking the board, a different-round reset, a same-round restart reset,
  the sentinel ignored; `maxResearchEventSeq` / `researchSnapshotRound` /
  `isResearchActiveSnapshot`; every `stream_end` status classified; the
  terminal-over-running race rejected on round 1 **and** on a later round,
  the restart-vs-late-fetch division of labour, a further-reaching fetch
  adopted, a different round replaced wholesale, equal-watermark
  non-regression in both directions, generation rejection and acceptance,
  first-snapshot and round-less adoption, and the milestone set.
  `frontend/tests/researchStream.test.ts` (3): the abort signal reaches
  `fetch`, aborting ends the stream, and breaking out releases the body —
  all against a stubbed SSE body that **never closes**, so a leak cannot
  pass by accident.
  Both fixes were verified load-bearing by reverting them: restoring the
  pre-2.3 length-based reconcile turns the two race tests red, and deleting
  `readSse`'s reader release hangs the break-out test.
  Gate green: `npm test` 121 passed (was 96), `npm run build` clean,
  `pytest -q` 1178 passed / 9 skipped (unchanged — no backend change).
- Deviations:
  - **A restarted round is deliberately NOT handled in reconcile.** A
    rule for it was written, and the existing tests rejected it — "stop
    round 2, start round 2 again" and "a late fetch from the middle of
    round 2" are the same triple (same round, shorter fetched log), so
    adopting the first re-opens the second, which is the whole point of
    the chunk. The restart is instead covered by two mechanisms that both
    run first: `onStartResearch` bumps the refresh generation, and
    `mergeResearchEvent` resets the log on the new round's
    `research_started` frame, which always precedes the milestone refetch
    it triggers. Round identity genuinely repeats here —
    `ResearchRunner.start` numbers from `profile_result.round_count` and a
    stopped round is never adopted — so this is not hypothetical. Both
    mechanisms and the reasoning are pinned by tests and written into the
    module docstring.
  - **`mergeResearchEvent`'s reset became run-aware** (the plan left merge
    alone; 2.4 owns its dedupe cost). It had to: the reset was
    unconditional on `research_started`, and reconnect — which this chunk
    introduces — replays every round from seq 0, so the board would blank
    and rebuild on each transport hiccup. A frame starts a new run when
    the local log is empty, the round differs, **or the local snapshot is
    not running**. 2.4's actual job (a constant-time duplicate index) is
    untouched.
  - **`readSse` gained a `finally` that cancels the reader, and
    `streamResearch` takes an `AbortSignal`.** The plan asked for "an
    `AbortController` (or equivalent epoch token)"; the token alone would
    not have been enough. Breaking out of a `for await` unwinds the
    generator but cancels nothing, so every prior `break` — and now every
    reconnect — left the browser reading a body no one would consume.
    Pinned by a test whose stub body never closes.
  - **`advanceWorkspaceEpoch()` replaced three inline
    `workspaceEpochRef.current += 1`** so the abort cannot be forgotten at
    a fourth site. QC's follower is deliberately left on its epoch-check-
    plus-`break`; converting it is its own change.
  - **The transition also CLEARS the research snapshot** — a P1 caught in
    review on PR #94, and a bug this chunk introduced. Round number is the
    reconcile identity and it means nothing across workspaces: two
    projects both sit at round 1, and a restored project's entire log is a
    single `research_complete` at seq 0 (`ResearchRunner.restore` empties
    `events` first). So opening project B over researched project A
    arrived at reconcile as same-round-with-a-shorter-log — indistinguishable
    from a late fetch — and the new watermark rule rejected it
    **permanently**, since a restored run never streams and nothing else
    reset the ref. The pre-2.3 code half-adopted instead, which was also
    wrong but self-corrected. Clearing in `advanceWorkspaceEpoch` is the
    identity reset; reconcile then sees `previous === null` and adopts.
    QC needs no equivalent — its run ids are UUIDs, so two workspaces
    never compare as the same run. `advanceWorkspaceEpoch` moved below
    `replaceResearchSnapshot` for it: a `useCallback` dependency array is
    evaluated at render time in declaration order, and naming a later
    `const` there is the first-render TDZ crash CLAUDE.md already records
    from the research-drawer work.
  - **`refreshResearch`'s catch no longer nulls the snapshot** — it used
    to erase a live board on one dropped poll. It now does nothing, which
    is the `refreshQc` posture and what acceptance criterion 3 asks for.
  - `frontend/tests/researchStream.test.ts` is a second new file the plan
    did not list (it names only `researchLive.test.ts`). The abort path is
    a transport concern at the `api.ts` seam, not a pure-helper one, and
    the repo's no-vitest convention means the follower loop itself is not
    directly testable — this covers the half that is.
  - `frontend/src/types.ts` did get the narrowed type (the plan said
    "optionally"): `ResearchStreamEndStatus` is closed and
    `classifyResearchStreamEnd` switches over it with a `never` arm, which
    is what makes step 6's "compiler-checked rather than string-matched"
    true rather than aspirational.
- Manual QA owed: Phase 2's list — interrupt the research SSE transport
  while the server keeps running and confirm the board reconnects and
  reaches its terminal state. Not reproducible hermetically (the repo has
  no browser-driven suite); the pure helpers and the transport seam are
  covered, the `followResearch` loop between them is not.

## Chunk 2.4 — Constant-time replay duplicate handling

### Implementation

1. Check run/round identity **before** any sequence comparison: a frame from a
   different run or round must take the reset path, never the dedupe path, so
   sequence collisions across runs cannot masquerade as duplicates.
2. In `frontend/src/lib/qcLive.ts::mergeQcEvent`, maintain a per-snapshot
   sequence index (a `Set`/max-watermark pair, or a keyed map) so the replay
   check is a direct lookup. Calling `maxEventSeq`/`Array.find` per frame is
   itself O(n) and would keep replay quadratic; the point of this chunk is a
   constant-time duplicate test. On a duplicate, return the original snapshot
   object unchanged — the runner log is append-only, so a replay duplicate
   cannot update authoritative content.
3. Reserve the map/re-sort path for a genuine missing/out-of-order sequence.
4. Apply the same rule to `mergeResearchEvent` in the new research live module.
5. Tests must assert referential identity (`result === previous`) and event-array
   identity for duplicates, correct insertion for a genuine gap, and new-run
   reset behavior.
6. Add a non-timing bounded-access test: instrument the merge (a counting proxy
   over the event array or index) and prove that replaying a large dense log
   performs O(1) work per duplicate frame — no full-array scan or rebuild. Do
   not use elapsed-time thresholds in CI.
6. If profiling still shows excessive React work, batch events at the stream
   reader boundary as a separate measured follow-up; do not complicate this
   chunk speculatively.

### Files

- `frontend/src/lib/qcLive.ts`
- `frontend/src/lib/researchLive.ts`
- `frontend/tests/qcLive.test.ts`
- `frontend/tests/researchLive.test.ts`

### Focused verification and phase gate

```powershell
Push-Location frontend
npm test
npm run build
Pop-Location
venv\Scripts\python -m pytest -q tests/test_research_api.py tests/test_qc_live_runner.py tests/test_qc_live_events.py
```

Then run the full standard verification commands from the master plan.

### Acceptance criteria

- Replaying an already-present sequence performs no array rebuild and no state
  update.
- New and genuinely out-of-order frames still merge in sequence order.
- Lifecycle state never regresses from a duplicate terminal/start frame.

## Phase 2 manual QA

- Start research and confirm visible query and URL labels populate.
- Interrupt the research SSE transport while the server keeps running; confirm
  the board reconnects and reaches its terminal state.
- Run normal Final QC and confirm no stop/settling language appears.
- Stop a QC run and confirm the real settlement language remains until paid
  in-flight work attaches.
- Reconnect/reopen the Review Room mid-run and confirm no long UI freeze.
