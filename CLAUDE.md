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
- Reused Spec Critic code is **copied in and adapted**, never imported across
  repos. When porting a file, keep its design and docstring posture, update
  identity strings (BuildASpec / BUILD_A_SPEC_*), and note the provenance in
  the module docstring.
- Frozen decisions (2026-07-21, confirmed with Abraham): pywebview+React+FastAPI
  UI; copy-based reuse; first module = hyperscale fire suppression Div 21;
  research agents land right after the core drafting loop works.
- NFPA 13 default edition is **2025** (current edition). Jurisdiction-adopted
  earlier editions override when known — never silently, always with the
  adoption basis stated. This mirrors Spec Critic's pinned-edition philosophy
  (`code_cycles.StandardEdition`), which will be ported in Phase 3.
- Keep `README.md`, `requirements.txt`, and this file current when the
  implementation, dependencies, or conventions change.

## Layout

```
main.py                    entry point: uvicorn thread + pywebview window
backend/
  settings.py              models (claude-sonnet-5 default), port 8756, env knobs
  app.py                   FastAPI app factory; SSE at POST /api/chat; doc/undo/
                           redo, docx export, project save/load endpoints
  app_paths.py             [PORT: Spec Critic src/core/app_paths.py]
  api_key_store.py         [PORT: Spec Critic src/core/api_key_store.py + save_api_key]
  sessions.py              single module-level SessionState (history + DocumentStore)
  spec_doc/model.py        SectionFormat tree; stable ids (pt1.a2.p3); statuses;
                           transactional apply_edits; DocumentStore (per-turn
                           versions, undo/redo); open_questions; outline;
                           APPLY_SPEC_EDITS_TOOL schema
  spec_doc/docx_export.py  python-docx rendering + assumptions/open-items schedules
  spec_doc/project.py      JSON project files (save/resume) + chat transcript
  llm/client.py            client factory; MissingApiKeyError; per-key cache
  llm/prompts.py           SYSTEM_PROMPT — tool drafting + defaults-first interview
  llm/conversation.py      stream_user_turn generator; tool dispatch + continuation
frontend/src/
  App.tsx                  state owner: messages[], doc, open items, changed ids,
                           health, send loop (the SSE event switch)
  lib/api.ts               streamChat async generator; doc/undo/redo/project calls
  components/*             Chat / MessageBubble / Composer / ArtifactPanel
                           (stepper, export, save/open, open items) /
                           SpecDocument (paper rendering) / Header / ApiKeyBanner
tests/
  conftest.py              hermetic env + fresh session per test
  fakes.py                 scripted fake streaming client (text + tool_use turns)
  test_app.py              API surface: SSE round-trips, tool loop, rollback,
                           undo/redo, export, project save/resume
  test_spec_doc.py         document model units: ids, transactions, versions
```

## Event protocol (SSE, `POST /api/chat`)

Each frame is `data: <json>\n\n`. Event types:

| type | payload | meaning |
|---|---|---|
| `text_delta` | `text` | streamed assistant text chunk (all continuation rounds) |
| `doc_patch` | `ops`, `doc` | an applied edit batch: ops echo server-assigned element ids (highlighting); `doc` is the authoritative full snapshot (rendering) |
| `doc_snapshot` | `doc` | committed tree after a doc-changing turn — mid-turn patches carry a pre-commit version pointer; this one is current |
| `open_questions` | `items` | open-item list (TBD markers + needs_input blocks); emitted when a turn changed the doc |
| `turn_complete` | `stop_reason` | turn ended; history + doc version committed server-side |
| `error` | `message` | turn failed; history untouched and doc rolled back (retry is safe) |

The frontend switch in `App.tsx#send` is the single place events dispatch.
Snapshots outside a turn travel over REST, not SSE: `GET /api/doc`,
`POST /api/doc/undo|redo`, and `POST /api/project/load` all return
`{doc, open_questions}` (load adds `chat`, the rebuilt transcript).
Patches and snapshots always carry the full tree — the frontend never
applies ops itself.

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
- `SessionState.generation` increments on reset and project load; an
  in-flight turn checks it before each round, each tool dispatch, and the
  final commit, so a zombie turn discards itself instead of polluting the
  fresh/loaded session ("New session" is also disabled in the UI while a
  turn streams).
- System prompt is a block list: the stable prompt block carries
  `cache_control: ephemeral` (prompt-cache hits across the growing
  interview); a **dynamic document-outline block follows it**, outside the
  cached prefix, keeping the model's map of element ids/statuses current —
  including after undo/redo and project resume.
- The tool loop in `stream_user_turn` follows Spec Critic's streaming
  continuation pattern (`requirements_research.py`): stream → on
  `tool_use`, apply edits + emit `doc_patch` + send tool_result → stream
  again. An invalid edit batch becomes an `is_error` tool_result (with the
  current outline) for the model to self-correct — never a turn failure.
- Document edits are transactional per batch (`spec_doc.apply_edits` works
  on a copy, swaps on success). Element ids come from monotonic per-parent
  counters and are never reused; display numbering (1.1 / A. / 1. / a. /
  1)) derives from position at serialization time. A new edit after undo
  truncates the redo tail, so ids can't collide with an abandoned future.

## Phase 2 — implemented notes

- `apply_spec_edits` op schema (see `APPLY_SPEC_EDITS_TOOL` in
  `spec_doc/model.py`): ops `{action: add_article|add_paragraph|replace|
  delete, target_id, position?, text?, numbering?, status?}`. The section
  header is set via `replace` on target `sec` (`text` = title,
  `numbering` = section number) — no fifth action. Omitted `status`
  defaults to `assumed`: over-flagging for the reviewer beats silently
  confirming a model guess.
- `.docx` export (`spec_doc/docx_export.py`) renders SectionFormat body +
  **assumptions schedule** + open-items schedule; download at
  `GET /api/export/docx`. Project save/resume is a JSON file with the full
  history (tool blocks included) and the complete version list, so undo
  survives a resume (`spec_doc/project.py`).
- Next up is **Phase 3 — spec modules** (registry-validated `SpecModule`,
  pinned standards editions, deterministic linting): see `README.md` →
  Roadmap and the Spec Critic port pointers below.

Interview policy (decided 2026-07-21, conversation w/ Abraham):

- **Defaults-first.** Every question carries the model's recommended answer;
  "I don't know" is a first-class reply — the model applies the defensible
  NFPA 13-2025 / hyperscale-norm default and stamps the block `assumed`.
  The panel badges assumptions; the export schedules them so a senior
  reviewer audits every guess in one pass. The interview never stalls on an
  unanswered question unless it is truly non-defaultable (section, location,
  client, hazard basics).
- **Guide-me mode.** Optional mode where open questions become 2–4 concrete
  options with plain-language tradeoffs (novices pick, experts type). Plus
  an "explain why you're asking" affordance on any question.
- **Model routing.** Conversational turns stay on Sonnet 5; heavy one-shot
  passes (e.g. "draft all of PART 2 from the gathered profile") may route to
  Opus 4.8 via the existing `stream_user_turn(model=...)` override.

## Commands

```
.venv/bin/python -m pytest -q          # backend suite (Windows: .venv\Scripts\python)
cd frontend && npm run dev             # UI hot reload (with BUILD_A_SPEC_DEV=1 backend)
cd frontend && npm run build           # tsc --noEmit && vite build -> dist/
python main.py                         # run the app (serves dist/)
```

## Source-of-truth pointers into Claude-Spec-Critic

When porting in later phases, pull from these (paths in the Spec Critic repo):

- `src/core/project_profile.py` — ProjectProfile (city/state/country/client),
  `web_search_user_location()`, `jurisdiction_fingerprint()`.
- `src/core/code_cycles.py` — `StandardEdition` / `BaseCode` pinned-edition
  records with provenance `source` fields.
- `src/research/` — requirements fan-out (`run_requirements_research`,
  `RequirementsProfile`, grounding via accepted-vs-cited URLs).
- `src/modules/base.py` + `registry.py` — frozen module objects, import-time
  registry validation; `datacenter_fire.py` seeds the first SpecModule's
  content.
- `src/verification/retry_policy.py`, `source_grounding.py` — retry/backoff
  classification and citation grounding.
- `src/tracing/` — whole package is domain-neutral (spans/events JSONL +
  HTML viewer).
- `core/updates.py`, `packaging/windows/`, `docs/RELEASE_WINDOWS.md` — the
  installer + SHA-256-verified auto-updater pipeline.
