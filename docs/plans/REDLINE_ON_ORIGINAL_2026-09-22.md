# Redline on your original — tracked changes in the Word file you imported

**Status:** Phase 0 built 2026-09-22 in PR #184 — not yet released; the
owner picks the release (its note is under "Release-note drafts"). All six
decisions ratified 2026-09-22 (see "Decisions"). **Phase 1 is next and not
started** — start from "Phase 0 — as built" under "Phases".
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

**Built in PR #184 (2026-09-22); not yet released.** No VERSION bump, no
`release_notes.py` entry. The contract is in `docs/DOCX_FIDELITY.md` →
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
7. **Open question for the owner:** a provision added right after the last
   paragraph of a section lands after the break, at the top of the next
   section, because the break stays with the content above it. Word's Enter
   at the end of that paragraph would keep the new text in the section.
   Documented in DOCX_FIDELITY as current behaviour; change it only if the
   owner prefers Word's.

What Phase 1 starts from:

- `source_splice.plan_splice(source, target)` returns keep / delete / insert
  `SpliceOp`s whose keep and delete ranges partition the source in order
  (`style_at` picks a new word's formatting). `map_paragraph(element,
  expected_text=)` returns `(ParagraphMap, "")` or `(None, reason)`, and
  `_pieces(pmap, ops)` is the per-op run slicing `render_clean` uses. Add
  `render_redline` beside `render_clean` over the same pieces: delete →
  `w:del` with `w:delText`, insert → `w:ins`.
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

Abraham answered all six on 2026-09-22, each as recommended. They are
binding on every phase; changing one is a new decision, recorded here.

| # | Decision | Ratified |
|---|---|---|
| 1 | Moves | **Delete plus insert in Phase 1, native Word "Moved" marks in Phase 2.** The alternative was native moves from day one: more risk up front for a prettier first release. |
| 2 | Typed letters ("A." → "B.") | **Track the letter changes.** Required for "Reject All = your original"; Word-numbered masters have no such noise. |
| 3 | Masters that already carry tracked changes | **Refuse in Phase 1, and name the fix** (accept or reject them in Word, save, re-import). Layering our changes over theirs would lose "Reject All = original". |
| 4 | Track Changes switched on inside the exported file | **Off.** With it on, edits made in Word while reviewing would also be tracked, and it would have to be switched off again before the file becomes the master. Every Build-a-Spec change is a tracked change either way. |
| 5 | Author shown on each change | **"Build-a-Spec".** Matches today's redline and makes Word's "Reject all changes by Build-a-Spec" meaningful. The alternative was the user's name, set once in Settings. |
| 6 | Phase 0 may change the shipped *Export Word (keeps your formatting)* output | **Yes.** It is strictly better output, and it is what makes Accept All trustworthy. |

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
  your original back, so you can review it, save it, and use it to replace
  your master."
