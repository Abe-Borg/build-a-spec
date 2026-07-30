# Phase 3 — Research and QC truthfulness

- Status: in progress (3.1 and 3.2 complete; 3.3 planned)
- Prerequisites: Phases 1 and 2 complete
- Risk: high; this phase changes issue-readiness and the audit report's claims

## Goal

Represent incomplete research as first-class evidence. The drafting model,
readiness checklist, Final QC manifest, in-app report, JSON, and Word report
must distinguish “this dimension completed and found nothing” from “this
dimension never completed.” Required coverage must block issue readiness while
the runner's cumulative partial-success/retry behavior remains intact.

## Final product rule

- Runner `complete` means at least one dimension completed in the latest round.
- Profile dimension statuses are cumulative: a dimension completed in any
  earlier round remains completed.
- `ResearchDimension.required` defines issue-critical coverage, and it
  **defaults to required**. Every dimension in both shipped modules is
  required: on the projects these modules serve, AHJ requirements, owner and
  insurer standards, and site/seismic/freeze factors are not inherently
  optional coverage any more than governing codes are. A module may declare a
  dimension optional only explicitly, with a machine-readable rationale
  (`optional_rationale`) that registry validation enforces — there is no
  silent fail-open default.
- Readiness is green only when runner state is complete, a coherent profile is
  present, and every required dimension has cumulative status `completed`.
- Optional incomplete dimensions are named in a passing warning. A reviewer
  waiver flow for missing optional coverage is a possible future enhancement;
  it is out of scope for this program, and truthful surfacing ships first.
- Malformed or structurally invalid research state fails **closed for
  readiness** (research is not complete) while remaining **open for project
  loading** (a corrupt optional structure never blocks a project from
  opening) — the codebase's existing lenient-loader posture.
- Pressing Research again remains the remediation path.

## Chunk 3.1 — Name incomplete coverage and persist manifest facts

### Implementation

1. In `RequirementsProfile.render_text`:
   - find cumulative statuses whose status is not `completed`;
   - when none exist, preserve the existing rendered text exactly;
   - otherwise add an unmistakable header line using each
     `DimensionStatus.title`, for example:
     `INCOMPLETE COVERAGE: research for ... never completed. Findings from
     these areas are ABSENT, not verified-empty; do not treat them as
     researched.`;
   - do not render raw provider error strings into the prompt; titles and the
     semantic absence warning are sufficient; and
   - keep deterministic module declaration order.
2. Ensure `research_context_block` trimming cannot remove the incomplete-
   coverage header. It is provenance/control text, not a low-confidence item.
3. Expand `build_qc_input_manifest`'s `requirements_research` record with
   serialized facts needed by report projections without live state:
   - total dimension count;
   - completed dimension ids/titles;
   - failed dimension ids/titles;
   - and, after Chunk 3.2, required and incomplete-required ids/titles.
   Use deterministic lists in module/status order.
4. Keep the profile's existing full-data fingerprint. Adding manifest fields
   intentionally changes the full QC input fingerprint for new runs; do not
   mutate an already persisted report.
5. Make partial failure observable in diagnostics, not only in prompts and
   reports: include the failed dimension ids/titles and a sanitized error kind
   (never raw provider payloads or key material) in the research trace span
   close and in the `/api/diagnostics` snapshot's session block, so a support
   bundle answers "which coverage failed" without opening a project.
6. Add tests for fully complete byte identity, one incomplete dimension,
   multiple incomplete dimensions, cumulative completion after a later retry,
   QC lens prompt inclusion, and the diagnostics snapshot facts.

### Files

- `backend/research/engine.py`
- `backend/research/schema.py` only if context trimming requires a named
  protected-prefix seam
- `backend/qc/engine.py`
- `backend/research/runner.py` and/or `backend/tracing/capture.py` (span-close
  facts)
- `backend/diagnostics.py` (snapshot facts)
- `tests/test_research_engine.py`
- `tests/test_research_rounds.py`
- `tests/test_qc_manifest_integrity.py`
- `tests/test_qc_audit_report.py`
- `tests/test_diagnostics.py`
- `CLAUDE.md`

### Focused verification

```powershell
venv\Scripts\python -m pytest -q tests/test_research_engine.py tests/test_research_rounds.py tests/test_qc_manifest_integrity.py tests/test_qc_audit_report.py
```

### Acceptance criteria

- A complete profile renders exactly as before.
- A partial profile names missing coverage and says it is absent, not empty.
- Every QC lens receives that warning through `_render_profile`.
- A new QC result persists enough ordered facts for UI/Word limitations without
  consulting the current session.

### Implementation record

- Status: **complete** (2026-07-30)
- Commit/PR: `4d3c0f2` — PR #96
- Tests: 21 new (18, plus 3 from the PR #96 review), spread over the plan's
  named files plus `test_tracing.py`
  (the only place the trace half of step 5 is observable).
  `tests/test_research_engine.py` (8): a fully complete profile renders
  byte-identically (the provenance line followed immediately by both marker
  legends, nothing spliced between); one never-completed dimension named as
  ABSENT-not-empty with singular phrasing and no completed dimension leaking
  into the warning; several named in module order with plural phrasing; the
  title falling back to the id; the raw provider error never reaching the
  drafting context; trimming that cannot drop the warning even when items
  are dropped; the sanitized kind recorded per failure mode
  (`incomplete_response` / `no_payload` / a raised error's failure class /
  empty for a success) with every kind inside `DIMENSION_ERROR_KINDS`; and
  the facts projection's order and `None` handling.
  `tests/test_research_rounds.py` (3): a later round that covers the gap
  retires the warning while the round's own fresh failure stays recorded
  (and the reverse — a dimension that completed earlier does not
  reintroduce a warning), a serialization round trip, and a profile saved
  before `error_kind` existed reading as an empty kind.
  `tests/test_qc_manifest_integrity.py` (4): the manifest naming which
  coverage failed rather than only how many (module order, disjoint sets),
  an absent profile recording empty coverage rather than omitting the keys,
  partial coverage changing the full input identity, and every lens being
  told.
  `tests/test_diagnostics.py` (2) and `tests/test_tracing.py` (1): the
  snapshot's `session.research` record and the span's
  `incomplete_dimensions`, both asserting the provider payload is absent
  from the response/file text, plus the clean-run cases (empty coverage;
  the span key absent rather than an empty list).
  Focused run green (research engine / rounds / manifest integrity / audit
  report / diagnostics / tracing — 114 passed); full gate green:
  `pytest -q` **1199 passed, 9 skipped** (was 1178/9), `npm test` 143
  passed and `npm run build` clean (no frontend change — regression check).
  Both mechanisms were reverted in place to prove them load-bearing:
  removing the warning interpolation turns 7 red, removing `error_kind`
  from the cumulative merge turns 1 red.
- Deviations:
  - **`DimensionStatus.error_kind` is a new serialized field, which the plan
    did not call for.** Step 5 asks for "a sanitized error kind" in the
    trace and the diagnostics snapshot, and the only alternative was to
    reverse-engineer one from the stored English message — brittle against
    any future reword, and silently degrading to a useless bucket. `_failed`
    is a single choke point, so recording the kind at the failure site cost
    8 call sites and is exact. A raised provider error reports its
    `retry_policy.FailureClass` value rather than a new token: that enum is
    already closed and str-valued "for cheap telemetry" per its own
    docstring, so a support bundle reads `rate_limit` instead of
    "something raised". `_statuses_from_raw` defaults it empty, so older
    files load unchanged (pinned).
  - **The manifest uses one `dimension_titles` MAP plus two id lists**, not
    parallel id/title arrays. Step 3 says "completed dimension ids/titles";
    parallel arrays can fall out of alignment, and 3.2 validates the id
    lists structurally (unique, disjoint, resolvable against the module),
    which wants ids as the machine identity with naming kept separate.
  - **`research_manifest_facts` is extracted as its own function** rather
    than staying an inline dict in `build_qc_input_manifest`. 3.2 extends it
    with required-policy ids and 3.3 reads it from two projections, so it
    needed a name and a docstring stating why counts are insufficient.
  - **Step 2 needed no code, and the test says why.** The warning renders in
    the header, and `research_context_block` trims by dropping whole ITEMS
    and re-rendering — so the header is unreachable by the trimmer by
    construction. Rather than add a "protected prefix" seam the plan left
    optional, the invariant is pinned by a test that trims a profile until
    items are dropped and requires the warning to survive; the comment at
    the render site states the reason so a future refactor that moves the
    warning into an item has to argue with it.
  - **The warning tells the model what to do, not the user.** The plan's
    example wording ends at "do not treat them as researched"; a clause was
    added asking it to say so where a provision would depend on the missing
    area, since the model cannot press Research and silence is the failure
    mode this chunk exists to remove. "Press Research again" copy belongs to
    the readiness detail in 3.2.
  - **`tests/test_qc_audit_report.py` is untouched.** The plan lists it, but
    3.1 only captures the facts; rendering them into the Word/JSON/UI
    limitations is 3.3's job, and that file owns the projections. Its
    existing tests are part of the focused run as a regression check.
  - **The lens test asserts the SHARED prefix**, not a loop over per-lens
    messages: `_lens_shared_prefix` takes no lens argument, so the profile
    physically cannot vary between lenses (that is the v1.8.0 caching
    invariant), which is a stronger claim than five equal strings. An
    earlier version of the test also asserted no per-lens suffix mentions
    the profile at all — wrong, because the `completeness` brief legitimately
    references the `<project_requirements_profile>` tag in its prose.
  - **The closed vocabulary is ENFORCED at both ends** (`sanitized_error_kind`)
    — a P2 raised in review on PR #96, and a fair one: the field was
    *documented* as closed and telemetry-safe, but `_statuses_from_raw` is
    deliberately permissive, so arbitrary text in a shared `.baspec` file rode
    `incomplete_dimension_facts` into `/api/diagnostics` and a support bundle.
    Normalizing at load alone would have left the projection still trusting
    its input, so both ends call the same helper and the projection is the
    load-bearing one — that is where the guarantee is made, and a future code
    path that bypasses the loader must not be able to reopen it. An
    unrecognized value becomes `unrecognized` rather than `""`: a bundle
    should show that the file carried something odd without the odd thing
    travelling, and `""` already means "a success, or a pre-3.1 file". The
    permissive LOAD is unchanged (the project still opens) and the
    user-facing `error` keeps its detail — free text is what that field is
    for, which is exactly why it is not in the projection. Four assertions
    across three tests, two of which fail against the pre-fix code: the
    smuggling attempt through a project file, the same guarantee on a
    directly-constructed status (proving the projection enforces it and not
    only the loader), the vocabulary round-trip, and an end-to-end
    `/api/diagnostics` check that the crafted text is absent from the
    response body.
- Manual QA owed: Phase 3's first bullet — open a deliberately partial saved
  profile and inspect the PROJECT CONTEXT in a deep diagnostic trace,
  confirming the missing titles and the absent-not-empty instruction. The
  rendering is pinned hermetically; what the trace viewer shows is not.

## Chunk 3.2 — Required research dimensions and readiness

### Implementation

1. Add `required: bool = True` and `optional_rationale: str = ""` to
   `backend/spec_modules/base.py::ResearchDimension` and document that they
   are an issue-readiness policy, not a fan-out failure policy.
   Required-by-default is the frozen decision: a dimension may opt out only
   explicitly, and the rationale must be **machine-readable** — a source
   comment beside the declaration is invisible to registry validation, so the
   rationale lives in the field. Validation enforces the pairing both ways:
   `required=False` with a blank `optional_rationale` fails registration
   (startup failure, per the module-registry posture), and a nonblank
   rationale on a required dimension also fails (a stale rationale must not
   outlive an un-opt-out). The rationale rides the manifest facts and the
   readiness warning copy so a declared-optional gap can say why it is
   optional.
2. Extend module registry validation to require an actual boolean. Keep old
   module constructors source-compatible through the default.
3. Leave every dimension in `backend/spec_modules/generic.py` and
   `backend/spec_modules/hyperscale_fire.py` required (the default). Do not add
   any `required=False` declaration in this program.
4. Add a small pure helper near readiness derivation that joins module
   dimensions to cumulative profile statuses by `dimension_id` and returns:
   - all incomplete statuses;
   - incomplete required dimensions; and
   - unknown/missing required status records (fail closed).
5. Validate the research facts the readiness/manifest path consumes,
   structurally: dimension ids unique; completed and failed sets disjoint;
   counts consistent with the id lists; every required dimension id resolvable
   against the module. A record that fails validation is treated as not
   research-complete (fail closed for readiness, with a detail message naming
   the inconsistency) while project load remains permissive per the final
   product rule.
6. Rewrite only the `research_complete` readiness check in
   `backend/app.py::_readiness_payload`:
   - non-complete runner status: fail with existing status;
   - complete runner with no profile: fail with an evidence-missing message;
   - structurally invalid research facts: fail with the validation detail;
   - incomplete required dimension: fail and name it, with “press Research
     again to retry” guidance;
   - only optional incomplete dimensions: pass but explicitly state `N of M`
     and name the absent optional areas;
   - all dimensions complete: retain “Requirements research complete.”
7. Use cumulative `profile_result.dimension_statuses`; never judge only the
   latest round event. A failed extra round must not regress an earlier
   completed required dimension.
8. Update manifest fields added in Chunk 3.1 with required-policy ids/titles.
   The current module policy, not a hard-coded id, determines them.
9. Preserve legacy behavior through profile deserialization: pre-round profiles
   already synthesize completed statuses. Add an explicit legacy test.

### Files

- `backend/spec_modules/base.py`
- `backend/spec_modules/generic.py`
- `backend/spec_modules/hyperscale_fire.py`
- `backend/app.py`
- `backend/qc/engine.py`
- `tests/test_spec_modules.py`
- `tests/test_research_rounds.py`
- `tests/test_research_api.py`
- `tests/test_qc.py`
- `tests/test_qc_audit_report.py`
- `README.md`
- `CLAUDE.md`

### Focused verification

```powershell
venv\Scripts\python -m pytest -q tests/test_spec_modules.py tests/test_research_rounds.py tests/test_research_api.py tests/test_qc.py tests/test_qc_audit_report.py
```

### Acceptance criteria

- Any never-completed required dimension (all four, in both shipped modules)
  blocks readiness even when another dimension completed.
- A later successful round for that dimension restores readiness.
- A later failed rerun does not revoke cumulative completion.
- A declared-optional failed dimension (test-only module fixture) produces
  truthful passing detail that includes its `optional_rationale`; registry
  validation rejects `required=False` with a blank rationale and a nonblank
  rationale on a required dimension.
- Structurally invalid research facts read as not-research-complete without
  blocking project load.
- No runner status or round accumulation semantics changed.

### Implementation record

- Status: **complete** (2026-07-30)
- Commit/PR: `5e24b5c` — PR #97
- Tests: 12 new.
  `tests/test_spec_modules.py` (5): every shipped dimension required by
  default plus the dataclass default itself; a silent opt-out rejected; a
  stale rationale on a required dimension rejected; a non-bool `required`
  rejected; and a properly declared optional dimension accepted.
  `tests/test_research_api.py` (7), all through `GET /api/readiness`: the
  false pass removed (one of four dimensions completing now fails, names the
  missing area, and carries "1 of 4" plus the retry guidance); all-complete
  reading as the unchanged "Requirements research complete."; a later round
  restoring readiness AND a subsequent failed rerun not revoking it (the
  cumulative rule, over three real `append_research_round` rounds); a
  complete runner with no profile failing closed; a self-contradicting
  record failing closed while `/api/doc` and the runner's own status stay
  unaffected; a declared-optional gap PASSING with its rationale quoted; and
  a legacy no-rounds profile still reading complete.
  Focused run green (spec_modules / research_api / research_rounds /
  qc / qc_manifest_integrity — 91 passed); full gate green: `pytest -q`
  **1211 passed, 9 skipped** (was 1199/9), `npm test` 143 passed,
  `npm run build` clean (no frontend change — regression check).
  Load-bearing checks: reverting readiness to the status-only test turns 5
  red, and flipping the `required` default to `False` does not merely fail a
  test — it fails the registry at IMPORT with
  `SpecModule 'generic': research dimension 'governing_codes' declares
  required=False without an optional_rationale`, so the fail-open default the
  plan forbids cannot be introduced silently.
- Deviations:
  - **`hyperscale_fire.py` and `generic.py` are untouched**, though the plan
    lists them. Step 3 says to leave every dimension required and add no
    `required=False`, and required-by-default means that is exactly zero
    edits. A test asserts the property instead, over every module in
    `AVAILABLE_MODULES`, so a future module that opts out has to do it
    deliberately.
  - **`research_manifest_facts` moved from `qc/engine.py` to
    `research/engine.py`** and gained the module parameter. Readiness and the
    QC manifest now consume the same record (step 5 speaks of "the research
    facts the readiness/manifest path consumes"), and having `app.py` reach
    into a QC internal for a research fact would have been backwards — QC
    imports research, not the reverse. `profile_fingerprint` came with it
    because `qc/engine._sha256_json` is not importable from research without
    a cycle; it is pinned byte-compatible with what it replaced, so a
    retained report's research fingerprint does not shift from the move
    alone.
  - **The structural checks are ordered most-specific-first**, which the plan
    did not specify but the message demands: a duplicated status (the case a
    corrupt project file actually produces) trips the per-list count check
    before the disjointness check, and "counts disagree with lists" names
    nothing a user can act on. It now reports more records than distinct
    dimensions and says one is recorded twice. Note the overlap check is
    unreachable from freshly derived facts by construction — it stays for a
    facts record read back from a persisted report in 3.3.
  - **The readiness helper lives in `app.py`** next to `_readiness_payload`
    ("near readiness derivation"), while the pure join/validation live in
    `research/engine.py`. The helper is the only part that needs a
    `SessionState`; keeping the domain logic out of `app.py` is what let the
    join be unit-tested by the manifest tests as well.
  - **The Word memo needed no change**, which is worth stating because 3.3
    step 7 asks for it: `docx_export` consumes the readiness checks out of
    the QC state rather than re-deriving them, so the truthful detail reaches
    the export for free. A second derivation would be free to disagree with
    the checklist, so keep it consuming.
  - **`tests/test_qc.py` and `tests/test_qc_audit_report.py` are untouched.**
    They are in the plan's file list and were run as regression checks (the
    existing `test_qc.py` readiness assertion still passes unchanged), but
    3.2 adds no QC-report behavior — rendering the new manifest fields is
    3.3's job.
  - One 3.1 test expectation changed: with the module now in hand, an ABSENT
    profile's facts still name the module's declared dimensions (and count
    them all as incomplete-required) instead of leaving `dimension_titles`
    empty. That is strictly better for 3.3's "no profile" limitation, so the
    assertion was updated to pin the new meaning rather than the old
    emptiness.
- Manual QA owed: Phase 3's third bullet — press Research again on a
  deliberately partial profile, complete the previously missing required
  dimension, and confirm readiness recovers without losing prior findings.
  The recovery is pinned hermetically over three rounds; what is not covered
  is the drawer's own rendering of the new detail string.

## Chunk 3.3 — Consistent partial-research limitations in every report

### Canonical source

Both projections must read the captured
`input_manifest.requirements_research`. They must not read the live profile,
because the report is an audit of the run's input snapshot.

### Implementation

1. Add a backend helper in `backend/spec_doc/docx_export.py` that interprets
   the captured research manifest and returns a human-readable partial-profile
   limitation. It should:
   - use names when the new manifest fields exist;
   - fall back to completed/failed counts for older records;
   - distinguish absent profile from present-but-partial profile; and
   - mention required missing coverage when recorded.
2. Append the limitation in `_qc_render_limitations_and_signoff` after stale and
   failed-lens facts, before static boilerplate or unresolved anchors as fits
   current formatting.
3. Make the Word identity row truthful: render `No`, `Yes — complete`, or
   `Yes — partial (N of M completed)` instead of a reassuring bare `Yes` for a
   partial manifest. Legacy records without a manifest may retain “not
   recorded.”
4. Mirror the same decision tree in
   `frontend/src/lib/qcReport.ts::qcReportLimitations`. Keep wording semantically
   equivalent even if line wrapping differs.
5. Update frontend report types only as needed; preserve unknown manifest
   fields for forward compatibility.
6. Add Word extraction tests and frontend unit tests for:
   - no profile;
   - full profile;
   - partial optional profile;
   - partial required profile with names;
   - count-only legacy partial manifest; and
   - a report exported after live research later changes, proving captured
     manifest facts still win.
7. Verify the readiness checklist copied into Word now carries the truthful
   detail from Chunk 3.2 and the report cannot say issue-ready when required
   research was absent.

### Files

- `backend/spec_doc/docx_export.py`
- `frontend/src/lib/qcReport.ts`
- `frontend/src/components/QCReportModal.tsx` only if the identity summary is
  projected there separately
- `frontend/src/types.ts`
- `tests/test_qc_audit_report.py`
- `frontend/tests/qcReport.test.ts`
- `README.md`
- `CLAUDE.md`

### Focused verification and phase gate

```powershell
venv\Scripts\python -m pytest -q tests/test_qc_audit_report.py tests/test_qc_manifest_integrity.py tests/test_qc.py
Push-Location frontend
npm test
npm run build
Pop-Location
```

Then run the full standard verification commands from the master plan.

### Acceptance criteria

- Word and the in-app report state the same captured research limitation.
- Raw JSON continues to retain the full manifest.
- A partial profile is never summarized as merely “Research profile present:
  Yes.”
- Required missing coverage makes export-time issue readiness false.

## Phase 3 manual QA

- Open a deliberately partial saved profile and inspect the PROJECT CONTEXT
  shown in a deep diagnostic trace; confirm missing titles and the absent-not-
  empty instruction.
- Export a QC report from that profile and compare the in-app Limitations,
  downloaded JSON manifest, Word Limitations, identity row, and readiness table.
- Press Research again, complete the previously required dimension, and confirm
  readiness recovers without losing prior findings.
