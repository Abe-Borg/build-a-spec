# Build-a-Spec — Phase 2 Kickoff

*Starter prompt for a fresh Claude (Cowork) session. Connect both folders —
`C:\Github-Repos\build-a-spec` and `C:\Github-Repos\Claude-Spec-Critic` —
then paste this file's contents (or say "read PHASE_2_KICKOFF.md and go").
Safe to delete from the repo once Phase 2 ships.*

---

## Context

You are continuing an in-progress project. **Build-a-Spec** is a desktop app
for conversationally authoring CSI SectionFormat construction spec sections:
Claude interviews the user, drafts incrementally, and builds the section in a
live document panel beside the chat (artifacts-style). First domain:
**Division 21 fire suppression for hyperscale data centers (USA)**, starting
with 21 13 13 Wet-Pipe Sprinkler Systems.

**Phase 1 is complete, verified, and on disk** at
`C:\Github-Repos\build-a-spec`: FastAPI backend + React/Tailwind frontend in
a pywebview window, streaming SSE chat against `claude-sonnet-5`, API key
management (env → keyring → file), prompt-cached system prompt, retry-safe
history, 5 hermetic tests passing, frontend building clean (`tsc` + Vite).
The document panel is currently a placeholder; drafted spec text arrives in
chat as fenced blocks. **Phase 2 replaces exactly that.**

The sibling repo `C:\Github-Repos\Claude-Spec-Critic` (spec *review* app,
same author) is the source for code ports — its domain-neutral machinery
(research fan-out, pinned standards editions, project profile, tracing,
packaging) gets copied in during phases 3–5.

**Read before coding, in this order:**

1. `build-a-spec/CLAUDE.md` — engineering reference: layout, conventions,
   frozen decisions, the Phase 2 build spec ("Phase 2 sketch"), interview
   policy, and Spec Critic port pointers. This is the source of truth.
2. `build-a-spec/README.md` — product-level roadmap (phases 1–5) and how
   the app runs.

## Mission: Phase 2 — the living document

Build, in roughly this order:

1. **Document model** (`backend/spec_doc/`): `SpecSection` tree — PART 1
   GENERAL / PART 2 PRODUCTS / PART 3 EXECUTION → articles → paragraphs
   (SectionFormat numbering: 1.1, A., 1., a.). Stable element ids
   (`pt1.a2.p3` style). Per-block provenance: `confirmed` / `assumed` /
   `needs_input`. `[TBD: …]` markers tracked as first-class open items.
2. **`apply_spec_edits` tool**: registered in the `_TOOLS` seam in
   `backend/llm/conversation.py`; ops
   `{action: add_article|add_paragraph|replace|delete, target_id, position?,
   text?, numbering?, status?}`. Validated server-side, applied
   transactionally, snapshotted per turn for undo. Grow `stream_user_turn`
   with tool-use dispatch + continuation (Spec Critic's streaming
   continuation pattern in `src/research/requirements_research.py` is the
   reference).
3. **SSE document events**: `doc_patch`, `doc_snapshot`, `open_questions`
   alongside the existing `text_delta` / `turn_complete` / `error`. Frontend
   dispatch lives in the single switch in `App.tsx#send`.
4. **Real ArtifactPanel**: renders the tree with SectionFormat typography on
   the paper surface; changed blocks highlighted after each turn; version
   stepper (undo/redo across snapshots); badges for `assumed` blocks and
   open `[TBD]` items.
5. **System prompt rewrite** (`backend/llm/prompts.py`): drop the
   "draft in fenced blocks" instruction; instruct tool-based drafting; bake
   in the interview policy below.
6. **`.docx` export** via `python-docx`: office SectionFormat styling plus
   an **assumptions schedule** listing every `assumed` block. Backend
   download endpoint + a button in the panel header. (Add `python-docx` to
   `requirements.txt`.)
7. **Save / resume**: JSON project file (conversation history + document
   tree + open items); load on demand.
8. **Tests**: hermetic throughout — fake tool-use streaming responses
   (extend the fake client in `tests/test_app.py`), document-model unit
   tests (id stability, transactional apply, undo), export smoke test.

## Decisions already made — do not relitigate

- Stack: pywebview + React + FastAPI. Reuse from Spec Critic is **copy and
  adapt**, never cross-repo imports; note provenance in module docstrings.
- First module: hyperscale Div 21 fire suppression. Research agents are
  **Phase 4** (immediately after this phase) — do not build them now.
- Models: `claude-sonnet-5` for conversational turns; Opus 4.8
  (`claude-opus-4-8`) optionally for heavy one-shot passes via the existing
  `stream_user_turn(model=...)` override.
- NFPA 13 default edition is **2025**; jurisdiction-adopted earlier editions
  override once known — never silently, always with the adoption basis
  stated.
- **Interview policy — defaults-first**: every question carries a
  recommended answer; "I don't know" is a first-class reply — apply the
  defensible NFPA 13-2025 / hyperscale-norm default and stamp the block
  `assumed`. Never stall the interview except on the truly non-defaultable
  minimum (section, location, client, hazard basics). Include "guide me"
  mode (open questions become 2–4 options with tradeoffs) and an
  "explain why you're asking" affordance.

## Working conventions (standing)

- Hermetic tests only — no network, no real key; monkeypatch
  `backend.llm.conversation.get_client`.
- Owner's machine is Windows; development happens in your cloud container.
  **Deliver every changed/new file back to `C:\Github-Repos\build-a-spec`
  via the device bridge** (SendUserFile → device_commit_files) — uncommitted
  files never reach the disk.
- Verify before delivering: `pytest -q` green, `npm run build` clean
  (`tsc --noEmit` + Vite), and send a screenshot of the updated UI
  (Playwright + the preinstalled Chromium works).
- Update `README.md`, `CLAUDE.md`, and `requirements.txt` whenever the
  implementation, conventions, or dependencies change.

## Definition of done

Interviewing a test project produces articles that land **in the panel, not
chat**, with change highlighting, correct SectionFormat numbering, provenance
statuses and tracked TBDs; undo steps back through versions; the section
exports to a properly styled `.docx` with an assumptions schedule; a project
file saves and resumes; all tests pass; docs updated; files committed to
disk; screenshot delivered.

## First moves

List both connected folders, read `CLAUDE.md` and `README.md`, skim the
Phase 1 code (`backend/llm/conversation.py`, `backend/app.py`,
`frontend/src/App.tsx`, `ArtifactPanel.tsx`), then present a brief
implementation plan — file-by-file, with anything you'd amend in the sketch
above — for confirmation **before writing code**.
