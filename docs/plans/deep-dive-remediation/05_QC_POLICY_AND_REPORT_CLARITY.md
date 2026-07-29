# Phase 5 — QC policy and report clarity

- Status: planned
- Prerequisites: Phases 1-4 complete
- Risk: medium-to-high; Chunk 5.1 deliberately changes finding survival
  semantics

## Goal

Replace the verification-threshold inversion with the adjudicated v4 outcome
scheme (upheld / disputed / refuted / inconclusive, plus a severity-gated
evidence rule), conservatively consolidate duplicate cross-lens candidates
before verifier fan-out, make the Word/in-app audit report use consistent
version labels and explain exactly what request/response totals count, and
make issue readiness agree with the report's own sign-off language.

Chunk 5.1 must be its own attributable change. Do not combine it with model,
prompt, lens, panel-size, concurrency, or cost tuning.

## Chunk 5.1 — Panel outcome scheme under Final QC v4

### Why not a threshold tweak

Two simpler policies were considered and rejected during adjudication:

- *"Uphold when upholds > refutes, ties to refuters"* is algebraically
  identical to the shipped `(size // 2) + 1` (2/2 standard, 2/3 critical) and
  fixes nothing.
- *"Critical/high requires unanimous uphold, else refuted"* makes a 2-of-3
  upheld life-safety finding silently disappear — false negatives maximized on
  exactly the class where they cost the most.

The resolution: on a high-severity finding, panel disagreement is itself
decision-relevant evidence. It is surfaced as a first-class outcome, not
rounded to a binary.

### Policy (binding)

Outcomes per completed panel, by original severity:

| Panel | Votes (uphold–refute) | Outcome |
|---|---|---|
| 2 seats (medium/low) | 2–0 | upheld |
| 2 seats (medium/low) | 1–1 | **disputed** |
| 2 seats (medium/low) | 0–2 | refuted |
| 3 seats (critical/high) | 3–0 | upheld |
| 3 seats (critical/high) | 2–1 | **disputed — human review** |
| 3 seats (critical/high) | 1–2 or 0–3 | refuted, subject to the evidence rule |

Any missing/failed/cancelled seat keeps the existing outcome:
infrastructure-inconclusive. Disputed is substantive disagreement among a
fully completed panel; inconclusive remains infrastructure failure. The two
stay distinct outcomes with distinct copy.

**Evidence rule (severity-gated).** A critical/high refutation is recorded as
refuted only when at least one completed refuting seat persisted supporting
evidence (retrieved sources or an internal-document cross-reference captured
in its verdict record). When no refuting seat carries evidence, the candidate
resolves **disputed** with reason `insufficient_refutation_evidence` instead
of refuted. This encodes the RF-001 lesson from the reviewed run: three seats
refuted a life-safety-adjacent finding with zero searches and zero sources —
an under-evidenced refutation that a human should have seen. Medium/low
refutations are not evidence-gated.

**Disputed semantics.** A disputed candidate:

- blocks `qc_audit_complete` (and therefore issue readiness) exactly as an
  open critical does, until a human dispositions it — dismiss-with-reason and
  re-run remain the disposition paths;
- is never auto-applicable: its proposed operations are not semantically or
  mechanically validated (same posture as inconclusive), and Apply excludes it;
- rides the existing inconclusive-style machinery: its own persisted
  collection, its own report appendix, and its own QCDrawer group with the
  seat votes and any dispute reason visible; and
- carries per-seat records unchanged — the report must show who upheld, who
  refuted, and why the outcome is disputed.

### Schema/protocol compatibility

This semantic change creates:

- `QC_REPORT_SCHEMA_VERSION = 4`
- `QC_PROTOCOL_VERSION = "final-qc/4"`

V3 reports retain their recorded thresholds/outcomes and remain
readable/exportable as historical evidence. They are not revalidated with v4
rules and do not satisfy the current audit-complete readiness gate. Project
loading must not drop or corrupt them. Because `finding_id` hashes
`verification_outcome` and the panel projection, ids for affected findings
change across the v3→v4 boundary and dismiss memory will not carry across the
bump — expected protocol-change behavior; state it in release notes so it is
not filed as a bug.

### Implementation

1. Replace the survival predicate with one outcome helper (for example
   `_panel_outcome(original_severity, seats) -> str`) that implements the
   table plus the evidence rule, and use it at every live site:
   - candidate roster event (carry the outcome vocabulary, panel size, and
     uphold requirement);
   - candidate-complete adjudication event;
   - final outcome decision; and
   - persisted `verification_outcome` / `verification_threshold` fields
     (persist the full rule identity, not just an integer threshold).
2. Determine seat evidence from persisted verdict records only (retrieved
   sources, search records, or captured cross-reference notes) — never from
   un-persisted stream content; the report must be able to justify every
   outcome from serialized data alone.
3. Update `_structural_verification_outcome`:
   - schema v4 validates outcomes against the v4 helper, including disputed
     and the evidence rule;
   - schema v3 validates its recorded strict-majority threshold under the v3
     compatibility path;
   - older legacy behavior remains as currently documented.
4. Update the input manifest's rule string to describe the outcome table and
   evidence rule with panel sizes explicit.
5. Update settings comments that currently promise a generic majority.
6. Surface disputed everywhere a candidate outcome is projected: runner
   events, `/api/qc/status` snapshot, `qcLive.ts` fold (own group beside
   Upheld/Refuted/Inconclusive), QCDrawer (with disposition affordances),
   QCReportModal, Word report (own appendix), JSON export, and readiness
   detail copy.
7. Update fake QC-result builders so they accept/derive outcomes using the
   schema/protocol of the fixture. Do not make old fixture payloads silently
   v4.
8. Rewrite/add tests:
   - every row of the outcome table, both panel sizes;
   - critical 1–2 with an evidenced refuting seat → refuted; the same votes
     with zero evidenced refuting seats → disputed with
     `insufficient_refutation_evidence` (the RF-001 shape);
   - failed/missing seats remain inconclusive regardless of apparent votes;
   - a disputed candidate blocks `qc_audit_complete`, is excluded from Apply,
     and appears in its own report appendix and drawer group;
   - roster, live event, model object, serialized JSON, reload validation, and
     report display all show the same outcome and rule identity;
   - v3 2/3 historical result loads with its original outcome;
   - v3 is no longer current audit-grade after the version bump; and
   - dismissal/finding-id behavior remains conservative; the id change across
     the protocol bump is asserted, not accidental.
9. Update `README.md`, `CLAUDE.md`, and report methodology text. State the
   rationale: extra seats increase scrutiny; disagreement on severe findings
   escalates to a human instead of silently killing or passing the finding.
   Note the considered-and-rejected alternatives (majority-equivalent formula;
   unanimity-to-survive; 5-seat supermajority panels — the last remains a
   future option if disputed volume proves low).

### Files

- `backend/qc/engine.py`
- `backend/qc/runner.py` (snapshot outcome vocabulary)
- `backend/app.py` (readiness copy; the gate itself is Chunk 5.4)
- `backend/spec_doc/docx_export.py`
- `backend/settings.py` comments only unless final product version is selected
- `tests/fakes.py`
- `tests/test_qc.py`
- `tests/test_qc_verifier_v3.py` (keep historical tests even if the filename is
  now legacy-focused)
- `tests/test_qc_audit_report.py`
- `tests/test_qc_runner_audit_integrity.py`
- `frontend/src/lib/qcLive.ts`, `frontend/src/components/QCDrawer.tsx`,
  `frontend/src/components/QCReportModal.tsx`, `frontend/src/types.ts`
- `frontend/tests/qcLive.test.ts`, `frontend/tests/qcReport.test.ts`
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

- No outcome/threshold formula remains duplicated in live v4 code. A
  repository search for `(size // 2) + 1` should find only the helper or
  explicit v3 compatibility test/loader code.
- All v4 projections agree on outcome, rule identity, and disputed handling.
- A v3 report with a 2/3 high finding stays readable with its original facts.
- An under-evidenced critical refutation cannot silently disappear.
- No change weakens `finding_id` materiality; the v3→v4 id/dismissal boundary
  is documented and tested.

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
   sorted origin identities under the existing materiality posture. Origin
   identities are **content-addressed** (a hash over each original claim's
   material facts), never ordinal positions — an unrelated extra candidate in
   a rerun must not churn every later origin id or the consolidated hash. Do
   not simplify the hash or auto-carry a dismissal when group membership
   changes.

### Conservative eligibility and grouping

1. Partition candidates into hard-compatible buckets before asking a model to
   group them. At minimum require the same resolved element anchor (or the same
   explicit section-level scope) — i.e. an overlapping write scope. Never merge
   findings on different editable elements merely because their titles look
   similar.
2. **Group by actionable defect, not by identical operations.** The reviewed
   run's clearest duplicate pair (SF-009/SF-023: same parent element, same
   missing access/service requirement, different inserted wording) proposed
   non-identical operations; an identical-ops prerequisite would have kept
   them separate and re-created the duplicate provisions after apply.
   Eligibility is the hard structural checks above plus the semantic
   same-defect test — proposed operations are reconciled *after* adjudication,
   not used as an eligibility gate.
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
   - element id is the common hard-compatible anchor; and
   - the verifier prompt includes every original issue/rationale and every
     member's proposed operations, not only a lossy generated summary.
8. Reconcile operations only after the consolidated candidate is adjudicated:
   - member operation sets identical after canonical JSON normalization → use
     them, unchanged;
   - differing member operations → the consolidation call may synthesize one
     reconciled operation set, which the verifier panel sees and must approve
     (`ops_adequate`) like any other fix; if no reconciled set is produced or
     approved, the finding survives with the alternative remediations listed,
     is not auto-applicable, and requires human selection.
   Never apply more than one member's operations for the same defect.
9. Add a deterministic post-apply advisory lint (`duplicate_provision`, in
   `backend/spec_doc/linting.py`): flag sibling paragraphs whose normalized
   text is identical or near-identical, so any duplicate that still reaches
   the document — QC-applied or model-drafted — is visible in the existing
   Issues surface. Advisory, never blocking, consistent with every other lint
   rule.

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
- The SF-009/SF-023 regression shape: same parent element, same missing
  requirement, different inserted wording → one candidate, one panel, one
  reconciled-or-human-selected remediation, never both operations applied.
- Same title at different elements stays separate.
- Same element, same defect, differing ops: grouped; identical ops pass
  through unchanged; an unreconciled group survives as advisory-only with
  alternatives listed.
- Related but non-identical section-level issues stay separate without evidence
  overlap.
- The `duplicate_provision` lint flags two near-identical siblings and stays
  quiet on distinct provisions.
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
- `backend/spec_doc/linting.py` and `tests/test_linting.py`
  (`duplicate_provision`)
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
7. Add one glossary/methodology line for grounding, in both projections:
   "grounded" records that a cited source was actually retrieved and matched —
   retrieval confirmation, not truth verification. Do not rename the persisted
   field; the clarification is textual and rides the v4 methodology copy.
8. Add tests using both the historical five-lens/95-verifier shape (total 100)
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

### Focused verification

```powershell
venv\Scripts\python -m pytest -q tests/test_qc_audit_report.py tests/test_qc.py
Push-Location frontend
npm test
npm run build
Pop-Location
```

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

## Chunk 5.4 — Issue readiness, sign-off consistency, and report layering

### The contradiction being fixed

The reviewed run's exported report says "Issue readiness at export: Yes" on
its identity page and "REVIEW REQUIRED — OPEN FINDINGS REMAIN. Resolve or
formally disposition every open finding before issue." in its sign-off — with
25 open findings. The cause is `qc_audit_complete` gating only on
`open_critical_count() == 0` while the sign-off speaks for every open finding.
One meaning must win; the ratified default is the sign-off's.

### Readiness policy (owner-ratified default)

Split and rename the QC-side readiness checks so each answers one question:

- `qc_current` — unchanged: the retained report belongs to the live inputs and
  latest attempt.
- `qc_execution_complete` — the run executed with full lens and verifier
  coverage (the coverage half of today's `qc_audit_complete`).
- `no_open_qc_findings` — **every surviving open finding is applied or
  dismissed-with-reason, and no disputed candidate is undispositioned.** Open
  medium/low findings block issue readiness under this default, exactly as the
  sign-off demands; dismiss-with-reason remains the pressure valve. If the
  owner later prefers advisory mediums/lows, the check flips to advisory in
  one place — but "issue ready" and "open findings remain" can never again be
  simultaneously true.
- `issue_ready` — all of the above plus the existing document/research checks.

### Implementation

1. Rework the readiness derivation in `backend/app.py`: keep every existing
   document/research check id stable (`no_open_items` keeps meaning document
   TBD/needs-input items — clarify its detail copy to say so), add the split
   QC checks above, and remove the collapsed `qc_audit_complete` boolean or
   retain it as a derived alias for API compatibility.
2. Align every projection of readiness: `/api/readiness`, the QCDrawer
   checklist, the Word report's readiness table, masthead "Issue readiness at
   export", and the sign-off's recommended control state must be derived from
   the same facts and cannot disagree by construction — derive masthead and
   sign-off from one helper.
3. Post-apply lineage labeling: once any finding from a report is applied to a
   later document version, exports of that report label it
   "pre-remediation — document has been modified since this review" beside the
   existing staleness facts (fingerprint staleness already forces a re-run for
   current readiness; this is the disclosure half). Record apply/dismiss
   disposition events with the document identity they acted on — extending the
   existing `QCDispositionEvent` history, not inventing a parallel record.
4. Add a compact executive layer to the report without removing anything:
   a one-to-two-page summary at the top of the Word report and modal —
   run identity, readiness verdict with the failing checks named, counts by
   outcome, the open/disputed queue, and cost — followed by the existing full
   audit annex unchanged. The full-lineage posture ("no content truncation")
   is a deliberate prior decision and stays; the executive layer is additive.
5. Tests:
   - a complete run with open medium findings: `issue_ready` false, masthead
     and sign-off agree, and the failing check names the open findings;
   - all findings dispositioned: `issue_ready` true, sign-off no longer says
     findings remain;
   - a disputed candidate blocks until dispositioned;
   - masthead/sign-off consistency asserted directly (extract both from the
     rendered Word document and compare verdicts);
   - post-apply export carries the pre-remediation label; and
   - legacy v3 reports keep their historical rendering.

### Files

- `backend/app.py`
- `backend/qc/engine.py` / `backend/qc/runner.py` (disposition lineage)
- `backend/spec_doc/docx_export.py`
- `frontend/src/lib/qcReport.ts`, `frontend/src/components/QCDrawer.tsx`,
  `frontend/src/components/QCReportModal.tsx`, `frontend/src/types.ts`
- `tests/test_qc.py`, `tests/test_qc_audit_report.py`,
  `tests/test_qc_apply_history.py`
- `frontend/tests/qcReport.test.ts`
- `README.md`, `CLAUDE.md`

### Focused verification and phase gate

```powershell
venv\Scripts\python -m pytest -q tests/test_qc.py tests/test_qc_audit_report.py tests/test_qc_apply_history.py
Push-Location frontend
npm test
npm run build
Pop-Location
```

Then run the full standard verification commands from the master plan.

### Acceptance criteria

- "Issue readiness: Yes" and "OPEN FINDINGS REMAIN" can never both render in
  one report.
- Open and disputed findings block issue readiness until dispositioned.
- The executive summary and the full annex describe the same serialized facts.
- No existing readiness check id changed meaning silently.

### Implementation record

- Status: planned
- Commit/PR:
- Tests:
- Deviations:
- Manual QA owed:

## Phase 5 manual QA

- Run/export one v4 fixture with a high-severity 2/3 panel and confirm it is
  **disputed** (drawer group, report appendix, readiness block), a 3/3 panel
  survives, and an evidence-free 0/3 refutation resolves disputed with
  `insufficient_refutation_evidence`.
- Run a duplicate-heavy fixture (including the SF-009/SF-023 shape) and
  confirm raw candidates, consolidated groups, panels avoided, origin lineage,
  reconciled operations, and request/cost totals reconcile.
- With open medium findings, confirm the report masthead, sign-off, drawer
  checklist, and `/api/readiness` all say not issue-ready for the same named
  reason; disposition everything and confirm they flip together.
- Open a pre-upgrade v3 project and confirm the old report remains visible as
  historical/legacy evidence while readiness requests a new current QC run.
- Inspect a Word report with active and retained version records and a large
  verifier population; compare the executive summary, labels, in-app modal,
  and JSON.
