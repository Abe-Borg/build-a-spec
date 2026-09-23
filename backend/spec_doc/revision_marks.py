"""Writing Word tracked changes into a copy of an uploaded body.

Redline on your original, D-2/D-3/D-6 (``docs/plans/REDLINE_ON_ORIGINAL_
2026-09-22.md``). The READING side — Accept All, Reject All, the canonical
comparison — lives in :mod:`backend.spec_doc.revisions` and deliberately
shares no code with this module: the export's self-check must not be able to
agree with the writer because it IS the writer.

What Word is strict about, and this module therefore owns:

* **Revision ids** start above the highest ``w:id`` already in the package's
  story parts (:func:`highest_annotation_id`). Bookmarks, comments and
  revisions share that id space in practice, so Batch 5's ``count(1)`` is
  only safe on the fresh documents it was written for.
* **Schema order.** A paragraph mark's flag is the FIRST child of
  ``w:pPr/w:rPr`` (``EG_ParaRPrTrackChanges`` leads ``CT_ParaRPr``);
  ``w:rPr`` itself precedes ``w:sectPr`` and ``w:pPrChange`` inside
  ``w:pPr``; a row's flag follows its other ``w:trPr`` properties and
  precedes ``w:trPrChange``. Batch 5's ``_mark_paragraph`` appends at both
  levels, which is only right on the bare paragraphs it builds.
* **What a wrapper may hold.** ``w:ins``/``w:del`` hold run-level content —
  runs, bookmarks, inline content controls, smart tags, math — but not a
  hyperlink or a simple field. A hyperlink keeps its element and has its
  runs wrapped; a simple field cannot be tracked at all and is refused with
  a named reason (:class:`UntrackableContent`) rather than guessed at.
* **Deleted text is ``w:delText``** (``w:delInstrText`` for a field
  instruction) — except inside a text box, which is its own story: deleting
  the run that anchors it deletes the box whole.
"""

from __future__ import annotations

import copy
import io
import zipfile

from docx.oxml.ns import qn
from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"

_W_P = qn("w:p")
_W_PPR = qn("w:pPr")
_W_RPR = qn("w:rPr")
_W_R = qn("w:r")
_W_T = qn("w:t")
_W_DEL_TEXT = qn("w:delText")
_W_INSTR_TEXT = qn("w:instrText")
_W_DEL_INSTR_TEXT = qn("w:delInstrText")
_W_INS = qn("w:ins")
_W_DEL = qn("w:del")
_W_TBL = qn("w:tbl")
_W_TR = qn("w:tr")
_W_TRPR = qn("w:trPr")
_W_TBL_PR_EX = qn("w:tblPrEx")
_W_TR_PR_CHANGE = qn("w:trPrChange")
_W_TC = qn("w:tc")
_W_SDT = qn("w:sdt")
_W_SDT_CONTENT = qn("w:sdtContent")
_W_CUSTOM_XML = qn("w:customXml")
_W_HYPERLINK = qn("w:hyperlink")
_W_SECTPR = qn("w:sectPr")
_W_PPR_CHANGE = qn("w:pPrChange")
_W_BOOKMARK_START = qn("w:bookmarkStart")
_W_BOOKMARK_END = qn("w:bookmarkEnd")
_W_TXBX_CONTENT = qn("w:txbxContent")
_W_ID = qn("w:id")
_W_AUTHOR = qn("w:author")
_W_DATE = qn("w:date")

#: Run-level content a ``w:ins``/``w:del`` may hold whole
#: (``EG_ContentRunContent`` and ``EG_RunLevelElements``).
_WRAPPABLE = frozenset(
    {
        _W_R,
        _W_SDT,
        qn("w:smartTag"),
        _W_CUSTOM_XML,
        qn("w:dir"),
        qn("w:bdo"),
        _W_BOOKMARK_START,
        _W_BOOKMARK_END,
        qn("w:proofErr"),
        qn("w:permStart"),
        qn("w:permEnd"),
        qn("w:commentRangeStart"),
        qn("w:commentRangeEnd"),
        f"{{{_M_NS}}}oMath",
        f"{{{_M_NS}}}oMathPara",
    }
)
#: Containers that cannot sit inside a wrapper but whose content can.
_DESCEND = frozenset({_W_HYPERLINK})

#: Named reasons a body element cannot be carried as a tracked change.
UNTRACKABLE_SIMPLE_FIELD = "simple_field"
UNTRACKABLE_CONTENT_CONTROL = "block_content_control"
UNTRACKABLE_FIELD_BLOCK = "field_block"
UNTRACKABLE_REVISIONS = "pending_revisions"
UNTRACKABLE_MARKUP = "untrackable_markup"
UNTRACKABLE_REASONS = frozenset(
    {
        UNTRACKABLE_SIMPLE_FIELD,
        UNTRACKABLE_CONTENT_CONTROL,
        UNTRACKABLE_FIELD_BLOCK,
        UNTRACKABLE_REVISIONS,
        UNTRACKABLE_MARKUP,
    }
)
_REASON_BY_TAG = {
    qn("w:fldSimple"): UNTRACKABLE_SIMPLE_FIELD,
    _W_INS: UNTRACKABLE_REVISIONS,
    _W_DEL: UNTRACKABLE_REVISIONS,
    qn("w:moveFrom"): UNTRACKABLE_REVISIONS,
    qn("w:moveTo"): UNTRACKABLE_REVISIONS,
}


class UntrackableContent(ValueError):
    """Content Word cannot represent as a tracked insertion or deletion.

    ``reason`` is one of :data:`UNTRACKABLE_REASONS`; ``tag`` is the local
    name of the element in the way. Neither carries document text.
    """

    def __init__(self, reason: str, tag: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.tag = tag


class RevisionMarks:
    """Ids, author and date for the tracked changes of one export."""

    def __init__(self, *, author: str, date: str, first_id: int = 1) -> None:
        self.author = author
        self.date = date
        self._next = max(1, int(first_id))
        self.first_id = self._next
        self.count = 0

    def stamp(self, element):
        element.set(_W_ID, str(self._next))
        element.set(_W_AUTHOR, self.author)
        element.set(_W_DATE, self.date)
        self._next += 1
        self.count += 1
        return element

    def wrapper(self, tag: str):
        """A new stamped ``w:ins`` / ``w:del`` (``tag`` in Clark or ``w:``
        form)."""
        return self.stamp(etree.Element(qn(tag) if tag.startswith("w:") else tag))


def highest_annotation_id(source_bytes: bytes) -> int:
    """The largest integer ``w:id`` in the package's WordprocessingML parts
    (0 when there is none). Over-reading costs nothing — the export's ids
    only start higher — so every ``word/…xml`` member is scanned."""
    attribute = f"{{{W_NS}}}id"
    highest = 0
    with zipfile.ZipFile(io.BytesIO(source_bytes)) as archive:
        for name in archive.namelist():
            folded = name.casefold()
            if not (folded.startswith("word/") and folded.endswith(".xml")):
                continue
            try:
                data = archive.read(name)
                for _event, element in etree.iterparse(
                    io.BytesIO(data), events=("start",), resolve_entities=False
                ):
                    value = element.get(attribute)
                    if value is not None and value.strip().isdigit():
                        highest = max(highest, int(value))
            except (etree.XMLSyntaxError, KeyError, RuntimeError, zipfile.BadZipFile):
                continue
    return highest


# ---------------------------------------------------------------------------
# Paragraph marks
# ---------------------------------------------------------------------------


def _paragraph_properties(paragraph):
    properties = paragraph.find(_W_PPR)
    if properties is None:
        properties = etree.Element(_W_PPR)
        paragraph.insert(0, properties)
    return properties


def paragraph_mark_properties(paragraph):
    """The paragraph mark's ``w:rPr``, created at its schema position:
    after the other paragraph properties, before ``w:sectPr`` and
    ``w:pPrChange``."""
    properties = _paragraph_properties(paragraph)
    mark = properties.find(_W_RPR)
    if mark is None:
        mark = etree.Element(_W_RPR)
        successor = next(
            (c for c in properties if c.tag in (_W_SECTPR, _W_PPR_CHANGE)), None
        )
        if successor is None:
            properties.append(mark)
        else:
            successor.addprevious(mark)
    return mark


def holds_section_break(paragraph) -> bool:
    return (
        paragraph.tag == _W_P
        and paragraph.find(f"{_W_PPR}/{_W_SECTPR}") is not None
    )


def mark_paragraph(paragraph, tag: str, marks: RevisionMarks) -> None:
    """Flag ``paragraph``'s mark inserted (``"w:ins"``) or deleted
    (``"w:del"``). A mark holding a section break is never flagged deleted:
    Accept All would merge two Word sections."""
    if tag == "w:del" and holds_section_break(paragraph):
        raise AssertionError(
            "a paragraph mark holding a section break must never be deleted"
        )
    mark = paragraph_mark_properties(paragraph)
    mark.insert(0, marks.wrapper(tag))


def record_paragraph_properties(paragraph, original_properties, marks) -> None:
    """Record ``original_properties`` (a ``w:pPr``) as what this paragraph's
    properties were: a ``w:pPrChange`` at the end of its ``w:pPr``, holding
    the base properties only (``CT_PPrBase`` has no ``w:rPr``, ``w:sectPr``
    or change of its own)."""
    properties = _paragraph_properties(paragraph)
    change = marks.wrapper("w:pPrChange")
    old = etree.SubElement(change, _W_PPR)
    if original_properties is not None:
        for child in original_properties:
            if isinstance(child.tag, str) and child.tag not in (
                _W_RPR,
                _W_SECTPR,
                _W_PPR_CHANGE,
            ):
                old.append(copy.deepcopy(child))
    properties.append(change)


# ---------------------------------------------------------------------------
# Inline content
# ---------------------------------------------------------------------------


def _inside_text_box(node, root) -> bool:
    """Is ``node`` inside a text box nested in ``root`` (not ``root``'s
    own story)?"""
    if node is root:
        return False
    for ancestor in node.iterancestors():
        if ancestor is root:
            return False
        if ancestor.tag == _W_TXBX_CONTENT:
            return True
    return False


def as_deleted(element):
    """``element`` with its text written the way deleted text is:
    ``w:t`` → ``w:delText``, ``w:instrText`` → ``w:delInstrText`` — except
    inside a text box, whose story the deletion does not reach. Returns
    ``element`` for chaining."""
    for node in list(element.iter(_W_T, _W_INSTR_TEXT)):
        if not _inside_text_box(node, element):
            node.tag = _W_DEL_TEXT if node.tag == _W_T else _W_DEL_INSTR_TEXT
    return element


def _check(child) -> None:
    tag = child.tag
    if tag in _WRAPPABLE or tag in _DESCEND or tag == _W_PPR:
        return
    reason = _REASON_BY_TAG.get(tag, UNTRACKABLE_MARKUP)
    raise UntrackableContent(reason, etree.QName(tag).localname)


def _wrap_content(container, tag: str, marks: RevisionMarks, *, deleted: bool) -> None:
    group = None
    for child in list(container):
        if not isinstance(child.tag, str):
            group = None  # an XML comment stays where it is
            continue
        if child.tag == _W_PPR:
            continue
        _check(child)
        if child.tag in _DESCEND:
            group = None
            _wrap_content(child, tag, marks, deleted=deleted)
            continue
        if group is None:
            group = marks.wrapper(tag)
            child.addprevious(group)
        group.append(child)
        if deleted:
            as_deleted(child)


def delete_content(container, marks: RevisionMarks) -> None:
    """Mark everything in ``container`` (a paragraph or an inline container)
    deleted, in place. Its paragraph properties stay as they are. (The old
    copy of a MOVED provision gives its bookmarks up before this runs —
    ``source_render._give_up_identity`` — because a file must never carry
    one bookmark name twice.)
    """
    _wrap_content(container, "w:del", marks, deleted=True)


def insert_content(container, marks: RevisionMarks) -> None:
    """Mark everything in ``container`` inserted, in place."""
    _wrap_content(container, "w:ins", marks, deleted=False)


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


def _rows(table):
    """The rows that belong to ``table`` itself — including rows inside a
    row-level content control or custom-XML element, never a nested
    table's."""
    for child in table:
        if child.tag == _W_TR:
            yield child
        elif child.tag in (_W_SDT, _W_CUSTOM_XML):
            inner = child.find(_W_SDT_CONTENT) if child.tag == _W_SDT else child
            if inner is not None:
                yield from _rows(inner)


def _block_paragraphs(container):
    """Paragraphs of a cell (or block container), recursing into block-level
    content controls and custom XML, handing nested tables back."""
    for child in container:
        if child.tag == _W_P:
            yield ("p", child)
        elif child.tag == _W_TBL:
            yield ("tbl", child)
        elif child.tag == _W_SDT:
            inner = child.find(_W_SDT_CONTENT)
            if inner is not None:
                yield from _block_paragraphs(inner)
        elif child.tag == _W_CUSTOM_XML:
            yield from _block_paragraphs(child)


def mark_table(table, tag: str, marks: RevisionMarks) -> None:
    """Flag every row of ``table`` (nested tables included) inserted or
    deleted, and mark its cells' content the same way."""
    deleted = tag == "w:del"
    for row in _rows(table):
        properties = row.find(_W_TRPR)
        if properties is None:
            properties = etree.Element(_W_TRPR)
            prefix = row.find(_W_TBL_PR_EX)
            if prefix is None:
                row.insert(0, properties)
            else:
                prefix.addnext(properties)
        flag = marks.wrapper(tag)
        change = properties.find(_W_TR_PR_CHANGE)
        if change is None:
            properties.append(flag)
        else:
            change.addprevious(flag)
        for cell in row.iter(_W_TC):
            if next(cell.iterancestors(_W_TR), None) is not row:
                continue
            for kind, element in _block_paragraphs(cell):
                if kind == "tbl":
                    mark_table(element, tag, marks)
                elif deleted:
                    delete_content(element, marks)
                else:
                    insert_content(element, marks)


__all__ = [
    "RevisionMarks",
    "UNTRACKABLE_REASONS",
    "UntrackableContent",
    "as_deleted",
    "delete_content",
    "highest_annotation_id",
    "holds_section_break",
    "insert_content",
    "mark_paragraph",
    "mark_table",
    "paragraph_mark_properties",
    "record_paragraph_properties",
]
