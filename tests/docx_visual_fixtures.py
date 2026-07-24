"""Deterministic fixtures shared by optional DOCX renderer regressions."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from docx.oxml.ns import qn
from lxml import etree

from backend.spec_doc.importer import parse_master_docx
from backend.spec_doc.model import (
    Article,
    Paragraph,
    SpecSection,
    apply_edits,
)
from backend.spec_doc.source_mapping import SourceBodyMap
from backend.spec_doc.source_patch import (
    SourcePatchContext,
    build_source_patch_context,
    build_source_preserving_docx,
    source_patch_readiness,
)
from tests.docx_fidelity_helpers import (
    assert_valid_docx_package,
    document_xml,
    make_numbered_island_master,
    rewrite_zip_members,
)


_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W = f"{{{_W_NS}}}"


@dataclass(frozen=True)
class SourceRenderInputs:
    source: bytes
    source_map: SourceBodyMap
    baseline: SpecSection
    context: SourcePatchContext


@dataclass(frozen=True)
class SourceRenderVariant:
    payload: bytes
    current: SpecSection
    applied: tuple[dict[str, Any], ...]


def _word_value(element, name: str, value: str) -> None:
    element.set(qn(f"w:{name}"), value)


def _text_paragraph(text: str):
    paragraph = etree.Element(f"{_W}p")
    run = etree.SubElement(paragraph, f"{_W}r")
    text_element = etree.SubElement(run, f"{_W}t")
    text_element.text = text
    return paragraph


def _append_opaque_landscape_section(payload: bytes) -> bytes:
    """Add a third, landscape page outside the imported semantic section.

    The first source section retains distinct first/even/default page
    furniture.  The added section removes ``titlePg`` so page three exercises
    the default header/footer, including the existing image and PAGE field.
    """
    root = etree.fromstring(document_xml(payload))
    body = root.find(f"{_W}body")
    assert body is not None
    final_properties = body.find(f"{_W}sectPr")
    assert final_properties is not None and body[-1] is final_properties

    portrait_properties = deepcopy(final_properties)
    section_type = portrait_properties.find(f"{_W}type")
    if section_type is None:
        section_type = etree.Element(f"{_W}type")
        page_size = portrait_properties.find(f"{_W}pgSz")
        insert_at = (
            portrait_properties.index(page_size)
            if page_size is not None
            else len(portrait_properties)
        )
        portrait_properties.insert(insert_at, section_type)
    _word_value(section_type, "val", "nextPage")

    break_paragraph = etree.Element(f"{_W}p")
    paragraph_properties = etree.SubElement(break_paragraph, f"{_W}pPr")
    paragraph_properties.append(portrait_properties)
    final_index = body.index(final_properties)
    body.insert(final_index, break_paragraph)
    final_index += 1

    for index in range(1, 19):
        body.insert(
            final_index,
            _text_paragraph(
                f"OPAQUE LANDSCAPE APPENDIX LINE {index:02d} - KEEP EXACT"
            ),
        )
        final_index += 1

    page_size = final_properties.find(f"{_W}pgSz")
    page_margins = final_properties.find(f"{_W}pgMar")
    assert page_size is not None and page_margins is not None
    _word_value(page_size, "w", "15840")
    _word_value(page_size, "h", "12240")
    _word_value(page_size, "orient", "landscape")
    _word_value(page_margins, "top", "864")
    _word_value(page_margins, "right", "1152")
    _word_value(page_margins, "bottom", "1008")
    _word_value(page_margins, "left", "1008")
    title_page = final_properties.find(f"{_W}titlePg")
    if title_page is not None:
        final_properties.remove(title_page)

    updated_xml = etree.tostring(
        root,
        encoding="UTF-8",
        xml_declaration=True,
        standalone=True,
    )
    updated = rewrite_zip_members(
        payload,
        replacements={"word/document.xml": updated_xml},
    )
    assert_valid_docx_package(updated)
    return updated


def build_source_render_inputs(work_dir: Path) -> SourceRenderInputs:
    filename = "visual-numbered-source.docx"
    source = make_numbered_island_master(work_dir, filename=filename)
    source = _append_opaque_landscape_section(source)
    source_path = work_dir / filename
    source_path.write_bytes(source)

    imported = parse_master_docx(source_path)
    assert imported.source_map is not None
    baseline = SpecSection.from_dict(imported.section.to_dict())
    context = build_source_patch_context(
        source_bytes=source,
        source_map=imported.source_map,
        baseline=baseline,
    )
    readiness = source_patch_readiness(
        source_bytes=source,
        source_map=imported.source_map,
        baseline=baseline,
        current=baseline,
        context=context,
    )
    assert readiness.ready, [
        (issue.blocker, issue.message) for issue in readiness.blockers
    ]
    return SourceRenderInputs(
        source=source,
        source_map=imported.source_map,
        baseline=baseline,
        context=context,
    )


def build_source_render_variant(
    inputs: SourceRenderInputs,
    operations: Iterable[dict[str, Any]],
) -> SourceRenderVariant:
    current, applied = apply_edits(inputs.baseline, list(operations))
    payload = build_source_preserving_docx(
        source_bytes=inputs.source,
        source_map=inputs.source_map,
        baseline=inputs.baseline,
        current=current,
        context=inputs.context,
    )
    return SourceRenderVariant(
        payload=payload,
        current=current,
        applied=tuple(applied),
    )


def _paragraph(
    uid: str,
    text: str,
    *children: Paragraph,
) -> Paragraph:
    return Paragraph(
        uid=uid,
        text=text,
        status="confirmed",
        children=list(children),
        next_seq=len(children) + 1,
    )


def normalized_numbering_visual_section() -> SpecSection:
    """Small four-level tree with restart boundaries in two articles."""
    section = SpecSection.empty()
    section.number = "21 13 99"
    section.title = "VISUAL NUMBERING REGRESSION"
    section.parts[0].articles = [
        Article(
            uid="pt1.a1",
            title="PRIMARY NUMBERING",
            paragraphs=[
                _paragraph(
                    "pt1.a1.p1",
                    "Primary alpha provision.",
                    _paragraph(
                        "pt1.a1.p1.p1",
                        "Primary child one.",
                        _paragraph(
                            "pt1.a1.p1.p1.p1",
                            "Primary grandchild alpha.",
                            _paragraph(
                                "pt1.a1.p1.p1.p1.p1",
                                "Primary fourth-level one.",
                            ),
                        ),
                    ),
                ),
                _paragraph(
                    "pt1.a1.p2",
                    "Primary beta provision.",
                    _paragraph(
                        "pt1.a1.p2.p1",
                        "Primary beta child restart.",
                    ),
                ),
            ],
            next_seq=3,
        ),
        Article(
            uid="pt1.a2",
            title="SECONDARY NUMBERING",
            paragraphs=[
                _paragraph("pt1.a2.p1", "Secondary alpha restart."),
                _paragraph("pt1.a2.p2", "Secondary beta provision."),
            ],
            next_seq=3,
        ),
    ]
    section.parts[0].next_seq = 3
    return section


__all__ = [
    "SourceRenderInputs",
    "SourceRenderVariant",
    "build_source_render_inputs",
    "build_source_render_variant",
    "normalized_numbering_visual_section",
]
