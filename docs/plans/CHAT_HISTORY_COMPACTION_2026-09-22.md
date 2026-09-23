# Chat history compaction

Owner: Abraham. Opened 2026-09-22 ("we will need some kind of compaction
mechanism … but we need to be smart about it otherwise we will lose
valuable context"). This file is the plan AND the handoff: a fresh session
reads it top to bottom, reconciles the implementation record against
`master`, picks the next phase, and records where it stands here before
stopping.

## The problem, in one paragraph

Only the chat history needs compacting. Everything else the model sees
each turn — the document, project facts, follow-ups, research, Final QC,
reference stubs — is rebuilt fresh from structured state every turn
(`conversation._turn_context_text`); research and QC are bounded one-shot
fan-outs; and the project brief already carries a project's assets between
sections without any transcript. `session.history`, by contrast, only
grows. Every turn re-sends all of it, and nothing bounds it: once history
plus context passes the model's 1M-token window, every turn fails with
`prompt is too long`, nothing in the app recovers, and the saved project
carries the problem into every later session. Long before that it costs
money (a lapsed 1-hour cache re-writes the whole conversation at 2× input)
and quality (Anthropic's context-window docs: "as token count grows,
accuracy and recall degrade").

## What actually fills the history (measured 2026-09-22)

Most of it is not conversation. Every successful `apply_spec_edits` call
returns the **entire document outline** (`_run_tool`:
`{"applied": …, "outline": outline(session.doc.doc)}`), so do rejected edit
batches (the outline appended to the error) and `apply_qc_fixes`; the
result is committed to history permanently. Measured with the real
document model (`apply_edits` + `outline`) on a synthetic but realistic
21 13 13-sized section — 3 parts, 27 articles, ~300 paragraphs, ~24k
tokens of text — edited one article batch per call, as
`_FULL_DRAFT_POLICY` paces a full draft:

| | Saved to history | With the outline dropped |
|---|---|---|
| One "Draft full section" turn | ~260k tokens, 85% stale outlines | ~38k |
| Each later one-sentence edit | ~17k (the outline again) | ~140 |
| Full draft + 60 ordinary turns (35 with one edit) | ~876k — at the ceiling | ~71k (12× smaller) |

Estimates: synthetic paragraph text, ~3.5 characters per token (Sonnet 5's
tokenizer runs above the app's usual len/4). The mechanism is exact; the
totals scale with document size and edit cadence. Phase 1's
`tools/chat_history_profile.py` measures real `.baspec` files. The owner ran
it on one real project on 2026-09-23 (below); this table stays because it
shows the mechanism.

### Measured on a real project (2026-09-23)

The owner's run of `tools/chat_history_profile.py` on one saved project
(identified only by its hash, `74da7e1a3def`): 23 typed turns, 184
messages. The token figures are the profiler's len/4 estimates, which
overstate encrypted payloads such as web search results.

| | ~tokens | Share |
|---|---:|---:|
| As saved: what an older build re-sent with every message | ~431,166 | |
| Stale outlines (30 of them), which Phase 1 removes | ~325,431 | 75.5% of as saved |
| Fetched page text, which Phase 2 removes | 0 | the project read one page, already small |
| Now: what this build sends once the file is opened | ~105,735 | |
| of which web search results (6 blocks, kept) | ~52,912 | 50% of now |

What it shows:

- **The synthetic finding holds.** Stale outlines were three quarters of
  what this project re-sent, at about 10.8k tokens per outline. Phase 1
  alone cuts the re-sent history to a quarter.
- **Growth per turn fell from about 18.7k tokens to about 4.6k.** At the
  old rate, the history alone would have reached the backstop's 85% of the
  1M window around turn 45. At the new rate, D1's 600k trigger is about
  130 turns in.
- **Money (Sonnet 5 list prices, rough).** Each message now re-reads about
  $0.02 of history instead of about $0.09 (cache reads at $0.20/M).
  Rebuilding a lapsed 1-hour cache costs about $0.42 instead of about
  $1.72 ($4.00/M).
- **The harvest already reads all of this conversation.** Its transcript
  cap (`HARVEST_MAX_TRANSCRIPT_CHARS`) is 400,000 characters, and this
  project's typed text is about 75,000.
- **Web search results are half of what is left**, and only condensing can
  remove them: the API needs their encrypted content intact.
- This is one project; the figures scale with document size and with how
  often the model edits.

**What it implies for the open items** (recommendations; the owner
decides):

- **Routine condensing:** a project like this one would reach the trigger
  around turn 130, more than 100 turns past where this one stands. The paid
  recall check that gates the default (Phase 3 → Before it is on by
  default) buys little for now. Keep it off; the backstop covers the
  ceiling. *The owner decided otherwise the same day (D5): routine
  condensing is on by default, without the recall check.*
- **D4's harvest wiring stays outstanding.** The owner decided D4 (yes,
  through the harvest), and it is half built. The measurement only bears
  on its priority: a summary exists only once a chat is condensed, and the
  harvest already reads this whole conversation, so the wiring pays off
  only in chats far longer than this one. It stays owed unless the owner
  reverses D4.
- **Phase 5** is the one open item with a measurable payoff. A full draft
  of about 25 article-by-article edit calls writes each ~10.8k-token
  outline once and re-reads it on every later call. That is about $0.68
  written (25 × 10.8k at $2.50/M) plus about $0.65 read (300 × 10.8k at
  $0.20/M), roughly $1.30 per full draft of a section this size. Returning
  only the edited article's outline would remove most of it. It still needs
  the paid before/after its section names.

Two more growth sources, both kept forever today:

- **Fetched web pages.** Each chat `web_fetch` can leave up to
  `WEB_FETCH_MAX_CONTENT_TOKENS` (50k) tokens of page text in history, four
  fetches per round (`CHAT_MAX_FETCHES`). Only fetched PDFs are elided at
  commit (`elide_all_pdf_sources`). (Phase 2 drops the page text too; see
  its section.)
- **Web search results** (encrypted content the API needs intact — they
  can only leave with a whole summarized span, never be edited).

The same outlines also pile up *inside* the full-draft turn itself (~220k
tokens by the last article), which commit-time elision does not touch —
see Phase 5.

## The design: three layers, cheapest and safest first

1. **Don't save what is already saved elsewhere** — deterministic, lossless,
   no model call. Most of the win (Phases 1–2).
2. **Condense the conversation rarely, between turns** — a summary the model
   sees in place of old turns. The full transcript is never deleted
   (Phase 3).
3. **Condensed is not deleted** — a recall tool pulls exact earlier wording
   back on demand, so a detail the summary dropped is one call away
   (Phase 3, shipped with Layer 2).

### How each kind of context survives

| Could be lost | Where it survives |
|---|---|
| Provision text | Never summarized; the full document is sent every turn |
| Settled decisions | Project facts ledger, plus the rationale in the summary; decisions the ledger is missing go to the harvest (Phase 4) |
| Open questions and next steps | "Waiting on you" ledger, plus the summary's "where things stand" |
| Rejected options, preferences, corrections | Their own sections in the summary — what generic summaries drop first |
| Exact numbers and wording | Kept verbatim in the summary; everything else through recall |
| The last few exchanges | Kept word for word after the summary |
| The user's chat log | Untouched on screen and in the saved file; a divider marks the cut |

### Standing rules (every phase)

- **`session.history` stays complete and append-only.** Compaction is a
  VIEW the request builder sends, never an edit of the record. The chat
  transcript (`project.chat_transcript`), figure `message_index`, the
  harvest's transcript source refs (project-workspace Phase 4) and the
  saved file all read the full history. Deterministic elisions of
  machine payloads (PDFs, figure source, reference bodies, outlines) are
  the one exception, and they only ever touch data held elsewhere.
- **Never compact inside a turn.** Only between turns, and only at the start
  of a user turn you typed — never splitting a `tool_use`/`tool_result`
  pair, a `server_tool_use`/result pair, or a `pause_turn` resume.
- **Each prefix change costs one cache re-write**, so a compaction must be
  rare and big. Commit-time elisions are cache-free: commit already
  rewrites the last exchange (strip-at-commit), and the next turn writes
  that exchange fresh anyway.
- **Never summarize what is rendered fresh** (document, research, QC,
  facts, follow-ups): a summary copy goes stale and contradicts the live
  state. The summary may point at them by id.
- **Fail open.** A compaction that fails, refuses or is not ready leaves the
  turn on full history; only the hard backstop (Phase 3) may block, and it
  says so on screen.

## Implementation record

| Phase | What | Status | Commit/PR | Notes |
|---|---|---|---|---|
| plan | this file | **complete** | `72a3b2f` (PR #182, merged `7edddd3`) | |
| 1 | Stale outlines out of saved history + history composition | **complete** | `43a8ad8` (PR #182, merged `7edddd3`) | commit-time + load-time elision; Developer tools row; offline profiler |
| 2 | Fetched web-page text out of saved history | **complete** | `a6e5fea` (PR #183, merged `7fc6e24`); rework `6cc34bc` (PR #192, merged `7e32d14`); default on (PR #194, merged `7634c8d`) | commit-time + load-time elision. The PR merged without its live canary; the canary's first run (2026-09-23) was **refused** — a saved citation into the trimmed text. The rework folds quoted passages into the note and drops those citations. The second run (2026-09-23) **passed** on that shape, so the trim is **on by default** (`BUILD_A_SPEC_ELIDE_FETCHED_PAGES=0` turns it off) — see Phase 2 → Canary result |
| 3 | Condensed conversation (Layer 2) + `recall_conversation` (Layer 3) | **complete** | `ab7e402`, `2e98b3a`, `9fa6aaf`; review fixes `48dd034`, `b7cd064`, `7545c46`, `a1ecfab`, `a609e0d` (PR #189, merged `971683f`); citation repair `6cc34bc` (PR #192, merged `7e32d14`); default on (PR #196) | both halves; backstop always on. Routine condensing shipped off until the recall check, and is **on by default** since owner decision D5 (2026-09-23), without that check (`BUILD_A_SPEC_CHAT_COMPACTION=0` turns it off). A condensed view broke citation numbering until the citation repair — see Phase 3 → As built |
| 4 | Promote before prune | **handed off** | | this is project-workspace Phase 4 (`project-workspace/04_HARVEST.md`); don't build it twice |
| 5 | Within-turn outline trim (optional) | not started | | changes what the model sees mid-turn; measure first |

A status moves to **complete** only after the PR merges, set by the next
session that touches this file (the project-workspace convention).

**Where it stands (2026-09-23; recorded at the 1.21.0 closeout, updated
when PR #189 merged, when the Phase 2 canary's first run came back
refused, when its second run passed, when the owner supplied a real
measurement, and when the owner turned routine condensing on).** Phases 1–3 are on `master`. 1.21.0 was prepared to carry
Phases 1 and 2 (the project-workspace closeout,
`project-workspace/07_RELEASE_CLOSEOUT.md`), but it is not tagged. The owner decided D1–D4 on
2026-09-22 (see the Decisions table). The owner supplied one real
measurement on 2026-09-23 ("Measured on a real project", under "What
actually fills the history"). It pointed to keeping routine condensing off
for now; the owner decided otherwise the same day (D5: on by default,
without the recall check). It puts a price on Phase 5. It does not change
D4: that wiring stays decided and outstanding (below), with little payoff
for projects this size.

- **Phase 2** merged in PR #183 (`7fc6e24`) without its live canary, so
  the 1.21.0 closeout switched it off: `BUILD_A_SPEC_ELIDE_FETCHED_PAGES`
  gates both the commit-time and the load-time elision. The owner ran the
  canary on 2026-09-23 and it was **refused**: the API checks a saved
  citation against the document it lands on, and the trim had left the
  reply's citation pointing past the end of its note (see Phase 2 → Canary
  result). The rework (PR #192, merged `7e32d14`) folds each quoted passage
  into the page's note and removes the citations into the trimmed text.
  The owner's second run, the same day, **passed** on that shape, so the
  trim is **on by default** on `master` (PR #194); `=0` turns it off.
  1.21.0's release notes were written while it was off, so the next release
  cut from `master` owes Phase 2's release-note draft (below), beside
  Phase 3's.
- **Phase 3** merged in PR #189 (`971683f`) — before v1.21.0 was tagged, so
  `master` carries it although 1.21.0's release notes do not mention it. No
  release is planned for now (owner, 2026-09-23); whichever release next
  ships from `master` must carry Phase 3's release-note draft (below). It
  condenses with our own summarizer at D1 (600k tokens of committed
  conversation, the last 3 turns kept) and ships `recall_conversation` with
  it. **Routine condensing is on by default** since the owner decided it
  on 2026-09-23 (D5), without the paid recall check under "Before it is on
  by default"; `BUILD_A_SPEC_CHAT_COMPACTION=0` turns it off (PR #196).
  Each routine summary is a billed background call. The backstop, which
  condenses only when a message would not otherwise fit, runs either
  way. The canary's refusal
  exposed a defect in it: a condensed view drops the pages its oldest turns
  fetched, which shifted every later citation's `document_index`. The
  citation repair (PR #192) fixes every outgoing request (see Phase 3 →
  As built).
- **D4 is yes, and half of it is in.** Phase 3's summary lists the
  decisions missing from the ledgers, each tagged with the turn it was
  settled in, and that list is saved with the summary. Feeding it to the
  harvest (project-workspace Phase 4, merged in PR #185) is not wired yet —
  a harvest proposal must cite a source that resolves, and a summary line
  is not one (see Phase 3 → As built). The harvest spec keeps the seam
  (`project-workspace/04_HARVEST.md`, deviation 23): one more framed,
  neutralized block in `HarvestInputs`, reaching the sheet through the same
  checks and commit. That deviation was written before D1, D3 and D4 were
  decided, so it still calls them open.
- **Phase 5** is optional and still waits on a measured before/after on
  real full drafts. The real measurement puts its payoff at roughly $1.30
  per full draft of a section that size.

## Decisions (owner)

| # | Question | Recommendation | Status |
|---|---|---|---|
| D1 | Trigger size for condensing, and how many turns to keep word for word | 150k tokens of committed conversation (the API's own default threshold); keep the last 3 user turns | **decided 2026-09-22: condense at 600k tokens of committed conversation; keep the last 3 user turns word for word.** Differs from the recommendation, so the figures under Phase 3's "Cost" (which assume 150k) scale up for the Phase 3 build |
| D2 | Drop fetched web-page text when a turn is saved, like PDFs? | Yes: the reply keeps its cited passages; the model can re-fetch | **decided 2026-09-22: yes** |
| D3 | Our own summarizer, or Anthropic's on-demand compaction beta? | Our own (reasons under Phase 3) | **decided 2026-09-22: our own summarizer**, not the on-demand compaction beta |
| D4 | Should flagged decisions become Project-facts suggestions? | Yes, via the harvest (Phase 4 hand-off) | **decided 2026-09-22: yes**, through the harvest (the Phase 4 hand-off) |
| D5 | Turn routine condensing on by default before the paid recall check? | No: keep it off until the check passes ("Before it is on by default"); the real measurement put the trigger around turn 130 for a project like the one measured | **decided 2026-09-23: on by default, without the recall check.** `BUILD_A_SPEC_CHAT_COMPACTION=0` turns it off; the backstop runs either way |

Phase 1 needs none of these: it removes only data that is stale by
construction and duplicated in full by every turn's PROJECT CONTEXT.

## Phase 1 — stale outlines out of saved history

**Goal.** A committed turn keeps what an edit DID (the small `applied`
records, the QC `outcomes`) and drops the document outline it returned,
which is stale the moment the next edit lands and is superseded every turn
by the full, current document with every element id in PROJECT CONTEXT.
Within a turn nothing changes: the model still receives the outline
between calls, which is what it maps new ids with.

**Scope.**

- `backend/llm/history_hygiene.py` (new leaf module — `project.py` and
  `conversation.py` both need it, the `server_tool_pairing` precedent):
  `elide_stale_outlines(messages)` (copy-on-write; returns the SAME list
  object when nothing changed; idempotent), `count_stale_outlines`,
  `history_composition(messages)` (size by category — counts and
  characters only, never text), and the two shared literals the producer
  and the elider must agree on: `REJECTED_BATCH_DOCUMENT_HEADER` and
  `STALE_OUTLINE_NOTE`.
- Tool results matched back to their `tool_use` by id (the
  `_elide_reference_tool_results` shape), for `apply_spec_edits` and
  `apply_qc_fixes`. A JSON result keeps every key and only its `outline`
  value becomes the note; a rejected batch keeps its error text and loses
  the document appended after `REJECTED_BATCH_DOCUMENT_HEADER`. Anything
  unrecognized is left exactly as it is.
- `conversation._committed_messages` applies it beside the figure and
  reference elisions (before the pairing guard); `_run_tool` builds the
  rejected-batch text from the shared header constant.
- `project.load_project` applies it to histories saved by earlier builds,
  after the unpaired-server-tool repair, logged at INFO with a count
  (`buildaspec.project`, never a trace event — `load_project` runs under
  `session_state_guard()`). The file itself changes only at the next save.
- `/api/diagnostics` session block gains `history_composition`, computed
  from a shallow snapshot taken under the guard and measured after it;
  Developer tools → Session state renders it as one row.
- `tools/chat_history_profile.py`: offline, read-only profile of saved
  `.baspec` / `.json` projects — the composition of the history as saved,
  and what Phase 1 removes from it. Prints counts and sizes only, with the
  lint/QC profilers' privacy posture (no text, artifacts named by hash in
  the pasteable block).

**As built** (differences from the scope above, and why):

- The note that replaces an outline does not name the context block by its
  header. A first draft said "…is in this turn's PROJECT CONTEXT", which
  `test_context_block_never_fossilizes_into_history` rightly rejects (that
  header must never appear in saved history); it now says the current
  document "arrives fresh with each new message".
- An outline shorter than the note (an empty document's one line) is never
  replaced, so the elision can only shrink a history. Enforced inside the
  helper and again at every caller.
- Tests: `tests/test_history_hygiene.py` (7). Reverted in place to prove
  them load-bearing: commit elision → 3 red, load elision → 1 red, tool
  scoping → 2 red.

**Release-note draft** (shipped in 1.21.0; see "Release policy"
below):

> **Long sessions stay lighter.** Every edit used to save a full copy of
> the section's outline into the conversation, and the model re-read all
> of those copies on every later message. Those stale copies are no
> longer kept (the model still sees the whole, current document each
> turn), so long drafting sessions cost less per message, stay further
> from the model's context limit, and save smaller project files.
> Projects saved by earlier versions are trimmed the same way when you
> open them.

## Phase 2 — fetched web-page text out of saved history

Gated on D2 and on one live request (below). Extend
`elide_all_pdf_sources`' posture to non-PDF `web_fetch_tool_result`
documents at commit: keep `url`, `retrieved_at`, the document `title`;
replace the text source's `data` with a note naming the URL and saying the
model can fetch it again. Same block surgery the PDF elision already ships.

**Why a live check first.** Chat fetches carry `citations: {enabled:
true}`, so later assistant text can hold `char_location` citations into the
fetched document. The PDF elision has shipped with the equivalent shape
for PDFs and no failure has been reported, but whether the API validates a
historical citation against its (now replaced) document is not documented.
One opt-in request settles it: a history holding an elided fetched page
plus a citation into it, sent once. **Settled 2026-09-23: the API checks,
and refused it; the reworked shape, with no citation into the trimmed text,
passed the same day** (see Canary result). Follow the `tools/qc_verifier_canary.py`
pattern (no request without `--run`) and record the result here; CLAUDE.md
names that canary as the sole paid exception, so adding a second one means
updating that ground rule in the same change.

**As built** (differences from the scope above, and why):

- It also runs when an older project is opened, not only at commit (the
  scope names commit). Otherwise a file saved before this phase re-sends
  every page it ever fetched, on every turn. Phase 1 set the same posture:
  copy-on-write, an INFO log, and the file changes at the next save.
- `elide_fetched_page_text` lives in `backend/llm/history_hygiene.py` beside
  Phase 1's elision, not inside `resend_sanitizer.elide_all_pdf_sources`.
  The sanitizer is a near-verbatim Spec Critic port shared with the research
  and QC continuations, which must keep page text mid-run.
- Only the text source's `data` changes. The document block stays, because a
  citation's `document_index` counts every document block across all
  messages, so dropping one would point later citations at the wrong page.
  `url`, `retrieved_at`, `title` and the citation setting stay too.
- A fetched PDF keeps the note the PDF elision already wrote (matched by
  `PDF_ELISION_NOTE_PREFIX`). A page no longer than its note stays as it is,
  and that rule is also what makes the elision idempotent.
- `history_composition` gains a `fetched_page_texts` count and a "page text
  in fetched web pages" category. Developer tools' History makeup row shows
  the count when it is nonzero, and the profiler's "Now" applies both trims
  and counts fetched pages per file. The composition counts assistant
  messages only, matching the elision; a test caught a first draft that
  counted a fetch-shaped block in a user message.
- The canary is `tools/fetch_elision_canary.py`. It uses the production
  commit transform, chat tools and resend sanitizer, with a one-line system
  prompt, adaptive thinking at `low` effort and a 1,024-token ceiling (about
  two cents at most). It refuses to send if the commit transform did not
  replace the page. `--control` sends the same conversation with the page
  text kept, as one more request, only when a refusal needs diagnosing.
  Unlike the QC canary it has hermetic tests.
- **Reworked after the canary's first run (PR #192).** The run was
  refused because the reply's citation still pointed into the trimmed
  text. The trim now removes the citations into a page it trims and writes
  each quoted passage into the page's note, deduped, in quoting order,
  within 4,000 characters per page and with a disclosed count of what did
  not fit, never growing the page. The passage goes to the page the
  citation names (its index, read against the request it was written in,
  so a commit passes how many documents its view sent ahead of the turn),
  because a page fetched twice or a mirror can hold the same passage
  (Codex, PR #192). A citation that also fits a document the trim keeps is
  left alone. A trimmed page is recognized by the note's prefix, because a
  note with quotes is longer than a bare one. The canary sends the new
  shape and refuses to send the old one.
- **Opening a project reads no citation numbers (PR #194).** Once the
  default was on, Codex showed that the load-time trim read each citation's
  index against the whole saved history. A reply written after the
  conversation was condensed was numbered against the condensed view, and
  nothing saved says which replies those were. So, for a passage two pages
  hold (a page read twice, or a mirror), the quote could be filed under
  the wrong page and its citation removed, and the next save would keep
  that. Load now passes `document_offset=None`: a passage only one page
  holds still folds into it, and one that more than one page holds leaves
  those pages and their citations as they are. The offline profiler does
  the same, and a commit, whose numbering is exact, is unchanged.
- Tests: `tests/test_fetched_page_elision.py` (6) and
  `tests/test_fetch_elision_canary.py` (6). Each mechanism was reverted in
  place to prove it load-bearing: the commit wiring → 4 red, the load wiring
  → 1, the PDF-note check → 3, the shrink rule → 4, the composition split
  → 2, its assistant-only scope → 1, the profiler's second trim → 1, the
  canary's guard → 1.

**Canary result** (the owner's run; paste its output here):

- **Run 1 — refused** (the owner's run, 2026-09-23, on the shape PR #183
  shipped: the page trimmed, the reply's citation into it kept). Output,
  verbatim:

  ```
  Configured API key: yes (source: keyring).
  Sending one request to claude-sonnet-5: elided page; the saved page is 257 of 2,489 characters and a reply cites characters 2406-2455 of the original.
  Fetch elision canary: the request failed: BadRequestError (400): Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'messages.1.content.4.citations.0: Start index 2406 is beyond document length 257'}, 'request_id': 'req_011CfLMxM5J8f5pAjbUfWPvq'}
  The provider REFUSED a saved conversation whose fetched page text was replaced while a reply still cites it. Keep the page-text trim switched off (BUILD_A_SPEC_ELIDE_FETCHED_PAGES) until this is resolved. Run again with --control to check whether the same conversation is accepted with its page text intact.
  ```

  The API checks a saved citation against the document it lands on. The
  `--control` run was not made: the error names the cause exactly (the
  citation's span lies past the end of the note), which settles the
  question `--control` exists to answer.
- **What run 1 changed** (PR #192): the trim now writes each passage a
  reply quoted into the page's note and removes the citations into the
  trimmed text; the canary sends that shape, and refuses to send the old
  one. Every chat request also goes through the citation repair (Phase 3 →
  As built), which drops a citation that no longer fits its document on the
  way out. The canary forces the trim on for its own request whatever the
  switch says, so it tests the shape the switch turns on.
- **What run 1 decided:** the trim stayed **switched off**
  (`BUILD_A_SPEC_ELIDE_FETCHED_PAGES`, default `0`, as the 1.21.0 closeout
  had set it) until a run passed on the new shape, so no saved history took
  an unverified shape. With the switch off, commit and project load keep
  fetched page text exactly as they did before this phase.
- **Run 2 — passed** (the owner's run, 2026-09-23, on the shape PR #192
  ships: the quoted passage in the page's note, and no citation into the
  trimmed text). Output, verbatim:

  ```
  Configured API key: yes (source: keyring).
  Sending one request to claude-sonnet-5: elided page; the saved page is 292 of 2,489 characters, and the reply's citation into characters 2406-2455 of the original was replaced by that passage, kept in the page's note.
  Fetch elision canary passed: the provider accepted a saved conversation whose fetched page text was replaced by a note carrying the passage its reply quoted (stop_reason=end_turn). Record this in the plan's Phase 2 section; the page-text trim's default can then be switched on.
  ```

  The 292 characters are the 201-character note plus the passage the reply
  quoted, which is what the same request rebuilt offline produces.
- **What run 2 decided: the trim is on by default** (PR #194).
  `settings.ELIDE_FETCHED_PAGE_TEXT` defaults to `True`;
  `BUILD_A_SPEC_ELIDE_FETCHED_PAGES=0` keeps page text, exactly as before
  this phase. `test_the_page_text_trim_ships_switched_on` pins the default,
  README's Configuration row and "Fetched web pages" subsection say so, and
  the Release-note draft below is owed to the next release cut from
  `master`.
- **If a later run is ever refused:** set
  `BUILD_A_SPEC_ELIDE_FETCHED_PAGES=0`, run it again with `--control`,
  record both outputs here, and rework the elision before the default goes
  back on. (Run 1's refusal took the option this bullet used to name: drop
  the citations that point into a trimmed page, keeping their quoted
  passages in the note.)

**Release-note draft** (not in 1.21.0's release notes, which were written
while the trim was off. It is owed to the next release cut from `master`,
where the trim is on by default since run 2 passed):

> **Web pages the assistant reads stop riding along.** When the assistant
> read a web page during a chat, the page's full text was saved into the
> conversation and re-sent with every later message, up to about 50,000
> tokens a page. Now a saved turn keeps the page's address, its title and
> the passages the reply quoted, and drops the rest; the assistant reads the
> page again whenever it needs the exact wording. Research-heavy sessions
> cost less per message, stay further from the model's context limit, and
> save smaller project files. Projects saved by earlier versions are trimmed
> the same way when you open them.

## Phase 3 — condensed conversation + recall

Ship both halves together: condensing without recall is where context is
actually lost.

### When

- After a turn commits, measure the **committed conversation** — the
  request view without PROJECT CONTEXT, which cannot be condensed. Cheap
  local estimate every turn; confirm with `messages.count_tokens` on the
  exact request view before compacting.
- Over the D1 threshold (recommended 150k), start a summary **in the
  background** while the user reads the reply (the runner/warm pattern:
  daemon thread, snapshot of `(generation, len(history))`, abandoned on
  reset/load). Adopt it at the start of the next turn under
  `owned_model_turn_guard` only if the generation is unchanged and history
  has only grown since the snapshot (append-only makes that a length
  check). Not ready → that turn runs on the full view, still far below the
  window.
- Keep the last D1 user turns word for word, so the next compaction is
  ~100k+ tokens away. Never compact again while one is pending.
- **Hard backstop:** if the next request's view would exceed ~85% of
  `settings.MODEL_CONTEXT_WINDOW`, condense synchronously before sending,
  with a status frame ("Condensing earlier conversation…"). If that fails
  too, drop the oldest condensed turns from the VIEW with a disclosed
  marker pointing at recall — never a 400 that bricks the project.
- Off in tutorial workspaces (short, disposable).

### How (D3 recommendation: our own summarizer)

- A fork of the chat request: same model, `system`, `tools`, `thinking`
  and `effort`, same committed history, plus one final user message with
  the instruction. Anthropic's caching guidance: "Fork operations must
  reuse the parent's exact prefix" — so it reads the cache the last turn
  wrote. Put a breakpoint at the previous request's committed-history
  boundary for an exact hit (a full-draft turn adds more than the cache's
  20-position lookback). No `tool_choice` (it invalidates the messages
  cache).
- Instruction: Anthropic's recommended client-side compaction prompt
  (the six retention items, `shared/model-migration.md` in the claude-api
  skill) adapted to spec work —
  - keep, close to the user's own words: decisions and why, options
    ruled out and why, exact values (numbers, standards, editions, names),
    how the user likes to work, corrections they made;
  - keep where things stand and what the model promised to do next;
  - do not restate the document, facts, follow-ups, research or QC —
    they arrive fresh every turn; point at facts/follow-ups by id;
  - end with "Do not call any tools while writing this summary; respond
    with text only" (load-bearing when tools stay in the request).
  The instruction message carries the current ESTABLISHED PROJECT FACTS
  and WAITING ON THE USER blocks (small) so the summary can reference them
  instead of restating them, and can list decisions they are missing.
- Validate: non-empty, under a size cap, required headings present, no
  `tool_use`. Anything else (including a refusal) → keep the full view,
  retry after the next turn with backoff.
- Meter it as its own ledger category ("Conversation condensing"), priced
  on the interview model.

### Where it lives

- `session.compaction`: `{summary, keep_from, covers_turns, created_at,
  model, tokens_before, tokens_after, version}` — local to the section,
  wiped on reset (decide it in `test_session_wipe.py`'s field sweep),
  persisted as an optional `.baspec` key (lenient load; a malformed record
  loads as "no compaction", never an error), restored on load.
- Only `capture_request_inputs` / `_build_chat_request` change: the view is
  `history[keep_from:]` with the summary prepended as a separate text block
  of the first kept user message (copy-on-write; keeps role alternation
  and a user-first request — Sonnet 5 has no mid-conversation system
  messages). The summary is stable between compactions, so it sits inside
  the cached prefix; `_committed_history_boundary` works on the view.
- Framing: `<earlier_conversation_summary turns="1–40">…</…>` plus
  "written when earlier turns were condensed; PROJECT CONTEXT is current
  wherever the two differ; recall a turn before relying on an exact detail
  from before the cut". Neutralize the frame's own tags and the PROJECT
  CONTEXT markers inside the summary (it quotes the user; the
  untrusted-text posture from reference documents).
- Re-compaction summarizes the previous summary plus the turns since.
- Keep-tail is safe here: commit already strips thinking blocks, which is
  what breaks keep-tail compaction under Anthropic's preserved-thinking
  check (Opus 5.5 / Fable 5.1). Re-check if that ever changes.

### Recall

- `recall_conversation` chat tool: keyword search over the condensed turns
  (user and assistant text; tool calls by name only) returning the top
  matches with their turn numbers, or `turns: [N, M]` returning those
  turns verbatim, both capped. Local search, no embeddings, no new
  dependency.
- In the tool list from the first turn of every session (adding a tool
  later re-writes the whole cache: tools render first). Appended last in
  `_chat_tools()`.
- Results elided from committed history like `read_reference_doc`'s (they
  are copies of the record).

### Surfaces

- Chat: a divider at the cut, "Earlier conversation condensed (turns 1–40)
  · View summary"; everything above it stays scrollable. The context pill
  drops.
- Settings usage table: the "Conversation condensing" line.
- Trace: `chat_compaction` app event (sizes before/after, usage, duration,
  outcome, fallback reason). Developer tools: the compaction record.
- `TrustDeepDiveModal`: a runtime card, and re-scope "no model runs on its
  own" (the background summary is a model call the user did not click —
  the v1.11.0 debrief precedent).

### Why not Anthropic's built-in options

- **Threshold compaction** (`compact_20260112`) runs inside the request
  that crosses the line — mid-turn, possibly mid-tool-loop — and would
  summarize the newest message, which carries the full document.
- **Context editing** (`clear_tool_uses_20250919`) clears only client tool
  results (not web results) and invalidates the cache every time it
  clears; Phase 1 does the useful part once and permanently.
- **On-demand compaction** (`compact-2026-09-04`; supports Sonnet 5) is
  the real alternative, and Anthropic's docs recommend it where available.
  Against it here: a beta header on every chat request from then on and a
  signed block in every saved project, with nothing documenting whether a
  saved block survives a beta revision or a model switch (the app has
  already had a saved-history defect make every later request fail — the
  unpaired `server_tool_use`); it summarizes exactly what it is sent, so it
  cannot see the kept tail it should not repeat; the data-retention page
  lists threshold compaction as ZDR-eligible but not the on-demand kind
  separately, which the trust dossier's ZDR claim depends on. Its main
  advantage — keeping thinking valid in kept turns — does not apply,
  because commit strips thinking blocks. If owning a summary prompt is
  unwanted, it is the choice; nothing else in this design changes.

### Cost (Sonnet 5 list prices)

About $0.10–0.15 per compaction at 150k (cache-read input plus a few
thousand output tokens). Afterwards each turn re-reads ~25k tokens instead
of 150k+, and rebuilding a lapsed 1-hour cache costs ~$0.10 instead of
$0.60+. Pays for itself within a handful of turns.

### Before it is on by default

A small paid, opt-in recall check on the owner's own transcripts:
condense at turn K, then ask about a decision, a rejected option and an
exact value from before K — with and without the summary, and with recall.
The claude-api skill's `build-eval` guide is the method.

**Waived (owner decision D5, 2026-09-23).** Routine condensing is on by
default without this check. The check is still how to learn what a summary
loses, and worth running once a real transcript passes the trigger. If it
shows a summary losing something that matters, rework the summary
instruction; `BUILD_A_SPEC_CHAT_COMPACTION=0` is the way to keep routine
condensing off while that happens.

### As built, and the release-note draft

**As built** (differences from the scope above, and why):

- **Sizing is an estimate calibrated by the provider's own count, not
  `messages.count_tokens`.** The counting endpoint returns
  `invalid_request_error` for requests carrying server tools — web search
  and web fetch among them — and every chat request carries both (checked
  against the token-counting docs on 2026-09-23, which point such requests
  at the Messages API's `usage` instead). So every size is serialized
  characters × a tokens-per-character ratio learned per session from the
  last committed turn's `usage` (clamped between 1/8 and 1/1.5; 1/3.5 until
  the first measurement, which errs toward condensing early). The ratio is
  process-local and cleared on reset and load.
- **Two measures, deliberately different.** Routine condensing compares
  the committed conversation (the history as later requests send it,
  without PROJECT CONTEXT, system prompt or tools) against D1, as the
  decision is worded. The backstop compares the whole first request (system
  and tools, the view, and the new turn with its PROJECT CONTEXT) against
  85% of `settings.MODEL_CONTEXT_WINDOW`.
- **Routine condensing is a knob, off by default**
  (`BUILD_A_SPEC_CHAT_COMPACTION`, with `_THRESHOLD` and `_KEEP_TURNS` for
  D1). "Before it is on by default" names the paid recall check as the
  gate; the knob is how the gate is kept. The backstop is not a knob and
  runs regardless: without it a conversation past the window fails on
  every message and the saved project carries that. *Since 2026-09-23 the
  knob defaults on (D5, PR #196); the gate was waived, not passed.*
- **Adopted at whichever comes first, checked by content.** A finished
  background summary is adopted by the worker itself when no turn is
  streaming, when a turn ends (after `finalize_model_turn`), or at the
  start of the next turn under `owned_model_turn_guard` — never mid-turn.
  The check is a digest over the role and text of every condensed message
  (`CompactionRecord.fits`), not the plan's length check:
  `delete_reference_if_idle` truncates history without a generation bump,
  and a history that was truncated and then grew back past its old length
  would pass a length check while the summary described turns that are
  gone. The same digest makes a saved record load only while it still
  describes the file's history. A record is only ever replaced by one
  covering more turns.
- **A reference delete drops a record that covers or starts at a removed
  turn**, and always abandons the runner (a summary still being written
  read the old history).
- **Re-compaction** sends the current view (the earlier summary as a
  preface, plus the kept turns) and tells the model to carry everything
  the earlier summary records into the new one, which replaces it.
- **The fork**, as specified: the chat's model, system, tools, thinking and
  effort; one breakpoint at the previous turn's committed-history boundary
  (the message before the last typed turn of the view) at
  `CHAT_CACHE_TTL`; the tail unmarked (`_with_cache_breakpoints(...,
  mark_tail=False)` — no later request repeats the instruction, so caching
  it would be pure cost); no `tool_choice`, no container. Its output
  ceiling is 64k (`CHAT_COMPACTION_MAX_TOKENS`), which is not part of the
  cached prefix.
- **The instruction** carries eight required headings: Anthropic's six
  retention items as seven sections (exact values get their own), plus
  D4's "Decisions the ledgers are missing", each line tagged with the turn
  it was settled in (recall's numbering). The facts and waiting-on-you
  blocks ride in a `<ledgers>` frame, and every frame is made inert inside
  what it frames. A reply is refused — and still metered — when it is a
  refusal, calls a tool, stops for any reason but `end_turn`, has no
  `<summary>`, is empty or over 200k characters, or misses a heading; the
  error carries a closed code for the trace and Developer tools.
- **Backoff** after a failed routine attempt: the next may start one
  committed turn later, then two, four, up to sixteen. The backstop
  ignores it (a message that would not fit tries at once) and, when a
  background summary is already running, waits for it for up to 300 s,
  showing the `condensing` status, before writing one itself.
- **The last resort covers the no-summary case too.** The plan named
  dropping the oldest *condensed* turns; as built, when no summary can be
  made at all, the one request leaves the oldest turns out (fewest first)
  with a disclosed note, `recall_conversation` can read every turn left
  out, and nothing about the session changes.
- **New: a request the provider rejects as too long is retried once.** The
  estimate can let through a request the provider counts as too long (a
  conversation of unusually token-dense text). That 400 arrives before any
  output, so the turn retries once with a pessimistic view (0.5 tokens per
  character, 70% of the window, at least one more turn left out) instead
  of failing now and on every later message. `_enter_stream` no longer
  mistakes that 400 for a rejected thinking-display key: before, it would
  have switched the thinking summary off for the whole process and resent
  the same request.
- **The turn announces its view.** A `compaction` SSE event (sizes and the
  turn range, never the text) opens every turn whose view carries a
  summary, so the chat's divider moves the moment one is adopted;
  `condensing` joins the status vocabulary. `GET /api/chat/compaction`
  returns the text for **View summary**. The divider's position is derived
  client-side from `covers_turns`, counting only the messages that reached
  the saved history (notes and failed turns skipped).
- **Surfaces**: the "Conversation condensing" usage line (priced on the
  interview model), the `chat_compaction` trace event, a Developer tools
  row, a trust-dossier runtime card with "no model runs on its own"
  re-scoped (the background summary is the one you can switch on), a Help
  line, and a tour step on the always-present chat pane (appended to the
  chapter, so no resume index moves and `TOUR_VERSION` stays 8).
- **Not done here: feeding D4 to the harvest.** The summary's
  missing-decisions list is written, turn-tagged and saved, but not yet a
  source for the harvest sheet. Two things need deciding first, and both
  are the harvest's: a harvest proposal must cite a source that resolves,
  and a summary line is not one (a summary turn number is the Nth message
  the user sent, while the harvest's `turn:N` is the Nth assistant reply,
  and the two can differ when a turn committed no reply text); and the
  harvest reads the full history, so a summary line would compete with the
  turn it came from. `04_HARVEST.md` deviation 23 keeps the seam.
- **Cost at D1, re-estimated** (Sonnet 5 list prices; the "Cost" section
  above assumed 150k). One routine summary re-reads ~600k tokens from the
  last turn's cache (~$0.12 at the 0.1× read rate), sends a ~2k-token
  instruction uncached, and writes a summary plus its thinking (10–35k
  output tokens, ~$0.10–0.35): roughly $0.25–0.50 each. The next turn
  writes a fresh cache for the much shorter view once, and every turn
  after that re-reads tens of thousands of tokens instead of 600k (about
  $0.10 less per turn), so a summary pays for itself within a handful of
  turns — the same shape as the 150k figures, scaled up. Estimates, not a
  measurement: the usage table's "Conversation condensing" line is the
  real number.
- **Found in the build**: `conversation._CONTEXT_BOUNDARY_PATTERN` is
  quadratic on a long run of `=` that never completes a marker (recorded
  found-not-fixed in project-workspace Phase 5A). This module's copy
  carries the `(?<!=)` that makes it linear without changing a match,
  because it frames whole summaries and recalled turns.
- **Review fixes (PR #189, Codex).** Three findings, all real. Removing a
  reference now answers the record its truncation left, and the chat
  applies it, so the divider cannot outlive its summary. A condensed turn
  longer than one read (60,000 characters) pages: `recall_conversation`
  takes `offset`, a partly shown turn names the exact call that reads on,
  pages end on a word, and a search match in a long turn says which offset
  to read from. And a summary that lands after its turn's stream has
  closed now reaches the chat: the doc payload says one is pending
  (running, or finished and not yet adopted), `GET
  /api/chat/compaction/status` answers `{pending, compaction}`, and the
  chat asks it while pending and no turn is streaming, applying only a
  settled answer. Twenty-six more mechanisms were reverted in place; two
  first stayed green (an offset rule refused by a different rule, and a
  "pending" that ignored a finished-but-unadopted summary) and got
  stronger tests.
- Tests: `tests/test_chat_compaction.py` (44; 35 before the review fixes,
  first recorded here as 36) and `frontend/tests/compaction.test.ts`
  (15), plus the wipe-sweep probes and the tool-order pin. Thirty backend
  mechanisms and six frontend ones were reverted in place; the matrix first found three blind spots (the chat
  route's scope flag, the too-long retry, and escaping the summary inside
  its own frame), each fixed by a stronger test before this was recorded.
  Every mechanism now turns at least one test red.
- **Citations in a condensed view (found 2026-09-23, after merge; fixed in
  PR #192).** A citation names its document by `document_index`, which
  counts every document in the request, and the Phase 2 canary's refusal
  showed the provider checks it against the document it lands on. The
  view leaves the oldest turns out — and the pages they fetched with them —
  so every later citation landed early: on the wrong page, or past the end
  of the list, which the provider refuses. Phase 2's own as-built note had
  said exactly this about removing a document block; this phase missed
  it. The fix is `backend/llm/citations.repair_document_citations`, run on
  every chat request and on the summary call: a citation that no longer
  fits the document it lands on is re-pointed at the earlier document it
  fits (matched by its quoted text, or by title where it quotes nothing),
  or dropped with its words kept.
  It keeps no record of which request numbered what (a reply answered
  under one view carries that view's numbers), looks only backwards (so
  the cached prefix never moves, and the summary call's copy of the view
  is byte for byte the chat's), and returns the same list when nothing
  needs repair. It also covers a fetched PDF's citations once its turn is
  saved as a note. Details and the revert matrix: CLAUDE.md → "Citations
  must fit the request that carries them".

**Release-note draft** (for whichever release carries this; see "Release
policy" below):

> **Long conversations keep working.** A very long drafting conversation
> used to grow until the model could no longer take it in, and from then
> on every message failed. Now the oldest turns are condensed into a
> summary the model reads instead: in the background, while you read a
> reply, once a conversation passes about 600,000 tokens, and on the spot
> whenever a message would not otherwise fit. The summary is kept close to
> your own words: decisions and why, options ruled out, exact values, your
> corrections, where things stand. The last three turns stay word for
> word, and the document, research, QC and project facts still arrive
> fresh with every message. Nothing is deleted: the chat and the saved
> project keep every turn, a divider marks where the condensed part ends,
> and **View summary** shows exactly what the model reads. When it needs
> an exact detail from before the cut, the model looks the turn up in your
> saved conversation. Each summary is one small billed call, with its own
> line in the usage table. Switch the background summaries off with
> BUILD_A_SPEC_CHAT_COMPACTION=0 if you would rather the model condense
> only when a message would not otherwise fit.

## Phase 4 — promote before prune

Handed off. The summary instruction's "decisions the ledgers are missing"
list is exactly the input project-workspace Phase 4 (the harvest,
`project-workspace/04_HARVEST.md`) proposes facts from. Wire the two
together there — as a candidate source for the harvest's preview — rather
than building a second path into the facts store.

## Phase 5 — within-turn outline trim (optional)

The full-draft turn itself re-reads every outline it has been returned
(~220k tokens by the last article of a 300-paragraph section). Returning
only the edited article's outline (plus the `applied` ids) would fix that,
but it changes what the model sees while drafting, so it needs a measured
before/after on real full drafts, not a guess.

## Release policy

Follows the owner's current policy (project-workspace README, 2026-09-22):
PRs land with **no version bump and no release-notes entry**; each phase
writes a "Release-note draft" in this file, and the next release's
closeout writes the real entry from the drafts. v1.20.0 was published
2026-09-22, so its entry is frozen. `project-workspace/07_RELEASE_CLOSEOUT.md`
step 3 names this file so the drafts are collected.

**1.21.0 (2026-09-23)** is that release. It carries Phase 1 and
Phase 2, and its entry uses Phase 1's draft. Phase 2's draft waited: the
trim shipped switched off until its canary passed, and a release note
describing a change nobody receives would be false.

Phase 3 (PR #189) is not part of 1.21.0's notes, and it merged on
2026-09-23 before 1.21.0 was tagged, so `master` carries it. No release is
planned for now (owner, 2026-09-23). Whichever release next ships from
`master` — 1.21.0 tagged at a later commit, or a later version — must carry
Phase 3's release-note draft. The citation repair (PR #192) is part of
Phase 3's behaviour before any release, so it needs no note of its own.
The draft describes routine condensing as on by default (D5, PR #196). A
release cut from a commit that has Phase 3 but not PR #196 would ship
routine condensing off, and its note would need to say it condenses only
when a message would not otherwise fit.

The canary passed on its second run the same day, and PR #194 turned the
trim on by default on `master`. So that same release must carry Phase 2's
draft too. A 1.21.0 tagged at the closeout commit (`a273ab7`) still ships
the trim off, and its notes stay as they are.
