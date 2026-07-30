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
  `tools/qc_verifier_canary.py --run` is the sole explicit paid exception: one
  low-token QC verifier request for provider-side strict-schema acceptance,
  never a full Final QC run. Without `--run` it performs no request.
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
- Commit style is a standing owner preference (Abraham): commit messages are
  sassy, spicy, and funny where warranted — especially when something fought
  back — never obnoxious.

## Layout

```
main.py                    entry point: diagnostics.init_logging() FIRST, then
                           uvicorn thread (log_config=None so uvicorn loggers
                           propagate to the diagnostics file handler;
                           access_log off — the app middleware is the access
                           log) + pywebview window (debug=dev_mode());
                           _CloseController offers to save progress on window
                           close (closing-event veto → off-thread frontend
                           prompt → js_api save_and_close/discard_and_close,
                           native save via webview.FileDialog.SAVE; never traps
                           the user); Developer tools reuses open_external_link
                           for the trace viewer; the pywebview-fallback except
                           now logs
backend/
  settings.py              models (claude-sonnet-5 default), effort levels
                           (interview high / research high, dialed back
                           2026-07-28 from xhigh — cost), max_tokens at
                           the 128k model ceiling, chat web-tool allowances,
                           port 8756, env knobs; PRICING carries BOTH
                           cache-write rates per model (cache_write = 1.25×
                           input for the 5m entry, cache_write_1h = 2.0× for
                           the 1h one) — a new model must land with both or
                           every 1h write on it is silently underpriced
  app.py                   FastAPI app factory; SSE at POST /api/chat; POST
                           /api/draft/full (Batch 3 directive); doc/undo/redo/edit,
                           docx export, project save/load endpoints; Batch 4 adds
                           /api/qc/start|status|stream|apply|apply/preview|
                           dismiss|export +
                           /api/qc/export.json + /api/readiness (audit endpoints
                           kept, deprecated); Batch 5
                           adds GET /api/doc/diff + ?redline=master|version on
                           /api/export/docx (+ baseline_index in _doc_payload);
                           the tutorial is NOT frontend-only any more: it owns
                           /api/tutorial/status|start|enrich|scenario/start|
                           scenario/finish|restore|keep, and ~two dozen unrelated
                           routes call _stale_tutorial_response() for the same
                           lease check; templates own /api/templates(+preview,
                           import, {id}/export, {id}/instantiate);
                           Batch 7 adds POST /api/chat/stop + /api/research/stop
                           + /api/qc/stop (409 when nothing is running/streaming);
                           Batch 9 adds suggested_prompts to _doc_payload (no new
                           endpoint — the suggest_prompts SSE event rides /api/chat);
                           Batch 10 adds an optional reset body {module_id,
                           discipline} + GET /api/modules + health.discipline +
                           a 400 research backstop (generic module, no discipline);
                           the diagnostics batch adds the _request_diagnostics
                           middleware (per-request log + api_request trace event,
                           quiet-path list), catch-all 500 + 422 handlers in the
                           {ok,error,code} idiom, ~25 app_event capture sites,
                           and GET/POST /api/diagnostics(+/log,/traces,/activity,
                           /bundle,/client-event) behind Settings → Developer tools
  standards.py             [PORT: Spec Critic src/core/code_cycles.py]
                           StandardEdition (+title for REFERENCES) / BaseCode /
                           StandardsBasis; effective_editions (pins + overrides −
                           per-doc suppressions; EffectiveEdition.is_added for
                           user-added non-pins); standards_context_block (marks
                           added + lists intentionally-excluded standards);
                           validate_overrides_shape (+optional title) /
                           validate_suppressed_shape; Batch 10 adds
                           StandardsBasis.unpinned (sanctioned pinless basis +
                           its own context-block posture)
  project_profile.py       [PORT: Spec Critic src/core/project_profile.py]
                           ProjectProfile: US/CA tables, country/state
                           normalization, web_search_user_location, fingerprint
  runtime_context.py       the wall clock, for the model's benefit: local
                           timezone-aware current_datetime (injectable `now`)
                           + current_date_iso + date_context_block(with_time)
                           and the shared DATE_AWARENESS_DIRECTIVE. No I/O, no
                           app imports. Four consumers (chat context, research
                           fan-out, QC fan-out, the deprecated audit); the two
                           fan-outs read it ONCE per run and thread the string,
                           because it leads their cached shared prefixes
  research/engine.py       [PORT: Spec Critic src/research/requirements_research.py]
                           the fan-out: ThreadPoolExecutor over module dimensions,
                           pause_turn continuation loop, 2× search-budget ceiling,
                           structured→tagged-JSON parse, grounding, retries with
                           billed-usage aggregation; RequirementsProfile +
                           render_text + research_context_block (trim-to-cap);
                           ResearchRound + append_research_round: rounds APPEND
                           (item_id join, evidence-only upgrade, cumulative
                           dimension view, per-item as-of dates, pure/no-mutate);
                           incomplete_dimensions / dimension_display_title /
                           incomplete_dimension_facts + DimensionStatus.
                           error_kind: missing coverage is NAMED, not counted
                           (see "Incomplete research coverage is named");
                           Batch 7 threads a should_stop callback into
                           _run_dimension (checked before each retry/continuation
                           — cooperative, not mid-call interruption); Batch 10
                           threads discipline into the dimension kwargs (set
                           unconditionally — no KeyError) + the header line
                           (only when non-empty — curated runs byte-identical);
                           the live-visibility batch makes workers NARRATE:
                           _run_dimension takes event_sink, emits
                           dimension_started/retry, and _relay_stream_activity
                           (copy-adapted from conversation._stream_events)
                           iterates the raw stream INSIDE the existing `with`
                           before get_final_message() — live activity/search/
                           fetch events, server-tool inputs buffered ONLY,
                           per-frame try/except (a malformed frame never fails
                           a dimension), no early break (stop semantics
                           unchanged)
  research/grounding.py    [PORT: source_grounding.py + verifier collectors]
                           normalize_url, validate_cited_sources, evidence
                           collectors, stop-reason classes
  research/retry_policy.py [PORT: verification/retry_policy.py realtime subset]
  research/resend_sanitizer.py  [PORT ≈verbatim: fetched-PDF elision; pypdf]
  research/schema.py       [PORT: structured_schemas.py research slice +
                           api_config.py web-tool builders + domain blocklist];
                           WEB_TOOL_ALLOWED_CALLERS pins direct invocation on
                           both web tools — the one choke point all three
                           channels (chat / research / QC) build them from
  research/runner.py       session-bound run lifecycle: daemon thread, event
                           log, snapshot, SSE follow generator (Build-a-Spec
                           native — no Spec Critic source); Batch 7 adds stop()
                           (per-run cancel_event + race-free _try_resolve). The
                           snapshot's per-dimension view now carries the human
                           dimension title (DimensionStatus.title, defaulted +
                           serialized) for the findings-report headings.
                           start() no longer clears profile_result — a run is
                           the NEXT ROUND: _try_resolve takes an `adopt`
                           callable that folds the round in under the same CAS
                           lock, the meter still gets the round's OWN usage
                           (the merged total is cumulative), and a
                           failed/stopped round says earlier rounds survived.
                           A round where EVERY dimension failed or was
                           cancelled is metered too (ResearchFanoutError
                           .usage_totals), BEFORE the CAS and unconditionally
                           — a stop has already resolved the run by then, so
                           gating it on winning would drop the spend.
                           The live-visibility batch adds a per-start run
                           token (QCRunner pattern, one notch stronger:
                           _try_resolve CLEARS it on any terminal win): _emit
                           drops stale-token events, so a stopped run's
                           still-unwinding workers can neither append to a
                           later round's log nor — via the token check in
                           _try_resolve — adopt their discarded round once
                           the next one is RUNNING; sse_events binds to the
                           token at call time and closes `superseded`
  updates.py               [PORT ≈verbatim: Spec Critic src/core/updates.py]
                           GitHub-Releases manifest updater: https-only +
                           redirect-downgrade guard, SHA-256 verify before
                           launch, atomic .part promote, throttle/skip state;
                           + last_seen_version/mark_version_seen (the
                           What's-new marker rides the same state file)
  release_notes.py         the user-facing changelog SHIPPED IN THE BUILD:
                           ReleaseNote/Section/Item + RELEASE_NOTES (newest
                           first), resolve_pending (seen → newer-than-seen;
                           no marker but ran_before → everything since
                           EARLIEST_KNOWN_VERSION; fresh install → nothing),
                           manifest_summary (latest.json notes — what a
                           NOT-yet-updated app reads) + markdown_notes (the
                           release page). No I/O, no app imports. A version
                           with no entry fails the suite AND the workflow
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
                           research/schema; Opus 5 added to _STRICT_CAPABLE_MODELS) +
                           observable reviewed-check/finding/verdict normalization
                           (no hidden reasoning) + median-severity math; Chunk 5.2
                           adds submit_qc_consolidation + normalize_consolidation
                           (SHAPE only — the partition is the engine's to validate
                           against the bucket it asked about)
  qc/engine.py             [Batch 4, pattern: research/engine.py] run_final_qc:
                           lens fan-out (ThreadPool cap settings.QC_MAX_WORKERS
                           = 8, pause_turn loop, 2×
                           search ceiling, PDF elision, retry policy, grounding) →
                           Chunk 5.2 cross-lens consolidation (hard-compatible
                           buckets → one grouping call each → strict partition
                           validation → singletons on ANY failure; QCCandidateOrigin
                           / QCConsolidationGroup / QCConsolidation persisted) →
                           adversarial verification panel (tie→refuters) → ops
                           dry-run validation → audit-grade QCResult (versioned
                           run/input identity; complete lens, source, verifier-seat,
                           ops/disposition, usage/cost and limitation evidence;
                           content-addressed findings + dismiss memory); Batch 7
                           threads should_stop
                           into _run_lens/_verify_one (same cooperative pattern
                           as research/engine.py); Batch 10 threads discipline into
                           the lens user message (<project_discipline>, only when
                           non-empty); the Review Room batch relays observable
                           activity/search/fetch/retry frames from each open lens
                           and verifier stream, plus explicit verification and
                           local-validation phase events (never hidden reasoning)
  qc/runner.py             [Batch 4, pattern: research/runner.py] QCRunner:
                           daemon thread, event log, snapshot, SSE follow +
                           stream_end; accept/dismiss mutators under lock;
                           Batch 7 adds stop() (same cancel_event/_try_resolve
                           pattern as research/runner.py); Review Room keeps the
                           existing endpoints and run-token isolation, exposes
                           top-level error_kind, and holds stopped attempts in a
                           truthful settling state until in-flight work unwinds
  tracing/                 [PORT: Spec Critic src/tracing/ core, since diverged]
                           recorder (JSONL spans/events/prompts + run.json,
                           writer thread, per-line flush, ContextVar parent
                           for span() only — the ported thread-local span
                           stack is REMOVED: capture spans close on daemon
                           threads where it leaked stale parents), spans (BAS
                           kind vocabulary), config (BUILD_A_SPEC_TRACE*,
                           default on), redaction (credential patterns;
                           token(?!s) so usage counts survive); capture.py =
                           native never-raise hooks incl. app_event/
                           turn_round/turn_prompts; research_event/qc_event
                           rename the sink event's "type" key to event_type
                           (it collided with add_event's positional arg — a
                           swallowed TypeError meant NO research/QC progress
                           event ever reached a trace until the
                           live-visibility batch); viewer/trace_viewer.html
                           is a native self-contained rewrite (no CDN)
  diagnostics.py           always-on activity log (RotatingFileHandler in
                           <state dir>/logs beside traces/, BUILD_A_SPEC_LOG*
                           knobs, third-party loggers tamed) + crash capture
                           (faulthandler, sys/threading excepthooks,
                           unclean-shutdown run marker) + the read-only
                           helpers behind /api/diagnostics* (snapshot,
                           tail_log, list_trace_runs, read_recent_trace_
                           events, build_bundle) — key material never enters
                           any of it (key_status masked + scrub_data)
  app_paths.py             [PORT: Spec Critic src/core/app_paths.py]
  api_key_store.py         [PORT: Spec Critic src/core/api_key_store.py + save_api_key]
                           Batch 2 adds key_status (masked, never leaks) + delete_api_key
  usage_ledger.py          [Batch 2] session-scoped billed-usage ledger (interview/
                           research/audit/qc), thread-safe, cost estimate from
                           settings.PRICING; not persisted (per-session meter).
                           Chunk 4.1 adds per-TTL cache-write accounting:
                           usage_to_dict flattens the provider's nested
                           usage.cache_creation.ephemeral_1h_input_tokens to
                           cache_creation_1h_input_tokens, and cache_write_split
                           (+ estimate_usage_cost) charge the 5m and 1h rates
                           over DISJOINT slices — the subtotal rides INSIDE
                           cache_creation_input_tokens, so adding it would
                           double-bill and ignoring it would under-bill.
                           Chunk 4.4 adds ESTIMATED_OUTPUT_TOKENS_KEY /
                           USAGE_ESTIMATED_KEY (the module owns the usage-key
                           vocabulary; conversation.py imports them): the
                           stopped-turn output estimate, priced at the OUTPUT
                           rate because it is DISJOINT from output_tokens —
                           the exact opposite of the 1h subtotal above. add()
                           now rejects bools (isinstance(True, int) is True)
                           and snapshot() DERIVES includes_estimated_output
                           from the counter.
                           The context gauge is deliberately NOT here: it lives on
                           SessionState.last_context_tokens (a gauge, not spend —
                           the ledger's snapshot/merge tutorial plumbing is
                           additive and would corrupt it): the Anthropic-counted
                           conversation size after the last committed chat turn —
                           its final request's prompt (input + cache r/w) plus
                           that reply's retained non-thinking output — written
                           only in the guarded commit block, cleared on
                           reset/load, served as `context` {tokens, window:
                           settings.MODEL_CONTEXT_WINDOW (1M; env
                           BUILD_A_SPEC_CONTEXT_WINDOW pairs with a model
                           override)} | null beside the /api/usage + session-
                           bundle snapshot (app._usage_payload)
  figures.py               [Batch 8] chat-authored figures: Figure + FigureStore
                           (per-turn atomic like DocumentStore — begin/commit/
                           rollback, monotonic never-reused ids, validation, CSV
                           render, source-free context stubs, project persistence)
                           + CREATE_FIGURE_TOOL. Figure SOURCE never enters the
                           re-billed doc context or tool results (PDF-elision
                           posture) — only id/kind/title do; recurring token cost
                           is negligible regardless of figure count
  suggestions.py           [Batch 9] model-driven reply chips: MAX_PROMPTS/
                           MAX_PROMPT_CHARS, SuggestError, validate_prompts (strict,
                           fold-whitespace/dedupe/cap; empty list valid) +
                           restore_prompts (lenient project loader) +
                           SUGGEST_PROMPTS_TOOL. Latest-only session state, tiny
                           payload — no store, no elision (rides history verbatim)
  reference_docs.py        user-attached background documents the model reads FROM
                           and never edits: ReferenceDoc (+ kind) + ReferenceDocStore
                           (monotonic ids, loud truncation at MAX_TEXT_CHARS,
                           body-free metadata()/context_stubs(), lenient project
                           load) + READ_REFERENCE_DOC_TOOL. NOT turn-atomic (they
                           arrive over REST, not in a turn) — reset in place only.
                           The body never enters PROJECT CONTEXT and is elided
                           from committed history (PDF posture); the model re-reads
                           on demand. context_stubs() shows the REAL Anthropic-
                           counted token_count (post-truncation — the number the
                           100k cap and the panel use), never a chars/4 guess
  reference_extract.py     the attachment → text boundary: REFERENCE_KINDS
                           (.docx/.pdf/.txt/.xml/.csv) + labels, kind-for-filename,
                           sanitize_reference_filename (keeps the file's own
                           extension — the shared sanitizer appends .docx),
                           extract_reference_document dispatch. docx delegates to
                           the importer behind inspect_docx_package; pdf is pypdf
                           per page with [page N] markers (page cap, owner-password
                           unlock, no-text-layer refusal); txt/xml/csv decode
                           through a BOM/UTF-8/cp1252/latin-1 ladder with a NUL
                           binary guard, structure kept verbatim. Blocking — worker
                           thread only
  templates.py             reusable semantic templates: TemplateCatalog (curated
                           + personal libraries, preview→commit two-phase create,
                           Exact vs AI-Generalize, import/export/instantiate);
                           templates/curated/*.bastemplate ship with the app
  tutorial.py              tutorial fixtures + coverage: analyze_tutorial_coverage,
                           tutorial_enrichment_directive, validate_tutorial_
                           enrichment (additive-only proof), build_showcase_session,
                           repair_tutorial_copy, and the practice-copy builders
                           (blank / detached / structural / review / references)
  sessions.py              SessionState (history + DocumentStore
                           + SpecModule + discipline (Batch 10, session-level
                           like module) + ResearchRunner + AuditRunner + QCRunner
                           + FigureStore + ReferenceDocStore + UsageLedger +
                           suggested_prompts) + SessionManager, which owns the
                           ACTIVE one across three scopes (original → tutorial →
                           scenario, never nested): begin_tutorial (idempotent per
                           request_id, refuses while busy), push/pop_scenario,
                           replace_tutorial, finish_tutorial(restore|keep),
                           clone_session_for_tutorial. Every scope change mints a
                           new workspace_id; that + generation is the lease the
                           tutorial routes re-check —
                           has_unsaved_progress /
                           project_payload / project_default_stem /
                           project_default_filename (timestamped
                           buildaspec-<stem>-<YYYY-MM-DD-HHMMSS>.json, so
                           same-day re-saves never collide; shared by
                           /api/project/save and the native save-on-close)
  spec_modules/base.py     [PORT: Spec Critic src/modules/base.py]
                           frozen SpecModule (catalog, playbook, prompt slots, lint
                           vocabulary, dormant research dimensions); import-time
                           validate_module_registry — bad module = startup failure.
                           ResearchDimension.required (default True) +
                           optional_rationale are an issue-READINESS policy,
                           validated as a bound pair both ways
  spec_modules/registry.py [PORT: Spec Critic src/modules/registry.py]
                           AVAILABLE_MODULES / DEFAULT_MODULE / get_module
                           (unknown id degrades to default, never errors)
  spec_modules/hyperscale_fire.py
                           [SEED: Spec Critic src/modules/datacenter_fire.py]
                           first module: 21 13 13 lead + sibling catalog, playbook,
                           current-edition NFPA pins w/ provenance, research dims
  spec_modules/generic.py  [Batch 10, native] the any-discipline module (USA &
                           Canada): unpinned basis (NO pins — editions enter via
                           set_standard_edition w/ stated basis), open_catalog
                           (empty catalog; section from the session discipline),
                           generic scaffold playbook w/ META-defaults,
                           {discipline}-parameterized research dimensions
  spec_doc/model.py        SectionFormat tree; stable ids (pt1.a2.p3); statuses
                           (confirmed/assumed/needs_input/imported);
                           transactional apply_edits; edition_overrides +
                           project_profile + suppressed_standards on the tree
                           (set_standard_edition gains optional title for adds;
                           set_standard_suppressed excludes/restores a standard,
                           reason optional); DocumentStore (per-turn
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
                           OR w:numPr ilvl); keep-everything-warn-loudly.
                           spec_shape_detected: True iff the parse recognized a
                           SECTION line / PART heading / N.M article (the same
                           three signals it acts on, so the verdict can never
                           disagree with the tree) — False prepends
                           UNSTRUCTURED_IMPORT_WARNING and rides the persisted
                           import_report. extract_reference_text() is the
                           no-tree counterpart for attached reference documents
  spec_doc/linting.py      [PORT: Spec Critic src/input/preprocessor.py logic]
                           deterministic advisory lint: stale editions vs effective
                           pins (negation suppression), placeholders/markers,
                           empty/duplicate articles, unset header (suppressed via
                           unstructured_import= for a non-spec import — nothing
                           else is gated); Batch 10 adds
                           unrecorded_edition (unpinned modules ONLY: designation
                           cited w/ a year but absent from effective_editions —
                           publisher-grammar discovery, then the same four
                           citation shapes/suppression as the stale rule);
                           Chunk 5.2 adds duplicate_provision (SIBLING paragraphs
                           only, ≥25 chars, numeric tokens must match before any
                           similarity ratio — a dimension or article number
                           differing is decisive evidence AGAINST duplication)
  spec_doc/docx_export.py  python-docx rendering + assumptions/open-items schedules;
                           Batch 4 adds build_qc_memo (full standalone Final QC
                           Word report) + a QC
                           closing that supersedes the audit closing in build_docx;
                           Batch 5 adds the redline body writer (build_docx(...,
                           redline=SectionDiff): w:ins/w:del/w:delText + para-mark
                           ins/del via docx.oxml; clean path untouched, byte-stable)
                           + redline_filename
  spec_doc/project.py      JSON project files (save/resume) + chat transcript +
                           module_id + legacy discipline fallback (the versioned
                           document project_identity is authoritative) + audit_result +
                           qc_result (baseline_index rides store.to_dict/load — no
                           project.py change); Batch 9 adds an optional
                           suggested_prompts key (omitted when empty;
                           restore_prompts on load, assigned unconditionally)
  llm/server_tool_pairing.py
                           the use/result pairing invariant: turn-wide,
                           copy-on-write, duck-typed over dicts + SDK blocks.
                           Its own module because three packages need it
                           (conversation, resend_sanitizer, project load) and
                           a private helper in conversation.py could not be
                           reached from resend_sanitizer without a cycle
  llm/client.py            client factory; MissingApiKeyError; per-key cache
  llm/prompts.py           engine protocol blocks + render_system_prompt(module);
                           FULL_DRAFT_DIRECTIVE (Batch 3 full-draft user message);
                           Batch 9 adds _SUGGESTED_PROMPTS_POLICY (after
                           _FIGURE_POLICY);
                           Batch 10 splits sanitize_discipline (public, no
                           fallback — shared w/ reset + project load), renders
                           open-catalog guidance in _render_catalog, and rewords
                           _STANDARDS_POLICY/_PROVENANCE/FULL_DRAFT_DIRECTIVE
                           to stay true for pinless modules
  llm/conversation.py      stream_user_turn generator; tool dispatch + continuation;
                           lint event + standards_payload; PROJECT CONTEXT begins
                           with versioned discipline/project type identity, using
                           legacy session discipline only when identity is absent;
                           Batch 7 adds
                           SessionState.stop_requested (threading.Event) — a
                           user stop ends the round loop early but still
                           commits (current_message_snapshot, not
                           get_final_message(), so the closed request doesn't
                           drain); Batch 9 adds the suggest_prompts tool
                           (_run_suggest_prompts + turn-local staged_suggestions
                           committed beside doc/figures — latest-only replace) +
                           SessionState.suggested_prompts
frontend/src/
  App.tsx                  state owner: messages[], doc, open items, lint issues,
                           standards, changed ids, health, usage, qc, readiness,
                           baselineIndex, suggestions (Batch 9 reply chips),
                           NewSessionDialog (blank slate active; template choices
                           visible but disabled),
                           settings-open, closePromptOpen
                           (window.buildaspecRequestClose hook), send loop (SSE
                           switch incl. status/thinking_delta); QC follow-stream
                           + accept/dismiss; Batch 6: drawerNonces + useOnboarding
                           wiring, send → Promise<boolean> (clean-turn signal);
                           addNote posts a terse, non-conversational chat marker
                           when research / Final QC starts (acknowledges the
                           panel-button click in the chat window); Final QC follows
                           its chatty SSE log locally, reconciles milestone
                           snapshots, reconnects unexpected closes while running
                           or settling, and auto-expands once on successful start
  lib/api.ts               streamChat async generator; doc/undo/redo/edit/project;
                           draftFull; key status/delete/test; usage; Batch 4 qc
                           start/status/stream/apply/dismiss + readiness; Batch 5
                           getDocDiff; Batch 7 stopChat/stopResearch/stopQc (409
                           from an already-settled run/turn is swallowed, not
                           thrown); resetSession(opts?) retains compatibility APIs
  lib/qcLive.ts            Final QC's pure live-state layer: discriminated event
                           fold, seq-deduplicating merge, same-run snapshot
                           reconciliation, milestone policy and three-stage board
  lib/eventSeqIndex.ts     the {min, max, missing} sequence index BOTH live
                           followers dedupe replay against — memoized per
                           events array, derived in O(1) on append, so a
                           reconnect's replay is one pass and not a scan per
                           frame (see "Constant-time replay dedupe" below).
                           Imported WITH its .ts extension on purpose: `npm
                           test` runs node --test over the sources and Node's
                           resolver requires a real extension
  lib/useSmoothText.ts     [Batch 2] rAF typewriter smoothing + reduced-motion +
                           splitStableTail (cheap-markdown prefix/tail split)
  lib/reviewQueue.ts       [Batch 3] pure buildQueue(doc, mode) — the review
                           queue as a document-order walk (port of iter_paragraphs);
                           reviewCounts (outstanding imported/assumed)
  lib/qcReport.ts          pure audit-report helpers: coverage/limitations,
                           safe source links, formatting for identity, lens/seat
                           telemetry, operations/dispositions, usage and cost;
                           qcResearchCoverage mirrors docx_export's
                           qc_research_coverage so both projections read the
                           CAPTURED research manifest the same way
  lib/capabilities.ts      END_USER_CAPABILITIES: the one vocabulary of end-user
                           capability ids. Production controls declare them via
                           data-capability; tour.ts steps reference them; the
                           tour test asserts set equality BOTH ways. Adding one
                           is a three-place edit (see "Capability coverage")
  lib/tour.ts              the versioned tutorial manifest: starter prompts,
                           TOUR_VERSION, chunks (each with an optional backend
                           `scenario`) → steps (capabilities, mode, anchor or
                           document `resolve`, `drawer`, `readiness`, `actions`).
                           anchorSelector() resolves a step against the LIVE
                           SpecDoc; capabilityCoverage() flattens the contract
  lib/useOnboarding.ts     the tutorial lifecycle machine: workspace start /
                           enrich / scenario swap / restore / keep, chapter
                           jump, stale-generation rejection, resume persistence;
                           endConfirm flag (requestEnd/cancelEnd) gates every popup close (✕ /
                           backdrop) behind an end-or-continue confirmation —
                           orthogonal to phase, so "Continue" restores the popup
                           untouched and abort/start clear it
  lib/onboardingStorage.ts [Batch 6] "tour completed" flag — the codebase's first
                           localStorage use; try/caught, cosmetic only
  lib/figures.ts           [Batch 8] figure render + security helpers: DOMPurify
                           SVG sanitize, lazy mermaid.render (securityLevel strict,
                           htmlLabels off), sandbox-iframe srcdoc with a strict CSP
                           (default-src none), canvas SVG→PNG, SVG/CSV blob
                           downloads — the render-time sanitization boundary for
                           model-authored markup (never inline into the bridge DOM)
  components/*             Chat (Batch 6 starter chips in the empty state) /
                           MessageBubble (smoothing + thinking block; renders a
                           ChatMessage.note as a compact centered event marker) /
                           Composer (WI2 ask-model prefill; Batch 7 swaps the send
                           button for a stop-square while streaming, Claude.ai-style
                           — always clickable, no confirmation) / ArtifactPanel
                           (stepper, Batch 5 Compare toggle + base picker + stat line
                           + export menu, save/open, ⚠ badge, "Draft full section"
                           button, open items) / ReviewDrawer (Batch 3 keyboard
                           review walk) / IssuesDrawer (lint + StandardsStrip —
                           editable: add a standard, edit an edition, or
                           exclude/restore any standard per document, all via
                           /api/doc/edit) /
                           ResearchDrawer (research only — audit UI retired in
                           Batch 4; also hosts the project-profile form for direct
                           upfront entry; Batch 7 adds a Stop button while running,
                           gated by ConfirmDialog; a "View report" button opens
                           ResearchReportModal; the live-visibility batch turns
                           the running body into the per-agent board —
                           foldResearchBoard over research.events → AgentCard
                           per dimension with breathing .agent-dot, status-dots
                           + shimmer activity line, sliding recent queries/URLs,
                           .tally-flash counters, retry notices, and the report
                           modal's exact telemetry line on completion; the
                           header summary + start label read the fold's
                           done/total, never events[last]) / ResearchReportModal (the full
                           research findings report — a read-only modal grouping
                           the completed profile's items by dimension/agent with
                           per-dimension telemetry + full item detail; the same
                           profile already rides the chat model's per-turn context)
                           / QCDrawer (Batch 4: readiness and compact
                           accept/dismiss fix queue, hold-to-apply-criticals;
                           Batch 7 adds a Stop button while running, gated by
                           ConfirmDialog; Review Room replaces readiness during an
                           attempt with specialist cards → candidate verifier
                           panels → local fix validation, then a recap and the
                           existing remediation controls) /
                           QCReportModal (complete read-only Final QC audit report,
                           no content truncation, DOCX + JSON downloads) /
                           SpecDocument (paper rendering + inline manual-edit
                           affordances; Batch 5 read-only diff render via `diff` prop)
                           / Header (spend ticker + context pill "142k / 1M" from
                           usage.context, hidden until a turn commits; Batch 6 Tour
                           button) / ApiKeyBanner /
                           StatusStrip (live status strip) / SettingsPanel (key mgmt +
                           usage table + ContextLine gauge + What's new + developer
                           tools) / WhatsNewModal (release notes after an update —
                           opens once from App's mount check, reopened on demand
                           from Settings; useDialogFocus, z-[60] scroll sheet)
                           / CloseDialog (save-before-leaving
                           prompt: Save & close / Close without saving / Cancel)
                           / OnboardingOverlay (Batch 6: blocking spotlight + passive
                           step bubbles; drawers gain an openNonce prop, controls
                           gain data-tour anchors; every popup close (✕ / backdrop)
                           routes to ob.requestEnd, and Escape yields to the confirm
                           while it's open) / ConfirmDialog (Batch 7: generic
                           title/body/confirm/cancel modal — the lose-progress
                           warnings for stopping research/QC; the Final-QC launch
                           confirmation stays its own purpose-built modal; an
                           `elevated` prop (z-80) lets App host the tour's
                           end-or-continue confirm above the overlay's own modals) /
                           ModalShell (Batch 10: extracted from OnboardingOverlay
                           + primaryBtn/quietBtn, shared) / NewSessionDialog
                           (blank slate plus disabled template choices) /
                           FigureCard (Batch 8: inline figure render — sanitized
                           SVG/mermaid in a sandbox="" iframe, escaped data table,
                           SVG/PNG/CSV downloads + a ✕ to remove) /
                           SuggestedPrompts (Batch 9: model-staged reply-chip bar
                           between the scroll region and Composer — rounded-full
                           accent pills, hidden when empty, disabled while
                           streaming, click sends via onSend; .prompt-chip-in
                           rise-in, reduced-motion-gated)
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
                           coverage-blocked readiness, full Word + JSON reports,
                           audit-record persistence, QC-model-priced usage
  test_qc_audit_report.py  [Batch 4] audit-grade identity/evidence/coverage,
                           full Word + JSON fidelity, failed-latest vs retained
                           success, persistence hardening, dispositions and cost
  test_qc_runner_audit_integrity.py
                           [Batch 4] coherent runner snapshots, terminal partial
                           records, restoration and dismissal audit invariants
  test_qc_manifest_integrity.py
                           [Batch 4] source bytes/map/baseline/patch-context
                           constituent invalidation for full-input identity
  test_qc_consolidation.py [Chunk 5.2] cross-lens grouping: one defect one panel,
                           hard-compatibility gates, ops reconciliation incl. the
                           SF-009/SF-023 shape, every failure path falling back to
                           singletons, determinism, the membership-in-the-hash pin,
                           live/report reconciliation, partition integrity on load
  test_diffing.py          [Batch 5] diff_sections units: identical/insert/delete/
                           text-edit (byte-exact run invariants) / nested / article
                           title / move-not-marked / status-only / section header /
                           vs-empty / token_runs whitespace / serialization
  test_redline_export.py   [Batch 5] Accept-All==cur & Reject-All==base round-trip
                           (real importer + custom reject reader), XML shapes
                           (author/date/unique id, w:delText not w:t, para-mark
                           ins/del), doc/diff + redline API validation, no-baseline
                           400, baseline_index project round-trip, clean-path no-marks
  frontend/tests/tour.test.ts
                           [Batch 6] passive tour-data invariants and current anchors
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
  test_suggested_prompts.py [Batch 9] validate_prompts/restore_prompts units
                           (fold/dedupe/cap, empty valid, lenient degrade) +
                           suggest_prompts through /api/chat (SSE event + commit,
                           token-discipline result + no-elision, not-called clears,
                           failed turn preserves prior, is_error self-correction,
                           empty clears, latest-call-wins, reset clears) +
                           project save/load round-trip + stable-policy/demo pins
  test_session_modules.py  [Batch 10] reset-with-body matrix (bodyless keeps,
                           switch + invariant, unknown degrades, sanitize),
                           GET /api/modules, discipline in context / not the
                           stable block, project round-trip + old-file compat +
                           invariant-on-load
  test_research_rounds.py  rounds APPEND: the merge (add / confirm-in-place /
                           no-mutate / cumulative-vs-summed counts / a
                           dimension that failed this round), rendering (one
                           round byte-identical, many rounds dated per item),
                           serialization + legacy-file synthesis, the runner
                           (accumulate, meter each round once, failed and
                           stopped rounds keep the rest), and the whole thing
                           over the API + a save/resume that keeps counting
  test_runtime_date.py     the model is told what day it is: helper units
                           (local + aware, injected now, zone label), the date
                           in chat context but NOT the cached system prompt +
                           no fossilization into history, and the counter-clock
                           pins — one read per research round (== the round's
                           own research_date stamp) and one per QC run (both
                           cached prefixes), plus the deliberate
                           not-in-the-input-manifest decision
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
| `suggested_prompts` | `prompts` | the model staged up to 5 one-tap reply chips via `suggest_prompts` this round (Batch 8→9), shown above the composer; emitted live on the tool dispatch. Latest-only, committed turn-atomically: a committed turn REPLACES the session's set with what it staged (not calling the tool = clear, which is the wind-down; a failed turn keeps the prior set). Tiny payload — rides committed history verbatim (no elision, no PROJECT CONTEXT stub) |
| `doc_patch` | `ops`, `doc` | an applied edit batch: ops echo server-assigned element ids (highlighting); `doc` is the authoritative full snapshot (rendering) |
| `doc_snapshot` | `doc` | committed tree after a doc-changing turn — mid-turn patches carry a pre-commit version pointer; this one is current |
| `open_questions` | `items` | open-item list (TBD markers + needs_input blocks); emitted when a turn changed the doc |
| `lint` | `items`, `standards` | advisory lint issues + the editions in effect (pins + overrides); emitted right after `open_questions` when a turn changed the doc |
| `turn_complete` | `stop_reason`, `usage` | turn ended; history + doc version committed server-side. `usage` aggregates the turn's billed tokens across every round (input/output/cache/thinking + web-tool request counts) — raw material for the future cost meter. A turn stopped mid-stream adds `estimated_output_tokens` + `usage_estimated: true` (see "Disclosed stopped-turn output estimate"); `output_tokens` stays exactly what the provider reported |
| `error` | `message` | turn failed; history untouched and doc rolled back (retry is safe) |

The frontend switch in `App.tsx#send` is the single place events dispatch.
Snapshots outside a turn travel over REST, not SSE: `GET /api/doc`,
`POST /api/doc/undo|redo`, and `POST /api/project/load` all return
`{doc, open_questions, lint, standards, profile_complete, research_status,
baseline_index, figures, suggested_prompts}` (load adds `chat`, the rebuilt
transcript; `baseline_index` is the imported-master version for the redline
picker; `suggested_prompts` re-syncs the reply-chip bar, incl. restore-on-error). Patches and snapshots
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

Onboarding is frontend-only and adds no REST or SSE surface. It is a passive
overlay over the current project and never sends chat, edit, research, or QC
requests.

Research has its own channel (a run outlives any one chat turn):
`POST /api/research/start` (400 incomplete profile / no key; 409 while
running), `GET /api/research/status` (snapshot: status/error/events/
profile view), and `GET /api/research/stream` — an SSE stream that replays
the run's event log from seq 0 and follows until terminal, closing with a
`stream_end` sentinel. Coordinator/runner event types: `research_started`
(now also `dimension_titles: {id: title}` beside the `dimensions` id list),
`dimension_complete`, `dimension_failed`, `research_complete`,
`research_failed`. The live-visibility batch adds WORKER events, emitted by
each dimension thread as it works (all carry `dimension_id`; they
interleave freely across dimensions, but a dimension's terminal event
always follows its own live ones): `dimension_started` {title,
max_searches, max_fetches}, `dimension_activity` {kind: thinking|searching|
fetching|writing, on change only}, `dimension_search` {query} /
`dimension_fetch` {url} (detected live from the raw stream, chat-loop
style), and `dimension_retry` {attempt, max_attempts, reason, backoff_s}.
The `stream_end` sentinel is still exactly `{type, status}` — `status` may
now be `superseded` when a NEWER run takes the runner over mid-stream
(`sse_events` binds to the run token at call time, the QC shape). Every
event carries the 1-based `round` it belongs to;
`research_complete` reports the CUMULATIVE `item_count`/`grounded_count`
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
- **Context architecture ("Sonnet unleashed", 2026-07-21).** The system
  prompt is ONLY the stable module block (`render_system_prompt`,
  deterministic per module, `cache_control: ephemeral`). Everything
  session-varying — standards editions in effect, the research profile,
  the **full document text** (`outline(doc, max_text=None)`, with
  ◆source chips), the lint report, and open items — renders into a
  PROJECT CONTEXT block spliced ahead of the user's text in the **newest
  user message** (`_turn_context_text`, frozen at turn start). Two more
  cache breakpoints ride each request's messages
  (`_with_cache_breakpoints`, copy-on-write — stored history never
  carries `cache_control`): the **committed-history boundary** and the
  **tail**. The boundary is what makes caching roll across turns; the
  tail alone cannot (see "Rolling chat cache breakpoint" below). TTLs are
  NON-INCREASING across the request: system and boundary carry
  `settings.CHAT_CACHE_TTL`, the tail the shortest supported
  (`CHAT_TAIL_CACHE_TTL`) because its entry cannot outlive its own turn.
  A SHORT-before-LONG request is a nonretryable 400, which the pin makes
  unbuildable. Nothing session-varying
  may render into the stable block (pinned by
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
  default `high`; research runs `RESEARCH_EFFORT`, default `high` —
  dialed back from `xhigh` on 2026-07-28: research fans out 4 concurrent
  dimension calls, so `xhigh`'s reasoning depth multiplied across all of
  them was the single biggest driver of research cost).
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
  (current default: `generic`); reset keeps it, project load resolves it
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
  `test_session_reset_abandons_running_research`). Re-running **appends a
  round** to the terminal run's results — it never replaces them (see
  "Research rounds" at the end of this file).
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
  `sys._MEIPASS` in `settings._resolve_frontend_dist`), the trace
  viewer, and the root `LICENSE` file (the MIT notice must travel with
  every installed copy, not just the git checkout — `installer.iss`
  picks it up for free via its wholesale `dist\BuildASpec\*` bundling);
  Inno AppId `{89E58C42-A4F6-49F8-8FCB-1147CB0186DB}` is
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
  multi-agent review on Opus 5 (`claude-opus-5`) before a
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
  usage by category (interview/research/audit, with `qc` added in Batch 4),
  thread-safe (research, audit, and QC fold run totals in from daemon threads
  — they meter BEFORE the
  status flip so a poller that sees `complete` finds the ledger updated).
  Reset/load clear it; not persisted. `settings.PRICING` (`VERIFY`-checked
  2026-07: Sonnet 5 at post-intro $3/$15, cache read 0.1×, cache write
  1.25×, web search $0.01/req, Opus 5 $5/$25 for Final QC, Fable 5 $10/$50)
  drives
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

## Batch 4 — implemented notes (v0.9.0: Final QC; model updated in v1.8.0)

The one place a model other than Sonnet 5 appears (frozen decision).
**Model superseded in v1.8.0 — see "Final QC cost + speed" below:**
`settings.QC_MODEL` now defaults to `MODEL_OPUS_5` ("claude-opus-5"), added
to `schema._STRICT_CAPABLE_MODELS`, priced $5/$25 in `settings.PRICING`.
Opus 5 runs adaptive thinking by default; QC requests state
`thinking: {type: adaptive}` + `output_config.effort` (`QC_EFFORT`, default
`high` since v1.8.0) — never a manual budget (it would 400; the engine never
sends it). Batch 4 originally shipped on Fable 5 at `xhigh`.

### Audit-grade Final QC report extension (implemented 2026-07-24)

This work promotes Final QC from a memo-plus-fix-queue feature to two explicit
surfaces backed by one canonical result: `QCReportModal` is the complete,
read-only audit report, while `QCDrawer` remains the compact action queue.
`GET /api/qc/export` produces the human sign-off `.docx`;
`GET /api/qc/export.json` produces the lossless machine-readable audit envelope
(`report` + generated `current_state`).
Do not add report-only facts in the frontend or exporter: all three projections
must come from serialized `QCResult` data, with a current/stale comparison
against the live session.

The reporting boundary is observable work product, never hidden reasoning.
Allowed evidence includes input/config identity, lens-submitted concise
`reviewed_checks`, server-tool queries and retrieval results, grounding
verdicts, model-submitted findings/verdict notes, errors, usage, and
deterministic validation/disposition events. Prompts and UI copy must not ask
for, promise, serialize, or display private chain-of-thought.

**The reporting contract.** (Preserved verbatim-in-substance from the
2026-07-24 audit-grade amendment when its plan file was retired — it is the
maintenance contract, and the bullets below are how it is implemented.) Final
QC has two coordinated outputs: a full, first-class audit report — the trust
and traceability surface — and a compact action queue of surviving findings —
the remediation surface. The queue never replaces or truncates the report. The
report must preserve enough detail to answer: *what ran, against what, what did
it check, what evidence did it retrieve, how was each result adjudicated, and
what happened next?* Four rules follow, and they bind every future change:

- **Word may summarize; it may never omit.** Word can format or condense dense
  structures for readability, but must not silently drop failures, incomplete
  coverage, refuted candidates, validation errors, or limitations. JSON is the
  lossless record for downstream audit and integration.
- **Success is not coverage.** A successful HTTP response or a verifier
  majority is not proof of complete coverage. A runner-level terminal failure
  keeps using the runner status/error channel and never fabricates a canonical
  completed report. Every surface must distinguish "checked and passed" from
  "not checked" and from "could not complete."
- **Never infer evidence the API did not give.** The report distinguishes
  cited URLs, actually retrieved URLs, and URLs accepted by grounding. It never
  converts an aggregate `grounded` boolean into a claim that every source was
  accepted, and never invents a one-to-one query→source causal mapping the API
  did not provide.
- **Refuted means excluded from the action queue, not deleted from the audit
  trail.** A refuted candidate keeps its issue, rationale, element anchor,
  original severity, sources/grounding, full verifier-seat ledger, threshold/
  outcome and fix proposal, plus the recorded basis for refutation. Fix records
  keep the exact proposed `apply_spec_edits` operations (not merely a prose
  preview); open, applied, dismissed, advisory/no-op, invalid and stale
  outcomes stay distinguishable; and dismiss memory never erases the original
  proposal or its validation history.

- **Versioned run/input envelope.** `QCResult` carries
  `schema_version=3`, `protocol_version="final-qc/3"`, a UUID `run_id`,
  `execution_status` (`complete|partial|failed|cancelled`),
  start/finish/duration, reviewed
  `version_index` + `version_fingerprint`, and a deterministic
  `input_manifest` + `input_fingerprint`. The manifest covers material review
  inputs (document, research/profile presence, module/discipline and standards
  context, source guard, model and request configuration) so staleness is not
  reduced to a mutable history index. It also records model, effort,
  max-tokens, request/response counts, run usage, and estimated cost.
  Schema-2 reports remain readable/exportable historical records, but endpoint
  guards never accept them as an actionable queue because they lack semantic
  fix verification.
- **Complete lens records.** `QCLensStatus` persists the lens id/title/brief,
  status (`completed|failed|cancelled`) and error, summary,
  finding/grounding counts,
  `reviewed_checks` (`passed|finding|not_applicable`, notes, element ids and
  per-check `source_checks`), exact server-tool `search_queries`,
  `retrieved_sources`, per-lens `usage_totals`, and request/response counts. A
  completed lens with zero findings therefore still shows its tested coverage;
  a failed lens is not dropped. Accepted final-attempt queries/sources are the
  only records eligible for grounding; `attempted_search_queries` and
  `attempted_sources` separately preserve every billed attempt (including
  failed fetches and abandoned retries) for operational traceability.
- **Per-source grounding.** `QCSourceRecord` carries the requested URL,
  normalized URL, title, retrieval method(s), accepted/rejected/not-cited
  state, and grounding reason. Findings and reviewed checks retain their own
  source records. Grounding means the cited URL matches retrieved evidence; it
  does not claim that a URL proves every word of the model's rationale.
- **Every expected verifier seat.** `QCVerdict` records stable
  `reviewer_index`, `status` (`completed|failed|cancelled`), error, uphold,
  revised severity, concise note, `ops_adequate`, `ops_note`, `search_queries`,
  `retrieved_sources`, `usage_totals`, and request/response counts. The
  verifier sees the complete proposed-operation payload as untrusted data and
  must reject fixes that are partial, choice-creating, TBD-producing,
  contradictory, scope-changing, or otherwise unsafe. Seats are allocated in
  the result even when calls fail. A failed/cancelled/missing seat makes the whole
  candidate `inconclusive`; it is neither actionable nor substantively
  refuted, and the report becomes partial. Majority adjudication is performed
  only for a fully completed panel.
- **Full finding adjudication.** `QCFinding` retains `original_severity` and
  final `severity`, all seat records, persisted `verification_panel_size`,
  `verification_threshold`, `verification_outcome`, model-supplied element id,
  saved `reviewed_ref`/`reviewed_text`, `element_resolved`, rationale,
  cited/accepted URLs and per-source checks. Unresolved anchors are preserved
  and disclosed as limitations rather than treated as verified locations.
  Refuted candidates keep the same detail in the report's full appendix.
  Infrastructure-inconclusive candidates occupy a separate collection and
  appendix with their failed/cancelled seat evidence; only verified survivors
  enter the compact action queue. `ops_semantic_status`
  (`not_proposed|not_evaluated|approved|rejected`) and
  `ops_semantic_reason` keep finding survival separate from fix eligibility:
  a surviving finding needs every expected seat to complete, uphold, and set
  `ops_adequate=true` before its operations can be approved.
- **Full fix and disposition record.** Exact `proposed_ops`, aggregate semantic
  decision, snapshot dry-run validity/error, status and dismiss reason persist.
  Only semantically approved fixes proceed to deterministic/source-preservation
  validation, so `ops_valid=true` means both semantic and mechanical approval.
  `QCDispositionEvent`
  records apply/dismiss/stale/no-op outcomes with time, reason, and document
  identity where available. Revalidation against the current document remains
  authoritative; audit display of raw proposed ops never makes invalid or
  stale operations executable. Refuted operations are preserved but do not
  enter survivor-only validation; report surfaces label them "not evaluated,"
  not "invalid."
- **Usage, cost, and limitations.** The report renders aggregate and per-call
  token/cache/search usage plus API/model counts and estimated cost from the
  configured pricing table (`estimated_cost_usd`). `cost_basis` persists the
  rate-model/fallback decision, per-token rates, web-tool rates, thinking-token
  treatment, and pricing authority used when the estimate was calculated.
  Limitations are derived
  from recorded facts: missing research, legacy report schema, partial
  lens/seat coverage, failed or cancelled calls, retrieval/grounding gaps,
  unresolved element anchors, source-preservation limits, and stale input
  identity. An estimate is labeled as such, not represented as an invoice.
- **Coverage is a hard readiness condition.** `QCResult.coverage_complete()`,
  `verification_complete()`, and `is_complete()` keep coverage separate from
  finding votes. Readiness requires a complete, current result in addition to
  the existing document checks and no open criticals. One failed/missing lens
  or verifier seat blocks readiness, including when the remaining seats reach
  a majority or refute every candidate.
- **Latest attempt is distinct from last success.** `QCRunner` owns each worker
  with an unforgeable run token so a stopped daemon cannot emit into or resolve
  a replacement run. It retains a structured latest-attempt record (including
  partial/all-failed paid activity) separately from the last successful
  `result`; failed/cancelled/running latest attempts block readiness, persist in
  `.baspec`, and appear in exports without deleting the earlier success.
  Terminal status/result/event publication is one runner-lock transaction;
  readiness, persistence, and exports consume `audit_record_snapshot()` once
  rather than sampling mutable fields separately. SSE is bound to its starting
  run token and closes `superseded` before exposing a replacement run's events.
  Restore promotes only an execution-complete, structurally complete report to
  the retained report slot; partial/failed/cancelled reports remain latest-
  attempt evidence. Apply and dismiss independently require the current
  schema/protocol (so retained schema-2 history is still nonactionable) and
  stay blocked during worker settling.
- **Surfaces and safety.** `QCReportModal` shows run/document identity,
  readiness, methodology/input manifest, every lens/check/query/source/error,
  metrics, complete surviving and refuted records, raw proposed-op JSON,
  dispositions, usage/operations, and limitations without content truncation.
  It downloads both report formats, pinning each request to the run id shown in
  the snapshot so a changed backend selection returns a conflict. Non-HTTP or
  unsafe source strings render
  as inert text, never clickable links. `QCDrawer` stays compact and optimized
  for apply/dismiss work. The Word artifact stamps Build-a-Spec title/subject,
  author, last-modifier, and current creation/modification metadata. Its
  masthead and sign-off treat a failed/cancelled/partial/running latest attempt
  or blocked `qc_current`/`qc_audit_complete` check as controlling; any retained
  prior success is labeled historical and cannot produce a complete sign-off.

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
  Findings retain per-URL grounding decisions against retrieved sources
  (`validate_cited_sources`, same trust model as research). Completed lenses
  retain their summary, observable reviewed checks, queries/retrievals, and
  usage; failed lenses retain their error. (2) Every finding faces a panel of
  independent refuters (`QC_VERIFIERS_STANDARD` 2 for medium/low,
  `QC_VERIFIERS_CRITICAL` 3 for critical/high). **Adjudication is
  `final-qc/4` (Chunk 5.1) — the v3 rule `upholds >= size//2 + 1` described
  here originally is GONE**; see "Final QC v4 panel outcomes" below. A
  dead/cancelled/missing verifier makes the candidate infrastructure-
  inconclusive and the run partial; it is never treated as substantive
  refutation evidence. Verifications for all findings flatten into
  ONE bounded thread pool (per-`(finding, verifier)` task) with at most
  settings.QC_MAX_WORKERS
  submitted futures; `verify_progress`
  {done,total} fires as each finding's panel resolves. Surviving severity =
  `median_severity([original, *upheld revisions])`; both original and final
  severity persist. Refuted findings are retained under `QCResult.refuted`;
  disputed ones under `QCResult.disputed`; incomplete panels under
  `QCResult.inconclusive`, all with full
  evidence/seat/fix detail (transparency, excluded from the compact issue
  queue). An immediate nonretryable `INVALID_REQUEST` before any model response
  is treated as a shared verifier-phase failure: queued seats are synthesized
  as failed zero-request records, in-flight calls settle, and the run is
  partial. Ordinary transport, throttling, output, parse, and cancellation
  failures do not open that circuit.
  (3) Semantic then deterministic ops validation: only a surviving finding
  with unanimous complete-panel operation approval is dry-run via
  `apply_edits(deepcopy(snapshot))` (copy per finding — they never see each
  other's effects); rejected or invalid fixes stay advisory with
  `ops_valid=False` and their semantic/mechanical reason persisted.
- **Content-addressed findings + dismiss memory.** `finding_id` is `qc-` plus
  the first 12 characters of a canonical-JSON SHA-256 over every material fact
  a carried disposition relies on: lens and element ids; normalized title,
  issue, rationale and submitted severity; normalized cited URLs; exact
  proposed operations; reviewed text; final severity; verification outcome;
  the reviewer-index-sorted panel projection (`reviewer_index`, `status`,
  `upholds`, `revised_severity`); normalized/sorted grounding decisions
  (`source`, `accepted`, `reason`); and normalized accepted sources. The
  runner captures the prior result's `dismissed_ids` before `start()` clears
  it and threads them
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
  anti-mutation pattern. History index, document fingerprint, and the broader
  input-manifest fingerprint stamp the reviewed input → staleness markers in
  the drawer, full report, both downloads, and readiness gate.
- **Migration — the compliance audit is deprecated.** The `code_compliance`
  + `completeness` lenses supersede it. The audit BUTTON is retired from the
  UI (`ResearchDrawer` is research-only; the frontend no longer calls
  `/api/audit/*`). The removal had to be wholesale rather than button-only
  because `tsconfig` sets `noUnusedLocals`/`noUnusedParameters`, so the audit
  state, handlers and `api.ts` functions could not be left orphaned behind a
  hidden button. The endpoints + `AuditRunner` remain untouched. The main
  export closing renders the QC summary when a QC result exists, else falls
  back to the audit closing (`build_docx(..., qc_result=...)`).
- **Persistence + serialization.** `QCResult.to_dict`/`from_dict` round-trip
  the full result; `spec_doc/project.py` gains a `qc_result` field restored
  via `QCRunner.restore` (same as the audit's). `usage_ledger` gains a `qc`
  category priced on `QC_MODEL`; report-level and per-lens/seat usage stay in
  the canonical QC record together with the labeled cost estimate.
- **Tracing.** A `qc` span (`KIND_QC`) with mirrored `qc_progress` events;
  hooks never raise (`capture.qc_start/qc_event/qc_end`).
- **Deliberate non-ports.** Server-side refusal `fallbacks` (recommended for
  both Fable 5 and Opus 5 by the claude-api skill) is NOT wired: it needs the
  beta endpoint and is out of the batch's plan scope; a refusal surfaces as an
  incomplete stop_reason → the lens fails clean under the existing failure
  policy. The Fable-era 30-day-retention caveat no longer applies: Opus 5 has
  no retention requirement, so ZDR orgs can run Final QC (v1.8.0). **The
  model was only half of that claim** — the `code_compliance` lens carries
  the web tools, whose caller mode is the other half; both are ZDR-eligible
  only with `allowed_callers: ["direct"]` (see "Server-tool caller mode"
  below). A change to either half re-opens the claim.

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

> **SUPERSEDED.** Batch 6 shipped a *passive* tour: frontend-only, four
> chunks, no backend route, no project mutation. That is no longer what
> ships. The tutorial now runs on a real, server-owned **tutorial
> workspace** with per-chapter **scenarios**, and it does start runs, apply
> edits, and prefill the composer when you ask it to. See "Guided tutorial —
> implemented notes" below for the current architecture. The starter chips
> and the `openNonce` drawer idiom described here are the parts that
> survived unchanged.

- **Starter chips.** Five prompts in the empty chat (`starterPrompts()` in
  `lib/tour.ts`, rendered by `Chat.tsx`); the first is the frozen onboarding
  ask and launches the tutorial instead of sending a message.
- **Drawer opening = optional `openNonce` prop** (Review / Research / QC
  drawers + the panel's open-items block): a bump does `setExpanded(true)`.
  App owns `drawerNonces` + `bumpDrawer` (the `prefill.nonce` idiom).
- **The spotlight leaves real controls interactive** (it did not, in Batch 6).
  The root is `pointer-events-none`; only the step card takes pointer events.
  Anchor lookup retries ~2s, follows resize and scrolling, and falls back to
  an honest "this control is not available in the current UI state" card.
- **Completion flag** is cosmetic localStorage; `abort()` handles external
  session/project teardown without marking completion (it is now
  `restoreOriginal({completed: false})` — see "One ending" below).

## Guided tutorial — implemented notes (real workspaces + per-chapter scenarios)

The tutorial teaches against **actual document state**, not a scripted
mock-up. It runs in a protected server-owned copy of the user's project (or a
generated spec, or the bundled showcase), and each chapter can swap in a
purpose-built practice copy. The original is retained throughout and is
**always** restored on exit — there is no other ending.

- **Three scopes, one lease.** `SessionManager` (`backend/sessions.py`) moves
  `original` → `tutorial` → `scenario`; scenarios never nest. Every
  transition mints a new monotonic `workspace_id`, and that plus
  `session.generation` is the lease every tutorial route re-checks
  (`_tutorial_request_is_current`) — a stale request gets
  `409 {code: "stale_workspace"}` rather than mutating the wrong workspace.
  `begin_tutorial` is idempotent per `request_id` and refuses while a chat
  turn, research, audit, or QC run is active or settling.
- **Routes** (`backend/app.py`): `GET /api/tutorial/status`, `POST
  /api/tutorial/{start,enrich,scenario/start,scenario/finish,restore}`.
  `enrich` is SSE. `restore` has no `keep` counterpart — see "One ending"
  below. `GET /api/project/save?scope=tutorial` is the only mid-tutorial
  save; there is no original-scope download, because the original is never
  replaced and is always there to save after the tour ends.
- **Coverage, not decoration.** `analyze_tutorial_coverage`
  (`backend/tutorial.py`) checks 15+ teaching anchors the manifest needs
  (section number/title, substantive content, all three PARTs, sibling
  articles and paragraphs, four paragraph levels, assumed/needs_input/TBD
  content, version history, one figure of each kind, suggested prompts).
  Gaps drive enrichment.
- **Enrichment is additive and proven so.** `mode:"live"` runs a real
  `stream_user_turn` against the enrichment directive; `mode:"bundled"` adds
  deterministic fixtures with zero spend. `validate_tutorial_enrichment`
  rejects the result outright if any pre-existing PART, article, paragraph,
  identity or metadata value changed or moved, or if a new block claims
  provenance. A failed/incomplete/invalid enrichment atomically swaps in the
  bundled showcase (`tutorial_fallback`) — paid usage from the attempt stays
  visible. The swap is silent to the end user (no notice banner or disclosure
  copy): `tutorial_fallback` carries `reason`/`source` for callers that want
  it, but no `message` field, and the frontend no longer surfaces one.
- **Nine scenario kinds** (`push_scenario`'s allowlist): `blank`,
  `structural`, `review`, `import`, `template`, `project_roundtrip`,
  `references`, `research`, `qc`. Several run **production** code paths — the
  import scenario builds a DOCX and runs `_prepare_master_import`; the
  project scenario round-trips real `.baspec` bytes; the references scenario
  attaches five real files through the real extractors.
- **The chapter→kind mapping is an ordered substring chain with `structural`
  as a silent catch-all** (`tutorial_scenario_start`). An unmapped scenario
  name does **not** error — it quietly starts the structural practice copy.
  Every new kind needs its own branch ahead of the fallback, and a test that
  pins it (`test_an_unmapped_chapter_name_does_not_silently_start_a_practice_fixture`).
- **Frontend.** `lib/useOnboarding.ts` is the lifecycle machine (phases
  `idle`, `source-choice`, `enrichment-choice`, `preparing`, `touring`,
  `chunk-break`, `paused`); `OnboardingOverlay.tsx` renders the
  spotlight, per-step actions, readiness repair, and the restore
  progress/error card; `lib/onboardingStorage.ts` holds the resume record
  keyed on `TOUR_VERSION` + the workspace lease (the server is authoritative
  — only an exact three-way match restores progress).
- **One ending, one code path.** `restoreOriginal({completed})` in
  `useOnboarding.ts` is the single terminal transition; the natural finish,
  `end()` (the "End the guided tour?" confirm), the post-reload
  lease-mismatch recovery, and `abort()` (New session / Open project) all
  call it, and `finish_tutorial` has no `disposition` to pick. Only
  `completed` differs — external teardown and reload recovery pass `false`,
  so a tour the user never took is never marked complete (pinned by a
  single-call-site assertion in `tour.test.ts`). There is no
  "keep the tutorial" disposition anywhere in the stack, and no modal asking
  which ending the user wants: **ending returns the exact retained
  pre-tutorial `SessionState` object**, so the project comes back whole.
  Progress and failure ride the existing `preparing`/`stage:"finishing"`
  phase — note `retryPrepare` needs its `finishing` branch ahead of the
  `chooseSource` fall-through, or a failed restore restarts the tutorial.
  The `tour.finish` capability lives on the touring card's **End** button and
  the paused pill's ✕ (both reachable states), not on a modal wrapper.
- **Paid results are never fabricated.** `research`, `imported` and `qc`
  readiness are deliberately **not** repairable by "Build the missing
  example"; a missing result renders honest copy saying so. Two
  `doesNotMatch` assertions in `tour.test.ts` keep it that way.

## Reusable templates — implemented notes

A semantic template is a reusable starter document — not a project, and not a
Word file. Curated templates ship in `backend/templates/curated/`; personal
ones live in the app config dir (`app_paths.template_library_dir()`).

- **Routes**: `GET /api/templates`, `POST /api/templates/preview` (SSE when
  `Accept: text/event-stream`), `POST /api/templates`, `PATCH|DELETE
  /api/templates/{id}`, `GET /api/templates/{id}/export`, `POST
  /api/templates/import`, `POST /api/templates/{id}/instantiate`.
- **Two creation modes.** *Exact* snapshots the current document verbatim.
  *AI Generalize* has the model rewrite project-specific wording into
  reusable language and clears profile/overrides/suppressions/provenance —
  then returns a **diff preview to approve before anything is saved**.
  `_template_structure_contract` rejects an AI preview outright if it changed
  structure, ids, identity, or resolved an open decision.
- **Two-phase commit**: preview token → commit, bound to workspace,
  generation and document version.
- **`template_origin`** (id, name, `seed_block_ids`) rides a seeded document
  and renders a "template starter" badge. It is explicitly **not** source-DOCX
  provenance and never unlocks source-preserving export.
- **`.bastemplate`** is `application/vnd.buildaspec.template+json` (16 MiB
  import cap). `main.py` exposes `js_api.save_template(id)` for a native
  Save dialog.

## Capability coverage — implemented notes (the tutorial as a contract)

`frontend/src/lib/capabilities.ts` is the single vocabulary of end-user
capabilities. Production controls declare `data-capability="…"`;
`lib/tour.ts` steps reference the same ids; `frontend/tests/tour.test.ts`
asserts **set equality in both directions**. A capability that exists in only
two of the three places fails the suite — that is the contract working.

- **Adding a capability is a three-place edit**: registry entry, a
  `data-capability` on the real control (any `frontend/src/**` file except
  `capabilities.ts`/`tour.ts`), and at least one tour step. Ids must match
  `/"([a-z][a-z0-9.-]+)"/` — the test extracts them by regex over file text,
  so no capitals and no underscores. Space-separated ids
  (`data-capability="help.topics tour.controls"`) and the
  `data-capability={cond ? "a" : "b"}` form are both supported.
- **The contract only polices the vocabulary against itself.** A shipped
  feature with no id is invisible to it. An audit of every route, op and
  affordance closed six such gaps: `chat.thinking` (the reasoning
  disclosure), `research.stop` / `qc.stop` (aborting a paid run —
  `chat.stop` already existed for the cheap one), `document.section-header`
  and `document.first-article` (the from-scratch on-ramp), and `help.trust`
  (the trust dossier, which had been sharing `help.topics`).
- **Anchors are now validated too.** `tour.test.ts` checks that every step's
  non-empty `anchor` exists as a `data-tour` attribute in production UI, and
  that an anchorless step supplies a `resolve` instead. Before this a typo
  degraded silently into the "control not available" card, which reads as a
  legitimate product state rather than a bug.
- **The section header became editable.** `SpecDocument`'s `SectionHeader`
  mirrors `ArticleTitle` (hover pencil, Enter saves, Escape cancels) and
  writes one `replace` on `sec` carrying `text` + `numbering` — the same op
  the model uses, so it is one ordinary undoable version. It is gated on the
  already-computed `sectionReplaceCapability` and suppressed entirely on a
  `bareImport`, where inventing a section number is exactly what the
  non-spec-upload framing exists to prevent.
- **The `blank` scenario + chapter** exist because every other chapter runs
  on a populated workspace, so the panel's empty-state controls could never
  be on screen. `blank_practice_copy` returns a genuinely empty session
  carrying only module/discipline/primer — not a cleared clone, because a
  transcript describing a document that is no longer there is worse than no
  transcript. Its two steps deliberately carry **no `readiness`**: emptiness
  is the thing the user is about to destroy by doing the exercise, so a
  readiness check would flip the step into its own "could not be prepared"
  warning the moment they succeeded. Anchor resolution already covers the
  case honestly — if the fixture failed, `first-article` does not resolve and
  the step degrades to the standard "control is not available" card.
- **Lint finally has its own step.** `document.lint` lives on the always-
  rendered Issues strip (`data-tour="lint-issues"`), which returns `null`
  only when there are no findings — so the step needs no drawer plumbing, and
  the `structural` scenario's deliberately seeded findings are what it points
  at.

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
  discarded (the QC dialog also notes the QC spend already incurred
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
  already in flight (bounded by the `ThreadPoolExecutor` worker cap) completes
  naturally and its result is simply discarded. Restructuring the ported
  research/QC engines to interrupt an in-flight streaming call the way the
  chat loop now does would touch the "hard-won" fan-out machinery for
  marginal benefit given stopping is already lossy by design — not worth
  it. The spend already committed to those in-flight calls is still metered
  into the usage ledger even though the result is thrown away (mirrors the
  existing "the spend is real even on a failed turn" posture). **That claim
  had a hole in research until Chunk 4.3**: it held whenever a dimension
  still completed (the runner meters the returned profile before the CAS
  it loses), but the common stop — every dimension cancelled — raised
  `ResearchFanoutError`, which carried no usage, so the whole round went
  unbilled. The error now carries it.
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

## Batch 9 — implemented notes (v1.4.0: dynamic suggested-prompts bar)

Abraham's ask: a row of up to 5 pretty, clickable prompt chips just above the
chat composer, the model choosing the set each turn — direct answers to what
it just asked, or momentum moves — and the count winding down toward zero as
the section nears issue-ready. The whole feature is the Batch 8 `create_figure`
blueprint re-applied: one new chat tool on the ONE chat/tool loop, one live
SSE event, turn-atomic session state, optional project persistence, one stable
policy block. No new deps, no new env vars, no new endpoints, no format bump.

- **`suggest_prompts` is a third chat tool** (peer of `apply_spec_edits` /
  `create_figure`, defined in `backend/suggestions.py`), lenient schema (the
  create_figure posture, NOT the research strict shape), appended LAST in
  `_chat_tools()` so the existing tool bytes stay a stable cached prefix. It
  rides the one tool loop: `conversation._run_tool` dispatches it to
  `_run_suggest_prompts`, which validates, returns a compact token-discipline
  result (`{"suggested": N}`), and yields a live `suggested_prompts` UI event
  with the validated list. A bad payload becomes an `is_error` result the
  model self-corrects from — never a turn failure (the apply_spec_edits /
  create_figure posture). No `drawing`-style status hint (payload streams
  sub-second; the round-top `working` status covers it).
- **Latest-only, turn-atomic REPLACE semantics.** `stream_user_turn` keeps a
  turn-local `staged_suggestions` (initialized `[]`); the dispatch loop records
  each call's list (latest wins) before yielding the event; the success `else:`
  block assigns `session.suggested_prompts = staged_suggestions` beside the
  doc/figure commits. A committed turn REPLACES the set — including `[]` when
  the tool was NOT called, which is the wind-down (silence = clear the bar). A
  failed turn `return`s before the commit, so the previous list is simply never
  overwritten — rollback by construction, no begin/rollback bookkeeping, nothing
  extra for zombie turns (commit is already generation-guarded). A user stop
  takes the commit path (Batch 7) with whatever was staged; a stop caught
  mid-`suggest_prompts`-block strips the unexecuted `tool_use` (existing
  truncation branch) → commits `[]` (bar clears). `SessionState.suggested_prompts`
  clears in `reset()`.
- **No elision, no PROJECT CONTEXT stub** (the deliberate contrast with
  figures). The payload is tiny (≤5 × ≤120 chars), so the `tool_use` input rides
  committed history verbatim and the model sees last turn's chips naturally —
  `_elide_figure_tool_inputs` filters on `create_figure` only, so nothing
  touches the suggest_prompts input. The tool RESULT still stays compact.
- **Validation** (`suggestions.validate_prompts`, strict → `SuggestError` →
  `is_error`): dict with `prompts: list`; each entry a string; internal
  whitespace folds to single spaces; blank-after-cleanup → error; > 120 chars →
  error; dedupe preserving order; the > 5 check runs AFTER cleanup (so a list
  that dedupes down to 5 passes). An EMPTY list is VALID — the deliberate
  "nothing useful to suggest" signal. `restore_prompts` is the lenient project
  loader (malformed → `[]`, the FigureStore.load posture).
- **Persistence + one-way sync.** Optional `suggested_prompts` key in the
  project file (`save_project`, gated on truthy — omitted when empty; no format
  bump), restored by `load_project` UNCONDITIONALLY (it doesn't call reset(), so
  a load over a live session must not inherit stale chips). `_doc_payload`
  carries `suggested_prompts`, so boot, project load (`**_doc_payload`),
  undo/redo, and the failed-turn `refreshDoc()` all re-sync the bar one way —
  a failed turn's refresh returns the untouched pre-turn list, restoring the
  bar for free.
- **`_SUGGESTED_PROMPTS_POLICY`** joins the STABLE prompt after `_FIGURE_POLICY`:
  chips in the USER'S voice (complete sendable replies, never fill-in-the-blank /
  questions / spec text); answers-to-your-questions first (incl. an accept-default
  / "I don't know" option), then momentum moves; chat-actionable only (research /
  QC / export / undo / save are panel buttons, never chips); ≤60-char aim,
  120 hard; wind down near issue-ready; harmonize with the full-draft close (the
  chips ARE the clickable answers to its 2-3 follow-ups). Module-stable, zero
  session data (the cache rule). The passive tour never enters the chat path,
  so it cannot stage or replace suggestions.
- **Frontend**: `SuggestedPrompts.tsx` renders between the chat scroll region
  and `Composer` (outside `data-tour="composer"`, so tour spotlight rects are
  untouched) — `rounded-full` accent pills, hidden entirely when empty, disabled
  while streaming, click → `onSend(label)` (send-immediately, the starter-chip
  pattern; a prefill variant is a one-call swap). `App` owns `suggestions` state:
  cleared at turn start, set live from the `suggested_prompts` SSE branch,
  re-synced authoritatively via `refreshDoc`/`applyDocPayload` from the doc
  payload. `.prompt-chip-in` rise-in, reduced-motion-gated. No-vitest convention
  stands — pinned by `test_suggested_prompts.py` (24 tests) + `npm run build`.

## Batch 10 — implemented notes (v1.5.0: generic any-discipline module)

"Max flexibility" tier 2 (decided w/ Abraham 2026-07-22): a second module,
`generic`, drafts ANY discipline for projects anywhere in the USA or Canada,
leaning on model knowledge + the existing research fan-out. Tier 3
(model-synthesized modules) is deferred pending Abraham's go-ahead. Same
batch shape as always: no new SSE events, no new env vars, no new deps. One
new REST route (`GET /api/modules`) + an optional body on the reset route.

- **The unpinned basis is the design center.** `StandardsBasis.unpinned`
  sanctions a pinless basis (registration coherence matrix: unpinned pins
  NOTHING; pinned keeps the old non-empty rules — an accidentally-empty
  basis still fails startup). The generic module ships ZERO pins: every
  referenced-standard edition enters per-project through the existing
  `set_standard_edition` op with a mandatory stated basis — a grounded
  research item id, the user's statement, or an honestly-labeled model
  proposal ("model-proposed, unverified"). Until recorded, designations are
  cited year-free. `standards_context_block` renders a dedicated unpinned
  posture (recorded-overrides-only list + the mandatory-basis directive);
  the pinned rendering is byte-identical to v1.2.0.
- **Enforcement is lint + readiness, never a gate.** New rule
  `unrecorded_edition` (linting.py), active ONLY when `basis.unpinned`: a
  publisher-grammar designation scan (CAN/ULC…UL, longest-first,
  case-sensitive) finds designations absent from `effective_editions`, then
  reuses the stale rule's four citation shapes + span-dedup + negation
  suppression to flag year citations on them. Recorded-but-wrong-year stays
  `stale_edition`; not-recorded-at-all is `unrecorded_edition` — never both.
  Hyphen/space designation forms match the same record. The readiness
  checklist's existing "lint clean" check makes this the no-pins posture's
  gate without blocking any edit or turn. Hyperscale lint output is
  unchanged by construction (the scan is unreachable for pinned modules).
- **Open catalog.** `SpecModule.open_catalog` allows an empty
  `section_catalog` (validated per-entry when suggestions ARE shipped);
  `_render_catalog` renders establish-the-section-from-discipline guidance
  instead of a list. `lead_section()` has no runtime callers (verified) so
  an empty catalog needs no engine guard.
- **Discipline has a versioned source of truth.** `SpecSection.project_identity`
  carries optional `discipline` and facility/use `project_type` fields.
  `set_project_identity` supports partial corrections and explicit clearing;
  it is transactional, undoable, import-preserved, and serialized in every
  document version. `SessionState.discipline` remains only for reset/API and
  old-project compatibility. `effective_discipline()` always prefers document
  identity and falls back to the sanitized top-level field when older projects
  lack it.
- **Context placement (the cache rule).** The discipline renders as the
  first block of PROJECT CONTEXT beside project type (or an unknown marker),
  never the stable prompt. Prompt guidance records or corrects identity only
  once the user or clear document context establishes it. Research threads
  effective discipline into the
  dimension-template kwargs (set unconditionally — a `{discipline}`
  template can never KeyError; dummy kwarg registration-checks it) and the
  fan-out header; QC threads it as a `<project_discipline>` block in the
  lens user message; both only when non-empty (curated runs byte-identical).
  `POST /api/research/start` 400s for an open-catalog module with no
  effective discipline.
- **The generic module's content** (spec_modules/generic.py, native):
  discipline-agnostic scaffold playbook — 3 must-ask topics
  (section_selection / project_identity / scope_basis, the hazard_picture
  analog) + 9 defaultable topics whose defaults are honest META-defaults
  ("propose the discipline-standard practice… stamp assumed"), including
  the no-pins posture itself as the standards_editions default and a
  units topic (USA inch-pound / Canada SI). Conventions carry the
  region-aware prose: USA (I-codes model context, adoption per-project,
  UL/FM) vs Canada (NBC/NFC as provincially adopted, CSA/ULC + ULC
  listings, metric, no silent US→CA standard mapping). Research dimensions
  mirror the hyperscale four (same ids + budgets), `{discipline}`-
  parameterized and two-country aware. `DEFAULT_MODULE` is now `generic`.
- **Frontend.** "New session" opens `NewSessionDialog`: Blank slate actively
  resets to `{module_id:"generic", discipline:"", project_context:""}`;
  Start from a template and Load your own template are visible, disabled, and
  labeled as in development. Cancel/Escape and the shared unsaved-work gate
  keep existing content safe. The frontend no longer fetches modules or
  collects discipline/project description. `ModalShell` plus
  `primaryBtn`/`quietBtn` remain shared.
  `projectHeading.ts` formats directly from the current document: blank with
  no discipline; discipline-only until project type, city, and region all
  exist; otherwise `Discipline · Project Type · City, Region` (never country).
  This makes chat edits, profile edits, load/import, undo, and redo reactive.
  `StandardsStrip` already self-hides on an empty list (a state previously
  unreachable — on a generic session it appears with the first recorded
  override; the tour's standards anchor falls back to a centered bubble).
- **Tests.** conftest now restores the default module + empty discipline
  around every test (reset keeps them by design — restoration is test-only
  leak hygiene). New: test_session_modules.py; unpinned/open-catalog
  validation matrix + generic coherence/prompt tests (test_spec_modules);
  unpinned context-block rendering (test_standards); the
  unrecorded_edition matrix incl. the pinned-module-scoped-off byte
  guard (test_linting); research discipline threading via
  SequencedFakeClient keys that CONTAIN the discipline (routing success ==
  the proof) + the 400 backstop (test_research_api); the lens-message
  discipline block (test_qc).
- **Post-merge remediation (v1.4.0, after the master merge + review).**
  This work renumbered to Batch 10 / v1.5.0 after master shipped its own
  Batch 8 (chat figures) and Batch 9 (suggested-prompts bar). Five review findings fixed: (1) the stale
  scan is now punctuation-tolerant for unpinned modules
  (`_scan_editions(variant_tolerant=unpinned)` builds patterns for a
  recorded name's hyphen AND space forms) — closes the hole where a
  recorded standard cited in the other punctuation at a wrong year escaped
  both rules; pinned modules pass `variant_tolerant=False` → byte-identical.
  (2) `_scan_unrecorded_editions` binds designations longest-first so a
  shorter designation's match inside a longer citation is span-deduped, not
  double-reported. (3) The deprecated audit path threads `discipline` into
  `build_audit_user_message` (`<project_discipline>`, non-empty only), so
  the generic `compliance_persona`'s session-discipline reference isn't
  dangling. (4) The later context-aware-session work removed the obsolete
  onboarding demo route and made document identity authoritative. (5)
  `App.onLoadProject` still refreshes health for legacy module compatibility;
  the heading itself reads current document state.

## Standards management — implemented notes (per-document add / delete)

The pinned-standards list is now user-curatable **per document**, never by
mutating the module's frozen `StandardsBasis.standards` (curated in code with
`docs/standards_provenance.md` receipts). Same posture as `edition_overrides`
/ `project_profile`: metadata on `SpecSection`, transactional / undoable /
persisted / fed to the model + lint + QC through the one `effective_editions`
merge. No new SSE event, no new REST route, no new Python/npm deps.

- **Add** rides the existing `set_standard_edition` op (an unpinned override
  already appended to `effective_editions`). Two refinements: the op + the
  override entry gain an optional `title` (so an added standard renders a real
  REFERENCES line, not a bare designation), and `effective_editions` marks
  appended non-pins `is_added=True` so `standards_context_block` labels them
  "added for this project" rather than "jurisdiction-adopted override". Adding
  a standard or changing an edition still **requires** a reason (`basis`) —
  the "never silent" doctrine stands.
- **Delete** is the genuinely new capability: `SpecSection.suppressed_standards`
  (`{canonical name: reason}`) + the `set_standard_suppressed
  {target_id:"sec", standard, suppressed, basis?}` op. `effective_editions`
  skips a suppressed name **before** applying any override — suppression wins
  and is **non-destructive** (a dormant override returns intact on restore).
  This is the ONLY way to drop a module pin; removing an override just reverts
  a pin to its default edition. Excluding a standard is a scope decision, so
  the **reason is optional** (decided with the user); a suppressed standard is
  absent from the editions in effect (lint stops checking it) and named in a
  "do not reintroduce into REFERENCES" advisory line so the model won't re-add
  it. Suppressed pins ride into `standards_payload` as `is_suppressed` rows
  (with the pin's display edition/title) so the panel can strike them through
  with a Restore control. On the generic **unpinned** module, suppression is
  threaded through the same way (the unpinned context block honors it too).
- **UI**: `StandardsStrip` (in `IssuesDrawer.tsx`) is now editable — a per-row
  action matrix (default → Edit edition · Exclude; override → Edit edition ·
  Revert to default; added → Edit edition · Remove; suppressed → Restore), an
  Add-standard form, all posting through `onEditDoc` → `POST /api/doc/edit`
  (one undo step), modeled on `ProjectProfileForm`. `StandardInfo` gains
  `is_added` / `is_suppressed` / `reason`; `EditOp` / `DocOp` gain the two
  standards actions + `standard`/`edition`/`basis`/`title`/`suppressed`.
- **QC apply parity**: `qc/schema.py` `QC_OP_ACTIONS` + `_QC_OP_KEYS`
  (+ `_QC_OP_SCHEMA`) mirror `set_standard_suppressed` and the new
  `title`/`suppressed` fields, so a standards-scope fix a lens proposes
  (e.g. exclude a standard that shouldn't reach REFERENCES) survives
  `_clean_op` and Apply QC can enact it — the QC allow-list must track the
  `apply_spec_edits` vocabulary the lens reasons from (`set_project_profile`
  stays excluded).
- **Serialization / no-change surfaces**: `suppressed_standards` rides
  `SpecSection.to_dict`/`from_dict` (+ `validate_suppressed_shape`, reason
  optional) and `is_empty()`; the diff/redline engine, `project.py`,
  `app.py`, and module-import validation need no change (per-document tree
  metadata is invisible to the diff and rides the store round-trip for free).
- **Tests**: `test_standards.py` (suppression skip + non-destructive restore,
  added-title carry-through, `validate_suppressed_shape`, optional override
  title), `test_spec_doc.py` (op records/removes + undo/redo + serialization +
  malformed-load), `test_manual_edit.py` (add / suppress / restore / undoable
  through the endpoint), `test_app.py` (payload flag set), `test_qc.py`
  (QC-proposed suppression survives cleaning).

## Content persistence — implemented notes (save gate + figure minimize/confirm)

The rule (Abraham): never lose session content unless the user starts a new
session (or opens another project) AND explicitly declines to save; anything
created must be saved when the user saves. The save path already captured
everything — figures included (`project_payload` → `save_project(figures=…)`).
What was missing were guards on the destructive paths. No new SSE events, no
new deps, no project-file format bump; one thin REST route + one js_api method.

- **Two destructive paths are now save-gated.** "New session"
  (Header → `requestNewSession`) and "Open project"
  (`ArtifactPanel` file input → `onLoadProject`) both replace the whole
  session, so each first checks `has_unsaved_progress` and, if there's work,
  opens a Save / Don't save / Cancel prompt. Loss now requires an explicit
  "…without saving". The native window-close prompt (`main._CloseController`)
  was already gated; these reuse the same predicate + save machinery. The raw
  Header's explicit blank-slate reset runs only after this gate resolves.
- **`CloseDialog` is now the shared 3-way "save before you lose this?" modal**
  — optional `title`/`body`/`saveLabel`/`discardLabel` props default to the
  window-close wording (that instance is byte-unchanged); the in-app gate
  renders a second instance whose copy switches on the pending action
  (new-session vs open-project). App owns `saveGate`
  (`{kind:"new-session"} | {kind:"open-project"; file} | null`) — the pending
  File rides the state so the load runs after the gate resolves.
- **The save mechanism.** `_CloseController.save_project()` (main.py) reuses
  `_save_project_file()` (native `webview.FileDialog.SAVE`) but never
  `_force_close()` — auto-exposed to JS as `window.pywebview.api.save_project`,
  returning True on write / False on a cancelled Save-As. The frontend proceeds
  to the reset/load ONLY on True, so a cancelled save keeps the session.
  Dev/browser fallback (no bridge): `downloadProjectFile()` fetches
  `/api/project/save` (awaited — so a fast reset can't race the payload) and
  downloads the blob.
- **`has_unsaved_progress` now counts figures explicitly**
  (`or bool(session.figures.figures)`) — a session whose only work is a
  diagram/table still offers to save (they no longer merely ride the chat
  history that produced them). Surfaced to the frontend by a thin
  `GET /api/session/unsaved` → `{ok, unsaved}` (`checkUnsaved()`), called on
  the New-session / Open click so the gate uses the same truth as the close
  prompt (frontend falls back to a local heuristic if that fetch fails).
- **Figure ✕ is now confirm-then-delete** (was a silent hard delete — the odd
  one out vs the doc-tree 🗑 / standards deletes). Inline two-step confirm in
  `FigureCard`, mirroring `SpecDocument`'s row-delete pattern; the backend
  `DELETE /api/figure/{fid}` + `FigureStore.delete` are unchanged.
- **Figure minimize** is a new non-destructive fold: a caret in the figure
  card collapses the rendered body/caption/downloads while the figure stays in
  the session (and in every save). LOCAL `FigureCard` state (no prop drilling,
  no store/endpoint) — it survives in-session re-syncs (the card is keyed by
  fid) and resets on New session / project load (the bubbles remount). A
  deliberate view-only preference; persisting it across reload would be a
  one-field add, intentionally skipped.
- **Tests**: `test_close_prompt.py` (`has_unsaved_progress` with only a figure;
  `save_project()` writes-but-keeps-open + cancelled-returns-False),
  `test_figures.py` (`/api/session/unsaved` reflects work). Frontend pinned by
  `npm run build` (tsc) — the no-vitest convention stands.

## Upload responsiveness — implemented notes (import / open never freeze the app)

Reported symptom: choosing a spec froze the app while it uploaded, and the chat
stalled — the user could type, but the model could not answer until the import
finished. Two independent causes, both fixed; no new endpoints, no new deps.

- **The event-loop rule (the invariant to keep).** `POST /api/import/master`
  and `POST /api/project/load-file` were the app's only two `async def`
  handlers, and each did its parsing/indexing inline — i.e. on the asyncio
  loop. For the whole import the server answered *nothing*: no SSE frame, no
  REST call, no health poll. Every other endpoint is a plain `def`, which
  FastAPI already runs in a threadpool, which is why nothing else showed this.
  The blocking halves are now module-level `_prepare_master_import` /
  `_stage_project_load`, invoked through `run_in_threadpool`. Both touch only
  their arguments (a throwaway `SessionState` for the load), so nothing
  session-owned crosses the thread boundary; the commit still happens on the
  loop thread inside `session_state_guard()`, and its re-checks
  (`turn_active`, `has_body_content`) were already written for exactly this
  "the document changed while the master was being inspected" race. The
  **response payload** is offloaded the same way (`await
  run_in_threadpool(_doc_payload, session)` in both handlers): on an imported
  master `_doc_payload` runs the first source-capability sweep, which is by
  far the most expensive thing either request does. Every other endpoint
  reaches `_doc_payload` through a plain `def` handler — i.e. already on a
  worker thread — so these two were the only ones that had to say it out
  loud. **An `async def` handler in this app must never do seconds of CPU
  inline — make it `def`, or push the work through `run_in_threadpool`.**
- **The import was also quadratic.** `SourceXmlIndex.body_child` /
  `word_text` / `element_for_span` / `direct_children` each scanned the full
  element tuple, and anchor binding calls them once per body child: a
  5,854-paragraph master spent **12.6s** in `build_source_patch_context`
  alone. They now build a lookup dict on first use (fields declared
  `init=False, compare=False, repr=False`, populated via
  `object.__setattr__` — the frozen-dataclass memoization escape hatch, so
  equality/hashing/serialization are untouched), preserving first-match and
  the typed `XmlLexicalError` misses. Same master: **2.0s**, and roughly
  linear from there.
- **The UI now says it is working.** `App.fileLoading`
  (`{kind:"import"|"open", name}` + a ref as the double-submit guard) drives:
  a chat marker note on import (`addNote`, the research/QC convention), an
  accent progress line under the panel actions reusing the existing
  `.status-dots`/`.status-shimmer` language, `Importing…`/`Opening…` button
  labels with both file actions disabled, and a `LoadingState` sheet in the
  document area (the `EmptyState` paper with staggered `.skeleton-line`
  pulses, reduced-motion gated). The open path deliberately posts no chat
  note — `doLoadProject` replaces the transcript wholesale, so a note there
  would vanish on success.
- **STILL QUADRATIC, no longer on any critical path (see the next section).**
  `source_patch.source_edit_capabilities` derives each element's permissions
  by probing the authoritative final gate, and every non-no-op probe runs
  `_validate_source_and_plan` end to end — which composes the full patched
  `word/document.xml`, reparses it, rebuilds the whole lexical index, and
  does a complete raw-ZIP rebuild preflight. That is O(document) work, run
  ~5× per paragraph, so the sweep is O(n²) in body size. Measured end-to-end
  through `POST /api/import/master`: **86 blocks 1.6s · 164 blocks 6.0s ·
  218 blocks 10.7s · 332 blocks 28.4s**, and a 4,685-paragraph master
  extrapolates to *hours*. Making the derivation itself cheap (per-island
  analytically, probing only what the UI needs, or reusing plan state across
  probes) means changing the most safety-critical subsystem in the repo — a
  deliberate design decision, not a tuning pass, so it is still its own
  future change. What DID change is that nothing waits for it any more.
- **Tests**: `tests/test_import_responsiveness.py`. The two responsiveness
  tests drive `TestClient` from two real threads (a blocked event loop cannot
  honour an `asyncio` timeout — the loop is the thing that is stuck, which is
  why an `asyncio.wait_for` version of this test passes against the bug) and
  assert an unrelated `/api/health` returns in well under the time the upload
  is held. The complexity test counts full scans of the element table rather
  than timing anything, so it pins the O(1) lookup contract without flaking.
  `test_a_real_import_keeps_the_server_answering` is the end-to-end guard: no
  monkeypatching, a 164-block master, `/api/health` polled throughout, every
  sample required to return promptly (it reports a 5.8s stall the moment any
  phase goes back on the loop). All five fail against the pre-fix code.

## Chat responsiveness on an imported master — implemented notes

Reported symptom: the chat still froze on a DOCX import, a successful import
posted a model-looking message into the chat, and an explanatory banner sat
at the top of the document panel. The offload above had unblocked the event
loop but nothing had been done about the thing the chat actually waited on.
No new deps, no new SSE event, one new REST route.

- **The sweep never runs inline any more.** `SessionState.source_edit_
  capabilities()` gained `block: bool = False`. A memo hit is unchanged
  (still zero probes). A memo MISS no longer sweeps on the caller's thread:
  it starts — or joins — ONE background sweep for that state
  (`start_capability_warm`, a daemon thread on the session, modelled on the
  runners' zombie-abandonment pattern) and returns a **`pending`** report
  immediately. `_sweep_and_publish` holds the old publish guard verbatim, so
  a warm whose state moved under it still refuses to write the memo. The
  warm is cleared with the memo on reset (`_reset_while_locked`) and project
  load (`project.py`); an abandoned one settles harmlessly because its
  publish guard compares the retained artifacts by identity.
- **At most one sweep runs at a time (`_capability_warm_next`).** The sweep
  is one opaque call into the source gate and cannot be interrupted, so a
  superseded one would run to completion anyway and have its result thrown
  away by the publish guard. A thread per state would therefore stack
  minutes-long useless O(n²) work — a streaming turn committing ten
  provisional bodies while the panel polls would leave ten sweeps fighting
  over one GIL, starving the only one that can still publish. A newer
  request instead replaces the single *queued* state (newest wins) and is
  picked up when the running sweep finishes, so superseded states are
  skipped rather than swept. The `pending` report is memoized on the same
  state key (`_pending_capability_cache`) — it is a complete per-element map,
  and rebuilding it per poll tick would allocate thousands of records a
  second and steal CPU from the sweep being waited on.
- **A sweep analyzes the tree its key describes, never the live one.**
  `_capability_work()` snapshots the document and hashes the projection FROM
  that snapshot, so key and tree are consistent by construction, and
  `_compute_source_edit_capabilities(current=…)` analyzes exactly it. Reading
  live state instead was a latent correctness hole that the move to a
  background thread widened from microseconds to minutes: a streaming turn's
  provisional edits would be swept, and if that turn then rolled back to the
  state the sweep was keyed on, the publish guard would pass and memoize a
  report describing a tree that never committed. This is the same
  anti-mutation snapshot the audit and Final QC passes already take. The
  publish guard survives, answering a different question — is this state
  still live, i.e. worth caching.
- **The model is told "pending", not "read-only".** Every operation is denied
  while the sweep runs, so `source_capability_summary` rendered the ordinary
  way would tell the model the whole imported document is permanently
  read-only, which it would then repeat to the user. It gets an explicit
  pending branch that says the analysis has not finished and that the gate
  will refuse (with its exact reason) anything attempted meanwhile.
- **`pending` is fail-closed, and that is what makes it safe.**
  `blocked_source_edit_capabilities` already took a `status`, so
  `CAPABILITY_STATUS_PENDING` reuses it: every body operation is denied with
  the `capabilities_pending` blocker, while the workspace-only metadata ops
  stay allowed (they do not touch the retained Word body — so the review
  walk's confirmations keep working while the sweep runs). An underived
  permission can only make the UI *more* restrictive. Authority never moved:
  `apply_doc_edits` and the read-only Final-QC remediation preview share
  `validate_source_backed_candidate`, which runs `validate_source_transition`
  over the complete proposed final state on every body change, exactly as
  `docs/DOCX_FIDELITY.md` says ("capability reports are UI guidance, not
  authorization").
- **Who blocks and who does not.** `_source_editing_boundary_block` (every
  turn's PROJECT CONTEXT) and `_doc_payload` never block — this is the fix
  for the freeze, since `_turn_context_text` runs BEFORE `stream_user_turn`
  yields its first SSE frame. The hot `/api/readiness` and `/api/qc/status`
  polls never block either; a pending capability summary simply reads as "the
  recorded inputs no longer match", i.e. stale, which is conservative and,
  after an import (empty document required) or a body change (QC stale
  anyway), also correct. The paths that ACT on the answer — `POST
  /api/qc/start`, `POST /api/qc/apply/preview`, `POST /api/qc/apply`, and both
  QC exports — pass `block=True`, and all five call
  `_settle_source_capabilities(session)`
  **before** taking `session_state_guard()` so they never hold
  `_turn_state_lock` across a sweep (that lock is what `claim_model_turn`
  needs, so holding it was a second, independent way to freeze the chat).
  When the sweep settles, the panel's poll also re-asks QC status and
  readiness: both compare against the capability summary, so while it was
  pending they answered "stale"/"not ready", and nothing else would have
  re-asked — a project opened with a retained master and a retained QC
  result would otherwise sit on a wrong "re-run Final QC" indefinitely.
- **Import returns without the sweep.** `POST /api/import/master` calls
  `session.start_capability_warm()` right after the commit and reports
  `pending`; `_doc_payload` then costs only one source-readiness plan (O(n)).
  New route `GET /api/doc/capabilities` returns just the report, so the panel
  can poll it while pending without rebuilding the outline, lint and
  readiness plan every tick. The poll is a self-scheduling `setTimeout` chain
  with backoff (750ms → 5s cap), never `setInterval`: the sweep takes minutes
  on a large master, so ticks must not stack behind a slow response. `App.send()`
  is now also gated on `fileLoading`, and the composer says so (a `uploading`
  prop, distinct from `disabled`, which means a turn is streaming and offers
  Stop) rather than swallowing the click — a turn started mid-upload would
  make the import fail its own `turn_active` guard after a long parse.
- **Nothing about an import goes into the chat.** `onImportMaster` lost all
  three writes: the `addNote("Importing …")` marker, the hand-written
  *assistant* bubble ("Imported N provisions … Tell me about the project"),
  and the `Import failed:` error bubble. `addNote` itself stays for research
  and Final QC. Both outcomes now render in the panel beside the progress
  line that already lived there, as ONE dismissible `importNotice`
  (`{tone: "error" | "warn", name, lines}`) — they are mutually exclusive by
  construction, so modelling them as two states would have been two
  near-identical strips that drift. A clean import shows nothing at all, but
  the importer's keep-everything-warn-loudly rule still has a surface. The
  project-open path is deliberately untouched (it was not part of the ask),
  though opening a project clears the notice, which described the document
  being replaced.
- **The imported-DOCX banner is gone** (the warn strip above the document).
  `Download original upload` moved into the Export menu, which was already
  the `importedMode` branch; nothing else it carried was load-bearing —
  `passThroughOnly`/`preservationReady` still drive that menu, and the
  "why is this disabled" text was already per-control via `draftTip`,
  `CapabilityButton` and `ReadOnlyBadge`, which render the server's own
  message. The one thing that replaces it is the pending line described
  above, which disappears when the sweep lands — and it renders that same
  server message rather than a third hand-written wording of it
  (`lib/sourceCapabilities.ts`: never add client prose to a denial).
- **Tests**: `test_import_reports_pending_permissions_without_sweeping_inline`
  and `test_a_chat_turn_starts_promptly_on_a_freshly_imported_master` in
  `tests/test_import_responsiveness.py` — the second builds a turn's PROJECT
  CONTEXT while the warm is deliberately still in flight and requires it
  under 1.5s; the same fixture costs 9.3s if you let the sweep block. Also
  `test_a_pending_report_still_refuses_a_forged_body_edit` (fail-closed
  survives the async window — the gate refuses regardless of the report) and
  `test_the_model_is_told_permissions_are_pending_not_read_only`. Tests
  that assert what permissions ARE now call `settle_capability_sweep()`
  (`tests/conftest.py`), which DERIVES the report (`block=True`) rather than
  only waiting on a warm — waiting alone is a race: with nothing in flight it
  returns instantly and the test's next read is the one that starts the
  sweep. `tests/fakes.py::audit_grade_qc_result` builds its input manifest
  with `block=True` for the same reason a real QC run does. The snapshot rule
  is pinned by `test_a_sweep_describes_the_tree_it_was_keyed_on_not_the_live_one`.

## Non-spec uploads — implemented notes (honest framing + reference documents)

Reported symptom: importing a file that is not a spec section still produced a
`SECTION [TBD]` header, three PART headings, and a "you forgot the section
number" lint finding — the app dressing a memo as a spec. Investigation split
it into a framing bug and a genuinely missing feature, fixed in that order.
No new SSE events, no new deps, no project-format bump.

- **The tree stays SectionFormat, deliberately.** Every edit, lint, diff, QC,
  readiness and export path is typed to it, and the exact source bytes were
  already retained, so this was never data loss — the panel was simply
  *asserting* scaffolding the file never had. The fix stops the assertions; it
  does not add a second document model. (The "real document mode" option was
  considered and rejected: roughly half the app's value — 5 QC lenses, every
  lint rule, readiness, research dimensions, standards, export schedules — is
  spec-shaped by construction, so it would be a second product, plus a
  permanent "does this apply in document mode?" tax on every future batch.)
- **Detection uses the parse's own signals.** `parse_master_docx` sets
  `spec_shape_detected` from whether `_SECTION_RE` / `_PART_RE` / `_ARTICLE_RE`
  ever matched — the same three branches it acts on, so the verdict can never
  claim structure the tree does not have. A paragraph consumed by the
  direct-numbering (`w:numPr`) branch never reaches the heading patterns and
  so never counts (pinned). It rides the existing sanitized `import_report`,
  which is already persisted; legacy files lacking the key, and any malformed
  value, degrade to `True` (conservative — a project we cannot re-examine
  keeps its original presentation and never fails to load).
- **What gets suppressed, and only while it is true.** `SessionState.
  import_is_unstructured()` is the one predicate. Lint drops
  `missing_section_header` (`lint_document(..., unstructured_import=)`; every
  other rule still applies — a stale edition is real in any prose). The panel
  swaps the invented header for a short explanation, hides empty parts, and
  drops `END OF SECTION`; `el-sec` moves to whichever block renders so the
  tour anchor and header edit still resolve. All of it turns back **on** the
  moment `section.number`/`title` exist, so converting the file into a spec
  behaves normally from that point — the gate reads document state, it does
  not latch at import.
- **The model is told too.** PROJECT CONTEXT gains an `IMPORTED DOCUMENT IS
  NOT A SPEC SECTION` block (never the stable prompt — the cache rule) saying
  the scaffolding is the app's, not to invent a section number, and to ask
  what the user wants done with the content. Without it the model reliably
  "fixed" the empty header on its own.
- **Reference documents are the feature the symptom was hiding.** The only
  ways to get a `.docx` in were `import/master` (becomes THE document) and
  `project/load-file` — so "here is the owner's standard, draft from it" had
  to masquerade as an import. `backend/reference_docs.py` adds the third
  path: text attached as *context*, never in the tree, never edited, invisible
  to lint / diff / QC / readiness / export.
- **Token discipline is the design's spine** (same reasoning as figures and
  fetched PDFs). The body never renders into PROJECT CONTEXT — only a stub
  line per document (id, title, block count, estimated tokens, TRUNCATED /
  Accept-All marks). The model opens one with `read_reference_doc`, whose
  result is elided from committed history by
  `_elide_reference_tool_results` (matches `tool_result` blocks back to their
  `read_reference_doc` `tool_use` ids; `is_error` results are kept — a
  correction the model should learn from is small). The tool description and
  the elision placeholder both say re-reading is expected and cheap to ask
  for. `READ_REFERENCE_DOC_TOOL` is appended LAST in `_chat_tools()`, after
  `suggest_prompts`, so existing tool bytes stay a stable cached prefix.
- **Upload path mirrors master import** minus the retention: bounded read,
  the same `inspect_docx_package` safety pass for a `.docx` (same attack
  surface), then extraction on a **worker thread** — `POST
  /api/reference/upload` is a third `async def` upload handler and is bound by
  the event-loop rule above. Nothing is retained for any type: text out, bytes
  dropped. There is no blank-document precondition (it never touches the
  spec), so it works at any point in a session. Truncation at
  `MAX_TEXT_CHARS` is loud in three places (the record, the stored text's own
  marker, the upload warning).
- **Five file types, because none of it becomes the document.** An import must
  build a SectionFormat tree, so it is `.docx`-only; a reference is just text,
  so it accepts `.docx`, `.pdf`, `.txt`, `.xml`, and `.csv` —
  `reference_extract.REFERENCE_KINDS` is the one table (extension → kind →
  extractor → label → the user-facing "or .csv" phrase, so a rejection message
  can never drift from the set actually accepted). The extension chooses the
  extractor only; every extractor still validates the bytes it gets. Design
  calls: a PDF is read per page with `[page N]` markers (worth the ~4 tokens a
  page — it is what lets the model answer "where does the standard say that?";
  `MAX_PDF_PAGES` is a runaway breaker, reported in both the warning and the
  text), a PDF with **no text layer is refused with the reason** rather than
  attached as an empty document the model would be told it holds, an
  owner-password-only PDF is unlocked with the empty user password (the common
  circulated-standard case) and a real user password is refused, and CSV/XML
  are kept **verbatim** — rows and tags are the content for a schedule or an
  export, so nothing is parsed away. Text decoding is a ladder (BOM UTF-16 →
  UTF-8 → cp1252 → latin-1) because Windows is the primary platform and an
  Excel CSV export is routinely not UTF-8; latin-1 cannot fail, so the NUL
  check is the only place a renamed binary is caught. `kind` rides the record
  (defaulting to `docx` — the store shipped Word-only, so a pre-existing
  project entry can only be Word), the panel row, the context stub, and the
  tool-result header, because how a reference should be read depends on what
  it is. Two shared helpers gained keyword-only parameters with byte-identical
  defaults: `read_upload_bounded(label=)` (the too-large message said "DOCX")
  and `sanitize_source_filename(extension=, fallback=)` (it appends `.docx` to
  anything else, which would rename `acme.pdf` and then send it to the Word
  extractor).
- **REST**: `POST /api/reference/upload`, `GET /api/references`, `DELETE
  /api/reference/{rid}`; `reference_docs` (metadata only — bodies would cost a
  full copy per poll) rides `_doc_payload`. Persisted as an optional
  `reference_docs` project key (no format bump, `load()` resets first so a
  project without them clears a live session's), and counted in
  `has_unsaved_progress` — a reference can be the only work in a session.
  `_REFERENCE_DOC_POLICY` joins the stable prompt after
  `_SUGGESTED_PROMPTS_POLICY`: read before drafting, never paste wording into
  a provision, never cite a reference as authority for a code requirement,
  what the file types mean (cite a PDF's `[page N]` markers, never quote one
  as content; a CSV/XML is data).
- **Tests**: `test_import_shape_detection.py` (detection incl. the
  numbering-branch agreement case, lint gating both ways, sanitize
  round-trip + legacy/malformed degrade, e2e + context block + the cache
  rule, exact-source export unaffected) and `test_reference_docs.py` (store
  units, endpoints, the three token-discipline invariants, persistence and
  lifecycle, plus every file type end-to-end: all five attach and read back,
  CSV/XML structure survives, PDF page markers and the page cap, no-text-layer
  and password-protected and renamed-binary refusals, the encoding fallbacks,
  extension preservation, kind through panel/stub/tool header and the project
  round-trip, legacy entries reading as Word). The PDF fixture (`_pdf_bytes`)
  is a hand-built minimal PDF — the repo has no PDF writer and pypdf cannot
  draw text, and a test-only dependency was not worth it. Plus a
  reference-upload case in `test_import_responsiveness.py`. Frontend pinned by
  `npm run build`.

## Research rounds — implemented notes (append, never overwrite)

Reported ask (Abraham): the user may press Research more than once in a
session, and each additional round must APPEND rather than overwrite. It
previously replaced everything — `start()` cleared `profile_result`, and the
completing run's profile became the whole truth. That threw away paid,
grounded findings and dangled every `Paragraph.source_item_id` chip pointing
at an item id the new run happened not to re-mint. No new endpoint, no new
SSE event type, no new dep, no project-format bump (one additive key).

- **The merge is a pure function in the engine; the runner owns when.**
  `append_research_round(previous, fresh)` (engine.py, beside the
  dataclasses) is the whole policy — no I/O, no model, fully unit-testable.
  `run_requirements_research` still produces exactly one round and now
  stamps it as round 1 through the same function, so a profile is
  well-formed from birth and the runner's job is only to renumber and fold.
- **Items join on `item_id`** — which is already a content hash of
  `(dimension_id, category, requirement)`, so "the same requirement found
  again" is identical by construction. A re-found item is **confirmed in
  place**, never duplicated: citations union, `grounded` ORs, `confidence`
  takes the max, blank descriptive fields fill in — evidence only ever
  strengthens. `round_index` stays the round that first found it. This also
  dedupes a single round's own duplicate ids, which the old path kept.
- **`research_date` dates EVIDENCE, not assertion** (review finding, fixed
  before merge): it advances only when the fresh occurrence actually
  grounded the item in a retrieved source. A round that re-states an item
  whose citations fail grounding has confirmed nothing — and since the
  union above keeps the earlier round's `grounded` flag and accepted
  sources, re-dating it would render older evidence as freshly verified,
  the exact overstatement `[UNVERIFIED]` exists to prevent. An item no
  round ever grounded carries the round that first reported it. The
  multi-round header states this rule verbatim so the model reads the same
  contract the code enforces.
- **Nothing is mutated.** The merge builds a new profile from
  `dataclasses.replace` copies, because the conversation thread may be
  rendering the previous profile into a turn's PROJECT CONTEXT at that exact
  moment. Pinned by
  `test_the_previous_profile_is_never_mutated_by_a_later_round`.
- **`dimension_statuses` is now the cumulative view; `rounds[]` keeps each
  round's own.** Cumulative `status` is `completed` once a dimension has
  completed in ANY round — its findings are real and still in the profile —
  with `error` carrying the latest round's message so a fresh failure stays
  visible, and the unmerged per-round record showing what actually happened.
  Item counts are RECOMPUTED from the merged items (a re-found requirement
  is one requirement); billed usage IS summed (every round's spend was
  real).
- **The meter must not re-bill.** `usage_total()` is cumulative by
  construction now, so the runner keeps feeding the ledger
  `result.usage_total()` — the round's OWN profile, before the merge. Pinned
  by `test_runner_accumulates_rounds_and_meters_each_one_once` (140 total
  across two rounds arrives as 100 then 40, never 100 then 140). A round
  that failed outright is metered from the raised error instead (Chunk
  4.3) and, never having been adopted, cannot re-enter that accumulation.
- **The merge runs inside the existing compare-and-set.** `_try_resolve`
  keeps being the single point every terminal transition goes through; the
  success path now passes an `adopt` callable applied under the same lock.
  That is what makes a stopped run safe: its late-finishing thread loses the
  CAS, so its discarded round is never folded into a profile that has moved
  on. `start()` no longer clears `profile_result` (the model keeps drafting
  from paid research while the next round runs); it DOES clear `events`,
  which is this round's progress log.
- **A failed or stopped round costs only that round**, and says so —
  `_failure_message` appends "Earlier research rounds are unchanged and still
  in use." when a profile survives, and the stop message became "this
  round's progress was discarded". `stop()` tags that terminal event with
  the active round (`_round_number`, read before the CAS while no fresh
  `start()` can renumber it) — a stop that beats the worker's first event
  would otherwise leave the round's whole log one untagged entry. Readiness
  is deliberately unchanged
  (still `status == "complete"`): a failed extra round leaves the session no
  worse off than before this work, and loosening a readiness gate was not
  the ask.
- **One round renders byte-identical.** The drafting-context header only
  changes shape at `round_count > 1`, and only then does each item line gain
  `; as of <date>` — because a single header date would otherwise claim
  round 1's findings were confirmed today. Same posture as every other
  "curated output stays byte-identical" rule in this file.
- **Legacy files are one round.** `from_dict` synthesizes round 1 from the
  saved `dimension_statuses`/`research_date` and back-dates the items when a
  file carries no `rounds`, so appending to a resumed project numbers the
  next round 2 rather than 1. Every serialized collection now goes through
  `_as_list` (review finding): `from_dict` promises garbage degrades to
  `None` and project load promises a malformed profile degrades to "not
  researched", but the load endpoint only translates
  `ProjectPackageError`/`ValueError` — so iterating a scalar (`"rounds": 1`)
  escaped as a 500 that blocked the whole project from opening. The same
  hole pre-existed for `items`/`dimension_statuses`; all three are closed.
  Note the knock-on: `to_dict()` gained keys, so
  the QC input manifest's research fingerprint changes — an old project
  resumed after this change reads its retained Final QC as stale once. That
  is the conservative direction (a stale marker over-warns; it never calls a
  stale result current) and it is also correct for every genuine second
  round, which really does change what the reviewers would see.
- **UI**: the button reads "Research again (round N+1)" with a tooltip
  saying it adds to what is there; the drawer strip shows "over N rounds";
  the stop confirmation now says only this round's progress is lost and
  names what survives; the findings report gains a **Research rounds**
  section (per round: date, new vs re-confirmed, dimensions completed, and a
  "failed this round" chip the cumulative view cannot show) and dates every
  item once there is more than one round.

## Trust dossier — implemented notes (the "I'm not convinced" modal)

The help nav's five topics answer *what* the app does; a reader who has to put
their seal on the output needs *how*. `components/TrustDeepDiveModal.tsx` is
that second layer: a long-form, twelve-section dossier opened from a link at
the bottom of "Why trust it?", written for a working AEC professional rather
than a developer. Frontend only — no route, no SSE event, no dep, no state
outside the modal.

- **It stacks, it doesn't replace.** Local `deepDive` state inside `HelpModal`
  (cleared whenever `topic !== "why-trust-it"`, so closing help or switching
  tabs drops it). The dossier renders as a **sibling** of the help backdrop,
  not a child — a click on its own backdrop must close only the dossier.
  `z-[60]` sits above help (`z-50`) and below `ModalShell` (`z-[70]`), which is
  never open at the same time.
- **Keyboard: `useDialogFocus`, same as the QC dialogs.** Containment matters
  more here than usual — without it Shift+Tab reaches the topic strip
  *underneath*, and activating a topic button changes `topic`, which clears
  `deepDive` and closes the dossier out from under a keyboard user.
- **One Escape closes one dialog, and that needs TWO guards.** `deepDive`
  keeps the help listener off while the child dialog owns the keyboard — that
  is the intent — but intent alone loses a race: the dossier's handler is on
  `document`, help's is on `window`, and **React flushes the close
  synchronously inside that native handler**, so help's effect has already
  re-run and re-attached by the time the SAME keydown finishes bubbling.
  (Verified in a browser: a probe listener on `document` bubble already sees
  the dossier unmounted.) `useDialogFocus` calls `preventDefault()` before
  closing, so help's handler also checks `e.defaultPrevented` — the reliable
  "already handled" signal, independent of React's flush timing. Removing
  either guard reintroduces a real bug.
- **Shape**: `max-w-5xl`, `max-h-[88vh]`, a sticky contents rail (`lg:` and up)
  with IntersectionObserver scroll-spy rooted on the scroll container, and
  `#id` anchors per section. The rail is presentation only — everything is in
  one scroll, so nothing is hidden behind a click.
- **The runtime section is the point.** Thirteen `<Runtime>` cards, one per
  user-triggered action (a chat turn, full draft, research, Final QC, applying
  a QC fix, master import, reference attach, figures, suggested replies, manual
  editing, export, the tutorial, stop), each answered in the SAME five terms —
  *you do / what runs / what is sent / AI involved / bounded by*. The uniform
  anatomy is what makes thirteen cards comparable rather than thirteen essays,
  and "AI involved: None" on six of them is the most load-bearing line in the
  document.
- **Every number is real** and traceable to the code it describes: 8/4 chat web
  allowances, 50 tool rounds, 40/12 governing-codes budget, 16 continuations,
  4 concurrent research dimensions, 5 lenses at 8 concurrent, 3/2 verifier
  seats, tie-to-refuters. When one of those settings moves, this modal moves
  with it — a trust document that has drifted from the code is worse than none.
- **One hand-authored inline SVG** (the data-flow diagram: your computer →
  Anthropic API → public web, plus the dashed optional GitHub update check). No
  external asset, no mermaid — the UI's "loads nothing from the internet" claim
  is made *by* the modal, so it must hold *in* the modal.
- **The `.docx` precision that matters**: extracted provision text DOES travel
  in the per-turn context; the retained package bytes never do. Both statements
  appear together everywhere the topic comes up.
- **"Nothing runs on its own" was an overclaim and is gone.** `App` mounts and
  calls `checkUpdate()` unprompted (the server throttles to once a day, but the
  first launch of a day really does hit GitHub). The dossier invites readers to
  *watch a firewall log* — so an unqualified claim would have been caught by
  exactly the reader it is written for. Everywhere the topic appears (short
  answer, the boundary list, the diagram label, Money, Security, and the
  `WhyTrustIt` point) it is now scoped to **model** work plus an explicit
  disclosure of the update check. Keep that scoping if the claim is reworded.
- **`SOURCE_OUTPUT_GUIDANCE` is reused, not restated** (the export card renders
  the shared constant) — the five contracts must read identically in Help,
  onboarding, and here.
- **The other four topics were resynced** in the same pass: templates and
  reference documents as on-ramps (`HowToUse`), two new recipes (`Workflows`:
  reusable template, drafting against an owner's standard), a
  "most of the app is not AI at all" point plus templates/references/figures
  (`HowItWorks`), and two new points on `WhyTrustIt` (deterministic gates,
  nothing runs on its own).
- Reuses `data-capability="help.topics"` rather than minting a capability id —
  the dossier is part of help, and the tour manifest is a coverage contract
  over that vocabulary.

## Guided tutorial figures — implemented notes (Chapter 6 generates its own figures)

The 10-chapter guided tour (`frontend/src/lib/tour.ts`'s `TOUR`, rendered as
"Chapter N/10" in `OnboardingOverlay.tsx`) runs against a disposable
protected tutorial workspace with its own REST surface
(`/api/tutorial/start|enrich|scenario/start|scenario/finish|restore|keep|
status`, `backend/tutorial.py` + the corresponding routes in `backend/app.py`)
and a scenario mechanism: several chapters build a throwaway `SessionState`
clone via `SessionManager.push_scenario`/`pop_scenario` (`backend/sessions.py`)
for that chapter only, discarded on exit. Picking a tutorial source can
trigger a coverage-gap-driven enrichment pass (a live model turn, with a
deterministic bundled fallback) before Chapter 1 begins. This supersedes the
"onboarding is frontend-only and passive" framing elsewhere in this file,
which predates the tutorial's REST/scenario rewrite; fully reconciling that
older description is a separate, larger documentation cleanup, not attempted
here.

- **Chapter 6's figures now come from Chapter 6, not tutorial start.**
  Reported symptom: the tour's "Figures and references" chapter has a step
  describing the model's live `create_figure` ability, and does show a real
  loading pause when entered — but the example figures visible at that point
  were actually attached to the session much earlier (either hardcoded
  fixtures or, on the live-AI-enrichment path, genuinely model-generated
  content, but still upfront, before Chapter 1). `tutorial.py`'s
  `media_practice_copy` fixes the timing: it builds Chapter 6's combined
  figures + references scenario by trying one real, scoped model turn
  (`tutorial_figures_directive`) asking for 2-3 simple figures through the
  normal `create_figure` tool, at the moment the tour actually reaches this
  chapter. Any failure — no key, an API/network error, the model touching
  document content it must not, or simply producing no usable figure —
  discards the attempt and falls back to `_ensure_tutorial_figures`'s bundled
  fixtures (backfilling only whichever kind(s) are still missing), mirroring
  `/api/tutorial/enrich`'s existing live-then-bundled pattern.
  `build_showcase_session`/`repair_tutorial_copy` no longer create figures
  upfront, and `tutorial_enrichment_directive` no longer asks for them either.
  `reference_practice_copy` is unchanged in behavior, refactored to share its
  fixture-attachment logic (`_attach_reference_fixtures`) with
  `media_practice_copy` so both scenario builders attach identical reference
  fixtures without double-cloning.
- **Coverage no longer gates on figures.** `analyze_tutorial_coverage` dropped
  its `figure_{kind}` gaps — under the new timing, no upfront path ever
  creates figures, so that gap would otherwise be permanently unresolvable
  and wrongly keep `coverage.ready`/`needs_enrichment` from ever settling.
  `counts["figures"]`/`counts["valid_figure_kinds"]` remain as informational
  counts only.
- **`/api/tutorial/scenario/start` stays a plain JSON endpoint** (the
  "behind the scenes" decision — no new SSE surface). The figure-generation
  turn is fully drained server-side (`for _event in stream_user_turn(...):
  pass`, events discarded) before the handler returns, behind the existing
  "Preparing the … chapter…" modal, which simply takes a bit longer now.
- **The scenario slot is reserved BEFORE paying for the build, not after.**
  `SessionManager.push_scenario` gained an optional `build:
  Callable[[SessionState], SessionState]` alongside `staged_session` — the
  same reserve-then-build ordering it already used for its own
  `clone_session_for_tutorial` fallback (check scope/no-existing-scenario/
  not-busy under lock, THEN construct outside the lock, THEN re-verify and
  activate). `app.py`'s `references` branch passes
  `build=media_practice_copy` instead of computing `staged` eagerly, so the
  reservation check happens before the (possibly billed) model call, not
  after. `push_scenario` also gained a `_transitioning` guard it was
  missing (review finding, fixed before merge): without it, two overlapping
  `scenario/start` requests could both pass the reservation check and both
  pay for their own build before either discovered the slot was taken —
  `_transitioning` is exactly the in-progress-build signal
  `restore_original_for_native_close` already reads, so checking it here
  closes the same race for every scenario kind, not just figures. Every
  other kind's construction stays cheap enough that pre-computing it (the
  existing `staged =` pattern) remains fine — only `references` needed the
  deferred path. Pinned by
  `test_push_scenario_rejects_a_second_request_before_the_first_pays_for_its_build`
  (a genuine background-thread race, not a state-poking unit test: a
  blocked-until-released `build` proves a second overlapping call is
  rejected — and never invokes its own `build` — before the first
  releases).
- **Real spend from a discarded attempt is never lost.** The attempt runs on
  a session clone (never the live tutorial session), so it's safe to build
  outside any lock; but `SessionManager.push_scenario` always re-derives the
  returned scenario's usage ledger from the base tutorial session's own
  ledger (`scenario.usage.load_snapshot(tutorial.usage.snapshot())`), so
  `media_practice_copy` merges the attempt's usage delta onto the base
  session (`source.usage.merge_delta(usage_before, attempt.usage.snapshot())`)
  before returning, win or lose — otherwise a failed live attempt would
  silently drop real billed usage. The same function also restores
  `suggested_prompts` from the base session afterward (a figures-only turn
  must not wind down the reply-chip bar the way an ordinary chat turn's
  unconditional latest-only replace would) and rewrites the committed
  directive's user message to a short honest summary (mirroring
  `/api/tutorial/enrich`'s existing rewrite) so the raw internal directive
  text never leaks into the visible chat transcript.
- **Idempotency.** Re-entering Chapter 6 without leaving it never re-triggers
  generation (`useOnboarding.ts`'s `enterChunk` short-circuits when the
  desired scenario is already active). Leaving and returning always rebuilds
  fresh from the same figureless base tutorial session (scenarios are
  discarded on `pop_scenario`, never merged back), so repeated visits cost a
  repeated (and potentially re-billed) generation rather than ever
  duplicating figures or reference docs — an accepted tradeoff, not a bug.

## Developer tools + always-on diagnostics — implemented notes (v1.6.0)

Abraham's ask: "very detailed and thorough diagnostics so I can troubleshoot
the software… capture as much as possible during each run, not just when
things go wrong", behind a **Developer tools** button in Settings. Two halves:
every run now leaves a rich local forensic record by default, and Settings
gains the read-only window onto it. No new Python deps (logging/faulthandler/
zipfile stdlib), no new npm deps; three new env knobs; six new REST routes
(all `include_in_schema=False`, the trace-viewer precedent).

- **The activity log is the new base layer** (`backend/diagnostics.py`).
  The packaged windowed build points stdout/stderr at `os.devnull`
  (`main._ensure_std_streams`), so before this NOTHING the app, uvicorn, or
  an unhandled exception printed survived anywhere. `init_logging()` (first
  thing `main()` does; idempotent, also called defensively — no-op under
  the test env) attaches a `RotatingFileHandler` (10MB × 5, local-time
  format with thread names) to the root logger in
  `<user_state_dir>/BuildASpec/logs/` — the *state* root, beside `traces/`,
  deliberately not `app_paths.app_config_dir()` (same folder on Windows).
  Root runs at `BUILD_A_SPEC_LOG_LEVEL` (default DEBUG); chatty third-party
  loggers (`httpx`/`httpcore`/`urllib3`/`keyring`→WARNING, `anthropic`→INFO,
  `uvicorn.access`→WARNING…) stay tamed so DEBUG means *the app* at DEBUG.
  uvicorn runs `log_config=None, log_level="info", access_log=False` — its
  loggers propagate to our handler; the request middleware is the access
  log. `BUILD_A_SPEC_LOG=0` disables (conftest sets it, the trace pattern).
- **Crash capture**: `faulthandler.enable()` into `logs/crash-faulthandler.log`
  (handle retained for signal-context writes), chain-preserving
  `sys.excepthook`/`threading.excepthook` wrappers that log CRITICAL with the
  traceback, and an atomic `run-marker.json` — next boot logs "previous run
  (pid N…) did not shut down cleanly". `main.py`'s pywebview-fallback
  `except Exception` (previously silent — a GUI failure was
  indistinguishable from pywebview-not-installed) now logs the exception;
  `webview.start(debug=settings.dev_mode())` adds the inspector in dev only.
- **Request + error visibility.** `_request_diagnostics` middleware
  (registered AFTER `_lease_slow_session_operations` in code — Starlette
  prepends, so last-registered = outermost, and it records the lease 409s
  too): one log line per request (method/path/status/duration-to-response-
  START — for SSE that is time-to-first-frame, deliberately; never drain a
  stream to time it) + an `api_request` trace event. `_QUIET_PATHS` (health/
  capabilities/qc-status/research-status/readiness/usage polls) log at DEBUG
  and skip the trace. A catch-all `Exception` handler logs the traceback and
  returns `{ok:false, error, code:"internal_error"}` 500 so the frontend's
  error idiom survives (Starlette sends it then re-raises by design — tests
  use `raise_server_exceptions=False`); a `RequestValidationError` handler
  translates 422s into the same idiom built from loc/msg ONLY (pydantic v2's
  `errors()[i]["input"]` would echo the submitted API key — pinned by test).
- **Recorder hardening** (backend/tracing/, deviations from the port noted
  in docstrings): (1) per-line flush — a hard kill loses at most the line in
  flight, previously everything since start (fsync stays close-only);
  (2) the thread-local span stack is REMOVED — capture hooks open spans on
  request threads and close them from daemon threads (research/QC/audit),
  where the stack never popped and later spans on a reused threadpool
  thread inherited a stale `parent_span_id`; every hook passes handles
  explicitly and `span()` nesting rides the ContextVar alone (existing
  nesting test still green); (3) `run.json` gains an `environment` block
  (platform/python/frozen/port/pid/models); (4) `open_span_summaries()` for
  the live-activity view; (5) redaction's bare `token` key pattern became
  `token(?!s)` — it was silently redacting every usage count
  (`input_tokens`…) out of every span, which would have gutted the new
  per-round records; auth tokens still redact (pinned both ways).
- **Capture breadth.** New never-raise hooks: `app_event(type, **fields)`
  (run-level one-liner; lazily starts the recorder so a launch that never
  chats still leaves a trace), `turn_round` (one `round_end` event per
  streaming round: round #, stop_reason, ms, per-round usage, tool/web-tool
  counts — emitted before the pause_turn branch so every round records), and
  `turn_prompts` (one `prompt_refs` event per turn: stable system block +
  frozen PROJECT CONTEXT + user text through `recorder.prompt_ref` —
  hash-deduped into prompts.jsonl at the default level, the stable prompt
  costs ONE entry per app run; deep mode inlines, making
  `BUILD_A_SPEC_TRACE_DEEP` real for the first time. `prompt_ref` runs
  `redaction.redact_text` — SUBSTRING-level credential redaction — before
  hashing/storing: prompts carry whatever the user pasted into chat, this
  is the one write path `scrub_data` does not cover, and whole-string
  scrubbing would erase the entire prompt over one pasted key). Event vocabulary from
  the REST layer: `api_request`, `workspace_conflict`, `server_started`
  (end of create_app — the run dir exists from boot), `doc_edit` (op
  count/actions/ok), `doc_history` (undo/redo), `project_save`/
  `project_load`, `export` (docx/qc_docx/qc_json/template/original_source/
  diagnostics_bundle), `reference` (upload/delete), `figure_delete`,
  `qc_apply` (outcome counts)/`qc_dismiss`, `session_reset`, `template`,
  `tutorial`, `key` (action + outcome, NEVER material), `update`,
  `stop_requested` (chat/research/qc), `client_error`. Events emit after
  outcomes and outside `session_state_guard()` wherever the route holds it
  (undo/redo/edit were restructured to return after the lock) — `app_event`'s
  lazy first-start does one-time file I/O that must not run under the
  turn-state lock.
- **Diagnostics REST surface** (all plain `def` — file I/O on worker
  threads, the event-loop rule): `GET /api/diagnostics` (scrubbed snapshot:
  app/tracing/logging/key(masked)/workspace/session/usage — field reads
  only under the guard, never `_doc_payload`, never the capability sweep),
  `GET /api/diagnostics/log?tail=` (seek-from-end bounded read, clamp
  1..5000, grace when disabled/missing), `GET /api/diagnostics/traces`
  (newest-first inventory, sizes only — never line counts),
  `GET /api/diagnostics/activity?tail=` (current run's events read back
  leniently after a short `recorder.flush()` barrier — per-line flush
  makes the file readable, the barrier makes it CURRENT — plus
  `open_span_summaries`), `GET /api/diagnostics/bundle` (zip written to a
  TEMP FILE and streamed via `FileResponse` + background unlink — a
  deep-trace run can be hundreds of MB and an in-memory zip would spike
  the process exactly when the user needs it; `recorder.flush(2.0)` runs
  before the copy so a bundle grabbed right after an incident CONTAINS
  the incident: snapshot.json + all log files + the CURRENT trace run in
  full + `run.json` of the 3 most recent prior runs; `_attachment_headers`
  + no-store + nosniff, the `/api/import/original` posture; byte-scan test
  proves the key never appears), `POST /api/diagnostics/client-event`
  (the frontend collector's sink: bounded >32KB→400, kind-allowlisted,
  logged + `client_error` event).
- **Frontend collector** (`lib/clientLog.ts`, installed in `main.tsx`
  before the root renders): `window.onerror` + `unhandledrejection` +
  wrapped `console.error/warn` (originals first, re-entrancy flag) →
  keepalive fetch, NOT via api.ts (helpers there throw; a reporter must
  never throw or recurse). Client-side throttle: one per `kind:message`
  per 10s, 40 reports/session hard cap (then one capped notice). The
  frontend previously had zero `console.*` calls and no error record.
- **Developer tools UI.** SettingsPanel gains a third section
  (`data-capability="session.developer-tools"`) whose button opens
  `DeveloperToolsModal` rendered as a SIBLING of the settings backdrop
  (the TrustDeepDiveModal stacking pattern — a child of the backdrop would
  bubble its backdrop click into settings' onClose; z-[60] over z-50).
  The modal (`useDialogFocus`, role=dialog — better a11y than settings
  itself) is single-scroll, manual-Refresh (`Promise.allSettled` over the
  four fetches, per-section failure lines): Environment (versions/models/
  paths/tracing/log status + copy-snapshot-JSON), Session state
  (workspace/doc versions/counts/flags/spend), Recent activity (newest-
  first event lines, type filter, in-flight spans), Activity log tail
  (+copy), Trace files (run list + root path + "Open trace viewer" via the
  shared `open_external_link` js_api bridge with `window.open` fallback —
  the shell has no reliable target=_blank), and the bundle download (plain
  `<a download>` + the sensitivity caveat: contains draft text and
  prompts, local-only, share deliberately — the trust posture).
- **Trace viewer rewrite** (same route, native file): the previous
  634-line Spec Critic artifact read files/fields Build-a-Spec never
  writes (findings.jsonl, mode/cycle_label — rendered "undefined"),
  filtered on event types never emitted, and needed the Tailwind CDN to
  style at all — in an app whose trust dossier claims the UI loads
  nothing from the internet. The rewrite is self-contained (inline CSS,
  zero network): real run.json fields + environment, span tree by
  `parent_span_id` (run-level events under a synthetic root; events
  pointing at a span that never closed get an "unclosed" node — crash
  forensics), event timeline with filter chips built from the types
  PRESENT in the file (new vocabulary needs no viewer edit), and
  prompt-ref resolution ({ref} → expandable text from prompts.jsonl,
  {inline} for deep runs).
- **Capability contract**: `session.developer-tools` minted (three-place
  edit: capabilities.ts, the settings section, a `ship`-chunk tour step
  anchored on the existing `settings` data-tour). `TOUR_VERSION` 3 → 4
  (step order changed; in-flight tutorial resume records reset — accepted).
- **Tests.** `tests/test_diagnostics.py` (16: logging init/knobs/marker,
  middleware log+event+quiet-list, catch-all + 422-no-echo, snapshot
  shape + no-key-material, tail bounds/grace, traces list, activity,
  bundle members + decompressed byte-scan for the key, collector
  logs/bounds/coercion, capture sites, round_end ×3 + prompt_refs +
  system-prompt dedupe across turns, workspace_conflict via the lease
  middleware) + three in `test_tracing.py` (readable-before-stop flush,
  cross-thread close leaves no stale parent, environment in run meta) +
  the `token(?!s)` scrub pin. conftest adds `BUILD_A_SPEC_LOG=0`
  (setdefault, the trace pattern); diagnostics tests opt back in with tmp
  dirs and MUST tear down via `diagnostics.reset_for_tests()` (logging is
  process-global; the log_env fixture does it).
- **Privacy posture unchanged, stated in more places**: key material never
  enters logs, traces, the snapshot, or the bundle (masked `key_status` +
  `scrub_data` everywhere outbound); document text DOES ride traces and
  the bundle by design (that is what makes them useful) and every surface
  that offers them says so. TrustDeepDiveModal's trace card became "Trace
  files and the activity log" (+ the data-flow diagram label) — keep those
  in sync with this section.

## In-app release notes — implemented notes (v1.7.0)

Reported ask (Abraham): publish a release, and let the user read what
changed when their app updates. The plumbing was half there — `latest.json`
already carried a `notes` string and `/api/update/check` already returned it
— but it only ever reached the user as the **tooltip** on the update pill,
so in practice nobody ever read it. No new deps, no new SSE event, no
project-format bump; two REST routes and one new capability-free modal.

- **The notes ship INSIDE the build** (`backend/release_notes.py`), they are
  not fetched. A freshly-updated app can say what changed with no network at
  all, which is the same posture the trust dossier promises everywhere else
  ("nothing runs on its own" is scoped to model work plus the disclosed
  update check — a release-notes fetch would have been a third thing to
  disclose). Structured data (`ReleaseNote` → `ReleaseSection` →
  `ReleaseItem`), not markdown, so the modal, the manifest summary and the
  release-page markdown are three renderings of ONE source and cannot drift.
- **One entry, three audiences.** The bundled entry drives the in-app modal;
  `manifest_summary()` writes `latest.json`'s `notes` (what a **not-yet
  updated** app reads — it describes the version you would be getting);
  `markdown_notes()` writes the GitHub Release body.
  `packaging/windows/render_release_notes.py` renders the latter two at
  build time and **exits non-zero when the version has no entry**, and
  `test_the_shipped_notes_describe_the_shipped_version` fails the suite for
  the same reason — a version bump without notes would put an empty modal in
  front of every user who updates, which is worse than no feature.
- **Fresh install vs upgrade is decided at boot, not per request.** The
  marker is `last_seen_version` in the existing update state file. Absent on
  every build before this one, so absence alone cannot distinguish "never
  ran this app" from "upgraded from 1.0.0". The tiebreaker is whether that
  state file **existed at startup** (`app.state.ran_before`, a pure read in
  `create_app`) — sampled once because `/api/update/check` CREATES the file
  on first run, and `App.tsx` fires both on mount, so a per-request read
  would show a first-time user the product's back catalogue whenever the
  update check happened to land first. Three cases, in
  `release_notes.resolve_pending`: marker present → everything newer than
  it; no marker but `ran_before` → everything since
  `EARLIEST_KNOWN_VERSION` (1.0.0, the only release that ever shipped
  without the marker); neither → nothing.
- **A cosmetic modal must never break a launch.** `notes_between` treats an
  unparseable bound as "no bound" rather than raising, and the boot probe is
  wrapped — a corrupt state file degrades to showing the notes, never to a
  failed start. Pinned by `test_a_corrupt_last_seen_version_degrades_
  instead_of_raising` and `test_the_endpoint_survives_a_corrupt_state_file`.
- **Routes**: `GET /api/release-notes` (launch check; `?all=true` is the
  Settings button, which must work even when nothing is due — it returns the
  entries with `pending: false`, i.e. "the user asked, the app did not
  volunteer") and `POST /api/release-notes/seen`. Dismissing marks the
  version seen **even when opened from Settings** — the user has now seen
  them either way, and the alternative re-opens them unprompted next launch.
  `mark_version_seen` merges into the existing state dict, so recording it
  cannot erase the update throttle's `last_check` (pinned).
- **No new capability id.** The Settings entry declares the existing
  `updates.manage` — the TrustDeepDiveModal precedent (the dossier reuses
  `help.topics`): the tour manifest is a coverage contract over a
  *vocabulary*, and "keep the app current / see what changed" is one
  capability with two controls. The existing `help-updates` tour step
  already covered it; its body text was **stale** (it described an About
  section that PR #23 deleted) and now describes what actually ships.
- **v1.7.0 is the first release since v1.0.0.** 1.1.0–1.6.0 exist only as
  version numbers in the code — 66 merged PRs and ~45k lines that no user
  ever received. The 1.7.0 entry is therefore written as one combined,
  theme-grouped list ("since 1.0"), not six per-version sections for
  releases nobody had. Future entries are per-version as normal; the data
  model was per-version from the start.

## Live research visibility — implemented notes (the per-agent board)

Abraham's ask: research takes minutes and the user saw nothing between
`research_started` and the first `dimension_complete` — "I want users to
know what is happening, in as much detail as possible, without breaking my
app." Research only (QC parity is a noted follow-up); panel board only (no
live modal, no chat ticker — both offered, declined). No new endpoints, no
new deps, no new env vars, no new SSE channel; the fan-out's stop
semantics, retry policy, pause_turn loop, and grounding are untouched.

- **Workers narrate through the existing sink.** Five new worker event
  types (all carry `dimension_id`; see the research-channel paragraph for
  payloads): `dimension_started`, `dimension_activity` (on change only —
  per-worker last-kind memory, reset after a retry so attempt 2
  re-announces), `dimension_search`/`dimension_fetch` (live, not post-hoc),
  `dimension_retry`. `research_started` gains `dimension_titles` so the
  board seeds real names before any worker speaks. Deliberately NOT added:
  continuation/grounding events (activity + search ticks already prove
  liveness; grounding is local milliseconds already summarized in
  `dimension_complete`). ~200–800 events/round worst case, ≤~120KB — the
  log is per-round and cleared at each start.
- **The engine iterates the stream it already had open.**
  `_relay_stream_activity` (engine.py, copy-adapted from
  `conversation._stream_events` per the copy-don't-import posture) runs
  INSIDE the existing `with client.messages.stream(...)` before
  `get_final_message()` — the chat loop's proven iterate-then-final shape
  (the SDK accumulates during iteration, so the final message is
  byte-identical to before). Detection triple: `content_block_start`
  records (type, name), announces the activity kind, and copies any
  already-complete `block.input` (see below);
  `input_json_delta` accumulates ONLY for `server_tool_use` blocks (the
  output tool streams the whole findings payload — never buffer it);
  `content_block_stop` parses the buffer and emits the query/URL, skipping
  empties. Every frame is wrapped per-event try/except — a malformed frame
  is skipped, never a dimension failure (pinned) — while iteration errors
  propagate into the existing retry classifier exactly as
  `get_final_message` errors always did. No `should_stop` inside
  iteration, no early break: Batch 7's no-mid-call-interruption decision
  stands; post-stop EMISSION is what changed, and the runner drops it.
- **A tool input arrives in one of two shapes, and all three relays read
  both** (deep-dive remediation Chunk 2.1). The direct caller streams
  `input_json_delta` frames; the **code-execution** caller can hand the
  whole input over on `content_block_start.content_block.input` and stream
  no deltas at all, which is what left the research board, the Review Room
  and the chat chip with a nameless "Searching the web…". Chunk 1.1's
  `allowed_callers: ["direct"]` is the fix; this is the fallback that
  survives a future code-execution-called tool, provider-side shape drift,
  or an owner decision to re-enable dynamic filtering. Each relay
  (`research/engine._relay_stream_activity`,
  `qc/engine._relay_stream_activity`, `conversation._stream_events`) keeps
  a `start_inputs` map beside its JSON buffers, **copies** the mapping
  (`_start_input` / `_start_block_input` — never retains the SDK block,
  which the stream accumulates into), and at stop uses
  `streamed or started`: **deltas win** when both exist, so nothing about
  the normal path changed. Every index is popped from all three tracking
  dicts at `content_block_stop` — a repeat stop therefore has nothing left
  to replay, which is how the popping is pinned. Absence behavior is
  deliberately NOT uniform: research and QC skip a query/URL they do not
  have, chat still emits its chip with empty text (the round did search;
  saying so unlabelled beats silence).
- **The runner got the QC run token, one notch stronger.** Multiplying
  event volume made two latent races load-bearing: a stopped run's
  still-unwinding workers appending to the NEXT round's cleared log
  (before: ≤4 stale frames; now: a flood), and — genuinely pre-existing —
  a stopped round's late thread passing `_try_resolve`'s status CAS once
  the next round is RUNNING and adopting its discarded profile.
  `ResearchRunner` mints an unforgeable token per `start()`; `_emit`
  drops mismatches (and the trace mirror with them); `_try_resolve`
  checks it AND clears it on any terminal win, so after `stop()`'s
  terminal event nothing from the ended run lands anywhere.
  `sse_events()` became bind-at-call (the QC shape — an outer function
  captures the token under lock and returns the inner generator, because
  a lazy generator "bound" inside Starlette's response iterator binds
  nothing), closing `stream_end {status: "superseded"}` when a newer live
  run appears; the sentinel stays exactly `{type, status}` (pinned by
  `test_research_api`'s exact-dict assert — never add a key to it).
- **The trace-capture bug is fixed because these events are the payoff.**
  `capture.research_event`/`qc_event` passed the sink event dict as
  `**kwargs` into `recorder.add_event(handle, type, ...)` — every event
  carries a `"type"` key, so every call TypeError'd into the never-raise
  except and NO research/QC progress event had ever reached a trace. The
  hooks now pop it into `event_type`; the new fine-grained events land in
  `research_progress` trace events for the diagnostics surface (pinned by
  a test that fails against the old code).
- **The frontend merges payloads instead of refetching per frame.**
  `followResearch` used to discard every SSE payload and refetch the FULL
  snapshot (whole log + whole profile) per frame — O(frames × payload)
  once the log turned chatty. Now `mergeResearchEvent` appends by `seq`
  into local state (replay-from-0 on reconnect dedupes; `research_started`
  restarts the local log; epoch-guarded like every other stream), and the
  authoritative snapshot is refetched only on the five milestone events +
  the stream's end. Status/error/profile stay snapshot-owned — the merge
  never touches them. The refetch itself lands through
  `reconcileResearchSnapshot` (review finding, fixed before merge): the
  fetch is async and its worst case fires on `research_started` — the
  exact moment all four workers burst — so a response captured early but
  resolved late would wholesale-replace a longer local log and march the
  board backward until the next milestone, minutes away. **Superseded by
  the reconnect work below** — same-round comparison is now by WATERMARK
  and a stale response is rejected outright rather than half-adopted.
- **The drawer's running body is the agent board.** `foldResearchBoard`
  (a pure `useMemo` fold over `research.events`, the QCDrawer lens-row
  precedent) seeds one `AgentCard` per dimension from `research_started`:
  queued → "Waiting for an agent…"; running → breathing `.agent-dot` +
  status-dots + shimmering StatusStrip-vocabulary activity line
  ("Thinking… / Searching the web… / Reading a source… / Writing up
  findings…"), the last 3 live queries ("quoted") / URLs (host/path,
  full text on hover) sliding in via `.prompt-chip-in`, live tallies
  flashing accent per increment (`.tally-flash` retriggered by
  `key={count}` remounts), and a warn retry line ("Retrying (attempt
  2/3) — rate limited…"); done → the report modal's EXACT telemetry line
  ("✓ N findings · M grounded · X searches · Y fetches" — the live view
  visually becomes the report); failed → the err line. The header
  summary and start-button label read the fold's done/total — the old
  `events[events.length-1].done` derivation silently regresses once the
  last event is almost never a `dimension_*`. The drawer auto-opens on
  start (`bumpDrawer("research")` in `onStartResearch` — the declaration
  moved above the research callbacks; referencing it from a deps array
  1400 lines before its `const` was a first-render TDZ crash). Failed/
  stopped runs yield the board to the existing error line + retained
  earlier-round findings.
- **Animation stays inside the house style.** Two new keyframes in
  index.css, each with its own reduced-motion block immediately after:
  `.agent-dot` (soft accent box-shadow ring, 1.6s) and `.tally-flash`
  (0.6s color-from-accent, runs once per remount). Everything else reuses
  `.status-dots`/`.status-shimmer`/`.prompt-chip-in`. The board originally
  shipped with no `data-capability`/`data-tour` (passive display) —
  SUPERSEDED by the agent-activity modal below, which makes the cards
  interactive under `research.agent-detail`.
- **The cards are clickable — `AgentActivityModal` is the full per-agent
  view.** The board fold + its vocabulary (`ACTIVITY_LABELS`,
  `RETRY_REASONS`, `trimUrl`, `labelFromId`, `DimLive`,
  `foldResearchBoard`) moved verbatim to `lib/researchAgents.ts`, which
  adds `foldAgentDetail(events, dimensionId)` — the uncapped fold: a
  chronological, timestamped timeline of ONE dimension's events (started
  w/ budgets, every activity change, every query full-text, every URL,
  every retry incl. backoff, the terminal telemetry), with `dim` reused
  from `foldResearchBoard` so the modal header and the card can never
  disagree. Pinned by `tests/researchAgents.test.ts` (registered in
  package.json's explicit `node --test` list). `AgentCard` became a real
  `<button>` (inner p/ul/li → block spans — a button allows no flow
  descendants; recent URLs stay plain text) opening one drawer-owned
  modal (`agentDetailId` state, torn down when `research` goes null),
  rendered OUTSIDE the `running`/`expanded` gates like
  ResearchReportModal so it survives run completion mid-view.
  ResearchReportModal chrome + QCReportModal `useDialogFocus` wiring;
  live via a `useMemo` re-fold per SSE merge; follow-bottom is Chat.tsx's
  pinned-scroll pattern minus the rAF loop (this feed only grows on
  commits); a run that ends before the agent's terminal event shows an
  "interrupted" pill + a "Run ended before this agent finished." row —
  never "running" for a dead run. Capability `research.agent-detail`
  rides the EXISTING `research-run` tour step (one step, three controls
  — the `updates.manage` precedent), so no new step, no anchor, no
  `TOUR_VERSION` bump.
- **Fakes**: `_FakeResearchStreamCtx` gained `__iter__` (explicit
  `.events` override for malformed-frame injection, else
  `_synthesize_events` — which already emits the start/delta/stop triple
  for `server_tool_use`); `research_response` gained `queries=` (prepends
  `server_tool_use`(web_search) blocks — result blocks alone synthesize
  no stream events, so a fixture without it emits no live search).
- **Tests**: engine live-emission matrix (per-dimension subsequences,
  never global order — four threads interleave), activity-on-change-only,
  retry event, malformed-frame survival (test_research_engine); the
  supersession race — a stopped round's blocked-then-successful client
  released only after the next round completes, proving both the log
  token drop and the adopt token check (test_research_rounds); the
  stopped-round test now also pins `research_failed` as the log's LAST
  word after the workers unwind; live query over the API + the untouched
  `stream_end` exact-dict pin (test_research_api); the trace-capture fix
  (test_tracing). Frontend pinned by `npm run build` + `npm test` (the
  no-vitest convention stands).

## Research follower reconnect — implemented notes (`lib/researchLive.ts`)

Deep-dive remediation Chunk 2.3. The research follower was the last live
stream in the app that could not survive its own transport: one close and
the agent board froze mid-run with no way back short of a reload — on a
fan-out that routinely runs half an hour. `followResearch` now runs the
loop `followQc` already proved, and the pure helpers behind it moved out
of `App.tsx` into their own module. Backend untouched: no route, no event
type, no dep.

- **`frontend/src/lib/researchLive.ts` is the sibling of `qcLive.ts`**, and
  deliberately shares its vocabulary (`RESEARCH_MILESTONE_TYPES`,
  `mergeResearchEvent`, `reconcileResearchSnapshotUpdate` returning
  `{snapshot, accepted}`, `isResearchActiveSnapshot`) so a reader who has
  understood one follower does not re-derive the other. What differs is
  **identity**: QC has a `run_id`, research has a 1-based `round` — and a
  round number is NOT unique, because `ResearchRunner.start` numbers from
  `profile_result.round_count` and a stopped round is never adopted, so
  the next start reuses it.
- **Same-round staleness is decided by WATERMARK, not length.** The old
  rule kept the longer local log but still adopted the fetch's
  status/error/profile, so a pre-terminal response resolving late could
  report `running` over a finished round and drop the profile that had
  just been adopted. Now: fetched max-seq **<** local ⇒ the response is
  wholly stale and is **rejected** (`accepted: false`, no side effects,
  notably not the auth modal); **>** ⇒ adopt, unioned by seq; **=** ⇒ a
  peer, adopted except for a lifecycle state it would regress (a terminal
  status must not become `running`, an adopted profile must not vanish).
  A different round — or either side round-less — replaces wholesale.
- **A restarted round is deliberately NOT a reconcile case, and cannot
  be.** "Stop round 2, start round 2 again" and "a late fetch from the
  middle of round 2" present the identical triple (same round, shorter
  fetched log); a rule that adopted the first re-opens the second, which
  is the bug this chunk exists to fix. It was tried, and the tests said
  no. Two mechanisms outside reconcile keep it honest, and both run before
  any refresh for the new round can resolve: `onStartResearch` bumps the
  refresh generation (rejecting everything in flight), and
  `mergeResearchEvent` resets the log on the new round's
  `research_started`, which always precedes the milestone refetch that
  frame triggers.
- **The merge's reset became run-aware, because reconnect made replays
  routine.** Every reconnect replays the round from seq 0, so resetting on
  every `research_started` would blank the board on each transport hiccup
  and rebuild it from the replay. A frame starts a new run when the local
  log is empty, the round differs, **or the local snapshot is not
  running** — that last clause is what catches the same-number restart,
  since a terminal local status means the previous round ended.
- **`stream_end` is classified, not string-matched.**
  `types.ResearchStreamEndStatus` is closed and
  `classifyResearchStreamEnd` switches over it with a `never` arm →
  `terminal` / `superseded` / `interrupted`. Only `interrupted`
  reconnects. Adding a status without deciding what it means for
  reconnection is now a type error.
- **The follower owns an `AbortController`**, and
  `advanceWorkspaceEpoch()` — the one helper every session/tutorial/
  project transition now calls — bumps the epoch *and* aborts it, so the
  abort cannot be forgotten at a future call site. `streamResearch(signal)`
  passes it to `fetch`, and `readSse` gained a `finally` that cancels the
  reader: unwinding the generator released nothing, so every prior
  `break` left the browser holding a body no one would read again. (QC's
  follower still relies on its epoch check plus a `break`; giving it the
  same abort is a separate change.)
- **`refreshResearch` no longer nulls the snapshot on a failed fetch.**
  One dropped poll used to erase a live board. It now does nothing (the
  `refreshQc` posture) and lets the follower reconcile at the next
  milestone or at stream end.
- **`researchSnapshotRef` backs every between-render decision** (the
  `qcSnapshotRef` pattern): reconnect-or-not and accept-or-not are read
  synchronously, never from React state that may not have committed.
  Every write goes through `replaceResearchSnapshot` — there is exactly
  one `setResearch` call site left, inside it.
- **Tests**: `frontend/tests/researchLive.test.ts` (22 — merge ordering
  and dedupe, replay-does-not-blank, different-round reset, same-number
  restart, watermark/round/active helpers, every `stream_end` status, the
  terminal-over-running race on round 1 and on a later round, wholesale
  replacement, equal-watermark non-regression both ways, generation
  rejection and acceptance, first-snapshot and round-less adoption, and
  the milestone set) and `frontend/tests/researchStream.test.ts` (3 — the
  signal reaches `fetch`, an abort ends the stream, and breaking out
  releases the body, against a stubbed SSE body that never closes so a
  leak cannot pass by accident). Both are registered in `package.json`'s
  explicit `node --test` list.

## Constant-time replay dedupe — implemented notes (`lib/eventSeqIndex.ts`)

Deep-dive remediation Chunk 2.4, and the chunk reconnect made necessary.
Both followers now replay their whole runner log from seq 0 on every
transport hiccup, and both decided "have I already got this frame?" by
walking the log — `maxEventSeq(...)` per frame in QC, `events[length - 1]`
plus a full re-sort per duplicate in research. So recovery was quadratic in
the log it was recovering, on the two logs in the app that run to hundreds
or thousands of frames. Frontend only: no route, no event type, no
dependency, no backend change.

- **`frontend/src/lib/eventSeqIndex.ts` is shared by both followers**, which
  is the one place this codebase's copy-don't-import posture does not apply:
  the backend relays are deliberately triplicated because they diverged, but
  this is a single correctness-critical primitive whose two copies would be
  identical, and drift in a duplicate test is exactly what would go
  unnoticed. The index is `{min, max, missing}` — the span of sequences
  present plus the ones absent from inside it — and `hasEventSeq` is a
  bounds check plus one `Set` lookup.
- **`missing` is what makes the append path O(1).** A plain `Set` of present
  sequences would have to be COPIED per append, trading one O(n) scan per
  frame for one O(n) copy per frame — the same complexity, no win. A log the
  server produced is dense, so `missing` is empty and the next index shares
  the same set object. Only a jump past the watermark allocates, and only
  the gap's width.
- **Memoized per events ARRAY, and immutably.** Nothing in this app mutates
  an event array (every merge and reconcile builds a new one), so a `WeakMap`
  entry can never describe an array that has since changed. Deriving an index
  for a new array never touches the old array's entry, so merging onto an
  older snapshot costs one rebuild rather than producing a wrong answer —
  which a mutate-and-share design would have.
- **A duplicate returns the PREVIOUS snapshot object, untouched.** The runner
  log is append-only and a sequence number is assigned once, so a replayed
  frame carries the payload it carried the first time and has nothing to
  update. First arrival wins. That is a deliberate reversal of the previous
  behavior (both merges re-sorted and let the replay REPLACE its
  same-seq predecessor, and two tests pinned the replacement), and it is what
  lets React's `Object.is` bail-out skip the render entirely — the point of
  the chunk is that recovering a hiccup costs nothing, not that it costs
  less. It is also why `foldQcLiveState` / `foldResearchBoard` needed no
  change: they are `useMemo`d on the snapshot, so an unchanged object means
  the O(n log n) fold does not run either.
- **Identity is settled BEFORE any sequence comparison**, because sequences
  restart at 0 for every run and round: a frame from another run collides
  with this one's numbering, so a dedupe that ran first would read the
  collision as a replay and drop the frame on the floor. What each merge does
  with a foreign frame differs, and deliberately:
  - research resets on a LATER round (the follower missed the roster frame;
    the milestone refetch restores it) and IGNORES an earlier one — an
    abandoned round's straggler belongs to no log on screen, and the
    re-sort path would have overwritten the live round's frame at that
    sequence with it;
  - QC keeps its existing `qc_started` reset and ignores any other foreign
    frame. There is no reset to take: a run id is a UUID, so unlike a round
    number it cannot be read as "newer", and a superseded run's
    `qc_complete` would otherwise flip the live run to complete.
- **Dropping a frame takes stronger evidence than resetting.** QC's reset
  still consults `qcSnapshotRunId` (with its retained-report fallbacks,
  unchanged); the ignore path consults only the LOG's own run id. A retained
  result's id says nothing about which run is streaming, and mistaking the
  live run for a foreign one would swallow its terminal frame permanently.
- **`researchEventsRound` is memoized the same way**, for the same reason and
  with the same safety argument: the merge asks for it on every frame.
- **Tests**: `frontend/tests/eventSeqIndex.test.ts` (11, new and registered
  in `package.json`'s explicit `node --test` list) pins the primitive
  directly — density, gaps, a log that does not start at zero, a repeated
  sequence, non-finite input, memoization, the shared gap set, and that
  deriving a new array's index leaves the old array's correct.
  `qcLive.test.ts` and `researchLive.test.ts` each gain the referential-
  identity claim (a replay returns the same snapshot AND the same array), a
  cross-run/round rejection, and the **bounded-access** test the plan asks
  for: a `Proxy` counts every property read on a 400-frame log and the whole
  replay must cost one index build and then literally nothing — no elapsed
  time anywhere. Each of the three mechanisms was reverted in place to prove
  it load-bearing (dedupe removed → 3 red; identity-first removed → 1 red;
  memo removed → 4 red).
- **`allowImportingTsExtensions` is now set in `frontend/tsconfig.json`**,
  and the two imports of this module carry `.ts`. `npm test` runs
  `node --test` directly over the sources and Node's ESM resolver requires a
  real extension on a relative specifier; every other extensionless sibling
  import in `src/lib` is either `import type` (erased before Node sees it) or
  in a module no test loads. The flag only PERMITS the extension, requires
  `noEmit` (already set), and Vite resolves it unchanged. Any future value
  import between two `src/` modules that a test file loads needs the same
  extension.

## Incomplete research coverage is named — implemented notes

Deep-dive remediation Chunk 3.1. A research round succeeds when ANY dimension
completes, so a partial profile is a normal, supported outcome — and every
surface described it as a COUNT ("2 of 4 dimensions completed"). A count is
not actionable: absent findings are indistinguishable from a dimension that
looked and found nothing, which is the difference between "no seismic
requirement applies here" and "nobody checked". Four surfaces now name the
gap. No new endpoint, no new SSE event, no new dep, no project-format bump.

- **`incomplete_dimensions(profile)` is the one definition** (research/
  engine.py, beside `DimensionStatus`): cumulative statuses that have never
  completed, in module declaration order. `dimension_display_title` falls
  back to the id, because a legacy profile saved before titles were stored
  would otherwise name nothing at all. `incomplete_dimension_facts` is the
  telemetry-safe projection (`{dimension_id, title, error_kind}`). Four
  callers, one rule — and the rule has to be CUMULATIVE: a dimension that
  completed in an earlier round is researched even if the latest round
  failed it, so judging the latest round alone would warn about coverage the
  session actually has.
- **`render_text` names it directly after the provenance line it qualifies**,
  and never as an item — so `research_context_block`'s trimming, which drops
  whole items lowest-confidence-first to fit the cap, cannot remove it. That
  matters most in exactly the profile where it is trimmed: the one whose
  remaining findings would otherwise look like the whole picture. A profile
  with every dimension completed renders **byte-identically** to before (the
  warning interpolates as `""`), which is the same posture as the
  single-round rendering rule above.
- **The wording is the semantic point, not the count**: "INCOMPLETE
  COVERAGE: research for X never completed. Findings from this area are
  ABSENT, not verified-empty; do not treat them as researched. Where a
  provision would depend on this area, say so rather than assuming that
  nothing applies." Aimed at the model (it cannot press Research), and it
  reaches all five QC lenses for free through `_render_profile` →
  `_lens_shared_prefix` — which takes no lens argument, so the warning
  physically cannot vary between them.
- **`DimensionStatus.error_kind` is a sanitized token beside the
  user-facing message.** Two of the recorded messages embed provider
  exception text (`f"{type(exc).__name__}: {exc}"`), and the drawer is the
  right place for that but a trace span and a support bundle are not. The
  kind is chosen at the `_failed` choke point rather than reverse-engineered
  from prose later; a raised provider error reports its
  `retry_policy.FailureClass` value, which is already closed and
  str-valued "for cheap telemetry" — so a bundle reads `rate_limit`, not
  "something raised". `DIMENSION_ERROR_KINDS` is the union. The cumulative
  merge carries `error_kind` beside `error` (both report the LATEST round's
  outcome), and `_statuses_from_raw` defaults it empty on older files.
- **The closed vocabulary is ENFORCED, not just documented** (`sanitized_
  error_kind`, caught in review on PR #96). `.baspec` files are shared
  between people and the deserializer is deliberately permissive, so
  arbitrary text in a saved `error_kind` rode the facts projection straight
  into `/api/diagnostics` and a support bundle — precisely the payload the
  vocabulary exists to keep out. Applied at BOTH ends: at load, so a
  `DimensionStatus` never carries a value its own docstring forbids, and
  again in the projection, because that is where the telemetry-safe promise
  is made and it must hold however the value arrived (a future code path
  that bypasses the loader cannot reopen it). An unrecognized value becomes
  `unrecognized` rather than `""` — a bundle should be able to show that the
  file carried something odd without the odd thing itself travelling, and
  `""` already means "a success, or a pre-3.1 file". The user-facing `error`
  message is untouched: free text is what it is for, which is exactly why it
  is not in the projection.
- **The QC manifest captures the names, because a report is an audit of the
  run's INPUT snapshot** (`qc/engine.research_manifest_facts`): a report
  opened later cannot consult the live profile, which may have gained a
  round. `dimension_count` + `completed_dimension_ids` /
  `failed_dimension_ids` (module order, disjoint) + one `dimension_titles`
  map — a map rather than parallel title arrays, which cannot fall out of
  alignment. This changes `input_fingerprint` for new runs by design; a
  persisted report is never rewritten. Chunk 3.2 adds the required-policy
  ids here, and Chunk 3.3 renders them into both report projections.
- **Partial coverage is observable without opening a project**: the research
  trace span closes with `incomplete_dimensions` (absent, not an empty list,
  when the run was clean — a complete span stays byte-identical), and
  `/api/diagnostics`'s session block gains a `research` record with the same
  facts. Read as plain runner attributes the way readiness and
  `_doc_payload` already do, never the runner's own lock — that would nest a
  second lock under the session guard.
- **Tests**: 18 new. `test_research_engine.py` — complete-profile byte
  identity, one gap, several gaps in module order, the id fallback, the raw
  provider error staying out of the context, trimming that cannot drop the
  warning, the kind recorded per failure mode, and the facts projection.
  `test_research_rounds.py` — a later round that covers the gap retires the
  warning while the fresh failure stays recorded, a serialization round
  trip, and a pre-`error_kind` file. `test_qc_manifest_integrity.py` —
  names not just counts, an absent profile recording empty coverage,
  partial coverage changing input identity, and every lens being told.
  `test_diagnostics.py` / `test_tracing.py` — the snapshot and span facts,
  each asserting the provider payload is absent. Every mechanism was
  reverted in place to prove it load-bearing (the warning → 7 red, the
  merge's kind → 1 red).

## Required research coverage gates readiness — implemented notes

Deep-dive remediation Chunk 3.2, and the first behavior change in Phase 3.
`research_complete` readiness was `session.research.status == "complete"` —
but a round reports complete when ANY dimension completes, so three of four
could have failed and the checklist still said research was done. That is a
false pass on the one gate that exists to say "this can go out the door".
No new endpoint, no new SSE event, no new dep, no project-format bump.

- **`ResearchDimension.required` defaults to `True`, and every shipped
  dimension keeps the default** (so `hyperscale_fire.py` and `generic.py`
  are untouched — the frozen decision is that AHJ requirements, owner/insurer
  standards and site conditions are not more optional than governing codes).
  It is an **issue-readiness** policy, NOT a fan-out failure policy: one
  dimension failing still never cancels the others, a round still succeeds
  when any completes, and the profile still accumulates.
- **Opting out costs a machine-readable reason.** `optional_rationale` is a
  field, not a comment, because registry validation cannot enforce a comment
  — and the rationale is what the readiness warning and the QC manifest
  quote. Validation binds the pair BOTH ways: `required=False` with a blank
  rationale fails startup, and a leftover rationale on a required dimension
  fails too, so a stale reason cannot outlive the opt-out it was written
  for. `required` must be an actual `bool` (`required=0` reads as a
  deliberate opt-out while behaving nothing like one). A fail-open default
  is therefore not silently introducible: flipping the default to `False`
  fails the registry at import with a named error rather than quietly
  passing sections.
- **`research_coverage(module, profile)` is the join** (research/engine.py,
  pure): declared dimensions against the CUMULATIVE profile statuses, giving
  `gaps` (per declared dimension, with `required`/`optional_rationale` and a
  `recorded` flag), `required_gaps`, `optional_gaps`, `missing_required` (a
  required dimension with no status record at all — fail closed) and
  `incomplete_statuses` (including any for a dimension the current module no
  longer declares, which cannot be required and so never block). Cumulative
  is load-bearing: a dimension that completed in round 1 and failed in round
  3 is researched, so a failed rerun must not revoke readiness.
- **`validate_research_facts` fails readiness CLOSED on a self-contradicting
  record.** The deserializer is permissive by design, so a corrupt or
  hand-edited project file can hold two statuses for one dimension. Checks
  are ordered most-specific-first because the message is what a user reads:
  a duplicated status surfaces as more records than distinct ids and says so,
  rather than being reported as a count mismatch that names nothing. Project
  LOADING stays permissive — this judges readiness only.
- **The readiness detail is now six branches** (`app._research_readiness`):
  runner not complete → its status; complete with no profile → evidence
  missing; invalid record → the validation detail; a missing REQUIRED
  dimension → named, with "N of M completed" and "Press Research again to
  retry" (the only action the user can take); only optional gaps → **passes**
  but names each absent area and its declared rationale; all complete →
  the unchanged "Requirements research complete."
- **`research_manifest_facts` moved to research/engine.py** and takes the
  module, since readiness and the QC manifest now share it — it is
  research-domain data, and QC imports research rather than the reverse. It
  gains `declared_dimension_count`, `required_dimension_ids`,
  `incomplete_required_dimension_ids`, `incomplete_optional_dimension_ids`
  and `optional_rationales`, all from the module's CURRENT declaration
  rather than a hard-coded id set. `profile_fingerprint` came with it and is
  byte-compatible with the `_sha256_json` it replaced (pinned), so a
  retained report's research fingerprint is unchanged by the move.
- **The Word memo needed no change**: it CONSUMES the readiness checks from
  the QC state rather than re-deriving them, so the truthful detail reaches
  the export for free. Keep it that way — a second derivation would be free
  to disagree with the checklist.
- **Tests**: 12 new. `test_spec_modules.py` (5): every shipped dimension
  required by default and the default itself, a silent opt-out rejected, a
  stale rationale rejected, a non-bool rejected, and a properly declared
  optional dimension accepted. `test_research_api.py` (7): the false pass
  removed (one of four completing now fails, named, with the count and the
  retry guidance), all-complete reading as complete, a later round restoring
  readiness while a failed rerun does NOT revoke it, a complete runner with
  no profile failing closed, a self-contradicting record failing closed while
  the rest of the surface keeps working, a declared-optional gap passing with
  its rationale quoted, and a legacy no-rounds profile still reading complete.
  Reverting readiness to the status-only test turns 5 red.

## Partial research reads the same in every report — implemented notes

Deep-dive remediation Chunk 3.3, and the chunk that finally consumes what
3.1 and 3.2 recorded. Both report projections now interpret the CAPTURED
research manifest, so a partial profile can no longer render as a reassuring
"Research profile present: Yes" with no limitation beside it. No new
endpoint, no new SSE event, no new dep, no schema bump.

- **The canonical source is `input_manifest.requirements_research`, never
  live research state.** A report is an audit of the run's input snapshot,
  and the session's profile may have gained a round since the run — so
  completing the missing dimension afterwards must not retroactively make an
  exported report claim full coverage. Pinned by a test that completes the
  live profile and re-renders the same payload.
- **One function computes identity AND limitation together**, on each side:
  `docx_export.qc_research_coverage` returns `(identity, limitation)` and
  `qcReport.qcResearchCoverage` returns `{state, identity, limitation}`. They
  are deliberate mirrors — the reason they are one function rather than two
  is that an identity row saying "Yes" beside a limitation saying half of it
  never ran is exactly the half-truth this chunk removes, and two functions
  could drift into saying it.
- **The identity row is three-state**: `No` / `Yes — complete` /
  `Yes — partial (N of M areas completed)`, replacing a bare `Yes`. It reaches
  the Word identity block and `QCReportModal`'s "Requirements research
  present" field, which projects it separately.
- **`M` is the DECLARED dimension count when recorded**, not the number of
  status records. A profile holding two completed statuses for a module that
  declares four has two areas nobody ever ran, and "2 of 2" would hide them.
  Legacy records without `declared_dimension_count` fall back to the recorded
  total.
- **Six branches, and the empty one matters**: no manifest record + present
  flag → "coverage not recorded" (an older schema cannot be read as
  complete); no record + no flag → absent; `present: False` → absent;
  complete → **empty limitation**, because a clean report must not manufacture
  one; partial with ids → names required gaps first (marked REQUIRED for
  issue readiness), then declared-optional ones with their rationale, then
  any remaining failed status; partial with counts only → says the schema did
  not record which areas, rather than guessing.
- **The verdict is about DECLARED coverage, and a retired dimension cannot
  move it** (caught in review on PR #98). A persisted profile can keep a
  failed status for a dimension the current module no longer declares —
  `research_coverage` supports exactly that — and counting it as a gap
  rendered "Yes — partial (4 of 4 areas completed)", a self-contradiction. On
  a 3.2+ record every declared incomplete dimension is in one of the two
  policy lists, so whatever is left in `failed_dimension_ids` is retired: it
  is still DISCLOSED (this report never drops a recorded failure) but does not
  change the verdict or the count. A legacy record has no declared scope to
  filter against, so its failed ids still lead — otherwise a pre-3.2 partial
  report would read clean.
- **Step 7 was a verification, not an implementation.** `docx_export`
  consumes the readiness checks out of `export_current_state` rather than
  re-deriving them, so Chunk 3.2's truthful detail reaches Word for free and
  `ready` is already False when a required area is missing. A test pins both,
  so a future refactor that re-derives readiness in the exporter has to
  disagree with a test. Note booleans render as `Yes`/`No` there, not
  `True`/`False`.
- **Tests**: 24 new. `tests/test_qc_audit_report.py` (12): each of the six
  branches as a unit, the Word report stating partial coverage in BOTH places
  end-to-end through the real writer, captured facts surviving a live-research
  change, and the readiness table carrying the blocked detail.
  Three more per side cover the retired-dimension verdict, a retired record
  riding along beside a real gap without inflating the count, and the legacy
  no-declared-scope fallback.
  `frontend/tests/qcReport.test.ts` (12): the same branches mirrored, the
  limitation reaching `qcReportLimitations` verbatim, unknown manifest fields
  preserved (forward compatibility), and a malformed record degrading instead
  of throwing. The identity assertion was written `"Research profile present:
  Yes\n"` on purpose — label and value share one paragraph, so the colon form
  is what makes it bite (the newline-separated version was vacuous), and
  reverting the row to a bare `Yes` turns two tests red.

## Final QC Review Room — live three-stage contract

The live-research parity follow-up turns Final QC's existing drawer into an
inline command center without adding a route, dependency or environment knob.
The transport remains `GET /api/qc/status` plus `GET /api/qc/stream`; report
identity, finding identity, billing, retries, verification math, cancellation,
run-token isolation and the existing `stream_end` shape remain authoritative.
The new frames describe observable provider/tool work and deterministic local
validation only. Prompts, submitted notes, thinking/token text and hidden
reasoning never cross this channel, and the UI never invents progress to fill
quiet time.

- **The event vocabulary is additive and phase-complete.** Lens workers emit
  `lens_started`, `lens_activity` (only when activity kind changes),
  `lens_search`, `lens_fetch` and `lens_retry`. `lens_started` carries the
  lens id/title and its search/fetch ceilings; search and fetch carry the
  observed query/URL; retry carries attempt, ceiling, observable reason and
  backoff. Existing `lens_complete` / `lens_failed` terminal frames keep their
  prior fields and add the factual `reviewed_check_count`, `candidate_count`,
  `grounded_count`, `search_count`, `fetch_count` and `request_count` telemetry.
  Phase-one futures still interleave, so tests assert per-worker order, never a
  global lens ordering.
- **Verification begins with the whole roster.** `verification_started`
  carries `candidates[]` plus aggregate candidate/seat/worker totals. Each
  roster row has deterministic run-local `candidate_id` (`candidate-1`, …),
  title, original severity, originating `lens_id`, `panel_size` and
  `threshold`; it does not replace the final finding's content-addressed id.
  `verifier_started`, `verifier_activity`, `verifier_search`,
  `verifier_fetch` and `verifier_retry` identify a seat by `candidate_id` plus
  `reviewer_index`. `verifier_complete` exposes seat status and infrastructure
  error, and only for a completed seat its uphold vote, revised severity and
  fix-adequacy result — never the submitted verifier note.
  `candidate_complete` records the fully accounted panel's outcome, size,
  threshold, completed seats and uphold count; `verification_complete` records
  total/completed seats and upheld/refuted/inconclusive candidate totals.
  The legacy `verify_progress` frames remain compatible.
- **Local fix checks are an explicit third phase.** `validation_started`
  declares the number of upheld candidates to inspect;
  `validation_progress` identifies the candidate and real done/total counters,
  then classifies the dry-run result as `safe_fix`, `advisory` or `manual`
  alongside `ops_semantic_status`, `ops_valid` and the observable reason.
  `validation_complete` closes the phase with category totals. A run with zero
  candidates still emits `verification_started`, `verify_progress` `0/0`,
  `verification_complete`, `validation_started` and `validation_complete` in
  order. These empty transitions are immediate truth, not animation dwell.
- **Raw stream relay follows Research's proven shape.** Each lens/verifier call
  iterates the already-open SDK stream before requesting its final message.
  Content-block starts announce observable activity and copy any
  already-complete `block.input`; server-tool JSON deltas
  are buffered narrowly enough to emit live search/fetch inputs at block stop;
  final structured finding/verdict payloads are not copied into activity
  frames. A block stop resolves `streamed or started` — deltas win, the
  copied start input is the code-execution-caller fallback described under
  "Live research visibility" above, and both are dropped at stop. Per-frame
  decoding is defensive, so malformed optional telemetry is
  ignored, while a real iterator/request failure escapes into the unchanged
  retry classifier. Change-only activity suppresses duplicate noise and retry
  resets the worker's activity memory so the next attempt can announce itself.
- **Runner and API truth win over presentation.** Every event remains in the
  append-only, monotonically sequenced runner log and is gated by the current
  run token, preventing late workers from a stopped/superseded attempt from
  contaminating another run. `/api/qc/status` includes the runner's top-level
  `error_kind` (as does latest-attempt evidence) so authentication failure copy
  does not depend on scraping a message. A Stop resolves the visible attempt
  as cancelled; its `qc_failed` frame carries `settling: true` until
  already-paid in-flight work has unwound and `qc_attempt_settled` lands.
  Action/start controls remain locked for that interval. The live board stays
  mounted instead of implying that provider work ended synchronously.
- **`settling` means a STOPPED attempt unwinding — never an ordinary run**
  (deep-dive remediation Chunk 2.2). The predicate is
  `status in _TERMINAL and not _worker_settled`, defined once as
  `QCRunner._is_settling_locked()` and used by both `is_settling` and
  `audit_record_snapshot()["runner"]["settling"]` so the two cannot drift.
  `_worker_settled` alone only says "a worker thread exists" — it is False
  for the whole of every normal run, so reading it directly told the
  double-start 409, the readiness `qc_current` detail, and a run-long
  Review Room banner that a stop had been requested every single time
  anyone ran Final QC. The state table: running → `settling` false;
  terminal + settled → false; **terminal + not settled → true**; that last
  row is the only one. Callers that must block both an active run and a
  genuine settlement keep asking `status == "running" or is_settling` —
  the gates did not change, only the copy they select. Readiness's running
  branch deliberately avoids the word "settled" now, since it is a term of
  art here. The frontend mirrors it with one exported predicate,
  `qcLive.isQcStopSettling` (`status !== "running" && settling === true`),
  used by `foldQcLiveState`, `isQcActiveSnapshot`, and every `QCDrawer`
  label/banner/button/aria-live; `reconcileQcSnapshotUpdate` keeps
  settlement sticky only for a terminal attempt, so a running snapshot
  clears an erroneous prior bit instead of latching the drawer into stop
  language for the session.
- **The client treats the event log as local live state.** `QcEvent` is a
  discriminated union for every legacy and new frame. `lib/qcLive.ts` keeps the
  pure helpers: `mergeQcEvent` appends by `seq`, ignores replay duplicates and
  restarts only for a genuinely new run while terminal frames also update the
  local lifecycle if a refresh fails; `reconcileQcSnapshot` rejects an entire
  same-run response when its event watermark trails the local log (including
  stale report/result/attempt fields), applies the client's explicit refresh
  generation across different runs, and reports whether a response was
  accepted so stale authentication state cannot drive UI side effects; and
  `foldQcLiveState` derives
  phases, five lens cards, candidate panels/seats, retries/tools, validation,
  settling and complete/failed/cancelled/partial outcomes from the append-only
  log. Chatty
  frames merge immediately; full snapshots/report data are refetched only at
  milestones and stream completion. If the follower closes unexpectedly while
  a snapshot is still running or settling, it reconnects and safely dedupes the
  replay.
- **The drawer renders what the fold knows — and nothing more.** A successful
  start auto-expands Final QC once; a later user collapse is respected. While
  work is live, the readiness checklist yields to a compact phase rail and the
  active stage. Specialist cards show queued/running/completed/failed, current
  activity, retries, up to three inert query/source labels and completed work
  totals. Candidate rows are grouped into In review, Waiting and Resolved and
  show two or three numbered seats as queued, active, upheld, not upheld,
  failed or cancelled. **Upheld**, **Refuted** and **Inconclusive** appear only
  after all expected seats are accounted for. Validation rows show real local
  outcomes. Once settled, the live board becomes a concise lens/candidate/
  adjudication/safe-fix recap before the unchanged guided-remediation queue and
  user-opened full-report controls; readiness then returns.
- **Accessibility is part of the state contract.** The warm research-board
  surfaces, breathing `.agent-dot`, shimmer, `.tally-flash` and entry motion
  are reused, with reduced-motion overrides for every animation. Layout keys
  off the drawer container (including the 420px minimum pane), not the browser
  viewport. Symbols always have text labels; transient URLs are inert; one
  aggregate polite live region announces useful progress. Event arrival never
  steals focus or auto-scrolls the specification.
- **Tests pin truthfulness and races, not animation timing.** Engine coverage
  includes change-only activity, search/fetch payloads, retries, malformed
  frames, interleaved workers, candidate rosters, all seat outcomes, shared
  verifier failure, validation and the zero-candidate sequence. Runner/API
  coverage pins full replay, the exact terminal sentinel, stop settlement,
  superseded-run isolation, partial reports, top-level authentication errors
  and absence of late-event leakage. Frontend pure-helper tests cover folding,
  dynamic panels, retries, refuted versus inconclusive, duplicate/out-of-order
  frames, same-run reconciliation, generation-guarded run replacement, settling and
  empty phases. Required verification is the targeted QC Python suites, full
  `npm test`, `npm run build`, then full pytest; visual QA covers narrow/wide
  drawers, keyboard use and reduced motion.

## Final QC cost + speed — implemented notes (v1.8.0)

Reported ask (Abraham): Final QC is expensive and slow, and may be hitting
diminishing returns. A run is **~40 calls** — five lenses plus two or three
verifier seats per finding — and every one carried the full document render
at full input price. Modelled at ~$15.80 and 20-40 minutes; now ~$5.40 and
~3x faster through phase 2. **No review rigor was traded away**: same five
lenses, same seat counts, same grounding, adversarial verification, ops
validation and readiness gate.

- **Caching covered the wrong 5-10% of each request.** `cache_control` sat
  on `system` and `tools` only (2,356 of ~24,000 tokens per lens call; 612
  of ~12,600 per verifier call). The document, standards block and research
  profile rode in the user message as a bare string. The cache is a **strict
  prefix match**, so shared content has to physically lead: `_lens_user_message`
  / `_verifier_user_message` are split into `_lens_shared_prefix` +
  `_lens_request_suffix` and `_verifier_shared_prefix` +
  `_verifier_request_suffix`, and the user turn is now two text blocks
  (`_qc_user_content`) with the breakpoint on block 0. **The order reversed** —
  the `[[QC-LENS:...]]` marker used to lead.
- **Two lineages, not one.** The four web-toolless lenses share a cached
  prefix; every verifier seat shares another. `code_compliance` **cannot join
  the lens lineage** — its `tools` array carries web search/fetch and tools
  render ahead of system and messages, so its byte prefix diverges from the
  start. No reordering fixes that. It still caches across its own retries.
- **1h TTL on the whole verifier request.** Phase 2 spans 10-20 minutes, so a
  5-minute entry would lapse mid-phase and be rewritten. 1h costs 2x to write,
  breaks even after three reads, and a panel run has dozens. **The TTL is
  uniform across every breakpoint in a request** (`_cache_control`), not just
  the document block: the API requires longer-lived cache entries to precede
  shorter-lived ones in prompt order, and the render order is tools → system →
  messages, so default-TTL tools/system followed by a 1h user block is
  **rejected outright** — a nonretryable 400 on every verifier call, which
  trips the shared circuit breaker and settles the run partial with zero
  actionable findings after phase 1 has already been billed. Shipped that way
  briefly on PR #82 and was caught in review; do not "optimise" the small
  blocks back to the default. Pinned by the mixed-TTL assertion in
  `test_qc_requests_cache_the_shared_prefix_across_the_whole_fan_out` — the
  fakes accept any request dict, so nothing else can catch it before a
  provider does.
- **No pre-warm priming (deliberate).** The first `QC_MAX_WORKERS` calls of
  each phase all miss — a cache entry is only readable once the first response
  starts streaming. That costs ~$0.46/run. A serial prime would recover it but
  add ~60s to the phase this change exists to speed up.
- **No messages-tail breakpoint (deliberate).** The interview loop's
  `_with_tail_cache_breakpoint` cannot be ported as-is: QC's pause_turn branch
  re-sends `response.content` **verbatim as SDK block objects**, not the
  serialized dicts `conversation.py` builds, so there is no dict to hang
  `cache_control` on. Marking them would mean changing what gets re-sent —
  a behavioural change to the resume path, not a caching change. See the NOTE
  above `_run_streaming_call`.
- **Effort `xhigh` -> `high`**, and threaded as a run parameter through
  `QCRunner.start` -> `run_final_qc` -> `_run_lens`/`_verify_one` ->
  `_run_streaming_call` and `build_qc_input_manifest`, instead of being
  re-read from module scope at four sites. Thinking bills as output, so
  `xhigh` across ~40 calls compounded the same way it did across research's
  four (PR #78). Pinning it per-run also means the audit record provably
  describes what was sent rather than what the env said at each read.
- **Concurrency 4 -> `settings.QC_MAX_WORKERS` (default 8, env-overridable).**
  Was a bare `_QC_MAX_WORKERS` module constant. **Phase-2-only speedup** —
  phase 1 is five lenses gated by `code_compliance` alone. Opus 5 draws on its
  own rate-limit bucket rather than the Opus 4.x pool, hence 8 not 12.
- **Two silent failure modes, both now covered by tests.** A QC model absent
  from `settings.PRICING` is metered at Sonnet 5 rates via `_rates`'s
  `dict.get` fallback — and the resulting `cost_basis` still passes every
  audit-integrity gate, so the wrong dollar figure ships unnoticed. A model
  absent from `_STRICT_CAPABLE_MODELS` just omits `strict: true`, degrading
  payload conformance with no error. **A new QC model must land in both.**
- **Retained QC results go stale, by design.** `model` and `effort` are inside
  the hashed `input_manifest.configuration`, so every saved `.baspec` result
  flips stale and `POST /api/qc/apply` 409s. That is correct — an Opus/high
  review is not a Fable/xhigh review — and unavoidable anyway, since
  `application_version` is hashed too. What was wrong was the copy: it blamed
  "the document or another review input", which is false when only the config
  moved. Both messages now name the configuration.
- **`tests/fakes.py` was the blocker.** `SequencedFakeClient` routed by
  substring-matching a **string** user message and coerced anything else to
  `""`, so block content would have matched no script and taken the entire QC
  suite down at once. `_user_text()` now flattens text blocks; it is also the
  right helper for tests that were matching against a `str(...)` repr by
  accident.
- **Deferred, on purpose** (revisit with real run data): Sonnet 5 for the
  verifier seats (~18% once on Opus 5 — the seat also sets `ops_adequate`, the
  only semantic gate before an auto-applied edit, and a weaker model biases an
  already kill-biased panel further toward refuting, silently); reducing
  `QC_VERIFIERS_STANDARD` 2 -> 1; and the **threshold inversion** —
  `(size // 2) + 1` means a medium finding needs 2 of 2 (unanimous) while a
  critical needs 2 of 3 (majority), so the extra critical seat buys leniency,
  not rigor. Real bug; changing survival semantics alongside a model swap
  would make regressions impossible to attribute.

## Server-tool caller mode — implemented notes (direct callers)

Deep-dive remediation Chunk 1.1, and the first change of that program. Both
web server tools now declare `allowed_callers: ["direct"]`. No new endpoint,
no new SSE event, no new env knob, no new dep — one key added in two builder
dicts, plus the documentation that key makes true.

- **The provider default was the bug.** Left unset, `web_search_20260209` /
  `web_fetch_20260209` default to the **code-execution caller** ("dynamic
  filtering"), which runs a server-side code-execution container under the
  hood. That default cost three separate things, all observed in production:
  (1) **reliability** — resuming a `pause_turn` that has a pending
  code-execution-called tool use requires the response's provider container
  id on the continuation request; no fan-out sent one, so a paused dimension
  died on a nonretryable 400 (two research dimensions lost in the reviewed
  run); (2) **visibility** — a code-execution caller does not stream
  per-search `input_json_delta`, so the live query/URL labels on the research
  agent board and in the QC Review Room had nothing to render; (3) **ZDR** —
  dynamic filtering is not zero-data-retention-eligible by default, while the
  trust dossier claimed every part of the app is. Direct callers remove all
  three at the source, which is why this lands before the container
  propagation of Chunks 1.2–1.3 — those stay as defense-in-depth for any
  future code-execution-called tool, not as the fix. Chunk 2.1 is the
  visibility half of that same defense-in-depth: all three relays now also
  read a complete tool input off `content_block_start`, so re-enabling
  dynamic filtering would no longer blank the live query/URL labels (it
  would still re-open the container and ZDR halves).
- **One choke point, three channels.** `WEB_TOOL_ALLOWED_CALLERS` in
  `backend/research/schema.py` is the single declaration; both builders spread
  a fresh `list(...)` of it, so a consumer mutating one request's tool list
  can't reach across into another's. Every consumer — the interview's
  `_chat_tools()`, `research/engine._run_dimension`, and QC's `_lens_tools` /
  verifier seats — goes through those builders. **Never hand-roll a web-tool
  dict**: it would silently take the provider default again, and nothing
  except a live 400 would say so.
- **Cache consequence, stated once.** Tools render ahead of system and
  messages, so changing the tool bytes invalidates every previously cached
  prefix. Each lineage (chat session, research dimension, the four
  web-toolless QC lenses, `code_compliance`, the verifier seats) writes one
  fresh entry and then behaves exactly as before. One-time and expected — not
  a regression in the v1.8.0 caching work.
- **`tool_choice` stays absent, for a new reason.** The old comment said the
  API rejects a forcing/parallel-disable `tool_choice` combined with dynamic
  filtering — true, and now moot. Direct callers lift the constraint, but the
  behavior is deliberately unchanged: the system prompt tells the model to
  end its turn with the output tool and the tagged-JSON fallback catches a
  text detour, and that fallback is what makes the loop robust. Adding a
  forcing choice is a behavioral change, not a cleanup.
- **The ZDR claim is now true and is coupled.** `TrustDeepDiveModal`'s
  data-handling table and the Final QC note above both say ZDR eligibility
  depends on the models **and** this caller mode. Re-enabling dynamic
  filtering is an owner decision (token savings vs. ZDR + the container
  obligation) and must re-qualify those claims in the same change.
- **Continuation containers ride along as defense-in-depth (Chunk 1.2).**
  `grounding.response_container_id` reads `container.id` off a response
  (duck-typed, blank when absent); `research/engine._run_dimension` and
  `qc/engine._run_streaming_call` each keep an **attempt-local**
  `container_id`, build a fresh `stream_kwargs = dict(request_kwargs)` per
  request, and add a top-level `container` key only when nonblank. Three
  rules make it correct: (1) the refresh is `response_container_id(r) or
  container_id`, so a continuation that omits the field has not *revoked*
  the container; (2) the reset lives inside the retry loop and outside the
  continuation loop — a retry abandons the conversation, so inheriting the
  failed attempt's container would point a fresh request at a context that
  is no longer its own; (3) `request_kwargs` is never mutated, so the
  cached prefix (tools → system → the shared user block) stays byte-
  identical and the container touches nothing cacheable. It is never
  serialized into messages, history, `QCResult`, `RequirementsProfile`, or
  a project file. With direct callers no container is expected at all —
  this exists so the *next* code-execution-called tool doesn't need an
  incident first.
- **The chat loop closes the third path (Chunk 1.3).** `stream_user_turn`
  keeps the same `container_id` as a plain turn-local, refreshed from
  whichever message the round selected — `get_final_message()` normally,
  `current_message_snapshot` on a user stop, because that is what actually
  arrived. `request_kwargs` takes it as a **parameter** rather than closing
  over it: a closure would bind whatever the variable held whenever the
  request was later built, which Phase 6's move of request construction
  outside the turn-state lock would turn into a real bug. The reset needs
  no bookkeeping at all — a new `stream_user_turn` is a new conversation
  and a fresh `""` — and the scope is the whole TURN, so a continuation
  after a client `tool_result` reuses the id just as a `pause_turn` resume
  does. `_enter_stream`'s thinking.display degrade rebuilds kwargs with
  `{**kwargs, ...}`, so the container survives that retry.
- **Tests.** Exact-dict assertions, not just the one key, because the tool
  bytes lead the cached prefix: `test_research_engine.py`
  (`test_web_tools_declare_direct_callers_on_every_research_request` over all
  four dimensions, plus a builder unit test proving the returned list is a
  copy), `test_qc.py`
  (`test_qc_web_tools_declare_direct_callers_in_both_phases` — the web lens
  and its two verifier seats, and that the other four lenses carry no web
  tools at all), and the chat request assertion in `test_app.py`. The live
  direct-mode canary (Chunk 6.5) is the paid confirmation and is not required
  to land this. Chunk 1.2 adds the container round-trip in
  `test_research_engine.py` and `test_qc_live_events.py`: one scripted
  dimension/lens covers pause-with-container → pause-without (retained) →
  retryable failure → clean retry (dropped), plus a no-container path that
  must stay untouched. `tests/fakes.py` gained an optional `container=` on
  `research_response` / `pause_response` / `qc_findings_response` /
  `qc_verdict_response` / `raw_turn` (absent → the attribute is not set, so
  every existing fixture is byte-identical), and `SequencedFakeClient` now
  **snapshots** each captured request's `messages` list — the engines append
  to one list across continuations, so capturing by reference made every
  request in an attempt show that attempt's final conversation, which would
  have made "this continuation re-sent exactly the paused content" quietly
  assert something else.

## Per-TTL cache-write pricing — implemented notes

Deep-dive remediation Chunk 4.1, and the accounting that has to exist
BEFORE Chunk 4.2 puts the interview on one-hour cache entries. v1.8.0 gave
Final QC's verifier seats a one-hour TTL and the ledger kept pricing every
cache write at the five-minute rate, so the most expensive phase of the
most expensive feature was billed at 1.25× input where the provider
charges 2×. No new endpoint, no new SSE event, no new dep, no schema bump.

- **The subtotal is INSIDE the total, and that is the whole design.** The
  provider reports `cache_creation_input_tokens` as the total across TTL
  classes and nests the one-hour count at
  `usage.cache_creation.ephemeral_1h_input_tokens`. Two rates over one
  overlapping pair of counters is a bug in either direction: adding the
  subtotal on top double-bills it, ignoring it under-bills it. Everything
  here computes **disjoint slices** — `five_minute = total − one_hour` —
  and every surface that prices cache creation goes through one of the two
  splitters (`usage_ledger.cache_write_split` live,
  `qc.engine._cache_write_tokens_by_ttl` persisted). A new pricing site
  that reaches for `cache_creation_input_tokens * cache_write` directly is
  the regression to watch for.
- **`PRICING` carries both rates per model** — `cache_write` at 1.25×
  input, `cache_write_1h` at 2.0× (VERIFIED 2026-07). The 1h entry costs
  more to create because it lives longer; it breaks even against the 5m
  entry after ~3 reads instead of 2. `test_every_priced_model_configures_
  both_cache_write_rates` pins the multipliers for every model, because a
  model added with only the 5m rate would silently underprice every run on
  it and nothing else in the app would notice — the same failure mode the
  existing "absent from PRICING falls back to Sonnet 5" note warns about.
- **Two readers, because chat aggregates its own usage.** `usage_to_dict`
  covers research/audit/QC; `conversation._merge_usage` is a separate
  accumulator over continuation rounds and needed the nested read too
  (kept as a local `getattr` walk — the module's fakes are
  `SimpleNamespace`, and importing a QC-private helper was explicitly out).
  Research's `DimensionStatus` has explicit usage fields and simply ignores
  the new key; that is correct, since research writes no 1h entries.
- **Live values are clamped; persisted values are not.** A malformed
  provider subtotal is clamped into `[0, total]` so an estimate can skew
  but never invert into a negative charge. A persisted audit record gets
  the opposite treatment: `_cache_write_subtotal_possible` makes a subtotal
  larger than its own total **fail** `_audit_accounting_consistent`, per
  record and in aggregate. A report claims its own arithmetic, so quietly
  repairing it into something plausible would forge the claim — the same
  posture the rest of the QC deserializer already takes.
- **A cost basis is an immutable claim, so BOTH shapes are read and echoed
  back verbatim.** `_persisted_cost_basis` accepts the legacy nine-key /
  four-rate shape and the new ten-key / five-rate one, and preserves
  whichever it read rather than upgrading it — minting a `cache_write_1h`
  onto an old report would assert a rate that run never used. Accepting two
  shapes is not accepting any shape: an unknown rate key or an unknown
  top-level field is still refused, pinned by
  `test_a_cost_basis_with_an_unknown_rate_key_is_still_refused`.
  `_estimated_cost_from_basis` uses `rates.get("cache_write_1h",
  rates["cache_write"])`, so a legacy basis prices the whole total at the
  five-minute rate and reproduces its saved estimate exactly.
- **The outer field set and the rate map are validated as a PAIR**
  (`_COST_BASIS_SHAPES`), never independently — caught in review on PR #99.
  Checking them separately accepts two hybrids, and one of them is exactly
  the forged claim the immutability rule exists to prevent: a basis that
  keeps `cache_write_treatment` (prose promising per-TTL pricing) while
  dropping `cache_write_1h` prices a million one-hour tokens at $6.25
  instead of $10.00 while its own saved text says otherwise, and validates
  clean. The mirror hybrid is a current five-rate report missing the
  required explanation. `cache_write_treatment` and `cache_write_1h` ship
  together or not at all, so pairing rejects only corruption —
  `usage_pricing_snapshot` always emits both, and a pre-4.1 report has
  neither.
- **`QCResult.to_dict()` shallow-copies `cost_basis`**, so a test that
  mutates `payload["cost_basis"]["rates_per_token"]` in place edits the
  LIVE result and silently reshapes every later case built from the same
  fixture. That is why every case in these tests starts from one
  `copy.deepcopy(baseline)` — the convention the rest of the file already
  followed, and the reason the first draft of the pairing test passed its
  second assertion for the wrong reason.
- **`cache_write_treatment` is a sibling of `thinking_token_treatment`**,
  and exists for the same reason: both explain a non-obvious "this is
  already inside that number, don't charge it twice" decision. It reaches
  the Word memo and the report modal for free — both render `cost_basis`
  generically (the exporter iterates its items, the modal prints a JSON
  block), so no renderer changed.
- **The Settings usage table is unchanged and still correct**: it shows the
  cache-creation TOTAL, which the subtotal is part of. Only the dollar
  figure moved. Breaking the split out in the UI is deliberately left to
  Chunk 4.4's disclosure work rather than bolted on here.
- **Retained QC results do NOT go stale from this change.** `cost_basis`
  is not part of the hashed `input_manifest` (`model`/`effort` are), so a
  saved report keeps its identity and stays actionable — it simply carries
  the basis it was priced under. That is the intended asymmetry: the rate
  table is documentation of how a number was reached, not an input the
  review's conclusions depend on.
- **Tests**: 11 in `test_usage.py` (the golden per-model rate matrix, pure
  5m, pure 1h at 2× input, mixed-not-double-counted, zero, clamped
  malformed subtotal, ledger accrual, nested read + absent-key identity,
  malformed nested object, the snapshot's rate + wording, and the
  subtotal's trip through a real `/api/chat` turn) and 5 in
  `test_qc_audit_report.py` (a verifier seat's subtotal captured/priced/
  round-tripped, legacy basis loads and reproduces its estimate, legacy
  basis + a subtotal prices conservatively, impossible subtotal rejected
  per-record and in aggregate, unknown keys still refused). `token_usage`
  and the research/QC `usage` fake gain an optional `cache_write_1h=`; it
  attaches the nested object only when supplied, so every pre-existing
  fixture stays byte-identical. Each mechanism was reverted in place to
  prove it load-bearing (the guard → 1 red, the split formula → 1 red).

## Rolling chat cache breakpoint — implemented notes

Deep-dive remediation Chunk 4.2, and the chunk 4.1 existed to make
priceable. The interview had a cache breakpoint on the request tail and a
docstring claiming history "caches incrementally" — it did not. Every turn
re-billed the entire conversation as fresh input. No new endpoint, no new
SSE event, no new dep, one new env knob.

- **A tail breakpoint cannot cache across turns, and the reason is
  strip-at-commit.** The entry it writes is keyed on a prefix that ENDS
  with that turn's PROJECT CONTEXT block, and commit replaces that block
  with the user's bare text. The next turn's prefix therefore diverges at
  exactly the point the entry was cut, and a prefix match that fails at
  byte N caches nothing after byte N — which here is everything. The tail
  breakpoint was never useless (it caches *continuation rounds within* a
  turn, where nothing has been stripped yet); it just could not do the job
  the docstring claimed.
- **The fix is a breakpoint on the committed-history boundary** — the last
  block of the last message in `session.history`, i.e. the last byte of
  the stripped form every later turn re-sends. That form is stable, so
  turn N's entry is a genuine byte-prefix of turn N+1's request: N+1 reads
  the whole prefix and writes only the newest exchange. Pinned directly by
  `test_a_turns_cached_prefix_is_a_byte_prefix_of_the_next_request`, which
  asserts the cache-read condition itself rather than a proxy for it.
- **The boundary is passed in, never inferred from roles.** Roles alternate
  and a continuation round appends more user-role messages, so any
  role-walking heuristic would land in the wrong place on a tool turn.
  `request_kwargs` snapshots `history = list(session.history)` and hands
  `_committed_history_boundary(len(history), len(raw), len(sanitized))`
  to the builder.
- **Index arithmetic is CHECKED, not trusted.** Sanitization replaces
  messages positionally and never adds or drops one (an emptied assistant
  message becomes a placeholder rather than disappearing — pinned by
  `test_sanitizing_a_request_never_adds_or_drops_a_message`), so the
  boundary is `len(history) - 1`. If a future sanitizer ever changes the
  count, the helper returns `-1` and the request falls back to the tail
  alone: one missed cache read, rather than a breakpoint silently
  annotating the wrong message and splitting the prefix in the wrong place.
- **The request is NON-INCREASING in TTL, not uniform — and the tail is the
  one that differs.** The boundary is read by the next user turn, so it
  takes `settings.CHAT_CACHE_TTL`. The tail is keyed on the fresh PROJECT
  CONTEXT, which commit strips, so no later turn can ever read it — its
  only readers are this turn's continuation rounds, seconds apart. Buying
  it an hour costs 2.0× input to write against 1.25×, on a block the size
  of the whole document, every turn (~$0.02–0.11/turn). It therefore takes
  `settings.CHAT_TAIL_CACHE_TTL`, pinned to the SHORTEST supported TTL.
  Caught in review on PR #100; the original plan froze "uniform" on the
  belief that any mixed TTL trips the provider, but the constraint is only
  SHORT-before-LONG.
- **The tail TTL is deliberately NOT env-overridable**, and that is what
  preserves the safety property uniformity used to give. Because the tail
  is the LAST breakpoint and pinned shortest, it can never precede a
  longer-lived one — so no setting can build the out-of-order request that
  400s (the exact failure PR #82's review caught in the QC fan-out). A knob
  here would hand that footgun straight back. Pinned by
  `test_no_setting_can_build_an_out_of_order_request`, which sweeps every
  supported setting; the fakes accept any request dict, so nothing else
  would catch it before a provider did. Note `SUPPORTED_CACHE_TTLS` is
  ordered shortest-first and that order is load-bearing
  (`settings._cache_ttl_rank`) — a new TTL is an insertion, not an append.
- **A merged breakpoint takes the LONGER TTL.** When the boundary resolves
  to the same message as the tail, one annotation is written: it is doing
  the boundary's cross-turn job, and under-living it would throw away the
  read it exists for. The tail is registered first in `ttl_by_index` so the
  boundary overwrites it.
- **`BUILD_A_SPEC_CHAT_CACHE_TTL` defaults to `1h`** because an interview
  turn is a person reading a drafted provision and typing a reply, which
  routinely outlives five minutes — and a lapsed entry is re-WRITTEN at
  full price, not read at 0.1×. A 1h write costs 2.0× input against 1.25×
  for 5m (Chunk 4.1 prices both), so it breaks even after ~3 reads instead
  of ~2. `settings._cache_ttl_env` validates against
  `SUPPORTED_CACHE_TTLS` and falls back to the default with a WARNING: an
  unsupported TTL is a 400 on every request, so passing one through would
  take chat down, and degrading silently would leave an operator believing
  their override took effect.
- **Three breakpoints, no tool breakpoint.** System (which also closes the
  tools prefix, since tools render first) + boundary + tail = 3, inside
  the provider's limit of 4. A separate tool breakpoint would spend the
  remaining slot on bytes the system breakpoint already covers.
- **Residual limitation, deliberately not worked around**: a breakpoint
  looks back at most 20 content blocks for a prior entry, so a single turn
  appending more than 20 blocks (a long tool-heavy round) can push the
  previous entry out of the window. That turn re-writes; the next caches
  normally again. Interior breakpoints would spend the request's remaining
  budget on a rare case.
- **Tests**: 9 new in `test_app.py` (the rolling layout across three turns
  with exact marked-message indexes and last-block-only placement, the
  byte-prefix cache-read condition, the long/short TTL split, the
  non-increasing-order sweep over every supported setting, continuation
  rounds, nothing surviving into history or a saved project, the TTL
  setting's validation and loud fallback, the boundary helper's fail-safe,
  and the sanitizer count invariant). Existing exact-dict assertions moved
  from bare `ephemeral` to the explicit TTL across `test_app.py`,
  `test_runtime_date.py` and `test_session_modules.py`. Every mechanism was
  reverted in place to prove it load-bearing: tail-only → 2 red, uniform
  1h tail under a 5m setting → 1 red on the ordering sweep.

## A failed research round is still a paid round — implemented notes

Deep-dive remediation Chunk 4.3. The runner metered research only on the
success path, so a round where EVERY dimension failed — or one the user
stopped — spent real money and told the session nothing. The data was
already there and simply had nowhere to go: `ResearchFanoutError` carried
only a message. No new endpoint, no new SSE event, no new dep, no
project-format bump.

- **The spend was already recorded; only the carrier was missing.**
  `_run_dimension`'s `_failed` has always summed the attempt's billed
  responses into its `DimensionStatus`, retries included (a retryable
  death abandons its attempt's conversation but not its bill). What broke
  the chain is that the all-dimensions-failed path raises instead of
  returning a profile, and the exception had no room for usage. It now
  takes `usage_totals`, mirroring `QCFanoutError` — which has carried
  exactly this since Batch 4, and whose runner branch is the shape copied
  here.
- **One aggregation helper, because two would drift.**
  `dimension_usage_total(statuses)` is the single definition;
  `RequirementsProfile.usage_total()` delegates to it and the raise site
  calls it directly. The failing path is the one nobody watches, so a
  duplicated key tuple would let a newly-recorded usage field reach the
  meter on success and quietly not on failure.
  `test_every_recorded_usage_field_reaches_the_meter` compares
  `_DIMENSION_USAGE_KEYS` against the dataclass's own `*_tokens`/
  `*_requests` fields, so adding a field without wiring it is a red test
  rather than a silent undercount. `cache_creation_1h_input_tokens` is
  deliberately absent: research writes no one-hour entries, so Chunk 4.1's
  per-TTL split has nothing to separate here.
- **The meter runs BEFORE the compare-and-set, and unconditionally — that
  ordering is the whole point on a stop.** `stop()` resolves the run
  synchronously so the UI never waits on the background thread, which
  means that by the time the worker's `ResearchFanoutError` surfaces, the
  failure branch's `_try_resolve` has already LOST. Metering inside that
  `if` would therefore drop exactly the spend a user who just cancelled is
  most likely to ask about. Pinned by
  `test_a_stopped_research_round_still_meters_what_it_already_spent`,
  which is the only test that goes red when the meter is moved inside the
  CAS. The app's `add_usage_if_current` generation guard remains the thing
  that decides whether this session still owns the charge — the runner
  does not second-guess it.
- **A failed round bills itself once and never re-bills an earlier one.**
  A failed round is never adopted (`_try_resolve` takes no `adopt` on that
  path), so it cannot enter the cumulative profile a later round re-reads.
  Success → total failure → success bills 100 · 28 · 40 while the profile's
  cumulative total stays 140, not 168.
- **Empty stays empty.** A module declaring no research dimensions raises
  before any request, so `usage_totals` is `{}` and the runner's
  `if ... and exc.usage_totals` guard makes it a no-op — a zero-valued
  ledger entry would be a fake turn in the meter. Zero-valued keys are
  likewise omitted per dimension.
- **Deliberately unchanged**: the generic `except Exception` branch does
  not meter (an unexpected exception carries no usage to report), and
  research stop stays lossy — this bills the discarded work, it does not
  retain it.
- **It makes an already-shipped trust claim true.** `TrustDeepDiveModal`'s
  Money section says "Money spent on work that failed or was stopped is
  still real, and the meter records it rather than quietly writing it
  off." For research that held only when some dimension still completed;
  the common stop did not. No frontend change was needed — the claim was
  already the one the code should have been keeping, which is the sense in
  which the dossier is a contract and not a brochure.
- **Tests**: 9 new. `test_research_engine.py` (5 — four failed dimensions
  with distinct usage asserted as an exact dict, a retried attempt counted
  exactly once, the nothing-billed no-op, the key-tuple guard, and both
  callers proven to be one computation); `test_research_rounds.py` (2 — a
  totally failed round metered, and the success/failure/success sequence
  above); `test_stop.py` (1 — the stop ordering, using a barrier client
  that holds all four dimensions at their first call so the stop lands
  after each has banked a billed response); `test_usage.py` (1 — the spend
  reaching `/api/usage`'s research category end to end). Both mechanisms
  were reverted in place to prove them load-bearing: dropping
  `usage_totals` from the raise → 6 red; gating the meter on the CAS → 1
  red, the stop test.

## Disclosed stopped-turn output estimate — implemented notes

Deep-dive remediation Chunk 4.4, and the last of Phase 4. Stopping a turn
closes the request immediately — the whole point, since draining it would
keep paying for tokens the UI stopped showing — but the provider's
authoritative output count rides the closing `message_delta`, which is
exactly what a closed stream never receives. `current_message_snapshot`
therefore reports whatever `message_start` announced: a placeholder of a
few tokens, no matter how much text arrived. A user who stopped a long
reply was billed for it and told almost nothing. No new endpoint, no new
SSE event type, no new dep, no project-format bump.

- **Provider counts and estimates never share a field** (frozen decision 8,
  and the reason this chunk is shaped the way it is). `output_tokens`
  always holds exactly what the provider reported — it is the one number
  reconcilable against an invoice, so a heuristic mixed into it would
  corrupt precisely the field an auditor trusts. The shortfall lands in
  `estimated_output_tokens` beside it, with `usage_estimated: true` on the
  record. The two are DISJOINT, so a consumer wanting the best available
  total adds them and one wanting provider truth reads `output_tokens`
  alone, unaffected by this feature existing. Pinned by six tests;
  blending them turns those six red.
- **The estimate can only ever ADD.** `estimated_output_shortfall` is
  `max(0, estimate - reported)`, so when the provider's number already
  exceeds the heuristic — a short reply, or a stop that still caught the
  final delta — it contributes nothing and the record stays purely
  provider-reported. It never revises the provider's figure downward.
- **Only model-authored blocks are counted.** Text, thinking summaries, and
  tool/server-tool `name` + JSON-serialized `input` — because those were
  billed as output, and a stop most often lands mid tool-input JSON. Tool
  RESULTS (client, web-search, web-fetch) are excluded: they are provider-
  or app-supplied and bill as INPUT on the following request, so counting
  them would inflate a stopped turn by whole retrieved pages. A thinking
  block's `signature` is excluded too — an opaque attestation whose length
  tracks nothing the user paid for.
- **`ceil(chars / 4)`, deliberately coarse.** The SDK exposes no running
  output count and a real tokenizer is neither available offline nor worth
  a dependency for a number every surface labels an estimate. The tests
  assert MONOTONICITY and a floor, never a tokenizer-exact figure — pinning
  an exact count would make them brittle for no gain.
- **`bool` is a subclass of `int`, and that was a live trap.** The turn's
  usage record carries `usage_estimated: True` and is handed straight to
  `UsageLedger.add`, whose filter was `isinstance(v, (int, float)) and v` —
  so the flag would have accumulated as a token count (1, then 2, then 3)
  and rendered in the usage table as if it were one. `add()` now rejects
  bools explicitly (the precedent already existed in `load_snapshot`), and
  the ledger's own disclosure is DERIVED —
  `snapshot()["includes_estimated_output"]` reads the counter — so the flag
  and the number it describes cannot disagree. Removing the guard turns 2
  tests red.
- **`usage_pricing_snapshot` is deliberately untouched.** It is consumed as
  Final QC's persisted `cost_basis`, whose validator asserts exact set
  equality over BOTH the top-level keys and the rate map (Chunk 4.1's
  `_COST_BASIS_SHAPES`). Adding a field would break every new audit
  record's validation — to describe something a QC run cannot produce, since
  its fan-out always reads a final message. The disclosure lives on the
  session-meter surfaces and in the module docstring instead.
- **The context gauge gets a DIFFERENT estimate from the bill, and that
  distinction is load-bearing** (caught in review on PR #102). The gauge
  pairs the last request's prompt with the reply about to be committed, so
  omitting a stopped turn's output would undercount the conversation the
  NEXT turn re-sends — but counting the BILLING figure overstates it just
  as badly. A stopped turn discards two whole categories on the way to
  commit: `thinking` blocks are stripped by `_committed_messages`, and
  unexecuted `tool_use` blocks are dropped by the truncation branch. Both
  were billed (so they belong in spend) and neither is ever re-sent (so
  they must not reach the gauge). `estimated_retained_output` counts text
  blocks only; `estimated_output_shortfall` counts everything authored.
  The first draft used one figure for both, which would have had the pill
  promising thousands of tokens the next turn never carries — precisely
  undoing the subtraction `_retained_output_tokens` performs for exactly
  this reason. Server-tool blocks are also left out of the gauge: a stop
  usually catches one unpaired and `_without_unpaired_server_tool_uses`
  scrubs it, and a surviving one carries only a short query — so the gauge
  understates by a few tokens rather than overstating by a reasoning block.
- **Two senses of "estimate" now meet in the ledger** and the docstring
  separates them: the DOLLAR figure has always been an estimate (list
  prices, not an invoice) computed from exact TOKEN COUNTS; a stopped turn
  is the one case where a token count itself is not exact.
- **Surfaces**: the Settings usage table shows `+N` in faint text beside the
  affected row's reported output (separate, never summed into it) plus a
  disclosure paragraph in the `cache_saved_usd` idiom; the header pill's
  tooltip gains the same disclosure (the pill has no room, and its `≈`
  already reads as an estimate). `includes_estimated_output` on the
  snapshot is what both gate on.
- **Fakes**: `raw_turn` gained `usage=` and `snapshot_usage=`, and
  `_FakeStreamCtx.current_message_snapshot` prefers the latter — modelling
  the one way a snapshot genuinely differs from a final message. Both
  attach ONLY when supplied (the `container` convention), because
  `SequencedFakeClient` routes on `hasattr(turn, "usage")` to pick its
  stream context and an unconditional `usage=None` would silently change
  which context a `raw_turn` got if one were ever scripted through it.
- **Tests**: 13 new. `test_stop.py` (7 — the headline long-stop case, a
  provider count larger than the heuristic adding nothing, thinking +
  tool-input counted in the BILL, the gauge excluding those same blocks,
  monotonicity, a normal turn carrying no estimate at
  all, and the context gauge); `test_usage.py` (4 — priced at the output
  rate, the derived flag both ways, the bool trap, and the whole thing
  through `/api/usage`); `test_diagnostics.py` (2 — the `round_end` trace
  event disclosing it, and a normal round claiming no estimate; the
  round_end records live there, not in `test_tracing.py`). Every mechanism
  reverted in place to prove it load-bearing: blending the estimate into
  `output_tokens` → 6 red; dropping the bool guard → 2 red; feeding the
  gauge the billing figure → 1 red.

## Final QC v4 panel outcomes — implemented notes

Deep-dive remediation Chunk 5.1, and the first change in the program that
deliberately alters which findings survive. `QC_REPORT_SCHEMA_VERSION` is
now **4** and `QC_PROTOCOL_VERSION` **`final-qc/4`**. No new endpoint, no
new dep; one new SSE payload field and one new persisted collection.

- **The v3 rule inverted with panel size, and that was the bug.** Survival
  was `upholds >= (size // 2) + 1` — "majority, ties to the refuters". On a
  2-seat panel that is 2 of 2 (unanimous); on a 3-seat panel it is 2 of 3
  (a majority). So the extra seat a critical/high finding gets bought it
  **leniency**, not scrutiny, which is exactly backwards. Two simpler
  fixes were considered and rejected during adjudication: "upholds >
  refutes, ties refute" is algebraically the shipped formula and fixes
  nothing, and "critical/high must be unanimous else refuted" makes a
  2-of-3 upheld life-safety finding vanish silently — false negatives
  maximised on the class where they cost most.
- **v4 makes disagreement a first-class outcome.** `panel_outcome()` is the
  single decision point: `upholds == size` → **upheld**; `refutes >
  upholds` → **refuted**; anything else → **disputed**. That yields the
  adjudicated table exactly (2 seats: 2-0/1-1/0-2 =
  upheld/disputed/refuted; 3 seats: 3-0/2-1/1-2/0-3 =
  upheld/disputed/refuted/refuted). Disputed blocks audit completeness
  like an open critical, is never auto-applied, and needs a human
  disposition.
- **"Like an open critical" is literal, and getting it wrong deadlocked
  the feature** (caught in review on PR #103). The first implementation
  made a dispute fail `verification_complete()`, i.e. `is_complete()` —
  but `is_complete()` gates the dismiss endpoint AND whether the runner
  retains the result at all, so the dispute could never be dismissed, and
  the drawer copy telling users to dismiss it described an impossible
  workflow. `verification_complete()` is now a purely STRUCTURAL question
  (did every panel complete, does every recorded outcome match its seats —
  `disputed` passes), and the blocking lives in `open_disputed_count()`, a
  separate readiness term exactly parallel to `open_critical_count()`.
  `QCResult.finding()` was widened to survivors + disputed so a dismissal
  can reach one; both apply paths re-check `ops_semantic_status`/
  `ops_valid` immediately after that lookup, which a disputed candidate
  fails by construction, so widening it cannot make one applicable.
  Refuted and inconclusive stay unreachable — they are audit records, not
  an action queue.
- **`dismissed_ids` spans both dismissable collections.** `QCRunner.dismiss`
  records whatever it dismissed, so the run's `dismissed_ids` and the
  reload reconciliation must both count survivors AND disputed. Computing
  the expected set from survivors alone made a dismissed dispute fail
  `from_dict`'s consistency check on the next project load — which returns
  `None`, silently discarding an entire paid report. Dismiss memory also
  carries to a re-generated dispute, for the same reason it carries to a
  survivor: a content-addressed id means it is the same disagreement the
  user already set aside.
- **`disputed` and `inconclusive` are different things and must stay
  different.** Disputed = a COMPLETE panel that disagreed, which is
  substantive information. Inconclusive = infrastructure failure, which is
  no information at all. Both need a human; conflating them would tell a
  reviewer a provider timeout was a professional disagreement. Separate
  collections, separate report appendices, separate drawer groups,
  separate copy.
- **The evidence rule is severity-gated, and activity is not evidence.**
  A critical/high refutation only counts as refuted when at least one
  completed refuting seat carries a citation that VALIDATED — a source
  whose normalized URL matches something that seat actually retrieved, or
  a `document_ref` that resolves against the reviewed snapshot. This
  encodes the RF-001 lesson (three seats refuted a life-safety-adjacent
  finding having run zero searches) and closes the adjacent loophole where
  one token search would have laundered the same refutation:
  `search_queries`/`retrieved_sources` are records of what a seat DID and
  can never satisfy the gate alone. Medium/low refutations are not gated —
  the gate exists for the findings whose false-negative cost is highest,
  not as a tax on every refutation. An UPHOLDING seat's citation never
  opens it either.
- **Failed citations are retained and marked, never dropped.** That a seat
  tried to justify its refutation and cited something unverifiable is
  precisely what a human reviewing a disputed candidate needs to see.
  `QCRefutationEvidence` carries `validated` plus the reason it failed.
- **The rule identity is persisted, not an integer.** An integer threshold
  cannot express this scheme — the same "2" means unanimous on a 2-seat
  panel and a dispute on a 3-seat one. Every v4 finding carries
  `verification_rule = VERIFICATION_RULE_V4`; `verification_threshold`
  stays (now equal to the panel size) so a record remains self-describing
  next to v3 ones, and the reload check keys off the rule.
- **v3 is re-checked under v3's rule, never re-adjudicated under v4.**
  `_structural_verification_outcome` branches on schema version: v4 calls
  `panel_outcome` (so a reloaded report either agrees with itself or fails
  the check), schema 3 validates its recorded strict-majority threshold,
  and pre-v3 legacy keeps its own laxer path (no threshold recorded, and
  all-zero reviewer indexes tolerated). Reinterpreting a v3 2-of-3 uphold
  with v4 rules would rewrite a decision nobody re-made on evidence nobody
  re-examined.
- **The v3→v4 boundary breaks finding ids and dismiss memory, by
  construction.** `finding_id` hashes `verification_outcome` and the panel
  projection, so a candidate whose outcome moved from `refuted`/`upheld`
  to `disputed` mints a new id and its remembered dismissal does not carry
  across. That is expected protocol-change behavior, not a bug — **and it
  has to be stated in the release notes of whatever version ships this.**
  It is not in them yet: the version is not bumped in this chunk, and a
  notes entry for a version that does not exist would fail the
  version-consistency gate. The release chunk owns it.
- **Surfaces**: the roster event carries `uphold_requires` + `rule` +
  `evidence_gated` instead of a bare `threshold`; `candidate_complete`
  carries `outcome` + `dispute_reason`; `verification_complete` and the
  runner snapshots count disputed separately; readiness gets its own
  branch (re-running a dispute re-litigates it rather than resolving it,
  so the copy says review-and-disposition, never "re-run"); the Word memo
  gains **Appendix A1: Disputed Candidate Register**; `QCReportModal`
  gains section 07b; `QCDrawer` gains a warn-toned disputed group.
- **Tests**: 23 new. `test_qc.py` (19 — every row of the outcome table on
  both panel sizes, the RF-001 shape, activity-is-not-evidence, an
  unretrieved citation, a resolving and a non-resolving `document_ref`,
  medium not gated, an upholder's evidence not counting, failed seats
  staying inconclusive, disputed blocking readiness while staying
  structurally complete and unapplicable, the dismissal round trip end to
  end, a dismissed dispute surviving save/reload, the persisted rule
  identity, and a reload that re-adjudicates). `test_qc_audit_report.py`
  (3 — the Word appendix, a v3 2-of-3 keeping its original outcome, and v3
  being readable but not current audit grade). Frontend:
  `qcLive.test.ts` (4) and `qcReport.test.ts` (4, incl. the report-wide
  aggregation totals summing). Backend **1292 passed, 9 skipped**;
  `npm test` **162**; `npm run build` clean. The completeness/readiness
  split was reverted in place to prove it load-bearing → 4 red.

## Cross-lens candidate consolidation — implemented notes

Deep-dive remediation Chunk 5.2, built on 5.1's v4 schema. Five lenses
reviewing one document routinely raise the SAME defect in different words,
and every variant used to buy its own verifier panel — so cost scaled with
lens overlap rather than with unique actionable issues. A grouping step
between phase 1 and the roster gives one defect one panel. No new endpoint,
no new dep, no schema bump (5.1's `final-qc/4` already covers it).

- **The safety posture is the design, not a caveat on it.** Every original
  claim survives verbatim as an immutable audit record; grouping is gated on
  HARD structural compatibility computed before any model sees the
  candidates; and every failure path — request, parse, coverage, validation,
  an oversized bucket, the feature switched off — lands on deterministic
  singletons, which is exactly the pre-5.2 behaviour. Consolidation can cost
  money. It can never lose a finding.
- **Bucketing runs first, and that ordering is the whole guarantee.**
  Element-anchored candidates bucket by their resolved anchor (an
  overlapping write scope is the minimum for "one fix disposes of both").
  Section-level candidates have no anchor to overlap, so they need the extra
  deterministic gate: two are eligible only when they share a normalized
  cited-or-accepted source, taken as connected components so bucket order
  never depends on which lens finished first. A model can only ever group
  WITHIN a bucket, so two findings on different editable elements can never
  be merged however alike their titles read.
- **Grouping is by actionable defect, never by identical operations.** The
  reviewed run's clearest duplicate pair (SF-009/SF-023: same parent
  element, same missing access requirement, different inserted wording)
  proposed non-identical ops; an identical-ops eligibility gate would have
  kept them separate and re-created the duplicate provision after apply.
  Ops are reconciled AFTER adjudication, never used as an eligibility gate.
- **`submit_qc_consolidation`** (`qc/schema.py`) is the third strict tool,
  beside `submit_qc_findings`/`submit_qc_verdict`. One call per eligible
  bucket, on the shared `_run_streaming_call` machinery with
  `event_prefix="consolidation"`. Indexes are LOCAL to the bucket, so a
  hallucinated index fails the coverage check rather than silently
  addressing another bucket's candidate. `_validate_consolidation_groups`
  is strict by design — every index exactly once, no unknown or duplicate
  index, a merge must state the shared defect — because a partition we had
  to repair is one we cannot claim accounts for every original, and the
  cost of refusing it is only the panels that bucket would have bought
  anyway.
- **Everything except the prose is derived deterministically.** Severity is
  the MAXIMUM original (so a critical merged with a medium faces the
  critical panel, never the smaller one); the anchor, the source union, the
  grounding flag and the source checks all come from the members. The
  grouping call can restate a defect; it can never quietly escalate its
  severity, re-anchor it, or claim evidence no member had. **A
  single-member group keeps its original claim VERBATIM** — the merge path
  is unreachable for a singleton, because nothing downstream could catch
  the call quietly rewriting one lens's finding.
- **Operation reconciliation, and the one reading that is not literal.**
  Members' op sets identical after canonical JSON → used unchanged
  (`identical`); differing → the call may synthesize one reconciled set the
  panel must approve like any other fix (`reconciled`); nothing reconciled
  → the finding survives ADVISORY-ONLY with `proposed_ops == []`
  (`unreconciled`), and the alternatives stay readable on the origins so a
  human picks. Never more than one member's operations for one defect. The
  deliberate non-literal reading: "identical" compares the NON-EMPTY sets,
  because a member that proposed nothing has not proposed a *different*
  fix, and treating `{[X], []}` as a conflict would make the common shape
  (one lens proposes, another declines) advisory — worse than
  pre-consolidation, where that lens's fix was applicable.
  `_reconciled_ops_in_scope` additionally refuses a reconciliation
  targeting anything outside the members' own targets plus the anchor's
  ancestors: the call's job is to GROUP, and the dry-run would happily
  validate an unreviewed edit to an unrelated element.
- **`candidate_origins` holds stable REFERENCES, not copies.** The full
  `QCCandidateOrigin` records live exactly once, in
  `QCConsolidation.origins`; a finding carries content-addressed ids.
  That makes "no original candidate disappears" a checkable PARTITION
  invariant — `_consolidation_record_consistent` requires every origin in
  exactly one group, every group producing exactly one candidate, and every
  reference resolving — rather than two copies free to drift.
  `QCResult.origins_for` / `qcReport.qcCandidateOrigins` /
  `docx_export.qc_origins_for` are the one join, and all three projections
  go through it.
- **`origin_id` is content-addressed over the claim, never its ordinal**
  (`_mint_origin_id`, `qco-` prefix). A rerun surfacing one extra unrelated
  candidate must not renumber every later origin and, through the
  consolidated hash, churn dismissals on defects nothing about which
  changed. `_mint_finding_id` gains the sorted membership: a consolidated
  claim's top-level wording is CANONICAL rather than any one lens's, so the
  members' own words would otherwise vanish from the hash entirely — and a
  rerun that groups a defect differently is a different thing to dismiss.
  **The test that pins this had to be built carefully**: the obvious
  dismiss-memory test passes for the wrong reason (the canonical wording
  differs, so the ids differ on claim text alone). The real pin is a group
  whose canonical claim reproduces one member's words verbatim, where
  membership is the only difference.
- **A repeated claim is disambiguated, never deduplicated**
  (`_unique_origin_id`; review finding on PR #104, Codex). `normalize_findings`
  deduplicates nothing, so a lens CAN emit the same finding twice — both then
  content-address to one id, and a duplicate origin id is exactly what the
  reload partition check refuses. The run finished, serialized, and had the
  whole paid report discarded the next time the project was opened. The
  suffix (`qco-<digest>-2`) counts only byte-identical EARLIER claims, so an
  unrelated candidate can never shift it and the ordinal-independence above
  survives. Dropping the duplicate instead would have been simpler and
  wrong: "no original candidate disappears" is the criterion the whole step
  is built around. **The same fix closes a PRE-EXISTING instance of the same
  bug** — two identical claims from one lens minted one `finding_id` on
  master too, and `from_dict`'s duplicate-id check discarded the report for
  that alone, before consolidation existed.
- **The manifest gains `consolidation_enabled` + `consolidation_rule`**, so
  a report always states which regime produced it and a retained pre-5.2
  result reads STALE — deliberate, and the same posture `model`/`effort`
  already take. `matches_inputs` rebuilds with the CURRENT setting.
  `settings.QC_CONSOLIDATION` (env `BUILD_A_SPEC_QC_CONSOLIDATION`, default
  on) is the operator escape hatch that makes the flag meaningful;
  `QC_CONSOLIDATION_MAX_BUCKET` (25) is a runaway guard whose breach is
  RECORDED in `fallback_reason`, never silent.
- **The consolidation call is a billed record like any other.** It joins
  the population `_audit_accounting_consistent` reconciles and the run's
  `api_request_count`/`model_response_count` totals, so a reader can
  reconcile the total from lens + consolidation + verifier records without
  inference. A failed call is still billed and still reconciles.
- **Live: a named TRANSITION, not a fourth stage.** `consolidation_started`
  / `consolidation_complete` (raw / grouped / panels-avoided counts) ride
  the existing channel; `QcLivePhase` gains `"consolidation"` and the
  drawer shows a line, while `stages` stays the three gates a reviewer can
  pass or fail — the step decides nothing, so showing it as a gate would
  overstate it. The grouping call's own activity/search/fetch frames are
  emitted by the shared machinery but deliberately NOT folded into visible
  state (pinned: a noisy log and a quiet one fold identically). The roster
  entry gains `origin_count`.
- **Reports.** Word gains a "Cross-Lens Candidate Consolidation" section, an
  "Original Lens Claims" subsection on every multi-origin candidate, an
  operation-provenance line, and a methodology step; `QCReportModal` mirrors
  all of it (section 05b + a `CandidateOrigins` block); `QCDrawer` shows a
  `×N` chip on a consolidated finding and the transition line. A failed
  grouping step is a disclosed LIMITATION in both projections, worded to
  say the honest thing: the run cost more, it did not lose work.
- **`duplicate_provision`** (`spec_doc/linting.py`) is the advisory backstop
  — consolidation stops QC proposing two fixes for one defect, but nothing
  stops a model restatement, an older report's fixes applied one at a time,
  or a hand edit. Two SIBLING paragraphs only (a repeat across parts is
  usually a legitimate cross-reference), ≥25 normalized chars, and
  **numeric tokens must match before similarity is consulted** — "Provide
  4 inch pipe" and "Provide 6 inch pipe" are ~95% similar as text and are
  two entirely different requirements. Each paragraph reports against its
  first match only, so three identical siblings give two findings.
- **`tests/fakes.py` needed a routing fix, not just a builder.** A grouping
  call quotes every candidate's title, so it matched — and consumed —
  scripts keyed on a finding title, desynchronizing that finding's whole
  panel. `SequencedFakeClient` routes consolidation requests against
  marker-bearing keys only, and answers an unscripted one with the identity
  partition (`singleton_consolidation_for`), so every pre-5.2 fixture keeps
  meaning what it always meant while still exercising the real path.

## Commands

```
.venv/bin/python -m pytest -q          # backend suite (Windows: .venv\Scripts\python)
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

## Runtime date awareness — implemented notes (the app knows what day it is)

Reported symptom (Abraham): the app doesn't seem to know the current date
and time at run time, "and it will affect the code cycles it is aware of."
Correct, and it was a total gap — the string "current date" appeared nowhere
in any prompt the app sent. A model has no clock, so every judgement about
edition currency was being made against the shape of its training data,
which is frozen and drifts further out of date with every month a build
stays in the field. For a tool whose whole domain revises on fixed
multi-year cycles (NFPA 13 every three years, the I-codes every three),
that is a wrong answer delivered with total confidence, and it gets worse
on its own. No new deps, no new endpoint, no new SSE event, no
project-format bump.

- **`backend/runtime_context.py` is the one place the app reads the wall
  clock for the model's benefit.** `current_datetime` (aware, with an
  injectable `now` so tests pin a date without monkeypatching a module
  globally), `current_date_iso`, and `date_context_block(with_time=)` —
  the rendered stamp followed by `DATE_AWARENESS_DIRECTIVE`. Pure, no app
  imports, no I/O.
- **Local time, not UTC.** This is a desktop app; "today" means the user's
  today, and a UTC reading is a day ahead for every user in the Americas
  each evening. The zone is named in the stamp (`local time, PDT,
  UTC-07:00`, degrading to whatever the platform supplies) so the model is
  never guessing which clock it is reading. Audit timestamps —
  `QCResult.started_at`, the redline `w:date` — stay UTC: those are
  records, not context.
- **The date is necessary but not sufficient, so the directive ships with
  it.** Stating the date alone leaves the model free to keep treating its
  own recollection as current. `DATE_AWARENESS_DIRECTIVE` is shared
  verbatim by all four surfaces — the date is authoritative and supersedes
  any trained sense of the present; a revision cycle recalled as pending
  may have closed; never call an edition current from memory alone; and
  when the elapsed time makes a newer edition likely, **say so rather than
  quietly drafting to either one**. That last clause is the whole point
  for this app: a jurisdiction on an older edition is normal, so a newer
  publication is a question to raise, never grounds to change a recorded
  edition unasked. One shared constant so the posture cannot drift into
  four subtly different ones.
- **Never in the stable system prompt.** That block carries
  `cache_control` and must stay byte-identical for the session, so the
  date renders into dynamic context only — first in every turn's PROJECT
  CONTEXT, ahead of `PROJECT IDENTITY`, because everything under it is
  dated (the editions in effect, the research profile's as-of stamps, the
  model's own currency judgement). It costs nothing there: the whole block
  is stripped again at commit, so it never fossilizes a stale date into
  history. The stable prompt gained the date-free half — `_STANDARDS_POLICY`
  now says editions are on cycles, that the app runs long after training,
  and to measure the recorded editions against the date the context opens
  with.
- **Chat gets the time, the fan-outs get only the date, and that
  asymmetry is load-bearing.** Time of day cannot change the answer to a
  code-cycle question, and it is precisely the component that would churn
  a cached prefix on every single call.
- **The fan-outs read the clock ONCE per run and thread the string** —
  the same discipline v1.8.0 applied to `effort`, for a sharper reason.
  `run_final_qc` pins `today = date_context_block()` beside `effort` and
  threads it to `_run_lens`/`_verify_one` → `_lens_shared_prefix` /
  `_verifier_shared_prefix`, where it LEADS both cached prefixes. Reading
  the clock inside the prefix builders would look correct in review and
  pass every same-day test, then silently fork both cache lineages on any
  run that crossed midnight — a regression visible only as a bill that
  failed to drop. `run_requirements_research` does the same, and reuses
  the one reading for the round's own `research_date` stamp, so a round
  that starts at 23:59 can't research "yesterday" and file under "today"
  (this replaced the stray `time.strftime` there).
- **Recorded on the QC result, deliberately NOT in its input manifest.**
  Hashing the date would flip every retained Final QC result stale at each
  midnight and demand a re-run of a review that costs real money and has
  not actually gone out of date. So `QCResult.context_date` persists the
  local date the run gave its reviewers, and the fingerprint ignores it —
  recorded, not fingerprinted, pinned by a test so a later "the manifest
  should cover every input" pass has to argue with it. `started_at` does
  NOT stand in for it (a first draft of this claimed it did, caught in
  review): that field is a UTC audit timestamp while the context date is
  the user's local one, so they are different calendar days for an evening
  run west of UTC. ONE `current_datetime()` reading feeds both the prompt
  and the record, so the two can never disagree. Surfaced as "Current date
  supplied to reviewers" in the Word memo and the report modal, and absent
  (→ "Not recorded") on every pre-1.8.0 record rather than defaulted to
  today.
- **The deprecated compliance audit got it too** (`build_audit_user_message`),
  reading inline since it is a single uncached call. It is superseded by the
  QC lenses but still reachable, and it would otherwise have been the one
  surface left judging currency against training data.
- **Tests**: `tests/test_runtime_date.py` (11). Seven fail against the
  pre-fix code. The two that matter most use a **counter clock** — a fake
  that hands out a different day on every call — so any refactor toward a
  per-call read shows up as four disagreeing dimension dates, or as more
  than one reading inside a QC run, rather than passing quietly until the
  next midnight run.

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
