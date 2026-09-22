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
construction.

Eligibility is deliberately conservative (widen with corpus evidence, per
the plan): a plain paragraph whose children are runs, bookmarks and
``w:proofErr`` markers, and whose runs hold only properties and the text
nodes above (plus the zero-width layout markers Word writes everywhere). A
hyperlink, field, content control, comment or note reference, ``w:sym``,
drawing or pending revision sends the paragraph to the fallback, which the
caller owns; :func:`map_paragraph` names the reason so diagnostics can
count them.
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
_W_TAB = qn("w:tab")
_W_PTAB = qn("w:ptab")
_W_BR = qn("w:br")
_W_CR = qn("w:cr")
_W_NO_BREAK_HYPHEN = qn("w:noBreakHyphen")
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

    ``run`` indexes :attr:`ParagraphMap.runs`, or is ``-1`` for a
    paragraph-level marker. ``text`` is what the node contributes to the
    visible text (``""`` for a zero-width node); ``start`` is its offset.
    """

    run: int
    node: object
    text: str
    start: int


@dataclass(frozen=True)
class ParagraphMap:
    element: object
    runs: tuple
    atoms: tuple[_Atom, ...]
    text: str

    def run_properties_at(self, offset: int):
        """The ``w:rPr`` of the run that produced the character at
        ``offset``; with no such character, the first run's."""
        for atom in self.atoms:
            if atom.run >= 0 and atom.text and atom.start <= offset < atom.start + len(atom.text):
                return self.runs[atom.run].find(_W_RPR)
        for run in self.runs:
            return run.find(_W_RPR)
        return None


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


def map_paragraph(element, *, expected_text: str | None = None):
    """``(ParagraphMap, "")`` for a splice-eligible paragraph, else
    ``(None, reason)`` with a :data:`FALLBACK_REASONS` code.

    ``expected_text`` is the importer's reading of the paragraph; when
    given, a map that disagrees with it is refused (``text_mismatch``)
    rather than trusted.
    """
    if getattr(element, "tag", None) != _W_P:
        return None, FALLBACK_NOT_PARAGRAPH
    runs: list = []
    atoms: list[_Atom] = []
    offset = 0
    for child in element:
        tag = child.tag
        if not isinstance(tag, str):
            # An XML comment or processing instruction: carried in place.
            atoms.append(_Atom(-1, child, "", offset))
            continue
        if tag == _W_PPR:
            continue
        if tag in _PARAGRAPH_MARKERS:
            atoms.append(_Atom(-1, child, "", offset))
            continue
        if tag != _W_R:
            return None, _reason(tag)
        run_index = len(runs)
        runs.append(child)
        for node in child:
            node_tag = node.tag
            if node_tag == _W_RPR:
                continue
            if node_tag in _TEXT_NODES:
                text = _node_text(node)
            elif node_tag in _ZERO_WIDTH_NODES:
                text = ""
            else:
                return None, _reason(node_tag)
            atoms.append(_Atom(run_index, node, text, offset))
            offset += len(text)
    text = "".join(atom.text for atom in atoms)
    if expected_text is not None and text != expected_text:
        return None, FALLBACK_TEXT_MISMATCH
    return ParagraphMap(element, tuple(runs), tuple(atoms), text), ""


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


def _pieces(pmap: ParagraphMap, ops: list[SpliceOp]) -> list:
    """The rendered content, in order: ``("run", run_index, node)`` for
    source content, ``("marker", node)`` for a paragraph-level marker,
    ``("new", text, style_at)`` for inserted text."""
    atoms = pmap.atoms
    pieces: list = []
    cursor = 0
    consumed = 0  # characters of atoms[cursor] already assigned
    for op in ops:
        if op.op == OP_INSERT:
            pieces.append(("new", op.text, op.style_at))
            continue
        keep = op.op == OP_KEEP
        while cursor < len(atoms):
            atom = atoms[cursor]
            if not atom.text:
                if atom.start >= op.end:
                    break
                # A marker is never deleted. A zero-width run node goes with
                # the characters around it, except on the edge of a deleted
                # span, where it is kept: a page break before a relettered
                # label must not die with the old letter.
                if atom.run < 0:
                    pieces.append(("marker", copy.deepcopy(atom.node)))
                elif keep or atom.start == op.start:
                    pieces.append(("run", atom.run, copy.deepcopy(atom.node)))
                cursor += 1
                continue
            low = atom.start + consumed
            if low >= op.end:
                break
            high = min(atom.start + len(atom.text), op.end)
            if keep:
                if consumed == 0 and high == atom.start + len(atom.text):
                    node = copy.deepcopy(atom.node)
                else:
                    node = _text_node(
                        atom.text[low - atom.start : high - atom.start]
                    )
                pieces.append(("run", atom.run, node))
            consumed = high - atom.start
            if consumed == len(atom.text):
                cursor += 1
                consumed = 0
            else:
                break
    for atom in atoms[cursor:]:
        # Only zero-width content can remain: every character is covered by
        # a keep or delete step.
        if atom.run < 0:
            pieces.append(("marker", copy.deepcopy(atom.node)))
        elif not atom.text:
            pieces.append(("run", atom.run, copy.deepcopy(atom.node)))
    return pieces


def render_clean(pmap: ParagraphMap, ops: list[SpliceOp]):
    """The paragraph with the edit script applied and accepted.

    The element keeps its attributes and ``w:pPr``; its content is rebuilt
    from the pieces, consecutive pieces of one source run sharing a copy of
    that run (its attributes and ``w:rPr`` intact).
    """
    paragraph = copy.deepcopy(pmap.element)
    for child in list(paragraph):
        if child.tag != _W_PPR:
            paragraph.remove(child)
    open_run = None
    open_index = -2
    for piece in _pieces(pmap, ops):
        kind = piece[0]
        if kind == "run":
            _kind, run_index, node = piece
            if open_run is None or open_index != run_index:
                source_run = pmap.runs[run_index]
                open_run = copy.deepcopy(source_run)
                for child in list(open_run):
                    if child.tag != _W_RPR:
                        open_run.remove(child)
                paragraph.append(open_run)
                open_index = run_index
            open_run.append(node)
            continue
        open_run = None
        open_index = -2
        if kind == "marker":
            paragraph.append(piece[1])
            continue
        _kind, text, style_at = piece
        run = etree.SubElement(paragraph, _W_R)
        properties = pmap.run_properties_at(style_at)
        if properties is not None:
            run.append(copy.deepcopy(properties))
        append_text(run, text)
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
    "splice_paragraph",
    "word_spans",
]
