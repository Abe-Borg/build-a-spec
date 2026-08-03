"""Focused regressions for XML-illegal text in generated DOCX artifacts."""
from __future__ import annotations

import io
import zipfile

from docx import Document
from lxml import etree

from backend.spec_doc.diffing import diff_sections
from backend.spec_doc.docx_export import (
    build_docx,
    build_qc_memo,
    export_filename,
    redline_filename,
)
from backend.spec_doc.model import Article, Paragraph, SpecSection
from backend.spec_doc.xml_text import xml_safe_title, xml_safe_upper


def _assert_all_package_xml_is_parseable(payload: bytes) -> dict[str, bytes]:
    parts: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for name in archive.namelist():
            if name.endswith((".xml", ".rels")):
                content = archive.read(name)
                etree.fromstring(content)
                parts[name] = content
    return parts


def _hostile_section() -> SpecSection:
    section = SpecSection.empty()
    section.number = "01\x00 00"
    section.title = "CONTROL\x0bCHARACTERS"
    section.parts[0].title = "PART\ud800 1 - GENERAL"
    section.parts[0].articles = [
        Article(
            uid="pt1.a1",
            title="SUMMARY\x01BAD\ufffe",
            paragraphs=[
                Paragraph(
                    uid="pt1.a1.p1",
                    text="Preserve the source marker \x01 visibly.",
                    status="assumed",
                )
            ],
        )
    ]
    return section


def test_clean_and_audit_closing_escape_illegal_xml_without_mutating_inputs() -> None:
    section = _hostile_section()
    audit = {
        "audited_at": "2026-08-03\x02",
        "summary": "Audit summary\x03",
        "coverage": [
            {
                "status": "covered\x04bad",
                "requirement_id": "REQ\x05",
                "note": "Evidence\x06",
            }
        ],
        "findings": [
            {
                "severity": "high\x07",
                "issue": "Finding\x08",
                "suggestion": "Correct\x0cthis",
            }
        ],
    }

    payload = build_docx(section, audit_result=audit)
    parts = _assert_all_package_xml_is_parseable(payload)
    document_xml = parts["word/document.xml"]

    for escaped in (
        rb"\u0000",
        rb"\u000B",
        rb"\uD800",
        rb"\uFFFE",
        rb"\u0001",
        rb"\u0002",
        rb"\u0008",
        rb"\u000C",
    ):
        assert escaped in document_xml
    assert rb"SUMMARY\u0001BAD\uFFFE" in document_xml
    assert rb"COVERED\u0004BAD" in document_xml

    # Export is a projection only: the live document and saved audit retain
    # their exact original values for diagnostics and subsequent edits.
    assert section.title == "CONTROL\x0bCHARACTERS"
    assert section.parts[0].title == "PART\ud800 1 - GENERAL"
    assert audit["summary"] == "Audit summary\x03"


def test_case_transforms_preserve_short_and_long_visible_escape_tokens() -> None:
    assert xml_safe_upper("alpha\x01bad") == r"ALPHA\u0001BAD"
    assert xml_safe_title("alpha\x01bad") == r"Alpha\u0001Bad"
    assert (
        xml_safe_title(r"alpha\U000ABCDEomega")
        == r"Alpha\U000ABCDEOmega"
    )


def test_suggested_export_filenames_never_carry_raw_control_characters() -> None:
    section = _hostile_section()
    section.title += "\tTAB"

    clean = export_filename(section)
    redline = redline_filename(section)

    assert "01u0000 00" in clean
    assert "CONTROLu000BCHARACTERS" in clean
    assert "u0009TAB" in clean
    assert redline == clean.removesuffix(".docx") + " - REDLINE.docx"
    assert all(ord(char) >= 0x20 for char in clean)
    assert all(ord(char) >= 0x20 for char in redline)


def test_redline_escapes_illegal_text_and_revision_attributes() -> None:
    base = SpecSection.empty()
    base.title = "BASE\x01TITLE"
    current = SpecSection.empty()
    current.title = "CURRENT\x02TITLE"

    payload = build_docx(
        current,
        redline=diff_sections(base, current),
        redline_date="2026-08-03T12:00:00\x03Z",
    )
    document_xml = _assert_all_package_xml_is_parseable(payload)[
        "word/document.xml"
    ]

    assert rb"BASE\u0001TITLE" in document_xml
    assert rb"CURRENT\u0002TITLE" in document_xml
    assert rb"2026-08-03T12:00:00\u0003Z" in document_xml
    assert base.title == "BASE\x01TITLE"
    assert current.title == "CURRENT\x02TITLE"


def test_qc_memo_escapes_body_hyperlink_and_core_property_text() -> None:
    section = _hostile_section()
    qc_result = {
        "run_id": "run\x00id",
        "summary": "Model summary\x0b",
        "input_manifest": {
            "source_urls": ["https://example.test/evidence\x01record"]
        },
    }

    payload = build_qc_memo(qc_result, section, stale=False)
    parts = _assert_all_package_xml_is_parseable(payload)
    combined_xml = b"\n".join(parts.values())

    assert rb"run\u0000id" in combined_xml
    assert rb"Model summary\u000B" in combined_xml
    assert rb"evidence\u0001record" in combined_xml
    assert rb"CONTROL\u000BCHARACTERS" in combined_xml

    document = Document(io.BytesIO(payload))
    assert "CONTROL\\u000BCHARACTERS" in document.core_properties.title
    assert "run\\u0000id" in document.core_properties.subject
    assert qc_result["run_id"] == "run\x00id"
    assert section.title == "CONTROL\x0bCHARACTERS"
