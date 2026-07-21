"""Unit tests for the document model: ids, transactions, versions, items."""
from __future__ import annotations

import pytest

from backend.spec_doc import DocumentStore, SpecEditError, open_questions, outline
from backend.spec_doc.model import SpecSection


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
