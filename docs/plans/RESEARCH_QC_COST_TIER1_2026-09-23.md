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
| 3 | Streamed lead seat warms the batch | QC phase 2 reuses one cache write per lineage | 2 | `BUILD_A_SPEC_QC_BATCH_WARM_LEAD` (**off** until M3 passes) | built or skipped on M2; up to ~$2 per Final QC when the batch reuses little |
| 4 | Cache `pause_turn` continuations | continuations read the previous request's cache | 1 | `BUILD_A_SPEC_CONTINUATION_CACHE` (**off** until M3 passes) | research ~$1–2.50 per round; QC less |
| 5 | Resume, don't restart, on a transient failure | stop re-paying finished continuations | 4 | none | situational; removes worst-case double bills |
| 6 | Closeout | measurements, docs, release notes | 1–5 | — | — |

Chunk 3 may be skipped by its gate. Chunks 3 and 4 both ship switched
off. Each flips on in a later session, once M3 passes that chunk's own
test (their "Flip" sections).

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

Built on 2026-09-23 from `master` at `3d600d9`. `tools/research_cost_profile.py`
and `tests/test_research_cost_profile.py` are new; README, CLAUDE.md and
`tests/test_docs_consistency.py` are edited as the spec lists. No gate, no
switch, no measurement taken (M1 is the owner's, after the merge).

Deviations and additions, each recorded because the spec text is not
rewritten:

1. **The legacy round key is the engine's hash of the tuple, not the bare
   tuple** (design point 3). When a brief merges research,
   `merge_research_profiles` replays a round saved without a `round_id`
   under `legacy_round_key(round)` — a SHA-256 of
   `("legacy-round", section, research_date, round_index)` — as its NEW
   `round_id`, and renumbers it. Keyed by the bare tuple, the section's copy
   and the brief's copy never meet, and the round is counted twice. The
   script copies `legacy_round_key` byte for byte (never imports it: the
   engine loads the API client) and keys a legacy round by the hash, so a
   carried round meets its original. Pinned by
   `test_a_legacy_round_a_brief_carried_under_its_hash_counts_once`, which
   builds the case with the real merge, and by
   `test_the_legacy_round_key_is_the_engines`.
2. **An "All rounds, by research area" table** beside the per-round tables
   and the overall roll-up (design point 5). Chunk 4's M3 pass compares the
   uncached share "on comparable dimensions", and this is the row it reads.
3. **An "Artifacts" section** listing, per file hash, how many rounds it
   held and how many were already counted from an earlier file, so a reader
   can see the deduplication happen. The same bytes named twice (a glob plus
   a path) are read once.
4. **Privacy by shape, not by field choice** (design point 7). Area ids,
   section numbers, dates and round ids are the fields the spec allows, but
   a saved file is untrusted: each prints only when it matches its shape
   (`^[a-z][a-z0-9_]*$`, digits/spaces/dots/hyphens, ISO date, hex) and
   otherwise as a hash or a fixed word. Status prints as completed or
   failed only; error messages and area titles never print.
5. **`--model` refuses a model with no row in `settings.PRICING`** (exit 2,
   listing the priced models) rather than guessing rates. When the
   CONFIGURED research model has no row, the script falls back to Sonnet 5,
   as the app's own meter does, and the rate line says so.
6. **A brief that leads with a BOM or blank line is still read as a brief.**
7. **Five tests beyond the spec's seven**: a failed area billed and
   included; the legacy-hash case; the two copied constants pinned to the
   app's (`legacy_round_key`, the brief kind, the latter also reading a
   BOM-led brief); and the unpriced-model fallback. Every mechanism was
   reverted in place and turned its own test red (CLAUDE.md, "Research cost
   is measurable", has the matrix).

No existing test changed except `tests/test_docs_consistency.py`, which the
spec lists: the script joins `_WINDOWS_COMMAND_DOCS`, and the comment above
it now says "four profilers and the fetch-elision canary".

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
   - Build each call's pieces once, and hand the same objects to the key
     and to the call. For example, a small helper that returns a lens's
     system prompt, tools and shared prefix, and that `_run_lens` also
     uses. Then the key cannot drift from the request it names.
   - A wrong key cannot break a review: it only costs the saving, or makes
     a follower wait for nothing. Still pin it (the lineage-key test
     below).
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
4. **Phase 1.** `_run_lens` gains `first_output` and forwards it to
   `_run_streaming_call`. In `run_final_qc`, replace the dict-comprehension
   submit with the helper. `as_completed` collection is unchanged, and the pool
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

Built on 2026-09-23 from `master` at `df4d55f`. No gate. No measurement was
supplied (the session's prompt left the M1 placeholder unfilled, read as
"none"). The switch ships **on** (default 45 s), as the spec's F5 reasoning
allows: the guarantee is documented, and a follower that finds nothing
readable writes exactly as before.

Files: `backend/qc/engine.py`, `backend/settings.py`, the new
`tests/test_qc_warm_launch.py`, one knowing change in
`tests/test_qc_live_events.py`, README, CLAUDE.md, `docs/RELEASE_WINDOWS.md`
and the trust dossier (`frontend/src/components/TrustDeepDiveModal.tsx`).

Deviations and additions, each recorded because the spec text is not
rewritten:

1. **`_launch_staggered` takes no `pool`.** Its `submit(item, first_output)`
   closure owns the pool and returns the `Future`, so the launcher is a pure
   scheduling function a test can drive with already-done futures. The
   spec's signature was "for example".
2. **A fourth release point: a done-callback on each leader's `Future`.**
   `_run_lens` checks `should_stop` and returns before it ever calls
   `_run_streaming_call`, so on that path none of the spec's three release
   points fires, and the followers would sit out a wait slice or the bound.
   The callback releases them the moment the leader's task ends for any
   reason. The function-level `finally` in `_run_streaming_call` stays: the
   consolidation calls reach it directly, and it is pinned on its own
   (`test_a_call_stopped_before_its_first_request_still_releases`).
3. **The pieces are a frozen `_CallPieces`**, built by `_lens_call_pieces` /
   `_consolidation_call_pieces` and passed INTO `_run_lens` /
   `_run_consolidation_call`, which no longer build them. Both keep their
   names and their other parameters. `_CallPieces.cache_ttl` rides with
   the pieces so the key and the call read the same TTL (`""`, the 5-minute
   default, passed explicitly now).
4. **`run_final_qc(..., warm_wait_seconds=None)`**, `None` meaning
   `settings.QC_WARM_WAIT_SECONDS`, pinned once per run and handed to both
   phase 1 and consolidation (the `batch_verification` precedent), so the
   two stagger alike even if the environment changes mid-run.
   `_consolidate_candidates` defaults to 0 for a direct caller.
5. **Per-lineage release.** The wait releases each lineage the moment its
   own leader fires, against one deadline shared by all leaders (they are
   sent together). Phase 1 and consolidation each have one shared lineage
   today, but Chunk 3 reuses this.
6. **The log's `warm` outcome also covers a leader released by the end of
   a request or of its task.** The event cannot say which fired, and the
   line reports that the followers were released, not that a cache entry
   exists. Stated in the README.
7. **`_WARM_WAIT_SLICE_SECONDS` (1.0)** is a module constant: how late a
   Stop is noticed during the wait. The stop test lowers it; the wait itself
   still returns the instant its leader releases.
8. **Knowing test change:**
   `test_parallel_lens_activity_interleaves_without_breaking_worker_order`.
   The spec listed it as "keep green, unchanged … (per-worker order, never
   global)". But it asserts a global order, engineered by a rendezvous
   between `completeness` and `coordination_consistency`, which now share a
   lineage: the follower cannot be sent until the leader has already
   emitted, so the rendezvous times out and the order assertion fails
   (reproduced). The test now pairs the leader with `code_compliance`, a
   lineage of its own that still starts alongside it, so it keeps proving
   what its name says under the shipped default. The lens ids are the only
   change.
9. **Tests beyond the spec's list:** a one-worker pool keeps the lineage
   together (see 12); `warm_wait_seconds=None` reads the setting; the relay counts the
   first frame after `message_start`, not `message_start` itself (the fakes
   never emit one, so only a direct test can pin it); a call stopped before
   its first request still releases; the done-callback; one eligible
   consolidation bucket never waits; and the default read from the source
   with `ast`. The F3 test also compares the two runs' input fingerprints.
10. **Not built: staggering the streamed verifier transport.** With
    `BUILD_A_SPEC_QC_BATCH_VERIFICATION=0`, verifier seats share two
    lineages and still start together. The spec scopes this chunk to phase 1
    and consolidation. The default transport is batched, and Chunk 3 is the
    one that addresses it.
11. **The trust dossier overclaimed caching before this chunk.** Its model
    paragraph said every later call in a stage reads the cached copy. In
    phase 1 all four lenses used to start together and write, and calls whose
    tools differ can never share a copy. It now says what happens, and stage
    1 says one lens goes a few seconds first.

12. **Review finding (Codex, PR #210): a single-call lineage never waits in
    the queue ahead of released followers.** The spec's order — leaders,
    then every single-call lineage, then the wait — let a small pool
    (`QC_MAX_WORKERS=1`) queue `code_compliance` behind the leader and ahead
    of its followers, so the sole worker ran the long web-tooled lens in
    between, long enough for the leader's 5-minute entry to expire and a
    follower to pay a second write: worse than the declared order the
    stagger replaced. `_launch_staggered` now takes the pool's `capacity`
    and submits a single-call lineage ahead of the wait only while a worker
    is free for it; the rest go after the followers. With the default 8
    workers nothing changes (`code_compliance` still starts at once and
    never waits). `test_a_one_worker_pool_sends_the_leader_first` became
    `test_a_one_worker_pool_keeps_the_lineage_together`, and
    `test_a_single_call_goes_ahead_of_the_wait_only_while_a_worker_is_free`
    pins the rule directly.

Every mechanism was reverted in place and turned its own test red; the
matrix is in CLAUDE.md ("Final QC's calls that share a cache start
staggered").

---

### Chunk 3 — Warm the batched verifier cache with a streamed lead seat

**Gate.** Two decisions, taken at two different times. Record both in
the progress file.

1. **Build or skip: M2, before any code.** M2 (§8) is the QC profiler run
   on a real batched Final QC export. For each phase-2 lineage in it, take
   that bucket's row in the profiler's table (`seat:batched:web-tooled`,
   `seat:batched:no-web`) and work out three numbers:
   - p = (Cache read + 1h write) / Records: the shared prefix, in tokens
     per seat;
   - h = Cache read / (Cache read + 1h write): the share of that prefix
     the batch already reads today;
   - o = Output / Records: output tokens per seat.

   Do **not** use the profiler's printed "Read share" column. Its
   denominator includes each seat's uncached input, so it is not the
   share of the prefix read. For `web-tooled` seats, continuations re-read
   the prefix, so p is an upper bound and h reads high. That makes the rule
   conservative for that lineage, which is the right direction.

   §10.3 turns (h, p, o) into `n_min`: the smallest lineage, in seats, for
   which a lead saves more than it costs. No single read-share cut-off
   works, because the break-even rises with the lineage's size (§10.3).
   - **Skip** when every measured lineage's `n_min` is larger than that
     lineage's Records in M2, so no real run would have gained. Mark the
     chunk `skipped (measured)` with the numbers, and continue with
     Chunk 4 in the same session.
   - **Build** otherwise. Each lineage type's minimum is its own `n_min`,
     never below 8 (design point 1).
   - **No M2:** build, with both minimums at the fallback of 20. At that
     size a lead still pays while the batch reads up to 88% of the prefix
     today (§10.3, at p = 40k and o = 5k).
2. **Default: M3, after the build.** The chunk always ships **switched
   off**. A lead saves money only if the batch requests that follow it can
   read the cache entry it streamed. Nothing documents that sharing between
   the two transports (§10.3), and M2 cannot show it, because no lead ran.
   The default flips on in a later session, and only on an M3 pass (see
   **Flip**, below).

The break-even arithmetic is §10.3.

**Goal.** When a phase-2 batch would carry at least its lineage type's
minimum number of seats (see the gate) of one cache lineage, stream one of
them first at list price. Submit the rest only after its first output, so
they read the 1-hour entry it wrote instead of each writing their own.

**Depends on:** Chunk 2, for `first_output`, `_prefix_lineage_key` and the
bounded wait.

**Switch.** `BUILD_A_SPEC_QC_BATCH_WARM_LEAD` → `settings.QC_BATCH_WARM_LEAD`,
a boolean, **default off in this chunk** (the gate's second decision). It
is also inert when `QC_WARM_WAIT_SECONDS` is 0.

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
   - A lineage's type is `web-tooled` when its seats carry the web tools
     (the `lens.web` branch of `_verifier_tools`), and `no-web` otherwise.
   - A lineage gets a lead when it has at least its type's minimum seats:
     `_WARM_LEAD_MIN_SEATS_WEB` or `_WARM_LEAD_MIN_SEATS_NO_WEB`. Both are
     module constants that the gate sets from M2 (20 each without M2),
     never below 8. Below 8, one list-price seat is a large share of the
     phase, and a misestimate of p or o outweighs the saving.
   - The lead is the lineage's first key in `specs` order. That is
     submission order, and therefore deterministic.
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

   **Traps in the round loop.** The loop was written for a phase in which
   every unsettled seat is in the batch. A streaming lead is unsettled but
   is not a batched seat, and five places would treat it as one:
   - The submission list comes from `unsettled()`. Exclude streaming
     leads. When every unsettled seat is a lead, stop submitting and join
     the leads: never call `batches.create` with an empty list.
   - `_consume_batch_results(..., submitted=...)` and the `unread` list
     must be built from the keys actually submitted. Otherwise a lead reads
     as a missing result row, and is settled as failed with an uncollected
     request.
   - `settle_all(...)` on a stop, a refused submission or the wall-clock
     ceiling must skip a lead. So must the tail's "did not settle within
     the round ceiling" loop. A lead's record is the `_CallResult` its own
     `_run_streaming_call` returns, and settling it anywhere else would
     overwrite a real, billed record.
   - The refused-submission retry path (`restart_attempt()` today, and
     Chunk 5's resume later) applies to batched seats only.
   - The top-of-loop stop and deadline checks settle `pending`. `pending`
     must exclude leads there too, and the leads are then joined.
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
- `test_the_lineage_minimums_are_never_below_eight`.
- `test_warm_lead_ships_switched_off`: reads the default from the source
  with `ast`, as Chunk 4's pin does. The flip replaces it (**Flip**,
  below).
- Frontend, `frontend/tests/qcLive.test.ts`: a batch-transport run in
  which one seat streams `verifier_activity` and `verifier_search` frames
  folds them into that seat's panel. The batch line still renders the
  phase-level `settled` its `verification_batch` frames report. On the batch
  transport, every seat's `verifier_complete`, the lead's included, arrives
  only after the phase returns.

**Measurement: M3.** On a Final QC made with the switch on, the QC profiler
should show:
- a `seat:list-price:<lineage>` row, with one record per warmed lineage;
- that lineage's `seat:batched:<lineage>` row reading nearly its whole
  prefix: Cache read / (Cache read + 1h write) close to 1.

M3 needs a run in which at least one lineage reaches its minimum. If the
profiler shows no `seat:list-price` row, no lead ran, and M3 says nothing
about this chunk. Record that, and ask for another run.

**Flip.** The first later session that finds a recorded M3 pass for this
chunk makes the flip its own commit:
- set the default to True;
- replace `test_warm_lead_ships_switched_off` with
  `test_warm_lead_ships_switched_on`;
- update the README row and text, and the release-note draft.

**M3 decides the flip.** The test is the arithmetic of §10.3, applied to
the run that happened.
- **Pass:** a lead ran, no seat failed with an `invalid_request` error,
  and in every lineage that had a lead:
  (n − 1) × b × (h₁ − h₀) × (w − r) × p > (1 − b) × C + b × h₀ × (w − r) × p.
  - n counts the batched Records plus the lead. p and h₁ come from the
    lineage's `seat:batched:<lineage>` row, worked out as in the gate.
  - C is the Cost on the lead's `seat:list-price:<lineage>` row. The right
    side is what the lead actually cost beyond a batched seat at the
    baseline: its continuations and searches included. For a lead with no
    continuations it equals §10.3's extra cost.
  - Take h₀ from M2's row for the same lineage type, or 0.88 if M2 was
    never recorded.
  - In words: the batch read enough more of the prefix than it did before
    to pay for the list-price seat. If the two transports share the entry,
    h₁ should be close to 1.
- **Fail:** the switch stays off. Record the numbers. Chunk 6 records the
  flip as owed or abandoned. The most likely cause is that a streamed
  request's cache entry is not readable by the batch.

**Docs.**
- README: the Configuration row and a subsection.
- CLAUDE.md: implemented notes, plus the Layout entry for
  `backend/qc/engine.py` / `_run_batch_calls`.
- `docs/RELEASE_WINDOWS.md`, one QA row: in the Review Room, one seat per
  candidate type shows live activity while the rest wait on the batch,
  and the report's usage table prices that seat at list.

**Release note:** §7, Chunk 3. It only ships if the chunk is built and on.

#### As built

Built on 2026-09-23 from `master` at `ed75f7a`. **Gate, first decision:
build.** No M2 was supplied (the session's prompt left the measurements
placeholder unfilled, read as "none"), so the gate's "No M2" branch holds:
both lineage minimums sit at the fallback of 20 seats, and the chunk ships
**switched off**. Recorded as O2 in the progress file. The second decision
(the default) is untouched: it waits for an M3 pass of this chunk's own
test.

Files: `backend/qc/engine.py`, `backend/settings.py`,
`backend/spec_doc/docx_export.py`, `frontend/src/lib/qcReport.ts`,
`frontend/src/components/QCReportModal.tsx`, doc comments in
`frontend/src/lib/qcLive.ts` and `frontend/src/components/QCDrawer.tsx`, the
new `tests/test_qc_batch_warm_lead.py`, one knowing change in
`tests/test_qc_batch_verification.py`, new cases in
`frontend/tests/qcLive.test.ts` and `frontend/tests/qcReport.test.ts`,
README, CLAUDE.md, `docs/RELEASE_WINDOWS.md` and the trust dossier
(`frontend/src/components/TrustDeepDiveModal.tsx`).

Deviations and additions, each recorded because the spec text is not
rewritten:

1. **One wait, not two.** Chunk 2's wait loop was lifted out of
   `_launch_staggered` into `_await_leaders(leaders, wait_seconds,
   should_stop, on_release)`, and both the staggered launch and the lead
   seat call it, so the two cannot drift into two different waits. Chunk 2's
   tests pass unchanged against the extraction.
2. **The join is `finish()`, plus a backstop in `results()`, not a
   `try/finally`** (the spec allowed either). Every return path of the round
   loop — and `settle_open_batch` — now ends in `finish(status, ...)`, which
   joins the leads FIRST, then emits the terminal `verification_batch`
   frame, then builds the outcome, so "ended" (or "failed", "timeout",
   "cancelled") counts every seat, the leads included. `results()` joins
   again as a backstop for any future path that skips `finish()`. A
   `try/finally` around ~350 lines would have re-indented the whole round
   loop for no additional guarantee. What neither covers: an exception that
   escapes `_run_batch_calls` (a `KeyboardInterrupt` re-raised from
   `batches.create`, or an event sink that raises). The lead then runs out
   on its own thread, bounded like any streamed seat, and the interpreter
   joins its non-daemon worker at exit.
3. **The fold is every-poll and every-round, as specified, and both are
   observable.** A finished lead is folded at the top of each round and at
   each poll; `verification_batch`'s `settled` counts it from then on
   (`test_a_finished_lead_counts_toward_the_batch_line_at_the_next_poll` /
   `_at_the_next_round`, each deterministic: the lead is held until the
   point that only that fold can catch).
4. **`settle_all` skips a lead, and the `results()` backstop joins — both
   defence in depth that no test can observe.** Every settlement a phase-wide
   path writes onto an unfolded lead is overwritten by the join that always
   follows (the join assigns the lead's own `_CallResult` unconditionally),
   and every path joins in `finish()` before `results()` runs. The spec asks
   for the skip; it stays. The revert matrix records both as 0 red by
   design.
5. **The floor is enforced at runtime, not only pinned.**
   `_warm_lead_minimum(kind)` returns `max(_WARM_LEAD_SEAT_FLOOR, constant)`
   (`_WARM_LEAD_SEAT_FLOOR = 8`), so a later gate that lowers a constant
   below 8 cannot slip under the floor.
6. **A lineage's type is read from the tools the seats send** (a
   `web_search` or `web_fetch` tool → `web-tooled`), the same split the QC
   profiler reports by, rather than from a lens list. The lineage itself is
   Chunk 2's `_prefix_lineage_key`, computed from each seat's `_CallSpec`
   (`_spec_lineage_key`), the one object both transports send.
7. **`run_final_qc(..., batch_warm_lead=None)`**, `None` meaning
   `settings.QC_BATCH_WARM_LEAD`, pinned once per run beside
   `warm_wait_seconds` (the `batch_verification` precedent). The switch is
   inert when the wait is 0, as specified. Not in the input manifest (F3).
8. **A lead that raises is recorded, not lost.** `_run_streaming_call`
   never raises by contract; if it ever did, the fold records a failed seat
   with the exception text rather than letting it escape the phase, and the
   done-callback on the lead's `Future` (Chunk 2's fourth release point,
   reused) releases the wait. Pinned by
   `test_a_lead_that_raises_is_recorded_failed_and_releases_the_batch`.
9. **The methodology line is rendered only for a run that sent a lead**,
   in both projections, rather than always: a methodology describing a
   mechanism the run never used describes a review that did not happen (the
   PR #159 lesson). "A lead" is read off the records —
   `docx_export.qc_streamed_lead_seats` / `qcReport.qcStreamedLeadSeats`,
   mirrors: a verifier record at `cost_multiplier` 1.0 in a run where some
   other record is below 1.0; anything that is not a finite number (a bool,
   NaN, a string) reads as list price. The sentence is
   `QC_WARM_LEAD_METHODOLOGY_NOTE`, the same literal in both, pinned equal.
   `QcReportVerdict` gained the optional `cost_multiplier` it was already
   carrying on the wire.
10. **The four named tests were not rewritten in place.** They pin the
    batch contract itself, and none of their lineages reaches any minimum.
    Following the spec's own rule for batch-contract tests, the knowing
    change is in `tests/test_qc_batch_verification.py`'s `_run` helper, which
    now passes `batch_warm_lead=False`, so every test in that file keeps
    meaning what it says whichever way the switch ships. The lead-side
    version of each lives in the new file:
    - "every non-lead seat" →
      `test_one_lead_per_large_lineage_streams_before_the_batch_is_created`
      (the batch carries every non-lead seat, each under its own custom_id);
    - no live frames →
      `test_the_lead_streams_its_own_frames_and_the_batch_stays_quiet`;
    - still lowers the cost → `test_a_lead_still_lowers_the_reported_run_cost`
      (batched < with a lead < streamed; the fake has no cache, so the lead
      can only cost its discount there);
    - parity → `test_a_run_with_a_lead_reaches_the_same_verdicts` (with a
      lead, without one, and streamed).
11. **The return-path test is three tests**, one per path
    (`test_every_return_path_joins_the_leads_on_a_refused_submission`,
    `_at_the_wall_clock_ceiling`, `_at_the_round_ceiling`), each holding the
    lead past the last non-blocking fold so only the join can record it, and
    each asserting the terminal frame's `settled == total`.
12. **Tests beyond the spec's list:** a lead in its retry backoff does not
    hold the batch; a lead that raises; the two fold-boundary tests; the
    lineage key and kind; the setting reaching the run; the profiler showing
    the lead as its own `seat:list-price:no-web` row (the M3 path, run on the
    real tool); and malformed multipliers read the same by both mirrors.
13. **Flip readiness, measured once.** With the switch on and both minimums
    temporarily at the floor of 8, every QC test outside the new file
    (`-k "qc or QC or final"`, 369 tests) passed. So a later flip does not
    break the existing suites.
14. **The fake needed nothing.** A lead reaches `SequencedFakeClient.stream`
    and the batch reaches `_FakeBatches.create`, both over the same scripts;
    since the lead streams before the batch is created, it always takes its
    title's first scripted turn, which is what lets a test script the lead
    alone.
15. **Docs corrected in passing.** README's
    `BUILD_A_SPEC_QC_BATCH_VERIFICATION` row and the trust dossier's stage 2
    still said "one batch"; both now say one per round (the v1.18.0 erratum,
    applied where it had not reached).

16. **Review finding (Codex, PR #213): only a lead that sent a request is
    priced as one.** The spec says `_BatchPhaseOutcome.streamed_keys` names
    the leads; the first cut named every PICKED lead. A Stop landing after
    the roster but before the lead's first request leaves it cancelled with
    nothing sent, and priced at 1.0 it made both report projections claim a
    streamed lead the run never sent. `streamed_keys` now holds only leads
    whose own call made at least one request; the rest are priced with the
    seats that were never batched.
    `test_a_lead_stopped_before_its_first_request_is_not_reported_as_streamed`
    pins it, and fails against the first cut.

Every mechanism was reverted in place; the matrix is in CLAUDE.md ("Final
QC's batched phase can stream a lead seat first").

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
   - Two more comments call one TTL per request absolute. They are
     `_cache_control`'s docstring ("EVERY breakpoint in a single request
     must be built from the same `cache_ttl`"), and the comment that opens
     `_run_streaming_call` ("One TTL for every breakpoint in the
     request"). Make both say every *explicit* marker. Name the
     continuation tail as the one shorter-lived breakpoint the ordering
     rule allows. The rule they defend is unchanged: longer-lived entries
     come first.
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

Built on 2026-09-23 from `master` at `d6f2c32`. **No gate applies to the
build**; the chunk ships **switched off**, as specified, and its default
flips only on an M3 pass of the rule above. No measurements came with the
session (the prompt's placeholder was left unfilled, read as "none"), so
nothing was added to the progress file's Measurements.

Files: `backend/settings.py`, `backend/research/engine.py`,
`backend/qc/engine.py`, the new `tests/test_continuation_cache.py`, new
cases in `tests/test_research_engine.py` and `tests/test_qc_live_events.py`
(plus one knowing helper change there), README, CLAUDE.md and
`docs/RELEASE_WINDOWS.md`.

Deviations and additions, each recorded because the spec text is not
rewritten:

1. **The switch is pinned per round and per run**, the Chunks 2 and 3
   precedent: `run_requirements_research(continuation_cache=)` and
   `run_final_qc(continuation_cache=)` take `None` to mean the setting and
   read it once — research beside the clock, Final QC beside the other
   switches — so every dimension, lens, seat and grouping call of one run
   resumes the same way whatever the environment does mid-run.
   `_run_dimension` and `_run_streaming_call` default to off for a direct
   caller.
2. **Every streamed QC call gets the tail, not only lenses and seats**,
   because they all resume through `_run_streaming_call`: the lenses, the
   grouping calls (`_consolidate_candidates` → `_run_consolidation_call`),
   the streamed seats, and Chunk 3's leads (`_run_batch_calls` hands the
   switch to its leads ONLY; the batch builder never adds it). In practice
   only a call carrying web tools can pause — research dimensions,
   `code_compliance` and its seats — so the grouping call's wiring is
   uniformity, not a saving; a test pins it anyway, so no future call can
   resume differently from the rest.
3. **The constant is read-only and each request gets a copy.**
   `_CONTINUATION_CACHE_CONTROL` is a `MappingProxyType`, and each request
   gets `dict(_CONTINUATION_CACHE_CONTROL)`: the SDK JSON-serializes the
   argument, which a mappingproxy is not, and one shared dict would let one
   request's mutation reach the next. A mappingproxy compares equal to a
   dict, so the tests pin `type(...) is dict` as well as the value.
4. **`_is_continuation(messages)`** is the spec's condition, one helper
   per engine (copied, not imported): the last message's role is
   `assistant`, read off a dict or an SDK object. An empty list is not a
   continuation.
5. **SDK floor: no change.** `requirements.txt` allows `anthropic>=1.0`.
   1.0.0 was installed in a scratch venv and a `messages.stream(...,
   cache_control={"type": "ephemeral"}, container="cont_1")` call against a
   mock transport put both keys, as given, in the request body.
6. **The QC pair is one test each for both calls the spec names.**
   `test_a_qc_continuation_carries_the_automatic_breakpoint_when_on` and
   `test_the_switch_off_sends_todays_qc_requests_exactly` (in
   `tests/test_qc_live_events.py`) each run a compliance lens that pauses
   twice and a streamed web-lineage seat that pauses once, asserting the
   lens's 5-minute markers and the seat's `1h`/`1h`/`1h` markers beside the
   5-minute tail. The spec's cross-engine tests live in the new
   `tests/test_continuation_cache.py`, under the names the spec gives.
7. **Tests beyond the spec:** the guard refusing the shapes it exists to
   catch — the three the provider rejects (a 1-hour tail after 5-minute
   markers, an explicit marker on the last block beside the tail, a fifth
   breakpoint) and a tail on a first request (accepted, but a pure write
   surcharge) — so the guard itself is proven, not only obeyed; a
   streamed lead resuming with the tail while its batch carries none; a
   paused grouping call resuming with it; the setting reaching a research
   round and a Final QC run; and `test_continuation_cache_ships_switched_off`
   (the default read from the source with `ast`, the page-trim pin's idiom;
   the flip replaces it with `..._ships_switched_on`).
8. **F3 runs on both verifier transports.** The transport IS a manifest
   input (`configuration.batch_verification`) and the staleness check
   rebuilds with the live setting, so each run is compared under the
   transport it used; only the continuation switch moves.
9. **One knowing test-helper change:** `tests/test_qc_live_events.py`'s
   `_run_client` gained `continuation_cache=` (default `None`, which reads
   the setting — off), so the new QC tests can drive the switch. Every
   existing caller is unchanged.
10. **Kept green, unchanged:**
    `test_qc_requests_cache_the_shared_prefix_across_the_whole_fan_out`,
    `test_the_research_request_caches_the_shared_half`, and the reference-
    and facts-visibility layout tests. The first's comment ("Every
    breakpoint in one request must carry the SAME ttl") is left as it is:
    it checks the explicit markers of first requests, of which it is still
    true.
11. **Comments corrected as point 7 asks:** `_cache_control`'s docstring
    and the comment opening `_run_streaming_call` now say every *explicit*
    marker, and name the tail as the one shorter-lived breakpoint the
    ordering rule allows; the NOTE above `_qc_request_kwargs` is rewritten;
    `_dimension_user_content`'s docstring says the fourth slot is the
    tail's, so a fourth explicit marker would be a 400 on every resume.
12. **The guard ran once over the whole suite**, beyond the five scenarios
    `test_no_request_exceeds_four_breakpoints` captures: a scratch pytest
    plugin (not committed) wrapped `SequencedFakeClient.stream` and the fake
    batch `create`, and ran the same checks on every research and QC request
    the full suite sends, with `BUILD_A_SPEC_CONTINUATION_CACHE=1`.
    Across the 2,782 tests that passed it checked 2,238 streamed requests (43
    of them carrying the tail) and 904 batched requests (none carrying it),
    with **no violation**. The same run is the flip-readiness check: with the
    switch on, the only failure was the README knob test, which had read
    README before its Configuration row landed mid-run; it passes on the
    finished tree, with the switch on and off. So a later flip breaks no
    existing suite.
13. **The trust dossier is unchanged.** It describes the shipped defaults
    and makes no claim about what a resume costs. The flip session should
    re-read its research and Final QC cards when the default moves.
14. **The release-note draft (§7) is unchanged**; it is conditional, and
    still accurate.

Revert matrix: each mechanism reverted in place, one at a time, restored
from the exact text read, the tree checked clean after every row, with the
new file and the research, live-events, batch-verification, warm-lead and
warm-launch suites run each time. All 22 rows are red; the counts are in
CLAUDE.md's implemented notes.

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
- **Resume** on a retryable failure when both hold:
  - the current conversation has at least one completed response;
  - the retry about to run is not the final attempt.

  Keep `messages`, `all_responses`, `container_id` and the continuation
  count, and re-send the request that failed. With `max_attempts` = 3, the
  first retry can resume, and the second (the final attempt) always
  restarts.
- **Restart** otherwise — no progress yet, or the final attempt — exactly
  as today: `billed.extend(all_responses)`, fresh messages, container
  cleared.
- Backoff and attempt counting are unchanged. The `{prefix}_retry` events
  gain a `mode: "resume" | "restart"` field. Three tests pin those events
  as exact dicts; they are listed below.

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
- Three tests assert a retry event as an exact dict:
  `test_retry_emits_dimension_retry_event` (`dimension_retry`),
  `test_malformed_frames_are_ignored_and_stream_failure_retries`
  (`lens_retry`) and
  `test_verifier_retry_then_relays_tool_activity_for_the_same_seat`
  (`verifier_retry`). Each fails before any response, so each still
  restarts. Each gains `"mode": "restart"`, and nothing else changes.
- Keep green, unchanged:
  - `test_a_retryable_seat_failure_restarts_on_a_fresh_conversation`
    (batch). Its seat fails on its first request, before any response, so
    the rule still restarts it. It now pins that case. The resume case is
    the new `test_a_retryable_item_error_resumes_the_seat`.
  - `test_retryable_failure_retries_then_succeeds`, which also fails
    before any response.
  - `test_retry_success_counts_billed_usage_from_abandoned_attempt` and
    `test_the_failed_round_bill_counts_a_retried_attempt_exactly_once`.
    Both fail after a billed pause, so both now resume rather than
    restart. Their totals (300) are the same either way, which is the
    billing invariant working. Their docstrings' "abandoned" wording
    becomes loose; leave the assertions alone.

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

**Also in this session.** If M3 is recorded, apply each chunk's flip
rule. Chunk 3's and Chunk 4's defaults flip only on their own passes,
each as its own commit.

**Docs.**
- README: a subsection.
- CLAUDE.md: implemented notes and the container erratum.
- `docs/RELEASE_WINDOWS.md`: no row. A transient failure cannot be staged
  on demand; the hermetic tests carry this chunk.

**Release note:** §7, Chunk 5.

#### As built

Built on 2026-09-24 from `master` at `f3aaf88`. **No gate applies, and there
is no switch**, as specified. No measurements came with the session (the
prompt's placeholder was left unfilled, read as "none"), so nothing was
added to the progress file's Measurements, and — M3 not being recorded — no
flip was applied under "Also in this session".

Files: `backend/research/retry_policy.py`, `backend/research/engine.py`,
`backend/qc/engine.py`, `frontend/src/types.ts`, the trust dossier
(`TrustDeepDiveModal.tsx`, one clause), the new `tests/test_retry_resume.py`,
the knowing changes in `tests/test_research_engine.py`,
`tests/test_qc_live_events.py` and `tests/test_qc_audit_report.py`, README
and CLAUDE.md.

Deviations and additions, each recorded because the spec text is not
rewritten:

1. **Only a request's own failure resumes (`in_request`).** The rule names
   two conditions; "re-send the request that failed" implies a third: the
   failure must be the request's own. `in_request` is True only while the
   request is in flight (`messages.stream` through `get_final_message()`).
   A failure after the response arrived — the resend sanitizer, parsing,
   research's grounding — finds the response appended and the messages
   possibly not, so there is no request to send again as it stood, and the
   retry restarts as every retry used to. Both streaming engines therefore
   pass `progressed = in_request and bool(all_responses)`. The batch has no
   such window — its failures are always a request's own (an errored result
   line, a refused submission) — so `_BatchSeatState.retry` reads progress
   alone. Pinned by `test_a_failure_after_the_response_arrived_restarts` on
   both engines.
2. **The rule is one function**, `retry_policy.retry_mode`, beside the
   policy both engines already import: the ported module's one
   Build-a-Spec addition, said so in its docstring.
   `RETRY_MODE_RESUME` / `RETRY_MODE_RESTART` are the wire vocabulary, and
   `test_every_retry_site_reads_the_one_rule` pins both engines reading the
   same function.
3. **The continuation budget is `len(all_responses)`.** `while
   len(all_responses) <= MAX` replaces `for _ in range(MAX + 1)` in both
   engines, so the budget is the conversation's by construction and needs no
   counter carried: a failed request appends no response, so its resend
   spends none, and a restart empties it with the conversation. The batch
   keeps its `continuations` counter, which `resume_attempt` carries.
4. **Every terminal path bills `[*billed, *all_responses]`, and two needed
   it.** The top-of-attempt Stop read the abandoned pool alone — right while
   every attempt began from nothing, wrong once a resumed conversation
   reaches it (a Stop landing in the resume's backoff); pinned. The
   fallthrough after the attempt loop cannot be reached (every attempt
   returns or retries, and the last one's failure returns), so its change is
   consistency only; the revert matrix records it at 0 red, by design.
5. **`_CallResult.responses` is the final CONVERSATION's**, which a resume
   carries across attempts; its comment says so. Grounding therefore pools a
   resumed conversation's pre-failure retrievals with no other change, and a
   restart still moves the abandoned conversation into `billed`, i.e.
   attempted-only evidence. Final QC's `api_request_count` still counts
   every request sent, the failed one and its resend both; its
   `model_response_count` counts each response once.
6. **`mode` rides all five retry-emitting sites:** research's
   `dimension_retry`, the streamed `{prefix}_retry` (lens, consolidation,
   verifier), and the batch's two `verifier_retry` sites (an errored item, a
   refused submission). No new event type.
7. **`_BatchSeatState.retry(attempts=)`** applies the rule, advances the
   seat, and returns the mode, so the two batch sites cannot apply it
   differently. The backoff is still read BEFORE the retry advances the
   attempt (the Batch 1 convention); the refused path's comment now says
   "the retry", not `restart_attempt`. A streamed lead seat (Chunk 3) is an
   ordinary `_run_streaming_call` and follows the streaming path.
8. **The frontend shows nothing new.** `types.ts` gains `RetryMode` and an
   optional `mode` on the four retry event members. No copy changed: the
   board's "Retrying (attempt N/M)", the Review Room's "Retrying N/M" and
   the agent timeline's "retrying in Ns" are accurate for either mode. The
   mode reaches the activity log and the trace, which mirror every event
   key.
9. **The activity state resets on either mode**, so a resumed request
   re-announces its first phase and the board moves off the retry notice as
   soon as work resumes, as a restart's always did.
10. **One trust-dossier clause.** The Research card's bounds said "retries
    with backoff on transient failures"; they now add that the first picks
    the conversation up at the step that failed and the last starts it
    fresh. Nothing else in the dossier describes a retry.
11. **The spec's research-and-QC tests are ONE assertion set**, parametrized
    over both engines' call functions (`_run_dimension`, and `_run_lens` on
    the web-tooled compliance lens), under the names the spec gives: the
    engines keep separate copies of the loop, so the same test over both is
    what keeps them together.
12. **Tests beyond the spec:** the rule's truth table and the one-function
    pin; `test_a_resumed_continuation_carries_the_tail_the_failed_one_did`
    (the dependency on Chunk 4: the resent request equals the failed one,
    byte for byte, tail included, while the opening request carries none);
    `test_a_stop_during_a_resume_still_bills_the_conversation`;
    `test_a_failure_after_the_response_arrived_restarts`;
    `test_a_batched_seat_keeps_its_continuation_budget_across_a_resume` (its
    pauses search nothing, because a verifier seat's 2× search ceiling, 16,
    is below the 17 pauses the budget allows);
    `test_a_batched_seat_on_its_final_attempt_restarts_fresh`;
    `test_a_research_round_records_the_resume` (through the fan-out, the mode
    in the round's log); and `test_a_resumed_run_stays_current_like_a_clean_one`,
    F3 for a chunk with no switch: a run whose compliance lens resumed
    fingerprints its inputs exactly as a run that never failed.
13. **Existing tests, as the spec lists.** The two container tests are
    renamed `test_pause_continuation_echoes_the_container_a_resume_keeps_it_and_a_restart_drops_it`
    and `test_qc_pause_continuation_echoes_the_container_a_resume_keeps_it_and_a_restart_drops_it`,
    each now scripted pause → pause → failure (resumed: container kept) →
    failure (the final attempt: restarted, container dropped) → success; the
    QC one also asserts the two retry events' modes. The three exact-dict
    tests gained `"mode": "restart"` and nothing else. Kept green,
    unchanged: `test_a_retryable_seat_failure_restarts_on_a_fresh_conversation`,
    `test_retryable_failure_retries_then_succeeds`,
    `test_retry_success_counts_billed_usage_from_abandoned_attempt` and
    `test_the_failed_round_bill_counts_a_retried_attempt_exactly_once` — the
    last two now resume, and their totals (300) are unchanged, the billing
    invariant working; their "abandoned" wording is left loose, as the spec
    says.
14. **One knowing test change the inventory above missed:**
    `test_failed_fetch_and_abandoned_retry_cannot_ground_final_payload`
    (`tests/test_qc_audit_report.py`) scripted its abandoned retry as pause →
    connection failure → success. That retry now resumes, so the page it
    read is the model's own evidence and grounds the citation, which is the
    point of the chunk. One more connection failure makes the final
    attempt's restart the abandonment; the test's name and every assertion
    stand.
15. **The release-note draft (§7) is unchanged**; it is accurate as written.

Revert matrix: each mechanism reverted in place, one at a time, restored
from the exact text read, the tree checked clean afterwards, with the new
file and the research, live-events, batch-verification, warm-lead,
continuation-cache, research-rounds, verifier-v3 and stop suites run each
time. 27 of 29 rows are red; the two at 0 are the unreachable fallthroughs
(point 4). The counts are in CLAUDE.md's implemented notes.

---

### Chunk 6 — Closeout

**Goal.** Finish the program, leaving an honest record.

**Depends on:** Chunks 1–5, each complete, skipped or blocked-and-decided.

1. **Reconcile and backfill.** Fill in every merge commit in the progress
   table.
2. **Flip, if owed.** Chunks 3 and 4 each have their own M3 test (their
   "Flip" sections). For each chunk whose M3 pass is recorded and whose
   default is still off, flip it, each as its own commit. If M3 was never
   run, or its result for a chunk is missing or inconclusive, leave that
   default off. Then record, for example, "Chunk 4 shipped off; flip owed
   after M3" in `docs/plans/README.md` (the compaction plan's precedent).
3. **Revisit Chunk 3 against M4.** If its default is on and M4 no longer
   passes its M3 test (Chunk 3, "Flip"), turn the default off and say
   why.
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

Built on 2026-09-24 from `master` at `02b2985`. No measurements came with
the session: the prompt's placeholder was left unfilled, read as "none", as
the sessions for Chunks 2–5 read theirs. **The closeout changed no code.**
Its files are the progress file, this plan, README, CLAUDE.md and
`docs/plans/README.md`.

The ten steps, as they went:

1. **Reconcile and backfill.** Chunk 5's merge commit, `02b2985`, is filled
   in. No open pull request carried the program's title (#217 and #218 were
   open, and unrelated). Chunk 6's own merge commit stays blank, because no
   later chunk session exists to fill it; the flip session's step 2 does.
2. **Flip, if owed.** No M3 is recorded, so neither default flips. That is
   recorded in three places: owner decision O3; `docs/plans/README.md` ("Chunk
   3 shipped off; flip owed after M3. Chunk 4 shipped off; flip owed after
   M3."); and the progress file's new "After the program".
3. **Revisit Chunk 3 against M4.** Does not apply: its default is off, and
   there is no M4.
4. **Record the after-numbers.** There are none. The progress file's
   Measurements section says so, dated.
5. **Consolidate §7.** "Release-note draft (Tier 1)" now opens §7 (see
   deviation 2).
6. **README.** The section is retitled "## Research and Final QC cost
   (Tier 1)", and every subsection is kept. Only the intro changed (see
   deviation 5).
7. **CLAUDE.md.** One closeout section, "Research and Final QC cost, Tier 1,
   as shipped", with two errata (see deviation 6).
8. **`docs/plans/README.md`.** The entry is marked **complete** in place,
   the compaction precedent. It names the two owed flips and the owed
   release-note draft.
9. **No version bump.** The prompt asked for no release. v1.20.0 is still
   the latest published release (checked through the GitHub Releases API),
   and `settings.VERSION` is 1.21.0, unreleased.
10. **After merge.** Abraham is told the program is complete, and given the
    follow-ups he owns.

Deviations and additions, each recorded because the spec text is not
rewritten:

1. **"After the program", with a flip-session prompt.** The spec says there
   is no next-session prompt, meaning there is no Chunk 7. But two flips are
   owed, and the Session procedure cannot reach them, because every row is
   complete. The progress file now has an "After the program" section:
   - what is still owed, and who owes it;
   - the two free baselines that sharpen M3's verdict (M1 is what Chunk 4's
     test compares against; M2 gives Chunk 3's test its h₀);
   - the prompt that starts a flip session once M3 has been run;
   - the flip session's seven steps, built from the Chunk 3 and Chunk 4
     "Flip" sections, Chunk 4's As built 13 (re-read the trust dossier when
     a default moves) and the Session procedure's verify-and-PR rules.

   There is still no next *chunk* prompt. The "What Abraham does between
   sessions" row for Chunk 6 now points at that section instead of saying
   there is nothing.
2. **The per-chunk drafts are kept, under a subheading.** The spec text is
   append-only, so the consolidated block opens §7 and says it supersedes
   the drafts under "The per-chunk drafts". The block is shaped as one
   `ReleaseSection`: a section title, then numbered items whose bold line is
   the `ReleaseItem.title` and whose paragraph is its `body`. It adds three
   things the drafts did not have: which release owes it (none of it is in a
   1.21.0 tagged at `a273ab7`), each conditional item marked by the setting
   that decides it, and one optional summary sentence. The item wording is
   the chunks' own, unchanged. Each draft was re-checked against the final
   code, and all four still describe it.
3. **O3.** The flip step's outcome is recorded as an owner-decision row,
   because O2 is the precedent: a gate the plan directs, applied by a
   session.
4. **Flip readiness, re-measured on the final tree.** Chunk 3 measured it
   before Chunks 4 and 5 changed the same loops, and Chunk 4 before Chunk 5.
   The closeout ran it again with both switches on through the environment,
   using two scratch pytest plugins that are not committed: one lowers the
   lead minimums, and one runs a record-only copy of Chunk 4's breakpoint
   guard on every captured request.
   - The whole suite, with the minimums as shipped: 2,833 passed, 64
     skipped, none failed. The guard saw 2,370 streamed requests (57
     carrying the tail) and 938 batched ones (none carrying it), with no
     violation.
   - Chunk 3's selection (`-k "qc or QC or final"`, outside
     `tests/test_qc_batch_warm_lead.py`) with both minimums at the floor of
     8: 403 passed, none failed. 3 of 138 batch phases picked a lead, so the
     lead path ran. The guard found no violation in 1,460 streamed and 618
     batched requests.

   Each chunk's `..._ships_switched_off` pin reads the default from the
   source, so it passed in both runs; the flip replaces it. Everything else
   passes either way, so a flip is still one commit per chunk.
5. **The README intro now says what is true at the close.** The old intro
   said every change "is measured on real saved files before it is
   trusted". That is F8's intent, not what happened: nothing was measured,
   and Chunks 2 and 5 are on without a measurement (Chunk 2 under F5's
   documented-guarantee branch, Chunk 5 because it has no switch). The new
   intro says the program is complete and that no release carries it yet.
   It says what a user sees, that no saving has been measured, and why
   Chunks 3 and 4 wait. The rest of the section is unchanged.
6. **The errata sweep** read every earlier CLAUDE.md section the program
   touched, the README outside the program's section, Help, the trust
   dossier and the QC drawer's copy. It found two statements the program
   made stale that no chunk's errata covered, and both are errata in the
   closeout section:
   - Chunk 3's "The refused-submission retry restarts only the seats that
     were submitted". Since Chunk 5, a seat with progress resumes.
   - Chunk 2's "the progress file records it". M2 was never recorded.

   Every other stale statement had already been corrected by the chunk that
   made it stale. The trust dossier's Research and Final QC cards describe
   the shipped defaults as they are, so the dossier is unchanged.
7. **Found in passing, not done:** the Final QC launch confirmation's
   hard-coded "Opus 5" (CLAUDE.md's closeout section). It is outside the
   program, from PR #177, so it is left for its own change. Queuing it as a
   suggested task timed out twice.
8. **§2's "today" is the program's starting point.** §2.3–§2.4 describe the
   code at `cef31d0`. Chunks 2–5 changed every behaviour they name, and each
   chunk's As built records how. Section 2 is left as written: it is the
   evidence the chunks were designed from.

---

## 7. Release-note drafts

Written for a spec author, not for someone reading `CLAUDE.md`. Chunk 6
consolidates them. A draft marked *(conditional)* ships only if its switch
is on in the release that carries it.

### Release-note draft (Tier 1)

Consolidated by the closeout (Chunk 6, 2026-09-24) from the per-chunk drafts
under the next heading, which it supersedes. **This is the block a release
lifts.** It is one `ReleaseSection` for `backend/release_notes.py`: the
section title, then one `ReleaseItem` per numbered entry, the bold line as
its `title` and the paragraph under it as its `body`. The wording is the
chunks' own, unchanged. Chunk 1 is a developer tool and has no note.

- **Which release owes it.** Whichever release next ships from `master`.
  None of this program is in a 1.21.0 tagged at the project-workspace
  closeout commit (`a273ab7`), so that release needs none of it. A release
  cut from `master` at or after `02b2985` (Chunk 5's merge) carries all of
  it.
- **Which items.** Items 1 and 2 go in any such release: Chunk 2's
  staggered launch is on by default, and Chunk 5 has no switch. Items 3 and
  4 are *(conditional)*. Include each only if its switch defaults on in the
  `backend/settings.py` being released. At the closeout both are off, and
  each flip is owed after an M3 pass of that chunk's own test (the progress
  file's "After the program").
- **Where it goes.** If the release's entry already has a section on cost
  (1.21.0's has "What a review costs"), the items can join it instead.
  Keep them in this order either way, so leaving out the conditional ones
  leaves a clean list.

**Section title:** What research and Final QC cost

1. **Final QC's first stage reuses what it already paid for**

   Four of Final QC's five reviewers read the same copy of your section.
   They used to start at the same moment, so each paid to store its own
   copy. Now one starts a few seconds ahead and the other three reuse its
   copy. The step that groups duplicate findings does the same when it has
   several groups to check. The reviewers, their instructions and what they
   find are unchanged, and a Final QC result you already have stays current;
   the review simply starts a few seconds later.

2. **A dropped connection no longer starts a research area over**

   A brief rate limit or connection failure used to throw away every step a
   research area or reviewer had already finished, and paid for, and start
   it from the top. The app now retries the step that failed and carries on.
   If that retry fails too, the last attempt still starts fresh.

3. **Batched verification reuses what it already paid for** —
   *(conditional: only if `BUILD_A_SPEC_QC_BATCH_WARM_LEAD` defaults on in
   the released build)*

   When many of Final QC's verifying reviewers work from the same copy of
   your section, one of them now starts first, at full price, so the rest of
   the batch can read its copy instead of each storing their own. The report
   lists that reviewer at full price, so the cost it shows stays exact.

4. **Long research stops paying twice for what it already read** —
   *(conditional: only if `BUILD_A_SPEC_CONTINUATION_CACHE` defaults on in
   the released build)*

   A research area that searches the web in several steps used to pay full
   price, at every step, to re-send everything it had already found. It now
   reuses its own cached copy. The code-compliance review gets the same
   benefit. The findings, sources and limits are unchanged.

**For the release's summary paragraph**, if it mentions this program, one
sentence covers the two unconditional items: "Research and Final QC pay less
for the same work: four of Final QC's reviewers now share one stored copy of
your section instead of each paying for its own, and a dropped connection no
longer starts a research area over."

### The per-chunk drafts

Each chunk's own draft, as it wrote it. The block above supersedes them.

- **Chunk 1.** None; a developer tool.
- **Chunk 2 — Final QC's first stage reuses what it already paid for.**
  Four of Final QC's five reviewers read the same copy of your section.
  They used to start at the same moment, so each paid to store its own
  copy. Now one starts a few seconds ahead and the other three reuse its
  copy. The step that groups duplicate findings does the same when it has
  several groups to check. The reviewers, their instructions and what they
  find are unchanged, and a Final QC result you already have stays
  current; the review simply starts a few seconds later.
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
made with batch verification at its default. Chunk 3's gate reads the
per-bucket table's `seat:batched:web-tooled` and `seat:batched:no-web`
rows: Records, 1h write, Cache read and Output (Chunk 3, "Gate"). It does
not use the printed "Read share" column. This also closes
step 2 of `docs/review-results/2026-09-09/EXECUTION_RECORD.md`; record it
there as well.

**M3 — trial with the new switches on.** After Chunk 4 merges. Chunk 3,
if it was built, merged before it and ships switched off too, so this trial
tests both. This uses real runs you would make anyway,
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

Chunk 3's half needs a Final QC that produces enough findings for at least
one lineage to reach its minimum. The QC profiler's `seat:list-price` row
shows whether a lead ran. If there is no such row, say so; that half of the
trial then waits for a larger run.

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

One lineage of n seats in a batch shares a prefix of p tokens, and each
seat writes o output tokens. Today a share h of that prefix is read rather
than written (the gate says how to measure h, p and o from M2). The rates
are the QC model's list rates in `settings.PRICING`: w for a 1-hour write,
r for a read, u for output. b is `settings.BATCH_COST_MULTIPLIER`. On
Claude Opus 5.5, w = $8.00, r = $0.20 and u = $20.00 per million, and
b = 0.5.

- **Saving.** A lead turns each other seat's expected write into a read:
  (n − 1) × b × (1 − h) × (w − r) × p, when every other seat then reads.
- **Extra cost.** The lead gives up its batch discount:
  [w − b × (h r + (1 − h) w)] × p + (1 − b) × u × o.
  (Its uncached suffix loses the discount too; that term is negligible.)
- **`n_min`** is the smallest integer n for which the saving exceeds the
  extra cost: the smallest integer above
  1 + extra ÷ [b × (1 − h) × (w − r) × p].

On Claude Opus 5.5, with p = 40k and o = 4k:

| h (read today) | 0 | 0.5 | 0.7 | 0.8 | 0.85 | 0.9 | 0.95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `n_min` | 3 | 5 | 8 | 12 | 16 | 23 | 46 |

The same arithmetic, turned around: the break-even h for a lineage of n
seats.

| n | p | o | Break-even h |
|---:|---:|---:|---:|
| 8 | 40k | 3k | ≈ 0.72 |
| 20 | 40k | 5k | ≈ 0.88 |
| 40 | 40k | 4k | ≈ 0.94 |
| 80 | 40k | 4k | ≈ 0.97 |

So no single read-share cut-off is right for every lineage. With p = 40k
and o = 3k, an 8-seat lineage at h = 0.8 loses about $0.10 on a lead: it
saves $0.22 and costs $0.31. An 80-seat lineage at h = 0.9 (o = 4k) gains
about $0.89. Hence the gate's per-lineage minimum, and its floor of 8. On a
20-seat lineage at h = 0.3 (o = 5k), a lead saves about $1.80.

**The M3 test.** A run with a lead measures h₁, the batched seats' read
share with the lead in place, against the baseline h₀ from M2. The saving
is (n − 1) × b × (h₁ − h₀) × (w − r) × p; the formula above is the case
h₁ = 1. For the extra cost, the trial uses the lead's actual list-price
cost C, from its own row in the profiler: (1 − b) × C + b × h₀ × (w − r) ×
p. That counts everything the lead did, continuations and searches
included. For a single-request lead, it equals the extra-cost formula
above.

Two estimates in the gate lean the safe way for `web-tooled` seats, whose
continuations re-read the prefix. With P the true prefix, h the true read
share and k continuations per seat, the gate measures p = P × (1 + k) and
a read share of (h + k) / (1 + k). Their product p × (1 − measured share)
is still P × (1 − h), so the saving is exact. The extra cost comes out
higher by k × P × (w − r). That is far more than the re-sent turn content
the formula leaves out, so in any realistic run `n_min` comes out higher,
never lower.

**The assumption only M3 can confirm:** a streamed request's cache entry is
readable by the batch requests that follow it. Caches are per workspace
and are not documented as separate for the two paths. But nothing states
the sharing outright either. That is why the chunk ships switched off, and
its default flips only on an M3 pass.

### 10.4 Chunk 5: resumes

A transient failure after k continuations re-pays their input and output
today. On a large research dimension that can be $0.50–2.00 per event.
How often it happens is unmeasured; the value is in removing the worst
case.
