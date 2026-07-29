# Source report coverage index

This index accounts for every titled finding in
`BUILDASPEC_DEEP_DIVE_REPORT.md` plus the cross-lens cost improvement called out
in its run-forensics summary. Later verifier corrections in the report control
severity and scope. “Consolidated” means the heading shares one implementation
with another heading; it does not mean the behavior is ignored.

## Run-forensics observations

| Observation | Disposition |
|---|---|
| No crashes; empty faulthandler log; ResizeObserver browser noise | No change. These are healthy/benign facts. |
| Two research dimensions failed with container-id 400 | Chunks 1.1 and 1.2 fix every consumer; 1.3 prevents persistent chat poison. |
| Live research board recorded activity but no queries/URLs | Chunk 2.1. |
| QC readiness/report contradicted the partial research manifest | Chunks 3.1-3.3. |
| Terminal events absent from `events.jsonl` | Narrowed by the report's refutation: Chunk 6.1 closes stop spans; normal terminal outcomes remain span closes. |
| Final QC cost grew with 45 candidates and no cross-lens dedup | Chunk 5.2 adds conservative lineage-preserving consolidation. |
| Morning stop returned 409 after the run had settled | No change; report identifies this as harmless. |

## A — Critical provider/run failures

| Source finding heading (abridged) | Disposition |
|---|---|
| Research `pause_turn` drops response container id | Chunk 1.1. |
| User stop/max-tokens persists pending `server_tool_use` | Chunk 1.3. |
| Between-round stop after `pause_turn` persists pending server tool | Consolidated into Chunk 1.3's turn-wide pairing scrub. |
| QC `pause_turn` drops code-execution container id (critical entry) | Chunk 1.1. |
| Same missing container in Final QC `_run_streaming_call` (high entry) | Duplicate; Chunk 1.1. |
| Chat tool loop resends `pause_turn` without container (high entry) | Chunk 1.2. |
| Chat omits container but low search volume hides it (medium entry) | Duplicate; Chunk 1.2. |
| Web-tool builders choose container-backed versions with no downstream support | Design-side duplicate; Chunks 1.1-1.2 plus invariant docs. |

## B — Live visibility and UI failures

| Source finding heading (abridged) | Disposition |
|---|---|
| Research relay ignores start-event input | Chunk 2.1. |
| QC Review Room relay copies delta-only extraction | Chunk 2.1. |
| Chat web chips have blank query/URL | Chunk 2.1. |
| Every normal QC run appears stop-requested/settling | Chunk 2.2, including backend readiness/double-start copy and defensive UI state. |
| Research SSE follower never reconnects | Chunk 2.3. |

## C — Readiness and audit-report honesty

| Source finding heading (abridged) | Disposition |
|---|---|
| Readiness passes `research_complete` when half failed | Chunks 3.1-3.2. Required missing coverage fails; optional missing coverage is truthfully named. |
| QC report derives no limitation from failed research dimensions | Chunks 3.1 and 3.3. |
| Drafting context gives only a count, not missing coverage names | Chunk 3.1. |
| Partial-complete has no must-have dimension | Chunk 3.2; `governing_codes` is required in both shipped modules. |

## D — Cost architecture and metering

| Source finding heading (abridged) | Disposition |
|---|---|
| Strip-at-commit voids the only history cache breakpoint | Chunk 4.2. |
| History/tail cache uses five-minute TTL | Chunk 4.2. |
| All interview breakpoints need uniform one-hour `_cache_control` | Duplicate/design refinement; Chunk 4.2. |
| Cache docstrings falsely claim incremental caching | Chunk 4.2 documentation and regression tests. |
| Verifier one-hour cache writes priced as five-minute writes | Chunk 4.1; also covers interview once Chunk 4.2 enables 1h. |
| All-dimension research failure is never metered | Chunk 4.3. |
| Stopped turn misses output usage terminal delta | Chunk 4.4 with explicit estimate disclosure. |

## E — QC logic, reporting, and responsiveness

| Source finding heading (abridged) | Disposition |
|---|---|
| Verification threshold inversion | Chunk 5.1 under schema/protocol v4. |
| Terminal research/QC events bypass trace mirror | Broad claim superseded by Section G; genuine explicit-stop span gap fixed in Chunk 6.1. |
| Reviewed version 1-based, active version 0-based | Chunk 5.3. |
| Per-lens and run-total request labels describe different populations | Chunk 5.3, extended for consolidation records. |
| Template import blocks event loop | Chunk 6.3. |
| QC event replay is quadratic | Chunk 2.4; same optimization applied to research replay. |

## F — Races and lock hygiene

| Source finding heading (abridged) | Disposition |
|---|---|
| Research stop can cancel/corrupt successor | Chunk 6.1 atomic terminal transaction. |
| Worker terminal events can land in successor log | Duplicate race; Chunk 6.1. |
| Stop terminal event outside lock can land at successor seq 0 | Duplicate race; Chunk 6.1. |
| `_try_resolve` publishes complete before profile adoption | Chunk 6.1, terminal status published last. |
| Tutorial finish/force restore ignore transition build | Chunk 6.2 owner token and busy refusal. The verifier's `pop_scenario` correction is honored. |
| Document diff reads versions without lock | Chunk 6.3 capture-under-lock/diff-outside. |
| Stale research status fetch regresses board/profile | Chunk 2.3 generation and sequence-watermark rejection. |
| QC apply and `/api/doc` build mutable payload outside lock | Chunk 6.3 coherent payload capture. |
| DOCX export performs raw ZIP/render under turn lock | Chunk 6.4 snapshot/render split for both source and normalized paths. |
| Chat request kwargs parse fetched PDFs under turn lock | Chunk 6.4 detached sanitize/build plus pre-send revalidation. |

## G — Claims explicitly refuted by the source report

| Refuted claim | Required non-action |
|---|---|
| Scenario/tutorial transition lacks a paid-run start recheck | Do not add a redundant run-start guard. Existing `active_write` and busy checks remain. Chunk 6.2 only fixes transition ownership and finish/force behavior. |
| Normal terminal outcomes are absent from the forensic record | Do not duplicate every normal terminal event into `events.jsonl`; `research_end`/`qc_end` already close normal spans. Chunk 6.1 fixes explicit stop span closure only. |
| `finding_id` should omit panel/grounding facts to improve dismissal carry | Do not change the conservative hash policy. Chunk 5.2 adds origin material for consolidated candidates rather than weakening identity. |

## Completion audit

Before closing Chunk 6.5, compare this index with the final diff and mark each
implemented row in the master implementation record. Any skipped row needs an
owner-approved deviation explaining why the current code no longer exhibits
the reported behavior. A passing test suite alone is not evidence that every
row was addressed.

