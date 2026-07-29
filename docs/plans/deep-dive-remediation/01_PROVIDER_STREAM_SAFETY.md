# Phase 1 — Provider stream safety

- Status: in progress (1.1–1.3 complete; 1.4 planned)
- Prerequisite: repository baseline green
- Risk: critical; this phase changes provider tool configuration, continuation
  requests, and committed conversation history

## Goal

Make the `web_search_20260209` and `web_fetch_20260209` tools safe in all
three consumers, in two layers:

1. **Restore direct tool invocation first.** The `_20260209` versions default
   to `allowed_callers: ["code_execution_20260120"]` (dynamic filtering), which
   runs server-side code execution under the hood. Anthropic documents that
   this mode is **not ZDR-eligible by default** and that resuming a pause with
   pending code-execution-called tool uses requires the provider container id —
   the exact 400 that killed two research dimensions in the reviewed run.
   Setting `allowed_callers: ["direct"]` removes the container requirement at
   the source, restores the documented ZDR posture, and restores per-search
   `input_json_delta` streaming (the live query/URL labels Phase 2 also hardens).
2. **Keep container propagation and history scrubbing as defense-in-depth.** A
   paused server-tool call must resume inside the same provider container when
   one exists, and a stopped or truncated chat turn must never save a server
   tool use that has no matching result — regardless of caller mode.

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

## Chunk 1.1 — Direct server-tool callers (reliability + ZDR)

### Decision being implemented

Frozen decision: the shipped web tools run with `allowed_callers: ["direct"]`.
Dynamic filtering's token savings are real but unproven for this workload, and
its code-execution caller is what produced the nonretryable container-id 400,
the invisible live queries, and the undocumented non-ZDR posture. Re-enabling
dynamic filtering later is an explicit owner decision, contingent on the
container support (Chunks 1.2–1.3) being live and canary-verified.

### Implementation

1. In `backend/research/schema.py`, add `"allowed_callers": ["direct"]` to both
   web-tool builder dicts (`web_search_20260209` and `web_fetch_20260209`).
   Every consumer — research dimensions, the QC `code_compliance` lens, and the
   chat loop — receives the tools through these builders, so one change covers
   all three channels.
2. Update the module docstring and the engine comments that describe
   "programmatic tool calling under the hood": with direct callers there is no
   code-execution container by default, and per-search inputs stream as
   `input_json_delta` again.
3. Reconcile the ZDR claims: update the `CLAUDE.md` Final QC note and any
   README/release-note/trust-dossier wording so the ZDR statement matches the
   shipped tool configuration. If dynamic filtering is ever re-enabled, those
   claims must be re-qualified in the same commit.
4. Note the cache consequence in the commit message and docs: changing the
   tool definition changes the request's tool bytes, so previously cached
   prefixes stop matching and subsequent requests write fresh entries. This is
   expected and one-time per cache lineage.
5. Tests: assert the exact tool dicts (including `allowed_callers`) that each
   engine sends, via the fake client's captured request kwargs. Existing
   streaming fixtures already model direct-shaped delta streams and must pass
   unchanged.

### Files

- `backend/research/schema.py`
- `backend/research/engine.py` (comments)
- `backend/qc/engine.py` (comments)
- `tests/test_research_engine.py`
- `tests/test_research_api.py`
- `tests/test_qc.py`
- `CLAUDE.md`, `README.md`

### Focused verification

```powershell
venv\Scripts\python -m pytest -q tests/test_research_engine.py tests/test_research_api.py tests/test_qc.py
```

### Acceptance criteria

- Every research, QC, and chat request that carries web tools declares
  `allowed_callers: ["direct"]` on both tools.
- No documentation still claims ZDR compatibility for a configuration that
  does not have it.
- The live direct-mode canary (Phase 6.5) is the paid confirmation that
  research completes without a container-id 400 and that query/URL deltas
  stream; it is not required to land this chunk.

### Implementation record

- Status: **complete** (2026-07-29)
- Commit/PR: `4bd0c18` — PR #88
- Tests: `tests/test_research_engine.py` gains
  `test_web_tools_declare_direct_callers_on_every_research_request` (exact
  tool dicts on all four dimension requests) and
  `test_builders_are_the_only_source_of_the_web_tool_shape` (builder unit:
  the returned `allowed_callers` list is a fresh copy, so a consumer
  mutating one request's tools can't reach into another's).
  `tests/test_qc.py` gains
  `test_qc_web_tools_declare_direct_callers_in_both_phases` (the
  `code_compliance` lens plus its two verifier seats; also pins that the
  other four lenses carry no web tools). `tests/test_app.py`'s chat-request
  assertion now covers the two web tools' type and caller mode. Focused run
  green (`test_research_engine` / `test_research_api` / `test_qc` /
  `test_app`, 84 passed); full gate green — `pytest -q` 1142 passed,
  9 skipped; `npm test` 92 passed; `npm run build` clean.
- Deviations:
  - The literal is a named constant,
    `research/schema.WEB_TOOL_ALLOWED_CALLERS`, rather than being inlined
    twice. One declaration, one place for the rationale; both builders
    spread a fresh `list(...)` of it.
  - `backend/llm/conversation.py::_chat_tools` also got a docstring pointer
    (the plan listed only the two engines' comments). Chat is the third
    consumer and the acceptance criterion covers it, so the reader who
    finds the tool list there needs the same pointer.
  - `tests/test_app.py` was added to the test set for the same reason —
    the plan's file list stopped at research and QC, but "every research,
    QC, **and chat** request" is the stated criterion.
  - `tests/test_research_api.py` deliberately unchanged: the API path runs
    the same `_run_dimension`, and the engine test already asserts the
    exact dicts across all four dimensions. A second assertion there would
    duplicate coverage, not add it.
  - `backend/release_notes.py` deliberately unchanged. Its v1.8.0 item
    ("Available on zero-retention accounts") is a **model**-scoped claim —
    Fable 5 required 30-day retention, Opus 5 does not — and that was and
    remains accurate. The app-level ZDR statement lives in the trust
    dossier, which is updated. Editing a shipped changelog entry rewrites
    what users already read, for no accuracy gain.
- Manual QA owed: the live direct-mode canary (Chunk 6.5) — paid and
  owner-authorized. Assert a search-heavy research dimension completes with
  streamed query/URL deltas and no container-id 400.

## Chunk 1.2 — Research and QC continuation containers (defense-in-depth)

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

- Status: **complete** (2026-07-29)
- Commit/PR: `f0a5ac5` — PR #89
- Tests: `tests/test_research_engine.py` gains
  `test_pause_continuation_echoes_the_container_and_a_retry_drops_it` —
  one scripted dimension covering the whole contract in order (pause with
  `cont_research_1` → pause *without* the field, retained → retryable
  failure → clean retry that carries nothing) plus the assertions that the
  paused assistant content is re-sent verbatim and the id reaches neither
  `system`, `tools`, nor `messages` — and
  `test_a_dimension_without_any_container_is_unaffected` (the normal
  direct-caller path emits no `container` key at all).
  `tests/test_qc_live_events.py` gains the QC equivalent,
  `test_qc_pause_continuation_echoes_the_container_and_a_retry_drops_it`,
  which additionally pins that the four non-web lenses stay container-free.
  Focused run green (research engine / QC verifier v3 / QC live events / QC
  / research API / research rounds / stop — 99 passed); full gate green:
  `pytest -q` 1145 passed, 9 skipped. No frontend change, so `npm test` /
  `npm run build` were not required.
- Deviations:
  - `response_container_id` lives in `research/grounding.py`, not
    duplicated in both engines. That module already owns "read one fact off
    a provider response" (`web_search_count`, `classify_stop_reason`, the
    evidence collectors) and both engines already import from it, so the
    retain-latest-nonblank rule has exactly one definition.
  - It returns `""` rather than `str | None`, which makes the retention
    rule a plain `container_id = response_container_id(r) or container_id`.
    The plan's `str | None` was a typing detail; the behavior it specifies
    is unchanged.
  - `research_response` also took the `container=` kwarg (the plan listed
    four factories). An asymmetric fakes module is a trap, and the kwarg is
    byte-compatible when absent.
  - **`SequencedFakeClient` now snapshots each captured request's
    `messages` list.** Not in the plan, but required to make the plan's own
    "the assistant pause content is still resent verbatim" bullet
    assertable: both engines append to a single list across continuations,
    so capturing it by reference made every request in an attempt show that
    attempt's *final* conversation. The first draft of the research test
    asserted `["user", "assistant"]` and got `["user", "assistant",
    "assistant"]` — the aliasing, not a bug in the engine. A shallow copy
    is enough (the message dicts are never mutated in place; the resend
    sanitizer builds new ones).
  - `_FakeStreamCtx.get_final_message` and `SequencedFakeClient`'s
    non-`usage` fallback both rebuilt the response and dropped `container`;
    they now carry it through. Not needed by this chunk's paths (research
    and QC take `_FakeResearchStreamCtx`, which returns the response as-is)
    but required by Chunk 1.3's chat loop, and a fake that silently drops
    the field under test is worse than no fake.
  - `tests/test_qc_verifier_v3.py` untouched — the plan said "and/or", and
    `test_qc_live_events.py` already had the engine-level harness with
    request capture the test needs.
- Manual QA owed: none specific to this chunk. The provider cannot be made
  to return `pause_turn` on demand, which is exactly why the contract is
  proven hermetically; Phase 1's shared live canary (Chunk 6.5) confirms a
  natural pause opportunistically.

## Chunk 1.3 — Chat continuation container (defense-in-depth)

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

- Status: **complete** (2026-07-29)
- Commit/PR: `d39e778` — PR #90
- Tests: `tests/test_app.py` gains
  `test_chat_carries_the_container_through_a_turn_and_drops_it_next_turn` —
  one turn of three rounds (pause with `cont_chat_1` → resume → a
  continuation after a client `tool_result`) proving the id is *retained*
  across the turn rather than re-read per round, then a second user turn
  that starts clean, then the negative assertions that it reaches neither
  `system`, `tools`, `messages`, committed history, nor the saved project
  file. `test_pause_turn_resumes_and_emits_web_activity` gains one line:
  a pause carrying no container does not make one up. Full gate green:
  `pytest -q` 1146 passed, 9 skipped. No frontend change.
- Deviations:
  - Reuses `research/grounding.response_container_id` from Chunk 1.2
    instead of a chat-local reader, so all three channels share one
    definition of the retain-latest-nonblank rule.
  - The plan suggested expanding
    `test_pause_turn_resumes_and_emits_web_activity`; it offered "or add a
    neighboring test", and a neighbour was the better fit — the contract
    needs a three-round turn plus a second turn, which would have buried
    the existing test's actual subject (live web activity).
  - No trace assertion was needed: `_enter_stream` never traces request
    kwargs (only a note on the thinking.display degrade path), so there is
    no payload for the container to leak into. Verified by reading, and the
    request-level negative assertions cover the rest.
- Manual QA owed: none specific to this chunk; the pause contract is
  hermetic for the same reason as 1.2. Phase 1's shared live canary
  (Chunk 6.5) still applies.

## Chunk 1.4 — Dangling server-tool history scrub and legacy repair

### Design

A valid server-tool pair can cross assistant-message boundaries after a
`pause_turn`, so a per-message filter is incorrect. Implement one turn-wide
helper that:

1. collects every `tool_use_id` from **every recognized server-result family**
   across all messages in the turn — `web_search_tool_result`,
   `web_fetch_tool_result`, and the code-execution result families
   (`bash_code_execution_tool_result`, `text_editor_code_execution_tool_result`,
   and any future `*_tool_result` block carrying a `tool_use_id`) — so a
   completed code-execution use is never mistaken for a dangling one;
2. preserves complete use/result pairs exactly as received;
3. removes `server_tool_use` blocks whose `id` is absent from that global
   result-id set; and
4. removes orphaned server-result blocks whose `tool_use_id` matches no
   retained `server_tool_use` — an unpaired result is as invalid on resend as
   an unpaired use.

Blocks with genuinely unknown types remain untouched. A helper that cannot
identify a mapping must fail safe for the dangling use without deleting
completed result or citation blocks.

### Legacy poisoned-history repair

The stop bug may already have written a dangling `server_tool_use` into a
saved `.baspec`. Committed-history scrubbing alone cannot heal those files, so
apply the same pairing helper defensively at the two read boundaries:

- during project load, over the restored chat history (log a sanitized
  diagnostics event when anything was repaired — never silently); and
- in `sanitize_messages_for_resend`, as a final guard over the outgoing
  request, so even an unrepaired in-memory history cannot poison a request.

Repair is copy-on-write and must not rewrite the saved file until the user
next saves normally.

### Implementation

1. Add a private copy-on-write scrubber in
   `backend/llm/conversation.py`. Give it a name that states the pairing rule,
   such as `_without_unpaired_server_tool_uses`.
2. Use it in all four safety layers:
   - the mid-stream `user_stop`/`max_tokens` truncation path before the
     assistant message is appended;
   - the between-round stop path, including a trailing paused assistant
     message;
   - `_committed_messages` as the final invariant guard over the entire turn;
     and
   - the legacy read boundaries described above (project load and
     `sanitize_messages_for_resend`).
3. If a scrub empties the terminal assistant content, use the existing
   user-stop or truncation placeholder. Do not introduce adjacent user roles.
4. Preserve the existing removal of client `tool_use`, thinking, context,
   fetched PDF bodies, reference bodies, and heavy figure inputs.
5. Give the fakes realistic wire shapes: scripted `server_tool_use` blocks and
   result blocks must carry matching `srvtoolu_`-style ids (never placeholder
   collisions), and paused fixtures must preserve any scripted container so the
   Chunk 1.2/1.3 threading stays covered by the same scenarios.
6. Add scripted tests for:
   - stop after `server_tool_use` start but before a result;
   - `max_tokens` with a bare server tool use;
   - stop in the gap immediately after a paused response;
   - a valid use/result pair in one assistant message;
   - a valid pair split across two assistant messages by `pause_turn`;
   - a completed code-execution-family pair that must be preserved intact;
   - an orphaned server-result block that must be removed with its lost use;
   - a follow-up user turn after each unsafe stop/truncation, proving the
     captured outgoing history validates and the turn succeeds;
   - project save/load after a stopped web turn, proving poison cannot persist;
     and
   - loading a hand-built legacy project containing a dangling
     `server_tool_use`, proving the load repairs it, logs the repair, and the
     next turn's outgoing request validates.
7. Update the conversation invariants in `CLAUDE.md`: stop strips both dangling
   client and server tool calls, with global pairing across the turn and
   defensive repair at load/resend.

### Files

- `backend/llm/conversation.py`
- `backend/research/resend_sanitizer.py` (resend-boundary guard seam)
- `backend/spec_doc/project.py` or the session load path that restores history
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
the owner explicitly authorizes them earlier. The provider cannot be forced to
return `pause_turn` on demand, so pause-continuation correctness is proven by
the hermetic contract tests above; the live canary asserts what is
deterministic and verifies a natural pause opportunistically:

- Run a search-heavy research dimension in direct mode and confirm it
  completes, query/URL deltas stream, and no container-id 400 occurs. If a
  natural `pause_turn` occurs, confirm its continuation succeeds.
- Stop chat while the UI says “Searching the web…”, then send another message
  and save/reopen the project.
- Exercise a web-enabled Final QC lens and inspect diagnostics for successful
  completion.
