# Batch 2 — Buttery-smooth streaming UX, direct editing, settings/key panel, cost meter

Ships as **v0.7.0**. Four work items, ordered by priority. Work item 1 is
Abraham's explicit mandate, verbatim requirement: *"we need a buttery
smooth chat experience. right now the model output is jerky as fuck.
looks ugly. we need the user to 'see' what the model is thinking and
doing while the user waits. right now the model goes quiet. i need the
user to be engaged in some way so they don't think the app froze up."*

---

## Work item 1 — Streaming experience overhaul (the headline)

### The problem, precisely

Three independent causes of "jerky and quiet", all fixable:

1. **The backend only surfaces text deltas.** `stream_user_turn` in
   `backend/llm/conversation.py` iterates `stream.text_stream`, which
   yields ONLY text. Everything else the model does is invisible until
   the round's `get_final_message()`:
   - Adaptive thinking (Sonnet 5 thinks before text and between tool
     calls) → seconds-to-minutes of dead air.
   - `apply_spec_edits` tool-call JSON streams as `input_json_delta`
     events we ignore → a 40-op batch is a long silence, then a sudden
     document explosion.
   - `web_search`/`web_fetch` server tool blocks → the `web_search` /
     `web_fetch` SSE events added in v0.6.0 fire only AFTER the round
     completes (they are derived from the final message), so the "🔍
     Searched…" chip appears when the search is already over. Useless as
     a liveness signal.
2. **The frontend renders every delta the expensive way.** `App.tsx`
   `appendToLast` mutates the message on every SSE frame, and
   `MessageBubble` re-runs `ReactMarkdown` over the ENTIRE message text
   per frame. Long messages → full markdown re-parse dozens of times per
   second → dropped frames, flicker, layout shifting. Deltas also arrive
   in network bursts, so text appears in chunks rather than flowing.
3. **Nothing animates between rounds.** Between continuation rounds
   (tool dispatch, next-request TTFT) there is no signal at all.

### Design (decided — implement as specified)

**Backend: iterate raw stream events, emit a richer SSE vocabulary.**

Replace the `for delta in stream.text_stream` loop with iteration over
the stream's event objects (`for event in stream:` on the SDK's
`MessageStream`). Map events to SSE frames as follows. `VERIFY:` exact
event type names against the installed `anthropic` SDK (>= 0.117) before
coding — probe with a scratch script; the mapping below uses the
documented raw-event names.

| Stream event | SSE frame emitted |
|---|---|
| `content_block_start`, block type `thinking` | `status` `{kind: "thinking"}` |
| `thinking` delta (only if display=summarized works, see below) | `thinking_delta` `{text}` |
| `content_block_start`, block type `text` | `status` `{kind: "writing"}` (frontend clears status on first `text_delta` anyway; this covers zero-length starts) |
| text delta | `text_delta` `{text}` (unchanged) |
| `content_block_start`, block type `tool_use`, name `apply_spec_edits` | `status` `{kind: "drafting"}` |
| `input_json` deltas for that block | throttled `status` `{kind: "drafting", progress_chars: N}` at most every 250ms (N = accumulated JSON length; frontend can show "drafting… 2.4k") |
| `content_block_start`, block type `server_tool_use`, name `web_search` | `status` `{kind: "searching"}` — then, once the block's input JSON is complete (`content_block_stop`), emit the existing `web_search` `{query}` event **live** |
| same, name `web_fetch` | `status` `{kind: "fetching"}` → live `web_fetch` `{url}` on stop |
| each continuation round begins (before the API call) | `status` `{kind: "working", round: n}` |
| `pause_turn` resume | `status` `{kind: "searching"}` (server work continues) |

Notes:
- Emit `web_search`/`web_fetch` events from the LIVE block-stop handler
  and DELETE the post-hoc `_web_activity_events` pass over the final
  message (do not double-emit). Keep the function only if repurposed for
  tests; otherwise remove.
- `status` frames are transient UI hints. They carry
  `{type: "status", kind, ...}` and the frontend replaces the current
  status; `text_delta` and `thinking_delta` clear it. They are NOT
  persisted to history, traces, or project files.
- After the event loop, `stream.get_final_message()` continues to work
  exactly as today; the commit/serialize/strip machinery from v0.6.0 is
  untouched.
- **Thinking visibility attempt:** set the interview request's thinking
  param to `{"type": "adaptive", "display": "summarized"}` behind a new
  settings knob `THINKING_DISPLAY` (`BUILD_A_SPEC_THINKING_DISPLAY`,
  default `summarized`). On Sonnet 5 the default display is `omitted`;
  the docs indicate `summarized` streams a readable thinking summary via
  thinking deltas — that stream is EXACTLY the "see what the model is
  thinking" experience Abraham wants, and billing is identical either
  way. `VERIFY:` that Sonnet 5 accepts `display: "summarized"`; if the
  API rejects it (400), degrade at runtime to omitted (catch once,
  re-request without the display key, remember for the session) and rely
  on `status {kind: "thinking"}` alone. Log which mode is active into
  the trace.

**Fakes (`tests/fakes.py`):** `_FakeStreamCtx` must become event-capable.
Give scripted turns an optional `events` list (SimpleNamespace objects
with `.type` etc. matching the SDK shapes used above); `__iter__` yields
them; keep `text_stream` derived from text events for any legacy use;
`get_final_message()` unchanged. Add builders: `thinking_start_event()`,
`thinking_delta_event(text)`, `tool_start_event(name)`,
`input_json_event(partial)`, `server_tool_start_event(name)`,
`block_stop_event(block)` — whatever shape the implementation consumes.
Update `raw_turn`/`text_turn`/`tool_turn` to synthesize a plausible
default event sequence from their content so EXISTING tests keep passing
without modification wherever possible.

**Frontend: smooth rendering pipeline.**

1. **Typewriter smoothing.** New hook `frontend/src/lib/useSmoothText.ts`:
   incoming deltas append to a ref'd buffer; a `requestAnimationFrame`
   loop drains the buffer into displayed state at an adaptive rate —
   `charsPerFrame = max(2, ceil(backlog / 30))` so it never lags the
   stream by more than ~0.5s; flush instantly on `turn_complete` /
   `error` / unmount. Respect `prefers-reduced-motion`: when set, skip
   the animation and append directly.
2. **Cheap markdown while streaming.** `MessageBubble` currently
   re-parses the whole message per frame. Split the streaming message at
   the last blank line: the **stable prefix** renders through a
   `React.memo`'d `ReactMarkdown` (re-renders only when the prefix
   grows past another blank line); the **live tail** renders as a plain
   `whitespace-pre-wrap` span with the existing caret. On stream end,
   render the full text through markdown once. This bounds markdown
   parsing to paragraph boundaries instead of every animation frame.
3. **Status strip.** New component `frontend/src/components/
   StatusStrip.tsx` rendered inside the streaming assistant bubble:
   animated shimmer line mapping `status.kind` →
   `thinking: "Thinking…"`, `searching: "Searching the web…"`,
   `fetching: "Reading a source…"`, `drafting: "Writing to the
   document…"`, `working: "Working…"`, with the pulsing-dots animation
   in `index.css`. Replaced on each status frame; hidden on first
   `text_delta`/`thinking_delta`; gone at turn end. THIS is the "app is
   not frozen" guarantee: from send to first token, something is always
   visibly alive.
4. **Thinking display.** When `thinking_delta` frames arrive, render a
   collapsible muted block above the message body ("Thinking ▸ / ▾",
   collapsed by default, auto-collapsed when real text starts, full
   summary preserved and expandable after the turn). Style it clearly
   distinct from the answer (smaller, dimmed, italic).
5. **Scroll behavior.** Auto-follow only when the user is within ~80px
   of the bottom of the chat scroller; never yank scroll while they read
   history. Set `overflow-anchor: none` on the scroller and manage the
   pin manually. Keep the changed-block highlight in the doc panel as
   is; additionally scroll the doc panel to the first changed element on
   each `doc_patch` (subtle, `scrollIntoView({block:"nearest",
   behavior:"smooth"})`).
6. **Types.** Extend `StreamEvent` in `types.ts` with `status` and
   `thinking_delta`; keep unknown-event tolerance in the `App.tsx`
   switch (default: ignore) so older/newer backends never crash the UI.

**CLAUDE.md:** update the SSE table with `status` and `thinking_delta`
rows and note their transience.

### Tests (work item 1)

- Backend: scripted event sequences produce the expected SSE frame order
  (`status thinking` → `thinking_delta` → `text_delta`…); live
  `web_search` event fires BEFORE the round's final `doc_patch`; status
  frames absent from committed history and project files;
  display-summarized fallback path (fake raises the 400 once → retried
  without display, turn succeeds).
- Frontend has no test harness — do not add one in this batch; instead
  keep all logic in pure helpers where feasible (`useSmoothText` drain
  math as a pure function with unit-testable core if trivially
  extractable, else manual QA). Manual QA checklist in the PR
  description: long drafting turn, search turn, thinking-heavy turn,
  reduced-motion, scroll-while-streaming.

---

## Work item 2 — Direct manual editing + one-click confirm

Today every change round-trips through the model. Spec writers need to
fix a word themselves and to approve `assumed` blocks without composing
chat messages. Thanks to the v0.6.0 context architecture, manual edits
need NO history surgery — the model sees the full current document in
its next turn's PROJECT CONTEXT automatically.

### Backend

1. **New op `set_status`** in `backend/spec_doc/model.py`: `{action:
   "set_status", target_id, status}` — validates target exists and
   status ∈ STATUSES; changes only the status (text, children,
   source_item_id untouched). Add to `_ACTIONS`, `_apply_one`, and the
   `APPLY_SPEC_EDITS_TOOL` schema (the model may use it too — e.g.
   gap-and-adapt confirmations without retyping text). Applied record:
   `{action, id, status}`.
2. **Turn-activity guard.** `SessionState` gains `turn_active: bool`
   set/cleared inside `stream_user_turn` (set after `begin_turn()`,
   cleared in the `finally`). Manual edits while a model turn streams
   must be rejected — a mid-turn manual edit would be swept into that
   turn's commit/rollback.
3. **Endpoint `POST /api/doc/edit`** in `backend/app.py`: body
   `{ops: [...]}` (same op vocabulary as the tool). Flow: 409 if
   `session.turn_active`; `session.doc.begin_turn()` → `apply_edits`
   → `commit_turn()` on success / `rollback_turn()` + 400 with the
   `SpecEditError` message on failure. One undo snapshot per call.
   Response: `{ok, applied, **_doc_payload(session)}`. Status semantics:
   the frontend sends `status: "confirmed"` for user-authored text edits
   (user wrote it = confirmed); the endpoint itself stays neutral and
   applies whatever ops say.
4. Generation check: capture `session.generation` at entry; if it moved
   before commit (reset raced), 409 and rollback.

### Frontend

- `SpecDocument.tsx`: hover affordances per paragraph — ✏️ opens an
  inline textarea (pre-filled, Esc cancels, Ctrl/Cmd+Enter saves →
  `replace` op with `status: "confirmed"`, preserving `source_item_id`
  by echoing it), ✓ on `assumed`/`imported` blocks → `set_status`
  `confirmed`, 🗑 → `delete` with a one-step inline confirm. Article
  titles get ✏️ (replace). All affordances disabled while `busy` (a
  chat turn is streaming) — mirror the backend guard.
- `lib/api.ts`: `editDoc(ops)` calling the endpoint; on success run the
  same `applyDocPayload` path as undo/redo. Flash the edited element via
  `changedIds`.
- Undo button already covers manual edits (they're ordinary versions).

### Tests

`tests/test_app.py` (or a new `test_manual_edit.py`): endpoint applies
replace/set_status/delete transactionally; invalid batch → 400 and
document unchanged; 409 while `turn_active` (drive a fake streaming turn
with a generator held mid-yield); undo reverts a manual edit; set_status
`assumed→confirmed` removes the block from the export's assumptions
schedule (extend the docx smoke test); set_status appears in
`APPLY_SPEC_EDITS_TOOL` schema enum.

---

## Work item 3 — Settings panel with real API-key management

Fixes Abraham's original complaint: the key banner vanishes once a key
is stored, leaving no way to view/replace/remove/test it.

### Backend (`backend/api_key_store.py` + `backend/app.py`)

1. `key_status()` in `api_key_store.py`: returns `{present: bool,
   source: "env" | "keyring" | "file" | "none", masked: "…" + last 4}`.
   Resolution mirrors `load_api_key` exactly. Never returns the key.
2. `delete_api_key()`: keyring `delete_password` (swallow errors) AND
   unlink every existing path from `api_key_paths()`. Returns which
   stores were cleared. Note: the env var cannot be cleared — callers
   must surface that.
3. Endpoints: `GET /api/key/status` → `key_status()` (+ `env_locked:
   bool` when source is env). `DELETE /api/key` → delete + fresh
   status + `reset_client_cache()`. `POST /api/key/test` → body
   optionally `{api_key}`; construct a throwaway `anthropic.Anthropic`
   with the candidate (or stored) key and make the cheapest possible
   authenticated call — `client.models.list(limit=1)` (`VERIFY:` the
   SDK models.list signature; fallback: `client.messages.count_tokens`
   with a one-word message). 200 `{ok: true}` on success; 200
   `{ok: false, error}` on auth failure (surface the API's message);
   never store as a side effect. `POST /api/key` (existing) stays; the
   frontend now tests-then-saves.

### Frontend

- `Header.tsx`: gear icon → `SettingsPanel.tsx` (modal or slide-over,
  match the warm dark theme). Sections:
  - **API key**: source label ("Windows Credential Manager" / "key
    file" / "environment variable — read-only"), masked tail, Replace
    (input + Test + Save flow: Save runs test first, saves only on
    pass, shows the API error verbatim on fail), Remove (confirm step;
    hidden when env-locked), and the same "never sent anywhere except
    Anthropic" footnote as the banner.
  - **Usage** (work item 4's meter breakdown lives here).
  - **About**: app version, current model name (from `/api/health`),
    link-style "Check for updates" that calls the existing forced update
    check.
- Keep `ApiKeyBanner` for the no-key first-run state, unchanged.

### Tests

`key_status`/`delete_api_key` unit tests with tmp config dir + fake
keyring (monkeypatch `_keyring`); endpoint tests: status masks
correctly and never leaks the key (assert full key absent from response
body), delete → `api_key_present` false in health (env var removed via
monkeypatch for that test), test endpoint success/failure paths with a
monkeypatched client factory.

---

## Work item 4 — Cost & usage meter

The v0.6.0 groundwork already aggregates per-turn billed usage into
`turn_complete.usage` and traces. Surface it.

### Backend

1. `backend/usage_ledger.py`: `UsageLedger` dataclass on `SessionState`
   — `add(category: str, usage: dict)` accumulating the union of usage
   keys per category (`interview`, `research`, `audit`; Batch 4 adds
   `qc`) plus grand totals and a turn counter. Session-scoped: reset and
   project-load clear it (decision: the meter answers "what has THIS
   session spent"; traces remain the permanent record). Not persisted in
   project files.
2. Wire-in points: `stream_user_turn`'s `finally` (spend is real even on
   failed turns); research — extend `research/engine.py` so each
   dimension aggregates `response.usage` token fields
   (input/output/cache read/cache write) into `DimensionStatus`
   (extend the dataclass + `from_dict`; extend `tests/fakes.py
   usage()` with token fields) and the runner adds the run total to the
   ledger at terminal; audit — same pattern in `compliance/runner.py`.
3. `GET /api/usage` → `{categories: {...}, totals: {...}, turns: n,
   estimated_cost_usd: {by_category, total}}`. Pricing table in
   `settings.py` as `PRICING` with a provenance comment — as of
   2026-07 (`VERIFY:` all of these against current Anthropic pricing
   before shipping): `claude-sonnet-5` $3/M input, $15/M output
   (intro $2/$10 through 2026-08-31 — use the POST-intro numbers so the
   meter never under-reports), cache read 0.1× input, cache write
   1.25× input, `claude-fable-5` $10/$50 for Batch 4, web search
   priced per-1k requests (`VERIFY:` exact rate; if unverifiable, show
   search/fetch COUNTS without dollars). Estimates are labeled
   estimates in the UI.
4. Emit a `usage` field on the existing `lint`… no — keep it simple:
   the frontend refreshes `/api/usage` on `turn_complete` and on
   research/audit terminal states.

### Frontend

- Header: compact live ticker — `≈ $0.42 this session` — clicking opens
  the settings panel's Usage section. Update on turn_complete/research/
  audit refresh. Show `—` until the first spend.
- Settings → Usage: table by category (tokens in/out, cache read/write,
  thinking tokens, searches/fetches, est. $), a "cache saved ≈ $X" line
  (computed: cache_read_tokens × (input_rate − cache_read_rate)), and a
  footnote that figures are estimates from list pricing.

### Tests

Ledger accumulation math (unit); interview turn adds to ledger via the
fake's `usage` (extend a fake turn with token usage and assert
`/api/usage`); failed turn still adds; research run rolls up dimension
usage; estimate math against the PRICING table (golden numbers);
reset clears.

---

## Also in this batch (small)

- **`.gitattributes`** at repo root: `* text=auto eol=lf` — ends the
  CRLF/LF churn between Windows tools and cloud sessions (the cause of
  the whole-repo-looking diffs in the v0.6.0 history). Add, commit, and
  run `git add --renormalize .` in the same commit if it reports
  changes.
- CLAUDE.md: new implemented-notes section for this batch; SSE table
  update (work item 1); layout entries for `usage_ledger.py`,
  `SettingsPanel.tsx`, `StatusStrip.tsx`, `useSmoothText.ts`.
- README: current-status rewrite for v0.7.0; config table gains
  `BUILD_A_SPEC_THINKING_DISPLAY`.

## Acceptance criteria

1. From clicking send to turn end, there is NEVER a moment with no
   visible activity: status strip, thinking stream, text flow, search
   chips, or document patches — something is always moving.
2. Text renders smoothly (no burst-chunking, no flicker, no layout
   jumps) on a 2,000-word streamed answer with tool rounds; scroll
   position is never stolen while reading history.
3. A paragraph can be hand-edited and a block confirmed with one click,
   both undoable, both blocked during a streaming turn, and the model's
   next turn sees the results without any special plumbing.
4. The key can be viewed (masked), replaced (test-then-save), removed,
   and tested from the settings panel; env-var keys show read-only.
5. The header shows session spend; the usage table's numbers reconcile
   with the sum of `turn_complete.usage` payloads in a scripted test.
6. Suite green, build clean, docs current, version gate passes at
   0.7.0.
