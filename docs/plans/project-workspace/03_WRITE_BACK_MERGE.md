# Phase 3 — Write-back: the brief is a living file, the merge is append-only

**Status:** in review (PR pending). **Depends on:** Phase 2 (complete, PR #176) for the automatic
save-time trigger and the panel's affordances (`project_home`). The merge
itself (3.1–3.4) is pure and may be built and shipped first behind the
explicit *Update project brief* action and the export-onto-existing-file
path, with 3.5's save-time trigger landing once a home exists.

D2 and D4 are ratified: refresh on every save, silently, as an append-only
merge; newest export wins for the profile; an edition-override disagreement
is a warning AND a project fact.

## Goal

Today a seeded section is a **fork**. Facts, rounds and references added in
21 30 00 reach 21 40 00 only if the user exports from 21 30 00; anything
settled in 21 13 13 after 21 30 00 was seeded never reaches 21 30 00. After
this phase: saving a section refreshes the project brief with an
append-only, id-joined, idempotent merge; exporting a brief onto an
existing file merges instead of overwriting; and a section can PULL what
its siblings added since it was seeded. Nothing a merge touches is ever
deleted.

## Current code (verified 2026-09-22) — and the four identities Codex found missing

1. **A research round has no stable identity.** `ResearchRound`
   (`backend/research/engine.py`) serializes `round_index`, `research_date`,
   `dimension_statuses`, `new_items`, `repeat_items` and `section` (only when
   set). Item ids live cumulatively on `RequirementsProfile.items`; an
   item's `round_index` is the round that FIRST found it, so a re-confirmed
   item cannot be attributed to a later round from the record alone.
   `append_research_round(previous, fresh, *, section)` is the one merge:
   items join on `item_id` (a content hash of dimension+category+
   requirement) through `_confirm_item` (citations union, grounded OR,
   confidence max, evidence-dated); the round record is REBUILT there.
2. **`ref-N` is a per-session counter.** `ReferenceDocStore.add` mints
   `ref-{_next_seq}`; `get(rid)` returns the first match; `load()` derives
   `_next_seq` from the max restored tail. Two forks each mint `ref-3` for
   different documents.
3. **`pf-N` is a per-session counter and the duplicate key is the statement
   alone.** `ProjectFactStore.record` refuses a second ACTIVE fact whose
   `_match_key(statement)` matches, whatever its scope or bound discipline;
   `supersede` sets `status="superseded"`, `superseded_by`, `supersede_reason`;
   `load()` keeps pids as given and derives `_next_seq`. `ProjectFact` carries
   `source_kind` ∈ `FACT_SOURCE_KINDS` (`brief` accepted on load only, reserved
   for exactly this phase), `source_ref`, `recorded_in`, `recorded_at`.
4. `project_brief.build_project_brief` upserts THIS section's registry
   record by number; `parse_project_brief` rebounds references and drops
   what it cannot read with a warning; `brief_manifest` renders counts.
   `main.py`'s `save_project_brief` fetches `GET /api/project/brief` and
   writes the bytes to the chosen path — **overwriting** an existing file.
   `_CloseController._atomic_write_target(path, payload, prefix=)` is the
   atomic write; the template catalog has the same idiom.
5. `ResearchRunner.restore(profile)` installs a profile as complete;
   `ReferenceDocStore.add(...)` and `ProjectFactStore.apply(...)` are the
   append entry points; `project_link.brief_updated_at` is the session's
   memory of the brief it last agreed with.

## Design

### 3.1 Round identity — two additive fields, serialized only when set

```python
# ResearchRound (append AFTER `section`; positional construction must keep working)
round_id: str = ""          # uuid4().hex, minted at the round's birth
item_ids: list[str] = field(default_factory=list)  # this round's own membership
```

- `run_requirements_research` mints `round_id` into the round it stamps at
  birth (the same place it stamps `section`); `append_research_round`
  carries an existing `round_id` over (the `section` rule) and fills
  `item_ids` with every `item_id` in `fresh.items` — new AND re-confirmed —
  which is what makes a round replayable (3.3).
- `to_dict` emits `round_id` / `item_ids` only when non-empty; `from_dict`
  reads them leniently (`_as_list`, strings only). A legacy profile's bytes
  and the QC research fingerprint over them are untouched (pinned). A new
  round's bytes change, and a retained Final QC result that predates it
  reads stale — correct, a new round is a new input.
- The findings report gains nothing visible; `round_id` is plumbing.

### 3.2 `merge_project_brief(existing, incoming) -> tuple[ProjectBrief, MergeReport]`

`backend/project_brief.py`, pure, deterministic, idempotent
(`merge(merge(a, b), b) == merge(a, b)` by value). `MergeReport` is a
dataclass the routes serialize: counts per asset (added / confirmed /
re-minted / folded), the warnings, and `conflicts` (D4).

- **Identity:** both briefs must share `project_id`, else
  `ProjectBriefError` ("different projects") — the caller decides what to do
  (the shell asks; the save-time trigger reports and does not write).
- **Research** (3.3): `merge_research_profiles(base, incoming)`.
- **Facts** (3.4): `merge_facts(base, incoming, *, incoming_section)`.
- **References:** identity is `content_fingerprint` (the brief already
  carries one per document; a fingerprint over `text` for a brief that
  predates it). A document already present keeps its EXISTING rid. A new
  document whose rid collides with an existing one is re-minted past the
  existing max tail; the rid map feeds 3.4 so an incoming fact's `ref-N`
  `source_ref` is rewritten. The cap (`MAX_REFERENCE_DOCS` /
  `MAX_REFERENCE_TOKENS`, `within_reference_cap`) applies to the merged
  list; drops are NAMED in the report. A merged store with a duplicate rid
  is a `ProjectBriefError` — reported, never written.
- **Profile:** newest `updated_at` wins whole (D4); a field-level
  difference is a warning naming both values.
- **Edition overrides:** per standard, the newest export's entry wins; a
  disagreement (different `edition` for one standard) is a warning AND a
  project fact (3.4 appends it): scope `project`, status `assumed`,
  `source_kind="brief"`, `source_ref` naming both sections, statement
  "Sections disagree on the <standard> edition: <section A> records <ed>
  (<basis>); <section B> records <ed> (<basis>). Resolve before issue."
  Idempotent because the statement is the duplicate key.
- **Sections registry:** by number, newest `exported_at` wins; order is
  export order (existing first, incoming records appended, re-exports moved
  to the end — `build_project_brief`'s rule).
- `created_at` = min, `updated_at` = now, `app_version` = this build.

### 3.3 Research merge = replay the unseen rounds through `append_research_round`

```python
def merge_research_profiles(base, incoming) -> RequirementsProfile
```

- Rounds are identified by `round_id`. A legacy round without one is keyed
  on `(section, research_date, round_index)` and the report says so.
- For every incoming round not in `base`, in incoming order: build a
  one-round `RequirementsProfile` from that round's `item_ids` (the
  incoming profile's items, filtered) plus its own statuses, date, project
  and section, and call `append_research_round(acc, that)`. This REUSES the
  existing item-level rules (confirm in place, citations union, evidence
  dating) rather than restating them; the cumulative statuses fall out of
  `_accumulate_statuses` as they do for any later round. Round indexes are
  renumbered in the merged order; `round_id` is carried.
- A legacy round without `item_ids` replays with the incoming items whose
  `round_index` equals its index (first-found attribution only; re-confirmed
  membership is unknowable) and the report notes it.
- The merged profile's `project` is the newest round's (the existing rule).

### 3.4 Facts merge — the statement is the key, scope conflicts resolve, ids re-mint

```python
def merge_facts(base, incoming, *, rid_map, section_of_incoming) -> tuple[list[dict], FactsMergeReport]
```

Working on the serialized snapshots (`ProjectFactStore.snapshot()` shape):

- **Key:** `_match_key(statement)` — the store's own, scope-blind, so a
  merged store never holds a state `record()` would refuse (Codex, PR
  #173). `_match_key` becomes public as `fact_match_key`.
- **Incoming fact whose statement matches an existing ACTIVE fact:**
  - both active, same scope → confirmed in place; `detail` fills if blank;
    `status` upgrades `assumed → confirmed` never the reverse; nothing else
    changes (the existing fact keeps its pid and provenance).
  - both active, different scope → the WIDER scope is retained
    (`project > discipline > section`; tie → the earlier `recorded_at`),
    the other is folded in as `superseded` with
    `supersede_reason = "Merged: the same fact was recorded at <scope> by
    <section>"` and `superseded_by` → the retained pid; the report's
    `conflicts` names it.
  - incoming is superseded, existing is active → the existing becomes
    superseded with the incoming's reason (a supersede on either side is
    terminal); if the incoming has a replacement, that replacement lands as
    a new fact and the link is rewritten to its new pid.
- **Incoming fact with no match:** re-minted `pf-{next}` past the merged
  max tail; `superseded_by` links among incoming facts are rewritten
  through the pid map; `source_ref` values of kind `reference` are rewritten
  through `rid_map` (3.2).
- **Provenance is kept, never restamped.** `source_kind`, `source_ref`,
  `recorded_in`, `recorded_at` travel as recorded. `source_kind="brief"` —
  the reserved value — is used in exactly two cases: (a) the D4 conflict
  fact above, and (b) a merged fact whose `source_ref` can no longer resolve
  after the merge (a `ref-N` dropped at the cap; an `r-…` item not in the
  merged profile — cannot happen through 3.3, but a hand-edited brief can
  say anything): the ref is cleared, the kind becomes `brief`, and
  `source_ref` names the recording section, so the fact stays attributed
  without claiming a resolvable source it lacks. (This corrects the plan
  file's Phase 3 sentence that stamped every pulled fact `brief`; a pulled
  fact recorded by a user in 21 13 13 is still a user fact.)
- The active-fact cap (`MAX_ACTIVE_FACTS`) applies to the merged list; past
  it the merge REFUSES with a report rather than dropping (a ledger nobody
  can read is worse than no ledger, and silently dropping is deletion).

### 3.5 When it runs — three triggers, one implementation

- **`POST /api/project/brief/merge`** (multipart `file` = an existing
  brief's bytes) → `{ok, brief: <bytes as attachment>, report}`: builds THIS
  session's brief under the guard (`_build_brief_locked`), parses the
  upload on a worker thread, merges, returns the merged bytes. Used by the
  shell's `save_project_brief` when the chosen path already exists: read
  it, POST it, write the answer — export-onto-existing-file merges instead
  of overwriting. A different `project_id` → 409 `different_project` and the
  shell asks "Replace the other project's brief?" (only then does it
  overwrite).
- **`POST /api/project/brief/refresh`** (no body) → `{ok, report,
  brief_updated_at}`: requires `project_home`; reads
  `project_home["brief_path"]` on a worker thread, merges with this
  session's brief, writes back atomically (the shell's
  `_atomic_write_target` idiom, moved into `backend/project_brief.py` as
  `write_brief_atomically(path, payload)` so both callers share it), and
  updates `project_link.brief_updated_at`. Refuses in a tour and while
  `busy_reasons` (a merge reads the session; it must not race a commit).
  Called (a) by the shell at the end of a successful `_save_project_file`
  when a home is known — silently; a failure is reported in the save result
  (`brief_refreshed`, `brief_error`) and never fails the save; (b) by the
  panel's **Update project brief** button (Phase 2's panel; capability
  `project.brief-refresh`).
- **`POST /api/project/pull`** (no body) → `{ok, report, session bundle}`:
  the reverse direction. Reads the home brief, merges it INTO the session:
  `research.restore(merged_profile)` (rounds appended, the runner's
  restore), `references.add(...)` for new documents (rid re-mint is
  automatic — the store mints), `facts` through a new
  `ProjectFactStore.absorb(merged_snapshot)` that replaces the list with
  the merged one under the turn-atomic contract (refused while a turn owns
  the store, 409 `turn_active`). Profile and edition differences are
  REPORTED, never applied — the document is the section's own; the report
  names them so the user can act. Refuses in a tour and while busy. Updates
  `brief_updated_at`. Panel affordance: "Project has changes since this
  section synced — Pull" when the on-disk brief's `updated_at` is newer than
  the link's `brief_updated_at`.

### 3.6 What stays out

- No background watcher, no periodic refresh: the three triggers are all
  user actions or a save.
- No merge of the document, the transcript, figures, QC, follow-ups or
  suppressed standards — none of them travel.
- No re-attach of a rid the OTHER section's document cites: a
  `source_item_id` chip belongs to that document and is not carried.

## Files

- `backend/research/engine.py` — `ResearchRound.round_id` / `item_ids`;
  minting in `run_requirements_research`; carry in `append_research_round`;
  `merge_research_profiles`.
- `backend/project_facts.py` — `fact_match_key` (public), `merge_facts`,
  `ProjectFactStore.absorb`.
- `backend/project_brief.py` — `MergeReport`, `merge_project_brief`,
  `write_brief_atomically`, reference re-minting.
- `backend/app.py` — the three routes; `brief_updated_at` bookkeeping.
- `backend/sessions.py` — nothing new beyond Phase 2's home.
- `main.py` — `save_project_brief` merges onto an existing file; the
  refresh call after a successful save.
- Frontend: panel buttons **Update project brief** and **Pull project
  changes** (`ProjectPanel.tsx`), `api.ts` (`refreshProjectBrief`,
  `pullProject`), `types.ts` (`MergeReport`), `capabilities.ts`
  (`project.brief-refresh`, `project.pull`), the Phase 2 tour step's
  capability list gains both (one step, several controls), `HelpModal.tsx`
  recipe, `TrustDeepDiveModal.tsx` brief card (the file is now written on
  save).

## Tests

`tests/test_brief_merge.py` (new):

- Round identity: `test_round_id_and_item_ids_round_trip_and_legacy_bytes_are_untouched`
  (a legacy profile serializes byte-identically; the QC research
  fingerprint over it is unchanged), `test_a_new_round_carries_its_id_through_append`.
- Research replay: `test_unseen_rounds_replay_through_append_research_round`
  (S1 rounds 1–2, S2 seeded with both, S2 adds round 3, S1 adds round 3 the
  SAME DAY: merge holds four rounds, renumbered, no duplicates; a
  re-confirmed item is confirmed in place with citations unioned),
  `test_a_legacy_round_without_membership_replays_first_found_items_and_says_so`.
- Facts: `test_the_statement_is_the_key_and_scope_conflicts_resolve_to_the_wider`,
  `test_a_supersede_on_either_side_is_terminal`,
  `test_incoming_pids_are_re_minted_and_links_rewritten`,
  `test_provenance_travels_and_brief_is_used_only_when_a_ref_cannot_resolve`,
  `test_the_cap_refuses_rather_than_drops`.
- References: `test_a_present_document_keeps_its_rid_and_a_new_colliding_one_is_re_minted`
  (with the fact `source_ref` rewritten), `test_a_duplicate_rid_is_a_merge_failure_never_written`,
  `test_cap_drops_are_named`.
- D4: `test_an_override_disagreement_is_a_warning_and_a_fact`,
  `test_newest_export_wins_the_profile_with_a_field_warning`.
- Whole: `test_merge_is_idempotent`, `test_different_projects_refuse`.
- Routes: `test_merge_route_returns_merged_bytes_and_a_report`,
  `test_refresh_writes_the_home_brief_atomically_and_updates_the_link`
  (tmp_path home; a failed write leaves the old file byte-identical),
  `test_refresh_and_pull_refuse_busy_tour_and_no_home`,
  `test_pull_installs_only_the_three_append_assets_and_reports_the_rest`
  (profile/override differences reported, document untouched, readiness
  unchanged, QC reads stale because facts moved), `test_export_onto_an_existing_brief_merges`
  (`tests/test_close_prompt.py`, the shell path: a fork's other branch
  survives; a different project asks).
- `tests/test_facts_agent_visibility.py` / `test_qc_manifest_integrity.py`
  — a pull that changes facts flips the retained QC result stale (the
  existing hash covers it; assert it once).

Frontend: source-level pins that both buttons declare their capabilities,
are hidden in a tour, disabled while busy, and that Pull refreshes readiness
and QC state after success (`refreshReadiness()` / `refreshQc()` calls).

## Docs

- `README.md`: the "Project workspace (in progress)" subsection — a save
  refreshes the brief; export merges; Pull; what is never merged.
- `CLAUDE.md`: implemented notes ("The brief is a living file") — round
  identity, the replay-through-append design, the statement key and scope
  rule, rid/pid re-minting, when `brief` is used, the three triggers; an
  errata bullet correcting the plan file's "every pulled fact is stamped
  brief" sentence. Layout entries for `engine.py`, `project_facts.py`,
  `project_brief.py`, `app.py`, `main.py`.
- `docs/RELEASE_WINDOWS.md`: QA rows — save twice from two sections and
  read the brief; export onto the existing file; Pull into the older
  section; a hand-edited brief with a bad ref.

## Release-note draft (Phase 7 copies from here)

- **The project brief stays current on its own.** Saving a section now
  refreshes the project's brief beside it, adding what the section
  established — research rounds, references, facts — without removing
  anything another section recorded. A save that adds nothing leaves the
  brief untouched, and a brief that cannot be updated never stops the save
  (it says why instead). **Update project brief** in the Project panel does
  the same on demand.
- **Exporting onto an existing brief merges.** Picking the project's
  existing `.basproject` keeps everything already in it; picking another
  project's brief (or a file that is not one) asks before replacing it.
- **Pull what your other sections learned.** When a section's siblings have
  added work it lacks, the Project panel says so and offers **Pull project
  changes**: their facts, research rounds and reference documents come in,
  nothing is removed, and differences in the project setup (a city, a
  client, an edition) are shown, not applied.
- **Two sections that disagree on an edition are told so.** A conflict in a
  recorded edition between sections becomes a project fact to resolve, not
  a silent choice.
- **An edited project fact keeps its identity.** Each fact now carries its
  own id, so a fact edited in one section is recognised in another, and the
  later edit wins.

## Deviations from the plan

(Record each as-built deviation here, dated, with the reason. Already
known: the plan file's Phase 3 stamps every pulled fact `source_kind="brief"`;
this spec keeps provenance and uses `brief` only for the D4 conflict fact
and an unresolvable ref — see 3.4.)

As built, 2026-09-22:

1. **Facts carry an identity (`uid`) and an edit stamp (`edited_at`)**
   beyond the spec's statement key. The panel's Edit changes a fact's
   statement, so the statement alone cannot recognise an edited fact across
   a fork — it would land as a second active fact beside its older self.
   `record()` mints the uid, `update()` keeps it and stamps `edited_at`;
   both are serialized only when set. The merge takes a twin by uid first
   (for a fact recorded before uids: statement + placement + where and when
   recorded), a retirement on either side stays terminal, and between two
   live copies the later edit wins. The statement key then applies exactly
   as specified to everything that is not a twin.
2. **The research merge reconciles two things after the replay.** Replaying
   unseen rounds through `append_research_round` assumes the fresh round is
   the newer one; a fork's rounds interleave in time, so a sequential
   replay can mis-date an item's evidence and pick the wrong "latest"
   error. After the replay, an item held on both sides is dated by the
   symmetric rule (latest grounding, else earliest report), and each
   dimension's error, the profile date and its project are recomputed from
   the merged rounds by date. The item-level join itself is untouched.
3. **A legacy round's key is carried as its `round_id`.** The spec keys a
   round with no id on `(section, research_date, round_index)`; an index
   changes the moment a merge renumbers the round, so the key is hashed
   into a round-id-shaped string and carried as the replayed round's id —
   the next merge still recognises it.
4. **The profile merges field by field.** "Newest `updated_at` wins whole"
   would let a section with a blank field erase the project's value (a
   deletion). Each field takes the newest export's value where it records
   one and the older value otherwise; every difference is still reported
   naming both.
5. **The D4 conflict statement is direction-independent.** The spec's
   template names both sections in the statement. The brief does not
   record which section set its override, a statement is bounded at 240
   characters (bases are free text), and — the decisive one — with "newest
   export wins" the brief's edition flips on each section's save, so a
   directional statement recorded ONE disagreement as TWO facts. The
   statement names the sorted editions ("… 2022 and 2025 are both recorded.
   Resolve before issue."); who holds which, and each basis, ride in
   `detail`; the uid derives from the statement.
6. **The reference cap applies to what a merge ADDS.** Applying the cap to
   the whole merged list could drop a document the extended side already
   held (a legacy file past the token cap) — a deletion. Held documents are
   always kept; new ones land while they fit; the rest are named.
7. **Duplicate rids are refused on either side**, not only in the merged
   store: on a pull the file is the incoming side, and two documents under
   one id would point a fact at whichever the rid map saw first.
8. **A pull installs references through `ReferenceDocStore.load`**, not
   `add()`: the merge already minted new rids past the store's counter
   (`reference_mint_floor = next_seq`, a new read-only property) and
   rewrote the facts' refs to them; `add()` would re-prepare the text,
   re-stamp it and mint again. Facts install first (`absorb` is the one
   install that can refuse), so a refused pull changes nothing.
9. **The pull answers `{ok, report, installed, …doc payload}`**, not a
   session bundle — a bundle would reset client state (the transcript
   view, the panes) that a pull never touches; the client re-reads
   research, Final QC and readiness instead.
10. **Pull availability is a dry run.** The spec offered the pull when the
    file's `updated_at` is newer than the link's `brief_updated_at`; a
    sibling's save rewrites its registry record without adding an asset,
    so that rule would offer empty pulls. `brief_updated_at` now moves only
    when the section holds everything the file holds, equality of the two
    stamps short-circuits, and anything else runs the pull as a dry merge
    (`GET /api/project/sections` gains `pull_available` + `pull_summary`).
11. **Brief timestamps have microsecond resolution.** Found by a smoke run:
    a sibling's refresh landing in the same second as this section's sync
    produced the same seconds-resolution `updated_at`, and the pull offer
    was silently withheld. The ISO form still sorts chronologically against
    an older build's stamps; the frontend reads only the date.
12. **A registry record that moved only its `exported_at` is not news.**
    Every save rebuilds the section's record with a fresh stamp; without
    this rule every save would rewrite (and reorder) the brief. A save that
    brings nothing new leaves the file byte-identical.
13. **The shell's save-time refresh calls `refresh_project_brief`
    directly**, not over HTTP: the same function the route runs, no token,
    and it works in a session with no backend runtime. An in-process lock
    serializes read-merge-write — the export-onto-existing path holds it
    too, but asks its "Replace the file?" question with it released; there
    is no cross-process lock (two app instances on one folder: the later
    write wins; a torn file is impossible).
14. **The merge route answers the brief as text in JSON** (`brief`, plus
    `filename`, `report`, `pull_available`) rather than as an attachment:
    the shell needs the report with the bytes in one response.
15. **A write failure is a 500 `write_failed`**, not a 409 — an
    environment failure, not a state conflict.
16. **Carried research stays disclosed after a pull.** The rounds a pull
    installs grow `research_rounds_at_seed` (so the registry keeps the
    section's own count), and the readiness disclosure also names the
    sections a carried round's stamp records — so it appears for a
    pulled-into section that was never seeded.
17. **Test placement.** The "a pull that changes facts flips the retained
    QC result stale" assertion lives in the pull route test
    (`tests/test_brief_merge.py`), asserted once where the pull happens,
    rather than in `test_facts_agent_visibility.py` /
    `test_qc_manifest_integrity.py`. Writing it exposed that
    `tests/fakes.audit_grade_qc_result` built its manifest without the
    effective discipline and the attached documents — stale from birth for
    any rich session — and it was corrected.
18. **Two knowing test updates outside the new file**: the tutorial's facts
    fixture takes deterministic uids (a bundled fixture must not mint random
    ones), and `test_next_section.py`'s state-for-state projection drops the
    per-record uid as it already drops the per-export project id.
19. **Limits recorded, not solved.** Deleting a reference document in one
    section does not remove it from the brief, and a later pull offers it
    back (append-only; retiring a fact is the travelling way to withdraw
    it). A browser (dev) export stays a plain download — a browser cannot
    read the destination file to merge into it.
