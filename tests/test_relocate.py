"""A relocation lands whole or not at all.

Step 5 of the prompt-review follow-up (an outside review Abraham forwarded,
checked against the code). ``move`` only reorders siblings, because an id
names its parent. So the writing policy (version 1) told the model to
relocate a provision by adding it at its new place and deleting the
original, then "re-add its subparagraphs under the returned id in the same
turn". The delete took the subparagraphs with it in the first batch, and
they came back only in a later one. A Stop or a cut-off reply between the
two commits what already landed (by design), and so does a model that
forgets the second step: the obligations under the provision are gone.

Now ``relocate`` moves an article or paragraph and everything under it to a
new parent in ONE operation, inside the batch's one transaction. The copy is
built before the original is removed, carrying text, status and source link;
ids are new (``id_map``), reason trails follow, and the tree records where
each element went (``relocations``), so a Waiting on you item linked to the
provision still finds it, through undo and redo too. The writing policy (version 2) and the tool
guide say to use it.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend import sessions, writing_policy
from backend.app import create_app
from backend.llm.prompts import render_system_prompt
from backend.qc.schema import QC_OP_ACTIONS
from backend.spec_doc.diffing import diff_sections
from backend.spec_doc.docx_export import build_docx
from backend.spec_doc.model import (
    APPLY_SPEC_EDITS_TOOL,
    MAX_PARAGRAPH_DEPTH,
    SpecEditError,
    SpecSection,
    apply_edits,
    resolve_relocated,
)
from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE
from tests.fakes import FakeClient, text_turn, tool_turn


def _seed() -> SpecSection:
    """PART 1 article with a three-level provision; PART 3 article."""
    section, _ = apply_edits(
        SpecSection.empty(),
        [
            {"action": "add_article", "target_id": "pt1", "text": "GENERAL"},
            {"action": "add_article", "target_id": "pt1", "text": "SUBMITTALS"},
            {"action": "add_article", "target_id": "pt3", "text": "FIELD QUALITY CONTROL"},
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "text": "Hydrostatically test the system at 200 psi for 2 hours.",
                "status": "assumed",
                "source_item_id": "r-1a2b3c4d5e6f",
                "reason": "NFPA 13 acceptance test",
            },
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1.p1",
                "text": "Notify the authority having jurisdiction 48 hours before the test.",
                "status": "confirmed",
                "reason": "user asked for AHJ notice",
            },
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1.p1.p1",
                "text": "Give the notice in writing.",
                "reason": "user said written notice",
            },
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "text": "Comply with NFPA 13.",
                "reason": "governing standard",
            },
        ],
    )
    return section


def _texts(paragraphs) -> list:
    return [(p.text, p.status, p.source_item_id, _texts(p.children)) for p in paragraphs]


# ---------------------------------------------------------------------------
# The operation
# ---------------------------------------------------------------------------


def test_a_paragraph_moves_to_another_part_with_everything_under_it():
    section = _seed()
    before = _texts(section.parts[0].articles[0].paragraphs[:1])
    moved, applied = apply_edits(
        section,
        [
            {
                "action": "relocate",
                "target_id": "pt1.a1.p1",
                "parent_id": "pt3.a1",
                "reason": "testing belongs in PART 3",
            }
        ],
    )
    record = applied[0]
    assert record["action"] == "relocate"
    assert record["id"] == "pt3.a1.p1"
    assert record["previous_id"] == "pt1.a1.p1"
    assert record["parent_id"] == "pt3.a1"
    assert record["id_map"] == {
        "pt1.a1.p1": "pt3.a1.p1",
        "pt1.a1.p1.p1": "pt3.a1.p1.p1",
        "pt1.a1.p1.p1.p1": "pt3.a1.p1.p1.p1",
    }
    # Words, statuses, source links and order travel unchanged.
    assert _texts(moved.parts[2].articles[0].paragraphs) == before
    # The original is gone; its sibling stays.
    assert [p.text for p in moved.parts[0].articles[0].paragraphs] == ["Comply with NFPA 13."]
    # The tree is valid (ids, counters) and survives a save and load.
    reloaded = SpecSection.from_dict(moved.to_dict())
    assert reloaded.to_dict() == moved.to_dict()
    # The input is untouched (apply_edits works on a copy).
    assert section.parts[0].articles[0].paragraphs[0].children


def test_an_article_moves_to_another_part_with_its_paragraphs():
    section = _seed()
    moved, applied = apply_edits(
        section,
        [
            {
                "action": "relocate",
                "target_id": "pt1.a1",
                "parent_id": "pt3",
                "position": 0,
                "reason": "the test article belongs in PART 3",
            }
        ],
    )
    assert applied[0]["id"] == "pt3.a2"
    assert [a.title for a in moved.parts[2].articles] == [
        "GENERAL",
        "FIELD QUALITY CONTROL",
    ]
    assert [a.title for a in moved.parts[0].articles] == ["SUBMITTALS"]
    assert _texts(moved.parts[2].articles[0].paragraphs) == _texts(
        section.parts[0].articles[0].paragraphs
    )
    assert applied[0]["id_map"]["pt1.a1.p1.p1.p1"] == "pt3.a2.p1.p1.p1"
    SpecSection.from_dict(moved.to_dict())


def test_a_paragraph_nests_under_another_paragraph_at_a_position():
    section = _seed()
    moved, applied = apply_edits(
        section,
        [
            {
                "action": "relocate",
                "target_id": "pt1.a1.p2",
                "parent_id": "pt1.a1.p1",
                "position": 0,
                "reason": "the standard applies to the test",
            }
        ],
    )
    first = moved.parts[0].articles[0].paragraphs[0]
    assert [c.text for c in first.children] == [
        "Comply with NFPA 13.",
        "Notify the authority having jurisdiction 48 hours before the test.",
    ]
    assert applied[0]["id"] == "pt1.a1.p1.p2"
    SpecSection.from_dict(moved.to_dict())


def test_new_ids_never_reuse_old_ones():
    section = _seed()
    once, _ = apply_edits(
        section,
        [{"action": "relocate", "target_id": "pt1.a1.p1", "parent_id": "pt3.a1", "reason": "x"}],
    )
    twice, applied = apply_edits(
        once,
        [{"action": "relocate", "target_id": "pt3.a1.p1", "parent_id": "pt1.a1", "reason": "x"}],
    )
    # pt1.a1 handed out p1 and p2 before; the way back is p3.
    assert applied[0]["id"] == "pt1.a1.p3"


@pytest.mark.parametrize(
    "op, message",
    [
        ({"target_id": "pt1.a1.p1"}, "'parent_id' (the new parent's id) is required"),
        ({"target_id": "pt1.a1.p1", "parent_id": "pt9.a1"}, "no element with id 'pt9.a1'"),
        ({"target_id": "pt1.a1.p1", "parent_id": "pt1.a1"}, "use move to reorder it there"),
        ({"target_id": "pt1.a1", "parent_id": "pt1"}, "use move to reorder it there"),
        ({"target_id": "pt1.a1.p1", "parent_id": "pt1.a1.p1.p1"}, "cannot move inside itself"),
        ({"target_id": "pt1.a1.p1", "parent_id": "pt1.a1.p1"}, "cannot move inside itself"),
        ({"target_id": "pt1.a1.p1", "parent_id": "pt3"}, "must be an article id"),
        ({"target_id": "pt1.a1", "parent_id": "pt3.a1"}, "must be a part id"),
        ({"target_id": "pt1", "parent_id": "pt3"}, "Sections and parts cannot be relocated"),
        (
            {"target_id": "pt1.a1.p1", "parent_id": "pt3.a1", "text": "new words"},
            "unsupported field(s): text",
        ),
        (
            {"target_id": "pt1.a1.p1", "parent_id": "pt3.a1", "status": "confirmed"},
            "unsupported field(s): status",
        ),
    ],
)
def test_a_bad_relocation_is_refused_with_the_way_out(op, message):
    with pytest.raises(SpecEditError) as excinfo:
        apply_edits(_seed(), [{"action": "relocate", "reason": "x", **op}])
    assert message in str(excinfo.value)


def test_depth_counts_the_whole_subtree():
    section = _seed()
    # Build a chain under SUBMITTALS that ends one level short of the limit.
    ops = [{"action": "add_paragraph", "target_id": "pt1.a2", "text": "Level 0.", "reason": "x"}]
    parent = "pt1.a2.p1"
    for level in range(1, MAX_PARAGRAPH_DEPTH - 1):
        ops.append({"action": "add_paragraph", "target_id": parent, "text": f"Level {level}.", "reason": "x"})
        parent += ".p1"
    section, _ = apply_edits(section, ops)
    # The test provision is three levels tall. Under "Level 2" (depth 2) it
    # would need depths 3, 4 and 5 — one past the deepest, MAX - 1.
    with pytest.raises(SpecEditError) as excinfo:
        apply_edits(
            section,
            [{"action": "relocate", "target_id": "pt1.a1.p1", "parent_id": "pt1.a2.p1.p1.p1", "reason": "x"}],
        )
    assert "deeper than the maximum nesting depth" in str(excinfo.value)
    # Under "Level 1" it fits exactly.
    moved, _ = apply_edits(
        section,
        [{"action": "relocate", "target_id": "pt1.a1.p1", "parent_id": "pt1.a2.p1.p1", "reason": "x"}],
    )
    SpecSection.from_dict(moved.to_dict())


def test_preserved_content_cannot_be_relocated():
    section = _seed()
    section.parts[0].articles[0].paragraphs[0].children[0].locked = "table"
    with pytest.raises(SpecEditError) as excinfo:
        apply_edits(
            section,
            [{"action": "relocate", "target_id": "pt1.a1.p1", "parent_id": "pt3.a1", "reason": "x"}],
        )
    assert "preserved content, which cannot be relocated" in str(excinfo.value)
    with pytest.raises(SpecEditError) as excinfo:
        apply_edits(
            section,
            [{"action": "relocate", "target_id": "pt1.a1.p2", "parent_id": "pt1.a1.p1.p1", "reason": "x"}],
        )
    assert "cannot hold nested provisions" in str(excinfo.value)


def test_a_failing_batch_leaves_the_provision_where_it_was():
    """The guarantee the two-batch recipe lacked: all or nothing."""
    section = _seed()
    with pytest.raises(SpecEditError):
        apply_edits(
            section,
            [
                {"action": "relocate", "target_id": "pt1.a1.p1", "parent_id": "pt3.a1", "reason": "x"},
                {"action": "delete", "target_id": "pt9.a9", "reason": "x"},
            ],
        )
    assert section.parts[0].articles[0].paragraphs[0].children[0].children
    assert section.parts[2].articles[0].paragraphs == []


# ---------------------------------------------------------------------------
# Reasons, the redline and follow-ups
# ---------------------------------------------------------------------------


def test_each_element_keeps_its_reasons_and_gains_the_relocation():
    moved, _ = apply_edits(
        _seed(),
        [{"action": "relocate", "target_id": "pt1.a1.p1", "parent_id": "pt3.a1", "reason": "testing belongs in PART 3"}],
    )
    assert moved.edit_reasons["pt3.a1.p1"] == ["NFPA 13 acceptance test", "testing belongs in PART 3"]
    assert moved.edit_reasons["pt3.a1.p1.p1.p1"] == ["user said written notice", "testing belongs in PART 3"]
    # The deleted originals say why too: the redline comments on them.
    assert moved.edit_reasons["pt1.a1.p1"][-1] == "testing belongs in PART 3"


def test_a_hand_relocation_drops_the_assistants_reasons():
    """The panel's own edits carry no reason; the place is the user's now."""
    moved, applied = apply_edits(
        _seed(),
        [{"action": "relocate", "target_id": "pt1.a1.p1", "parent_id": "pt3.a1"}],
    )
    assert "reason" not in applied[0]
    assert "pt3.a1.p1" not in moved.edit_reasons
    assert "pt1.a1.p1" not in moved.edit_reasons


def test_the_redline_shows_a_deletion_and_an_insertion():
    section = _seed()
    moved, _ = apply_edits(
        section,
        [{"action": "relocate", "target_id": "pt1.a1.p1", "parent_id": "pt3.a1", "reason": "x"}],
    )
    diff = diff_sections(section, moved)
    kinds = {(e.uid, e.kind) for e in diff.elements if e.kind in ("inserted", "deleted")}
    assert ("pt1.a1.p1.p1.p1", "deleted") in kinds
    assert ("pt3.a1.p1.p1.p1", "inserted") in kinds
    assert build_docx(moved, redline=diff)


def _seed_session():
    """The seeded document as a committed version (so undo has one to step
    back to), plus a Waiting on you item linked to a subparagraph."""
    session = sessions.get_session()
    session.doc.begin_turn()
    session.doc.doc = _seed()
    session.doc.commit_turn()
    item, _ = session.followups.add(
        {
            "kind": "question",
            "title": "Which test pressure does the owner require?",
            "element_id": "pt1.a1.p1.p1",
        }
    )
    return session, item


_RELOCATE = {
    "edits": [
        {
            "action": "relocate",
            "target_id": "pt1.a1.p1",
            "parent_id": "pt3.a1",
            "reason": "testing belongs in PART 3",
        }
    ]
}


def _chat(fake, monkeypatch, message="go") -> list[dict]:
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    body = TestClient(create_app()).post("/api/chat", json={"message": message}).text
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


def _context_text(request: dict) -> str:
    newest = request["messages"][-1]["content"]
    return " ".join(b.get("text", "") for b in newest if isinstance(b, dict))


def test_the_tree_records_where_each_element_went():
    moved, applied = apply_edits(_seed(), _RELOCATE["edits"])
    assert moved.relocations == applied[0]["id_map"]
    assert resolve_relocated(moved, "pt1.a1.p1.p1") == "pt3.a1.p1.p1"
    assert resolve_relocated(moved, "pt1.a1.p2") == "pt1.a1.p2"
    # A second relocation chains.
    again, _ = apply_edits(
        moved,
        [{"action": "relocate", "target_id": "pt3.a1.p1.p1", "parent_id": "pt1.a2", "reason": "x"}],
    )
    assert resolve_relocated(again, "pt1.a1.p1.p1") == "pt1.a2.p1"
    # Saved and loaded; a document never relocated carries no key.
    assert SpecSection.from_dict(again.to_dict()).relocations == again.relocations
    assert "relocations" not in _seed().to_dict()


@pytest.mark.parametrize(
    "bad",
    [["pt1.a1.p1"], {"": "pt3.a1.p1"}, {"pt1.a1.p1": ""}, {"pt1.a1.p1": "pt1.a1.p1"}, {"pt1.a1.p1": 3}],
)
def test_a_malformed_relocation_map_is_refused_on_load(bad):
    data = _seed().to_dict()
    data["relocations"] = bad
    with pytest.raises(ValueError):
        SpecSection.from_dict(data)


def test_a_template_forgets_the_relocations():
    from backend.templates import _canonical_document

    moved, _ = apply_edits(_seed(), _RELOCATE["edits"])
    assert "relocations" in moved.to_dict()
    assert "relocations" not in _canonical_document(moved.to_dict(), rebase_statuses=False)


def test_a_waiting_question_follows_its_provision(monkeypatch):
    """The link is never rewritten; the next turn's context reads it through
    the document's relocations."""
    session, item = _seed_session()
    fake = FakeClient(
        [
            tool_turn([], _RELOCATE, tool_id="toolu_relocate"),
            text_turn(["Moved the hydrostatic test to PART 3."]),
            text_turn(["Which pressure does the owner want?"]),
        ]
    )
    events = _chat(fake, monkeypatch)
    assert events[-1]["type"] == "turn_complete"
    patch = next(e for e in events if e["type"] == "doc_patch")
    assert patch["ops"][0]["id_map"]["pt1.a1.p1.p1"] == "pt3.a1.p1.p1"
    assert patch["doc"]["relocations"]["pt1.a1.p1.p1"] == "pt3.a1.p1.p1"
    result = session.history[2]["content"][0]
    assert "is_error" not in result
    assert '"id_map"' in result["content"]
    assert session.followups.get(item.fid).element_id == "pt1.a1.p1.p1"

    events = _chat(fake, monkeypatch, "next")
    assert events[-1]["type"] == "turn_complete"
    context = _context_text(fake.messages.last_request)
    assert "Relates to element pt3.a1.p1.p1." in context
    assert "Relates to element pt1.a1.p1.p1." not in context


def test_undo_and_redo_keep_the_link_right(monkeypatch):
    """Codex review on PR #311: a link rewritten when the provision moved
    would point at an id the undone document no longer has. The map rides
    the document's versions instead."""
    session, item = _seed_session()
    fake = FakeClient(
        [tool_turn([], _RELOCATE), text_turn(["Moved."]), text_turn(["Ok."]), text_turn(["Ok."])]
    )
    _chat(fake, monkeypatch)
    client = TestClient(create_app())
    assert client.post("/api/doc/undo").status_code == 200
    doc = session.doc.doc
    assert doc.relocations == {}
    assert resolve_relocated(doc, item.element_id) == "pt1.a1.p1.p1"
    _chat(fake, monkeypatch, "after undo")
    assert "Relates to element pt1.a1.p1.p1." in _context_text(fake.messages.last_request)

    assert client.post("/api/doc/redo").status_code == 200
    _chat(fake, monkeypatch, "after redo")
    assert "Relates to element pt3.a1.p1.p1." in _context_text(fake.messages.last_request)


def test_a_rolled_back_turn_puts_the_provision_back(monkeypatch):
    session, item = _seed_session()
    fake = FakeClient([tool_turn([], _RELOCATE), RuntimeError("provider fell over")])
    events = _chat(fake, monkeypatch)
    assert events[-1]["type"] == "error"
    assert session.doc.doc.relocations == {}
    assert session.doc.doc.parts[0].articles[0].paragraphs[0].uid == "pt1.a1.p1"
    assert resolve_relocated(session.doc.doc, item.element_id) == "pt1.a1.p1.p1"


# ---------------------------------------------------------------------------
# What the model is told
# ---------------------------------------------------------------------------


def test_the_writing_policy_relocates_in_one_operation():
    # The relocation rule arrived in version 2; the exact version is pinned
    # with its hash in tests/test_writing_policy.py.
    assert writing_policy.WRITING_POLICY_VERSION >= 2
    core = " ".join(writing_policy.core_text().split())
    assert "re-add its subparagraphs under the returned id" not in core
    assert (
        "To relocate a provision to a different PART, article, or parent "
        "paragraph, use one relocate operation: it carries the provision and "
        "everything under it — text, status, and source_item_id unchanged — "
        "and assigns new ids." in core
    )
    assert (
        "never delete a provision with subparagraphs before its replacement "
        "and every subparagraph exist" in core
    )


def test_the_tool_guide_and_schema_name_relocate():
    prompt = " ".join(render_system_prompt(HYPERSCALE_FIRE).split())
    assert (
        "To put a provision under a different PART, article or parent "
        "paragraph, use relocate (target_id = the article or paragraph, "
        "parent_id = its new parent)" in prompt
    )
    items = APPLY_SPEC_EDITS_TOOL["input_schema"]["properties"]["edits"]["items"]
    assert "relocate" in items["properties"]["action"]["enum"]
    assert "relocate only: the new parent's id" in items["properties"]["parent_id"]["description"]


def test_final_qc_keeps_its_own_vocabulary():
    """QC's lenses read the tool's top-level description as their op
    vocabulary, and QC_OP_ACTIONS has no relocate: a QC fix that relocates
    nested content stays advisory, as it was."""
    assert "relocate" not in QC_OP_ACTIONS
    assert "relocate" not in APPLY_SPEC_EDITS_TOOL["description"]
