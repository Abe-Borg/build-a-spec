"""``.docx`` export of the SectionFormat tree via python-docx.

Office-style SectionFormat layout: centered section header, PART headings,
``1.1  TITLE`` articles, hanging-indent paragraph levels (A. / 1. / a. /
1)), END OF SECTION — followed on a new page by the **assumptions
schedule**: every ``assumed`` block listed with its numbering so a senior
reviewer can audit each model default in one pass, plus the open-item
schedule ([TBD: ...] markers and ``needs_input`` blocks).
"""
from __future__ import annotations

import io
import itertools
import re
from datetime import datetime, timezone

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

from .. import settings
from .diffing import ElementDiff, SectionDiff
from .model import (
    SpecSection,
    _paragraph_label,
    iter_paragraphs,
    open_questions,
)

_LEVEL_INDENT = Inches(0.45)


def _style_base(document) -> None:
    normal = document.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(11)
    for docx_section in document.sections:
        docx_section.top_margin = Inches(1)
        docx_section.bottom_margin = Inches(1)
        docx_section.left_margin = Inches(1)
        docx_section.right_margin = Inches(1)


def _centered(document, text: str, *, bold: bool = True):
    p = document.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(text)
    run.bold = bold
    return p


def _labelled(document, label: str, text: str, level: int):
    """A hanging-indent paragraph: label at the level's indent, text after."""
    p = document.add_paragraph()
    pf = p.paragraph_format
    pf.left_indent = _LEVEL_INDENT * (level + 1)
    pf.first_line_indent = -_LEVEL_INDENT
    pf.tab_stops.add_tab_stop(_LEVEL_INDENT * (level + 1), WD_TAB_ALIGNMENT.LEFT)
    pf.space_after = Pt(6)
    p.add_run(f"{label}\t{text}")
    return p


def _schedule_table(document, rows: list[tuple[str, str]], headers: tuple[str, str]):
    table = document.add_table(rows=1, cols=2)
    table.style = "Table Grid"
    hdr = table.rows[0].cells
    for cell, text in zip(hdr, headers):
        cell.text = ""
        run = cell.paragraphs[0].add_run(text)
        run.bold = True
    for ref, text in rows:
        ref_cell, text_cell = table.add_row().cells
        ref_cell.text = ref
        text_cell.text = text
    table.columns[0].width = Inches(1.1)
    table.columns[1].width = Inches(5.4)
    return table


def build_docx(
    section: SpecSection,
    audit_result: dict | None = None,
    qc_result: dict | None = None,
    redline: SectionDiff | None = None,
    redline_date: str | None = None,
) -> bytes:
    """Render the section; a QC or audit closing carries the review trail.

    ``qc_result`` is the Batch 4 Final-QC dict (:meth:`QCResult.to_dict`);
    ``audit_result`` is the Phase 5 audit dict. When a QC result is present
    it supersedes the audit closing (the QC lenses cover the audit's ground
    and more); otherwise the audit closing is rendered as before. The
    rendering states which document version was reviewed.

    When ``redline`` (a :class:`SectionDiff`) is supplied (Batch 5), the body
    is rendered as genuine Word tracked changes (``w:ins``/``w:del`` with
    ``w:delText``, deleted/inserted paragraph marks) instead of the plain
    tree. **Accept All** reproduces ``section`` (the cur tree) exactly,
    numbering included; **Reject All** reproduces the diff's base tree's
    provision *text*. Display numbering (A. / 1.1 / a.) is positional and
    recomputes to the rendered view — it is written as a plain literal so a
    survivor whose position shifted keeps the current label, never a tracked
    mark (the frozen "moves are not marked" decision). The schedules below are
    always rendered plainly from ``section`` (the current document), never
    redlined. ``redline_date`` overrides the ISO-8601 ``w:date`` stamp.
    """
    document = Document()
    _style_base(document)

    if redline is not None:
        _render_redline_body(
            document,
            section,
            redline,
            settings.APP_NAME,
            redline_date or _redline_now(),
        )
    else:
        _render_clean_body(document, section)

    # -- assumptions schedule ----------------------------------------------
    document.add_page_break()
    _centered(document, "ASSUMPTIONS SCHEDULE")
    document.add_paragraph(
        "The following provisions were drafted from defaults (NFPA 13-2025 "
        "/ hyperscale data-center norms) without explicit confirmation. "
        "Each requires review by the design professional of record."
    )
    assumed = [
        (ref, p.text)
        for _part, _article, p, _depth, ref in iter_paragraphs(section)
        if p.status == "assumed"
    ]
    if assumed:
        _schedule_table(document, assumed, ("Ref", "Assumed provision"))
    else:
        document.add_paragraph("None — every provision is confirmed.")

    # -- imported provisions not yet reviewed (Phase 5 master import) ------
    imported = [
        (ref, p.text)
        for _part, _article, p, _depth, ref in iter_paragraphs(section)
        if p.status == "imported"
    ]
    if imported:
        document.add_paragraph()
        _centered(document, "IMPORTED PROVISIONS NOT YET REVIEWED")
        document.add_paragraph(
            "The following provisions were imported from a master "
            "specification and have not been confirmed or adapted for this "
            "project. Each requires review before issue."
        )
        _schedule_table(document, imported, ("Ref", "Imported provision"))

    items = open_questions(section)
    if items:
        document.add_paragraph()
        _centered(document, "OPEN ITEMS")
        rows = [
            (
                item["ref"],
                ("[TBD] " if item["kind"] == "tbd" else "[NEEDS INPUT] ")
                + item["label"],
            )
            for item in items
        ]
        _schedule_table(document, rows, ("Ref", "Open item"))

    # -- Final QC closing (Batch 4) supersedes the audit closing -----------
    if qc_result and (qc_result.get("findings") or qc_result.get("lens_statuses")):
        _render_qc_closing(document, qc_result, compact=True)
    # -- compliance audit closing section (Phase 5) ------------------------
    elif audit_result and audit_result.get("coverage"):
        document.add_page_break()
        _centered(document, "COMPLIANCE AUDIT SUMMARY")
        audited_at = audit_result.get("audited_at", "")
        version_index = audit_result.get("version_index")
        version_note = (
            f" of document version v{int(version_index) + 1}"
            if isinstance(version_index, int)
            else ""
        )
        document.add_paragraph(
            f"Advisory audit{version_note} against the researched project "
            f"requirements profile ({audited_at}). Grounded requirements "
            "only; this summary is not a substitute for review by a "
            "licensed design professional."
        )
        summary = str(audit_result.get("summary") or "").strip()
        if summary:
            document.add_paragraph(summary)
        coverage_rows = [
            (
                str(entry.get("status", "")).upper(),
                f"[{entry.get('requirement_id', '')}] "
                + str(entry.get("note") or entry.get("evidence_quote") or ""),
            )
            for entry in audit_result.get("coverage", [])
        ]
        if coverage_rows:
            _schedule_table(document, coverage_rows, ("Status", "Requirement"))
        findings = audit_result.get("findings") or []
        if findings:
            document.add_paragraph()
            _centered(document, "AUDIT FINDINGS")
            finding_rows = [
                (
                    str(finding.get("severity", "")).upper(),
                    str(finding.get("issue", ""))
                    + (
                        f" Suggestion: {finding['suggestion']}"
                        if finding.get("suggestion")
                        else ""
                    ),
                )
                for finding in findings
            ]
            _schedule_table(document, finding_rows, ("Severity", "Finding"))

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _render_clean_body(document, section: SpecSection) -> None:
    """The plain SectionFormat body: header, parts/articles, END OF SECTION."""
    _centered(document, f"SECTION {section.number or '[TBD]'}")
    _centered(document, section.title or "[TBD: SECTION TITLE]")
    document.add_paragraph()

    for part in section.parts:
        p = document.add_paragraph()
        p.paragraph_format.space_before = Pt(12)
        p.add_run(part.title).bold = True
        if not part.articles:
            document.add_paragraph("(Not used.)")
        for a_idx, article in enumerate(part.articles):
            ap = document.add_paragraph()
            apf = ap.paragraph_format
            apf.space_before = Pt(10)
            apf.tab_stops.add_tab_stop(_LEVEL_INDENT, WD_TAB_ALIGNMENT.LEFT)
            ap.add_run(
                f"{part.number}.{a_idx + 1}\t{article.title.upper()}"
            ).bold = True

            def walk(paragraphs, depth: int) -> None:
                for i, para in enumerate(paragraphs):
                    _labelled(
                        document,
                        _paragraph_label(depth, i),
                        para.text,
                        depth,
                    )
                    walk(para.children, depth + 1)

            walk(article.paragraphs, 0)

    document.add_paragraph()
    _centered(document, f"END OF SECTION {section.number or ''}".rstrip())


def export_filename(section: SpecSection) -> str:
    stem = f"SECTION {section.number}" if section.number else "DRAFT SECTION"
    if section.title:
        stem += f" - {section.title}"
    stem = re.sub(r'[\\/:*?"<>|]+', "", stem).strip() or "DRAFT SECTION"
    return f"{stem}.docx"


def redline_filename(section: SpecSection) -> str:
    """The clean export name with a `` - REDLINE`` suffix before ``.docx``."""
    name = export_filename(section)
    return name[: -len(".docx")] + " - REDLINE.docx"


# ---------------------------------------------------------------------------
# Tracked-changes (redline) body — Batch 5
#
# python-docx has no tracked-changes API, so the w:ins / w:del / w:delText
# elements are built directly with docx.oxml, mirroring the shapes the
# importer's tests already manufacture (tests/test_importer.py). The killer
# invariant: re-importing this export (Accept-All resolution) reproduces the
# cur tree exactly; a Reject-All reading reproduces the base tree's provision
# TEXT. Display numbering (A. / 1.1 / a.) is positional and recomputes to the
# rendered view — it is a literal label, not tracked content, so a survivor
# whose position shifted shows the current number under both resolutions
# (the frozen "moves are not marked" decision). Reject-All is therefore
# text-faithful, not label-faithful, for position-shifted survivors.
# ---------------------------------------------------------------------------


def _redline_now() -> str:
    """ISO-8601 UTC stamp for w:date (e.g. ``2026-07-21T14:30:00Z``)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _set_revision_attrs(element, ids, author: str, date: str) -> None:
    element.set(qn("w:id"), str(next(ids)))
    element.set(qn("w:author"), author)
    element.set(qn("w:date"), date)


def _content_run(text: str, *, del_text: bool = False, bold: bool = False):
    """A ``w:r`` carrying ``text``; tabs become ``w:tab`` for real indents.

    Uses ``w:delText`` inside deletions (required) and ``w:t`` otherwise;
    ``xml:space=preserve`` keeps token whitespace byte-exact.
    """
    run = OxmlElement("w:r")
    if bold:
        rpr = OxmlElement("w:rPr")
        rpr.append(OxmlElement("w:b"))
        run.append(rpr)
    tag = "w:delText" if del_text else "w:t"
    for i, segment in enumerate(text.split("\t")):
        if i > 0:
            run.append(OxmlElement("w:tab"))
        if segment:
            text_el = OxmlElement(tag)
            text_el.set(qn("xml:space"), "preserve")
            text_el.text = segment
            run.append(text_el)
    return run


def _append_equal(paragraph, text: str, *, bold: bool = False) -> None:
    paragraph._p.append(_content_run(text, bold=bold))


def _append_ins(paragraph, text: str, ids, author, date, *, bold=False) -> None:
    ins = OxmlElement("w:ins")
    _set_revision_attrs(ins, ids, author, date)
    ins.append(_content_run(text, bold=bold))
    paragraph._p.append(ins)


def _append_del(paragraph, text: str, ids, author, date, *, bold=False) -> None:
    dele = OxmlElement("w:del")
    _set_revision_attrs(dele, ids, author, date)
    dele.append(_content_run(text, del_text=True, bold=bold))
    paragraph._p.append(dele)


def _append_runs(paragraph, runs, ids, author, date, *, bold=False) -> None:
    for run in runs:
        if run.op == "equal":
            _append_equal(paragraph, run.text, bold=bold)
        elif run.op == "ins":
            _append_ins(paragraph, run.text, ids, author, date, bold=bold)
        else:  # del
            _append_del(paragraph, run.text, ids, author, date, bold=bold)


def _mark_paragraph(paragraph, tag: str, ids, author, date) -> None:
    """Flag the paragraph MARK as inserted/deleted (``w:pPr/w:rPr/<tag>``).

    A deleted paragraph mark is what makes Word collapse the whole paragraph
    on Accept; an inserted one makes Reject remove it. ``w:rPr`` is the last
    child of ``w:pPr`` per the schema, so appending it is valid.
    """
    ppr = paragraph._p.get_or_add_pPr()
    rpr = ppr.find(qn("w:rPr"))
    if rpr is None:
        rpr = OxmlElement("w:rPr")
        ppr.append(rpr)
    marker = OxmlElement(tag)
    _set_revision_attrs(marker, ids, author, date)
    rpr.append(marker)


def _redline_paragraph_format(paragraph, level: int) -> None:
    pf = paragraph.paragraph_format
    pf.left_indent = _LEVEL_INDENT * (level + 1)
    pf.first_line_indent = -_LEVEL_INDENT
    pf.tab_stops.add_tab_stop(_LEVEL_INDENT * (level + 1), WD_TAB_ALIGNMENT.LEFT)
    pf.space_after = Pt(6)


def _render_redline_section(document, element: ElementDiff, ids, author, date):
    # SECTION <number> line (centered). The clean body substitutes "[TBD]" for
    # an empty number, so an empty side must carry that placeholder too, or the
    # round-trip diverges on a from-scratch (vs-empty) redline.
    p = document.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _append_equal(p, "SECTION ", bold=True)
    if element.number_base == element.number_cur:
        _append_equal(p, element.number_cur or "[TBD]", bold=True)
    else:
        _append_del(p, element.number_base or "[TBD]", ids, author, date, bold=True)
        _append_ins(p, element.number_cur or "[TBD]", ids, author, date, bold=True)

    # Section title line (centered): word-level diff when both sides have a
    # title, whole del/ins with the placeholder when a side is empty.
    q = document.add_paragraph()
    q.alignment = WD_ALIGN_PARAGRAPH.CENTER
    placeholder = "[TBD: SECTION TITLE]"
    if element.base_text == element.cur_text:
        _append_equal(q, element.cur_text or placeholder, bold=True)
    elif not element.base_text or not element.cur_text:
        _append_del(q, element.base_text or placeholder, ids, author, date, bold=True)
        _append_ins(q, element.cur_text or placeholder, ids, author, date, bold=True)
    else:
        _append_runs(q, element.runs or [], ids, author, date, bold=True)
    document.add_paragraph()


def _render_redline_article(document, element: ElementDiff, ids, author, date):
    p = document.add_paragraph()
    apf = p.paragraph_format
    apf.space_before = Pt(10)
    apf.tab_stops.add_tab_stop(_LEVEL_INDENT, WD_TAB_ALIGNMENT.LEFT)
    number = element.ref_cur or element.ref_base
    if element.kind == "inserted":
        _append_ins(
            p, f"{number}\t{element.cur_text.upper()}", ids, author, date, bold=True
        )
        _mark_paragraph(p, "w:ins", ids, author, date)
    elif element.kind == "deleted":
        _append_del(
            p, f"{number}\t{element.base_text.upper()}", ids, author, date, bold=True
        )
        _mark_paragraph(p, "w:del", ids, author, date)
    elif element.kind == "changed":
        _append_equal(p, f"{number}\t", bold=True)
        upper = [
            type(run)(run.op, run.text.upper()) for run in (element.runs or [])
        ]
        _append_runs(p, upper, ids, author, date, bold=True)
    else:  # unchanged
        _append_equal(p, f"{number}\t{element.cur_text.upper()}", bold=True)


def _render_redline_paragraph(document, element: ElementDiff, ids, author, date):
    p = document.add_paragraph()
    _redline_paragraph_format(p, element.depth)
    label = element.label
    if element.kind == "inserted":
        _append_ins(p, f"{label}\t{element.cur_text}", ids, author, date)
        _mark_paragraph(p, "w:ins", ids, author, date)
    elif element.kind == "deleted":
        _append_del(p, f"{label}\t{element.base_text}", ids, author, date)
        _mark_paragraph(p, "w:del", ids, author, date)
    elif element.kind == "changed":
        _append_equal(p, f"{label}\t")
        _append_runs(p, element.runs or [], ids, author, date)
    else:  # unchanged
        _append_equal(p, f"{label}\t{element.cur_text}")


def _render_redline_body(
    document, section: SpecSection, redline: SectionDiff, author: str, date: str
) -> None:
    ids = itertools.count(1)
    # Per part, does it hold any article in cur / in base? The clean body
    # prints "(Not used.)" for an empty part, so a part that empties (or fills)
    # between the two versions must track that placeholder — else Accept-All
    # (or Reject-All) would not reproduce the clean export. An article row's
    # kind says which side it exists on: not-deleted => in cur; not-inserted
    # => in base.
    part_cur: dict[str, bool] = {}
    part_base: dict[str, bool] = {}
    current_part: str | None = None
    for element in redline.elements:
        if element.node_type == "part":
            current_part = element.uid
            part_cur.setdefault(current_part, False)
            part_base.setdefault(current_part, False)
        elif element.node_type == "article" and current_part is not None:
            if element.kind != "deleted":
                part_cur[current_part] = True
            if element.kind != "inserted":
                part_base[current_part] = True

    for element in redline.elements:
        if element.node_type == "section":
            _render_redline_section(document, element, ids, author, date)
        elif element.node_type == "part":
            p = document.add_paragraph()
            p.paragraph_format.space_before = Pt(12)
            p.add_run(element.cur_text).bold = True
            has_cur = part_cur.get(element.uid, False)
            has_base = part_base.get(element.uid, False)
            if not has_cur and not has_base:
                document.add_paragraph("(Not used.)")  # empty both ways
            elif not has_cur:
                # Emptied in cur (articles all deleted): "(Not used.)" appears
                # on Accept, is gone on Reject (where the articles return).
                np = document.add_paragraph()
                _append_ins(np, "(Not used.)", ids, author, date)
                _mark_paragraph(np, "w:ins", ids, author, date)
            elif not has_base:
                # Filled in cur (empty in base): "(Not used.)" appears on
                # Reject, is gone on Accept (where the articles are present).
                np = document.add_paragraph()
                _append_del(np, "(Not used.)", ids, author, date)
                _mark_paragraph(np, "w:del", ids, author, date)
        elif element.node_type == "article":
            _render_redline_article(document, element, ids, author, date)
        else:  # paragraph
            _render_redline_paragraph(document, element, ids, author, date)

    document.add_paragraph()
    _centered(document, f"END OF SECTION {section.number or ''}".rstrip())


# ---------------------------------------------------------------------------
# Final QC memo (Batch 4)
# ---------------------------------------------------------------------------

_SEVERITY_ORDER = ("critical", "high", "medium", "low")


def _qc_version_note(qc_result: dict) -> str:
    version_index = qc_result.get("version_index")
    if isinstance(version_index, int):
        return f" of document version v{version_index + 1}"
    return ""


def _render_qc_closing(document, qc_result: dict, *, compact: bool) -> None:
    """The Final-QC section appended to the issued spec (compact form)."""
    document.add_page_break()
    _centered(document, "FINAL QC SUMMARY")
    model = str(qc_result.get("model") or "the QC model")
    finished = str(qc_result.get("finished_at") or "")
    document.add_paragraph(
        f"Final quality-control review{_qc_version_note(qc_result)} by "
        f"{model} ({finished}). Every finding below survived an adversarial "
        "verification pass. This summary is advisory and is not a substitute "
        "for review by a licensed design professional."
    )
    summary = str(qc_result.get("summary") or "").strip()
    if summary:
        document.add_paragraph(summary)

    findings = qc_result.get("findings") or []
    open_findings = [f for f in findings if f.get("status") == "open"]
    if open_findings:
        rows = [
            (
                str(f.get("severity", "")).upper(),
                (f"[{f.get('element_id')}] " if f.get("element_id") else "")
                + str(f.get("title", "")),
            )
            for f in _sorted_by_severity(open_findings)
        ]
        _schedule_table(document, rows, ("Severity", "Open finding"))
    else:
        document.add_paragraph("No open findings — every finding was applied or dismissed.")

    if not compact:
        return
    applied = sum(1 for f in findings if f.get("status") == "applied")
    dismissed = sum(1 for f in findings if f.get("status") == "dismissed")
    document.add_paragraph(
        f"Disposition: {len(open_findings)} open, {applied} applied, "
        f"{dismissed} dismissed. "
        f"{len(qc_result.get('refuted') or [])} finding(s) were refuted in "
        "verification and are not shown."
    )


def _sorted_by_severity(findings: list[dict]) -> list[dict]:
    rank = {s: i for i, s in enumerate(_SEVERITY_ORDER)}
    return sorted(findings, key=lambda f: rank.get(str(f.get("severity")), 99))


def build_qc_memo(qc_result: dict, section: SpecSection, *, stale: bool) -> bytes:
    """The standalone Final-QC memo a senior reviewer signs off on.

    Header (project, section, model, date, doc version ± staleness), the
    summary, findings by severity with element refs / rationale / sources /
    disposition, and the refuted appendix. ``stale`` is True when the
    document has moved on from the version QC reviewed.
    """
    document = Document()
    _style_base(document)

    _centered(document, "FINAL QC REVIEW MEMORANDUM")
    profile = section.project_profile or {}
    where = ", ".join(
        v
        for v in (
            profile.get("city"),
            profile.get("state_or_province"),
            profile.get("country"),
        )
        if v
    )
    _centered(
        document,
        f"SECTION {section.number or '[TBD]'} — "
        f"{section.title or '[TBD]'}",
        bold=False,
    )
    header_bits = [
        b
        for b in (
            where,
            f"Client: {profile['client_name']}" if profile.get("client_name") else "",
        )
        if b
    ]
    if header_bits:
        _centered(document, " | ".join(header_bits), bold=False)
    model = str(qc_result.get("model") or "the QC model")
    finished = str(qc_result.get("finished_at") or "")
    document.add_paragraph(
        f"Reviewed by {model}{_qc_version_note(qc_result)}, {finished}. "
        "Every finding below survived an adversarial verification panel."
    )
    if stale:
        p = document.add_paragraph()
        run = p.add_run(
            "STALE: the document has changed since this QC ran — re-run "
            "Final QC before relying on this memo."
        )
        run.bold = True
    summary = str(qc_result.get("summary") or "").strip()
    if summary:
        document.add_paragraph(summary)

    findings = qc_result.get("findings") or []
    if not findings:
        document.add_paragraph("No findings survived verification.")
    for severity in _SEVERITY_ORDER:
        band = [f for f in findings if str(f.get("severity")) == severity]
        if not band:
            continue
        _centered(document, f"{severity.upper()} FINDINGS ({len(band)})")
        for finding in band:
            _render_memo_finding(document, finding)

    refuted = qc_result.get("refuted") or []
    if refuted:
        document.add_page_break()
        _centered(document, "APPENDIX — REFUTED FINDINGS")
        document.add_paragraph(
            "The following candidate findings were raised by a lens but did "
            "not survive verification. They are recorded for transparency and "
            "are not open issues."
        )
        for finding in refuted:
            rp = document.add_paragraph(style="List Bullet")
            rp.add_run(
                f"[{str(finding.get('severity','')).upper()}] "
                f"{finding.get('title','')}"
            )

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _render_memo_finding(document, finding: dict) -> None:
    p = document.add_paragraph()
    element = finding.get("element_id")
    label = f"[{element}] " if element else "[section-level] "
    p.add_run(f"{label}{finding.get('title', '')}").bold = True
    disposition = str(finding.get("status") or "open")
    if disposition != "open":
        note = document.add_paragraph()
        run = note.add_run(f"Disposition: {disposition.upper()}")
        run.italic = True
        if finding.get("dismiss_reason"):
            note.add_run(f" — {finding['dismiss_reason']}").italic = True
    issue = str(finding.get("issue") or "").strip()
    if issue:
        document.add_paragraph(f"Issue: {issue}")
    rationale = str(finding.get("rationale") or "").strip()
    if rationale:
        document.add_paragraph(f"Rationale: {rationale}")
    sources = finding.get("accepted_sources") or []
    if sources:
        document.add_paragraph("Sources: " + ", ".join(str(s) for s in sources))
    elif finding.get("source_urls"):
        document.add_paragraph(
            "Cited (unverified): " + ", ".join(str(s) for s in finding["source_urls"])
        )
