"""Emit an imported master back as Word, with its formatting intact.

The contract (decided with Abraham, 2026-08-21):

    Every part of the uploaded package except the body of
    ``word/document.xml`` is carried through byte-for-byte — headers,
    footers, styles, theme, fonts, numbering definitions, page setup,
    section properties. Inside the body:

    * a provision you did not touch is emitted as a byte-identical clone of
      its source element;
    * a provision you edited keeps its paragraph properties and its own
      runs: the new words are spliced into the runs that were already there
      (``source_splice``, D-2 of the Redline-on-your-original plan), so a
      bold phrase you did not change stays bold and the tab after a typed
      letter stays a tab — relettering included;
    * a preserved block (table, picture, embedded object, content control)
      is emitted verbatim;
    * a provision you added is cloned from the nearest kin of its own kind
      (a provision from a provision at its depth, an article heading from an
      article heading), taking its formatting, its label convention and its
      separator — never its identity, bookmarks, comment anchors or section
      break;
    * body content the tree never modelled travels with the element below
      it — blank spacers, a cover page, a picture-only paragraph, a page
      break — except a SECTION BREAK, which belongs to the content above it
      (see "Section breaks" below).

What is deliberately NOT promised is the older mode's byte-exact whole-file
clone. That promise bought so little editing surface (three of twenty-seven
body operations on a clean master) that it was the feature's real defect.

Two limits remain, and are disclosed rather than worked around:

* **New words inherit their formatting from a neighbour.** A word typed
  over others takes the formatting of the first character it replaced, and
  an inserted word the formatting of the character before it — what Word
  itself does. Unchanged words keep their own. A paragraph the splice cannot
  map (a hyperlink, field, content control, comment or note reference,
  symbol or drawing inside it) is rebuilt from its first run's properties
  instead; the export's diagnostics count those fallbacks by reason.
* **A revision-bearing paragraph is rewritten, never cloned.** The importer
  showed the Accept-All view, so cloning the original markup would export
  text the user never saw. Those paragraphs take the rewrite path even when
  their text is unchanged.

Section breaks (Phase 0 of the Redline-on-your-original plan): a Word
section break is the END of a section — the content above it — so it never
moves or disappears because of an edit to what sits below it. An empty
paragraph holding a break stays after the content above it; a provision that
holds one in its own ``w:pPr`` keeps it while it stays in place, and when it
is deleted or moved away an empty paragraph holding the break is left where
it was; a clone never copies one. No edit loses a break. Where "in place"
is ambiguous after a reorder, the break goes where it best separates the
content that was above it from the content that was below it.
"""

from __future__ import annotations

import copy
import re
import zipfile
from dataclasses import dataclass, field
from io import BytesIO

from docx.oxml import parse_xml
from docx.oxml.ns import qn
from lxml import etree

from .importer import _accept_all_paragraph_text, _element_has_tracked_changes
from .model import (
    Article,
    Paragraph,
    SpecSection,
    _paragraph_label,
    labelled_paragraphs,
)
from .raw_zip import replace_document_xml_raw
from .source_format import (
    HEADER_SOURCE_CHROME,
    HEADER_SOURCE_FRONT_MATTER,
    LABEL_AUTO,
    LABEL_MANUAL,
    NO_ORIGIN,
    SECTION_TITLE_UID,
    SourceFormatMap,
)
from .source_splice import (
    FALLBACK_REVISIONS,
    ParagraphMap,
    SpliceOp,
    append_text,
    map_paragraph,
    plan_splice,
    render_clean,
)

_DOCUMENT_PART = "word/document.xml"

_W_P = qn("w:p")
_W_PPR = qn("w:pPr")
_W_R = qn("w:r")
_W_RPR = qn("w:rPr")
_W_SECTPR = qn("w:sectPr")
_W_NUMPR = qn("w:numPr")
_W_NUMID = qn("w:numId")
_W_VAL = qn("w:val")
_W14_PARA_ID = "{http://schemas.microsoft.com/office/word/2010/wordml}paraId"
_W14_TEXT_ID = "{http://schemas.microsoft.com/office/word/2010/wordml}textId"
# A paragraph carrying any of these is layout, not a spacer: a picture, a
# text box (most cover pages), an embedded object, a content control. It is
# never dropped as a blank, and it travels with the element below it.
_LAYOUT_TAGS = (
    qn("w:drawing"),
    qn("w:pict"),
    qn("w:object"),
    qn("w:txbxContent"),
    qn("w:sdt"),
)
# ``w:pPr`` children in schema order (CT_PPr), for inserting ``w:numPr``
# where Word expects it.
_PPR_ORDER = tuple(
    qn(f"w:{name}")
    for name in (
        "pStyle",
        "keepNext",
        "keepLines",
        "pageBreakBefore",
        "framePr",
        "widowControl",
        "numPr",
        "suppressLineNumbers",
        "pBdr",
        "shd",
        "tabs",
        "suppressAutoHyphens",
        "kinsoku",
        "wordWrap",
        "overflowPunct",
        "topLinePunct",
        "autoSpaceDE",
        "autoSpaceDN",
        "bidi",
        "adjustRightInd",
        "snapToGrid",
        "spacing",
        "ind",
        "contextualSpacing",
        "mirrorIndents",
        "suppressOverlap",
        "jc",
        "textDirection",
        "textAlignment",
        "textboxTightWrap",
        "outlineLvl",
        "divId",
        "cnfStyle",
        "rPr",
        "sectPr",
        "pPrChange",
    )
)
# An article heading as the importer's ``_ARTICLE_RE`` reads it, with the
# pieces the export must reproduce: the ordinal's width ("1.01" vs "1.1"), a
# trailing dot, and the raw separator (a tab, a space, " - ").
_ARTICLE_HEADING_RE = re.compile(r"^([1-5])\.(\d{1,2})(\.?)(\s+[-–—]?\s*)(\S.*)$")
_LEADING_TOKEN_RE = re.compile(r"\S+(\s+)")
# The line the importer skips under a PART with no articles.
_NOT_USED = "(not used.)"


class SourceRenderError(ValueError):
    """The retained package cannot back an appearance-preserving export."""


def _normalized(text: str) -> str:
    """The importer's whitespace fold, for comparing like with like."""
    return " ".join((text or "").split())


def _is_blank_paragraph(element) -> bool:
    """A spacer: a paragraph with no text and nothing drawn in it."""
    if element.tag != _W_P:
        return False
    if _accept_all_paragraph_text(element).strip():
        return False
    return all(element.find(f".//{tag}") is None for tag in _LAYOUT_TAGS)


def _holds_break(element) -> bool:
    """A paragraph whose mark ends a Word section."""
    return (
        element.tag == _W_P
        and element.find(f"{_W_PPR}/{_W_SECTPR}") is not None
    )


def _strip_break(element) -> None:
    properties = element.find(_W_PPR)
    if properties is None:
        return
    for sect_pr in properties.findall(_W_SECTPR):
        properties.remove(sect_pr)


def _strip_identity(element) -> None:
    """Drop the ``w14`` ids Word expects to be unique; it regenerates them."""
    for attribute in (_W14_PARA_ID, _W14_TEXT_ID):
        if attribute in element.attrib:
            del element.attrib[attribute]


def _cancel_numbering(element) -> None:
    """``w:numId 0`` — "no numbering", whatever the paragraph style says.

    Word prints the number of an empty numbered paragraph, so an empty
    paragraph left holding a section break must not stay in the list.
    """
    properties = element.find(_W_PPR)
    if properties is None:
        properties = etree.Element(_W_PPR)
        element.insert(0, properties)
    numbering = properties.find(_W_NUMPR)
    if numbering is None:
        numbering = etree.Element(_W_NUMPR)
        after = _PPR_ORDER[_PPR_ORDER.index(_W_NUMPR) + 1 :]
        successor = next(
            (child for child in properties if child.tag in after), None
        )
        if successor is None:
            properties.append(numbering)
        else:
            successor.addprevious(numbering)
    for child in list(numbering):
        numbering.remove(child)
    etree.SubElement(numbering, _W_NUMID).set(_W_VAL, "0")


def _write_paragraph_text(paragraph_element, text: str) -> None:
    """Replace a cloned paragraph's inline content with ``text``.

    The fallback for a paragraph the splice cannot map, and the writer for a
    new provision. ``w:pPr`` survives untouched — that is the whole of the
    paragraph-level promise (style, numbering, indent, spacing, and the run
    defaults the style carries). Everything else is rebuilt from the first
    run's properties.
    """
    run_properties = None
    for run in paragraph_element.iterchildren(_W_R):
        found = run.find(_W_RPR)
        run_properties = copy.deepcopy(found) if found is not None else None
        break
    for child in list(paragraph_element):
        if child.tag != _W_PPR:
            paragraph_element.remove(child)
    run = etree.SubElement(paragraph_element, _W_R)
    if run_properties is not None:
        run.append(run_properties)
    append_text(run, text)


def _blank_template(numbered: bool):
    """A last-resort paragraph for a document that anchors nothing."""
    paragraph = etree.Element(_W_P)
    if numbered:  # pragma: no cover - defensive; templates carry their own
        etree.SubElement(etree.SubElement(paragraph, _W_PPR), _W_NUMPR)
    return paragraph


# ---------------------------------------------------------------------------
# The walk: the current tree, in document order
# ---------------------------------------------------------------------------


@dataclass
class _Item:
    """One element the export emits, in current document order."""

    uid: str
    kind: str  # "section" | "part" | "article" | "paragraph"
    origin: int  # NO_ORIGIN when the element is new
    mode: str  # "text" | "verbatim" | "locked" | "new"
    text: str = ""
    template: int | None = None


def _classify(uid: str) -> tuple[str, int | None]:
    """``(kind, depth)`` from the stable id's shape."""
    if uid in ("sec", SECTION_TITLE_UID):
        return "section", None
    if re.fullmatch(r"pt\d+", uid):
        return "part", None
    if re.fullmatch(r"pt\d+\.a\d+", uid):
        return "article", None
    return "paragraph", uid.count(".p") - 1


@dataclass(frozen=True)
class _ArticleStyle:
    padded: bool = False
    dot: str = ""
    separator: str = " "


def _article_style(element) -> _ArticleStyle:
    """How a master writes its article numbers: ``1.01`` or ``1.1``, a
    trailing dot, and what separates the number from the title."""
    if element is None or element.tag != _W_P:
        return _ArticleStyle()
    match = _ARTICLE_HEADING_RE.match(_accept_all_paragraph_text(element).strip())
    if not match:
        return _ArticleStyle()
    _part, ordinal, dot, separator, _title = match.groups()
    return _ArticleStyle(
        padded=len(ordinal) == 2 and ordinal.startswith("0"),
        dot=dot,
        separator=separator,
    )


def _article_heading(
    part_number: int, index: int, title: str, style: _ArticleStyle
) -> str:
    ordinal = f"{index + 1:02d}" if style.padded else str(index + 1)
    return f"{part_number}.{ordinal}{style.dot}{style.separator}{title}".rstrip()


def _label_separator(element) -> str:
    """The whitespace a master writes after a typed label — usually a tab."""
    if element is None or element.tag != _W_P:
        return " "
    match = _LEADING_TOKEN_RE.match(_accept_all_paragraph_text(element).lstrip())
    return match.group(1) if match else " "


class _Walker:
    """Collects the export's items and chooses templates for new ones.

    A new element is cloned from its nearest kin OF ITS OWN KIND — a
    provision from a provision at its depth, an article heading from an
    article heading — preferring the last one emitted before it, then the
    first one in the upload. Cloning from "whatever came last" made a new
    article look like the provision above it.
    """

    def __init__(self, children: list, format_map: SourceFormatMap):
        self._children = children
        self._map = format_map
        self.items: list[_Item] = []
        self._last: dict[tuple, int] = {}
        self._last_any: int | None = None
        self._first: dict[tuple, int] = {}
        self._kind_at: dict[int, str] = {}
        for anchor in sorted(format_map.anchors, key=lambda a: a.origin_index):
            index = anchor.origin_index
            if anchor.label_kind and 0 <= index < len(children):
                self._kind_at.setdefault(index, anchor.label_kind)
            if anchor.locked or not 0 <= index < len(children):
                continue
            if children[index].tag != _W_P:
                continue
            for key in self._keys(*_classify(anchor.uid)):
                self._first.setdefault(key, index)

    @staticmethod
    def _keys(kind: str, depth: int | None) -> list[tuple]:
        if kind == "paragraph":
            return [("paragraph", depth), ("paragraph",)]
        if kind in ("part", "article"):
            return [(kind,)]
        return []

    # -- source access ---------------------------------------------------
    def origin(self, uid: str) -> int:
        anchor = self._map.anchor(uid)
        if anchor is None or anchor.origin_index == NO_ORIGIN:
            return NO_ORIGIN
        if not 0 <= anchor.origin_index < len(self._children):
            return NO_ORIGIN
        return anchor.origin_index

    def label_kind(self, uid: str) -> str:
        anchor = self._map.anchor(uid)
        return anchor.label_kind if anchor is not None else ""

    def _label_kind_at(self, index: int) -> str:
        recorded = self._kind_at.get(index)
        if recorded:
            return recorded
        element = self._children[index]
        if element.tag == _W_P and element.find(f"{_W_PPR}/{_W_NUMPR}") is not None:
            return LABEL_AUTO
        return LABEL_MANUAL

    # -- templates -------------------------------------------------------
    def template_for(self, kind: str, depth: int | None = None) -> int | None:
        if kind == "paragraph":
            order = [
                self._last.get(("paragraph", depth)),
                self._first.get(("paragraph", depth)),
                self._last.get(("paragraph",)),
                self._first.get(("paragraph",)),
            ]
        else:
            order = [self._last.get((kind,)), self._first.get((kind,))]
        for index in order:
            if index is not None:
                return index
        return self._last_any

    def template_label_kind(self, kind: str, depth: int | None = None) -> str:
        """The label convention a NEW element inherits from its template.

        A clone of an auto-numbered sibling is auto-numbered too, and Word
        will render its label — so writing one into the text as well would
        print it twice. Absence of ``w:numPr`` does not mean "manual": an
        unstructured import records no label at all.
        """
        index = self.template_for(kind, depth)
        if index is None:
            return LABEL_MANUAL
        return self._label_kind_at(index)

    def template_separator(self, kind: str, depth: int | None = None) -> str:
        index = self.template_for(kind, depth)
        if index is None or self._label_kind_at(index) != LABEL_MANUAL:
            return " "
        return _label_separator(self._children[index])

    def template_article_style(self) -> _ArticleStyle:
        index = self.template_for("article")
        if index is None or self._label_kind_at(index) != LABEL_MANUAL:
            return _ArticleStyle()
        return _article_style(self._children[index])

    def article_style_of(self, uid: str) -> _ArticleStyle:
        index = self.origin(uid)
        if index == NO_ORIGIN:
            return _ArticleStyle()
        return _article_style(self._children[index])

    def _remember(self, kind: str, depth: int | None, index: int) -> None:
        for key in self._keys(kind, depth):
            self._last[key] = index
        self._last_any = index

    # -- items -----------------------------------------------------------
    def add_verbatim(self, uid: str, kind: str) -> bool:
        """An anchored element exactly as it arrived, whatever it says.

        Used where the CALLER knows the content is unchanged but cannot
        reconstruct the original wording — the section header, whose line
        has several legitimate forms the parse folds into one number and
        title.
        """
        index = self.origin(uid)
        if index == NO_ORIGIN:
            return False
        self.items.append(_Item(uid, kind, index, "verbatim"))
        return True

    def add_locked(self, uid: str) -> bool:
        """A preserved block exactly as it arrived."""
        index = self.origin(uid)
        if index == NO_ORIGIN:
            return False
        self.items.append(_Item(uid, "paragraph", index, "locked"))
        return True

    def add_text(
        self, uid: str, kind: str, text: str, *, depth: int | None = None
    ) -> None:
        index = self.origin(uid)
        if index != NO_ORIGIN:
            if self._children[index].tag == _W_P:
                self._remember(kind, depth, index)
                self.items.append(_Item(uid, kind, index, "text", text))
            else:
                # An anchored non-paragraph (a table claimed by an editable
                # element) can only be emitted as itself.
                self.items.append(_Item(uid, kind, index, "verbatim"))
            return
        self.items.append(
            _Item(
                uid,
                kind,
                NO_ORIGIN,
                "new",
                text,
                template=self.template_for(kind, depth),
            )
        )


def _render_body(
    section: SpecSection,
    walker: _Walker,
    format_map: SourceFormatMap,
) -> None:
    if section.number or section.title:
        # While the section identity is exactly what was imported, reproduce
        # the header ELEMENT rather than rebuild its text. The parse folds
        # several legitimate header forms into one number and title — a
        # "SECTION 23 05 48" line, a keyword-less "23 05 48 — TITLE", a
        # number and title on separate lines — so rebuilding a canonical
        # form rewrites the firm's header on a NO-OP export. Caught in
        # review on PR #141 (Codex).
        unchanged_identity = (
            section.number == format_map.section_number
            and section.title == format_map.section_title
        )
        if unchanged_identity and walker.add_verbatim("sec", "section"):
            walker.add_verbatim(SECTION_TITLE_UID, "section")
            return _render_parts(section, walker)
        if format_map.header_source in (
            HEADER_SOURCE_FRONT_MATTER,
            HEADER_SOURCE_CHROME,
        ):
            # The identity lives in content this export carries through
            # verbatim anyway — a cover page's "Section Number:" field, the
            # page header/footer — so there is no header element to rewrite
            # and inventing one would print the section twice. A changed
            # identity is reported by the stale-identifier lint instead.
            return _render_parts(section, walker)
        header = " ".join(part for part in ("SECTION", section.number) if part)
        if walker.origin(SECTION_TITLE_UID) != NO_ORIGIN:
            walker.add_text("sec", "section", header.strip())
            walker.add_text(SECTION_TITLE_UID, "section", section.title)
        else:
            combined = f"{header} {section.title}".strip() if section.title else header
            walker.add_text("sec", "section", combined.strip())
    _render_parts(section, walker)


def _render_parts(section: SpecSection, walker: _Walker) -> None:
    for part in section.parts:
        if not part.articles and walker.origin(part.uid) == NO_ORIGIN:
            # SectionFormat always has three parts; the master may not have
            # written all three. Emitting a heading the upload never carried
            # would ADD content to the user's file.
            continue
        # ``Part.title`` already carries the whole heading line
        # ("PART 1 - GENERAL"), so an auto-numbered master is the only case
        # that needs the number stripped back off.
        label_kind = walker.label_kind(part.uid) or walker.template_label_kind(
            "part"
        )
        if label_kind == LABEL_AUTO:
            walker.add_text(part.uid, "part", _part_title_only(part))
        else:
            walker.add_text(part.uid, "part", part.title)
        for article_index, article in enumerate(part.articles):
            _render_article(part.number, article_index, article, walker)


def _part_title_only(part) -> str:
    """``"GENERAL"`` from ``"PART 1 - GENERAL"`` — Word renders the number."""
    _prefix, separator, remainder = part.title.partition(" - ")
    return remainder.strip() if separator else part.title


def _render_article(
    part_number: int, index: int, article: Article, walker: _Walker
) -> None:
    label_kind = walker.label_kind(article.uid)
    if label_kind:
        style = walker.article_style_of(article.uid)
    else:
        label_kind = walker.template_label_kind("article")
        style = walker.template_article_style()
    if label_kind == LABEL_AUTO:
        walker.add_text(article.uid, "article", article.title)
    else:
        # The master's own number format — "1.01" stays "1.01", "1.1 -
        # TITLE" keeps its dash — or an untouched heading is rewritten on
        # an export with no edits at all.
        walker.add_text(
            article.uid,
            "article",
            _article_heading(part_number, index, article.title, style),
        )
    for paragraph, label_index in labelled_paragraphs(article.paragraphs):
        _render_paragraph(paragraph, 0, label_index, walker)


def _render_paragraph(
    paragraph: Paragraph, depth: int, index: int, walker: _Walker
) -> None:
    if paragraph.locked:
        # A locked block with no source behind it cannot be invented: writing
        # a flattened table back as a paragraph would replace a grid with
        # pipe characters. Its children are still rendered either way —
        # the importer no longer lets a preserved block become a parent, but
        # a project saved before that fix can still carry one, and dropping
        # provisions on the floor is exactly the failure this guards.
        walker.add_locked(paragraph.uid)
        for child, child_label in labelled_paragraphs(paragraph.children):
            _render_paragraph(child, depth, child_label, walker)
        return
    label_kind = walker.label_kind(paragraph.uid)
    separator = " "
    if not label_kind:
        label_kind = walker.template_label_kind("paragraph", depth)
        # A new provision writes its kin's separator — the master's tab, not
        # a space. (An edited one keeps its own through the splice.)
        separator = walker.template_separator("paragraph", depth)
    if label_kind == LABEL_MANUAL:
        text = f"{_paragraph_label(depth, index)}{separator}{paragraph.text}"
    else:
        text = paragraph.text
    walker.add_text(paragraph.uid, "paragraph", text, depth=depth)
    for child, child_label in labelled_paragraphs(paragraph.children):
        _render_paragraph(child, depth + 1, child_label, walker)


# ---------------------------------------------------------------------------
# Assembly: unmodelled content, section breaks, and the render
# ---------------------------------------------------------------------------


@dataclass
class _Group:
    """Content bound to a POSITION in the upload rather than to an element.

    ``position`` is a source body index (a half-step between two indexes is
    "right after" the lower one). Every emitted element whose origin sits
    before it is "above" the group; every one after it is "below".
    """

    position: float
    members: list[int] = field(default_factory=list)
    holder: int | None = None  # a provision whose own w:pPr holds the break
    leftover: bool = False  # emit an empty paragraph holding the holder's break
    gap: int = 0


# ---------------------------------------------------------------------------
# The emission plan (D-1 of the Redline-on-your-original plan)
# ---------------------------------------------------------------------------

#: A source element emitted as a byte-identical clone: an untouched
#: provision, a heading reproduced verbatim, a preserved block.
RECORD_KEPT = "kept"
#: A source paragraph whose words changed: the word-level splice into its
#: own runs, or — when the splice cannot map it — the first-run fallback.
RECORD_SPLICED = "spliced"
#: A new element, cloned from its nearest kin of its own kind.
RECORD_INSERTED = "inserted"
#: An anchored source element the user removed.
RECORD_DELETED = "deleted"
#: Unmodelled source content, carried through verbatim.
RECORD_CARRIED = "carried"
#: Unmodelled source content the clean export omits: the blank spacers of a
#: deleted element, a stale "(Not used.)" line.
RECORD_DROPPED = "dropped"
#: The empty paragraph a displaced section-break holder leaves where its
#: break belongs — the holder's own paragraph properties, nothing else.
RECORD_LEFTOVER = "leftover"

RECORD_KINDS = frozenset(
    {
        RECORD_KEPT,
        RECORD_SPLICED,
        RECORD_INSERTED,
        RECORD_DELETED,
        RECORD_CARRIED,
        RECORD_DROPPED,
        RECORD_LEFTOVER,
    }
)
#: The kinds the clean export renders nothing for.
_REMOVED_KINDS = frozenset({RECORD_DELETED, RECORD_DROPPED})


@dataclass
class Record:
    """One body child of the emission plan.

    ``source`` is the upload body index the record reproduces, removes or
    derives from (``NO_ORIGIN`` for an inserted element). The clean export
    renders every record except ``deleted`` and ``dropped``; the redline
    renders every record, marking what Accept All and Reject All disagree
    about. One plan feeding both is what makes "Accept All equals the
    formatted export" true by construction.
    """

    kind: str
    source: int = NO_ORIGIN
    item: _Item | None = None
    #: The clean export emits the source WITHOUT its section break: the
    #: break was displaced and is left behind in a ``leftover`` record.
    strip_break: bool = False
    #: ``spliced``: the character map and edit script, or — when the splice
    #: could not map the paragraph — the fallback reason instead.
    pmap: ParagraphMap | None = None
    ops: list[SpliceOp] | None = None
    fallback: str = ""
    #: ``inserted``: the kin the new element is cloned from.
    template: int | None = None
    #: Redline only: this record is one copy of a MOVED element — an
    #: ``inserted`` copy at its new position or a ``deleted`` copy at its
    #: old one (Phase 1, D-1/D-3).
    moved: bool = False


class _Assembler:
    def __init__(
        self,
        children: list,
        format_map: SourceFormatMap,
        walker: _Walker,
        section: SpecSection,
    ):
        self._children = children
        self._walker = walker
        self.items = walker.items
        self.stats: dict = {
            "cloned": 0,
            "spliced": 0,
            "fallback": {},
            "inserted": 0,
            "preserved": 0,
            "break_leftovers": 0,
            "not_used_dropped": 0,
        }
        anchored: dict[int, str] = {}
        for anchor in format_map.anchors:
            if 0 <= anchor.origin_index < len(children):
                anchored.setdefault(anchor.origin_index, anchor.uid)
        self._anchored = anchored
        self._anchored_order = sorted(anchored)
        self._position: dict[int, int] = {}
        for index, item in enumerate(self.items):
            if item.origin != NO_ORIGIN:
                self._position.setdefault(item.origin, index)
        self._parts_with_articles = {
            part.uid for part in section.parts if part.articles
        }
        self._dropped: set[int] = set()
        # The blank spacers of a deleted element: the clean export drops
        # them with it, the redline marks them deleted with it.
        self._dropped_spacers: list[int] = []
        self._tails: dict[int, list[int]] = {}
        self._trailing: list[int] = []
        self._strip_break: set[int] = set()
        self._groups: list[_Group] = []

    # -- classification ----------------------------------------------------
    def _is_stale_not_used(self, index: int, above: int) -> bool:
        """A PART's "(Not used.)" line once that PART has an article.

        The importer skips the line, so it is unmodelled content; left alone
        the export printed "2.1 ISOLATORS" and then "(Not used.)" under it.
        A line that also holds a section break is never dropped — no edit
        loses a break.
        """
        element = self._children[index]
        if element.tag != _W_P or _holds_break(element):
            return False
        if _normalized(_accept_all_paragraph_text(element)).casefold() != _NOT_USED:
            return False
        uid = self._anchored.get(above, "")
        return _classify(uid)[0] == "part" and uid in self._parts_with_articles

    def _classify_runs(self) -> None:
        """Assign every unmodelled body child to exactly one place.

        The children between two anchored elements lead the later one (they
        travel with it), except that everything up to and including the LAST
        section break among them is bound to its position: a break is the
        end of the section above it, not the start of the element below.
        Everything after the last anchored element is trailing content —
        ``END OF SECTION``, an appendix — carried verbatim, blanks, page
        breaks and section breaks included.
        """
        previous = -1
        boundaries = self._anchored_order + [len(self._children)]
        for origin in boundaries:
            between = list(range(previous + 1, origin))
            for index in between:
                if self._is_stale_not_used(index, previous):
                    self._dropped.add(index)
            between = [i for i in between if i not in self._dropped]
            if origin == len(self._children):
                self._trailing = between
                break
            breaks = [i for i in between if _holds_break(self._children[i])]
            if breaks:
                last = breaks[-1]
                cut = between.index(last) + 1
                self._groups.append(_Group(position=last, members=between[:cut]))
                between = between[cut:]
            if origin in self._position:
                self._tails[origin] = between
            else:
                # The element below was deleted. Its spacers go with it; what
                # the tree never modelled (a picture-only paragraph, a body
                # bookmark) stays where it was instead of being swept to the
                # end of the file.
                kept = []
                for i in between:
                    if _is_blank_paragraph(self._children[i]):
                        self._dropped_spacers.append(i)
                    else:
                        kept.append(i)
                if kept:
                    self._groups.append(_Group(position=origin - 0.5, members=kept))
            previous = origin
        for origin in self._anchored_order:
            if _holds_break(self._children[origin]):
                self._groups.append(
                    _Group(
                        position=origin + 0.5,
                        holder=origin,
                        leftover=origin not in self._position,
                    )
                )

    # -- placement ---------------------------------------------------------
    def _nearest_emitted_above(self, position: float) -> int | None:
        found = None
        for origin in self._anchored_order:
            if origin >= position:
                break
            if origin in self._position:
                found = origin
        return found

    def _place(self) -> None:
        """Choose each group's gap in the output.

        A gap is scored by how many emitted elements it keeps on the right
        side: those that were above the group's position before it, those
        that were below it after it. The best-scoring gap wins; among ties,
        the one right after the nearest surviving element that was above it
        in the upload ("a break belongs to the content above it"), then any
        gap right after an element from above, then the earliest. Groups are
        placed in upload order and never cross each other, so sections keep
        their order.
        """
        count = len(self.items)
        origins = [item.origin for item in self.items]
        floor = 0
        for group in sorted(self._groups, key=lambda g: g.position):
            above = [
                origin != NO_ORIGIN and origin < group.position for origin in origins
            ]
            below = [
                origin != NO_ORIGIN and origin > group.position for origin in origins
            ]
            before = [0] * (count + 1)
            for index in range(count):
                before[index + 1] = before[index] + above[index]
            after = [0] * (count + 1)
            for index in range(count - 1, -1, -1):
                after[index] = after[index + 1] + below[index]
            scores = [before[gap] + after[gap] for gap in range(count + 1)]
            best = max(scores[floor:])
            candidates = [
                gap for gap in range(floor, count + 1) if scores[gap] == best
            ]
            chosen = None
            nearest = self._nearest_emitted_above(group.position)
            if nearest is not None and self._position[nearest] + 1 in candidates:
                chosen = self._position[nearest] + 1
            if chosen is None:
                after_above = [gap for gap in candidates if gap and above[gap - 1]]
                chosen = after_above[0] if after_above else candidates[0]
            group.gap = chosen
            floor = chosen
            if group.holder is not None and not group.leftover:
                if chosen == self._position[group.holder] + 1:
                    # The holder is still where its break belongs: it keeps
                    # it, in its own w:pPr, and nothing else is emitted.
                    group.holder = None
                else:
                    self._strip_break.add(group.holder)
                    group.leftover = True
            if group.leftover:
                self.stats["break_leftovers"] += 1

    # -- the plan ----------------------------------------------------------
    def _item_record(self, item: _Item) -> Record:
        """What the export does with one tree element, decided once.

        The decision the renderers used to make while rendering — clone,
        splice or rebuild — is made here, so the clean export and the
        redline read the same answer.
        """
        strip_break = item.origin in self._strip_break
        if item.mode in ("verbatim", "locked"):
            self.stats["preserved"] += 1
            return Record(
                RECORD_KEPT, item.origin, item, strip_break=strip_break
            )
        if item.mode == "new":
            self.stats["inserted"] += 1
            return Record(RECORD_INSERTED, NO_ORIGIN, item, template=item.template)
        source = self._children[item.origin]
        source_text = _accept_all_paragraph_text(source)
        revised = _element_has_tracked_changes(source)
        if not revised and _normalized(source_text) == _normalized(item.text):
            # Compare the NORMALIZED forms. The importer folds runs of
            # whitespace, so a source paragraph containing a double space
            # after a period — which is most office masters — would otherwise
            # never match its own semantic text. Caught in review on PR #141
            # (Codex). This is the untouched-provision guarantee: a
            # byte-identical clone, markup and all.
            self.stats["cloned"] += 1
            return Record(
                RECORD_KEPT, item.origin, item, strip_break=strip_break
            )
        if revised:
            pmap, reason = None, FALLBACK_REVISIONS
        else:
            pmap, reason = map_paragraph(source, expected_text=source_text)
        if pmap is not None:
            self.stats["spliced"] += 1
            return Record(
                RECORD_SPLICED,
                item.origin,
                item,
                strip_break=strip_break,
                pmap=pmap,
                ops=plan_splice(pmap.text, item.text),
            )
        fallbacks = self.stats["fallback"]
        fallbacks[reason] = fallbacks.get(reason, 0) + 1
        return Record(
            RECORD_SPLICED,
            item.origin,
            item,
            strip_break=strip_break,
            fallback=reason,
        )

    def _removed_records(self) -> list[Record]:
        """What the upload holds and the clean export does not, in upload
        order: removed anchored elements, their blank spacers, and stale
        "(Not used.)" lines. The clean export renders none of them; the
        redline marks each one deleted."""
        holders = {
            group.holder
            for group in self._groups
            if group.holder is not None and group.leftover
        }
        removed = [
            Record(RECORD_DELETED, origin)
            for origin in self._anchored_order
            if origin not in self._position and origin not in holders
        ]
        removed.extend(
            Record(RECORD_DROPPED, index)
            for index in sorted(self._dropped) + self._dropped_spacers
        )
        removed.sort(key=lambda record: record.source)
        return removed

    def plan(self) -> tuple[list[Record], list[Record]]:
        """``(records, removed)``: the clean export's body children in
        output order, and what it leaves out of the upload."""
        self._classify_runs()
        self._place()
        self.stats["not_used_dropped"] = len(self._dropped)
        by_gap: dict[int, list[_Group]] = {}
        for group in sorted(self._groups, key=lambda g: g.position):
            if group.members or group.leftover:
                by_gap.setdefault(group.gap, []).append(group)
        records: list[Record] = []
        emitted_tails: set[int] = set()
        for gap in range(len(self.items) + 1):
            for group in by_gap.get(gap, ()):
                records.extend(Record(RECORD_CARRIED, i) for i in group.members)
                if group.leftover and group.holder is not None:
                    records.append(Record(RECORD_LEFTOVER, group.holder))
            if gap == len(self.items):
                break
            item = self.items[gap]
            if item.origin != NO_ORIGIN and item.origin not in emitted_tails:
                emitted_tails.add(item.origin)
                records.extend(
                    Record(RECORD_CARRIED, i)
                    for i in self._tails.get(item.origin, ())
                )
            records.append(self._item_record(item))
        records.extend(Record(RECORD_CARRIED, i) for i in self._trailing)
        return records, self._removed_records()

    # -- the clean rendering ----------------------------------------------
    def render_leftover(self, holder: int):
        """An empty paragraph holding a displaced provision's section break.

        The provision's own paragraph properties, so the break keeps its
        place and spacing — minus its text, its identity (the provision may
        still exist elsewhere) and its list numbering.
        """
        paragraph = copy.deepcopy(self._children[holder])
        for child in list(paragraph):
            if child.tag != _W_PPR:
                paragraph.remove(child)
        _strip_identity(paragraph)
        if self.holder_auto_numbered(holder):
            _cancel_numbering(paragraph)
        return paragraph

    def holder_auto_numbered(self, holder: int) -> bool:
        uid = self._anchored.get(holder, "")
        return self._walker.label_kind(uid) == LABEL_AUTO

    def render_inserted(self, record: Record):
        """A new element: its kin's formatting, never its identity."""
        if record.template is None:
            element = _blank_template(False)
        else:
            element = copy.deepcopy(self._children[record.template])
            # Clone hygiene: the kin's formatting, never its identity.
            _strip_break(element)
            _strip_identity(element)
        _write_paragraph_text(element, record.item.text)
        return element

    def render_accepted(self, record: Record):
        """``record`` as the clean export emits it (``None``: nothing)."""
        kind = record.kind
        if kind in _REMOVED_KINDS:
            return None
        if kind == RECORD_INSERTED:
            return self.render_inserted(record)
        if kind == RECORD_LEFTOVER:
            return self.render_leftover(record.source)
        if kind == RECORD_SPLICED:
            if record.pmap is not None:
                element = render_clean(record.pmap, record.ops or [])
            else:
                element = copy.deepcopy(self._children[record.source])
                _write_paragraph_text(element, record.item.text)
        else:  # kept, carried
            element = copy.deepcopy(self._children[record.source])
        if record.strip_break:
            _strip_break(element)
        return element

    def assemble(self) -> list:
        records, _removed = self.plan()
        rendered = (self.render_accepted(record) for record in records)
        return [element for element in rendered if element is not None]


def render_preserving_docx(
    *,
    source_bytes: bytes,
    format_map: SourceFormatMap,
    current: SpecSection,
    stats: dict | None = None,
) -> bytes:
    """Return the upload with a rebuilt body carrying ``current``.

    ``stats``, when given, is filled with counts for the export's
    diagnostics: elements cloned, spliced, rebuilt by the fallback (by
    reason), inserted and preserved; empty paragraphs left holding a
    displaced provision's section break (``break_leftovers``); stale
    "(Not used.)" lines dropped. Counts only — never provision text.
    """
    if not isinstance(source_bytes, bytes):
        raise TypeError("source_bytes must be bytes")
    if format_map is None:
        raise SourceRenderError(
            "This document has no retained formatting map, so its original "
            "layout cannot be reproduced."
        )
    if not format_map.matches(source_bytes):
        # Origin indexes are positions in a specific package. Read beside
        # different bytes they address whatever now sits at that index, so
        # refusing is the only safe answer.
        raise SourceRenderError(
            "The retained formatting map does not describe the retained "
            "source document."
        )
    try:
        with zipfile.ZipFile(BytesIO(source_bytes), "r") as archive:
            document_xml = archive.read(_DOCUMENT_PART)
    except (KeyError, zipfile.BadZipFile, RuntimeError) as exc:
        raise SourceRenderError(
            "The retained source document could not be opened."
        ) from exc
    try:
        # python-docx's parser, NOT etree.fromstring: the Accept-All text
        # reader depends on python-docx's registered oxml element classes,
        # and a plain lxml tree reports every paragraph as empty — which
        # silently sends untouched provisions down the rewrite path and
        # loses the byte-identical-clone guarantee.
        root = parse_xml(document_xml)
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise SourceRenderError(
            "The retained source document XML could not be parsed."
        ) from exc
    body = root.find(qn("w:body"))
    if body is None:
        raise SourceRenderError("The retained source document has no body.")

    children = [child for child in body.iterchildren() if isinstance(child.tag, str)]
    # Word requires the body's section properties to remain its last child;
    # they carry page size, margins and the header/footer references, which
    # is most of what "keep my formatting" means.
    trailing_sect_pr = (
        children[-1] if children and children[-1].tag == _W_SECTPR else None
    )
    content = children[:-1] if trailing_sect_pr is not None else children

    walker = _Walker(content, format_map)
    _render_body(current, walker, format_map)
    assembler = _Assembler(content, format_map, walker, current)
    rendered = assembler.assemble()
    if stats is not None:
        stats.update(assembler.stats)

    for child in list(body):
        body.remove(child)
    for element in rendered:
        body.append(element)
    if trailing_sect_pr is not None:
        body.append(copy.deepcopy(trailing_sect_pr))

    rebuilt = etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", standalone=True
    )
    return replace_document_xml_raw(source_bytes, rebuilt)


__all__ = [
    "SECTION_TITLE_UID",
    "SourceRenderError",
    "render_preserving_docx",
]
