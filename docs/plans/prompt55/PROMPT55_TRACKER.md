# The 5.5 prompting upgrade — tracker

<!-- PROMPT55-STATUS: IN PROGRESS -->

**Next session:** P55-3 — Draft passes finish in one turn, and effort is re-based

Owner: Abraham. Opened 2026-09-29.

**This file is the only record of where the "5.5 prompting upgrade" program
stands.** A chat, a pull request description or a commit message is not.
The spec for every session is
[`PROMPT55_PLAN.md`](PROMPT55_PLAN.md). `tests/test_prompt55_tracker.py`
checks this file's shape (and its agreement with the plan) on every pull
request, so a malformed edit fails CI rather than misleading the next
session.

A new session starts from [The handoff prompt](#the-handoff-prompt), and
then follows the [Session procedure](#session-procedure) step by step.

## What this program is

On 2026-09-29 Abraham asked for a review of the app against Anthropic's
prompting guides for Claude Sonnet 5.5 and Claude Opus 5.5 (the interview,
research, the harvest and the condensing summary run on Sonnet 5.5; Final QC
runs on Opus 5.5). The review found eleven things worth changing, and he
approved all of them (D1). The plan groups them into eight sessions, one
pull request each:

1. **P55-1** hardens output parsing (tool-name case, the last complete
   tagged JSON value) and makes the fact harvest think first and refuse a
   cut-off answer.
2. **P55-2** makes the interview write its reply after its last tool call,
   and adds an owner-run canary for Sonnet 5.5's progress-update blocks.
3. **P55-3** makes whole-section draft passes finish in one turn, and
   re-bases Final QC's lens effort for Opus 5.5.
4. **P55-4** reminds a streamed research or Final QC call that ended
   without its output tool.
5. **P55-5** does the same for a batched verifier seat.
6. **P55-6** keeps thinking valid when the harness edits a request
   (preserved thinking).
7. **P55-7** lets a streamed Final QC call fall back to another model when
   the QC model declines it.
8. **P55-8** marks pasted text in chat, and closes the program out.

## Status

| ID | Title | Status | PR | Merge commit |
|---|---|---|---|---|
| P55-1 | Harden output parsing and the fact harvest | done | PR #236 | `b5c282a` |
| P55-2 | The interview replies after its last tool call | done | PR #237 | `77da938` |
| P55-3 | Draft passes finish in one turn, and effort is re-based | not started | — | — |
| P55-4 | Remind a streamed fan-out call that skipped its output tool | not started | — | — |
| P55-5 | Remind a batched verifier seat that skipped its output tool | not started | — | — |
| P55-6 | Keep thinking valid when the harness edits a request | not started | — | — |
| P55-7 | Final QC falls back when a streamed call is declined | not started | — | — |
| P55-8 | Mark pasted text in chat, and close out | not started | — | — |

### What each status means

- **not started**: nothing for this session has merged.
- **done**: the session's work is on `master`.
  - The session's own pull request sets it, as that PR's last change, with
    the PR number in the PR column.
  - The row reaches `master` only when the PR merges, so on `master`
    "done" always means merged.
  - The next session fills in the merge commit in its reconcile step,
    before it ticks an item or marks itself blocked. The last session's own
    merge commit stays blank, because no session comes after it.
- **blocked**: the session cannot be built safely as specified.
  - The row names the pull request that records why, with code evidence.
  - Abraham decides what happens next. A later session never works around
    a blocked one.

Rows change in order: every `done` row comes before any other row, at most
one row is `blocked`, and everything after the first row that is not done
is `not started`. An open pull request is not a status: the reconcile step
finds it on GitHub.

## Checklists

Each item mirrors the acceptance criterion with the same ID in
[`PROMPT55_PLAN.md`](PROMPT55_PLAN.md). The test checks that the two lists
match.
- Tick an item (`- [x]`) only when it is true on your branch, and append
  `— evidence:` with where it is proven: a test name, a file, a command's
  output, or an As built item.
- A `done` session has every item ticked, with evidence.
- Only the session in progress may have ticks while its row is not done.

### P55-1 — Harden output parsing and the fact harvest

- [x] P55-1.1 — `extract_tool_use_block` accepts a case-insensitive name only when no exact match exists, and the last match wins — evidence: `tests/test_prompt55_parsing_and_harvest.py::test_an_exact_name_wins_over_a_later_case_insensitive_one`, `::test_a_case_insensitive_name_is_accepted_and_the_last_one_wins`, `::test_a_mis_cased_output_tool_call_completes_the_call` (research + qc); `backend/research/schema.py` `extract_tool_use_block`
- [x] P55-1.2 — the chat's unknown-tool `is_error` names the exact declared tool, without dispatching a mis-cased name; missing-key errors name the expected key — evidence: `tests/test_prompt55_parsing_and_harvest.py::test_a_mis_cased_chat_tool_is_told_its_exact_name_and_is_not_run`, `::test_an_unknown_chat_tool_is_told_every_tool_it_can_call`, `::test_a_read_reference_doc_call_without_ref_id_names_the_key`, `::test_every_chat_tool_names_the_key_an_empty_call_is_missing`; As built deviation 2 (only `read_reference_doc` changed)
- [x] P55-1.3 — one `last_tagged_json_object` helper replaces all five greedy tagged-JSON patterns — evidence: `backend/research/schema.py` `last_tagged_json_object`; `tests/test_prompt55_parsing_and_harvest.py::test_research_reads_the_final_block_newest_response_first`, `::test_each_final_qc_fallback_reads_the_final_block` (x3), `::test_the_compliance_audit_reads_the_final_block`, `::test_no_greedy_tagged_json_pattern_is_left_anywhere`
- [x] P55-1.4 — the harvest's system prompt ends with "Think the problem through before you answer." — evidence: `tests/test_prompt55_parsing_and_harvest.py::test_the_harvest_system_prompt_ends_with_the_guides_line`
- [x] P55-1.5 — a harvest `max_tokens` stop is `harvest_cut_off` even with a payload, metered, and `HARVEST_MAX_TOKENS` (64k, env + README row) bounds the call — evidence: `tests/test_prompt55_parsing_and_harvest.py::test_a_cut_off_harvest_is_refused_even_with_a_payload_and_carries_its_usage`, `::test_the_route_refuses_a_cut_off_harvest_meters_it_and_shows_nothing`, `::test_the_harvest_ceiling_ships_at_64k_with_a_floor`, `::test_the_harvest_ceiling_reads_its_knob_and_clamps_to_the_floor`, `::test_a_lower_interview_ceiling_still_caps_the_harvest` (a lower `BUILD_A_SPEC_MAX_TOKENS` still binds it, Codex on PR #236); README Configuration row
- [x] P55-1.6 — `tests/test_prompt55_parsing_and_harvest.py` covers every item above — evidence: `tests/test_prompt55_parsing_and_harvest.py` (44 cases) covers P55-1.1 to P55-1.5, mapped in the plan's P55-1 As built
- [x] P55-1.7 — verified: ruff, pytest, npm test, npm run build — evidence: ruff clean; `pytest -q` 3064 passed, 64 skipped; `npm test` 438 passed; `npm run build` clean (plan's P55-1 As built, Verified)
- [x] P55-1.8 — revert matrix recorded in As built — evidence: plan's P55-1 As built, Revert matrix (26 rows; 23 red alone, the other three explained and proven red in combination)
- [x] P55-1.9 — CLAUDE.md, README and the release note updated — evidence: CLAUDE.md "Output parsing and the fact harvest, hardened" + Layout entries; README harvest bullet, routes and `BUILD_A_SPEC_HARVEST_MAX_TOKENS` row; `backend/release_notes.py` 1.21.0 "A harvest that runs out of room says so"

### P55-2 — The interview replies after its last tool call

- [x] P55-2.1 — the stable prompt tells the model to make its tool calls first and write the user-facing reply after the last one — evidence: `tests/test_prompt55_closing_message.py::test_the_stable_prompt_makes_tool_calls_first_and_replies_last` (every module); `backend/llm/prompts.py` `_HOW_YOU_WORK` steps 2–3 and the reason paragraph
- [x] P55-2.2 — `suggest_prompts` is the last tool call before the closing message; the old "near the end of your reply" order is gone — evidence: `tests/test_prompt55_closing_message.py::test_suggest_prompts_is_the_last_tool_call_before_the_closing_message`, `::test_the_suggest_prompts_tool_description_says_the_same_order`; As built deviation 1 (the tool description changed too)
- [x] P55-2.3 — every directive that stages chips (full draft, adapt, prerequisites, both debriefs and their variants) says to stage them first and then write the closing message — evidence: `tests/test_prompt55_closing_message.py::test_every_chip_staging_directive_stages_first_and_closes_after` (16 directives and variants), `::test_a_debrief_brief_is_the_closing_message`, `::test_a_whole_section_pass_closes_after_its_last_edit`; `prompts._REPLY_AFTER_TOOL_CALLS`; As built deviation 2
- [x] P55-2.4 — the web-lookup policy carries the "even when you feel confident" sentence and no longer reads as "don't search" — evidence: `tests/test_prompt55_closing_message.py::test_the_web_lookup_policy_checks_specifics_even_when_confident`; `backend/llm/prompts.py` `_WEB_LOOKUP_POLICY`
- [x] P55-2.5 — `tools/prompt55_progress_update_canary.py` sends nothing without `--run`, re-sends the production request with `display: "updates"`, and prints a pass/fail verdict — evidence: `tests/test_prompt55_progress_update_canary.py::test_the_canary_sends_nothing_without_run`, `::test_every_round_resends_exactly_what_the_production_engine_builds`, `::test_the_resent_request_differs_only_in_display_the_cap_and_the_beta`, `::test_main_reports_a_pass`, `::test_main_reports_a_failed_verdict_nonzero`; As built deviations 3, 4 and 7
- [x] P55-2.6 — `tests/test_prompt55_closing_message.py` and `tests/test_prompt55_progress_update_canary.py` cover every item above; knowing test changes recorded — evidence: `tests/test_prompt55_closing_message.py` (30 cases) covers P55-2.1 to P55-2.4, `tests/test_prompt55_progress_update_canary.py` (17) covers P55-2.5; plan's P55-2 As built, Knowing test changes (the docs-consistency scan widened; no older pin moved)
- [x] P55-2.7 — verified: ruff, pytest, npm test, npm run build — evidence: ruff clean; `pytest -q` 3111 passed, 64 skipped; `npm test` 438 passed; `npm run build` clean (plan's P55-2 As built, Verified)
- [x] P55-2.8 — revert matrix recorded in As built — evidence: plan's P55-2 As built, Revert matrix (45 rows, every one red; the first run's one green row strengthened, one mis-aimed row re-aimed)
- [x] P55-2.9 — CLAUDE.md (notes + the paid-canary ground rule), README and the release note updated — evidence: CLAUDE.md "The reply comes after the last tool call" section, the ground rule's three canaries and Layout entries; README "Live web lookups" bullet and the third paid check; `backend/release_notes.py` 1.21.0 Chat "The assistant's questions stay in the chat" and "The assistant checks code specifics first"; the trust dossier's chat card

### P55-3 — Draft passes finish in one turn, and effort is re-based

- [x] P55-3.1 — both whole-section directives tell the model to carry the pass through in one turn — evidence: `backend/llm/prompts.py` `_CARRY_THE_PASS_THROUGH` in `FULL_DRAFT_DIRECTIVE` and `ADAPT_IMPORTED_DIRECTIVE`; `tests/test_prompt55_effort.py::test_both_whole_section_directives_carry_the_pass_through` (4 variants), `::test_the_carry_through_line_says_what_the_guide_says`, `::test_a_collecting_turn_does_not_carry_it`
- [x] P55-3.2 — `DRAFT_PASS_EFFORT` (default high, env + README row) is used for every round of a ready full-draft or adapt turn, and only those — evidence: `backend/settings.py` `DRAFT_PASS_EFFORT`; README `BUILD_A_SPEC_DRAFT_PASS_EFFORT` row; `tests/test_prompt55_effort.py::test_the_shipped_draft_pass_effort_is_one_level_above_the_interview`, `::test_the_knob_reads_a_level_and_falls_back_to_high`, `::test_every_round_of_a_full_draft_turn_carries_the_draft_effort`, `::test_every_round_of_an_adapt_turn_carries_the_draft_effort`, `::test_an_ordinary_turn_carries_the_interview_effort`, `::test_a_collecting_turn_carries_the_interview_effort`, `::test_every_other_turn_runs_at_the_interview_effort`
- [x] P55-3.3 — the turn's effort is decided once per turn and recorded in its trace — evidence: `conversation.turn_effort` + `_ChatRequestInputs.effort`; `capture.turn_prompts(effort=)`; `tests/test_prompt55_effort.py::test_the_effort_is_decided_once_per_turn` (draft pass + ordinary), `::test_the_request_inputs_carry_the_turn_effort`, `::test_the_trace_records_the_effort_each_turn_ran_at`; As built deviation 2 (on `prompt_refs`)
- [x] P55-3.4 — `QC_EFFORT` defaults to medium; the verifier default is unchanged; overrides keep working — evidence: `backend/settings.py` `QC_EFFORT`; `tests/test_prompt55_effort.py::test_the_shipped_qc_effort_is_medium`, `::test_the_verifier_default_is_unchanged`, `::test_every_qc_phase_defaults_to_medium`, `::test_a_lens_a_grouping_call_and_a_seat_are_sent_at_medium`, `::test_every_override_keeps_working` (4 combinations), `::test_a_result_retained_at_high_reads_stale_against_the_new_default`
- [x] P55-3.5 — `tests/test_prompt55_effort.py` covers every item above; knowing test changes recorded — evidence: `tests/test_prompt55_effort.py` (43 cases) covers P55-3.1 to P55-3.4; plan's P55-3 As built, Knowing test changes (`test_qc_phase_effort.py`'s default pin, `test_citation_repair.py`'s required `effort=`)
- [x] P55-3.6 — verified: ruff, pytest, npm test, npm run build — evidence: ruff clean; `pytest -q` 3154 passed, 64 skipped; `npm test` 438 passed; `npm run build` clean (plan's P55-3 As built, Verified)
- [x] P55-3.7 — revert matrix recorded in As built — evidence: plan's P55-3 As built, Revert matrix (21 rows, every one red on the first run)
- [x] P55-3.8 — CLAUDE.md, README, the trust dossier and the release note updated (including the stale-once disclosure) — evidence: CLAUDE.md "Draft passes finish in one turn, and effort is re-based" + Layout entries; README `BUILD_A_SPEC_DRAFT_PASS_EFFORT`, `QC_EFFORT` and `QC_LENS_EFFORT` rows and the adaptive-thinking bullet; `TrustDeepDiveModal.tsx` model table, chat, full-draft and Final QC cards; `backend/release_notes.py` 1.21.0 Chat "Whole-section passes finish in one go" and Final QC "Final QC reasons on Opus 5.5's own scale" ("run Final QC again before you apply its fixes")

### P55-4 — Remind a streamed fan-out call that skipped its output tool

- [ ] P55-4.1 — research and streamed Final QC calls remind a text-only end of turn, at most twice per conversation, never after max_tokens, a refusal or a Stop
- [ ] P55-4.2 — the reminder appends the assistant content verbatim and one user message; an invented tool name gets `is_error` tool results
- [ ] P55-4.3 — billing, grounding, the continuation budget, retries and the continuation tail behave as specified
- [ ] P55-4.4 — the research, lens, consolidation and verifier system prompts name the early stop to avoid
- [ ] P55-4.5 — `tests/test_prompt55_missing_tool_reminder.py` covers every item above, over both engines
- [ ] P55-4.6 — verified: ruff, pytest, npm test, npm run build
- [ ] P55-4.7 — revert matrix recorded in As built
- [ ] P55-4.8 — CLAUDE.md and the release note updated

### P55-5 — Remind a batched verifier seat that skipped its output tool

- [ ] P55-5.1 — a batched seat is reminded in the next round, at most twice, never while recovering or without a round left
- [ ] P55-5.2 — the reminder count survives a resume and resets on a restart
- [ ] P55-5.3 — the reminded seat's record reconciles, priced at the batch rate
- [ ] P55-5.4 — `tests/test_prompt55_batch_reminder.py` covers every item above
- [ ] P55-5.5 — verified: ruff, pytest, npm test, npm run build
- [ ] P55-5.6 — revert matrix recorded in As built
- [ ] P55-5.7 — CLAUDE.md (and the release note, if needed) updated

### P55-6 — Keep thinking valid when the harness edits a request

- [ ] P55-6.1 — the chat, research and streamed Final QC send `drop_block` with the beta on exactly the requests (and, in the engines, the rest of the conversation) the harness edited
- [ ] P55-6.2 — an unedited request is byte-identical to today; batched params never carry it
- [ ] P55-6.3 — the display probe degrades only on a display-worded 400
- [ ] P55-6.4 — CT-1 never latches because of a `block_binding` rejection
- [ ] P55-6.5 — a non-empty `input_transformations` is logged without content
- [ ] P55-6.6 — `tests/test_prompt55_preserved_thinking.py` covers every item above
- [ ] P55-6.7 — verified: ruff, pytest, npm test, npm run build
- [ ] P55-6.8 — revert matrix recorded in As built
- [ ] P55-6.9 — CLAUDE.md (and the release note, if warranted) updated

### P55-7 — Final QC falls back when a streamed call is declined

- [ ] P55-7.1 — with the switch on, every streamed Final QC request carries `fallbacks: "default"` and its beta; batched params never do
- [ ] P55-7.2 — a rescued call completes and records `served_by_model` on its record, round-tripped and backward-compatible
- [ ] P55-7.3 — accounting reconciles; rescued usage is priced at the QC model's rates and disclosed
- [ ] P55-7.4 — CT-2 treats a fallback-served response as unmeasured
- [ ] P55-7.5 — the Word memo and the report modal state the same disclosure sentence, pinned equal
- [ ] P55-7.6 — the backend and frontend tests cover every item above
- [ ] P55-7.7 — verified: ruff, pytest, npm test, npm run build
- [ ] P55-7.8 — revert matrix recorded in As built
- [ ] P55-7.9 — CLAUDE.md, README, the trust dossier and the release note updated

### P55-8 — Mark pasted text in chat, and close out

- [ ] P55-8.1 — pasted blocks worth marking are recorded by position and wrapped at send (the occurrence the paste inserted, never an identical earlier one) with a random 8-hex ID per block, each tag on its own line
- [ ] P55-8.2 — the chat never shows the tags, live or after a reload
- [ ] P55-8.3 — the stable prompt carries the pasted-content note and stays module-deterministic
- [ ] P55-8.4 — the frontend and backend tests cover every item above
- [ ] P55-8.5 — verified: ruff, pytest, npm test, npm run build
- [ ] P55-8.6 — revert matrix recorded in As built
- [ ] P55-8.7 — closeout: CLAUDE.md closing section, README, release notes checked, the plans index marked complete
- [ ] P55-8.8 — the tracker reads COMPLETE, with the banner, as the PR's last change

## Rules

These bind every session. Only Abraham changes one, and the change is
recorded in the [Decision log](#decision-log).

- **R1 — CLAUDE.md binds, and the docs are live.** Unlike the Tier 1 finish
  program, nothing is frozen until the end. Each session keeps `CLAUDE.md`
  (a new implemented-notes section, appended before
  "## Source-of-truth pointers into Claude-Spec-Critic"; Layout entries
  corrected in place; errata for earlier sections recorded in the new
  section, never by rewriting them), `README.md` (never shrink it; add and
  correct) and `requirements.txt` (only if a dependency changes; none is
  expected) current, as its plan section's Docs list says.
- **R2 — Release notes go into the newest unreleased entry.** A
  user-visible change adds an item to the newest `ReleaseNote` in
  `backend/release_notes.py`, in the same commit as the change. First check
  with the GitHub Releases API (`mcp__github__list_releases` or
  `get_latest_release`) that the entry's version has not been published; a
  fresh clone has no tags, so never trust `git tag -l`. If it has been
  published, follow CLAUDE.md "A released version's entry is frozen" (a new
  entry and a version bump in all five sites). Never push a git tag.
- **R3 — Tests are hermetic.** No test makes a real API call. The one paid
  tool this program adds (P55-2's canary) sends nothing without `--run`,
  and only Abraham runs it. No session runs it.
- **R4 — Copy, don't import, between the two engines.** Research and Final
  QC keep separate copies of their loop code. A small pure helper beside
  `extract_tool_use_block` in `backend/research/schema.py` is fine.
  Behaviour both engines must share gets ONE assertion set, parametrized
  over both (the `tests/test_retry_resume.py` precedent).
- **R5 — No new SSE event type, and no new key on a pinned payload**
  (`verification_started`, `stream_end` and the other exact-dict tests).
  New telemetry goes to a logger, a trace event field, or the diagnostics
  snapshot.
- **R6 — Nothing new enters the QC input manifest** except what P55-3's
  spec says (the lens effort default, an existing hashed value; retained
  results read stale once, disclosed). No QC schema or protocol bump. A new
  `QCResult` record field (P55-7's `served_by_model`) is serialized only
  when set, so older reports load byte for byte.
- **R7 — A new `BUILD_A_SPEC_*` knob needs a README Configuration row and a
  floor** (`tests/test_docs_consistency.py` and
  `tests/test_settings.py::test_every_shipped_knob_declares_a_floor`).
  Constants that are not knobs stay module constants.
- **R8 — Every mechanism earns a revert-matrix row.** Revert each one in
  place, one at a time, restore the exact text, and check the tree is clean
  afterwards. Record the rows in the session's As built. A mechanism with no
  red test gets a stronger test before the session finishes, or an
  explanation of why none can see it.
- **R9 — One session, one pull request.** Abraham merges. A session never
  merges its own PR, never force-pushes someone else's branch, and never
  pushes a tag.
- **R10 — Copy is true at every merge.** Help, the trust dossier
  (`frontend/src/components/TrustDeepDiveModal.tsx`), Developer tools, code
  comments, `docs/RELEASE_WINDOWS.md` and the README all describe the code
  as it is on the branch being merged.
- **R11 — Windows commands run as written** in PowerShell and Command
  Prompt (`.\.venv\Scripts\python`, one command per line, no `^` or `&&`;
  `tests/test_docs_consistency.py` pins it).
- **R12 — If the current code makes the spec unsafe, stop.** Mark the row
  `blocked` (see the Session procedure), never work around it.
- **R13 — New file names carry `prompt55`**: tests
  `tests/test_prompt55_*.py`, frontend tests
  `frontend/tests/prompt55*.test.ts`, tools `tools/prompt55_*.py`. A new
  production module gets a name that exists nowhere else in the repository
  (check with `git ls-files`).

## Decision log

| # | Date | Decision |
|---|---|---|
| D1 | 2026-09-29 | **Implement every recommendation of the 5.5 prompting review.** Abraham: "Let's implement them." Eight sessions, one pull request each, tracked here; at the end of each session, after its PR merges, the next session's prompt, followed by a sentence saying about how many sessions are left; the final session announces completion in big letters. |
| D2 | 2026-09-29 | **`THINKING_DISPLAY` stays `summarized`.** The reasoning summaries are a shipped UX. P55-2 moves the reply's substance after the last tool call instead of switching the display, and its canary checks whether `display: "updates"` would be worth a later change. |
| D3 | 2026-09-29 | **A draft pass raises effort for its whole turn, at the top level.** The one-time messages-cache miss for that turn and the next is accepted (these passes run early, when history is short). The per-message effort beta is declined: it would put a `role: "system"` message into history that every history consumer would have to learn. |
| D4 | 2026-09-29 | **Final QC's lens effort default becomes `medium`** (Opus 5.5's `medium` is at least Opus 5's `high`, per the guide). The verifier default stays `medium`. Retained results read stale once, and the release note says so. |
| D5 | 2026-09-29 | **The missing-output-tool reminder is capped at two per conversation**, as a module constant in each engine (copied, not a knob). A reminder request carries no continuation tail. |
| D6 | 2026-09-29 | **Server-side fallback is for streamed Final QC only**, behind a switch that defaults on; Batches cannot carry it, and Sonnet 5.5's fallback covers too few categories to help research or chat. Rescued usage is priced at the configured QC model's rates, and both report projections disclose it. |
| D7 | 2026-09-29 | **A paste is marked when it holds a line break or at least 120 characters.** Short inline pastes stay plain text. |
| D8 | 2026-09-29 | **Every new file carries `prompt55` in its name** (Abraham asked for unique names), and this program's plan and tracker live in `docs/plans/prompt55/`. |

## Splitting a session

A session is sized to fit one working session. If one turns out not to (the
plan flags P55-7 as the likeliest), do not ship a half-built mechanism.
Instead, in the same pull request:

1. Finish a coherent part that is tested, verified and safe on its own.
2. In the plan, directly after the current session's section, add a new
   section `## P55-<n>b — <title>` (then `c`, and so on), with its own
   Implements/Depends/Size line, Goal, the remaining Design and Tests (moved
   or referenced), Acceptance and an `### As built` heading. Its acceptance
   items are `P55-<n>b.1`, `P55-<n>b.2`, and so on.
3. Remove the moved items from the current session's Acceptance list. This
   is the one edit to spec text a session may make; say what moved, and
   why, in the current session's As built.
4. In this tracker, add the matching Status row (not started) and checklist
   directly after the current session's, and mirror the items exactly.
5. Mark the current session done as usual. The Next-session line then names
   the new row, and the handoff's session count includes it.

The closeout always stays on the last row: if P55-8 splits, move its
pasted-content items and keep the closeout items (P55-8.7 and P55-8.8, or
their renumbered equivalents) on the last session. The tracker test checks
that the plan's sections and this file agree after a split.

## Session procedure

1. **Read** CLAUDE.md (binding), this tracker and the plan, in full. Load
   the bundled `claude-api` skill before you write any request code (its
   `shared/model-migration.md` covers Sonnet 5.5 and Opus 5.5).
2. **Reconcile.**
   1. Run `git fetch origin master`, and read this file as it is on
      `origin/master`, not as it is on an unmerged branch.
   2. List the open pull requests in `Abe-Borg/build-a-spec` whose title
      starts with `5.5 prompting`. An open one means a session is in
      flight.
      - If your prompt asks you to finish that PR, drive it to merge.
      - Otherwise, stop and tell Abraham which PR is open and what it is
        waiting on.
   3. Fill in the merge commit of every `done` row whose cell is blank,
      from the GitHub API or `git log origin/master`, and check every hash
      already there against the log, correcting any that is wrong (the test
      checks a hash's shape; only the log knows whether it is the right
      one). This rides your PR. Do it first: from the moment your session
      ticks an item or is marked blocked, the tracker test requires it. A
      session never writes its own row's merge commit.
3. **Pick the session.** Take the first row that is not `done`.
   - If it is `blocked`, stop and tell Abraham. Never work around a
     blocked session.
   - If every row is done, the program is complete: tell Abraham, and
     change nothing.
   - Your prompt names the session it expects. The tracker decides, not
     the prompt. If they disagree, say so in your first message.
4. **Build it** on the branch your session was given (create it from
   `origin/master` if it does not exist).
   - The plan's section for that session is the spec. Tick checklist items
     here, with evidence, as each one becomes true.
   - Record every deviation, every knowing test change and the revert
     matrix under the session's **As built** in the plan. Append to the
     spec text; never rewrite it (a split, above, is the one exception).
   - **If the current code makes the spec unsafe, stop.**
     - Mark the row `blocked`, with the code evidence, in a PR that carries
       only that record and step 2.3's merge commits, titled
       `5.5 prompting — <ID>: blocked`.
     - Set the Next-session line to `none — <ID> is blocked; Abraham decides`.
     - Tell Abraham.
5. **Verify** from the repository root, and fix everything before you push.
   On Windows (PowerShell):

   ```powershell
   .\.venv\Scripts\python -m ruff check .
   .\.venv\Scripts\python -m pytest -q
   cd frontend
   npm test
   npm run build
   cd ..
   ```

   On Linux or in a cloud container, use CLAUDE.md's Commands section:
   `.venv/bin/python -m ruff check .`, `.venv/bin/python -m pytest -q`,
   and `npm test` and `npm run build` in `frontend/`. If the container has
   no virtual environment, create one from `requirements.txt` first (and
   run `npm ci` in `frontend/` before `npm test`).
6. **Docs** (R1, R2, R10): the session's Docs list, its As built, and any
   copy the change made false.
7. **Commit and open the pull request.**
   - Commit in the house style: sassy where warranted, never obnoxious
     (CLAUDE.md, ground rules).
   - Push, and open a ready-for-review PR titled
     `5.5 prompting — <ID>: <title>`.
   - Its body lists what changed, the new tests, every knowing test change,
     a summary of the revert matrix, and any default that changed for
     everyone running from `master` (with how to switch it back).
   - Subscribe to the PR's activity. Schedule a check-in, if your session
     can.
8. **Mark the session done, as the PR's last commit.**
   - Set its row to `done`, with `PR #<number>`. Leave its merge commit
     blank (`—`).
   - Check that every checklist item is ticked, with evidence.
   - Set the Next-session line to the next row's `<ID> — <title>`, or, for
     the last row, follow the plan's closeout (the COMPLETE state below).
   - Run `tests/test_prompt55_tracker.py`, and push.
9. **Drive the PR to merge.**
   - Handle CI failures, review threads and merge conflicts under your
     session's standing rules.
   - Never merge it yourself: Abraham merges.
10. **After it merges,** send the handoff (next section). For the last
    session, send the completion message instead
    ([When every session is done](#when-every-session-is-done)).

## After the pull request merges: the handoff

**Do this only after the merge.** You will learn of it from the PR
subscription or from a scheduled check-in. On each check-in, re-check the
PR, and re-arm the check-in until the PR is merged or closed.

Then send Abraham one message with these parts, in this order:

1. **What merged**, in one line: the session, the PR link and the merge
   commit.
2. **What comes next**, in one line: the next session's ID and title, and
   anything it changes for everyone running from `master` once its PR
   merges.
3. **The prompt for the next session**, in a fenced `text` block: the
   template below, with the next session's ID and title filled in.
4. **Immediately after the fenced block — outside it, as the very next
   sentence — say how many sessions are left**, in exactly this form:

   About N sessions left, counting the one this prompt starts.

   N is the number of Status rows that are not `done` on `master` after the
   merge (a split adds rows, so count them; never guess). When N is 1, write
   "About 1 session left — the one this prompt starts." Nothing comes
   between the closing fence and that sentence, and the sentence is never
   inside the prompt.

Three exceptions:
- **The PR closed without merging.** Say so, and give him the prompt to
  redo the same session, followed by the same sessions-left sentence.
- **Your session cannot learn of the merge** (it has no subscription or
  check-in tools). End your final message with the prompt anyway, under the
  heading "Next session — start only after PR #N is merged", followed by
  the sessions-left sentence (counting as if it had merged).
- **The last session.** There is no next session. Send the completion
  message instead.

Abraham has nothing to do between sessions except merge.

## The handoff prompt

The first session's prompt is this template, with this filled in:

`P55-1 — Harden output parsing and the fact harvest`

Every later prompt is the same template, with the next session filled in.

```text
Continue the "5.5 prompting upgrade" program in this repository (implementing the review of Anthropic's Claude Sonnet 5.5 and Opus 5.5 prompting guides).

Read these files completely before touching code:
1. CLAUDE.md (binding; it is loaded as your project instructions)
2. docs/plans/prompt55/PROMPT55_TRACKER.md
3. docs/plans/prompt55/PROMPT55_PLAN.md

Then follow the tracker's "Session procedure" step by step. In short: reconcile the tracker against master and GitHub; do exactly ONE session, the one the tracker names; tick its checklist with evidence, and mark its row done as the pull request's last change; keep CLAUDE.md, README.md and the release notes current as the tracker's rules say; drive the PR to merge (I merge); and only after it has merged, give me the prompt for the next session, followed immediately (outside the prompt) by one sentence saying about how many sessions are left, or, if this was the last session, the completion message in big letters.

I expect this session to be: <ID> — <title>. (The tracker decides, not this line.)
```

## When every session is done

**The completion banner** — copy it exactly:

```text
########     ########    ########   #######  ##    ## ########
##           ##          ##     ## ##     ## ###   ## ##
##           ##          ##     ## ##     ## ####  ## ##
#######      #######     ##     ## ##     ## ## ## ## ######
      ##           ##    ##     ## ##     ## ##  #### ##
##    ## ### ##    ##    ##     ## ##     ## ##   ### ##
 ######  ###  ######     ########   #######  ##    ## ########
```

**The COMPLETE state.** The last session sets it in this file as its PR's
last change (P55-8.8):
- Its row is `done` with its PR number, and its merge commit blank.
- Every item of every checklist is ticked, with evidence.
- The status line changes from `<!-- PROMPT55-STATUS: IN PROGRESS -->` to
  `<!-- PROMPT55-STATUS: COMPLETE -->`.
- Directly under the status line sit these, in this order, each on its own
  line. Blank lines may separate them; nothing else may.
  1. The opening comment `<!-- PROMPT55-BANNER -->`.
  2. The completion banner above, exactly as given, in a `text` code block
     (three backticks and `text`, the banner's seven lines, three
     backticks), with no blank line inside it.
  3. The closing comment `<!-- /PROMPT55-BANNER -->`.
  4. `**ALL WORK IN THE 5.5 PROMPTING UPGRADE IS COMPLETE.**`
- The Next-session line comes right after them, and reads
  `none — the program is complete`.
- `tests/test_prompt55_tracker.py` checks all of this.

**The completion message.** After the last session's pull request merges,
that session's final message to Abraham has these parts, in order, and
nothing before them:

1. The completion banner above, in a `text` code block.
2. The heading line `# ALL WORK IN THE 5.5 PROMPTING UPGRADE IS COMPLETE`.
3. **Merged:** the last session, with the PR link and its merge commit.
4. **What changed, and the new defaults:** one line per session, naming
   any switch it added and its default, and how to switch it off.
5. **Still owed:** the optional P55-2 canary run (only if Abraham has not
   run it), and the release notes riding the newest unreleased entry to
   whichever release next ships from `master`. Nothing else.
6. **There is no next session.**
