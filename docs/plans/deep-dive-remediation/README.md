# Deep-dive remediation program

- Owner: Abraham
- Source review: the diagnostics/code review dated 2026-07-28. It is not in
  this repository; these plans are self-contained and an implementation agent
  does not need it.
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

- `pause_turn` continuations in chat, research, and Final QC retain the provider
  container required by the current server web tools;
- stopping or truncating chat during a web call cannot persist a dangling
  `server_tool_use` or poison saved conversation history;
- live search/fetch activity displays its real query or URL, normal QC runs are
  not mislabeled as stopped, and research/QC followers recover from replay and
  transport interruption efficiently;
- incomplete research is named to the model and user, required research
  coverage blocks issue readiness, and every QC report projects the limitation
  consistently;
- interview prompt caching is actually incremental across turns, uses a
  deliberate one-hour TTL, and all one-hour writes are priced correctly;
- cross-lens versions of the same actionable QC defect can share one verifier
  panel without deleting their original claims, evidence, or lens lineage;
- failed/stopped work is metered as honestly as the provider data permits;
- QC verification thresholds become monotone with severity and the report's
  version/request-count language becomes unambiguous;
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

Implement exactly ONE numbered chunk in this session. Work in the documented
order; do not begin a later chunk. Treat the decisions and non-goals in the
plans as binding unless current code makes one unsafe, in which case stop and
explain the conflict with concrete code evidence.

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
session. A phase may contain several chunks and must stay sequential even when
some code areas look independent: the ordering preserves audit compatibility
and prevents temporarily activating behavior that the ledger cannot price.

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

These resolve ambiguity in the source review and should not be relitigated by
an implementation agent:

1. **Partial research stays a valid runner result.** A round succeeds when at
   least one dimension completes, preserving the existing append/retry model.
   Readiness fails only when the runner is incomplete, no coherent profile is
   available, or a module-declared required dimension has never completed.
   Optional incomplete dimensions produce a named warning but do not alone
   fail readiness.
2. **`governing_codes` is required in both shipped modules.** The policy is a
   `ResearchDimension.required` flag, not a hard-coded dimension id in the app.
3. **Critical/high QC panels require unanimous completion and uphold.** With
   the current size of three that is 3/3. Standard panels keep strict majority,
   which remains 2/2 at the current size of two. This preserves the documented
   tie-to-refuters posture and makes scrutiny monotone with severity.
4. **The threshold change creates `final-qc/4` / schema 4.** V3 reports remain
   readable historical evidence but no longer satisfy current audit readiness.
   Do not reinterpret a persisted v3 2/3 high-severity decision under v4 rules.
5. **Chat cache TTL is one hour and uniform.** System, committed-history, and
   tail breakpoints use the same TTL. Correct per-TTL accounting lands first.
6. **Normal terminal runner outcomes stay span endings.** Do not duplicate all
   `research_complete`/`qc_complete` frames into `events.jsonl`; the report's
   refuted section correctly identifies `spans.jsonl` as the normal outcome
   record. The real observability fix is closing stopped-run spans and retaining
   the terminal details already promised by the runner.
7. **`finding_id` remains audit-conservative.** Do not remove panel, severity,
   or grounding facts from its hash. That proposed change was refuted.
8. **The existing transition guard remains the paid-run start guard.** Do not
   add a second guard for the refuted scenario-start claim. Phase 6 fixes only
   transition ownership and the two methods that can clear/ignore it.
9. **Live provider canaries are manual and owner-authorized.** Hermetic tests
   are required; a paid research/QC run is a release check, not an automatic
   test.
10. **QC candidate consolidation is conservative and non-destructive.** A
   failed/invalid consolidator falls back to singleton candidates. A merged
   candidate retains every original lens claim and may share a verifier panel
   only when hard structural checks establish the same actionable defect; no
   fuzzy string threshold may silently delete a finding.

## Sequential roadmap

| Order | Plan | Chunks | Gate before continuing |
|---|---|---:|---|
| 1 | [Provider stream safety](01_PROVIDER_STREAM_SAFETY.md) | 1.1-1.3 | All three continuation paths echo a fake container; stopped/truncated history is reusable |
| 2 | [Live state and stream resilience](02_LIVE_STATE_AND_STREAM_RESILIENCE.md) | 2.1-2.4 | Real-shaped start-input events render; running QC is not settling; followers reconnect/dedupe |
| 3 | [Research and QC truthfulness](03_RESEARCH_AND_QC_TRUTHFULNESS.md) | 3.1-3.3 | Required missing research blocks readiness and is named in context plus both report projections |
| 4 | [Cache and metering accuracy](04_CACHE_AND_METERING_ACCURACY.md) | 4.1-4.4 | Per-TTL cost math is backward compatible; chat history hits a rolling 1h breakpoint; failed spend lands |
| 5 | [QC policy and report clarity](05_QC_POLICY_AND_REPORT_CLARITY.md) | 5.1-5.3 | V4 threshold semantics are consistent; duplicate candidates retain lineage while sharing panels; report labels reconcile exactly |
| 6 | [Concurrency, responsiveness, and release](06_CONCURRENCY_RESPONSIVENESS_AND_RELEASE.md) | 6.1-6.5 | Atomic transitions, short lock holds, full automated gates, approved manual QA |

Do not reorder Phase 4. Per-TTL pricing support must ship before the interview
begins creating one-hour cache entries. Do not fold Phase 5.1 into another
change: threshold semantics need an attributable before/after boundary.

## Finding coverage matrix

The source report repeats several root causes at different severities. This
matrix is the authoritative deduplication. The exact heading-by-heading audit,
including duplicate and refuted entries, is in
[`REPORT_COVERAGE_INDEX.md`](REPORT_COVERAGE_INDEX.md).

| ID | Consolidated remediation | Source report coverage | Chunk |
|---|---|---|---|
| R01 | Thread response container ids through research, QC, and chat continuations | All A-section missing-container findings and the web-tool-builder design finding | 1.1, 1.2 |
| R02 | Remove unpaired server tool uses on stop/truncation and before commit | Critical mid-stream poison plus between-round pause stop | 1.3 |
| R03 | Fall back to `content_block_start.input` for live web activity | Research, QC Review Room, and chat chip findings | 2.1 |
| R04 | Make QC `settling` mean stopped-terminal work only | Normal runs shown as stop-requested across API/readiness/UI | 2.2 |
| R05 | Reconnect the research SSE follower | Research transport/30-minute timeout freeze | 2.3 |
| R06 | Reject stale research snapshots and cheap-dedupe replay frames | Stale research regression plus QC/research O(n-squared) replay | 2.3, 2.4 |
| R07 | Name incomplete dimensions in drafting/QC context | Count-only research provenance finding | 3.1 |
| R08 | Persist interpretable partial-research manifest facts and limitations | Missing QC report limitation finding | 3.1, 3.3 |
| R09 | Add required dimensions and truthful readiness | False research-complete pass plus no must-have dimension | 3.2 |
| R10 | Add a rolling committed-history cache breakpoint | Structural whole-history cache miss | 4.2 |
| R11 | Use uniform one-hour interview cache TTL and correct docs | Both TTL findings plus inaccurate cache docstrings | 4.2 |
| R12 | Price one-hour cache creation separately | Verifier and future interview 1h write underpricing | 4.1 |
| R13 | Meter all-dimension research failures/stops | `ResearchFanoutError` usage loss | 4.3 |
| R14 | Best-effort meter stopped-turn output with disclosure | Missing terminal `message_delta` output usage | 4.4 |
| R15 | Make verification threshold monotone with severity | 2/2 standard versus 2/3 critical inversion | 5.1 |
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
| R26 | Consolidate duplicate cross-lens candidates before verifier fan-out | Run-forensics cost observation: near-duplicate findings each buy a panel | 5.2 |
| R27 | Preserve refuted behavior intentionally | Scenario busy-guard claim, broad terminal-event duplication, `finding_id` simplification | Explicit non-goals |

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
| 1.1 | planned | | |
| 1.2 | planned | | |
| 1.3 | planned | | |
| 2.1 | planned | | |
| 2.2 | planned | | |
| 2.3 | planned | | |
| 2.4 | planned | | |
| 3.1 | planned | | |
| 3.2 | planned | | |
| 3.3 | planned | | |
| 4.1 | planned | | |
| 4.2 | planned | | |
| 4.3 | planned | | |
| 4.4 | planned | | |
| 5.1 | planned | | |
| 5.2 | planned | | |
| 5.3 | planned | | |
| 6.1 | planned | | |
| 6.2 | planned | | |
| 6.3 | planned | | |
| 6.4 | planned | | |
| 6.5 | planned | | |
