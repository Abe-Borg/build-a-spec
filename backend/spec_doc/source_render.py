"""Emit an imported master back as Word, with its formatting intact.

The contract (decided with Abraham, 2026-08-21):

    Every part of the uploaded package except the body of
    ``word/document.xml`` is carried through byte-for-byte — headers,
    footers, styles, theme, fonts, numbering definitions, page setup,
    section properties. Inside the body:

    * a provision you did not touch is emitted as a byte-identical clone of
      its source element;
    * a provision you edited keeps its paragraph properties and its run
      properties, and only the words change;
    * a preserved block (table, picture, embedded object, content control)
      is emitted verbatim;
    * a provision you added is cloned from the nearest kin at its own depth,
      so new content looks like the content around it;
    * blank spacer paragraphs travel with the provision they precede.

What is deliberately NOT promised is the older mode's byte-exact whole-file
clone. That promise bought so little editing surface (three of twenty-seven
body operations on a clean master) that it was the feature's real defect.

Two limits are inherent and are disclosed rather than worked around:

* **Intra-paragraph emphasis on an edited provision is best-effort.** Word
  splits a paragraph into runs, and a bolded phrase is a run boundary. When
  the words change there is no correct place to put a boundary that was
  attached to words that no longer exist, so an edited provision is emitted
  with its dominant run properties. Paragraph-level formatting — style,
  font, size, indent, spacing, numbering — is exact either way.
* **A revision-bearing paragraph is rewritten, never cloned.** The importer
  showed the Accept-All view, so cloning the original markup would export
  text the user never saw. Those paragraphs take the rewrite path even when
  their text is unchanged.
"""

from __future__ import annotations

import copy
import zipfile
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

_DOCUMENT_PART = "word/document.xml"

_W_P = qn("w:p")
_W_PPR = qn("w:pPr")
_W_R = qn("w:r")
_W_RPR = qn("w:rPr")
_W_T = qn("w:t")
_W_TAB = qn("w:tab")
_W_BR = qn("w:br")
_W_SECTPR = qn("w:sectPr")
_W_NUMPR = qn("w:numPr")
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
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


def _template_run_properties(paragraph_element):
    """The ``w:rPr`` an edited or synthesized run should carry.

    The FIRST run's properties, because a spec provision's first run is its
    body text — a trailing run is as likely to be a stray formatting island
    left by an editor as it is to be meaningful.
    """
    for run in paragraph_element.iterchildren(_W_R):
        run_properties = run.find(_W_RPR)
        if run_properties is not None:
            return copy.deepcopy(run_properties)
        return None
    return None


def _write_paragraph_text(paragraph_element, text: str) -> None:
    """Replace a cloned paragraph's inline content with ``text``.

    ``w:pPr`` survives untouched — that is the whole of the paragraph-level
    promise (style, numbering, indent, spacing, and the run defaults the
    style carries). Everything else is rebuilt from the dominant run's
    properties, so an edited provision keeps its typeface and size but not
    a bold phrase whose words may no longer exist.
    """
    run_properties = _template_run_properties(paragraph_element)
    for child in list(paragraph_element):
        if child.tag != _W_PPR:
            paragraph_element.remove(child)
    # Tabs and line breaks are real Word markup, not characters; a provision
    # carrying them would otherwise export as a literal control character.
    segments = text.split("\t")
    run = etree.SubElement(paragraph_element, _W_R)
    if run_properties is not None:
        run.append(run_properties)
    for index, segment in enumerate(segments):
        if index:
            etree.SubElement(run, _W_TAB)
        lines = segment.split("\n")
        for line_index, line in enumerate(lines):
            if line_index:
                etree.SubElement(run, _W_BR)
            if not line:
                continue
            node = etree.SubElement(run, _W_T)
            node.text = line
            if line != line.strip():
                node.set(_XML_SPACE, "preserve")


def _blank_template(numbered: bool):
    """A last-resort paragraph for a document that anchors nothing."""
    paragraph = etree.Element(_W_P)
    if numbered:  # pragma: no cover - defensive; templates carry their own
        etree.SubElement(etree.SubElement(paragraph, _W_PPR), _W_NUMPR)
    return paragraph


class _BodyRenderer:
    def __init__(self, body_children: list, format_map: SourceFormatMap):
        self._children = body_children
        self._map = format_map
        self._claimed: set[int] = set()
        # Every source element the import DID model. The distinction matters
        # at the end of the render: an anchored element the walk never
        # reached was deleted by the user, while an unanchored one is content
        # the tree does not model and must not lose.
        self._anchored = {
            anchor.origin_index
            for anchor in format_map.anchors
            if anchor.origin_index != NO_ORIGIN
        }
        self._emitted_blanks: set[int] = set()
        self.output: list = []
        # Depth -> the last origin paragraph seen at that depth, so a newly
        # added provision can be cloned from its nearest kin rather than from
        # whatever happened to be adjacent.
        self._depth_templates: dict[int, int] = {}
        self._last_template: int | None = None
        # The label convention each template ACTUALLY used, recorded rather
        # than re-inferred. Absence of ``w:numPr`` does not mean "manual
        # label": an unstructured import has no labels at all, and inventing
        # one for its new siblings prints a "B." the document never had.
        self._depth_label_kinds: dict[int, str] = {}
        self._last_label_kind: str = ""

    # -- source access ---------------------------------------------------
    def _origin(self, uid: str) -> int:
        anchor = self._map.anchor(uid)
        if anchor is None or anchor.origin_index == NO_ORIGIN:
            return NO_ORIGIN
        if not 0 <= anchor.origin_index < len(self._children):
            return NO_ORIGIN
        return anchor.origin_index

    def _label_kind(self, uid: str) -> str:
        anchor = self._map.anchor(uid)
        return anchor.label_kind if anchor is not None else ""

    def _emit_leading_blanks(self, origin_index: int) -> None:
        """Carry the unmodelled content that directly preceded this element.

        Blank spacers, but also everything the tree never modelled: a cover
        page, a revision history, a table of contents, a picture-only
        paragraph, a page break. Attaching them to the FOLLOWING element
        rather than replaying them at fixed positions is what makes them
        survive a reorder (the spacing above an article travels with the
        article when it moves) and what keeps a cover page ahead of the
        section it introduces. The walk stops at the nearest modelled
        element: content between two provisions belongs to the later one.
        """
        run: list[int] = []
        cursor = origin_index - 1
        while cursor >= 0 and cursor not in self._claimed:
            if cursor in self._anchored or cursor in self._emitted_blanks:
                break
            child = self._children[cursor]
            if child.tag == _W_SECTPR:
                break
            run.append(cursor)
            cursor -= 1
        for index in reversed(run):
            self._emitted_blanks.add(index)
            self.output.append(copy.deepcopy(self._children[index]))

    # -- emission --------------------------------------------------------
    def emit_locked(self, uid: str) -> bool:
        """Emit a preserved block exactly as it arrived."""
        origin_index = self._origin(uid)
        if origin_index == NO_ORIGIN:
            return False
        self._claimed.add(origin_index)
        self._emit_leading_blanks(origin_index)
        self.output.append(copy.deepcopy(self._children[origin_index]))
        return True

    def emit_verbatim(self, uid: str) -> bool:
        """Emit an anchored element exactly as it arrived, whatever it says.

        Used where the CALLER knows the content is unchanged but cannot
        reconstruct the original wording — the section header, whose line has
        several legitimate forms the parse folds into one number and title.
        """
        origin_index = self._origin(uid)
        if origin_index == NO_ORIGIN:
            return False
        self._claimed.add(origin_index)
        self._emit_leading_blanks(origin_index)
        self.output.append(copy.deepcopy(self._children[origin_index]))
        return True

    def emit_text(self, uid: str, text: str, *, depth: int | None = None) -> None:
        origin_index = self._origin(uid)
        if origin_index != NO_ORIGIN:
            self._claimed.add(origin_index)
            self._emit_leading_blanks(origin_index)
            source = self._children[origin_index]
            if source.tag == _W_P:
                recorded = self._label_kind(uid)
                if depth is not None:
                    self._depth_templates[depth] = origin_index
                    if recorded:
                        self._depth_label_kinds[depth] = recorded
                self._last_template = origin_index
                if recorded:
                    self._last_label_kind = recorded
                # Compare the NORMALIZED forms. The importer folds runs of
                # whitespace (`" ".join(text.split())`), so a source
                # paragraph containing a double space after a period — which
                # is most office masters — would otherwise never match its
                # own semantic text, take the rewrite path, and have its
                # inline runs collapsed on a no-op export. Caught in review
                # on PR #141 (Codex).
                unchanged = _normalized(
                    _accept_all_paragraph_text(source)
                ) == _normalized(text)
                if unchanged and not _element_has_tracked_changes(source):
                    # The untouched-provision guarantee: a byte-identical
                    # clone, markup and all.
                    self.output.append(copy.deepcopy(source))
                    return
                clone = copy.deepcopy(source)
                _write_paragraph_text(clone, text)
                self.output.append(clone)
                return
            # An anchored non-paragraph (a table claimed by an editable
            # element) can only be emitted as itself.
            self.output.append(copy.deepcopy(source))
            return
        template_index = None
        if depth is not None:
            template_index = self._depth_templates.get(depth)
        if template_index is None:
            template_index = self._last_template
        if template_index is None:
            clone = _blank_template(False)
        else:
            clone = copy.deepcopy(self._children[template_index])
        _write_paragraph_text(clone, text)
        self.output.append(clone)

    def template_label_kind(self, depth: int) -> str:
        """The label convention a NEW provision at ``depth`` inherits.

        A clone of an auto-numbered sibling is auto-numbered too, and Word
        will render its label — so writing one into the text as well would
        print it twice.
        """
        recorded = self._depth_label_kinds.get(depth) or self._last_label_kind
        if recorded:
            return recorded
        index = self._depth_templates.get(depth)
        if index is None:
            index = self._last_template
        if index is None:
            return LABEL_MANUAL
        element = self._children[index]
        if element.tag == _W_P and element.find(f"{_W_PPR}/{_W_NUMPR}") is not None:
            return LABEL_AUTO
        return LABEL_MANUAL

    def trailing(self) -> list:
        """Body content the tree never modelled, in source order.

        This is what keeps ``END OF SECTION`` — and anything the parse
        stopped at — in the exported file. Dropping it would be silent
        content loss the byte-exact mode could never have caused.

        An ANCHORED element that the walk did not reach is excluded, and
        that exclusion is what makes deletion work: the user removed it, so
        replaying it here would quietly undo the edit.
        """
        remainder = []
        for index, child in enumerate(self._children):
            if index in self._claimed or index in self._emitted_blanks:
                continue
            if index in self._anchored:
                continue
            if child.tag == _W_SECTPR:
                continue
            if _is_blank_paragraph(child):
                continue
            remainder.append(copy.deepcopy(child))
        return remainder


def _render_body(
    section: SpecSection,
    renderer: _BodyRenderer,
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
        if unchanged_identity and renderer.emit_verbatim("sec"):
            renderer.emit_verbatim(SECTION_TITLE_UID)
            return _render_parts(section, renderer)
        if format_map.header_source in (
            HEADER_SOURCE_FRONT_MATTER,
            HEADER_SOURCE_CHROME,
        ):
            # The identity lives in content this export carries through
            # verbatim anyway — a cover page's "Section Number:" field, the
            # page header/footer — so there is no header element to rewrite
            # and inventing one would print the section twice. A changed
            # identity is reported by the stale-identifier lint instead.
            return _render_parts(section, renderer)
        header = " ".join(part for part in ("SECTION", section.number) if part)
        if renderer._origin(SECTION_TITLE_UID) != NO_ORIGIN:
            renderer.emit_text("sec", header.strip())
            renderer.emit_text(SECTION_TITLE_UID, section.title)
        else:
            combined = f"{header} {section.title}".strip() if section.title else header
            renderer.emit_text("sec", combined.strip())
    _render_parts(section, renderer)


def _render_parts(section: SpecSection, renderer: _BodyRenderer) -> None:

    for part in section.parts:
        if not part.articles and renderer._origin(part.uid) == NO_ORIGIN:
            # SectionFormat always has three parts; the master may not have
            # written all three. Emitting a heading the upload never carried
            # would ADD content to the user's file.
            continue
        # ``Part.title`` already carries the whole heading line
        # ("PART 1 - GENERAL"), so an auto-numbered master is the only case
        # that needs the number stripped back off.
        if renderer._label_kind(part.uid) == LABEL_AUTO:
            renderer.emit_text(part.uid, _part_title_only(part))
        else:
            renderer.emit_text(part.uid, part.title)
        for article_index, article in enumerate(part.articles):
            _render_article(part.number, article_index, article, renderer)


def _part_title_only(part) -> str:
    """``"GENERAL"`` from ``"PART 1 - GENERAL"`` — Word renders the number."""
    _prefix, separator, remainder = part.title.partition(" - ")
    return remainder.strip() if separator else part.title


def _render_article(
    part_number: int, index: int, article: Article, renderer: _BodyRenderer
) -> None:
    number = f"{part_number}.{index + 1}"
    if renderer._label_kind(article.uid) == LABEL_AUTO:
        renderer.emit_text(article.uid, article.title)
    else:
        renderer.emit_text(article.uid, f"{number} {article.title}".strip())
    for paragraph, label_index in labelled_paragraphs(article.paragraphs):
        _render_paragraph(paragraph, 0, label_index, renderer)


def _render_paragraph(
    paragraph: Paragraph, depth: int, index: int, renderer: _BodyRenderer
) -> None:
    if paragraph.locked:
        # A locked block with no source behind it cannot be invented: writing
        # a flattened table back as a paragraph would replace a grid with
        # pipe characters. Its children are still rendered either way —
        # the importer no longer lets a preserved block become a parent, but
        # a project saved before that fix can still carry one, and dropping
        # provisions on the floor is exactly the failure this guards.
        renderer.emit_locked(paragraph.uid)
        for child, child_label in labelled_paragraphs(paragraph.children):
            _render_paragraph(child, depth, child_label, renderer)
        return
    label_kind = renderer._label_kind(paragraph.uid)
    if not label_kind:
        label_kind = renderer.template_label_kind(depth)
    if label_kind == LABEL_AUTO:
        text = paragraph.text
    elif label_kind == LABEL_MANUAL:
        text = f"{_paragraph_label(depth, index)} {paragraph.text}"
    else:
        text = paragraph.text
    renderer.emit_text(paragraph.uid, text, depth=depth)
    for child, child_label in labelled_paragraphs(paragraph.children):
        _render_paragraph(child, depth + 1, child_label, renderer)


def render_preserving_docx(
    *,
    source_bytes: bytes,
    format_map: SourceFormatMap,
    current: SpecSection,
) -> bytes:
    """Return the upload with a rebuilt body carrying ``current``."""
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

    renderer = _BodyRenderer(children, format_map)
    _render_body(current, renderer, format_map)
    rendered = renderer.output + renderer.trailing()

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
