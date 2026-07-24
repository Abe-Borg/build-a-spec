"""Acceptance tests for genuine numbering in normalized/fresh DOCX exports.

Chunk 6 deliberately changes only the clean semantic renderer.  A normalized
document must use one deterministic four-level ``w:abstractNum`` shared by
article-local ``w:num`` instances, while source-preserving export remains an
exact clone/patch path and semantic redline keeps its independently verified
literal-label representation for now.
"""
from __future__ import annotations

from copy import deepcopy
import io
import zipfile

from fastapi.testclient import TestClient
from lxml import etree

from backend.app import create_app
from backend.spec_doc.diffing import diff_sections
from backend.spec_doc.docx_export import build_docx
from backend.spec_doc.importer import parse_master_docx
from backend.spec_doc.model import Article, Paragraph, SpecSection, iter_paragraphs
from tests.docx_fidelity_helpers import (
    DOCX_MEDIA_TYPE,
    TARGET_MODEL_TEXT,
    make_fidelity_master,
)


_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS = {"w": _W_NS}
_W_VAL = f"{{{_W_NS}}}val"
_W_ILVL = f"{{{_W_NS}}}ilvl"
_W_NUM_ID = f"{{{_W_NS}}}numId"
_W_ABSTRACT_NUM_ID = f"{{{_W_NS}}}abstractNumId"
_REDLINE_DATE = "2026-07-23T12:00:00Z"
_ABSTRACT_NAME = "Build-a-Spec SectionFormat Provisions"
_INDENT_DXA = 648
_ARTICLE_COUNT = 3


_ARTICLE_ONE = (
    ("Article one top alpha", 0),
    ("Article one alpha child one", 1),
    ("Article one child-one grandchild one", 2),
    ("Article one fourth-level one", 3),
    ("Article one fourth-level two", 3),
    ("Article one child-one grandchild two", 2),
    ("Article one alpha child two", 1),
    ("Article one child-two grandchild one", 2),
    ("Article one top beta", 0),
    ("Article one beta child one", 1),
    ("Article one beta grandchild one", 2),
)
_ARTICLE_TWO = (
    ("Article two top alpha", 0),
    ("Article two top beta", 0),
)
_ALL_PROVISIONS = _ARTICLE_ONE + _ARTICLE_TWO


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


def _numbered_section() -> SpecSection:
    """Two populated articles plus one empty article restart boundary."""
    section = SpecSection.empty()
    section.number = "21 13 13"
    section.title = "NORMALIZED NUMBERING FIXTURE"
    section.parts[0].articles = [
        Article(
            uid="pt1.a1",
            title="FIRST ARTICLE",
            paragraphs=[
                _paragraph(
                    "pt1.a1.p1",
                    "Article one top alpha",
                    _paragraph(
                        "pt1.a1.p1.p1",
                        "Article one alpha child one",
                        _paragraph(
                            "pt1.a1.p1.p1.p1",
                            "Article one child-one grandchild one",
                            _paragraph(
                                "pt1.a1.p1.p1.p1.p1",
                                "Article one fourth-level one",
                            ),
                            _paragraph(
                                "pt1.a1.p1.p1.p1.p2",
                                "Article one fourth-level two",
                            ),
                        ),
                        _paragraph(
                            "pt1.a1.p1.p1.p2",
                            "Article one child-one grandchild two",
                        ),
                    ),
                    _paragraph(
                        "pt1.a1.p1.p2",
                        "Article one alpha child two",
                        _paragraph(
                            "pt1.a1.p1.p2.p1",
                            "Article one child-two grandchild one",
                        ),
                    ),
                ),
                _paragraph(
                    "pt1.a1.p2",
                    "Article one top beta",
                    _paragraph(
                        "pt1.a1.p2.p1",
                        "Article one beta child one",
                        _paragraph(
                            "pt1.a1.p2.p1.p1",
                            "Article one beta grandchild one",
                        ),
                    ),
                ),
            ],
            next_seq=3,
        ),
        Article(
            uid="pt1.a2",
            title="SECOND ARTICLE",
            paragraphs=[
                _paragraph("pt1.a2.p1", "Article two top alpha"),
                _paragraph("pt1.a2.p2", "Article two top beta"),
            ],
            next_seq=3,
        ),
        Article(
            uid="pt1.a3",
            title="EMPTY ARTICLE",
            paragraphs=[],
            next_seq=1,
        ),
    ]
    section.parts[0].next_seq = 4
    return section


def _xml_part(payload: bytes, name: str):
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return etree.fromstring(archive.read(name))


def _member_contents(payload: bytes) -> dict[str, bytes]:
    """Compare deterministic content without python-docx ZIP timestamps."""
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return {name: archive.read(name) for name in sorted(archive.namelist())}


def _paragraph_text(paragraph) -> str:
    return "".join(paragraph.xpath(".//w:t/text()", namespaces=_NS))


def _provision_paragraph(document_root, text: str):
    matches = [
        paragraph
        for paragraph in document_root.xpath("./w:body/w:p", namespaces=_NS)
        if text in _paragraph_text(paragraph)
    ]
    assert len(matches) == 1, (
        f"expected one direct body paragraph containing provision {text!r}; "
        f"found {len(matches)}"
    )
    return matches[0]


def _direct_numbering_ref(paragraph, text: str) -> tuple[str, int]:
    num_pr = paragraph.find("w:pPr/w:numPr", namespaces=_NS)
    assert num_pr is not None, (
        f"provision {text!r} must carry direct w:pPr/w:numPr numbering"
    )
    ilvl = num_pr.find("w:ilvl", namespaces=_NS)
    num_id = num_pr.find("w:numId", namespaces=_NS)
    assert ilvl is not None, f"provision {text!r} is missing direct w:ilvl"
    assert num_id is not None, f"provision {text!r} is missing direct w:numId"
    return num_id.get(_W_VAL), int(ilvl.get(_W_VAL))


def _provision_refs(payload: bytes) -> dict[str, tuple[str, int]]:
    document_root = _xml_part(payload, "word/document.xml")
    return {
        text: _direct_numbering_ref(
            _provision_paragraph(document_root, text),
            text,
        )
        for text, _depth in _ALL_PROVISIONS
    }


def _shared_abstract(payload: bytes, used_num_ids: set[str]):
    numbering_root = _xml_part(payload, "word/numbering.xml")
    abstract_ids: set[str] = set()
    for num_id in used_num_ids:
        matches = numbering_root.xpath(
            "./w:num[@w:numId=$num_id]",
            namespaces=_NS,
            num_id=num_id,
        )
        assert len(matches) == 1, f"expected one w:num instance for numId {num_id}"
        reference = matches[0].find("w:abstractNumId", namespaces=_NS)
        assert reference is not None
        abstract_ids.add(reference.get(_W_VAL))
    assert len(abstract_ids) == 1, (
        "all article numbering instances must share one SectionFormat "
        f"abstractNum; found {sorted(abstract_ids)}"
    )
    abstract_id = next(iter(abstract_ids))
    abstracts = numbering_root.xpath(
        "./w:abstractNum[@w:abstractNumId=$abstract_id]",
        namespaces=_NS,
        abstract_id=abstract_id,
    )
    assert len(abstracts) == 1
    return numbering_root, abstracts[0]


def test_normalized_export_has_one_shared_four_level_definition():
    payload = build_docx(_numbered_section())
    refs = _provision_refs(payload)
    numbering_root, abstract = _shared_abstract(
        payload,
        {num_id for num_id, _depth in refs.values()},
    )

    named_abstracts = numbering_root.xpath(
        "./w:abstractNum[w:name/@w:val=$name]",
        namespaces=_NS,
        name=_ABSTRACT_NAME,
    )
    assert len(named_abstracts) == 1, (
        "normalized export must contain exactly one named custom "
        "SectionFormat abstractNum"
    )
    assert named_abstracts[0].get(_W_ABSTRACT_NUM_ID) == abstract.get(
        _W_ABSTRACT_NUM_ID
    )
    first_num = numbering_root.find("w:num", namespaces=_NS)
    assert first_num is not None
    assert numbering_root.index(abstract) < numbering_root.index(first_num), (
        "w:abstractNum must precede every w:num in numbering.xml schema order"
    )
    multi_level_type = abstract.find("w:multiLevelType", namespaces=_NS)
    assert multi_level_type is not None
    assert multi_level_type.get(_W_VAL) == "multilevel"

    expected = {
        0: ("upperLetter", "%1."),
        1: ("decimal", "%2."),
        2: ("lowerLetter", "%3."),
        3: ("decimal", "%4)"),
    }
    levels = {
        int(level.get(_W_ILVL)): level
        for level in abstract.findall("w:lvl", namespaces=_NS)
    }
    assert set(levels) == set(expected)
    for depth, (number_format, level_text) in expected.items():
        level = levels[depth]
        start = level.find("w:start", namespaces=_NS)
        fmt = level.find("w:numFmt", namespaces=_NS)
        text = level.find("w:lvlText", namespaces=_NS)
        assert start is not None and start.get(_W_VAL) == "1"
        assert fmt is not None and fmt.get(_W_VAL) == number_format
        assert text is not None and text.get(_W_VAL) == level_text


def test_each_article_has_one_numbering_instance_and_restarts_at_a():
    payload = build_docx(_numbered_section())
    refs = _provision_refs(payload)
    first_ids = {refs[text][0] for text, _depth in _ARTICLE_ONE}
    second_ids = {refs[text][0] for text, _depth in _ARTICLE_TWO}

    assert len(first_ids) == 1, "the first article must use exactly one w:num"
    assert len(second_ids) == 1, "the second article must use exactly one w:num"
    assert first_ids.isdisjoint(second_ids), (
        "each article needs a distinct w:num so top-level lettering restarts at A."
    )

    numbering_root, abstract = _shared_abstract(payload, first_ids | second_ids)
    level_zero = abstract.xpath("./w:lvl[@w:ilvl='0']", namespaces=_NS)
    assert len(level_zero) == 1
    assert level_zero[0].find("w:start", namespaces=_NS).get(_W_VAL) == "1"
    for num_id in first_ids | second_ids:
        instance = numbering_root.xpath(
            "./w:num[@w:numId=$num_id]",
            namespaces=_NS,
            num_id=num_id,
        )[0]
        overrides = instance.xpath(
            "./w:lvlOverride[@w:ilvl='0']/w:startOverride",
            namespaces=_NS,
        )
        assert not overrides or all(item.get(_W_VAL) == "1" for item in overrides)

    # The third fixture article is intentionally empty.  The exact contract
    # is one deterministic instance per article, not merely one per article
    # that happens to contain provisions.
    abstract_id = abstract.get(_W_ABSTRACT_NUM_ID)
    article_instances = numbering_root.xpath(
        "./w:num[w:abstractNumId/@w:val=$abstract_id]",
        namespaces=_NS,
        abstract_id=abstract_id,
    )
    article_num_ids = {item.get(_W_NUM_ID) for item in article_instances}
    used_num_ids = first_ids | second_ids
    assert len(article_instances) == _ARTICLE_COUNT
    for instance in article_instances:
        overrides = instance.xpath(
            "./w:lvlOverride[@w:ilvl='0']/w:startOverride",
            namespaces=_NS,
        )
        assert len(overrides) == 1
        assert overrides[0].get(_W_VAL) == "1"
    assert used_num_ids < article_num_ids
    assert len(article_num_ids - used_num_ids) == 1, (
        "the empty article must still mint exactly one otherwise-unused w:num"
    )


def test_every_provision_has_direct_numpr_with_tree_depth_as_ilvl():
    payload = build_docx(_numbered_section())
    refs = _provision_refs(payload)
    assert {text: ilvl for text, (_num_id, ilvl) in refs.items()} == dict(
        _ALL_PROVISIONS
    )


def test_provision_runs_contain_text_only_without_literal_labels_or_tabs():
    payload = build_docx(_numbered_section())
    document_root = _xml_part(payload, "word/document.xml")

    for text, _depth in _ALL_PROVISIONS:
        paragraph = _provision_paragraph(document_root, text)
        assert _paragraph_text(paragraph) == text, (
            f"the w:t content for {text!r} must not duplicate its Word number"
        )
        assert paragraph.xpath(".//w:tab", namespaces=_NS) == [], (
            f"provision {text!r} must not retain the old literal-label tab"
        )


def test_child_levels_restart_after_their_immediate_parent():
    payload = build_docx(_numbered_section())
    refs = _provision_refs(payload)
    _numbering_root, abstract = _shared_abstract(
        payload,
        {num_id for num_id, _depth in refs.values()},
    )

    # Explicit lvlRestart is one-based, so ilvl N names N to restart after
    # its immediate parent.  The deterministic definition also owns the tab
    # suffix and the legacy 0.45-inch hanging-indent geometry at every level.
    level_zero = abstract.xpath("./w:lvl[@w:ilvl='0']", namespaces=_NS)
    assert len(level_zero) == 1
    assert level_zero[0].find("w:lvlRestart", namespaces=_NS) is None
    for depth in (1, 2, 3):
        matches = abstract.xpath(
            "./w:lvl[@w:ilvl=$ilvl]",
            namespaces=_NS,
            ilvl=str(depth),
        )
        assert len(matches) == 1
        restart = matches[0].find("w:lvlRestart", namespaces=_NS)
        assert restart is not None and restart.get(_W_VAL) == str(depth)

    for depth in range(4):
        level = abstract.xpath(
            "./w:lvl[@w:ilvl=$ilvl]",
            namespaces=_NS,
            ilvl=str(depth),
        )[0]
        suffix = level.find("w:suff", namespaces=_NS)
        indent = level.find("w:pPr/w:ind", namespaces=_NS)
        tabs = level.findall("w:pPr/w:tabs/w:tab", namespaces=_NS)
        expected_left = str(_INDENT_DXA * (depth + 1))
        assert suffix is not None and suffix.get(_W_VAL) == "tab"
        assert indent is not None
        assert indent.get(f"{{{_W_NS}}}left") == expected_left
        assert indent.get(f"{{{_W_NS}}}hanging") == str(_INDENT_DXA)
        assert len(tabs) == 1
        assert tabs[0].get(_W_VAL) == "num"
        assert tabs[0].get(f"{{{_W_NS}}}pos") == expected_left

    # Keep the restart boundaries in the fixture live: a second child under
    # A., then a first child under B., then a new article are all present in
    # document order with the intended levels.
    document_root = _xml_part(payload, "word/document.xml")
    actual = []
    for paragraph in document_root.xpath("./w:body/w:p", namespaces=_NS):
        visible = _paragraph_text(paragraph)
        for text, _depth in _ALL_PROVISIONS:
            if text in visible:
                actual.append((text, _direct_numbering_ref(paragraph, text)[1]))
                break
    assert actual == list(_ALL_PROVISIONS)


def test_repeated_normalized_export_has_deterministic_member_contents():
    section = _numbered_section()
    first = build_docx(section)
    second = build_docx(section)

    assert _member_contents(first) == _member_contents(second)
    assert _xml_part(first, "word/numbering.xml") is not None
    assert _xml_part(first, "word/document.xml") is not None


def test_normalized_export_reimports_with_identical_provision_text_and_depth(
    tmp_path,
):
    section = _numbered_section()
    path = tmp_path / "normalized-numbering.docx"
    path.write_bytes(build_docx(section))

    reimported = parse_master_docx(path).section
    expected = [
        (part.number, article.title, paragraph.text, depth)
        for part, article, paragraph, depth, _ref in iter_paragraphs(section)
    ]
    actual = [
        (part.number, article.title, paragraph.text, depth)
        for part, article, paragraph, depth, _ref in iter_paragraphs(reimported)
    ]
    assert reimported.number == section.number
    assert reimported.title == section.title
    assert [
        [article.title for article in part.articles]
        for part in reimported.parts
    ] == [
        [article.title for article in part.articles]
        for part in section.parts
    ]
    assert actual == expected


def test_direct_numbering_preserves_structure_like_provision_text_on_reimport(
    tmp_path,
):
    section = _numbered_section()
    article = section.parts[0].articles[1]
    article.paragraphs = [
        _paragraph("pt1.a2.p1", "END OF SECTION is semantic provision text."),
        _paragraph("pt1.a2.p2", "PART 2 - PRODUCTS is semantic provision text."),
        _paragraph("pt1.a2.p3", "1.9 NOT AN ARTICLE HEADING"),
        _paragraph("pt1.a2.p4", "A. Literal-looking text remains intact."),
    ]
    article.next_seq = 5
    expected = [
        (paragraph.text, depth)
        for _part, owner, paragraph, depth, _ref in iter_paragraphs(section)
        if owner.uid == article.uid
    ]

    path = tmp_path / "normalized-structure-like-text.docx"
    path.write_bytes(build_docx(section))
    reimported = parse_master_docx(path).section
    actual = [
        (paragraph.text, depth)
        for _part, owner, paragraph, depth, _ref in iter_paragraphs(reimported)
        if owner.title == article.title
    ]

    assert actual == expected


def test_source_mode_stays_byte_exact_when_normalized_numbering_is_requested(
    tmp_path,
):
    source = make_fidelity_master(tmp_path)
    client = TestClient(create_app())
    imported = client.post(
        "/api/import/master",
        files={"file": ("master.docx", source, DOCX_MEDIA_TYPE)},
    )
    assert imported.status_code == 200, imported.text

    before = client.get("/api/export/docx", params={"mode": "source"})
    normalized = client.get("/api/export/docx", params={"mode": "normalized"})
    after = client.get("/api/export/docx", params={"mode": "source"})
    assert before.status_code == normalized.status_code == after.status_code == 200
    assert before.content == source
    assert after.content == source

    normalized_root = _xml_part(normalized.content, "word/document.xml")
    paragraph = _provision_paragraph(normalized_root, TARGET_MODEL_TEXT)
    _direct_numbering_ref(paragraph, TARGET_MODEL_TEXT)


def test_semantic_redline_remains_on_its_separate_literal_label_path():
    base = _numbered_section()
    current = deepcopy(base)
    current.parts[0].articles[0].paragraphs[0].text = (
        "Article one top alpha revised"
    )
    payload = build_docx(
        current,
        redline=diff_sections(base, current),
        redline_date=_REDLINE_DATE,
    )
    document_root = _xml_part(payload, "word/document.xml")

    # Chunk 6 does not silently reinterpret tracked numbering.  Until that
    # path is independently verified, an unchanged redline provision keeps
    # the established literal label + tab and carries no clean-body numPr.
    text = "Article one top beta"
    paragraph = _provision_paragraph(document_root, text)
    assert paragraph.find("w:pPr/w:numPr", namespaces=_NS) is None
    assert paragraph.xpath(".//w:t/text()", namespaces=_NS)[0] == "B."
    assert paragraph.xpath(".//w:tab", namespaces=_NS)
    assert document_root.xpath(".//w:ins", namespaces=_NS)
    assert document_root.xpath(".//w:del", namespaces=_NS)
