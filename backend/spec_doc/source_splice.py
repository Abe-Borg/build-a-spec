"""The word-level splice: an edit written into a paragraph's original runs.

Redline on your original, D-2 (``docs/plans/REDLINE_ON_ORIGINAL_2026-09-22
.md``), built in Phase 0 for the appearance-preserving export and shared
with the Phase 1 redline.

Word stores a paragraph as runs, and a bolded phrase is a run boundary. The
export used to rebuild an edited provision from its first run's properties,
so every edit — and every provision merely RELETTERED because a sibling was
added above it — lost its bold and italic phrases and the tab after its
typed letter. The splice keeps the paragraph's own runs instead:

1. **Map characters to runs** (:func:`map_paragraph`). Every visible
   character of the source paragraph comes from exactly one node: a
   ``w:t`` character, a ``w:tab``/``w:ptab`` (``"\\t"``), a text-wrapping
   ``w:br`` or a ``w:cr`` (``"\\n"``), a ``w:noBreakHyphen`` (``"-"``). Those
   are python-docx ``CT_R.text``'s rules, which is what the importer read,
   so the map and the imported text cannot disagree — and the map is checked
   against the importer's reading anyway.
2. **Diff words** (:func:`plan_splice`). Source words against the new text's
   words with the same ``SequenceMatcher`` approach as
   ``diffing.token_runs``. Words are compared WITHOUT their whitespace: the
   importer folded it, so a double space or a tab in the source is not an
   edit.
3. **Emit** (:func:`render_clean`). Kept words are the original runs, split
   at word boundaries where needed with their ``w:rPr`` copied — which is
   what keeps a bold phrase bold. Source whitespace survives beside the
   words it separated, so the tab after a letter stays a tab. New words get
   the formatting Word itself would give them: a word typed over a
   selection takes the formatting of the first character it replaced; a
   word inserted takes the formatting of the character before it.

The edit script is a list of :class:`SpliceOp` over the SOURCE characters:
``keep`` and ``delete`` ranges that partition them in order, plus
``insert`` texts. The clean export renders keep + insert; Phase 1's redline
renders the same script with ``delete`` as ``w:del`` and ``insert`` as
``w:ins`` — which is how "Accept All equals the formatted export" holds by
construction. One placement rule lives in the rendering rather than the
script, and the redline must share it: words inserted before a paragraph's
first word go after the zero-width content in front of that word (a leading
page break, a bookmark opening over the text); see :func:`_pieces`.

Eligibility is deliberately conservative (widen with corpus evidence, per
the plan): a plain paragraph whose children are runs, hyperlinks, bookmarks
and ``w:proofErr`` markers, and whose runs hold only properties and the text
nodes above (plus the zero-width layout markers Word writes everywhere). A
field, content control, comment or note reference, ``w:sym``, drawing or
pending revision — or a hyperlink holding anything but runs and those
markers (a nested link, a field) — sends the paragraph to the fallback,
which the caller owns; :func:`map_paragraph` names the reason so diagnostics
can count them.

**Hyperlinks** are mapped like the paragraph around them: a link's runs and
markers are atoms that remember the link they sit in, the same way the
importer reads the link's text (python-docx ``CT_Hyperlink.text``: its own
runs, in order). An edit therefore keeps the link, its runs and their
formatting on every word it did not change. Four rules decide what an edit
does at a link, each so a link never quietly changes what it covers:

* **New words go in a link only when they are wholly its own.** Words
  typed over words that all sit in one link stay in it (its display text
  changed; its target did not), and words inserted between two characters
  of one link go in it. Anywhere else — at a link's edge, or replacing words
  on both sides of it — new words go OUTSIDE every link: a link never grows
  to cover words the user added beside it.
* **New words never borrow a link's look from outside it.** They take the
  formatting of the character Word would use (above) when that character
  sits where they go — in the same link, or outside every link — and
  otherwise the formatting of the nearest character that does, ties to the
  earlier one; with none, that of the first run where they go. So a word
  added after a link is not drawn in its blue underline.
* **A link is never split.** A replacement that runs INTO a link from
  outside it puts its new words in front of the link, at its edge; and a
  link's zero-width content (a bookmark closing inside it) stays inside it
  beside new words that are not (:func:`_pieces`).
* **A link whose words are all deleted is gone** from the clean rendering —
  an empty hyperlink shows nothing — unless a marker inside it (a bookmark,
  never deleted) keeps it. The redline leaves the link holding its deleted
  runs, so Accept All leaves it empty, which is the same thing.

A paragraph with no hyperlink renders byte for byte as it did before links
were mapped: every rule above is a no-op there.
"""

from __future__ import annotations

import copy
import difflib
from dataclasses import dataclass

from docx.oxml.ns import qn
from lxml import etree

from .xml_text import xml_safe_text

OP_KEEP = "keep"
OP_DELETE = "delete"
OP_INSERT = "insert"

#: A paragraph the splice cannot map. Closed vocabulary — diagnostics count
#: these to decide what to widen next, and never carry provision text.
FALLBACK_NOT_PARAGRAPH = "not_paragraph"
FALLBACK_REVISIONS = "revisions"
FALLBACK_HYPERLINK = "hyperlink"
FALLBACK_FIELD = "field"
FALLBACK_CONTENT_CONTROL = "content_control"
FALLBACK_COMMENT = "comment"
FALLBACK_NOTE_REFERENCE = "note_reference"
FALLBACK_SYMBOL = "symbol"
FALLBACK_DRAWING = "drawing"
FALLBACK_MARKUP = "other_markup"
FALLBACK_TEXT_MISMATCH = "text_mismatch"

FALLBACK_REASONS = frozenset(
    {
        FALLBACK_NOT_PARAGRAPH,
        FALLBACK_REVISIONS,
        FALLBACK_HYPERLINK,
        FALLBACK_FIELD,
        FALLBACK_CONTENT_CONTROL,
        FALLBACK_COMMENT,
        FALLBACK_NOTE_REFERENCE,
        FALLBACK_SYMBOL,
        FALLBACK_DRAWING,
        FALLBACK_MARKUP,
        FALLBACK_TEXT_MISMATCH,
    }
)

_W_P = qn("w:p")
_W_PPR = qn("w:pPr")
_W_R = qn("w:r")
_W_RPR = qn("w:rPr")
_W_T = qn("w:t")
_W_DEL_TEXT = qn("w:delText")
_W_TAB = qn("w:tab")
_W_PTAB = qn("w:ptab")
_W_BR = qn("w:br")
_W_CR = qn("w:cr")
_W_NO_BREAK_HYPHEN = qn("w:noBreakHyphen")
_W_HYPERLINK = qn("w:hyperlink")
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

#: Paragraph-level markers the splice keeps in place. They carry no text, so
#: they are never deleted by an edit — only positioned.
_PARAGRAPH_MARKERS = frozenset(
    {qn("w:bookmarkStart"), qn("w:bookmarkEnd"), qn("w:proofErr")}
)
_TEXT_NODES = frozenset(
    {_W_T, _W_TAB, _W_PTAB, _W_BR, _W_CR, _W_NO_BREAK_HYPHEN}
)
#: Run content with no visible character of its own: a layout cache Word
#: writes on every save, and an optional hyphen. They travel with their
#: neighbours rather than disqualifying the paragraph.
_ZERO_WIDTH_NODES = frozenset(
    {qn("w:lastRenderedPageBreak"), qn("w:softHyphen")}
)

_REASON_BY_TAG = {
    # Only a hyperlink NESTED in a hyperlink reaches this: a paragraph's own
    # links are mapped (see the module docstring).
    qn("w:hyperlink"): FALLBACK_HYPERLINK,
    qn("w:fldSimple"): FALLBACK_FIELD,
    qn("w:fldChar"): FALLBACK_FIELD,
    qn("w:instrText"): FALLBACK_FIELD,
    qn("w:delInstrText"): FALLBACK_FIELD,
    qn("w:sdt"): FALLBACK_CONTENT_CONTROL,
    qn("w:commentRangeStart"): FALLBACK_COMMENT,
    qn("w:commentRangeEnd"): FALLBACK_COMMENT,
    qn("w:commentReference"): FALLBACK_COMMENT,
    qn("w:annotationRef"): FALLBACK_COMMENT,
    qn("w:footnoteReference"): FALLBACK_NOTE_REFERENCE,
    qn("w:endnoteReference"): FALLBACK_NOTE_REFERENCE,
    qn("w:footnoteRef"): FALLBACK_NOTE_REFERENCE,
    qn("w:endnoteRef"): FALLBACK_NOTE_REFERENCE,
    qn("w:sym"): FALLBACK_SYMBOL,
    qn("w:drawing"): FALLBACK_DRAWING,
    qn("w:pict"): FALLBACK_DRAWING,
    qn("w:object"): FALLBACK_DRAWING,
    qn("w:ins"): FALLBACK_REVISIONS,
    qn("w:del"): FALLBACK_REVISIONS,
    qn("w:moveFrom"): FALLBACK_REVISIONS,
    qn("w:moveTo"): FALLBACK_REVISIONS,
    qn("w:delText"): FALLBACK_REVISIONS,
    qn("w:moveFromRangeStart"): FALLBACK_REVISIONS,
    qn("w:moveFromRangeEnd"): FALLBACK_REVISIONS,
    qn("w:moveToRangeStart"): FALLBACK_REVISIONS,
    qn("w:moveToRangeEnd"): FALLBACK_REVISIONS,
}


# ---------------------------------------------------------------------------
# The edit script (pure: text in, operations out)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpliceOp:
    """One step of an edit script over the source paragraph's characters.

    ``keep``/``delete`` cover the source range ``[start, end)``; the ranges
    of all keep and delete steps partition the source text in order.
    ``insert`` carries new ``text`` and ``style_at``, the source character
    whose run formatting the new text takes (``-1``: none to borrow).
    """

    op: str
    start: int = 0
    end: int = 0
    text: str = ""
    style_at: int = -1


def word_spans(text: str) -> list[tuple[int, int]]:
    """``[start, end)`` of every word, where a word is a maximal run of
    characters for which ``str.isspace()`` is false — ``str.split()``'s
    definition, which is the importer's whitespace fold."""
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for index, char in enumerate(text):
        if char.isspace():
            if start is not None:
                spans.append((start, index))
                start = None
        elif start is None:
            start = index
    if start is not None:
        spans.append((start, len(text)))
    return spans


class _Script:
    def __init__(self) -> None:
        self.ops: list[SpliceOp] = []

    def _range(self, kind: str, start: int, end: int) -> None:
        if start >= end:
            return
        if self.ops and self.ops[-1].op == kind and self.ops[-1].end == start:
            self.ops[-1] = SpliceOp(kind, self.ops[-1].start, end)
        else:
            self.ops.append(SpliceOp(kind, start, end))

    def keep(self, start: int, end: int) -> None:
        self._range(OP_KEEP, start, end)

    def delete(self, start: int, end: int) -> None:
        self._range(OP_DELETE, start, end)

    def insert(self, text: str, style_at: int) -> None:
        if text:
            self.ops.append(SpliceOp(OP_INSERT, text=text, style_at=style_at))


def plan_splice(source: str, target: str) -> list[SpliceOp]:
    """The edit script that turns ``source`` into ``target``, word by word.

    Whitespace rules, each chosen so an edit changes words and nothing else:

    * whitespace between two words that both survive is the SOURCE's (a tab
      or a double space is formatting, not wording);
    * a replaced block keeps the source whitespace on both sides of it — so
      relettering "A.<tab>" to "B." keeps the tab;
    * an inserted block keeps the source whitespace before it and takes the
      new text's own spacing after it;
    * a deleted block takes the whitespace AFTER it along (the whitespace
      before it now separates the survivors), except at the end of the
      paragraph, where it takes the whitespace before it so no trailing gap
      is left;
    * whitespace before the first word and after the last one is kept.
    """
    source_spans = word_spans(source)
    target_spans = word_spans(target)
    script = _Script()
    count = len(source_spans)
    if count == 0:
        script.keep(0, len(source))
        if target_spans:
            script.insert(
                target[target_spans[0][0] : target_spans[-1][1]],
                len(source) - 1 if source else -1,
            )
        return script.ops

    source_words = [source[start:end] for start, end in source_spans]
    target_words = [target[start:end] for start, end in target_spans]
    script.keep(0, source_spans[0][0])
    opcodes = difflib.SequenceMatcher(
        None, source_words, target_words, autojunk=False
    ).get_opcodes()
    last = len(opcodes) - 1
    for index, (tag, i1, i2, j1, j2) in enumerate(opcodes):
        final = index == last
        if tag == "equal":
            script.keep(source_spans[i1][0], source_spans[i2 - 1][1])
            if final:
                script.keep(source_spans[i2 - 1][1], len(source))
            elif i2 < count:
                gap = (source_spans[i2 - 1][1], source_spans[i2][0])
                following = opcodes[index + 1]
                if following[0] == "delete" and following[2] == count:
                    script.delete(*gap)
                else:
                    script.keep(*gap)
        elif tag == "replace":
            script.delete(source_spans[i1][0], source_spans[i2 - 1][1])
            script.insert(
                target[target_spans[j1][0] : target_spans[j2 - 1][1]],
                source_spans[i1][0],
            )
            end = source_spans[i2][0] if i2 < count else len(source)
            script.keep(source_spans[i2 - 1][1], end)
        elif tag == "delete":
            script.delete(source_spans[i1][0], source_spans[i2 - 1][1])
            if i2 < count:
                script.delete(source_spans[i2 - 1][1], source_spans[i2][0])
            else:
                script.keep(source_spans[i2 - 1][1], len(source))
        else:  # insert
            if i1 == 0:
                end = (
                    target_spans[j2][0]
                    if j2 < len(target_spans)
                    else target_spans[j2 - 1][1]
                )
                script.insert(target[target_spans[j1][0] : end], 0)
            elif i1 == count:
                script.insert(
                    target[target_spans[j1 - 1][1] : target_spans[j2 - 1][1]],
                    source_spans[count - 1][1] - 1,
                )
                if final:
                    script.keep(source_spans[count - 1][1], len(source))
            else:
                script.insert(
                    target[target_spans[j1][0] : target_spans[j2][0]],
                    source_spans[i1][0] - 1,
                )
    return script.ops


# ---------------------------------------------------------------------------
# The character map
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Atom:
    """One node of the paragraph's content, in document order.

    ``run`` indexes :attr:`ParagraphMap.runs`, or is ``-1`` for a marker
    (a bookmark, a ``w:proofErr``, an XML comment, an empty hyperlink).
    ``text`` is what the node contributes to the visible text (``""`` for a
    zero-width node); ``start`` is its offset. ``link`` indexes
    :attr:`ParagraphMap.links` — the hyperlink the node sits in — or is
    ``-1`` outside every link.
    """

    run: int
    node: object
    text: str
    start: int
    link: int = -1


@dataclass(frozen=True)
class ParagraphMap:
    element: object
    runs: tuple
    atoms: tuple[_Atom, ...]
    text: str
    #: The paragraph's hyperlinks (``w:hyperlink``) in document order; an
    #: atom's ``link`` indexes this.
    links: tuple = ()
    #: Per run of :attr:`runs`, the link it sits in (``-1``: none).
    run_links: tuple[int, ...] = ()
    #: Per character of :attr:`text`, the link it sits in (``-1``: none).
    char_links: tuple[int, ...] = ()

    def run_properties_at(self, offset: int):
        """The ``w:rPr`` of the run that produced the character at
        ``offset``; with no such character, the first run's."""
        for atom in self.atoms:
            if atom.run >= 0 and atom.text and atom.start <= offset < atom.start + len(atom.text):
                return self.runs[atom.run].find(_W_RPR)
        for run in self.runs:
            return run.find(_W_RPR)
        return None

    def first_run_properties(self, link: int):
        """The ``w:rPr`` of the first run in ``link`` (``-1``: outside every
        link) — where new words find their formatting when no character sits
        where they go. In a paragraph with no hyperlink this is the first
        run's, which is what :meth:`run_properties_at` falls back to."""
        for index, run in enumerate(self.runs):
            owner = self.run_links[index] if index < len(self.run_links) else -1
            if owner == link:
                return run.find(_W_RPR)
        return None

    def link_at(self, offset: int) -> int:
        """The link the character at ``offset`` sits in: ``-1`` for none,
        and for an offset before the first character or past the last."""
        if 0 <= offset < len(self.char_links):
            return self.char_links[offset]
        return -1

    def link_start(self, link: int) -> int:
        """The offset of ``link``'s first character (it has one)."""
        return self.char_links.index(link)


def _node_text(node) -> str:
    """python-docx ``CT_R.text``'s contribution for one run child."""
    tag = node.tag
    if tag == _W_T:
        return node.text or ""
    if tag in (_W_TAB, _W_PTAB):
        return "\t"
    if tag == _W_CR:
        return "\n"
    if tag == _W_NO_BREAK_HYPHEN:
        return "-"
    if tag == _W_BR:
        kind = node.get(qn("w:type"))
        return "\n" if kind in (None, "textWrapping") else ""
    return ""


def _reason(tag) -> str:
    if not isinstance(tag, str):
        return FALLBACK_MARKUP
    return _REASON_BY_TAG.get(tag, FALLBACK_MARKUP)


class _MapBuilder:
    """Collects a paragraph's atoms, runs and links, in document order."""

    def __init__(self) -> None:
        self.runs: list = []
        self.run_links: list[int] = []
        self.atoms: list[_Atom] = []
        self.links: list = []
        self.char_links: list[int] = []
        self.offset = 0

    def marker(self, node, link: int = -1) -> None:
        self.atoms.append(_Atom(-1, node, "", self.offset, link))

    def run(self, run, link: int) -> str:
        """Map one run; ``""``, or the fallback reason it is refused for."""
        run_index = len(self.runs)
        self.runs.append(run)
        self.run_links.append(link)
        for node in run:
            node_tag = node.tag
            if node_tag == _W_RPR:
                continue
            if node_tag in _TEXT_NODES:
                text = _node_text(node)
            elif node_tag in _ZERO_WIDTH_NODES:
                text = ""
            else:
                return _reason(node_tag)
            self.atoms.append(_Atom(run_index, node, text, self.offset, link))
            self.offset += len(text)
            self.char_links.extend([link] * len(text))
        return ""

    def hyperlink(self, element) -> str:
        """Map a paragraph's ``w:hyperlink``: its runs and markers, which is
        what python-docx ``CT_Hyperlink.text`` (the importer's reading)
        reads. Anything else inside it — a nested link, a field, a content
        control — is refused by name."""
        if len(element) == 0:
            # An empty hyperlink shows nothing: carried in place, like a
            # marker, rather than silently dropped from an edited paragraph.
            self.marker(element)
            return ""
        link = len(self.links)
        self.links.append(element)
        for child in element:
            tag = child.tag
            if not isinstance(tag, str) or tag in _PARAGRAPH_MARKERS:
                self.marker(child, link)
                continue
            if tag != _W_R:
                return _reason(tag)
            reason = self.run(child, link)
            if reason:
                return reason
        return ""

    def build(self, element, text: str) -> ParagraphMap:
        return ParagraphMap(
            element,
            tuple(self.runs),
            tuple(self.atoms),
            text,
            tuple(self.links),
            tuple(self.run_links),
            tuple(self.char_links),
        )


def map_paragraph(element, *, expected_text: str | None = None):
    """``(ParagraphMap, "")`` for a splice-eligible paragraph, else
    ``(None, reason)`` with a :data:`FALLBACK_REASONS` code.

    ``expected_text`` is the importer's reading of the paragraph; when
    given, a map that disagrees with it is refused (``text_mismatch``)
    rather than trusted.
    """
    if getattr(element, "tag", None) != _W_P:
        return None, FALLBACK_NOT_PARAGRAPH
    builder = _MapBuilder()
    for child in element:
        tag = child.tag
        if not isinstance(tag, str):
            # An XML comment or processing instruction: carried in place.
            builder.marker(child)
            continue
        if tag == _W_PPR:
            continue
        if tag in _PARAGRAPH_MARKERS:
            builder.marker(child)
            continue
        if tag == _W_HYPERLINK:
            reason = builder.hyperlink(child)
        elif tag == _W_R:
            reason = builder.run(child, -1)
        else:
            reason = _reason(tag)
        if reason:
            return None, reason
    text = "".join(atom.text for atom in builder.atoms)
    if expected_text is not None and text != expected_text:
        return None, FALLBACK_TEXT_MISMATCH
    return builder.build(element, text), ""


# ---------------------------------------------------------------------------
# The clean rendering (keep + insert)
# ---------------------------------------------------------------------------


def append_text(run, text: str) -> None:
    """Append ``text`` to ``run`` as Word markup.

    Tabs and line breaks are real Word nodes, not characters: a provision
    carrying them would otherwise export as a literal control character.
    Characters XML 1.0 cannot carry are written as visible escapes, the
    posture of every other exporter (``xml_text``).
    """
    text = xml_safe_text(text)
    for index, segment in enumerate(text.split("\t")):
        if index:
            etree.SubElement(run, _W_TAB)
        for line_index, line in enumerate(segment.split("\n")):
            if line_index:
                etree.SubElement(run, _W_BR)
            if not line:
                continue
            node = etree.SubElement(run, _W_T)
            node.text = line
            if line != line.strip():
                node.set(_XML_SPACE, "preserve")


def _text_node(text: str):
    node = etree.Element(_W_T)
    node.text = text
    if text != text.strip():
        node.set(_XML_SPACE, "preserve")
    return node


@dataclass(frozen=True)
class _Placement:
    """Where one insert step's new words go, decided once for both
    renderings (the hyperlink rules of the module docstring).

    ``link`` is the link they go in (``-1``: outside every link). ``style``
    is the source character whose run formatting they take (``-1``: the
    first run where they go). ``split``, when positive, is the source offset
    they are written at instead of after the deletion before them: the start
    of the link that deletion runs into, which they are not in.
    """

    link: int
    style: int
    split: int = -1


def _nearest(char_links: tuple[int, ...], offset: int, link: int) -> int:
    """The character nearest ``offset`` that sits in ``link`` (ties to the
    earlier one), or ``-1`` when none does."""
    count = len(char_links)
    if 0 <= offset < count and char_links[offset] == link:
        return offset
    for distance in range(1, count + 1):
        for candidate in (offset - distance, offset + distance):
            if 0 <= candidate < count and char_links[candidate] == link:
                return candidate
    return -1


def _place_new_words(
    pmap: ParagraphMap, op: SpliceOp, position: int, previous: SpliceOp | None
) -> _Placement:
    """The placement of ``op``'s new words, which land at source offset
    ``position`` after the step ``previous``.

    A paragraph with no hyperlink keeps exactly the script's own choice.
    Otherwise the words replace ``[op.style_at, position)`` when the step
    before them deleted it (typed over a selection), and are inserted at
    ``position`` when it did not.
    """
    if not pmap.links:
        return _Placement(-1, op.style_at)
    before = pmap.link_at(position - 1)
    after = pmap.link_at(position)
    replaced = (
        previous is not None
        and previous.op == OP_DELETE
        and previous.end == position
        and previous.start <= op.style_at < position
    )
    if replaced:
        # Typed over characters that all sit in one link: the first and the
        # last replaced character share it (a link's characters are one run
        # of offsets, so everything between them does too).
        first = pmap.link_at(op.style_at)
        link = first if first >= 0 and first == before else -1
    else:
        # Inserted between two characters of one link.
        link = before if before >= 0 and before == after else -1
    split = -1
    if before >= 0 and before == after and link != before:
        # A replacement that runs INTO a link from outside it ends inside
        # the link; written there, its words would cut the link in two.
        split = pmap.link_start(before)
    return _Placement(link, _nearest(pmap.char_links, op.style_at, link), split)


def _placements(pmap: ParagraphMap, ops: list[SpliceOp]) -> dict[int, _Placement]:
    """Every insert step's placement, by its index in ``ops``."""
    placements: dict[int, _Placement] = {}
    position = 0
    previous = None
    for index, op in enumerate(ops):
        if op.op == OP_INSERT:
            placements[index] = _place_new_words(pmap, op, position, previous)
        else:
            position = op.end
        previous = op
    return placements


class _PieceWalk:
    """The source atoms, walked in order and cut into pieces (see
    :func:`_pieces`). Every piece ends with the link it sits in."""

    def __init__(self, pmap: ParagraphMap):
        self.atoms = pmap.atoms
        spans = word_spans(pmap.text)
        self.first_word = spans[0][0] if spans else -1
        self.pieces: list = []
        self.cursor = 0
        self.consumed = 0  # characters of atoms[cursor] already assigned

    def _zero_width(self, atom: _Atom) -> tuple:
        if atom.run < 0:
            return ("marker", copy.deepcopy(atom.node), atom.link)
        return ("run", atom.run, copy.deepcopy(atom.node), atom.link)

    def _flush(self, link: int) -> None:
        """Emit the zero-width content at the cursor that sits in ``link``."""
        atoms = self.atoms
        while (
            self.cursor < len(atoms)
            and not atoms[self.cursor].text
            and atoms[self.cursor].link == link
        ):
            self.pieces.append(self._zero_width(atoms[self.cursor]))
            self.cursor += 1

    def new_words(self, op: SpliceOp, placement: _Placement, position: int) -> None:
        if self.pieces:
            last = self.pieces[-1][-1]
            if last >= 0 and last != placement.link:
                # A link the new words are not in closes before them: its
                # zero-width content here (a bookmark closing inside it)
                # stays inside it.
                self._flush(last)
        if position == self.first_word:
            # Leading content leads the new words — where they go.
            self._flush(placement.link)
        self.pieces.append(("new", op.text, placement.style, placement.link))

    def advance(self, op: SpliceOp, start: int, end: int) -> None:
        """The keep or delete step ``op``, over the source range
        ``[start, end)`` (all of it, unless new words are written inside)."""
        keep = op.op == OP_KEEP
        atoms = self.atoms
        while self.cursor < len(atoms):
            atom = atoms[self.cursor]
            if not atom.text:
                if atom.start >= end:
                    break
                # A marker is never deleted. A zero-width run node goes with
                # the characters around it, except on the edge of a deleted
                # span, where it is kept: a page break before a relettered
                # label must not die with the old letter.
                if atom.run < 0:
                    self.pieces.append(("marker", copy.deepcopy(atom.node), atom.link))
                elif keep or atom.start == op.start:
                    self.pieces.append(
                        ("run", atom.run, copy.deepcopy(atom.node), atom.link)
                    )
                else:
                    self.pieces.append(
                        ("gone", atom.run, copy.deepcopy(atom.node), atom.link)
                    )
                self.cursor += 1
                continue
            low = atom.start + self.consumed
            if low >= end:
                break
            high = min(atom.start + len(atom.text), end)
            if self.consumed == 0 and high == atom.start + len(atom.text):
                node = copy.deepcopy(atom.node)
            else:
                node = _text_node(atom.text[low - atom.start : high - atom.start])
            self.pieces.append(("run" if keep else "gone", atom.run, node, atom.link))
            self.consumed = high - atom.start
            if self.consumed == len(atom.text):
                self.cursor += 1
                self.consumed = 0
            else:
                break

    def finish(self) -> list:
        for atom in self.atoms[self.cursor :]:
            # Only zero-width content can remain: every character is covered
            # by a keep or delete step.
            if atom.run < 0 or not atom.text:
                self.pieces.append(self._zero_width(atom))
        return self.pieces


def _pieces(pmap: ParagraphMap, ops: list[SpliceOp]) -> list:
    """The rendered content, in order: ``("run", run_index, node, link)``
    for kept source content, ``("gone", run_index, node, link)`` for deleted
    source content, ``("marker", node, link)`` for a marker, and
    ``("new", text, style_at, link)`` for inserted text. ``link`` is the
    hyperlink the piece sits in (``-1``: none).

    Both renderings read the same pieces: the clean export skips ``gone``
    (without closing the run it is building — a deleted word between two
    kept words of one source run leaves them in ONE run), and the redline
    writes it inside ``w:del``. Keep plus new is therefore the clean export
    by construction, and keep plus gone is the source paragraph. The pieces
    of one link are consecutive, so each rendering writes a link as ONE
    ``w:hyperlink``.

    Zero-width content (a page or column break, a bookmark, Word's layout
    cache) sits BETWEEN two characters, so where an insertion lands beside
    it is a choice, made here. Content in front of the paragraph's first
    word leads the paragraph: a break there starts it on a new page or
    column, a bookmark there opens over its text. Words inserted before
    that first word therefore go AFTER it, or they would be stranded on the
    page before the break (Codex review on PR #184). Everywhere else,
    zero-width content stays with what follows it: a break in front of a
    later word stays in front of that word, and a break at the end stays
    after words appended there. The Phase 1 redline places its ``w:ins``
    the same way, or Accept All would move a page break.

    A link is never split, which refines both rules: the leading content
    that goes ahead of new words is only what sits where they go (a bookmark
    opening inside a link the words are not in stays in the link), and a
    link the new words are not in keeps its own zero-width content at the
    insertion point (a bookmark closing inside it). A replacement that runs
    into a link from outside it writes its new words at the link's start
    (:class:`_Placement`).
    """
    walk = _PieceWalk(pmap)
    placements = _placements(pmap, ops)
    position = 0  # the source offset the keep and delete steps have reached
    index = 0
    while index < len(ops):
        op = ops[index]
        if op.op == OP_INSERT:
            walk.new_words(op, placements[index], position)
            index += 1
            continue
        position = op.end
        written = placements.get(index + 1)
        if written is not None and op.start < written.split < op.end:
            walk.advance(op, op.start, written.split)
            walk.new_words(ops[index + 1], written, written.split)
            walk.advance(op, written.split, op.end)
            index += 2
            continue
        walk.advance(op, op.start, op.end)
        index += 1
    return walk.finish()


def _stripped_copy(element):
    """``element`` with its attributes and ``w:pPr`` and nothing else."""
    paragraph = copy.deepcopy(element)
    for child in list(paragraph):
        if child.tag != _W_PPR:
            paragraph.remove(child)
    return paragraph


def _open_link(paragraph, source_link):
    """Append a copy of ``source_link`` — its attributes (the target), none
    of its content — to ``paragraph``, and return it."""
    link = copy.deepcopy(source_link)
    for child in list(link):
        link.remove(child)
    # (``.text`` is left alone: python-docx's ``CT_Hyperlink`` makes it a
    # read-only property — the text of its runs — and its parser keeps no
    # blank text to clear.)
    link.tail = None
    paragraph.append(link)
    return link


def render_clean(pmap: ParagraphMap, ops: list[SpliceOp]):
    """The paragraph with the edit script applied and accepted.

    The element keeps its attributes and ``w:pPr``; its content is rebuilt
    from the pieces, consecutive pieces of one source run sharing a copy of
    that run (its attributes and ``w:rPr`` intact), and consecutive pieces
    of one link sharing a copy of that link. A link left with nothing to
    hold — its words all deleted, no marker in it — is not written.
    """
    paragraph = _stripped_copy(pmap.element)
    target = paragraph  # where content goes: the paragraph, or a link in it
    open_link = -1
    open_run = None
    open_index = -2
    for piece in _pieces(pmap, ops):
        kind = piece[0]
        if kind == "gone":
            continue  # deleted: nothing emitted, and the open run stays open
        link = piece[-1]
        if link != open_link:
            target = paragraph if link < 0 else _open_link(paragraph, pmap.links[link])
            open_link = link
            open_run = None
            open_index = -2
        if kind == "run":
            _kind, run_index, node, _link = piece
            if open_run is None or open_index != run_index:
                open_run = _empty_copy(pmap.runs[run_index])
                target.append(open_run)
                open_index = run_index
            open_run.append(node)
            continue
        open_run = None
        open_index = -2
        if kind == "marker":
            target.append(piece[1])
            continue
        _kind, text, style_at, new_link = piece
        target.append(_new_run(pmap, text, style_at, new_link))
    return paragraph


def _empty_copy(source_run):
    """``source_run`` with its attributes and ``w:rPr`` and nothing else."""
    run = copy.deepcopy(source_run)
    for child in list(run):
        if child.tag != _W_RPR:
            run.remove(child)
    return run


def _new_run(pmap: ParagraphMap, text: str, style_at: int, link: int = -1):
    """A run carrying new ``text`` with the formatting Word would give it:
    that of the source character ``style_at``, or — with none — of the
    first run in ``link``, where the words go."""
    run = etree.Element(_W_R)
    if style_at >= 0:
        properties = pmap.run_properties_at(style_at)
    else:
        properties = pmap.first_run_properties(link)
    if properties is not None:
        run.append(copy.deepcopy(properties))
    append_text(run, text)
    return run


def _as_deleted_node(node):
    """A text node of deleted content: ``w:t`` is written ``w:delText``."""
    if node.tag == _W_T:
        node.tag = _W_DEL_TEXT
    return node


def render_redline(pmap: ParagraphMap, ops: list[SpliceOp], marks):
    """The paragraph with the edit script as Word tracked changes.

    The same pieces as :func:`render_clean`: kept content is the original
    runs (split where the script splits them, ``w:rPr`` copied), deleted
    content is the original runs inside ``w:del`` (``w:t`` written
    ``w:delText``), new text is a run inside ``w:ins`` carrying the
    formatting Word would give it. Markers stay where the clean export puts
    them, outside any wrapper — never deleted, only positioned. A link is
    one copy of its ``w:hyperlink`` holding its own pieces, wrappers inside
    it (a hyperlink cannot sit inside ``w:ins``/``w:del``; its runs can). So
    Accept All is :func:`render_clean` and Reject All is the source
    paragraph.

    ``marks`` (a :class:`~backend.spec_doc.revision_marks.RevisionMarks`)
    stamps each wrapper's id, author and date.
    """
    paragraph = _stripped_copy(pmap.element)
    target = paragraph  # where content goes: the paragraph, or a link in it
    open_link = -1
    wrapper = None
    wrapper_state = ""
    open_run = None
    open_key = None
    for piece in _pieces(pmap, ops):
        kind = piece[0]
        link = piece[-1]
        if link != open_link:
            target = paragraph if link < 0 else _open_link(paragraph, pmap.links[link])
            open_link = link
            wrapper = None
            open_run = None
            open_key = None
        if kind == "marker":
            wrapper = None
            open_run = None
            target.append(piece[1])
            continue
        state = {"run": "keep", "gone": "del", "new": "ins"}[kind]
        if state == "keep":
            if wrapper is not None:
                wrapper = None
                open_run = None
            container = target
        else:
            if wrapper is None or wrapper_state != state:
                wrapper = marks.wrapper("w:del" if state == "del" else "w:ins")
                target.append(wrapper)
                wrapper_state = state
                open_run = None
            container = wrapper
        if kind == "new":
            _kind, text, style_at, new_link = piece
            container.append(_new_run(pmap, text, style_at, new_link))
            open_run = None
            continue
        _kind, run_index, node, _link = piece
        key = (state, run_index)
        if open_run is None or open_key != key:
            open_run = _empty_copy(pmap.runs[run_index])
            container.append(open_run)
            open_key = key
        open_run.append(_as_deleted_node(node) if state == "del" else node)
    return paragraph


def splice_paragraph(element, target_text: str, *, expected_text: str | None = None):
    """``(paragraph, "")`` with ``target_text`` spliced into ``element``'s
    own runs, or ``(None, reason)`` when the paragraph is not eligible."""
    pmap, reason = map_paragraph(element, expected_text=expected_text)
    if pmap is None:
        return None, reason
    return render_clean(pmap, plan_splice(pmap.text, target_text)), ""


__all__ = [
    "FALLBACK_REASONS",
    "OP_DELETE",
    "OP_INSERT",
    "OP_KEEP",
    "ParagraphMap",
    "SpliceOp",
    "append_text",
    "map_paragraph",
    "plan_splice",
    "render_clean",
    "render_redline",
    "splice_paragraph",
    "word_spans",
]
