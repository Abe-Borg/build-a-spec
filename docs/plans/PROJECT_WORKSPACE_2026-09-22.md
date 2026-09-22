# Project workspace — carry a project's work across its sections

Owner: Abraham. Drafted 2026-09-22 against `d5c9034` (v1.19.1).

**This file is the design RECORD, not the handoff.** The program's handoff
— the implementation record, the prompt a fresh session starts from, the
release policy (one release at the end, cut by Phase 7), the ratified
decisions and the program rules — lives in
[`project-workspace/README.md`](project-workspace/README.md), and each
remaining phase has a full spec file beside it (`02_PROJECT_HOME.md` …
`07_RELEASE_CLOSEOUT.md`). Where a phase file and Part 2 below differ, the
phase file wins and records the difference. Phase 1 shipped in v1.20.0
(PR #174); its as-built deviations are under its heading below.

The ask: finish the common work on the fire-sprinkler section (21 13 13) —
the client and jurisdiction research, the location facts, the system facts,
the project facts — then swap in the fire-pump section (21 30 00) and have all
of that carry over, without dragging the previous session's context along.

## Part 1 — What the app already does (verified in the tree)

**Yes. The core of this shipped in v1.17.0 as "project briefs", and it does
most of what the ask describes.** The relevant code, all present at `d5c9034`:

| Piece | Where | What it does today |
|---|---|---|
| The brief file | `backend/project_brief.py` (760 lines) | A `.basproject` carries the project profile (city/state/country/client), project type, recorded edition overrides with their bases, the **whole** research profile (every round), the attached reference documents (full text, fingerprinted), the established project facts (retired ones included), and a registry of the sections drafted so far. It never carries the transcript, the document, figures, the Final QC report, follow-ups, suppressed standards, or an imported Word source. |
| Project facts | `backend/project_facts.py` (1,180 lines), `frontend/src/components/ProjectFactsPanel.tsx` | The `record_project_facts` chat tool plus a panel. Scopes `project` / `discipline` / `section`; statuses confirmed / assumed / superseded (never deleted); source kinds user / research / reference / qc / model. Rendered into every turn's PROJECT CONTEXT and into both fan-outs' cached prefixes; hashed into the Final QC input manifest. |
| Export | `GET /api/project/brief`, `/brief/manifest`; Export menu → *Export project brief*; `main.py` `save_project_brief` | Confirm dialog lists what will travel, then a native Save. Exporting stamps `session.project_link` so a project has one id across re-exports. |
| Seed | `POST /api/project/brief/inspect`, `POST /api/project/brief/start`; New session → *New section in an existing project*; `SessionState.start_from_brief` | Reads a `.basproject` **or a finished section's `.baspec`** (the brief is built from it on the fly), shows a manifest card, lets you set the discipline and pair a template, then seeds in one transaction: version 0 is the project setup, research restores as complete, references and facts land in their panels. |
| Research is briefed, not repeated | `research/engine.established_facts_for`, `scope: all\|gaps\|selected` | The seeded section's first Research press is a *briefed* round: every agent is told what the project already established and reports only what is new, changed or wrong. Readiness passes the carried research and says which section it came from. |
| Tests | `tests/test_project_brief.py` (1,203 lines), `tests/test_project_facts.py` (1,121), `tests/test_facts_agent_visibility.py` | Round trip, seed transaction, routes, agent visibility. |

So the fire-sprinkler → fire-pump case works **today** like this:

1. In the 21 13 13 session: Export → *Export project brief* (or just save the
   `.baspec`).
2. New session → *New section in an existing project* → pick the
   `.basproject` (or the 21 13 13 `.baspec`) → the manifest card → Start.
3. The 21 30 00 session begins with the profile, the jurisdiction/client
   research (complete, briefed for the next round), the owner's attached
   standards, the adopted editions, and every recorded project fact. The
   model's context also carries a PROJECT SECTIONS block naming 21 13 13 and
   its articles, so it cross-references rather than re-specifies.

`hyperscale_fire`'s sibling catalog already lists **21 30 00 Fire Pumps**, so
the model knows what that section is when it arrives.

### What the ask exposes as missing

Verified against the code, and mostly already recorded as "deferred, on
purpose" in `README.md` (v1.17.0 section) and `CLAUDE.md`:

1. **It is a file relay, not a project.** The brief is a snapshot you must
   remember to export, and re-export after every section. Nothing in the app
   knows where a project's sections live: the registry records only a file
   *basename* (`section_record.file_name`), the save target is deliberately
   never persisted, and "swap in" the fire-pump section means Open project and
   a file dialog. There is no project hub.
2. **Nothing flows backward or sideways.** A section seeded from the brief is
   a **fork**. Facts, rounds and references added in 21 30 00 reach 21 40 00
   only if you export *from 21 30 00*; anything settled in 21 13 13 after
   21 30 00 was seeded never reaches 21 30 00 at all. The append-only,
   id-joined write-back merge is explicitly deferred (`project_facts.py`
   reserves `source_kind="brief"` for it).
3. **Capture depends on the model remembering.** A fact is recorded only when
   the model judges it "settled" in that turn, or when you add it by hand.
   Decisions that live in confirmed provisions or the transcript — the
   sprinkler demand at the base of riser, the water-supply basis, the hazard
   classification of the data halls — are exactly the "system facts" the
   fire-pump section needs, and nothing sweeps for them. The paid harvest
   pass is deferred.
4. **Later sections pay for everything, every turn.** The carried research
   block (`RESEARCH_CONTEXT_MAX_TOKENS = 100_000` estimated), the facts block
   (6k) and the sections block (3k) ride each turn's PROJECT CONTEXT, which is
   stripped at commit and re-written every turn. Per-section relevance
   trimming is deferred, and unmeasured.
5. **Nothing is client-level.** A hyperscale client repeats across sites, and
   the `client_standards` research dimension, client-standard reference
   documents and client-preference facts would be reusable across
   *projects* — but a brief is a project, and facts have no client scope.
6. **Minor:** open project-level follow-ups (a question still owed to the AHJ
   or the client) do not carry; the "Waiting on you" list is per section.

Items 1–3 are the substance of the ask. Item 4 becomes real by the fifth
section. Items 5–6 are optional extensions.

## Part 2 — The plan

**Target:** a project is a first-class thing. One living project file, sections
that hang off it, switching between them in one click, everything
project-level captured automatically and flowing in every direction. Each
phase below is independently shippable and ordered by dependency; every phase
keeps the standing rules (no transcript or document ever travels; a brief
carries provenance, never a model summary; every failure path is disclosed,
never silent).

### Decisions (ratified as recommended, 2026-09-22 — binding)

| # | Decision | Recommendation |
|---|---|---|
| D1 | Where a project lives | **A folder.** `<Project>.basproject` beside one `.baspec` per section. Discovered by co-location plus a matching `project_id` — no paths persisted in any file, so a folder can be moved or shared whole. Browser/dev sessions keep today's file relay. |
| D2 | When the brief is refreshed | **On every project Save**, silently, when a co-located brief exists (an append-only merge, never an overwrite); plus an explicit *Update project brief* action. The user should never have to remember to export. |
| D3 | Harvest is a paid call | **Opt-in, one call, preview-then-commit.** Offered (not forced) in the Next-section and Export flows when there are replies since the last harvest. Never fires on its own. |
| D4 | Conflicting project setup between sections | **Newest export wins for the profile; edition-override disagreements are recorded as a warning AND a project fact**, because two sections adopting different editions of NFPA 13 is a coordination defect, not a merge artifact. |
| D5 | Client library (Phase 6) | **Defer** until Phases 1–4 have been used on a real multi-section project; the client scope is a small addition once the merge exists. |

### Phase 1 — Next section in one click (no file relay)

*Ships alone. Smallest change, biggest daily win.* **Shipped in v1.20.0.**
As built, three deviations from the text below: (1) template pairing stays
on the New-session route (the dialog says so) — the fast path is a fast
path; (2) the dialog gained a third choice, **leave it unnamed**, because a
named page counts as content (`has_body_content`) and the master import
refuses it — the choice belongs in the dialog, not at the Import button;
(3) the capability rides the `template-use` tour step, which is where
`project.brief-start` already lived (the plan said "the `project.brief-start`
step"; that is the step).

- **`POST /api/project/next-section`** `{module_id?, discipline?, number?,
  title?, template_id?}` builds `build_project_brief(session)` in memory,
  refuses under the same `busy_reasons` and tutorial-scope rules as
  `/api/project/brief/start`, and calls `SessionState.start_from_brief` — one
  transaction, no upload, no temp file. Returns the same session bundle the
  brief start returns, plus the seed report.
- **The panel gains "Next section →"** beside Export. It runs the existing
  save gate first (`CloseDialog`: Save / Continue without saving / Cancel —
  the New-session precedent), then a small dialog pre-filled from the
  module's sibling catalog **minus the sections already in the registry**
  ("21 30 00 Fire Pumps" is one click for a hyperscale project), with the
  discipline and optional template pairing the brief flow already offers.
  The seeded document gets its section header written into version 0 so the
  new session opens on a named, empty section.
- Capability `project.next-section` — the three-place edit (registry, control,
  tour step); it rides the existing `project.brief-start` tour step rather
  than a new one (one step, two controls — the `updates.manage` precedent),
  so no `TOUR_VERSION` bump.
- Tests: the in-memory seed produces a session byte-identical (state
  projection) to export-file-then-start; the busy/tutorial 409 matrix; the
  catalog exclusion; the header landing in version 0; `test_session_wipe.py`
  needs no new field.

### Phase 2 — The project has a home (folder + Project panel)

*Depends on D1. Desktop shell only; the browser keeps the file relay.*

- **Discovery, not paths.** When the shell opens or saves a `.baspec` whose
  `project_link.project_id` is set, `main.py` looks for a `.basproject` in the
  same folder with that id and hands the backend a **project home** —
  `SessionState.project_home` (folder path + brief path), local-only, never
  persisted into any file, cleared on reset and load like `save_target`
  (added to `test_session_wipe.py`'s sweep as a declared wipe). A `.baspec`
  opened from a folder with no brief is what it is today.
- **`ProjectPanel.tsx`**, a collapsible panel under Project facts: project
  name, the section registry (number, title, ready, last exported, facts and
  rounds it recorded, whether its file is present beside the brief), the
  current section highlighted. Per row: **Open** (save-gated; the backend
  resolves `home + file_name`, refuses anything outside the folder, and runs
  the ordinary project-load path so every validation stays shared) and
  **Next section →** (Phase 1, which now also writes the new section's brief
  entry back — Phase 3). A header line says where the project lives.
- Routes: `GET /api/project/sections` (registry joined with file presence;
  read-only), `POST /api/project/open-section {number}`; js_api
  `open_project_at(path)` is shell-internal — the frontend never sees a path.
- Capabilities `project.sections` and `project.open-section`; one new tour
  step in the project chapter (`TOUR_VERSION` bump — a step is added
  mid-chapter, the v1.17.0 precedent).
- Tests: discovery (matching id → home; wrong id or no brief → none; a moved
  folder still resolves), the outside-the-folder refusal, the save gate,
  `test_session_wipe.py`, and the browser fallback showing no panel.

### Phase 3 — Write-back: the brief is a living file, merge is append-only

*Depends on Phase 2 for the automatic trigger; the merge itself is pure and
ships with an explicit action even in the browser.*

- **`merge_project_brief(existing, fresh) -> ProjectBrief`** in
  `project_brief.py`, pure and deterministic, the `append_research_round`
  posture applied to every asset:
  - *Research*: a round has no stable identity today. `ResearchRound`
    serializes only its index, date, dimension statuses, counts and section
    stamp; the item ids live cumulatively on the profile. So no fingerprint
    can be derived from the current record: two genuine same-day rounds on
    one section and roster would collide and one would be lost, and a
    fingerprint over the profile's cumulative item ids would drift as each
    fork adds research, duplicating inherited rounds. Phase 3 therefore
    FIRST adds two additive fields, serialized only when set (the `section`
    precedent, so a legacy profile's bytes and the QC research fingerprint
    over them are untouched): `round_id`, a uuid minted at the round's birth
    in `run_requirements_research` and kept verbatim by
    `append_research_round`, and `item_ids`, the round's own membership
    (new and re-confirmed). Rounds then merge by `round_id`; a legacy round
    with no id is keyed on (section, date, index) and the manifest says so;
    unseen rounds append and renumber; items merge by `item_id` with the
    existing confirm-in-place rule (citations union, grounded OR,
    confidence max, evidence-dated).
  - *Facts*: `pf-N` ids are per-session and collide across forks, so the
    join key is the store's own duplicate key — and that key is the
    normalized STATEMENT alone (`_match_key`): `record()` refuses a second
    active fact with the same statement whatever its scope or bound
    discipline, and the merge must never build a store `record()` would
    refuse. Two active facts with one statement at different scopes are
    resolved deterministically: the wider scope is retained (project >
    discipline > section; tie → the earlier `recorded_at`), the other folds
    in as superseded with the reason "Merged: the same fact was recorded at
    <scope> by <section>" and its link pointing at the retained one, and
    the manifest reports the disagreement — the audit posture, nothing
    deleted. Incoming facts are re-minted past the existing `_next_seq`
    with `superseded_by` links rewritten; a supersede on either side is
    terminal and wins; a fact pulled in this way is stamped
    `source_kind="brief"` — the reserved value, finally used — with
    `source_ref` naming the section that recorded it.
  - *References*: identity is `content_fingerprint`, but the ids are not
    stable across forks either — `ReferenceDocStore.add` mints `ref-N` from
    a per-session counter and `get()` / `read_reference_doc` resolve the
    FIRST match, so two seeded forks can hand the same rid to different
    documents. A document already present by fingerprint keeps the
    EXISTING rid (so an open section's `source_item_id` chips stay valid
    on a pull); a new incoming document whose rid collides is re-minted
    past the existing `_next_seq`, and every incoming fact `source_ref`
    naming the old rid is rewritten to the new one, exactly as `pf-N` is
    handled. A merged store with a duplicate rid is a merge failure that
    is reported and never written. The cap applies and drops are named.
  - *Profile / edition overrides*: D4.
  - *Sections registry*: by number (already the rule).
  - Idempotent: merging the same fresh brief twice is a no-op.
- **When it runs.** (a) On project Save when a home is known (D2) — the
  brief is re-read, merged, and written atomically through the shell's
  existing `_atomic_write_target`; a merge failure never fails the save, it
  is reported beside it. (b) *Export project brief* onto an existing file
  merges instead of overwriting — today that overwrite silently loses the
  fork's other branch. (c) An explicit **Update project brief** control for
  the browser and for a section opened outside its folder.
- **Pull, the reverse direction.** `POST /api/project/pull` merges the
  brief's newer facts, rounds and references INTO the open section (the
  assets with append semantics); profile and edition differences are
  *reported*, never applied — the document is the section's own. Offered by
  the Project panel when the brief is newer than the session's
  `project_link.brief_updated_at`, and once on open.
- Tests: the two additive research fields round-tripping and a legacy
  profile's bytes unchanged (plus the QC research fingerprint over them);
  the fork scenario end to end (S1 rounds 1–2 → seed S2 → both add facts,
  a reference and a round on the same day → merge = union, no duplicate
  rounds, rids and pids re-minted with every `source_ref` and
  `superseded_by` link intact, the existing section's own rids untouched,
  supersedes win, a same-statement scope conflict resolved to the wider
  scope with the loser superseded and the manifest warning, readiness
  unchanged); idempotence; the overwrite that can no longer lose a
  branch; pull applying only the three
  append assets; the save that reports a merge failure and still writes the
  `.baspec`.

### Phase 4 — Harvest: nothing settled is left in the transcript

*Depends on D3. Paid, so gated exactly like AI-generalize: preview, then
commit.*

- **`POST /api/project/facts/harvest`** runs ONE model call (Sonnet 5,
  `HARVEST_EFFORT` medium, a strict `propose_project_facts` output tool —
  the `template_document_tool` precedent) over: the transcript's user and
  assistant **text** only (tool blocks, thinking and elided PDF payloads
  stripped — committed history is already PDF-free), the outline with
  statuses so confirmed provisions are evidence, Final QC dismissal reasons,
  and the CURRENT facts, profile, identity and standards blocks so it
  proposes only what is new. Each proposal carries scope, status, the
  evidence's real source kind and ref, and the quoted line it rests on.
  Not a chat turn: history is untouched.
- **Review sheet** in the Project facts panel: accept / edit / reject per
  proposal; commit through one new batch form of the existing facts route
  (all-or-nothing, the store's `apply()` contract). A proposal that
  duplicates an active fact is dropped before it is shown.
- **A source reference is RESOLVED, not just length-bounded.** Today
  `ProjectFactStore.record()` only normalizes and bounds `source_ref`;
  nothing checks that it names anything, so a harvested proposal citing a
  research item, reference or QC finding that does not exist would commit
  with fabricated provenance. Phase 4 adds `resolve_fact_source(kind, ref,
  session)`: `research` → an `r-…` item present in the profile;
  `reference` → a `ref-…` in the store; `qc` → a finding id in the retained
  result (survivors and disputed, the `QCResult.finding()` set); `user` and
  `model` → a transcript locator the harvest itself minted (`turn:N`, the
  assistant-bubble ordinal the follow-ups store already counts) or empty;
  `brief` → a section number in the link's registry. The harvest commit
  REFUSES an unresolvable ref; the chat tool returns it as an `is_error`
  result the model corrects (the `apply_spec_edits` posture, and a small
  behaviour change to state in the release note); the panel form shows the
  validation message. A fact that already carries an unresolvable ref
  (recorded before this phase, or carried in from a brief) is left alone
  and marked in the panel, never rewritten.
- **Offered, never forced**: a *Harvest facts…* button in the panel, and a
  one-line offer in the Next-section and Export-brief flows when there are
  assistant replies since the last harvest ("14 replies since facts were
  last harvested"). `SessionState.last_harvest_bubble` is the marker; it
  persists in the `.baspec`.
- Metered under `interview` at Sonnet rates; the trust dossier's Project
  facts card and the "no model runs on its own" scoping gain the harvest
  line, because the dossier is a contract.
- Tests (hermetic, `tests/fakes.py`): the request carries text only, the
  strict tool, and the current facts; a duplicate proposal is dropped; each
  source kind resolving and refusing (a missing research item, reference,
  QC finding and transcript turn), the tool's `is_error` self-correction
  and the panel message; commit is one batch and a bad payload rolls it
  back; the marker advances only on commit; readiness and QC staleness
  refresh after commit (facts are a hashed QC input).

### Phase 5 — Later sections stay cheap: measure, then trim by relevance

*Measure first — the deferred-item posture `docs/review-results/2026-09-09/
EXECUTION_RECORD.md` already sets for cache work.*

- **Measure** (free): the `round_end` trace event and `/api/diagnostics`
  session block record the rendered sizes of the research, facts and
  sections blocks per turn. One real second-section session on a hyperscale
  project decides whether the rest of this phase is worth building; the
  threshold is a research block that routinely renders past ~40k estimated
  tokens on a section that uses a fraction of it.
- **If it earns it**: keep the whole profile in state (nothing dropped),
  render the block **relevance-first** — dimension weight for the section
  (site and governing codes always lead; client standards always lead),
  then term overlap between an item's category/requirement and the section
  number, title, article titles and catalog scope — with the existing trim
  becoming relevance-then-confidence and a disclosed "N more items held
  back; ask for a category to expand it" line, plus a `read_research_items`
  chat tool in the `read_reference_doc` shape. Tool additions change the
  cached tool prefix once; that is expected.
- Tests: the ranking is deterministic and section-sensitive; a single-section
  session renders byte-identically to today when nothing is held back; the
  held-back count is disclosed; the tool result is elided from committed
  history.

### Phase 6 (optional, D5) — Client library across projects

- `scope: "client"` on facts, bound to `profile.client_name`; a *Start a new
  project for this client* seed that carries only client-scoped assets: the
  client name, client-scoped facts, `client_standards` research items, and
  references flagged as client standards. Everything jurisdiction- or
  site-specific stays behind, and the manifest card says so.
- The small addition it is once Phase 3's merge exists; not worth designing
  further until a second project for the same client is actually started.

### Not in scope, and why

- Carrying the transcript or the document in any form — the constraint that
  shaped v1.17.0 stands; a model summary of a session is an unverifiable
  paragraph the next session treats as fact.
- A cross-section coordination QC lens — a real idea (does 21 30 00 respect
  what 21 13 13 specified?), but it is a Final QC change, not a carry-over
  change, and it needs the Project panel and merge to have something to read.
- Carrying Final QC results or dismissals — they are about one section's
  provisions.

### Order and sizing

| Phase | Depends on | Size | Value |
|---|---|---|---|
| 1 Next section in one click | — | small | daily |
| 2 Project folder + panel | D1 | medium | the "swap in" the ask names |
| 3 Write-back merge + pull | 2 (auto trigger) | medium, mostly pure code | closes the fork |
| 4 Harvest | D3 | medium | closes the capture gap |
| 5 Relevance trim | a measurement | small→medium | cost on section 4+ |
| 6 Client library | 3, D5 | small | optional |

Phases 1 and 4 can be built in parallel; 2 → 3 is a chain. Each lands as its
own PR with its own release-note item, `README.md` and `CLAUDE.md` updates,
and the capability-contract edits `npm test` enforces. `requirements.txt` is
untouched by any phase as designed (no new dependency).
