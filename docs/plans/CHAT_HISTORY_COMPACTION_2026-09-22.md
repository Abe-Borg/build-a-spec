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
`tools/chat_history_profile.py` measures real `.baspec` files — run it on a
few of yours to replace these numbers with real ones.

Two more growth sources, both kept forever today:

- **Fetched web pages.** Each chat `web_fetch` can leave up to
  `WEB_FETCH_MAX_CONTENT_TOKENS` (50k) tokens of page text in history, four
  fetches per round (`CHAT_MAX_FETCHES`). Only fetched PDFs are elided at
  commit (`elide_all_pdf_sources`).
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
| plan | this file | **in review** | `72a3b2f` (PR #182) | |
| 1 | Stale outlines out of saved history + history composition | **in review** | `43a8ad8` (PR #182) | commit-time + load-time elision; Developer tools row; offline profiler |
| 2 | Fetched web-page text out of saved history | not started | | gated: owner decision D2 + one live canary request |
| 3 | Condensed conversation (Layer 2) + `recall_conversation` (Layer 3) | not started | | gated: D1, D3; ship both halves together |
| 4 | Promote before prune | **handed off** | | this is project-workspace Phase 4 (`project-workspace/04_HARVEST.md`); don't build it twice |
| 5 | Within-turn outline trim (optional) | not started | | changes what the model sees mid-turn; measure first |

A status moves to **complete** only after the PR merges, set by the next
session that touches this file (the project-workspace convention).

## Decisions (owner)

| # | Question | Recommendation | Status |
|---|---|---|---|
| D1 | Trigger size for condensing, and how many turns to keep word for word | 150k tokens of committed conversation (the API's own default threshold); keep the last 3 user turns | open — needed before Phase 3 |
| D2 | Drop fetched web-page text when a turn is saved, like PDFs? | Yes: the reply keeps its cited passages; the model can re-fetch | open — needed before Phase 2 |
| D3 | Our own summarizer, or Anthropic's on-demand compaction beta? | Our own (reasons under Phase 3) | open — needed before Phase 3 |
| D4 | Should flagged decisions become Project-facts suggestions? | Yes, via the harvest (Phase 4 hand-off) | open |

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

**Release-note draft** (for whichever release carries this; see "Release
policy" below):

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
plus a citation into it, sent once. Follow the `tools/qc_verifier_canary.py`
pattern (no request without `--run`) and record the result here; CLAUDE.md
names that canary as the sole paid exception, so adding a second one means
updating that ground rule in the same change.

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
