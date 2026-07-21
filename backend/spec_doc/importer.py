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
drives the depth.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph as DocxParagraph

from .model import (
    MAX_PARAGRAPH_DEPTH,
    Article,
    Paragraph,
    SpecSection,
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
_PART_RE = re.compile(r"^PART\s*([123])\b", re.IGNORECASE)
_END_RE = re.compile(r"^END\s+OF\s+SECTION\b", re.IGNORECASE)
# "1.1 SUMMARY" / "1.01 SUMMARY" / "2.3 - PIPING" (part digit + article no.)
_ARTICLE_RE = re.compile(r"^([123])\.(\d{1,2})\.?\s+[-–—]?\s*(\S.*)$")
# Manual paragraph labels by depth.
_LEVEL_RES = (
    re.compile(r"^([A-Z]{1,2})\.\s+(\S.*)$"),  # A.  (depth 0)
    re.compile(r"^(\d{1,2})\.\s+(\S.*)$"),  # 1.  (depth 1)
    re.compile(r"^([a-z]{1,2})\.\s+(\S.*)$"),  # a.  (depth 2)
    re.compile(r"^(\d{1,2})\)\s+(\S.*)$"),  # 1)  (depth 3)
)


@dataclass
class ImportResult:
    """A parsed master: the tree plus the parse's honesty trail."""

    section: SpecSection
    warnings: list[str] = field(default_factory=list)
    tracked_changes_detected: bool = False
    imported_block_count: int = 0
    skipped_empty_count: int = 0


class MasterImportError(ValueError):
    """The file could not be parsed as a master spec at all."""


def _numbering_level(paragraph: DocxParagraph) -> int | None:
    """The Word auto-numbering indent level (``ilvl``) or ``None``.

    Auto-numbered masters carry no visible "A."/"1." text — the label
    lives in the numbering definition. The indent level maps directly to
    SectionFormat depth for the common single-list masters.
    """
    p_pr = paragraph._p.pPr
    if p_pr is None or p_pr.numPr is None or p_pr.numPr.ilvl is None:
        return None
    ilvl = p_pr.numPr.ilvl.val
    return int(ilvl) if ilvl is not None else None


def _iter_body_texts(document) -> "list[tuple[str, DocxParagraph | None]]":
    """Body content in document order: (accept-all text, paragraph or None).

    Tables are flattened row by row (cells joined with `` | ``) — spec
    masters use tables mostly for schedules; the content survives as
    paragraphs and the caller records a warning. The paragraph object is
    carried for numbering-level access (None for table rows).
    """
    results: list[tuple[str, DocxParagraph | None]] = []
    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph = DocxParagraph(child, document)
            results.append((_accept_all_paragraph_text(child), paragraph))
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
                    results.append((text, None))
    return results


class _TreeBuilder:
    """Builds the SpecSection with the same uid/seq discipline as apply."""

    def __init__(self) -> None:
        self.section = SpecSection.empty()
        self.current_part = None
        self.current_article: Article | None = None
        # Paragraph stack by depth for nesting (index = depth).
        self.stack: list[Paragraph] = []
        self.warnings: list[str] = []
        self.imported_count = 0

    def part(self, number: int):
        self.current_part = self.section.parts[number - 1]
        self.current_article = None
        self.stack = []

    def article(self, part_number: int, title: str) -> None:
        self.part(part_number)
        part = self.current_part
        article = Article(uid=f"{part.uid}.a{part.next_seq}", title=title)
        part.next_seq += 1
        part.articles.append(article)
        self.current_article = article
        self.stack = []

    def ensure_article(self, line_no: int) -> None:
        """Synthesize a container when content precedes any article."""
        if self.current_article is not None:
            return
        if self.current_part is None:
            self.part(1)
        self.warnings.append(
            f"Line {line_no}: content arrived before any article heading — "
            "kept under a synthetic 'IMPORTED CONTENT' article in "
            f"{self.current_part.title}."
        )
        self.article(self.current_part.number, "IMPORTED CONTENT")

    def paragraph(self, depth: int, text: str, line_no: int) -> None:
        self.ensure_article(line_no)
        if depth >= MAX_PARAGRAPH_DEPTH:
            self.warnings.append(
                f"Line {line_no}: nesting deeper than "
                f"{MAX_PARAGRAPH_DEPTH} levels — clamped to level "
                f"{MAX_PARAGRAPH_DEPTH}."
            )
            depth = MAX_PARAGRAPH_DEPTH - 1
        # A deeper level than the stack supports attaches at the deepest
        # available parent + 1 (a master can open with "1." under nothing).
        if depth > len(self.stack):
            self.warnings.append(
                f"Line {line_no}: paragraph level jumped deeper than its "
                f"context — attached at level {len(self.stack)}."
            )
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


def parse_master_docx(filepath: str | Path) -> ImportResult:
    """Parse a master ``.docx`` into an all-``imported`` SectionFormat tree.

    Raises :class:`MasterImportError` for a file that isn't a readable
    ``.docx``; every structural surprise inside a readable file becomes a
    warning, never a drop.
    """
    filepath = Path(filepath)
    try:
        document = Document(str(filepath))
    except PackageNotFoundError as exc:
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

    entries = _iter_body_texts(document)
    for line_no, (raw_text, docx_paragraph) in enumerate(entries, start=1):
        text = " ".join(raw_text.split())
        if not text:
            skipped_empty += 1
            continue
        if docx_paragraph is None and not saw_table:
            saw_table = True
            builder.warnings.append(
                "The master contains tables; their rows were flattened into "
                "paragraphs (cells joined with ' | ') — review formatting."
            )

        if _END_RE.match(text):
            break

        section_match = _SECTION_RE.match(text)
        if section_match:
            g1, g2, g3, g4, remainder = section_match.groups()
            number = f"{g1} {g2} {g3}" + (f".{g4}" if g4 else "")
            builder.section.number = number
            if remainder.strip():
                builder.section.title = remainder.strip()
                pending_title = False
            else:
                pending_title = True
            continue
        if pending_title:
            pending_title = False
            if not (_PART_RE.match(text) or _ARTICLE_RE.match(text)):
                builder.section.title = text
                continue

        part_match = _PART_RE.match(text)
        if part_match:
            builder.part(int(part_match.group(1)))
            continue

        article_match = _ARTICLE_RE.match(text)
        if article_match:
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
            builder.paragraph(matched_level[0], matched_level[1], line_no)
            continue

        # Auto-numbered masters: the numbering indent level drives depth.
        if docx_paragraph is not None:
            ilvl = _numbering_level(docx_paragraph)
            if ilvl is not None:
                builder.paragraph(min(ilvl, MAX_PARAGRAPH_DEPTH - 1), text, line_no)
                continue

        # Unlabeled content: keep as a level-0 paragraph (never drop).
        builder.paragraph(0, text, line_no)

    if builder.imported_count == 0 and builder.section.is_empty():
        raise MasterImportError(
            "No importable content found — the document has no recognizable "
            "SectionFormat structure and no body text."
        )

    return ImportResult(
        section=builder.section,
        warnings=builder.warnings,
        tracked_changes_detected=tracked,
        imported_block_count=builder.imported_count,
        skipped_empty_count=skipped_empty,
    )
