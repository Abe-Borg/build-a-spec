# Phase 4 — Harvest: nothing settled is left in the transcript

**Status:** complete (`54d2437` + `9b8b40d`, PR #185, merged `a0f66c6`). **Depends on:** nothing in this program (Phase 1
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

- **Harvest the facts the chat settled.** Harvest facts… in the Project
  facts panel reads the conversation since the last harvest, the draft's
  provisions and your Final QC dismissal reasons, and proposes the project
  facts nobody recorded — each with its source and the line it rests on
  (a quote that cannot be found in what was read is flagged for you to
  check). You tick, edit or reject every proposal before anything is
  saved; a proposal that points at a research finding, attached document,
  Final QC finding or reply that does not exist cannot be recorded until
  you correct it, and fixing one never costs another call. It is one paid
  model call, runs only when you press Run, and shows in Settings → Usage
  as "Fact harvest". It is there whenever the section has something to
  read — a reply, a provision or a dismissal reason — so a master you
  imported and edited by hand can be harvested too. Next section and the
  Export menu say how many replies have gone unharvested and offer to
  harvest first — they never run it.
- **A fact's source has to exist.** Recording a fact — by the assistant,
  in the panel or from a harvest — now checks that its cited research
  finding, attached document, Final QC finding or reply is really there;
  the assistant is told why and corrects it. A fact that cites a reply is
  tied to that very reply, not just its number: when removing a document
  forgets the replies that read it, a later reply taking the number does
  not pass for the one the fact cited. A fact recorded before this check,
  carried in from another section, or whose document or reply was removed
  is marked "source not found" in the panel and otherwise left exactly as
  it was.

## Deviations from the plan

(Record each as-built deviation here, dated, with the reason.)

As built, 2026-09-22:

1. **The resolver takes a snapshot, not the session.** The spec's
   `resolve_fact_source(kind, ref, *, session)` became
   `resolve_fact_source(kind, ref, *, sources: FactSources)`, with
   `conversation.fact_sources(session)` building the frozen id sets
   (research items, attached documents, retained QC survivors + disputed,
   committed replies, known section numbers) and `source_resolver(sources)`
   the hook `record` / `supersede` / `apply` / `update` accept as
   `resolve=`. `project_facts.py` stays a leaf (no session import), a caller
   holding the guard gets an answer coherent with what it is about to write,
   and the harvest's `<available_sources>` block is rendered from the same
   sets the commit checks against — the prompt cannot offer an id the check
   would refuse.
2. **`record()` resolves before the duplicate check, and `update()` only
   when the source changes.** A restated fact citing a missing source is
   refused rather than silently confirmed — every ref a caller sends must
   resolve. An edit that leaves `source_kind` / `source_ref` alone is not
   checked, so a fact recorded before the check (flagged, never rewritten)
   can still be edited or retired from the panel.
3. **`brief` resolves to the merge's own provenance too.** The spec said
   "a section number in the link's registry"; Phase 3 writes
   `"project brief; <who>"` for the D4 conflict fact and for a carried fact
   whose source broke in the merge, so `"project brief"` and
   `"project brief; …"` resolve as well, and the known section numbers are
   the registry plus this section's own. `brief` stays unrecordable by the
   tool, the panel and the harvest; the rule only decides the flag.
4. **Every facts surface carries the flag.** "`unresolved_ref: true` in the
   panel payload" is `SessionState.facts_payload()`, the ONE ledger view the
   document payload, the `project_facts` SSE event, the panel routes and the
   harvest commit all return — a flag on one surface and not the next would
   flicker on every turn.
5. **Shape fails the preview; everything else is a per-proposal problem.**
   A payload that is not a list, a field of the wrong type, a value outside
   an enum, a missing required field, or more than 40 proposals fails the
   whole preview (a reply that broke the schema cannot be trusted in its
   other parts). An over-long statement, detail, section or source, an
   empty statement, and a source that does not resolve are shown on the
   sheet as that proposal's `problem` — the strict-mode subset carries no
   length limits, and one over-long line must not throw away a paid call's
   other proposals. Over-long `evidence` (display-only, never recorded) is
   trimmed and marked rather than refused.
6. **The token survives a fixable commit.** A token is consumed by a
   successful commit or a stale binding, but NOT by `invalid_fact` (the
   reasons come back per proposal index) or `turn_active` — the preview
   was a paid call and a typo must not cost another one.
7. **The binding is re-checked when the call returns**, not only at
   commit: a project that changed while the call ran answers 409
   `harvest_stale` at once (still metered) instead of showing a sheet that
   could never commit.
8. **A commit also refuses collisions.** An accepted proposal the user
   edited into a statement an active fact already says, or two accepted
   proposals saying the same thing, is an `invalid_fact` error for that
   index rather than a silent no-op — the user edited it and should know.
9. **Recording nothing is a commit.** An empty `accepted` still advances
   the marker (the user reviewed those replies and chose none); the dialog
   labels it "Record none — mark these replies read". Cancel is the way to
   leave the marker where it was.
10. **The marker never runs past its conversation.** It moves to the reply
    count the preview read, clamped to the replies the history holds now
    and never backwards; project load clamps a hand-edited value (and reads
    a boolean as 0); deleting a reference document that truncates the
    history clamps it too.
11. **Metered through `add_usage_if_current`**, not `usage.add`: a harvest
    whose session was replaced while the call ran must not bill the new
    session (the research/QC posture). Every error after a response — a
    refusal, no tool block, a malformed payload — carries its usage.
12. **Refusal codes and statuses.** Beyond the spec's tour 409 and no-key
    400: 409 `turn_active`; 400 `nothing_to_harvest` (no replies, no
    provisions and no dismissal reasons — refused without a call); 502
    `harvest_refused` / `harvest_no_output` / `harvest_malformed` /
    `auth_error` / `provider_error`; and at commit 409 `harvest_stale` /
    `harvest_expired`. The preview answers the transcript window too
    (`turns_read`, `turns_dropped`, `first_turn`, `last_turn`,
    `replies_total`, `since_bubble`, `provisions`, `dismissals`) and an
    `estimated_cost_usd`, so the sheet can say what was read and what it
    cost.
13. **`evidence_found` is advisory.** Each proposal says whether its quote
    appears in what the call read (spacing, case and markdown emphasis
    aside); a quote not found is flagged on the sheet for a human to check,
    never blocked — the model was told to quote exactly, and a line nobody
    wrote is the first sign of a fact nobody settled.
14. **The harvest does not propose research findings.** They already
    travel with the project in its research profile; the system prompt says
    to cite one as a source instead of restating it, so a brief never
    carries the same requirement twice.
15. **`harvest` on the document payload carries `replies_total`** beside
    the spec's `replies_since` / `last_bubble`, and
    `_assistant_bubble_count` is public (`assistant_bubble_count`): the
    harvest numbers its transcript by it, so `turn:N` means the same reply
    to the prompt, the resolver and the hint.
16. **The pending-preview cache also caps its count** (16, oldest out)
    beside the template cache's expiry and byte cap.
17. **The brief-export hint lives in the Export menu.** The spec put it in
    the brief export's confirm; PR #178 removed that confirm (the entry
    saves straight away), so the hint is a line under *Export project brief*
    that closes the menu and opens the dialog. Next section's hint re-reads
    its receipt when a harvest records something, keeping the user's choice.
18. **The dialog has an intro and a done state.** Opening it never spends:
    the intro says what will be read and that it is one paid call, and only
    *Run the harvest* makes it. After a commit, the done state says what was
    recorded before the dialog closes (back to whatever opened it — Next
    section stacks under it, and Escape closes only the top dialog).
19. **The Project facts panel renders when replies are waiting** (widened
    by 25), outside
    a tour, not only when facts exist or the section is linked — the harvest
    is how a section that never recorded a fact finds the ones it settled,
    so its door cannot hide behind an empty ledger. The harvest button is
    hidden in a tour (the server refuses there too); its capability rides the
    `project-facts` step as specified, with no `TOUR_VERSION` bump.
20. **Settings gets a label.** The spec expected nothing to change because
    categories render generically; they do, but as the raw key, so
    `CATEGORY_LABEL` gains `harvest: "Fact harvest"`.
21. **`ModalShell` gains `xwide`** (`max-w-3xl`) for a review sheet whose
    rows carry quoted evidence; every existing consumer is unchanged.
22. **The trust dossier gains more than the fifteenth card.** Its model
    roster says the harvest runs on the interview model at `medium`, the
    boundary line names "no automatic fact harvest", and the "What the model
    may touch" matrix gains the `record_project_facts` row it never had —
    this phase changes that tool's constraint, and the matrix is a contract.
23. **The compaction hand-off is recorded, not wired.** PR #182's chat-history
    compaction plan (`../CHAT_HISTORY_COMPACTION_2026-09-22.md`, its Phase 4
    "promote before prune") hands this harvest one more candidate source: the
    condensed-conversation summary's "decisions the ledgers are missing"
    list. That summary does not exist yet — the plan's Phase 3 is not started
    and its D1/D3/D4 are open — so there is nothing to wire. The seam is ready
    for it: it becomes one more framed, neutralized block in `HarvestInputs`,
    and its flagged decisions reach the sheet as proposals like any other,
    through `assess_proposal` and the same commit — never a second path into
    the facts store. The harvest already honours that plan's standing rule:
    it reads `session.history` (the full record, never a compacted view), so
    `turn:N` stays the Nth assistant bubble of the saved conversation.
    *Follow-up, 2026-09-23: the summary exists now (compaction Phase 3,
    PR #189) and carries that list; D1–D4 were decided on 2026-09-22. The
    owner then dropped this hookup for now on 2026-09-23 (the compaction
    plan's D4), so nothing is wired and this harvest reads no summary. The
    list stays in every summary for the chat model. The seam above still
    describes how to revive it; the compaction plan's "Where it stands"
    names the one question to settle first (what a summary line cites).*

From the Codex review of PR #185, 2026-09-22:

24. **A reply source is pinned to the reply it cited.** The spec's `turn:N`
    check was a range check (`1 ≤ N ≤` the committed replies), but a reply is
    named by POSITION and `delete_reference_if_idle` truncates the history
    without touching the generation — the replies that follow take over the
    discarded numbers, so a fact citing a discarded `turn:3` read as resolved
    again once an unrelated reply 3 existed, its provenance silently changed
    and its flag cleared. A fact carried in from another section had the
    same hole: its `turn:N` names that section's conversation. Every
    committed reply now has an identity (`project_facts.reply_digests` over
    `chat_transcript`: the reply's text and its prompt's, neither of which
    changes once committed), `FactSources` carries them as `turn_digests`
    (`turn_count` is their length), the resolver hook returns a
    `ResolvedSource` (ref + digest), and `record` / `update` stamp
    `ProjectFact.source_digest` — serialized only when set, so every other
    fact keeps its bytes. `annotate_fact_sources` checks a recorded fact
    against the reply it was pinned to; a turn-cited fact with no pin
    (recorded before this) cannot be matched to a reply and is flagged —
    none shipped: the released tool never offered `turn:N`. An edit that
    keeps naming `turn:N` keeps its pin — re-sending the same ref is no
    change at all (`update()` re-checks a source only when the kind or ref
    really differs, as its docstring promised), and a kind change alone
    keeps it too — and a merged edit carries it. The
    harvest binding gains the identity of the replies the preview could cite
    (the first `bubble_count` digests, hashed), so a commit after a
    truncation is `harvest_stale` instead of pinning a proposal to a reply it
    never read; a reply ADDED after the preview changes no number it used
    and leaves the binding alone.
25. **The panel's door follows what the harvest can read, not the reply
    hint.** Deviation 19 rendered the panel for facts, a link, or replies
    waiting — so an unlinked section with no facts and no reply (an imported
    master edited by hand) hid the only unconditional door, while
    `HarvestInputs.has_material()` would have run a call on its provisions or
    its QC dismissal reasons. `harvest_status` moved from `conversation.py`
    to `harvest.py`, beside `has_material`, and asks the same question ahead
    of time: the payload's `harvest` gains `harvestable` (a reply since the
    marker, a provision, or a dismissal reason). The panel renders on it,
    and *Harvest facts…* is disabled — saying why — without it. Next
    section's and the Export menu's nudges stay reply-based: they count what
    is unread, and a draft's provisions have no marker to be read against,
    so a provisions-based nudge could never be dismissed.
