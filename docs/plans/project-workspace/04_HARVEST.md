# Phase 4 — Harvest: nothing settled is left in the transcript

**Status:** not started. **Depends on:** nothing in this program (Phase 1
shipped the surfaces it hooks into). D3 is ratified: a paid, opt-in,
preview-then-commit pass; never fires on its own.

## Goal

A project fact is recorded only when the model judges something "settled"
in that turn, or when the user adds one by hand. The decisions that live in
confirmed provisions and in the transcript — the sprinkler demand at the
base of riser, the water-supply basis, the hazard classification of the
data halls — are exactly the "system facts" the fire-pump section needs,
and nothing sweeps for them. After this phase, one paid model call proposes
the facts the ledger is missing, the user accepts or rejects each, and only
accepted proposals commit — every one resolving to a real source.

## Current code (verified 2026-09-22)

- **The one-off model call idiom** is `app._ai_generalized_template_document`:
  `get_client().messages.stream(model=settings.INTERVIEW_MODEL,
  max_tokens=settings.INTERVIEW_MAX_TOKENS, thinking={"type": "adaptive"},
  output_config={"effort": settings.TEMPLATE_EFFORT}, tools=[<one output
  tool>], messages=[{"role": "user", "content": prompt}])`, then
  `session.usage.add("template", response.usage, count_turn=True)`, a
  `stop_reason == "refusal"` branch through `refusal_category`, and
  `extract_tool_use_block(response, TOOL_NAME)`. `template_document_tool()`
  (`backend/templates.py`) is a non-strict tool because its payload is a
  recursive tree; a FLAT payload takes `strict: true` per
  `research/schema`'s conventions (`_STRICT_CAPABLE_MODELS` lists the
  Sonnet 5 id).
- **Preview → commit** is `TemplateCatalog.preview(...) -> (token, view)`
  and `commit_preview(token, binding=)`: a token bound to
  `_template_binding(lease)` = `{workspace_id, generation, doc_version}`,
  refused when the binding moved or the token expired, with a byte cap on
  the preview cache.
- **Facts entry points:** `ProjectFactStore.apply(payload, *, recorded_in,
  recorded_at, default_source_kind, discipline)` is all-or-nothing and
  returns `recorded / duplicate / superseded`; `validate_record_payload`
  validates the tool's shape; `SessionState.add_project_fact_if_idle` is
  the panel path (409 while a turn owns the store). `source_ref` is only
  normalized and length-bounded — **no validator resolves it** (Codex, PR
  #173).
- **Sources a ref can name:** `session.research.profile_result.item(item_id)`
  (`r-…`), `session.references.get(rid)` (`ref-…`),
  `session.qc.result.finding(fid)` (survivors + disputed; `qc-…`), the
  transcript (`_assistant_bubble_count(history)` is the ordinal the
  follow-ups store already uses), and the link's `sections[].number`.
- **What is readable:** `session.history` (committed, PDF-elided, thinking
  stripped; text blocks are `{"type": "text", "text": ...}`; tool blocks
  are `tool_use` / `tool_result`; server-tool blocks end in `_tool_result`),
  `outline(section, max_text=None)` (`spec_doc.model`) with statuses, the
  retained QC result's findings with `dismiss_reason` and
  `disposition_events`, and the current facts/profile/identity/standards
  blocks the turn context already renders.
- **Metering:** `usage_ledger._category_models()` maps a category to its
  model; the Settings usage table renders categories generically.
- **Tracing:** `capture.app_event(type, **fields)` for run-level events.

## Design

### 4.1 `backend/harvest.py` — the pass, pure where it can be

- `HARVEST_TOOL_NAME = "propose_project_facts"`; `harvest_tool()` returns
  the STRICT tool: `{"proposals": [{statement (≤240), detail (≤600), scope
  ∈ {project, discipline, section}, section (string, "" unless scope is
  section), status ∈ {confirmed, assumed}, source_kind ∈ {user, research,
  reference, qc, model}, source_ref (string), evidence (≤300 — the quoted
  line the proposal rests on)}]}` with `additionalProperties: false` and
  `required` on every key (the strict-mode subset). ≤ 40 proposals per
  call; more is refused as malformed (the model is told the cap).
- `build_harvest_request(session) -> HarvestInputs` (pure over a snapshot
  taken under the guard): `transcript_text` (user + assistant TEXT blocks
  only, since `last_harvest_bubble`, each assistant bubble prefixed
  `[turn:N]` so a proposal can cite it; tool inputs/results, thinking and
  the elided PDF placeholders excluded), `outline_text` (full outline with
  statuses — confirmed provisions are evidence), `qc_dismissals` (finding
  id, title, dismiss reason), `known_facts` (the current active statements
  — the model proposes only what is NEW), `identity_blocks` (profile,
  identity, standards in effect — so it never re-proposes them),
  `research_ids` / `reference_ids` / `qc_ids` (what a ref may name). Caps:
  `HARVEST_MAX_TRANSCRIPT_CHARS` (400k); over it the OLDEST turns are
  dropped and the preview says so. The prompt frames every block as data
  (`<transcript>`, `<specification>`, …) and neutralizes the frame tags in
  the content (`neutralize_reference_delimiters`' pattern).
- `run_harvest(client, inputs, *, model, effort) -> HarvestResult`: the
  one-off call idiom; `stop_reason == "refusal"` → `HarvestError` with the
  category; no tool block → `HarvestError`; the payload validated field by
  field; usage returned for the caller to meter.
- `resolve_fact_source(kind, ref, *, session) -> str` (in
  `project_facts.py`, beside `validate_record_payload`): `research` → an
  `r-…` present in the profile; `reference` → a `ref-…` in the store; `qc`
  → an id in `QCResult.finding()`'s set (survivors + disputed) of the
  retained result; `user` / `model` → `""` or `turn:N` with
  `1 ≤ N ≤ _assistant_bubble_count(history)`; `brief` → a section number
  in the link's registry. Returns the normalized ref or raises
  `ProjectFactError` naming what does not exist. **Wired in three places:**
  the harvest commit (refuses), the `record_project_facts` tool dispatch
  (an `is_error` result the model corrects — a behaviour change to state
  in the release-note draft), and the panel add/update routes (400 with
  the message). Facts already recorded with an unresolvable ref are left
  alone and marked `unresolved_ref: true` in the panel payload, never
  rewritten.
- Proposals that duplicate an active fact by `fact_match_key` are dropped
  before the preview is shown (with a count); proposals whose ref fails to
  resolve are SHOWN with the error and cannot be accepted until the user
  edits the ref or clears it (a `user` fact with no ref is valid).

### 4.2 Preview → commit, bound like a template preview

- `POST /api/project/facts/harvest` → runs 4.1 on a worker thread (the
  event-loop rule; the model call is seconds to minutes) after refusing in
  a tour, while `turn_active`, or with no key; meters `session.usage.add(
  "harvest", usage, count_turn=True)` (new ledger category, model
  `INTERVIEW_MODEL`, rate table shared — add it to `_category_models`);
  stores the proposals in a module-level `HarvestPreviews` (token →
  {proposals, binding, created}) with the template cache's expiry and byte
  cap; returns `{ok, token, proposals[], dropped_duplicates, transcript_
  truncated, usage}`. Binding = `{workspace_id, generation, doc_version,
  facts_len}`.
- `POST /api/project/facts/harvest/commit {token, accepted: [index …],
  edits: {index: {statement?, detail?, scope?, section?, status?,
  source_kind?, source_ref?}}}` → re-validates each accepted proposal after
  edits (`validate_record_payload` shape + `resolve_fact_source`), then
  `SessionState.commit_harvest_if_idle(records)` — `facts.apply({"record":
  records, "supersede": []}, recorded_in=<section>, recorded_at=<today>,
  default_source_kind=…, discipline=effective_discipline)` under the guard,
  409 while a turn owns the store, all-or-nothing. Advances
  `session.last_harvest_bubble` to the bubble count the preview was built
  at (a new persisted `SessionState` field: optional project key
  `last_harvest_bubble`, cleared on reset, declared in the wipe sweep).
  Returns the facts snapshot; the frontend refreshes readiness AND QC state
  (facts are a hashed QC input).
- `capture.app_event("harvest", action="preview"|"commit", proposals=…,
  accepted=…, ok=…)`; never the proposal text.

### 4.3 Offered, never forced

- `_doc_payload` gains `harvest: {replies_since: N, last_bubble: M}` from
  `_assistant_bubble_count(history) - last_harvest_bubble`.
- The Project facts panel gets a **Harvest facts…** button (capability
  `project.facts-harvest`), with a one-line "N replies since facts were
  last harvested" hint; it opens `HarvestDialog.tsx` (ModalShell): a
  running state ("Reading N replies and the draft…"), then the review
  sheet — one row per proposal with the quoted evidence, editable fields,
  an accept checkbox (unchecked when the ref failed, with the reason),
  Commit / Cancel; the cost of the preview call shown from `usage`.
- `NextSectionDialog` and the brief export confirm show the same one-line
  hint with a "Harvest first" link that opens the dialog and returns to
  where the user was. Neither runs it.
- `HARVEST_EFFORT` (`BUILD_A_SPEC_HARVEST_EFFORT`, default `medium` — the
  pass extracts, it does not draft; `_effort_env` convention) with a
  README Configuration row (the knob-inventory test will fail without it).

### 4.4 Deliberately out

- No automatic harvest on export, save or next-section (D3).
- No supersedes proposed by the harvest: it adds; retiring stays with the
  model's in-turn tool and the panel.
- No harvest of figures, follow-ups or the QC report body — QC
  DISMISSAL REASONS are the one QC input, because a written rationale for
  setting a finding aside is a settled decision.

## Files

- `backend/harvest.py` (new), `backend/project_facts.py`
  (`resolve_fact_source`, `fact_match_key` public if Phase 3 has not landed
  yet), `backend/llm/conversation.py` (`last_harvest_bubble`,
  `commit_harvest_if_idle`, the tool-dispatch resolver call),
  `backend/app.py` (two routes, `harvest` on `_doc_payload`, the panel
  routes' 400), `backend/settings.py` (`HARVEST_EFFORT`),
  `backend/usage_ledger.py` (`harvest` category), `backend/spec_doc/project.py`
  (the optional key), `backend/llm/prompts.py` (`_PROJECT_FACTS_POLICY`:
  a `source_ref` must name something that exists).
- Frontend: `HarvestDialog.tsx` (new), `ProjectFactsPanel.tsx`,
  `NextSectionDialog.tsx`, `ArtifactPanel.tsx` (the export confirm hint),
  `api.ts`, `types.ts`, `capabilities.ts` (`project.facts-harvest`),
  `tour.ts` (the `project-facts` step's capability list gains it),
  `SettingsPanel.tsx` (nothing — categories render generically; verify),
  `TrustDeepDiveModal.tsx` (a fifteenth runtime card: *Harvesting facts* —
  you do / what runs / what is sent / AI involved / bounded by), `HelpModal.tsx`.

## Tests

`tests/test_harvest.py` (new, hermetic through `tests/fakes.py`; add a
`harvest_response(proposals)` builder that emits the strict tool block, and
a refusal variant):

- Inputs: `test_the_request_carries_text_blocks_only` (tool inputs,
  tool results, server-tool blocks and thinking absent; PDF placeholders
  absent; `[turn:N]` prefixes present), `test_known_facts_and_identity_blocks_are_sent_so_nothing_is_reproposed`,
  `test_the_transcript_cap_drops_the_oldest_and_discloses`,
  `test_frame_tags_inside_the_transcript_are_neutralized`.
- Resolver: one case per source kind resolving and refusing (`r-` absent,
  `ref-` absent, `qc-` absent or refuted-only, `turn:0` and `turn:N+1`,
  a section number not in the registry), the tool's `is_error`
  self-correction through `/api/chat`, the panel routes' 400, an existing
  unresolvable fact left alone and flagged.
- Pass: `test_preview_returns_proposals_and_meters_under_harvest`,
  `test_duplicates_of_active_facts_are_dropped_before_the_sheet`,
  `test_a_refusal_is_named_not_parsed`, `test_a_reply_without_the_tool_is_refused`,
  `test_a_malformed_proposal_fails_the_whole_preview`.
- Commit: `test_commit_is_one_batch_and_a_bad_edit_rolls_it_back`,
  `test_commit_refuses_a_stale_binding_and_an_expired_token`,
  `test_commit_refuses_mid_turn`, `test_the_marker_advances_only_on_commit_and_persists`,
  `test_commit_flips_the_retained_qc_result_stale`.
- Guards: tour 409, no key 400/409 (the research-start idiom), the
  `harvest` row in `/api/usage`.
- `tests/test_session_wipe.py` — `last_harvest_bubble` declared.
- `tests/test_docs_consistency.py` — the knob row (fails without it).

Frontend: `frontend/tests/harvest.test.ts` (source-level) — the dialog
never commits unchecked rows, refreshes readiness and QC after commit,
declares its capability, and the two hint sites open the dialog rather than
running the pass.

## Docs

- `README.md`: the "Project workspace (in progress)" subsection — what
  harvest reads, that it is one paid call the user previews, the resolver;
  a Configuration row for `BUILD_A_SPEC_HARVEST_EFFORT`.
- `CLAUDE.md`: implemented notes ("Harvest") — the one-off call idiom
  reused, the strict flat tool, the resolver wired in three places (and
  the tool-dispatch behaviour change), the preview binding, the marker, the
  "offered never forced" rule; Layout entries for the new module and test.
- `docs/RELEASE_WINDOWS.md`: QA rows — a real harvest on a real section
  (paid, owner-run), a proposal with a bad ref refused, commit then the QC
  stale marker.

## Release-note draft (Phase 7 copies from here)

- **Harvest the facts the chat settled.** One button reads the
  conversation, the confirmed provisions and your Final QC dismissal
  reasons, and proposes the project facts nobody recorded — each with the
  line it rests on. You accept or reject every proposal before anything is
  saved, and a proposal that points at a research item, reference or
  finding that does not exist cannot be accepted. It is a paid model call,
  runs only when you press it, and the Next-section and Export flows tell
  you how many replies have gone unharvested.
- **A fact's source has to exist.** Recording a fact — by the assistant or
  in the panel — now checks that its cited research item, reference
  document or Final QC finding is really there.

## Deviations from the plan

(Record each as-built deviation here, dated, with the reason.)
