"""Import a firm's master, edit it freely, export it looking the same.

This is the contract the appearance-preserving mode makes, stated as tests:

    Everything except the body of ``word/document.xml`` is carried through
    byte-for-byte — headers, footers, styles, theme, fonts, numbering
    definitions, page setup. Inside the body a provision you did not touch is
    a byte-identical clone, a provision you edited keeps its paragraph and
    run properties, preserved blocks (tables, pictures, embedded objects,
    content controls) are emitted verbatim, and a provision you added is
    cloned from its nearest kin.

It replaces the older byte-exact mode as the product's import path because
that promise, while stronger, left almost nothing editable: three of
twenty-seven body operations on a clean master. The byte-exact machinery is
still reachable at ``?mode=source`` for projects that never gave up the
claim, and its own suites still pin it.
"""
from __future__ import annotations

import io
import zipfile

import pytest
from docx import Document
from docx.enum.text import WD_BREAK
from docx.oxml.ns import qn
from docx.shared import Pt
from fastapi.testclient import TestClient
from lxml import etree

from backend.app import create_app
from backend.spec_doc.importer import parse_master_docx
from backend.spec_doc.model import SpecSection, apply_edits
from backend.spec_doc.source_render import (
    SourceRenderError,
    render_preserving_docx,
)
from tests.docx_fidelity_helpers import DOCX_MEDIA_TYPE

HEADER_TEXT = "ACME ENGINEERING — ISSUED FOR BID"
FOOTER_TEXT = "23 05 48 - 1"


def _master_bytes(*, with_table: bool = True) -> bytes:
    """A master with firm styling, a header, a footer and a schedule."""
    document = Document()
    document.styles["Normal"].font.name = "Cambria"
    document.styles["Normal"].font.size = Pt(11)
    section = document.sections[0]
    section.header.paragraphs[0].text = HEADER_TEXT
    section.footer.paragraphs[0].text = FOOTER_TEXT
    for line in (
        "SECTION 23 05 48",
        "VIBRATION AND SEISMIC CONTROLS",
        "PART 1 - GENERAL",
        "1.1 SUMMARY",
        "A. Section includes vibration isolation for mechanical equipment.",
        "",
        "B. Related requirements are specified elsewhere.",
        "1.2 SCHEDULE",
    ):
        document.add_paragraph(line)
    if with_table:
        table = document.add_table(rows=3, cols=2)
        rows = [
            ("Equipment", "Static Deflection"),
            ("AHU-1", "1 inch"),
            ("Pump P-1", "0.75 inch"),
        ]
        for row, (left, right) in zip(table.rows, rows):
            row.cells[0].text = left
            row.cells[1].text = right
    document.add_paragraph("A. Provide isolators as scheduled.")
    document.add_paragraph("END OF SECTION")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _body_children(payload: bytes) -> list:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        xml = archive.read("word/document.xml")
    body = etree.fromstring(xml).find(qn("w:body"))
    return [child for child in body.iterchildren() if isinstance(child.tag, str)]


def _texts(payload: bytes) -> list[str]:
    return [
        paragraph.text
        for paragraph in Document(io.BytesIO(payload)).paragraphs
        if paragraph.text.strip()
    ]


def _members(payload: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _render(source: bytes, section: SpecSection, format_map) -> bytes:
    return render_preserving_docx(
        source_bytes=source, format_map=format_map, current=section
    )


def _import(client: TestClient, source: bytes, *, detach: bool = True):
    response = client.post(
        "/api/import/master",
        files={"file": ("office-master.docx", source, DOCX_MEDIA_TYPE)},
        data={"detach": "true" if detach else "false"},
    )
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# The package-level promise
# ---------------------------------------------------------------------------


def test_only_the_document_body_is_rewritten(tmp_path):
    source = _master_bytes()
    path = tmp_path / "master.docx"
    path.write_bytes(source)
    imported = parse_master_docx(path)
    section = imported.section
    target = section.parts[0].articles[0].paragraphs[0].uid
    section, _ = apply_edits(
        section,
        [{"action": "replace", "target_id": target, "text": "Rewritten."}],
    )

    exported = _render(source, section, imported.format_map)

    before, after = _members(source), _members(exported)
    assert sorted(before) == sorted(after)
    changed = [name for name in before if before[name] != after[name]]
    assert changed == ["word/document.xml"], changed


def test_the_header_footer_and_fonts_survive(tmp_path):
    source = _master_bytes()
    path = tmp_path / "master.docx"
    path.write_bytes(source)
    imported = parse_master_docx(path)

    exported = _render(source, imported.section, imported.format_map)

    document = Document(io.BytesIO(exported))
    assert document.sections[0].header.paragraphs[0].text == HEADER_TEXT
    assert document.sections[0].footer.paragraphs[0].text == FOOTER_TEXT
    assert document.styles["Normal"].font.name == "Cambria"
    assert document.styles["Normal"].font.size == Pt(11)


def test_an_untouched_document_round_trips_element_for_element(tmp_path):
    """The guarantee the whole design rests on.

    If an unedited provision is not a byte-identical clone then every claim
    about preserved formatting is a guess. This is also the regression guard
    for parsing the body with python-docx's oxml classes: a plain lxml parse
    reports every paragraph as empty, which silently sends untouched
    provisions down the rewrite path and this test straight to red.
    """
    source = _master_bytes()
    path = tmp_path / "master.docx"
    path.write_bytes(source)
    imported = parse_master_docx(path)

    exported = _render(source, imported.section, imported.format_map)

    before = [etree.tostring(el) for el in _body_children(source)]
    after = [etree.tostring(el) for el in _body_children(exported)]
    assert before == after


def test_one_edit_changes_exactly_one_element(tmp_path):
    source = _master_bytes()
    path = tmp_path / "master.docx"
    path.write_bytes(source)
    imported = parse_master_docx(path)
    section = imported.section
    target = section.parts[0].articles[0].paragraphs[1].uid
    section, _ = apply_edits(
        section,
        [
            {
                "action": "replace",
                "target_id": target,
                "text": "Related requirements are specified in Division 22.",
            }
        ],
    )

    exported = _render(source, section, imported.format_map)

    before = [etree.tostring(el) for el in _body_children(source)]
    after = [etree.tostring(el) for el in _body_children(exported)]
    differing = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(differing) == 1
    assert "Division 22" in "".join(
        _body_children(exported)[differing[0]].itertext()
    )


def test_content_the_parse_stopped_at_is_still_exported(tmp_path):
    """``END OF SECTION`` is not a provision, and must not be dropped.

    Under the byte-exact mode nothing could be lost because the upload was
    patched rather than rebuilt. Rebuilding the body makes every body child
    the tree does not model a candidate for silent deletion.
    """
    source = _master_bytes()
    path = tmp_path / "master.docx"
    path.write_bytes(source)
    imported = parse_master_docx(path)

    exported = _render(source, imported.section, imported.format_map)

    assert "END OF SECTION" in _texts(exported)


def test_blank_spacers_travel_with_the_provision_below_them(tmp_path):
    source = _master_bytes()
    path = tmp_path / "master.docx"
    path.write_bytes(source)
    imported = parse_master_docx(path)
    section = imported.section
    article = section.parts[0].articles[0]
    # The spacer sat above "B."; moving B to the top must take it along
    # rather than leaving a gap where B used to be.
    section, _ = apply_edits(
        section,
        [
            {
                "action": "move",
                "target_id": article.paragraphs[1].uid,
                "position": 0,
            }
        ],
    )

    exported = _render(source, section, imported.format_map)

    kinds = [
        "blank" if not "".join(el.itertext()).strip() else "text"
        for el in _body_children(exported)
        if el.tag == qn("w:p")
    ]
    assert "blank" in kinds


# ---------------------------------------------------------------------------
# Preserved blocks
# ---------------------------------------------------------------------------


def test_a_table_is_one_locked_block_not_a_row_per_paragraph(tmp_path):
    source = _master_bytes()
    path = tmp_path / "master.docx"
    path.write_bytes(source)
    imported = parse_master_docx(path)

    schedule = imported.section.parts[0].articles[1]
    locked = [p for p in schedule.paragraphs if p.locked]
    assert len(locked) == 1
    assert locked[0].locked == "table"
    # The grid still reads, one row per line, so the panel and the model can
    # both see what the schedule says.
    assert locked[0].text.splitlines()[0] == "Equipment | Static Deflection"
    assert len(locked[0].text.splitlines()) == 3


def test_a_locked_block_takes_no_label_and_shifts_no_sibling(tmp_path):
    source = _master_bytes()
    path = tmp_path / "master.docx"
    path.write_bytes(source)
    imported = parse_master_docx(path)

    payload = imported.section.to_dict()
    schedule = payload["parts"][0]["articles"][1]["paragraphs"]
    assert [p["label"] for p in schedule] == ["", "A."]

    exported = _render(source, imported.section, imported.format_map)
    assert "A. Provide isolators as scheduled." in _texts(exported)


def test_a_table_can_be_deleted_and_moved_but_not_retyped(tmp_path):
    source = _master_bytes()
    path = tmp_path / "master.docx"
    path.write_bytes(source)
    imported = parse_master_docx(path)
    section = imported.section
    table_uid = section.parts[0].articles[1].paragraphs[0].uid

    from backend.spec_doc.model import SpecEditError

    try:
        apply_edits(
            section,
            [{"action": "replace", "target_id": table_uid, "text": "no"}],
        )
    except SpecEditError as exc:
        assert "preserved Word table" in str(exc)
        assert "move it or delete it" in str(exc)
    else:  # pragma: no cover - the lock is the point of the test
        raise AssertionError("a preserved table accepted a retype")

    moved, _ = apply_edits(
        section, [{"action": "move", "target_id": table_uid, "position": 1}]
    )
    assert moved.parts[0].articles[1].paragraphs[1].locked == "table"

    deleted, _ = apply_edits(
        section, [{"action": "delete", "target_id": table_uid}]
    )
    exported = _render(source, deleted, imported.format_map)
    assert not Document(io.BytesIO(exported)).tables


def test_a_moved_table_is_still_a_table_in_the_export(tmp_path):
    source = _master_bytes()
    path = tmp_path / "master.docx"
    path.write_bytes(source)
    imported = parse_master_docx(path)
    section = imported.section
    table_uid = section.parts[0].articles[1].paragraphs[0].uid
    section, _ = apply_edits(
        section, [{"action": "move", "target_id": table_uid, "position": 1}]
    )

    exported = _render(source, section, imported.format_map)

    document = Document(io.BytesIO(exported))
    assert len(document.tables) == 1
    assert document.tables[0].rows[1].cells[0].text == "AHU-1"
    kinds = [etree.QName(el).localname for el in _body_children(exported)]
    assert kinds.index("tbl") > kinds.index("p")


# ---------------------------------------------------------------------------
# Added content
# ---------------------------------------------------------------------------


def test_a_new_provision_is_cloned_from_its_nearest_kin(tmp_path):
    source = _master_bytes()
    path = tmp_path / "master.docx"
    path.write_bytes(source)
    imported = parse_master_docx(path)
    section = imported.section
    article = section.parts[0].articles[0]
    section, _ = apply_edits(
        section,
        [
            {
                "action": "add_paragraph",
                "target_id": article.uid,
                "text": "Comply with ASHRAE Applications Chapter 49.",
            }
        ],
    )

    exported = _render(source, section, imported.format_map)

    children = _body_children(exported)
    existing = next(
        el for el in children if "Section includes vibration" in "".join(el.itertext())
    )
    added = next(
        el for el in children if "ASHRAE Applications" in "".join(el.itertext())
    )
    # Same paragraph properties as the sibling it was modelled on — that is
    # what "new content looks like the content around it" means.
    def _properties(element):
        found = element.find(qn("w:pPr"))
        return etree.tostring(found) if found is not None else None

    assert _properties(existing) == _properties(added)
    assert "C. Comply with ASHRAE Applications Chapter 49." in _texts(exported)


def test_renumbering_follows_the_document_not_the_upload(tmp_path):
    source = _master_bytes()
    path = tmp_path / "master.docx"
    path.write_bytes(source)
    imported = parse_master_docx(path)
    section = imported.section
    article = section.parts[0].articles[0]
    section, _ = apply_edits(
        section, [{"action": "delete", "target_id": article.paragraphs[0].uid}]
    )

    exported = _render(source, section, imported.format_map)

    texts = _texts(exported)
    assert "A. Related requirements are specified elsewhere." in texts
    assert not any(text.startswith("B. Related") for text in texts)


# ---------------------------------------------------------------------------
# Binding and failure
# ---------------------------------------------------------------------------


def test_a_format_map_is_refused_beside_bytes_it_does_not_describe(tmp_path):
    source = _master_bytes()
    other = _master_bytes(with_table=False)
    path = tmp_path / "master.docx"
    path.write_bytes(source)
    imported = parse_master_docx(path)

    try:
        _render(other, imported.section, imported.format_map)
    except SourceRenderError as exc:
        assert "does not describe" in str(exc)
    else:  # pragma: no cover - the binding is the point of the test
        raise AssertionError("a formatting map was trusted against foreign bytes")


# ---------------------------------------------------------------------------
# End to end, through the API
# ---------------------------------------------------------------------------


def test_import_edit_export_keeps_the_firms_formatting():
    client = TestClient(create_app())
    source = _master_bytes()
    _import(client, source)

    doc = client.get("/api/doc").json()["doc"]
    summary = doc["parts"][0]["articles"][0]
    first = summary["paragraphs"][0]["id"]
    edit = client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "replace",
                    "target_id": first,
                    "text": "Section includes seismic restraint and isolation.",
                },
                {
                    "action": "add_paragraph",
                    "target_id": summary["id"],
                    "text": "Submit isolator calculations for review.",
                },
            ]
        },
    )
    assert edit.status_code == 200, edit.text

    exported = client.get("/api/export/docx")
    assert exported.status_code == 200
    payload = exported.content

    before, after = _members(source), _members(payload)
    assert [n for n in before if before[n] != after[n]] == ["word/document.xml"]
    document = Document(io.BytesIO(payload))
    assert document.sections[0].footer.paragraphs[0].text == FOOTER_TEXT
    assert len(document.tables) == 1
    texts = _texts(payload)
    assert "A. Section includes seismic restraint and isolation." in texts
    assert "C. Submit isolator calculations for review." in texts


def test_a_preserved_project_still_preserves_after_save_and_reload():
    client = TestClient(create_app())
    source = _master_bytes()
    _import(client, source)
    saved = client.get("/api/project/save")
    assert saved.status_code == 200

    reopened = TestClient(create_app())
    loaded = reopened.post(
        "/api/project/load-file",
        files={
            "file": (
                "project.baspec",
                saved.content,
                "application/octet-stream",
            )
        },
    )
    assert loaded.status_code == 200, loaded.text

    exported = reopened.get("/api/export/docx")
    assert exported.status_code == 200
    before, after = _members(source), _members(exported.content)
    # Reopened and re-exported without an edit, so at most the body may
    # differ — and in practice nothing does, which is the round trip being
    # exact rather than merely close.
    assert set(n for n in before if before[n] != after[n]) <= {
        "word/document.xml"
    }
    assert (
        Document(io.BytesIO(exported.content))
        .sections[0]
        .header.paragraphs[0]
        .text
        == HEADER_TEXT
    )


def test_normalized_export_stays_available_explicitly():
    client = TestClient(create_app())
    _import(client, _master_bytes())

    normalized = client.get("/api/export/docx", params={"mode": "normalized"})
    assert normalized.status_code == 200
    # A normalized export is Build-a-Spec's own document, so the firm's
    # header does not travel with it — that is the whole difference.
    document = Document(io.BytesIO(normalized.content))
    assert document.sections[0].header.paragraphs[0].text != HEADER_TEXT


# ---------------------------------------------------------------------------
# The header/footer the export never rewrites
# ---------------------------------------------------------------------------


def test_a_stale_footer_section_number_is_reported_not_rewritten():
    """Headers and footers are immutable; a wrong one must still be caught.

    Adapting a master is exactly when the section identifier changes, and a
    spec footer conventionally carries it. Rewriting it would break the
    "we do not touch your header and footer" contract, so the app says so
    instead and names the remedy (Word), rather than letting a stale number
    print on every page of an issued deliverable.
    """
    client = TestClient(create_app())
    _import(client, _master_bytes())

    clean = client.get("/api/doc").json()
    assert not [
        item
        for item in clean["lint"]
        if item["rule"] == "stale_document_identifier"
    ]

    renumbered = client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "replace",
                    "target_id": "sec",
                    "text": "SEISMIC CONTROLS",
                    "numbering": "23 05 93",
                }
            ]
        },
    )
    assert renumbered.status_code == 200, renumbered.text

    findings = [
        item
        for item in renumbered.json()["lint"]
        if item["rule"] == "stale_document_identifier"
    ]
    assert len(findings) == 1
    assert "23 05 48" in findings[0]["message"]
    assert "update them in Word" in findings[0]["message"]

    exported = client.get("/api/export/docx")
    assert exported.status_code == 200
    # Reported, never rewritten.
    assert (
        Document(io.BytesIO(exported.content))
        .sections[0]
        .footer.paragraphs[0]
        .text
        == FOOTER_TEXT
    )


def test_the_model_is_told_a_preserved_block_is_not_retypeable(tmp_path):
    from backend.spec_doc.model import outline

    source = _master_bytes()
    path = tmp_path / "master.docx"
    path.write_bytes(source)
    imported = parse_master_docx(path)

    rendered = outline(imported.section, max_text=None)

    assert "[preserved table]" in rendered
    # It still carries an id, because moving and deleting it are allowed.
    table_uid = imported.section.parts[0].articles[1].paragraphs[0].uid
    assert f"[id: {table_uid}]" in rendered


# ---------------------------------------------------------------------------
# Review findings (PR #141, Codex) — each reproduced before it was fixed
# ---------------------------------------------------------------------------


def _master_with(lines: list[str], *, table_after: str | None = None) -> bytes:
    document = Document()
    for line in lines:
        document.add_paragraph(line)
        if table_after is not None and line == table_after:
            table = document.add_table(rows=1, cols=2)
            table.rows[0].cells[0].text = "Equipment"
            table.rows[0].cells[1].text = "Deflection"
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_a_preserved_block_never_becomes_a_hierarchy_parent(tmp_path):
    """P1: a provision nested under a table was deleted by the export.

    A locked block placed at depth 0 became the current depth-0 node, so a
    following manually labelled "1." attached as its CHILD. The renderer
    returned straight after emitting the locked element, and the trailing
    sweep excluded those children as anchored — so an otherwise untouched
    export silently dropped them.
    """
    source = _master_with(
        [
            "SECTION 23 05 48",
            "VIBRATION",
            "PART 1 - GENERAL",
            "1.1 SCHEDULE",
            "A. Intro paragraph.",
            "1. Nested provision after the table.",
        ],
        table_after="A. Intro paragraph.",
    )
    path = tmp_path / "master.docx"
    path.write_bytes(source)
    imported = parse_master_docx(path)

    article = imported.section.parts[0].articles[0]
    table = next(p for p in article.paragraphs if p.locked)
    assert table.children == []
    # It went to the real provision above the table, which is where a
    # subparagraph of a schedule intro belongs.
    assert [c.text for c in article.paragraphs[0].children] == [
        "Nested provision after the table."
    ]

    exported = _render(source, imported.section, imported.format_map)
    assert "Nested provision after the table." in " ".join(_texts(exported))


def test_a_legacy_locked_parent_still_renders_its_children(tmp_path):
    """The belt-and-braces half: a project saved before the fix above."""
    source = _master_with(
        ["SECTION 23 05 48", "VIBRATION", "PART 1 - GENERAL", "1.1 SCHEDULE"],
        table_after="1.1 SCHEDULE",
    )
    path = tmp_path / "master.docx"
    path.write_bytes(source)
    imported = parse_master_docx(path)
    section = imported.section
    table = section.parts[0].articles[0].paragraphs[0]
    assert table.locked
    # Hand-build the shape the old importer produced.
    from backend.spec_doc.model import Paragraph

    table.children.append(
        Paragraph(uid=f"{table.uid}.p1", text="Stranded provision.")
    )

    exported = _render(source, section, imported.format_map)
    assert "Stranded provision." in " ".join(_texts(exported))


def test_an_unlabelled_import_does_not_invent_labels_for_new_siblings(
    tmp_path,
):
    """P2: absence of ``w:numPr`` is not the same as "manual label".

    An unstructured import has no labels at all, so a new sibling was
    exported as "B. Another clause." — a label the document never had.
    """
    source = _master_with(["This memo describes the vibration approach."])
    path = tmp_path / "memo.docx"
    path.write_bytes(source)
    imported = parse_master_docx(path)
    article = imported.section.parts[0].articles[0]
    section, _ = apply_edits(
        imported.section,
        [
            {
                "action": "add_paragraph",
                "target_id": article.uid,
                "text": "Another clause.",
            }
        ],
    )

    exported = _render(source, section, imported.format_map)

    texts = _texts(exported)
    assert "Another clause." in texts
    assert not any(text.startswith("B. ") for text in texts)


def test_a_keyword_less_section_header_is_not_rewritten(tmp_path):
    """P2: importing and exporting must not restyle the firm's header.

    The parse folds "23 05 48 — TITLE" and "SECTION 23 05 48" into the same
    number and title, so rebuilding one canonical form changed the header
    text — and its runs — on a no-op export.
    """
    source = _master_with(
        [
            "23 05 48 — VIBRATION CONTROLS",
            "PART 1 - GENERAL",
            "1.1 SUMMARY",
            "A. Intro.",
        ]
    )
    path = tmp_path / "bare.docx"
    path.write_bytes(source)
    imported = parse_master_docx(path)

    exported = _render(source, imported.section, imported.format_map)

    assert _texts(exported)[0] == "23 05 48 — VIBRATION CONTROLS"
    before = [etree.tostring(el) for el in _body_children(source)]
    after = [etree.tostring(el) for el in _body_children(exported)]
    assert before == after

    # Once the user changes the identity there is nothing to reproduce, and
    # the canonical form is written instead.
    renamed, _ = apply_edits(
        imported.section,
        [
            {
                "action": "replace",
                "target_id": "sec",
                "text": "SEISMIC CONTROLS",
                "numbering": "23 05 93",
            }
        ],
    )
    rewritten = _render(source, renamed, imported.format_map)
    assert _texts(rewritten)[0] == "SECTION 23 05 93 SEISMIC CONTROLS"


def test_collapsed_whitespace_does_not_count_as_an_edit(tmp_path):
    """P2: a double space after a period is most office masters.

    The importer folds whitespace, so comparing its normalized text against
    the raw source made every such provision look edited — taking the
    rewrite path and collapsing its inline runs on a NO-OP export.
    """
    source = _master_with(
        [
            "SECTION 23 05 48",
            "VIBRATION",
            "PART 1 - GENERAL",
            "1.1 SUMMARY",
            "A. Provide isolators.  Comply with ASHRAE.",
        ]
    )
    path = tmp_path / "spaced.docx"
    path.write_bytes(source)
    imported = parse_master_docx(path)

    exported = _render(source, imported.section, imported.format_map)

    before = [etree.tostring(el) for el in _body_children(source)]
    after = [etree.tostring(el) for el in _body_children(exported)]
    assert before == after
    # The original spacing survives, because the element was never rewritten.
    assert "A. Provide isolators.  Comply with ASHRAE." in _texts(exported)


# ---------------------------------------------------------------------------
# Redline on your original, Phase 0 — the formatted export's findings
#
# docs/plans/REDLINE_ON_ORIGINAL_2026-09-22.md, "Findings". The redline's
# Accept All must equal this export, so every defect here would otherwise be
# reproduced faithfully into it. Each was reproduced before it was fixed.
# ---------------------------------------------------------------------------


_W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"


def _save(document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _parse(tmp_path, source: bytes, name: str = "master.docx"):
    path = tmp_path / name
    path.write_bytes(source)
    return parse_master_docx(path)


def _paragraphs(payload: bytes):
    return Document(io.BytesIO(payload)).paragraphs


def _paragraph(payload: bytes, prefix: str):
    matches = [p for p in _paragraphs(payload) if p.text.startswith(prefix)]
    assert len(matches) == 1, [p.text for p in _paragraphs(payload)]
    return matches[0]


def _section_breaks(payload: bytes) -> list:
    """Every body paragraph that ends a Word section (``w:pPr/w:sectPr``)."""
    return [
        child
        for child in _body_children(payload)
        if child.tag == qn("w:p")
        and child.find(f"{qn('w:pPr')}/{qn('w:sectPr')}") is not None
    ]


def _child_texts(payload: bytes) -> list[str]:
    """One entry per body child: its text, "<break>" for an empty paragraph
    holding a section break, "" for any other empty paragraph, and the tag
    name for a non-paragraph."""
    texts = []
    for child in _body_children(payload):
        if child.tag != qn("w:p"):
            texts.append(etree.QName(child).localname)
            continue
        text = "".join(child.itertext())
        if not text and child.find(f"{qn('w:pPr')}/{qn('w:sectPr')}") is not None:
            text = "<break>"
        texts.append(text)
    return texts


def _hold_break(paragraph, document) -> None:
    """Give ``paragraph`` a section break (a copy of the body's sectPr)."""
    import copy as _copy

    paragraph._p.get_or_add_pPr().append(_copy.deepcopy(document.sections[0]._sectPr))


def _typed_letter_master() -> bytes:
    """Typed letters with a TAB after them and a bolded phrase — the shape of
    an office master that does not use Word numbering."""
    document = Document()
    for line in (
        "SECTION 23 05 48",
        "VIBRATION CONTROLS",
        "PART 1 - GENERAL",
        "1.1\tSUMMARY",
    ):
        document.add_paragraph(line)
    provision = document.add_paragraph()
    provision.add_run("A.\tSection includes ")
    provision.add_run("vibration isolation").bold = True
    provision.add_run(" for mechanical equipment.")
    document.add_paragraph("B.\tRelated requirements are specified elsewhere.")
    document.add_paragraph("END OF SECTION")
    return _save(document)


def test_a_relettered_provision_keeps_its_tab_and_emphasis(tmp_path):
    """Finding #1. Inserting a provision at the top reletters every sibling
    below it, and each relettered provision used to be rebuilt from its
    first run: the tab after the letter became a space and the bold phrase
    was lost. The new provision got "A. " where the master uses a tab."""
    source = _typed_letter_master()
    imported = _parse(tmp_path, source)
    article = imported.section.parts[0].articles[0]
    section, _ = apply_edits(
        imported.section,
        [
            {
                "action": "add_paragraph",
                "target_id": article.uid,
                "position": 0,
                "text": "Provide seismic restraints.",
            }
        ],
    )

    exported = _render(source, section, imported.format_map)

    texts = [p.text for p in _paragraphs(exported)]
    assert "A.\tProvide seismic restraints." in texts
    assert "B.\tSection includes vibration isolation for mechanical equipment." in texts
    assert "C.\tRelated requirements are specified elsewhere." in texts
    relettered = _paragraph(exported, "B.\tSection")
    assert [run.text for run in relettered.runs if run.bold] == [
        "vibration isolation"
    ]


def test_an_edited_provision_keeps_emphasis_on_its_unchanged_words(tmp_path):
    """The plan's new test: an edit changes words, not the formatting of the
    words around them. A replaced word takes the formatting of the word it
    replaced, which is what Word itself does when you type over a selection."""
    source = _typed_letter_master()
    imported = _parse(tmp_path, source)
    first = imported.section.parts[0].articles[0].paragraphs[0]

    outside, _ = apply_edits(
        imported.section,
        [
            {
                "action": "replace",
                "target_id": first.uid,
                "text": "Section includes vibration isolation for HVAC equipment.",
            }
        ],
    )
    exported = _render(source, outside, imported.format_map)
    edited = _paragraph(exported, "A.\t")
    assert edited.text == "A.\tSection includes vibration isolation for HVAC equipment."
    assert [run.text for run in edited.runs if run.bold] == ["vibration isolation"]

    inside, _ = apply_edits(
        imported.section,
        [
            {
                "action": "replace",
                "target_id": first.uid,
                "text": "Section includes seismic isolation for mechanical equipment.",
            }
        ],
    )
    exported = _render(source, inside, imported.format_map)
    edited = _paragraph(exported, "A.\t")
    assert edited.text == (
        "A.\tSection includes seismic isolation for mechanical equipment."
    )
    assert "".join(run.text for run in edited.runs if run.bold) == (
        "seismic isolation"
    )
    assert not any(run.bold for run in edited.runs if "mechanical" in run.text)


def _break_master(*, held: bool) -> bytes:
    """A section break between SUMMARY and SCHEDULE: in its own empty
    paragraph, or held in the pPr of the last provision above it (how Word
    usually saves one)."""
    document = Document()
    for line in (
        "SECTION 23 05 48",
        "VIBRATION CONTROLS",
        "PART 1 - GENERAL",
        "1.1 SUMMARY",
        "A. Section includes vibration isolation.",
    ):
        document.add_paragraph(line)
    last = document.add_paragraph("B. Related requirements.")
    if held:
        _hold_break(last, document)
    else:
        _hold_break(document.add_paragraph(), document)
    for line in (
        "1.2 SCHEDULE",
        "A. Provide isolators as scheduled.",
        "END OF SECTION",
    ):
        document.add_paragraph(line)
    return _save(document)


def test_deleting_the_provision_below_a_section_break_keeps_the_break(tmp_path):
    """Finding #2. The empty paragraph holding the break was treated as a
    spacer, and spacers die with the element below them: the document went
    from two Word sections to one — how a landscape schedule turns portrait."""
    source = _break_master(held=False)
    imported = _parse(tmp_path, source)
    schedule = imported.section.parts[0].articles[1]
    section, _ = apply_edits(
        imported.section, [{"action": "delete", "target_id": schedule.uid}]
    )

    exported = _render(source, section, imported.format_map)

    assert len(_section_breaks(exported)) == 1
    texts = _child_texts(exported)
    assert texts[texts.index("B. Related requirements.") + 1] == "<break>"


def test_moving_the_provision_below_a_section_break_leaves_the_break(tmp_path):
    """A break belongs to the content above it: moving what sits below it
    must not carry it along (it used to travel as the moved element's
    leading spacer)."""
    document = Document()
    for line in (
        "SECTION 23 05 48",
        "VIBRATION CONTROLS",
        "PART 1 - GENERAL",
        "1.1 SUMMARY",
        "A. First provision.",
        "B. Second provision.",
    ):
        document.add_paragraph(line)
    _hold_break(document.add_paragraph(), document)
    document.add_paragraph("C. Third provision.")
    document.add_paragraph("D. Fourth provision.")
    source = _save(document)
    imported = _parse(tmp_path, source)
    article = imported.section.parts[0].articles[0]
    third = article.paragraphs[2]
    assert third.text == "Third provision."
    section, _ = apply_edits(
        imported.section, [{"action": "move", "target_id": third.uid, "position": 0}]
    )

    exported = _render(source, section, imported.format_map)

    assert _child_texts(exported)[4:9] == [
        "A. Third provision.",
        "B. First provision.",
        "C. Second provision.",
        "<break>",
        "D. Fourth provision.",
    ]


def test_moving_the_provision_above_a_section_break_leaves_the_break(tmp_path):
    source = _break_master(held=False)
    imported = _parse(tmp_path, source)
    summary = imported.section.parts[0].articles[0]
    section, _ = apply_edits(
        imported.section,
        [{"action": "move", "target_id": summary.paragraphs[1].uid, "position": 0}],
    )

    exported = _render(source, section, imported.format_map)

    texts = _child_texts(exported)
    assert texts[4:8] == [
        "A. Related requirements.",
        "B. Section includes vibration isolation.",
        "<break>",
        "1.2 SCHEDULE",
    ]


def test_adding_a_provision_after_a_break_holder_does_not_copy_the_break(
    tmp_path,
):
    """Finding #3. The new provision was cloned from the paragraph holding
    the break, pPr and all: one section break became two."""
    source = _break_master(held=True)
    imported = _parse(tmp_path, source)
    summary = imported.section.parts[0].articles[0]
    section, _ = apply_edits(
        imported.section,
        [
            {
                "action": "add_paragraph",
                "target_id": summary.uid,
                "text": "Provide seismic restraints.",
            }
        ],
    )

    exported = _render(source, section, imported.format_map)

    breaks = _section_breaks(exported)
    assert len(breaks) == 1
    assert "".join(breaks[0].itertext()) == "B. Related requirements."
    added = next(
        child
        for child in _body_children(exported)
        if "Provide seismic restraints." in "".join(child.itertext())
    )
    assert added.find(f"{qn('w:pPr')}/{qn('w:sectPr')}") is None


def test_deleting_a_break_holder_leaves_an_empty_paragraph_holding_it(tmp_path):
    source = _break_master(held=True)
    imported = _parse(tmp_path, source)
    holder = imported.section.parts[0].articles[0].paragraphs[1]
    assert holder.text == "Related requirements."
    section, _ = apply_edits(
        imported.section, [{"action": "delete", "target_id": holder.uid}]
    )

    exported = _render(source, section, imported.format_map)

    breaks = _section_breaks(exported)
    assert len(breaks) == 1
    assert breaks[0].find(qn("w:r")) is None  # empty: nothing but the break
    texts = _child_texts(exported)
    assert texts[4:7] == [
        "A. Section includes vibration isolation.",
        "<break>",
        "1.2 SCHEDULE",
    ]


def test_moving_a_break_holder_leaves_the_break_where_it_was(tmp_path):
    source = _break_master(held=True)
    imported = _parse(tmp_path, source)
    holder = imported.section.parts[0].articles[0].paragraphs[1]
    section, _ = apply_edits(
        imported.section,
        [{"action": "move", "target_id": holder.uid, "position": 0}],
    )

    exported = _render(source, section, imported.format_map)

    texts = _child_texts(exported)
    assert texts[4:8] == [
        "A. Related requirements.",
        "B. Section includes vibration isolation.",
        "<break>",
        "1.2 SCHEDULE",
    ]
    assert len(_section_breaks(exported)) == 1


def test_a_deleted_auto_numbered_break_holder_leaves_no_orphan_number(tmp_path):
    """The empty paragraph that keeps the break must not keep the list
    numbering too: Word prints the number of an empty numbered paragraph."""
    from tests.test_importer import _define_numbering, _numbered

    document = Document()
    document.add_paragraph("SECTION 23 05 48")
    document.add_paragraph("VIBRATION CONTROLS")
    document.add_paragraph("PART 1 - GENERAL")
    _define_numbering(document, 50, {1: ("decimal", "%1.%2"), 2: ("upperLetter", "%3.")})
    _numbered(document, "SUMMARY", 1, "50")
    _numbered(document, "Section includes vibration isolation.", 2, "50")
    holder = _numbered(document, "Related requirements.", 2, "50")
    _hold_break(holder, document)
    _numbered(document, "SCHEDULE", 1, "50")
    _numbered(document, "Provide isolators as scheduled.", 2, "50")
    source = _save(document)
    imported = _parse(tmp_path, source)
    summary = imported.section.parts[0].articles[0]
    assert [p.text for p in summary.paragraphs] == [
        "Section includes vibration isolation.",
        "Related requirements.",
    ]
    section, _ = apply_edits(
        imported.section,
        [{"action": "delete", "target_id": summary.paragraphs[1].uid}],
    )

    exported = _render(source, section, imported.format_map)

    breaks = _section_breaks(exported)
    assert len(breaks) == 1
    num_id = breaks[0].find(f"{qn('w:pPr')}/{qn('w:numPr')}/{qn('w:numId')}")
    assert num_id is not None and num_id.get(qn("w:val")) == "0"


def _revision(tag: str, change_id: int):
    """A tracked-change record by an author who is not Build-a-Spec."""
    element = etree.Element(qn(tag))
    element.set(qn("w:id"), str(change_id))
    element.set(qn("w:author"), "Another Author")
    element.set(qn("w:date"), "2026-09-01T09:00:00Z")
    return element


#: Every revision record a kin can carry in its paragraph properties, and
#: where it sits (Phase 0 follow-up): the clone must drop each of them.
_MARK_REVISION_TAGS = ("w:ins", "w:del", "w:moveFrom", "w:moveTo", "w:rPrChange")


def _give_pending_revisions(paragraph) -> None:
    """``w:pPrChange``; the mark's ``w:ins``/``w:del``/``w:moveFrom``/
    ``w:moveTo``/``w:rPrChange``; ``w:numPr``'s ``w:numberingChange`` and
    ``w:ins``; and a run's ``w:rPrChange`` — each a pending change by another
    author, each at its schema position."""
    properties = paragraph._p.get_or_add_pPr()
    numbering = properties.get_or_add_numPr()
    numbering.get_or_add_ilvl().val = 0
    numbering.get_or_add_numId().val = 0  # "no numbering": the label stays typed
    numbering_change = _revision("w:numberingChange", 90)
    numbering_change.set(qn("w:original"), "%1.")
    numbering.append(numbering_change)
    numbering.append(_revision("w:ins", 91))
    mark = etree.Element(qn("w:rPr"))
    for offset, tag in enumerate(("w:ins", "w:del", "w:moveFrom", "w:moveTo")):
        mark.append(_revision(tag, 92 + offset))
    etree.SubElement(mark, qn("w:b"))
    old_mark = _revision("w:rPrChange", 96)
    etree.SubElement(old_mark, qn("w:rPr"))
    mark.append(old_mark)
    # The mark's w:rPr sits before w:sectPr and w:pPrChange.
    section_break = properties.find(qn("w:sectPr"))
    if section_break is not None:
        section_break.addprevious(mark)
    else:
        properties.append(mark)
    old_properties = _revision("w:pPrChange", 97)
    etree.SubElement(old_properties, qn("w:pPr"))
    properties.append(old_properties)
    run_properties = paragraph.runs[0]._r.get_or_add_rPr()
    old_run = _revision("w:rPrChange", 98)
    etree.SubElement(old_run, qn("w:rPr"))
    run_properties.append(old_run)


def _revision_records(paragraph) -> list[str]:
    """Where ``paragraph`` carries a revision record, as ``parent/tag``."""
    found = []
    properties = paragraph.find(qn("w:pPr"))
    if properties is not None:
        if properties.find(qn("w:pPrChange")) is not None:
            found.append("pPr/pPrChange")
        mark = properties.find(qn("w:rPr"))
        for tag in _MARK_REVISION_TAGS:
            if mark is not None and mark.find(qn(tag)) is not None:
                found.append(f"pPr/rPr/{tag[2:]}")
        numbering = properties.find(qn("w:numPr"))
        for tag in ("w:numberingChange", "w:ins"):
            if numbering is not None and numbering.find(qn(tag)) is not None:
                found.append(f"pPr/numPr/{tag[2:]}")
    for run in paragraph.iterchildren(qn("w:r")):
        if run.find(f"{qn('w:rPr')}/{qn('w:rPrChange')}") is not None:
            found.append("r/rPr/rPrChange")
    return found


def test_a_cloned_template_carries_no_break_identity_or_anchors(tmp_path):
    """Clone hygiene: a new provision is cloned from its kin's formatting,
    never its identity (w14 ids Word expects to be unique), its bookmarks,
    its comment anchors, its section break — or its pending revisions
    (Phase 0 follow-up): the formatted export runs on masters that still
    carry tracked changes, and a kin's ``w:pPrChange`` or tracked paragraph
    mark on a new provision is a change Word attributes to somebody who never
    made it. Run twice: a kin with no pending revisions, then one with every
    record a paragraph's properties can hold."""
    for pending in (False, True):
        document = Document()
        for line in (
            "SECTION 23 05 48",
            "VIBRATION CONTROLS",
            "PART 1 - GENERAL",
            "1.1 SUMMARY",
        ):
            document.add_paragraph(line)
        template = document.add_paragraph()
        template._p.set(f"{{{_W14_NS}}}paraId", "1A2B3C4D")
        template._p.set(f"{{{_W14_NS}}}textId", "4D3C2B1A")
        start = etree.SubElement(template._p, qn("w:bookmarkStart"))
        start.set(qn("w:id"), "7")
        start.set(qn("w:name"), "_Ref7")
        comment = etree.SubElement(template._p, qn("w:commentRangeStart"))
        comment.set(qn("w:id"), "3")
        template.add_run("A. Section includes vibration isolation.")
        end = etree.SubElement(template._p, qn("w:bookmarkEnd"))
        end.set(qn("w:id"), "7")
        comment_end = etree.SubElement(template._p, qn("w:commentRangeEnd"))
        comment_end.set(qn("w:id"), "3")
        _hold_break(template, document)
        if pending:
            _give_pending_revisions(template)
        document.add_paragraph("1.2 SCHEDULE")
        document.add_paragraph("END OF SECTION")
        source = _save(document)
        imported = _parse(tmp_path, source, name=f"master-{pending}.docx")
        summary = imported.section.parts[0].articles[0]
        section, _ = apply_edits(
            imported.section,
            [
                {
                    "action": "add_paragraph",
                    "target_id": summary.uid,
                    "text": "Provide seismic restraints.",
                }
            ],
        )

        exported = _render(source, section, imported.format_map)

        added = next(
            child
            for child in _body_children(exported)
            if "Provide seismic restraints." in "".join(child.itertext())
        )
        assert added.get(f"{{{_W14_NS}}}paraId") is None
        assert added.get(f"{{{_W14_NS}}}textId") is None
        for tag in (
            "w:bookmarkStart",
            "w:bookmarkEnd",
            "w:commentRangeStart",
            "w:commentRangeEnd",
        ):
            assert added.find(f".//{qn(tag)}") is None, tag
        assert added.find(f"{qn('w:pPr')}/{qn('w:sectPr')}") is None
        assert _revision_records(added) == [], pending
        kept = next(
            child
            for child in _body_children(exported)
            if "Section includes vibration" in "".join(child.itertext())
        )
        assert kept.get(f"{{{_W14_NS}}}paraId") == "1A2B3C4D"
        if not pending:
            assert kept.find(f".//{qn('w:bookmarkStart')}") is not None
            continue
        # The formatting survives without the history: the mark stays bold.
        assert added.find(f"{qn('w:pPr')}/{qn('w:rPr')}/{qn('w:b')}") is not None
        # The kin is the master's own, and so is its history. (A paragraph
        # with pending revisions is rewritten by the fallback even when
        # untouched — Phase 0 deviation 2 — which keeps its w:pPr and first
        # run's properties and drops the rest, its bookmark included.)
        assert _revision_records(kept) == [
            "pPr/pPrChange",
            *(f"pPr/rPr/{tag[2:]}" for tag in _MARK_REVISION_TAGS),
            "pPr/numPr/numberingChange",
            "pPr/numPr/ins",
            "r/rPr/rPrChange",
        ]


def _not_used_master() -> bytes:
    document = Document()
    for line in (
        "SECTION 23 05 48",
        "VIBRATION CONTROLS",
        "PART 1 - GENERAL",
        "1.1 SUMMARY",
        "A. Section includes vibration isolation.",
        "PART 2 - PRODUCTS",
        "(Not used.)",
        "PART 3 - EXECUTION",
        "(Not used.)",
        "END OF SECTION",
    ):
        document.add_paragraph(line)
    return _save(document)


def test_a_part_that_now_has_articles_drops_its_not_used_line(tmp_path):
    """Finding #4. The importer skips a PART's "(Not used.)" line, so the
    export carried it as leading content of the next PART heading — and a
    PART that gained an article printed "2.1 ISOLATORS" then "(Not used.)"."""
    source = _not_used_master()
    imported = _parse(tmp_path, source)
    assert _child_texts(_render(source, imported.section, imported.format_map)).count(
        "(Not used.)"
    ) == 2

    products, _ = apply_edits(
        imported.section,
        [{"action": "add_article", "target_id": "pt2", "text": "ISOLATORS"}],
    )
    texts = _child_texts(_render(source, products, imported.format_map))
    assert texts[5:8] == ["PART 2 - PRODUCTS", "2.1 ISOLATORS", "PART 3 - EXECUTION"]
    # PART 3 still has no article, so its line stays.
    assert texts[8] == "(Not used.)"

    both, _ = apply_edits(
        products,
        [{"action": "add_article", "target_id": "pt3", "text": "INSTALLATION"}],
    )
    texts = _child_texts(_render(source, both, imported.format_map))
    assert "(Not used.)" not in texts
    assert texts[5:10] == [
        "PART 2 - PRODUCTS",
        "2.1 ISOLATORS",
        "PART 3 - EXECUTION",
        "3.1 INSTALLATION",
        "END OF SECTION",
    ]


def test_unmodelled_content_above_a_deleted_provision_stays_in_place(tmp_path):
    """The migration issue: a picture-only paragraph (never modelled — it
    has no text) above a provision the user deleted used to be swept to the
    end of the section."""
    from tests.docx_fidelity_helpers import _png_bytes

    document = Document()
    for line in (
        "SECTION 23 05 48",
        "VIBRATION CONTROLS",
        "PART 1 - GENERAL",
        "1.1 SUMMARY",
        "A. Section includes vibration isolation.",
    ):
        document.add_paragraph(line)
    document.add_picture(io.BytesIO(_png_bytes()))
    document.add_paragraph("B. See the figure above.")
    document.add_paragraph("C. Related requirements.")
    document.add_paragraph("END OF SECTION")
    source = _save(document)
    imported = _parse(tmp_path, source)
    article = imported.section.parts[0].articles[0]
    assert [p.text for p in article.paragraphs] == [
        "Section includes vibration isolation.",
        "See the figure above.",
        "Related requirements.",
    ]
    section, _ = apply_edits(
        imported.section,
        [{"action": "delete", "target_id": article.paragraphs[1].uid}],
    )

    exported = _render(source, section, imported.format_map)

    children = _body_children(exported)
    picture = next(
        i for i, child in enumerate(children) if child.find(f".//{qn('w:drawing')}") is not None
    )
    texts = _child_texts(exported)
    assert texts[picture - 1] == "A. Section includes vibration isolation."
    assert texts[picture + 1] == "B. Related requirements."


def test_article_numbers_keep_the_masters_format(tmp_path):
    """Found while building Phase 0: a master numbering its articles "1.01"
    or writing "1.2 - TITLE" had every untouched article heading rewritten
    as "1.1 TITLE" on an export with no edits at all."""
    document = Document()
    for line in (
        "SECTION 23 05 48",
        "VIBRATION CONTROLS",
        "PART 1 - GENERAL",
        "1.01\tSUMMARY",
        "A. Section includes vibration isolation.",
        "1.02 - SUBMITTALS",
        "A. Product data.",
        "1.03. QUALITY ASSURANCE",
        "A. Installer qualifications.",
        "END OF SECTION",
    ):
        document.add_paragraph(line)
    source = _save(document)
    imported = _parse(tmp_path, source)

    untouched = _render(source, imported.section, imported.format_map)
    before = [etree.tostring(el) for el in _body_children(source)]
    assert [etree.tostring(el) for el in _body_children(untouched)] == before

    section, _ = apply_edits(
        imported.section,
        [
            {
                "action": "add_article",
                "target_id": "pt1",
                "position": 0,
                "text": "GENERAL REQUIREMENTS",
            }
        ],
    )
    texts = [p.text for p in _paragraphs(_render(source, section, imported.format_map))]
    assert texts[3:9] == [
        "1.01\tGENERAL REQUIREMENTS",
        "1.02\tSUMMARY",
        "A. Section includes vibration isolation.",
        "1.03 - SUBMITTALS",
        "A. Product data.",
        "1.04. QUALITY ASSURANCE",
    ]


def test_a_new_article_is_cloned_from_an_article_heading(tmp_path):
    """Found while building Phase 0: a new article was cloned from whatever
    was emitted last — usually a provision — so it looked like one."""
    document = Document()
    for line, style in (
        ("SECTION 23 05 48", None),
        ("VIBRATION CONTROLS", None),
        ("PART 1 - GENERAL", "Heading 1"),
        ("1.1 SUMMARY", "Heading 2"),
        ("A. Section includes vibration isolation.", None),
        ("B. Related requirements.", None),
        ("END OF SECTION", None),
    ):
        document.add_paragraph(line, style=style)
    source = _save(document)
    imported = _parse(tmp_path, source)
    section, _ = apply_edits(
        imported.section,
        [{"action": "add_article", "target_id": "pt1", "text": "SUBMITTALS"}],
    )

    exported = _render(source, section, imported.format_map)

    added = _paragraph(exported, "1.2 SUBMITTALS")
    assert added.style.name == "Heading 2"


def test_a_new_article_in_an_auto_numbered_master_types_no_number(tmp_path):
    """Word renders an auto-numbered heading's "1.2" itself; typing one into
    the text as well printed it twice."""
    from tests.test_importer import _define_numbering, _numbered

    document = Document()
    document.add_paragraph("SECTION 23 05 48")
    document.add_paragraph("VIBRATION CONTROLS")
    document.add_paragraph("PART 1 - GENERAL")
    _define_numbering(document, 50, {1: ("decimal", "%1.%2"), 2: ("upperLetter", "%3.")})
    _numbered(document, "SUMMARY", 1, "50")
    _numbered(document, "Section includes vibration isolation.", 2, "50")
    source = _save(document)
    imported = _parse(tmp_path, source)
    section, _ = apply_edits(
        imported.section,
        [{"action": "add_article", "target_id": "pt1", "text": "SUBMITTALS"}],
    )

    exported = _render(source, section, imported.format_map)

    added = _paragraph(exported, "SUBMITTALS")
    assert added.text == "SUBMITTALS"
    assert added._p.find(f"{qn('w:pPr')}/{qn('w:numPr')}") is not None


def test_content_after_the_last_provision_is_carried_verbatim(tmp_path):
    """Found while building Phase 0: blank lines, page breaks and section
    breaks after the last provision (around END OF SECTION, before an
    appendix) were dropped on an export with no edits at all."""
    document = Document()
    for line in (
        "SECTION 23 05 48",
        "VIBRATION CONTROLS",
        "PART 1 - GENERAL",
        "1.1 SUMMARY",
        "A. Section includes vibration isolation.",
        "",
        "END OF SECTION",
    ):
        document.add_paragraph(line)
    _hold_break(document.add_paragraph(), document)
    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    document.add_paragraph("APPENDIX - KEEP")
    source = _save(document)
    imported = _parse(tmp_path, source)

    exported = _render(source, imported.section, imported.format_map)

    before = [etree.tostring(el) for el in _body_children(source)]
    assert [etree.tostring(el) for el in _body_children(exported)] == before


def test_a_no_op_export_changes_only_what_the_tree_changed():
    """Every corpus master, exported with no edits. Content the tree does not
    model must come through untouched and in place; the one element allowed
    to differ is a provision whose label the tree itself renumbered (the
    fidelity fixture mixes typed letters with a Word-numbered sibling).

    Compared as exclusive C14N: LibreOffice repeats a namespace declaration
    the document root already makes, and lxml drops the redundant copy when
    an element is re-parented — the same XML, spelled differently.
    """
    import tempfile
    from pathlib import Path

    from tests.docx_corpus import build_case, corpus_cases

    def canonical(element) -> bytes:
        return etree.tostring(element, method="c14n", exclusive=True)

    workspace = Path(tempfile.mkdtemp())
    for case in corpus_cases():
        source = build_case(case, workspace)
        path = workspace / f"{case.case_id}.docx"
        path.write_bytes(source)
        imported = parse_master_docx(path)
        exported = _render(source, imported.section, imported.format_map)
        before = [canonical(el) for el in _body_children(source)]
        after = [canonical(el) for el in _body_children(exported)]
        assert len(before) == len(after), case.case_id
        anchored = {
            anchor.origin_index for anchor in imported.format_map.anchors
        }
        differing = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        assert set(differing) <= anchored, (case.case_id, differing)
        assert len(differing) <= 1, (case.case_id, differing)


def test_a_control_character_in_a_provision_exports_as_a_visible_escape(tmp_path):
    """XML 1.0 cannot carry a vertical tab; the export used to raise instead
    of writing the file (the other exporters escape such characters)."""
    source = _typed_letter_master()
    imported = _parse(tmp_path, source)
    first = imported.section.parts[0].articles[0].paragraphs[0]
    section, _ = apply_edits(
        imported.section,
        [{"action": "replace", "target_id": first.uid, "text": "Bad \x0b char."}],
    )

    exported = _render(source, section, imported.format_map)

    assert "A.\tBad \\u000B char." in [p.text for p in _paragraphs(exported)]


# ---------------------------------------------------------------------------
# Hyperlinks and nesting levels (the redline plan's two "Found, not done")
# ---------------------------------------------------------------------------


def _linked_typed_master() -> bytes:
    """A typed-letter master whose provision A holds a bold phrase and a
    hyperlink — the paragraph every edit used to flatten."""
    from tests.docx_fidelity_helpers import _append_hyperlink

    document = Document()
    for line in ("SECTION 23 05 48", "VIBRATION CONTROLS", "PART 1 - GENERAL", "1.1\tSUMMARY"):
        document.add_paragraph(line)
    provision = document.add_paragraph()
    provision.add_run("A.\tSection includes ")
    provision.add_run("vibration isolation").bold = True
    provision.add_run(" per ")
    _append_hyperlink(provision, "the client standard", "https://example.com/std")
    provision.add_run(" for mechanical equipment.")
    document.add_paragraph("B.\tRelated requirements are specified elsewhere.")
    document.add_paragraph("END OF SECTION")
    return _save(document)


def test_a_relettered_provision_keeps_its_link_emphasis_and_tab(tmp_path):
    """The reported reproduction. Adding a provision above one that holds a
    hyperlink reletters it, and a hyperlink sent the whole paragraph to the
    fallback: the link became plain text, the bold phrase lost its bold and
    the tab after "B." became a space. Now the splice maps the link."""
    source = _linked_typed_master()
    imported = _parse(tmp_path, source)
    article = imported.section.parts[0].articles[0]
    section, _ = apply_edits(
        imported.section,
        [
            {
                "action": "add_paragraph",
                "target_id": article.uid,
                "position": 0,
                "text": "Provide seismic restraints.",
            }
        ],
    )
    stats: dict = {}
    exported = render_preserving_docx(
        source_bytes=source, format_map=imported.format_map, current=section, stats=stats
    )
    assert stats["fallback"] == {}
    relettered = _paragraph(exported, "B.\t")
    assert relettered.text == (
        "B.\tSection includes vibration isolation per the client standard "
        "for mechanical equipment."
    )
    assert [run.text for run in relettered.runs if run.bold] == ["vibration isolation"]
    (link,) = relettered._p.findall(qn("w:hyperlink"))
    source_link = _paragraph(source, "A.\t")._p.find(qn("w:hyperlink"))
    assert link.get(qn("r:id")) == source_link.get(qn("r:id"))
    assert "".join(t.text for t in link.iter(qn("w:t"))) == "the client standard"
    assert link.find(f".//{qn('w:u')}") is not None  # the link keeps its look


def test_a_word_added_after_a_link_is_not_part_of_it(tmp_path):
    source = _linked_typed_master()
    imported = _parse(tmp_path, source)
    first = imported.section.parts[0].articles[0].paragraphs[0]
    section, _ = apply_edits(
        imported.section,
        [
            {
                "action": "replace",
                "target_id": first.uid,
                "text": "Section includes vibration isolation per the client standard "
                "latest edition for mechanical equipment.",
            }
        ],
    )
    exported = _render(source, section, imported.format_map)
    edited = _paragraph(exported, "A.\t")
    (link,) = edited._p.findall(qn("w:hyperlink"))
    assert "".join(t.text for t in link.iter(qn("w:t"))) == "the client standard"
    added = [r for r in edited.runs if "latest" in r.text]
    assert added and all(r._r.find(f".//{qn('w:u')}") is None for r in added)


def _numbered_levels_master(levels: dict, *, typed_article: bool = False) -> bytes:
    """Direct Word numbering on every provision, one article, two provisions
    at the list's provision level (the ilvl drawing "%N." upper letters)."""
    from tests.test_importer import _define_numbering, _numbered

    document = Document()
    for line in ("SECTION 23 05 48", "VIBRATION CONTROLS", "PART 1 - GENERAL"):
        document.add_paragraph(line)
    _define_numbering(document, 50, levels)
    provision = next(i for i, (fmt, _t) in levels.items() if fmt == "upperLetter")
    if typed_article:
        document.add_paragraph("1.1 SUMMARY")
    else:
        _numbered(document, "SUMMARY", provision - 1, "50")
    _numbered(document, "Section includes vibration isolation.", provision, "50")
    _numbered(document, "Related requirements.", provision, "50")
    document.add_paragraph("END OF SECTION")
    return _save(document)


def _add_child(tmp_path, source: bytes):
    """Add a provision under the first one; the export, its stats, and the
    new provision's own ``w:numPr``."""
    imported = _parse(tmp_path, source)
    parent = imported.section.parts[0].articles[0].paragraphs[0]
    section, _ = apply_edits(
        imported.section,
        [{"action": "add_paragraph", "target_id": parent.uid, "text": "Spring isolators."}],
    )
    stats: dict = {}
    exported = render_preserving_docx(
        source_bytes=source, format_map=imported.format_map, current=section, stats=stats
    )
    (added,) = [p._p for p in _paragraphs(exported) if p.text.endswith("Spring isolators.")]
    return section, exported, stats, added.find(f"{qn('w:pPr')}/{qn('w:numPr')}")


def _level(numbering) -> tuple[str, str]:
    return (
        numbering.find(qn("w:ilvl")).get(qn("w:val")),
        numbering.find(qn("w:numId")).get(qn("w:val")),
    )


def _depths(payload: bytes, tmp_path) -> list[tuple[int, str]]:
    path = tmp_path / "reread.docx"
    path.write_bytes(payload)

    def walk(nodes, depth):
        for node in nodes:
            yield depth, node.text
            yield from walk(node.children, depth + 1)

    article = parse_master_docx(path).section.parts[0].articles[0]
    return list(walk(article.paragraphs, 0))


def test_a_new_sub_provision_takes_its_own_numbering_level(tmp_path):
    """No provision in the master sits one level down, so the new one is
    cloned from its parent-level kin — and used to keep its ``w:ilvl``, so
    Word drew it one level up and the importer read it back as its parent's
    sibling. Its level is now the kin's offset by the depth difference."""
    source = _numbered_levels_master(
        {1: ("decimal", "%1.%2"), 2: ("upperLetter", "%3."), 3: ("decimal", "%4.")}
    )
    section, exported, stats, numbering = _add_child(tmp_path, source)
    assert (stats["level_offset"], stats["level_kept"]) == (1, 0)
    assert _level(numbering) == ("3", "50")
    assert _depths(exported, tmp_path) == [
        (0, "Section includes vibration isolation."),
        (1, "Spring isolators."),
        (0, "Related requirements."),
    ]


def test_a_style_numbered_kin_gives_the_clone_its_own_level_explicitly(tmp_path):
    """Numbering on the paragraph STYLE (PR1), the office-master shape: the
    clone keeps its kin's style and gains its own ``w:numPr`` naming both
    the level and the instance, which Word and the importer read alike."""
    from tests.test_import_office_master import _add_style
    from tests.test_importer import _define_numbering

    document = Document()
    _define_numbering(
        document, 70, {1: ("decimal", "%1.%2"), 2: ("upperLetter", "%3."), 3: ("decimal", "%4.")}
    )
    body = _add_style(document, "SpecBody", num_id=None, ilvl=None)
    article = _add_style(document, "ART", num_id=70, ilvl=1, based_on=body)
    provision = _add_style(document, "PR1", num_id=70, ilvl=2, based_on=body)
    for line in ("SECTION 21 05 00", "COMMON WORK RESULTS", "PART 1 - GENERAL"):
        document.add_paragraph(line)
    document.add_paragraph("SUMMARY", style=article)
    document.add_paragraph("Section includes vibration isolation.", style=provision)
    document.add_paragraph("Related requirements.", style=provision)
    document.add_paragraph("END OF SECTION")
    source = _save(document)
    _section, exported, stats, numbering = _add_child(tmp_path, source)
    assert stats["level_offset"] == 1
    assert _level(numbering) == ("3", "70")
    added = _paragraph(exported, "Spring isolators.")._p
    style = added.find(f"{qn('w:pPr')}/{qn('w:pStyle')}")
    assert style is not None and style.get(qn("w:val")) == "PR1"
    assert [depth for depth, _text in _depths(exported, tmp_path)] == [0, 1, 0]


def test_a_new_sibling_of_word_numbered_kin_is_its_kins_clone_unchanged(tmp_path):
    """Kin at the new provision's own depth needs no level of its own: the
    clone is the kin's paragraph exactly — in a style-numbered master, no
    ``w:numPr`` of its own beside the style's — and nothing is counted."""
    from tests.test_import_office_master import _add_style
    from tests.test_importer import _define_numbering

    document = Document()
    _define_numbering(
        document, 70, {1: ("decimal", "%1.%2"), 2: ("upperLetter", "%3."), 3: ("decimal", "%4.")}
    )
    body = _add_style(document, "SpecBody", num_id=None, ilvl=None)
    article = _add_style(document, "ART", num_id=70, ilvl=1, based_on=body)
    provision = _add_style(document, "PR1", num_id=70, ilvl=2, based_on=body)
    for line in ("SECTION 21 05 00", "COMMON WORK RESULTS", "PART 1 - GENERAL"):
        document.add_paragraph(line)
    document.add_paragraph("SUMMARY", style=article)
    document.add_paragraph("Section includes vibration isolation.", style=provision)
    document.add_paragraph("Related requirements.", style=provision)
    document.add_paragraph("END OF SECTION")
    source = _save(document)
    imported = _parse(tmp_path, source)
    target = imported.section.parts[0].articles[0]
    section, _ = apply_edits(
        imported.section,
        [{"action": "add_paragraph", "target_id": target.uid, "text": "Spring isolators."}],
    )
    stats: dict = {}
    exported = render_preserving_docx(
        source_bytes=source, format_map=imported.format_map, current=section, stats=stats
    )
    assert (stats["level_offset"], stats["level_kept"]) == (0, 0)
    added = _paragraph(exported, "Spring isolators.")._p
    kin = _paragraph(exported, "Related requirements.")._p
    assert added.find(f"{qn('w:pPr')}/{qn('w:numPr')}") is None
    assert etree.tostring(added.find(qn("w:pPr"))) == etree.tostring(kin.find(qn("w:pPr")))
    assert [depth for depth, _text in _depths(exported, tmp_path)] == [0, 0, 0]


def test_a_level_the_masters_numbering_does_not_define_keeps_its_kins(tmp_path):
    """The master's list stops at the provision level: there is no number
    to give a sub-provision, so it keeps its kin's level (one level up in
    Word — never a number the master's numbering cannot draw), and the
    export's diagnostics count it."""
    source = _numbered_levels_master({1: ("decimal", "%1.%2"), 2: ("upperLetter", "%3.")})
    _section, _exported, stats, numbering = _add_child(tmp_path, source)
    assert (stats["level_offset"], stats["level_kept"]) == (0, 1)
    assert _level(numbering) == ("2", "50")


@pytest.mark.parametrize(
    "label",
    [("none", "%4."), ("decimal", ""), ("decimal", "  ")],
    ids=["numFmt-none", "empty-lvlText", "blank-lvlText"],
)
def test_a_level_that_draws_no_label_is_never_taken(tmp_path, label):
    """The definition has the next level down, but it draws no label —
    ``numFmt="none"``, or an empty ``lvlText`` — so a sub-provision given it
    would print with no number at all. It keeps its kin's level instead: a
    label, one level up (Codex review on PR #193)."""
    source = _numbered_levels_master(
        {1: ("decimal", "%1.%2"), 2: ("upperLetter", "%3."), 3: label}
    )
    _section, _exported, stats, numbering = _add_child(tmp_path, source)
    assert (stats["level_offset"], stats["level_kept"]) == (0, 1)
    assert _level(numbering) == ("2", "50")


def test_a_level_the_numbering_draws_as_a_heading_is_never_taken(tmp_path):
    """A list whose next level down is written "%2.%3" draws ARTICLE
    numbers there, and the importer promotes such a paragraph to an
    article: a sub-provision given that level would come back as a heading.
    It keeps its kin's level instead."""
    source = _numbered_levels_master(
        {1: ("upperLetter", "%2."), 2: ("decimal", "%2.%3")}, typed_article=True
    )
    _section, exported, stats, numbering = _add_child(tmp_path, source)
    assert (stats["level_offset"], stats["level_kept"]) == (0, 1)
    assert _level(numbering) == ("1", "50")
    path = tmp_path / "reread.docx"
    path.write_bytes(exported)
    assert len(parse_master_docx(path).section.parts[0].articles) == 1


def test_a_typed_letter_kin_is_never_renumbered(tmp_path):
    """A typed label carries its own level ("1." under "A."): nothing to
    offset, and no Word numbering is invented for it."""
    source = _typed_letter_master()
    _section, _exported, stats, numbering = _add_child(tmp_path, source)
    assert (stats["level_offset"], stats["level_kept"]) == (0, 0)
    assert numbering is None


def test_numbering_that_cannot_be_read_offsets_nothing():
    """The export reads the upload's numbering the way the importer does,
    and degrades the way it does: a package whose numbering or styles cannot
    be read has none. Nothing raises, and no level is offset — a new
    sub-provision keeps its kin's."""
    from backend.spec_doc.source_render import _NumberingTables

    tables = _NumberingTables(b"not a Word package")
    assert tables.draws_a_provision(0, 1) is False
    assert (tables.catalog, tables.style_numbering, tables.default_style_id) == (
        {},
        {},
        "",
    )
