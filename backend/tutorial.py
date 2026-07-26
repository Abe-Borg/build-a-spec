"""Content-driven tutorial fixtures, readiness analysis, and directives."""
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
    valid_figure_kinds = _valid_figure_kinds(session)
    for kind in FIGURE_KINDS:
        if kind not in valid_figure_kinds:
            gaps.append(f"figure_{kind}")
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


def tutorial_enrichment_directive(coverage: TutorialCoverage) -> str:
    gaps = ", ".join(coverage.gaps) or "none"
    return f"""TUTORIAL WORKSPACE ENRICHMENT

You are working only in a disposable tutorial copy. Preserve every existing
section heading, article, provision, and user decision. Add only the material
needed to make the live specification demonstrate the application's editing
and review features. Current validated gaps: {gaps}.

Use the normal document and figure tools. Ensure all three PARTs contain useful
domain-relevant content; one PART has at least two articles; one article has
movable sibling provisions; and one branch demonstrates all four supported
paragraph levels. Include at least one genuine unresolved [TBD: ...] provision
with status needs_input and at least one defensible default with status assumed.
Never mark new material confirmed or imported. Never invent research citations,
source_item_id values, governing editions, client requirements, or QC results.
If discipline or project type is unknown, keep wording broadly applicable and
use needs_input rather than guessing. Create one useful Mermaid diagram, one
SVG schematic, and one data table, each attached to your assistant response,
and offer two short suggested next replies. Finish with a concise explanation
that all additions are tutorial-only and require project review.
"""


def validate_tutorial_enrichment(
    before: SpecSection, after: SpecSection
) -> tuple[bool, tuple[str, ...]]:
    """Prove enrichment only added truthful fixtures around existing work."""
    reasons: list[str] = []

    if before.number and before.number != after.number:
        reasons.append("section number changed")
    if before.title and before.title != after.title:
        reasons.append("section title changed")
    for field in (
        "project_identity",
        "project_profile",
        "edition_overrides",
        "suppressed_standards",
    ):
        if getattr(before, field) != getattr(after, field):
            reasons.append(f"section metadata {field} changed")

    before_parts = [
        (part.uid, part.number, part.title) for part in before.parts
    ]
    after_parts = [(part.uid, part.number, part.title) for part in after.parts]
    if before_parts != after_parts:
        reasons.append("PART identity, order, or title changed")

    before_articles: dict[str, tuple[str, int, str]] = {}
    after_articles: dict[str, tuple[str, int, str]] = {}
    for part in before.parts:
        for index, article in enumerate(part.articles):
            before_articles[article.uid] = (part.uid, index, article.title)
    for part in after.parts:
        for index, article in enumerate(part.articles):
            after_articles[article.uid] = (part.uid, index, article.title)
    for uid, value in before_articles.items():
        if after_articles.get(uid) != value:
            reasons.append(f"existing article {uid} changed or moved")

    def paragraph_map(section: SpecSection) -> dict[str, tuple[Any, ...]]:
        mapped: dict[str, tuple[Any, ...]] = {}

        def visit(paragraphs: list[Any], parent: str) -> None:
            for index, paragraph in enumerate(paragraphs):
                mapped[paragraph.uid] = (
                    parent,
                    index,
                    paragraph.text,
                    paragraph.status,
                    paragraph.source_item_id,
                )
                visit(paragraph.children, paragraph.uid)

        for part in section.parts:
            for article in part.articles:
                visit(article.paragraphs, article.uid)
        return mapped

    old_paragraphs = paragraph_map(before)
    new_paragraphs = paragraph_map(after)
    for uid, value in old_paragraphs.items():
        if new_paragraphs.get(uid) != value:
            reasons.append(f"existing provision {uid} changed or moved")
    for uid, (_parent, _index, _text, status, source_item_id) in new_paragraphs.items():
        if uid in old_paragraphs:
            continue
        if status not in {"assumed", "needs_input"}:
            reasons.append(f"new provision {uid} has unsupported provenance")
        if source_item_id:
            reasons.append(f"new provision {uid} invented source provenance")
    return not reasons, tuple(reasons)


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
    # Make Compare meaningful with one real committed refinement.
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
                }
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
    _ensure_tutorial_figures(session)
    session.suggested_prompts = [
        "Use the recommended default",
        "Show me the next open item",
    ]
    return session


def repair_tutorial_copy(source: SessionState) -> SessionState:
    """Append deterministic showcase fixtures without rewriting user content.

    Live enrichment is allowed to stop or return an invalid partial result.
    Recovery must still leave every pre-enrichment node under the same parent
    and at the same sibling position.  These additions use the production
    document/figure stores, carry only assumed or needs-input provenance, and
    retain any real source package attached to the protected copy.
    """
    from .sessions import clone_session_for_tutorial

    repaired = clone_session_for_tutorial(source)
    store = repaired.doc
    store.begin_turn()
    try:
        if not store.doc.number.strip() or not store.doc.title.strip():
            store.apply_edits(
                [
                    {
                        "action": "replace",
                        "target_id": "sec",
                        "numbering": store.doc.number or "01 80 00",
                        "text": store.doc.title
                        or "PERFORMANCE AND COORDINATION REQUIREMENTS",
                    }
                ]
            )

        # Every PART gets real content. Appending avoids changing the order of
        # any article the user already had.
        for part in store.doc.parts:
            if part.articles:
                continue
            created = store.apply_edits(
                [
                    {
                        "action": "add_article",
                        "target_id": part.uid,
                        "text": "TUTORIAL PRACTICE REQUIREMENTS",
                    }
                ]
            )[0]["id"]
            store.apply_edits(
                [
                    {
                        "action": "add_paragraph",
                        "target_id": created,
                        "text": (
                            "Coordinate this tutorial-only requirement with adjacent work "
                            "and verify the final project criteria before issue."
                        ),
                        "status": "assumed",
                    }
                ]
            )

        # One appended article supplies movable siblings, eight substantive
        # provisions, a truthful open item, and all four supported depths.
        fixture_article = store.apply_edits(
            [
                {
                    "action": "add_article",
                    "target_id": store.doc.parts[0].uid,
                    "text": "TUTORIAL COORDINATION AND VERIFICATION",
                }
            ]
        )[0]["id"]

        roots: list[str] = []
        root_specs = [
            (
                "Coordinate interfaces, access, sequencing, and responsibilities with "
                "related work before procurement so conflicts are resolved in the project record.",
                "assumed",
            ),
            (
                "[TBD: Confirm the owner review interval and responsible approving party "
                "before the final specification is issued for construction.]",
                "needs_input",
            ),
            (
                "Submit coordinated product data, installation instructions, and identified "
                "deviations early enough to support a complete project review.",
                "assumed",
            ),
            (
                "Protect completed work and document corrective action when field conditions "
                "do not match the reviewed coordination basis.",
                "assumed",
            ),
            (
                "Record verification results, responsible participants, and unresolved items "
                "in a closeout record available to the project team.",
                "assumed",
            ),
        ]
        for text, status in root_specs:
            roots.append(
                store.apply_edits(
                    [
                        {
                            "action": "add_paragraph",
                            "target_id": fixture_article,
                            "text": text,
                            "status": status,
                        }
                    ]
                )[0]["id"]
            )

        parent = roots[0]
        for text in (
            "Identify each affected interface and assign a responsible coordinator in the project record.",
            "For each interface, compare dimensions, tolerances, access needs, and required completion dates.",
            "When a conflict remains, document the accepted resolution before dependent work proceeds.",
        ):
            parent = store.apply_edits(
                [
                    {
                        "action": "add_paragraph",
                        "target_id": parent,
                        "text": text,
                        "status": "assumed",
                    }
                ]
            )[0]["id"]
        store.commit_turn()
    except Exception:
        store.rollback_turn()
        raise

    _ensure_tutorial_figures(repaired)
    if not repaired.suggested_prompts:
        repaired.suggested_prompts = [
            "Use the recommended default",
            "Show me the next open item",
        ]
    return repaired


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
    return clone


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


def reference_practice_copy(source: SessionState) -> SessionState:
    """Clone the tutorial and attach five extractor-produced references.

    These files are derived from the active spec and flow through the same
    DOCX/PDF/plain-text extractors as user uploads.  Only extracted text is
    retained, exactly like production; the byte fixtures are disposable.
    """
    from .sessions import clone_session_for_tutorial

    clone = clone_session_for_tutorial(source)
    section = source.doc.doc
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
