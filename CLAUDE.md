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
  standards.py             [PORT: Spec Critic src/core/code_cycles.py]
                           StandardEdition (+title for REFERENCES) / BaseCode /
                           StandardsBasis; effective_editions (pins + overrides);
                           standards_context_block; validate_overrides_shape
  project_profile.py       [PORT: Spec Critic src/core/project_profile.py]
                           ProjectProfile: US/CA tables, country/state
                           normalization, web_search_user_location, fingerprint
  research/engine.py       [PORT: Spec Critic src/research/requirements_research.py]
                           the fan-out: ThreadPoolExecutor over module dimensions,
                           pause_turn continuation loop, 2× search-budget ceiling,
                           structured→tagged-JSON parse, grounding, retries with
                           billed-usage aggregation; RequirementsProfile +
                           render_text + research_context_block (trim-to-cap)
  research/grounding.py    [PORT: source_grounding.py + verifier collectors]
                           normalize_url, validate_cited_sources, evidence
                           collectors, stop-reason classes
  research/retry_policy.py [PORT: verification/retry_policy.py realtime subset]
  research/resend_sanitizer.py  [PORT ≈verbatim: fetched-PDF elision; pypdf]
  research/schema.py       [PORT: structured_schemas.py research slice +
                           api_config.py web-tool builders + domain blocklist]
  research/runner.py       session-bound run lifecycle: daemon thread, event
                           log, snapshot, SSE follow generator (Build-a-Spec
                           native — no Spec Critic source)
  updates.py               [PORT ≈verbatim: Spec Critic src/core/updates.py]
                           GitHub-Releases manifest updater: https-only +
                           redirect-downgrade guard, SHA-256 verify before
                           launch, atomic .part promote, throttle/skip state
  compliance/checker.py    [PORT: Spec Critic src/compliance/compliance_checker.py]
                           controlling = grounded spec_requirements only;
                           coverage matrix (represented/missing/contradicted/
                           unclear, always complete); strict tool + tagged
                           fallback; single streaming call, no chunking
  compliance/runner.py     AuditRunner: thread/status; result stamps
                           audited_at + version_index (staleness marker)
  tracing/                 [PORT: Spec Critic src/tracing/ core ≈verbatim]
                           recorder (JSONL spans/events/prompts + run.json,
                           writer thread, ContextVar parents), spans (BAS
                           kind vocabulary), config (BUILD_A_SPEC_TRACE*,
                           default on), redaction (inlined credential
                           patterns); capture.py = native never-raise hooks;
                           viewer/trace_viewer.html bundled
  app_paths.py             [PORT: Spec Critic src/core/app_paths.py]
  api_key_store.py         [PORT: Spec Critic src/core/api_key_store.py + save_api_key]
  sessions.py              single module-level SessionState (history + DocumentStore
                           + SpecModule + ResearchRunner)
  spec_modules/base.py     [PORT: Spec Critic src/modules/base.py]
                           frozen SpecModule (catalog, playbook, prompt slots, lint
                           vocabulary, dormant research dimensions); import-time
                           validate_module_registry — bad module = startup failure
  spec_modules/registry.py [PORT: Spec Critic src/modules/registry.py]
                           AVAILABLE_MODULES / DEFAULT_MODULE / get_module
                           (unknown id degrades to default, never errors)
  spec_modules/hyperscale_fire.py
                           [SEED: Spec Critic src/modules/datacenter_fire.py]
                           first module: 21 13 13 lead + sibling catalog, playbook,
                           current-edition NFPA pins w/ provenance, research dims
  spec_doc/model.py        SectionFormat tree; stable ids (pt1.a2.p3); statuses
                           (confirmed/assumed/needs_input/imported);
                           transactional apply_edits; edition_overrides +
                           project_profile on the tree; DocumentStore (per-turn
                           versions, undo/redo, adopt_imported); open_questions;
                           outline; APPLY_SPEC_EDITS_TOOL schema
  spec_doc/importer.py     [PORT: Spec Critic src/input/extractor.py mechanics]
                           Accept-All tracked-changes text, content-loss
                           warning; native SectionFormat tree builder (labels
                           OR w:numPr ilvl); keep-everything-warn-loudly
  spec_doc/linting.py      [PORT: Spec Critic src/input/preprocessor.py logic]
                           deterministic advisory lint: stale editions vs effective
                           pins (negation suppression), placeholders/markers,
                           empty/duplicate articles, unset header
  spec_doc/docx_export.py  python-docx rendering + assumptions/open-items schedules
  spec_doc/project.py      JSON project files (save/resume) + chat transcript +
                           module_id
  llm/client.py            client factory; MissingApiKeyError; per-key cache
  llm/prompts.py           engine protocol blocks + render_system_prompt(module)
  llm/conversation.py      stream_user_turn generator; tool dispatch + continuation;
                           lint event + standards_payload
frontend/src/
  App.tsx                  state owner: messages[], doc, open items, lint issues,
                           standards, changed ids, health, send loop (SSE switch)
  lib/api.ts               streamChat async generator; doc/undo/redo/project calls
  components/*             Chat / MessageBubble / Composer / ArtifactPanel
                           (stepper, export, save/open, ⚠ badge, open items) /
                           IssuesDrawer (lint list + StandardsStrip) /
                           SpecDocument (paper rendering) / Header / ApiKeyBanner
docs/standards_provenance.md  receipts for every pinned edition (keep current!)
tests/
  conftest.py              hermetic env + fresh session per test
  fakes.py                 scripted fake streaming client (text + tool_use turns)
  test_app.py              API surface: SSE round-trips, tool loop, rollback,
                           undo/redo, export, project save/resume, lint/standards
  test_spec_doc.py         document model units: ids, transactions, versions,
                           set_standard_edition
  test_standards.py        pins, overrides, rendering helpers
  test_spec_modules.py     registry-validation failure modes
  test_linting.py          every lint rule + suppression + override interplay
```

## Event protocol (SSE, `POST /api/chat`)

Each frame is `data: <json>\n\n`. Event types:

| type | payload | meaning |
|---|---|---|
| `text_delta` | `text` | streamed assistant text chunk (all continuation rounds) |
| `doc_patch` | `ops`, `doc` | an applied edit batch: ops echo server-assigned element ids (highlighting); `doc` is the authoritative full snapshot (rendering) |
| `doc_snapshot` | `doc` | committed tree after a doc-changing turn — mid-turn patches carry a pre-commit version pointer; this one is current |
| `open_questions` | `items` | open-item list (TBD markers + needs_input blocks); emitted when a turn changed the doc |
| `lint` | `items`, `standards` | advisory lint issues + the editions in effect (pins + overrides); emitted right after `open_questions` when a turn changed the doc |
| `turn_complete` | `stop_reason` | turn ended; history + doc version committed server-side |
| `error` | `message` | turn failed; history untouched and doc rolled back (retry is safe) |

The frontend switch in `App.tsx#send` is the single place events dispatch.
Snapshots outside a turn travel over REST, not SSE: `GET /api/doc`,
`POST /api/doc/undo|redo`, and `POST /api/project/load` all return
`{doc, open_questions, lint, standards, profile_complete, research_status}`
(load adds `chat`, the rebuilt transcript). Patches and snapshots always
carry the full tree — the frontend never applies ops itself.

Research has its own channel (a run outlives any one chat turn):
`POST /api/research/start` (400 incomplete profile / no key; 409 while
running), `GET /api/research/status` (snapshot: status/error/events/
profile view), and `GET /api/research/stream` — an SSE stream that replays
the run's event log from seq 0 and follows until terminal, closing with a
`stream_end` sentinel (event types: `research_started`,
`dimension_complete`, `dimension_failed`, `research_complete`,
`research_failed`).

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
- System prompt is a block list: the stable block — `render_system_prompt
  (session.module)`, deterministic per module — carries
  `cache_control: ephemeral` (prompt-cache hits across the growing
  interview); a **dynamic block follows it**, outside the cached prefix,
  carrying everything session-varying: the standards editions in effect
  (`standards_context_block`: module pins + recorded jurisdiction
  overrides) and the document outline — current after undo/redo and
  project resume. Nothing session-varying may render into the stable
  block (pinned by `test_stable_system_prompt_is_cached_and_module_rendered`).
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
  delete|set_standard_edition, target_id, position?, text?, numbering?,
  status?, standard?, edition?, basis?}`. The section header is set via
  `replace` on target `sec` (`text` = title, `numbering` = section
  number). Omitted `status` defaults to `assumed`: over-flagging for the
  reviewer beats silently confirming a model guess.
- `.docx` export (`spec_doc/docx_export.py`) renders SectionFormat body +
  **assumptions schedule** + open-items schedule; download at
  `GET /api/export/docx`. Project save/resume is a JSON file with the full
  history (tool blocks included) and the complete version list, so undo
  survives a resume (`spec_doc/project.py`).

## Phase 3 — implemented notes

- **Modules.** `SessionState.module` holds the active `SpecModule`
  (default: `hyperscale_fire`); reset keeps it, project load resolves it
  from the file's `module_id` via `get_module` (unknown → default, the
  Spec Critic degrade posture — visible through the standards block, never
  silent in effect). Registry validation runs at import: bad prompt slots,
  a defaultable playbook topic without its default, a pin without
  provenance, or a malformed research template all fail startup.
- **Edition overrides** live ON the tree (`SpecSection.edition_overrides`,
  `{canonical name: {edition, basis}}`) so they ride the existing
  transactional apply / per-turn versioning / undo / project machinery.
  The `set_standard_edition` op targets `sec`; `basis` is required when
  setting (never silent); empty `edition` removes an override; names are
  normalized (`normalize_standard_name`) for case-insensitive matching
  against pins. Overrides count as document content (`is_empty` is False
  with one recorded).
- **Pins are drafting defaults** — current published editions (NFPA
  13-2025 et al.), deliberately different from Spec Critic's
  `datacenter_fire`, which pins what the 2024 I-codes *reference* (NFPA
  13-2022): reviewing audits against a stated basis; drafting defaults to
  current and lets the jurisdiction override. Checked 2026-07: the
  rumored NFPA 13D/13R/24/291 consolidation into NFPA 13-2025 did NOT
  happen; NFPA 24-2025 and 291-2025 are separate pins. Receipts:
  `docs/standards_provenance.md` (keep it current when touching pins).
- **Lint** (`spec_doc/linting.py`) recomputes on demand — REST payloads
  via `app._doc_payload`, SSE via the `lint` event after doc-changing
  turns. Stale-edition patterns are per-standard-name, longest shape
  first (REFERENCES-line: designation + digit-free title gap + "<year>
  edition"), span-deduped, negation-suppressed within the sentence
  window. `[TBD: ...]` is open-items territory, not lint. Issues are
  advisory — they never block an edit or a turn.
- **Research dimensions** (governing_codes / ahj_requirements /
  client_standards / site_environment, seeded from Spec Critic) are
  registration-validated on the module; Phase 4's engine consumes them.

## Phase 4 — implemented notes

- **Profile enters conversationally** via the `set_project_profile` op
  (target `sec`; fields city/state/country/client; provided fields
  update, explicit `""` clears; country folds to US/CA or the op errors;
  state names fold to codes). Stored on `SpecSection.project_profile` —
  transactional, undoable, persisted like `edition_overrides`. The
  applied record reports `complete`, and `_doc_payload.profile_complete`
  gates the panel's research button.
- **Research never auto-triggers.** `POST /api/research/start` is the
  only entry: validates profile completeness + module dimensions + key,
  then `ResearchRunner.start` fans out on a daemon thread with the
  session's client. Reset/load swap in a fresh runner — an in-flight run
  settles into the abandoned object (zombie-turn pattern; pinned by
  `test_session_reset_abandons_running_research`). Re-running replaces a
  terminal run's results.
- **Grounding invariant** (ported): an item is `grounded` only when ≥1
  cited URL matches (post-`normalize_url`) a URL the server tools
  actually retrieved in that dimension's conversation — pooled across
  pause_turn continuations. Ungrounded items are kept, marked
  `[UNVERIFIED]`, and the prompt instructs they are leads, not facts.
  Grounding proves retrieval, not truth.
- **Context splice**: when the session's runner holds a completed
  profile, `_system_blocks` appends `research_context_block(profile)` to
  the DYNAMIC block — token-capped (est. len/4, 16k default), whole items
  trimmed lowest-confidence-first, structured profile untouched. The
  stable prompt may mention the profile in policy text but never carries
  run data (cacheability; pinned in `test_research_api`).
- **Provenance**: `Paragraph.source_item_id` (set via `source_item_id` on
  add_paragraph/replace; `""` clears) links a provision to a profile item
  → the ◆ chip in `SpecDocument`. Advisory — never validated against the
  current profile (research can re-run and re-mint ids).
- **Overrides from research**: no new machinery — the prompt instructs
  recording `set_standard_edition` with the research item id in the
  `basis` when a grounded item establishes the adopted edition.
- **Model knobs**: `settings.RESEARCH_MODEL` /
  `BUILD_A_SPEC_RESEARCH_MODEL`, `RESEARCH_MAX_TOKENS`. Strict tool shape
  attaches only for the known model ids (`schema._STRICT_CAPABLE_MODELS`)
  — an unknown override degrades to lenient, never a 400.
- **Deliberately not ported**: adaptive thinking/effort config (no
  capability table yet), `corpus_signals.py` (re-evaluate now that
  masters can be imported), diagnostics rollups.
- **New dependency**: `pypdf` (the resend sanitizer's page counting).

## Phase 5 — implemented notes

- **Import** (`spec_doc/importer.py` + `POST /api/import/master`): only
  into an EMPTY document (409 otherwise — a starting point, never a
  merge); lands as ONE version via `DocumentStore.adopt_imported` (undo
  → blank page); bumps `session.generation` (an import is
  session-changing work). Parse philosophy: keep everything, warn loudly
  — orphan content → synthetic "IMPORTED CONTENT" article, depth clamps,
  tables flatten (` | `), trailing content after END OF SECTION ignored.
  Manual labels win; else `w:numPr` ilvl drives depth; else level-0.
  Tracked changes import as the Accept-All view (ported byte-behavior:
  no-revision docs match `Paragraph.text` exactly) + a warning.
- **`imported` is the fourth status**: model never creates it (prompts
  say so); gap-and-adapt policy (stable prompt) retires it; the export
  schedules whatever remains under "IMPORTED PROVISIONS NOT YET
  REVIEWED"; badge blue in the panel; not an open item, not lint.
- **Audit** (`compliance/`): gates on a completed research run + a
  non-empty draft; audits a `SpecSection.from_dict` SNAPSHOT so a
  streaming turn can't mutate under the call; single call on
  `settings.RESEARCH_MODEL`. Normalization enforces the trust model:
  coverage for non-controlling ids DROPS, skipped controlling ids
  become `unclear` (never invisible). Result carries `version_index` —
  the drawer shows a stale marker when the doc moves on; the export's
  closing section states the audited version. Persisted in the project
  file (`audit_result`); restored via `AuditRunner.restore`.
- **Updater** (`updates.py` + `/api/update/*`): state lives in the
  platformdirs config dir (with the key file), not `~/.build_a_spec`.
  Auto-check is server-throttled (once/day) via `/api/update/check`;
  `?force=true` bypasses throttle AND the skip-this-version marker.
  Install downloads → SHA-256-verifies → spawns, then the frontend
  announces the app will close. Non-Windows → 400, releases-page link.
  Tests set `BUILD_A_SPEC_DISABLE_UPDATE_CHECK=1` in conftest.
- **Packaging** (`packaging/windows/`): PyInstaller one-folder →
  `dist/BuildASpec`; bundles `frontend/dist` (resolved frozen via
  `sys._MEIPASS` in `settings._resolve_frontend_dist`) and the trace
  viewer; Inno AppId `{89E58C42-A4F6-49F8-8FCB-1147CB0186DB}` is
  Build-a-Spec's own — NEVER change it, NEVER share it with Spec
  Critic. `check_release_version.py` gates settings/package.json/tag
  agreement and runs inside pytest (`test_version_consistency_gate`) —
  bump BOTH files every release. Runbook: `docs/RELEASE_WINDOWS.md`.
- **Tracing**: one app-lifetime recorder, lazily started on first
  capture (`capture._ensure_recorder`), run id `session-<hex>-<ts>`,
  stopped atexit; session resets stay inside the same trace. Capture
  sites: turn spans (opened in `stream_user_turn`, ALWAYS closed in its
  `finally`), tool_dispatch events, research run span (+ mirrored sink
  events), audit span, import event. Hooks never raise. Hermetic tests
  set `BUILD_A_SPEC_TRACE=0` in conftest; tracing tests opt back in
  with a tmp trace dir.

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

Ported in Phase 3 (done — kept for archaeology): `src/core/code_cycles.py`
→ `backend/standards.py`; `src/modules/base.py` + `registry.py` →
`backend/spec_modules/`; `src/modules/datacenter_fire.py` seeded
`hyperscale_fire.py`; `src/input/preprocessor.py` detector logic →
`backend/spec_doc/linting.py`.

Ported in Phase 4 (done): `src/core/project_profile.py` →
`backend/project_profile.py`; `src/research/requirements_research.py` →
`backend/research/engine.py`; `src/verification/source_grounding.py` + the
verifier's evidence collectors → `backend/research/grounding.py`;
`src/verification/retry_policy.py` (realtime subset) →
`backend/research/retry_policy.py`; `src/core/resend_sanitizer.py` →
`backend/research/resend_sanitizer.py`; the research slice of
`src/review/structured_schemas.py` + `src/core/api_config.py` web-tool
builders → `backend/research/schema.py`.

Ported in Phase 5 (done — **the port plan is complete**):
`src/input/extractor.py` mechanics → `backend/spec_doc/importer.py`;
`src/compliance/compliance_checker.py` trust model →
`backend/compliance/checker.py`; `src/core/updates.py` →
`backend/updates.py` (≈verbatim); `packaging/windows/*` +
`docs/RELEASE_WINDOWS.md` cloned with Build-a-Spec identity;
`src/tracing/` core (recorder/spans/config/redaction + viewer HTML) →
`backend/tracing/` with a native `capture.py`.

Not ported, on purpose: `extraction_cache.py` (imports are one-shot
here, nothing to cache), `corpus_signals.py` (re-evaluate if research
should ever scrape imported masters for vocabulary), the adaptive
thinking/effort config and diagnostics rollups (no capability table /
diagnostics surface in this app yet).
