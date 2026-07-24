"""Optional same-renderer visual regressions for Chunk 7.

These tests are intentionally supplemental to XML/package preservation
assertions.  Configure the documents-skill renderer to exercise them; without
that explicit environment they collect as clean skips and perform no work.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import pytest

from backend.spec_doc.docx_export import build_docx
from tests.docx_corpus import build_case, corpus_cases
from tests.docx_render_harness import (
    DocxRenderHarness,
    RENDERER_SKIP_REASON,
    RenderedDocx,
    renderer_is_configured,
)
from tests.docx_visual_fixtures import (
    SourceRenderInputs,
    build_source_render_inputs,
    build_source_render_variant,
    normalized_numbering_visual_section,
)


pytestmark = pytest.mark.skipif(
    not renderer_is_configured(),
    reason=RENDERER_SKIP_REASON,
)


_REPLACED_TEXT = "Provide supervised control valves."
_ADDED_TEXT = "Provide tamper switches at every supervised valve."


@dataclass(frozen=True)
class OperationCase:
    name: str
    operations: tuple[dict[str, Any], ...]
    max_changed_pixel_fraction: float
    max_bbox_area_fraction: float


_OPERATION_CASES = (
    OperationCase(
        name="replace",
        operations=(
            {
                "action": "replace",
                "target_id": "pt1.a1.p1",
                "text": _REPLACED_TEXT,
                "status": "confirmed",
            },
        ),
        max_changed_pixel_fraction=0.01,
        max_bbox_area_fraction=0.03,
    ),
    OperationCase(
        name="add",
        operations=(
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "position": 1,
                "text": _ADDED_TEXT,
                "status": "confirmed",
            },
        ),
        max_changed_pixel_fraction=0.03,
        max_bbox_area_fraction=0.18,
    ),
    OperationCase(
        name="delete",
        operations=({"action": "delete", "target_id": "pt1.a1.p2"},),
        max_changed_pixel_fraction=0.03,
        max_bbox_area_fraction=0.18,
    ),
    OperationCase(
        name="reorder",
        operations=(
            {
                "action": "move",
                "target_id": "pt1.a1.p3",
                "position": 0,
            },
        ),
        max_changed_pixel_fraction=0.04,
        max_bbox_area_fraction=0.25,
    ),
    OperationCase(
        name="combined",
        operations=(
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "position": 1,
                "text": _ADDED_TEXT,
                "status": "confirmed",
            },
            {
                "action": "move",
                "target_id": "pt1.a1.p3",
                "position": 0,
            },
            {"action": "delete", "target_id": "pt1.a1.p2"},
            {
                "action": "replace",
                "target_id": "pt1.a1.p1",
                "text": _REPLACED_TEXT,
                "status": "confirmed",
            },
        ),
        max_changed_pixel_fraction=0.05,
        max_bbox_area_fraction=0.30,
    ),
)


@pytest.fixture(scope="module")
def renderer() -> DocxRenderHarness:
    pytest.importorskip("PIL.Image")
    pytest.importorskip("pypdf")
    return DocxRenderHarness.from_environment()


@pytest.fixture(scope="module")
def render_work_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("docx-visual-render")


@pytest.fixture(scope="module")
def source_inputs(tmp_path_factory) -> SourceRenderInputs:
    work_dir = tmp_path_factory.mktemp("docx-visual-source")
    return build_source_render_inputs(work_dir)


@pytest.fixture(scope="module")
def rendered_source(
    renderer: DocxRenderHarness,
    render_work_dir: Path,
    source_inputs: SourceRenderInputs,
) -> RenderedDocx:
    return renderer.render_bytes(
        source_inputs.source,
        work_dir=render_work_dir,
        stem="source-baseline",
    )


@pytest.fixture(scope="module")
def rendered_variants(
    renderer: DocxRenderHarness,
    render_work_dir: Path,
    source_inputs: SourceRenderInputs,
) -> dict[str, RenderedDocx]:
    rendered: dict[str, RenderedDocx] = {}
    for case in _OPERATION_CASES:
        # Every variant goes through the public final-state gate with the same
        # immutable cached source context; operation names are never trusted.
        variant = build_source_render_variant(source_inputs, case.operations)
        rendered[case.name] = renderer.render_bytes(
            variant.payload,
            work_dir=render_work_dir,
            stem=f"source-{case.name}",
        )
    return rendered


@pytest.mark.parametrize(
    "case",
    [pytest.param(case, id=case.name) for case in _OPERATION_CASES],
)
def test_source_operation_visual_changes_are_body_local(
    case: OperationCase,
    renderer: DocxRenderHarness,
    rendered_source: RenderedDocx,
    rendered_variants: dict[str, RenderedDocx],
):
    comparison = renderer.compare(rendered_source, rendered_variants[case.name])
    comparison.assert_changed_pages({1})
    comparison.assert_page_furniture_unchanged()
    comparison.assert_changes_within_body()
    comparison.assert_diff_budget(
        max_changed_pixel_fraction=case.max_changed_pixel_fraction,
        max_bbox_area_fraction=case.max_bbox_area_fraction,
    )


def test_multipage_mixed_layout_and_all_page_furniture_are_stable(
    renderer: DocxRenderHarness,
    rendered_source: RenderedDocx,
    rendered_variants: dict[str, RenderedDocx],
):
    replacement = rendered_variants["replace"]
    comparison = renderer.compare(rendered_source, replacement)
    assert len(rendered_source.pages) >= 3

    sizes = [page.size for page in comparison.pages]
    assert any(width < height for width, height in sizes), sizes
    assert any(width > height for width, height in sizes), sizes
    comparison.assert_changed_pages({1})
    comparison.assert_page_furniture_unchanged(
        top_fraction=0.11,
        bottom_fraction=0.11,
    )

    # The three-page fixture exercises first-page, even-page, and default
    # header/footer variants; its default footer contains a PAGE field and its
    # default header contains the retained image.
    rendered_text = " ".join(
        DocxRenderHarness.extract_pdf_text(rendered_source).split()
    )
    for phrase in (
        "NUMBERED FIRST-PAGE HEADER | KEEP EXACT",
        "NUMBERED EVEN-PAGE HEADER | KEEP EXACT",
        "NUMBERED MASTER HEADER | KEEP EXACT |",
        "NUMBERED FIRST-PAGE FOOTER | KEEP EXACT",
        "NUMBERED EVEN-PAGE FOOTER | KEEP EXACT",
        # Require the evaluated field result, not just the static prefix.
        "NUMBERED MASTER FOOTER | PAGE 3",
    ):
        assert phrase in rendered_text


def test_normalized_real_numbering_renders_all_levels_and_article_restarts(
    renderer: DocxRenderHarness,
    render_work_dir: Path,
):
    payload = build_docx(normalized_numbering_visual_section())
    rendered = renderer.render_bytes(
        payload,
        work_dir=render_work_dir,
        stem="normalized-real-numbering",
    )
    assert len(rendered.pages) >= 2
    renderer.assert_pages_have_ink(rendered)

    # PDF extraction checks the markers that the renderer actually emitted,
    # not the text-only w:t content already covered by structural tests.
    rendered_text = " ".join(renderer.extract_pdf_text(rendered).split())
    expected_markers = (
        r"A\.\s+Primary alpha provision\.",
        r"1\.\s+Primary child one\.",
        r"a\.\s+Primary grandchild alpha\.",
        r"1\)\s+Primary fourth-level one\.",
        r"B\.\s+Primary beta provision\.",
        r"1\.\s+Primary beta child restart\.",
        r"A\.\s+Secondary alpha restart\.",
        r"B\.\s+Secondary beta provision\.",
    )
    for pattern in expected_markers:
        assert re.search(pattern, rendered_text), (
            f"Rendered numbering marker did not match {pattern!r}: "
            f"{rendered_text}"
        )


def test_manual_footnotes_and_endnotes_render_with_page_furniture(
    renderer: DocxRenderHarness,
    render_work_dir: Path,
    tmp_path_factory,
):
    case = next(
        item for item in corpus_cases() if item.case_id == "manual_notes_ooxml"
    )
    payload = build_case(
        case,
        tmp_path_factory.mktemp("docx-visual-notes-source"),
    )
    rendered = renderer.render_bytes(
        payload,
        work_dir=render_work_dir,
        stem="manual-footnotes-endnotes",
    )
    assert len(rendered.pages) == 2
    renderer.assert_pages_have_ink(rendered)

    rendered_text = " ".join(renderer.extract_pdf_text(rendered).split())
    for phrase in (
        "Footnote anchor",
        "Endnote anchor",
        "Sanitized synthetic footnote content.",
        "Sanitized synthetic endnote content.",
        "CLIENT EVEN-PAGE HEADER | KEEP EXACT",
        "CLIENT EVEN-PAGE FOOTER | KEEP EXACT",
    ):
        assert phrase in rendered_text
