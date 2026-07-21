## As built (2026-07-21)

**Status: shipped (v0.9.0).** The full pipeline landed as planned — a new
`backend/qc/` package (structural clone of `research/`), five parallel Fable 5
lenses → adversarial verification → deterministic ops validation → an
accept/dismiss fix queue + a signed-off QC memo, plus a deterministic
readiness gate. 193 backend tests green (19 new in `test_qc.py`); frontend
builds clean; version gate at 0.9.0.

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
  is more transparent (the memo/preview can show what was proposed) and the
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

**Manual QA still owed (hermetic tests can't cover):** a live Fable 5 run
(streaming feel of lens→verify progress, real finding quality, refusal
handling), the accept-fix Word round-trip, the QC-memo `.docx` opening in Word,
and the hold-to-apply-criticals / readiness-goes-green flows in the real app.

---

# Batch 4 — Final QC on Fable 5 (spare-no-expense pre-issue review)

Ships as **v0.9.0**. Abraham's framing (frozen): *the one place a model
other than Sonnet 5 appears.* A user-triggered button that sends the
draft to `claude-fable-5` for a last quality-control pass before the
section goes out the door — "this option should spare no expense and use
as many agents as necessary." The output is not a report to read; it is
a set of verified findings, each with a ready-to-apply fix, in an
accept/dismiss queue. Depends on Batch 2 (manual-edit machinery, status
strip, usage ledger) and pairs naturally with Batch 3's queue UX.

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

Prompt obligations for every lens: findings must anchor to element ids
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
five failing fails the run clean.

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
UI, full transparency, never shown as issues).

This phase is the "as many agents as necessary" clause: total calls =
5 lenses + Σ panel sizes. Do not add a cap on findings count; the
runaway guard is per-call (search ceilings, retry policy), not
per-run.

---

## Phase 3 — Ops validation & assembly (deterministic, no model)

For each surviving finding with `proposed_ops`:

1. Dry-run `apply_edits(ops)` against a fresh copy of the SNAPSHOT
   (each finding independently — copy per finding, they must not see
   each other's effects). Invalid → strip ops, keep the finding as
   advisory, record `ops_invalid_reason`.
2. Findings are content-addressed: `qc-` + sha256 of
   (lens, element_id, title, issue)[:12] — stable across re-runs for
   dismiss-memory (below).

Assemble `QCResult`:

```python
@dataclass
class QCFinding: finding_id, lens_id, severity, element_id, title,
    issue, rationale, source_urls, accepted_sources, grounded,
    proposed_ops, ops_valid, verdicts: list[QCVerdict],
    status: "open" | "applied" | "dismissed"
@dataclass
class QCResult: summary, findings, refuted, lens_statuses,
    started_at, finished_at, version_index, model, usage_totals,
    research_profile_present: bool
```

`version_index` stamps the audited version → staleness marker in the UI
and export when the doc moves on (audit pattern, `compliance/runner.py`).
Serialization to/from dict for the project file (`spec_doc/project.py`
gains a `qc_result` field; restore via `QCRunner.restore`, same as the
audit's).

---

## API surface (`backend/app.py`)

- `POST /api/qc/start` — gates: non-empty doc (400), no key (400), QC
  already running (409), model turn streaming (409 via Batch 2's
  `turn_active`). Research is NOT required: when absent, the
  completeness lens brief adapts (skip profile coverage, note it) and
  `research_profile_present: false` flags the result + UI shows
  "run research first for full coverage" advisory. Launches
  `QCRunner.start(...)` with a fresh client.
- `GET /api/qc/status` — snapshot: status, error, event log, result
  view.
- `GET /api/qc/stream` — SSE replay-and-follow (event types:
  `qc_started`, `lens_complete`, `lens_failed`, `verify_progress`
  {done, total}, `qc_complete`, `qc_failed`, `stream_end`). The
  frontend uses Batch 2's status strip patterns for liveness — a QC run
  takes minutes; the drawer must show lens-by-lens progress the whole
  time (no dead air; the Batch 2 UX mandate applies here explicitly).
- `POST /api/qc/apply` — body `{finding_ids: [..]}`; gates like manual
  edit (409 while turn_active); applies each finding's validated ops
  via ONE `begin_turn`/`apply_edits`/`commit_turn` sequence per request
  (one undo snapshot for the accepted set); re-dry-runs against the
  CURRENT doc first (doc may have moved since QC ran) — ops that no
  longer apply are reported per-finding as `stale` and skipped, never
  partially applied within a finding. Marks applied findings
  `status: "applied"`. Returns `_doc_payload` + per-finding outcomes.
- `POST /api/qc/dismiss` — `{finding_id, reason?}` → `status:
  "dismissed"` (reason retained; dismissed ids remembered in the result
  so a re-run that regenerates the same content-addressed finding
  auto-marks it dismissed — reviewer decisions survive re-runs).
- `GET /api/qc/export` — the QC memo as a standalone `.docx`
  (`spec_doc/docx_export.py` gains `build_qc_memo(result, section)`):
  header (project, section, model, date, doc version ± staleness note),
  summary, findings by severity with element refs, rationale, sources,
  applied/dismissed/open disposition, and the refuted appendix. This is
  the record a senior reviewer signs off on.

### Readiness gate

`GET /api/readiness` — deterministic checklist (no model call):

```
{checks: [
  {id: "no_open_items",      ok, detail},   // no TBD / needs_input
  {id: "no_imported_left",   ok, detail},
  {id: "no_assumed_left",    ok, detail},   // or count — advisory tier
  {id: "lint_clean",         ok, detail},
  {id: "profile_complete",   ok, detail},
  {id: "research_complete",  ok, detail},
  {id: "qc_current",         ok, detail},   // result exists, version matches, no open criticals
], ready: bool}   // ready = all non-advisory checks ok
```

Frontend: an "Issue readiness" section at the top of the QC drawer —
green/red checklist, click-to-jump where applicable. This is the
"can it go out the door" screenshot moment.

---

## Frontend

`QCDrawer.tsx` (replaces the audit UI — see migration note):
- Idle: "Send to Final QC" primary button + a cost expectation line
  ("runs on Claude Fable 5 — the strongest model; typically $X–$Y" fed
  by the ledger's observed history, or a static estimate first run) +
  readiness checklist.
- Running: lens progress list (five rows with per-lens status), then
  "Verifying findings… (7/12)" — live via the SSE stream.
- Complete: findings grouped by severity; each card: title, badge,
  element ref (click-to-jump — reuse the lint drawer's jump), issue,
  rationale (collapsible), source links, and actions **Apply fix**
  (when `ops_valid`; shows the ops as a human-readable preview —
  reuse the review-queue text-diff presentation) / **Dismiss** (with
  optional reason). "Apply all criticals" convenience with hold-to-
  confirm (Batch 3 pattern). Refuted findings in a collapsed section.
  Staleness banner when `version_index` ≠ current.
- Usage rolls into the Batch 2 ledger under `qc` and the header ticker
  moves while the run streams (poll `/api/usage` on lens events).

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
  severity median math; refuted findings land in `refuted`.
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
- Memo export smoke: docx opens, contains severity sections and the
  staleness note when stale.
- Usage: QC run totals reach the ledger under `qc` with Fable pricing.

---

## Docs & version

README (v0.9.0 story: "one button, a fleet of Fable 5 reviewers, an
accept/dismiss fix queue, and a signed-off QC memo"), CLAUDE.md
(implemented notes; QC event table; audit deprecation note; layout
entries), config table additions, version gate at 0.9.0.

## Acceptance criteria

1. One click runs the full pipeline and the drawer never goes quiet:
   lens progress → verification counter → results.
2. Zero refuted-tier noise reaches the main findings list; every
   surviving finding anchors to an element or is explicitly
   section-level.
3. Accepting a fix edits the document exactly as previewed, in one undo
   step; dismissing survives a re-run.
4. The QC memo exports and reads like a document a reviewer would file.
5. Readiness gate goes green exactly when: no open items, no
   unreviewed imported/assumed blocks, lint clean, research complete,
   QC current with no open criticals.
6. Works with research absent (flagged, reduced completeness scope).
7. Suite green, build clean, docs current, version gate at 0.9.0.
