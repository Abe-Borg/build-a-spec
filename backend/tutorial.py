"""Content-driven tutorial fixtures and coverage analysis.

The tutorial runs exclusively on the bundled showcase (decided with Abraham,
2026-08-03): no source choice, no live enrichment, no billed model calls.
Every fixture here is deterministic, and the whole tour works without an
API key.
"""
from __future__ import annotations

import copy
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .figures import FIGURE_KINDS, FigureError
from .llm.conversation import SessionState
from .reference_extract import extract_reference_document
from .spec_doc.docx_export import build_docx
from .spec_doc.model import SpecSection, iter_paragraphs
from .spec_modules import get_module
from .templates import TemplateCatalog

TUTORIAL_MANIFEST_VERSION = 2


_TUTORIAL_FIGURE_FIXTURES: tuple[dict[str, Any], ...] = (
    {
        "kind": "mermaid",
        "title": "Tutorial Coordination Flow",
        "caption": "A bundled tutorial-only process fixture.",
        "alt_text": "Flow from coordinate to review to verify.",
        "source": "flowchart LR\n  A[Coordinate] --> B[Review]\n  B --> C[Verify]",
    },
    {
        "kind": "svg",
        "title": "Tutorial Review Status Key",
        "caption": "A bundled tutorial-only schematic.",
        "alt_text": "Assumed and needs-input review states.",
        "source": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 90">'
            '<rect x="10" y="15" width="150" height="55" rx="8" fill="#f5c46b"/>'
            '<rect x="200" y="15" width="150" height="55" rx="8" fill="#e98585"/>'
            '<text x="85" y="49" text-anchor="middle">Assumed</text>'
            '<text x="275" y="49" text-anchor="middle">Needs input</text></svg>'
        ),
    },
    {
        "kind": "table",
        "title": "Tutorial Review Checklist",
        "caption": "Example table content tied to the tutorial section.",
        "alt_text": "Checklist of coordination, review, and verification actions.",
        "columns": ["Stage", "Action"],
        "rows": [
            ["Coordinate", "Resolve interfaces"],
            ["Review", "Confirm assumptions"],
            ["Verify", "Close open items"],
        ],
    },
)


def _assistant_message_count(session: SessionState) -> int:
    return sum(1 for message in session.history if message.get("role") == "assistant")


def _valid_figure_kinds(session: SessionState) -> set[str]:
    assistant_count = _assistant_message_count(session)
    return {
        figure.kind
        for figure in session.figures.figures
        if figure.kind in FIGURE_KINDS
        and 0 <= figure.message_index < assistant_count
    }


def _ensure_tutorial_figures(session: SessionState) -> None:
    """Attach one real, renderable fixture of every supported figure kind."""
    assistant_count = _assistant_message_count(session)
    if assistant_count == 0:
        if not session.history or session.history[-1].get("role") != "user":
            session.history.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Add clearly labeled tutorial-only examples without "
                                "changing my protected specification."
                            ),
                        }
                    ],
                }
            )
        session.history.append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "I added clearly labeled, tutorial-only showcase examples "
                            "around the unchanged protected specification."
                        ),
                    }
                ],
            }
        )
        assistant_count = 1
    present = _valid_figure_kinds(session)
    for payload in _TUTORIAL_FIGURE_FIXTURES:
        if payload["kind"] in present:
            continue
        try:
            session.figures.create(payload, message_index=assistant_count - 1)
        except FigureError:
            continue


@dataclass(frozen=True, slots=True)
class TutorialCoverage:
    ready: bool
    gaps: tuple[str, ...]
    anchors: dict[str, str]
    counts: dict[str, int]
    doc_version: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_tutorial_coverage(session: SessionState) -> TutorialCoverage:
    section = session.doc.doc
    paragraphs = list(iter_paragraphs(section))
    nonempty = [entry for entry in paragraphs if entry[2].text.strip()]
    usable = [
        entry
        for entry in nonempty
        if len(entry[2].text.split()) >= 4 and len(entry[2].text.strip()) >= 24
    ]
    assumed = [entry for entry in paragraphs if entry[2].status == "assumed"]
    needs = [entry for entry in paragraphs if entry[2].status == "needs_input"]
    tbds = [entry for entry in paragraphs if "[TBD:" in entry[2].text]
    nested = [entry for entry in paragraphs if entry[3] > 0]
    deepest = max((entry[3] for entry in paragraphs), default=-1)

    article_pair: tuple[str, str] | None = None
    paragraph_pair: tuple[str, str] | None = None
    for part in section.parts:
        if article_pair is None and len(part.articles) >= 2:
            article_pair = (part.articles[0].uid, part.articles[1].uid)
        for article in part.articles:
            if paragraph_pair is None and len(article.paragraphs) >= 2:
                paragraph_pair = (
                    article.paragraphs[0].uid,
                    article.paragraphs[1].uid,
                )
            stack = list(article.paragraphs)
            while stack:
                paragraph = stack.pop()
                if paragraph_pair is None and len(paragraph.children) >= 2:
                    paragraph_pair = (
                        paragraph.children[0].uid,
                        paragraph.children[1].uid,
                    )
                stack.extend(paragraph.children)

    gaps: list[str] = []
    if not section.number.strip():
        gaps.append("section_number")
    if not section.title.strip():
        gaps.append("section_title")
    if len(usable) < 8 or sum(len(entry[2].text) for entry in usable) < 480:
        gaps.append("substantive_content")
    if any(not part.articles for part in section.parts):
        gaps.append("all_parts")
    if article_pair is None:
        gaps.append("article_siblings")
    if paragraph_pair is None:
        gaps.append("paragraph_siblings")
    if deepest < 3:
        gaps.append("four_paragraph_levels")
    if not assumed:
        gaps.append("assumed_content")
    if not needs:
        gaps.append("needs_input_content")
    if not tbds:
        gaps.append("tbd_content")
    if len(session.doc.versions) < 2:
        gaps.append("version_history")
    # Figures are not part of upfront coverage — Chapter 6 attaches its
    # bundled fixtures when the tour reaches it, not at tutorial start. See
    # media_practice_copy. Kept only as an informational count below.
    valid_figure_kinds = _valid_figure_kinds(session)
    if not session.suggested_prompts:
        gaps.append("suggested_prompts")

    anchors: dict[str, str] = {}
    if nonempty:
        anchors["first_paragraph"] = nonempty[0][2].uid
    if assumed:
        anchors["first_assumed"] = assumed[0][2].uid
    if needs:
        anchors["first_needs_input"] = needs[0][2].uid
    if tbds:
        anchors["tbd_paragraph"] = tbds[0][2].uid
    if nested:
        anchors["nested_paragraph"] = nested[-1][2].uid
    if article_pair:
        anchors["article_move_source"], anchors["article_move_target"] = article_pair
    if paragraph_pair:
        (
            anchors["paragraph_move_source"],
            anchors["paragraph_move_target"],
        ) = paragraph_pair
    counts = {
        "articles": sum(len(part.articles) for part in section.parts),
        "paragraphs": len(paragraphs),
        "usable_paragraphs": len(usable),
        "assumed": len(assumed),
        "needs_input": len(needs),
        "tbd": len(tbds),
        "versions": len(session.doc.versions),
        "figures": len(session.figures.figures),
        "valid_figure_kinds": len(valid_figure_kinds),
    }
    return TutorialCoverage(not gaps, tuple(gaps), anchors, counts, session.doc.index)


def build_showcase_session() -> SessionState:
    """Return the bundled, transparently pre-generated tutorial project."""
    curated = Path(__file__).resolve().parent / "templates" / "curated"
    with tempfile.TemporaryDirectory(prefix="buildaspec-tutorial-") as tmp:
        catalog = TemplateCatalog(
            personal_root=Path(tmp),
            curated_root=curated,
        )
        template, _source = catalog.get("curated:complete-section-starter")
    section = SpecSection.from_dict(copy.deepcopy(template["document"]))
    for _part, _article, paragraph, _depth, _ref in iter_paragraphs(section):
        if paragraph.status == "imported":
            paragraph.status = "assumed"
    session = SessionState()
    session.module = get_module("generic")
    session.doc.seed_template(section)
    # One real committed refinement makes Compare meaningful, and one
    # recorded standard edition makes the standards strip render — the
    # generic module ships no pins, so without a recorded override the
    # strip self-hides and the standards tutorial step would degrade to
    # the "control is not available" card on every run.
    first = next(iter(iter_paragraphs(session.doc.doc)), None)
    if first is not None:
        paragraph = first[2]
        session.doc.begin_turn()
        session.doc.apply_edits(
            [
                {
                    "action": "replace",
                    "target_id": paragraph.uid,
                    "text": paragraph.text.replace("necessary", "required"),
                    "status": paragraph.status,
                },
                {
                    "action": "set_standard_edition",
                    "target_id": "sec",
                    "standard": "ASTM E84",
                    "edition": "2024",
                    "basis": (
                        "Bundled showcase example — a recorded edition with a "
                        "stated basis, exactly what the standards chapter teaches"
                    ),
                    "title": (
                        "Standard Test Method for Surface Burning "
                        "Characteristics of Building Materials"
                    ),
                },
            ]
        )
        session.doc.commit_turn()
    session.history = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Create a complete, tutorial-ready SectionFormat starter and show assumptions openly.",
                }
            ],
        },
        {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": "I created a reusable example with nested structure, review items, and an unresolved project decision.",
                }
            ],
        },
    ]
    session.suggested_prompts = [
        "Use the recommended default",
        "Show me the next open item",
    ]
    return session


def detached_practice_copy(source: SessionState) -> SessionState:
    """Clone semantic content without claiming Word-source preservation."""
    from .sessions import clone_session_for_tutorial

    clone = clone_session_for_tutorial(source)
    clone.source_docx_bytes = None
    clone.source_docx_filename = ""
    clone.source_docx_map = None
    clone.source_patch_context = None
    clone.import_report = None
    clone.doc.baseline_index = None
    for version in clone.doc.versions:
        section = SpecSection.from_dict(version)
        for _part, _article, paragraph, _depth, _ref in iter_paragraphs(section):
            paragraph.source_item_id = ""
        version.clear()
        version.update(section.to_dict())
    clone.doc.doc = SpecSection.from_dict(clone.doc.versions[clone.doc.index])
    return clone


def blank_practice_copy(source: SessionState) -> SessionState:
    """Create the empty-page fixture the from-scratch on-ramp needs.

    Every other chapter runs on a populated workspace, so the panel's
    empty-state surface — chiefly the unset section header — can never be on
    screen.  This is a genuinely blank session rather than a cleared clone:
    carrying the tutorial's transcript onto a blank page would leave a
    conversation describing a specification that is no longer there.  Module
    and discipline ride along so the heading and drafting context stay
    coherent.
    """
    blank = SessionState()
    blank.module = source.module
    blank.discipline = source.discipline
    blank.project_context = source.project_context
    return blank


def structural_practice_copy(source: SessionState) -> SessionState:
    """Create a disposable edit/reorder/lint fixture through real doc ops."""
    from .sessions import clone_session_for_tutorial

    clone = (
        detached_practice_copy(source)
        if source.source_docx_bytes is not None
        else clone_session_for_tutorial(source)
    )
    first = next(iter(iter_paragraphs(clone.doc.doc)), None)
    if first is None:
        return clone
    paragraph = first[2]
    clone.doc.begin_turn()
    clone.doc.apply_edits(
        [
            {
                "action": "replace",
                "target_id": "sec",
                "text": "",
                "numbering": "",
            },
            {
                "action": "replace",
                "target_id": paragraph.uid,
                "text": (
                    paragraph.text
                    + " [VERIFY: tutorial placeholder] TODO: resolve template note. "
                    + "NFPA 13-2019 and UL 1234-2020."
                ),
                "status": paragraph.status,
            },
            {
                "action": "set_standard_edition",
                "target_id": "sec",
                "standard": "NFPA 13",
                "edition": "2022",
                "basis": "Temporary tutorial lint fixture",
            },
            {
                "action": "add_article",
                "target_id": "pt1",
                "text": "TUTORIAL DUPLICATE ARTICLE",
            },
            {
                "action": "add_article",
                "target_id": "pt1",
                "text": "TUTORIAL DUPLICATE ARTICLE",
            },
        ]
    )
    clone.doc.commit_turn()
    _seed_tutorial_followups(clone, paragraph.uid)
    return clone


def _seed_tutorial_followups(clone: SessionState, element_id: str) -> None:
    """Put three items on the "Waiting on you" panel for the tour to point at.

    The panel renders only when the list is non-empty, so without a fixture
    the chapter's anchor could never resolve. Bundled and deterministic —
    like every other tutorial fixture, no model call is involved. One
    already-settled item is included so the Done section, which is what
    "checked off" looks like, is on screen too.
    """
    clone.followups.apply(
        {
            "add": [
                {
                    "kind": "decision",
                    "title": "Confirm the commodity classification for the storage room",
                    "detail": (
                        "The density/area basis for PART 2 changes with it."
                    ),
                    "blocking": True,
                    "element_id": element_id,
                },
                {
                    "kind": "question",
                    "title": "Is the site fed by a dedicated fire main or a shared service?",
                },
                {
                    "kind": "todo",
                    "title": "Send the owner's insurer datasheet when you have it",
                },
            ],
            "resolve": [],
        },
        message_index=1,
    )
    clone.followups.apply(
        {
            "add": [],
            "resolve": [
                {
                    "id": "fu-3",
                    "resolution": "Received — FM Global datasheet 8-9 applies.",
                }
            ],
        },
        message_index=2,
    )


def review_practice_copy(source: SessionState) -> SessionState:
    """Add a truthful external-starter item for the Imported review filter.

    A template seed is not Word-source provenance.  The fixture is therefore
    tagged through ``template_origin`` while source-preserving permissions
    remain exactly as they were on the copied tutorial document.
    """
    from .sessions import clone_session_for_tutorial

    clone = clone_session_for_tutorial(source)
    imported = [
        paragraph.uid
        for _part, _article, paragraph, _depth, _ref in iter_paragraphs(clone.doc.doc)
        if paragraph.status == "imported"
    ]
    if imported:
        return clone
    article = next(
        (
            article
            for part in clone.doc.doc.parts
            for article in part.articles
        ),
        None,
    )
    if article is None:
        return clone
    clone.doc.begin_turn()
    try:
        fixture_id = clone.doc.apply_edits(
            [
                {
                    "action": "add_paragraph",
                    "target_id": article.uid,
                    "text": (
                        "Tutorial external-starter provision: verify project-specific "
                        "coordination responsibilities before accepting this reusable wording."
                    ),
                    "status": "imported",
                }
            ]
        )[0]["id"]
        clone.doc.commit_turn()
    except Exception:
        clone.doc.rollback_turn()
        raise
    existing = clone.template_origin or {}
    seed_ids = list(existing.get("seed_block_ids") or [])
    if fixture_id not in seed_ids:
        seed_ids.append(fixture_id)
    clone.template_origin = {
        "template_id": existing.get("template_id") or "tutorial:review-starter",
        "name": existing.get("name") or "Tutorial review starter",
        "seed_block_ids": seed_ids,
    }
    return clone


def _tutorial_pdf(lines: list[str]) -> bytes:
    """Build one tiny text-bearing PDF without adding a writer dependency."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    parts = [b"BT", b"/F1 12 Tf", b"72 720 Td"]
    for line in lines:
        escaped = (
            line.replace("\\", r"\\")
            .replace("(", r"\(")
            .replace(")", r"\)")
            .encode("latin-1", "replace")
        )
        parts.extend((b"(" + escaped + b") Tj", b"0 -14 Td"))
    parts.append(b"ET")
    stream = b"\n".join(parts)
    content_id = add(
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)
    )
    pages_id = len(objects) + 2
    page_id = add(
        b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
        % (pages_id, font_id, content_id)
    )
    add(b"<< /Type /Pages /Kids [%d 0 R] /Count 1 >>" % page_id)
    catalog_id = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)

    output = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += b"%d 0 obj\n" % index + body + b"\nendobj\n"
    xref_at = len(output)
    output += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        output += b"%010d 00000 n \n" % offset
    output += (
        b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objects) + 1, catalog_id, xref_at)
    )
    return bytes(output)


def _attach_reference_fixtures(
    clone: SessionState, section: SpecSection
) -> SessionState:
    """Attach five extractor-produced reference-document fixtures to ``clone``.

    Derived from ``section``'s heading and first provisions; each fixture
    flows through the same DOCX/PDF/plain-text extractors as a user upload,
    and only the extracted text is retained — exactly like production.
    """
    provisions = [
        paragraph.text
        for _part, _article, paragraph, _depth, _ref in iter_paragraphs(section)
        if paragraph.text.strip()
    ][:6]
    heading = f"SECTION {section.number} {section.title}".strip()
    text = "\n".join([heading, *provisions])
    fixtures = [
        ("tutorial-spec-reference.docx", build_docx(section)),
        ("tutorial-review-notes.pdf", _tutorial_pdf([heading, *provisions[:3]])),
        ("tutorial-basis.txt", text.encode("utf-8")),
        (
            "tutorial-requirements.xml",
            (
                "<requirements>"
                + "".join(
                    f"<item>{item.replace('&', '&amp;').replace('<', '&lt;')}</item>"
                    for item in provisions[:3]
                )
                + "</requirements>"
            ).encode("utf-8"),
        ),
        (
            "tutorial-checklist.csv",
            ("stage,requirement\n" + "\n".join(
                f'"Review","{item.replace(chr(34), chr(34) * 2)}"'
                for item in provisions[:3]
            )).encode("utf-8"),
        ),
    ]
    for filename, payload in fixtures:
        extracted = extract_reference_document(payload, filename=filename)
        clone.references.add(
            filename=filename,
            title=filename.rsplit(".", 1)[0].replace("-", " ").title(),
            text=extracted.text,
            block_count=extracted.block_count,
            tracked_changes=extracted.tracked_changes,
            kind=extracted.kind,
        )
    return clone


def media_practice_copy(source: SessionState) -> SessionState:
    """Build Chapter 6's combined figures + references scenario.

    Bundled-only, like the rest of the tutorial: one renderable fixture of
    every supported figure kind attaches to the cloned transcript, and five
    extractor-produced reference documents ride beside them.  No model call,
    no spend — the showcase tour works end to end without an API key.
    """
    from .sessions import clone_session_for_tutorial

    clone = clone_session_for_tutorial(source)
    _ensure_tutorial_figures(clone)
    return _attach_reference_fixtures(clone, source.doc.doc)
