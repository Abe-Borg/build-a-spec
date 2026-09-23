# Redline on your original — tracked changes in the Word file you imported

**Status:** Phase 0 built 2026-09-22 in PR #184 — announced in 1.21.0's
release entry (the project-workspace closeout; its draft is under
"Release-note drafts"). All seven decisions ratified 2026-09-22 (see
"Decisions"). **Phase 1 is built, in two PRs:** the backend (PR #187,
merged `0aa2e98`: the export, the self-check, the refusals, the route and
the payload flag — see "Phase 1 (backend PR) — as built") and the UI
(PR #190: the menu item, *Open redline in Word*, the capability, the copy
and the QA rows — see "Phase 1 (UI PR) — as built"). Neither has a release
entry. 1.21.0's, written while only the backend had merged, leaves the
redline out, since on its own the export is reachable only through the API.
Which release announces it, with the Phase 1 draft under "Release-note
drafts", is the owner's pick — and v1.21.0 was not yet tagged when PR #190
was written, so a v1.21.0 tag cut from `master` after PR #190 merges ships
the menu item too, and that entry (not frozen until tagged) would need the
draft. The new export's real-Word QA rows in `docs/RELEASE_WINDOWS.md` are
still to run.
**The two losses Phase 1 recorded in the formatted export are fixed** (a
follow-up PR, 2026-09-23): a provision holding a hyperlink is spliced like
any other instead of being rebuilt from its first run, and a new
sub-provision in a Word-numbered master takes its own numbering level — see
"Phase 1 follow-up (links and the nesting level) — as built". No release
entry either; its draft is under "Release-note drafts".
**Phase 2 is next and not started**; start from the two Phase 1 as-built
notes and the follow-up's.
**Builds on:** the v1.14.0 appearance-preserving export (`source_render.py`),
the Batch 5 diff engine and redline writer (`diffing.py`, `docx_export.py`),
and the retained upload + formatting map every import already keeps.

## The ask

> I love the redline export that compares the docx I imported with the final
> edited spec. Ideally it would be the original docx, with all the formatting
> intact, and the changes in that same docx, with tracked changes on. I
> understand it would be a copy of the uploaded docx with the tracked changes
> on, and then I would need to overwrite the original. — Abraham, 2026-09-22

## The promise this plan makes

A new export, **Redline on your original**, which is a copy of your uploaded
Word file:

- Every package part except the document body is **byte-for-byte your
  upload**: headers, footers, styles, fonts, theme, numbering definitions,
  page setup, comments, everything.
- In the body, every change Build-a-Spec made since the import is a **native
  Word tracked change** (author "Build-a-Spec", dated at export). Nothing
  else in the body is touched.
- **Reject All gives you back the original upload.**
- **Accept All gives you exactly what *Export Word (keeps your formatting)*
  produces.**

The intended workflow: open the file in Word, review and accept or reject,
save, and replace your master with it.

The app checks both halves of the promise itself before it hands over the
file. If either check fails, the export is refused with a reason rather than
delivering a redline that is wrong (see D-7).

"Equal" in the two bullets above means element-for-element equal, up to how
Word splits text into runs (Word re-splits runs on every save anyway). It
does not mean byte-for-byte equal. There is one invisible exception, set out
in D-6: when you move a provision, its bookmarks stay with its new position,
so Reject All does not restore them at the old one.

## Why today's redline can't do this

`GET /api/export/docx?redline=master` builds a brand-new python-docx document
from the semantic tree (`docx_export._render_redline_body`). That means app
styles (Times New Roman), positional literal labels, and the Build-a-Spec
schedules appended. It is a redline of the *extracted provisions*, not of
your file. The Export menu already says so in its tooltip: "this is not a
redline of the original DOCX package". `_capture_export_inputs` forces every
redline to `normalized`, and `redline` + `mode=source` is a 400.

## Why it's feasible now (verified in the tree, 2026-09-22)

Almost every piece already exists; what's missing is the join between them.

1. **The exact upload is kept.** It lives on `session.source_docx_bytes`, and
   `session.source_docx_filename` records its name.
2. **Every element knows where it came from.** `SourceFormatMap`
   (`spec_doc/source_format.py`) records each element's body-child index in
   the upload. It also records its label kind: `auto` (Word numbering),
   `manual` (a typed "A." the importer stripped), or `none`. The map is
   bound to the upload by SHA-256.
3. **The formatting-preserving renderer already rebuilds the body from those
   origins.** `source_render.render_preserving_docx` handles each element as
   follows:
   - an untouched provision is a byte-identical clone;
   - an edited provision is its original `w:pPr` with new words;
   - an added provision is cloned from its nearest kin;
   - a preserved block is emitted verbatim;
   - unmodelled content (a cover page, spacers, `END OF SECTION`) is carried
     through.

   The result goes through `raw_zip.replace_document_xml_raw`, which keeps
   every other ZIP record exact.
4. **Master vs current is already aligned.** `store.baseline_index` is the
   post-import version. `diff_sections(baseline, current)` joins the two
   trees by stable uid and places each deleted element where it sat.
5. **The revision XML shapes are known.** Batch 5's writer already emits
   `w:ins`/`w:del`/`w:delText` and paragraph-mark revisions.
   `raw_zip.replace_raw_zip_member` can swap any single part, so
   `word/settings.xml` can be replaced too, if we want Track Changes on
   (Decision 4).
6. **There is real-Word tooling.** `tools/render_docx_word.py` +
   `render_docx_word_automation.ps1` already drive a hidden Word process on
   Windows. `tests/docx_corpus.py` + `tests/fixtures/docx_corpus/external/`
   hold Word-saved masters.

## Findings: four bugs in today's formatted export (fix first)

Each was reproduced with a scratch probe against a small master; the probes
are not committed. They matter here because the redline's Accept All must
equal this export. Left alone, the redline would faithfully reproduce all
four, and #2 and #3 would show up as tracked section-break changes nobody
asked for. They are worth fixing even if the redline is never built.

1. **Relettered provisions lose their formatting.** Insert a provision at the
   top of an article in a typed-letter master ("A.⇥Section includes
   **vibration isolation** …"). Every sibling below is relettered and treated
   as edited, so `_write_paragraph_text` rebuilds it from the first run's
   properties.
   - Result: the tab after the letter becomes a space, and the bold phrase is
     lost.
   - The new provision also gets "A. " with a space instead of the master's
     tab.
   - This hits every user who inserts or deletes a provision in a
     typed-letter master.
2. **Deleting the provision under a section break drops the section break.**
   An empty paragraph carrying `w:pPr/w:sectPr` has no text and no drawing,
   so `_is_blank_paragraph` classes it as a spacer. Spacers die with the
   element below them (`trailing()` skips blanks). In the probe the document
   went from 2 Word sections to 1, which is how a landscape schedule page
   turns portrait.
3. **Adding a provision after a section-ending paragraph duplicates the
   section break.** In a Word-saved file the break usually sits in the `w:pPr`
   of the last *text* paragraph of the section. The new provision is cloned
   from that paragraph, `w:pPr` and all. In the probe: 1 section break → 2.
4. **A "(Not used.)" line goes stale.** Add an article to a PART that read
   "(Not used.)": the export prints `2.1 ISOLATORS` and then the stale
   "(Not used.)". The importer skips that line, and the renderer carries it
   as leading content of the next PART heading.

Two smaller issues, found by reading the code rather than by probe:

- **Unmodelled non-blank content can move to the end of the file.** When such
  content sits above a deleted provision (for example a body-level bookmark),
  `trailing()` re-emits it at the end of the body. Rare.
- **The diff engine numbers provisions differently from the panel.**
  `diffing.py` computes letters as `_paragraph_label(depth, cur_index)` over
  *all* siblings. The panel and the formatted export use
  `model.labelled_paragraphs`, which skips preserved tables. So after a
  preserved table, the compare view and today's redline show "C." where the
  panel shows "B.". The new renderer must use `labelled_paragraphs`, and the
  diff should be fixed too.

## Design

### D-1. One emission plan, two renderings

Split `source_render.py` into two layers:

- **Plan.** Walk the master → current alignment and produce an ordered list
  of records, one per body child to emit:

  | Record | Meaning |
  |---|---|
  | `kept` | verbatim clone |
  | `spliced` | source element plus a word-level change list |
  | `inserted` | template clone plus new text |
  | `deleted` | source element the user removed |
  | `carried` | unmodelled content, verbatim |
  | `dropped` | an unmodelled spacer or stale placeholder the clean export omits |

- **Render.** The same plan feeds two renderers:

  | Record | Clean render (today's export, `render_clean`) | Redline render (new, `render_redline`) |
  |---|---|---|
  | `kept`, `carried` | clone | clone, untracked |
  | `spliced` | the accept view | `w:ins`/`w:del` *inside the original runs* (D-2) |
  | `inserted` | new paragraph | inserted runs + inserted paragraph mark |
  | `deleted`, `dropped` | nothing | a tracked deletion of the original element |

Accept All == clean then holds by construction, and D-7 proves it on every
export.

**How the plan decides each element:** the diff decides *order*, and the
source element decides *text*.

- `diff_sections(baseline, current)` supplies the merged document order and
  where each deleted element sat, tree-aware.
- **The diff cannot see a reorder, so the plan detects moves itself.**
  `_merge_by_uid` emits every surviving element as `both`, in current order.
  Taken as it is, an unchanged but reordered provision would come out `kept`
  at its new position, untracked. Reject All would then keep the new order,
  and D-7 would refuse every reordered document. (Codex, PR #181.)
  - **The rule.** Within each sibling list, the survivors whose relative
    order held are the longest increasing subsequence of their base indices,
    taken in current order. Every other survivor is `moved`.
  - **Rendering a move.** A moved element's whole subtree is `deleted` at
    its base position and `inserted` at its current position.
  - **Where the old copy goes.** It is spliced at its base position the way
    `_merge_by_uid` already places deleted nodes: after the nearest
    preceding stable survivor.
  - **What the new copy is built from.** The inserted copy is cloned from
    the element's *own* origin, not from kin. Its runs are wrapped in
    `w:ins`, and its text is the accept view: the D-2 splice's result when
    the text also changed. That makes Accept All yield exactly what the
    clean export places there.
  - **Where it lives.** An opt-in on `diff_sections`, so the compare view
    and the normalized redline are unchanged when the flag is off.
- Each element's content is judged relative to the upload, through the
  format map:
  - A current element with an origin is `kept` when its rendered text
    (letter + text, per label kind) matches the source. Otherwise it is
    `spliced`. This is why a relettered provision is spliced even though the
    diff calls it `unchanged`.
  - A current element with no origin is `inserted`, whatever the diff says.
    This matters for the synthetic `IMPORTED CONTENT` heading: it sits in
    the baseline, but was never in your file, so relative to the upload it
    is an insertion.
  - An anchored origin that the current tree no longer contains is
    `deleted`.

### D-2. Word-level changes inside the original runs (the core)

Today an edited provision is rebuilt from its first run's properties. The
new approach splices the change into the runs that are already there.

**Steps:**

1. **Map characters to runs.** Map the source paragraph's visible characters
   to the runs and nodes that produce them: `w:t` chars, `w:tab` "\t",
   `w:br` "\n", `w:cr`, `w:noBreakHyphen` "-", `w:ptab`. These are exactly
   what the importer read through python-docx `CT_R.text`, so the mapping
   and the imported text cannot disagree.
2. **Align words.** Align the source's words with the master text. The
   importer only folded whitespace and stripped the typed letter, so the
   word sequences must match. Verify this per paragraph; on a mismatch, use
   the fallback below.
3. **Diff words.** Word-diff master against current, with the same
   `SequenceMatcher` approach as `diffing.token_runs`.
4. **Emit:**
   - **Unchanged words** are the *original runs*, split at word boundaries
     where needed with their `w:rPr` copied. This is what keeps a bold phrase
     bold.
   - **Deleted words** are the original runs wrapped in `w:del`
     (`w:t` → `w:delText`, keeping `xml:space="preserve"`).
   - **New words** are a new run carrying the formatting of the nearest
     surviving character, wrapped in `w:ins`.
   - **Whitespace:** source whitespace survives next to surviving words, so
     the tab after a letter stays a tab.
5. **Letters.** A typed letter is its own token, so relettering "A." → "B."
   shows as a one-token change and the tab after it is kept. See D-4.

**Eligibility.** Start conservative and widen only with corpus evidence.
The splice applies to plain paragraphs whose children are only:

- `w:pPr`, runs, bookmarks, `w:proofErr`;
- and whose runs contain only `w:rPr` and the text nodes above.

Anything else takes the **fallback**: fields, inline content controls,
hyperlinks, footnote or comment references, `w:sym`, pending revisions, and
drawings.

**The fallback** marks the whole original content deleted and inserts the
new text in the same paragraph. It is still exact under Accept All and
Reject All, just coarser. Diagnostics record the fallback rate so we can
see what to widen next.

This same splice is what the clean export uses (Phase 0), which fixes
finding #1.

### D-3. Deletions, insertions, moves, and everything else

- **Deleted provision.** Emit the source paragraph with every run wrapped in
  `w:del` (`w:t` → `w:delText`, `w:instrText` → `w:delInstrText`), plus a
  deleted paragraph mark (`w:pPr/w:rPr/w:del`). Accept removes it; Reject
  restores it exactly. Its leading blank spacers are deleted with it, just
  as the clean export drops them.
- **A deleted provision that carries a section break in its own `w:pPr`
  keeps its paragraph mark.** (Codex, PR #181.)
  - **What is marked.** Only its runs are marked deleted. Accept All then
    leaves an empty paragraph holding the break, which is exactly what the
    Phase 0 rule makes the clean export produce. Reject All restores the
    text.
  - **The old copy of a moved provision** that carries a break gets the
    same treatment. The break stays at the old position (a break belongs to
    the content above it), and the moved copy never carries one (clone
    hygiene, D-6).
  - **The general rule.** No tracked change ever deletes a paragraph mark
    that holds `w:sectPr`. Deleting one would merge two Word sections on
    Accept All. The renderer asserts it, and D-7 would catch a violation.
- **Deleted preserved block.**
  - A table: every row gets `w:trPr/w:del` and every cell's text is marked
    deleted.
  - A picture paragraph: deleted like any paragraph.
  - Blocks Word can't represent as a tracked deletion (a block-level content
    control, a TOC field block): the D-7 check refuses the export with a
    named reason. We never guess.
- **Inserted provision.** Cloned from kin as today, minus the Phase 0 clone
  hygiene; runs wrapped in `w:ins`; paragraph mark inserted. In a
  Word-numbered master, Word renumbers itself under both Accept and Reject.
- **Moves.** Batch 5's "moves are not marked" rule cannot hold here: Reject
  All has to restore your order.
  - Phase 1 detects them (D-1, per-sibling longest increasing subsequence)
    and renders each as deleted-here plus inserted-there.
  - Phase 2 changes only the rendering: it upgrades *pure* moves to native
    Word move tracking (`w:moveFrom`/`w:moveTo` with paired range markers;
    Word shows them green with "Moved" balloons).
  - Moving a provision to a different parent is already a delete plus an add
    in the model (`move` refuses a new parent), so the diff already sees it
    as delete plus insert.
- **Section header.**
  - When the identity is a header *line* in the body (`header_source` =
    `line`), a renumber or retitle is a word-level change on that line.
  - When it lives on the cover page or in the page header/footer, it is not
    redlined, because those are never rewritten. The existing
    `stale_document_identifier` lint already warns about them.
- **Left untouched:**
  - Front matter, headers and footers, and `END OF SECTION` plus anything
    after it: untouched and untracked.
  - Status changes (assumed → confirmed) are not content changes, so they
    are not marked (same as today).
  - No Build-a-Spec schedules and no QC closing are added. The file is your
    document.
- **Section breaks follow the Phase 0 rules.** A break belongs to the
  content above it. No edit loses one, duplicates one, or carries one
  somewhere new unless the user deleted the content on both sides of it.

### D-4. Typed letters vs Word numbering

- **Word-numbered masters (`w:numPr`):** there is no letter text to mark,
  and Word renumbers itself. These produce the cleanest redlines.
- **Typed-letter masters ("A."):** a relettered provision carries a tracked
  letter change. That is the only way Reject All can give back your letters.
  It gets noisy when you insert near the top of a long article, but it is
  honest.
- **Scope of the departure:** this export departs from Batch 5's "labels are
  positional literals, never tracked" rule by design. The normalized redline
  keeps that rule.

### D-5. Masters that already carry tracked changes

The importer shows such a master's Accept-All view and warns that it did.

**Phase 1 refuses this export for such masters,** and names the fix: accept
or reject the pending changes in Word, save, and re-import. The *Redline of
extracted provisions* still works for them.

- **Why refuse:** Reject All would also reject the other author's pending
  changes, so "Reject All = your original" could not hold. Layering our
  marks inside theirs is Phase 3 material.
- **Detection:** reuse the existing package-wide `tracked_changes` check,
  which covers the document, headers, footers, styles and numbering.

### D-6. Revision metadata and package details

- **Author and date.** Author "Build-a-Spec" (Decision 5). Date is the
  export time, in UTC.
- **Revision ids.** Start above the highest `w:id` already in the package's
  story parts. Bookmarks and comments share that annotation-id space, so
  Batch 5's `itertools.count(1)` is only safe on the fresh documents it was
  written for.
- **Schema order (Word is strict about it):**
  - the paragraph-mark marker is the *first* child of `w:pPr/w:rPr`;
  - `w:rPr` precedes `w:sectPr` and `w:pPrChange` inside `w:pPr`;
  - a row's `w:del` follows its other `w:trPr` properties.

  Batch 5's `_mark_paragraph` appends at both levels. That is only correct on
  the fresh paragraphs it was written for, so it must not be reused as-is.
- **Clone hygiene (Phase 0, both exports).** A template clone drops:
  - `w:sectPr` (finding #3);
  - `w14:paraId` and `w14:textId`, which Word expects to be unique and
    regenerates when they are absent;
  - bookmarks and comment anchors.
- **Identity markers on a moved provision.** A move puts two copies of one
  element in the redline, but the file must never carry two bookmarks with
  one name, and Word drops a duplicate on load.
  - **Which copy keeps them.** The copy that survives Accept All (the new
    position) keeps the provision's bookmarks and `w14` ids. It never keeps
    its `w:sectPr`, which stays at the old position (D-3). That way Accept
    All still equals the clean export, which moves the element the same way.
    The deleted old copy gives the bookmarks and ids up.
  - **The limit this creates.** Reject All restores a moved provision's
    text and formatting at the old position, but not its bookmarks. Word
    recreates `_GoBack` on its own, and a TOC update restores `_Toc`
    anchors.
  - **What D-7 checks.** Content and formatting exactly, plus
    every-bookmark-name-at-most-once. It treats `w14` ids as identity, not
    content.
- **Track Changes switched on in the file (optional, Decision 4).** Add
  `<w:trackRevisions/>` at its schema position in `word/settings.xml`, as the
  one other replaced member.
- **Filename.** `<your upload's name> - REDLINE.docx`, built from
  `session.source_docx_filename`, so overwriting the master is a natural
  rename.
- **The retained upload is never modified.** This is a new file. The
  DOCX_FIDELITY contract line "It never adds tracked changes to the retained
  source" stays true.

### D-7. The export checks its own promise (fail closed)

Before returning the file, render both the redline R and the clean export C
from the same plan. Then run pure-XML **Accept All** and **Reject All**
transforms on R's body and compare canonically: adjacent runs with identical
properties merged, empty runs dropped, and `w14` ids treated as identity
rather than content. All four of these must hold:

- `accept(R) ≡ C`
- `reject(R) ≡` the upload's body, except for the one documented D-6 limit:
  a moved provision's bookmarks stay with its new copy
- no bookmark name appears twice in R
- every package member other than `word/document.xml` (and `settings.xml`,
  if Decision 4 turns Track Changes on) is byte-identical to the upload

If any check fails, the route returns a 409 with a named reason, the panel's
export error strip shows it, and an `export` diagnostics event records which
check failed and the first mismatching element.

This is the same posture as the byte-exact mode's independent output audit.
The transforms are cheap: a few linear XML passes, which Phase 1 must
measure on a 1,000+ paragraph master. The same transforms are the core test
oracle.

### D-8. API and UI

- **Route.** `GET /api/export/docx?redline=master&mode=preserved` returns the
  new file.
  - Today a redline forces `normalized`; this pairing becomes allowed.
  - **Defaults, for callers that name no mode:**
    - `redline=master` → `preserved` when it is available, the same way the
      clean export defaults to `preserved`;
    - `redline=version` → `normalized`, because the preserved redline is
      master-only in Phase 1.
  - `redline=version` + `preserved` is a 400 in Phase 1. Redlining against
    an arbitrary version on the original is Phase 3.
- **The frontend never relies on either default.** (Codex, PR #181.)
  - **What it sends.** Every redline menu action states its mode
    explicitly: `preserved` for the new item, and `normalized` for both
    *Redline of extracted provisions* and *Redline vs version…*.
  - **Why it matters.** Today *Redline of extracted provisions* calls the
    bare `redline=master` URL. Under the new default it would silently
    download the new format, leaving two menu items that produce the same
    file.
  - **The code change.** `ExportDocxQuery` / `exportDocxUrl` in
    `frontend/src/lib/api.ts` gain a `mode` on redline queries. Today a
    redline query cannot carry one.
  - **The pin.** `frontend/tests/downloads.test.ts` updates its bare-URL
    assertions to the explicit forms.
- **Payload.** `preserved_redline_available` plus a reason. It is one
  derivation, shared by the route and the menu, the
  `_preserved_export_available` pattern. False when:
  - there is no retained upload or format map;
  - there is no baseline;
  - the package carries pending revisions.
- **Export menu** (imported documents):
  - New item: **Redline on your original (tracked changes)**, placed right
    under *Export Word (keeps your formatting)*.
  - *Redline of extracted provisions* stays below it, for masters D-5
    refuses and for comparing against a version.
  - **Open redline in Word** through the existing native bridge
    (`main.py` `open_in_word` gains the redline variant).
- **Capability.** `export.redline-original` is the usual three-place edit:
  `capabilities.ts`, the control's `data-capability`, and the existing
  `export` tour step. No new step and no `TOUR_VERSION` bump.
- **Copy.** Update `SOURCE_OUTPUT_GUIDANCE`, Help, and the trust dossier's
  export card (both are contracts).

## Phases

### Phase 0 — Fix today's formatted export (ships on its own)

**Scope:**

- The four findings and the migration issue.
- The shared word-level splice (D-2), used by the clean export.
- Clone hygiene (D-6).
- Section-break rules:
  - an empty section-break paragraph stays after the content above it;
  - a clone never copies a break;
  - deleting *or moving* the provision that carries the break in its own
    `w:pPr` leaves an empty paragraph holding the break where the provision
    was. The redline's Accept All must be able to reproduce this, which is
    why D-3 never deletes a paragraph mark that holds `w:sectPr`.
- Stale "(Not used.)": a PART that now has articles drops its
  "(Not used.)" line.
- The diff's letter numbering: use `labelled_paragraphs`.

**What users will notice:** edited and relettered provisions keep their tabs
and bold/italic; section breaks survive edits; the stale "(Not used.)" line
goes away.

**Tests:**

- The four probes become regression tests.
- `test_an_untouched_document_round_trips_element_for_element` must stay
  green.
- New: an edited provision keeps emphasis on its unchanged words.

**Docs:** the "two inherent limits" in `DOCX_FIDELITY.md` shrink to
"formatting of *new* words is inherited from their neighbour". Plus a
release note.

**Size:** one medium PR.

#### Phase 0 — as built

**Built in PR #184 (2026-09-22); ships in 1.21.0**, whose
`release_notes.py` entry uses the Phase 0 draft below. PR #184 itself
bumped no VERSION and added no entry. The contract is in `docs/DOCX_FIDELITY.md` →
"Appearance-preserving export"; the why and the traps are in `CLAUDE.md` →
"The formatted export stops losing things". Code: `spec_doc/source_splice.py`
(new — the D-2 splice), `spec_doc/source_render.py` (rewritten: `_Walker`
collects what to emit, `_Assembler` places the unmodelled content and
section breaks and renders), `diffing._letters`, the redline writer's label
prefix, and the `export` trace event (`mode` is now the mode that ran, plus
`render` counts). Tests: `tests/test_source_splice.py`, the Phase 0 block of
`tests/test_preserving_export.py`, and one each in `test_diffing.py`,
`test_redline_export.py` and `test_diagnostics.py`.

Deviations from the text above:

1. **Five more defects of the same export were fixed**, each found while
   building it and each with its own test: article numbers rewritten on an
   export with no edits (`1.01` → `1.1`, `1.2 - X` → `1.2 X`); blank lines,
   page breaks and section-break paragraphs after the last provision
   dropped (the old trailing sweep skipped blanks); a new article cloned
   from a provision (so it looked like one, and in a Word-numbered master
   printed a typed number beside Word's); a control character in a
   provision failing the export with a 500; and the `export` event logging
   `normalized` while the preserving render ran.
2. **The limits shrink to two, not one.** New words inherit their
   neighbour's formatting, as planned. But a paragraph with pending tracked
   changes is still rewritten from its first run rather than spliced —
   splicing it means accepting its revisions first, which is D-5 territory —
   and a paragraph outside the splice's eligibility (hyperlink, field,
   content control, comment or note reference, `w:sym`, drawing) still takes
   the fallback. The `export` event's `render.fallback` counts both by
   reason.
3. **Eligibility is slightly wider than D-2's list.**
   `w:lastRenderedPageBreak`, `w:softHyphen` and a page or column `w:br`
   are zero-width run nodes (Word writes the first on every save; excluding
   it would send most Word-saved paragraphs to the fallback), and XML
   comments and processing instructions count as markers. A zero-width node
   on the EDGE of a deleted span is kept, so a page break in front of a
   relettered label survives.
4. **Break placement is gap scoring, not an LIS.** Phase 0 detects no
   moves, so where a break goes after a reorder is `_Assembler._place`'s
   call: the gap that keeps the most emitted elements on their side (above
   it before, below it after), ties to the gap right after the nearest
   surviving element that was above it. **Phase 1 must place the redline's
   breaks by this same rule** (call `_place`, or prove its LIS agrees), or
   Accept All will differ from this export exactly on reordered sections.
5. **A break is never dropped**, although D-3 permits it when the content on
   both sides was deleted. An emptied section keeps its break, and so an
   empty page. Phase 1 cannot delete a paragraph mark holding `w:sectPr`, so
   the clean export must not either.
6. **What a displaced break holder leaves is its `w:pPr` only** — no runs,
   bookmarks or `w14` ids — plus `w:numId 0` when it was Word-numbered
   (`_cancel_numbering`; Word prints the number of an empty numbered
   paragraph). This holds for a moved holder as well as a deleted one. For
   Phase 1: the old copy keeps its paragraph mark and `w:sectPr` (D-3), and
   its Accept All view must equal this leftover, so a Word-numbered holder
   needs a `w:pPrChange` recording the numbering cancel, and the old copy
   gives up its bookmarks and ids (D-6 already says so).
7. **Settled 2026-09-22 (Decision 7): keep today's placement.** A provision
   added right after the last paragraph of a section lands after the break,
   at the top of the next section, because the break stays with the content
   above it. Word's Enter at the end of that paragraph would keep the new
   text in the section; the owner chose the current rule, which DOCX_FIDELITY
   already documents.
8. **Words prepended to a paragraph go after the zero-width content in
   front of its first word** (caught in review on PR #184). D-2 does not
   say where an insertion lands beside a page break or a bookmark, and the
   script cannot say it (an insert carries no offset), so `_pieces` decides:
   an insertion before the first word goes after the zero-width content
   leading that word, so a leading page or column break still starts the
   paragraph on a new page and a bookmark opening over the text still wraps
   it. Everywhere else zero-width content stays with what follows it (a
   break at the end stays after words appended there, which is why the rule
   is leading-only).

What Phase 1 starts from:

- `source_splice.plan_splice(source, target)` returns keep / delete / insert
  `SpliceOp`s whose keep and delete ranges partition the source in order
  (`style_at` picks a new word's formatting). `map_paragraph(element,
  expected_text=)` returns `(ParagraphMap, "")` or `(None, reason)`, and
  `_pieces(pmap, ops)` is the per-op run slicing `render_clean` uses. Add
  `render_redline` beside `render_clean` over the same pieces: delete →
  `w:del` with `w:delText`, insert → `w:ins`. `_pieces` also places
  zero-width content beside an insertion (deviation 8); the redline's walk
  must place it the same way, or Accept All moves a leading page break. The
  simplest route is to have `_pieces` emit the deleted content too, as its
  own piece kind that `render_clean` skips.
- `_Assembler.assemble()` already decides, per body child: clone, splice,
  fallback, new, carried, or dropped. D-1's record list is a refactor of
  it, not a second walk.
- The clean fallback is `_write_paragraph_text`: it keeps `w:pPr`, drops
  every other child (bookmarks included) and writes one run with the first
  run's `w:rPr`. The redline fallback's accept view must equal it.

Found, not done (outside Phase 0):

- `iter_paragraphs` refs — the open-items list, lint issues, Final QC's
  `reviewed_ref`, the export schedules — still count preserved blocks, so
  after a table they can disagree with the panel's letters (the review queue
  reads the serialized labels and already agrees).
- The normalized clean export still gives a preserved block a letter.
- The appearance-preserving export of a non-spec import (a memo) still
  prints `PART 1 - GENERAL` and `1.1 IMPORTED CONTENT`, scaffolding the file
  never had and the panel hides.
- A template clone keeps the template's own revision marks (`w:pPrChange`,
  a tracked paragraph mark in `w:pPr/w:rPr`). Outside D-6's hygiene list;
  it matters only for masters with pending revisions.

### Phase 1 — Redline on your original

**Scope:**

- The plan/render split (D-1) and the revision writer.
- Deletes, inserts, word-level splices, letters and tables (D-2 through
  D-4).
- Move detection: the per-sibling longest increasing subsequence, as an
  opt-in on `diff_sections`. With the flag off, output is byte-identical,
  so the compare view and the normalized redline are untouched. Moves are
  rendered as delete plus insert.
- The explicit `mode` on every redline URL the frontend builds (D-8).
- The D-5 refusal.
- Ids, schema order, the self-check (D-7).
- The route, the payload flag, the menu item, Open in Word, the capability.

**Docs:**

- `DOCX_FIDELITY.md`: a new contract row and a section.
- `README.md`.
- `CLAUDE.md` implemented notes.
- `RELEASE_WINDOWS.md` QA rows.
- The release note.

**Size:** large. Probably two PRs: backend, then UI.

#### Phase 1 (backend PR) — as built

**Built 2026-09-22/23 in PR #187 (merged `0aa2e98`).** The 1.21.0 build
carries it, but only through the API: the Export menu does not offer it until
the UI PR lands, so 1.21.0's release notes do not mention it, and the Phase 1
draft under "Release-note drafts" waits for the release that carries the UI.
PR #187 itself bumped no VERSION and added no `release_notes.py` entry. The contract is in `docs/DOCX_FIDELITY.md` →
"Redline on your original"; the why and the traps are in `CLAUDE.md` →
"Redline on your original — implemented notes (Phase 1, backend PR)".

Code: `spec_doc/revisions.py` (new — the D-7 oracle), `spec_doc/revision_marks.py`
(new — the revision writer), `spec_doc/source_render.py` (D-1's records;
`_RedlineBuilder`; `render_preserving_redline`), `spec_doc/source_splice.py`
(`_pieces` emits deleted content as its own piece; `render_redline`),
`spec_doc/diffing.py` (`detect_moves=`), `spec_doc/source_mapping.py`
(`detect_pending_revisions`), `spec_doc/docx_export.py`
(`upload_redline_filename`), `app.py` (route, defaults, payload, one
derivation), and — the one frontend change that could not wait —
`frontend/src/lib/api.ts` + `ArtifactPanel.tsx` (every redline URL names its
mode). Tests: `tests/test_revisions.py`, `tests/test_redline_original.py`, and
additions to `test_source_splice.py`, `test_diffing.py`, `test_diagnostics.py`
and `frontend/tests/downloads.test.ts`.

Deviations from the text above:

1. **Moves are reconciled with `_place` at body level.** The diff's
   per-sibling LIS is built as planned (an opt-in, byte-identical off — 600
   diffs and 600 normalized redline bodies compared). But deviation 4 asked
   the redline to place breaks by `_place` or prove the LIS agrees, and it
   does not always agree: reversing three provisions across two breaks makes
   the diff keep the last one, which cannot sit between the two breaks
   `_place` placed. So the redline decides what is shared (kept in place)
   with its own heaviest increasing subsequence over the clean records'
   upload positions (`source_render._keep_in_place`, pure and unit-tested):
   every record `_place` pinned around a break is forced in (weight count² +
   1), then the diff's stayers (count), then its movers (1). Wherever a break
   allows, that honours the diff; where one does not, it moves the fewest
   elements it can, and the export event counts the extras
   (`redline.moves_added` — two moves, not three, in the reversal). The
   diff's say changes real output: moving a provision with two children below
   its two siblings, a raw body-level chain would keep the parent's three
   paragraphs and move the siblings, while the diff (longest sibling run
   first) moves the parent — and the redline shows the parent moved.
2. **The refusal vocabulary is wider than D-3 and D-5 name.** Beyond pending
   revisions (plus `revision_scan_unavailable`, a scan that could not say)
   and the blocks Word cannot track (`block_content_control`, `field_block`,
   `simple_field`, `untrackable_markup`, `pending_revisions_in_body`), the
   plan had no word for three cases: a reorder that would need a section
   break to move (`section_break_reorder`); a moved provision carrying a
   comment range or reference or a note reference (`moved_annotation` — Word
   cannot show one annotation in two places, and unlike a bookmark it cannot
   simply stay with the new copy); and an upload element no record accounts
   for (`unaccounted_content` — refused rather than risk losing it). The
   payload adds two availability codes, `no_baseline` and `no_original`.
   Every reason has a server-authored sentence, and every sentence names
   what still works.
3. **D-5 uses the package-wide scan, split in two** (the owner's correction
   to the plan). The `tracked_changes` check of
   `detect_global_source_blockers` also fires when Track Changes is merely
   switched on (`w:trackRevisions` in `settings.xml`) with nothing pending,
   which does not break "Reject All = your original". The story-part scan was
   extracted (`_revision_scan_blockers`, same order and answers for the
   byte-exact mode) and `detect_pending_revisions` runs it without the
   settings half. The refusal has its own reason and remedy — the byte-exact
   mode's `tracked_changes` remedy offers "Edit freely", which means nothing
   here — naming the fix and saying the redline of extracted provisions still
   works.
4. **Word cannot track a document's last paragraph mark.** A tracked
   insertion or deletion of the body's last paragraph marks its words and
   leaves its mark alone, and the canonical comparison tolerates the empty
   last paragraph that leaves. The plan did not mention it; it came up the
   first time a test deleted the last provision. **Only a FORMATTING-FREE one
   is tolerated** (Codex, PR #187): an empty paragraph that kept its Word
   numbering prints a stray letter and one that kept `w:pageBreakBefore`
   makes a blank page, and the first cut passed both. The writer records the
   last paragraph's formatting as a tracked change whose side in the
   resolution that leaves it behind is empty (`neutralize_last_paragraph`:
   `w:pPrChange` plus a mark `w:rPrChange`), so the leftover sets nothing.
5. **Deleted content keeps its bookmarks inside `w:del`; only a MOVED old
   copy gives them up** (with its `w14` ids), because the new copy carries
   them. The self-check excludes exactly the names a moved copy carries, and
   no other.
6. **`settings.xml` is never touched** (Decision 4), so a master whose Track
   Changes was already on exports with it still on.
7. **A new provision nested deeper than any provision a Word-numbered master
   already has** keeps its template's `w:ilvl` in the formatted export, so
   Word shows it one level up (found here, a Phase 0 behaviour — see "Found,
   not done" below). The redline's Accept All reproduces it by construction,
   which is the promise; the re-import test therefore checks the tree for
   typed-letter masters and "the same as the formatted export" for every
   labelling kind.

Measured (recorded, not asserted):

- **Cost, linear.** A 1,200-paragraph master with forty edits: the whole
  redline 0.65–0.7 s, the self-check 0.26–0.29 s, the clean export inside it
  about 0.2 s, the pending-revisions scan 0.075 s. 2,400 paragraphs: 1.3 s /
  0.56 s. 4,800: 2.4 s / 1.16 s.
- **Corpus sweep** (17 corpus masters × 60 scripted edit mixes of up to 14
  edits, 1,020 renders): no refusal and no failed self-check. The fallback
  rate is the number to widen from: 230 of 525 edited provisions (44%) took
  the fallback, **every one for `hyperlink`** — Word-saved masters put
  hyperlinks in provisions, and the splice does not descend into
  `w:hyperlink` yet (the writer already does).
- **Fixture sweep** (the eleven hand-built masters of the suites × 200 edit
  mixes, 2,200 renders — two of them ending in a formatted provision): no
  refusal and no failed self-check under the formatting-free tolerance; 118
  emptied break holders, 5 extra moves forced by breaks and 347 last
  paragraphs whose untrackable mark was neutralized among them.

What the UI PR starts from:

- Every doc payload carries `preserved_redline_available` and, when false,
  `preserved_redline_reason` `{code, message}` — the message is
  server-authored; render it verbatim (the `sourceCapabilities.ts` rule).
  Add both to the frontend `DocPayload` type; nothing reads them yet.
- `exportDocxUrl({redline: "master", mode: "preserved"})` already builds the
  URL (pinned in `downloads.test.ts`). The new item goes right under *Export
  Word (keeps your formatting)* with its own `runExport` key — the two
  existing redline actions are pinned by theirs (`redline-master`,
  `redline-version`) to `mode: "normalized"`.
- A refusal is a 409 whose body carries `error` (the sentence
  `downloadAttachment` already surfaces) and `code` (the reason).
- *Open redline in Word*: `main.py`'s `open_in_word(mode)` fetches
  `/api/export/docx?mode=…`; it needs the redline variant. The filename comes
  from `Content-Disposition` — `<your upload's name> - REDLINE.docx`.
- The capability `export.redline-original` (the three-place edit) on the
  existing `export` tour step; no `TOUR_VERSION` bump.
- The copy: `SOURCE_OUTPUT_GUIDANCE`, Help, and the trust dossier's export
  card; and the new export's `RELEASE_WINDOWS.md` rows (real Word: no repair
  prompt, the reviewing pane shows Build-a-Spec, Accept All looks like the
  formatted export, Reject All like the original, and the overwrite-the-
  master workflow end to end). The Phase 0 rows are in.

Found, not done (outside this PR):

- **A new nested provision in a Word-numbered master takes its kin's
  `w:ilvl`** when the master has no provision at that depth (deviation 7
  above). Fix in the formatted export: when an auto-numbered template sits at
  another depth, offset the clone's `w:ilvl` by the depth difference (the
  importer reads `ilvl` as a relative level), if the numbering definition
  defines that level.
- **Hyperlinks send edited provisions to the fallback** (44% of the corpus
  sweep's edited provisions). The splice's eligibility widens from here.
- Phase 0's "Found, not done" list stands unchanged.

(Both fixed on 2026-09-23 — see "Phase 1 follow-up (links and the nesting
level) — as built" below. The list above is kept as the record of what the
backend PR found.)

#### Phase 1 (UI PR) — as built

**Built 2026-09-23 in PR #190.** It bumped no VERSION, added no
`release_notes.py` entry and changed no backend route or payload — the UI
reads what the backend PR already serves. The why and the traps are in
`CLAUDE.md` → "Redline on your original — implemented notes (Phase 1, UI
PR)"; the contract gained an "In the app" paragraph in
`docs/DOCX_FIDELITY.md`.

**Which release carries it depends on when v1.21.0 is tagged — the owner's
call.** 1.21.0's entry was written by the project-workspace closeout
(PR #188) while only the backend had merged, and it leaves the redline out;
v1.21.0 was still untagged when this PR was written. Tagged before this PR
merges, 1.21.0 ships none of this UI, and the Phase 1 draft under
"Release-note drafts" waits for the next release; nothing else changes.
Tagged from `master` after it merges, 1.21.0 ships the menu item, so before
that tag the 1.21.0 entry (not frozen until tagged) takes the Phase 1 draft,
and the statements that the 1.21.0 build reaches the redline only through
the API are corrected in the same change:
`project-workspace/07_RELEASE_CLOSEOUT.md` deviation 8, the
project-workspace README's phase-7 row and release-policy paragraph, the
backend as-built note above, and the closeout's `CLAUDE.md` section (by
erratum — it is append-only). The tag need not be `master`'s head: PR #189
(compaction Phase 3) merged first and raises the same question for its own
work, and tagging the closeout merge, `a273ab7`, keeps both out of 1.21.0.

Code: `frontend/src/types.ts` (`preserved_redline_available`,
`preserved_redline_reason` and `PreservedRedlineReason` on the payload; the
bridge's `open_in_word(mode, redline?)`), `App.tsx` (both fields read on
both payload paths, forwarded by the session-bundle mapping, cleared with a
new session; `onOpenInWord(mode, redline)`), `ArtifactPanel.tsx` (the item,
its opener, a busy state keyed by target, the extracted-provisions tooltip),
`main.py` (`open_in_word(mode, redline="")`), `lib/capabilities.ts` +
`lib/tour.ts` (`export.redline-original` on the existing `export` step, whose
body was resynced), and the copy: `lib/sourceOutputGuidance.ts`,
`HelpModal.tsx`, `TrustDeepDiveModal.tsx`. Tests: three new pins in
`frontend/tests/downloads.test.ts`, one in `sourceCapabilities.test.ts` (and
its id list), and the four `open_in_word` tests in `tests/test_close_prompt.py`
extended (the first parametrized over both variants). QA rows:
`docs/RELEASE_WINDOWS.md` → "Redline on your original (redline program,
Phase 1)".

Deviations from the text above:

1. **"Right under *Export Word (keeps your formatting)*" means right under
   its pair.** In the desktop app the formatted export's own *Open in Word*
   stays directly beneath it, and the redline follows with *Open redline in
   Word* directly beneath the redline, so each opener sits beside the export
   it opens. In a browser there are no openers, and the redline is directly
   under the formatted export. The order is pinned (formatted → original →
   extracted), not the adjacency.
2. **The unavailable item is disabled, not hidden, and its opener is hidden,
   not disabled.** The item stays in the menu with the server's reason on
   hover (a `Tip`, because a disabled button never shows a native title);
   one greyed row saying why is enough, so *Open redline in Word* is offered
   only while the redline is available. The formatted export's opener
   differs deliberately: it falls back to the styled export, and there is no
   redline to fall back to.
3. **The shell pairs the redline with `preserved` only.**
   `open_in_word("normalized", "master")` is refused rather than opening the
   extracted-provisions redline in Word — nothing in the menu asks for that,
   and quietly opening a different file than the one asked for is the thing
   the house rules forbid. `redline` is a closed vocabulary (`""`,
   `"master"`) checked before a URL is built.
4. **`SOURCE_OUTPUT_GUIDANCE` gained a sixth concept** rather than a
   rewritten fifth: *Redline on your original* sits beside *Normalized
   redline*, which now points at it. Three surfaces render the list (Help,
   the tour's export step, the dossier's export card), and two places
   counted it ("five") — both now say six. The guidance test's id list was
   updated knowingly.
5. **The copy the feature made false was fixed beyond the three named
   surfaces**, because each was a contract a reader could now catch out:
   Help's trust point "The redline scope is explicit" (it described one
   redline whose Reject All cannot recreate the master), the dossier's
   card 10 (Compare shares code with "the redline export" — now true only of
   the extracted-provisions one), the dossier's "What this does not do"
   bullet, the extracted-provisions menu tooltip, Help's Export step and its
   office-master recipe. Help gained a *Redline on your original* recipe for
   the replace-your-master workflow. Pre-existing staleness the feature did
   not touch (the office-master recipe's pre-1.14.0 permission steps) was
   left alone.
6. **A new session also clears the formatted export's flag.**
   `clearSessionState` never reset `preservedExportAvailable` — invisible,
   since the Export menu needs content, but it was the one payload flag left
   for the refetch; it now clears with the two new ones, and all three are
   pinned.
7. **The tutorial-scope refusal of `open_in_word` was never pinned.** The
   fourth test now pins it for both variants, along with the closed
   vocabulary and the pairing rule.

Checked (recorded, not committed): a headless Chromium run against the real
backend and the production build — the item enabled and downloading
`<upload name> - REDLINE.docx` on an ordinary master; disabled, with hover
text byte-identical to the payload's reason, on a master carrying a pending
`w:ins`; the stubbed desktop bridge called with `("preserved", "master")` by
*Open redline in Word* and `("preserved", "")` by *Open in Word*. None of it
stands in for the real-Word QA rows, which are still to run.

What Phase 2 starts from: unchanged by this PR — the native-moves rendering
and the real-Word judge above, plus the Phase 1 and Phase 0 "Found, not
done" lists, none of which this PR touched.

#### Phase 1 follow-up (links and the nesting level) — as built

**Built 2026-09-23**, one PR, fixing the two losses the backend as-built note
recorded under "Found, not done". Both were losses of *Export Word (keeps
your formatting)*, so the redline's Accept All reproduced them by
construction; fixing the export fixes both. It bumped no VERSION, added no
`release_notes.py` entry and changed no route, payload, SSE event,
dependency, env knob or project format. The contract is in
`docs/DOCX_FIDELITY.md` ("Hyperlinks are spliced like the rest of the
paragraph", and the new-provision bullet); the why and the traps are in
`CLAUDE.md` → "Links and the nesting level survive the formatted export —
implemented notes".

Code: `spec_doc/source_splice.py` — the map descends into `w:hyperlink`
(`_MapBuilder`; atoms, runs and characters remember their link:
`ParagraphMap.links` / `run_links` / `char_links`), `_place_new_words` /
`_placements` decide each insertion's `_Placement` (its link, where its
formatting comes from, and the link start a replacement running into a link
is written at), `_PieceWalk` keeps a link's zero-width content inside it,
and both renderings open one copy of a link per stretch of its pieces
(`_open_link`). `spec_doc/source_render.py` — `_NumberingTables` (the
importer's own numbering readers over the upload, lazy), `_Walker.depth_at`,
`_Assembler._nesting_level` → `Record.level` → `_set_numbering_level` in
`render_inserted`, and two render counts (`level_offset`, `level_kept`).
Tests: 13 in `tests/test_source_splice.py` (one of them a 600-edit property
test over a paragraph holding every link shape: Accept All is the clean
rendering, Reject All the source, the importer reads the edit, no link is
ever split), 5 link rows and the Word-numbered re-import checks in
`tests/test_redline_original.py`, 2 link and 7 level tests in
`tests/test_preserving_export.py`, and 1 in `tests/test_diagnostics.py`. QA
rows: `docs/RELEASE_WINDOWS.md` → "Links and deeper provisions (redline
program, Phase 1 follow-up)".

Three questions this left open, decided and pinned:

1. **Where inserted words go at a link's edge: outside it.** New words go in
   a link only when they are wholly its own — typed over words that all sit
   in one link, or inserted between two of its characters. At either edge,
   or replacing words on both sides of one, they go outside every link, so a
   link never grows to cover words the user added beside it. They take their
   formatting from the nearest character where they go (in the same link, or
   outside every link), so a word added after a link is not drawn in its
   blue underline. A replacement that runs INTO a link from outside it is
   written at the link's start — written where the script puts it, it would
   cut the link in two — and a link's own zero-width content (a bookmark
   closing or opening inside it) stays inside it.
2. **A link whose words are all deleted: gone.** The clean export does not
   write it (an empty hyperlink shows nothing), unless a bookmark inside it —
   never deleted — keeps it; the redline leaves the link holding its deleted
   runs, so Accept All leaves it empty, which the self-check's comparison
   drops as Word shows nothing for it. An EMPTY hyperlink in the upload is
   carried where it was.
3. **A level the master's numbering does not define: the clone keeps its
   kin's level.** Word shows it one level up (the old behaviour), but never
   at a level the master's list cannot draw; the export event counts it
   (`level_kept`).

Deviations from the backend note's text:

1. **The kin's level is resolved the way Word resolves it** — its own
   `w:numPr`, else its paragraph style's through `w:basedOn` (the importer's
   `_effective_numbering`) — because in most office masters the numbering
   rides the PR1–PR5 styles and a style-numbered kin has no `w:ilvl` of its
   own to offset. The clone's level is written on its OWN `w:numPr`
   (`w:ilvl`, then `w:numId`, which is `CT_NumPr`'s order), naming the
   instance explicitly even when its style names it too, so Word and the
   importer read one answer. The clone keeps the kin's paragraph style (see
   "Found, not done").
2. **"Defines that level" is read as "defines it and draws it as a
   provision".** A level whose label grammar the importer promotes to a PART
   or article heading (`_promoted_heading_kind` — say `%2.%3`) is never
   taken, or the new provision would re-import as an article.
3. **Only Word-numbered (AUTO) kin is renumbered.** A typed-letter kin
   carries its level in its label ("1."), which the importer reads.
4. **`FALLBACK_HYPERLINK` now means only a link inside a link.** Anything
   else inside a link that the splice cannot map is refused by its own reason
   (`field`, `content_control`, …), and a plain link is spliced. The export
   event's `render.fallback` counts therefore changed meaning for
   `hyperlink`.
5. **Two existing tests changed knowingly.**
   `test_unmappable_paragraphs_are_refused_by_name` pinned "a hyperlink is
   refused"; it now pins what inside a link is refused, by name.
   `test_a_fallback_paragraph_deletes_all_and_inserts_the_new_text` used a
   hyperlink to reach the fallback; it now uses `w:sym`, which still falls
   back, so its subject — the fallback's redline — is unchanged.
6. **The re-import test checks the TREE for Word-numbered masters too**, as
   the backend note said it would once this was fixed: typed letters, Word
   numbering on each paragraph, and Word numbering on the PR1–PR4 styles,
   ten seeds each. The random edit mix rarely nests (one seed in ten), so two
   more tests aim at it: every depth a master defines, and a nesting-heavy
   sweep (23 offsets per master kind, none kept).

Measured (recorded, not asserted):

- **Link-free paragraphs are untouched.** 7,500 seeded renders, clean and
  redline, of link-free paragraphs came out byte for byte identical to the
  module before this change. Link paragraphs: 3,000 seeded edits in scratch
  plus the committed 600 — Accept All is the clean rendering, Reject All the
  source, the importer reads the edit, and no link is split.
- **Corpus sweep** (the backend note's shape — 17 corpus masters × 60
  scripted edit mixes of up to 14 edits, 1,020 renders; seeds
  `seed × 97 + len(case id)`): no refusal and no failed self-check, before
  or after. Fallbacks on `master` (d1397ef): 245 of 516 edited provisions
  (47.5%), every one `hyperlink` (the backend note recorded 230 of 525 with
  its own seeds). On this PR: **0 of 516** — every edited provision spliced.
  New sub-provisions: 2 given a level of their own, 78 kept at their kin's —
  every kept one in a single-level list: the corpus builds its Word-numbered
  masters from python-docx's default template, whose list definitions
  define level 0 only, so there is no sub-level to give. Real multilevel
  masters define nine; the suite's own four-level masters are where the
  offset is proven.
- **Revert matrix**: see the `CLAUDE.md` section.

Found, not done (outside this PR):

- **A style-numbered clone keeps its kin's paragraph style.** Its number is
  right (its own `w:numPr` names the level), but whether Word draws it at
  the level's indent or the style's depends on how the master defines them —
  the new QA row finds out. If Word keeps the style's indent, the fix is to
  take the style the numbering level itself names (`w:lvl/w:pStyle`, which
  is how most office masters link PR1–PR5 to their list).
- **A single-level Word list cannot draw a sub-provision**, so the clone
  keeps its kin's level and re-imports as its parent's sibling. Writing the
  label as text (numbering cancelled) would draw it right; nothing in the
  corpus says real masters need it.
- **In a typed-letter master**, a new provision deeper than any the master
  has keeps its kin's paragraph formatting: its letter is right and it
  re-imports right, but it is drawn at its kin's indent where the master
  sets indents per paragraph or per style (a Phase 0 behaviour).
- Phase 0's "Found, not done" list stands unchanged.

What Phase 2 starts from: the native-moves rendering and the real-Word judge
above, plus the "Found, not done" lists — this one's and Phase 0's.

### Phase 2 — Native moves and real-Word proof

**Scope:**

- **Native moves:** render the pure moves Phase 1 already detects as
  `w:moveFrom`/`w:moveTo`. Detection itself does not change. A moved *and*
  edited element stays delete plus insert.
- **Real Word as the judge:** the hidden-Word automation gains an Accept All
  / Reject All + SaveAs mode, so real Word judges the corpus. It is an
  optional Windows suite, like the visual regression suite.
- **Settings:** the Decision 4 and 5 options, if chosen.

**To verify** against ECMA-376 and a Word-saved sample before building:

- the `moveFrom` run content (`w:t` vs `w:delText`);
- the range-marker pairing.

**Size:** one medium PR.

### Phase 3 — Later, only if wanted

- A redline against any version, on the original.
- Layering on masters that carry pending revisions. Word's "Reject all
  changes by Build-a-Spec" would then give back the original.
- A comment on each change citing the research item or QC finding behind it.
  `Paragraph.source_item_id` already carries the link.
- Redlining the section number in headers and footers. This breaks the
  "headers are never rewritten" contract, so it needs its own decision.

## Tests (Phase 1)

- **The two XML oracles** (`accept_all`, `reject_all`, pure lxml) plus the
  canonical comparator, unit-tested on hand-built revision XML.
- **The invariant matrix.** Each row asserts both `accept(R) ≡ C` and
  `reject(R) ≡ original`. Rows:
  - a one-word edit;
  - an edit inside a bold phrase;
  - a deletion at a run boundary;
  - insertion at the start and at the end of a paragraph;
  - relettering in a typed-letter master;
  - insert and delete in a Word-numbered master;
  - deleting a paragraph that has children;
  - deleting an article;
  - deleting a table;
  - a pure reorder with every text unchanged, both of provisions within an
    article and of an article carrying its children: Reject All restores the
    upload's order. This is the case the diff alone cannot see;
  - a reorder that also edits the moved provision's text;
  - filling a "(Not used.)" PART;
  - deleting the provision under a section break;
  - deleting, and moving, a provision that carries the section break in its
    own `w:pPr`: Accept All leaves the break in an empty paragraph, and no
    paragraph mark holding `w:sectPr` is ever marked deleted;
  - a section renumber on a header line;
  - a fallback paragraph (field or hyperlink);
  - spacers travelling with their provision;
  - front matter untouched.
- **Package-level checks:**
  - every non-body member is byte-identical;
  - revision ids are unique and above the package's existing ids;
  - every revision carries an author and a date;
  - schema-order shape checks pass.
- **Re-import.** Re-importing R through the app's own Accept-All reader
  reproduces the current tree. This is the Batch 5 invariant, now on your
  original.
- **Corpus sweep.** Every corpus master, run through a scripted mix of
  edits, satisfies the invariants. Record the fallback rate.
- **API matrix:**
  - `redline=master` + `mode=preserved` → 200;
  - `redline=version` + `mode=preserved` → 400;
  - no baseline → 400;
  - pending revisions → 409 naming the fix;
  - a failed self-check → 409;
  - the filename comes from the upload;
  - the trace event is recorded.
- **Frontend:**
  - `npm test` (the capability contract);
  - a menu-wiring pin;
  - the URL-builder test pinning an explicit mode on all three redline
    actions;
  - `npm run build`.
- **Manual, in real Word, on a real office master:**
  - the file opens with no repair prompt;
  - the reviewing pane shows Build-a-Spec;
  - Accept All looks like the formatted export;
  - Reject All looks like the original;
  - the overwrite-the-master workflow works end to end.

## Decisions (ratified as recommended, 2026-09-22 — binding)

Abraham answered all seven on 2026-09-22, each as recommended (the seventh
was Phase 0's open question, deviation 7). They are binding on every phase;
changing one is a new decision, recorded here.

| # | Decision | Ratified |
|---|---|---|
| 1 | Moves | **Delete plus insert in Phase 1, native Word "Moved" marks in Phase 2.** The alternative was native moves from day one: more risk up front for a prettier first release. |
| 2 | Typed letters ("A." → "B.") | **Track the letter changes.** Required for "Reject All = your original"; Word-numbered masters have no such noise. |
| 3 | Masters that already carry tracked changes | **Refuse in Phase 1, and name the fix** (accept or reject them in Word, save, re-import). Layering our changes over theirs would lose "Reject All = original". |
| 4 | Track Changes switched on inside the exported file | **Off.** With it on, edits made in Word while reviewing would also be tracked, and it would have to be switched off again before the file becomes the master. Every Build-a-Spec change is a tracked change either way. |
| 5 | Author shown on each change | **"Build-a-Spec".** Matches today's redline and makes Word's "Reject all changes by Build-a-Spec" meaningful. The alternative was the user's name, set once in Settings. |
| 6 | Phase 0 may change the shipped *Export Word (keeps your formatting)* output | **Yes.** It is strictly better output, and it is what makes Accept All trustworthy. |
| 7 | Where a provision added right after a section's last paragraph lands (Phase 0 deviation 7) — ratified 2026-09-22 | **Keep today's placement:** after the break, at the top of the next section, because a break stays with the content above it. The alternative was Word's own Enter behaviour, which keeps the new text in the section. |

## Risks

- **Word's strictness about XML** is the main risk. Mitigations:
  schema-order shape tests, the self-check, and a real-Word pass over the
  corpus before release.
- **Unusual masters** (fields and content controls everywhere) send more
  paragraphs to the fallback. The result is still correct, just coarser.
  Measure the fallback rate and widen eligibility from evidence.
- **Moves across a section break** are the corner the Phase 0 rules exist
  for. They are covered by tests, not assumptions.
- **Performance** should be linear in document size. The self-check adds a
  few XML passes; measure on a 1,000+ paragraph master.

## Release-note drafts

- **Phase 0:** "Export Word (keeps your formatting) keeps more of it.
  Provisions that get relettered when you add or remove one above them keep
  their tab and any bold or italic, and so do the unchanged words of a
  provision you edit. Word section breaks survive your edits — deleting or
  moving the provision below one no longer removes or moves it, and adding a
  provision no longer duplicates one — and a '(Not used.)' line disappears
  once its PART has an article. Article numbers keep your master's format
  (1.01 stays 1.01), a new article looks like your other article headings,
  and the blank lines, page breaks and pictures around what you change stay
  where they were."
- **Phase 1:** "Redline on your original. Export a copy of the Word file you
  imported with every change Build-a-Spec made shown as Word tracked
  changes; your fonts, headers, footers and numbering are untouched. In
  Word, Accept All gives you the updated section and Reject All gives you
  your original back — the app checks both before it hands you the file —
  so you can review it, save it, and use it to replace your master. If your
  master already carries someone's tracked changes, accept or reject them in
  Word and import it again first; the redline of extracted provisions still
  works either way."
- **Phase 1 follow-up (links and the nesting level):** "Links and new
  sub-provisions survive *Export Word (keeps your formatting)* — and so the
  redline on your original. A provision holding a hyperlink keeps the link,
  its bold and italic and the tab after its letter when you edit it or it
  gets relettered, and words you add beside a link are never pulled into it.
  In a master whose provisions Word numbers, a new sub-provision (the first
  '1.' under an 'A.') now prints at its own level instead of one level up,
  whenever your master's numbering defines that level."
