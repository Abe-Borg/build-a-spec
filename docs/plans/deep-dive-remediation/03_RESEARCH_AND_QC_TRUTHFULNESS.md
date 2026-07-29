# Phase 3 — Research and QC truthfulness

- Status: planned
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
- `ResearchDimension.required` defines issue-critical coverage.
- Readiness is green only when runner state is complete, a coherent profile is
  present, and every required dimension has cumulative status `completed`.
- Optional incomplete dimensions are named in a passing warning.
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
5. Add tests for fully complete byte identity, one incomplete dimension,
   multiple incomplete dimensions, cumulative completion after a later retry,
   and QC lens prompt inclusion.

### Files

- `backend/research/engine.py`
- `backend/research/schema.py` only if context trimming requires a named
  protected-prefix seam
- `backend/qc/engine.py`
- `tests/test_research_engine.py`
- `tests/test_research_rounds.py`
- `tests/test_qc_manifest_integrity.py`
- `tests/test_qc_audit_report.py`
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

- Status: planned
- Commit/PR:
- Tests:
- Deviations:
- Manual QA owed:

## Chunk 3.2 — Required research dimensions and readiness

### Implementation

1. Add `required: bool = False` to
   `backend/spec_modules/base.py::ResearchDimension` and document that it is an
   issue-readiness policy, not a fan-out failure policy.
2. Extend module registry validation to require an actual boolean. Keep old
   module constructors source-compatible through the default.
3. Mark `governing_codes` required in:
   - `backend/spec_modules/generic.py`; and
   - `backend/spec_modules/hyperscale_fire.py`.
4. Add a small pure helper near readiness derivation that joins module
   dimensions to cumulative profile statuses by `dimension_id` and returns:
   - all incomplete statuses;
   - incomplete required dimensions; and
   - unknown/missing required status records (fail closed).
5. Rewrite only the `research_complete` readiness check in
   `backend/app.py::_readiness_payload`:
   - non-complete runner status: fail with existing status;
   - complete runner with no profile: fail with an evidence-missing message;
   - incomplete required dimension: fail and name it, with “press Research
     again to retry” guidance;
   - only optional incomplete dimensions: pass but explicitly state `N of M`
     and name the absent optional areas;
   - all dimensions complete: retain “Requirements research complete.”
6. Use cumulative `profile_result.dimension_statuses`; never judge only the
   latest round event. A failed extra round must not regress an earlier
   completed required dimension.
7. Update manifest fields added in Chunk 3.1 with required-policy ids/titles.
   The current module policy, not a hard-coded id, determines them.
8. Preserve legacy behavior through profile deserialization: pre-round profiles
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

- A never-completed `governing_codes` dimension blocks readiness even when
  another dimension completed.
- A later successful governing-codes round restores readiness.
- A later failed rerun does not revoke cumulative completion.
- An optional failed dimension produces truthful passing detail.
- No runner status or round accumulation semantics changed.

### Implementation record

- Status: planned
- Commit/PR:
- Tests:
- Deviations:
- Manual QA owed:

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
