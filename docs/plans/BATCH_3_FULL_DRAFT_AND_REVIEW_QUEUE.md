# Batch 3 — "Draft the full section now" + the review queue

Ships as **v0.8.0**. Two work items that together complete the core
workflow symmetry: whether a project starts from scratch or from an
imported master, the user ends up with a full draft and then walks it
block-by-block to reviewed status. Depends on Batch 2 (manual-edit
endpoints, `set_status` op, status strip / streaming machinery).

Design principle (frozen): **from-scratch drafting is a first-class
feature, not the fallback.** Import is one on-ramp; the interview plus
the full-draft pass is the other. Both converge on the same review
surface.

---

## Work item 1 — Full-section draft pass

### What it is

One button that has Sonnet lay down a complete, properly-stamped draft
of the whole section in a single turn — the payoff moment for the
v0.6.0 no-limits work (128k output, full-document context, thinking).
After it runs, the interview pivots from "build from zero" to "refine
what's on the page", exactly like gap-and-adapt does after an import.

### Design (decided)

**No new backend machinery for the drafting itself.** The pass is an
ordinary `stream_user_turn` carrying a canned directive as the user
message — it rides the existing SSE stream, tool loop, undo (one
snapshot), rollback, and (from Batch 2) the status strip while it runs.
Rejected alternative: a dedicated `/api/draft/full` endpoint
duplicating the chat pipeline — more code, fewer invariants, no upside.

1. **Directive text lives in the backend**, not the frontend:
   `backend/llm/prompts.py` gains `FULL_DRAFT_DIRECTIVE` — a canned user
   message along these lines (tune wording during implementation, keep
   the obligations):
   - Draft the COMPLETE section now: every PART, every article the
     section conventionally carries per the module catalog/playbook and
     the project's known facts.
   - Use everything already established: interview answers, the project
     profile, grounded research items (pass `source_item_id` where a
     provision derives from one), the standards editions in effect.
   - Stamp provenance honestly per the existing discipline: `confirmed`
     only for user-stated facts; playbook defaults `assumed`;
     unresolvable values as `[TBD: …]` / `needs_input`. Over-flagging
     beats silent guessing — the review queue (work item 2) is the
     consumer.
   - Batch edits sensibly (an article or a few related articles per
     `apply_spec_edits` call) so `doc_patch` events flow continuously
     rather than one mega-batch at the end — this is a UX requirement:
     the user should watch the document assemble live.
   - End with a short summary in chat plus the 2–3 highest-value
     follow-up questions.
2. **Endpoint**: `POST /api/draft/full` in `app.py` — thin: 409 if
   `turn_active` or research is `running`; otherwise returns the
   directive text `{ok: true, message: FULL_DRAFT_DIRECTIVE}` and the
   FRONTEND sends it through the normal `send()` path (so the message
   appears in chat as a visible, honest "Draft the complete section…"
   user turn, streams normally, and lands in history/undo like any
   turn). Rationale for the fetch-then-send shape: keeps exactly one
   code path for turns; the directive stays server-owned and versioned.
3. **Frontend**: `ArtifactPanel.tsx` gets a primary-styled "Draft full
   section" button, enabled when the doc is empty-or-sparse (heuristic:
   fewer than N=3 articles) AND not busy; tooltip explains what it will
   do; after research completes and the doc is sparse, the button gets a
   subtle attention pulse (one-time). Confirm dialog is NOT needed — the
   whole thing is one undo step; say so in the tooltip ("One click to
   undo").
4. **Prompt-policy note** in the stable prompt (`prompts.py`): a short
   paragraph telling the model that when a full-draft directive arrives
   it should draft breadth-first (structure first, then flesh) and keep
   individual tool calls under ~25 ops so patches stream steadily.
   (The 25 figure is a pacing hint in prose, not an enforced cap — the
   no-limits rule stands.)

### Tests

- Directive endpoint: 409s (busy, research running), payload carries the
  directive.
- A scripted multi-round fake turn driven by the directive produces
  multiple `doc_patch` events and one committed version; provenance
  statuses from the script survive.
- Prompt snapshot: `FULL_DRAFT_DIRECTIVE` includes the provenance and
  batching obligations (string-contains assertions, same style as
  existing prompt tests).

---

## Work item 2 — The review queue

### What it is

A guided walk over every block that needs a human decision — `imported`
blocks after a master import, `assumed` blocks after drafting — one
element at a time, with keyboard-speed accept / edit / delete /
ask-the-model actions. This turns the export's "assumptions schedule /
imported provisions" from a paper trail into an in-app workflow, and is
the second consumer (after Batch 2's ✓ button) of the manual-edit
endpoints.

### Design (decided)

**Queue derivation is frontend-computed** from the doc snapshot the app
already holds (the full tree is in state; no new backend query needed).
Backend additions are limited to one convenience: nothing. Batch 2's
`POST /api/doc/edit` (`replace`, `set_status`, `delete`) is the entire
mutation surface. "Ask the model to adapt" goes through the normal chat
channel with a targeted message.

1. **Queue model** (`frontend/src/lib/reviewQueue.ts`):
   `buildQueue(doc, mode)` walks parts→articles→paragraphs in document
   order and returns entries `{elementId, ref, articleTitle, text,
   status, sourceItemId}` filtered by mode: `imported` mode → status
   `imported`; `assumptions` mode → status `assumed`; `all` → both
   (imported first, then assumed, each in document order). Pure
   function, unit-testable with a serialized doc fixture (add a tiny
   vitest setup for `lib/` pure functions ONLY if trivial; otherwise
   mirror the fixture as a Python test against `iter_paragraphs`
   ordering — the ordering contract already exists there. Prefer the
   Python-side ordering test + treat the TS walk as a straight port).
2. **`ReviewDrawer.tsx`** (sibling of ResearchDrawer, opens from an
   ArtifactPanel button that shows the outstanding count, e.g.
   "Review 87"): shows current entry — ref + article context + full
   text + status badge + ◆source chip (click shows the research item,
   reusing the existing chip machinery) — progress ("12 of 87"), and
   actions:
   - **Keep / Confirm** (`k` or `Enter`): `set_status: confirmed`.
   - **Edit** (`e`): inline textarea → `replace` with
     `status: confirmed` (echo `source_item_id`).
   - **Delete** (`d`): `delete` (single-keystroke, because undo exists;
     flash the removal).
   - **Ask model** (`a`): prefills the composer with
     `Regarding [ref] "<first 80 chars…>": ` and focuses it — the user
     states what to change; the model edits via the normal loop. The
     drawer stays open; when the turn completes, the queue recomputes
     (the block may now be `assumed`/`confirmed` or gone).
   - **Skip** (`s` / `→`): next without changes; skipped items remain
     in the outstanding count.
   - Each mutation advances to the next entry automatically; the drawer
     recomputes the queue from every fresh doc payload (single source
     of truth: the doc, not drawer-local state — survives undo,
     model edits, resets).
3. **Batch affordance, guarded:** an "Article: confirm remaining N"
   button per article group — but per the gap-and-adapt policy (stable
   prompt: "Do not mass-upgrade statuses without actually reviewing
   content"), it requires a press-and-hold (800ms) and sends one
   `/api/doc/edit` call with N `set_status` ops (one undo step).
   No document-wide bulk confirm. Do not add one.
4. **Completion state:** queue empty → drawer shows a clean "Nothing
   left to review ✓" panel with counts of what was confirmed / edited /
   deleted this session (drawer-local tallies, cosmetic only).
5. **While busy** (model turn streaming): drawer stays open read-only;
   action buttons disabled (mirrors the Batch 2 guard).
6. Export interplay: no changes needed — confirmed blocks already leave
   the assumptions/imported schedules; the queue empties exactly as the
   schedules do.

### Tests

- Python: ordering contract test on `iter_paragraphs` covering the queue
  order (imported-then-assumed is a frontend concern; the
  document-order guarantee is the backend contract).
- Batch `set_status` (N ops in one call) transactionality + single undo
  step (extends Batch 2 tests).
- Manual QA checklist: keyboard flow end-to-end on an imported master
  (use a real office master), ask-model round-trip recomputes the
  queue, hold-to-confirm article, busy lockout, undo after a delete.

---

## Docs & version

- README: v0.8.0 status section — lead with the workflow story (two
  on-ramps, one review surface); document the button, the drawer, the
  shortcuts.
- CLAUDE.md: implemented-notes section; note the frozen decisions
  (directive rides the normal chat path; queue derives from the doc;
  no document-wide bulk confirm).
- Version 0.8.0 in both files; suite green; build clean.

## Acceptance criteria

1. On a fresh project with profile + research done, one click produces a
   complete multi-article draft that streams into the panel article by
   article, is one undo step, and leaves honest statuses (spot-check:
   user-stated facts `confirmed`, defaults `assumed`, unknowns TBD).
2. After a master import, the review drawer walks every `imported`
   block in document order; a full review pass can be driven entirely
   from the keyboard.
3. "Ask model" on a queue entry results in a targeted edit and the queue
   reflects it without reopening the drawer.
4. The outstanding-review count in the panel matches the export's
   assumptions + imported schedules at all times.
5. Suite green, build clean, docs current, version gate at 0.8.0.
