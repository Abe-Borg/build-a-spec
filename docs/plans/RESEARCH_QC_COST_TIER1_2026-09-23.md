# Research and Final QC cost — Tier 1 (no quality change)

Owner: Abraham. Opened 2026-09-23.

**Where the program stands lives in
[`RESEARCH_QC_COST_TIER1_PROGRESS.md`](RESEARCH_QC_COST_TIER1_PROGRESS.md)
and nowhere else.** That file also holds the handoff prompt, the session
procedure, the measurements and the owner decisions. This file is the spec:
what each chunk builds, why, and how to prove it worked.

Written for coding agents that can reason. Each chunk states its intent,
its constraints, the evidence behind its design, and the files, functions
and tests it touches. Where the code has moved since this was written, the
code wins: adapt, and record the difference under that chunk's **As built**
heading rather than rewriting the spec. Where current code makes a design
unsafe, stop and report it with code evidence (the progress file's
`blocked` state).

## Contents

1. [The ask](#1-the-ask)
2. [Where the money goes](#2-where-the-money-goes)
3. [Frozen decisions](#3-frozen-decisions)
4. [Invariants the chunks must keep](#4-invariants-the-chunks-must-keep)
5. [Chunk map](#5-chunk-map)
6. [Chunks](#6-chunks) — [1](#chunk-1--research-cost-profiler) ·
   [2](#chunk-2--staggered-launch-for-calls-that-share-a-cached-prefix) ·
   [3](#chunk-3--warm-the-batched-verifier-cache-with-a-streamed-lead-seat) ·
   [4](#chunk-4--cache-pause_turn-continuations) ·
   [5](#chunk-5--resume-dont-restart-on-a-transient-failure) ·
   [6](#chunk-6--closeout)
7. [Release-note drafts](#7-release-note-drafts)
8. [Measurement procedures (owner-run)](#8-measurement-procedures-owner-run)
9. [Out of scope](#9-out-of-scope)
10. [Appendix: the sizing arithmetic](#10-appendix-the-sizing-arithmetic)

---

## 1. The ask

Abraham, 2026-09-23: requirements research and Final QC still cost more
than he wants. Cut the cost without compromising quality. Small, disclosed
compromises for real savings are open for discussion, but not in this
program.

**Tier 1 changes no model, effort level, prompt text, tool, search or fetch
budget, verifier panel, adjudication rule, grounding rule or output.** It
changes only how and when the same requests reach the provider:

- **Cache reuse.** Calls that share a cached prefix stop writing it several
  times over (Chunks 2 and 3).
- **Continuations read their own cache.** A resumed `pause_turn` stops
  paying full price for the conversation it re-sends (Chunk 4).
- **Retries resume.** A transient failure stops throwing away the work
  already done and paid for (Chunk 5).
- **Measurement.** Chunk 1 adds the research profiler. Every chunk states
  how its effect is measured on real saved files.

Tier 2 (quality trade-offs) is listed in §9 for the record and is not
built here.

---

## 2. Where the money goes

### 2.1 Prices

Per million tokens, from `settings.PRICING` on `master` at `cef31d0`:

| Model | Used by | Input | Output | 5-minute cache write | 1-hour cache write | Cache read |
|---|---|---:|---:|---:|---:|---:|
| Claude Opus 5.5 (`claude-opus-5-5`) | Final QC | $4.00 | $20.00 | $5.00 | $8.00 | $0.20 (0.05× input) |
| Claude Sonnet 5 (`claude-sonnet-5`) | Research, chat | $2.00 | $10.00 | $2.50 | $4.00 | $0.20 (0.1× input) |

- Web search: $10 per 1,000 searches (`settings.WEB_SEARCH_COST`). Web fetch
  bills tokens only.
- Message Batches: 50% off every token class, stacking with the cache
  rates (`settings.BATCH_COST_MULTIPLIER` = 0.5).

### 2.2 The cache rules that matter

Source: the claude-api skill's `shared/prompt-caching.md` and Anthropic's
batch-processing documentation. Re-check them before building a chunk,
because they change.

- **Prefix match.** Render order is tools → system → messages. A one-byte
  difference at position N invalidates every breakpoint at or after N.
  The `cache_control` markers themselves are not part of the key.
- **At most 4 breakpoints per request.**
- **TTL order.** Longer-lived entries must come before shorter-lived ones.
  A 1-hour marker followed by a 5-minute tail is allowed. The reverse is a
  400.
- **Readable only after streaming begins.** An entry can be read only once
  the first response begins streaming. Concurrent requests sharing a
  prefix therefore all miss, and all write. The documented fix is to send
  one request, wait for its first streamed token, then send the rest.
- **Automatic caching.** A top-level `cache_control` on the request places
  one breakpoint on the last cacheable block. If that block is ineligible,
  it walks backward to the nearest eligible one, and skips caching if
  there is none.
  - It uses one of the 4 slots.
  - It defaults to 5 minutes and accepts `ttl: "1h"`.
  - Two documented 400s: all 4 slots already taken by explicit markers,
    and an explicit marker on the last block with a different TTL.
- **Lookback.** A breakpoint walks back at most 20 positions to find an
  earlier entry.
- **Server tools cache after their results.** Web search and web fetch
  write a 5-minute entry after their tool results automatically, when the
  request already uses caching.
- **Batches.** Cache hits are best-effort; Anthropic quotes 30–98% as
  typical.
- **`max_tokens: 0` pre-warm.** Rejected inside a batch and with
  `stream: true`.
- **SDK support.** The SDK (checked on anthropic 1.8.0) accepts top-level
  `cache_control` on `messages.stream`, `messages.create` and batch
  params. `requirements.txt` pins `anthropic>=1.0,<2`; Chunk 4 confirms
  the floor.

### 2.3 Research (`backend/research/engine.py`)

**Setup.** Four dimensions run concurrently (`_RESEARCH_MAX_WORKERS` = 4)
on `settings.RESEARCH_MODEL` at `RESEARCH_EFFORT` (high). Their budgets
(searches / fetches, from `backend/spec_modules/`) are governing codes
40/12, AHJ 32/10, client standards 24/8, site 16/8.

**No shared lineage.** Each dimension is its own cache lineage from byte 0:
its `web_search` tool carries its own `max_uses`, and tools render first.
Staggering the launch therefore buys research nothing.

**One breakpoint in the messages.** Each request carries three explicit
5-minute markers: the last tool, the system block, and user block 0
(`_dimension_user_content`). Nothing after block 0 is marked.

**Continuations pay full price (Chunk 4).**
- A dimension runs a long server-tool loop and pauses repeatedly
  (`RESEARCH_MAX_CONTINUATIONS` = 16).
- Each continuation re-sends the whole conversation. Block 0 reads from
  the cache; block 1 and every re-sent assistant turn bill as uncached
  input — thinking, search results, fetched pages.
- The provider already wrote 5-minute entries after those tool results,
  inside the previous request. The continuation has no breakpoint that
  can reach them.

**Retries start over (Chunk 5).** A retryable failure (429, 5xx,
connection) abandons the conversation, and the dimension starts over from
its first request (`_run_dimension`'s retry loop). Every continuation
already done is paid for again.

**Nothing reads the cost back (Chunk 1).** `DimensionStatus` persists usage
per dimension per round: uncached input, cache read, cache write, output,
searches and fetches. It has no per-request counts, and no tool reads it
back as a cost report.

### 2.4 Final QC (`backend/qc/engine.py`)

**Phase 1 (Chunk 2).**
- Five lenses run concurrently on `QC_MODEL` at `QC_LENS_EFFORT` (high),
  with 5-minute entries.
- The four web-toolless lenses share one cache lineage: the same tools,
  system and block 0, with the lens brief in block 1. `code_compliance`
  carries web tools and is a lineage of its own.
- All four start together, so all four miss and all four write.

**Consolidation (Chunk 2).** One grouping call per eligible bucket runs
concurrently, all sharing `_consolidation_shared_prefix`. With two or more
buckets, they hit the same problem.

**Phase 2 (Chunk 3, gated on a measurement).**
- Every verifier seat runs batched by default (`QC_BATCH_VERIFICATION`),
  with a 1-hour TTL, at `QC_VERIFIER_EFFORT` (medium).
- There are two lineages. Seats on `code_compliance` findings carry web
  tools; every other seat carries only the verdict tool.
- Each batch round submits its seats together (one batch per round). So
  how many seats read the shared prefix, and how many write it, depends
  on how the provider schedules the batch.
- `tools/qc_export_cost_profile.py` measures this from a saved export. No
  one has run it on a real export yet: it is step 2 of
  `docs/review-results/2026-09-09/EXECUTION_RECORD.md`.

**Continuations and retries (Chunks 4 and 5).** The `code_compliance` lens
and its web-lineage seats pause as research does. Streamed ones re-bill
their continuations, and every retry restarts.

### 2.5 Measured, and only modelled

The sizes in §10 are arithmetic from list prices and representative prompt
sizes. They are not measurements, and this repository has already shipped
one cost model built on a dropped term (PR #166's review). So:

- Every chunk states how its effect is measured.
- Abraham runs the (free, local) profilers on real saved files.
- The numbers go into the progress file.
- A gate is decided by recorded numbers, never by this file's estimates.

---

## 3. Frozen decisions

These bind every chunk. Changing one is an owner decision, recorded in the
progress file.

| # | Decision |
|---|---|
| F1 | **No quality change.** Same models, efforts, prompt text, tool sets and `max_uses`, panel sizes, adjudication, consolidation rules, grounding and output schemas. A chunk that cannot deliver its saving without touching one of these stops and reports. It does not drift into Tier 2. |
| F2 | **One request shape.** `_qc_request_kwargs` stays the only builder of a QC call's tools, system, thinking and effort, for both transports; research keeps its own. Anything new rides beside the request as a top-level argument, as `container` does, never inside a content block. |
| F3 | **Retained Final QC results stay current.** No change to `build_qc_input_manifest`, `QC_REPORT_SCHEMA_VERSION` or `QC_PROTOCOL_VERSION`. Transport and cache mechanics are not review inputs, and a stale flip costs Abraham a paid re-run. Every chunk that touches QC adds a test proving a retained result still `matches_inputs` with the chunk's switch in either position. |
| F4 | **The accounting reconciles.** Every billed call is still exactly one record. A record carries its own `cost_multiplier`: 1.0 when streamed, `BATCH_COST_MULTIPLIER` when batched. No new record type. `_audit_accounting_consistent` holds for every run a chunk can produce. |
| F5 | **An operator switch per behaviour.** Chunks 2–4 each ship a switch with a row in README's Configuration table. Integer knobs go through `_int_env(..., minimum=...)`, which `test_every_shipped_knob_declares_a_floor` enforces. **A switch defaults on only when a measurement or a documented guarantee supports it.** Chunk 5 has no switch, for the reason given there. |
| F6 | **Hermetic tests only.** Use the fakes in `tests/fakes.py`. A paid check is run by Abraham, with the request count, expected spend and cleanup stated in advance. |
| F7 | **No version bump and no `backend/release_notes.py` entry in any chunk.** Each chunk writes its user-facing note into §7 here, and Abraham picks the release. As of 2026-09-23 none is planned; `settings.VERSION` is 1.21.0, and v1.20.0 is the latest published release. |
| F8 | **Measure, don't model** (§2.5). |
| F9 | **One chunk per session, one pull request per chunk.** |

---

## 4. Invariants the chunks must keep

Each item is in `CLAUDE.md`; the section names are given so a session can
read the reasoning.

- **Cache layout.** TTLs are non-increasing through the request, and a
  request has at most 4 breakpoints. QC's explicit markers share one TTL
  per request (`_cache_control`; the PR #82 lesson, "Final QC cost +
  speed"). A tail may be shorter than the markers before it: the chat's
  `CHAT_TAIL_CACHE_TTL` is the precedent ("Rolling chat cache
  breakpoint").
- **The `pause_turn` contract.** Re-send `response.content` verbatim, and
  run `sanitize_messages_for_resend` before each resend. The trailing
  `server_tool_use` of a paused turn is legitimate ("Conversation engine
  invariants", the server-tool pairing bullet).
- **Containers.** A container is attempt-local today ("Server-tool caller
  mode", Chunk 1.2). Chunk 5 makes it conversation-local and must update
  every docstring and note that says otherwise.
- **Stop.** A research stop is lossy but bills what ran ("A failed
  research round is still a paid round"). A QC stop settles ("A stopped
  batch is settled, not written off"). A new wait must be stop-aware and
  bounded. A new thread must be joined before its phase returns, so its
  spend reaches the record.
- **Live events.** No new SSE event types. An existing payload gains a
  key only where no exact-dict test pins it; `verification_started` is
  pinned in `tests/test_qc_live_events.py`. `stream_end` stays exactly
  `{type, status}`.
- **Billing.** Every billed response is counted exactly once, including
  across a retry. `test_retry_success_counts_billed_usage_from_abandoned_attempt`
  and `test_the_failed_round_bill_counts_a_retried_attempt_exactly_once`
  pin it.
- **The chat loop is out of scope.** `backend/llm/conversation.py` already
  caches its tail.
- **Windows is the primary platform.** Time-based tests use events or a
  stepped clock, never real sleeps ("A test that had only ever run on
  Linux").
- **Copy-don't-import between research and QC.** The two engines keep
  separate copies of shared helpers, as they already do.

---

## 5. Chunk map

| # | Chunk | Lever | Depends on | Switch (default) | Expected effect (§10) |
|---|---|---|---|---|---|
| 1 | Research cost profiler | measurement | — | none (a developer tool) | makes Chunks 4 and 5 measurable |
| 2 | Staggered launch for calls sharing a cached prefix | QC phase 1 and consolidation reuse one cache write | 1 (order only) | `BUILD_A_SPEC_QC_WARM_WAIT_SECONDS` (45) | ~$0.40–1.20 per Final QC |
| 3 | Streamed lead seat warms the batch | QC phase 2 reuses one cache write per lineage | 2 | `BUILD_A_SPEC_QC_BATCH_WARM_LEAD` (see its gate) | gated on M2; up to ~$2 per Final QC when the batch reuses little |
| 4 | Cache `pause_turn` continuations | continuations read the previous request's cache | 1 | `BUILD_A_SPEC_CONTINUATION_CACHE` (**off** until M3 passes) | research ~$1–2.50 per round; QC less |
| 5 | Resume, don't restart, on a transient failure | stop re-paying finished continuations | 4 | none | situational; removes worst-case double bills |
| 6 | Closeout | measurements, docs, release notes | 1–5 | — | — |

Chunk 3 may be skipped by its gate. Chunk 4's flip to on happens in a
later session, once M3 passes (Chunk 4, "Flip").

---

## 6. Chunks

Every chunk shares the done-checklist in the progress file. The sections
below give what is specific to each.

### Chunk 1 — Research cost profiler

**Goal.** A read-only script that reports what saved research rounds cost,
and how much of their input came from the cache. It is the before-and-after
instrument for Chunks 4 and 5, and the sibling of
`tools/qc_export_cost_profile.py`.

**Depends on:** nothing. **Switch:** none.

**Files.**
- New: `tools/research_cost_profile.py`, `tests/test_research_cost_profile.py`.
- Edited: `tests/test_docs_consistency.py` (add the script to
  `_WINDOWS_COMMAND_DOCS`, so its usage docstring is policed like the
  other profilers'), README, CLAUDE.md, and the progress file.

**Design.**

1. **CLI.** Copy the QC profiler's shape. Globs are expanded by the
   script, because PowerShell does not expand them.

   ```powershell
   .\.venv\Scripts\python tools\research_cost_profile.py "C:\specs\*.baspec" "C:\specs\Project.basproject" --out research-measurement.md
   ```

   `--model <id>` overrides the model the rounds are priced at.
2. **Inputs.**
   - `.baspec` packages and legacy `.json` projects, via
     `backend.spec_doc.project_package.parse_project_file`; the profile is
     the payload's `requirements_profile`.
   - `.basproject` briefs (JSON); the profile is `research_profile`.
   - Read the raw dicts. **Never import `backend.research.engine`**: it
     imports `backend.llm.client`, and like the QC profiler this script
     must never load the client. Reimplement the few lines you need.
3. **Rounds.**
   - Iterate `profile["rounds"]`.
   - Key each round by `round_id`, or, when that is empty, by
     `(section, research_date, round_index)`. Deduplicate across files: a
     brief and the sections seeded from it share rounds.
   - A profile with no `rounds` is one legacy round built from its
     top-level `dimension_statuses` (what `RequirementsProfile.from_dict`
     does). Label it "legacy, cumulative".
4. **Per dimension per round.**
   - Report: status, searches, fetches, uncached input, cache read, cache
     write and output.
   - Derive the uncached share of the input side:
     `input / (input + cache read + cache write)`.
   - Estimate cost at the rates of `settings.RESEARCH_MODEL` (or
     `--model`) from `settings.PRICING`, plus searches at
     `settings.WEB_SEARCH_COST`. Research writes only 5-minute entries, so
     every cache write prices at `cache_write`.
   - Report output's share of the cost.
5. **Roll-ups.** Per round and overall. The headline line states the
   uncached share of the input side for the whole set: it is the number
   Chunk 4 exists to move, and the baseline M1 records.
6. **Caveats, printed in the output.**
   - The model that ran a round is not recorded, so rounds are priced at
     the current research model.
   - There are no per-request counts, so continuations cannot be counted.
   - A failed dimension was still billed and is included.
7. **Privacy** (the QC profiler's rules).
   - Print counts, tokens, rates and ratios only. Dimension ids, round
     dates and section numbers are fine.
   - Never print requirement text, URLs, client or project names, or
     filenames in the pasteable output.
   - Identify each artifact by a SHA-256 prefix of its bytes. Name the
     file only on the stderr console line.
8. **Exit code.** 0 on success. Non-zero with a one-line message when no
   file holds readable research.

**Tests** (`tests/test_research_cost_profile.py`; call
`research_cost_profile.main([...])` as `tests/test_history_hygiene.py`
calls the chat-history profiler):

- `test_a_saved_project_reports_each_rounds_dimensions`. Build the
  `.baspec` through the production save path (`sessions.project_package`),
  not a hand-written dict, so the test tracks the real shape. Give the
  round known usage with `research_response(..., tokens=...)` and the
  `SequencedFakeClient` used by `tests/test_research_rounds.py`.
- `test_a_round_shared_by_a_brief_and_its_section_counts_once`.
- `test_a_legacy_profile_without_rounds_is_one_cumulative_round`.
- `test_the_cost_arithmetic_matches_the_pricing_table`: golden numbers
  from `settings.PRICING[settings.RESEARCH_MODEL]`.
- `test_the_output_carries_no_text_names_or_paths`: a requirement probe, a
  URL and a client name in both the filename and the profile; none of
  them may appear in the report.
- `test_the_script_never_loads_the_client`: in a subprocess, import the
  module and assert `"backend.llm.client" not in sys.modules`.
- `test_nothing_readable_is_a_clean_error`.

**Docs.**
- README: open a new top-level section,
  `## Research and Final QC cost (in progress)`, after the other
  in-progress programs and before `## Shipped in v1.20.0`. Give it a
  one-paragraph intro pointing at this plan, and a `### Measure what
  research costs (Chunk 1)` subsection with the command.
- CLAUDE.md: an implemented-notes section before "Source-of-truth
  pointers".
- `docs/RELEASE_WINDOWS.md`: no rows; this is a developer tool.

**Acceptance.** The suite, ruff and the frontend gates are clean, and the
tests above pass.

**Measurement after merge.** Abraham runs the profiler on one to three
saved projects that have research (**M1**, §8). It is free and local.

**Release note:** none; the tool is not part of the app.

#### As built

*(Filled in by the session that builds this chunk.)*

---

### Chunk 2 — Staggered launch for calls that share a cached prefix

**Goal.** When several QC calls share one cached prefix and would start
together, send one first. Wait for its first streamed output, which is the
moment its cache entry becomes readable, then send the rest. This applies
to phase 1's web-toolless lenses and to the consolidation grouping calls.

**Depends on:** Chunk 1, for order only.

**Switch.** `BUILD_A_SPEC_QC_WARM_WAIT_SECONDS` → `settings.QC_WARM_WAIT_SECONDS`.
- An integer, default 45, declared `minimum=0`.
- It is the longest a follower waits for its leader's first output.
- `0` switches staggering off: everything starts at once, as today.
- Chunk 3 reuses it.
- **Default on:** the guarantee is documented, and a follower that finds
  no readable entry simply writes, exactly as today.

**Design.**

1. **Signal first output from `_run_streaming_call`.** Add
   `first_output: threading.Event | None = None`.
   - `_relay_stream_activity` gains the same argument. It sets the event
     on the first raw event whose `type` is not `message_start`: the first
     content output, which follows prefill. The fakes' synthesized streams
     emit no `message_start`, so their first event releases.
   - Wrap each request's `with client.messages.stream(...)` so the event
     is also set when that request ends, raised or not. A first request
     that fails with a 429 or 5xx then releases the followers instead of
     holding them through the retry backoff: its entry was never written,
     and waiting buys nothing.
   - A function-level `finally` sets it on every return path, including
     a stop before the first request.
   - `Event.set()` is idempotent.
2. **Name the lineage.** Add `_prefix_lineage_key(*, tools, system_prompt,
   shared_prefix, model, effort, cache_ttl) -> str`: a SHA-256 over the
   canonical JSON (`sort_keys=True`) of exactly what precedes the block-0
   breakpoint, plus the settings that fork a cache (model, effort, TTL).
   Two calls share a lineage exactly when their keys match. Compute it
   from the real builders: `_lens_tools(lens, model)`,
   `_lens_system_prompt(module)` and `_lens_shared_prefix(...)` for
   lenses, and the consolidation equivalents. **Do not hard-code "the
   four web-toolless lenses"**: the key decides, so a later lens change
   stays correct.
3. **One launcher helper**, for example `_launch_staggered(pool, calls, *,
   key_of, submit, wait_seconds, should_stop) -> dict[Future, item]`:
   - Group the calls by key.
   - Submit each lineage's leader first: its first call in the input
     order (`QC_LENSES` order, or bucket order), with a fresh `Event`.
   - Then submit every single-call lineage (for example
     `code_compliance`).
   - Then wait on each leader's event with a bounded, stop-aware wait:
     `event.wait(timeout=min(1.0, remaining))` in a loop that checks
     `should_stop()` and a `time.monotonic()` deadline.
   - Then submit the followers.
   - **Leaders go before single-call lineages** so a small pool
     (`QC_MAX_WORKERS=1`) never parks a leader behind a minutes-long call
     while its followers wait.
   - **On a stop, submit the followers anyway.** Each one's
     `_run_lens` / `_run_streaming_call` sees `should_stop()` first and
     returns cancelled without sending a request. The phase's records
     stay complete, and the existing cancellation paths stay unchanged.
4. **Phase 1.** In `run_final_qc`, replace the dict-comprehension submit
   with the helper. `as_completed` collection is unchanged, and the pool
   size stays `min(_qc_max_workers(), len(QC_LENSES))`. Confirm that
   `lens_statuses` still come out in `QC_LENSES` order, and pin it.
5. **Consolidation.** Run the same helper over `eligible` in
   `_consolidate_candidates`; `_run_consolidation_call` forwards
   `first_output` to `_run_streaming_call`.
6. **Diagnostics.** Log one INFO line per wait. `backend/qc/engine.py` has
   no logger yet; add `logging.getLogger("buildaspec.qc")`, following the
   app's `buildaspec.*` names. Log the lineage size, the outcome (`warm`,
   `timeout` or `stopped`) and the milliseconds waited. Emit no new SSE event: the Review Room shows the
   followers queued until their `lens_started`, which is accurate.

**Edge cases to pin.**
- `QC_WARM_WAIT_SECONDS=0` submits exactly as today.
- A leader that fails fast releases at once.
- A leader that never produces output releases the followers at the
  bound.
- A stop during the wait.
- One eligible consolidation bucket means no wait.

**Tests** (new file `tests/test_qc_warm_launch.py`, built on
`SequencedFakeClient` plus a small blocking wrapper that holds the
leader's stream after its first event until the test releases it):

- `test_followers_start_only_after_the_leader_streams`. The three
  followers' requests are captured only after the leader's first event,
  and `code_compliance` is never held.
- `test_a_leader_that_fails_before_streaming_releases_the_followers`: the
  followers start before the leader's retry backoff ends (record
  `engine.time.sleep`).
- `test_a_stop_during_the_wait_sends_no_follower_request`: the followers
  are recorded cancelled, with no captured request.
- `test_the_wait_is_bounded`: a leader that never produces output releases
  the followers at `QC_WARM_WAIT_SECONDS`. Use a stepped clock or a
  monkeypatched wait, never a real 45 seconds.
- `test_zero_wait_launches_everything_at_once`.
- `test_the_lineage_key_groups_exactly_the_calls_that_share_a_prefix`:
  the four web-toolless lenses share a key; `code_compliance` does not;
  changing the model, effort, TTL or tools changes the key.
- `test_consolidation_buckets_stagger_behind_the_first`.
- `test_staggering_changes_no_request_bytes`: the multiset of captured
  requests equals a zero-wait run's.
- `test_a_retained_result_stays_current_across_the_switch` (F3).
- Keep green, unchanged:
  `test_qc_requests_cache_the_shared_prefix_across_the_whole_fan_out`,
  the live-event tests (per-worker order, never global), and `tests/test_qc.py`.

**Measurement.** After a real Final QC made with this build, the QC
profiler's line "Phase 1, the 4 web-toolless lenses: X read the shared
prefix, Y wrote one" should read **3 read, 1 wrote**. The expected baseline
is about 0 read and 4 wrote.

**Docs.**
- README: a subsection under the program heading, and a Configuration row
  for the knob.
- CLAUDE.md: implemented notes, plus the `backend/qc/engine.py` Layout
  entry.
- `docs/RELEASE_WINDOWS.md`, one QA row: after pressing Run Final QC,
  three lens cards stay queued for a few seconds, the review completes,
  and the report's per-lens cache columns show three lenses reading.
- The trust dossier (`frontend/src/components/TrustDeepDiveModal.tsx`):
  if its Final QC card says the five lenses start together, say one goes
  a few seconds first. It is a contract, not a brochure.

**Release note:** §7, Chunk 2.

#### As built

*(Filled in by the session that builds this chunk.)*

---

### Chunk 3 — Warm the batched verifier cache with a streamed lead seat

**Gate.** Decide this before writing code, and record the decision in the
progress file. Use **M2** (§8): the QC profiler on a real batched Final QC
export.

- **Skip** when every lineage that has batched seats shows a token-weighted
  read share of **85% or more**. That is the profiler's "Phase 2 batched
  `web-tooled` / `no-web` seats: … token-weighted read share X%" line. The
  provider already reuses the cache there, and a lead would only add cost.
  Mark the chunk `skipped (measured)` with the numbers, and continue with
  Chunk 4 in the same session.
- **Build, default on** when the recorded share is below 85%.
- **Build, default off** when M2 was not provided. The build is then
  unmeasured, so it does not go on (F5). The post-merge message asks for
  M3, which covers it.

The break-even arithmetic is §10.3.

**Goal.** When a phase-2 batch would carry at least `_WARM_LEAD_MIN_SEATS`
seats of one cache lineage, stream one of them first at list price. Submit
the rest only after its first output, so they read the 1-hour entry it
wrote instead of each writing their own.

**Depends on:** Chunk 2, for `first_output`, `_prefix_lineage_key` and the
bounded wait.

**Switch.** `BUILD_A_SPEC_QC_BATCH_WARM_LEAD` → `settings.QC_BATCH_WARM_LEAD`,
a boolean whose default follows the gate. It is also inert when
`QC_WARM_WAIT_SECONDS` is 0.

**Why a real seat, and not a `max_tokens: 0` pre-warm.** A pre-warm is
billed but is not a seat. It would need a new record type in `QCResult`
(F4) and new report rows, and it cannot run inside the batch anyway. A
streamed lead is an ordinary seat with an ordinary record at
`cost_multiplier` 1.0, which the mixed-multiplier accounting has handled
since v1.12.0. What the lead costs is its batch discount.

**Design.**

1. **Pick the leads.** `_run_batch_calls(..., warm_leads: bool = False)`.
   When true:
   - Group `specs` by `_prefix_lineage_key(...)`.
   - For each lineage with at least `_WARM_LEAD_MIN_SEATS` seats (a
     module constant, **8**; §10.3 gives the reason), the lead is its
     first key in `specs` order, which is submission order and therefore
     deterministic.
2. **Stream the leads.** Run each lead on a small `ThreadPoolExecutor`,
   at most one worker per lineage, calling `_run_streaming_call` with:
   - the lead's `_CallSpec` fields;
   - `event_prefix=seat_event_prefix` and `event_fields=fields_for[key]`;
   - the caller's `event_sink` and `should_stop`;
   - a `first_output` event.

   The lead's `verifier_activity`, `_search`, `_fetch` and `_retry` frames
   are real and are emitted. The frontend fold already handles per-seat
   frames on either transport.
3. **Wait.** Wait on every lead's event with Chunk 2's bounded, stop-aware
   wait (`QC_WARM_WAIT_SECONDS`). On a stop, take the existing stop path:
   no batch is submitted, and the leads are joined.
4. **Batch the rest.** The round loop submits only non-lead seats: its
   submission list excludes seats flagged `streamed=True`. Keep a
   `_BatchSeatState` for each lead, for bookkeeping.
5. **Keep all state on the coordinator thread.**
   - Poll the lead futures at each poll and round boundary. Fold a
     finished lead with `states[key].settled = future.result()`.
   - `emit()`'s `settled` therefore counts a lead once it finishes;
     `total` stays `len(states)`.
6. **Join the leads on every return path.** Use a `try/finally` around
   the loop, or one `finish()` helper; their requests were billed and must
   reach the record.
   - The lead is the one seat the batch settlement window does not bound.
   - It is bounded the way any streamed seat is: `should_stop` between
     requests, and the SDK timeout within one. That is the guarantee the
     streaming transport already gives in its settling state. Say so in
     the docstring.
7. **Price the lead at list.** `_BatchPhaseOutcome` gains
   `streamed_keys: frozenset[str]`. The batch branch in `run_final_qc`
   stops hard-coding `cost_multiplier=settings.BATCH_COST_MULTIPLIER`, and
   passes 1.0 for a streamed key.
   - The meter split (`usage_by_meter_category`) reads each record's
     multiplier, so the lead lands in the `qc` bucket with no change
     there.
   - `batch_usage_capture` is unaffected: a lead has
     `uncollected_requests = 0`, and a run with a lead always has batched
     seats too.
8. **Live events.** Do not add keys to `verification_started`; an
   exact-dict test pins it. `verification_batch` frames keep their
   phase-level `total` and `settled`, now counting the leads.
9. **Methodology copy.** Both report projections must carry the same
   sentence (the Chunk 5.3 contract): the Word memo (`docx_export`) and
   `QCReportModal`. Something like: "When a batch carries several seats
   that share one cached document, one of them is sent first, streamed at
   full price, so the rest of the batch can read its cached copy."
   - Find the existing batch methodology text by grep.
   - Pin that the two projections match, as
     `test_the_memo_methodology_states_the_v4_rule_the_report_modal_states`
     does.
   - Update README's Final QC transport description and the trust
     dossier's Final QC card wherever they describe phase 2 as one batch
     at half price.

**Existing tests to update in place** (record each under As built):
- `test_every_seat_rides_one_batch_under_its_own_custom_id` becomes "every
  non-lead seat".
- `test_a_batched_seat_emits_no_live_activity_frames`: batched seats still
  emit none; the lead emits its own.
- `test_batching_actually_lowers_the_reported_run_cost` must still lower
  it, with the lead at list price.
- `test_batched_and_streamed_verification_reach_the_same_verdicts` must
  stay true as written: parity is the point.

Where a test is about the batch contract itself, pin `warm_leads=False`,
as `tests/test_qc_verifier_v3.py` pins `batch_verification=False` (the
precedent in CLAUDE.md's "Final QC phase 2 is batched").

**New tests** (`tests/test_qc_batch_warm_lead.py`):
- `test_one_lead_per_large_lineage_streams_before_the_batch_is_created`
- `test_a_lineage_below_the_minimum_has_no_lead`
- `test_the_lead_is_priced_at_list_and_the_rest_at_the_batch_rate`: the
  record multipliers, the `qc` and `qc_batched` meter buckets, and
  `_audit_accounting_consistent`.
- `test_a_lead_that_fails_before_streaming_still_releases_the_batch`, with
  its failed seat recorded.
- `test_a_stop_during_the_lead_wait_submits_no_batch_and_joins_the_lead`
- `test_every_return_path_joins_the_leads`: a non-retryable refused
  submission, the wall-clock ceiling and the round ceiling. The lead's
  usage is in the record each time.
- `test_the_switch_off_is_todays_batch_exactly`: request bytes and counts.
- `test_a_retained_result_stays_current_across_the_switch` (F3).
- `test_the_methodology_sentence_is_the_same_in_both_projections`.
- Frontend, `frontend/tests/qcLive.test.ts`: a batch-transport run where
  one seat streams `verifier_activity` frames folds them into that seat's
  panel, and the batch line counts it settled once its `verifier_complete`
  lands.

**Measurement.** On a Final QC made with the switch on, the QC profiler
should show:
- nearly every batched seat reading a cached prefix;
- a `list-price` seat line with one seat per warmed lineage.

This is part of M3 when the chunk ships default off, and M4 otherwise.

**Docs.**
- README: the Configuration row and a subsection.
- CLAUDE.md: implemented notes, plus the Layout entry for
  `backend/qc/engine.py` / `_run_batch_calls`.
- `docs/RELEASE_WINDOWS.md`, one QA row: in the Review Room, one seat per
  candidate type shows live activity while the rest wait on the batch,
  and the report's usage table prices that seat at list.

**Release note:** §7, Chunk 3. It only ships if the chunk is built and on.

#### As built

*(Filled in by the session that builds this chunk.)*

---

### Chunk 4 — Cache `pause_turn` continuations

**Goal.** A continuation request — one that resumes a paused turn —
carries a top-level automatic cache breakpoint. It then reads the entries
the provider already wrote after the previous request's tool results,
instead of paying full input price for the turn it re-sends.

**Depends on:** Chunk 1, for the measurement.

**Switch.** `BUILD_A_SPEC_CONTINUATION_CACHE` → `settings.CONTINUATION_CACHE`,
a boolean, **default off in this chunk**.
- The default flips on in a later session, only after Abraham's trial
  (M3) passes. This is compaction Phase 2's precedent: PR #183 shipped the
  trim off, the live canary passed, and PR #194 turned it on.
- The shape is allowed by the documented rules. But a provider rejection
  would fail every paused research dimension and compliance lens with a
  400, so it is proven live before it is on for anyone.

**Design.**

1. **Where.** The two streaming loops that resume a pause: research
   `_run_dimension` and QC `_run_streaming_call`. Not the batch transport
   (point 5). Not the chat loop, which already caches its tail.
2. **When.** Only on a continuation request, that is when
   `messages[-1]["role"] == "assistant"`.
   - Never on a first request. Its tail is the unique block 1, and a
     breakpoint there is a pure write surcharge: the documentation's "the
     prompt ends in unique per-request content" case.
3. **What.** `stream_kwargs["cache_control"] = {"type": "ephemeral"}`:
   top-level, with the shortest (5-minute) TTL.
   - It goes on the per-request `stream_kwargs` copy beside `container`,
     never into `request_kwargs` or any block (F2).
   - Five minutes is right for both TTL families. A continuation's reader
     is the next continuation, seconds later. A 5-minute tail after
     1-hour markers is allowed. A 1-hour tail would pay double for an
     entry nothing reads after five minutes.
   - Use one module constant per engine (for example
     `_CONTINUATION_CACHE_CONTROL`); the two engines copy, not import.
4. **Slots.** Every request that carries it has exactly three explicit
   markers (the last tool, the system block and block 0), so the
   automatic one is the fourth.
   - A continuation's last block is re-sent paused content, which is
     never explicitly marked, so the "explicit marker on the last block
     with a different TTL" 400 cannot arise.
   - Pin both facts with a guard test that counts markers on every
     captured request.
5. **Why not batches.** Batch rounds are minutes apart. The 5-minute
   entries the previous request's server loop wrote have usually expired
   by the next round. A tail there pays the write premium on the whole
   re-sent turn and reads nothing. Say so in the comment. If a later
   measurement shows batched continuations are common and long, a 1-hour
   batch tail is a separate decision.
6. **Why it should work.** Record this in the comment, marked as
   measured by M3.
   - The documentation says server tools write a 5-minute entry after
     their tool results when the request already uses caching.
   - A continuation re-sends that content verbatim, so a breakpoint at
     its end can walk back (up to 20 positions) to the last such entry.
     Without a breakpoint after block 0, it cannot.
   - **The worst case** is that the entries do not match. Each
     continuation then pays the 5-minute write premium on its re-sent
     turn: +25% on that part. M3 would show cache writes rising while
     reads do not, and the flip rule below catches it.
7. **Correct the NOTE above `_qc_request_kwargs`.** It says there are
   deliberately no message breakpoints, because re-sent SDK blocks have no
   dict to hang `cache_control` on. The top-level automatic breakpoint is
   the way around that, which the NOTE did not consider. Rewrite it, and
   add a CLAUDE.md erratum for the "Final QC cost + speed" bullet "No
   messages-tail breakpoint (deliberate)".
8. **SDK floor.** Confirm the oldest `anthropic` release `requirements.txt`
   allows accepts top-level `cache_control` on `messages.stream`. If it
   does not, raise the floor to the first release that does, and say so
   in the PR.

**Tests.**
- `tests/test_research_engine.py`:
  - `test_a_research_continuation_carries_the_automatic_breakpoint_when_on`:
    the first request has no top-level `cache_control`; every
    continuation has `{"type": "ephemeral"}`.
  - `test_the_switch_off_sends_todays_research_requests_exactly`.
- QC (`tests/test_qc_live_events.py` or `tests/test_qc.py`): the same two
  for a paused compliance lens and for a streamed web-lineage seat (1-hour
  markers plus a 5-minute tail).
- `test_no_request_exceeds_four_breakpoints`: a guard over every request
  the research and QC fixtures capture. At most three explicit markers;
  when the top-level one is present, no explicit marker on the last
  block; TTLs non-increasing, the tail included.
- `test_batched_continuations_carry_no_automatic_breakpoint`.
- `test_the_container_and_the_breakpoint_ride_side_by_side`: a
  continuation with a container carries both top-level keys, and its
  cached blocks are byte-identical to the first request's.
- `test_a_retained_result_stays_current_across_the_switch` (F3).
- Keep green, unchanged:
  `test_qc_requests_cache_the_shared_prefix_across_the_whole_fan_out`,
  `test_the_research_request_caches_the_shared_half`, and the
  reference- and facts-visibility layout tests.

**Flip.** The first later session that finds an M3 pass recorded
(normally Chunk 5's) makes the flip its own commit:
- set the default to True;
- add `test_continuation_cache_ships_switched_on`, which reads the default
  from the source with `ast` (the page-trim pin's idiom, so no developer's
  environment can make it pass or fail);
- update the README row and text, and the release-note draft.

**M3 decides the flip.**
- **Pass:** no dimension, lens or seat failed with an `invalid_request`
  error; the research profiler shows a lower uncached input share than M1
  on comparable dimensions; and cache writes did not grow faster than
  cache reads.
- **Fail:** the switch stays off. Record the error text, and Chunk 6
  records the flip as owed or abandoned.

**Docs.**
- README: the Configuration row and a subsection.
- CLAUDE.md: implemented notes and the erratum.
- `docs/RELEASE_WINDOWS.md` rows: with the switch on, a research round
  completes, and the profiler shows cache reads on long dimensions.

**Release note:** §7, Chunk 4. It only ships once the default is on.

#### As built

*(Filled in by the session that builds this chunk.)*

---

### Chunk 5 — Resume, don't restart, on a transient failure

**Goal.** A request can fail with a retryable class (rate limit, server
error, connection) after its conversation has made progress. Retry that
same request with the conversation so far, instead of discarding every
completed continuation and starting over.

**Depends on:** Chunk 4. The same loops change, and a resumed continuation
must carry Chunk 4's tail consistently.

**Switch:** none. The rule below keeps a fresh conversation as the last
attempt, so the new path can cost at most one extra attempt, in the rare
case where a conversation keeps failing.

**The rule: resume first, restart last.**
- **Resume** on a retryable failure when the current conversation has at
  least one completed response and this is not the final attempt. Keep
  `messages`, `all_responses`, `container_id` and the continuation count,
  and re-send the request that failed.
- **Restart** otherwise — no progress yet, or the final attempt — exactly
  as today: `billed.extend(all_responses)`, fresh messages, container
  cleared.
- Backoff, attempt counting and the `{prefix}_retry` events are unchanged.
  The events gain a `mode: "resume" | "restart"` field. Check that no
  exact-dict test pins them, and update one knowingly if it does.

**Where.**
1. **Research `_run_dimension`.** Hoist `messages`, `all_responses`,
   `container_id` and the continuation counter out of the attempt loop so
   a resume carries them.
   - The continuation budget (`RESEARCH_MAX_CONTINUATIONS`) counts across
     a resume: it bounds the conversation, not the attempt.
   - The 2× search ceiling keeps reading `all_responses`.
2. **QC `_run_streaming_call`.** The same change.
3. **Batch.** Add `_BatchSeatState.resume_attempt()` beside
   `restart_attempt()`: `attempt += 1`, and keep the messages, responses,
   container and continuation count. Apply the rule in two places:
   - `_apply_batch_item`, for a per-item retryable error;
   - the refused-submission path of `_run_batch_calls`, where nothing ran,
     so a resume re-submits the same messages.

**Billing.** Every response is counted exactly once.
- On a resume, the failed request produced no response object, and
  `all_responses` carries on. The terminal `[*billed, *all_responses]`
  counts each response once.
- Only a restart moves `all_responses` into `billed`.

**Grounding.** A resumed conversation's retrievals belong to the
conversation the model actually saw. They stay in the accepted-evidence
pool, which follows the "pooled across `pause_turn` continuations" rule.
Only a restart moves them to the attempted-only pool.

**Containers.** Containers become conversation-local. Update the
docstrings that say "Reset per ATTEMPT" in both engines and in
`restart_attempt`, and add a CLAUDE.md erratum for the container notes.

**Existing tests to update in place** (record each under As built):
- `test_pause_continuation_echoes_the_container_and_a_retry_drops_it`
  (research) and `test_qc_pause_continuation_echoes_the_container_and_a_retry_drops_it`
  (QC): the first retry now resumes and keeps the container; the final
  attempt's restart drops it. Rename both to say so.
- `test_a_retryable_seat_failure_restarts_on_a_fresh_conversation`
  (batch): the seat now resumes. Add the restart-on-the-last-attempt case.
- Keep green, unchanged:
  `test_retryable_failure_retries_then_succeeds`,
  `test_retry_emits_dimension_retry_event`,
  `test_retry_success_counts_billed_usage_from_abandoned_attempt`,
  `test_the_failed_round_bill_counts_a_retried_attempt_exactly_once`,
  `test_malformed_frames_are_ignored_and_stream_failure_retries` and
  `test_verifier_retry_then_relays_tool_activity_for_the_same_seat`.

**New tests** (research and QC unless marked):
- `test_a_failure_after_continuations_resumes_the_same_conversation`: the
  retried request's messages equal the failed request's, and no earlier
  continuation is re-sent from scratch.
- `test_the_final_attempt_restarts_fresh`.
- `test_a_failure_before_any_response_restarts`.
- `test_a_resumed_conversation_bills_each_response_once`.
- `test_resumed_retrievals_stay_eligible_for_grounding`.
- `test_the_continuation_budget_spans_a_resume`.
- `test_the_retry_event_names_its_mode`.
- Batch: `test_a_refused_submission_resubmits_the_same_messages` and
  `test_a_retryable_item_error_resumes_the_seat`.

**Also in this session.** If M3 is recorded as a pass, flip Chunk 4's
default, as its own commit.

**Docs.**
- README: a subsection.
- CLAUDE.md: implemented notes and the container erratum.
- `docs/RELEASE_WINDOWS.md`: no row. A transient failure cannot be staged
  on demand; the hermetic tests carry this chunk.

**Release note:** §7, Chunk 5.

#### As built

*(Filled in by the session that builds this chunk.)*

---

### Chunk 6 — Closeout

**Goal.** Finish the program, leaving an honest record.

**Depends on:** Chunks 1–5, each complete, skipped or blocked-and-decided.

1. **Reconcile and backfill.** Fill in every merge commit in the progress
   table.
2. **Flip, if owed.** If an M3 pass is recorded and Chunk 4's default is
   still off, flip it (Chunk 4, "Flip"). If M3 was never run, leave the
   default off, and record "Chunk 4 shipped off; flip owed after M3" in
   `docs/plans/README.md`, the compaction plan's precedent.
3. **Revisit Chunk 3.** If it was built default off, apply the same rule
   against M3. If M4 shows its lead did not raise batched reads, turn its
   default off and say why.
4. **Record the after-numbers.** Put Abraham's M4 outputs in the progress
   file.
5. **Consolidate §7** into one "Release-note draft (Tier 1)" block that a
   release can lift verbatim. Mark the items that depend on a switch
   being on at release time.
6. **README.** Retitle the program section "## Research and Final QC cost
   (Tier 1)". Keep every subsection; never shrink the README.
7. **CLAUDE.md.** One program-level implemented-notes paragraph, with
   errata for any earlier section the program falsified.
8. **`docs/plans/README.md`.** Move the entry to complete, with the line
   saying which release-note drafts are owed to the next release.
9. **No version bump**, unless Abraham's prompt for this session asks for
   a release. If it does:
   - ask the GitHub Releases API which version was last published;
   - follow CLAUDE.md's "A released version's entry is frozen";
   - bump all five version sites;
   - write the entry from the consolidated draft.
10. **After merge.** Tell Abraham the program is complete, and list any
    follow-up he owns (for example, running the profilers after his next
    real runs). There is no next-session prompt.

#### As built

*(Filled in by the session that builds this chunk.)*

---

## 7. Release-note drafts

Written for a spec author, not for someone reading `CLAUDE.md`. Chunk 6
consolidates them. A draft marked *(conditional)* ships only if its switch
is on in the release that carries it.

- **Chunk 1.** None; a developer tool.
- **Chunk 2 — Final QC's first stage reuses what it already paid for.**
  Four of Final QC's five reviewers read the same copy of your section.
  They used to start at the same moment, so each paid to store its own
  copy. Now one starts a few seconds ahead and the other three reuse its
  copy. The reviewers, their instructions and what they find are
  unchanged; the review simply starts a few seconds later.
- **Chunk 3 *(conditional)* — Batched verification reuses what it already
  paid for.** When many of Final QC's verifying reviewers work from the
  same copy of your section, one of them now starts first, at full price,
  so the rest of the batch can read its copy instead of each storing
  their own. The report lists that reviewer at full price, so the cost it
  shows stays exact.
- **Chunk 4 *(conditional)* — Long research stops paying twice for what
  it already read.** A research area that searches the web in several
  steps used to pay full price, at every step, to re-send everything it
  had already found. It now reuses its own cached copy. The code-compliance
  review gets the same benefit. The findings, sources and limits are
  unchanged.
- **Chunk 5 — A dropped connection no longer starts a research area
  over.** A brief rate limit or connection failure used to throw away
  every step a research area or reviewer had already finished, and paid
  for, and start it from the top. The app now retries the step that
  failed and carries on. If that retry fails too, the last attempt still
  starts fresh.

---

## 8. Measurement procedures (owner-run)

All of these are free: they read files on your machine, except where
noted. Paste the output into the prompt of the session that needs it. That
session copies it into the progress file's **Measurements** section, with
the date. The profilers print no document text, names or paths, so the
output is safe to commit.

**M1 — research baseline.** Before Chunk 4's trial.

```powershell
.\.venv\Scripts\python tools\research_cost_profile.py "C:\path\to\a-researched-project.baspec" --out research-baseline.md
```

Use one to three projects researched with the current build, and more
than one round if you have them.

**M2 — Final QC batch baseline.** The gate for Chunk 3.

```powershell
.\.venv\Scripts\python tools\qc_export_cost_profile.py "C:\path\to\FINAL QC REPORT.json" --out qc-baseline.md
```

Use the newest Final QC JSON export you have (Final QC → Download JSON),
made with batch verification at its default. The lines Chunk 3 reads are
"Phase 2 batched … seats: … token-weighted read share". This also closes
step 2 of `docs/review-results/2026-09-09/EXECUTION_RECORD.md`; record it
there as well.

**M3 — trial with the new switches on.** After Chunk 4 merges; after
Chunk 3 too, if it shipped off. This uses real runs you would make anyway,
and costs nothing beyond them. From a source checkout of `master`, with the
frontend built as README's "Install & Run (from source, Windows)" section
describes:

```powershell
$env:BUILD_A_SPEC_CONTINUATION_CACHE = "1"
$env:BUILD_A_SPEC_QC_BATCH_WARM_LEAD = "1"
.\.venv\Scripts\python main.py
```

(In Command Prompt: `set BUILD_A_SPEC_CONTINUATION_CACHE=1` and
`set BUILD_A_SPEC_QC_BATCH_WARM_LEAD=1`, then the same last line.)

1. Run one Research round and one Final QC on a real project.
2. Save the project, and export the Final QC JSON.
3. Run both profilers on the results (M1's and M2's commands).
4. Note any dimension, lens or seat that failed, with its error text.

**M4 — after.** After the program's switches are on in normal use, run M1's
and M2's commands again on new runs. Chunk 6 records the result.

---

## 9. Out of scope

These are recorded so no chunk drifts into them. Each is a quality or
experience trade-off (Tier 2 or later) and needs its own owner decision.

- **Lower effort.** For example, research high → medium, or Final QC's
  lenses high → medium.
- **Smaller search or fetch budgets** per dimension or lens.
- **Fewer verifier seats.** One-seat panels are unsafe here: at one seat,
  a dispute is unreachable (CLAUDE.md, v1.19.0 notes).
- **A cheaper verifier model.** It biases an already refutation-leaning
  panel (CLAUDE.md, "Final QC cost + speed").
- **Dynamic filtering** (the code-execution web-tool caller). It is not
  zero-data-retention eligible by default, and it carries the container
  obligation ("Server-tool caller mode").
- **A shared prefix across research dimensions**, by equalizing their
  budgets.
- **Batching research.** It is half price, but the live agent board would
  go dark.
- **Skipping panels for already-dismissed findings.** It is blocked by the
  finding-id design (CLAUDE.md, v1.19.0 notes).
- **Keep-alive pings** to hold a 5-minute entry open.
- **Trimming the research profile** in QC's cached prefixes.

---

## 10. Appendix: the sizing arithmetic

The numbers are illustrative, from list prices (§2.1). P is the shared
prefix in tokens and O a seat's output tokens.

### 10.1 Chunk 2: phase 1

On Claude Opus 5.5, each follower that reads instead of writing saves the
5-minute write minus the read: ($5.00 − $0.20) per million, times P.

| P | Saving per follower | 3 followers |
|---:|---:|---:|
| 30k | $0.14 | $0.43 |
| 80k | $0.38 | $1.15 |

P grows with the section, the attached references (capped at 25k) and the
recorded project facts. Consolidation saves the same per additional
bucket, on its own prefix.

### 10.2 Chunk 4: continuations

A continuation that re-sends R tokens of turn content pays for it at the
input price today. With the tail breakpoint, it pays the read price if the
previous request's tool-result entries match, or the 5-minute write price
if they do not.

| Model | Today, per million | With the tail, per million | Worst case, per million |
|---|---:|---:|---:|
| Claude Sonnet 5 (research) | $2.00 | $0.20 | $2.50 (+25%) |
| Claude Opus 5.5 (compliance lens, streamed) | $4.00 | $0.20 | $5.00 (+25%) |

For example, a governing-codes dimension with 6 continuations re-sending
60k each saves 6 × 60k × $1.80/M ≈ $0.65. Across a round's four
dimensions that is roughly $1–2.50. M3 tells which case holds.

### 10.3 Chunk 3: the break-even

For one lineage of N seats in a batch, with a fraction h of seats already
reading the prefix today, on Claude Opus 5.5 with a 1-hour TTL at the
batch rate:

- A seat that writes the prefix pays $4.00 per million (the $8.00 write,
  halved).
- A seat that reads it pays $0.10 per million (the $0.20 read, halved).

The saving is (N − 1)(1 − h) × $3.90/M × P, when every non-lead seat reads
after the warm-up. The lead's extra cost is roughly P × ($8 − its batched
expectation) + O × $10/M.

| N | P | O | Break-even h |
|---:|---:|---:|---:|
| 20 | 40k | 5k | ≈ 0.88 |
| 8 | 40k | 3k | ≈ 0.72 |
| 4 | 40k | 3k | ≈ 0.45 |

Hence two rules. A lead is used only on a lineage of at least 8 seats
(`_WARM_LEAD_MIN_SEATS`). The chunk is skipped when M2 shows h ≥ 0.85.
On a lineage of 20 seats, a lead at h = 0.3 saves about $1.80.

**One assumption M3/M4 must confirm:** a streamed request's cache entry is
readable by the batch requests that follow it. Caches are per workspace
and are not documented as separate for the two paths. But nothing states
the sharing outright either, which is why the chunk's default follows the
measurement.

### 10.4 Chunk 5: resumes

A transient failure after k continuations re-pays their input and output
today. On a large research dimension that can be $0.50–2.00 per event.
How often it happens is unmeasured; the value is in removing the worst
case.
