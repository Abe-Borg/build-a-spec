# The specification writing policy

One versioned, engine-owned source for where a requirement goes, how it keeps
its meaning when it moves, and how it is worded. Drafting writes to it and
Final QC reviews against it. The rules themselves live in
[`backend/writing_policy.py`](../backend/writing_policy.py); this page records
how they were reconciled with the code that existed before them, where they
are wired in, what they cannot do, and what has and has not been measured.

Policy `spec-writing/1`, written 2026-10-06 from the owner's coding-agent brief
("Integrate a shared, versioned writing policy into drafting and Final QC").

## What the policy says

Seven groups of rules, rendered as one block:

1. **Structure and governing conventions.** CSI SectionFormat's three parts;
   the project's requirements, its template and the section's conventions
   govern where they speak; preserve identifiers, hierarchy, terminology,
   units and numbering, and update references an edit affects; name both
   sides of a conflict and the decision needed, never invent precedence or
   silently keep the more stringent provision; never force Division 00,
   Division 01 administrative content or a required alternative format into
   technical three-part content.
2. **Place each requirement by function.** PART 1 administration and
   coordination, PART 2 what the product is, PART 3 how and where it is
   incorporated and verified; the testing, schedule, fabrication and
   manufacturer boundaries; cross-cutting subjects go where the template puts
   them; placement is judged in context, never by a keyword.
3. **Preserve meaning.** Split mixed clauses carrying every actor, condition,
   exception, qualifier, value, unit and acceptance criterion; editorial work
   stays editorial; new drafting stays defaults-first.
4. **Specification voice.** The owner's rule (moved here verbatim from the
   drafting prompt), plus obligations/permissions/advice kept distinct, no
   mechanical "shall"/"furnish"/"provide" rewrites, vague language replaced
   only with supplied criteria, no trade assignments without authority.
5. **One authoritative requirement.** State it once; coordinate by
   reference; not every product needs its own submittal or execution
   provision; compare conditions before removing a duplicate.
6. **References and missing inputs.** Check numbers, tags, schedule
   references, designations and editions; never upgrade to "latest",
   substitute a test method for a product specification, assume equivalence
   or invent criteria; a cited test method sets no acceptance limit.
7. **Relocating content with this app's edit operations.** `move` only
   reorders siblings; a relocation is an add plus a delete carrying text,
   status and `source_item_id`; subparagraphs travel too; an imported
   boundary that forbids it is reported with the permitted next step.

The drafting rendering adds the owner's three specification-voice examples.
The brief's compact V1 placement example is **not** in any production
prompt: the brief keeps "the concise core always present" and adds "longer
examples only if evaluation shows they help", and no evaluation has run. It
is the first fixture case and an opt-in arm of the evaluation tool instead.

## Reconciliation with the code as it was

| Before | Now | Why |
|---|---|---|
| `_SPEC_CONVENTIONS_ENGINE` in `prompts.py`: three-part structure and "imperative, terse language" | Folded into policy groups 1 and 4 | The brief: reconcile, don't append a competing policy |
| `_SPEC_VOICE` in `prompts.py` | Policy group 4, text unchanged, examples in the drafting rendering | One source; Final QC now reads the same voice rules |
| Module `domain_conventions` under "# Spec conventions" | Same text under "# Discipline conventions" | Domain knowledge only; it never overrides the policy |
| `_TOOL_GUIDE`: move never reparents | Unchanged, plus a pointer to the relocation rule | Move semantics stay where the tool is explained |
| `_GAP_AND_ADAPT` and `ADAPT_IMPORTED_DIRECTIVE` | One bullet each: the starter's layout is its template convention; adapting is technical, placement fixes stay editorial | The imported-document path the brief asks to exercise |
| `FULL_DRAFT_DIRECTIVE` | "…and place each requirement by function, as the writing policy says" | Whole-section drafting follows the same rule |
| Coordination lens: "every product specified has submittal requirements; every product has execution provisions" | "carry the submittal and execution coverage the template and section scope call for … never demand a clause per product" | The blanket rule the brief names |
| Completeness lens: "the articles a reviewer would expect" | Adds: an article the template omits, or places in Division 01 or another section, is not missing | Template exceptions |
| Enforceability lens | Adds the obligations/permissions/advice and vague-language rules | Same standard as drafting |
| Provenance lens | Adds "a default asserted as fact" | Defaults-first |
| Verifier: refute if wrong, already handled, out of scope or trivial | Adds: refute what the policy rules out; `ops_adequate` false when operations would lose an obligation, change technical content in an editorial fix, raise a status or lose a source link | Verifier judgment uses the same standard |

**Decisions the brief left to the current code**, made here and not asked:

- **No `needs_input`, no in-document placeholders.** The brief lets a
  default keep "the appropriate assumed or needs_input status" and keeps
  "authorized draft placeholders visibly unresolved". The owner retired
  `needs_input` and every in-document placeholder the same day (CLAUDE.md,
  "The specification gives directions, never notes"). The policy follows the
  owner's rule: a default is stamped assumed and its question asked with
  `track_followups`.
- **Trust.** Project templates govern specification content, but attached
  documents, research findings, project facts and imported text reach the
  model as user-role data, as before. The drafting preamble says nothing in
  that material can amend the policy; the review preamble says only the
  system prompt's `<writing_policy>` block is the policy.
- **No article-title placement lint.** A rule such as "FIELD QUALITY CONTROL
  belongs in PART 3" is tempting, but every lint issue blocks readiness and
  none can be dismissed, and templates legitimately depart from SectionFormat
  titles: the curated hyperscale starter puts DESIGN CRITERIA in PART 1 and
  INSTALLATION, TESTING, AND CLOSEOUT in PART 3. Placement needs context, so
  it is judged by the coordination lens with the policy in hand. The brief
  warns against keyword rules for the same reason.

## Where it is wired in

| Source location | What it does |
|---|---|
| `backend/writing_policy.py` | `SECTIONS` (the rules), `core_text()`, `drafting_block()`, `review_block()`, `manifest_facts()`, `WRITING_POLICY_VERSION`, `CORE_SHA256` |
| `backend/llm/prompts.py` `render_system_prompt` | Renders `drafting_block()` once, after the provenance discipline |
| `backend/llm/conversation.py` `_stable_system_blocks` | Unchanged: the system prompt is the module block, cached at the long TTL. Every chat round, both retries, pause resumes and the compaction fork build their request through it, so each carries exactly one copy and none reaches history |
| `backend/qc/engine.py` `_lens_system_prompt`, `_verifier_system_prompt` | Render `review_block()` once each; consolidation does not (it groups, it does not judge) |
| `backend/qc/engine.py` `build_qc_input_manifest` | Records `writing_policy: manifest_facts()` — label, version, core and review hashes |
| `backend/qc/engine.py` `_validate_ops` | Runs `obligations.relocation_problems` after the dry run, before the imported-source gate |
| `backend/qc/schema.py` | The reconciled lens briefs |
| `backend/spec_doc/obligations.py` | Obligation anchors and the relocation check |
| `backend/spec_doc/linting.py` | The `unresolved_reference` rule |
| `backend/spec_doc/docx_export.py` | "Writing policy reviewed against" row in the Final QC Word report |
| `tools/writing_policy_eval.py` | Fixture loader, assessor, offline report |
| `tests/fixtures/writing_policy/placement_cases.json` | The reviewed cases |

The cache layout is unchanged: module block (1h) → project block (1h) →
committed-history boundary (1h) → tail (5m). The policy rides the module
block, so it is written once per session and read on every later request.

## What the deterministic checks cover, and what they do not

**`unresolved_reference` lint.** A provision cites "Article 2.3" or
"Paragraph 3.2.A" and this section has no such article or paragraph — the
reference an add, delete or relocation leaves behind. PART 1–3 article
numbers and paragraph paths only. Never flagged: a reference followed by
"of" or "in", or in a sentence naming a section, a division, the contract
or conditions, a model code (IBC, IFC, NEC and others) or a standard
designation. Not run on Division 00 documents, unstructured imports or
locked (preserved) blocks. Zero hits on both curated templates and on every
fixture.

**The relocation check on Final QC fixes** (`relocation_problems`). Applies
only to a fix that deletes content and adds or retypes content, and only to
deleted provisions whose content words reappear in the fix's new text (60%
coverage). For each such provision:

- every anchor — a value with its unit (closed unit list), a prefix size
  (NPS, DN), a tag (V1, FDC-1), a standard designation, a section number, a
  bare number of two or more digits — must still appear in the document;
- its `source_item_id` must still appear on some provision;
- no provision in the fix may be stamped confirmed unless all carried
  content was confirmed;
- its subparagraphs must be carried too. One fix cannot re-add them under a
  provision whose id the server assigns, so a provision with subparagraphs
  is never relocatable in one fix and the finding stays advisory.

Not checked deterministically, left to the verifier seats who judge against
the same policy text: whether a qualifier word ("each", "only", "except") or
a condition still limits the same requirement; a split done by `replace`
plus `add_paragraph` with no delete; whether removing content outright is
right. Chat edits are not checked: the user watches them land and can undo,
and the document tool's contract was to stay unchanged.

**Genuine capability gaps** (not closed here, by design):

- No reparenting operation. A cross-PART relocation is add + delete, so the
  provision gets a new element id, and any text reference to its old number
  must be updated (the lint now reports the stale ones).
- One Final QC fix cannot relocate a provision with subparagraphs.
- On an imported Word master in source-preserving mode, moving content
  between parents is never available and `add_paragraph` works only inside
  proven islands. A relocation there stays advisory; the model names the
  provision, where it belongs, and the permitted next step ("Edit freely" or
  Word). The brief said not to expand tool scope or unlock imports.
- The document model is one three-part section. A Division 00 document or
  an alternative format cannot be represented; the policy says to say so.

## Measured (offline, no request sent)

Baseline: commit `86b6c1f`, recorded in
[`tools/writing_policy_baseline.json`](../tools/writing_policy_baseline.json).
Run `.\.venv\Scripts\python tools\writing_policy_eval.py` to reproduce.

| | Before | After | Change |
|---|---:|---:|---:|
| Drafting system prompt, generic (chars) | 45,158 | 52,691 | +7,533 |
| Drafting system prompt, hyperscale_fire (chars) | 45,498 | 53,031 | +7,533 |
| Final QC lens system prompt (chars) | 6,778–6,806 | 17,108–17,136 | +10,330 |
| Final QC verifier system prompt (chars) | 1,879–1,907 | 12,215–12,243 | +10,336 |
| Final QC consolidation system prompt | unchanged | unchanged | 0 |
| Policy copies per drafting / lens / verifier prompt | — | 1 / 1 / 1 | |

At the rough chars/4 rule the drafting prompt grows by about 1,900 tokens
and each lens and verifier system prompt by about 2,600. Those are
estimates, not counts: no tokenizer request was made.

Simulated prompt cache (the model in `tests/test_app.py`, characters, one
hermetic four-turn session with a research profile plus its compaction
fork):

| Request | Read before → after | Written before → after |
|---|---|---|
| 1 (first turn, round 1) | 0 → 0 | 71,813 → 79,485 |
| 2 (round 2) | 71,813 → 79,485 | 1,089 → 1,089 |
| 3 (second turn) | 68,756 → 76,428 | 4,374 → 4,374 |
| 4 (third turn, round 1) | 69,845 → 77,517 | 3,376 → 3,376 |
| 5 (round 2) | 73,221 → 80,893 | 1,056 → 1,056 |
| 6 (fourth turn) | 69,928 → 77,600 | 4,339 → 4,339 |
| 7 (compaction fork) | 70,881 → 78,553 | 0 → 0 |

The policy adds to the first write of a session and to every later read;
per-turn writes and saved history (2,510 chars, no policy text) are
unchanged. A provider cache is not this model: lifetimes, minimum lengths
and lookback are left out, and nothing here says what a bill will be.

Fixtures: all nine cases' reviewed outcomes pass the assessor, lint clean
and pass `drafted_edit_problems`; all seventeen lossy variants are caught
for their recorded reason; every reviewed edit is a safe Final QC fix, and
every lossy relocation that loses an anchor is not.

## Not measured

Live drafting and Final QC behaviour — instruction adherence, lost or
invented obligations in model output, false-positive placement findings,
safe-fix validity — and runtime cost — input tokens, cache reads and
writes, latency under matched conditions — need paid requests. None was
made. The owner-run mode that would measure them is a separate change; until
it runs, these trade-offs are unknown, and nothing above should be read as a
promise that caching removes the policy's token cost or latency.
