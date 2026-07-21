# Implementation plans — batches 2 through 5

Owner: Abraham. These plans were written 2026-07-21 against the v0.6.0
("Sonnet unleashed") codebase, to be executed by Claude Code agents — one
batch per working session, in order. Each plan is self-contained: goal,
current-state anchors into the code, design decisions (already made — do
not relitigate them without asking Abraham), work items with file-level
detail, test plan, and acceptance criteria.

| Batch | File | Ships as | Status |
|---|---|---|---|
| 2 | `BATCH_2_STREAMING_UX_AND_CONTROL.md` | v0.7.0 | planned |
| 3 | `BATCH_3_FULL_DRAFT_AND_REVIEW_QUEUE.md` | v0.8.0 | planned |
| 4 | `BATCH_4_FINAL_QC_ON_FABLE.md` | v0.9.0 | planned |
| 5 | `BATCH_5_REDLINE_EXPORT.md` | v1.0.0 | planned |

The reusable kickoff prompt for each Claude Code session lives in
`AGENT_PROMPT.md` — Abraham pastes it at the start of every batch; it
auto-detects which batch is next from this table and the plan files'
as-built annotations.

## Ground rules for every batch (read before coding)

1. **Read `CLAUDE.md` first.** It is the source of truth for conventions,
   invariants, and frozen decisions. The "Sonnet unleashed" section and
   the conversation-engine invariants are the parts most batches touch.
2. **Hermetic tests, always.** No network, no real API key.
   `tests/conftest.py` injects a placeholder key; anything touching the
   API monkeypatches `backend.llm.conversation.get_client` (or the
   relevant runner's client) with the fakes in `tests/fakes.py`. New
   streaming/event behavior means extending the fakes — never skipping
   the test.
3. **Definition of done, per batch:**
   - `python -m pytest -q` fully green (no skips added).
   - `cd frontend && npm run build` clean (`tsc --noEmit` runs inside it).
   - `README.md`, `CLAUDE.md` (event protocol table + invariants +
     implemented-notes section), and `requirements.txt` updated when the
     batch changes behavior, contracts, or dependencies.
   - Version bumped in BOTH `backend/settings.py` (`VERSION`) and
     `frontend/package.json` — `test_version_consistency_gate` enforces
     agreement. Use the version from the table above.
   - The batch's plan file annotated with an `## As built (<date>)`
     section at the top — status, what shipped, every deviation from the
     plan with one line of why (mirror `ROADMAP.md`'s as-built style) —
     and the Status column in the table above flipped to `shipped`.
4. **Model policy (frozen, 2026-07-21):** everything runs on
   `claude-sonnet-5`. There is NO user-facing model picker and none may
   be added. The single exception is Batch 4's Final QC, which runs on
   `claude-fable-5`. `stream_user_turn(model=...)` is the routing seam.
5. **No-limits posture (frozen, 2026-07-21):** the app imposes no
   quality limits on the model. `max_tokens` defaults sit at the model
   ceiling; the only permissible caps are runaway circuit breakers sized
   so no legitimate operation ever meets them. Do not introduce token
   budgets, truncation, or "cost-saving" degradations anywhere.
6. **Copy-based reuse** from Claude-Spec-Critic only (no cross-repo
   imports); note provenance in module docstrings. Windows is the
   primary platform. Never change the Inno Setup AppId.
7. **Commit style:** Abraham's standing preference — commit messages are
   sassy, spicy, and funny where warranted (especially when something
   fought back), never obnoxious. Commit normally from Claude Code; the
   repo lives at `C:\Github-Repos\build-a-spec`.
8. **Verify external facts before wiring them.** Where a plan marks
   `VERIFY:`, the API/pricing/spec detail was current as of 2026-07-21
   but must be re-checked against live docs before implementation.

## Cross-batch context

- Batch 1 (v0.6.0, shipped) restructured the request: stable-only cached
  system prompt; a PROJECT CONTEXT block (full document text + standards
  + research profile + lint + open items) in the newest user message;
  incremental history caching via a tail breakpoint; strip-at-commit;
  adaptive thinking with effort knobs; interview `web_search`/`web_fetch`
  with `pause_turn` handling; per-turn billed-usage aggregation into
  `turn_complete.usage` and traces. Batches 2–5 build on all of that.
- The `turn_complete.usage` payload is the raw material for Batch 2's
  cost meter. The manual-edit endpoints from Batch 2 are load-bearing
  for Batch 3's review queue and Batch 4's accept-fix flow. Batch 5's
  diff engine also powers an in-app version diff view.
- A UX mandate from Abraham applies across every batch but is
  implemented primarily in Batch 2, work item 1: the chat must feel
  buttery smooth, and the user must always see what the model is doing —
  no dead air, ever. Any batch that adds a long-running operation
  (Batch 3's full draft, Batch 4's QC) must reuse Batch 2's status/
  activity machinery rather than inventing a new spinner.
