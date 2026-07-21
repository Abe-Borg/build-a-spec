"""Master-import tests: round-trip goldens, tracked changes, structure
heuristics — docx fixtures built in-memory with python-docx."""
from __future__ import annotations

import io

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from backend.spec_doc.docx_export import build_docx
from backend.spec_doc.importer import (
    ImportResult,
    MasterImportError,
    parse_master_docx,
)
from backend.spec_doc.model import DocumentStore, SpecSection


def _write_docx(tmp_path, paragraphs: list[str], name="master.docx"):
    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    path = tmp_path / name
    document.save(str(path))
    return path


_MASTER_LINES = [
    "SECTION 21 13 13",
    "WET-PIPE SPRINKLER SYSTEMS",
    "PART 1 - GENERAL",
    "1.1 SUMMARY",
    "A. Section includes wet-pipe sprinkler systems.",
    "B. Related sections: 21 13 19.",
    "1.2 REFERENCES",
    "A. NFPA 13 - Standard for the Installation of Sprinkler Systems.",
    "PART 2 - PRODUCTS",
    "2.1 PIPE AND FITTINGS",
    "A. Steel pipe: ASTM A53.",
    "1. Schedule 40 for 2 inches and smaller.",
    "a. Threaded joints.",
    "1) Approved thread sealant.",
    "PART 3 - EXECUTION",
    "3.1 INSTALLATION",
    "A. Install per NFPA 13.",
    "END OF SECTION 21 13 13",
    "This trailing text is after END OF SECTION and must not import.",
]


def test_import_builds_the_tree_with_imported_status(tmp_path):
    result = parse_master_docx(_write_docx(tmp_path, _MASTER_LINES))
    section = result.section
    assert section.number == "21 13 13"
    assert section.title == "WET-PIPE SPRINKLER SYSTEMS"
    assert [a.title for a in section.parts[0].articles] == [
        "SUMMARY",
        "REFERENCES",
    ]
    assert [a.title for a in section.parts[1].articles] == ["PIPE AND FITTINGS"]
    assert [a.title for a in section.parts[2].articles] == ["INSTALLATION"]

    # Nesting: A. -> 1. -> a. -> 1)
    piping = section.parts[1].articles[0]
    level0 = piping.paragraphs[0]
    assert level0.text == "Steel pipe: ASTM A53."
    assert level0.children[0].text.startswith("Schedule 40")
    assert level0.children[0].children[0].text == "Threaded joints."
    assert (
        level0.children[0].children[0].children[0].text
        == "Approved thread sealant."
    )

    # Every block imported; nothing after END OF SECTION.
    statuses = set()

    def collect(paragraphs):
        for p in paragraphs:
            statuses.add(p.status)
            collect(p.children)

    for part in section.parts:
        for article in part.articles:
            collect(article.paragraphs)
    assert statuses == {"imported"}
    assert result.imported_block_count == 8
    assert not any("trailing" in w for w in result.warnings)


def test_import_round_trip_through_store_and_export(tmp_path):
    result = parse_master_docx(_write_docx(tmp_path, _MASTER_LINES))
    store = DocumentStore()
    store.adopt_imported(result.section)
    # One version snapshot; undo returns to blank.
    assert store.index == 1 and len(store.versions) == 2
    assert store.undo() and store.doc.is_empty()
    assert store.redo()

    # Serialization round-trip (the integrity check accepts the built ids).
    restored = SpecSection.from_dict(store.doc.to_dict())
    assert restored.parts[1].articles[0].paragraphs[0].status == "imported"

    # Export carries the imported-provisions schedule.
    payload = build_docx(store.doc)
    document = Document(io.BytesIO(payload))
    texts = [p.text for p in document.paragraphs]
    assert "IMPORTED PROVISIONS NOT YET REVIEWED" in texts


def test_import_re_parse_golden(tmp_path):
    """docx → tree → export docx → re-import ≈ same tree shape."""
    first = parse_master_docx(_write_docx(tmp_path, _MASTER_LINES))
    exported = build_docx(first.section)
    exported_path = tmp_path / "exported.docx"
    exported_path.write_bytes(exported)
    second = parse_master_docx(exported_path)
    assert second.section.number == first.section.number
    assert [a.title for a in second.section.parts[0].articles][:2] == [
        "SUMMARY",
        "REFERENCES",
    ]
    # Paragraph counts survive (schedules after END OF SECTION are ignored).
    def count(section):
        total = 0

        def walk(paragraphs):
            nonlocal total
            for p in paragraphs:
                total += 1
                walk(p.children)

        for part in section.parts:
            for article in part.articles:
                walk(article.paragraphs)
        return total

    assert count(second.section) == count(first.section)


def test_unlabeled_and_orphan_content_is_kept_with_warnings(tmp_path):
    lines = [
        "PART 1 - GENERAL",
        "Orphan text before any article.",
        "1.1 SUMMARY",
        "A. Labeled provision.",
        "Continuation prose with no label.",
    ]
    result = parse_master_docx(_write_docx(tmp_path, lines))
    part1 = result.section.parts[0]
    assert [a.title for a in part1.articles] == ["IMPORTED CONTENT", "SUMMARY"]
    assert part1.articles[0].paragraphs[0].text == "Orphan text before any article."
    assert [p.text for p in part1.articles[1].paragraphs] == [
        "Labeled provision.",
        "Continuation prose with no label.",
    ]
    assert any("before any article" in w for w in result.warnings)


def test_tables_flatten_with_warning(tmp_path):
    document = Document()
    document.add_paragraph("PART 2 - PRODUCTS")
    document.add_paragraph("2.1 SCHEDULE")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "K5.6"
    table.rows[0].cells[1].text = "Quick response"
    path = tmp_path / "table.docx"
    document.save(str(path))

    result = parse_master_docx(path)
    schedule = result.section.parts[1].articles[0]
    assert schedule.paragraphs[0].text == "K5.6 | Quick response"
    assert any("tables" in w for w in result.warnings)


def test_tracked_changes_import_accept_all_view(tmp_path):
    document = Document()
    document.add_paragraph("PART 1 - GENERAL")
    document.add_paragraph("1.1 SUMMARY")
    paragraph = document.add_paragraph("A. Comply with ")
    # <w:ins><w:r>NFPA 13-2025</w:r></w:ins> — inserted text (kept).
    ins = OxmlElement("w:ins")
    ins_run = OxmlElement("w:r")
    ins_text = OxmlElement("w:t")
    ins_text.text = "NFPA 13-2025"
    ins_run.append(ins_text)
    ins.append(ins_run)
    paragraph._p.append(ins)
    # <w:del><w:r><w:delText>NFPA 13-2016</w:delText></w:r></w:del> (dropped).
    deleted = OxmlElement("w:del")
    del_run = OxmlElement("w:r")
    del_text = OxmlElement("w:delText")
    del_text.text = "NFPA 13-2016"
    del_run.append(del_text)
    deleted.append(del_run)
    paragraph._p.append(deleted)
    tail = paragraph.add_run(" throughout.")
    # Move the tail run after the revision wrappers (document order).
    paragraph._p.append(tail._r)

    path = tmp_path / "redline.docx"
    document.save(str(path))
    result = parse_master_docx(path)
    text = result.section.parts[0].articles[0].paragraphs[0].text
    assert text == "Comply with NFPA 13-2025 throughout."
    assert "NFPA 13-2016" not in text
    assert result.tracked_changes_detected is True
    assert any("tracked changes" in w.lower() for w in result.warnings)


def test_auto_numbered_master_uses_ilvl(tmp_path):
    document = Document()
    document.add_paragraph("PART 3 - EXECUTION")
    document.add_paragraph("3.1 INSTALLATION")

    def numbered(text: str, ilvl: int):
        paragraph = document.add_paragraph(text)
        p_pr = paragraph._p.get_or_add_pPr()
        num_pr = OxmlElement("w:numPr")
        ilvl_el = OxmlElement("w:ilvl")
        ilvl_el.set(qn("w:val"), str(ilvl))
        num_id = OxmlElement("w:numId")
        num_id.set(qn("w:val"), "1")
        num_pr.append(ilvl_el)
        num_pr.append(num_id)
        p_pr.append(num_pr)

    numbered("Install per the working plans.", 0)
    numbered("Support piping per NFPA 13.", 1)
    path = tmp_path / "autonum.docx"
    document.save(str(path))

    result = parse_master_docx(path)
    article = result.section.parts[2].articles[0]
    top = article.paragraphs[0]
    assert top.text == "Install per the working plans."
    assert top.children[0].text == "Support piping per NFPA 13."


def test_unreadable_and_empty_files_error(tmp_path):
    bogus = tmp_path / "not_a_docx.docx"
    bogus.write_bytes(b"this is not a zip")
    with pytest.raises(MasterImportError, match="not a readable"):
        parse_master_docx(bogus)

    empty = _write_docx(tmp_path, [], name="empty.docx")
    with pytest.raises(MasterImportError, match="No importable content"):
        parse_master_docx(empty)


def test_import_result_is_integrity_clean(tmp_path):
    result = parse_master_docx(_write_docx(tmp_path, _MASTER_LINES))
    # from_dict runs the full id/seq integrity check — must not raise.
    SpecSection.from_dict(result.section.to_dict())
    assert isinstance(result, ImportResult)


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------


def test_import_endpoint_gates_and_gap_adapt_context(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from backend import sessions
    from backend.app import create_app
    from tests.fakes import FakeClient, text_turn

    client = TestClient(create_app())
    path = _write_docx(tmp_path, _MASTER_LINES)

    # Wrong extension refused.
    resp = client.post(
        "/api/import/master",
        files={"file": ("master.txt", b"nope", "text/plain")},
    )
    assert resp.status_code == 400

    with open(path, "rb") as fh:
        resp = client.post(
            "/api/import/master",
            files={
                "file": (
                    "master.docx",
                    fh.read(),
                    "application/vnd.openxmlformats-officedocument"
                    ".wordprocessingml.document",
                )
            },
        )
    data = resp.json()
    assert data["ok"] is True
    assert data["imported_block_count"] == 8
    assert data["doc"]["section"]["number"] == "21 13 13"
    assert data["doc"]["version"] == {"index": 1, "count": 2}
    statuses = {
        p["status"]
        for part in data["doc"]["parts"]
        for a in part["articles"]
        for p in a["paragraphs"]
    }
    assert statuses == {"imported"}

    # Non-empty doc → 409 (import is a starting point).
    with open(path, "rb") as fh:
        resp = client.post(
            "/api/import/master",
            files={"file": ("master.docx", fh.read(), "application/zip")},
        )
    assert resp.status_code == 409

    # The next turn's outline shows imported statuses (gap-and-adapt fuel)
    # and the stable prompt carries the policy.
    fake = FakeClient([text_turn(["Walking the master."])])
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    client.post("/api/chat", json={"message": "adapt it"})
    request = fake.messages.last_request
    assert "(imported)" in request["system"][1]["text"]
    assert "Gap-and-adapt" in request["system"][0]["text"]

    # Undo steps back to the blank page.
    undone = client.post("/api/doc/undo").json()
    assert undone["doc"]["section"]["number"] == ""
