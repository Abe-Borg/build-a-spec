"""The redline on the original's comments say nothing of Build-a-Spec's own
bookkeeping (owner, 2026-10-08).

Two things a reader of the Word file saw and should not have:

* "Reason: user cleared assumed status after review" — the reason the
  assistant gave for a STATUS change, said on a provision whose words had
  changed for some other reason. A status or source-link change never
  reaches Word, so its reason is marked when recorded
  (``SpecSection.workflow_reasons``) and left out of the comment; a project
  edited before the mark existed has it read off the version history.
* "(item r-ec2b37e839e6, researched 2026-10-07)" — the research heading's
  item id and date. Gone, as is the QC heading's applied date; reasons and
  QC text have the app's ids rewritten (``redline_basis.reader_text``).

The panel's "why" chip still shows every reason: the owner rule that every
edit carries one is unchanged.
"""
from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient

from backend import sessions, templates
from backend.app import create_app
from backend.redline_basis import (
    _workflow_flags,
    reader_text,
    redline_comment_basis,
)
from backend.research.engine import RequirementsProfile
from backend.spec_doc.model import (
    MAX_EDIT_REASONS_PER_ELEMENT,
    DocumentStore,
    SpecSection,
    apply_edits,
)
from backend.spec_doc.source_format import SECTION_TITLE_UID
from tests.test_preserving_export import _import, _master_bytes
from tests.test_redline_comments import (
    _export_redline,
    _fix_entry,
    _item,
    _redline_comment_lines,
    _text,
)

_ARTICLE = {
    "action": "add_article",
    "target_id": "pt1",
    "text": "SUMMARY",
    "reason": "The user asked for a SUMMARY article first.",
}
_PROVISION = {
    "action": "add_paragraph",
    "target_id": "pt1.a1",
    "text": "Provide a complete wet-pipe sprinkler system.",
    "status": "assumed",
    "reason": "Scope the user stated.",
}
_STATUS_REASON = "user cleared assumed status after review"
_CONFIRM = {
    "action": "set_status",
    "target_id": "pt1.a1.p1",
    "status": "confirmed",
    "reason": _STATUS_REASON,
}


def _section(*ops: dict) -> SpecSection:
    section, _ = apply_edits(SpecSection.empty(), list(ops))
    return section


def _store(*turns: list[dict]) -> DocumentStore:
    """One committed version per turn, as the chat makes them."""
    store = DocumentStore()
    for ops in turns:
        store.begin_turn()
        store.apply_edits(ops)
        store.commit_turn()
    return store


def _as_legacy(store: DocumentStore) -> tuple[SpecSection, list[dict]]:
    """The store as a 1.25.0–1.26.0 build saved it: the same trails, no
    workflow marks anywhere."""
    versions = []
    for version in store.versions[: store.index + 1]:
        version = copy.deepcopy(version)
        version.pop("workflow_reasons", None)
        versions.append(version)
    return SpecSection.from_dict(versions[-1]), versions


# ---------------------------------------------------------------------------
# The mark, where the reason is recorded
# ---------------------------------------------------------------------------


def test_a_status_or_source_change_marks_its_reason_and_a_content_edit_does_not():
    section = _section(
        _ARTICLE,
        _PROVISION,
        _CONFIRM,
        {
            "action": "replace",
            "target_id": "pt1.a1.p1",
            "source_item_id": "pf-1",
            "reason": "Linked to the owner's coverage fact.",
        },
        {
            "action": "replace",
            "target_id": "pt1.a1.p1",
            "status": "assumed",
            "reason": "Coverage is not confirmed yet.",
        },
    )
    # The chip still has every reason, oldest first.
    assert section.edit_reasons["pt1.a1.p1"] == [
        "Scope the user stated.",
        _STATUS_REASON,
        "Linked to the owner's coverage fact.",
        "Coverage is not confirmed yet.",
    ]
    assert section.workflow_reasons == {
        "pt1.a1.p1": [
            _STATUS_REASON,
            "Linked to the owner's coverage fact.",
            "Coverage is not confirmed yet.",
        ]
    }
    # A replace that retypes AND re-stamps changes words: its reason is said.
    retyped = _section(
        _ARTICLE,
        _PROVISION,
        {
            "action": "replace",
            "target_id": "pt1.a1.p1",
            "text": "Provide a wet-pipe sprinkler system throughout.",
            "status": "confirmed",
            "reason": "The user confirmed full coverage.",
        },
    )
    assert retyped.workflow_reasons == {}


def test_a_mark_is_by_text_and_a_shown_edit_wins():
    # A reason the trail already says for a shown edit stays said when a
    # status change repeats it...
    section = _section(
        _ARTICLE,
        _PROVISION,
        {**_CONFIRM, "reason": "Scope the user stated."},
    )
    assert section.edit_reasons["pt1.a1.p1"] == ["Scope the user stated."]
    assert section.workflow_reasons == {}
    # ...and a status reason a later shown edit gives again loses its mark.
    section, _ = apply_edits(
        _section(_ARTICLE, _PROVISION, _CONFIRM, {**_PROVISION, "target_id": "pt1.a1", "reason": "Second."}),
        [
            {
                "action": "replace",
                "target_id": "pt1.a1.p1",
                "text": "Provide a complete wet-pipe system.",
                "reason": _STATUS_REASON,
            }
        ],
    )
    assert "pt1.a1.p1" not in section.workflow_reasons
    # A workflow reason repeated by another workflow edit keeps its mark.
    again, _ = apply_edits(
        _section(_ARTICLE, _PROVISION, _CONFIRM),
        [{**_CONFIRM, "status": "assumed"}, {**_CONFIRM, "reason": "Other."}, _CONFIRM],
    )
    assert again.workflow_reasons["pt1.a1.p1"] == ["Other.", _STATUS_REASON]


def test_marks_follow_the_trail_cap_and_a_hand_reset():
    section = _section(_ARTICLE, _PROVISION)
    for index in range(MAX_EDIT_REASONS_PER_ELEMENT + 2):
        section, _ = apply_edits(
            section,
            [{**_CONFIRM, "status": "assumed" if index % 2 else "confirmed",
              "reason": f"Status round {index}."}],
        )
    trail = section.edit_reasons["pt1.a1.p1"]
    assert len(trail) == MAX_EDIT_REASONS_PER_ELEMENT
    # A mark never outlives its entry.
    assert section.workflow_reasons["pt1.a1.p1"] == trail
    # The user's own retype drops the trail and its marks together.
    retyped, _ = apply_edits(
        section,
        [{"action": "replace", "target_id": "pt1.a1.p1", "text": "My own words."}],
    )
    assert "pt1.a1.p1" not in retyped.edit_reasons
    assert "pt1.a1.p1" not in retyped.workflow_reasons


def test_marks_serialize_only_when_set_and_load_strictly():
    plain = _section(_ARTICLE, _PROVISION)
    assert "workflow_reasons" not in plain.to_dict()
    marked = _section(_ARTICLE, _PROVISION, _CONFIRM)
    data = json.loads(json.dumps(marked.to_dict()))
    assert data["workflow_reasons"] == {"pt1.a1.p1": [_STATUS_REASON]}
    assert SpecSection.from_dict(data).workflow_reasons == marked.workflow_reasons
    for bad in ([], {"pt1.a1.p1": []}, {"pt1.a1.p1": [""]}, {"": ["x"]}):
        broken = dict(data, workflow_reasons=bad)
        with pytest.raises(ValueError, match="workflow_reasons"):
            SpecSection.from_dict(broken)


def test_undo_and_redo_carry_the_marks():
    store = _store([_ARTICLE, _PROVISION], [_CONFIRM])
    assert store.doc.workflow_reasons == {"pt1.a1.p1": [_STATUS_REASON]}
    store.undo()
    assert store.doc.workflow_reasons == {}
    store.redo()
    assert store.doc.workflow_reasons == {"pt1.a1.p1": [_STATUS_REASON]}


def test_a_template_starter_carries_no_marks():
    section = _section(_ARTICLE, _PROVISION, _CONFIRM)
    canonical = templates._canonical_document(section.to_dict(), rebase_statuses=True)
    assert "workflow_reasons" not in canonical


# ---------------------------------------------------------------------------
# The comment leaves workflow reasons out
# ---------------------------------------------------------------------------


def test_a_status_reason_is_not_said_but_the_content_reason_is():
    section = _section(_ARTICLE, _PROVISION, _CONFIRM)
    basis = redline_comment_basis(section)["pt1.a1.p1"]
    assert basis.reasons == (("Reason: Scope the user stated.",),)
    # An element whose only reason is a status change has nothing to say.
    hand = _section(_ARTICLE, _PROVISION)
    hand, _ = apply_edits(
        hand,
        [
            {"action": "replace", "target_id": "pt1.a1.p1", "text": "Reworded by hand."},
            _CONFIRM,
        ],
    )
    assert hand.edit_reasons["pt1.a1.p1"] == [_STATUS_REASON]  # the chip has it
    assert "pt1.a1.p1" not in redline_comment_basis(hand)


def test_an_older_projects_status_reasons_are_read_off_its_history():
    """A project edited by 1.25.0–1.26.0 has no marks. Its versions still
    say which turn recorded each reason and what that turn changed."""
    store = _store(
        [_ARTICLE, _PROVISION, {**_PROVISION, "text": "Second.", "reason": "Second provision."}],
        [_CONFIRM],
        [
            {
                "action": "replace",
                "target_id": "pt1.a1.p1",
                "text": "Provide a complete wet-pipe sprinkler system throughout.",
                "reason": "Added 'throughout' at the user's request.",
            }
        ],
        [{**_CONFIRM, "status": "assumed", "reason": "Reopened for the owner."}],
    )
    current, history = _as_legacy(store)
    assert current.workflow_reasons == {}
    assert _workflow_flags(history, current.to_dict())["pt1.a1.p1"] == [
        False,
        True,
        False,
        True,
    ]
    basis = redline_comment_basis(current, history=history)["pt1.a1.p1"]
    assert basis.reasons == (
        ("Reasons, oldest first:",),
        ("1. Scope the user stated.",),
        ("2. Added 'throughout' at the user's request.",),
    )
    # Without the history the older project says them all, as it did.
    assert len(redline_comment_basis(current)["pt1.a1.p1"].reasons) == 5
    # A marked project agrees with what its history says.
    assert redline_comment_basis(store.doc)["pt1.a1.p1"] == basis
    assert (
        redline_comment_basis(store.doc, history=store.versions[: store.index + 1])[
            "pt1.a1.p1"
        ]
        == basis
    )


def test_the_history_keeps_moves_deletions_and_new_text_said():
    store = _store(
        [
            _ARTICLE,
            _PROVISION,
            {**_PROVISION, "text": "Second.", "reason": "Second provision."},
            {**_PROVISION, "text": "Third.", "reason": "Third provision."},
        ],
        # A status change on p1 beside an insertion before it: p1's place
        # among the provisions both versions hold is unchanged.
        [
            {**_PROVISION, "text": "New first.", "position": 0, "reason": "Inserted first."},
            _CONFIRM,
        ],
        [{"action": "move", "target_id": "pt1.a1.p3", "position": 0, "reason": "Third reads first."}],
        [{"action": "delete", "target_id": "pt1.a1.p2", "reason": "Duplicates the scope."}],
    )
    current, history = _as_legacy(store)
    flags = _workflow_flags(history, current.to_dict())
    assert flags["pt1.a1.p1"] == [False, True]
    assert flags["pt1.a1.p4"] == [False]
    assert flags["pt1.a1.p3"] == [False, False]
    assert flags["pt1.a1.p2"] == [False, False]
    bases = redline_comment_basis(current, history=history)
    assert bases["pt1.a1.p1"].reasons == (("Reason: Scope the user stated.",),)
    assert bases["pt1.a1.p3"].reasons[-1] == ("2. Third reads first.",)
    assert bases["pt1.a1.p2"].reasons[-1] == ("2. Duplicates the scope.",)


def test_a_turn_that_retypes_and_restamps_one_provision_keeps_both_said():
    """The history cannot tell which of two reasons one turn recorded on an
    element whose words changed was the status one, so it says both — the
    side that never hides a reason for a change the reader sees. The mark,
    for a newer edit, is exact."""
    store = _store(
        [_ARTICLE, _PROVISION],
        [
            {
                "action": "replace",
                "target_id": "pt1.a1.p1",
                "text": "Provide a wet-pipe system throughout.",
                "reason": "The user asked for full coverage.",
            },
            _CONFIRM,
        ],
    )
    current, history = _as_legacy(store)
    assert _workflow_flags(history, current.to_dict())["pt1.a1.p1"] == [False, False, False]
    assert redline_comment_basis(store.doc, history=store.versions)["pt1.a1.p1"].reasons == (
        ("Reasons, oldest first:",),
        ("1. Scope the user stated.",),
        ("2. The user asked for full coverage.",),
    )


def test_the_header_and_a_malformed_history_degrade_to_saying_the_reason():
    store = _store(
        [_ARTICLE, {"action": "replace", "target_id": "sec", "text": "WET-PIPE", "reason": "Section chosen."}],
    )
    current, history = _as_legacy(store)
    bases = redline_comment_basis(current, history=history)
    assert bases["sec"].reasons == bases[SECTION_TITLE_UID].reasons == (
        ("Reason: Section chosen.",),
    )
    broken = [history[0], {"parts": "nonsense"}, history[-1]]
    assert redline_comment_basis(current, history=broken)["pt1.a1"].reasons == (
        ("Reason: The user asked for a SUMMARY article first.",),
    )


# ---------------------------------------------------------------------------
# No app ids, no bookkeeping dates
# ---------------------------------------------------------------------------

_NUMBERS = {"pt1": "PART 1", "pt1.a2": "1.2", "pt1.a2.p3": "1.2.C"}
_TITLES = {"ref-1": "Owner Standard"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The owner's example, whole.
        ("Per research (item r-ec2b37e839e6, researched 2026-10-07).", "Per research."),
        (
            "Per research item r-ec2b37e839e6, the density is 0.30 gpm/sq ft.",
            "Per research, the density is 0.30 gpm/sq ft.",
        ),
        ("Moved after pt1.a2.p3 so submittals read first.", "Moved after 1.2.C so submittals read first."),
        ("Moved after pt1.a9.p3 so submittals read first.", "Moved so submittals read first."),
        ("Under pt1, per ref-1.", 'Under PART 1, per "Owner Standard".'),
        ("Added per ref-1 and fact pf-3.", 'Added per "Owner Standard".'),
        ("Confirmed (pf-2; researched 2026-10-07) by the user.", "Confirmed by the user."),
        ("Density per item r-ec2b37e839e6 (Loudoun County amendment).", "Density (Loudoun County amendment)."),
        ("Paragraph pt2.a9.p1 duplicates the scope; finding qc-0123456789ab.", "Duplicates the scope."),
        ("Per research (researched 2026-10-07).", "Per research."),
        ("Per research, researched on 2026-10-07.", "Per research."),
        ("r-ec2b37e839e6", ""),
        ("(fu-4)", ""),
        # Nothing that only looks like an id.
        ("Per NFPA 13 (2025) §8.15; R-13 insulation and the r-value.", "Per NFPA 13 (2025) §8.15; R-13 insulation and the r-value."),
        ("Cited https://x.example/r-ec2b37e839e6 directly.", "Cited https://x.example/r-ec2b37e839e6 directly."),
        ("Per the 2026-10-01 RFI response (2026-10-01).", "Per the 2026-10-01 RFI response (2026-10-01)."),
        ("  user cleared assumed status after review ", "user cleared assumed status after review"),
    ],
)
def test_reader_text_rewrites_the_apps_ids(text, expected):
    assert reader_text(text, numbers=_NUMBERS, titles=_TITLES) == expected


def test_reasons_and_qc_text_reach_the_comment_without_ids():
    section = _section(
        _ARTICLE,
        {**_PROVISION, "source_item_id": "r-ec2b37e839e6"},
        {
            "action": "replace",
            "target_id": "pt1.a1.p1",
            "text": "Provide a complete wet-pipe sprinkler system throughout.",
            "reason": "Per research item r-ec2b37e839e6 (researched 2026-10-07), not pt1.a1.",
        },
        {**_PROVISION, "text": "Second.", "reason": "pf-2"},
    )
    profile = RequirementsProfile(
        items=[_item("r-ec2b37e839e6", grounded=True, research_date="2026-10-07",
                     accepted=["https://codes.example/x"])]
    )
    entry = _fix_entry(
        "pt1.a1.p1",
        "Provide a complete wet-pipe sprinkler system throughout.",
        title="Coverage in pt1.a1.p1",
        issue="Paragraph pt1.a1.p1 omits coverage (finding qc-0123456789ab).",
    )
    entry["lens_title"] = ""
    bases = redline_comment_basis(section, profile=profile, fix_log=[entry])
    lines = _text(bases["pt1.a1.p1"].qc) + _text(bases["pt1.a1.p1"].research) + _text(
        bases["pt1.a1.p1"].reasons
    )
    assert "Changed by a Final QC fix: Coverage in 1.1.A (high, code compliance)" in lines
    assert "Paragraph 1.1.A omits coverage." in lines
    assert "Basis: requirements research" in lines
    assert "2. Per research, not 1.1." in lines
    joined = "\n".join(lines)
    for leaked in ("r-ec2b37e839e6", "2026-10-07", "2026-09-20", "pt1.", "qc-0123456789ab", "code_compliance"):
        assert leaked not in joined
    # A reason that was nothing but an id says nothing at all.
    assert "pt1.a1.p2" not in bases


# ---------------------------------------------------------------------------
# Through the app
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    import backend.app as app_module

    monkeypatch.setattr(app_module, "_revision_timestamp", lambda: "2026-10-08T12:00:00Z")
    return TestClient(create_app())


def _edit(client, *ops: dict) -> None:
    response = client.post("/api/doc/edit", json={"ops": list(ops)})
    assert response.status_code == 200, response.text
    assert response.json()["ok"], response.text


def test_the_export_says_the_reworded_reason_and_not_the_status_one(client):
    _import(client, _master_bytes())
    doc = client.get("/api/doc").json()["doc"]
    first, second = doc["parts"][0]["articles"][0]["paragraphs"][:2]
    _edit(
        client,
        {"action": "replace", "target_id": first["id"], "text": "Reworded.",
         "reason": "Reworded so the provision directs the Contractor."},
    )
    _edit(client, {"action": "set_status", "target_id": first["id"], "status": "confirmed",
                   "reason": _STATUS_REASON})
    # Reworded by hand, then confirmed by the assistant: nothing to say.
    _edit(client, {"action": "replace", "target_id": second["id"], "text": "Mine."})
    _edit(client, {"action": "set_status", "target_id": second["id"], "status": "confirmed",
                   "reason": _STATUS_REASON})
    assert sessions.get_session().doc.doc.edit_reasons[second["id"]] == [_STATUS_REASON]
    expected = [["Reason: Reworded so the provision directs the Contractor."]]
    assert _redline_comment_lines(_export_redline(client)) == expected

    # The same project as an older build saved it — no marks — says the same,
    # because the export reads the version history.
    store = sessions.get_session().doc
    store.versions = [
        {key: value for key, value in version.items() if key != "workflow_reasons"}
        for version in store.versions
    ]
    store.doc = SpecSection.from_dict(store.versions[store.index])
    assert store.doc.workflow_reasons == {}
    assert _redline_comment_lines(_export_redline(client)) == expected
