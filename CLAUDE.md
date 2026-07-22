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
main.py                    entry point: uvicorn thread + pywebview window;
                           _CloseController offers to save progress on window
                           close (closing-event veto → off-thread frontend
                           prompt → js_api save_and_close/discard_and_close,
                           native save via SAVE_DIALOG; never traps the user)
backend/
  settings.py              models (claude-sonnet-5 default), effort levels
                           (interview high / research xhigh), max_tokens at
                           the 128k model ceiling, chat web-tool allowances,
                           port 8756, env knobs
  app.py                   FastAPI app factory; SSE at POST /api/chat; POST
                           /api/draft/full (Batch 3 directive); doc/undo/redo/edit,
                           docx export, project save/load endpoints; Batch 4 adds
                           /api/qc/start|status|stream|apply|dismiss|export +
                           /api/readiness (audit endpoints kept, deprecated); Batch 5
                           adds GET /api/doc/diff + ?redline=master|version on
                           /api/export/docx (+ baseline_index in _doc_payload);
                           Batch 6 adds POST /api/onboarding/demo (guided-tour
                           demo directive; 409 unless the document is blank);
                           Batch 7 adds POST /api/chat/stop + /api/research/stop
                           + /api/qc/stop (409 when nothing is running/streaming)
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
                           render_text + research_context_block (trim-to-cap);
                           Batch 7 threads a should_stop callback into
                           _run_dimension (checked before each retry/continuation
                           — cooperative, not mid-call interruption)
  research/grounding.py    [PORT: source_grounding.py + verifier collectors]
                           normalize_url, validate_cited_sources, evidence
                           collectors, stop-reason classes
  research/retry_policy.py [PORT: verification/retry_policy.py realtime subset]
  research/resend_sanitizer.py  [PORT ≈verbatim: fetched-PDF elision; pypdf]
  research/schema.py       [PORT: structured_schemas.py research slice +
                           api_config.py web-tool builders + domain blocklist]
  research/runner.py       session-bound run lifecycle: daemon thread, event
                           log, snapshot, SSE follow generator (Build-a-Spec
                           native — no Spec Critic source); Batch 7 adds stop()
                           (per-run cancel_event + race-free _try_resolve)
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
                           audited_at + version_index (staleness marker).
                           DEPRECATED (Batch 4): the qc/ code_compliance +
                           completeness lenses supersede it; endpoints retained
  qc/schema.py             [Batch 4] QCLens defs (5 lenses) + submit_qc_findings /
                           submit_qc_verdict strict tools (strict conventions from
                           research/schema; Fable added to _STRICT_CAPABLE_MODELS) +
                           findings/verdict normalization + median-severity math
  qc/engine.py             [Batch 4, pattern: research/engine.py] run_final_qc:
                           lens fan-out (ThreadPool cap 4, pause_turn loop, 2×
                           search ceiling, PDF elision, retry policy, grounding) →
                           adversarial verification panel (tie→refuters) → ops
                           dry-run validation → QCResult (content-addressed
                           findings, dismiss memory); Batch 7 threads should_stop
                           into _run_lens/_verify_one (same cooperative pattern
                           as research/engine.py)
  qc/runner.py             [Batch 4, pattern: research/runner.py] QCRunner:
                           daemon thread, event log, snapshot, SSE follow +
                           stream_end; accept/dismiss mutators under lock;
                           Batch 7 adds stop() (same cancel_event/_try_resolve
                           pattern as research/runner.py)
  tracing/                 [PORT: Spec Critic src/tracing/ core ≈verbatim]
                           recorder (JSONL spans/events/prompts + run.json,
                           writer thread, ContextVar parents), spans (BAS
                           kind vocabulary), config (BUILD_A_SPEC_TRACE*,
                           default on), redaction (inlined credential
                           patterns); capture.py = native never-raise hooks;
                           viewer/trace_viewer.html bundled
  app_paths.py             [PORT: Spec Critic src/core/app_paths.py]
  api_key_store.py         [PORT: Spec Critic src/core/api_key_store.py + save_api_key]
                           Batch 2 adds key_status (masked, never leaks) + delete_api_key
  usage_ledger.py          [Batch 2] session-scoped billed-usage ledger (interview/
                           research/audit), thread-safe, cost estimate from
                           settings.PRICING; not persisted (per-session meter)
  figures.py               [Batch 8] chat-authored figures: Figure + FigureStore
                           (per-turn atomic like DocumentStore — begin/commit/
                           rollback, monotonic never-reused ids, validation, CSV
                           render, source-free context stubs, project persistence)
                           + CREATE_FIGURE_TOOL. Figure SOURCE never enters the
                           re-billed doc context or tool results (PDF-elision
                           posture) — only id/kind/title do; recurring token cost
                           is negligible regardless of figure count
  sessions.py              single module-level SessionState (history + DocumentStore
                           + SpecModule + ResearchRunner + AuditRunner + QCRunner
                           + FigureStore + UsageLedger) + has_unsaved_progress /
                           project_payload / project_default_stem (shared by
                           /api/project/save and the native save-on-close)
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
                           versions, undo/redo, adopt_imported; Batch 5 baseline_index
                           = redline master version, cleared on truncation, persisted);
                           open_questions; outline; APPLY_SPEC_EDITS_TOOL schema
  spec_doc/diffing.py      [Batch 5] pure diff_sections(base, cur) -> SectionDiff:
                           uid join (unchanged/changed/inserted/deleted, deleted at
                           base position, moves unmarked), word-level token_runs
                           (re.findall \S+\s* + SequenceMatcher, byte-exact
                           reconstruction), status_changes (status-only, no marks),
                           stats; feeds the redline writer + the compare view
  spec_doc/importer.py     [PORT: Spec Critic src/input/extractor.py mechanics]
                           Accept-All tracked-changes text, content-loss
                           warning; native SectionFormat tree builder (labels
                           OR w:numPr ilvl); keep-everything-warn-loudly
  spec_doc/linting.py      [PORT: Spec Critic src/input/preprocessor.py logic]
                           deterministic advisory lint: stale editions vs effective
                           pins (negation suppression), placeholders/markers,
                           empty/duplicate articles, unset header
  spec_doc/docx_export.py  python-docx rendering + assumptions/open-items schedules;
                           Batch 4 adds build_qc_memo (standalone QC memo) + a QC
                           closing that supersedes the audit closing in build_docx;
                           Batch 5 adds the redline body writer (build_docx(...,
                           redline=SectionDiff): w:ins/w:del/w:delText + para-mark
                           ins/del via docx.oxml; clean path untouched, byte-stable)
                           + redline_filename
  spec_doc/project.py      JSON project files (save/resume) + chat transcript +
                           module_id + audit_result + qc_result (baseline_index
                           rides store.to_dict/load — no project.py change)
  llm/client.py            client factory; MissingApiKeyError; per-key cache
  llm/prompts.py           engine protocol blocks + render_system_prompt(module);
                           FULL_DRAFT_DIRECTIVE (Batch 3 full-draft user message);
                           Batch 6 adds onboarding_demo_directive (discipline-
                           sanitized) + _ONBOARDING_POLICY in the stable prompt
  llm/conversation.py      stream_user_turn generator; tool dispatch + continuation;
                           lint event + standards_payload; Batch 7 adds
                           SessionState.stop_requested (threading.Event) — a
                           user stop ends the round loop early but still
                           commits (current_message_snapshot, not
                           get_final_message(), so the closed request doesn't
                           drain)
frontend/src/
  App.tsx                  state owner: messages[], doc, open items, lint issues,
                           standards, changed ids, health, usage, qc, readiness,
                           baselineIndex, settings-open, closePromptOpen
                           (window.buildaspecRequestClose hook), send loop (SSE
                           switch incl. status/thinking_delta); QC follow-stream
                           + accept/dismiss; Batch 6: drawerNonces + useOnboarding
                           wiring, send → Promise<boolean> (clean-turn signal)
  lib/api.ts               streamChat async generator; doc/undo/redo/edit/project;
                           draftFull; key status/delete/test; usage; Batch 4 qc
                           start/status/stream/apply/dismiss + readiness; Batch 5
                           getDocDiff; Batch 7 stopChat/stopResearch/stopQc (409
                           from an already-settled run/turn is swallowed, not thrown)
  lib/useSmoothText.ts     [Batch 2] rAF typewriter smoothing + reduced-motion +
                           splitStableTail (cheap-markdown prefix/tail split)
  lib/reviewQueue.ts       [Batch 3] pure buildQueue(doc, mode) — the review
                           queue as a document-order walk (port of iter_paragraphs);
                           reviewCounts (outstanding imported/assumed)
  lib/tour.ts              [Batch 6] the guided tour as pure data: STARTER_PROMPTS,
                           DISCIPLINES, DEMO_PROFILE, 22 steps in 4 chunks,
                           anchorSelector (data-tour / el-* / doc-snapshot resolvers)
  lib/useOnboarding.ts     [Batch 6] tour phase machine: runId zombie guard on
                           every await, key-gate auto-advance, do-this-for-me +
                           run-it dispatch, fresh-vs-keep resolution
  lib/onboardingStorage.ts [Batch 6] "tour completed" flag — the codebase's first
                           localStorage use; try/caught, cosmetic only
  lib/figures.ts           [Batch 8] figure render + security helpers: DOMPurify
                           SVG sanitize, lazy mermaid.render (securityLevel strict,
                           htmlLabels off), sandbox-iframe srcdoc with a strict CSP
                           (default-src none), canvas SVG→PNG, SVG/CSV blob
                           downloads — the render-time sanitization boundary for
                           model-authored markup (never inline into the bridge DOM)
  components/*             Chat (Batch 6 starter chips in the empty state) /
                           MessageBubble (smoothing + thinking block) /
                           Composer (WI2 ask-model prefill; Batch 7 swaps the send
                           button for a stop-square while streaming, Claude.ai-style
                           — always clickable, no confirmation) / ArtifactPanel
                           (stepper, Batch 5 Compare toggle + base picker + stat line
                           + export menu, save/open, ⚠ badge, "Draft full section"
                           button, open items) / ReviewDrawer (Batch 3 keyboard
                           review walk) / IssuesDrawer (lint + StandardsStrip) /
                           ResearchDrawer (research only — audit UI retired in
                           Batch 4; also hosts the project-profile form for direct
                           upfront entry; Batch 7 adds a Stop button while running,
                           gated by ConfirmDialog) / QCDrawer (Batch 4: readiness
                           checklist, lens progress, accept/dismiss fix queue,
                           hold-to-apply-criticals, refuted appendix; Batch 7 adds a
                           Stop button while running, gated by ConfirmDialog) /
                           SpecDocument (paper rendering + inline manual-edit
                           affordances; Batch 5 read-only diff render via `diff` prop)
                           / Header (spend ticker; Batch 6 Tour button) / ApiKeyBanner /
                           StatusStrip (live status strip) / SettingsPanel (key mgmt +
                           usage table + about) / CloseDialog (save-before-leaving
                           prompt: Save & close / Close without saving / Cancel)
                           / OnboardingOverlay (Batch 6: spotlight cutout + step
                           bubbles + discipline/entry/work-choice dialogs + resume
                           pill; drawers gain an openNonce prop, controls gain
                           data-tour anchors) / ConfirmDialog (Batch 7: generic
                           title/body/confirm/cancel modal — the lose-progress
                           warnings for stopping research/QC; the Final-QC launch
                           confirmation stays its own purpose-built modal) /
                           FigureCard (Batch 8: inline figure render — sanitized
                           SVG/mermaid in a sandbox="" iframe, escaped data table,
                           SVG/PNG/CSV downloads + a ✕ to remove)
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
  test_qc.py               [Batch 4] lens fan-out, adversarial verification (tie
                           kills, median severity), ops validation, apply (one undo
                           step + stale skip), dismiss memory, runner lifecycle,
                           readiness, memo export, Fable-priced usage
  test_diffing.py          [Batch 5] diff_sections units: identical/insert/delete/
                           text-edit (byte-exact run invariants) / nested / article
                           title / move-not-marked / status-only / section header /
                           vs-empty / token_runs whitespace / serialization
  test_redline_export.py   [Batch 5] Accept-All==cur & Reject-All==base round-trip
                           (real importer + custom reject reader), XML shapes
                           (author/date/unique id, w:delText not w:t, para-mark
                           ins/del), doc/diff + redline API validation, no-baseline
                           400, baseline_index project round-trip, clean-path no-marks
  test_onboarding.py       [Batch 6] demo-endpoint guards (incl. the blank-doc gate,
                           with a profile-only variant pinning is_empty), directive
                           snapshot + discipline sanitization, stable-policy
                           snapshot, scripted 3-round demo e2e (patches per PART,
                           one undo step, open_questions carries tbd+needs_input)
  test_stop.py             [Batch 7] chat stop mid-stream (truncates the live
                           SSE events, still commits, history stays alternating
                           even when caught right after a tool dispatch) +
                           endpoint 409/200; research/QC stop (discards the run,
                           409 once already resolved, the abandoned thread's
                           eventual completion can't clobber the resolved status,
                           immediate restart works)
  test_figures.py          [Batch 8] FigureStore units (validation, turn atomicity,
                           monotonic ids, persistence, CSV, source-free stubs) +
                           create_figure through /api/chat (figure SSE event, the
                           token-discipline tool result, rollback on failure,
                           self-correction on a bad payload) + REST (list/CSV/
                           delete/project round-trip)
```

## Event protocol (SSE, `POST /api/chat`)

Each frame is `data: <json>\n\n`. Event types:

| type | payload | meaning |
|---|---|---|
| `status` | `kind`, `round?`, `progress_chars?` | transient liveness hint (Batch 2): `working`/`thinking`/`writing`/`drafting`/`searching`/`fetching`. Replaces the current status strip; cleared by the next `text_delta`/`thinking_delta`. NOT persisted to history/traces/project files |
| `text_delta` | `text` | streamed assistant text chunk (all continuation rounds) |
| `thinking_delta` | `text` | streamed adaptive-thinking summary chunk (Batch 2; only when `THINKING_DISPLAY=summarized` and the model streams it). Rendered in a collapsible block; transient, never persisted |
| `web_search` | `query` | the model ran a server-side web search this round — emitted LIVE (Batch 2) the instant the server-tool block's input completes, not derived post-hoc |
| `web_fetch` | `url` | the model fetched a page/document server-side this round — emitted live on the block's completion |
| `figure` | `figure` | the model created a figure (diagram/schematic/table) via `create_figure` this round — the full serialized `Figure` for inline chat rendering + downloads (Batch 8). Emitted live on the tool dispatch. Source is client-sanitized before render; it lives only in the figure store, never in history/traces/the re-billed doc context |
| `doc_patch` | `ops`, `doc` | an applied edit batch: ops echo server-assigned element ids (highlighting); `doc` is the authoritative full snapshot (rendering) |
| `doc_snapshot` | `doc` | committed tree after a doc-changing turn — mid-turn patches carry a pre-commit version pointer; this one is current |
| `open_questions` | `items` | open-item list (TBD markers + needs_input blocks); emitted when a turn changed the doc |
| `lint` | `items`, `standards` | advisory lint issues + the editions in effect (pins + overrides); emitted right after `open_questions` when a turn changed the doc |
| `turn_complete` | `stop_reason`, `usage` | turn ended; history + doc version committed server-side. `usage` aggregates the turn's billed tokens across every round (input/output/cache/thinking + web-tool request counts) — raw material for the future cost meter |
| `error` | `message` | turn failed; history untouched and doc rolled back (retry is safe) |

The frontend switch in `App.tsx#send` is the single place events dispatch.
Snapshots outside a turn travel over REST, not SSE: `GET /api/doc`,
`POST /api/doc/undo|redo`, and `POST /api/project/load` all return
`{doc, open_questions, lint, standards, profile_complete, research_status,
baseline_index}` (load adds `chat`, the rebuilt transcript; `baseline_index`
is the imported-master version for the redline picker). Patches and snapshots
always carry the full tree — the frontend never applies ops itself. The
Batch 3 full-draft pass adds NO SSE event: `POST /api/draft/full` returns the
canned directive `{ok, message}` over REST (409 while a turn or research runs)
and the frontend sends `message` straight back through `POST /api/chat`, so
the pass is an ordinary turn on the one streaming path.

The Batch 5 redline/compare surface is REST-only, adds NO SSE event: `GET
/api/doc/diff?base=N[&cur=M]` returns a serialized `SectionDiff`
(`{ok, elements, status_changes, stats, base_index, cur_index,
baseline_index}`; 400 out-of-range or base==cur), and `GET
/api/export/docx?redline=master|version&base=N` streams a tracked-changes
`.docx` (400 when `redline=master` and no baseline; filename gains
` - REDLINE`). The clean `GET /api/export/docx` is byte-identical to before.

The Batch 6 onboarding surface is REST-only too, NO new SSE event:
`POST /api/onboarding/demo` (`{discipline}`) returns `{ok, message}` (409
while a turn streams / research runs, or when the document is non-empty)
and the frontend sends `message` through `POST /api/chat` — the demo draft
is an ordinary turn on the one streaming path, exactly like the Batch 3
full-draft pass.

Research has its own channel (a run outlives any one chat turn):
`POST /api/research/start` (400 incomplete profile / no key; 409 while
running), `GET /api/research/status` (snapshot: status/error/events/
profile view), and `GET /api/research/stream` — an SSE stream that replays
the run's event log from seq 0 and follows until terminal, closing with a
`stream_end` sentinel (event types: `research_started`,
`dimension_complete`, `dimension_failed`, `research_complete`,
`research_failed`).

Final QC (Batch 4) has the same channel shape (a QC run also outlives a
chat turn): `POST /api/qc/start` (400 empty draft / no key; 409 while a turn
streams or QC runs — research is NOT required), `GET /api/qc/status`
(snapshot: status/error/events/result view), `GET /api/qc/stream` (replay +
follow + `stream_end`; event types `qc_started`, `lens_complete`,
`lens_failed`, `verify_progress` {done,total}, `qc_complete`, `qc_failed`),
`POST /api/qc/apply` (`{finding_ids}` → one undoable version; per-finding
`applied`/`stale`/`no_ops`/`unknown` outcomes; 409 while a turn streams),
`POST /api/qc/dismiss` (`{finding_id, reason?}` → remembered by
content-addressed id across re-runs), and `GET /api/qc/export` (the
standalone QC memo `.docx`). `GET /api/readiness` is a deterministic
checklist (no model call): `{checks: [{id, ok, detail, advisory}], ready}`
— `ready` = all non-advisory checks ok (no open items, no unreviewed
imported/assumed, lint clean, research complete, QC current with no open
criticals; `profile_complete` is advisory).

`POST /api/research/stop` / `POST /api/qc/stop` (Batch 7) stop a running
fan-out — unlike the chat stop, this one IS lossy (the drawer's confirm
dialog says so): `ResearchRunner.stop()` / `QCRunner.stop()` resolve the run
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
- **Context architecture ("Sonnet unleashed", 2026-07-21).** The system
  prompt is ONLY the stable module block (`render_system_prompt`,
  deterministic per module, `cache_control: ephemeral`). Everything
  session-varying — standards editions in effect, the research profile,
  the **full document text** (`outline(doc, max_text=None)`, with
  ◆source chips), the lint report, and open items — renders into a
  PROJECT CONTEXT block spliced ahead of the user's text in the **newest
  user message** (`_turn_context_text`, frozen at turn start). A second
  cache breakpoint rides the tail of each request's messages
  (`_with_tail_cache_breakpoint`, copy-on-write — stored history never
  carries `cache_control`), so history caches incrementally instead of
  re-billing every turn. Nothing session-varying may render into the
  stable block (pinned by
  `test_stable_system_prompt_is_cached_and_module_rendered`).
- **Strip at commit** (`_committed_messages`): the context block is
  replaced by the user's bare text (exactly one current state block per
  request, never a stale one — pinned by
  `test_context_block_never_fossilizes_into_history`), thinking blocks
  drop (only required within their own turn), and fetched-PDF payloads
  are elided wholesale (`elide_all_pdf_sources` — a PDF left in history
  would be re-billed forever and balloon the project file). Server-tool
  blocks (search results, citations) stay.
- **Adaptive thinking** is stated explicitly (`thinking: {type:
  "adaptive"}` + `output_config: {effort: settings.INTERVIEW_EFFORT}`,
  default `high`; research runs `RESEARCH_EFFORT`, default `xhigh`).
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

- **Profile enters conversationally, or directly from the panel** — both
  paths post the same `set_project_profile` op (target `sec`; fields
  city/state/country/client; provided fields update, explicit `""`
  clears; country folds to US/CA or the op errors; state names fold to
  codes): the model calls it as a tool during the interview, and
  `ResearchDrawer`'s project-profile form posts it through the existing
  `POST /api/doc/edit` manual-edit path (no new endpoint — the op
  vocabulary there was already unrestricted) so the user can fill the
  whole profile out up front without touching chat. Stored on
  `SpecSection.project_profile` — transactional, undoable, persisted
  like `edition_overrides`. The applied record reports `complete`, and
  `_doc_payload.profile_complete` gates the panel's research button.
  `project_identity` is a non-defaultable playbook topic, and a
  per-field `PROJECT PROFILE` status block renders into every turn's
  PROJECT CONTEXT (`conversation._profile_status_block`) naming exactly
  what's still missing — the model uses it to ask about a missing field
  incrementally, a turn or several apart, rather than only once.
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

## "Sonnet unleashed" — implemented notes (2026-07-21, v0.6.0)

Abraham's directive: no quality limits on the model, ever — the user
spends what the work needs. What landed:

- **No-limits posture.** `INTERVIEW_MAX_TOKENS` / `RESEARCH_MAX_TOKENS`
  default to `MODEL_MAX_OUTPUT_TOKENS` (128k — the model ceiling, so the
  app imposes nothing). Research search/fetch budgets doubled
  (hyperscale_fire dimensions now 16–40 searches, 8–12 fetches;
  engine defaults 24/8; `RESEARCH_MAX_CONTINUATIONS` 16). The rendered
  research-profile cap is 100k est. tokens. The ONLY remaining caps are
  runaway circuit breakers (`MAX_TOOL_ROUNDS` 50, the 2× search
  ceiling) — sized so no legitimate turn ever meets one; hitting one is
  a bug, and the turn fails retry-safe.
- **Full-document context** replaced the planned `read_element` tool:
  the model sees every provision's complete text each turn (PROJECT
  CONTEXT block), so there is nothing left to "read". Tool results keep
  the compact 160-char outline as an id map.
- **Lint + open items feed the model** every turn (same block), with
  prompt policy (`_LINT_POLICY`): stale editions are drafting errors to
  fix when touching the block; no derailing the interview.
- **Interview web lookups**: `web_search`/`web_fetch` (blocklist shared
  with research, `CHAT_MAX_SEARCHES`/`CHAT_MAX_FETCHES` per round) with
  prompt policy (`_WEB_LOOKUP_POLICY`): verify facts freely, weigh
  sources, never recreate the research phase piecemeal, never paste
  retrieved content into the spec. Activity streams as
  `web_search`/`web_fetch` SSE events → inline chips in the chat.
- **Adaptive thinking + effort** wired in both loops (see invariants);
  `anthropic>=0.117` floor for `output_config`. Verified 2026-07 against
  platform.claude.com docs: Sonnet 5 runs adaptive thinking BY DEFAULT
  and rejects manual `budget_tokens`; thinking blocks MUST ride
  continuation rounds during tool use (the old code dropped them —
  latent 400 on real tool turns; fixed by verbatim `_serialize`).
- **Usage telemetry**: every turn aggregates billed usage across rounds
  → `turn_complete.usage` + the trace span. Groundwork for the cost
  meter (next batch).

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
- **Model routing (revised 2026-07-21, w/ Abraham).** Everything runs on
  Sonnet 5 — no user-facing model picker, ever. The one exception is the
  "Final QC" pass (shipped Batch 4, v0.9.0): a user-triggered,
  spare-no-expense multi-agent review on Fable 5 (`claude-fable-5`) before a
  section goes out the door. It runs on its own channel (`backend/qc/`),
  NOT through `stream_user_turn` — the interview loop stays Sonnet-only.

## Batch 2 — implemented notes (v0.7.0: streaming UX, editing, settings, meter)

- **Raw-event streaming.** `stream_user_turn` iterates the SDK's raw
  stream events (`_stream_events`) instead of `text_stream`, emitting a
  richer live vocabulary: `status` liveness hints on block starts,
  `thinking_delta` summaries, throttled drafting `progress_chars`, and
  `web_search`/`web_fetch` the instant a server-tool block's input
  completes (the post-hoc `_web_activity_events` pass is gone — no
  double-emit). `status` frames are transient and never persisted (pinned
  by `test_status_frames_never_persist_to_history`). A `status
  working/searching` fires at the top of every round so there is never
  dead air between send and first token.
- **Thinking display probe.** Requests carry `thinking: {type: adaptive,
  display: THINKING_DISPLAY}` (`BUILD_A_SPEC_THINKING_DISPLAY`, default
  `summarized`). If a model/endpoint 400s on the `display` key,
  `_enter_stream` degrades to `omitted` once and remembers it for the
  process (`reset_thinking_display_probe` re-arms it between hermetic
  tests). Manual QA still needed: confirm Sonnet 5 actually streams a
  readable summary in prod.
- **Frontend smoothing.** `useSmoothText` drains streamed text into the
  DOM a few chars per animation frame (rAF, backlog-scaled, reduced-motion
  aware); `MessageBubble` renders the settled prefix through a memoized
  markdown and the live tail as a plain span, so a long answer never
  re-parses markdown per frame. `StatusStrip` is the shimmer/pulse
  liveness line; the thinking block is collapsible. `Chat` follows the
  bottom on a rAF loop while pinned, hands off while reading.
- **Manual editing (WI2).** New `set_status` op (paragraph-only) +
  `POST /api/doc/edit` (same op vocabulary as the tool; one undoable
  version; 409 while `SessionState.turn_active`, set/cleared in
  `stream_user_turn`). `SpecDocument` grows hover affordances (✏️ inline
  edit → `replace` with `status: confirmed`, preserving `source_item_id`;
  ✓ → `set_status`; 🗑 → `delete` with inline confirm), all disabled while
  a turn streams. No history surgery: the model sees the result in its
  next PROJECT CONTEXT.
- **Settings + key management (WI3).** `key_status()` (source + masked
  tail, never the key) and `delete_api_key()` in `api_key_store.py`;
  `GET /api/key/status`, `DELETE /api/key`, `POST /api/key/test`
  (cheapest authenticated call: `models.list(limit=1)` on a throwaway
  `build_probe_client`, never cached, never stores). `SettingsPanel` (gear
  in `Header`): key source/replace-with-test-then-save/remove (env keys
  read-only), usage table, about + forced update check.
- **Cost meter (WI4).** `UsageLedger` on `SessionState` accumulates billed
  usage by category (interview/research/audit), thread-safe (research and
  audit fold run totals in from daemon threads — they meter BEFORE the
  status flip so a poller that sees `complete` finds the ledger updated).
  Reset/load clear it; not persisted. `settings.PRICING` (`VERIFY`-checked
  2026-07: Sonnet 5 at post-intro $3/$15, cache read 0.1×, cache write
  1.25×, web search $0.01/req, Fable 5 $10/$50 staged for Batch 4) drives
  the estimate. `GET /api/usage` → categories/totals/turns/estimate/
  cache-saved. Header shows a live `≈ $X` ticker; the settings Usage table
  breaks it down. Estimates are labeled estimates; traces stay the exact
  record.
- **Deviations from the plan:** (1) the `read_element` tool the plan's
  history mentions was already replaced by full-document context in
  v0.6.0 — untouched. (2) The plan's SettingsPanel Usage section is fed a
  `usage` prop and rendered internally (a `UsageTable`), not passed as a
  `usageSection` node. (3) Web *fetch* is metered as a count only (no
  per-request dollar) because Anthropic bills web fetch by tokens, not
  per request — only web *search* carries the $0.01/req line.

## Batch 3 — implemented notes (v0.8.0: full-section draft + review queue)

Two work items that complete the workflow symmetry — from-scratch drafting
and master import both converge on a full draft you then walk to reviewed
status. Frozen decisions honored throughout.

- **Full-section draft (WI1) rides the normal chat path — no new drafting
  machinery.** `POST /api/draft/full` is thin: 409 when `turn_active` or
  `research.status == "running"`, else `{ok, message: FULL_DRAFT_DIRECTIVE}`.
  The frontend (`App.onDraftFull`) fetches the directive and sends it through
  the ordinary `send()` → `/api/chat`, so it appears as a visible, honest
  user turn and inherits the SSE stream, tool loop, one-undo-step commit,
  rollback, and Batch 2 status strip — one code path for turns. Rejected the
  dedicated-endpoint alternative (would duplicate the pipeline).
- **Directive is server-owned** (`prompts.FULL_DRAFT_DIRECTIVE`) so its
  obligations stay versioned with the engine: draft every PART/article from
  known facts, use profile + grounded research (tag with `source_item_id`),
  stamp provenance honestly (`confirmed`/`assumed`/`[TBD]`/`needs_input`),
  batch edits per-article so patches stream live, close with a summary + 2–3
  follow-ups. Complemented by `_FULL_DRAFT_POLICY` in the STABLE prompt
  (breadth-first; ~25-ops-per-call **pacing prose, explicitly not a cap** —
  the no-limits rule stands; hitting the soft guide is fine).
- **"Draft full section" button** (`ArtifactPanel`) is accent-primary in the
  panel header, shown only while empty-or-sparse (< 3 articles), disabled
  while busy, one-time `.draft-pulse` glow once research completes. No confirm
  dialog — one undo step, said in the tooltip.
- **Review queue (WI2) is a pure function of the doc** — `buildQueue(doc,
  mode)` in `lib/reviewQueue.ts`, a straight port of the backend
  `iter_paragraphs` document order (the contract is pinned by a Python test,
  per the plan's steer — no vitest toolchain added). Entries carry
  `{elementId, articleId, ref, articleTitle, text, status, sourceItemId}`;
  `all` mode lists imported-then-assumed, each in document order (matches the
  export schedules). The queue derives from every fresh doc payload — no
  drawer-owned list to drift; it survives undo, model edits, and resets.
- **`ReviewDrawer`** walks one block at a time with keyboard actions (`K`/Enter
  keep → `set_status confirmed`, `E` edit → `replace` + confirmed preserving
  `source_item_id`, `D` delete, `A` ask → composer prefill, `S`/→ skip, ← back).
  Mutations do NOT advance the cursor — the queue recomputes and the next item
  slides into the cursor position (single source of truth: the doc). The
  bar shows the outstanding count ("Review N") + an All/Imported/Assumed
  filter. All mutations go through Batch 2's `POST /api/doc/edit`; the drawer
  is read-only while a turn streams (mirrors the paper panel's busy guard).
- **Per-article batch confirm, guarded:** a press-and-hold (800ms) button
  (shown only when the current article has ≥2 outstanding blocks) sends N
  `set_status` ops in one `/api/doc/edit` call → one undo step. **No
  document-wide bulk confirm** (frozen decision).
- **"Ask model"** prefills the composer through an App-owned `{text, nonce}`
  state (nonce re-fires the focus effect on repeat asks), threaded App → Chat
  → Composer; the drawer stays open and recomputes when the turn completes.
- **No new SSE events, no new env vars, no new Python deps.** Only new REST
  route: `POST /api/draft/full`.

## Batch 4 — implemented notes (v0.9.0: Final QC on Fable 5)

The one place a model other than Sonnet 5 appears (frozen decision):
`settings.QC_MODEL` defaults to `MODEL_FABLE_5` ("claude-fable-5"), added to
`schema._STRICT_CAPABLE_MODELS`. Fable 5's adaptive thinking is always-on;
QC requests state `thinking: {type: adaptive}` + `output_config.effort`
(`QC_EFFORT`, default xhigh) — never a manual budget (a `{type: disabled}`
would 400; the engine never sends it). Pricing was already in the Batch 2
table ($10/$50; VERIFIED against the claude-api reference 2026-07).

- **`backend/qc/` is a structural clone of `research/`** (the port plan is
  complete; QC is native Build-a-Spec, not a Spec Critic port). `engine.py`
  lifts the streaming shape from `research/engine._run_dimension`
  verbatim-in-shape: one synchronous `run_final_qc`, ThreadPoolExecutor cap
  4, the `pause_turn` continuation loop, the 2× search-budget runaway
  ceiling, `sanitize_messages_for_resend` PDF elision on resume, and the
  ported realtime retry policy with cross-attempt billed-usage aggregation.
  `runner.py` is `ResearchRunner` re-typed for QC (daemon thread, event log,
  snapshot, replay-and-follow SSE with `stream_end`, zombie-run abandonment
  on reset/load, meter-before-terminal-flip).
- **Three phases.** (1) Five lenses (`code_compliance`,
  `coordination_consistency`, `completeness`, `enforceability_language`,
  `provenance_hygiene`) fan out over the full `outline(section,
  max_text=None)` rendering + standards block + research profile + the lens
  brief; only `code_compliance` gets web tools (the big search allowance) —
  the rest reason from the document. One lens failing never cancels the
  others; all five failing raises `QCFanoutError` (run fails clean).
  Findings are grounded against retrieved URLs (`validate_cited_sources`,
  same trust model as research). (2) Every finding faces a panel of
  independent refuters (`QC_VERIFIERS_STANDARD` 2 for medium/low,
  `QC_VERIFIERS_CRITICAL` 3 for critical/high); survives iff `upholds >=
  size//2 + 1` (**a tie goes to the refuters**; a dead verifier counts as a
  non-uphold — default-refuted). Verifications for all findings flatten into
  ONE thread pool (per-`(finding, verifier)` task); `verify_progress`
  {done,total} fires as each finding's panel resolves. Surviving severity =
  `median_severity([original, *upheld revisions])`. Refuted findings are
  retained under `QCResult.refuted` (transparency, never shown as issues).
  (3) Deterministic ops validation: each surviving finding's `proposed_ops`
  is dry-run via `apply_edits(deepcopy(snapshot))` (copy per finding — they
  never see each other's effects); invalid → `ops_valid=False` +
  `ops_invalid_reason`, kept advisory, never trusted raw.
- **Content-addressed findings + dismiss memory.** `finding_id = qc- +
  sha256((lens, element_id, title, issue))[:12]`. The runner captures the
  prior result's `dismissed_ids` before `start()` clears it and threads them
  as `remembered_dismissed`; a re-generated finding whose id matches
  auto-marks `dismissed`. Dismiss decisions survive re-runs and the project
  file.
- **Apply is one undo step, staleness-safe.** `POST /api/qc/apply`
  re-validates each finding's ops onto an ACCUMULATING working copy of the
  CURRENT doc (so the combined batch is guaranteed to replay); a finding
  whose target moved raises `SpecEditError` on the working copy → reported
  `stale`, skipped, never partially applied. The combined batch commits as
  one `begin_turn`/`apply_edits`/`commit_turn` (one undo snapshot for the
  whole accept-set); a generation-race after begin rolls back.
- **QC audits a SNAPSHOT** (`SpecSection.from_dict(doc.to_dict())` at start)
  so a streaming turn can't mutate the tree under the call — the audit's
  anti-mutation pattern. `version_index` stamps the reviewed version →
  staleness marker in the drawer / memo / readiness gate.
- **Migration — the compliance audit is deprecated.** The `code_compliance`
  + `completeness` lenses supersede it. The audit BUTTON is retired from the
  UI (`ResearchDrawer` is research-only; the frontend no longer calls
  `/api/audit/*`); the endpoints + `AuditRunner` remain untouched. The main
  export closing renders the QC summary when a QC result exists, else falls
  back to the audit closing (`build_docx(..., qc_result=...)`).
- **Persistence + serialization.** `QCResult.to_dict`/`from_dict` round-trip
  the full result; `spec_doc/project.py` gains a `qc_result` field restored
  via `QCRunner.restore` (same as the audit's). `usage_ledger` gains a `qc`
  category priced on `QC_MODEL`.
- **Tracing.** A `qc` span (`KIND_QC`) with mirrored `qc_progress` events;
  hooks never raise (`capture.qc_start/qc_event/qc_end`).
- **Deliberate non-ports.** Server-side refusal `fallbacks` (recommended for
  Fable 5 by the claude-api skill) is NOT wired: it needs the beta endpoint
  and is out of the batch's plan scope; a refusal surfaces as an incomplete
  stop_reason → the lens fails clean under the existing failure policy.
  Fable 5 requires 30-day data retention — a ZDR org 400s every QC request
  (operational caveat, not a code concern).

## Batch 5 — implemented notes (v1.0.0: redline export + version diff)

The 1.0 release milestone: a `.docx` with genuine Word tracked changes
showing exactly what Build-a-Spec did to the office master. One deterministic
diff engine powers both the export and an in-app compare view. No new SSE
events, no new env vars, no new Python deps (`difflib` is stdlib).

- **The diff engine (`spec_doc/diffing.py`) is pure and deterministic** — no
  model, no I/O. `diff_sections(base, cur) -> SectionDiff` joins the two trees
  by **stable uid** (an id join, never a fuzzy text match): in-both →
  `unchanged`/`changed` by normalized text; cur-only → `inserted`; base-only →
  `deleted`, spliced into the merged order at its base position relative to
  surviving siblings. **Pure moves are NOT marked** (frozen decision — display
  numbering is positional and recomputes; marking a move as delete+insert is
  noise). **Status-only changes** (text identical, provenance status moved)
  land in `status_changes`, never a redline mark. `changed` elements carry
  **word-level** `runs` (`re.findall(r'\S+\s*')` keeping whitespace attached +
  `SequenceMatcher(autojunk=False)`): joining the non-`del` runs reconstructs
  `cur_text` byte-exact, non-`ins` reconstructs `base_text` (stored provision
  text is always stripped, so nothing is lost). Parts (pt1/pt2/pt3) are fixed
  structural headings, never counted in `stats`. `diff_sections` knows nothing
  about "the master" — vs-master is `base = versions[baseline_index]`, vs-empty
  is `base = versions[0]` (the always-present empty snapshot).
- **Baseline bookkeeping.** `DocumentStore.baseline_index` (None for
  from-scratch) is set to the post-import version by `adopt_imported`, cleared
  by `reset`, persisted in `to_dict`/restored in `load` (old files tolerate
  absence + out-of-range → None). `commit_turn` drops it when a new edit after
  undo truncates the version it points at (the master was abandoned). It rides
  the project file for free through `store.to_dict()`/`store.load()`.
- **The redline `.docx` writer** extends `build_docx(..., redline=SectionDiff,
  redline_date=None)`. python-docx has no tracked-changes API, so `w:ins`/
  `w:del`/`w:delText` and the deleted/inserted paragraph *marks*
  (`w:pPr/w:rPr/<w:ins|w:del>`) are built with `docx.oxml`, mirroring the
  shapes the importer's tests manufacture. `w:id` is sequential-unique;
  `w:author = settings.APP_NAME`; `w:date` is ISO-8601 `…Z` (VERIFIED against
  ECMA-376 2026-07). Tabs become `w:tab`; token whitespace uses
  `xml:space=preserve`. The clean (non-redline) body path is extracted
  verbatim into `_render_clean_body` and is **byte-identical to v0.9.0** (a
  test pins it). The empty-part `(Not used.)` line and the section `[TBD]`
  placeholders are tracked (`w:ins`/`w:del` on the side that lacks them) so a
  part that empties/fills and a from-scratch vs-empty redline both round-trip
  exactly. Schedules (assumptions/imported/open-items/QC closing) always render
  plainly from the current section, never redlined.
- **The killer invariant (tested):** re-importing the redlined export through
  the real Accept-All resolver reproduces the current document (numbering
  included); a Reject-All reading (custom test extractor: keep
  `w:del`/`w:delText`, drop `w:ins`, drop paragraph-mark-inserted paragraphs)
  reproduces the baseline's provision **text**. So **Accept All in Word == the
  issued draft, Reject All == the master's provisions.** Display numbering
  (A./1.1/a.) is a positional literal, not tracked content — a survivor whose
  position shifted (a preceding sibling was inserted/deleted) keeps its current
  label under both resolutions (the frozen "moves are not marked" decision), so
  Reject-All is text-faithful, not label-faithful. Making it label-faithful
  would require Word auto-numbering (deferred; see the plan's as-built). Pinned
  by `test_position_shift_accept_exact_reject_text_faithful`.
- **API (REST-only).** `GET /api/doc/diff?base=N[&cur=M]` (cur defaults to
  head; 400 out-of-range or base==cur) returns the serialized diff.
  `GET /api/export/docx?redline=master|version&base=N` streams the tracked-
  changes `.docx` (400 when `redline=master` and `baseline_index is None`;
  filename gains ` - REDLINE`). `_doc_payload` now carries `baseline_index`.
- **Frontend.** `ArtifactPanel` gains a **Compare** toggle (disabled without a
  prior version or master) that opens a base picker (Master pinned first when a
  master was imported, else Blank start / each prior version), a `+N/−M/K
  edited` stat line + status-changes count, and renders the diff read-only via
  `SpecDocument`'s new `diff` prop (ins green/underline, del red/strikethrough,
  inserted/deleted whole-block left-border + badge, status-change footer
  strip). Compare mode exits automatically on any version change (the diff
  would be stale). The single Export button became a small menu: *Export
  clean* / *Redline vs master* (shown only with a baseline) / *Redline vs
  version…* (uses the compare selection). Because the compare view and the
  export read the *same* serialized diff, they match run-for-run.
- **Deviations from the plan:** (1) the round-trip test asserts
  Accept-All(redline) == a clean export of cur (and Reject-All == clean base)
  rather than raw text equality — the clean-export `(Not used.)` line for
  empty parts is pre-existing behavior, so comparing resolved views is the
  honest invariant; both the real importer path and a custom reject reader are
  exercised. (2) The compare view is a `diff` prop *inside* `SpecDocument.tsx`
  (a `DiffDocument` subcomponent) rather than mutating the editable renderer —
  literally "SpecDocument renders diff mode", kept read-only. (3) No vitest was
  added; the diff contract is pinned by the Python suite and the frontend
  consumes the identical serialization.

## Batch 6 — implemented notes (v1.1.0: guided onboarding + starter prompts)

Five starter-prompt chips in the empty chat; the first (verbatim, a frozen
ask: "New to this software, show me how to use this") runs a guided tour of
the whole workflow on a model-drafted demo spec. No new SSE events, no new
deps (Python or npm), one new REST route.

- **The demo rides the Batch 3 directive pattern.** `POST
  /api/onboarding/demo` (`{discipline}`) is thin: 409 while a turn streams
  or research runs, PLUS 409 when the document is non-empty — the tour
  drafts onto a blank page only (the frontend's entry guard offers
  fresh-start first; the endpoint backstops it). Returns `{ok, message:
  onboarding_demo_directive(discipline)}` — server-owned in `prompts.py`,
  sent back through `/api/chat` as a visible user turn.
  `_sanitize_discipline` folds all whitespace (newline-injection guard),
  caps at 80 chars, and falls back to the fire default when empty. The
  directive demands: deliberately SMALL (ONE brief article per PART),
  header via replace-on-`sec` (free-form, so any discipline works at the
  document level), honest provenance (user said nothing → ≈all `assumed`),
  exactly one `[TBD: …]` + one `needs_input` (live material for the tour's
  open-items and review steps), NO profile / NO overrides (later steps
  teach those), ~one-PART-per-call batches, a 2-3 sentence close, and NO
  follow-up questions (the tour drives what happens next).
- **`_ONBOARDING_POLICY`** ("# Guided-tour demo pass") joins the STABLE
  prompt right after `_FULL_DRAFT_POLICY`: honor the stated discipline over
  the catalog steer, small is correct, provenance/spec-language rules
  stand. Module-stable, zero session data (the cache rule).
- **The tour is scripted frontend, never model-driven.** `lib/tour.ts` is
  pure data (the `reviewQueue.ts` convention): STARTER_PROMPTS,
  DISCIPLINES, DEMO_PROFILE, 22 `TourStep`s in 4 chunks (*Reading the page
  / Tell it about the project / Make it yours / Out the door*), and
  `anchorSelector` (data-tour attrs, literal `el-*` ids, and
  first-assumed/first-paragraph resolvers over the doc snapshot).
  `lib/useOnboarding.ts` is the phase machine (`idle → entry-guard →
  discipline → key-gate → generating → touring ↔ chunk-break ↔ paused →
  work-choice`) with a `runId` counter re-checked after every await — the
  frontend mirror of the backend `generation` zombie guard. Caps arrive
  per render via latest-refs (StrictMode-safe); generation only ever
  starts from a click handler; the key-gate effect auto-advances when
  `health.api_key_present` flips (the banner's save already refreshes
  health).
- **Spotlight = no click shield (deliberate posture).** One
  `pointer-events: none` div whose `box-shadow: 0 0 0 9999px` paints the
  dim (box-shadows are never hit-testable) + an accent ring — every
  control stays usable at every step; the tour directs attention, it never
  jails. Anchor pipeline: querySelector retry (150ms × 14, drawers render
  a frame after their nonce bump) → `scrollIntoView` → rAF settle (rect
  stable 2 frames, ≤650ms) → follow via resize + capture-phase scroll +
  ResizeObserver; re-resolve on doc-version change (undo can delete the
  anchor); missing anchor → centered bubble, never a hang. z-layers:
  cutout 60, bubbles/pill 65, tour dialogs 70 (app modals stay 50).
- **`App.send` now returns `Promise<boolean>`** (true = the turn completed
  cleanly); the generating phase awaits it. Existing callers ignore the
  value. Failure → error card with Retry — safe because the failed turn
  rolled back server-side, so the doc is still blank and the 409 guard
  still passes.
- **Drawer opening = optional `openNonce` prop** (Review / Research / QC
  drawers + the panel's open-items block): a bump does `setExpanded(true)`;
  users can re-collapse and the tour never fights back. App owns
  `drawerNonces` + `bumpDrawer` (the `prefill.nonce` idiom).
- **"Do this for me" is deterministic where possible**: the profile step
  POSTs `set_project_profile` through `/api/doc/edit` (DEMO_PROFILE =
  Phoenix, Arizona, USA, "Demo Client (tour)"; the frontend `EditOp` type
  gained the action + fields), the review step confirms
  `buildQueue(doc,"all")[0]` via `set_status`. The research and Final QC
  steps offer REAL runs — "Run it now" + honest cost/time note + a
  prominent skip (Abraham's call, AskUserQuestion 2026-07-22) — gated like
  the drawers' own buttons (`profileComplete`/`hasContent`, not running,
  not busy). The QC run-it intentionally bypasses QCDrawer's confirm
  modal: the bubble's cost note IS that informed-consent step.
- **Fresh-vs-keep**: the work-choice dialog fires on any exit into real
  work (chunk-break "Start real work", the final Finish, the paused pill's
  ✕). Fresh = `newSession()`; keep = end the tour. Either marks
  `onboarding-completed` in localStorage (the codebase's FIRST localStorage
  use — try/caught, cosmetic, drives only the chip sub-line/pulse). The
  Header's "New session" and project load call `onboarding.abort()`; the
  hook's own fresh paths call the RAW `newSession` — never re-wrap it.
- **Re-entry**: a "Tour" button in the Header nav; same entry guard.
- **Tests**: `tests/test_onboarding.py` clones the `test_full_draft.py`
  shape (guards incl. a profile-only non-empty variant pinning the
  `is_empty` gate, directive snapshot, sanitization, stable-policy
  snapshot, and a scripted 3-round e2e). The frontend stays pinned by
  `npm run build` (tsc) — the no-vitest convention stands; a Playwright
  smoke of the pre-generation flow (chips → discipline → key-gate →
  entry-guard → fresh-start) was verified against the real DOM during
  development.

## Batch 7 — implemented notes (v1.2.0: stop generation / research / QC)

Three stop affordances, deliberately NOT uniform in behavior — chat stop
preserves progress (Claude.ai's actual behavior), research/QC stop discards
it (spelled out to the user before they click). Same shape as every prior
batch: no new SSE event types, no new env vars, no new Python deps.

- **Chat stop is graceful, not a rollback.** `Composer` swaps the send
  button for a filled stop-square the instant a turn starts streaming —
  same button, same position, Claude.ai's actual affordance — with no
  confirmation dialog (matching Claude.ai; a chat turn loses nothing by
  stopping, so there's nothing to warn about). `POST /api/chat/stop` sets
  `SessionState.stop_requested`; `stream_user_turn` checks it after every
  streamed event and, on the next check, closes the request via
  `stream.current_message_snapshot` (NOT `get_final_message()`, which would
  drain the rest of the network stream first) and takes the same
  content-truncation branch as a `max_tokens` cutoff. The turn still
  commits normally — history and any document edits from completed rounds
  survive, exactly what the user saw stays. See the Conversation engine
  invariants section for the one added wrinkle (a stop caught between
  rounds, right after a tool dispatch, needs a placeholder assistant
  message so history keeps alternating roles).
- **Research/QC stop is cooperative cancellation, explicitly lossy.**
  `ResearchDrawer` / `QCDrawer` show a **Stop** button only while running,
  gated by the new `ConfirmDialog` (generic reusable confirm modal —
  backdrop/Escape cancel, danger-red confirm) spelling out that progress is
  discarded (the QC dialog also notes the Fable 5 spend already incurred
  isn't refunded). `POST /api/research/stop` / `POST /api/qc/stop` call
  `ResearchRunner.stop()` / `QCRunner.stop()`, which resolve the run as
  `failed` (`"Stopped by user — progress was discarded."`) through a
  lock-guarded compare-and-set (`_try_resolve`) — the SAME single choke
  point every terminal transition goes through (success, failure, or stop),
  so whichever caller gets there first wins and the loser's mutation is
  silently dropped. This is what makes "stop, then immediately restart"
  safe: `stop()` flips status away from `running` synchronously, so a
  fresh `start()` right after is never blocked by the old (still-unwinding)
  background thread, and that old thread's eventual result — discarded by
  `_try_resolve` finding the status already resolved — can never clobber
  the new run. Pinned by `test_research_stop_discards_running_work_and_
  allows_immediate_restart` / the QC equivalent in `test_stop.py`, using the
  same blocking-fake-plus-release-event technique the existing
  double-start/reset-abandons tests already established.
- **No mid-call interruption for research/QC (scoped deliberately).** Unlike
  chat, `_run_dimension` / `_run_streaming_call` still call
  `stream.get_final_message()` — a `should_stop` callback (threaded through
  `run_requirements_research`/`run_final_qc` down to `_run_dimension`,
  `_run_lens`, `_verify_one`) is checked at each worker's entry and before
  each retry attempt / pause_turn continuation, so anything that hasn't
  started its next network call yet bails immediately and for free; a call
  already in flight (bounded by the `ThreadPoolExecutor` cap of 4) completes
  naturally and its result is simply discarded. Restructuring the ported
  research/QC engines to interrupt an in-flight streaming call the way the
  chat loop now does would touch the "hard-won" fan-out machinery for
  marginal benefit given stopping is already lossy by design — not worth
  it. The spend already committed to those in-flight calls is still metered
  into the usage ledger even though the result is thrown away (mirrors the
  existing "the spend is real even on a failed turn" posture).
- **Fake streaming client gains `current_message_snapshot`**
  (`tests/fakes.py`) so `test_stop.py` can exercise the "read the snapshot
  instead of draining the stream" branch. It mirrors `get_final_message()`
  rather than truly accumulating event-by-event like the real SDK (the fake
  replays a fixed script) — sufficient to prove the mechanism (the live SSE
  stream truncates; the turn still commits) without reimplementing the
  SDK's accumulator.

## Batch 8 — implemented notes (v1.3.0: chat figures — diagrams / schematics / tables)

Abraham's ask: the main chat gains the ability to create figures —
diagrams, schematics, data tables — surfaced to the user as download
links. Scoped deliberately to **Tier 1 only** (confirmed twice: "we don't
need tier 2/3 features"): Mermaid diagrams, hand-authored SVG, and CSV
tables, rendered inline in the chat. NOT in scope (by that decision):
`.docx` figure embedding, charts as a distinct type, a persistent figure
gallery panel, and model-side revision of an existing figure. No new
Python deps; two new frontend deps (`mermaid`, `dompurify`).

- **`create_figure` is a second document-adjacent tool** (peer to
  `apply_spec_edits`, defined in `backend/figures.py`), kinds
  `mermaid | svg | table`. It rides the ONE chat/tool loop — no new
  pipeline: `conversation._run_tool` dispatches it, the store stages the
  figure, and a live `figure` SSE event carries the full serialized
  `Figure` to the chat for inline rendering. A `drawing` status hint fires
  on the tool block's start. Bad input becomes an `is_error` tool result
  the model self-corrects from, never a turn failure — exactly the
  `apply_spec_edits` posture.
- **Token discipline is the design's spine** (the whole point of the
  feasibility analysis that preceded it). This app re-bills the ENTIRE
  document context every turn, so figure SOURCE — an SVG is easily
  thousands of tokens — must never land there. It lives only in the
  `FigureStore`: the model's tool RESULT echoes just `{fid, kind, title}`,
  and the per-turn PROJECT CONTEXT carries a one-line stub per figure
  (`context_stubs`), never the markup. This is the fetched-PDF elision
  policy applied to a new artifact class; recurring token cost is a
  rounding error regardless of figure count. Pinned by
  `test_figure_source_stays_out_of_the_next_turns_context`.
- **Turn atomicity now spans THREE stores.** `FigureStore.begin_turn`
  marks the pre-turn size; `commit_turn` keeps the turn's additions;
  `rollback_turn` truncates them — wired into `stream_user_turn` right
  beside the document store's begin/commit/rollback, so a failed or
  abandoned turn leaves no orphan figure. Ids are monotonic and never
  reused (the document-store philosophy): a rolled-back id is skipped, not
  recycled. Reset is IN PLACE (never reassigned, like `DocumentStore`) so a
  zombie turn's commit/rollback settles harmlessly against the cleared
  store; the generation guard already blocks a stale commit.
- **Security is render-time, in three independent layers** (the app runs
  in a pywebview shell with a native `window.pywebview.api` bridge, so an
  injection here is worse than plain-web XSS). (1) Mermaid runs
  `securityLevel: 'strict'` + `htmlLabels: false` — diagram text is data,
  never markup. (2) Every SVG (Mermaid output OR a raw `svg` figure) passes
  through DOMPurify's SVG profile (`<script>`/`<foreignObject>`/handlers/
  `javascript:` stripped). (3) The sanitized SVG renders inside a
  `sandbox=""` iframe (no `allow-scripts` → no execution, no
  `allow-same-origin` → no bridge reach) whose `srcdoc` carries a strict
  CSP (`default-src 'none'`) blocking every external resource load. The
  server NEVER serves executable SVG: SVG/PNG downloads are built
  client-side from the already-sanitized string (`lib/figures.ts`); only
  CSV is a server route, emitting `text/csv`. Tables render as plain
  React-escaped HTML.
- **Inline in the chat, no gallery** (the Tier-1 scope call). A figure
  attaches to the assistant bubble that created it (`ChatMessage.figureIds`
  → `FigureCard`), appearing the instant the `figure` event streams. It
  persists in the project file (optional `figures` block on the store's
  `to_dict`, no format bump, graceful-degrade on absence) and re-inlines on
  reload via a stored `message_index` (the ordinal among assistant bubbles,
  computed from `chat_transcript` at creation).
- **REST surface** (all thin): `figures` on every `_doc_payload`,
  `GET /api/figures` (standalone snapshot), `GET /api/figure/{fid}/csv`
  (table figures only; 400 non-table, 404 unknown), `DELETE
  /api/figure/{fid}` (409 while a turn owns the store — a mid-turn delete
  would shift the index the rollback bookkeeping relies on). No undo of a
  delete (figures are not version-tracked — a deliberate MVP simplification;
  the model can regenerate).
- **`_FIGURE_POLICY`** joins the STABLE prompt after `_WEB_LOOKUP_POLICY`:
  figures are exhibits, never a substitute for a provision (the enforceable
  words stay in `apply_spec_edits`); most turns need none; kind selection
  (mermaid = flow/sequence/decision, svg = spatial schematic, table =
  schedule); no source pasted into chat. Module-stable, zero session data
  (the cache rule).
- **Frontend deps**: `mermaid` is lazy-loaded (`import('mermaid')`) so its
  large bundle splits into its own chunk and only loads when a figure needs
  it; `dompurify` ships its own types. The no-vitest convention stands —
  the figure contract is pinned by `test_figures.py` (24 tests) and the
  frontend by `npm run build` (tsc).

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
