"""Comments on changes in the redline on your original (redline plan Phase 3).

Every tracked change with a recorded basis carries a Word comment from
"Build-a-Spec" saying what it rests on: the research finding, the attached
document, or the Final QC fix behind it, with the source web links. The
comments are added by a pass that runs after the self-check and only ADDS:

* removing exactly its anchors gives back the redline without comments,
  byte-identical in substance (element for element);
* Accept All still gives the formatted export and Reject All the upload —
  with the comment anchors excluded — and both leave every comment anchored;
* every part other than the body and the comment parts is the upload's, byte
  for byte, and a comment part the upload already had is the upload's part
  plus Build-a-Spec's additions and nothing else.

The first half of this file is the wording (``backend/redline_basis.py``);
the second the pass (``backend/spec_doc/redline_comments.py``) on every
master shape the redline handles; the third the durable QC fix record through
the app, and the switch.
"""
from __future__ import annotations

import ast
import copy
import io
import pathlib
import zipfile

import pytest
from docx import Document
from docx.oxml.ns import qn
from fastapi.testclient import TestClient
from lxml import etree

from backend import sessions, settings
from backend.app import create_app
from backend.qc.engine import QCSourceRecord
from backend.redline_basis import (
    MAX_COMMENT_LINKS,
    MAX_COMMENT_TEXT,
    redline_comment_basis,
    trimmed,
)
from backend.research.engine import RequirementsProfile, ResearchItem
from backend.spec_doc import redline_comments, source_render
from backend.spec_doc.redline_comments import (
    COMMENT_FALLBACK_REASONS,
    COMMENT_SKIP_REASONS,
    CommentBasis,
    CommentLink,
    strip_comment_anchors,
)
from backend.spec_doc.revisions import accept_all, first_difference, reject_all
from backend.spec_doc.source_render import (
    render_preserving_docx,
    render_preserving_redline,
)
from tests.fakes import audit_grade_qc_result
from tests.test_preserving_export import (
    _break_master,
    _import,
    _master_bytes,
    _save,
    _typed_letter_master,
)
from tests.test_qc_chat_apply import _finding
from tests.test_redline_original import (
    AUTHOR,
    CORPUS_SWEEP_SEEDS,
    DATE,
    STRUCTURAL_REFUSALS,
    _body,
    _edit,
    _highest_id,
    _inserted_bookmarks,
    _linked_master,
    _members,
    _numbered_family_master,
    _numbered_master,
    _parse,
    _plain_last_master,
    _provisions,
    _style_numbered_master,
    corpus_sweep_edits,
)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_COMMENT_PARTS = frozenset(
    {
        "word/comments.xml",
        "word/_rels/comments.xml.rels",
        "word/_rels/document.xml.rels",
        "[Content_Types].xml",
    }
)
_ANCHORS = (
    qn("w:commentRangeStart"),
    qn("w:commentRangeEnd"),
    qn("w:commentReference"),
)
_WRAPPERS = frozenset(qn(f"w:{n}") for n in ("ins", "del", "moveFrom", "moveTo"))


# ---------------------------------------------------------------------------
# The wording
# ---------------------------------------------------------------------------


def _text(paragraphs) -> list[str]:
    """Comment paragraphs as plain lines (a link shows its label)."""
    return [
        "".join(s.label if isinstance(s, CommentLink) else s for s in paragraph)
        for paragraph in paragraphs
    ]


def _links(paragraphs) -> list[str]:
    return [s.url for paragraph in paragraphs for s in paragraph if isinstance(s, CommentLink)]


def _tree(*provisions):
    """A current tree: one article, one provision per ``(text, source)``."""
    from backend.spec_doc.model import DocumentStore, apply_edits

    ops = [{"action": "add_article", "target_id": "pt1", "text": "SUMMARY"}]
    for text, source in provisions:
        op = {"action": "add_paragraph", "target_id": "pt1.a1", "text": text}
        if source:
            op["source_item_id"] = source
        ops.append(op)
    section, _ = apply_edits(DocumentStore().doc, ops)
    return section


def _item(item_id, *, grounded, accepted=(), cited=(), **extra):
    return ResearchItem(
        item_id=item_id,
        dimension_id="governing_codes",
        topic="t",
        category="c",
        requirement=extra.pop("requirement", "Provide seismic bracing per ASCE 7."),
        authority=extra.pop("authority", "City of Reno"),
        code_reference=extra.pop("code_reference", "IBC 1613"),
        source_urls=list(cited),
        accepted_sources=list(accepted),
        grounded=grounded,
        research_date=extra.pop("research_date", "2026-09-01"),
    )


def test_a_grounded_research_item_cites_what_grounding_accepted():
    item = _item(
        "r-abc123",
        grounded=True,
        accepted=["https://codes.example/asce7"],
        cited=["https://codes.example/asce7", "https://blog.example/only-cited"],
    )
    section = _tree(("Provide bracing.", "r-abc123"))
    basis = redline_comment_basis(
        section, profile=RequirementsProfile(items=[item])
    )["pt1.a1.p1"]
    assert basis.qc == () and not basis.unresolved_source
    assert _text(basis.research) == [
        "Basis: requirements research (item r-abc123, researched 2026-09-01)",
        "Provide seismic bracing per ASCE 7.",
        "Authority: City of Reno",
        "Code reference: IBC 1613",
        "Sources:",
        "https://codes.example/asce7",
    ]
    # Accepted only — never a URL grounding did not accept.
    assert _links(basis.research) == ["https://codes.example/asce7"]


def test_an_ungrounded_item_is_named_a_lead_and_its_citations_unverified():
    item = _item(
        "r-lead01", grounded=False, cited=["https://blog.example/claim"]
    )
    section = _tree(("Provide bracing.", "r-lead01"))
    basis = redline_comment_basis(
        section, profile=RequirementsProfile(items=[item])
    )["pt1.a1.p1"]
    lines = _text(basis.research)
    assert lines[0] == (
        "Basis: a requirements research lead (item r-lead01) that was not "
        "verified against a retrieved source"
    )
    assert "Cited (not verified):" in lines
    assert "Sources:" not in lines
    assert _links(basis.research) == ["https://blog.example/claim"]


def test_an_attached_document_is_named_without_a_link():
    section = _tree(("Per the owner standard.", "ref-2"))
    basis = redline_comment_basis(
        section,
        references=[{"rid": "ref-2", "title": "Owner Standard", "filename": "owner.pdf"}],
    )["pt1.a1.p1"]
    assert _text(basis.research) == [
        'Basis: attached document "Owner Standard" (owner.pdf).'
    ]
    assert _links(basis.research) == []


def test_an_id_that_resolves_to_nothing_is_flagged_not_described():
    section = _tree(("Gone.", "r-missing"), ("Also gone.", "ref-9"))
    bases = redline_comment_basis(section, profile=RequirementsProfile(items=[]))
    assert bases["pt1.a1.p1"] == CommentBasis(unresolved_source=True)
    assert bases["pt1.a1.p2"] == CommentBasis(unresolved_source=True)


def _fix_entry(uid, text, **extra):
    return {
        "finding_id": extra.pop("finding_id", "qc-1"),
        "title": extra.pop("title", "Cite the adopted edition"),
        "severity": "high",
        "lens_id": "code_compliance",
        "lens_title": "Code compliance",
        "issue": extra.pop("issue", "The provision cites a superseded edition."),
        "sources": extra.pop(
            "sources", [{"url": "https://www.nfpa.org/13", "title": "NFPA 13"}]
        ),
        "applied_at": "2026-09-20T10:00:00+00:00",
        "run_id": "run-1",
        "evidence": [
            {
                "key": ["field", uid],
                "value": {
                    "kind": "p",
                    "text": text,
                    "status": "confirmed",
                    "source_item_id": "",
                },
            }
        ],
    }


def test_a_qc_fix_names_the_finding_its_issue_and_its_accepted_sources():
    section = _tree(("Comply with NFPA 13-2025.", ""))
    basis = redline_comment_basis(
        section, fix_log=[_fix_entry("pt1.a1.p1", "Comply with NFPA 13-2025.")]
    )["pt1.a1.p1"]
    assert _text(basis.qc) == [
        "Changed by a Final QC fix, applied 2026-09-20: Cite the adopted "
        "edition (high, Code compliance)",
        "The provision cites a superseded edition.",
        "Sources:",
        "NFPA 13",
    ]
    assert _links(basis.qc) == ["https://www.nfpa.org/13"]
    # Nothing covers an element whose text moved on since the fix.
    assert redline_comment_basis(
        section, fix_log=[_fix_entry("pt1.a1.p1", "Something else.")]
    ) == {}


def test_both_bases_ride_one_comment_qc_first():
    item = _item("r-1", grounded=True, accepted=["https://a.example/"])
    section = _tree(("Comply.", "r-1"))
    basis = redline_comment_basis(
        section,
        profile=RequirementsProfile(items=[item]),
        fix_log=[_fix_entry("pt1.a1.p1", "Comply.")],
    )["pt1.a1.p1"]
    assert basis.qc and basis.research
    assert _text(basis.qc)[0].startswith("Changed by a Final QC fix")
    assert _text(basis.research)[0].startswith("Basis: requirements research")


def test_the_newest_record_of_a_finding_speaks_once():
    section = _tree(("Comply.", ""))
    older = _fix_entry("pt1.a1.p1", "Comply.", title="Old title")
    newer = _fix_entry("pt1.a1.p1", "Comply.", title="New title")
    basis = redline_comment_basis(section, fix_log=[older, newer])["pt1.a1.p1"]
    heads = [line for line in _text(basis.qc) if line.startswith("Changed by")]
    assert len(heads) == 1 and "New title" in heads[0]


def test_a_fix_to_the_section_header_speaks_on_the_header_line_too():
    """The section's number and title are one element in the tree ("sec")
    but the upload's header line in the redline: a QC fix covering the
    header is said on both."""
    from backend.qc.apply import evidence_value
    from backend.qc.fix_log import normalized
    from backend.spec_doc.source_format import SECTION_TITLE_UID

    section = _tree(("Comply.", ""))
    entry = _fix_entry("pt1.a1.p1", "Comply.")
    entry["evidence"] = [
        {"key": ["field", "sec"], "value": normalized(evidence_value(section, ("field", "sec")))}
    ]
    bases = redline_comment_basis(section, fix_log=[entry])
    assert set(bases) == {"sec", SECTION_TITLE_UID}
    assert bases["sec"] == bases[SECTION_TITLE_UID]


def test_links_are_capped_non_web_links_stay_text_and_long_text_is_trimmed():
    sources = [
        {"url": f"https://s{n}.example/", "title": f"Source {n}"} for n in range(7)
    ] + [{"url": "javascript:alert(1)", "title": "x"}]
    section = _tree(("Comply.", ""))
    basis = redline_comment_basis(
        section,
        fix_log=[
            _fix_entry("pt1.a1.p1", "Comply.", sources=sources, issue="word " * 400)
        ],
    )["pt1.a1.p1"]
    lines = _text(basis.qc)
    assert len(_links(basis.qc)) == MAX_COMMENT_LINKS
    assert lines[-1] == f"+{len(sources) - MAX_COMMENT_LINKS} more"
    issue = lines[1]
    assert len(issue) <= MAX_COMMENT_TEXT and issue.endswith("…")
    # A non-web "link" is never a hyperlink, wherever it lands in the list.
    first_six = [
        {"url": "javascript:alert(1)", "title": "x"},
        {"url": "file:///C:/secret.docx", "title": "y"},
    ]
    basis = redline_comment_basis(
        section, fix_log=[_fix_entry("pt1.a1.p1", "Comply.", sources=first_six)]
    )["pt1.a1.p1"]
    assert _links(basis.qc) == []
    assert "javascript:alert(1)" in _text(basis.qc)
    assert trimmed("a  b\n c") == "a b c"


# ---------------------------------------------------------------------------
# The pass: placement, proof, package
# ---------------------------------------------------------------------------


def _render(source, imported, section, comments, *, native_moves=False):
    stats: dict = {}
    payload = render_preserving_redline(
        source_bytes=source,
        format_map=imported.format_map,
        baseline=imported.section,
        current=section,
        author=AUTHOR,
        date=DATE,
        stats=stats,
        native_moves=native_moves,
        comments=comments,
    )
    return payload, stats


def _comments_root(payload: bytes):
    members = _members(payload)
    data = members.get("word/comments.xml")
    return etree.fromstring(data) if data is not None else None


def _our_ids(payload: bytes, source: bytes) -> list[str]:
    root = _comments_root(payload)
    upload = _comments_root(source)
    theirs = (
        {c.get(qn("w:id")) for c in upload.iter(qn("w:comment"))}
        if upload is not None
        else set()
    )
    if root is None:
        return []
    return [
        c.get(qn("w:id"))
        for c in root.iter(qn("w:comment"))
        if c.get(qn("w:author")) == AUTHOR and c.get(qn("w:id")) not in theirs
    ]


def _comment_texts(payload: bytes, ids) -> dict[str, list[str]]:
    root = _comments_root(payload)
    out = {}
    for comment in root.iter(qn("w:comment")):
        if comment.get(qn("w:id")) in ids:
            out[comment.get(qn("w:id"))] = [
                "".join(t.text or "" for t in p.iter(qn("w:t")))
                for p in comment.iter(qn("w:p"))
            ]
    return out


def _stripped(body, ids):
    body = copy.deepcopy(body)
    strip_comment_anchors(body, ids)
    return body


def _anchored_ids(body) -> set[str]:
    return {r.get(qn("w:id")) for r in body.iter(qn("w:commentReference"))}


def _canonical(element) -> bytes:
    # Exclusive: a deep copy detached from its document keeps only the
    # namespaces it uses, so inclusive C14N would compare declarations.
    return etree.tostring(element, method="c14n", exclusive=True)


def _verify_commented(source, imported, section, comments, *, native_moves=False):
    """Render with comments and hold the whole promise. Returns the payload,
    the stats, and each of Build-a-Spec's comments as lines of text."""
    payload, stats = _render(
        source, imported, section, comments, native_moves=native_moves
    )
    plain, _ = _render(source, imported, section, None, native_moves=native_moves)
    body = _body(payload)
    ids = _our_ids(payload, source)
    stats_comments = stats["redline"]["comments"]
    assert stats_comments["fallback"] == "", stats_comments
    assert stats_comments["added"] == len(ids)
    assert set(stats_comments["skipped"]) <= COMMENT_SKIP_REASONS

    # Additive: removing exactly our anchors is the redline without comments.
    assert _canonical(_stripped(body, ids)) == _canonical(_body(plain))

    # Every anchor a direct child of a paragraph, never inside a revision,
    # each id once as start, end and reference, in that order.
    for comment_id in ids:
        nodes = [
            n for n in body.iter(*_ANCHORS) if n.get(qn("w:id")) == comment_id
        ]
        assert [n.tag for n in nodes] == list(_ANCHORS)
        for node in nodes:
            holder = node if node.tag != qn("w:commentReference") else node.getparent()
            assert holder.getparent().tag == qn("w:p")
            assert not any(a.tag in _WRAPPERS for a in node.iterancestors())

    # Accept All / Reject All: every comment stays anchored, and with our
    # anchors excluded the resolutions are exactly what they were.
    clean = render_preserving_docx(
        source_bytes=source, format_map=imported.format_map, current=section
    )
    accepted, rejected = accept_all(body), reject_all(body)
    assert set(ids) <= _anchored_ids(accepted)
    assert set(ids) <= _anchored_ids(rejected)
    assert first_difference(_stripped(accepted, ids), _body(clean)) is None
    moved = _inserted_bookmarks(body)
    assert (
        first_difference(
            _stripped(rejected, ids), _body(source), exclude_bookmarks=moved
        )
        is None
    )

    # The package: only the comment parts differ, and each by additions only.
    before, after = _members(source), _members(payload)
    assert list(after)[: len(before)] == list(before)
    for name in list(after)[len(before) :]:
        assert name in _COMMENT_PARTS
    for name, data in before.items():
        if name == "word/document.xml":
            continue
        if data == after[name]:
            continue
        assert name in _COMMENT_PARTS, name
        upload_root, written = etree.fromstring(data), etree.fromstring(after[name])
        for child in list(written):
            known = {_canonical(c) for c in upload_root}
            if _canonical(child) not in known:
                written.remove(child)
        assert _canonical(written) == _canonical(upload_root), name

    # Metadata: author, initials, date; ids above everything in the upload.
    if not ids:
        # Nothing had a basis: the pass wrote nothing at all.
        assert payload == plain
        return payload, stats, {}
    root = _comments_root(payload)
    for comment in root.iter(qn("w:comment")):
        if comment.get(qn("w:id")) in ids:
            assert comment.get(qn("w:author")) == AUTHOR
            assert comment.get(qn("w:initials")) == "BAS"
            assert comment.get(qn("w:date")) == DATE
    assert all(int(i) > _highest_id(source) for i in ids)
    return payload, stats, _comment_texts(payload, ids)


def _basis(*lines, qc=False) -> CommentBasis:
    paragraphs = tuple((line,) for line in lines)
    return CommentBasis(qc=paragraphs) if qc else CommentBasis(research=paragraphs)


def test_a_changed_provision_gets_its_comment_anchored_around_it(tmp_path):
    source = _master_bytes()
    imported = _parse(tmp_path, source)
    article = imported.section.parts[0].articles[0]
    section = _edit(
        imported.section,
        {
            "action": "replace",
            "target_id": article.paragraphs[0].uid,
            "text": "Section includes seismic restraints for mechanical equipment.",
        },
    )
    target = article.paragraphs[0].uid
    payload, stats, comments = _verify_commented(
        source, imported, section, {target: _basis("Basis: research r-1")}
    )
    assert list(comments.values()) == [["Basis: research r-1"]]
    assert stats["redline"]["comments"]["elements"] == 1
    # Placement: the range opens right after the paragraph's properties and
    # closes, with the reference run, at the paragraph's end.
    (start,) = _body(payload).iter(qn("w:commentRangeStart"))
    paragraph = start.getparent()
    children = [c for c in paragraph if isinstance(c.tag, str)]
    assert children[0].tag == qn("w:pPr") or children[0] is start
    if children[0].tag == qn("w:pPr"):
        assert children[1] is start
    assert children[-2].tag == qn("w:commentRangeEnd")
    assert children[-1].tag == qn("w:r")
    assert children[-1].find(qn("w:commentReference")) is not None
    # The new parts are registered.
    members = _members(payload)
    types = etree.fromstring(members["[Content_Types].xml"])
    overrides = {
        o.get("PartName"): o.get("ContentType")
        for o in types
        if o.tag.endswith("Override")
    }
    assert overrides["/word/comments.xml"] == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
    )
    rels = etree.fromstring(members["word/_rels/document.xml.rels"])
    assert [
        r.get("Target")
        for r in rels
        if r.get("Type") == redline_comments.COMMENTS_REL_TYPE
    ] == ["comments.xml"]


def test_no_basis_means_the_redline_without_comments_byte_for_byte(tmp_path):
    source = _master_bytes()
    imported = _parse(tmp_path, source)
    article = imported.section.parts[0].articles[0]
    section = _edit(
        imported.section,
        {"action": "replace", "target_id": article.paragraphs[0].uid, "text": "Reworded."},
    )
    with_pass, stats = _render(source, imported, section, {})
    without, _ = _render(source, imported, section, None)
    assert with_pass == without
    assert stats["redline"]["comments"] == {
        "added": 0,
        "elements": 0,
        "skipped": {"no_basis": 1},
        "fallback": "",
    }
    assert "word/comments.xml" not in _members(with_pass)


def test_consecutive_changes_with_one_basis_share_one_comment(tmp_path):
    source = _master_bytes()
    imported = _parse(tmp_path, source)
    article = imported.section.parts[0].articles[0]
    section = imported.section
    for position, text in ((0, "Provide spring isolators."), (1, "Provide snubbers.")):
        section = _edit(
            section,
            {
                "action": "add_paragraph",
                "target_id": article.uid,
                "position": position,
                "text": text,
            },
        )
    new = [p.uid for p in section.parts[0].articles[0].paragraphs[:2]]
    shared = _basis("Basis: attached document \"Owner Standard\".")
    _payload, stats, comments = _verify_commented(
        source, imported, section, {new[0]: shared, new[1]: shared}
    )
    assert len(comments) == 1  # one comment spanning both
    assert stats["redline"]["comments"]["elements"] == 2
    # Different bases: two comments.
    _payload, _stats, comments = _verify_commented(
        source,
        imported,
        section,
        {new[0]: shared, new[1]: _basis("Basis: research r-2")},
    )
    assert len(comments) == 2


def test_a_relettered_provision_is_not_a_change_of_substance(tmp_path):
    """Adding a provision at the top of a typed-letter article reletters the
    ones below it: the letters are tracked, but a relettered provision's
    words are what they were, so its research basis says nothing new."""
    source = _typed_letter_master()
    imported = _parse(tmp_path, source)
    article = imported.section.parts[0].articles[0]
    below = article.paragraphs[0].uid
    section = _edit(
        imported.section,
        {"action": "add_paragraph", "target_id": article.uid, "position": 0, "text": "New."},
    )
    new = section.parts[0].articles[0].paragraphs[0].uid
    _payload, stats, comments = _verify_commented(
        source,
        imported,
        section,
        {new: _basis("Basis: research r-new"), below: _basis("Basis: research r-old")},
    )
    assert list(comments.values()) == [["Basis: research r-new"]]
    assert stats["redline"]["comments"]["skipped"] == {}


@pytest.mark.parametrize(
    "master", [_numbered_master, _style_numbered_master], ids=["numbered", "styled"]
)
def test_word_numbered_masters_carry_comments_on_inserts_and_deletions(tmp_path, master):
    source = master()
    imported = _parse(tmp_path, source)
    article = imported.section.parts[0].articles[0]
    deleted = article.paragraphs[-1].uid
    section = _edit(
        imported.section,
        {"action": "delete", "target_id": deleted},
        {"action": "add_paragraph", "target_id": article.uid, "position": 0, "text": "Provide restraints."},
    )
    new = section.parts[0].articles[0].paragraphs[0].uid
    _payload, stats, comments = _verify_commented(
        source,
        imported,
        section,
        {new: _basis("Basis: research r-1"), deleted: _basis("Removed by QC", qc=True)},
    )
    assert sorted(lines[0] for lines in comments.values()) == [
        "Basis: research r-1",
        "Removed by QC",
    ]


def test_a_provision_holding_a_link_keeps_the_link_whole(tmp_path):
    source = _linked_master()
    imported = _parse(tmp_path, source)
    provision = imported.section.parts[0].articles[0].paragraphs[0]
    section = _edit(
        imported.section,
        {
            "action": "replace",
            "target_id": provision.uid,
            "text": provision.text.replace("vibration", "seismic"),
        },
    )
    payload, _stats, comments = _verify_commented(
        source, imported, section, {provision.uid: _basis("Basis: research r-9")}
    )
    assert len(comments) == 1
    links = list(_body(payload).iter(qn("w:hyperlink")))
    assert len(links) == 1


def test_a_native_move_carries_its_qc_comment_on_the_moved_copy(tmp_path):
    source = _numbered_family_master()
    imported = _parse(tmp_path, source)
    moved = _provisions(imported.section)[2]
    section = _edit(
        imported.section, {"action": "move", "target_id": moved.uid, "position": 0}
    )
    payload, stats, comments = _verify_commented(
        source,
        imported,
        section,
        {moved.uid: _basis("Moved by a QC fix", qc=True)},
        native_moves=True,
    )
    assert stats["redline"]["moves_native"] == 1
    assert list(comments.values()) == [["Moved by a QC fix"]]
    (start,) = _body(payload).iter(qn("w:commentRangeStart"))
    paragraph = start.getparent()
    # On the moved-HERE copy, and outside its move wrappers.
    assert paragraph.find(f"{qn('w:pPr')}/{qn('w:rPr')}/{qn('w:moveTo')}") is not None


def test_a_deleted_section_break_holder_is_commented_where_its_words_were(tmp_path):
    source = _break_master(held=True)
    imported = _parse(tmp_path, source)
    holder = imported.section.parts[0].articles[0].paragraphs[1]
    section = _edit(imported.section, {"action": "delete", "target_id": holder.uid})
    payload, _stats, comments = _verify_commented(
        source, imported, section, {holder.uid: _basis("Deleted by QC", qc=True)}
    )
    assert list(comments.values()) == [["Deleted by QC"]]
    (start,) = _body(payload).iter(qn("w:commentRangeStart"))
    assert start.getparent().find(f"{qn('w:pPr')}/{qn('w:sectPr')}") is not None


@pytest.mark.parametrize("change", ["delete", "append"])
def test_the_last_paragraph_carries_its_comment(tmp_path, change):
    source = _plain_last_master()
    imported = _parse(tmp_path, source)
    article = imported.section.parts[0].articles[0]
    if change == "delete":
        uid = article.paragraphs[-1].uid
        section = _edit(imported.section, {"action": "delete", "target_id": uid})
        basis = _basis("Deleted by QC", qc=True)
    else:
        section = _edit(
            imported.section,
            {"action": "add_paragraph", "target_id": article.uid, "text": "Appended."},
        )
        uid = section.parts[0].articles[0].paragraphs[-1].uid
        basis = _basis("Basis: research r-5")
    _payload, _stats, comments = _verify_commented(
        source, imported, section, {uid: basis}
    )
    assert len(comments) == 1


def test_a_locked_block_is_never_commented(tmp_path):
    source = _master_bytes()  # holds a schedule table
    imported = _parse(tmp_path, source)
    table = next(
        p
        for article in imported.section.parts[0].articles
        for p in article.paragraphs
        if p.locked
    )
    section = _edit(imported.section, {"action": "delete", "target_id": table.uid})
    payload, stats = _render(
        source, imported, section, {table.uid: _basis("Deleted by QC", qc=True)}
    )
    assert stats["redline"]["comments"]["skipped"] == {"locked": 1}
    assert stats["redline"]["comments"]["added"] == 0
    assert "word/comments.xml" not in _members(payload)


def _commented_master(*, modern: bool) -> bytes:
    """A master a reviewer already commented on — optionally with the parts
    Word 2013+ writes beside comments.xml."""
    document = Document()
    for line in ("SECTION 23 05 48", "VIBRATION CONTROLS", "PART 1 - GENERAL", "1.1 SUMMARY"):
        document.add_paragraph(line)
    first = document.add_paragraph("A. Section includes vibration isolation.")
    document.add_comment(first.runs, text="Firm: confirm scope.", author="Reviewer", initials="RV")
    document.add_paragraph("B. Related requirements.")
    document.add_paragraph("END OF SECTION")
    payload = _save(document)
    if not modern:
        return payload
    extras = {
        "word/commentsExtended.xml": (
            b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            b'<w15:commentsEx xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml">'
            b'<w15:commentEx w15:paraId="1A2B3C4D" w15:done="0"/></w15:commentsEx>',
            "application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtended+xml",
            "http://schemas.microsoft.com/office/2011/relationships/commentsExtended",
        ),
        "word/commentsIds.xml": (
            b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            b'<w16cid:commentsIds xmlns:w16cid="http://schemas.microsoft.com/office/word/2016/wordml/cid">'
            b'<w16cid:commentId w16cid:paraId="1A2B3C4D" w16cid:durableId="5E6F7A8B"/></w16cid:commentsIds>',
            "application/vnd.openxmlformats-officedocument.wordprocessingml.commentsIds+xml",
            "http://schemas.microsoft.com/office/2016/09/relationships/commentsIds",
        ),
        "word/people.xml": (
            b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            b'<w15:people xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml">'
            b'<w15:person w15:author="Reviewer"><w15:presenceInfo w15:providerId="None" w15:userId="Reviewer"/></w15:person></w15:people>',
            "application/vnd.openxmlformats-officedocument.wordprocessingml.people+xml",
            "http://schemas.microsoft.com/office/2011/relationships/people",
        ),
    }
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    types = etree.fromstring(members["[Content_Types].xml"])
    rels = etree.fromstring(members["word/_rels/document.xml.rels"])
    ct = "{http://schemas.openxmlformats.org/package/2006/content-types}Override"
    rel = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
    for number, (name, (data, content_type, rel_type)) in enumerate(extras.items()):
        members[name] = data
        etree.SubElement(types, ct, PartName="/" + name, ContentType=content_type)
        etree.SubElement(
            rels, rel, Id=f"rIdModern{number}", Type=rel_type, Target=name.split("/", 1)[1]
        )
    members["[Content_Types].xml"] = etree.tostring(types, xml_declaration=True, encoding="UTF-8", standalone=True)
    members["word/_rels/document.xml.rels"] = etree.tostring(rels, xml_declaration=True, encoding="UTF-8", standalone=True)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return out.getvalue()


@pytest.mark.parametrize("modern", [False, True], ids=["classic", "modern"])
def test_a_master_that_already_has_comments_keeps_them(tmp_path, modern):
    source = _commented_master(modern=modern)
    imported = _parse(tmp_path, source)
    article = imported.section.parts[0].articles[0]
    target = article.paragraphs[1].uid
    section = _edit(
        imported.section,
        {"action": "replace", "target_id": target, "text": "Related requirements are elsewhere."},
    )
    payload, _stats, comments = _verify_commented(
        source, imported, section, {target: _basis("Basis: research r-3")}
    )
    assert list(comments.values()) == [["Basis: research r-3"]]
    root = _comments_root(payload)
    authors = [c.get(qn("w:author")) for c in root.iter(qn("w:comment"))]
    assert authors == ["Reviewer", AUTHOR]  # theirs first, untouched
    before, after = _members(source), _members(payload)
    # The comments part is extended, never registered twice; the Word 2013+
    # parts are the upload's, byte for byte.
    assert list(after) == list(before)
    for name in ("word/commentsExtended.xml", "word/commentsIds.xml", "word/people.xml"):
        if modern:
            assert after[name] == before[name]
    assert after["word/_rels/document.xml.rels"] == before["word/_rels/document.xml.rels"]
    assert after["[Content_Types].xml"] == before["[Content_Types].xml"]


def test_link_safety_escaping_and_the_style_fallback(tmp_path):
    source = _master_bytes()
    imported = _parse(tmp_path, source)
    target = imported.section.parts[0].articles[0].paragraphs[0].uid
    section = _edit(
        imported.section,
        {"action": "replace", "target_id": target, "text": "Reworded provision."},
    )
    basis = CommentBasis(
        research=(
            ("Basis: <owner> & \x01 standard",),
            (CommentLink("javascript:alert(1)", "not a link"),),
            (CommentLink("https://ok.example/a?b=1&c=2", "a real link"),),
        )
    )
    payload, _stats, comments = _verify_commented(source, imported, section, {target: basis})
    (lines,) = comments.values()
    assert lines[0] == "Basis: <owner> & \\u0001 standard"  # visible, never dropped
    assert lines[1] == "not a link"
    root = _comments_root(payload)
    links = list(root.iter(qn("w:hyperlink")))
    assert len(links) == 1
    rels = etree.fromstring(_members(payload)["word/_rels/comments.xml.rels"])
    (rel,) = list(rels)
    assert rel.get("Target") == "https://ok.example/a?b=1&c=2"
    assert rel.get("TargetMode") == "External"
    assert links[0].get(f"{{{redline_comments.R_NS}}}id") == rel.get("Id")
    # This master defines no comment or hyperlink styles, so none is
    # referenced — the link is formatted directly — and styles.xml is untouched.
    # Word's comment look, as direct formatting: 10 pt text, an 8 pt mark
    # (in the comment and in the body), the link blue and underlined.
    assert not list(root.iter(qn("w:pStyle"))) and not list(root.iter(qn("w:rStyle")))
    link_properties = links[0].find(f"{qn('w:r')}/{qn('w:rPr')}")
    assert [c.tag for c in link_properties] == [
        qn("w:color"), qn("w:sz"), qn("w:szCs"), qn("w:u")
    ]
    sizes = lambda run: run.find(f"{qn('w:rPr')}/{qn('w:sz')}").get(qn("w:val"))  # noqa: E731
    (comment,) = root.iter(qn("w:comment"))
    mark_run = next(comment.iter(qn("w:annotationRef"))).getparent()
    assert sizes(mark_run) == "16"
    text_runs = [r for r in comment.iter(qn("w:r")) if r.find(qn("w:t")) is not None]
    assert text_runs and all(sizes(r) == "20" for r in text_runs)
    (reference,) = _body(payload).iter(qn("w:commentReference"))
    assert sizes(reference.getparent()) == "16"
    assert _members(payload)["word/styles.xml"] == _members(source)["word/styles.xml"]


def test_the_comment_styles_are_used_when_the_master_defines_them(tmp_path):
    document = Document()
    styles = document.styles
    from docx.enum.style import WD_STYLE_TYPE

    styles.add_style("annotation text", WD_STYLE_TYPE.PARAGRAPH)
    styles.add_style("annotation reference", WD_STYLE_TYPE.CHARACTER)
    for line in ("SECTION 23 05 48", "VIBRATION CONTROLS", "PART 1 - GENERAL", "1.1 SUMMARY"):
        document.add_paragraph(line)
    document.add_paragraph("A. Section includes vibration isolation.")
    document.add_paragraph("END OF SECTION")
    source = _save(document)
    imported = _parse(tmp_path, source)
    target = imported.section.parts[0].articles[0].paragraphs[0].uid
    section = _edit(
        imported.section, {"action": "replace", "target_id": target, "text": "Reworded."}
    )
    payload, _stats, _comments = _verify_commented(
        source, imported, section, {target: _basis("Basis: research r-1")}
    )
    root = _comments_root(payload)
    assert {e.get(qn("w:val")) for e in root.iter(qn("w:pStyle"))} == {"annotationtext"}
    reference_styles = {
        e.get(qn("w:val"))
        for e in _body(payload).iter(qn("w:rStyle"))
    }
    assert "annotationreference" in reference_styles
    # The styles carry the look, so no run in the comment sets a size.
    assert not list(root.iter(qn("w:sz")))
    assert _members(payload)["word/styles.xml"] == _members(source)["word/styles.xml"]


def _para(inner: str = "<w:r><w:t>words</w:t></w:r>"):
    return etree.fromstring(f'<w:p xmlns:w="{W}">{inner}</w:p>')


def test_the_body_check_refuses_anything_but_the_anchors():
    """The pass's own proof, on its own: removing exactly its anchors must
    give back the proved body — one changed character, a lost element or a
    foreign anchor left behind is refused."""
    styles = redline_comments._Styles()
    proved = [_para(), _para("<w:r><w:t>more</w:t></w:r>")]

    def commented():
        final = copy.deepcopy(proved)
        redline_comments._insert_anchors(final[0], final[1], "9", styles)
        return final

    redline_comments._check_body(commented(), proved, ["9"])
    changed = commented()
    changed[1].find(f"{qn('w:r')}/{qn('w:t')}").text = "mote"
    lost = commented()[:1]
    for final in (changed, lost):
        with pytest.raises(redline_comments.CommentPassError) as refused:
            redline_comments._check_body(final, proved, ["9"])
        assert refused.value.reason == "body_check"
    with pytest.raises(redline_comments.CommentPassError):
        redline_comments._check_body(commented(), proved, ["8"])  # not ours


def _comments_part(*ids):
    root = etree.fromstring(f'<w:comments xmlns:w="{W}"/>')
    for comment_id in ids:
        etree.SubElement(root, qn("w:comment")).set(qn("w:id"), comment_id)
    return root


def test_the_pairing_check_refuses_a_misplaced_or_unpaired_anchor():
    styles = redline_comments._Styles()
    good = [_para()]
    redline_comments._insert_anchors(good[0], good[0], "9", styles)
    redline_comments._check_pairing(good, _comments_part("9"), ["9"])

    inside = [_para('<w:ins w:id="1"><w:r><w:t>new</w:t></w:r></w:ins>')]
    redline_comments._insert_anchors(inside[0], inside[0], "9", styles)
    wrapper = inside[0].find(qn("w:ins"))
    wrapper.append(inside[0].find(qn("w:commentRangeEnd")))  # moved inside
    swapped = copy.deepcopy(good)
    start = swapped[0].find(qn("w:commentRangeStart"))
    swapped[0].append(start)  # the start after the reference
    for final, comments in (
        (inside, _comments_part("9")),
        (swapped, _comments_part("9")),
        (good, _comments_part()),  # no comment for the id
        (good, _comments_part("9", "9")),  # two comments for it
    ):
        with pytest.raises(redline_comments.CommentPassError) as refused:
            redline_comments._check_pairing(final, comments, ["9"])
        assert refused.value.reason == "pairing_check"


def test_the_part_check_allows_only_the_passs_own_additions():
    upload = f'<w:comments xmlns:w="{W}"><w:comment w:id="1"/></w:comments>'.encode()
    ours = lambda n: n.get(qn("w:id")) == "9"  # noqa: E731
    added = f'<w:comments xmlns:w="{W}"><w:comment w:id="1"/><w:comment w:id="9"/></w:comments>'
    redline_comments._check_part(upload, added.encode(), ours)
    redline_comments._check_part(None, f'<w:comments xmlns:w="{W}"><w:comment w:id="9"/></w:comments>'.encode(), ours)
    for upload_bytes, written in (
        (upload, f'<w:comments xmlns:w="{W}"><w:comment w:id="2"/><w:comment w:id="9"/></w:comments>'),
        (upload, f'<w:comments xmlns:w="{W}"><w:comment w:id="9"/></w:comments>'),
        (None, f'<w:comments xmlns:w="{W}"><w:comment w:id="1"/><w:comment w:id="9"/></w:comments>'),
    ):
        with pytest.raises(redline_comments.CommentPassError) as refused:
            redline_comments._check_part(upload_bytes, written.encode(), ours)
        assert refused.value.reason == "part_check"


@pytest.mark.parametrize(
    ("seam", "reason"),
    [
        ("body", "body_check"),
        ("error", "error"),
        ("package", "package_check"),
        ("tampered", "package_check"),
        ("parts", "parts_unreadable"),
    ],
)
def test_a_pass_that_cannot_prove_itself_hands_over_the_redline_without_comments(
    tmp_path, monkeypatch, seam, reason
):
    source = _master_bytes()
    imported = _parse(tmp_path, source)
    target = imported.section.parts[0].articles[0].paragraphs[0].uid
    section = _edit(
        imported.section, {"action": "replace", "target_id": target, "text": "Reworded."}
    )
    if seam == "body":

        def broken(final, proved, ids):
            raise redline_comments.CommentPassError("body_check")

        monkeypatch.setattr(redline_comments, "_check_body", broken)
    elif seam == "error":

        def boom(*args, **kwargs):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(redline_comments, "_insert_anchors", boom)
    elif seam == "package":
        from backend.spec_doc.raw_zip import RawZipError

        def refuse(*args, **kwargs):
            raise RawZipError("ambiguous")

        monkeypatch.setattr(source_render, "rewrite_raw_zip_members", refuse)
    elif seam == "tampered":
        # A package writer that wrote the wrong thing: the package audit is
        # what catches it.
        monkeypatch.setattr(
            source_render,
            "rewrite_raw_zip_members",
            lambda source_bytes, **_kwargs: source_bytes,
        )
    else:
        real = redline_comments._parse

        def unreadable(data):
            if data.lstrip().startswith(b"<?xml") and b"Types" in data[:400]:
                raise etree.XMLSyntaxError("broken", 1, 1, 1)
            return real(data)

        monkeypatch.setattr(redline_comments, "_parse", unreadable)
    payload, stats = _render(source, imported, section, {target: _basis("Basis: r-1")})
    plain, _ = _render(source, imported, section, None)
    assert payload == plain
    assert stats["redline"]["comments"]["fallback"] == reason
    assert reason in COMMENT_FALLBACK_REASONS
    assert stats["redline"]["comments"]["added"] == 0


# ---------------------------------------------------------------------------
# Through the app: the durable QC fix record, the capture, the switch
# ---------------------------------------------------------------------------

_FIXED_DATE = "2026-09-22T12:00:00Z"


@pytest.fixture
def client(monkeypatch):
    import backend.app as app_module

    monkeypatch.setattr(app_module, "_revision_timestamp", lambda: _FIXED_DATE)
    return TestClient(create_app())


def _export_redline(client):
    response = client.get(
        "/api/export/docx", params={"redline": "master", "mode": "preserved"}
    )
    assert response.status_code == 200, response.text
    return response.content


def _redline_comment_lines(payload: bytes) -> list[list[str]]:
    root = _comments_root(payload)
    if root is None:
        return []
    return [
        ["".join(t.text or "" for t in p.iter(qn("w:t"))) for p in c.iter(qn("w:p"))]
        for c in root.iter(qn("w:comment"))
        if c.get(qn("w:author")) == AUTHOR
    ]


def _install_fix(client, uid: str, ops: list[dict]):
    session = sessions.get_session()
    finding = _finding("qc-fix0000001", ops)
    finding.element_id = uid
    finding.accepted_sources = ["https://www.nfpa.org/13"]
    finding.source_checks = [
        QCSourceRecord(url="https://www.nfpa.org/13", title="NFPA 13", accepted=True)
    ]
    result = audit_grade_qc_result(session, [finding])
    session.qc.result = result
    session.qc.status = "complete"
    session.qc.latest_attempt_run_id = result.run_id
    session.qc.latest_attempt_status = "complete"
    session.qc.latest_attempt_result = result
    applied = client.post("/api/qc/apply", json={"finding_ids": [finding.finding_id]})
    assert applied.status_code == 200, applied.text
    assert applied.json()["outcomes"] == {finding.finding_id: "applied"}


def _first_provision(client) -> str:
    doc = client.get("/api/doc").json()["doc"]
    return doc["parts"][0]["articles"][0]["paragraphs"][0]["id"]


def test_a_qc_fix_is_commented_through_undo_redo_edit_and_review(client):
    _import(client, _master_bytes())
    uid = _first_provision(client)
    fixed = "Section includes seismic isolation for mechanical equipment."
    _install_fix(
        client,
        uid,
        [{"action": "replace", "target_id": uid, "text": fixed, "status": "confirmed"}],
    )
    (lines,) = _redline_comment_lines(_export_redline(client))
    assert lines[0].startswith("Changed by a Final QC fix, applied ")
    assert "Finding qc-fix0000001" in lines[0]
    assert lines[-2:] == ["Sources:", "NFPA 13"]

    # A re-run replaces the retained result: the record does not care.
    sessions.get_session().qc.result = None
    assert len(_redline_comment_lines(_export_redline(client))) == 1

    # Undo: the document no longer holds the remedy — no comment.
    assert client.post("/api/doc/undo").json()["ok"]
    assert _redline_comment_lines(_export_redline(client)) == []
    # Redo brings it back.
    assert client.post("/api/doc/redo").json()["ok"]
    assert len(_redline_comment_lines(_export_redline(client))) == 1

    # Confirming it in the review walk keeps the comment…
    assert client.post(
        "/api/doc/edit",
        json={"ops": [{"action": "set_status", "target_id": uid, "status": "assumed"}]},
    ).json()["ok"]
    assert len(_redline_comment_lines(_export_redline(client))) == 1
    # …editing it afterwards does not.
    assert client.post(
        "/api/doc/edit",
        json={"ops": [{"action": "replace", "target_id": uid, "text": "Reworded by hand."}]},
    ).json()["ok"]
    assert _redline_comment_lines(_export_redline(client)) == []


def test_a_deletion_fix_and_a_move_fix_are_commented(client):
    _import(client, _numbered_master())
    doc = client.get("/api/doc").json()["doc"]
    provisions = doc["parts"][0]["articles"][0]["paragraphs"]
    _install_fix(
        client,
        provisions[1]["id"],
        [{"action": "delete", "target_id": provisions[1]["id"]}],
    )
    (lines,) = _redline_comment_lines(_export_redline(client))
    assert lines[0].startswith("Changed by a Final QC fix")
    body = _body(_export_redline(client))
    (start,) = body.iter(qn("w:commentRangeStart"))
    paragraph = start.getparent()
    assert paragraph.find(f"{qn('w:pPr')}/{qn('w:rPr')}/{qn('w:del')}") is not None

    # A move fix: the comment is on the moved-here copy.
    client.post("/api/session/reset")
    _import(client, _numbered_master())
    doc = client.get("/api/doc").json()["doc"]
    moved = doc["parts"][0]["articles"][0]["paragraphs"][2]["id"]
    _install_fix(
        client, moved, [{"action": "move", "target_id": moved, "position": 0}]
    )
    (lines,) = _redline_comment_lines(_export_redline(client))
    assert lines[0].startswith("Changed by a Final QC fix")
    # Moved back by hand: the fix no longer holds where it put it.
    assert client.post(
        "/api/doc/edit",
        json={"ops": [{"action": "move", "target_id": moved, "position": 2}]},
    ).json()["ok"]
    assert _redline_comment_lines(_export_redline(client)) == []


def test_research_and_attached_bases_reach_the_route(client):
    _import(client, _master_bytes())
    session = sessions.get_session()
    session.research.profile_result = RequirementsProfile(
        items=[_item("r-route1", grounded=True, accepted=["https://codes.example/x"])]
    )
    doc = client.get("/api/doc").json()["doc"]
    article = doc["parts"][0]["articles"][0]
    assert client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "add_paragraph",
                    "target_id": article["id"],
                    "text": "Provide seismic bracing.",
                    "source_item_id": "r-route1",
                }
            ]
        },
    ).json()["ok"]
    (lines,) = _redline_comment_lines(_export_redline(client))
    assert lines[0] == "Basis: requirements research (item r-route1, researched 2026-09-01)"


def test_the_switch_is_read_per_request_and_off_is_todays_file(client, monkeypatch):
    _import(client, _master_bytes())
    session = sessions.get_session()
    session.research.profile_result = RequirementsProfile(
        items=[_item("r-sw", grounded=True, accepted=["https://codes.example/x"])]
    )
    doc = client.get("/api/doc").json()["doc"]
    article = doc["parts"][0]["articles"][0]
    client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "add_paragraph",
                    "target_id": article["id"],
                    "text": "Provide seismic bracing.",
                    "source_item_id": "r-sw",
                }
            ]
        },
    )
    on = _export_redline(client)
    assert "word/comments.xml" in _members(on)
    monkeypatch.setattr(settings, "REDLINE_COMMENTS", False)
    off = _export_redline(client)
    assert "word/comments.xml" not in _members(off)
    assert not list(_body(off).iter(qn("w:commentReference")))
    # Off is the redline without comments exactly: removing on's anchors
    # gives back off's body.
    ids = [
        c.get(qn("w:id")) for c in _comments_root(on).iter(qn("w:comment"))
    ]
    assert _canonical(_stripped(_body(on), ids)) == _canonical(_body(off))


def test_the_switch_ships_on():
    tree = ast.parse(pathlib.Path(settings.__file__).read_text(encoding="utf-8"))
    (value,) = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", "") == "REDLINE_COMMENTS" for t in node.targets)
    ]
    assert isinstance(value, ast.Call)
    assert value.args[0].value == "BUILD_A_SPEC_REDLINE_COMMENTS"
    assert value.args[1].value is True


def test_every_corpus_master_keeps_the_promise_with_comments(tmp_path):
    """Every corpus master under the sweep's edit mixes, with a basis on
    every element that has one to give: the promise holds with the anchors
    excluded, and the comment fallback never fires."""
    from backend.spec_doc.source_render import SourceRedlineError
    from tests.docx_corpus import build_case, corpus_cases

    added = 0
    for case in corpus_cases():
        source = build_case(case, tmp_path)
        imported = _parse(tmp_path, source, f"{case.case_id}.docx")
        uids = set(source_render._element_texts(imported.section))
        for seed in CORPUS_SWEEP_SEEDS:
            section = corpus_sweep_edits(case.case_id, imported.section, seed)
            every = uids | set(source_render._element_texts(section))
            # Two bases, alternating, so neighbours sometimes share one.
            bases = {
                uid: _basis(
                    f"Basis: research r-{len(uid) % 2}",
                    qc=bool(len(uid) % 3 == 0),
                )
                for uid in every
            }
            try:
                _payload, stats, _comments = _verify_commented(
                    source, imported, section, bases
                )
            except SourceRedlineError as exc:
                assert exc.reason in STRUCTURAL_REFUSALS, (case.case_id, exc.reason)
                continue
            added += stats["redline"]["comments"]["added"]
    assert added
