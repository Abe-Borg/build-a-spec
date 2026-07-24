## Audit-grade reporting amendment (2026-07-24)

**Status: implemented v0.9.0 feature contract.** The lens, verification, fix,
readiness, full in-app report, Word/JSON downloads, audit persistence, and
coverage gates described below are implemented together. This amendment
supersedes the earlier queue-only framing and remains the maintenance contract
for future changes.

### Product contract

Final QC has two coordinated outputs:

1. A **full, first-class audit report** that a paying user can inspect in the
   app, download as a human-readable Word document, and download as
   machine-readable JSON. This is the trust and traceability surface.
2. A **compact action queue** containing the surviving findings that need a
   reviewer decision. This is the remediation surface; it does not replace or
   truncate the report.

The in-app view and both downloads are projections of the same canonical,
serialized `QCResult`. Word may format or summarize dense structures for
readability, but it must not silently omit failures, incomplete coverage,
refuted candidates, validation errors, or limitations. JSON is the lossless
record for downstream audit and integration. The report exposes observable
inputs, tool activity, evidence, outputs, and concise submitted notes. It does
**not** expose or claim to expose private chain-of-thought.

### Required report contents

The canonical report must preserve enough detail to answer "what ran, against
what, what did it check, what evidence did it retrieve, how was each result
adjudicated, and what happened next?"

- **Run and input identity:** schema/protocol version, unique run id, execution
  status, reviewed document history index, deterministic content fingerprint,
  input manifest/fingerprint, model id, effective effort and output ceiling,
  configured lens/search/fetch limits and verifier panel sizes, start and
  finish timestamps, duration, and research-profile availability. Current
  document identity is compared with the recorded input so every surface can
  mark the report current or stale.
- **Run outcome and coverage:** overall summary; complete/partial result
  execution; expected, completed, and failed lens counts; expected, completed,
  and failed verifier-seat counts; explicit limitations; and a coverage
  decision suitable for the deterministic readiness gate. A successful HTTP
  response or a verifier majority is not proof of complete coverage. A
  runner-level terminal failure still uses the runner status/error channel and
  does not fabricate a canonical completed report.
- **Every lens:** id, title, brief, status, error when failed, summary,
  finding/grounding totals, concrete `reviewed_checks` with
  `passed|finding|not_applicable` outcomes, relevant element ids, check notes,
  search queries, retrieved-source records, coverage observations, API/model
  request counts, and billed usage. A completed zero-finding lens must still
  show what it checked. A failed lens remains a first-class record rather than
  disappearing from the result.
- **Every source:** under its owning lens or verifier seat, the report keeps
  that call's query list and each source's requested and normalized URL,
  retrieval method, acceptance status, grounding result and reason, and links
  to the checks/findings that cite it. The report distinguishes cited URLs,
  actually retrieved URLs, and URLs accepted by grounding; it never converts
  an aggregate `grounded` boolean into a claim that every source was accepted
  or invents a one-to-one query/source causal mapping the API did not provide.
- **Every verifier seat:** finding id, stable seat index, expected/completed/
  failed status, verdict, revised severity, concise verdict note, error if the
  call failed, queries, retrieved sources, usage, and request/response counts.
  Failed or missing seats remain visible and place the candidate in a distinct
  infrastructure-inconclusive collection; they also make coverage partial and
  never serve as substantive refutation evidence.
- **Every surviving finding:** stable finding id, lens, element anchor or
  explicit section-level scope, saved reviewed reference/text and anchor
  resolution status, title, issue, rationale, cited and accepted sources with
  per-source checks, original severity, final severity, expected panel size and
  threshold, complete seat ledger, and verification outcome.
- **Full fix record:** exact proposed `apply_spec_edits` operations (not only a
  prose preview), snapshot dry-run result and error, eligibility for apply,
  current-document revalidation/apply outcome, and all recorded disposition
  events/reasons. Open, applied, dismissed, advisory/no-ops, invalid, and stale
  outcomes must remain distinguishable. Dismiss memory does not erase the
  original proposal or its validation history.
- **Detailed refuted appendix:** each refuted candidate keeps the same original
  issue/rationale, element anchor, original severity, sources/grounding, full
  verifier-seat ledger, threshold/outcome, and fix proposal/validation detail,
  plus the recorded basis for refutation. "Refuted" means excluded from the
  action queue, not deleted from the audit trail. Because Phase 3 validates
  only surviving fixes, a refuted proposal is labeled **not evaluated after
  refutation**, never incorrectly labeled invalid merely because its default
  `ops_valid` value is false.
- **Usage and cost:** run totals plus per-lens and per-verifier billed token,
  cache, and search usage where reported; API/model request counts; the pricing
  model selected by the recorded configuration; a persisted rate/fallback
  snapshot sufficient to reproduce the calculation; and clearly labeled
  estimated total cost. Cost is an estimate, not an invoice.
- **Limitations and staleness:** absent research context, lens/seat failures,
  incomplete retrieval, ungrounded evidence, unsupported/refused model calls,
  unresolved model-supplied element anchors, source-preservation constraints,
  and document changes after the reviewed snapshot. The report must
  distinguish "checked and passed" from "not checked" and "could not
  complete."

### Readiness rule added by this amendment

Readiness separates `qc_current` (exact full-input freshness plus latest
settled-attempt identity) from `qc_audit_complete` (current schema/protocol,
complete lens and verifier coverage, and no open critical findings). Both are
non-advisory. Readiness is blocked when any expected lens record is missing or
did not complete, or any expected verifier seat is missing or failed. A failed
seat makes that candidate infrastructure-inconclusive, so no incomplete panel
produces a substantive majority outcome. The in-app report and both downloads
must label such a result **partial** and list the exact missing/failed coverage.

---

## As built (2026-07-21; base pipeline)

**Base status: shipped (v0.9.0).** The original pipeline landed as planned — a
new `backend/qc/` package (structural clone of `research/`), five parallel Fable 5
lenses → adversarial verification → deterministic ops validation → an
accept/dismiss fix queue + the base Word QC artifact, plus a deterministic
readiness gate. The test/build counts in this historical note apply to that
base, not to the audit-grade reporting amendment above.

**VERIFY resolved.** Fable 5 (`claude-fable-5`) accepts the research request
shape unchanged — adaptive-thinking-always-on, depth via `output_config.effort`
(the engine never sends `{type: disabled}`, which would 400). Added
`MODEL_FABLE_5` to `schema._STRICT_CAPABLE_MODELS` (structured outputs are
supported). Pricing ($10/M in, $50/M out; cache read 0.1×, write 1.25×) was
already in the Batch 2 table and re-verified against the claude-api reference.

**Deviations from the plan (with one-line why):**

- **`no_assumed_left` gates `ready` (not advisory).** The plan body tagged it
  "advisory tier", but acceptance criterion 5 explicitly requires "no
  unreviewed imported/assumed blocks" for green — followed the acceptance
  criterion; `profile_complete` is the advisory check instead (research-complete
  already implies it, and criterion 5 omits it). Each check carries an
  `advisory: bool` so the UI can tier them.
- **Server-side refusal `fallbacks` deliberately NOT wired.** The claude-api
  skill recommends it for Fable 5, but it requires the beta messages endpoint
  and is outside this plan's scope; a refusal surfaces as an incomplete
  stop_reason and the lens fails clean under the existing failure policy. Noted
  as an operational caveat (as is Fable 5's 30-day-retention requirement — a
  ZDR org 400s every request).
- **Invalid `proposed_ops` are kept (advisory) rather than stripped.** The plan
  said "strip ops"; keeping them with `ops_valid=False` + `ops_invalid_reason`
  is more transparent (the report/preview can show what was proposed) and the
  apply path gates on `ops_valid`, so nothing invalid is ever applied.
- **Apply validates onto an accumulating working copy**, not N independent
  fresh copies, so the combined batch is guaranteed to replay as one
  `begin_turn`/`commit_turn` — this is how "never partially applied within a
  finding" + "one undo step" are both satisfied; a finding whose target moved
  raises on the working copy → `stale`.
- **Audit retired from the frontend entirely** (not just the button): `tsconfig`
  has `noUnusedLocals`/`noUnusedParameters`, so the audit state/handlers/api
  functions were removed from `App`/`ArtifactPanel`/`ResearchDrawer`/`api.ts`.
  The `/api/audit/*` endpoints + `AuditRunner` are untouched (deprecated).
- **QC runs on its own channel, never through `stream_user_turn`** — the plan's
  `stream_user_turn(model=...)` seam note described the original intent; the
  final shape keeps the interview loop Sonnet-only and puts Fable 5 entirely in
  `backend/qc/` (cleaner, and QC outlives a chat turn like research does).
- **Tracing:** added a `KIND_QC` span + `capture.qc_start/qc_event/qc_end`
  (the plan didn't call for it, but it mirrors the research/audit spans and the
  hooks never raise).

**Base-pipeline manual QA still owed (hermetic tests can't cover):** a live
Fable 5 run (streaming feel of lens→verify progress, real finding quality, refusal
handling), the accept-fix Word round-trip, the base `.docx` opening in Word,
and the hold-to-apply-criticals / readiness-goes-green flows in the real app.

**Audit-report extension manual QA owed:** a real large report in
`QCReportModal` (including source-link safety and no truncation), Word and JSON
downloads from the packaged app, legacy-result limitations, stale input
identity, and partial runs caused independently by one failed lens and one
failed verifier seat. Confirm that each partial case blocks
`qc_audit_complete` (and a failed latest attempt also blocks `qc_current`) and
that all three report surfaces identify the same run.

---

# Batch 4 — Final QC on Fable 5 (spare-no-expense pre-issue review)

Ships as **v0.9.0**. Abraham's framing (frozen): *the one place a model
other than Sonnet 5 appears.* A user-triggered button that sends the
draft to `claude-fable-5` for a last quality-control pass before the
section goes out the door — "this option should spare no expense and use
as many agents as necessary." Its primary record is the audit-grade Final QC
report defined by the 2026-07-24 amendment; its compact companion is a set of
verified findings, each with a ready-to-apply fix when possible, in an
accept/dismiss queue. Depends on Batch 2 (manual-edit machinery, status strip,
usage ledger) and pairs naturally with Batch 3's queue UX.

---

## Architecture overview

New package `backend/qc/`:

```
backend/qc/
  __init__.py     exports QCRunner, QCResult
  schema.py       lens definitions, submit_qc_findings / submit_qc_verdict
                  strict tools, finding/verdict normalization
  engine.py       run_final_qc(): lens fan-out → adversarial verification
                  → ops validation → QCResult   [pattern: research/engine.py]
  runner.py       QCRunner: session-bound daemon thread, event log,
                  snapshot, SSE follow            [pattern: research/runner.py]
```

Reuse deliberately (same-repo imports, not copies): `research/
grounding.py` stop-reason classes + evidence collectors, `research/
retry_policy.py`, `research/resend_sanitizer.py`, `research/schema.py`
web-tool builders + blocklist. The thread/event/SSE lifecycle is a
structural clone of the research runner — read it first and keep the
shapes parallel (status vocabulary `idle|running|complete|failed`,
replay-and-follow SSE with a `stream_end` sentinel, zombie-run
abandonment on session reset/load).

### Settings additions (`backend/settings.py`)

- `MODEL_FABLE_5 = "claude-fable-5"`.
- `QC_MODEL` (`BUILD_A_SPEC_QC_MODEL`, default `MODEL_FABLE_5`).
- `QC_MAX_TOKENS` (`BUILD_A_SPEC_QC_MAX_TOKENS`, default
  `MODEL_MAX_OUTPUT_TOKENS`).
- `QC_EFFORT` (`BUILD_A_SPEC_QC_EFFORT`, default `xhigh`).
  Note: Fable 5's adaptive thinking is always-on; `thinking:
  {type: "adaptive"}` + `output_config` effort as in the other loops.
  `VERIFY:` Fable 5 accepts the same request shape (it should — same
  API family; the strict-tool capability set in `research/schema.py
  _STRICT_CAPABLE_MODELS` must gain `MODEL_FABLE_5`).
- `QC_VERIFIERS_STANDARD = 2`, `QC_VERIFIERS_CRITICAL = 3` (verification
  panel sizes; see below).
- `QC_MAX_SEARCHES_COMPLIANCE = 24`, `QC_MAX_SEARCHES_LENS = 8`,
  fetch allowances 8/4 — per-call allowances, runaway guards not
  budgets, env-overridable like everything else.
- Add Fable pricing to the Batch 2 `PRICING` table ($10/M in, $50/M out
  as of 2026-07 — `VERIFY:`).

---

## Phase 1 — Lens fan-out

Five independent Fable calls, in parallel on a thread pool (pattern and
worker cap from `research/engine.py`; cap workers at 4, queue the rest —
account concurrency is the constraint, not cost). Each lens gets: the
full document rendering (`outline(doc, max_text=None)` of a SNAPSHOT —
`SpecSection.from_dict(session.doc.doc.to_dict())` taken at start, the
audit's anti-mutation pattern), the standards editions in effect, the
research profile render (untrimmed — `research_context_block` with its
100k guard), the module's domain conventions, and the lens brief.

Lens definitions live in `qc/schema.py` as frozen dataclasses
(`QCLens(lens_id, title, brief, max_searches, max_fetches)`):

1. **`code_compliance`** — verify every standard citation, edition, and
   technical requirement against the editions in effect; USE WEB SEARCH
   to check the standard's actual current content rather than recalling
   it (this lens gets the big search allowance); flag requirements that
   contradict the cited standard, editions that contradict the recorded
   basis, and provisions an AHJ would reject.
2. **`coordination_consistency`** — PART 1/2/3 alignment: every product
   specified has submittal requirements; every product has execution;
   execution references products that exist; cross-references resolve;
   duplicated or mutually contradicting provisions; terminology drift
   (same thing named two ways).
3. **`completeness`** — versus the research profile's grounded
   spec_requirements (each controlling item represented or consciously
   absent), the module playbook's topics, and conventional section
   scope; flag missing articles a reviewer would expect.
4. **`enforceability_language`** — spec-language quality: imperative
   mood, measurable criteria, no "as required"/"etc."/vague
   responsibility, no design-delegation traps, no narrative prose.
5. **`provenance_hygiene`** — risky `assumed` blocks (assumptions a
   reviewer must not miss), surviving TBD/needs_input/imported blocks,
   provisions citing research items that are `[UNVERIFIED]`.

Output tool `submit_qc_findings` (strict; schema conventions copied
from `research/schema.py` — every property required, optionals
nullable, no numeric constraints, clamp at parse):

```json
{"summary": str,
 "reviewed_checks": [{
   "check": str,                    // observable task, not hidden reasoning
   "outcome": "passed" | "finding" | "not_applicable",
   "notes": str,                    // concise result note
   "element_ids": [str],
   "source_urls": [str]             // URLs actually retrieved for this check
 }],
 "findings": [{
   "title": str,
   "severity": "critical" | "high" | "medium" | "low",
   "element_id": str | null,        // null = section-level finding
   "issue": str,                    // what is wrong
   "rationale": str,                // why, with citations when web-verified
   "source_urls": [str],            // pages actually retrieved, else []
   "proposed_ops": [ ... ] | null   // apply_spec_edits op objects that fix it
 }]}
```

Prompt obligations for every lens: `reviewed_checks` records substantive
checks that passed, found a defect, or were not applicable, so a completed
zero-finding lens still has an inspectable coverage record. It is concise
submitted work product and must never solicit private chain-of-thought.
Findings must anchor to element ids
from the rendering wherever possible; `proposed_ops` must use the exact
op vocabulary (echo the `APPLY_SPEC_EDITS_TOOL` schema into the prompt)
and target existing ids; a finding with no clean mechanical fix sets
`proposed_ops: null` (advisory); never propose mass status upgrades;
web-cited claims list their retrieved URLs (grounding: reuse
accepted-vs-cited validation from `research/grounding.py` — ungrounded
citations render as leads in the UI, same trust model as research).
`pause_turn` continuation + PDF elision + retry policy: lift the loop
structure from `research/engine._run_dimension` verbatim-in-shape.

Failure policy (ported): one lens failing never cancels the others; all
five failing fails the run clean. Any lesser lens failure produces an
explicitly partial report and blocks readiness under the reporting amendment.

---

## Phase 2 — Adversarial verification

Every finding from phase 1 goes before a panel of independent Fable
verifiers whose prompt instructs them to try to REFUTE it:
"You are reviewing a proposed QC finding against the specification
below. Attempt to refute it: is it factually wrong, already handled
elsewhere in the document, out of scope for this section, or trivial?
Default to refuted when uncertain — only real, actionable defects
survive." Verifier context: full document rendering + the finding +
that lens's brief. Web search allowed (small allowance) for
compliance-class findings.

Output tool `submit_qc_verdict` (strict):
`{"upholds": bool, "revised_severity": "critical"|"high"|"medium"|"low"|null, "note": str}`.

Panel sizes: `QC_VERIFIERS_STANDARD` (2) for medium/low,
`QC_VERIFIERS_CRITICAL` (3) for critical/high. Majority upholds →
finding survives (2-of-2 counts as survive on unanimous; 1-of-2 kills —
tie goes to the refuters, that is the point of the pass); severity
becomes the median of original + upheld revisions. Verifications for
different findings run in parallel through the same pool. Refuted
findings are retained in the result under `refuted` (collapsed in the
compact queue, full detail in the report, never shown as actionable issues).

The assembly layer creates an expected verifier-seat record before dispatch
and resolves every seat to `completed` or `failed`; it never builds the audit
trail only from successful responses. Each seat records its stable index,
status/error, submitted verdict and severity note when present, queries,
retrieved sources, billed usage, and request/response counts. A dead verifier
places the candidate in the infrastructure-inconclusive collection, marks the
overall report partial, and blocks readiness. Majority adjudication occurs
only when every expected seat completes.

This phase is the "as many agents as necessary" clause: total calls =
5 lenses + Σ panel sizes. Do not add a cap on findings count; the
runaway guard is per-call (search ceilings, retry policy), not
per-run.

---

## Phase 3 — Ops validation & assembly (deterministic, no model)

For each surviving finding with `proposed_ops`:

1. Dry-run `apply_edits(ops)` against a fresh copy of the SNAPSHOT
   (each finding independently — copy per finding, they must not see
   each other's effects). Invalid → retain the exact proposal for audit,
   keep the finding as advisory with `ops_valid=False`, and record
   `ops_invalid_reason`; the apply path must reject it.
2. Findings are content-addressed as `qc-` plus the first 12 characters of a
   canonical-JSON SHA-256 over every material fact a carried disposition
   relies on: lens id; element id; normalized title, issue, rationale and
   submitted severity; normalized cited URLs; exact proposed operations;
   reviewed text; final severity; verification outcome; the
   reviewer-index-sorted panel projection (`reviewer_index`, `status`,
   `upholds`, `revised_severity`); normalized/sorted grounding decisions
   (`source`, `accepted`, `reason`); and normalized accepted sources. A
   dismissal therefore survives only when the complete auditable identity is
   unchanged across runs.

Refuted candidates retain their exact proposed operations but do not enter the
survivor-only dry run. Every report renderer must distinguish that
**not-evaluated** state from an actually invalid surviving proposal.

Assemble `QCResult`:

```python
@dataclass
class QCVerdict: reviewer_index, status, upholds, revised_severity, note,
    error, search_queries, retrieved_sources, attempted_search_queries,
    attempted_sources, usage_totals, estimated_cost_usd,
    api_request_count, model_response_count
@dataclass
class QCLensStatus: lens_id, title, brief, status, error, summary,
    reviewed_checks, search_queries, retrieved_sources,
    attempted_search_queries, attempted_sources, finding_count,
    grounded_count, usage_totals, estimated_cost_usd,
    api_request_count, model_response_count
@dataclass
class QCFinding: finding_id, lens_id, original_severity, severity,
    element_id, reviewed_ref, reviewed_text, element_resolved, title, issue,
    rationale, source_urls, accepted_sources, source_checks, grounded,
    proposed_ops, ops_valid, ops_invalid_reason,
    verdicts: list[QCVerdict], verification_panel_size, verification_threshold,
    verification_outcome, status: "open" | "applied" | "dismissed",
    dismiss_reason, disposition_events
@dataclass
class QCResult: schema_version, protocol_version, run_id,
    execution_status: "complete" | "partial" | "failed" | "cancelled",
    summary, findings, refuted, inconclusive, lens_statuses,
    started_at, finished_at, duration_ms, version_index,
    version_fingerprint, input_fingerprint, input_manifest, model, effort,
    max_tokens, usage_totals, estimated_cost_usd, cost_basis, api_request_count,
    model_response_count, research_profile_present, dismissed_ids
```

`version_index` stamps the audited version → staleness marker in the UI
and export when the doc moves on (audit pattern, `compliance/runner.py`). The
fingerprints identify exact content even when a history index is reused after
load/reset. Field names may be grouped in nested serialized views during
implementation, but the information contract above is mandatory and must
round-trip through project persistence and JSON export. Coverage summaries,
limitations, and staleness are deterministic report projections of these
persisted facts rather than independent model assertions.
Serialization to/from dict for the project file (`spec_doc/project.py`
gains a `qc_result` field; restore via `QCRunner.restore`, same as the
audit's). The runner also persists `qc_latest_attempt`, separately from the
last successful `qc_result`, so a failed/cancelled rerun remains visible and
blocks readiness rather than making the earlier success look current.

---

## API surface (`backend/app.py`)

- `POST /api/qc/start` — gates: non-empty doc (400), no key (400), QC
  already running (409), model turn streaming (409 via Batch 2's
  `turn_active`). Research is NOT required: when absent, the
  completeness lens brief adapts (skip profile coverage, note it) and
  `research_profile_present: false` flags the result + UI shows
  "run research first for full coverage" advisory. Launches
  `QCRunner.start(...)` with a fresh client.
- `GET /api/qc/status` — snapshot: status, error, event log, compact result
  view plus the canonical report payload needed by the in-app full-report
  surface.
- `GET /api/qc/stream` — SSE replay-and-follow (event types:
  `qc_started`, `lens_complete`, `lens_failed`, `verify_progress`
  {done, total}, `qc_complete`, `qc_failed`, `stream_end`). The
  frontend uses Batch 2's status strip patterns for liveness — a QC run
  takes minutes; the drawer must show lens-by-lens progress the whole
  time (no dead air; the Batch 2 UX mandate applies here explicitly).
- `POST /api/qc/apply` — body `{finding_ids: [..]}`; gates like manual
  edit (409 while `turn_active` or a QC attempt is running); preserves-order
  deduplicates ids and applies each open finding's validated ops
  via ONE `begin_turn`/`apply_edits`/`commit_turn` sequence per request
  (one undo snapshot for the accepted set); re-dry-runs against the
  CURRENT doc first (doc may have moved since QC ran) — ops that no
  longer apply are reported per-finding as `stale` and skipped, never
  partially applied within a finding. Marks applied findings
  `status: "applied"`. Returns `_doc_payload` + per-finding outcomes.
- `POST /api/qc/dismiss` — `{finding_id, reason}` with a required nonblank
  audit rationale → `status: "dismissed"` (reason retained; dismissed ids remembered in the result
  so a re-run that regenerates the same content-addressed finding
  auto-marks it dismissed — reviewer decisions survive re-runs). Dismiss is
  rejected while QC is running so a disposition cannot be acknowledged on an
  old result and then silently lost when the new result settles.
- `GET /api/qc/export` — the full Final QC report as a standalone `.docx`
  (`spec_doc/docx_export.py` gains `build_qc_memo(result, section)`):
  report/run identity; input identity and staleness; model/config/timing;
  coverage and limitations; per-lens summaries/checks/search/retrieval/usage;
  source-grounding records; complete verifier-seat ledgers including failures;
  findings with original/final severity and full fix validation/disposition;
  detailed refuted appendix; and usage/estimated cost. This is the
  human-readable record a senior reviewer signs off on, with an explicit
  reviewer checklist/signature page that does not imply software approval or a
  professional seal. The package core properties identify Build-a-Spec, the
  report/run and section, and the actual generation time. The masthead,
  executive status, and sign-off fail closed when the latest attempt is
  failed/cancelled/partial/running or `qc_current`/`qc_audit_complete` is
  blocked. When a distinct prior success is retained, Word labels that run as
  historical beside the selected report identity; it never overrides the
  latest attempt or readiness state.
- `GET /api/qc/export.json` — a lossless machine-readable download of the
  audit envelope: `report` is the canonical serialized `QCResult`, and
  `current_state` records download time, current document version/fingerprint,
  current full input manifest, full-input staleness, runner/latest-attempt
  state, and the current readiness payload. If a rerun failed after an earlier
  success, the latest failed attempt is the primary exported report and the
  last success is included separately. It includes the same
  evidence and failures as the in-app and Word views, plus exact operation
  payloads and structured telemetry that Word may format for readability. It
  must not add private reasoning or claim chain-of-thought provenance.
  Both export endpoints accept the displayed `run_id`; a mismatch with the
  lock-coherent primary report selection returns `409` rather than downloading
  a different run than the one the user inspected.

### Readiness gate

`GET /api/readiness` — deterministic checklist (no model call):

```
{checks: [
  {id: "no_open_items",      ok, detail},   // no TBD / needs_input
  {id: "no_imported_left",   ok, detail},
  {id: "no_assumed_left",    ok, detail},   // no unreviewed assumed blocks
  {id: "lint_clean",         ok, detail},
  {id: "profile_complete",   ok, detail},
  {id: "research_complete",  ok, detail},
  {id: "qc_current",         ok, detail},   // exact full input + latest completed attempt identity
  {id: "qc_audit_complete",  ok, detail},   // v2 contract + complete lenses/seats + no open criticals
], ready: bool}   // ready = all non-advisory checks ok
```

Frontend: an "Issue readiness" section at the top of the QC drawer —
green/red checklist, click-to-jump where applicable. This is the
"can it go out the door" screenshot moment. Freshness/latest-attempt identity
and audit sufficiency are separate non-advisory rows, so a failed gate is
diagnosable. Any failed/missing lens or verifier seat blocks
`qc_audit_complete`, independent of the finding vote outcome.

---

## Frontend

`QCDrawer.tsx` (replaces the audit UI — see migration note) is deliberately a
compact run-status and action surface. `QCReportModal.tsx`, backed by pure
report helpers in `frontend/src/lib/qcReport.ts`, owns the roomy read-only
audit report:

- Idle: "Send to Final QC" primary button + a cost expectation line
  ("runs on Claude Fable 5 — the strongest model; typically $X–$Y" fed
  by the ledger's observed history, or a static estimate first run) +
  readiness checklist.
- Running: lens progress list (five rows with per-lens status), then
  "Verifying findings… (7/12)" — live via the SSE stream.
- Complete: coverage/status banner, **View full report**, **Download Word
  report**, and **Download JSON record** controls, followed by findings grouped
  by severity; each compact card: title, badge,
  element ref (click-to-jump — reuse the lint drawer's jump), issue,
  rationale (collapsible), source links, and actions **Apply fix**
  (when `ops_valid`; shows the ops as a human-readable preview —
  reuse the review-queue text-diff presentation) / **Dismiss** (with
  required rationale). "Apply all criticals" convenience with hold-to-
  confirm (Batch 3 pattern). Staleness banner when the recorded input identity
  does not match current content. Full refuted detail lives in the report
  rather than crowding the action queue.
- Usage rolls into the Batch 2 ledger under `qc` and the header ticker
  moves while the run streams (poll `/api/usage` on lens events).

`QCReportModal` renders, without content truncation: executive summary and
current/stale/partial status; run and document identity; readiness;
methodology/input manifest; effective model/config/timing; lens-by-lens work
records, checks, searches, retrievals, usage, and errors; aggregate metrics;
surviving findings with original/final severity, source evidence, every
verifier seat, exact proposed-op JSON, validation, and disposition history; a
full refuted appendix; aggregate usage/operations; and limitations. Its header
downloads `GET /api/qc/export?run_id=...` and
`GET /api/qc/export.json?run_id=...`. Unsafe or
non-HTTP source strings render as text, never as clickable links. Dense
sections may collapse, but failures and partial-coverage warnings must remain
clear and must never imply a clean sign-off.

**Migration note — the compliance audit:** the `code_compliance` +
`completeness` lenses strictly supersede the Phase 5 audit. Retire the
audit BUTTON from the UI (ResearchDrawer keeps research only); keep the
`/api/audit/*` endpoints and runner untouched this batch (breaking
nothing), mark them deprecated in CLAUDE.md, and fold the audit's
export closing section behind: if a QC result exists use it, else fall
back to the audit result. Removal happens in a later cleanup, not now.

---

## Tests (hermetic — the fakes carry the weight)

Extend `tests/fakes.py`: `SequencedFakeClient` already keys scripts by
first-user-message substring — key QC scripts by lens brief substrings
and verifier prompts by finding titles. Builders for
`qc_findings_response(lens, findings=[...])` and
`qc_verdict_response(upholds, severity=None)` mirroring
`research_response`.

- Fan-out: 5 lenses run, one failing lens doesn't kill the run, all
  failing does; grounding partition on a lens's cited URLs.
- Verification: 2-panel tie kills; 3-panel majority for criticals;
  severity median math; substantively refuted findings land in `refuted`,
  while any failed/cancelled/missing seat lands the candidate in
  `inconclusive` and makes coverage partial.
- Ops validation: invalid ops → advisory finding with reason; valid ops
  dry-run does not mutate the session doc.
- Apply: one undo snapshot per accept-set; stale ops (doc moved)
  skipped and reported; turn_active 409.
- Dismiss memory across re-runs (content-addressed ids).
- Runner lifecycle: 409 double-start, session reset abandons a running
  QC (zombie pattern — clone
  `test_session_reset_abandons_running_research`), project save/load
  round-trips `qc_result` and restores the drawer state, staleness
  marker flips when the doc changes.
- Readiness endpoint: each check's true/false paths.
- Word export smoke: docx opens and covers run/input identity, lens checks and
  failures, every verifier seat, source grounding, original/final severity,
  exact ops and validation/dispositions, detailed refuted entries, usage/cost,
  limitations, and the staleness note when stale.
- JSON export: exact canonical result, stable content type/filename,
  round-trip identity, and no omission of failed seats or invalid/stale fix
  records.
- Full-report UI: opens from the compact drawer and renders partial/stale
  banners, lens evidence, seat failures, refuted detail, and both downloads.
- Coverage readiness: every single lens failure, missing seat, or failed seat
  holds `ready=false`, including cases where remaining seats reach a majority
  and where no critical finding remains open.
- Usage: QC run totals reach the ledger under `qc` with Fable pricing.

---

## Docs & version

README (v0.9.0 story amended to: "one button, a fleet of Fable 5 reviewers, a
full audit-grade report, and a compact accept/dismiss action queue"), CLAUDE.md
(implemented notes; QC event table; audit deprecation note; layout
entries), config table additions, version gate at 0.9.0.

## Acceptance criteria

1. One click runs the full pipeline and the drawer never goes quiet:
   lens progress → verification counter → results.
2. The full in-app report and Word/JSON downloads expose the versioned
   run/input identity, effective model/config/timestamps, every lens and
   substantive coverage check, queries/retrieved sources and per-source
   grounding, every expected verifier seat including failures, original/final
   severity, full fix operations/validation/dispositions, detailed refuted
   appendix, usage/cost, and limitations/staleness. They expose observable
   work records, not private chain-of-thought.
3. Zero refuted-tier noise reaches the compact action queue; every surviving
   finding anchors to an element or is explicitly section-level. Refuted
   candidates remain complete in the report appendix, and their survivor-only
   operation validation state is labeled not evaluated rather than invalid.
4. Accepting a fix edits the document exactly as previewed, in one undo
   step; dismissing survives a re-run.
5. The Word report reads like a document a reviewer would file; JSON is a
   lossless machine-readable rendering of the same canonical result.
6. Readiness gate goes green exactly when: no open items, no
   unreviewed imported/assumed blocks, lint clean, research complete,
   QC current with no open criticals, and every expected lens and verifier seat
   completed successfully. Partial coverage always blocks readiness.
7. Works with research absent (flagged, reduced completeness scope and
   disclosed limitation).
8. Suite green, build clean, docs current, version gate at 0.9.0.
