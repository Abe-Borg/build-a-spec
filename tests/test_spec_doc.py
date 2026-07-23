"""Unit tests for the document model: ids, transactions, versions, items."""
from __future__ import annotations

import pytest

from backend.spec_doc import DocumentStore, SpecEditError, open_questions, outline
from backend.spec_doc.model import SpecSection, iter_paragraphs


def _store_with(edits: list[dict]) -> DocumentStore:
    store = DocumentStore()
    store.begin_turn()
    store.apply_edits(edits)
    store.commit_turn()
    return store


def test_ids_are_stable_across_deletes():
    store = DocumentStore()
    store.begin_turn()
    store.apply_edits(
        [
            {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
            {"action": "add_article", "target_id": "pt1", "text": "REFERENCES"},
        ]
    )
    store.apply_edits([{"action": "delete", "target_id": "pt1.a1"}])
    applied = store.apply_edits(
        [{"action": "add_article", "target_id": "pt1", "text": "SUBMITTALS"}]
    )
    store.commit_turn()

    # The deleted a1 is never reused; numbering renumbers by position.
    assert applied[0]["id"] == "pt1.a3"
    part = store.snapshot()["parts"][0]
    assert [a["id"] for a in part["articles"]] == ["pt1.a2", "pt1.a3"]
    assert [a["number"] for a in part["articles"]] == ["1.1", "1.2"]


def test_invalid_op_rolls_back_whole_batch():
    store = DocumentStore()
    store.begin_turn()
    with pytest.raises(SpecEditError):
        store.apply_edits(
            [
                {"action": "add_article", "target_id": "pt2", "text": "SPRINKLERS"},
                {"action": "replace", "target_id": "pt9.a1", "text": "nope"},
            ]
        )
    assert store.doc.is_empty()
    # Nothing dirty: committing records no version.
    assert store.commit_turn() is False
    assert len(store.versions) == 1


def test_paragraph_nesting_labels_and_depth_limit():
    store = DocumentStore()
    store.begin_turn()
    store.apply_edits(
        [
            {"action": "add_article", "target_id": "pt3", "text": "INSTALLATION"},
            {"action": "add_paragraph", "target_id": "pt3.a1", "text": "Level A"},
            {"action": "add_paragraph", "target_id": "pt3.a1.p1", "text": "Level 1"},
            {
                "action": "add_paragraph",
                "target_id": "pt3.a1.p1.p1",
                "text": "Level a",
            },
            {
                "action": "add_paragraph",
                "target_id": "pt3.a1.p1.p1.p1",
                "text": "Level 1)",
            },
        ]
    )
    with pytest.raises(SpecEditError, match="depth"):
        store.apply_edits(
            [
                {
                    "action": "add_paragraph",
                    "target_id": "pt3.a1.p1.p1.p1.p1",
                    "text": "too deep",
                }
            ]
        )
    store.commit_turn()

    article = store.snapshot()["parts"][2]["articles"][0]
    top = article["paragraphs"][0]
    assert top["label"] == "A."
    assert top["children"][0]["label"] == "1."
    assert top["children"][0]["children"][0]["label"] == "a."
    assert top["children"][0]["children"][0]["children"][0]["label"] == "1)"


def test_replace_section_header_and_paragraph_status():
    store = _store_with(
        [
            {
                "action": "replace",
                "target_id": "sec",
                "text": "Wet-Pipe Sprinkler Systems",
                "numbering": "21 13 13",
            },
            {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "text": "Density [TBD: value] applies.",
            },
        ]
    )
    assert store.doc.number == "21 13 13"
    # Omitted status defaults to assumed (over-flagging is the safe side).
    para = store.doc.parts[0].articles[0].paragraphs[0]
    assert para.status == "assumed"

    store.begin_turn()
    store.apply_edits(
        [
            {
                "action": "replace",
                "target_id": "pt1.a1.p1",
                "text": "Density 0.30 gpm/sq ft applies.",
                "status": "confirmed",
            }
        ]
    )
    store.commit_turn()
    para = store.doc.parts[0].articles[0].paragraphs[0]
    assert (para.text, para.status) == ("Density 0.30 gpm/sq ft applies.", "confirmed")
    assert open_questions(store.doc) == []


def test_position_inserts_before_siblings():
    store = _store_with(
        [
            {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
            {
                "action": "add_article",
                "target_id": "pt1",
                "text": "REFERENCES",
                "position": 0,
            },
        ]
    )
    part = store.snapshot()["parts"][0]
    assert [a["title"] for a in part["articles"]] == ["REFERENCES", "SUMMARY"]
    assert [a["number"] for a in part["articles"]] == ["1.1", "1.2"]


def _store_with_move_fixture() -> DocumentStore:
    return _store_with(
        [
            {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "text": "Top A",
            },
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "text": "Top B",
            },
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "text": "Top C",
            },
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1.p1",
                "text": "Nested 1",
            },
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1.p1",
                "text": "Nested 2",
            },
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1.p1",
                "text": "Nested 3",
            },
        ]
    )


def test_move_top_level_uses_final_index_in_both_directions_and_keeps_ids():
    store = _store_with_move_fixture()
    original_article = store.doc.parts[0].articles[0]
    original_ids = [paragraph.uid for paragraph in original_article.paragraphs]
    original_next_seq = original_article.next_seq

    store.begin_turn()
    moved_down = store.apply_edits(
        [{"action": "move", "target_id": "pt1.a1.p1", "position": 2}]
    )
    assert moved_down == [
        {
            "action": "move",
            "id": "pt1.a1.p1",
            "position": 2,
            "previous_position": 0,
        }
    ]
    article = store.doc.parts[0].articles[0]
    assert [paragraph.uid for paragraph in article.paragraphs] == [
        "pt1.a1.p2",
        "pt1.a1.p3",
        "pt1.a1.p1",
    ]

    moved_up = store.apply_edits(
        [{"action": "move", "target_id": "pt1.a1.p1", "position": 0}]
    )
    assert moved_up[0]["previous_position"] == 2
    article = store.doc.parts[0].articles[0]
    assert [paragraph.uid for paragraph in article.paragraphs] == original_ids
    assert article.next_seq == original_next_seq == 4

    added = store.apply_edits(
        [
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "text": "Top D",
            }
        ]
    )
    assert added[0]["id"] == "pt1.a1.p4"
    article = store.doc.parts[0].articles[0]
    assert article.next_seq == 5


def test_move_nested_siblings_keeps_parent_depth_ids_and_counter():
    store = _store_with_move_fixture()
    original_parent = store.doc.parts[0].articles[0].paragraphs[0]
    original_next_seq = original_parent.next_seq

    store.begin_turn()
    applied = store.apply_edits(
        [{"action": "move", "target_id": "pt1.a1.p1.p3", "position": 0}]
    )
    assert applied[0] == {
        "action": "move",
        "id": "pt1.a1.p1.p3",
        "position": 0,
        "previous_position": 2,
    }
    parent = store.doc.parts[0].articles[0].paragraphs[0]
    assert [child.uid for child in parent.children] == [
        "pt1.a1.p1.p3",
        "pt1.a1.p1.p1",
        "pt1.a1.p1.p2",
    ]
    assert parent.next_seq == original_next_seq == 4


def test_move_is_one_undoable_version_and_redo_restores_order():
    store = _store_with_move_fixture()
    original = [
        paragraph.uid for paragraph in store.doc.parts[0].articles[0].paragraphs
    ]

    store.begin_turn()
    store.apply_edits(
        [{"action": "move", "target_id": "pt1.a1.p3", "position": 0}]
    )
    assert store.commit_turn() is True
    moved = [
        paragraph.uid for paragraph in store.doc.parts[0].articles[0].paragraphs
    ]
    assert moved == ["pt1.a1.p3", "pt1.a1.p1", "pt1.a1.p2"]

    assert store.undo()
    assert [
        paragraph.uid for paragraph in store.doc.parts[0].articles[0].paragraphs
    ] == original
    assert store.redo()
    assert [
        paragraph.uid for paragraph in store.doc.parts[0].articles[0].paragraphs
    ] == moved


def test_invalid_move_rolls_back_the_whole_mixed_batch():
    store = _store_with_move_fixture()
    before = store.doc.to_dict()
    version_count = len(store.versions)

    store.begin_turn()
    with pytest.raises(SpecEditError, match="outside"):
        store.apply_edits(
            [
                {"action": "move", "target_id": "pt1.a1.p3", "position": 0},
                {"action": "move", "target_id": "pt1.a1.p1", "position": 9},
            ]
        )
    assert store.doc.to_dict() == before
    assert store.commit_turn() is False
    assert len(store.versions) == version_count


@pytest.mark.parametrize(
    ("op", "message"),
    [
        pytest.param(
            {"action": "move", "target_id": "pt1.a1.p1", "position": 0},
            "already at position",
            id="no-op",
        ),
        pytest.param(
            {"action": "move", "target_id": "pt1.a1.p1", "position": -1},
            "outside",
            id="negative-position",
        ),
        pytest.param(
            {"action": "move", "target_id": "pt1.a1.p1", "position": 3},
            "outside",
            id="position-equals-length",
        ),
        pytest.param(
            {"action": "move", "target_id": "pt1.a1.p1", "position": True},
            "integer",
            id="bool-position",
        ),
        pytest.param(
            {
                "action": "move",
                "target_id": "pt1.a1.p1",
                "position": 1,
                "parent_id": "pt2.a1",
            },
            "unsupported field",
            id="cross-parent-field",
        ),
        pytest.param(
            {"action": "move", "target_id": "pt1.a1", "position": 0},
            "paragraph id",
            id="article",
        ),
        pytest.param(
            {"action": "move", "target_id": "pt1", "position": 0},
            "paragraph id",
            id="part",
        ),
        pytest.param(
            {"action": "move", "target_id": "sec", "position": 0},
            "only supports 'replace'",
            id="section",
        ),
    ],
)
def test_move_rejects_invalid_or_non_paragraph_requests(op, message):
    store = _store_with_move_fixture()
    before = store.doc.to_dict()

    store.begin_turn()
    with pytest.raises(SpecEditError, match=message):
        store.apply_edits([op])
    assert store.doc.to_dict() == before
    assert store.commit_turn() is False


def test_open_questions_tracks_tbd_and_needs_input():
    store = _store_with(
        [
            {"action": "add_article", "target_id": "pt2", "text": "PIPING"},
            {
                "action": "add_paragraph",
                "target_id": "pt2.a1",
                "text": "Provide [TBD: pipe schedule] steel pipe.",
                "status": "confirmed",
            },
            {
                "action": "add_paragraph",
                "target_id": "pt2.a1",
                "text": "Joining method to be selected.",
                "status": "needs_input",
            },
        ]
    )
    items = open_questions(store.doc)
    kinds = {(i["kind"], i["ref"]) for i in items}
    assert kinds == {("tbd", "2.1.A"), ("needs_input", "2.1.B")}
    tbd = next(i for i in items if i["kind"] == "tbd")
    assert tbd["label"] == "pipe schedule"
    assert tbd["element_id"] == "pt2.a1.p1"


def test_undo_redo_and_redo_tail_truncation():
    store = DocumentStore()
    store.begin_turn()
    store.apply_edits([{"action": "add_article", "target_id": "pt1", "text": "ONE"}])
    store.commit_turn()
    store.begin_turn()
    store.apply_edits([{"action": "add_article", "target_id": "pt1", "text": "TWO"}])
    store.commit_turn()
    assert len(store.versions) == 3 and store.index == 2

    assert store.undo()
    assert [a.title for a in store.doc.parts[0].articles] == ["ONE"]
    assert store.can_redo()

    # A new turn after undo abandons the redo tail.
    store.begin_turn()
    store.apply_edits([{"action": "add_article", "target_id": "pt1", "text": "THREE"}])
    store.commit_turn()
    assert len(store.versions) == 3 and store.index == 2
    assert not store.can_redo()
    assert [a.title for a in store.doc.parts[0].articles] == ["ONE", "THREE"]

    assert store.undo() and store.undo()
    assert store.doc.is_empty()
    assert not store.undo()


def test_rollback_turn_restores_pre_turn_tree():
    store = _store_with(
        [{"action": "add_article", "target_id": "pt1", "text": "KEEP"}]
    )
    store.begin_turn()
    store.apply_edits([{"action": "add_article", "target_id": "pt1", "text": "DROP"}])
    store.rollback_turn()
    assert [a.title for a in store.doc.parts[0].articles] == ["KEEP"]
    assert len(store.versions) == 2  # no phantom version


def test_iter_paragraphs_document_order_is_the_review_queue_contract():
    """Batch 3, WI2: the review queue (frontend ``buildQueue``) is a straight
    port of ``iter_paragraphs`` document order. This pins that contract —
    parts, then articles, then nested paragraphs depth-first — with the human
    ``ref`` each entry carries, across mixed provenance statuses."""
    store = _store_with(
        [
            {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "text": "Imported provision.",
                "status": "imported",
            },
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "text": "Assumed provision.",
                "status": "assumed",
            },
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1.p2",
                "text": "Nested confirmed subparagraph.",
                "status": "confirmed",
            },
            {"action": "add_article", "target_id": "pt2", "text": "SPRINKLERS"},
            {
                "action": "add_paragraph",
                "target_id": "pt2.a1",
                "text": "Another assumed provision.",
                "status": "assumed",
            },
        ]
    )
    rows = [
        (ref, p.uid, p.status)
        for _part, _article, p, _depth, ref in iter_paragraphs(store.doc)
    ]
    # Document order, with the article number + stripped paragraph labels.
    assert [ref for ref, _uid, _status in rows] == [
        "1.1.A",
        "1.1.B",
        "1.1.B.1",
        "2.1.A",
    ]
    assert [status for _ref, _uid, status in rows] == [
        "imported",
        "assumed",
        "confirmed",
        "assumed",
    ]

    # The frontend "all" queue derives from this: reviewable statuses only
    # (imported / assumed), imported group first, each in document order.
    reviewable = [
        (ref, status) for ref, _uid, status in rows if status in ("imported", "assumed")
    ]
    all_order = [r for r in reviewable if r[1] == "imported"] + [
        r for r in reviewable if r[1] == "assumed"
    ]
    assert all_order == [("1.1.A", "imported"), ("1.1.B", "assumed"), ("2.1.A", "assumed")]


def test_outline_lists_ids_and_statuses():
    store = _store_with(
        [
            {"action": "replace", "target_id": "sec", "text": "T", "numbering": "21 13 13"},
            {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
            {"action": "add_paragraph", "target_id": "pt1.a1", "text": "Hello."},
        ]
    )
    text = outline(store.doc)
    assert "[id: pt1.a1]" in text
    assert "(assumed) Hello.  [id: pt1.a1.p1]" in text
    assert outline(SpecSection.empty()).startswith("(document is empty")


def test_store_load_rejects_malformed_history():
    store = DocumentStore()
    with pytest.raises(ValueError):
        store.load({"versions": [], "index": 0})
    with pytest.raises(ValueError):
        store.load({"versions": [{"bogus": True}], "index": 0})


def _forged_snapshot(mutate) -> dict:
    store = _store_with(
        [
            {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
            {"action": "add_paragraph", "target_id": "pt1.a1", "text": "Hello."},
        ]
    )
    snapshot = store.doc.to_dict()
    mutate(snapshot)
    return snapshot


def test_load_rejects_integrity_violations():
    store = DocumentStore()

    def dup_id(snap):  # second article reuses pt1.a1
        article = snap["parts"][0]["articles"][0]
        snap["parts"][0]["articles"].append(dict(article, paragraphs=[]))
        snap["parts"][0]["seq"] = 9

    def stale_seq(snap):  # counter behind existing child -> future id collision
        snap["parts"][0]["seq"] = 1

    def foreign_parent(snap):  # id not derived from its parent
        snap["parts"][0]["articles"][0]["id"] = "pt2.a1"

    def too_deep(snap):  # nesting beyond A./1./a./1)
        p = snap["parts"][0]["articles"][0]["paragraphs"][0]
        for i in range(4):
            child = {
                "id": f"{p['id']}.p1",
                "label": "1.",
                "text": "deep",
                "status": "assumed",
                "children": [],
                "seq": 2,
            }
            p["children"] = [child]
            p = child

    for mutate in (dup_id, stale_seq, foreign_parent, too_deep):
        snapshot = _forged_snapshot(mutate)
        with pytest.raises(ValueError, match="Malformed document data"):
            store.load({"versions": [snapshot], "index": 0})
        # The store never adopts any part of a rejected file.
        assert store.doc.is_empty()


def test_unknown_status_rejected():
    store = DocumentStore()
    store.begin_turn()
    with pytest.raises(SpecEditError, match="status"):
        store.apply_edits(
            [
                {"action": "add_article", "target_id": "pt1", "text": "A"},
                {
                    "action": "add_paragraph",
                    "target_id": "pt1.a1",
                    "text": "x",
                    "status": "definitely",
                },
            ]
        )
    assert store.doc.is_empty()


# ---------------------------------------------------------------------------
# Phase 3: set_standard_edition (jurisdiction edition overrides)
# ---------------------------------------------------------------------------


def _override_op(standard="NFPA 13", edition="2019", basis="2021 VCC per user"):
    op = {
        "action": "set_standard_edition",
        "target_id": "sec",
        "standard": standard,
    }
    if edition is not None:
        op["edition"] = edition
    if basis is not None:
        op["basis"] = basis
    return op


def test_set_standard_edition_records_normalized_override():
    store = DocumentStore()
    store.begin_turn()
    applied = store.apply_edits([_override_op(standard="nfpa  13")])
    store.commit_turn()
    assert applied == [
        {
            "action": "set_standard_edition",
            "id": "sec",
            "standard": "NFPA 13",
            "edition": "2019",
        }
    ]
    assert store.doc.edition_overrides == {
        "NFPA 13": {"edition": "2019", "basis": "2021 VCC per user"}
    }
    # Overrides count as document content.
    assert not store.doc.is_empty()


def test_set_standard_edition_requires_basis_and_sec_target():
    store = DocumentStore()
    store.begin_turn()
    with pytest.raises(SpecEditError, match="basis"):
        store.apply_edits([_override_op(basis="  ")])
    with pytest.raises(SpecEditError, match="'sec'"):
        store.apply_edits(
            [dict(_override_op(), target_id="pt1")]
        )
    with pytest.raises(SpecEditError, match="standard"):
        store.apply_edits([_override_op(standard="  ")])
    assert store.doc.edition_overrides == {}


def test_set_standard_edition_removal_and_unknown_removal():
    store = DocumentStore()
    store.begin_turn()
    store.apply_edits([_override_op()])
    applied = store.apply_edits([_override_op(edition="", basis=None)])
    assert applied[0]["removed"] is True
    assert store.doc.edition_overrides == {}
    with pytest.raises(SpecEditError, match="no override recorded"):
        store.apply_edits([_override_op(standard="NFPA 20", edition="")])


def test_overrides_ride_undo_redo_and_serialization():
    store = DocumentStore()
    store.begin_turn()
    store.apply_edits([_override_op()])
    store.commit_turn()

    assert store.undo()
    assert store.doc.edition_overrides == {}
    assert store.redo()
    assert store.doc.edition_overrides == {
        "NFPA 13": {"edition": "2019", "basis": "2021 VCC per user"}
    }

    # Round-trip through the persisted store shape.
    restored = DocumentStore()
    restored.load(store.to_dict())
    assert restored.doc.edition_overrides == store.doc.edition_overrides


def test_load_rejects_malformed_overrides():
    store = DocumentStore()
    snapshot = SpecSection.empty().to_dict()
    snapshot["edition_overrides"] = {"NFPA 13": {"edition": "2019"}}  # no basis
    with pytest.raises(ValueError, match="Malformed document data"):
        store.load({"versions": [snapshot], "index": 0})
    assert store.doc.is_empty()


# ---------------------------------------------------------------------------
# User-managed standards: added standards (title) + suppression
# ---------------------------------------------------------------------------


def test_add_standard_with_title_records_override():
    store = DocumentStore()
    store.begin_turn()
    store.apply_edits(
        [
            {
                "action": "set_standard_edition",
                "target_id": "sec",
                "standard": "nfpa 30",
                "edition": "2024",
                "basis": "on-site flammable storage",
                "title": "Flammable and Combustible Liquids Code",
            }
        ]
    )
    store.commit_turn()
    assert store.doc.edition_overrides["NFPA 30"] == {
        "edition": "2024",
        "basis": "on-site flammable storage",
        "title": "Flammable and Combustible Liquids Code",
    }


def _suppress_op(standard="NFPA 2001", suppressed=True, basis=None):
    op = {
        "action": "set_standard_suppressed",
        "target_id": "sec",
        "standard": standard,
        "suppressed": suppressed,
    }
    if basis is not None:
        op["basis"] = basis
    return op


def test_set_standard_suppressed_records_and_restores():
    store = DocumentStore()
    store.begin_turn()
    applied = store.apply_edits(
        [_suppress_op(standard="nfpa  2001", basis="no clean-agent system")]
    )
    assert applied == [
        {
            "action": "set_standard_suppressed",
            "id": "sec",
            "standard": "NFPA 2001",
            "suppressed": True,
        }
    ]
    assert store.doc.suppressed_standards == {"NFPA 2001": "no clean-agent system"}
    # An exclusion counts as document content.
    assert not store.doc.is_empty()

    restored = store.apply_edits([_suppress_op(suppressed=False)])
    assert restored[0]["restored"] is True
    assert store.doc.suppressed_standards == {}
    with pytest.raises(SpecEditError, match="no exclusion recorded"):
        store.apply_edits([_suppress_op(standard="NFPA 75", suppressed=False)])


def test_set_standard_suppressed_optional_reason_and_sec_target():
    store = DocumentStore()
    store.begin_turn()
    # Reason is optional — excluding a standard is a scope call, not an
    # edition change, so no basis is required.
    store.apply_edits([_suppress_op(basis=None)])
    assert store.doc.suppressed_standards == {"NFPA 2001": ""}
    with pytest.raises(SpecEditError, match="'sec'"):
        store.apply_edits([dict(_suppress_op(), target_id="pt1")])
    with pytest.raises(SpecEditError, match="standard"):
        store.apply_edits([_suppress_op(standard="  ")])
    with pytest.raises(SpecEditError, match="boolean"):
        store.apply_edits(
            [
                {
                    "action": "set_standard_suppressed",
                    "target_id": "sec",
                    "standard": "NFPA 20",
                    "suppressed": "yes",
                }
            ]
        )


def test_suppressed_ride_undo_redo_and_serialization():
    store = DocumentStore()
    store.begin_turn()
    store.apply_edits([_suppress_op(basis="no clean-agent system")])
    store.commit_turn()

    assert store.undo()
    assert store.doc.suppressed_standards == {}
    assert store.redo()
    assert store.doc.suppressed_standards == {"NFPA 2001": "no clean-agent system"}

    # Round-trip through the persisted store shape.
    restored = DocumentStore()
    restored.load(store.to_dict())
    assert restored.doc.suppressed_standards == store.doc.suppressed_standards


def test_load_rejects_malformed_suppressed():
    store = DocumentStore()
    snapshot = SpecSection.empty().to_dict()
    snapshot["suppressed_standards"] = {"NFPA 2001": 3}  # non-string reason
    with pytest.raises(ValueError, match="Malformed document data"):
        store.load({"versions": [snapshot], "index": 0})
    assert store.doc.is_empty()


# ---------------------------------------------------------------------------
# Redline baseline bookkeeping (Batch 5)
# ---------------------------------------------------------------------------


def _importable_section() -> SpecSection:
    section = SpecSection.empty()
    section.number = "21 13 13"
    section.title = "WET-PIPE SPRINKLER SYSTEMS"
    return section


def test_adopt_import_sets_baseline_index():
    store = DocumentStore()
    assert store.baseline_index is None
    store.adopt_imported(_importable_section())
    assert store.index == 1 and store.baseline_index == 1


def test_baseline_survives_forward_edits_and_save_load():
    store = DocumentStore()
    store.adopt_imported(_importable_section())
    # Editing forward keeps the baseline pointing at the master version.
    store.begin_turn()
    store.apply_edits([{"action": "add_article", "target_id": "pt1", "text": "SCOPE"}])
    store.commit_turn()
    assert store.index == 2 and store.baseline_index == 1

    data = store.to_dict()
    assert data["baseline_index"] == 1
    restored = DocumentStore()
    restored.load(data)
    assert restored.baseline_index == 1


def test_baseline_absence_and_out_of_range_degrade_to_none():
    store = DocumentStore()
    store.adopt_imported(_importable_section())
    data = store.to_dict()
    # Old project files predate baseline_index -> None (not a load failure).
    legacy = {"versions": data["versions"], "index": data["index"]}
    restored = DocumentStore()
    restored.load(legacy)
    assert restored.baseline_index is None
    # An out-of-range value also degrades to None.
    bad = {**data, "baseline_index": 99}
    restored2 = DocumentStore()
    restored2.load(bad)
    assert restored2.baseline_index is None


def test_baseline_dropped_when_its_version_is_truncated():
    store = DocumentStore()
    store.adopt_imported(_importable_section())  # index 1, baseline 1
    assert store.undo()  # back to the empty version 0
    # A new edit after undo truncates version 1 (the master) — the baseline
    # no longer exists, so the marker must clear.
    store.begin_turn()
    store.apply_edits([{"action": "add_article", "target_id": "pt1", "text": "NEW"}])
    store.commit_turn()
    assert store.baseline_index is None


def test_reset_clears_baseline():
    store = DocumentStore()
    store.adopt_imported(_importable_section())
    store.reset()
    assert store.baseline_index is None
