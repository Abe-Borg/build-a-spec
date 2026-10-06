# CLAUDE.md — Build-a-Spec engineering reference

Conversational spec-section authoring. Chat pane + live SectionFormat document
panel (Claude-artifacts style). Sibling project to Claude-Spec-Critic; this
file is the working reference for AI-assisted development sessions.

## Ground rules

- Python 3.11+, FastAPI backend, React 18 + TypeScript + Tailwind v4 frontend,
  pywebview native shell. Windows is the primary target platform.
- Tests are hermetic: no network, no real API key. `tests/conftest.py` injects
  a placeholder `ANTHROPIC_API_KEY`; anything touching the API monkeypatches
  `backend.llm.conversation.get_client` with a fake streaming client.
  Every request a fake in `tests/fakes.py` receives (streamed, counted,
  batched, or through a subclass override) is checked by
  `request_shape_problems` against the documented per-model 400s; a refused
  shape raises the API's `BadRequestError` and `conftest.py` fails the test
  even if the code under test swallowed it. When a model's request rules
  change, extend that oracle from Anthropic's documentation with literal
  model ids — never from the app's own capability tables.
  Four canaries and the compaction evaluator below are the only explicit
  paid exceptions. The first two canaries are
  each a single low-token request. `tools/qc_verifier_canary.py --run`
  checks that the provider accepts the strict QC verifier schema; it never
  runs a full Final QC. `tools/fetch_elision_canary.py --run` checks that the
  provider accepts a saved chat history whose fetched page text was trimmed
  to a note carrying the passage a reply quoted (compaction plan Phase 2; it
  passed on 2026-09-23, and the trim has been on by default since). Its
  optional `--control` run is one more request, made only on demand. The
  third, `tools/prompt55_progress_update_canary.py --run` (the 5.5 prompting
  upgrade, P55-2), runs ONE real interview turn through the production
  engine — typically two or three requests, each re-sent with
  `thinking.display: "updates"` (beta `thinking-display-updates-2026-08-18`)
  and `max_tokens` capped — and checks that the reply lands after the last
  tool call as closing text that asks the questions, with no question left
  in a progress note. The fourth, `tools/qc_thinking_binding_canary.py --run`,
  sends at most three bounded streaming requests: mint a genuine thinking
  signature, replay unchanged as a control, then replay an edited prefix
  with production `with_drop_block`. The control must report no drops and
  the edit must report the expected prefix-mismatch drops; SDK retries are
  disabled. It does not fetch a live PDF or test Batches enforcement, and
  its live result is unrun. Only Abraham runs any of them; no session does.
  Without `--run`, no canary sends anything.
  `tools/compaction_json_eval.py --run` is a separate paid experiment,
  also owner-only: up to ten local saved sessions, each with one billed
  zero-output cache warm and two summaries (at most 30 requests total).
  SDK retries/display fallbacks are disabled. Without `--run` it only
  prepares a plan; `--assess` is offline. No session runs its paid mode.
  Its private review files stay local, and even passing evidence never
  adopts JSON automatically. Live comparison remains unrun.
- Reused Spec Critic code is **copied in and adapted**, never imported across
  repos. When porting a file, keep its design and docstring posture, update
  identity strings (BuildASpec / BUILD_A_SPEC_*), and note the provenance in
  the module docstring.
- Frozen decisions (2026-07-21, confirmed with Abraham): pywebview+React+FastAPI
  UI; copy-based reuse; first module = hyperscale fire suppression Div 21;
  research agents land right after the core drafting loop works.
- NFPA 13 default edition is **2025** (current edition). Jurisdiction-adopted
  earlier editions override when known — never silently, always with the
  adoption basis stated. "Stated" means recorded in the app
  (`set_standard_edition`'s basis) and said in chat — never written into the
  specification text. This mirrors Spec Critic's pinned-edition philosophy
  (`code_cycles.StandardEdition`), which will be ported in Phase 3.
- The specification carries specification text and nothing else (owner
  rule, 2026-10-06): no `[TBD]` or other placeholders, no notes or reminders
  addressed to the user, no explanation of why a requirement applies. An
  unknown is written around, stamped `assumed`, and asked about with
  `track_followups`. See "The specification gives directions, never notes"
  below.
- Keep `README.md`, `requirements.txt`, and this file current when the
  implementation, dependencies, or conventions change.
- New as-built notes are appended to `docs/as-built.md`, not to CLAUDE.md.
- Commit style is a standing owner preference (Abraham): commit messages are
  sassy, spicy, and funny where warranted — especially when something fought
  back — never obnoxious.

## Event protocol (SSE, `POST /api/chat`)

Each frame is `data: <json>\n\n`. Event types:

| type | payload | meaning |
|---|---|---|
| `status` | `kind`, `round?`, `progress_chars?` | transient liveness hint (Batch 2): `working`/`thinking`/`writing`/`drafting`/`searching`/`fetching` (+ `condensing` while the backstop writes or waits for a summary before round 0 — compaction Phase 3). Replaces the current status strip; cleared by the next `text_delta`/`thinking_delta`. NOT persisted to history/traces/project files |
| `text_delta` | `text` | streamed assistant text chunk (all continuation rounds) |
| `thinking_delta` | `text` | streamed adaptive-thinking summary chunk (Batch 2; only when `THINKING_DISPLAY=summarized` and the model streams it). Rendered in a collapsible block; transient, never persisted |
| `web_search` | `query` | the model ran a server-side web search this round — emitted LIVE (Batch 2) the instant the server-tool block's input completes, not derived post-hoc |
| `web_fetch` | `url` | the model fetched a page/document server-side this round — emitted live on the block's completion |
| `figure` | `figure` | the model created a figure (diagram/schematic/table) via `create_figure` this round — the full serialized `Figure` for inline chat rendering + downloads (Batch 8). Emitted live on the tool dispatch. Source is client-sanitized before render; it lives only in the figure store, never in history/traces/the re-billed doc context |
| `suggested_prompts` | `prompts` | the model staged up to 5 one-tap reply chips (Batch 8→9), shown above the composer. Since 2026-10-06 they ride a `<suggested_replies>[…JSON array…]</suggested_replies>` block at the END of the closing message, not the retired `suggest_prompts` tool: the relay (`_stream_events` + `suggestions.ReplyChipFilter`) holds the markup back from `text_delta`, and emits this event when the block closes and passes `validate_prompts` (a malformed block = no event, logged). Latest-only, committed turn-atomically: a committed turn REPLACES the session's set with what it staged (no block = clear, which is the wind-down; an unclosed block from a stop or `max_tokens` stages nothing, so the bar clears; a failed turn keeps the prior set). A complete block stays verbatim in the committed reply text (how the model sees its last chips); `chat_transcript` and recall strip it |
| `followups` | `followups` | the model raised or settled tracked items via `track_followups` this round (v1.16.0) — the full "Waiting on you" list, emitted live on the tool dispatch. ACCUMULATING, not latest-only: the store persists across turns, so silence means nothing changed rather than "clear". Turn-atomic through the store's own begin/commit/rollback |
| `project_facts` | `project_facts` | the model recorded or superseded established project facts via `record_project_facts` this round (v1.17.0) — the full ledger snapshot, emitted live on the tool dispatch. Same accumulating, turn-atomic posture as `followups`; the store also persists into the project file and rides a project brief into the next section |
| `compaction` | `compaction` | the view this turn sends carries a summary of the oldest turns (compaction Phase 3): `{covers_turns, created_at, tokens_before, tokens_after, trigger, summary_chars}` — never the text (`GET /api/chat/compaction` returns it). Emitted at turn start, after `_prepare_turn_view`, whenever the turn's view has a record — including one adopted or written at that moment — so the chat's divider moves at once. Not persisted; the doc payload's `compaction` re-syncs it, and a summary adopted after a turn's stream has closed reaches the chat through the payload's `compaction_pending` + `GET /api/chat/compaction/status` |
| `qc_dispositions` | `outcomes` | apply_qc_fixes committed audit dispositions with this turn (v1.11.0): `{finding_id: applied\|stale\|no_ops\|already_applied\|not_open\|unknown}`. Emitted from the frozen post-commit payload ONLY when the turn commits with staged dispositions — a rolled-back turn never emits it; the frontend refreshes QC state + readiness on it |
| `doc_patch` | `ops`, `doc` | an applied edit batch: ops echo server-assigned element ids (highlighting); `doc` is the authoritative full snapshot (rendering) |
| `doc_snapshot` | `doc` | committed tree after a doc-changing turn — mid-turn patches carry a pre-commit version pointer; this one is current |
| `open_questions` | `items` | open-item list (TBD markers + needs_input blocks — leftovers only since 2026-10-06: the model can write neither); emitted when a turn changed the doc |
| `lint` | `items`, `standards` | advisory lint issues + the editions in effect (pins + overrides); emitted right after `open_questions` when a turn changed the doc |
| `turn_complete` | `stop_reason`, `usage` | turn ended; history + doc version committed server-side. `usage` aggregates the turn's billed tokens across every round (input/output/cache/thinking + web-tool request counts) — raw material for the future cost meter. A turn stopped mid-stream adds `estimated_output_tokens` + `usage_estimated: true` (see "Disclosed stopped-turn output estimate"); `output_tokens` stays exactly what the provider reported |
| `error` | `message` | turn failed; history untouched and doc rolled back (retry is safe) |

The frontend switch in `App.tsx#send` is the single place events dispatch.
Snapshots outside a turn travel over REST, not SSE: `GET /api/doc`,
`POST /api/doc/undo|redo`, and `POST /api/project/load` all return
`{doc, open_questions, lint, standards, profile_complete, research_status,
baseline_index, figures, suggested_prompts, followups, project_facts,
project_link}` (load adds `chat`, the rebuilt
transcript; `baseline_index` is the imported-master version for the redline
picker; `suggested_prompts` re-syncs the reply-chip bar, incl. restore-on-error). Patches and snapshots
always carry the full tree — the frontend never applies ops itself. The
Batch 3 full-draft pass adds NO SSE event: `POST /api/draft/full` returns
`{ok, ready, missing, message}` over REST (409 while a turn or research runs)
and the frontend sends `message` straight back through `POST /api/chat`, so
the pass is an ordinary turn on the one streaming path. `ready` is false —
still a 200 — when a draft prerequisite is unrecorded, and `message` is then
the directive that COLLECTS it rather than the one that drafts (see "The full
draft never drafts blind" below).

The Batch 5 redline/compare surface is REST-only, adds NO SSE event: `GET
/api/doc/diff?base=N[&cur=M]` returns a serialized `SectionDiff`
(`{ok, elements, status_changes, stats, base_index, cur_index,
baseline_index}`; 400 out-of-range or base==cur), and `GET
/api/export/docx?redline=master|version&base=N` streams a tracked-changes
`.docx` (400 when `redline=master` and no baseline; filename gains
` - REDLINE`). The clean `GET /api/export/docx` is byte-identical to before.

Onboarding is frontend-only and adds no REST or SSE surface. It is a passive
overlay over the current project and never sends chat, edit, research, or QC
requests.

Research has its own channel (a run outlives any one chat turn):
`POST /api/research/start` (400 incomplete profile / no key; 409 while
running; optional `scope: "all"|"gaps"` — see "Scoped research rounds"
below, 400 on an unknown scope or a `gaps` round with nothing to retry),
`GET /api/research/status` (snapshot: status/error/events/
profile view + a `coverage` block joined in `app.py`), and
`GET /api/research/stream` — an SSE stream that replays
the run's event log from seq 0 and follows until terminal, closing with a
`stream_end` sentinel. Coordinator/runner event types: `research_started`
(`dimension_titles: {id: title}` beside the `dimensions` id list — which on
a scoped round rosters only the dimensions that run — plus
`declared_dimension_count`, what the module declares, so the board can say
"2 of 4 areas" without a second fetch),
`dimension_complete`, `dimension_failed`, `research_complete`,
`research_failed`. The live-visibility batch adds WORKER events, emitted by
each dimension thread as it works (all carry `dimension_id`; they
interleave freely across dimensions, but a dimension's terminal event
always follows its own live ones): `dimension_started` {title,
max_searches, max_fetches}, `dimension_activity` {kind: thinking|searching|
fetching|writing, on change only}, `dimension_search` {query} /
`dimension_fetch` {url} (detected live from the raw stream, chat-loop
style), and `dimension_retry` {attempt, max_attempts, reason, backoff_s,
mode: resume|restart (Research/QC cost Tier 1, Chunk 5)}. A staggered
launch adds one COORDINATOR event per follower, `dimension_waiting`
{title, lead_id, max_wait_s}, emitted when its lead is sent and always
before the follower's own `dimension_started`, which a follower emits only
once released (see "The research launch is staggered" below).
The `stream_end` sentinel is still exactly `{type, status}` — `status` may
now be `superseded` when a NEWER run takes the runner over mid-stream
(`sse_events` binds to the run token at call time, the QC shape). Every
event carries the 1-based `round` it belongs to;
`research_complete` reports the CUMULATIVE `item_count`/`grounded_count`
plus that round's own counts, and every round record carries `section` —
the section number of the session that ran it (v1.17.0; serialized only
when set, so a legacy profile's bytes are untouched). The status payload's
`coverage` block also carries `carried_from` / `carried_rounds` for a
section seeded from a project brief. `research_complete` reports
plus that round's own `round_item_count`/`new_item_count`/
`repeat_item_count`. The event log is per-round (cleared at each start —
the accumulated knowledge is in the profile, not the log), and the
snapshot's `profile` gains `rounds[]` plus per-item `research_date` /
`round_index`. Starting a round does NOT clear the previous profile:
pressing Research again appends (see "Research rounds" below). The
frontend follows the stream by MERGING event payloads into local state by
`seq` (replay-safe) and refetching the authoritative snapshot only on
milestone events — refetching per frame was O(frames × payload) once the
log turned chatty. `ResearchDrawer` folds `events` into a live per-agent
board (see "Live research visibility" below).

Final QC (Batch 4) has the same channel shape (a QC run also outlives a
chat turn): `POST /api/qc/start` accepts optional
`{acknowledge_scope_mismatch: boolean}` (400 empty draft / no key; 409 while a
turn streams or QC runs; `module_section_mismatch` 409 with the compatibility
object when a curated closed-catalog mismatch is not acknowledged — research
is NOT required), `GET /api/qc/status` (snapshot: status/error/events/result
view plus `module_section_compatibility`), `GET /api/qc/stream` (replay +
follow + `stream_end`; event types `qc_started`, `lens_complete`,
`lens_failed`, `consolidation_started`, `consolidation_complete`
{status, raw/grouped/panels_avoided counts}, `verify_progress` {done,total},
`qc_complete`, `qc_failed`),
`POST /api/qc/apply` (`{finding_ids}` → one undoable version; per-finding
`applied`/`stale`/`no_ops`/`not_open`/`unknown` outcomes; duplicate ids and
identical operations are deduplicated; different operations claiming the same
deterministic write key return a structured 409 before any mutation; 409 while
a turn or QC run is active),
`POST /api/qc/dismiss` (`{finding_id, reason}` with a required nonblank audit
rationale → remembered by
content-addressed id across re-runs; 409 while QC runs),
`GET /api/qc/export` (the full standalone Final QC Word report, including the
selected report/latest-attempt state and any distinct retained-success
identity), and `GET /api/qc/export.json` (the lossless
machine-readable audit envelope: canonical `report` plus a generated
`current_state` containing current document/input identity, runner and latest
attempt state, full-input staleness, and readiness; a different retained last
success is included separately). `GET /api/readiness` is a
deterministic checklist (no model call):
`{checks: [{id, ok, detail, advisory}], ready}` — `ready` = all non-advisory
checks ok (no open items, no unreviewed imported/assumed, lint clean, research
complete — which since Chunk 3.2 means every REQUIRED module dimension has
cumulatively completed, not merely that the runner said `complete`; see
"Required research coverage gates readiness" — `qc_current`
exact-input/latest-attempt identity,
`qc_audit_complete` current schema/protocol plus complete lens/verifier
coverage and no open criticals; `profile_complete` is advisory). Any
failed/missing lens or verifier seat makes the QC result partial and blocks
readiness even when the available verifier votes reach a majority.

`POST /api/research/stop` / `POST /api/qc/stop` (Batch 7) stop a running
fan-out. Research stop remains lossy; QC preserves the latest attempt's
identity/error and any terminal partial record made available by the engine.
`ResearchRunner.stop()` / `QCRunner.stop()` resolve the run
as `failed` immediately via a lock-guarded compare-and-set
(`_try_resolve`), so the UI never waits on the background thread, and set a
per-run `cancel_event` the engine's `should_stop` callback polls before each
retry/continuation — work that hasn't started its next network call yet
bails without spending anything; a call already in flight finishes
naturally but its outcome is discarded (`_try_resolve` finds the status
already resolved and does nothing). 409 when nothing is running.

## Conversation engine invariants

- Turn atomicity spans both stores: history mutates and the document turn
  commits (one undo snapshot per changed turn) only after a fully
  successful turn — user message, every assistant message, and every
  tool_result appended together. Every failure path (including tool-round
  exhaustion, capped at `MAX_TOOL_ROUNDS`) yields one `error` event, rolls
  the document back to its pre-turn tree, and leaves history unchanged so
  resend never duplicates. Rollback lives in a `finally`, so it also
  covers `GeneratorExit` when the SSE client disconnects mid-stream (and
  `begin_turn` self-heals from an abandoned backup). A truncated response
  (`max_tokens`) strips unexecuted `tool_use` blocks before commit — a
  dangling tool call would invalidate every later request.
- **Server-tool calls are paired turn-wide, and unpaired ones never
  commit.** A `server_tool_use` block whose result never arrived is as
  invalid on the wire as a dangling client `tool_use`, and the truncation
  filter above only ever removed the client kind — so stopping while the UI
  said "Searching the web…" wrote one into history, and from there into the
  saved project, making **every** later request in that project a 400.
  `llm/server_tool_pairing.without_unpaired_server_tool_uses` is the one
  helper, applied at four boundaries: the mid-stream `user_stop`/`max_tokens`
  truncation, the between-round stop, `_committed_messages` (the final
  invariant guard over the whole turn), and — for files already written by a
  pre-fix build — project load plus `sanitize_messages_for_resend`. Three
  rules make it safe: pairing is computed across **every message it is
  given**, because a `pause_turn` legitimately splits a use from its result
  across two assistant messages; a use counts as paired when *anything*
  references its id, so an unrecognized or error-shaped result family can
  never make a completed call look dangling; and only blocks whose type ends
  in `_tool_result` are eligible to be dropped as orphans, so unknown blocks
  and citations are left alone. It is copy-on-write and does not rebuild
  surviving blocks — research and QC re-send `response.content` as SDK
  objects, and the pause contract says verbatim. **The outgoing-request
  boundary passes `protect_trailing_assistant=True`, and must.** A
  `pause_turn` pauses *because* a server tool has not finished, so that
  message's trailing `server_tool_use` is legitimately result-less and is
  the block the provider resumes from — scrubbing it deletes the resume
  signal and aborts the work the continuation exists to finish (caught in
  review on PR #91; the fixtures hid it because every scripted pause
  either had complete pairs or no `server_tool_use` at all). The exemption
  is narrow: a trailing assistant message only occurs while resuming a
  pause, and everything earlier is still checked, which is what catches a
  poisoned history. The load-boundary repair
  logs to `buildaspec.project` (never silent) and does **not** rewrite the
  user's file until they next save; it uses `logging` rather than
  `capture.app_event` because `load_project` runs inside
  `session_state_guard()` and `app_event`'s lazy first-call file I/O must
  not happen under that lock.
- **User stop is the one deliberate exception to "every failure rolls back"
  (Batch 7).** `POST /api/chat/stop` sets `SessionState.stop_requested`
  (a `threading.Event`, cleared at the start of every turn); the round loop
  checks it after every streamed event (not just between rounds) and, when
  set, closes the request immediately via `stream.current_message_snapshot`
  rather than `stream.get_final_message()` — the latter would drain the rest
  of the network stream, defeating an "immediate" stop. This is **not**
  treated as a failure: it takes the SAME truncation branch as a
  `max_tokens` cutoff (strip dangling `tool_use`, keep the text) and falls
  through to the normal commit, so whatever text/edits landed before the
  click survive, same as Claude.ai's stop button. The one extra guard: if
  the stop lands between rounds (e.g. right after a tool dispatch, before
  the model has replied to the `tool_result`) the message list doesn't yet
  end on an assistant turn, so a placeholder assistant message
  (`"[Generation stopped by user.]"`) is appended first — otherwise the next
  turn's user message would sit right after another user-role message
  (the dangling `tool_result`), which the API rejects.
- `SessionState.generation` increments on reset and project load; an
  in-flight turn checks it before each round, each tool dispatch, and the
  final commit, so a zombie turn discards itself instead of polluting the
  fresh/loaded session ("New session" is also disabled in the UI while a
  turn streams).
- **Context architecture ("Sonnet unleashed", 2026-07-21; C1, 2026-10-06).**
  The system prompt is ONLY the stable module block
  (`render_system_prompt`, deterministic per module, `cache_control:
  ephemeral`). When there is any, the **project block**
  (`_project_block_text`, framed `=== PROJECT BACKGROUND ===`, its own
  breakpoint) opens the request's FIRST USER message: the slow-changing
  material — the research profile, the PROJECT SECTIONS list, the project
  description and template note — whose inputs change only through rare
  actions outside a chat turn. It is user-role on purpose and must stay so:
  findings summarize retrieved pages and the description is the user's, so a
  system block would hand injected text the operator's authority (Codex
  review on PR #270). Everything that changes turn to turn — the
  date, standards editions in effect, established facts, the **full
  document text** (`outline(doc, max_text=None)`, with ◆source chips), the
  lint report, open items, the Final QC review, figure and reference stubs —
  renders into a PROJECT CONTEXT block spliced ahead of the user's text in
  the **newest user message** (`_turn_context_text`). Both render together
  at turn start (`_turn_context`) and are frozen for every round of the
  turn; `_ChatRequestInputs.project_block` carries the project block, and
  `_with_project_block` inserts it into a per-request copy of the first
  message (never into history, never moving a message index). Two more
  cache breakpoints ride each request's messages (`_with_cache_breakpoints`,
  copy-on-write — stored history never carries `cache_control`): the
  **committed-history boundary** and the **tail** — four in all, the
  provider's limit. The boundary is what makes caching roll across turns;
  the tail alone cannot (see "Rolling chat cache breakpoint" in
  docs/as-built.md). TTLs are NON-INCREASING across the request: the module
  block, the project block and the boundary carry `settings.CHAT_CACHE_TTL`,
  the tail
  the shortest supported (`CHAT_TAIL_CACHE_TTL`) because its entry cannot
  outlive its own turn. A SHORT-before-LONG request is a nonretryable 400,
  which the pin makes unbuildable (and `tests/fakes.py` now refuses, with a
  fifth breakpoint). Nothing session-varying may render into the module
  block (pinned by `test_stable_system_prompt_is_cached_and_module_rendered`
  and `test_a_completed_research_round_changes_only_the_project_block`);
  nothing per-turn — no timestamp, no counter — may render into the project
  block, and nothing a chat turn's own tools change may either (see
  `CACHED_CONTEXT_BLOCKS` for what moved and why). A changed project block
  rewrites the whole committed history once at the long TTL: that is the
  accepted trade. The compaction summary forks the chat request, so it
  carries the same project block (`_CompactionInputs.project_block`).
- **Strip at commit** (`_committed_messages`): the context block is
  replaced by the user's bare text (exactly one current state block per
  request, never a stale one — pinned by
  `test_context_block_never_fossilizes_into_history`; the project block is
  never in history at all), thinking blocks
  drop (only required within their own turn), and fetched-PDF payloads
  are elided wholesale (`elide_all_pdf_sources` — a PDF left in history
  would be re-billed forever and balloon the project file). Server-tool
  blocks (search results, citations) stay. A reply's complete
  `<suggested_replies>` block stays too (the model's view of its last chips);
  an UNCLOSED one is cut, and a text block it empties is dropped
  (`strip_unclosed_reply_chips` — the API refuses whitespace-only text).
- **Adaptive thinking** is stated explicitly (`thinking: {type:
  "adaptive"}` + `output_config: {effort: settings.INTERVIEW_EFFORT}`,
  default `medium`; research runs `RESEARCH_EFFORT`, default `medium`.
  Sonnet 5.5 recalibrated its effort scale; Anthropic's migration guidance
  starts multistep tool use at `medium`. Whole-section drafting/adaptation
  still uses `DRAFT_PASS_EFFORT`, default `high`.
  Thinking blocks are preserved **verbatim** across continuation rounds —
  the API requires them during tool use; `_serialize` round-trips every
  block type exactly (SDK `model_dump`, `vars()` for test fakes).
- The tool loop in `stream_user_turn` follows Spec Critic's streaming
  continuation pattern (`requirements_research.py`): stream → on
  `tool_use`, apply edits + emit `doc_patch` + send tool_result → stream
  again; on **`pause_turn`** (long server-tool work: the interview now
  carries `web_search`/`web_fetch` with static config — per-tool
  `user_location` would bust the cached prefix), re-send the assistant
  content verbatim and stream again, no synthetic user turn.
  `sanitize_messages_for_resend` guards every request against the inbound
  PDF page limit. An invalid edit batch becomes an `is_error` tool_result
  (with the current outline) for the model to self-correct — never a turn
  failure. `MAX_TOOL_ROUNDS` (50) is a runaway circuit breaker, not a
  quality limit — no legitimate turn approaches it.
- Document edits are transactional per batch (`spec_doc.apply_edits` works
  on a copy, swaps on success). Element ids come from monotonic per-parent
  counters and are never reused; display numbering (1.1 / A. / 1. / a. /
  1)) derives from position at serialization time. A new edit after undo
  truncates the redo tail, so ids can't collide with an abandoned future.

## Commands

```
.venv/bin/python -m pytest -q          # backend suite (Windows: .\.venv\Scripts\python)
.venv/bin/python -m ruff check .       # lint gate: pyflakes + bugbear + syntax (ruff.toml); CI runs it before pytest
cd frontend && npm test                # node --test: the capability/tour contract + units
cd frontend && npm run dev             # UI hot reload (with BUILD_A_SPEC_DEV=1 backend)
cd frontend && npm run build           # tsc --noEmit && vite build -> dist/
python main.py                         # run the app (serves dist/)
```

`npm test` is not optional after touching UI: it is what enforces the
capability-coverage contract (`frontend/tests/tour.test.ts`). A new control
without a `data-capability`, a capability without a tour step, or a step
anchor with no matching `data-tour` all fail there. CI runs it (ci.yml's
Frontend build job, before the build), so a gap fails the PR rather than
shipping — but find out locally, not from a red check. The workflow pins
**Node 22**: `npm test` runs `node --test` directly over the `.ts` test files
and depends on type stripping, which Node 20 cannot do.

A Windows command written into the docs has to run as written in
PowerShell, the owner's terminal, and in Command Prompt. Put `.\` in front
of any relative program path (`.\.venv\Scripts\python`), write one command
per line with no `^` continuation and no `&&`, and show
`$env:NAME = "value"` beside `set NAME=value`.
`tests/test_docs_consistency.py::test_the_docs_windows_commands_run_in_powershell`
pins the `.\` and `^` rules.

## Research effort on Sonnet 5.5 — implemented notes (2026-10-05)

`RESEARCH_EFFORT` now defaults to `medium`, following Sonnet 5.5's
recalibrated scale and Anthropic's migration guidance to start multistep
tool use at `medium`. The old `high` default was chosen for Sonnet 5.
Research fans out four dimension conversations, each with up to 16
continuations after its opening request; thinking bills as output at
Sonnet 5.5's $10/M rate on every request. Set
`BUILD_A_SPEC_RESEARCH_EFFORT=high` before starting the app to restore
the previous behavior.

The engine consumes the setting only in `output_config.effort`. The QC
input manifest records research findings and QC's own effort settings;
it does not record research effort. `cost_checks` uses reported usage,
pricing and cache switches, never this setting. Research's system/tool/shared
brief breakpoints and automatic five-minute continuation tail keep the same
layout. Changing effort can invalidate the provider's messages cache; an
unchanged layout does not promise unchanged cache hits. The settings pin
still reads defaults from source with `ast`, independent of local overrides.

**Adaptive thinking erratum.** The earlier conversation-engine invariant
said interview and research both defaulted to `high`. The interview has
defaulted to `medium` since 2026-09-29; research now does too. Final QC
also defaults to `medium`; the two whole-section drafting/adaptation
passes keep `DRAFT_PASS_EFFORT=high`. The invariant above is corrected.

No paid API calls or live quality/cost comparison were run. The owner can
run `tools/research_cost_profile.py` on saved projects before and after
the change to compare billed output, total cost and cache usage. Thinking
is included in billed output; actual savings remain unmeasured.

**Release-note draft for the next release:** “Research thinks at medium
effort by default, following Claude Sonnet 5.5's updated effort scale.
Set `BUILD_A_SPEC_RESEARCH_EFFORT=high` before starting the app to restore
the previous thinking depth.” On 2026-10-05 the GitHub Releases API
confirmed `v1.22.1` is already published; its entry is frozen. There is
no newer entry in this checkout. Keep this draft for the later release;
the app version remains `1.22.1`.

## Research's final submission is shaped per model — implemented notes (2026-10-05)

The final submission a research area sends after a guard trips (search,
fetch, continuation, reminder or context reserve; added 2026-10-04, after
`v1.22.1`, so never shipped) hard-coded `thinking: {"type": "disabled"}`
and a forced `tool_choice`. Sonnet 5.5 — the research default — rejects
both with HTTP 400, which is non-retryable, so every area that tripped a
guard failed at the finish line after its spend was billed. The suite
missed it because the fakes accepted any dict and every test ran Sonnet 5.

- **Thinking** comes from `research.schema.lowest_thinking(model, effort)`:
  `between_tools` on Sonnet 5.5 at effort `high` or below (alone in its
  dict, no beta header); `disabled` on Sonnet 5, Opus 4.8, and Opus 5 at
  `high` or below; otherwise `adaptive`. Thinking turned off still runs
  `budget.without_thinking` (notes become text, signatures are omitted).
  Adaptive (Opus 5.5, Fable, unknown overrides, Sonnet 5.5 above `high`)
  replays every thinking block unchanged and always carries
  `with_drop_block`, because removing the web tools edits the bound prefix.
- **Tool choice** comes from `single_output_tool_kwargs`: forced, with
  `disable_parallel_tool_use`, on Sonnet 5, Opus 5 and Fable 5 only.
  Elsewhere the request carries no `tool_choice`. Automatic choice does
  not guarantee a call, so a completed reply with neither the tool call nor
  the tagged-JSON fallback gets `_SUBMISSION_RESENDS` (1) more append-only
  submission request, then fails as before. Pause/refusal/truncation stay
  terminal; a forced submission gets no resend.
- Unchanged: the single output tool, the 32k cap, the beta-header removal
  (re-added only by `with_drop_block`), the context-fit elision loop, the
  budget failure, and transport retries resuming the submission.

The full record, release-note draft and reversion evidence are in
`docs/as-built.md` under the same heading. No paid API call was made.

## Research web tools keep their bytes — implemented notes (2026-10-05)

PR #262 (after `v1.22.1`, never shipped) rebuilt research's web tools before
every request with `max_uses` set to the remaining allowance. Tools lead the
cached prefix, so after an area's first fetch every continuation rewrote the
whole conversation at 1.25× instead of reading it at 0.1×. The continuation
tail could not pay off, and each change set `thinking_edited`.

- Every request of an area's conversation now declares the same tools:
  `RESEARCH_SEARCHES_PER_REQUEST = 8`, `RESEARCH_FETCHES_PER_REQUEST = 4`
  (`min` with the declared budget). That covers the opening, continuations,
  reminders, a resumed request and a restart's opening. The cumulative
  ceilings (2× searches, the declared fetches, 16 continuations) are checked
  between requests and request the submission. The crossing request may
  overshoot by its allowance less one; that is the documented trade.
- The context clip is one-way. When the full allowance no longer fits, the
  conversation switches once to `near_window_tools` (fetch `max_uses: 1`)
  and never back; when one fetch no longer fits, it submits. Only that
  switch, made after a response, and sanitizer edits set `thinking_edited`.
  Spending the allowance never does.
- All four areas share identical tool bytes and are byte-identical up to the
  shared block. The launch is still parallel; staggering it is a later
  change.
- The full declared budget per request was rejected: it overshoots by a whole
  budget, and its 800k context reserve would trip the clip at once.

Never make a research request's tool bytes depend on what the conversation
has spent. `tests/test_research_budget.py` captures tool bytes at send time
and pins them; `frontend/tests/verificationCopy.test.ts` pins the dossier's
and README's numbers to the constants. Full record, reversion evidence and
the release-note draft are in `docs/as-built.md` under the same heading. No
paid API call was made.

## The project background rides its own cache breakpoint — implemented notes (2026-10-06)

C1. Every chat turn rendered the research profile (up to 100k estimated
tokens) into the PROJECT CONTEXT, which commit strips, so the tail breakpoint
wrote it at 1.25× on every turn and no later turn read it.

- The request is now: module block (1h) → project block (1h) → committed
  boundary (1h) → tail (5m). The project block is the first content block of
  the request's first user message (`_with_project_block`), framed
  `=== PROJECT BACKGROUND ===`, and is sent only when it has content; an
  empty one leaves the request byte-identical to before. It was a second
  system block until the Codex review on PR #270 pointed out that this gave
  retrieved and user-authored text the system prompt's authority; the
  user-role placement caches identically.
- What moved (`CACHED_CONTEXT_BLOCKS` plus the `other` slice): the research
  profile and PROJECT SECTIONS, plus the session-fixed project description
  and template note. What stayed per-turn, deliberately: standards editions
  (the model records them with `set_standard_edition` during turns; undo
  reverts them), established facts (`record_project_facts` during turns),
  reference stubs (tiny; an attach would rewrite the history to save a few
  dozen tokens), and the date, document, lint, open items, waiting-on-you
  list and QC review.
- Each project-block change rewrites the whole committed history once at
  the long TTL. Background compaction carries the committed turn's frozen
  block; the backstop carries the current turn's block.
- `CONTEXT_SIZE_KEYS` gains `project_block` (a subtotal of `total`, not a
  slice); `total` now covers both blocks. Developer tools marks cached
  blocks, and `prompt_refs` gains a `project_block` ref beside `system`.
- `compaction.CONTEXT_BOUNDARY_PATTERN` also escapes `PROJECT BACKGROUND`
  markers. The stable prompt says where moved blocks live; the compaction
  preface names both current blocks. Changing the stable prompt rewrites
  every open session's cache once after upgrade.
- `tests/fakes.py` refuses a fifth breakpoint or a 1h-after-5m order.
  `tests/test_app.py` pins the layout with a small prompt-cache model
  (`_simulated_cache_usage`).

Full record, economics, reversion evidence and the release-note draft:
`docs/as-built.md` under the same heading. No paid API call was made.

## The research launch is staggered — implemented notes (2026-10-06)

Since PR #269 a round's areas are byte-identical up to the shared block's
breakpoint, but four requests sent together each wrote that entry: a cache
entry is readable only once its response begins streaming. The fan-out now
submits one lead per lineage first (`research.engine._launch_staggered`,
copy-adapted from Final QC's) and the rest when the lead's `first_output`
fires — first non-`message_start` frame, end of any request, any failed
attempt (a failed token count included), or the task's end via a
done-callback — or after `RESEARCH_WARM_WAIT_SECONDS` (default 45, `0` =
off), or on a Stop (followers are then submitted, see the Stop before
sending, and return cancelled). The wait runs on the coordinator thread in
1-second slices.

- Its own knob, not `QC_WARM_WAIT_SECONDS`, so either stagger switches off
  alone. Pinned once per round; the runner passes nothing new.
- The lineage key hashes what precedes the breakpoint (tools, system
  prompt, shared block, model, effort) from the request builders
  (`_research_tools`, `_per_request_allowance`), so an area with different
  tool bytes is its own lineage and never waits. A key that cannot be built
  makes that area unstaggered, never a failed round.
- Events stay truthful: a follower gets `dimension_waiting` when its lead is
  sent, and its `dimension_started` comes from its own worker only once
  released. The board shows "Waiting for {lead} to start, to share its
  cached copy…" (`lib/researchAgents.queuedLabel`).
- No cost self-check: the stagger sends no extra request and changes no
  byte, so it cannot lose money; Final QC's lens stagger has none either.
- Request bytes, budgets, grounding, the merge and the submission are
  unchanged (pinned by `tests/test_research_warm_launch.py`).

Full record, reversion evidence and the release-note draft are in
`docs/as-built.md` under the same heading. No paid API call was made.

## Suggested replies ride the reply — implemented notes (2026-10-06)

On Sonnet 5.5 the reply must follow the last tool call (P55-2), so the
`suggest_prompts` tool cost every turn one extra full-context request whose
only news was `{"suggested": N}`. The chips now end the closing message as
`<suggested_replies>["…", "…"]</suggested_replies>`: a question-only turn is
one request, a drafting turn two.

- **One grammar, three places** (`backend/suggestions.py`). An exact,
  case-sensitive tag; complete blocks anywhere are removed, and an unclosed
  one removes everything after its opening tag. The relay's
  `ReplyChipFilter` streams exactly what `strip_reply_chips` gives
  `chat_transcript`, however the deltas split (fuzz-pinned). Commit keeps
  complete blocks and cuts only unclosed fragments.
- **Strict, uncorrectable validation.** `parse_reply_chips` = `json.loads` +
  `validate_prompts`. A failure stages nothing and is logged
  (`buildaspec.chat`), never retried: a retry would be the round this
  removes. Latest valid block in a turn wins.
- **The stop button still works mid-block.** Held text yields throttled
  `writing` status frames (`_HELD_TEXT_TICK_S`, rendered as nothing), so the
  turn loop's stop check keeps running.
- **Neutralized where frames already are:** the PROJECT CONTEXT and project
  background (`_CONTEXT_ESCAPE_PATTERN`, one alternation with the boundary
  markers, still linear), the `read_reference_doc` result (chat-side only),
  and compaction's frames. Recall and the harvest read replies without
  their chips.
- **`suggest_prompts` stays declared and retired.** The API reference does
  not establish that a history naming an undeclared tool validates, and
  saved projects carry such calls. Its description says not to call it, and
  a call gets an `is_error` naming the block. Remove it in a later release.
  The tool list's bytes changed once (the description), as did the stable
  prompt.

Never let a chip reach `text_delta`, history-derived display text, or a
cached block. The full record, reversion evidence and release-note draft are
in `docs/as-built.md` under the same heading. No paid API call was made.

## The specification gives directions, never notes — implemented notes (2026-10-06)

Owner rule (Abraham): the software never writes TBDs, reminders, or anything
addressed to the user into the specification, and provisions direct rather
than explain. Until now the engine asked for the opposite (`[TBD: …]` inline,
`needs_input` placeholder blocks). This is PR 1 of 4: the drafting rules and
the hard guard. An advisory lint and Final QC check for explanatory prose,
the separate review report replacing the export's appended schedules, and
retiring needs-input from the panel and tour follow.

- **The prompt.** `render_system_prompt` gains `_SPEC_VOICE` after
  `_PROVENANCE`, carrying the owner's three before/after examples (the
  waterflow alarm, and the NFPA 25 and FM DS 5-32 REFERENCES entries).
  `_PROVENANCE`, `_FOLLOWUP_POLICY`, `_STANDARDS_POLICY`, `_RESEARCH_POLICY`,
  `_GAP_AND_ADAPT`, `_FULL_DRAFT_POLICY`, `FULL_DRAFT_DIRECTIVE` and
  `ADAPT_IMPORTED_DIRECTIVE` stop asking for `[TBD]`/needs_input: an unknown
  is written around, stamped assumed, and asked with `track_followups`
  (`element_id` → the provision). Code citations stay ("IBC §903.4.2
  (Alarms)"); a REFERENCES entry is designation, title, and edition only.
- **The guard.** `spec_doc/spec_voice.check_drafted_edits` runs on every
  model `apply_spec_edits` batch (`_run_tool`) and on every Final QC
  finding's `proposed_ops` (`_validate_ops`, so such a fix stays advisory).
  It refuses the whole batch when new text in add_article / add_paragraph /
  replace / set_standard_edition `title` carries `[TBD…]`, a bare TBD/TBC,
  the lint's placeholder or template-marker vocabulary, "to be
  determined/confirmed/decided", a specifier/designer note, or any
  square-bracketed text containing a letter; or when a status falls outside
  `model.MODEL_STATUSES` (confirmed, assumed). The vocabulary is
  high-precision on purpose: a refusal costs a round, and explanatory prose
  is left to the prompt (and, next, the lint and QC).
- **Not guarded, deliberately.** The panel's manual edits
  (`/api/doc/edit`, `apply_doc_edits` directly): what the user types is
  theirs, including a hand-set needs_input. QC apply (`qc/apply.py`)
  re-runs `apply_edits` only — the ops were checked when the review
  validated them.
- **Bytes that changed once.** The stable prompt, `apply_spec_edits`'
  description (plus a description on its `status` property), and
  `track_followups`' description: every open session rewrites its cached
  prefix once after upgrade. The `status` enum still lists every status on
  purpose — saved histories name `needs_input` in past inputs, and the API
  reference does not establish that a past input outside today's enum
  validates (the `suggest_prompts` precedent). `standards_context_block` ends with `_BASIS_IS_NOT_DOCUMENT_TEXT`,
  and the QC manifest fingerprints that render, so a retained Final QC
  report reads stale once (as any version bump also makes it).
- **Context and lint.** The per-turn OPEN ITEMS block is now LEFTOVER
  PLACEHOLDERS, saying to rewrite them. The placeholder and template-marker
  vocabularies and `scan_markers` moved to `spec_voice` (linting aliases
  them), and the placeholder lint message no longer says "convert to a
  tracked [TBD: ...]".
- **Modules, templates, tutorial.** The hyperscale water-supply and seismic
  defaults and the generic system-criteria default write around; both
  curated starters lost their TBD and needs_input lines. The tutorial
  showcase plants its two open-item examples itself
  (`tutorial._SHOWCASE_OPEN_ITEM_EXAMPLES`) until the tour's open-item
  chapter is retired. AI template generalization asks for neutral wording,
  and its structure contract compares `has_placeholder` per paragraph
  instead of `"[TBD:" in text`.
- **Unchanged.** `open_questions`, readiness `no_open_items`, the export
  schedules, and the frontend.

Never add a model-authored path that writes document text without
`check_drafted_edits`. Tests: `tests/test_spec_voice.py`. The full record,
reversion evidence and the release-note draft are in `docs/as-built.md`
under the same heading. No paid API call was made.

## As-built history

The as-built history, with the same headings, is `docs/as-built.md`.

### Warm leads at the eight-seat floor — implemented notes

Both Final QC batch lineage minimums are 8. The cost check's OFF-only,
process-local latches are keyed by model, tool kind and size cohort (8–19
or 20+); `_run_batch_calls` filters each picked lead through its own scope.
A small loss never disables large lineages. All losing cohorts in a phase
are latched atomically; restart re-arms them. Diagnostics lists
`warm_lead.disabled_scopes`; the legacy summary's `enabled` means all
cohorts are enabled and must never be used as a process-wide selection gate.

The streamed lead retains `extra_body.fallbacks` and the
`server-side-fallback-2026-07-01` beta: Anthropic's pinned caching and
fallback references do not document the opt-in as a cache-key invalidator.
An actual fallback changes models, whose caches are separate, so a lead
whose opening response fell back is `not_warm` for this check. A fallback
on a later pause/reminder continuation does not undo the opening response's
cache entry; the report still discloses fallbacks anywhere in the call.
Batch params still carry neither fallback field nor beta. The check still
requires eight measured batched seats; at the eight-seat floor its seven
batched seats are `too_few`.
The full design, evidence links and revert matrix are appended to
`docs/as-built.md` under the same heading. No paid probe is authorized.

Errata for the historical sections now in `docs/as-built.md`:
- "Final QC's batched phase can stream a lead seat first" records the
  unmeasured minimum of 20; both minimums are now the enforced floor of 8.
- "The two shelved savings are on, and watch themselves" describes one
  warm-lead latch that prevents all subsequent leads. Latches now affect
  only their model/tool/size cohort; its eight-measured-seat rule is retained.

## Closing a fact harvest keeps its paid result — implemented notes (2026-10-05)

`HarvestDialog` stays mounted inside App's session-keyed `ArtifactPanel`;
its `open` prop controls only the `ModalShell`. Closing via X, Escape,
backdrop or Close keeps the running phase, preview token, selections and
edits. New session and project load remount the panel and discard that
state; the mount guard rejects late responses belonging to its old owner.
The synchronous phase ref and `lib/harvestLifecycle.beginHarvestRun` prevent
a duplicate paid call before React renders. The three existing doors share
running/ready hints; reopening retained work stays possible while chat is
busy or no fresh material is harvestable. A completed commit resets on
close; Discard preview is the explicit way to set an uncommitted sheet aside.

Frontend-only behavior; `project.facts-harvest` and the opt-in paid-call rule
stay intact. The API, lease middleware and preview expiry are unchanged.
`frontend/tests/harvestLifecycle.test.ts` adds pure helper tests and source
pins, registered in `frontend/package.json`. All 31 targeted reversions
produced the expected assertion failures and were restored. `npm test` and
`npm run build` passed; no paid API call was made. README and the current
release's Projects notes describe the behavior. Full history and reversion
evidence are in [docs/as-built.md](docs/as-built.md#closing-a-fact-harvest-keeps-its-paid-result--implemented-notes-2026-10-05).

PR #266 review correction: the harvest owner also stays mounted through
guided tours, which restore the original session without advancing
`sessionNonce`. Only its shell is hidden with
`open={harvestOpen && !tutorialActive}`. A tour must never key or reset the
owner: its running call, preview token and edited rows belong to the original
session. The tour regression failed before the fix; restoring conditional
mounting, dropping the shell's tour gate or adding a tour-dependent key each
made it fail again, and all three reversions were restored.
