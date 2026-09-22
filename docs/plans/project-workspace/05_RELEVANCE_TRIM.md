# Phase 5 — Later sections stay cheap: measure, then trim by relevance

**Status:** not started. **Depends on:** Part A on nothing; Part B on Part
A's measurement crossing the gate below. Do not build Part B on a modelled
number: the last cost model in this repo (PR #166's review) turned out to
be an artifact of a dropped term.

## Goal

The carried research block (`research_context_block`,
`RESEARCH_CONTEXT_MAX_TOKENS = 100_000` estimated) rides every turn's
PROJECT CONTEXT together with the facts block (6k cap) and the sections
block (3k cap). PROJECT CONTEXT sits in the newest user message, is
stripped at commit and rewritten every turn, so its cost is a cache WRITE
per turn, not a read. A fire-pump section carrying a four-dimension
hyperscale profile pays for governing-code items about sprinkler spacing
on every turn. Part A makes that cost visible; Part B, only if it earns
it, renders the block relevance-first with a disclosed hold-back.

## Current code (verified 2026-09-22)

- `research_context_block(profile, *, max_tokens)` returns
  `(text, dropped)`; the trim drops whole items lowest-confidence-first,
  later items first among ties. `_turn_context_text` splices it, then the
  facts block, then the sections block, then reference stubs.
- `capture.turn_round(handle, *, round_index, stop_reason, duration_ms,
  usage, tool_uses, web_searches, web_fetches)` writes one `round_end`
  event per streaming round; `capture.turn_prompts` writes `prompt_refs`
  (stable system + frozen PROJECT CONTEXT + user text, hash-deduped).
- `/api/diagnostics` (`diagnostics.snapshot`) has a `research` block from
  `incomplete_dimension_facts` and a `session` block; the Developer tools
  modal renders both.
- `SessionState.last_context_tokens` is the Anthropic-counted size of the
  last committed request — the context pill in the header.
- `read_reference_doc` is the precedent for an on-demand read tool whose
  result is elided from committed history
  (`_elide_reference_tool_results`).

## Part A — measure (build first, unconditionally)

- `_turn_context_text` returns, beside the text, a `context_sizes` dict:
  estimated tokens per block — `research`, `research_dropped_items`,
  `facts`, `sections`, `references`, `document`, `lint`, `open_items`,
  `qc_review`, `total` (the `len // 4` estimate the caps already use).
  Frozen at turn start with the text.
- `capture.turn_prompts` gains `context_sizes` on the `prompt_refs` event;
  `/api/diagnostics` gains `session.last_context_sizes`; the Developer tools
  modal's Session state card renders the row; the trace viewer needs no
  change (it renders event fields generically).
- `tests/test_runtime_date.py`'s neighbour `tests/test_context_sizes.py`
  (new): the sizes sum to the total, the research size tracks the rendered
  block, the dropped count is what `research_context_block` reported, the
  event carries them, the diagnostics snapshot carries them and never the
  text.

**The gate.** Abraham runs one real second-section session on a hyperscale
project (21 30 00 seeded from a researched 21 13 13), works it for a normal
sitting, and reads `last_context_sizes` from Developer tools or the trace.
Part B is built only if the research block routinely renders past **~40k
estimated tokens** on a section that plainly uses a fraction of it. Record
the numbers in "Deviations / measurements" below either way.

## Part B — relevance-first rendering (gated)

- `SectionFocus` (pure, `backend/research/relevance.py`): derived from the
  current section — number, title, article titles, the module catalog
  entry's `scope_note` — into a lower-cased term set (stop-words removed,
  digits kept: "NFPA 20", "pump", "controller", "churn").
- `rank_items(profile, focus) -> list[(score, item)]`, deterministic:
  dimension weight first (`site_environment` and `governing_codes` always
  lead — jurisdiction and site apply to every section; `client_standards`
  leads too — an owner standard applies to every section;
  `ahj_requirements` by term overlap), then term overlap between the focus
  set and the item's `category`, `requirement`, `topic`,
  `code_reference`; ties broken by confidence then by item order (the
  existing trim's tie rule). The score is recorded on nothing — it is a
  rendering decision.
- `research_context_block(profile, *, max_tokens, focus=None)`: with a
  focus, items render in rank order and the trim becomes
  relevance-then-confidence; a **hold-back line** replaces the silent trim:
  "N further findings across <dimensions> are held back for length; ask to
  see a category, or a finding by id" — the disclosed-trim posture every
  other block already takes. With `focus=None` (no section named yet) the
  rendering is BYTE-IDENTICAL to today (pinned).
- `read_research_items` chat tool (appended LAST in `_chat_tools`, the
  cache-prefix rule): `{item_ids?: [...], category?: str, dimension_id?:
  str}` → the full rendering of the matching items; the result is elided
  from committed history exactly like `read_reference_doc`, with the same
  "re-reading is expected" placeholder. `_RESEARCH_POLICY` gains one line
  naming it. The tool exists only so a held-back finding is one call away;
  it changes nothing when nothing is held back.
- The cap stays 100k. This phase does not lower it; it reorders what the
  cap cuts and discloses the cut.

## Files

- Part A: `backend/llm/conversation.py`, `backend/tracing/capture.py`,
  `backend/diagnostics.py`, `frontend/src/components/DeveloperToolsModal.tsx`,
  `tests/test_context_sizes.py`.
- Part B: `backend/research/relevance.py` (new), `backend/research/engine.py`
  (`research_context_block` focus), `backend/llm/conversation.py` (the
  focus from the doc; the tool dispatch + elision), `backend/llm/prompts.py`,
  `tests/test_research_relevance.py`.

## Tests (Part B)

- `test_no_focus_renders_byte_identically_to_today`.
- `test_ranking_is_deterministic_and_section_sensitive` (the same profile
  ranks differently for 21 30 00 and 21 13 13; site and governing codes
  lead both).
- `test_the_hold_back_is_disclosed_with_counts_and_dimensions`.
- `test_read_research_items_returns_held_back_findings_and_is_elided`
  (through `/api/chat` with the fakes; the result is not in committed
  history; the tool is last in `_chat_tools`).
- `test_the_stable_prompt_names_the_tool_and_carries_no_profile_data`.

## Docs

- `README.md`: the in-progress subsection — what Developer tools now shows
  (Part A); the relevance rendering and the tool (Part B).
- `CLAUDE.md`: implemented notes for each part; the measurement recorded
  as numbers in the Part B section; Layout entries.
- `docs/RELEASE_WINDOWS.md`: a QA row — the context sizes row in Developer
  tools after one turn.

## Release-note draft (Phase 7 copies from here)

- Part A: **See what each turn carries.** Developer tools now shows how
  much of a turn's context each block takes — the research profile, the
  facts, the document — so a section that feels expensive can be read
  rather than guessed at.
- Part B (only if built): **Later sections read the research that fits
  them first.** A section carrying a large research profile now sees the
  findings most relevant to it first and is told how many are held back,
  and the assistant can fetch any of them by id or category on request.

## Deviations / measurements

(Record the gate measurement here with the date and the section it was
taken on, then any as-built deviation.)
