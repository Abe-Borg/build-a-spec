# DOCX fidelity and compatibility

This document is the canonical contract for imported DOCX preservation. It
covers the product boundary, export modes, API fields, blocker codes,
persistence compatibility, diagnostics, and fixture expectations.

## Product boundary

Build-a-Spec is a construction-specification authoring tool, not a general
Word editor. Import creates two deliberately separate artifacts:

1. the exact validated DOCX bytes, retained as an immutable recovery source;
2. a normalized SectionFormat tree containing only supported semantic body
   content.

The source package remains authoritative for source-preserving export. The
semantic tree remains authoritative for editing, normalized export, compare,
and normalized redline. Extraction does not make headers, footers, tables,
fields, hyperlinks, drawings, content controls, styles, page layout, or
arbitrary OOXML editable.

Because the tree is always SectionFormat, importing a file that is not a spec
section necessarily wraps it in scaffolding it never had. The parse therefore
records `spec_shape_detected` — false when it found no SECTION number, PART
heading, or numbered article — and the app must not present that scaffolding
as the document's own: the section header and empty parts are not rendered,
`missing_section_header` is suppressed, and the model is told the structure is
the app's. Nothing about extraction, retention, or export changes; this is a
presentation contract, and it lapses as soon as a real section header exists.
Projects saved before the field existed read as spec-shaped.

A **reference document** (`POST /api/reference/upload`) is not an import at
all. Its text is attached to the session as background for the model to read
and is never part of the specification: no tree, no source retention, no
capabilities, no effect on lint, compare, QC, readiness, or any export mode.
Because none of it becomes the document, the accepted types are wider than an
import's: Word, PDF, plain text, XML, and CSV (`backend/reference_extract.py`
owns the extension→extractor table and every extractor). A `.docx` attachment
passes the same bounded ZIP/OPC inspection as a master because it is the same
attack surface; every type is bounded by the same upload limit, and nothing is
retained beyond the extracted text.

The governing invariant is fail-closed: ambiguity may remove a body-editing
capability, but it must never create one. Build-a-Spec never silently converts
a failed source-preserving export into a normalized document.

An upload that fails the initial bounded ZIP/OPC safety checks is rejected
atomically and is not retained. A package that is safe enough to retain and
extract, but whose encoding, revisions, relationships, or raw ZIP layout
cannot be mutated safely, is retained as pass-through-only.

Full Strict OOXML semantic import is not supported. Package-level inspection
recognizes Strict relationship and Word namespaces so safety and revision
scans do not mistake them for malformed Transitional markup, but a fully
Strict `word/document.xml` main part is rejected atomically before any source
state is retained. It is not converted to Transitional OOXML. Supporting that
semantic dialect is a future compatibility feature, not part of the bounded
source-patching surface.

## Appearance-preserving export (the import path)

Every import takes this mode, and it is what "keep my formatting" means in
the product. It is deliberately a WEAKER promise than the byte-exact
source-preserving mode below, and it buys back the editing surface that
promise cost — three of twenty-seven body operations on a clean master.

**The contract.** Everything except the body of `word/document.xml` is
carried through byte-for-byte: headers, footers, styles, theme, fonts,
numbering definitions, page setup, section properties, and every other
package part. Inside the body:

* a provision the user did not touch is emitted as a **byte-identical clone**
  of its source element;
* a provision they edited keeps its `w:pPr` **and its own runs**: the new
  words are spliced into the runs already there (`spec_doc/source_splice.py`,
  the word-level splice of the Redline-on-your-original plan), so style,
  font, size, indent, spacing and numbering are exact, a bold or italic
  phrase they did not change stays bold or italic, and the tab after a typed
  letter stays a tab. A provision that is only **relettered** (a sibling was
  added or removed above it) is an edit of one token, so it keeps all of
  that too;
* a **preserved block** (table, picture, embedded object, content control) is
  emitted verbatim;
* a provision they added is cloned from the nearest kin **of its own kind**
  (a provision from a provision at its depth, an article heading from an
  article heading, first looking back to the last one emitted, then to the
  first in the upload), and takes that kin's label convention (Word-numbered
  or typed) and separator (a typed letter's tab, an article number's
  `1.01` width or ` - ` dash). It never copies the kin's identity (`w14:paraId`
  / `w14:textId`, which Word expects to be unique), bookmarks, comment
  anchors or section break. When the kin sits at ANOTHER depth — the first
  sub-provision anywhere under an "A." has no kin at its own depth — a
  Word-numbered clone takes its own level: the kin's `w:ilvl` offset by the
  depth difference (the importer reads `ilvl` relative to the article's
  list), in the kin's own numbering instance, written on the clone's own
  `w:numPr` with the instance named explicitly — even when the kin's
  numbering came from its paragraph style, whose style the clone keeps. Only
  a level the master's numbering defines with a visible label (not
  `numFmt="none"`, not an empty `lvlText`), and draws as a provision rather
  than a PART or article heading, is taken; otherwise the clone keeps its
  kin's level (Word shows it one level up — never a number the master's list
  cannot draw, nor no number at all) and the `export` event counts it
  (`render.level_kept`, beside `render.level_offset`). A typed-letter clone needs none of this: its label
  ("1.") carries its level;
* blank spacer paragraphs travel with the provision below them, so spacing
  survives a reorder;
* body content the tree never modelled that sits ABOVE a modelled element —
  a cover page, a revision history, a table of contents, a picture-only
  paragraph, a page break — travels with the element below it too, so a
  cover page stays ahead of the section it introduces. When that element is
  deleted, its blank spacers go with it and everything else stays where it
  was: an unmodelled block never migrates to the end of the file;
* body content after the last modelled element (`END OF SECTION`, an
  appendix, the blank lines, page breaks and section breaks between them) is
  carried through verbatim, in place — but an element the tree DID model and
  the walk did not reach was deleted by the user, and must not come back;
* a PART's `(Not used.)` line (the importer skips it) is dropped once that
  PART has an article, and kept while it has none.

**Section breaks belong to the content above them.** A Word section break is
the end of a section, so no edit to what sits below it moves or removes it,
and no edit loses or duplicates one:

* an empty paragraph holding a break (`w:pPr/w:sectPr`) stays after the
  content above it — deleting or moving the provision below it never takes
  it along;
* a provision that holds the break in its own `w:pPr` keeps it while it
  stays in place; deleted or moved away, it leaves an **empty paragraph
  holding the break where it was** (its own paragraph properties, without
  its text, its `w14` ids, or — when it was Word-numbered — its list
  numbering, `w:numId 0`, since Word prints the number of an empty numbered
  paragraph);
* a clone never copies a break (clone hygiene, above);
* a provision **added** right after the content that ends a section lands
  after its break — at the top of the next section — because the break stays
  with the content that was above it. (Pressing Enter at the end of that
  paragraph in Word would keep the new text in the section instead; moving a
  break is a Word edit.)
* where "in place" is ambiguous after a reorder, the break goes to the gap
  that keeps the most elements on the side they were on — above it before,
  below it after — preferring the gap right after the nearest surviving
  element that was above it. Breaks never cross each other, so sections keep
  their order. A section whose content was all deleted keeps its break (and
  so an empty page); removing it is a Word edit.

**Article numbers keep the master's format.** A heading typed `1.01 SUMMARY`
or `1.2 - SUBMITTALS` is reproduced in that form (width, trailing dot, dash
and the whitespace around them), so an export with no edits no longer
rewrites it as `1.1 SUMMARY`, and a renumbered heading keeps its form.

**The section identity is read in the front matter, once.** The body before
the first PART or article heading is the front matter. A `SECTION 21 05 00`
line, a bare `21 05 00 — TITLE` line, or a cover page's `Section Number:
21 05 00` field found there sets the identity; a SECTION-shaped line after
structure has begun is a provision citing a sibling section, and the first
header found is never overwritten. When the body states nothing, the page
header/footer is consulted last and the import notes disclose it. The map
records where the identity came from (`header_source`: `line`,
`front_matter`, `chrome`, or empty): only a header LINE is anchored and
reproduced; for the other two the identity already sits in content the
export carries through verbatim, so no header element is synthesized and a
renamed section is reported by the `stale_document_identifier` lint instead
(which reads the cover page's text as well as the headers and footers).
Front matter itself is recorded (`ImportResult.front_matter`, the import
report's `front_matter`, `SourceFormatMap.front_matter_text`) and never
modelled: not provisions, not editable, carried through exactly.

**Structure is read the way Word resolves it.** A paragraph's numbering is
its own `w:numPr`, else the `w:numPr` its paragraph style carries through
`w:basedOn` chains — where every MasterSpec-derived office master keeps its
outline. The CSI style names (PRT, ART, PR1..PR5) are the secondary signal
when the resolved numbering promotes nothing. A cached table of contents is
a Word field: its paragraphs are locked `field` blocks, never parsed as
articles. Text-box content is read into the projection (a cover page is
routinely built from text boxes) though the paragraph stays an `image` block.

**Provisions nest SectionFormat's five levels.** Under an article a provision
nests `A.` / `1.` / `a.` / `1)` / `a)` — the PR1..PR5 styles. Each is read
from a typed label (stripped from the text and regenerated positionally on
export: the importer's `_LEVEL_RES` and the model's `_paragraph_label` are
the same five forms), from Word numbering placed relative to the article's
own list, or from the PR*n* style name alone. Content nested deeper than
the fifth level is kept, clamped to the fifth, and named in the import notes
(`nesting deeper than 5 levels — clamped to level 5`, with the provision's
ref and id). Until the fifth level existed, a Word-numbered `a)` was clamped
into the fourth level as a sibling of its `1)`, and a typed `a)` was not
read as a label at all: it became a top-level provision with the letter left
in its text, and the provisions after it were relettered — which the
formatted export of an UNTOUCHED import then wrote into the file. The
normalized export numbers the fifth level with a fifth Word list level
(`lowerLetter`, `%5)`), a single-token `lvlText` like the other four.

**Two limits remain, and are disclosed rather than worked around.**
The formatting of *new* words is inherited from a neighbour: a word typed
over others takes the formatting of the first character it replaced, and an
inserted word the formatting of the character before it — what Word itself
does (at a hyperlink, refined below). Unchanged words keep their own. A
paragraph the splice cannot yet map (a field, content control, comment or
note reference, `w:sym` or drawing inside it — or a hyperlink holding
anything but runs, bookmarks and spelling markers: a nested link, a field)
is still rebuilt from its first run's properties; the export's `export`
diagnostics event counts those fallbacks by reason (`render.fallback`),
which is the evidence the splice's eligibility widens from. And a
revision-bearing paragraph is rewritten rather than cloned, because the
importer showed the Accept-All view and cloning the original markup would
export text the user never saw.

**The splice's rules.** The source paragraph's visible characters are mapped
to the nodes that produce them — `w:t` characters, `w:tab`/`w:ptab` (`\t`),
a text-wrapping `w:br` or a `w:cr` (`\n`), `w:noBreakHyphen` (`-`) —
python-docx `CT_R.text`'s rules, which is what the importer read (the map is
checked against the importer's reading and refused on a mismatch). Words are
diffed without their whitespace, since the importer folded it: whitespace
between two surviving words is the source's; a replaced block keeps the
source whitespace on both sides; an inserted block keeps the source
whitespace before it; a deleted block takes the whitespace after it, or
before it at the end of the paragraph. Bookmarks and `w:proofErr` markers are
never deleted, only positioned; a zero-width run node (`w:lastRenderedPageBreak`,
`w:softHyphen`, a page break) goes with the characters around it, except on
the edge of a deleted span, where it is kept — a page break before a
relettered label does not die with the old letter. Zero-width content sits
between two characters, so an insertion beside it needs a rule: words
inserted before a paragraph's first word go after the zero-width content in
front of that word (a leading page or column break starts the paragraph on a
new page or column, and prepended words join it there rather than being
stranded on the page before; a bookmark opening over the text still wraps
them). Anywhere else, zero-width content stays with what follows it — a break
in front of a later word stays in front of that word, and a break at the end
of a paragraph stays after words appended there. The edit script is a list
of keep / delete / insert steps whose keep and delete ranges partition the
source in order, so rendering keep + insert gives this export and rendering
delete as `w:del` and insert as `w:ins` gives the planned redline — the
redline's Accept All equals this export by construction.

**Hyperlinks are spliced like the rest of the paragraph.** A `w:hyperlink`'s
runs and markers are mapped the way the importer reads the link (its own
runs, python-docx `CT_Hyperlink.text`), each remembering the link it sits
in, so an edit keeps the link — its target, its runs and their formatting —
on every word it did not change, and a provision merely relettered keeps its
link whole. Four rules decide what an edit does at a link, each so a link
never quietly changes what it covers:

* **New words go in a link only when they are wholly its own:** words typed
  over words that all sit in one link stay in it (its display text changed;
  its target did not), and words inserted between two characters of one link
  go in it. Anywhere else — at either edge of a link, or replacing words on
  both sides of one — new words go outside every link: **a link never grows**
  to cover words added beside it.
* **New words never borrow a link's look from outside it.** They take Word's
  formatting source (above) when that character sits where they go — in the
  same link, or outside every link — and otherwise the nearest character that
  does, ties to the earlier one (with none, the first run where they go). A
  word added after a link is not drawn in its blue underline.
* **A link is never split.** A replacement that runs INTO a link from outside
  it (typed over the words before a link and its first word) puts its new
  words in front of the link, at its edge, and the link keeps its other
  words. A link's own zero-width content — a bookmark closing inside it at the
  insertion point, a bookmark opening it ahead of prepended words — stays
  inside it.
* **A link whose words are all deleted is gone** — an empty hyperlink shows
  nothing — unless a bookmark or other marker inside it (never deleted) keeps
  it. An empty hyperlink in the upload is carried where it was.

A paragraph with no hyperlink renders byte for byte as it did before links
were mapped (checked over 7,500 seeded edits, clean and redline): every rule
above is a no-op there.

**Mechanics.** `spec_doc/source_format.py` records, per semantic element, the
source body-child index it came from and whether its label was Word's
(`w:numPr`) or literal text the importer stripped — one anchor per element,
first wins, because a map `from_dict` refuses would fail every later project
save. Beside the anchors it keeps the header/footer text, the front-matter
text and `header_source`. That is all it records — the retained bytes are
the format store. `spec_doc/source_render.py` reads it back: it walks the
current tree into the elements to emit, assigns every unmodelled body child
to exactly one place (leading content of the element below it, a group
bound to its position — everything up to a section break, or what a deleted
element leaves behind — or the trailing content after the last modelled
element), places the position-bound groups, renders each element (clone,
splice through `spec_doc/source_splice.py`, fallback, or kin clone), and
hands the result to `replace_document_xml_raw`. The map is bound to the
upload by SHA-256; read beside different bytes its origin indexes address
whatever now sits there, so the export refuses.

**Preserved blocks are a document-model property**, not a capability report:
`Paragraph.locked` carries a `model.LOCK_REASONS` code, is persisted with the
tree, and survives undo/redo and project files the way `status` does.
`apply_edits` refuses a retype and refuses nesting under one; move, delete and
status changes stay available, because none of them touches the block the
export emits. A locked block takes no SectionFormat label and shifts none of
its siblings' (`model.labelled_paragraphs`, read by both the panel and the
renderer, so the label on screen is the label in the exported file).

**Headers and footers are never rewritten**, which is why a stale section
identifier in one is reported instead: the import captures their text into the
format map, and the `stale_document_identifier` lint rule compares any
MasterFormat-shaped number in them against the live section number. The
remedy named is Word.

**No permission sweep runs.** Capability derivation for this mode is the
`locked` flag, so the quadratic probe sweep and its fail-closed `pending`
state do not apply. The byte-exact machinery below is still reachable at
`?mode=source` for a project that never released the claim, and its own
suites still pin it.

## Redline on your original (tracked changes in the file you imported)

`GET /api/export/docx?redline=master&mode=preserved` returns a copy of the
upload with every change Build-a-Spec made since the import as a native Word
tracked change. It is the appearance-preserving export's redline, and makes
two promises about it:

* **Accept All gives exactly what the appearance-preserving export produces**
  — the file `?mode=preserved` returns for the same document;
* **Reject All gives back the upload's body**, with one exception: a moved
  provision's bookmarks stay with its new position (below).

Both allow one thing more, which Word imposes: when the body's last paragraph
is deleted or added, one of the two resolutions keeps an empty paragraph at
the very end, because a document's last paragraph mark cannot be tracked. The
export makes that paragraph plain (below), so it prints no number, breaks no
page and draws nothing.

"Exactly" means element for element, up to how Word splits text into runs
(Word re-splits runs on every save anyway), with `w14:paraId`/`w14:textId`
treated as identity rather than content. Every package part except
`word/document.xml` is the upload's, byte for byte — `word/settings.xml`
included: Track Changes is not switched on in the file (Decision 4), and a
file whose Track Changes was already on keeps it on. The retained upload is
never modified; this is a new file.

**The file checks its own promise before anyone sees it.** The export renders
the redline and the clean export from one plan, then runs pure-XML Accept All
and Reject All transforms over the redline's body (`spec_doc/revisions.py`,
which shares no code with the writer) and compares them canonically against
the clean body and the upload's body: adjacent runs with identical properties
merged, empty runs and containers dropped, XML comments and processing
instructions ignored, bookmarks compared by name, and a FORMATTING-FREE empty
last paragraph tolerated. Word cannot track a document's last paragraph mark,
so when the last paragraph is deleted or appended, one of the two resolutions
leaves it behind, empty. An empty paragraph still shows whatever it sets (its
number, a page break before it, a border, its line height), so the export
records that paragraph's formatting as a tracked formatting change whose side
in that resolution is empty: what is left sets nothing, and an empty last
paragraph that sets anything is a difference like any other. No bookmark name
may appear twice in the redline, and every other package member must be
byte-identical. If any check fails the route returns a 409 naming it, and the
`export` diagnostics event records the check and the first mismatching
element — its position and element names only, never text.

**Each element is decided against the upload, through the format map.** The
plan the clean export renders (`_Assembler.plan()` — kept, spliced, inserted,
deleted, carried, dropped, and the empty paragraph a displaced section-break
holder leaves) is the same plan the redline renders:

| Plan record | Clean export | Redline |
|---|---|---|
| kept, carried | clone | clone, untracked |
| spliced | the new words in the original runs | `w:del`/`w:ins` inside the original runs |
| fallback (the splice cannot map the paragraph) | the text in one run with the first run's properties | everything the paragraph held deleted, one run of the new text inserted |
| inserted | new paragraph | its runs inserted, its paragraph mark inserted |
| deleted, dropped | nothing | its runs deleted (`w:t` → `w:delText`, `w:instrText` → `w:delInstrText`), its paragraph mark deleted |
| a table inserted or deleted | the table / nothing | every row flagged (`w:trPr/w:ins` or `w:del`), every cell's content marked |
| the body's LAST paragraph, inserted or deleted | as above | its runs marked; its paragraph mark cannot be tracked, so its formatting is recorded instead (`w:pPrChange`, and `w:rPrChange` on the mark) with the empty side where the paragraph goes, and the one empty paragraph Word leaves there is plain |

The word-level changes are the splice's own edit script
(`source_splice.render_redline` beside `render_clean`, over the same pieces),
so a relettered provision is a one-token letter change with its tab kept, and
zero-width content (a page break, a bookmark) lands exactly where the clean
export puts it — including words prepended after a leading page break. A
hyperlink is written once, holding its own pieces: a word changed inside it
is a `w:del`/`w:ins` inside the `w:hyperlink` (a hyperlink cannot sit inside
a tracked change; its runs can), a word added beside it is tracked outside
it, and a link whose words were all deleted keeps its deleted runs, so
Accept All leaves it empty: it shows nothing, exactly as the clean export,
which does not write it (the self-check's comparison drops an empty
hyperlink, since Word shows nothing for one).

**Typed letters are tracked; Word numbering is not.** In a typed-letter master
a provision relettered by an insert above it carries a tracked letter change
(`A.` → `B.`), which is the only way Reject All can give the letters back; it
is noisy near the top of a long article, and honest. In a Word-numbered master
there is no letter text to mark and Word renumbers itself under both Accept
and Reject. This is a deliberate departure from the normalized redline, whose
labels stay positional literals.

**Moves are a deleted copy where it was and an inserted copy where it is.**
The diff sees a reorder (`diff_sections(..., detect_moves=True)`: within each
sibling list, the survivors whose relative order held are the LONGEST
increasing subsequence of their base positions, ties going first to the one
that keeps the most elements in place — an element weighs its whole subtree —
and then to the later position; a moved article carries its children, and in
a swap the heavier subtree stays put). The redline then keeps in place the
largest body-level chain of content both views share, preferring what the
diff did not report moved; everything else is a move. The inserted copy is
cloned from the element's own origin and keeps its bookmarks and `w14` ids;
the deleted old copy gives them up, because a file must never carry one
bookmark name twice — which is the Reject-All exception above: Reject All
restores a moved provision's text and formatting at its old position, but not
its bookmarks (Word recreates `_GoBack` itself, and a TOC update restores
`_Toc` anchors). The self-check excludes exactly those names. Word's own
"Moved" marks are Phase 2.

**No tracked change ever deletes a paragraph mark that holds a section break**
(Accept All would merge two Word sections). A deleted or moved provision that
holds the break in its own `w:pPr` keeps its mark: only its runs are deleted,
so Accept All leaves the same empty break-holding paragraph the clean export
leaves, and Reject All restores the text. When that provision was
Word-numbered, the numbering cancel the clean export applies (`w:numId 0`) is
recorded as a `w:pPrChange`. Breaks are placed by the clean export's own rule
(`_Assembler._place`), and the redline follows it: every record placed around
a break is held in place, and when keeping them all in place forces an extra
move, the redline moves the fewest elements it can (the export event counts
them as `moves_added`). A reorder that would need a break itself to move is
refused (`section_break_reorder`) — Word cannot track a moved section break.

**What is never touched:** front matter, headers and footers, `END OF SECTION`
and everything after it, the section identity when it lives on the cover page
or in a header or footer (the `stale_document_identifier` lint reports a
renamed section there instead). A section identity on a header line in the
body is redlined like any other line. Status changes (assumed → confirmed) are
not content and are not marked, and no Build-a-Spec schedule or QC closing is
added: the file is your document.

**Revision metadata.** Author `Build-a-Spec`; date the export time in UTC
(`2026-09-22T14:30:00Z`); revision ids start above the highest `w:id` already
in the package's `word/*.xml` parts, since bookmarks and comments share that
annotation-id space. Word's schema order is kept: the paragraph-mark flag is
the first child of `w:pPr/w:rPr`, `w:rPr` precedes `w:sectPr` and
`w:pPrChange`, `w:pPrChange` is the last child of `w:pPr` and holds the base
properties only, and a row's flag follows its other `w:trPr` properties. The
file is named `<your upload's name> - REDLINE.docx`, so replacing the master
with the reviewed file is a rename.

**Refusals, each named** (the 409's `code`, and the `export` event's
`refusal.reason`):

| Code | Why |
|---|---|
| `no_baseline` | No imported master in the document's history (the payload says so; the route's no-master 400 answers first). |
| `no_original` | The upload and the format map built from it are not both kept, or no longer describe each other (a project imported before 1.14.0 has no map; importing the file again gives it one). |
| `pending_revisions` | The package already carries another author's tracked changes, so Reject All would reject those too. Accept or reject them in Word, save, and import the file again. Track Changes merely switched ON (`w:trackRevisions`) with nothing pending is not refused. |
| `revision_scan_unavailable` | The revision-bearing parts could not be scanned confidently enough to say there are none. |
| `section_break_reorder` | The change would need a section break to move. |
| `moved_annotation` | A moved provision carries a comment range or reference, or a footnote or endnote reference, and Word cannot show one annotation in two places. |
| `simple_field` | A `w:fldSimple` would need tracking (Word cannot track one). |
| `block_content_control` | A body-level content control would be inserted, deleted or moved. |
| `field_block` | Part of a table of contents (a field spanning paragraphs) would be deleted or moved. |
| `pending_revisions_in_body`, `untrackable_markup` | Revision markup, or other markup that cannot be wrapped in a tracked change, met while marking content. |
| `unaccounted_content` | A body element of the upload could not be placed — refused rather than risk losing it. |
| `accept_check_failed`, `reject_check_failed`, `duplicate_bookmarks`, `package_check_failed` | The self-check. |

Every refusal names the redline of extracted provisions as still working
(`?redline=master&mode=normalized`), except `no_baseline`, which names redline
vs version.

**Cost.** Linear in the document: on a 1,200-paragraph master with forty
edits the whole redline took about 0.7 s, of which the self-check was about
0.3 s (2,400 paragraphs: 1.3 s / 0.6 s; 4,800: 2.4 s / 1.2 s) — about three
times the clean export, which renders once inside it. The pending-revisions
scan (about 0.1 s) is cached per upload for the payload.

**Limits.** A new provision nested deeper than any the master's own numbering
defines keeps its kin's level (see the appearance-preserving export above),
and the redline's Accept All reproduces that by construction. The redline is
master-only in this phase: a redline on the original against an arbitrary
version is a 400.

**In the app.** The Export menu of an imported document offers **Redline on
your original (tracked changes)** right under *Export Word (keeps your
formatting)* and its *Open in Word*, and always names both halves of the
query (`?redline=master&mode=preserved`) — never the bare-redline default.
The item is drawn off `preserved_redline_available`: when that is false it
stays in the menu disabled, and its hover shows `preserved_redline_reason`'s
message verbatim — the client adds no prose of its own to a refusal. A
refusal only a render can reach (a 409 from the render or its self-check)
arrives in the panel's export error strip as the route's own `error`. In the
desktop app, **Open redline in Word** (`js_api.open_in_word("preserved",
"master")`) writes the same export to a fresh temporary file and opens it in
Word; it is offered only while the redline is available. *Redline of
extracted provisions* (`?redline=master&mode=normalized`) stays below them.

### Real Word as the judge

The self-check proves the promise with the app's own resolver.
`tests/test_redline_word_judge.py` has **real Microsoft Word** check the same
files (optional, Windows with Word installed, `BUILD_A_SPEC_WORD_JUDGE=1`;
setup in [DOCX_RENDERER_WINDOWS.md](DOCX_RENDERER_WINDOWS.md)). It renders
every markup shape the writer emits (word-level splices, typed letters, Word
numbering, whole provisions, articles and tables deleted, moves plain and
across section breaks and carrying a bookmark, emptied break holders,
hyperlinks, a field, the fallback, the untrackable last paragraph, a picture,
a leading page break, front matter), and every corpus master under the corpus
sweep's own scripted edit mixes, exactly as the app does. A hidden Word the
harness starts and owns (`tools/render_docx_word.py --resolve`) then opens
each redline read-only, runs Accept All Changes or Reject All Changes, and
saves the result as a new DOCX. The judge requires that:

* Word opened every file and saved every result;
* Word read every change as `Build-a-Spec`'s (each `Revision.Author`, the name
  the Reviewing Pane shows), and none was left after Accept All or Reject All;
* after Accept All the body is the formatted export's, and after Reject All
  the upload's — losing only the bookmarks a moved copy carried.

**The comparison is symmetric.** A Word save rewrites more than it resolves
(formatting spelled its own way, table-look flags, a hyperlink's history
flag), so Word also re-saves the formatted export and the upload, and each
resolution is compared with Word's own save of its reference. Everything a
save rewrites the same way on both sides cancels without any tolerance. What
remains is what a Word save writes on its own, removed from BOTH sides — and
nothing else:

| Tolerance | Why it is not content |
|---|---|
| rsid attributes (`w:rsidR`, `w:rsidRPr`, `w:rsidRDefault`, `w:rsidP`, `w:rsidDel`, `w:rsidTr`, `w:rsidSect`) | Which Word editing session last touched a paragraph, run, row or section. Every save stamps its own session's ids. |
| `w:proofErr` | Where proofing last flagged a word. It holds no content, and depends on whether proofing ran before the save. |
| `w:lastRenderedPageBreak` | Word's layout cache of where a page last broke. A real page break (`w:br`) is still compared. |
| the `_GoBack` bookmark | The Shift+F5 position Word writes after an edit such as Accept All. Every other bookmark, hidden or not, is compared. |

The rest is the self-check's own canonical comparison
(`revisions.first_difference`): runs re-split, empty containers, `w14` ids,
XML comments and processing instructions, a formatting-free empty last
paragraph. The four tolerances live in `tests/word_judge.py` and **never in
the app**: the self-check compares the app's XML with the app's XML, and
`tests/test_word_judge.py` pins that it applies none of them. Each is tested
to remove exactly what it names and nothing more; a fifth needs evidence from
a real run, a reason in the table and a test. A judge that tolerated more
would prove nothing, and one that tolerated less would report differences
that are not there.

**The body is what is compared,** because every tracked change is in it:
every other part of the redline is the upload's byte for byte (the
self-check's package check), and neither resolution changes anything outside
the body. The judge cannot see a repair prompt (alerts are off in an owned
Word, so a file Word refuses to open fails its job instead) or how the
Reviewing Pane draws the changes; those stay manual rows in
`docs/RELEASE_WINDOWS.md`.

It writes a `report.json` — rewritten after every group, so a run that stops
part way still says what it found: each case's status, the redline's counts,
the tolerances applied, Word's version and build, and for a failure both
sides of the first difference — plus every file it handed Word and every file
Word saved, under `artifacts/word-judge/` (`BUILD_A_SPEC_WORD_JUDGE_DIR` moves
it). The cases are placeholder text, so the report may carry it; it never
carries a path. A mix the redline refuses for a structural reason (the corpus
sweep's own list) is recorded and not sent to Word; any other refusal is a
failed self-check and fails the judge.

The same suite has Word resolve **its own** tracked moves — the Word-saved
sample the corpus producer's TrackedMove recipe makes
([DOCX_FIDELITY_CORPUS.md](DOCX_FIDELITY_CORPUS.md)) — and requires the app's
resolver (`revisions.accept_all`/`reject_all`, which Phase 2's native moves
lean on) to give what Word gives, recording where the moved paragraph's
bookmark lands each way.

## Distinct user-visible contracts

| Contract | API selection | Package basis | Guarantee |
|---|---|---|---|
| Exact original | `GET /api/import/original` | Immutable imported bytes | The response is byte-for-byte identical to the retained upload. |
| Exact source no-op | `GET /api/export/docx?mode=source` when the semantic body matches the imported baseline | Immutable imported bytes | Returns the exact same bytes, without rebuilding XML or ZIP. Status, provenance, standards, project-profile, and other metadata-only changes do not make this a body mutation. |
| Source-preserving patched DOCX | `GET /api/export/docx?mode=source` after a proven-safe body change | Clone of the imported package | Only approved `word/document.xml` text slices or numbered-island paragraph spans change. Unchanged member payloads, local records, inter-record gaps, archive comment, and trailing bytes remain exact. Central-directory records change only for the replacement metadata and required local-header offsets. The proposed output is independently audited before return. |
| Appearance-preserving DOCX | `GET /api/export/docx?mode=preserved`, and the default for an imported document that has released the byte-exact claim (i.e. every import) | Clone of the imported package with a rebuilt body | Every package part except `word/document.xml` is byte-identical. Untouched provisions are byte-identical elements; edited ones keep their paragraph properties and their own runs (unchanged words keep their formatting); preserved blocks are verbatim; section breaks survive every edit. See the section above. |
| Normalized DOCX | `GET /api/export/docx?mode=normalized` | Current SectionFormat tree | Generates a new DOCX with Build-a-Spec styles, schedules, and genuine Word automatic numbering. It does not preserve source-package formatting or opaque parts. Fresh projects default to this mode. |
| Redline on your original | `GET /api/export/docx?redline=master&mode=preserved`, and the default for a bare `redline=master` whenever it is available (`preserved_redline_available`) | Clone of the imported package with a rebuilt body | Every package part except `word/document.xml` is byte-identical (Track Changes is not switched on). Every change since the import is a native Word tracked change by "Build-a-Spec", dated at export. Accept All gives exactly the appearance-preserving export; Reject All gives the upload's body back (a moved provision's bookmarks excepted). Both are checked before the file is returned, and a failure is a 409 naming the check. It never adds tracked changes to the retained source. See the section above. |
| Normalized redline | `GET /api/export/docx?redline=master&mode=normalized` (and a bare `redline=master` when the redline on the original is unavailable), or `GET /api/export/docx?redline=version&base=N` | Semantic baseline/version and current SectionFormat tree | Generates a new DOCX containing Word `w:ins`/`w:del` markup. It is a semantic provision redline, not a source-package redline. It never adds tracked changes to the retained source. |

Redline display labels remain positional literal text so a move or a preceding
deletion does not create misleading tracked-numbering noise; they are numbered
the way the panel numbers provisions (`model.labelled_paragraphs`), so a
preserved block takes no letter and shifts no sibling's. Accept-All is
text-faithful to the current semantic document, and Reject-All is text-faithful
to the selected semantic baseline. Clean normalized exports, in contrast, use
genuine Word numbering definitions and `w:numPr` bindings.

`pass_through_only` is a document state, not a sixth export format. In that
state exact-original download and exact source no-op remain available, while
source-backed body mutations are disabled. Status, research provenance,
standards, project-profile, and other workspace metadata may remain editable
because they do not alter the retained Word body. A normalized export remains
an explicit, separate reconstruction choice and must not be described as
preserving the source.

## Detaching a document from its source

Every restriction above exists to serve one promise: the source-preserving
export is a byte-exact clone of the upload. A user who does not need that
promise for a given document can give it up explicitly through `POST
/api/doc/detach-source` ("Edit freely"), after which the document is an
ordinary semantic document and the whole source-backed edit surface is
inactive. This is the supported answer to a package that cannot be patched at
all — `tracked_changes`, `active_content`, `document_protection`,
`signed_package`, or any `unsafe_*` scan failure — and to the ordinary case of
a manually-labelled master, where no provable numbered island exists and every
structural edit therefore fails closed. The panel offers "Edit freely" on
**every** source-attached state — frozen, pending, and the ordinary settled
master alike — because the documented answer to "this document is read-only"
must be reachable from every state that makes it true.

The same one-way door can be taken **at import time**: `POST
/api/import/master` accepts a `detach` form field, and `detach=true` ("use as
a starting point", the panel's recommended default) performs the import and
the detach in one transaction — no turn, edit, or poll ever observes an
attached document, and the per-element permission sweep is never started
(the scope it would describe is already inactive). The default without the
field is byte-compatible with every pre-intent client: attached,
source-preserving, sweep warming. A detach-at-import project is
indistinguishable from import-then-detach everywhere downstream — same
persisted flag, same loader posture, same re-arm on a later attached import.

Detaching drops the claim and none of the evidence, which is what the rest of
the system depends on:

- The retained bytes, the source map, and the imported baseline are all
  **kept**. A `.baspec` validates source, map, and baseline against each other
  on load, so removing a member would produce a file that rejects itself.
- `GET /api/import/original` keeps returning the byte-exact upload.
- `?redline=master` keeps working, because the imported baseline version is
  still present and immutable.
- `GET /api/export/docx` defaults to the appearance-preserving render when
  the formatting map is present, else normalized. An explicit `mode=source`
  returns 409 naming detachment as the reason, because every artifact it would
  otherwise report missing is in fact still there.
- `source_preservation` reports `status: "detached"` (`body_editing:
  "unrestricted"`, the exact original still available) and
  `source_capabilities` is `null`.
- `_doc_payload` carries an explicit `source_detached` boolean, and an
  explicit `preserved_export_available` boolean — the retained original and
  its formatting map are both present and describe each other. It is the
  same derivation the export route selects its default mode by, and it is
  what the panel's Export menu leads with ("Export Word (keeps your
  formatting)"). Clients must **not** infer source scope from the retained
  artifacts: a detached document presents `source_available: true`, a set
  `baseline_index`, and a null capability report, which is indistinguishable
  from a source-backed document whose report has not been delivered.
- **Open in Word** (`js_api.open_in_word(mode)`, desktop shell only) fetches
  the same export route with the launch's own token, writes the file under
  the user's temp folder and opens it with the default `.docx` application;
  every refusal is the server's own message. `open_in_word("preserved",
  "master")` is **Open redline in Word**: the same path for the redline on
  your original (`?redline=master&mode=preserved`). The redline argument is
  a closed vocabulary (`""` or `"master"`) and pairs with `preserved` only;
  anything else is refused before a URL is built.
- Project **loading** validates the retained source, its map, and the imported
  baseline exactly as before, but does not re-impose the per-version
  preservation boundary on a detached project. Exceeding that boundary is what
  detaching is for, so requiring it at load would make every project the
  feature exists for fail to reopen. An attached project is unchanged: every
  retained version from the baseline forward must still fit the boundary, so a
  forged redo version cannot enter the session and become active later.

The decision persists as `source_detached` on the document store, beside
`baseline_index`. It is deliberately not a version snapshot field: it
describes the document's relationship to its source, not its content, so undo
and redo must not flip it. On load, anything but an explicit `true` reads as
attached — a malformed flag must never hand out edit permissions the source
gate would refuse. `adopt_imported` clears it, so importing again is the
supported way back to source preservation.

Detachment is a change of contract, never a change of authority.
`apply_doc_edits` still routes every proposed final state through
`validate_source_transition` whenever the scope is active; detaching removes
the scope rather than bypassing the gate.

## Source-backed edit surface

Source-preserving text replacement is allowed only when one semantic provision
maps to one exact, contiguous source text slice in one ordinary Word text node.
The surrounding paragraph and run markup remains untouched. Tabs, line breaks,
illegal XML characters, unsupported `xml:space` transitions, CDATA, embedded
lexical markup, complex runs, and ambiguous anchors fail closed.

Add, delete, and reorder are allowed only inside a proven structural island:

- direct children of one Word body and one semantic article;
- flat leaf paragraphs, contiguous in the source body;
- at least two source members;
- one genuine direct Word-numbering definition and level;
- an isolated `numId` not used outside the island;
- no opaque barrier, nested content, section break, or manually rendered label;
- for insertion, a surviving source anchor and one unambiguous, allow-listed
  `w:pPr`/`w:rPr` template.

Existing paragraphs move as complete raw OOXML elements. A new paragraph is a
minimal element derived only from the proven local template. Structural edits
never cross a parent, island, table, field, hyperlink, content control, or
other opaque boundary.

Every manual, model, and QC body mutation is checked as a proposed final state
before commit. Undo and redo restore snapshots that passed that gate when they
were created. Native project load independently revalidates every retained
source-backed history version against the exact attached source before the
live session is replaced. Capability reports are UI guidance, not
authorization; forged or stale requests still pass through the final-state
gate.

## API contract

### Document payload

`GET /api/doc` and successful import, edit, undo, redo, project-load, and QC
apply responses include these source-specific fields:

```json
{
  "source_available": true,
  "preserved_export_available": true,
  "preserved_redline_available": true,
  "preserved_redline_reason": null,
  "preservation_ready": true,
  "source_preservation": {
    "status": "ready",
    "source_export_ready": true,
    "exact_original_available": true,
    "body_editing": "bounded",
    "no_op": true,
    "changed_uids": [],
    "blockers": []
  },
  "source_capabilities": {
    "status": "ready",
    "elements": {
      "pt1.a1.p1": {
        "replace_text": {"allowed": true},
        "move": {
          "allowed": true,
          "island_key": "pt1.a1.p1",
          "current_position": 0,
          "minimum_position": 1,
          "maximum_position": 2,
          "allowed_positions": [1, 2]
        }
      }
    }
  }
}
```

`preserved_redline_available` says whether the redline on the original can be
exported; when it cannot, `preserved_redline_reason` is `{code, message}` —
the closed code (`no_baseline`, `no_original`, `pending_revisions`,
`revision_scan_unavailable`) and the sentence to show, server-authored. It is
the same derivation the export route's default and refusal read, so the menu
can never offer a redline the route refuses.

Fresh projects return `null` for `source_preservation` and
`source_capabilities`. A resumed legacy import has no source capability lock:
`source_capabilities` is `null`, while `source_preservation` reports
`unavailable` because its historical import report/baseline exists without
source bytes. The legacy `preservation_ready` boolean is retained for
compatibility. It means the *current state* can be returned through source
mode; it does not mean body editing is allowed. In particular, a
pass-through-only no-op reports `preservation_ready: true` and
`body_editing: "disabled"`.

`source_preservation.status` has four values:

| Status | Meaning |
|---|---|
| `ready` | Source export is currently valid and a bounded body-editing surface may exist. Consult per-operation capabilities. |
| `pass_through_only` | Exact source no-op is valid, but source-backed body editing is disabled by a package-wide or runtime mutation blocker. |
| `blocked` | Source bytes exist, but the current semantic/source state cannot be exported through source mode. |
| `unavailable` | The session represents an import but no exact source artifact is available, as with a resumed legacy JSON project. |
| `detached` | The byte-exact claim was released ("Edit freely", and every import since 1.14.0). Body editing is unrestricted; the appearance-preserving export is the one in use; the exact original stays downloadable. |

`source_export_ready` answers whether the current state can be downloaded in
source mode. `exact_original_available` answers whether retained source bytes
exist. `no_op` is based only on the semantic Word-body projection; workspace
metadata does not change it. `changed_uids` identifies semantic body elements
in the approved patch plan. Each blocker has `uid`, stable `blocker` code, and
canonical server `message`.

### Per-operation capabilities

`source_capabilities` is `null` outside an active imported-source scope —
including a document detached through "Edit freely", which is not source-backed
however many source artifacts it still retains. Otherwise its `status` is
independent of `source_preservation.status` and has four values:

| Status | Meaning |
|---|---|
| `ready` | Source capability analysis completed without a package-wide mutation blocker. Individual body operations may still be denied; consult each operation record. |
| `pass_through_only` | Exact source no-op can remain available, but a package-wide or runtime blocker denies source-body mutations. |
| `blocked` | Required source identity, mapping, baseline, or capability analysis is unavailable or invalid. Body operations fail closed. |
| `pending` | Capability analysis for this exact document state is still running. Body operations fail closed with the `capabilities_pending` blocker; workspace-only metadata operations remain allowed. |

`causes` lists the package-wide reasons the whole document is frozen, in the
closed blocker vocabulary and first-seen order, each with the canonical server
`message` and a user-actionable `remedy`. It is empty on `ready`, and
deliberately empty on `pending` — a sweep still running is not a fault in the
user's file. Every element already carries the same blocker on each denied
operation, but a client cannot otherwise distinguish "this paragraph has
markup we cannot patch" from "the entire package is locked" without scanning
every element and inferring. Naming the cause once is what lets an interface
explain a read-only import, and for the user-fixable causes state what to do
about it. `remedy` is server-authored for the same reason `message` is:
clients must not restate a denial in their own prose.

`pending` is a transient state, not a defect. Capability analysis probes every
element against the authoritative final gate, so it is derived once per
document state on a background worker rather than inline on a request. An
import response, and the first read after any body change, therefore report
`pending` until that analysis lands. Clients poll `GET /api/doc/capabilities`
— which returns only `{"source_capabilities": …}` — and refresh the full
document payload once the status settles. `?status_only=1` returns the slim
polling projection — `status`, `causes`, and, while a sweep is running, a
`progress` object (`done`/`total` elements) — without the per-element map,
which is multi-MB on a large master and is never read by a poll tick.

Two classes of answer are categorical and derived without probing, because
the gate decides them as constants: a frozen package returns the fail-closed
document-wide report directly (proven byte-identical to the swept one), and
heading `replace_text` is reported as the unconditional `heading_change`
denial. Both were previously rediscovered at full per-probe price.

`pending` never widens the edit surface. It denies exactly what `blocked`
denies; the distinction exists only so an interface can say the analysis is
still running instead of describing an undecided permission as permanently
unavailable. This follows directly from the rule that capability reports are
guidance rather than authorization: every body mutation is still validated as
a complete proposed final state before commit, so an operation attempted
against a `pending` report is refused by the gate, never silently accepted.
Operations that act on the analysis rather than merely display it — starting
Final QC, applying QC fixes, and generating QC exports — wait for it instead
of reading `pending`.

`source_capabilities.elements[uid][operation]` is server-derived and has this
shape:

```json
{
  "allowed": false,
  "blocker": "complex_paragraph_markup",
  "message": "the source paragraph contains unsupported paragraph-level markup"
}
```

Allowed structural operations may also carry `island_key`,
`current_position`, exact `allowed_positions`, and one or more `placements`.
A placement contains an `island_key` and exact sibling positions; contiguous
positions additionally expose `minimum_position` and `maximum_position` for
compatibility. Consumers must use the exact positions and must not infer safe
gaps from a min/max range. The advertised positions may be a **subset** of
the theoretically safe set: `move` advertises adjacent positions only (what
the reorder buttons consume — probing every sibling slot was the sweep's
super-quadratic term). This is contract-honest under the rule above:
capability reports are guidance, never authorization, and the gate still
validates any position a request actually carries.

Capability operations include `replace_text`, `add_paragraph`, `delete`, and
`move` for body elements where semantically relevant. Paragraph status and
provenance operations and section-level project/standards metadata can remain
allowed when body operations are blocked.

### Import, export, and project endpoints

| Endpoint | Contract |
|---|---|
| `POST /api/reference/upload` | Bounded text extraction attached as model context, from a `.docx`, `.pdf`, `.txt`, `.xml`, or `.csv`. Not an import: no tree, no retained source, no blank-document precondition, and no effect on any export. Rejects an unsupported extension, a file that is not what its extension claims (an unreadable package, a non-PDF, binary content behind a text extension), a password-protected PDF, a document with no readable text, and an attachment past the per-session cap. |
| `GET /api/references` | Attached reference documents, metadata only — bodies are read by the model through its own tool, never shipped with a payload. |
| `DELETE /api/reference/{rid}` | Detach one reference document; 404 when unknown. |
| `POST /api/import/master` | Bounded, atomic DOCX import. On success, returns import counts/warnings plus the full document payload. A failed import leaves the live session unchanged. Parsing and indexing run on a worker thread, so a long master never blocks the chat stream or any other request; the session is still adopted on the event-loop thread under `session_state_guard()`, which re-checks that the document is still blank. An optional `detach=true` form field performs the Edit-freely detach inside the same transaction ("use as a starting point") and skips the capability sweep entirely; omitted, the behavior is the historical source-preserving default. |
| `GET /api/import/original` | Exact retained source with `Cache-Control: no-store`. Returns 409 for a source-less resumed legacy import and 404 when no import exists. |
| `GET /api/export/docx` | A detached imported project (every import) defaults to `mode=preserved` when its formatting map is present; a project still holding the byte-exact claim defaults to `mode=source`; fresh projects default to normalized. It never silently falls back from source mode. |
| `GET /api/export/docx?mode=source` | Exact no-op or audited source patch. A blocked request returns 409. It cannot be combined with `redline`. |
| `GET /api/export/docx?mode=normalized` | Explicit normalized reconstruction. |
| `GET /api/export/docx?redline=master` | The redline on the original when it is available (`preserved_redline_available`), else the normalized redline against the imported semantic baseline. Requires an imported baseline (400 without one). The frontend never relies on this default: every redline it requests names its mode. |
| `GET /api/export/docx?redline=master&mode=preserved` | The redline on the original. 409 with a named `code` when it is unavailable (`no_original`, `pending_revisions`, `revision_scan_unavailable`) or refused by the render or its self-check (see "Redline on your original"). The filename is `<upload name> - REDLINE.docx`. |
| `GET /api/export/docx?redline=master&mode=normalized` | Normalized redline against the imported semantic baseline. |
| `GET /api/export/docx?redline=version&base=N` | Normalized redline against a retained semantic version (a bare request, or `mode=normalized`). `mode=preserved` with `redline=version` is a 400: the redline on the original is master-only. |
| `GET /api/project/save` | Native `.baspec` package. An imported project includes exact source bytes and a typed source map. |
| `POST /api/project/load` | Legacy format-1 JSON compatibility endpoint. The loaded project is source-less. |
| `POST /api/project/load-file` | Loads native `.baspec` or current legacy JSON after complete side-effect-free validation, then replaces session state atomically. |

Source-preserving failures include a bracketed code in their human-readable
error, for example `...[complex_paragraph_markup]: ...`. API clients should use
`source_preservation.blockers` and `source_capabilities` for decisions rather
than parsing error prose.

### Bounded package limits

The import boundary accepts values at the stated ceiling and rejects the next
byte/member:

| Boundary | Current ceiling |
|---|---:|
| Compressed DOCX upload | 25 MiB |
| DOCX members | 5,000 |
| One uncompressed DOCX member | 100 MiB |
| Total uncompressed DOCX members | 250 MiB |
| Compressed `.baspec` upload | 96 MiB |
| `.baspec` members | 3 fixed members |
| `.baspec` manifest | 64 KiB |
| `.baspec` semantic project JSON | 64 MiB |
| Total uncompressed `.baspec` members | 96 MiB |

These limits bound compressed uploads, decompression, member count, and project
history independently. Unsafe paths, encryption, duplicate fixed project
members, undeclared project members, and required OPC parts that cannot be read
are hard import errors, not pass-through-only states.

## Blocker codes

The canonical user message table lives in
`backend/spec_doc/source_mapping.py:source_blocker_message`. Do not create an
independent policy or message table in the frontend or API layer. The code is
the stable machine value; the accompanying message is the server-owned user
explanation.

### Package-wide and runtime mutation blockers

| Code | Meaning |
|---|---|
| `active_content` | Macros, ActiveX, OLE, or other embedded active content is present. |
| `document_protection` | Word document protection is enabled. |
| `signed_package` | Mutation would invalidate a package signature. |
| `tracked_changes` | Pending Word revisions or revision-bearing related parts are present. Build-a-Spec does not author into that revision graph. |
| `unsafe_relationship_scan` | OPC relationships or content types cannot be inspected unambiguously. An external (`TargetMode="External"`) target is opaque and never trips it — Word writes backslashes and spaces into `file://` links — and a repeated, identical content-type declaration is tolerated; only declarations that disagree are ambiguous. |
| `unsafe_revision_scan` | Revision-bearing Word parts cannot be inspected unambiguously. |
| `unsafe_document_xml` | The main document XML is malformed or lexically unsafe. |
| `unsafe_settings_xml` | Settings XML is malformed or unsafe. |
| `unsupported_word_namespace` | The main document does not expose exactly one supported Word body namespace. |
| `unsupported_source_xml_encoding` | Source mutation supports UTF-8 Word XML only; the source is not transcoded. |
| `unsupported_raw_zip_layout` | The ZIP can be retained exactly but cannot be rebuilt without risking unrelated raw records. |
| `unsafe_package` | The retained package cannot be safely read or indexed for mutation. |

### Text and element blockers

| Code | Meaning |
|---|---|
| `heading_change` | Source mode does not patch section, part, or article headings. |
| `table_projection` | The element is a read-only semantic projection of an opaque table. |
| `complex_paragraph_markup` | Paragraph-level markup is outside the supported text-patch shape. |
| `complex_run_markup` | Multiple or unsupported inline runs make the text slice ambiguous. |
| `not_direct_body_paragraph` | The provision is not a direct body paragraph. |
| `section_break_paragraph` | The paragraph carries section-layout properties. |
| `noncontiguous_visible_text` | Visible provision text is not one contiguous Word text node. |
| `normalized_text_not_exact_slice` | Extracted text is not one exact source-text slice. |
| `unsupported_source_text_lexical_form` | CDATA or embedded lexical markup prevents a byte-local patch. |
| `unsupported_text_control` | A tab or line break would require unsupported run markup. |
| `unsupported_edge_whitespace` | Leading/trailing whitespace would require changing `xml:space` metadata. |
| `invalid_xml_character` | Replacement text contains a character XML cannot represent. |
| `unmapped_paragraph` | No validated source binding exists for the semantic paragraph. |

### Structural blockers

| Code | Meaning |
|---|---|
| `automatic_numbering_required` | Structural editing requires genuine direct Word numbering. |
| `ambiguous_structural_insert` | An insertion is not unambiguously inside one surviving numbered island. |
| `ambiguous_structural_template` | No single allow-listed paragraph/run template can be proven for synthesis. |
| `cross_island_move` | A move crosses an island, parent, or opaque boundary. |
| `cross_parent_structural_change` | A provision is reparented or moved between articles. |
| `manual_label_structural_change` | Literal labels cannot be renumbered safely. |
| `mixed_numbering_island` | Candidate members use different numbering definitions or levels. |
| `nested_structural_change` | Nested subtrees are being added, removed, moved, or reparented. |
| `noncontiguous_structural_island` | Candidate paragraphs are not contiguous direct body siblings. |
| `numbering_instance_not_isolated` | The `numId` is used outside the candidate island. |
| `structural_change` | The requested structure lies outside the proven structural surface. |
| `unsafe_structural_island` | The proposed final state crosses content outside one safe island. |

### Identity and output-audit blockers

These normally indicate stale, corrupted, incompatible, or internally
inconsistent source state rather than an ordinary unsupported user action.
They must fail closed and must never trigger normalized fallback.

| Code | Meaning |
|---|---|
| `source_unavailable` / `baseline_unavailable` | Required exact source bytes, source map, or imported semantic baseline is absent. |
| `source_hash_mismatch` / `document_hash_mismatch` | Retained bytes no longer match import-time identity. |
| `source_map_mismatch` / `baseline_mismatch` | Persisted mapping, cached context, or semantic baseline no longer agrees. |
| `body_anchor_mismatch` / `text_anchor_mismatch` | An immutable body or text anchor no longer resolves exactly. |
| `invalid_xml_patch` | A composed lexical byte-patch record has invalid bounds, types, or replacement data. |
| `overlapping_xml_patches` | Two proposed lexical patches overlap or target the same unsupported region. |
| `out_of_scope_document_xml_changed` | Document/body metadata or XML outside the approved body surface changed. |
| `body_structure_changed` / `unexpected_body_change` | The composed body differs from the approved final-state plan. |
| `section_properties_moved` | Final Word section properties no longer occupy their required terminal position. |
| `part_inventory_changed` / `out_of_scope_part_changed` | Package members or an opaque member payload changed. |
| `unexpected_document_xml` | The cloned package does not contain the approved lexical XML result. |
| `output_validation_failed` | The independent XML, package, or raw-ZIP output audit failed. |

When adding a blocker, update the canonical server message, this reference,
capability tests, and the relevant final-state/export test together. Never
weaken a blocker merely to make a new fixture editable.

## Persistence and compatibility

The current native package is `.baspec` package format 1. Its fixed members
are:

```text
manifest.json
project.json
source/original.docx    # optional; present for a source-backed import
```

The manifest records size and SHA-256 for the semantic project and optional
source. The semantic project is project format 1. A source-backed project also
contains source-map format 1 in `project.json`; raw source bytes are forbidden
inside that JSON.

Compatibility rules:

- keep package, project, and source-map format 1 while the serialized contract
  remains compatible;
- continue reading current P1/P1b `.baspec` files and current legacy format-1
  JSON projects;
- reject unsupported future format numbers explicitly and atomically;
- never ignore or silently drop a declared exact source member;
- validate source bytes, manifest hashes, source map, imported baseline, and
  every retained history version before replacing the live session;
- never serialize `SourcePatchContext`, lxml trees, raw lexical byte offsets,
  or a raw-ZIP index;
- recompute lexical indexes, raw-ZIP indexes, and the immutable patch context
  from retained source bytes after load, then identity-check them against the
  source map and baseline;
- if a future source-map schema is genuinely incompatible, bump its format and
  add an explicit reader/migration path for format 1 rather than reinterpreting
  old fields.

Legacy JSON contains no binary source member. Its semantic history can still
load and use the ordinary semantic edit/normalized-export path, but
exact-original and source-preserving export are unavailable.

## Developer architecture

The preservation path is intentionally one directional pipeline:

```text
bounded upload
  -> ZIP/OPC safety inspection
  -> semantic extraction + immutable source map
  -> derived SourcePatchContext
  -> per-operation capability probes
  -> authoritative final-state validation
  -> lexical word/document.xml patch
  -> raw ZIP clone/replacement
  -> decompressed-package + raw-record + semantic output audits
  -> response
```

Important modules:

- `backend/spec_doc/source_package.py`: upload limits, safe package inspection,
  exact-source import report, and required OPC checks.
- `backend/spec_doc/importer.py`: semantic extraction and source binding.
- `backend/spec_doc/source_mapping.py`: serialized source-map format, global
  blockers, paragraph bindings, and canonical blocker messages.
- `backend/spec_doc/xml_lexical.py`: encoding-aware lexical index and byte-local
  XML patches.
- `backend/spec_doc/raw_zip.py`: strict raw-record indexing and replacement.
- `backend/spec_doc/source_audit.py`: bounded decompressed-package comparison.
- `backend/spec_doc/source_patch.py`: derived context, capability probes,
  authoritative final-state gate, patch composition, and output audits.
- `backend/spec_doc/project.py` and `project_package.py`: semantic compatibility,
  bounded native package, and atomic load validation.
- `backend/llm/conversation.py` and `backend/app.py`: shared turn/session
  ownership so manual, model, QC, history, reset, import, and load cannot race
  around the source gate.

`SourcePatchContext` is immutable, derived, process-local cache state. Public
gates still receive source bytes, source map, and baseline and reject stale or
mismatched context. Capability probing builds disposable candidates and must
not mutate source bytes, current document, baseline, context, session history,
or version position.

## Privacy-safe diagnostics

There is no remote fidelity telemetry requirement. If optional diagnostics are
added, they may aggregate only coarse data such as app version, preservation
status, blocker code, and count. A privacy-safe diagnostic must never include:

- document, chat, finding, or replacement text;
- raw, escaped, normalized, or reconstructed OOXML;
- DOCX or `.baspec` bytes;
- source-map spans, raw ZIP records, relationships, or package member payloads;
- filenames, filesystem paths, client/project identifiers, URLs, element IDs,
  prompt content, or stable document hashes.

Apply suppression before data leaves the fidelity boundary; post-collection
redaction is not an acceptable substitute. Aggregation must not include free
form exception details because those may contain package names or document
content.

The existing local session trace is a separate developer-forensics feature and
is **not** privacy-safe telemetry. Default and especially deep traces can
contain document text or prompts. Treat trace directories as sensitive project
data: do not upload, attach, or share them without an explicit content review.

## Test fixtures and release evidence

The fixture layers have different evidentiary value:

- `tests/docx_fidelity_helpers.py` builds small, deterministic package shapes
  for source-map, lexical, numbering, relationship, and raw-ZIP tests.
- `tests/test_source_preserving_export.py`,
  `test_source_structural_export.py`, `test_source_xml_lexical.py`,
  `test_raw_zip_clone.py`, `test_source_global_blockers.py`, and
  `test_source_capabilities.py` cover the core fidelity contract.
- `tests/test_project_package.py` and source-history/context tests cover
  persistence, identity, compatibility, and atomic failure.
- `tests/test_chunk8_opc_adversarial.py`,
  `test_chunk8_raw_zip_paths.py`, `test_chunk8_lexical_adversarial.py`,
  `test_chunk8_limits_history.py`, and `test_chunk8_stress_concurrency.py`
  cover adversarial package ambiguity, exact limits, long histories, and
  concurrent lifecycle activity.
- `tests/fixtures/docx_corpus/manifest.json` plus `tests/docx_corpus.py` provide
  the provenance-explicit structural corpus described in
  [DOCX_FIDELITY_CORPUS.md](DOCX_FIDELITY_CORPUS.md).
- `tests/test_docx_visual_regression.py` provides optional renderer-backed
  evidence; setup is documented in
  [DOCX_RENDERER_WINDOWS.md](DOCX_RENDERER_WINDOWS.md).
- `tests/test_redline_word_judge.py` (optional, Windows with Word) has real
  Word resolve the redline on your original both ways and compares the result
  with the formatted export and the upload; `tests/word_judge.py` is its
  comparison, proven without Word by `tests/test_word_judge.py`. See
  "Real Word as the judge" above.

Synthetic metadata profiles are structural fixtures, not proof that a named
producer opened or rendered the file. An external fixture counts as producer
evidence only when the manifest records its real producer/version, production
method, checksum, modified parts, sanitization, and privacy review. Never add
client-origin or proprietary content merely to broaden coverage.

For release closure, run the complete backend suite, frontend capability tests,
and production frontend build. Materialize and validate the DOCX corpus. Run
Word and/or LibreOffice visual regression when those renderers are available,
and the real-Word judge when Word is, and report exactly which
renderer/version was exercised; do not imply visual verification that did not
occur.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider
Push-Location .\frontend
npm test
npm run build
Pop-Location
& '.\.venv\Scripts\python.exe' -m tests.docx_corpus .\artifacts\docx-corpus
```

The required result is not that every fixture becomes editable. The required
result is that every supported mutation stays within its proven byte surface,
every ambiguity narrows to pass-through-only, and exact-original recovery is
never silently lost.
