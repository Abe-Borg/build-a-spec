# Research and Final QC cost, Tier 1 — progress and handoff

Owner: Abraham. Opened 2026-09-23.

**This file is the only record of where the program stands.** A chat, a PR
description or a commit message is not. The spec is
[`RESEARCH_QC_COST_TIER1_2026-09-23.md`](RESEARCH_QC_COST_TIER1_2026-09-23.md).

A new session starts from [How to start a session](#how-to-start-a-session)
and then follows the [Session procedure](#session-procedure) step by step.

**The program is complete** (Chunk 6, the closeout, 2026-09-24). Every
chunk's code is on `master`. Two switches still ship off, each with its flip
owed after a measured run, and the release notes are owed to the next
release. [After the program](#after-the-program) says what is left and how
to do it. A session that applies the owed flips starts there, not from the
Session procedure.

## Status

| Chunk | Title | Status | PR | Merge commit | Notes |
|---|---|---|---|---|---|
| plan | The plan and this file | **complete** | PR #205 | `3d600d9` | set by its own PR, like every row |
| 1 | Research cost profiler | **complete** | PR #208 | `df4d55f` | M1, the optional baseline, is its first run |
| 2 | Staggered launch for calls that share a cached prefix | **complete** | PR #210 | `ed75f7a` | ships on (`BUILD_A_SPEC_QC_WARM_WAIT_SECONDS`, 45 s); M2 after merge measures it and gates Chunk 3 |
| 3 | Warm the batched verifier cache with a streamed lead seat | **complete** | PR #213 | `d6f2c32` | built at the gate's fallback minimums of 20 (no M2; O2); ships switched off (`BUILD_A_SPEC_QC_BATCH_WARM_LEAD`); the default flips on an M3 pass |
| 4 | Cache `pause_turn` continuations | **complete** | PR #215 | `f3aaf88` | ships switched off (`BUILD_A_SPEC_CONTINUATION_CACHE`); every streamed research and Final QC call that resumes a pause carries the tail, batches never; the default flips on an M3 pass |
| 5 | Resume, don't restart, on a transient failure | **complete** | PR #219 | `02b2985` | no switch (F5's exception): the first retry resumes a conversation that has a completed response, the final attempt always restarts; research, streamed and batched Final QC read one rule (`retry_mode`); M3 is still owed for Chunks 3 and 4 |
| 6 | Closeout | **complete** | PR #220 | | no flip applied (no M3; O3): Chunks 3 and 4 ship off, each flip owed after an M3 pass of its own test ([After the program](#after-the-program)); the release-note draft is consolidated in the plan's §7; no later chunk session exists to fill this row's merge commit, so the flip session's step 2 does |

### What each status means

- **not started**: nothing for this chunk has merged.
- **complete**: the chunk's work is on `master`.
  - The chunk's own pull request sets it, as that PR's last change.
  - The row reaches `master` only when the PR merges, so on `master`
    "complete" always means merged.
  - The row names the PR. The next session fills in the merge commit.
- **skipped (measured)**: Chunk 3 only. Its gate decided against building
  it, and the row gives the numbers that decided it.
- **blocked**: the chunk cannot be built safely as specified.
  - The row links the PR that records why, with code evidence.
  - Abraham decides what happens next. A later session does not work
    around a blocked chunk.

An open pull request is not a status. The reconcile step finds it on
GitHub.

## After the program

The closeout (Chunk 6) ended the chunk sequence on 2026-09-24. There is no
Chunk 7, and the Session procedure has nothing left to pick. Four things are
still owed. None of them is code a session can write on its own, because
each one waits on a real run.

| What | Why it is owed | Who | Cost |
|---|---|---|---|
| **M3**, the trial (the plan's §8) | It is the only thing that can decide the two flips below. | Abraham | only the runs themselves |
| **Chunk 4's flip** (`BUILD_A_SPEC_CONTINUATION_CACHE`) | It ships off, and flips only on an M3 pass of its own test (the plan, Chunk 4 → "M3 decides the flip"). | a flip session | — |
| **Chunk 3's flip** (`BUILD_A_SPEC_QC_BATCH_WARM_LEAD`) | It ships off, and flips only on an M3 pass of its own test (the plan, Chunk 3 → "M3 decides the flip"). | a flip session | — |
| **The release notes** | Whichever release next ships from `master` owes the plan's §7 "Release-note draft (Tier 1)". | that release's closeout | — |

**Two free baselines make M3's verdict sharper, and both read files you
already have.**
- **M1**, the research profiler on projects researched so far (every one of
  them was researched with the switch off), is what Chunk 4's test compares
  against. Without it the test has no "lower uncached share than M1".
- **M2**, the QC profiler on your newest Final QC JSON export, gives Chunk
  3's test its baseline h₀ (0.88 is assumed without it). It also closes step
  2 of `docs/review-results/2026-09-09/EXECUTION_RECORD.md`.

**M3** then needs one Research round and one Final QC made from a source
checkout with both switches on (the plan's §8 has the commands). Chunk 3's
half says nothing unless that Final QC had a group of at least 20 verifier
seats sharing one copy of the section. The QC profiler's `seat:list-price`
row says whether a lead ran.

**M4** comes last: the same two profilers on new runs, once the flips are in
normal use. It confirms the saving. It is also Chunk 3's standing check: if a
later M4 no longer passes Chunk 3's M3 test, its default goes back off (the
plan's Chunk 6, step 3).

### Starting a flip session

Once M3 has been run, give a new session this prompt, with the
measurements filled in:

```text
Apply the owed flips of the "Research and Final QC cost, Tier 1" program in
this repository.

Read these three files completely before touching code:
1. CLAUDE.md
2. docs/plans/RESEARCH_QC_COST_TIER1_PROGRESS.md
3. docs/plans/RESEARCH_QC_COST_TIER1_2026-09-23.md

The program is complete. Follow the progress file's "After the program"
section, not its Session procedure. In short: record the measurements;
apply Chunk 4's and Chunk 3's own "M3 decides the flip" tests to them; flip
each chunk whose test passes, each as its own commit; record the decision
either way; open one pull request and drive it to merge.

Measurements (M3's research and Final QC profiler output; M1 and M2 if you
have them; the error text of anything that failed):
<paste the profiler output here>
```

### The flip session's steps

1. **Read** the three files. CLAUDE.md is binding. The spec is the plan's
   Chunk 3 and Chunk 4 "Flip" and "M3 decides the flip" sections.
2. **Reconcile.** Run `git fetch origin master`.
   - If an open pull request's title starts with `Research/QC cost Tier 1`,
     stop and tell Abraham.
   - Fill in any blank merge commit in the Status table. Chunk 6's is blank
     at the close.
3. **Record** the measurements under [Measurements](#measurements), dated,
   each saying which build it was taken on.
4. **Decide each chunk separately, by its own test.** Record each decision,
   with its numbers, as the next row in [Owner decisions](#owner-decisions).
   If the prompt carries no M3, or a chunk's half of it is missing (Chunk
   3's needs a `seat:list-price` row), change nothing for that chunk, and say
   which run is still needed.
5. **Flip each chunk that passed, as its own commit.** Its "Flip" section
   lists the parts. In full:
   - its default in `backend/settings.py`;
   - `test_..._ships_switched_off` replaced by `..._ships_switched_on`, still
     read from the source with `ast`;
   - its README Configuration row and subsection;
   - the §7 block, where its item is no longer conditional;
   - an implemented-notes section in CLAUDE.md, with an erratum for the
     chunk's "It ships switched off";
   - the trust dossier's Research and Final QC cards, re-read against the new
     default;
   - `docs/plans/README.md`'s "flip owed" line.
6. **For a chunk that failed,** leave the default off. Record the numbers
   and the likely cause (the plan's "Fail" bullet). Say whether the flip is
   still owed (a larger run could pass it) or abandoned.
7. **Verify, then open one pull request.**
   - Verify as the Session procedure's step 5 says.
   - Title it `Research/QC cost Tier 1 — flip: <what flipped, or "none">`.
   - No version bump, `backend/release_notes.py` entry or tag (the plan's
     F7).
   - Drive it to merge. Abraham merges. After the merge there is no next
     prompt, unless a flip is still owed.

## How to start a session

Give a new session this prompt, with the expected chunk and the
measurements filled in:

```text
Continue the "Research and Final QC cost, Tier 1" program in this repository.

Read these three files completely before touching code:
1. CLAUDE.md
2. docs/plans/RESEARCH_QC_COST_TIER1_PROGRESS.md
3. docs/plans/RESEARCH_QC_COST_TIER1_2026-09-23.md

Then follow the progress file's "Session procedure" step by step. In short:
reconcile its status table against master and GitHub; build exactly ONE
chunk; mark that chunk complete in its own pull request, as the PR's last
change; drive the PR to merge; and only after it has merged, give me the
prompt for the next session.

I expect this session to build: Chunk 1 — Research cost profiler.
(The procedure decides, not this line.)

Measurements for this session (profiler output, or "none"):
none
```

## Session procedure

1. **Read.** Read CLAUDE.md, this file and the plan, in full. CLAUDE.md
   is binding. The plan's chunk section is the spec, and its frozen
   decisions (§3) and invariants (§4) apply to every chunk.

2. **Reconcile.**
   1. Run `git fetch origin master`, and read this file as it is on
      `origin/master`, not as it is on an unmerged branch.
   2. List the open pull requests in `Abe-Borg/build-a-spec` whose title
      starts with `Research/QC cost Tier 1`. An open one means a chunk is
      still in flight, so do not start another chunk.
      - If your prompt asks you to finish that PR, drive it to merge.
      - Otherwise, stop and tell Abraham which PR is open and what it is
        waiting on.
   3. For each row marked complete whose merge commit is blank, fill in
      the PR's merge commit, from the GitHub API or `git log origin/master`.
      This rides your PR.
   4. Copy the measurements from your prompt into
      [Measurements](#measurements), dated, under their heading (M1–M4).
      If the prompt says "none", change nothing.

3. **Pick the chunk.** Build the first row whose status is "not started"
   and whose dependencies (the plan's §5) are complete or skipped.
   - If the chunk has a gate, apply it before writing any code, and
     record the decision in [Owner decisions](#owner-decisions). M2
     decides whether Chunk 3 is built. M3 decides whether Chunk 3's and
     Chunk 4's defaults flip on.
   - If a gate skips the chunk, mark it "skipped (measured)" with the
     numbers, and go on to the next chunk in the same session.

4. **Build it** on the branch your session was given.
   - The plan's chunk section is the spec.
   - Record every deviation under that chunk's **As built** heading in
     the plan. Append; never rewrite the spec text.
   - If the current code makes the spec unsafe, stop. Record "blocked" in
     the table with the code evidence, open a PR that carries only that
     record, and tell Abraham.

5. **Verify** from the repository root, and fix everything before you
   push. On Windows:

   ```powershell
   .\.venv\Scripts\python -m ruff check .
   .\.venv\Scripts\python -m pytest -q
   cd frontend
   npm test
   npm run build
   cd ..
   ```

   On Linux or in a cloud container, use CLAUDE.md's Commands section
   (`.venv/bin/python -m ruff check .`, and so on). If the container has no
   virtual environment, create one from `requirements.txt` first. The only
   `package.json` is in `frontend/`, so the npm commands fail anywhere
   else.

6. **Docs.** Do what the chunk section lists, plus, every time:
   - **README.** The program's section, and a Configuration row for each
     new knob (`tests/test_docs_consistency.py` enforces the rows).
   - **CLAUDE.md.**
     - An implemented-notes section before "Source-of-truth pointers".
     - The Layout entries for the files you touched.
     - An erratum for any earlier section your change made false.
       Implemented notes are append-only.
   - **`docs/RELEASE_WINDOWS.md`** rows, where the chunk says so.
   - **`requirements.txt`**, if a dependency changed.
   - **The release-note draft** in the plan's §7, if what you built
     changed it.

7. **Commit and open the PR.**
   - Commit in the house style: sassy where warranted, never obnoxious
     (CLAUDE.md, ground rules).
   - Push, and open a ready-for-review PR titled
     `Research/QC cost Tier 1 — Chunk N: <title>`. Its body lists what
     changed, the new tests, every knowing test change, and any gate
     decision.
   - Subscribe to the PR's activity. Schedule a check-in with
     `send_later`, if your session has it.

8. **Mark the chunk complete, as the PR's last change.** Set the chunk's
   row to `complete` with the PR number, and push.

9. **Drive the PR to merge.**
   - Handle CI failures, review threads and merge conflicts under the
     session's standing rules.
   - Never merge it yourself: Abraham merges.
   - Never bump the version, add a `backend/release_notes.py` entry or
     push a git tag (the plan's F7).

10. **After it merges,** give Abraham the next prompt (next section).

## After the pull request merges: the next prompt

**Do this only after the merge.** You will learn of it from the PR
subscription or from your scheduled check-in. On each check-in, re-check
the PR, and re-arm the check-in until the PR is merged or closed.

Then send Abraham one message with three parts:

1. **What merged**, in one line: the chunk, the PR link and the merge
   commit.
2. **What he needs to do first**, if anything, from the table below, with
   the exact commands and what it costs.
3. **The prompt for the next session**, in a fenced `text` block. Copy it
   from [How to start a session](#how-to-start-a-session) and fill in the
   next chunk's number and title. Where the next session needs a
   measurement, leave the placeholder
   `<paste the profiler output here, or write "none">`.

Two exceptions:

- **The PR closed without merging.** Say so, and give him the prompt to
  redo the same chunk instead.
- **Your session cannot learn of the merge** (it has no subscription or
  check-in tools). End your final message with the prompt anyway, under
  the heading "Next session — start only after PR #N is merged".

**What Abraham does between sessions:**

| After | Before the next session | Cost |
|---|---|---|
| the plan | nothing | — |
| Chunk 1 | **M1**: run the research profiler on one to three researched projects, and paste its output into the next prompt. Optional, but Chunk 4's trial is judged against it. | free |
| Chunk 2 | **M2**: run the QC profiler on your newest Final QC JSON export, and paste its output. Chunk 3's gate reads it to decide whether to build Chunk 3, and how big a batch must be before a lead pays. Without it, Chunk 3 uses a conservative fallback. It ships switched off either way. | free |
| Chunk 3 | nothing new. M3, after Chunk 4, tests it: Chunk 3 ships switched off. | — |
| Chunk 4 | **M3**: from a source checkout, run one Research round and one Final QC with the new switches on. Paste both profilers' output, plus the error text of anything that failed. Chunks 3 and 4 each flip their default only on a pass of their own test. | only the runs themselves |
| Chunk 5 | M3, if it has not been done yet. | as above |
| Chunk 6 | nothing: the program is complete. M3 is still owed for the two switches that ship off, and the flips it decides are applied by a flip session ([After the program](#after-the-program)). | only the runs themselves |

The exact commands for M1–M4 are in the plan's §8.

## Measurements

Each entry is dated, and says which chunk's build it was taken on.

**None had been recorded at the closeout (2026-09-24).** No chunk's
session was given a measurement, so every gate took its no-measurement
branch (O2, O3). So no effect of this program has been
measured yet, Chunk 2's included, which ships on.
[After the program](#after-the-program) says what each measurement is still
good for.

### M1 — research baseline

*Not yet recorded.*

### M2 — Final QC batch baseline

*Not yet recorded.*

### M3 — trial with the new switches on

*Not yet recorded.*

### M4 — after

*Not yet recorded.*

## Owner decisions

| # | Date | Decision |
|---|---|---|
| O1 | 2026-09-23 | Build Tier 1: the four levers that change no quality, plus measurement. One chunk per session. Agents mark progress here, and give Abraham the next session's prompt after each merge. |
| O2 | 2026-09-23 | **Chunk 3's first gate (build or skip), applied by the Chunk 3 session as the plan directs.** No M2 is recorded: the session's prompt left the measurements placeholder unfilled, which is read as "none". The plan's "No M2" branch therefore holds: **build**, with both lineage minimums at the fallback of 20 seats (`_WARM_LEAD_MIN_SEATS_WEB`, `_WARM_LEAD_MIN_SEATS_NO_WEB`), and ship **switched off**. The second gate is unchanged: the default flips only on an M3 pass of Chunk 3's own test (the plan, Chunk 3 "Flip"). |
| O3 | 2026-09-24 | **Chunk 6's flip step, applied by the closeout session as the plan directs.** No M3 is recorded: the session's prompt left the measurements placeholder unfilled, which is read as "none", and M1, M2 and M4 are empty too. So neither default flips. Chunk 3 (`BUILD_A_SPEC_QC_BATCH_WARM_LEAD`) and Chunk 4 (`BUILD_A_SPEC_CONTINUATION_CACHE`) both ship **off**, each with its flip owed after an M3 pass of its own test. This is recorded in `docs/plans/README.md` and under [After the program](#after-the-program). Step 3 (revisit Chunk 3 against M4) does not apply: its default is off, and there is no M4. |

## Done checklist (every chunk)

- [ ] Reconciled; merge commits backfilled; measurements copied in.
- [ ] The gate applied and recorded (Chunks 3 and 4).
- [ ] Built to the chunk's spec, with deviations under its **As built** heading.
- [ ] New tests written. Every knowing test change is named in the PR body and under **As built**.
- [ ] For a chunk that touches QC: the F3 test, proving a retained Final QC result stays current with the switch in either position.
- [ ] ruff, the full pytest suite, `npm test` and `npm run build`, all clean.
- [ ] README, CLAUDE.md, `docs/RELEASE_WINDOWS.md` (where the chunk says), `requirements.txt` (if dependencies changed), and the release-note draft.
- [ ] No version bump, no `backend/release_notes.py` entry, no git tag.
- [ ] PR open with the program's title prefix; subscribed; check-in scheduled.
- [ ] Row marked complete with the PR number, as the PR's last commit.
- [ ] After the merge: the next prompt sent to Abraham.
