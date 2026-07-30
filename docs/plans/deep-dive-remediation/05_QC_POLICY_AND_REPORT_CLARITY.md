# Phase 5 — QC policy and report clarity

- Status: in progress (5.1 and 5.2 landed; 5.3-5.4 planned)
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
refuted only when at least one completed refuting seat carries **validated
supporting evidence** — a structured evidence link in its verdict record, not
mere tool activity. A search that returned nothing useful, or a retrieval of
an unrelated page, is an activity record and never satisfies the gate. When no
refuting seat carries validated evidence, the candidate resolves **disputed**
with reason `insufficient_refutation_evidence` instead of refuted. This
encodes the RF-001 lesson from the reviewed run: three seats refuted a
life-safety-adjacent finding with zero searches and zero sources — an
under-evidenced refutation that a human should have seen — and closes the
adjacent loophole where one token search would have laundered the same
refutation. Medium/low refutations are not evidence-gated.

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
2. Extend the v4 verdict contract with a structured evidence field so the
   gate validates cited evidence, not activity records:
   - `QCVerdict` gains `refutation_evidence` — a list of entries, each either
     `{type: "source", url}` or `{type: "document_ref", reference}` — and the
     `submit_qc_verdict` strict tool schema (`backend/qc/schema.py`) gains the
     matching optional field with prompt guidance: a refuting seat on a
     critical/high candidate must cite what supports its refutation.
   - Validate every entry before it can satisfy the gate, mirroring the
     existing grounding posture: a `source` entry counts only when its
     normalized URL matches a source that seat actually retrieved/accepted
     (`validate_cited_sources` semantics); a `document_ref` entry counts only
     when it resolves against the reviewed document snapshot. Invalid or
     unresolvable entries are retained in the record but marked not-validated
     and do not satisfy the gate.
   - Raw `search_queries`/`retrieved_sources` and the free-form note remain
     persisted operational records; they never satisfy the gate by
     themselves.
   - A v4 refuting seat that omits the field simply carries no evidence — on
     a critical/high candidate that fails toward disputed, never toward
     refuted.
   Determine everything from persisted verdict records only — never from
   un-persisted stream content; the report must be able to justify every
   outcome (including per-entry validation results) from serialized data
   alone.
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
   - critical 1–2 with a refuting seat citing a validated source (URL matches
     that seat's retrieved sources) → refuted; the same votes with zero
     evidence entries → disputed with `insufficient_refutation_evidence` (the
     RF-001 shape); the same votes where the only refuting-seat activity is
     searches/retrievals with no validated evidence entry → still disputed
     (activity is not evidence); a `document_ref` that resolves against the
     reviewed snapshot → refuted; an unresolvable or unretrieved citation →
     retained but not-validated, gate unsatisfied;
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
- `backend/qc/schema.py` (`submit_qc_verdict` evidence field + normalization)
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

- Status: **complete**
- Commit/PR: branch `claude/deep-dive-phase-4-cont-o2up2c` (restarted from
  master after the 4.4 PR merged; the branch name predates Phase 5)
- Tests: 23 new. `tests/test_qc.py` (19 — every row of the outcome table on
  both panel sizes; the RF-001 shape (3-0 refute, no evidence → disputed);
  activity-is-not-evidence; a cited URL the seat never retrieved; a
  resolving and a non-resolving `document_ref`; medium/low not gated; an
  UPHOLDING seat's citation not opening the gate; a failed seat staying
  inconclusive whatever the votes; disputed blocking READINESS while
  staying structurally complete and unapplicable; the dismissal round trip
  end to end; a dismissed dispute surviving save/reload; the persisted rule
  identity; and a serialize/reload that re-adjudicates to the same
  outcome).
  `tests/test_qc_audit_report.py` (3 — the Word disputed appendix end to
  end, a v3 2-of-3 keeping its original upheld outcome on load, and v3
  being readable but no longer current audit grade).
  `frontend/tests/qcLive.test.ts` (4) and `frontend/tests/qcReport.test.ts`
  (4, including the report-wide aggregation totals summing). Two
  v3-semantics tests in `tests/test_qc.py` were replaced by the
  matrix, and `test_qc_verifier_v3.py`'s
  `..._but_finding_vote_is_majority` was renamed and rewritten (a
  majority-upheld candidate is now disputed). Focused command green, then:
  backend **1292 passed, 9 skipped**; `npm test` **162**; `npm run build`
  clean. The completeness/readiness split was reverted in place to prove it
  load-bearing → 4 red.
- Deviations:
  - **`verification_threshold` is retained rather than replaced.** Item 1
    says to persist the full rule identity, not just an integer. It does —
    `verification_rule` carries `VERIFICATION_RULE_V4` and the reload check
    keys off it. But the integer field stays (set to the panel size under
    v4, since v4 upholds only unanimously) so a v4 record remains
    field-comparable with the v3 records sitting beside it in the same
    project file, and so the v3 compatibility path has something to
    validate against.
  - **The manifest keeps the key `majority_rule`.** Item 4 asks for the
    rule string to describe the table; renaming the key would make an old
    manifest and a new one non-comparable field-by-field for no gain, so
    the key is historical and the VALUE now states the v4 scheme with panel
    sizes explicit.
  - **`_structural_verification_outcome` grew a third branch, not two.**
    The plan describes v4 and "schema v3 validates its recorded threshold";
    the pre-existing code actually had one path for `>= CURRENT` and a
    laxer one below it (no threshold check, all-zero reviewer indexes
    tolerated). Bumping CURRENT to 4 would silently have moved v3 records
    onto the lax path, so the branches are now explicitly v4 / v3 /
    pre-v3-legacy and each keeps the rules it was validated under.
  - **Evidence validation happens in the verifier worker, not at
    adjudication.** A `source` entry can only be judged against what THAT
    seat retrieved, and that is in scope in `_verify_one`. The seat
    therefore persists `validated` per entry, and `panel_outcome` reads
    persisted records only — satisfying item 2's "determine everything from
    persisted verdict records, never from un-persisted stream content".
    `document_ref` resolution needs the reviewed tree, so
    `reviewable_element_ids(section)` is computed once per run and threaded
    to the workers as a `frozenset` — no worker thread touches the tree,
    matching the pass's existing anti-mutation posture.
  - **Item 6's readiness copy got its own branch ahead of the
    coverage-incomplete one.** A disputed candidate is not incomplete
    coverage, and the existing detail would have told the user to re-run —
    which re-litigates a disagreement rather than resolving it. The new
    copy names the count, calls out under-evidenced refutations
    specifically, and points at dismiss-with-reason.
  - **Release notes not written (item 9's release-notes clause).** The
    v3→v4 `finding_id`/dismiss-memory boundary must be stated to users, but
    the product version is not bumped in this chunk (per the program's
    handoff rule) and `test_the_shipped_notes_describe_the_shipped_version`
    ties notes to the shipped version. **The release chunk owns writing
    it**; the obligation is recorded in `CLAUDE.md` under "Final QC v4
    panel outcomes" so it cannot be lost.
  - **Chunk 5.2's `disputed` interactions are out of scope here**, as the
    plan intends — this chunk adds the outcome; consolidation lands on top
    of it.
  - **Review follow-up (PR #103, Codex): "blocks like an open critical"
    had to be implemented literally, and the first attempt deadlocked.**
    Making a dispute fail `verification_complete()` (hence `is_complete()`)
    looked like the direct reading of "blocks `qc_audit_complete`", but
    `is_complete()` also gates the dismiss endpoint and whether
    `QCRunner.restore` retains the result at all — so the dispute could
    never be dismissed, and the readiness/drawer copy described an
    impossible workflow. Fixed by separating the two questions the way the
    codebase already separates them for open criticals:
    `verification_complete()` is structural (does every recorded outcome
    match its seats — `disputed` passes) and `open_disputed_count()` is the
    readiness term. `QCResult.finding()` widened to survivors + disputed
    (both apply paths re-check ops eligibility right after, which a
    disputed candidate fails by construction). Pinned by
    `test_a_disputed_candidate_can_actually_be_dismissed_with_a_reason`.
  - **The same review surfaced a latent data-loss bug.** `QCRunner.dismiss`
    writes into `dismissed_ids`, but `from_dict`'s reconciliation computed
    the expected set from survivors only — so dismissing a dispute, saving,
    and reopening made `from_dict` return `None` and silently discard the
    whole retained report. Both the run-end computation and the reload
    check now span survivors + disputed. Pinned by
    `test_a_dismissed_dispute_survives_a_save_and_reload`.
  - **Report-wide aggregations needed the new bucket too** (same review):
    `allQcCandidates`, `buildQcReportMetrics` and
    `collectQcOperationRecords` omitted disputed, so the modal undercounted
    candidates/seats/sources and dropped disputed operations from the
    register while showing the same records in the appendix. Adding it made
    `totalCandidates` include disputed, so `disputedFindings` /
    `disputedSeverity` were added and the two modal breakdown strings
    updated — otherwise the displayed split would no longer sum to the
    total it sits beside.
  - **Reload validation also had to cover disputed** (same review): the
    `all_findings` list driving required-field, duplicate-id,
    bucket/outcome and ops-semantic checks skipped it, so a malformed v4
    report could retain a corrupt disputed entry and still load.
- Manual QA owed: with owner approval, a live Final QC run on a section
  with at least one critical/high finding, to confirm (a) real verifier
  seats populate `refutation_evidence` when prompted to, and (b) the
  disputed rate is not so high that the escalation becomes noise. The
  hermetic tests prove the adjudication and the gate; only a paid run shows
  whether the model actually cites evidence under the new prompt wording,
  which is the assumption the whole gate rests on. If seats routinely omit
  it, the prompt needs strengthening before the disputed volume is judged.

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

- Status: **complete**
- Commit/PR: branch `claude/phase-5-1-qc-panel-jzdd1n` (restarted from master
  after the 5.1 PR merged; the branch name predates 5.2)
- Tests: 40 new backend, 15 new frontend.
  `tests/test_qc_consolidation.py` (31 — three variants at one element
  buying exactly one panel with all three claims retained; every origin
  claim reaching the serialized AND rendered report; maximum original
  severity sizing the shared panel; the same title at different elements
  never grouped; section-level candidates needing a shared source to be
  eligible, and becoming eligible when they share one; identical member ops
  passing through; the SF-009/SF-023 shape reconciling to ONE write with
  both alternatives still readable; an unreconciled group surviving
  advisory-only; a reconciliation reaching outside the members' scope
  refused; a no-ops group recorded distinctly; five invalid-partition
  shapes falling back to singletons with the call still billed; a
  no-payload call; a raised call; the feature disabled; an oversized
  bucket; determinism across lens completion order; origin ids tracking
  content not position; **membership alone changing the finding id when the
  claim does not** (the test that actually pins the hash extension — see
  Deviations); a rerun that regroups not inheriting a dismissal while the
  same grouping does; a single-member group keeping its claim verbatim;
  live counts reconciling with the report and ordering before the roster;
  and the record round-tripping with three partition-corruption cases
  refused). `tests/test_linting.py` (9 — exact and near-identical siblings,
  the numeric guard, distinct provisions, short boilerplate, three
  identical siblings reporting two, cross-article repeats ignored, nested
  children compared against their own siblings, advisory severity).
  `frontend/tests/qcReport.test.ts` (10) and `qcLive.test.ts` (5).
  Backend **1332 passed, 9 skipped**; `npm test` **177**; `npm run build`
  clean. Eight mechanisms were reverted in place to prove them
  load-bearing: repeated-claim disambiguation → 3 red, hard bucketing → 1 red, strict partition validation → 5
  red, reconciliation containment → 1 red, reload partition integrity → 1
  red, singleton-verbatim → 1 red, the finding-hash membership → 1 red,
  the lint numeric guard → 1 red.
- Deviations:
  - **`candidate_origins` holds stable REFERENCES, not copies.** The plan
    allows either ("all member records or stable references"). References
    won because the full records then live exactly once, in
    `QCConsolidation.origins`, so a finding and the grouping record cannot
    drift — and because it makes the acceptance criterion ("no original
    candidate disappears") a checkable PARTITION invariant rather than a
    hope. `QCResult.origins_for` /
    `qcCandidateOrigins` / `qc_origins_for` are the one join, mirrored in
    all three projections. `from_dict` refuses a report whose origins,
    groups and candidates do not partition exactly.
  - **The first draft of the dismiss-memory test passed for the wrong
    reason, and was replaced.** Reverting `origin_ids` out of
    `_mint_finding_id` turned nothing red: the merged candidate's canonical
    wording differed from the singleton's, so the ids differed on claim
    text alone.
    `test_membership_alone_changes_the_finding_id_when_the_claim_does_not`
    constructs the case where a group's canonical claim reproduces one
    member's words verbatim — same lens, element, severity and panel shape
    — so membership is the only difference. It goes red when the hash
    extension is removed. (Same failure mode the session's two Codex
    findings had: a test encoding the same wrong intent as the code.)
  - **"Identical member operations" compares the NON-EMPTY sets.** Read
    strictly, `{[X], []}` are not identical and would need reconciliation.
    But a member that proposed nothing has not proposed a *different* fix,
    and refusing the common shape (one lens proposes, another declines)
    would make it advisory for no safety gain — the outcome would be worse
    than pre-consolidation, where that lens's fix was applicable. All
    non-empty sets identical → `identical`; the verifier panel still has to
    approve. "Never apply more than one member's operations" holds either
    way: the candidate carries exactly one op set.
  - **Reconciled operations are containment-checked** against the union of
    the members' own target ids plus the anchor's ancestors. Not in the
    plan, but the grouping call's job is to GROUP: without it, a
    reconciliation could edit an element no member ever proposed touching,
    and the dry-run would happily validate it. `_ancestor_ids` permits the
    natural parent-insert (`pt2.a1` for two `pt2.a1.pN` duplicates) while
    refusing an unrelated element.
  - **A single-member group always keeps its original claim verbatim.** The
    plan does not say this; it fell out of asking what a validator
    downstream could catch. Nothing could catch the grouping call quietly
    rewriting one lens's finding, so the merge path is simply not reachable
    for a singleton — the canonical fields are ignored.
  - **Section-level evidence overlap is a shared normalized source URL**,
    not a standard identifier. The plan offers both as examples; URL
    overlap reuses `normalize_url` and needs no designation scanner.
    Connected components over that relation keep bucketing
    order-independent.
  - **Live events are a named TRANSITION, not a fourth stage** (the plan's
    own steer). `QcLivePhase` gains `"consolidation"` and the drawer shows
    a line; `stages` stays the three gates a reviewer can pass or fail. The
    grouping call's activity/search/fetch frames are relayed by the shared
    streaming machinery but deliberately not folded into visible state —
    pinned by a test that a noisy log and a quiet one fold identically.
  - **The manifest gains `consolidation_enabled` + `consolidation_rule`,
    so retained pre-5.2 results read stale.** Deliberate and consistent
    with `model`/`effort`: a review where five near-duplicate claims each
    faced their own panel is a materially different review.
    `matches_inputs` rebuilds with the CURRENT setting.
  - **`settings.QC_CONSOLIDATION` and `QC_CONSOLIDATION_MAX_BUCKET` were
    added** (the plan permits a dedicated setting). The first is the
    operator escape hatch that makes the manifest flag meaningful; the
    second is a runaway guard whose breach is RECORDED in
    `fallback_reason`, never silent.
  - **`tests/fakes.py` needed a routing fix, not just a new builder.** A
    grouping call quotes every candidate's title, so it matched — and
    consumed — scripts keyed on a finding title, desynchronizing that
    finding's whole panel. `SequencedFakeClient` now routes consolidation
    requests against marker-bearing keys only, and answers an unscripted
    one with the identity partition, so every pre-5.2 fixture keeps meaning
    what it always meant while still running the real code path.
  - **Review follow-up (PR #104, Codex P2): a repeated claim collided on
    its origin id, and the reload check turned that into silent data
    loss.** `normalize_findings` deduplicates nothing, so a lens can emit
    the same normalized finding twice; both content-addressed to one
    `origin_id`, and a duplicate origin id is precisely what
    `_consolidation_record_consistent` refuses — the run finished,
    serialized, and the whole paid report was discarded the next time the
    project was opened. Fixed by `_unique_origin_id`, which disambiguates
    (`qco-<digest>-2`) rather than deduplicating: "no original candidate
    disappears" is the criterion this step is built around, so if a lens
    submitted a claim twice the record says so. The suffix counts only
    byte-identical EARLIER claims, so ordinal-independence survives.
    **Verified the same shape against master: this is a PRE-EXISTING bug
    the fix also closes** — two identical claims from one lens minted one
    `finding_id` before consolidation existed, and `from_dict`'s
    duplicate-id check discarded the report for that alone. Four tests
    (`test_a_lens_emitting_one_claim_twice_still_reloads`,
    `..._also_used_to_collide_on_the_finding_id`,
    `..._suffix_does_not_shift_with_unrelated_candidates`,
    `test_three_identical_claims_disambiguate...`); reverting the
    disambiguation turns 3 red.
  - **Chunk 5.3's composition language is not written here**, as the plan
    intends. The consolidation record IS in the reconciled population
    (`_audit_accounting_consistent` and the run totals include it, pinned
    by a test in `test_qc_audit_report.py`); 5.3 owns the Meaning-cell
    wording.
- Manual QA owed: with owner approval, a live Final QC run on a section
  likely to produce cross-lens duplicates, to confirm (a) the grouping call
  actually merges the near-duplicates a human would merge rather than
  returning all singletons — the hermetic tests prove the machinery, not the
  model's judgement — and (b) it does not over-merge distinct defects that
  happen to share an element. If it returns all singletons in practice, the
  panels-avoided number in the report is the direct measure, and the prompt
  needs strengthening before the feature is worth its own call.

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
