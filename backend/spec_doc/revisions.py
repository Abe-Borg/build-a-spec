"""Word revision markup, resolved: Accept All, Reject All, and the canonical
comparison the redline's self-check runs.

Redline on your original, D-7 (``docs/plans/REDLINE_ON_ORIGINAL_2026-09-22
.md``). The redline export promises two things about the file it hands
over — Accept All gives the formatted export, Reject All gives the upload
back — and checks both before it returns anything. These are the checks:
pure lxml, no Word, a few linear passes over the body. They are also the
test oracle for every redline test.

Accept All (:func:`accept_all`):

* a run-level ``w:ins`` / ``w:moveTo`` is unwrapped; a ``w:del`` /
  ``w:moveFrom`` is removed with everything in it;
* a paragraph mark flagged inserted (``w:pPr/w:rPr/w:ins``) loses the flag;
  one flagged deleted joins the paragraph to the next one: whatever the
  paragraph still holds moves to the front of the next paragraph (which
  keeps its own properties, as in Word), and a paragraph holding nothing is
  simply gone. With no next paragraph to join, it stays (Word cannot remove
  a container's last paragraph mark);
* a row flagged deleted (``w:trPr/w:del``) is removed, one flagged inserted
  loses the flag, and a table left with no rows is removed;
* every recorded property change (``w:pPrChange``, ``w:rPrChange``, …)
  is dropped — the current properties stand.

Reject All (:func:`reject_all`) is the mirror image: insertions go, deleted
content comes back (``w:delText`` → ``w:t``, ``w:delInstrText`` →
``w:instrText``), an inserted paragraph mark joins its paragraph to the
next, an inserted row goes, and a recorded property change restores the
old properties.

The comparison (:func:`first_difference`) is element-for-element, up to how
Word splits text into runs — the one thing it re-does on every save anyway:

* adjacent runs with identical attributes and properties are merged, and
  adjacent text in one run is joined; empty runs, empty text and empty
  property containers are dropped;
* ``w14:paraId`` / ``w14:textId`` are identity, not content, and are not
  compared; neither are XML comments or processing instructions (Word
  discards them on load) or ``xml:space`` on text;
* a bookmark is compared by NAME (ids are file-local), and the caller may
  exclude names — the one documented Reject-All limit: a moved provision's
  bookmarks stay with its new copy;
* an empty hyperlink is dropped (Word shows nothing for it), and so are
  FORMATTING-FREE empty paragraphs at the very end of the body: a document's
  last paragraph mark cannot be tracked, so resolving a deletion or insertion
  there leaves an empty paragraph behind. Only a formatting-free one is
  invisible — an empty paragraph still prints its number, breaks the page
  before it, draws its border, and takes its line height from its mark — so
  one that sets anything (any paragraph property, any run formatting on its
  mark, a section break) is a difference like any other. The redline writer
  makes sure the one it leaves sets nothing
  (``revision_marks.neutralize_last_paragraph``).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from docx.oxml.ns import qn
from lxml import etree

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
_W_MOVE_FROM = qn("w:moveFrom")
_W_MOVE_TO = qn("w:moveTo")
_W_TR = qn("w:tr")
_W_TRPR = qn("w:trPr")
_W_TBL = qn("w:tbl")
_W_TC = qn("w:tc")
_W_TCPR = qn("w:tcPr")
_W_NUMPR = qn("w:numPr")
_W_SECTPR = qn("w:sectPr")
_W_HYPERLINK = qn("w:hyperlink")
_W_BOOKMARK_START = qn("w:bookmarkStart")
_W_BOOKMARK_END = qn("w:bookmarkEnd")
_W_ID = qn("w:id")
_W_NAME = qn("w:name")
_W_TXBX_CONTENT = qn("w:txbxContent")
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
_W14_PARA_ID = f"{{{_W14}}}paraId"
_W14_TEXT_ID = f"{{{_W14}}}textId"

#: Where a ``w:ins``/``w:del`` is a FLAG on its parent rather than a wrapper
#: around content: a paragraph mark's run properties, a row's properties,
#: a numbering reference.
_MARKER_PARENTS = frozenset({_W_RPR, _W_TRPR, _W_NUMPR})
_INSERTED = frozenset({_W_INS, _W_MOVE_TO})
_DELETED = frozenset({_W_DEL, _W_MOVE_FROM})
_WRAPPERS = _INSERTED | _DELETED

_RANGE_MARKERS = frozenset(
    qn(f"w:{name}")
    for name in (
        "moveFromRangeStart",
        "moveFromRangeEnd",
        "moveToRangeStart",
        "moveToRangeEnd",
        "customXmlInsRangeStart",
        "customXmlInsRangeEnd",
        "customXmlDelRangeStart",
        "customXmlDelRangeEnd",
        "customXmlMoveFromRangeStart",
        "customXmlMoveFromRangeEnd",
        "customXmlMoveToRangeStart",
        "customXmlMoveToRangeEnd",
    )
)
_W_PPR_CHANGE = qn("w:pPrChange")
_W_RPR_CHANGE = qn("w:rPrChange")
#: Property-change records other than the paragraph and run ones, mapped to
#: the children of their parent that the change does NOT describe (and so
#: survive a Reject All that restores the rest).
_OTHER_CHANGES = {
    qn("w:sectPrChange"): frozenset({qn("w:headerReference"), qn("w:footerReference")}),
    qn("w:tblPrChange"): frozenset(),
    qn("w:tblPrExChange"): frozenset(),
    qn("w:trPrChange"): frozenset({_W_INS, _W_DEL}),
    qn("w:tcPrChange"): frozenset(
        {qn("w:cellIns"), qn("w:cellDel"), qn("w:cellMerge")}
    ),
    qn("w:tblGridChange"): frozenset(),
}
_W_NUMBERING_CHANGE = qn("w:numberingChange")
_W_CELL_INS = qn("w:cellIns")
_W_CELL_DEL = qn("w:cellDel")
_W_CELL_MERGE = qn("w:cellMerge")

#: Every element whose presence means "this XML still carries revisions".
REVISION_TAGS = (
    _WRAPPERS
    | _RANGE_MARKERS
    | {_W_PPR_CHANGE, _W_RPR_CHANGE, _W_NUMBERING_CHANGE}
    | set(_OTHER_CHANGES)
    | {_W_CELL_INS, _W_CELL_DEL, _W_CELL_MERGE, _W_DEL_TEXT, _W_DEL_INSTR_TEXT}
)


# ---------------------------------------------------------------------------
# Accept All / Reject All
# ---------------------------------------------------------------------------


def _plain(root):
    """A plain-lxml copy of ``root``.

    The oracle reads raw XML. An element parsed by python-docx is one of its
    oxml classes, several of which override ``.text`` with COMPUTED text (a
    ``CT_P``'s is the paragraph's visible text) — the wrong answer for a
    check that must see exactly what is in the file. Re-parsing the
    serialized element gives ordinary lxml elements whatever came in.
    """
    return etree.fromstring(etree.tostring(root))


def accept_all(root):
    """A copy of ``root`` (a ``w:body`` or any container) with every
    revision accepted. ``root`` itself is never modified."""
    resolved = _plain(root)
    _resolve(resolved, accept=True)
    return resolved


def reject_all(root):
    """A copy of ``root`` with every revision rejected."""
    resolved = _plain(root)
    _resolve(resolved, accept=False)
    return resolved


def has_revisions(root) -> bool:
    """Does ``root`` still carry any revision markup?"""
    return any(
        isinstance(element.tag, str) and element.tag in REVISION_TAGS
        for element in root.iter()
    )


def _remove(element) -> None:
    """Remove ``element``, keeping any tail text on the tree."""
    parent = element.getparent()
    if parent is None:
        return
    tail = element.tail
    if tail:
        previous = element.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    parent.remove(element)


def _unwrap(element) -> None:
    """Replace ``element`` with its children, in place."""
    parent = element.getparent()
    index = parent.index(element)
    for offset, child in enumerate(list(element)):
        parent.insert(index + offset, child)
    _remove(element)


def _restore_deleted_text(container) -> None:
    """``w:delText``/``w:delInstrText`` back to ``w:t``/``w:instrText``,
    outside any text box (a text box's own paragraphs are a separate story
    and never carry the wrapper's deletion)."""
    for element in list(container.iter(_W_DEL_TEXT, _W_DEL_INSTR_TEXT)):
        inside_box = False
        for ancestor in element.iterancestors():
            if ancestor is container:
                break
            if ancestor.tag == _W_TXBX_CONTENT:
                inside_box = True
                break
        if not inside_box:
            element.tag = _W_T if element.tag == _W_DEL_TEXT else _W_INSTR_TEXT


def _resolve_wrappers(parent, *, accept: bool) -> None:
    for child in list(parent):
        tag = child.tag
        if not isinstance(tag, str):
            continue
        if tag in _RANGE_MARKERS:
            _remove(child)
            continue
        if tag in _WRAPPERS and parent.tag not in _MARKER_PARENTS:
            dropped = tag in (_DELETED if accept else _INSERTED)
            if dropped:
                _remove(child)
                continue
            _resolve_wrappers(child, accept=accept)
            if not accept:
                _restore_deleted_text(child)
            _unwrap(child)
            continue
        _resolve_wrappers(child, accept=accept)


def _resolve_property_changes(root, *, accept: bool) -> None:
    for change in list(root.iter(_W_NUMBERING_CHANGE)):
        _remove(change)
    tags = (_W_PPR_CHANGE, _W_RPR_CHANGE, *_OTHER_CHANGES)
    for change in list(root.iter(*tags)):
        parent = change.getparent()
        if parent is None:  # pragma: no cover - removed with an ancestor
            continue
        if accept:
            _remove(change)
            continue
        old = next(
            (child for child in change if isinstance(child.tag, str)), None
        )
        old_children = [copy.deepcopy(c) for c in old] if old is not None else []
        if change.tag == _W_PPR_CHANGE:
            kept = [
                c for c in parent if c.tag in (_W_RPR, _W_SECTPR) and c is not change
            ]
            restored = old_children + kept
        elif change.tag == _W_RPR_CHANGE:
            markers = [
                c for c in parent if c.tag in _WRAPPERS and c is not change
            ]
            restored = markers + old_children
        else:
            preserved = _OTHER_CHANGES[change.tag]
            kept = [c for c in parent if c.tag in preserved and c is not change]
            restored = kept + old_children
        for child in list(parent):
            parent.remove(child)
        for child in restored:
            parent.append(child)


def _content(paragraph) -> list:
    return [c for c in paragraph if c.tag != _W_PPR]


def _join_to_next(paragraph) -> None:
    """Word's resolution of a removed paragraph mark: the paragraph's
    remaining content joins the next paragraph, which keeps its own
    properties. Nothing left: the paragraph is simply gone. No next
    paragraph to join: it stays."""
    content = _content(paragraph)
    if not content:
        _remove(paragraph)
        return
    following = paragraph.getnext()
    while following is not None and not isinstance(following.tag, str):
        following = following.getnext()
    if following is None or following.tag != _W_P:
        return
    properties = following.find(_W_PPR)
    index = 0 if properties is None else following.index(properties) + 1
    for offset, child in enumerate(content):
        following.insert(index + offset, child)
    _remove(paragraph)


def _resolve_rows(root, *, accept: bool) -> None:
    for row in list(root.iter(_W_TR)):
        if row.getparent() is None:  # pragma: no cover
            continue
        properties = row.find(_W_TRPR)
        if properties is None:
            continue
        flags = [c for c in properties if c.tag in (_W_INS, _W_DEL)]
        if not flags:
            continue
        gone = any(c.tag == (_W_DEL if accept else _W_INS) for c in flags)
        if gone:
            _remove(row)
            continue
        for flag in flags:
            _remove(flag)
    for cell in list(root.iter(_W_TC)):
        properties = cell.find(_W_TCPR)
        if properties is None:
            continue
        for flag in list(properties):
            if flag.tag == _W_CELL_MERGE:
                _remove(flag)
            elif flag.tag in (_W_CELL_INS, _W_CELL_DEL):
                gone = flag.tag == (_W_CELL_DEL if accept else _W_CELL_INS)
                if gone:
                    _remove(cell)
                    break
                _remove(flag)


def _resolve_marks(root, *, accept: bool) -> None:
    for paragraph in list(root.iter(_W_P)):
        properties = paragraph.find(_W_PPR)
        mark = properties.find(_W_RPR) if properties is not None else None
        if mark is None:
            continue
        flags = [c for c in mark if c.tag in _WRAPPERS]
        if not flags:
            continue
        joined = any(c.tag in (_DELETED if accept else _INSERTED) for c in flags)
        for flag in flags:
            mark.remove(flag)
        if joined:
            _join_to_next(paragraph)


def _remove_empty_tables(root) -> None:
    for table in list(root.iter(_W_TBL)):
        if table.getparent() is None:  # pragma: no cover
            continue
        if table.find(f".//{_W_TR}") is None:
            _remove(table)


def _resolve(root, *, accept: bool) -> None:
    _resolve_wrappers(root, accept=accept)
    _resolve_property_changes(root, accept=accept)
    _resolve_rows(root, accept=accept)
    _resolve_marks(root, accept=accept)
    _remove_empty_tables(root)


# ---------------------------------------------------------------------------
# The canonical comparison
# ---------------------------------------------------------------------------

_TEXT_TAGS = frozenset({_W_T, _W_DEL_TEXT, _W_INSTR_TEXT, _W_DEL_INSTR_TEXT})
_IDENTITY_ATTRIBUTES = frozenset({_W14_PARA_ID, _W14_TEXT_ID})
_DROP_WHEN_EMPTY = frozenset({_W_RPR, _W_PPR, _W_TRPR})


def _local(tag: str) -> str:
    return etree.QName(tag).localname


@dataclass(frozen=True)
class Difference:
    """Where two canonical bodies first disagree — positions and element
    names only, never text (it lands in diagnostics)."""

    index: int
    left: str
    right: str
    path: str

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "left": self.left,
            "right": self.right,
            "path": self.path,
        }


class _Canon:
    def __init__(self, names: dict[str, str], excluded: frozenset[str]):
        self.names = names
        self.excluded = excluded

    def element(self, element):
        tag = element.tag
        if not isinstance(tag, str):
            return None
        if tag == _W_BOOKMARK_START:
            name = element.get(_W_NAME, "")
            if name in self.excluded:
                return None
            attributes = tuple(
                sorted((k, v) for k, v in element.attrib.items() if k != _W_ID)
            )
            return (tag, attributes, (), "")
        if tag == _W_BOOKMARK_END:
            name = self.names.get(element.get(_W_ID, ""), "")
            if name in self.excluded:
                return None
            return (tag, (), (), name)
        if tag in _TEXT_TAGS:
            return (tag, (), (), element.text or "")
        attributes = tuple(
            sorted(
                (k, v)
                for k, v in element.attrib.items()
                if k not in _IDENTITY_ATTRIBUTES
            )
        )
        children = self._children(element)
        text = (element.text or "").strip()
        if tag == _W_R:
            content = [c for c in children if c[0] != _W_RPR]
            if not content:
                return None
        if tag == _W_HYPERLINK and not children:
            return None
        if tag in _DROP_WHEN_EMPTY and not children and not attributes:
            return None
        return (tag, attributes, tuple(children), text)

    def _children(self, element) -> list:
        children: list = []
        for child in element:
            canonical = self.element(child)
            if canonical is None:
                continue
            if (
                canonical[0] == _W_T
                and children
                and children[-1][0] == _W_T
            ):
                previous = children[-1]
                children[-1] = (_W_T, (), (), previous[3] + canonical[3])
                continue
            if canonical[0] == _W_T and not canonical[3]:
                continue
            if canonical[0] == _W_R and children and children[-1][0] == _W_R:
                merged = _merge_runs(children[-1], canonical)
                if merged is not None:
                    children[-1] = merged
                    continue
            children.append(canonical)
        return children


def _run_properties(run: tuple):
    return next((c for c in run[2] if c[0] == _W_RPR), None)


def _merge_runs(left: tuple, right: tuple):
    if left[1] != right[1] or _run_properties(left) != _run_properties(right):
        return None
    properties = _run_properties(left)
    content: list = []
    for child in [c for c in left[2] if c[0] != _W_RPR] + [
        c for c in right[2] if c[0] != _W_RPR
    ]:
        if child[0] == _W_T and content and content[-1][0] == _W_T:
            content[-1] = (_W_T, (), (), content[-1][3] + child[3])
        else:
            content.append(child)
    head = [properties] if properties is not None else []
    return (_W_R, left[1], tuple(head + content), left[3])


def _bookmark_names(root) -> dict[str, str]:
    return {
        start.get(_W_ID, ""): start.get(_W_NAME, "")
        for start in root.iter(_W_BOOKMARK_START)
    }


def _is_formatting_free_empty(canonical: tuple) -> bool:
    """An empty paragraph that sets nothing Word shows: no content and — once
    empty property containers are dropped — no properties at all (a section
    break, numbering, a page break before it, a style, a border, its mark's
    own run formatting: all differences). Attributes on ``w:p`` itself are
    revision-session and paragraph ids, never formatting."""
    return canonical[0] == _W_P and not canonical[2] and not canonical[3]


def canonical_body(body, *, exclude_bookmarks=frozenset()) -> list[tuple]:
    """The body's children in canonical form (see the module docstring)."""
    body = _plain(body)
    canon = _Canon(_bookmark_names(body), frozenset(exclude_bookmarks))
    children = [
        c for c in (canon.element(child) for child in body) if c is not None
    ]
    # Word cannot track a document's last paragraph mark, so an empty last
    # paragraph is what a tracked insertion or deletion there leaves behind
    # — tolerated only when it sets nothing (see the module docstring).
    tail = [children.pop()] if children and children[-1][0] == _W_SECTPR else []
    while children and _is_formatting_free_empty(children[-1]):
        children.pop()
    return children + tail


def _first_path(left, right, path: list[str]) -> list[str]:
    if left is None or right is None or left[0] != right[0]:
        return path
    path = path + [_local(left[0])]
    if left[1] != right[1] or left[3] != right[3]:
        return path
    for a, b in zip(left[2], right[2]):
        if a != b:
            return _first_path(a, b, path)
    return path


def first_difference(left_body, right_body, *, exclude_bookmarks=frozenset()):
    """``None`` when the two bodies are canonically equal, else the first
    :class:`Difference` (body-child index and element names, no text)."""
    left = canonical_body(left_body, exclude_bookmarks=exclude_bookmarks)
    right = canonical_body(right_body, exclude_bookmarks=exclude_bookmarks)
    for index in range(max(len(left), len(right))):
        a = left[index] if index < len(left) else None
        b = right[index] if index < len(right) else None
        if a == b:
            continue
        return Difference(
            index=index,
            left=_local(a[0]) if a is not None else "",
            right=_local(b[0]) if b is not None else "",
            path="/".join(_first_path(a, b, [])),
        )
    return None


def duplicate_bookmark_names(root) -> set[str]:
    """Bookmark names that start more than once under ``root``."""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for start in root.iter(_W_BOOKMARK_START):
        name = start.get(_W_NAME, "")
        if name in seen:
            duplicates.add(name)
        seen.add(name)
    return duplicates


__all__ = [
    "Difference",
    "REVISION_TAGS",
    "accept_all",
    "canonical_body",
    "duplicate_bookmark_names",
    "first_difference",
    "has_revisions",
    "reject_all",
]
