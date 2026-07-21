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
  app.py                   FastAPI app factory; SSE at POST /api/chat
  app_paths.py             [PORT: Spec Critic src/core/app_paths.py]
  api_key_store.py         [PORT: Spec Critic src/core/api_key_store.py + save_api_key]
  sessions.py              single module-level SessionState (Phase 1)
  llm/client.py            client factory; MissingApiKeyError; per-key cache
  llm/prompts.py           SYSTEM_PROMPT — Phase 1 Div 21 interviewer
  llm/conversation.py      stream_user_turn generator; _TOOLS seam (empty)
frontend/src/
  App.tsx                  state owner: messages[], health, send loop
  lib/api.ts               streamChat async generator (fetch + SSE parse)
  components/*             Chat / MessageBubble / Composer / ArtifactPanel /
                           Header / ApiKeyBanner
tests/
  conftest.py              hermetic env + fresh session per test
  test_app.py              health, SSE round-trip, error paths, reset
```

## Event protocol (SSE, `POST /api/chat`)

Each frame is `data: <json>\n\n`. Phase 1 event types:

| type | payload | meaning |
|---|---|---|
| `text_delta` | `text` | streamed assistant text chunk |
| `turn_complete` | `stop_reason` | turn ended; history committed server-side |
| `error` | `message` | turn failed; history untouched (retry is safe) |

Phase 2 adds document events (planned): `doc_patch` (an applied edit op with
element ids), `doc_snapshot` (full tree on load/undo), `open_questions`.
The frontend switch in `App.tsx#send` is the single place events dispatch.

## Conversation engine invariants

- History mutates only after a fully successful turn (user + assistant
  appended together); every failure path yields one `error` event and leaves
  history unchanged so resend never duplicates.
- System prompt is a block list with `cache_control: ephemeral` on the last
  block (prompt-cache hits across the growing interview).
- `_TOOLS` in `conversation.py` is the Phase 2 seam: register document tools
  there, then grow the loop with tool-use dispatch + continuation, mirroring
  Spec Critic's streaming continuation pattern (`requirements_research.py`).

## Phase 2 sketch (next up)

(Full phase list: `README.md` → Roadmap. This section is the build spec for
the next milestone.)

Server-owned document model in `backend/spec_doc/`:

- Tree: `SpecSection` → parts (`PART 1 - GENERAL` / 2 / 3) → `Article` →
  `Paragraph` (nested letters/numbers per SectionFormat). Stable element ids
  (`pt1.a2.p3` style — generative cousin of Spec Critic's `p7`/`t0r2` ids).
- Per-block provenance status: `confirmed` (user-supplied/approved) /
  `assumed` (model default, see interview policy) / `needs_input`;
  `[TBD: …]` markers tracked as first-class open items.
- `apply_spec_edits` tool schema: list of ops
  `{action: add_article|add_paragraph|replace|delete, target_id, position?,
  text?, numbering?, status?}` — validated server-side, applied
  transactionally, snapshotted for undo, broadcast as `doc_patch` events.
- Renderers: panel JSON → React; `.docx` export via python-docx (office
  SectionFormat styling) including an **assumptions schedule**; JSON project
  file for save/resume.

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
