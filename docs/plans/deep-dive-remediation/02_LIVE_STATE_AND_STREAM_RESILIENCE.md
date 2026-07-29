# Phase 2 — Live state and stream resilience

- Status: planned
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
the code-execution caller used by the `_20260209` tools, the full input can be
present on `content_block_start.content_block.input` and no JSON deltas follow.
Research and QC then suppress empty payloads; chat emits a blank chip.

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

- Status: planned
- Commit/PR:
- Tests:
- Deviations:
- Manual QA owed:

## Chunk 2.2 — Correct QC settling semantics

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

- Status: planned
- Commit/PR:
- Tests:
- Deviations:
- Manual QA owed:

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
5. Make `refreshResearch` generation-aware and do not drive auth-modal or other
   side effects from a rejected stale response.
6. Add `frontend/tests/researchLive.test.ts` to the explicit `npm test` command
   in `frontend/package.json`.
7. Test terminal-over-running races for round 1 and later rounds, a different
   round replacement, generation rejection, stream-end status classification,
   and profile preservation.

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

- Status: planned
- Commit/PR:
- Tests:
- Deviations:
- Manual QA owed:

## Chunk 2.4 — Constant-time replay duplicate handling

### Implementation

1. In `frontend/src/lib/qcLive.ts::mergeQcEvent`, before calling
   `normalizedEvents`, detect the common replay case:
   - event has a numeric sequence at or below the local maximum; and
   - an event with that exact sequence already exists.
   Return the original snapshot object unchanged. The runner log is append-only,
   so a replay duplicate cannot update authoritative content.
2. Reserve the map/re-sort path for a genuine missing/out-of-order sequence.
3. Apply the same rule to `mergeResearchEvent` in the new research live module.
4. Tests must assert referential identity (`result === previous`) and event-array
   identity for duplicates, correct insertion for a genuine gap, and new-run
   reset behavior.
5. Add a non-timing stress test that replays a large dense log and proves every
   duplicate returns the same object. Do not use elapsed-time thresholds in CI.
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
