"""Version-diff engine units (Batch 5).

The diff is a pure function of two :class:`SpecSection` trees joined by
stable uid. Every ``changed`` element's runs must reconstruct both texts
byte-exactly; moves and status-only changes never produce redline marks.
"""
from __future__ import annotations

from backend.spec_doc.diffing import diff_sections, token_runs
from backend.spec_doc.model import DocumentStore, SpecSection


def _section(store: DocumentStore) -> SpecSection:
    return SpecSection.from_dict(store.doc.to_dict())


def _seed() -> DocumentStore:
    """A two-article base: SUMMARY (two paragraphs) + REFERENCES (one)."""
    store = DocumentStore()
    store.begin_turn()
    store.apply_edits(
        [
            {
                "action": "replace",
                "target_id": "sec",
                "text": "WET-PIPE SPRINKLER SYSTEMS",
                "numbering": "21 13 13",
            },
            {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
            {"action": "add_article", "target_id": "pt1", "text": "REFERENCES"},
        ]
    )
    store.commit_turn()
    summary = store.doc.parts[0].articles[0].uid
    references = store.doc.parts[0].articles[1].uid
    store.begin_turn()
    store.apply_edits(
        [
            {
                "action": "add_paragraph",
                "target_id": summary,
                "text": "Section includes wet-pipe sprinkler systems.",
                "status": "assumed",
            },
            {
                "action": "add_paragraph",
                "target_id": summary,
                "text": "Related sections: 21 13 19.",
                "status": "assumed",
            },
            {
                "action": "add_paragraph",
                "target_id": references,
                "text": "NFPA 13-2025.",
                "status": "confirmed",
            },
        ]
    )
    store.commit_turn()
    return store


def _first_article(store: DocumentStore) -> str:
    return store.doc.parts[0].articles[0].uid


def _paras(store: DocumentStore) -> list[str]:
    return [p.uid for p in store.doc.parts[0].articles[0].paragraphs]


def _assert_run_invariants(element) -> None:
    """A changed element's runs reconstruct both texts byte-exactly."""
    assert element.runs is not None
    accept = "".join(r.text for r in element.runs if r.op != "del")
    reject = "".join(r.text for r in element.runs if r.op != "ins")
    assert accept == element.cur_text
    assert reject == element.base_text


# ---------------------------------------------------------------------------


def test_identical_trees_have_zero_marks():
    store = _seed()
    base = _section(store)
    cur = _section(store)
    diff = diff_sections(base, cur)
    assert diff.stats["inserted"] == 0
    assert diff.stats["deleted"] == 0
    assert diff.stats["changed"] == 0
    assert diff.stats["unchanged"] > 0
    assert not diff.status_changes
    assert all(e.runs is None for e in diff.elements)
    assert all(e.kind == "unchanged" for e in diff.elements)


def test_pure_insert():
    store = _seed()
    base = _section(store)
    store.begin_turn()
    store.apply_edits(
        [
            {
                "action": "add_paragraph",
                "target_id": _first_article(store),
                "text": "Provide seismic bracing per NFPA 13.",
                "status": "assumed",
            }
        ]
    )
    store.commit_turn()
    diff = diff_sections(base, store.doc)
    assert diff.stats["inserted"] == 1
    assert diff.stats["deleted"] == 0
    assert diff.stats["changed"] == 0
    inserted = [e for e in diff.elements if e.kind == "inserted"]
    assert len(inserted) == 1
    assert inserted[0].node_type == "paragraph"
    assert inserted[0].cur_text == "Provide seismic bracing per NFPA 13."
    assert inserted[0].base_text == ""
    assert inserted[0].runs is None


def test_pure_delete_keeps_base_position():
    store = _seed()
    base = _section(store)
    first, second = _paras(store)
    store.begin_turn()
    store.apply_edits([{"action": "delete", "target_id": first}])
    store.commit_turn()
    diff = diff_sections(base, store.doc)
    assert diff.stats["deleted"] == 1
    kinds = [
        e.kind
        for e in diff.elements
        if e.node_type == "paragraph" and e.uid in (first, second)
    ]
    # Deleted element emitted before its surviving sibling (base order).
    assert kinds == ["deleted", "unchanged"]
    deleted = next(e for e in diff.elements if e.kind == "deleted")
    assert deleted.uid == first
    assert deleted.base_text == "Section includes wet-pipe sprinkler systems."


def test_text_edit_produces_minimal_word_runs():
    store = _seed()
    base = _section(store)
    first = _paras(store)[0]
    store.begin_turn()
    store.apply_edits(
        [
            {
                "action": "replace",
                "target_id": first,
                "text": "Section includes wet-pipe automatic sprinkler systems.",
            }
        ]
    )
    store.commit_turn()
    diff = diff_sections(base, store.doc)
    assert diff.stats["changed"] == 1
    changed = next(e for e in diff.elements if e.kind == "changed")
    assert changed.uid == first
    _assert_run_invariants(changed)
    # The diff is word-level and minimal: "sprinkler" already exists in the
    # base, so only "automatic" is a genuine insertion — no deletions.
    ins_text = "".join(r.text for r in changed.runs if r.op == "ins")
    assert ins_text.strip() == "automatic"
    assert not any(r.op == "del" for r in changed.runs)


def test_nested_paragraph_insert_and_delete():
    store = _seed()
    first = _paras(store)[0]
    # Give the first paragraph a child, snapshot as base, then delete it.
    store.begin_turn()
    store.apply_edits(
        [
            {
                "action": "add_paragraph",
                "target_id": first,
                "text": "Design density 0.20 gpm/sqft.",
                "status": "assumed",
            }
        ]
    )
    store.commit_turn()
    base = _section(store)
    child = store.doc.parts[0].articles[0].paragraphs[0].children[0].uid
    store.begin_turn()
    store.apply_edits(
        [
            {"action": "delete", "target_id": child},
            {
                "action": "add_paragraph",
                "target_id": first,
                "text": "Design area 1500 sqft.",
                "status": "assumed",
            },
        ]
    )
    store.commit_turn()
    diff = diff_sections(base, store.doc)
    deleted = [e for e in diff.elements if e.kind == "deleted"]
    inserted = [e for e in diff.elements if e.kind == "inserted"]
    assert len(deleted) == 1 and deleted[0].depth == 1
    assert len(inserted) == 1 and inserted[0].depth == 1


def test_changed_article_title():
    store = _seed()
    base = _section(store)
    article = _first_article(store)
    store.begin_turn()
    store.apply_edits(
        [{"action": "replace", "target_id": article, "text": "SCOPE"}]
    )
    store.commit_turn()
    diff = diff_sections(base, store.doc)
    changed = next(
        e for e in diff.elements if e.node_type == "article" and e.kind == "changed"
    )
    assert changed.base_text == "SUMMARY"
    assert changed.cur_text == "SCOPE"
    _assert_run_invariants(changed)


def test_moved_paragraph_produces_no_marks():
    store = _seed()
    base = _section(store)
    first, second = _paras(store)
    # Delete then re-add the SAME uids in swapped order? uids are stable and
    # never reused, so instead move by inserting a new paragraph at the top,
    # which shifts the survivors' positions without changing their uids.
    store.begin_turn()
    store.apply_edits(
        [
            {
                "action": "add_paragraph",
                "target_id": _first_article(store),
                "text": "General requirements apply.",
                "status": "assumed",
                "position": 0,
            }
        ]
    )
    store.commit_turn()
    diff = diff_sections(base, store.doc)
    # The two survivors moved down one slot but are unchanged (no marks); only
    # the new paragraph is inserted.
    survivors = [
        e for e in diff.elements if e.uid in (first, second)
    ]
    assert all(e.kind == "unchanged" for e in survivors)
    assert diff.stats["inserted"] == 1
    assert diff.stats["deleted"] == 0
    assert diff.stats["changed"] == 0


def test_status_only_change_lands_in_status_changes_not_elements():
    store = _seed()
    base = _section(store)
    first = _paras(store)[0]
    store.begin_turn()
    store.apply_edits(
        [{"action": "set_status", "target_id": first, "status": "confirmed"}]
    )
    store.commit_turn()
    diff = diff_sections(base, store.doc)
    assert diff.stats["changed"] == 0
    assert diff.stats["deleted"] == 0
    assert diff.stats["inserted"] == 0
    assert len(diff.status_changes) == 1
    change = diff.status_changes[0]
    assert change.uid == first
    assert change.status_base == "assumed"
    assert change.status_cur == "confirmed"
    # The element itself is still unchanged (no redline mark).
    element = next(e for e in diff.elements if e.uid == first)
    assert element.kind == "unchanged"


def test_section_header_number_and_title_change():
    store = _seed()
    base = _section(store)
    store.begin_turn()
    store.apply_edits(
        [
            {
                "action": "replace",
                "target_id": "sec",
                "text": "WET-PIPE AUTOMATIC SPRINKLER SYSTEMS",
                "numbering": "21 13 16",
            }
        ]
    )
    store.commit_turn()
    diff = diff_sections(base, store.doc)
    section = next(e for e in diff.elements if e.node_type == "section")
    assert section.kind == "changed"
    assert section.number_base == "21 13 13"
    assert section.number_cur == "21 13 16"
    _assert_run_invariants(section)


def test_diff_vs_empty_is_all_insertions():
    store = _seed()
    diff = diff_sections(SpecSection.empty(), store.doc)
    assert diff.stats["deleted"] == 0
    # Every article and paragraph is inserted; the section header changed
    # (empty -> filled).
    assert diff.stats["inserted"] == 5  # 2 articles + 3 paragraphs
    section = next(e for e in diff.elements if e.node_type == "section")
    assert section.kind == "changed"


def test_token_runs_reconstruct_both_texts_byte_exact():
    cases = [
        ("the quick brown fox", "the quick red fox"),
        ("a b c", "a b c d"),
        ("one two three four", "four three two one"),
        ("same text", "same text"),
        ("", "entirely new"),
        ("all removed", ""),
        ("keep  the  doubled  spaces", "keep  the  tripled   spaces"),
    ]
    for base, cur in cases:
        runs = token_runs(base, cur)
        accept = "".join(r.text for r in runs if r.op != "del")
        reject = "".join(r.text for r in runs if r.op != "ins")
        assert accept == cur, (base, cur, accept)
        assert reject == base, (base, cur, reject)
        # Adjacent same-op runs are merged; no empty runs.
        assert all(r.text for r in runs)
        for a, b in zip(runs, runs[1:]):
            assert a.op != b.op


def test_serialization_shape():
    store = _seed()
    base = _section(store)
    first = _paras(store)[0]
    store.begin_turn()
    store.apply_edits(
        [{"action": "replace", "target_id": first, "text": "Edited provision text."}]
    )
    store.commit_turn()
    data = diff_sections(base, store.doc).to_dict()
    assert set(data) == {"elements", "status_changes", "stats"}
    assert set(data["stats"]) == {"inserted", "deleted", "changed", "unchanged"}
    changed = next(e for e in data["elements"] if e["kind"] == "changed")
    assert isinstance(changed["runs"], list)
    assert {r["op"] for r in changed["runs"]} <= {"equal", "ins", "del"}
