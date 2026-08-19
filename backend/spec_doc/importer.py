"""Master-spec ``.docx`` import: parse an office master into the tree.

The extraction mechanics are ported from Claude-Spec-Critic
``src/input/extractor.py`` — specifically the pieces that took field
sessions to get right: the **Accept-All tracked-changes text resolution**
(python-docx ``Paragraph.text`` silently produces a hybrid that matches
neither Accept-All nor Reject-All when a redline is pending), pending-
revision *detection* across body and tables, and the drawing-heavy
**content-loss warning**. The SectionFormat tree builder on top is
Build-a-Spec-native: Spec Critic extracts flat text for review; drafting
needs the PART → article → paragraph hierarchy.

Parsing philosophy — **keep everything, warn loudly**: office masters vary
wildly, so unrecognized structure is never dropped. A paragraph that fits
no heading pattern becomes a level-0 paragraph under the current article; a
paragraph arriving before any article lands in a synthetic ``IMPORTED
CONTENT`` article; nesting deeper than SectionFormat's four levels clamps
to level four. Every such decision is recorded in ``ImportResult.warnings``
so the reviewer knows exactly where the parse guessed. Every imported block
enters with status ``imported`` (not yet reviewed for this project) and the
interview pivots to gap-and-adapt mode.

Both manual-label masters ("A. Provide...") and Word-auto-numbered masters
(labels live in ``w:numPr``, not the text) are handled: explicit text
labels win; otherwise the paragraph's numbering indent level (``ilvl``)
drives the depth — placed *relative to its own list*, because ``ilvl`` is
an indent level inside a numbering definition and not an absolute outline
depth (see :meth:`_TreeBuilder.numbered_paragraph`).

Auto-numbered PART and ARTICLE **headings** are recognized through the
numbering definitions themselves: a ``w:numPr`` paragraph whose level's
``lvlText`` renders the literal word ``PART`` is a part heading, and one
whose ``lvlText`` is two decimal placeholders joined by a dot (``%1.%2`` —
the auto-numbered form of the ``2.01 TITLE`` label) is an article heading.
That is the structural signal the visible text cannot carry — an
auto-numbered article's text is just ``SUMMARY`` — and it is provably safe
for the export/re-import round trip: the app's own numbering definitions
use only single-token ``lvlText``s (see ``word_numbering._LEVELS``), so
neither grammar can match a normalized export. ``w:pStyle`` is deliberately
NOT consulted: style names are localized and free-form, so they are weak
evidence beside the label grammar the reader actually sees; revisit only
with corpus evidence that real masters need it.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph as DocxParagraph
from lxml.etree import XMLSyntaxError

from .model import (
    MAX_PARAGRAPH_DEPTH,
    Article,
    Paragraph,
    SpecSection,
    iter_paragraphs,
)
from .source_mapping import (
    SourceBodyMap,
    SourceParagraphBinding,
    bind_opaque_projection,
    bind_source_paragraph,
    build_source_body_map,
)

# ---------------------------------------------------------------------------
# Accept-All tracked-changes text resolution
# [PORT ≈verbatim: Spec Critic src/input/extractor.py]
# ---------------------------------------------------------------------------

_W_R = qn("w:r")
_W_HYPERLINK = qn("w:hyperlink")
_W_INS = qn("w:ins")
_W_DEL = qn("w:del")
_W_MOVE_FROM = qn("w:moveFrom")
_W_MOVE_TO = qn("w:moveTo")

_ACCEPTED_REVISION_WRAPPERS = frozenset({_W_INS, _W_MOVE_TO})
_REVISION_MARKER_TAGS = (_W_INS, _W_DEL, _W_MOVE_FROM, _W_MOVE_TO)


def _collect_accept_all_text(container, parts: list[str]) -> None:
    """Append the Accept-All run/hyperlink text under ``container``.

    Mirrors python-docx ``CT_P.text`` with one addition: descends through
    *accepted* revision wrappers (``<w:ins>`` / ``<w:moveTo>``) and skips
    ``<w:del>`` / ``<w:moveFrom>`` entirely — the text that remains once
    the redline is accepted, i.e. what will actually be issued. A document
    with no revision markup yields output byte-identical to
    ``Paragraph.text``.
    """
    for child in container:
        tag = child.tag
        if not isinstance(tag, str):
            continue  # comments / processing instructions carry no run text
        if tag == _W_R or tag == _W_HYPERLINK:
            parts.append(child.text or "")
        elif tag in _ACCEPTED_REVISION_WRAPPERS:
            _collect_accept_all_text(child, parts)


def _accept_all_paragraph_text(p_el) -> str:
    parts: list[str] = []
    _collect_accept_all_text(p_el, parts)
    return "".join(parts)


def _element_has_tracked_changes(el) -> bool:
    return any(el.find(".//" + tag) is not None for tag in _REVISION_MARKER_TAGS)


# ---------------------------------------------------------------------------
# Content-loss warning [PORT: Spec Critic extractor._detect_content_loss_warning]
# ---------------------------------------------------------------------------

_CONTENT_LOSS_WARNING_THRESHOLD = 0.20


def _detect_content_loss_warning(body) -> str | None:
    drawing_qn = qn("w:drawing")
    pict_qn = qn("w:pict")
    object_qn = qn("w:object")
    sect_pr_qn = qn("w:sectPr")

    total = 0
    non_text = 0
    for child in body:
        if child.tag == sect_pr_qn:
            continue
        total += 1
        if (
            child.find(".//" + drawing_qn) is not None
            or child.find(".//" + pict_qn) is not None
            or child.find(".//" + object_qn) is not None
        ):
            non_text += 1
    if total == 0 or non_text == 0:
        return None
    proportion = non_text / total
    if proportion <= _CONTENT_LOSS_WARNING_THRESHOLD:
        return None
    return (
        f"The master contains {round(proportion * 100)}% non-text elements "
        "(drawings, pictures, or embedded objects). Some content may not "
        "have been imported — verify against the source visually."
    )


# ---------------------------------------------------------------------------
# Heading / label patterns (Build-a-Spec-native tree heuristics)
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(
    r"^SECTION\s+(\d{2})\s*(\d{2})\s*(\d{2})(?:\.(\d{2}))?\b\s*[-–—]?\s*(.*)$",
    re.IGNORECASE,
)
# PART 1-3 are SectionFormat's parts; 4/5 are accepted as spec structure and
# mapped (loudly) onto PART 3 rather than demoting the file to unstructured.
_PART_RE = re.compile(r"^PART\s*([1-5])\b", re.IGNORECASE)
_END_RE = re.compile(r"^END\s+OF\s+SECTION\b", re.IGNORECASE)
# "1.1 SUMMARY" / "1.01 SUMMARY" / "2.3 - PIPING" (part digit + article no.)
_ARTICLE_RE = re.compile(r"^([123])\.(\d{1,2})\.?\s+[-–—]?\s*(\S.*)$")
# A header line without the SECTION keyword ("23 05 48 — TITLE"). Consulted
# for the FIRST content line only — anywhere else six digits and a dash are
# far more likely to be a provision than a header.
_BARE_SECTION_RE = re.compile(
    r"^(\d{2})\s?(\d{2})\s?(\d{2})(?:\.(\d{2}))?\s*[-–—]\s*(\S.*)$"
)
# Manual paragraph labels by depth.
_LEVEL_RES = (
    re.compile(r"^([A-Z]{1,2})\.\s+(\S.*)$"),  # A.  (depth 0)
    re.compile(r"^(\d{1,2})\.\s+(\S.*)$"),  # 1.  (depth 1)
    re.compile(r"^([a-z]{1,2})\.\s+(\S.*)$"),  # a.  (depth 2)
    re.compile(r"^(\d{1,2})\)\s+(\S.*)$"),  # 1)  (depth 3)
)

# The label grammars that promote a ``w:numPr`` paragraph to a HEADING. A
# level whose rendered label carries the literal word PART is a part heading
# whatever its number format ("PART %1" under decimal or upperRoman alike);
# one that renders two decimal placeholders joined by a dot is the
# auto-numbered form of the "2.01 TITLE" article label. Everything else —
# including every ``lvlText`` the app's own normalized exports write
# ("%1.", "%2.", "%3.", "%4)") — stays a provision, which is what keeps the
# export/re-import round trip untouched by construction.
_PART_LVLTEXT_RE = re.compile(r"\bPART\b", re.IGNORECASE)
_ARTICLE_LVLTEXT_RE = re.compile(r"^%\d+\.%\d+\.?\s*$")
_ARTICLE_NUM_FMTS = frozenset({"", "decimal", "decimalZero"})


# Said once, at the top of the warning list, when a file carries none of the
# three SectionFormat markers. The tree is still SectionFormat underneath (the
# whole editing/lint/diff/QC machinery is typed to it), so this states plainly
# what the app did rather than letting the panel imply the document always had
# a section header and three parts.
UNSTRUCTURED_IMPORT_WARNING = (
    "This file does not look like a SectionFormat spec section — no SECTION "
    "number, PART heading, or numbered article was found. Its content was "
    "imported in document order under a placeholder article, and the original "
    "file is retained exactly. Set a section number and title when you are "
    "ready to turn it into a spec section."
)


@dataclass
class ImportResult:
    """A parsed master: the tree plus the parse's honesty trail."""

    section: SpecSection
    warnings: list[str] = field(default_factory=list)
    tracked_changes_detected: bool = False
    imported_block_count: int = 0
    skipped_empty_count: int = 0
    # Immutable UID -> source-body anchors for P1 source-preserving export.
    # Kept outside SpecSection/version snapshots and never sent to the model.
    source_map: SourceBodyMap | None = None
    # True when the parse recognized at least one SectionFormat marker (a
    # SECTION line, a PART heading, or an ``N.M`` article). False means the
    # spec scaffolding around the content — section header, empty parts, the
    # synthetic article — is entirely the importer's, so the UI must not
    # present it as something the source document carried. Defaults True so a
    # caller that never sets it keeps the historical posture.
    spec_shape_detected: bool = True


class MasterImportError(ValueError):
    """The file could not be parsed as a master spec at all."""


def _numbering_level(paragraph: DocxParagraph) -> int | None:
    """The Word auto-numbering indent level (``ilvl``) or ``None``.

    Auto-numbered masters carry no visible "A."/"1." text — the label
    lives in the numbering definition. It is an indent level *within* that
    definition, not an outline depth — see
    :meth:`_TreeBuilder.numbered_paragraph`, which places it.
    """
    p_pr = paragraph._p.pPr
    if p_pr is None or p_pr.numPr is None or p_pr.numPr.ilvl is None:
        return None
    ilvl = p_pr.numPr.ilvl.val
    return int(ilvl) if ilvl is not None else None


def _numbering_num_id(paragraph: DocxParagraph) -> int | None:
    """The paragraph's numbering definition id (``numId``) or ``None``.

    ``numId`` 0 is OOXML for "no numbering" (it cancels an inherited
    definition), so it reads as ``None`` here — a cancelled definition has
    no label grammar to consult.
    """
    p_pr = paragraph._p.pPr
    if p_pr is None or p_pr.numPr is None or p_pr.numPr.numId is None:
        return None
    val = p_pr.numPr.numId.val
    if val is None:
        return None
    num_id = int(val)
    return num_id if num_id > 0 else None


def _lvl_entry(lvl_el) -> tuple[int, tuple[str, str]] | None:
    """One ``w:lvl`` element as ``(ilvl, (numFmt, lvlText))``, or ``None``."""
    raw_ilvl = lvl_el.get(qn("w:ilvl"))
    if raw_ilvl is None:
        return None
    try:
        ilvl = int(raw_ilvl)
    except ValueError:
        return None
    fmt_el = lvl_el.find(qn("w:numFmt"))
    text_el = lvl_el.find(qn("w:lvlText"))
    num_fmt = fmt_el.get(qn("w:val")) if fmt_el is not None else ""
    lvl_text = text_el.get(qn("w:val")) if text_el is not None else ""
    return ilvl, (num_fmt or "", lvl_text or "")


def _load_numbering_catalog(document) -> dict[tuple[int, int], tuple[str, str]]:
    """``(numId, ilvl) -> (numFmt, lvlText)`` from the package's numbering part.

    Read via the relationship directly — python-docx's ``numbering_part``
    property CREATES an empty part when none exists, and an importer must
    never mutate the package it is inspecting. A per-``num`` ``lvlOverride``
    that redefines a level replaces the abstract's grammar for it, so the
    label the reader actually sees is the one that decides. Any absence or
    malformation degrades to an empty catalog, i.e. exactly the
    no-promotion behavior every pre-catalog import had.
    """
    try:
        part = document.part.part_related_by(RELATIONSHIP_TYPE.NUMBERING)
        root = part.element
    except (KeyError, AttributeError, ValueError):
        return {}
    try:
        abstract: dict[str, dict[int, tuple[str, str]]] = {}
        for abstract_el in root.findall(qn("w:abstractNum")):
            abstract_id = abstract_el.get(qn("w:abstractNumId"))
            if abstract_id is None:
                continue
            levels: dict[int, tuple[str, str]] = {}
            for lvl_el in abstract_el.findall(qn("w:lvl")):
                entry = _lvl_entry(lvl_el)
                if entry is not None:
                    levels[entry[0]] = entry[1]
            abstract[abstract_id] = levels
        catalog: dict[tuple[int, int], tuple[str, str]] = {}
        for num_el in root.findall(qn("w:num")):
            raw_num_id = num_el.get(qn("w:numId"))
            abstract_ref = num_el.find(qn("w:abstractNumId"))
            if raw_num_id is None or abstract_ref is None:
                continue
            try:
                num_id = int(raw_num_id)
            except ValueError:
                continue
            for ilvl, entry in abstract.get(
                abstract_ref.get(qn("w:val")), {}
            ).items():
                catalog[(num_id, ilvl)] = entry
            for override in num_el.findall(qn("w:lvlOverride")):
                lvl_el = override.find(qn("w:lvl"))
                if lvl_el is None:
                    continue
                entry = _lvl_entry(lvl_el)
                if entry is not None:
                    catalog[(num_id, entry[0])] = entry[1]
        return catalog
    except Exception:  # noqa: BLE001 - a malformed numbering part is no catalog
        return {}


def _promoted_heading_kind(
    catalog: dict[tuple[int, int], tuple[str, str]],
    paragraph: DocxParagraph,
    ilvl: int,
) -> str:
    """``"part"`` / ``"article"`` when the numbering label says so, else ``""``."""
    if not catalog:
        return ""
    num_id = _numbering_num_id(paragraph)
    if num_id is None:
        return ""
    entry = catalog.get((num_id, ilvl))
    if entry is None:
        return ""
    num_fmt, lvl_text = entry
    if _PART_LVLTEXT_RE.search(lvl_text):
        return "part"
    if _ARTICLE_LVLTEXT_RE.match(lvl_text) and num_fmt in _ARTICLE_NUM_FMTS:
        return "article"
    return ""


@dataclass(frozen=True)
class _BodyTextEntry:
    text: str
    paragraph: DocxParagraph | None
    body_child_index: int
    source_element: object
    opaque_blocker: str = ""


def _iter_body_texts(document) -> list[_BodyTextEntry]:
    """Body content in document order: (accept-all text, paragraph or None).

    Tables are flattened row by row (cells joined with `` | ``) — spec
    masters use tables mostly for schedules; the content survives as
    paragraphs and the caller records a warning. The paragraph object is
    carried for numbering-level access (None for table rows).
    """
    results: list[_BodyTextEntry] = []
    body = document.element.body
    source_index = 0
    for child in body.iterchildren():
        if not isinstance(child.tag, str):
            continue
        body_child_index = source_index
        source_index += 1
        if child.tag == qn("w:p"):
            paragraph = DocxParagraph(child, document)
            results.append(
                _BodyTextEntry(
                    _accept_all_paragraph_text(child),
                    paragraph,
                    body_child_index,
                    child,
                )
            )
        elif child.tag == qn("w:tbl"):
            table = DocxTable(child, document)
            for row in table.rows:
                cells = []
                for cell in row.cells:
                    cell_text = " ".join(
                        _accept_all_paragraph_text(p._p) for p in cell.paragraphs
                    ).strip()
                    cells.append(cell_text)
                text = " | ".join(c for c in cells if c)
                if text:
                    results.append(
                        _BodyTextEntry(
                            text,
                            None,
                            body_child_index,
                            child,
                            "table_projection",
                        )
                    )
    return results


@dataclass(frozen=True)
class ReferenceExtraction:
    """Plain readable text pulled out of an attachment for model context.

    The counterpart to :func:`parse_master_docx` for files that are *not*
    becoming the document: no tree, no source map, no structural heuristics —
    just the readable text in document order. For a ``.docx`` that means the
    body resolved through the same Accept-All revision handling and table
    flattening a master import uses, so a reference file reads the way it does
    in Word.

    The type is shared with the non-Word extractors in
    ``backend/reference_extract.py`` (PDF, text, XML, CSV), which set ``kind``
    and may report ``warnings`` about what the read left out — a truncated
    page range, undecodable pages, a non-UTF-8 encoding. ``tracked_changes``
    is meaningful only for Word.
    """

    text: str
    block_count: int
    tracked_changes: bool
    kind: str = "docx"
    warnings: tuple[str, ...] = ()


def extract_reference_text(filepath: str | Path) -> ReferenceExtraction:
    """Extract a reference ``.docx``'s body text. Raises
    :class:`MasterImportError` for anything unreadable."""
    filepath = Path(filepath)
    try:
        document = Document(str(filepath))
    except (
        PackageNotFoundError,
        zipfile.BadZipFile,
        XMLSyntaxError,
        KeyError,
        ValueError,
    ) as exc:
        raise MasterImportError(
            "That file is not a readable .docx document."
        ) from exc

    lines: list[str] = []
    for entry in _iter_body_texts(document):
        text = " ".join(entry.text.split())
        if text:
            lines.append(text)
    return ReferenceExtraction(
        text="\n".join(lines),
        block_count=len(lines),
        tracked_changes=_element_has_tracked_changes(document.element.body),
    )


class _TreeBuilder:
    """Builds the SpecSection with the same uid/seq discipline as apply."""

    def __init__(self) -> None:
        self.section = SpecSection.empty()
        self.current_part = None
        self.current_article: Article | None = None
        # Paragraph stack by depth for nesting (index = depth).
        self.stack: list[Paragraph] = []
        # How far this article's Word numbering has been shifted shallower —
        # see numbered_paragraph. Resets with the stack it is relative to.
        self.numbering_offset = 0
        self.warnings: list[str] = []
        # warning index -> the uid of the element the warning is about, so
        # the parse can append a FINDABLE reference once the tree is built.
        # "Line N" alone counts body children (blank paragraphs and table
        # rows included) — a number the user can locate nowhere in Word or
        # in the panel.
        self.warning_uids: dict[int, str] = {}
        self.imported_count = 0

    def part(self, number: int):
        self.current_part = self.section.parts[number - 1]
        self.current_article = None
        self.stack = []
        self.numbering_offset = 0

    def article(self, part_number: int, title: str) -> None:
        self.part(part_number)
        part = self.current_part
        article = Article(uid=f"{part.uid}.a{part.next_seq}", title=title)
        part.next_seq += 1
        part.articles.append(article)
        self.current_article = article
        self.stack = []
        self.numbering_offset = 0

    def ensure_article(self, line_no: int) -> None:
        """Synthesize a container when content precedes any article."""
        if self.current_article is not None:
            return
        if self.current_part is None:
            self.part(1)
        part_title = self.current_part.title
        self.article(self.current_part.number, "IMPORTED CONTENT")
        self.warnings.append(
            f"Line {line_no}: content arrived before any article heading — "
            "kept under a synthetic 'IMPORTED CONTENT' article in "
            f"{part_title}."
        )
        self.warning_uids[len(self.warnings) - 1] = self.current_article.uid

    def numbered_paragraph(self, ilvl: int, text: str, line_no: int) -> Paragraph:
        """A Word-auto-numbered paragraph, placed relative to its own list.

        ``ilvl`` is an *indent level within a numbering definition*, not an
        absolute outline depth. A list that reserves level 0 and starts at
        level 1 is as ordinary as one that starts at 0, and a document can
        hold several definitions that disagree about where they begin.

        Reading ``ilvl`` as absolute depth corrupted such a list, and not by
        an off-by-one: its first paragraph had no parent to hang from, so it
        was pushed to the shallowest free depth — while every *sibling* at
        the same ``ilvl`` then found that stack sufficient and landed one
        deeper, i.e. as its **child**. A flat list of articles came back as
        one article owning all the others.

        The shift is therefore remembered for the rest of the article, so
        the siblings move with the paragraph that was already moved. That is
        the whole rule, and it is what makes it safe: the offset can only
        grow at the moment a paragraph was going to be pushed shallower
        anyway, so it never relocates content the old code left alone. In
        particular a list nested under a parent the stack does support — a
        restarted sub-list carrying its own ``numId`` at a deliberately deep
        ``ilvl`` — triggers nothing and keeps its depth, which a per-``numId``
        rebasing of the raw levels would have promoted to top level.

        It is deliberately scoped to the article rather than the document:
        the stack it is relative to resets there, and two articles whose
        lists begin at different levels each get their own answer. A
        document-wide minimum cannot do that, and it also cannot help the
        second list at all when the first one already uses level 0.
        """
        self.ensure_article(line_no)
        depth = max(0, ilvl - self.numbering_offset)
        if depth > len(self.stack):
            self.numbering_offset += depth - len(self.stack)
            depth = len(self.stack)
        return self.paragraph(depth, text, line_no)

    def paragraph(self, depth: int, text: str, line_no: int) -> Paragraph:
        self.ensure_article(line_no)
        pending_warning_indexes: list[int] = []
        if depth >= MAX_PARAGRAPH_DEPTH:
            self.warnings.append(
                f"Line {line_no}: nesting deeper than "
                f"{MAX_PARAGRAPH_DEPTH} levels — clamped to level "
                f"{MAX_PARAGRAPH_DEPTH}."
            )
            pending_warning_indexes.append(len(self.warnings) - 1)
            depth = MAX_PARAGRAPH_DEPTH - 1
        # A deeper level than the stack supports attaches at the deepest
        # available parent + 1 (a master can open with "1." under nothing).
        if depth > len(self.stack):
            self.warnings.append(
                f"Line {line_no}: paragraph level jumped deeper than its "
                f"context — attached at level {len(self.stack)}."
            )
            pending_warning_indexes.append(len(self.warnings) - 1)
            depth = len(self.stack)
        if depth == 0:
            owner = self.current_article
            siblings = owner.paragraphs
        else:
            owner = self.stack[depth - 1]
            siblings = owner.children
        paragraph = Paragraph(
            uid=f"{owner.uid}.p{owner.next_seq}",
            text=text,
            status="imported",
        )
        owner.next_seq += 1
        siblings.append(paragraph)
        self.stack = self.stack[:depth] + [paragraph]
        self.imported_count += 1
        for warning_index in pending_warning_indexes:
            self.warning_uids[warning_index] = paragraph.uid
        return paragraph


def parse_master_docx(filepath: str | Path) -> ImportResult:
    """Parse a master ``.docx`` into an all-``imported`` SectionFormat tree.

    Raises :class:`MasterImportError` for a file that isn't a readable
    ``.docx``; every structural surprise inside a readable file becomes a
    warning, never a drop.
    """
    filepath = Path(filepath)
    try:
        source_bytes = filepath.read_bytes()
    except OSError as exc:
        raise MasterImportError(
            "That file is not a readable .docx document."
        ) from exc
    try:
        document = Document(str(filepath))
    except (
        PackageNotFoundError,
        zipfile.BadZipFile,
        XMLSyntaxError,
        KeyError,
        ValueError,
    ) as exc:
        raise MasterImportError(
            "That file is not a readable .docx document."
        ) from exc

    builder = _TreeBuilder()
    tracked = _element_has_tracked_changes(document.element.body)
    loss_warning = _detect_content_loss_warning(document.element.body)
    if loss_warning:
        builder.warnings.append(loss_warning)
    if tracked:
        builder.warnings.append(
            "The master carries pending tracked changes; text was imported "
            "as the Accept-All-Changes view (insertions kept, deletions "
            "removed)."
        )

    skipped_empty = 0
    saw_table = False
    pending_title = False  # SECTION number seen; next line may be the title
    # Any one of these means the file carried real SectionFormat structure.
    # Deliberately the same three signals the parse itself acts on, so the
    # verdict can never disagree with the tree that was built: a paragraph
    # consumed by the direct-numbering branch below counts only when its
    # numbering label PROMOTED it to real structure (a part or article the
    # tree actually holds), never merely for being numbered.
    saw_spec_marker = False
    source_bindings: list[SourceParagraphBinding] = []
    # The numbering-definition label grammars, for heading promotion.
    numbering_catalog = _load_numbering_catalog(document)
    # Promoted PART headings are numbered by order of appearance — the
    # rendered "PART 1/2/3" is a counter this parse never runs.
    promoted_part_count = 0
    # The bare-section header is consulted for the first content line only.
    saw_any_content = False
    # Where END OF SECTION stopped the parse (1-based), for the trailing-
    # content accounting below. None = the file never said it ended.
    end_of_section_index: int | None = None

    entries = _iter_body_texts(document)
    for line_no, entry in enumerate(entries, start=1):
        raw_text = entry.text
        docx_paragraph = entry.paragraph
        text = " ".join(raw_text.split())
        if not text:
            skipped_empty += 1
            continue
        first_content = not saw_any_content
        saw_any_content = True
        if docx_paragraph is None and not saw_table:
            saw_table = True
            builder.warnings.append(
                "The master contains tables; their rows were flattened into "
                "paragraphs (cells joined with ' | ') — review formatting."
            )

        def add_mapped_paragraph(
            depth: int, semantic_text: str, *, numbered: bool = False
        ) -> None:
            # `depth` is a raw ``w:numPr`` indent level for a numbered
            # paragraph and an absolute depth for a manual label ("A." is
            # depth 0 by definition), so only the former is rebased.
            place = (
                builder.numbered_paragraph if numbered else builder.paragraph
            )
            paragraph = place(depth, semantic_text, line_no)
            if entry.opaque_blocker:
                binding = bind_opaque_projection(
                    uid=paragraph.uid,
                    body_child_index=entry.body_child_index,
                    element=entry.source_element,
                    source_visible_text=raw_text,
                    baseline_text=semantic_text,
                    blocker=entry.opaque_blocker,
                )
            else:
                binding = bind_source_paragraph(
                    uid=paragraph.uid,
                    body_child_index=entry.body_child_index,
                    element=entry.source_element,
                    source_visible_text=raw_text,
                    baseline_text=semantic_text,
                )
            source_bindings.append(binding)

        # Direct Word numbering is structural metadata, so it must win over
        # text-pattern heuristics. Normalized exports deliberately keep the
        # generated A./1./a./1) marker out of w:t; their semantic text may
        # therefore begin with strings such as "END OF SECTION", "PART 2",
        # "1.2", or "A." without becoming a false heading or manual label on
        # re-import. A numbered line also cannot serve as the pending section
        # title.
        if docx_paragraph is not None:
            ilvl = _numbering_level(docx_paragraph)
            if ilvl is not None:
                pending_title = False
                # An auto-numbered heading's visible text is just its title
                # ("SUMMARY") — no text pattern can ever reach it. The
                # numbering definition's own label grammar is the structural
                # signal: promote what it says is a PART or an article, and
                # leave everything else on the provision path. A promoted
                # heading gets no source binding, exactly like a
                # text-matched heading (headings live in the fixed
                # projection, not the editable body surface).
                kind = _promoted_heading_kind(
                    numbering_catalog, docx_paragraph, ilvl
                )
                if kind == "part":
                    saw_spec_marker = True
                    promoted_part_count += 1
                    part_number = promoted_part_count
                    if part_number > 3:
                        builder.warnings.append(
                            f"Line {line_no}: SectionFormat carries three "
                            f"parts; this file's PART heading number "
                            f"{part_number} was kept under PART 3 — review "
                            "placement."
                        )
                        part_number = 3
                    builder.part(part_number)
                    continue
                if kind == "article":
                    saw_spec_marker = True
                    builder.article(
                        builder.current_part.number
                        if builder.current_part is not None
                        else 1,
                        text,
                    )
                    continue
                # The raw indent level: numbered_paragraph places it against
                # its own list, never as an absolute depth.
                add_mapped_paragraph(max(0, ilvl), text, numbered=True)
                continue

        # The normalized renderer emits this exact line only to show that a
        # PART has no articles. It is presentation, not a semantic provision;
        # recognizing it here prevents an export/re-import round trip from
        # manufacturing an ``IMPORTED CONTENT`` article in an empty part.
        if (
            text.casefold() == "(not used.)"
            and builder.current_part is not None
            and builder.current_article is None
        ):
            continue

        if _END_RE.match(text):
            end_of_section_index = line_no
            break

        section_match = _SECTION_RE.match(text)
        if section_match:
            saw_spec_marker = True
            g1, g2, g3, g4, remainder = section_match.groups()
            number = f"{g1} {g2} {g3}" + (f".{g4}" if g4 else "")
            builder.section.number = number
            if remainder.strip():
                builder.section.title = remainder.strip()
                pending_title = False
            else:
                pending_title = True
            continue
        if first_content:
            # Only the FIRST content line may be a keyword-less header —
            # "23 05 48 — COMMON WORK RESULTS FOR HVAC". Later in a document
            # the same shape is far more likely a provision citing a sibling
            # section, so it never re-arms.
            bare_match = _BARE_SECTION_RE.match(text)
            if bare_match:
                saw_spec_marker = True
                b1, b2, b3, b4, bare_title = bare_match.groups()
                builder.section.number = f"{b1} {b2} {b3}" + (
                    f".{b4}" if b4 else ""
                )
                builder.section.title = bare_title.strip()
                pending_title = False
                builder.warnings.append(
                    "Line 1: no 'SECTION' keyword — the section number and "
                    "title were read from the first line."
                )
                continue
        if pending_title:
            pending_title = False
            if not (_PART_RE.match(text) or _ARTICLE_RE.match(text)):
                builder.section.title = text
                continue

        part_match = _PART_RE.match(text)
        if part_match:
            saw_spec_marker = True
            part_number = int(part_match.group(1))
            if part_number > 3:
                builder.warnings.append(
                    f"Line {line_no}: SectionFormat carries three parts; "
                    f"'PART {part_number}' content was kept under PART 3 — "
                    "review placement."
                )
                part_number = 3
            builder.part(part_number)
            continue

        article_match = _ARTICLE_RE.match(text)
        if article_match:
            saw_spec_marker = True
            part_digit, _article_no, title = article_match.groups()
            builder.article(int(part_digit), title.strip())
            continue

        # Manual paragraph labels, most-specific first (uppercase before
        # digit before lowercase ordering is inherent to the regexes).
        matched_level = None
        for depth, pattern in enumerate(_LEVEL_RES):
            match = pattern.match(text)
            if match:
                matched_level = (depth, match.group(2).strip())
                break
        if matched_level is not None:
            add_mapped_paragraph(matched_level[0], matched_level[1])
            continue

        # Unlabeled content: keep as a level-0 paragraph (never drop).
        add_mapped_paragraph(0, text)

    # END OF SECTION still stops the parse — the app's own normalized export
    # puts its assumptions/open-items schedules after that line, and
    # re-importing them as content would corrupt the round trip — but
    # stopping must never be SILENT: dropped content with no warning
    # violates this module's whole philosophy, and for a combined
    # multi-section master it quietly discarded every section but the
    # first. The one suppression is the app's own trailing schedules,
    # recognized by the exact heading the exporter writes first.
    if end_of_section_index is not None:
        trailing = [
            " ".join(item.text.split())
            for item in entries[end_of_section_index:]
        ]
        trailing = [item for item in trailing if item]
        if trailing and trailing[0] != "ASSUMPTIONS SCHEDULE":
            next_section = next(
                (item for item in trailing if _SECTION_RE.match(item)), None
            )

            def _clip(value: str) -> str:
                return value if len(value) <= 60 else value[:60] + "…"

            if next_section is not None:
                builder.warnings.append(
                    "This file contains more than one SECTION (next: "
                    f"'{_clip(next_section)}'); only the first was imported "
                    f"— split the file to import another. {len(trailing)} "
                    "block(s) after 'END OF SECTION' were not imported."
                )
            else:
                builder.warnings.append(
                    f"{len(trailing)} block(s) after 'END OF SECTION' were "
                    f"not imported (beginning '{_clip(trailing[0])}'). "
                    "Build-a-Spec authors one section at a time."
                )

    # Append a FINDABLE reference to every warning that is about a specific
    # element: the display ref the panel and export schedules use plus the
    # stable id. Runs before UNSTRUCTURED_IMPORT_WARNING's insert(0), which
    # would shift the recorded indexes. Refs derive from final positions, so
    # this must be a post-pass — mid-build numbering is not settled yet.
    if builder.warning_uids:
        refs = {
            p.uid: ref
            for _part, _article, p, _depth, ref in iter_paragraphs(
                builder.section
            )
        }
        for part in builder.section.parts:
            for article_index, article in enumerate(part.articles, start=1):
                refs.setdefault(
                    article.uid, f"{part.number}.{article_index}"
                )
        for warning_index, uid in builder.warning_uids.items():
            ref = refs.get(uid)
            where = f" (at {ref}, id {uid})" if ref else f" (id {uid})"
            builder.warnings[warning_index] += where

    if builder.imported_count == 0 and builder.section.is_empty():
        raise MasterImportError(
            "No importable content found — the document has no recognizable "
            "SectionFormat structure and no body text."
        )

    # Lead with the verdict so it reads before the per-line parse notes it
    # explains. Those notes are kept: they say *where* the content landed,
    # which stays useful once the user starts working with it.
    if not saw_spec_marker:
        builder.warnings.insert(0, UNSTRUCTURED_IMPORT_WARNING)

    try:
        source_map = build_source_body_map(
            source_bytes=source_bytes,
            document=document,
            section=builder.section,
            bindings=source_bindings,
        )
    except (TypeError, ValueError, zipfile.BadZipFile) as exc:
        raise MasterImportError(
            "The document body could not be mapped safely for editing."
        ) from exc

    return ImportResult(
        section=builder.section,
        warnings=builder.warnings,
        tracked_changes_detected=tracked,
        imported_block_count=builder.imported_count,
        skipped_empty_count=skipped_empty,
        source_map=source_map,
        spec_shape_detected=saw_spec_marker,
    )
