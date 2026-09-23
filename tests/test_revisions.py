"""The redline's oracles: Accept All, Reject All and the canonical comparison
(Redline on your original, D-7), pinned on hand-built revision XML.

Every redline test leans on these, and the export's self-check runs them on
every file it hands over — so they are pinned directly, one Word revision
shape at a time, before anything is built on top of them.
"""
from __future__ import annotations

import pytest
from lxml import etree

from backend.spec_doc.revisions import (
    MOVE_PROBLEMS,
    accept_all,
    canonical_body,
    duplicate_bookmark_names,
    first_difference,
    has_revisions,
    move_range_problem,
    reject_all,
)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
REV = 'w:id="{id}" w:author="Build-a-Spec" w:date="2026-09-22T00:00:00Z"'


def _body(inner: str):
    return etree.fromstring(
        f'<w:body xmlns:w="{W}" xmlns:w14="{W14}">{inner}</w:body>'
    )


def _rev(n: int) -> str:
    return REV.format(id=n)


def _texts(body) -> list[str]:
    """One string per body paragraph: its visible (w:t) text."""
    out = []
    for child in body:
        if child.tag == f"{{{W}}}p":
            out.append("".join(t.text or "" for t in child.iter(f"{{{W}}}t")))
        elif isinstance(child.tag, str):
            out.append(etree.QName(child).localname)
    return out


def _equal(left, right, **kwargs) -> bool:
    return first_difference(left, right, **kwargs) is None


# ---------------------------------------------------------------------------
# Run-level insertions and deletions
# ---------------------------------------------------------------------------


def test_an_inserted_run_is_kept_by_accept_and_gone_on_reject():
    body = _body(
        "<w:p><w:r><w:t xml:space='preserve'>Provide </w:t></w:r>"
        f"<w:ins {_rev(1)}><w:r><w:t xml:space='preserve'>spring </w:t></w:r></w:ins>"
        "<w:r><w:t>isolators.</w:t></w:r></w:p>"
    )
    assert _texts(accept_all(body)) == ["Provide spring isolators."]
    assert _texts(reject_all(body)) == ["Provide isolators."]
    assert not has_revisions(accept_all(body))
    assert not has_revisions(reject_all(body))


def test_a_deleted_run_comes_back_as_ordinary_text_on_reject():
    body = _body(
        "<w:p><w:r><w:t xml:space='preserve'>Provide </w:t></w:r>"
        f"<w:del {_rev(1)}><w:r><w:delText xml:space='preserve'>spring </w:delText>"
        "</w:r></w:del><w:r><w:t>isolators.</w:t></w:r></w:p>"
    )
    assert _texts(accept_all(body)) == ["Provide isolators."]
    rejected = reject_all(body)
    assert _texts(rejected) == ["Provide spring isolators."]
    assert rejected.find(f".//{{{W}}}delText") is None


def test_a_deleted_field_instruction_comes_back_as_an_instruction():
    body = _body(
        f"<w:p><w:del {_rev(1)}><w:r><w:fldChar w:fldCharType='begin'/></w:r>"
        "<w:r><w:delInstrText> PAGE </w:delInstrText></w:r>"
        "<w:r><w:fldChar w:fldCharType='end'/></w:r></w:del></w:p>"
    )
    rejected = reject_all(body)
    assert rejected.find(f".//{{{W}}}instrText").text == " PAGE "
    assert rejected.find(f".//{{{W}}}delInstrText") is None


def test_the_original_body_is_never_modified():
    body = _body(f"<w:p><w:ins {_rev(1)}><w:r><w:t>x</w:t></w:r></w:ins></w:p>")
    before = etree.tostring(body)
    accept_all(body)
    reject_all(body)
    assert etree.tostring(body) == before


def test_a_deletion_inside_a_hyperlink_resolves_where_it_sits():
    """A hyperlink cannot sit inside w:del, so Word marks the runs inside
    it; an emptied hyperlink shows nothing and compares as absent."""
    body = _body(
        "<w:p><w:hyperlink w:anchor='x'>"
        f"<w:del {_rev(1)}><w:r><w:delText>link</w:delText></w:r></w:del>"
        "</w:hyperlink><w:r><w:t>after</w:t></w:r></w:p>"
    )
    accepted = accept_all(body)
    assert _equal(accepted, _body("<w:p><w:r><w:t>after</w:t></w:r></w:p>"))
    rejected = reject_all(body)
    assert "".join(t.text for t in rejected.iter(f"{{{W}}}t")) == "linkafter"


def test_a_text_box_inside_a_deleted_run_keeps_its_own_text_nodes():
    """A text box is its own story: deleting the run that anchors it does
    not mark the box's text, so Reject All must not touch it either."""
    body = _body(
        f"<w:p><w:del {_rev(1)}><w:r><w:delText>A</w:delText>"
        "<w:drawing><w:txbxContent><w:p><w:r><w:t>box</w:t></w:r></w:p>"
        "</w:txbxContent></w:drawing></w:r></w:del></w:p>"
    )
    rejected = reject_all(body)
    assert [t.text for t in rejected.iter(f"{{{W}}}t")] == ["A", "box"]
    assert rejected.find(f".//{{{W}}}delText") is None


def test_move_wrappers_resolve_like_insertions_and_deletions():
    body = _body(
        f"<w:p><w:moveFromRangeStart w:id='9' w:name='m'/>"
        f"<w:moveFrom {_rev(1)}><w:r><w:t>old</w:t></w:r></w:moveFrom>"
        "<w:moveFromRangeEnd w:id='9'/>"
        f"<w:moveTo {_rev(2)}><w:r><w:t>new</w:t></w:r></w:moveTo></w:p>"
    )
    assert _texts(accept_all(body)) == ["new"]
    assert _texts(reject_all(body)) == ["old"]
    assert not has_revisions(accept_all(body))


def _moved_paragraph(side: str, text: str, *, flag: int, wrapper: int, start: str = "") -> str:
    """One paragraph of a native move: its mark flagged ``w:<side>``, its
    run in a ``w:<side>`` wrapper and — optionally — its range's start right
    after its properties (where Word writes it)."""
    return (
        f"<w:p><w:pPr><w:rPr><w:{side} {_rev(flag)}/></w:rPr></w:pPr>{start}"
        f"<w:{side} {_rev(wrapper)}><w:r><w:t>{text}</w:t></w:r></w:{side}></w:p>"
    )


def _range(side: str, edge: str, identifier: int, name: str = "move1") -> str:
    if edge == "Start":
        return f"<w:{side}RangeStart {_rev(identifier)} w:name='{name}'/>"
    return f"<w:{side}RangeEnd w:id='{identifier}'/>"


def _word_move(first: str = "B. Moved.", *, stays: str = "A. Stays.") -> str:
    """Word's own shape for a whole paragraph moved below another (the
    Open-XML-PowerTools RP015 sample, which Word wrote): each range opens
    inside the moved paragraph and closes BETWEEN paragraphs, after it, so
    the paragraph's mark is inside the range."""
    return (
        _moved_paragraph("moveFrom", first, flag=1, wrapper=2, start=_range("moveFrom", "Start", 3))
        + _range("moveFrom", "End", 3)
        + f"<w:p><w:r><w:t>{stays}</w:t></w:r></w:p>"
        + _moved_paragraph("moveTo", first, flag=4, wrapper=5, start=_range("moveTo", "Start", 6))
        + _range("moveTo", "End", 6)
    )


def test_a_moved_paragraph_resolves_to_one_copy_either_way():
    """Phase 2 (PR B): a whole paragraph moved. Accept All keeps the copy
    where it was moved to — the moved-away copy goes, mark and all, and
    leaves no empty paragraph behind — and Reject All keeps the one where it
    was. Neither leaves a range marker or a flag."""
    body = _body(_word_move() + "<w:p><w:r><w:t>C. Last.</w:t></w:r></w:p>")
    accepted, rejected = accept_all(body), reject_all(body)
    assert _texts(accepted) == ["A. Stays.", "B. Moved.", "C. Last."]
    assert _texts(rejected) == ["B. Moved.", "A. Stays.", "C. Last."]
    for resolved in (accepted, rejected):
        assert not has_revisions(resolved)  # the flags and markers went too
    assert move_range_problem(body) is None


def test_one_named_range_may_span_several_moved_paragraphs():
    """A provision and its two children, moved as one: one name, one range
    each side, opened in the first paragraph and closed after the last."""
    moved_from = (
        _moved_paragraph("moveFrom", "A. Parent.", flag=1, wrapper=2, start=_range("moveFrom", "Start", 20))
        + _moved_paragraph("moveFrom", "1. Child one.", flag=3, wrapper=4)
        + _moved_paragraph("moveFrom", "2. Child two.", flag=5, wrapper=6)
        + _range("moveFrom", "End", 20)
    )
    moved_to = (
        _moved_paragraph("moveTo", "A. Parent.", flag=7, wrapper=8, start=_range("moveTo", "Start", 21))
        + _moved_paragraph("moveTo", "1. Child one.", flag=9, wrapper=10)
        + _moved_paragraph("moveTo", "2. Child two.", flag=11, wrapper=12)
        + _range("moveTo", "End", 21)
    )
    stay = "<w:p><w:r><w:t>B. Stays.</w:t></w:r></w:p>"
    body = _body(moved_from + stay + moved_to + "<w:p><w:r><w:t>End.</w:t></w:r></w:p>")
    assert _texts(accept_all(body)) == [
        "B. Stays.",
        "A. Parent.",
        "1. Child one.",
        "2. Child two.",
        "End.",
    ]
    assert _texts(reject_all(body)) == [
        "A. Parent.",
        "1. Child one.",
        "2. Child two.",
        "B. Stays.",
        "End.",
    ]
    assert move_range_problem(body) is None


def test_deleted_text_inside_a_move_from_comes_back_on_reject():
    """The schema lets a ``w:moveFrom`` run hold ``w:delText``
    (``CT_RunTrackChange`` takes any run content), though ECMA-376 §17.3.3.7
    reserves it for ``w:del``, Word never writes it, and LibreOffice's
    tdf#165933 fix calls it invalid; the writer writes ``w:t``. The oracle
    reads it either way: Reject All restores ordinary text and Accept All
    removes it."""
    body = _body(
        f"<w:p>{_range('moveFrom', 'Start', 9)}"
        f"<w:moveFrom {_rev(1)}><w:r><w:delText>old</w:delText></w:r></w:moveFrom>"
        f"{_range('moveFrom', 'End', 9)}"
        f"<w:r><w:t> kept</w:t></w:r></w:p>"
    )
    assert _texts(accept_all(body)) == [" kept"]
    rejected = reject_all(body)
    assert _texts(rejected) == ["old kept"]
    assert rejected.find(f".//{{{W}}}delText") is None


@pytest.mark.parametrize(
    ("broken", "problem"),
    [
        # Two revisions sharing an id.
        (lambda xml: xml.replace('w:id="5"', 'w:id="2"'), "duplicate_id"),
        # A range start without a name.
        (lambda xml: xml.replace("w:name='move1'", "", 1), "unnamed_range"),
        # A name carried by a moved-from range and no moved-to one.
        (lambda xml: xml.replace("w:name='move1'", "w:name='other'", 1), "unpaired_name"),
        # A range never closed.
        (lambda xml: xml.replace("<w:moveToRangeEnd w:id='6'/>", ""), "unclosed_range"),
        # An end with no start of its kind.
        (
            lambda xml: xml.replace(
                "<w:moveToRangeEnd w:id='6'/>", "<w:moveFromRangeEnd w:id='6'/>"
            ),
            "stray_range_end",
        ),
    ],
    ids=["duplicate-id", "unnamed", "unpaired", "unclosed", "stray-end"],
)
def test_the_move_check_names_a_broken_range(broken, problem):
    assert move_range_problem(_body(_word_move())) is None
    assert move_range_problem(_body(broken(_word_move()))) == problem
    assert problem in MOVE_PROBLEMS


def test_a_range_closed_inside_its_paragraph_leaves_the_mark_outside():
    """A paragraph's mark is its END, so a range that closes inside the
    paragraph — after its words, before its mark — moves the words and not
    the mark: non-conformant (ECMA-376 §17.13.5.21). Word closes a
    whole-paragraph move between paragraphs."""
    closed_inside = _word_move().replace(
        "</w:moveFrom></w:p><w:moveFromRangeEnd w:id='3'/>",
        "</w:moveFrom><w:moveFromRangeEnd w:id='3'/></w:p>",
    )
    assert closed_inside != _word_move()
    assert move_range_problem(_body(closed_inside)) == "content_outside_range"


def test_moved_content_outside_its_range_is_a_problem():
    """A wrapper past its range's end, and a flagged mark on a paragraph no
    range reaches: both are content outside a range."""
    outside = _body(
        f"<w:p>{_range('moveFrom', 'Start', 3)}{_range('moveFrom', 'End', 3)}"
        f"<w:moveFrom {_rev(2)}><w:r><w:t>B.</w:t></w:r></w:moveFrom></w:p>"
        + _moved_paragraph("moveTo", "B.", flag=4, wrapper=5, start=_range("moveTo", "Start", 6))
        + _range("moveTo", "End", 6)
    )
    assert move_range_problem(outside) == "content_outside_range"
    unreached = _body(
        _word_move() + f"<w:p><w:pPr><w:rPr><w:moveTo {_rev(7)}/></w:rPr></w:pPr></w:p>"
    )
    assert move_range_problem(unreached) == "content_outside_range"


def test_two_ranges_of_one_kind_may_not_overlap():
    """"If multiple move source containers surround the same text, the
    document is non-conformant" (ECMA-376 §17.13.5.24)."""
    overlapping = _body(
        f"<w:p>{_range('moveFrom', 'Start', 3)}{_range('moveFrom', 'Start', 7, 'move2')}"
        f"<w:moveFrom {_rev(2)}><w:r><w:t>B.</w:t></w:r></w:moveFrom>"
        f"{_range('moveFrom', 'End', 3)}{_range('moveFrom', 'End', 7)}</w:p>"
        f"<w:p>{_range('moveTo', 'Start', 6)}<w:moveTo {_rev(5)}><w:r><w:t>B.</w:t></w:r></w:moveTo>"
        f"{_range('moveTo', 'End', 6)}{_range('moveTo', 'Start', 8, 'move2')}{_range('moveTo', 'End', 8)}</w:p>"
    )
    assert move_range_problem(overlapping) == "overlapping_ranges"


def test_a_move_id_may_not_be_a_bookmark_id():
    """Bookmarks and move ranges share Word's one id counter (every
    Word-authored move sample numbers them in one sequence)."""
    body = _body(
        _word_move()
        + "<w:p><w:bookmarkStart w:id='3' w:name='_Ref1'/><w:bookmarkEnd w:id='3'/></w:p>"
    )
    assert move_range_problem(body) == "duplicate_id"


def test_a_body_without_moves_has_no_move_problem():
    body = _body(
        "<w:p><w:bookmarkStart w:id='1' w:name='_Ref1'/><w:r><w:t>x</w:t></w:r>"
        f"<w:bookmarkEnd w:id='1'/></w:p><w:p><w:ins {_rev(2)}><w:r><w:t>y</w:t></w:r></w:ins></w:p>"
    )
    assert move_range_problem(body) is None


# ---------------------------------------------------------------------------
# Paragraph marks
# ---------------------------------------------------------------------------


def test_an_inserted_paragraph_is_kept_by_accept_and_gone_on_reject():
    body = _body(
        "<w:p><w:r><w:t>First.</w:t></w:r></w:p>"
        f"<w:p><w:pPr><w:rPr><w:ins {_rev(1)}/></w:rPr></w:pPr>"
        f"<w:ins {_rev(2)}><w:r><w:t>Inserted.</w:t></w:r></w:ins></w:p>"
        "<w:p><w:r><w:t>Last.</w:t></w:r></w:p>"
    )
    accepted = accept_all(body)
    assert _texts(accepted) == ["First.", "Inserted.", "Last."]
    assert not has_revisions(accepted)
    assert _texts(reject_all(body)) == ["First.", "Last."]


def test_a_deleted_paragraph_is_gone_on_accept_and_whole_on_reject():
    body = _body(
        "<w:p><w:r><w:t>First.</w:t></w:r></w:p>"
        f"<w:p><w:pPr><w:pStyle w:val='Body'/><w:rPr><w:del {_rev(1)}/></w:rPr></w:pPr>"
        f"<w:del {_rev(2)}><w:r><w:delText>Deleted.</w:delText></w:r></w:del></w:p>"
        "<w:p><w:r><w:t>Last.</w:t></w:r></w:p>"
    )
    assert _texts(accept_all(body)) == ["First.", "Last."]
    rejected = reject_all(body)
    assert _texts(rejected) == ["First.", "Deleted.", "Last."]
    assert rejected[1].find(f"{{{W}}}pPr/{{{W}}}pStyle").get(f"{{{W}}}val") == "Body"


def test_a_removed_mark_joins_what_is_left_to_the_next_paragraph():
    """Word's rule: the next paragraph keeps its own properties and takes
    the leftover content in front of its own."""
    body = _body(
        "<w:p><w:pPr><w:jc w:val='center'/>"
        f"<w:rPr><w:del {_rev(1)}/></w:rPr></w:pPr>"
        "<w:r><w:t xml:space='preserve'>Kept </w:t></w:r></w:p>"
        "<w:p><w:pPr><w:jc w:val='right'/></w:pPr><w:r><w:t>next.</w:t></w:r></w:p>"
    )
    accepted = accept_all(body)
    assert _texts(accepted) == ["Kept next."]
    assert accepted[0].find(f"{{{W}}}pPr/{{{W}}}jc").get(f"{{{W}}}val") == "right"


def test_the_last_paragraph_mark_cannot_join_anything_and_stays():
    body = _body(
        "<w:p><w:pPr><w:rPr>"
        f"<w:del {_rev(1)}/></w:rPr></w:pPr><w:r><w:t>Only.</w:t></w:r></w:p>"
    )
    assert _texts(accept_all(body)) == ["Only."]


def test_consecutive_deleted_paragraphs_all_go():
    body = _body(
        "".join(
            f"<w:p><w:pPr><w:rPr><w:del {_rev(n)}/></w:rPr></w:pPr>"
            f"<w:del {_rev(n + 10)}><w:r><w:delText>{n}</w:delText></w:r></w:del></w:p>"
            for n in (1, 2, 3)
        )
        + "<w:p><w:r><w:t>Survivor.</w:t></w:r></w:p>"
    )
    assert _texts(accept_all(body)) == ["Survivor."]
    assert _texts(reject_all(body)) == ["1", "2", "3", "Survivor."]


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


def _table(rows: list[str], flag: str = "") -> str:
    body = ""
    for n, text in enumerate(rows):
        properties = f"<w:trPr><w:cantSplit/>{flag.format(id=n + 1)}</w:trPr>" if flag else ""
        body += f"<w:tr>{properties}<w:tc><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:tc></w:tr>"
    return f"<w:tbl><w:tblPr/><w:tblGrid><w:gridCol/></w:tblGrid>{body}</w:tbl>"


def test_deleted_rows_go_on_accept_and_an_emptied_table_goes_with_them():
    body = _body(
        "<w:p><w:r><w:t>Before.</w:t></w:r></w:p>"
        + _table(["AHU-1", "P-1"], flag=f"<w:del {REV}/>")
        + "<w:p><w:r><w:t>After.</w:t></w:r></w:p>"
    )
    assert _texts(accept_all(body)) == ["Before.", "After."]
    rejected = reject_all(body)
    assert _texts(rejected) == ["Before.", "tbl", "After."]
    assert not has_revisions(rejected)


def test_inserted_rows_go_on_reject():
    body = _body(_table(["new row"], flag=f"<w:ins {REV}/>"))
    assert _texts(reject_all(body)) == []
    assert _texts(accept_all(body)) == ["tbl"]


def test_a_row_flag_follows_the_rows_other_properties():
    """The row keeps its other properties when a flag is resolved."""
    body = _body(_table(["kept"], flag=f"<w:ins {REV}/>"))
    accepted = accept_all(body)
    row_properties = accepted.find(f".//{{{W}}}trPr")
    assert [etree.QName(c).localname for c in row_properties] == ["cantSplit"]


# ---------------------------------------------------------------------------
# Property changes
# ---------------------------------------------------------------------------


def test_a_paragraph_property_change_restores_the_old_properties_on_reject():
    body = _body(
        "<w:p><w:pPr><w:pStyle w:val='New'/><w:numPr><w:numId w:val='0'/></w:numPr>"
        "<w:rPr><w:b/></w:rPr><w:sectPr/>"
        f"<w:pPrChange {_rev(1)}><w:pPr><w:pStyle w:val='Old'/>"
        "<w:numPr><w:ilvl w:val='1'/><w:numId w:val='7'/></w:numPr></w:pPr></w:pPrChange>"
        "</w:pPr><w:r><w:t>x</w:t></w:r></w:p>"
    )
    accepted = accept_all(body)
    assert accepted.find(f".//{{{W}}}pPrChange") is None
    assert accepted.find(f".//{{{W}}}numId").get(f"{{{W}}}val") == "0"
    rejected = reject_all(body)
    properties = rejected.find(f"{{{W}}}p/{{{W}}}pPr")
    assert [etree.QName(c).localname for c in properties] == [
        "pStyle",
        "numPr",
        "rPr",
        "sectPr",
    ]
    assert properties.find(f"{{{W}}}pStyle").get(f"{{{W}}}val") == "Old"
    assert properties.find(f".//{{{W}}}numId").get(f"{{{W}}}val") == "7"


def test_a_run_property_change_restores_the_old_properties_on_reject():
    body = _body(
        "<w:p><w:r><w:rPr><w:b/>"
        f"<w:rPrChange {_rev(1)}><w:rPr><w:i/></w:rPr></w:rPrChange>"
        "</w:rPr><w:t>x</w:t></w:r></w:p>"
    )
    accepted = accept_all(body)
    assert accepted.find(f".//{{{W}}}b") is not None
    assert accepted.find(f".//{{{W}}}rPrChange") is None
    rejected = reject_all(body)
    assert rejected.find(f".//{{{W}}}i") is not None
    assert rejected.find(f".//{{{W}}}b") is None


def test_a_paragraph_mark_property_change_restores_the_mark_formatting():
    """``w:pPr/w:rPr/w:rPrChange``: the paragraph mark's OWN run formatting,
    tracked — what the redline records for a document's last paragraph."""
    body = _body(
        "<w:p><w:pPr><w:rPr><w:i/>"
        f"<w:rPrChange {_rev(1)}><w:rPr><w:b/><w:sz w:val='28'/></w:rPr></w:rPrChange>"
        "</w:rPr></w:pPr><w:r><w:t>x</w:t></w:r></w:p>"
    )
    mark = f"{{{W}}}p/{{{W}}}pPr/{{{W}}}rPr"
    accepted = accept_all(body).find(mark)
    assert [etree.QName(c).localname for c in accepted] == ["i"]
    rejected = reject_all(body).find(mark)
    assert [etree.QName(c).localname for c in rejected] == ["b", "sz"]


def test_a_deleted_last_paragraph_with_its_formatting_recorded_resolves_both_ways():
    """The shape the redline writes when the document's LAST paragraph is
    deleted: its words in ``w:del``, its mark unflagged (Word cannot track
    it), and its formatting moved into property changes whose current side is
    empty. Accept All leaves a formatting-free empty paragraph — the one
    leftover the comparison tolerates; Reject All, the paragraph as it was."""
    original = _body(
        "<w:p><w:r><w:t>Kept.</w:t></w:r></w:p>"
        "<w:p><w:pPr><w:numPr><w:ilvl w:val='0'/><w:numId w:val='3'/></w:numPr>"
        "<w:rPr><w:b/></w:rPr></w:pPr><w:r><w:t>Last.</w:t></w:r></w:p><w:sectPr/>"
    )
    redline = _body(
        "<w:p><w:r><w:t>Kept.</w:t></w:r></w:p>"
        "<w:p><w:pPr><w:rPr>"
        f"<w:rPrChange {_rev(2)}><w:rPr><w:b/></w:rPr></w:rPrChange></w:rPr>"
        f"<w:pPrChange {_rev(1)}><w:pPr><w:numPr><w:ilvl w:val='0'/>"
        "<w:numId w:val='3'/></w:numPr></w:pPr></w:pPrChange></w:pPr>"
        f"<w:del {_rev(3)}><w:r><w:delText>Last.</w:delText></w:r></w:del></w:p>"
        "<w:sectPr/>"
    )
    kept = _body("<w:p><w:r><w:t>Kept.</w:t></w:r></w:p><w:sectPr/>")
    assert _equal(accept_all(redline), kept)
    assert _equal(reject_all(redline), original)


# ---------------------------------------------------------------------------
# The canonical comparison
# ---------------------------------------------------------------------------


def test_run_splits_are_not_differences():
    one = _body("<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Section includes</w:t></w:r></w:p>")
    split = _body(
        "<w:p><w:r><w:rPr><w:b/></w:rPr><w:t xml:space='preserve'>Section </w:t></w:r>"
        "<w:r><w:rPr><w:b/></w:rPr><w:t>includes</w:t></w:r></w:p>"
    )
    assert _equal(one, split)
    two_nodes = _body(
        "<w:p><w:r><w:rPr><w:b/></w:rPr><w:t xml:space='preserve'>Section </w:t>"
        "<w:t>includes</w:t></w:r></w:p>"
    )
    assert _equal(one, two_nodes)


def test_formatting_differences_are_differences():
    bold = _body("<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>x</w:t></w:r></w:p>")
    plain = _body("<w:p><w:r><w:t>x</w:t></w:r></w:p>")
    difference = first_difference(bold, plain)
    assert difference is not None
    assert difference.index == 0
    assert difference.path.startswith("p/r")


def test_text_differences_are_reported_without_the_text():
    left = _body("<w:p><w:r><w:t>Provide isolators.</w:t></w:r></w:p>")
    right = _body("<w:p><w:r><w:t>Provide restraints.</w:t></w:r></w:p>")
    difference = first_difference(left, right)
    assert difference.to_dict() == {
        "index": 0,
        "left": "p",
        "right": "p",
        "path": "p/r/t",
    }
    assert "isolators" not in str(difference.to_dict())


def test_identity_comments_and_empty_containers_are_not_content():
    left = _body(
        "<w:p w14:paraId='1A2B3C4D' w14:textId='77777777'><w:pPr/>"
        "<!-- a comment --><w:r><w:rPr/></w:r><w:r><w:t>x</w:t></w:r></w:p>"
    )
    right = _body("<w:p><w:r><w:t>x</w:t></w:r></w:p>")
    assert _equal(left, right)


def test_bookmarks_compare_by_name_and_can_be_excluded():
    left = _body(
        "<w:p><w:bookmarkStart w:id='1' w:name='_Ref1'/><w:r><w:t>x</w:t></w:r>"
        "<w:bookmarkEnd w:id='1'/></w:p>"
    )
    renumbered = _body(
        "<w:p><w:bookmarkStart w:id='44' w:name='_Ref1'/><w:r><w:t>x</w:t></w:r>"
        "<w:bookmarkEnd w:id='44'/></w:p>"
    )
    assert _equal(left, renumbered)
    moved = _body(
        "<w:p><w:r><w:t>x</w:t></w:r><w:bookmarkStart w:id='1' w:name='_Ref1'/>"
        "<w:bookmarkEnd w:id='1'/></w:p>"
    )
    assert not _equal(left, moved)
    bare = _body("<w:p><w:r><w:t>x</w:t></w:r></w:p>")
    assert not _equal(left, bare)
    assert _equal(left, bare, exclude_bookmarks={"_Ref1"})


def test_trailing_empty_paragraphs_are_not_differences_but_inner_ones_are():
    """Word cannot track a document's last paragraph mark, so a tracked
    insertion or deletion there leaves an empty paragraph behind."""
    short = _body("<w:p><w:r><w:t>x</w:t></w:r></w:p><w:sectPr/>")
    padded = _body("<w:p><w:r><w:t>x</w:t></w:r></w:p><w:p><w:pPr/></w:p><w:sectPr/>")
    assert _equal(short, padded)
    inner = _body("<w:p/><w:p><w:r><w:t>x</w:t></w:r></w:p><w:sectPr/>")
    assert not _equal(short, inner)
    # An empty paragraph holding a section break is never "trailing noise".
    held = _body(
        "<w:p><w:r><w:t>x</w:t></w:r></w:p><w:p><w:pPr><w:sectPr/></w:pPr></w:p>"
        "<w:sectPr/>"
    )
    assert not _equal(short, held)


@pytest.mark.parametrize(
    "properties",
    [
        "<w:pPr><w:numPr><w:ilvl w:val='0'/><w:numId w:val='3'/></w:numPr></w:pPr>",
        "<w:pPr><w:pageBreakBefore/></w:pPr>",
        "<w:pPr><w:pStyle w:val='PR1'/></w:pPr>",
        "<w:pPr><w:pBdr><w:top w:val='single' w:sz='4'/></w:pBdr></w:pPr>",
        "<w:pPr><w:rPr><w:sz w:val='72'/></w:rPr></w:pPr>",
    ],
    ids=["numbered", "page-break-before", "styled", "bordered", "sized-mark"],
)
def test_a_trailing_empty_paragraph_that_still_shows_something_is_a_difference(
    properties,
):
    """Codex, PR #187: an EMPTY paragraph is still visible when it sets
    anything — a numbered one prints its number, a page break before it
    makes a blank page, a style can do either, a border draws, and the
    mark's own run formatting sets the empty line's height. Only a
    formatting-free one is what the untrackable last paragraph mark may
    leave behind, so only that one is tolerated, on either side."""
    short = _body("<w:p><w:r><w:t>x</w:t></w:r></w:p><w:sectPr/>")
    padded = _body(
        f"<w:p><w:r><w:t>x</w:t></w:r></w:p><w:p>{properties}</w:p><w:sectPr/>"
    )
    assert not _equal(short, padded)
    assert not _equal(padded, short)


@pytest.mark.parametrize(
    "plain",
    [
        "<w:p/>",
        "<w:p><w:pPr/></w:p>",
        "<w:p><w:pPr><w:rPr/></w:pPr></w:p>",
        "<w:p w:rsidR='00AB12CD' w14:paraId='1A2B3C4D'/>",
    ],
    ids=["bare", "empty-properties", "empty-mark", "revision-session-ids"],
)
def test_a_formatting_free_trailing_paragraph_is_still_tolerated(plain):
    """Empty property containers and revision-session / paragraph ids set
    nothing Word shows."""
    short = _body("<w:p><w:r><w:t>x</w:t></w:r></w:p><w:sectPr/>")
    padded = _body(f"<w:p><w:r><w:t>x</w:t></w:r></w:p>{plain}{plain}<w:sectPr/>")
    assert _equal(short, padded)


def test_canonical_form_ignores_namespace_prefixes():
    spelled = etree.fromstring(
        f'<x:body xmlns:x="{W}"><x:p><x:r><x:t>x</x:t></x:r></x:p></x:body>'
    )
    assert _equal(spelled, _body("<w:p><w:r><w:t>x</w:t></w:r></w:p>"))
    assert canonical_body(spelled) == canonical_body(
        _body("<w:p><w:r><w:t>x</w:t></w:r></w:p>")
    )


def test_duplicate_bookmark_names_are_found():
    body = _body(
        "<w:p><w:bookmarkStart w:id='1' w:name='a'/><w:bookmarkEnd w:id='1'/>"
        "<w:bookmarkStart w:id='2' w:name='b'/><w:bookmarkEnd w:id='2'/>"
        "<w:bookmarkStart w:id='3' w:name='a'/><w:bookmarkEnd w:id='3'/></w:p>"
    )
    assert duplicate_bookmark_names(body) == {"a"}


def test_accept_and_reject_of_a_full_redline_round_trip():
    """The shape the export writes, end to end: kept, spliced, inserted and
    deleted paragraphs in one body."""
    body = _body(
        "<w:p><w:r><w:t>Kept.</w:t></w:r></w:p>"
        "<w:p><w:r><w:t xml:space='preserve'>A.</w:t><w:tab/></w:r>"
        f"<w:del {_rev(1)}><w:r><w:delText>vibration</w:delText></w:r></w:del>"
        f"<w:ins {_rev(2)}><w:r><w:t>seismic</w:t></w:r></w:ins>"
        "<w:r><w:t xml:space='preserve'> isolation.</w:t></w:r></w:p>"
        f"<w:p><w:pPr><w:rPr><w:ins {_rev(3)}/></w:rPr></w:pPr>"
        f"<w:ins {_rev(4)}><w:r><w:t>New.</w:t></w:r></w:ins></w:p>"
        f"<w:p><w:pPr><w:rPr><w:del {_rev(5)}/></w:rPr></w:pPr>"
        f"<w:del {_rev(6)}><w:r><w:delText>Gone.</w:delText></w:r></w:del></w:p>"
        "<w:sectPr/>"
    )
    accepted = _body(
        "<w:p><w:r><w:t>Kept.</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>A.</w:t><w:tab/></w:r><w:r><w:t>seismic</w:t></w:r>"
        "<w:r><w:t xml:space='preserve'> isolation.</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>New.</w:t></w:r></w:p><w:sectPr/>"
    )
    rejected = _body(
        "<w:p><w:r><w:t>Kept.</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>A.</w:t><w:tab/></w:r><w:r><w:t>vibration</w:t></w:r>"
        "<w:r><w:t xml:space='preserve'> isolation.</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Gone.</w:t></w:r></w:p><w:sectPr/>"
    )
    assert _equal(accept_all(body), accepted)
    assert _equal(reject_all(body), rejected)
