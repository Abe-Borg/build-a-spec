# Phase 1 — Provider stream safety

- Status: planned
- Prerequisite: repository baseline green
- Risk: critical; this phase changes provider continuation requests and
  committed conversation history

## Goal

Make the current `web_search_20260209` and `web_fetch_20260209` tools safe in
all three consumers. A paused server-tool call must resume inside the same
provider container, while a stopped or truncated chat turn must never save a
server tool use that has no matching result.

This phase fixes the two production-loss mechanisms in Section A of the source
report. Complete it before changing activity UI, research readiness, caching,
or QC policy.

## Current anchors

- `backend/research/engine.py::_run_dimension` builds one static
  `request_kwargs`, appends paused assistant content, and calls
  `client.messages.stream` again without `response.container.id`.
- `backend/qc/engine.py::_run_streaming_call` has the same omission.
- `backend/llm/conversation.py::stream_user_turn` does not carry a turn-local
  container into `request_kwargs`.
- `backend/llm/conversation.py::_committed_messages` preserves all
  `server_tool_use` blocks, while the stop/truncation filter removes only
  client `tool_use` blocks.
- `tests/fakes.py` records request kwargs but fake responses do not currently
  expose a provider container or a code-execution-shaped start event.

Use symbol names rather than trusting historic report line numbers; the current
baseline has already shifted slightly.

## Provider contract to preserve

- Read the container after the response is finalized:
  `getattr(getattr(response, "container", None), "id", None)`.
- Send it as the top-level `container` request argument on every later request
  in the same attempt/turn.
- Retain the most recent nonblank id if a later fake/response omits the field.
- Reset it at the start of every outer retry attempt and every new user turn.
- Never serialize it into messages, cacheable content, session history,
  `QCResult`, `RequirementsProfile`, or project files.
- Tests must prove a fresh retry does not inherit a failed attempt's id.

## Chunk 1.1 — Research and QC continuation containers

### Implementation

1. Extend `tests/fakes.py` so scripted responses may carry an optional
   `container=SimpleNamespace(id=...)`. Prefer optional keyword arguments on
   `pause_response`, `qc_findings_response`, `qc_verdict_response`, and
   `raw_turn` rather than custom one-off fake classes. Existing callers must
   remain byte-for-byte compatible when the argument is absent.
2. In `backend/research/engine.py::_run_dimension`:
   - initialize `container_id: str | None = None` inside each outer retry;
   - build a fresh `stream_kwargs = dict(request_kwargs)` for each continuation;
   - add `stream_kwargs["container"] = container_id` only when nonblank;
   - after `get_final_message()`, refresh from the response's container;
   - keep the reset boundary outside the continuation loop but inside the retry
     loop.
3. Apply the same shape in
   `backend/qc/engine.py::_run_streaming_call`. Do not place the container in
   `_qc_user_content`, system blocks, tools, or `_cache_control`.
4. Update the continuation docstrings/comments in both engines to state the
   `_20260209` container obligation and the retry reset rule.
5. Add regression tests that inspect the fake client's captured request list:
   - first request omits `container`;
   - a `pause_turn` response with `cont_research_1` or `cont_qc_1` causes the
     immediate next request to include it;
   - the assistant pause content is still resent verbatim;
   - a response without `.container` remains supported;
   - a retry after an injected retryable failure starts without the prior id.

### Files

- `backend/research/engine.py`
- `backend/qc/engine.py`
- `tests/fakes.py`
- `tests/test_research_engine.py`
- `tests/test_qc_verifier_v3.py` and/or `tests/test_qc_live_events.py`
- `CLAUDE.md`

### Focused verification

```powershell
venv\Scripts\python -m pytest -q tests/test_research_engine.py tests/test_qc_verifier_v3.py tests/test_qc_live_events.py
```

### Acceptance criteria

- Every research/QC resume echoes the latest fake container id.
- No initial request or fresh retry carries a stale container.
- Search ceilings, response accounting, retry classification, event relay, and
  cache-control payloads are unchanged.
- Existing fakes without `.container` continue to pass.

### Implementation record

- Status: planned
- Commit/PR:
- Tests:
- Deviations:
- Manual QA owed:

## Chunk 1.2 — Chat continuation container

### Implementation

1. Add `container_id: str | None = None` to the local state of one
   `stream_user_turn` invocation.
2. Make request construction accept the current id and add a top-level
   `container` key only when set. Do not close over or mutate global/session
   state. Keep this compatible with Phase 6's later snapshot-outside-lock
   refactor.
3. After either `get_final_message()` or the stopped
   `current_message_snapshot` is selected, refresh the id from that message
   before deciding whether the round pauses, dispatches a client tool, or
   terminates. Later tool-result rounds in the same turn should therefore reuse
   the current container automatically.
4. Reset happens naturally on the next `stream_user_turn`; add a test proving
   that a second user turn's first request has no container.
5. Expand `test_pause_turn_resumes_and_emits_web_activity` (or add a neighboring
   test) to assert the request kwargs, not merely resent assistant content.

### Files

- `backend/llm/conversation.py`
- `tests/fakes.py`
- `tests/test_app.py`
- `tests/test_streaming.py`
- `CLAUDE.md`

### Focused verification

```powershell
venv\Scripts\python -m pytest -q tests/test_app.py tests/test_streaming.py
```

### Acceptance criteria

- A paused chat response with `container.id == "cont_chat_1"` produces a next
  request with `container="cont_chat_1"`.
- A later client-tool continuation in that turn keeps the container.
- A subsequent user turn begins container-free.
- No container appears in `session.history`, project save output, prompts, or
  trace payloads.

### Implementation record

- Status: planned
- Commit/PR:
- Tests:
- Deviations:
- Manual QA owed:

## Chunk 1.3 — Dangling server-tool history scrub

### Design

A valid server-tool pair can cross assistant-message boundaries after a
`pause_turn`, so a per-message filter is incorrect. Implement one turn-wide
helper that:

1. collects every `tool_use_id` from `web_search_tool_result` and
   `web_fetch_tool_result` blocks across all messages in the turn; then
2. removes only `server_tool_use` blocks whose `id` is absent from that global
   result-id set.

Blocks with unknown types remain untouched. A helper that cannot identify a
mapping must fail safe for the dangling use without deleting completed result
or citation blocks.

### Implementation

1. Add a private copy-on-write scrubber in
   `backend/llm/conversation.py`. Give it a name that states the pairing rule,
   such as `_without_unpaired_server_tool_uses`.
2. Use it in all three chat safety layers:
   - the mid-stream `user_stop`/`max_tokens` truncation path before the
     assistant message is appended;
   - the between-round stop path, including a trailing paused assistant
     message; and
   - `_committed_messages` as the final invariant guard over the entire turn.
3. If a scrub empties the terminal assistant content, use the existing
   user-stop or truncation placeholder. Do not introduce adjacent user roles.
4. Preserve the existing removal of client `tool_use`, thinking, context,
   fetched PDF bodies, reference bodies, and heavy figure inputs.
5. Add scripted tests for:
   - stop after `server_tool_use` start but before a result;
   - `max_tokens` with a bare server tool use;
   - stop in the gap immediately after a paused response;
   - a valid use/result pair in one assistant message;
   - a valid pair split across two assistant messages by `pause_turn`;
   - a follow-up user turn after each unsafe stop/truncation, proving the
     captured outgoing history validates and the turn succeeds;
   - project save/load after a stopped web turn, proving poison cannot persist.
6. Update the conversation invariants in `CLAUDE.md`: stop strips both dangling
   client and server tool calls, with global pairing across the turn.

### Files

- `backend/llm/conversation.py`
- `tests/fakes.py`
- `tests/test_stop.py`
- `tests/test_app.py`
- `tests/test_project_package.py` or the existing project round-trip coverage
- `CLAUDE.md`

### Focused verification

```powershell
venv\Scripts\python -m pytest -q tests/test_stop.py tests/test_app.py tests/test_project_package.py
```

### Acceptance criteria

- No committed assistant history contains an unpaired `server_tool_use`.
- Healthy search/fetch result and citation history remains available.
- Stop remains immediate and commits already-received text/edits.
- A follow-up turn and a save/resume follow-up both work.
- Full Phase 1 gate passes:

```powershell
venv\Scripts\python -m pytest -q
```

## Phase 1 manual QA

These steps are paid/provider-dependent and are deferred to Phase 6.5 unless
the owner explicitly authorizes them earlier:

- Run a search-heavy research dimension long enough to produce `pause_turn` and
  confirm it resumes instead of returning the container-id 400.
- Stop chat while the UI says “Searching the web…”, then send another message
  and save/reopen the project.
- Exercise a web-enabled Final QC lens and inspect diagnostics for successful
  continuation.
