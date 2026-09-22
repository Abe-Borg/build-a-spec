"""The word-level splice (Redline on your original, D-2; built in Phase 0).

``plan_splice`` is pure — source text and target text in, an edit script
out — so its contract is pinned directly: the keep and delete steps
partition the source in order (Reject All would give back the original),
keep + insert reproduce the target's words (Accept All gives the edit), and
whitespace follows the rules the module states. The rendering half is
pinned on real paragraphs: unchanged words keep their runs, markers keep
their places, and anything the splice cannot map is refused with a named
reason rather than guessed at.
"""
from __future__ import annotations

import random

from docx import Document
from docx.enum.text import WD_BREAK
from docx.oxml.ns import qn
from lxml import etree

from backend.spec_doc.importer import _accept_all_paragraph_text
from backend.spec_doc.source_splice import (
    OP_DELETE,
    OP_INSERT,
    OP_KEEP,
    map_paragraph,
    plan_splice,
    splice_paragraph,
    word_spans,
)
from tests.docx_fidelity_helpers import _append_hyperlink, _append_page_field


def _views(source: str, ops) -> tuple[str, str]:
    """(accept, reject): the texts Accept All and Reject All would give."""
    accept = []
    reject = []
    for op in ops:
        if op.op == OP_INSERT:
            accept.append(op.text)
        elif op.op == OP_KEEP:
            accept.append(source[op.start : op.end])
            reject.append(source[op.start : op.end])
        else:
            reject.append(source[op.start : op.end])
    return "".join(accept), "".join(reject)


def _assert_script(source: str, target: str) -> str:
    ops = plan_splice(source, target)
    cursor = 0
    for op in ops:
        if op.op in (OP_KEEP, OP_DELETE):
            assert op.start == cursor, (source, target, ops)
            assert op.end > op.start
            cursor = op.end
        else:
            assert op.text
    assert cursor == len(source), (source, target, ops)
    accept, reject = _views(source, ops)
    assert reject == source
    assert accept.split() == target.split(), (source, target, accept)
    return accept


def test_word_spans_follow_str_split():
    for text in ("", "   ", "a", " a  b\tc\n", "A. Nbsp", "x y"):
        assert [text[a:b] for a, b in word_spans(text)] == text.split()


def test_relettering_keeps_the_tab_after_the_letter():
    assert _assert_script(
        "A.\tSection includes vibration isolation.",
        "B. Section includes vibration isolation.",
    ) == "B.\tSection includes vibration isolation."


def test_an_insertion_after_the_label_keeps_the_tab_before_it():
    assert _assert_script("A.\tSection includes.", "A. New Section includes.") == (
        "A.\tNew Section includes."
    )


def test_a_deleted_word_takes_the_gap_after_it():
    assert _assert_script(
        "A.\tSection includes vibration.", "A. includes vibration."
    ) == "A.\tincludes vibration."
    assert _assert_script("Foo bar baz", "bar baz") == "bar baz"


def test_a_deletion_at_the_end_leaves_no_trailing_gap():
    assert _assert_script("Provide spring isolators", "Provide spring") == (
        "Provide spring"
    )


def test_survivors_keep_the_source_spacing_between_them():
    assert _assert_script(
        "Provide isolators.  Comply.", "Provide isolators. Comply. Now."
    ) == "Provide isolators.  Comply. Now."


def test_an_insertion_at_the_end_is_separated_from_the_last_word():
    assert _assert_script("Provide isolators.", "Provide isolators. Comply.") == (
        "Provide isolators. Comply."
    )


def test_edge_scripts():
    assert _assert_script("", "New text") == "New text"
    assert _assert_script("   ", "New") == "   New"
    assert _assert_script("Old words", "Brand new") == "Brand new"
    assert _assert_script("Some words", "") == ""
    assert _assert_script("Same words", "Same words") == "Same words"


def test_new_text_takes_the_formatting_word_would_give_it():
    """Typed over a selection: the first replaced character's formatting.
    Inserted: the character before it; at the very start, the first."""
    source = "Section includes vibration isolation."
    (replace,) = [
        op for op in plan_splice(source, "Section includes seismic isolation.")
        if op.op == OP_INSERT
    ]
    assert replace.style_at == source.index("vibration")
    (insert,) = [
        op for op in plan_splice(source, "Section includes spring vibration isolation.")
        if op.op == OP_INSERT
    ]
    assert insert.style_at == source.index("vibration") - 1
    (first,) = [
        op for op in plan_splice(source, "Also Section includes vibration isolation.")
        if op.op == OP_INSERT
    ]
    assert first.style_at == 0
    (last,) = [
        op for op in plan_splice(source, "Section includes vibration isolation. Done.")
        if op.op == OP_INSERT
    ]
    assert last.style_at == len(source) - 1


def test_the_script_invariants_hold_across_many_edits():
    """Random words, random whitespace (tabs, double spaces, a leading and
    trailing gap), random insertions, deletions and replacements."""
    rng = random.Random(20260922)
    vocabulary = [
        "A.", "B.", "1.", "Section", "includes", "vibration", "isolation",
        "for", "mechanical", "equipment.", "NFPA", "13-2025.", "Provide",
        "spring", "isolators", "and", "restraints", "(TBD)",
    ]
    gaps = [" ", " ", " ", "  ", "\t", "\n", " \t"]
    for _ in range(600):
        words = [rng.choice(vocabulary) for _ in range(rng.randint(0, 9))]
        source = rng.choice(["", " ", "\t"])
        for index, word in enumerate(words):
            if index:
                source += rng.choice(gaps)
            source += word
        source += rng.choice(["", "", " "])
        edited = list(words)
        for _ in range(rng.randint(0, 4)):
            action = rng.choice(["insert", "delete", "replace"])
            position = rng.randint(0, len(edited))
            if action == "insert":
                edited.insert(position, rng.choice(vocabulary))
            elif edited and position < len(edited):
                if action == "delete":
                    del edited[position]
                else:
                    edited[position] = rng.choice(vocabulary)
        _assert_script(source, " ".join(edited))


# ---------------------------------------------------------------------------
# The character map and the rendering
# ---------------------------------------------------------------------------


def _paragraph():
    return Document().add_paragraph()


def test_the_map_reads_what_the_importer_reads():
    paragraph = _paragraph()
    paragraph.add_run("A.\tSection ")
    run = paragraph.add_run("vibration")
    run.bold = True
    run.add_break()  # a text-wrapping break reads as "\n"
    paragraph.add_run("isolation").add_break(WD_BREAK.PAGE)  # reads as ""
    etree.SubElement(paragraph.runs[0]._r, qn("w:lastRenderedPageBreak"))
    pmap, reason = map_paragraph(paragraph._p)
    assert reason == ""
    assert pmap.text == _accept_all_paragraph_text(paragraph._p)
    assert pmap.text == "A.\tSection vibration\nisolation"


def test_unmappable_paragraphs_are_refused_by_name():
    linked = _paragraph()
    linked.add_run("See ")
    _append_hyperlink(linked, "the client standard", "https://example.com")
    assert map_paragraph(linked._p) == (None, "hyperlink")

    field = _paragraph()
    field.add_run("Page ")
    _append_page_field(field)
    assert map_paragraph(field._p) == (None, "field")

    revised = _paragraph()
    inserted = etree.SubElement(revised._p, qn("w:ins"))
    etree.SubElement(etree.SubElement(inserted, qn("w:r")), qn("w:t")).text = "new"
    assert map_paragraph(revised._p) == (None, "revisions")

    symbol = _paragraph()
    etree.SubElement(symbol.add_run("x")._r, qn("w:sym"))
    assert map_paragraph(symbol._p) == (None, "symbol")

    plain = _paragraph()
    plain.add_run("Plain.")
    assert map_paragraph(plain._p, expected_text="Something else") == (
        None,
        "text_mismatch",
    )


def _runs(element) -> list[tuple[bool, str]]:
    """(bold, text) per run, tabs shown as \\t."""
    runs = []
    for run in element.iter(qn("w:r")):
        properties = run.find(qn("w:rPr"))
        bold = properties is not None and properties.find(qn("w:b")) is not None
        text = "".join(
            "\t" if node.tag == qn("w:tab") else (node.text or "")
            for node in run
            if node.tag in (qn("w:t"), qn("w:tab"))
        )
        runs.append((bold, text))
    return runs


def test_unchanged_words_keep_their_own_runs():
    paragraph = _paragraph()
    paragraph.add_run("A.\tSection includes ")
    paragraph.add_run("vibration isolation").bold = True
    paragraph.add_run(" for mechanical equipment.")

    spliced, reason = splice_paragraph(
        paragraph._p, "B. Section includes vibration isolation for HVAC equipment."
    )

    assert reason == ""
    assert "".join(text for _bold, text in _runs(spliced)) == (
        "B.\tSection includes vibration isolation for HVAC equipment."
    )
    assert [text for bold, text in _runs(spliced) if bold] == ["vibration isolation"]
    # The source element itself is never mutated.
    assert _accept_all_paragraph_text(paragraph._p) == (
        "A.\tSection includes vibration isolation for mechanical equipment."
    )


def test_markers_keep_their_places_and_a_leading_page_break_survives():
    """A bookmark around the text still wraps it after the letter changes,
    and a page break before a relettered label is not deleted with the old
    letter (it sits on the edge of the deletion, not inside it)."""
    paragraph = _paragraph()
    start = etree.SubElement(paragraph._p, qn("w:bookmarkStart"))
    start.set(qn("w:id"), "1")
    start.set(qn("w:name"), "_Ref1")
    paragraph.add_run().add_break(WD_BREAK.PAGE)
    paragraph.add_run("A.\tProvide isolators.")
    end = etree.SubElement(paragraph._p, qn("w:bookmarkEnd"))
    end.set(qn("w:id"), "1")

    spliced, reason = splice_paragraph(paragraph._p, "B. Provide isolators.")

    assert reason == ""
    tags = [etree.QName(child).localname for child in spliced if child.tag != qn("w:pPr")]
    assert tags[0] == "bookmarkStart" and tags[-1] == "bookmarkEnd"
    breaks = [
        br for br in spliced.iter(qn("w:br")) if br.get(qn("w:type")) == "page"
    ]
    assert len(breaks) == 1
    assert _accept_all_paragraph_text(spliced) == "B.\tProvide isolators."


def _layout(element) -> list[str]:
    """The paragraph's content in document order: ``<page>`` / ``<column>``
    for a break, ``[name]`` / ``[/name]`` for a bookmark, text otherwise."""
    names = {}
    tokens: list[str] = []
    for node in element.iter():
        if node.tag == qn("w:bookmarkStart"):
            names[node.get(qn("w:id"))] = node.get(qn("w:name"))
            tokens.append(f"[{node.get(qn('w:name'))}]")
        elif node.tag == qn("w:bookmarkEnd"):
            tokens.append(f"[/{names.get(node.get(qn('w:id')), '?')}]")
        elif node.tag == qn("w:br") and node.get(qn("w:type")) in ("page", "column"):
            tokens.append(f"<{node.get(qn('w:type'))}>")
        elif node.tag == qn("w:tab"):
            tokens.append("\t")
        elif node.tag == qn("w:t") and node.text:
            tokens.append(node.text)
    # Adjacent text tokens are one run of text to the reader.
    merged: list[str] = []
    for token in tokens:
        plain = not token.startswith(("<", "["))
        if plain and merged and not merged[-1].startswith(("<", "[")):
            merged[-1] += token
        else:
            merged.append(token)
    return merged


def test_words_prepended_to_a_paragraph_join_it_after_its_leading_break():
    """A page or column break in front of a paragraph's first word starts the
    paragraph on a new page or column. Words prepended to the paragraph join
    it there; they are not stranded on the page before the break (Codex
    review on PR #184). The rule is about LEADING content only: a break at
    the end of a paragraph stays after words appended to it."""
    own_run = _paragraph()
    own_run.add_run().add_break(WD_BREAK.PAGE)
    own_run.add_run("Provide isolators.")
    spliced, reason = splice_paragraph(own_run._p, "Also Provide isolators.")
    assert reason == ""
    assert _layout(spliced) == ["<page>", "Also Provide isolators."]

    same_run = _paragraph()
    run = same_run.add_run()
    run.add_break(WD_BREAK.COLUMN)
    etree.SubElement(run._r, qn("w:t")).text = "Provide isolators."
    spliced, reason = splice_paragraph(same_run._p, "Also Provide isolators.")
    assert reason == ""
    assert _layout(spliced) == ["<column>", "Also Provide isolators."]

    # "Before the first word", not "at offset 0": leading whitespace is kept
    # where it was, and the break still leads the words.
    indented = _paragraph()
    indented.add_run("\t")
    indented.add_run().add_break(WD_BREAK.PAGE)
    indented.add_run("Provide isolators.")
    spliced, reason = splice_paragraph(indented._p, "Also Provide isolators.")
    assert reason == ""
    assert _layout(spliced) == ["\t", "<page>", "Also Provide isolators."]

    # A bookmark opening in front of the first word still wraps all of it.
    bookmarked = _paragraph()
    start = etree.SubElement(bookmarked._p, qn("w:bookmarkStart"))
    start.set(qn("w:id"), "7")
    start.set(qn("w:name"), "_Toc7")
    bookmarked.add_run("SUMMARY")
    end = etree.SubElement(bookmarked._p, qn("w:bookmarkEnd"))
    end.set(qn("w:id"), "7")
    spliced, reason = splice_paragraph(bookmarked._p, "GENERAL SUMMARY")
    assert reason == ""
    assert _layout(spliced) == ["[_Toc7]", "GENERAL SUMMARY", "[/_Toc7]"]

    # The end is not the start: a trailing break stays after appended words.
    trailing = _paragraph()
    trailing.add_run("Provide isolators.")
    trailing.add_run().add_break(WD_BREAK.PAGE)
    spliced, reason = splice_paragraph(trailing._p, "Provide isolators. Comply.")
    assert reason == ""
    assert _layout(spliced) == ["Provide isolators. Comply.", "<page>"]

    # And a break in front of a later word stays in front of that word.
    middle = _paragraph()
    middle.add_run("Provide ")
    middle.add_run().add_break(WD_BREAK.PAGE)
    middle.add_run("isolators.")
    spliced, reason = splice_paragraph(middle._p, "Provide spring isolators.")
    assert reason == ""
    assert _layout(spliced) == ["Provide spring ", "<page>", "isolators."]


def test_a_zero_width_node_inside_a_deleted_word_goes_with_it():
    paragraph = _paragraph()
    run = paragraph.add_run("Provide spring")
    etree.SubElement(run._r, qn("w:softHyphen"))
    etree.SubElement(run._r, qn("w:t")).text = "loaded isolators."

    spliced, reason = splice_paragraph(paragraph._p, "Provide isolators.")

    assert reason == ""
    assert spliced.find(f".//{qn('w:softHyphen')}") is None
    assert _accept_all_paragraph_text(spliced) == "Provide isolators."
