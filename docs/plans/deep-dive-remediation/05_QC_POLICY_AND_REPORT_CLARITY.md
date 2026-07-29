# Phase 5 — QC policy and report clarity

- Status: planned
- Prerequisites: Phases 1-4 complete
- Risk: medium-to-high; Chunk 5.1 deliberately changes finding survival
  semantics

## Goal

Remove the verification-threshold inversion under a clearly versioned protocol,
conservatively consolidate duplicate cross-lens candidates before verifier
fan-out, then make the Word/in-app audit report use consistent version labels
and explain exactly what request/response totals count.

Chunk 5.1 must be its own attributable change. Do not combine it with model,
prompt, lens, panel-size, concurrency, or cost tuning.

## Chunk 5.1 — Monotone verification threshold under Final QC v4

### Policy

Define one helper:

```python
def _panel_threshold(severity: str, panel_size: int) -> int:
    if severity in {"critical", "high"}:
        return panel_size
    return (panel_size // 2) + 1
```

At current settings this yields 3/3 for critical/high and 2/2 for medium/low.
Every seat must still complete; infrastructure failure remains inconclusive.

### Schema/protocol compatibility

This semantic change creates:

- `QC_REPORT_SCHEMA_VERSION = 4`
- `QC_PROTOCOL_VERSION = "final-qc/4"`

V3 reports retain their recorded thresholds and remain readable/exportable as
historical evidence. They are not revalidated with v4's helper and do not
satisfy the current audit-complete readiness gate. Project loading must not
drop or corrupt them.

### Implementation

1. Add `_panel_threshold` next to `_panel_size` and use it at every live site:
   - candidate roster event;
   - candidate-complete adjudication event;
   - final survival decision; and
   - persisted `verification_threshold`.
2. Update `_structural_verification_outcome`:
   - schema v4 validates against `_panel_threshold(original_severity, size)`;
   - schema v3 validates its recorded strict-majority threshold under the v3
     compatibility path;
   - older legacy behavior remains as currently documented.
3. Update the input manifest's rule string to describe severity-dependent
   unanimity/strict-majority and keep panel sizes explicit.
4. Update settings comments that currently promise a generic majority.
5. Update fake QC-result builders so they accept/derive thresholds using the
   schema/protocol of the fixture. Do not make old fixture payloads silently v4.
6. Rewrite/add tests:
   - 2/3 high/critical is refuted under v4;
   - 3/3 high/critical survives;
   - 1/2 standard refutes and 2/2 survives;
   - failed/missing seats remain inconclusive regardless of apparent votes;
   - roster, live event, model object, serialized JSON, reload validation, and
     report display all show the same threshold;
   - v3 2/3 historical result loads with its original outcome;
   - v3 is no longer current audit-grade after the version bump; and
   - dismissal/finding-id behavior remains conservative and unchanged.
7. Update `README.md`, `CLAUDE.md`, and report methodology text. State the
   rationale: extra seats increase scrutiny rather than lower the uphold
   fraction.

### Files

- `backend/qc/engine.py`
- `backend/settings.py` comments only unless final product version is selected
- `tests/fakes.py`
- `tests/test_qc.py`
- `tests/test_qc_verifier_v3.py` (keep historical tests even if the filename is
  now legacy-focused)
- `tests/test_qc_audit_report.py`
- `tests/test_qc_runner_audit_integrity.py`
- `frontend/tests/qcReport.test.ts` if version normalization is asserted there
- `README.md`
- `CLAUDE.md`

### Focused verification

```powershell
venv\Scripts\python -m pytest -q tests/test_qc.py tests/test_qc_verifier_v3.py tests/test_qc_audit_report.py tests/test_qc_runner_audit_integrity.py
Push-Location frontend
npm test
npm run build
Pop-Location
```

### Acceptance criteria

- No threshold formula remains duplicated in live v4 code. A repository search
  for `(size // 2) + 1` should find only the helper or explicit v3 compatibility
  test/loader code.
- All v4 projections agree on threshold and outcome.
- A v3 report with a 2/3 high finding stays readable with its original facts.
- No change is made to `finding_id` materiality or dismissal carry rules.

### Implementation record

- Status: planned
- Commit/PR:
- Tests:
- Deviations:
- Manual QA owed:

## Chunk 5.2 — Audit-preserving cross-lens candidate consolidation

### Problem and safety posture

The reviewed run sent near-duplicate variants of the same defect to independent
verifier panels. That scales cost with lens overlap rather than unique
actionable issues. The fix must not replace that waste with silent finding
loss: original candidate claims remain immutable audit records and grouping
failure falls back to one panel per original.

### Pipeline position

Insert a consolidation phase after all lens records/raw findings are complete
and before the candidate roster and verifier tasks are built. It runs under the
v4 schema introduced in Chunk 5.1.

### Canonical data model

1. Add a persisted consolidation record to `QCResult`, containing:
   - status/error;
   - API request/model response counts and usage/cost;
   - every original candidate with its stable pre-consolidation id/index,
     lens id, severity, element id, claim fields, sources, and proposed ops;
   - the emitted groups and a concise grouping rationale; and
   - fallback reason when singleton grouping was used.
   Make the record required only when the v4 input manifest says consolidation
   was enabled. This keeps a v4 report created after Chunk 5.1 but before this
   chunk readable and self-consistent; new v4 runs always set the configuration
   flag and must carry the record.
2. A consolidated candidate retains `candidate_origins` (all member records or
   stable references to the immutable originals). Reports can therefore answer
   which lenses raised what, even though one panel adjudicated the shared root
   issue.
3. Extend `finding_id` inputs to include the canonical consolidated claim and
   sorted origin identities under the existing materiality posture. Do not
   simplify the hash or auto-carry a dismissal when group membership changes.

### Conservative eligibility and grouping

1. Partition candidates into hard-compatible buckets before asking a model to
   group them. At minimum require the same resolved element anchor (or the same
   explicit section-level scope). Never merge findings on different editable
   elements merely because their titles look similar.
2. Proposed operations must be identical after canonical JSON normalization,
   or all members must have no operations. Candidates with conflicting/different
   operations remain separate. This prevents one panel from authorizing a
   remediation that does not cover every member.
3. For section-level candidates, require an additional deterministic evidence
   overlap (for example a shared normalized cited/accepted source or standard
   identifier) before they are eligible for semantic grouping.
4. Within an eligible bucket, make one structured-output consolidation call
   that can group only supplied indexes. Its definition of duplicate is “the
   same actionable defect whose resolution would dispose of every member,” not
   merely related subject matter.
5. Validate the response strictly:
   - every original index appears exactly once;
   - no unknown/duplicate index;
   - every group obeys the hard compatibility rules;
   - singleton groups are always valid; and
   - canonical text is nonblank and grounded only by the union of member facts.
6. Any request, parse, coverage, or validation failure produces deterministic
   singleton groups. Lens results are never discarded and QC continues.
7. Derive the canonical candidate conservatively:
   - severity is the maximum original severity before verifier revisions;
   - source/citation/check records are a deterministic union with origin links;
   - matching operations are retained, otherwise the group would have been
     ineligible;
   - element id is the common hard-compatible anchor; and
   - the verifier prompt includes every original issue/rationale, not only a
     lossy generated summary.

### Live and report behavior

1. Emit `consolidation_started` and `consolidation_complete` (counts for raw,
   grouped, and panels avoided), plus activity/error if the structured call
   takes time. Integrate this as a named transition between lens review and
   verification so the no-dead-air contract holds without adding a misleading
   fourth completion gate.
2. The verifier roster is built from consolidated candidates. Threshold uses
   the canonical maximum original severity and Chunk 5.1's helper.
3. Persist and meter the consolidation call so aggregate usage/request counts
   remain exactly reconcilable. Chunk 5.3's composition language must include
   this record in addition to lenses and verifier seats.
4. Word and the in-app report show an “Original lens claims” subsection for a
   multi-origin finding/refutation/inconclusive result and a methodology line
   explaining shared-panel consolidation. Singleton presentation may remain
   compact.

### Tests

- Three eligible variants at one element become one candidate and buy exactly
  one panel; all three origin claims/sources appear in serialized and rendered
  reports.
- Same title at different elements stays separate.
- Same element with different proposed ops stays separate.
- Related but non-identical section-level issues stay separate without evidence
  overlap.
- Invalid, incomplete, refused, or failed consolidation output falls back to
  all singletons with no finding loss and preserved usage/error.
- Group order and ids are deterministic despite lens completion order.
- Maximum original severity selects panel size/threshold.
- A rerun with changed group membership does not incorrectly inherit a prior
  dismissal.
- Live raw/grouped/panels-avoided counts reconcile with the final report.

### Files

- `backend/qc/engine.py`
- `backend/qc/schema.py`
- `backend/spec_doc/docx_export.py`
- `backend/settings.py` only for a dedicated consolidation effort/limit setting
  if the existing QC settings cannot be reused
- `tests/fakes.py`
- `tests/test_qc.py`
- `tests/test_qc_live_events.py`
- `tests/test_qc_audit_report.py`
- `tests/test_qc_manifest_integrity.py`
- `frontend/src/lib/qcLive.ts`
- `frontend/src/lib/qcReport.ts`
- `frontend/src/components/QCDrawer.tsx`
- `frontend/src/components/QCReportModal.tsx`
- `frontend/src/types.ts`
- `frontend/tests/qcLive.test.ts`
- `frontend/tests/qcReport.test.ts`
- `README.md`
- `CLAUDE.md`

### Focused verification

```powershell
venv\Scripts\python -m pytest -q tests/test_qc.py tests/test_qc_live_events.py tests/test_qc_audit_report.py tests/test_qc_manifest_integrity.py
Push-Location frontend
npm test
npm run build
Pop-Location
```

### Acceptance criteria

- No original candidate disappears from the v4 audit record.
- Eligible duplicates buy one panel; ineligible/uncertain candidates remain
  independent.
- Consolidation failure cannot fail or weaken the QC run.
- Consolidation usage and request counts reconcile with aggregate accounting.
- Multi-origin findings are understandable in Word, JSON, and the modal.

### Implementation record

- Status: planned
- Commit/PR:
- Tests:
- Deviations:
- Manual QA owed:

## Chunk 5.3 — Version labels and request-count explanations

### Implementation: document versions

1. Add one backend formatter in `backend/spec_doc/docx_export.py`, equivalent to
   the frontend's `vN (stored index n)` convention. It must reject booleans as
   integers and return `Not recorded` for absent/malformed values.
2. Use it for:
   - reviewed document version;
   - active document version; and
   - retained reviewed document version.
3. Search the QC Word path for every other raw `version_index` presentation and
   normalize any user-facing equivalent. Do not change JSON's stored zero-based
   integer.

### Implementation: request/response counts

1. Keep engine counters unchanged; they are correct.
2. In `_qc_render_usage_and_cost`, calculate the population from serialized
   report records:
   - lens-record count;
   - candidate-consolidation request/response records;
   - verifier-seat record count across surviving, refuted, and inconclusive
     candidates; and
   - their persisted request/response sums.
3. Change the run-total Meaning cells to say they are sums of those record
   populations. If a malformed/legacy report cannot reconcile, say “recorded
   total; component population unavailable” rather than inventing a match.
4. Relabel per-lens and per-verifier fields to:
   - `Client API requests (streaming calls, including retries and pause_turn continuations)`;
   - `Final model responses received`.
5. Add a concise methodology note: server-side web search/fetch may perform
   multiple billed internal model iterations within one client streaming
   request, so token totals need not resemble a single inference pass.
6. Mirror the clarified labels/note in `QCReportModal.tsx`; the in-app and Word
   projections should not teach different meanings.
7. Add tests using both the historical five-lens/95-verifier shape (total 100)
   and a v4 consolidated shape. The historical Meaning cell explains 5 + 95;
   the v4 cell also identifies the consolidation record. Include
   legacy/malformed fallbacks.

### Files

- `backend/spec_doc/docx_export.py`
- `frontend/src/components/QCReportModal.tsx`
- `frontend/src/lib/qcReport.ts` if a composition helper belongs there
- `tests/test_qc_audit_report.py`
- `frontend/tests/qcReport.test.ts`
- `README.md` or `CLAUDE.md` methodology section

### Focused verification and phase gate

```powershell
venv\Scripts\python -m pytest -q tests/test_qc_audit_report.py tests/test_qc.py
Push-Location frontend
npm test
npm run build
Pop-Location
```

Then run the full standard verification commands from the master plan.

### Acceptance criteria

- The same document index is labeled identically everywhere in one Word report.
- JSON retains the raw index for machine use.
- A reader can reconcile historical 100-request runs from 5 lens and 95
  verifier records, and v4 runs from lens + consolidation + verifier records,
  without inference.
- Report text explains server-tool internal billing without claiming private
  reasoning or unavailable iteration detail.

### Implementation record

- Status: planned
- Commit/PR:
- Tests:
- Deviations:
- Manual QA owed:

## Phase 5 manual QA

- Run/export one v4 fixture with a high-severity 2/3 panel and confirm it is
  refuted, then one 3/3 panel and confirm it survives.
- Run a duplicate-heavy fixture and confirm raw candidates, consolidated
  groups, panels avoided, origin lineage, and request/cost totals reconcile.
- Open a pre-upgrade v3 project and confirm the old report remains visible as
  historical/legacy evidence while readiness requests a new current QC run.
- Inspect a Word report with active and retained version records and a large
  verifier population; compare labels with the in-app modal and JSON.
