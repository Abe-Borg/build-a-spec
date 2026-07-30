# Deep-dive remediation program

- Owner: Abraham
- Source review: the diagnostics/code review dated 2026-07-28. It is not in
  this repository; these plans are self-contained and an implementation agent
  does not need it.
- Adjudication revision: 2026-07-29. A second reviewer critiqued these plans
  and the original review; the authors converged. This revision encodes the
  agreed outcome: direct server-tool callers land first (ZDR + reliability),
  the QC panel gains a `disputed` outcome and a severity-gated evidence rule
  (replacing the earlier unanimity-to-survive policy), all research dimensions
  are required by default, issue readiness must agree with the report
  sign-off, provider-reported usage is never blended with estimates, and the
  strict sequential roadmap is replaced with dependency edges.
- Repository baseline inspected for this plan: `6f10c94`
- Plan status: ready for implementation

This directory turns the diagnostics review into an implementation program that
can be handed to a reasoning-capable coding agent. It is intentionally more
specific than the source report: duplicate findings are consolidated, later
verifier corrections win over earlier wording, conflicts with the report's
refuted section are resolved, and every change has a file-level scope, test
strategy, acceptance criteria, and dependency order.

The plans are self-contained. The implementation agent does not need the
original report in order to execute them.

## Outcome

When all six plans are complete:

- the web tools run direct (`allowed_callers: ["direct"]`), eliminating the
  container-id 400 class at its source, restoring live query streaming, and
  making the app's ZDR claims true again;
- `pause_turn` continuations in chat, research, and Final QC retain the
  provider container whenever one exists (defense-in-depth for any future
  code-execution-called tool);
- stopping or truncating chat during a web call cannot persist a dangling
  `server_tool_use` or poison saved conversation history, and already-poisoned
  saved projects are repaired at load/resend;
- live search/fetch activity displays its real query or URL, normal QC runs are
  not mislabeled as stopped, and research/QC followers recover from replay and
  transport interruption efficiently;
- incomplete research is named to the model and user, every research dimension
  is required for issue readiness by default, and every QC report projects the
  limitation consistently;
- interview prompt caching is actually incremental across turns, uses a
  deliberate configurable TTL (default one hour), and all one-hour writes are
  priced correctly with estimates kept separate from provider-reported usage;
- cross-lens versions of the same actionable QC defect can share one verifier
  panel without deleting their original claims, evidence, or lens lineage;
- failed/stopped work is metered as honestly as the provider data permits;
- QC panels resolve upheld / disputed / refuted / inconclusive with a
  severity-gated evidence rule, disagreement on severe findings escalates to a
  human instead of vanishing, issue readiness agrees with the report sign-off,
  and the report's version/request-count language becomes unambiguous;
- runner transitions, tutorial transitions, document reads, QC apply, exports,
  and request construction obey coherent locking/snapshot rules; and
- hermetic tests, frontend tests/build, diagnostics, docs, and an explicitly
  authorized live-canary checklist cover the complete release.

## How to hand this off

Give a fresh coding agent this prompt from the repository root:

```text
Read these files completely before touching code:

1. CLAUDE.md
2. docs/plans/deep-dive-remediation/README.md
3. The phase file containing the next incomplete chunk

Implement exactly ONE numbered chunk in this session. Pick the next incomplete
chunk whose binding dependency edges (README roadmap) are satisfied; within a
phase, respect chunk order. Treat the decisions and non-goals in the plans as
binding unless current code makes one unsafe, in which case stop and explain
the conflict with concrete code evidence.

Keep tests hermetic: no network and no real API key. Extend tests/fakes.py for
provider stream behavior. Run the chunk's focused tests, then the phase gate.
If frontend code changed, run both npm test and npm run build. Do not perform a
paid live research/QC run without explicit owner approval.

Before stopping, update that chunk's implementation record with the commit,
tests run, deviations, and any manual QA still owed. Do not bump the product
version unless executing the final release chunk and the owner has selected
the release version.
```

One chunk is sized as one reviewable pull request or one substantial agent
session. Chunks within a phase stay ordered; across phases, the roadmap's
binding edges are the law — they preserve audit compatibility and prevent
temporarily activating behavior that the ledger cannot price (4.1 before 4.2
is the canonical example). Independent chunks whose edges are satisfied may
land in any order.

## Required reading and repository rules

- `CLAUDE.md` is binding. In particular, preserve conversation turn atomicity,
  strip-at-commit, the no-dead-air UI contract, hermetic provider fakes, and
  snapshot-before-expensive-work patterns.
- Windows is the primary target. Use `venv\Scripts\python` from PowerShell.
- Do not add real API calls to the automated suite.
- Do not weaken quality, token, lens, finding, or panel limits as a cost fix.
- Do not mutate stored history merely to attach `cache_control`; request cache
  annotations must remain copy-on-write.
- New serialized audit fields must be validated and backward compatible, or be
  introduced behind the protocol/schema bump specified in Phase 5.
- Update `README.md` and `CLAUDE.md` in the same chunk whenever a runtime
  contract, event protocol, audit schema, cache policy, or concurrency invariant
  changes.

## Frozen decisions for this remediation

These resolve ambiguity in the source review and the 2026-07-29 adjudication,
and should not be relitigated by an implementation agent:

1. **The web tools run with `allowed_callers: ["direct"]`.** Dynamic
   filtering's code-execution caller produced the container-id 400, hid live
   queries, and is not ZDR-eligible by default per Anthropic's documentation.
   Direct callers are the first production patch (Chunk 1.1); container
   propagation and start-input parsing remain as defense-in-depth.
   Re-enabling dynamic filtering later is an owner decision, contingent on the
   container support being live and canary-verified, and must re-qualify the
   ZDR claims in the same change.
2. **Partial research stays a valid runner result.** A round succeeds when at
   least one dimension completes, preserving the existing append/retry model.
   Readiness fails only when the runner is incomplete, no coherent profile is
   available, or a required dimension has never completed. Declared-optional
   incomplete dimensions produce a named warning but do not alone fail
   readiness.
3. **Every dimension is required by default; all four shipped dimensions are
   required.** `ResearchDimension.required` defaults to `True`; a module may
   declare a dimension optional only explicitly with a machine-readable
   rationale (`optional_rationale`, nonblank, enforced by registry validation
   — a source comment is not enforceable). There is no silent fail-open
   default. Malformed research state fails closed for readiness while project
   loading stays permissive.
4. **QC panels resolve upheld / disputed / refuted / inconclusive.** Standard
   two-seat panels: 2–0 upheld, 1–1 disputed, 0–2 refuted. Critical/high
   three-seat panels: 3–0 upheld, 2–1 disputed (human review), 0–1 uphold
   refuted subject to the evidence rule. A critical/high refutation counts as
   evidenced only through a validated structured evidence link (a cited source
   the seat actually retrieved, or a resolvable document reference) — tool
   activity alone never qualifies — otherwise the candidate resolves disputed
   (`insufficient_refutation_evidence`). Disputed blocks
   `qc_audit_complete`, is excluded from auto-apply, and requires human
   disposition. Rationale: a majority formula is algebraically the shipped
   inversion, and unanimity-to-survive silently kills majority-upheld severe
   findings; disagreement on severe findings is decision-relevant evidence
   and escalates to a human.
5. **The outcome change creates `final-qc/4` / schema 4.** V3 reports remain
   readable historical evidence but no longer satisfy current audit readiness.
   Do not reinterpret a persisted v3 2/3 high-severity decision under v4
   rules. Because `finding_id` hashes outcome/panel facts, ids and dismissal
   carry do not bridge the v3→v4 boundary; release notes state this.
6. **Issue readiness agrees with the report sign-off.** The QC readiness gate
   splits into `qc_current` / `qc_execution_complete` / `no_open_qc_findings`;
   open surviving findings and undispositioned disputed candidates block
   `issue_ready` (owner-ratified default — dismiss-with-reason is the pressure
   valve). "Issue readiness: Yes" and "OPEN FINDINGS REMAIN" can never both
   render in one report.
7. **Chat cache TTL is uniform per request and configurable.**
   `BUILD_A_SPEC_CHAT_CACHE_TTL` defaults to one hour; system,
   committed-history, and tail breakpoints always share one TTL (mixed TTLs
   trip the provider's ordering constraint). Correct per-TTL accounting lands
   first.
8. **Provider-reported usage is never blended with estimates.**
   `output_tokens` always holds the provider's number; stopped-turn estimates
   live in a separate disclosed `estimated_output_tokens` component.
9. **Normal terminal runner outcomes stay span endings.** Do not duplicate all
   `research_complete`/`qc_complete` frames into `events.jsonl`; the report's
   refuted section correctly identifies `spans.jsonl` as the normal outcome
   record. The real observability fix is closing stopped-run spans and
   retaining the terminal details already promised by the runner.
10. **`finding_id` remains audit-conservative.** Do not remove panel,
    severity, or grounding facts from its hash. That proposed change was
    refuted. Consolidated-candidate origin identities are content-addressed,
    never ordinal.
11. **The existing transition guard remains the paid-run start guard.** Do not
    add a second guard for the refuted scenario-start claim. Phase 6 fixes
    only transition ownership and the two methods that can clear/ignore it.
12. **Live provider canaries are manual and owner-authorized, and pause
    contracts are proven hermetically.** The live API cannot be forced to
    return `pause_turn`; the forced-pause contract lives in the fakes, and the
    live direct-mode canary asserts completion, streamed deltas, and no
    container error. Rerun the research canary after any change to the
    web-tool definitions.
13. **QC candidate consolidation is conservative and non-destructive.** A
    failed/invalid consolidator falls back to singleton candidates. A merged
    candidate retains every original lens claim and may share a verifier panel
    only when hard structural checks establish the same actionable defect at
    an overlapping write scope; identical operations are NOT an eligibility
    requirement — operations are reconciled after adjudication, and an
    unreconciled group is advisory-only. No fuzzy string threshold may
    silently delete a finding, and never apply more than one member's
    operations for the same defect.

### Decisions reserved for the owner (not defaulted by these plans)

- Re-enabling dynamic filtering (decision 1) once container support is proven,
  trading ZDR eligibility and simplicity for token savings.
- Softening `no_open_qc_findings` from blocking to advisory for medium/low
  findings (decision 6) — the blocking default matches the sign-off language.
- Growing critical panels to five seats with two-sided supermajority outcomes
  if disputed volume proves low in practice (decision 4 alternative).

## Roadmap (dependency edges, not a total order)

The phases are the recommended sequence, but only the real dependency edges
are binding. Within a phase, chunks are ordered; across phases, an agent may
pull independent work forward when a phase is blocked.

| Phase | Plan | Chunks | Gate before dependents |
|---|---|---:|---|
| 1 | [Provider stream safety](01_PROVIDER_STREAM_SAFETY.md) | 1.1-1.4 | Direct callers shipped and documented; all three continuation paths echo a fake container; stopped/truncated/legacy history is reusable |
| 2 | [Live state and stream resilience](02_LIVE_STATE_AND_STREAM_RESILIENCE.md) | 2.1-2.4 | Real-shaped start-input events render; running QC is not settling; followers reconnect/dedupe |
| 3 | [Research and QC truthfulness](03_RESEARCH_AND_QC_TRUTHFULNESS.md) | 3.1-3.3 | Required missing research blocks readiness and is named in context plus both report projections |
| 4 | [Cache and metering accuracy](04_CACHE_AND_METERING_ACCURACY.md) | 4.1-4.4 | Per-TTL cost math is backward compatible; chat history hits a rolling configurable-TTL breakpoint; failed spend lands |
| 5 | [QC policy and report clarity](05_QC_POLICY_AND_REPORT_CLARITY.md) | 5.1-5.4 | V4 outcome semantics (incl. disputed + evidence rule) are consistent; duplicate candidates retain lineage while sharing panels; readiness, masthead, and sign-off agree; report labels reconcile exactly |
| 6 | [Concurrency, responsiveness, and release](06_CONCURRENCY_RESPONSIVENESS_AND_RELEASE.md) | 6.1-6.5 | Atomic transitions, short lock holds, full automated gates, approved manual QA |

Binding edges:

- **Chunk 1.1 first, always.** It is the production fix and the ZDR fix; every
  other chunk assumes the direct-caller configuration.
- **Chunk 2.2 (settling) may land immediately after 1.1** — it ships wrong
  backend copy today; do not hold it behind 2.1.
- **Chunk 6.1 (atomic runner settlement) may be pulled forward after Phase 1**
  — event volume from Phase 2 makes those races more load-bearing, so earlier
  is better. Coordinate its metering seam with Chunk 4.3.
- **Chunk 4.1 before 4.2** (per-TTL pricing before one-hour entries exist).
- **Chunk 5.1 before 5.2 and 5.4**, and 5.1 stays its own attributable change:
  outcome semantics need a clean before/after boundary.
- **Chunk 6.5 last**, after every other chunk.

## Finding coverage matrix

The source report repeats several root causes at different severities. This
matrix is the authoritative deduplication. The exact heading-by-heading audit,
including duplicate and refuted entries, is in
[`REPORT_COVERAGE_INDEX.md`](REPORT_COVERAGE_INDEX.md).

| ID | Consolidated remediation | Source report coverage | Chunk |
|---|---|---|---|
| R01 | Thread response container ids through research, QC, and chat continuations | All A-section missing-container findings and the web-tool-builder design finding | 1.2, 1.3 |
| R02 | Remove unpaired server tool uses on stop/truncation and before commit | Critical mid-stream poison plus between-round pause stop | 1.4 |
| R03 | Fall back to `content_block_start.input` for live web activity | Research, QC Review Room, and chat chip findings | 2.1 |
| R04 | Make QC `settling` mean stopped-terminal work only | Normal runs shown as stop-requested across API/readiness/UI | 2.2 |
| R05 | Reconnect the research SSE follower | Research transport/30-minute timeout freeze | 2.3 |
| R06 | Reject stale research snapshots and cheap-dedupe replay frames | Stale research regression plus QC/research O(n-squared) replay | 2.3, 2.4 |
| R07 | Name incomplete dimensions in drafting/QC context | Count-only research provenance finding | 3.1 |
| R08 | Persist interpretable partial-research manifest facts and limitations | Missing QC report limitation finding | 3.1, 3.3 |
| R09 | Require all dimensions by default and make readiness truthful | False research-complete pass plus no must-have dimension | 3.2 |
| R10 | Add a rolling committed-history cache breakpoint | Structural whole-history cache miss | 4.2 |
| R11 | Use a uniform configurable interview cache TTL (default 1h) and correct docs | Both TTL findings plus inaccurate cache docstrings | 4.2 |
| R12 | Price one-hour cache creation separately | Verifier and future interview 1h write underpricing | 4.1 |
| R13 | Meter all-dimension research failures/stops | `ResearchFanoutError` usage loss | 4.3 |
| R14 | Meter stopped-turn output as a separate disclosed estimate | Missing terminal `message_delta` output usage | 4.4 |
| R15 | Replace the panel formula with the v4 outcome scheme (upheld/disputed/refuted/inconclusive) | 2/2 standard versus 2/3 critical inversion; adjudication rejected both the majority-equivalent formula and unanimity-to-survive | 5.1 |
| R16 | Format document versions consistently | 1-based reviewed versus 0-based active version | 5.3 |
| R17 | Explain request/response count populations | Lens-versus-run count ambiguity | 5.3 |
| R18 | Offload template import parsing/writing | Async handler blocking the event loop | 6.3 |
| R19 | Atomically settle research runner state and terminal event | Three successor-log/cancel races and status-before-profile publication | 6.1 |
| R20 | Close trace spans on explicit runner stop | Narrow genuine part of the terminal-trace complaint | 6.1 |
| R21 | Give tutorial transitions an owner token | `finish_tutorial`/`force_restore_original` orphaning and guard clobber | 6.2 |
| R22 | Snapshot document diff under the session guard | Diff TOCTOU `IndexError` | 6.3 |
| R23 | Build `/api/doc` and QC-apply responses coherently | Mutable post-lock response payloads | 6.3 |
| R24 | Render DOCX outside the turn-state lock | Source and normalized export lock hold | 6.4 |
| R25 | Sanitize/build chat requests outside the turn-state lock | PDF resend page-counting under lock | 6.4 |
| R26 | Consolidate duplicate cross-lens candidates before verifier fan-out (defect-level grouping, ops reconciled after adjudication) | Run-forensics cost observation: near-duplicate findings each buy a panel; SF-009/SF-023 shape | 5.2 |
| R27 | Preserve refuted behavior intentionally | Scenario busy-guard claim, broad terminal-event duplication, `finding_id` simplification | Explicit non-goals |
| R28 | Direct server-tool callers and ZDR claim consistency | Adjudication: `_20260209` dynamic filtering is not ZDR-eligible by default and caused the container/visibility failures | 1.1 |
| R29 | Issue readiness agrees with the report sign-off (`no_open_qc_findings`) | Adjudication: "Issue readiness: Yes" rendered beside "OPEN FINDINGS REMAIN" | 5.4 |
| R30 | Post-apply pre-remediation labeling and disposition lineage | Adjudication, scoped down: fingerprint staleness already forces a re-run; labeling was the missing half | 5.4 |
| R31 | Severity-gated evidence rule for refutations (`insufficient_refutation_evidence`) | Adjudication: RF-001 refuted with zero searches/sources — an under-evidenced refutation | 5.1 |
| R32 | Compact executive report layer over the unchanged full annex | Adjudication: 6,570-paragraph report is unreadable; full-lineage posture stays | 5.4 |
| R33 | Failed-dimension observability in traces and diagnostics snapshot | Adjudication: support bundles could not answer "which coverage failed" | 3.1 |
| R34 | Repair legacy poisoned histories at load/resend | Adjudication: commit-time scrubbing alone cannot heal already-saved projects | 1.4 |
| R35 | Post-apply `duplicate_provision` advisory lint | Adjudication: a duplicate that survives consolidation must still be visible | 5.2 |

## Program-wide invariants

Every chunk must preserve these properties:

- A failed chat turn leaves history and document unchanged; user stop remains a
  deliberate successful truncation that commits safe content.
- A provider continuation container exists only for one attempt/turn. Never
  persist it in a project, reuse it in a fresh retry, or place it inside cached
  prompt content.
- Legitimate server-tool use/result pairs may span multiple assistant messages
  after `pause_turn`; pairing logic therefore examines the whole new turn, not
  one message at a time.
- Research profiles remain cumulative across rounds. A failed later round never
  erases a dimension that completed earlier.
- QC report UI, JSON, and Word remain projections of serialized `QCResult` and
  its captured manifest, never live mutable research state.
- Cache creation tokens are not double-priced: the provider's top-level cache
  creation total includes the one-hour subtotal.
- Runner event logs remain dense, append-only, and sequence-numbered.
- Expensive work can run outside locks only after the necessary inputs and
  generation/version identities are captured under the lock and revalidated
  before a mutation lands.

## Standard verification commands

Run focused tests listed in each chunk first. At every phase gate run:

```powershell
venv\Scripts\python -m pytest -q
Push-Location frontend
npm test
npm run build
Pop-Location
```

The frontend uses Node 22 for direct TypeScript test execution. No new skip,
xfail, network call, real API key, or timing-fragile sleep is acceptable.

## Implementation record

Update this table as chunks land. A chunk is complete only when its focused
tests and phase gate are recorded in the phase file.

| Chunk | Status | Commit/PR | Notes |
|---|---|---|---|
| 1.1 | **complete** | `4bd0c18` (PR #88) | direct callers shipped in all three channels; ZDR claims reconciled |
| 1.2 | **complete** | `f0a5ac5` (PR #89) | research + QC continuation containers, attempt-local |
| 1.3 | **complete** | `d39e778` (PR #90) | chat continuation container, turn-local |
| 1.4 | **complete** | `2f759e3` (PR #91) | server-tool pairing at four boundaries + legacy repair — **Phase 1 done** |
| 2.1 | **complete** | `0cfad61` (PR #93) | start-input fallback in all three relays; every index popped at block stop |
| 2.2 | **complete** | `66abd43` (PR #92) | settling = stopped-and-unwinding only; pulled ahead of 2.1 per the roadmap |
| 2.3 | **complete** | `a42fc76` (PR #94) | `lib/researchLive.ts`; watermark staleness, generation guard, reconnect + abort |
| 2.4 | **complete** | `58eebec` (PR #95) | `lib/eventSeqIndex.ts`; identity before sequence, O(1) duplicate test, first-arrival-wins — **Phase 2 done** |
| 3.1 | **complete** | pending | incomplete coverage NAMED in context/manifest/trace/diagnostics; `DimensionStatus.error_kind` |
| 3.2 | planned | | |
| 3.3 | planned | | |
| 4.1 | planned | | |
| 4.2 | planned | | |
| 4.3 | planned | | |
| 4.4 | planned | | |
| 5.1 | planned | | |
| 5.2 | planned | | |
| 5.3 | planned | | |
| 5.4 | planned | | |
| 6.1 | planned | | may be pulled forward after Phase 1 |
| 6.2 | planned | | |
| 6.3 | planned | | |
| 6.4 | planned | | |
| 6.5 | planned | | |
