"""The brief is a living file (Project workspace Phase 3): the write-back merge.

A section seeded from a project brief is a FORK — facts, rounds and
references added in one section reach another only through the brief. The
contract under test (``docs/plans/project-workspace/03_WRITE_BACK_MERGE.md``):

- round identity: a round carries a uuid and its own membership, serialized
  only when set, so a legacy profile's bytes — and the QC research
  fingerprint over them — do not move;
- research: the rounds one copy lacks are REPLAYED through
  ``append_research_round``; a same-day fork keeps all four rounds,
  renumbered, with a re-found item confirmed in place;
- facts: the statement is the key, a scope conflict resolves to the wider
  scope, a supersede on either side is terminal, carried pids re-mint with
  their links rewritten, provenance travels (``brief`` only for the D4
  conflict and an unresolvable ref), and the cap refuses rather than drops;
- references: kept by content, a colliding rid re-mints (and the facts that
  cite it follow), a duplicate rid refuses, cap drops are named — and an
  attachment the extended side already holds is never dropped;
- D4: an edition disagreement is a warning AND a project fact; the newest
  export wins the profile, field by field, with a warning;
- the whole merge is idempotent and refuses two projects;
- the routes: merge answers bytes and a report; refresh writes the home
  brief atomically and a failed write leaves it byte-identical; refresh and
  pull refuse a tour, running work and a section with no home; a pull
  installs only the three append-only assets and reports the rest.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from pathlib import Path

import pytest

from backend import project_brief, sessions
from backend.app import _carried_research_note
from backend.project_brief import (
    ProjectBrief,
    ProjectBriefMergeRefused,
    ProjectBriefMismatchError,
    brief_bytes,
    merge_project_brief,
    parse_project_brief,
    write_brief_atomically,
)
from backend.project_facts import (
    MAX_ACTIVE_FACTS,
    FactsMergeRefused,
    ProjectFactStore,
    merge_facts,
)
from backend.reference_docs import MAX_REFERENCE_DOCS, MAX_REFERENCE_TOKENS
from backend.research import ResearchRunner
from backend.research.engine import (
    DimensionStatus,
    RequirementsProfile,
    append_research_round,
    legacy_round_key,
    merge_research_profiles,
    profile_fingerprint,
)
from tests.fakes import SequencedFakeClient, research_response
from tests.test_close_prompt import _FakeWindow, _controller_with, _fake_webview
from tests.test_project_brief import PROFILE, _client, _item, _rich_session
from tests.test_project_home import BRIEF_NAME, _home, _project_folder
from tests.test_research_rounds import _run_round, _scripts_for_rounds
from tests.test_research_engine import _item as _engine_item

PROJECT = "a" * 32
OTHER_PROJECT = "b" * 32


def _brief(**fields) -> ProjectBrief:
    values = {
        "project_id": PROJECT,
        "name": "Client X · Data Center",
        "created_at": "2026-09-01T00:00:00+00:00",
        "updated_at": "2026-09-01T00:00:00+00:00",
        "app_version": "1.20.0",
    }
    values.update(fields)
    return ProjectBrief(**values)


def _fact(pid: str, statement: str, **fields) -> dict:
    fact = {
        "pid": pid,
        "statement": statement,
        "detail": "",
        "scope": "project",
        "section": "",
        "discipline": "",
        "status": "confirmed",
        "source_kind": "user",
        "source_ref": "",
        "recorded_in": "21 13 13",
        "recorded_at": "2026-09-04",
        "superseded_by": "",
        "supersede_reason": "",
    }
    fact.update(fields)
    return fact


def _doc(rid: str, text: str, *, tokens: int = 100, title: str = "") -> dict:
    return {
        "rid": rid,
        "filename": f"{rid}.txt",
        "title": title or f"Document {rid}",
        "text": text,
        "char_count": len(text),
        "block_count": 1,
        "truncated": False,
        "tracked_changes": False,
        "added_at": "2026-09-04T00:00:00+00:00",
        "kind": "txt",
        "token_count": tokens,
        "content_fingerprint": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def _one_round(items, *, date: str) -> RequirementsProfile:
    """One round's own fan-out output, before it is appended anywhere."""
    return RequirementsProfile(
        items=list(items),
        dimension_statuses=[
            DimensionStatus(
                dimension_id="governing_codes",
                status="completed",
                title="Governing building and fire codes",
                item_count=len(items),
                grounded_count=sum(1 for item in items if item.grounded),
            )
        ],
        research_date=date,
        project=PROFILE.to_dict(),
    )


def _canonical(value) -> str:
    """``profile_fingerprint``'s serialization, over a plain dict."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def _section(number: str, exported_at: str) -> dict:
    return {
        "number": number,
        "title": f"Section {number}",
        "module_id": "generic",
        "discipline": "Fire Suppression",
        "article_titles": [],
        "ready": False,
        "exported_at": exported_at,
        "file_name": f"{number}.baspec",
        "fact_count": 0,
        "research_rounds": 0,
    }


# ---------------------------------------------------------------------------
# 3.1 Round identity
# ---------------------------------------------------------------------------


def test_round_id_and_item_ids_round_trip_and_legacy_bytes_are_untouched():
    first = append_research_round(
        None,
        _one_round([_item("r-1", "Rule 1.")], date="2026-08-01"),
        section="21 13 13",
        round_id="1" * 32,
    )
    second = append_research_round(
        first,
        _one_round([_item("r-1", "Rule 1."), _item("r-2", "Rule 2.")], date="2026-08-02"),
        round_id="2" * 32,
    )
    raw = second.to_dict()
    assert [r["round_id"] for r in raw["rounds"]] == ["1" * 32, "2" * 32]
    # A round's membership is new AND re-confirmed — what makes it replayable.
    assert raw["rounds"][1]["item_ids"] == ["r-1", "r-2"]
    assert RequirementsProfile.from_dict(json.loads(json.dumps(raw))).to_dict() == raw

    # A profile saved before either field existed round-trips byte for byte,
    # and the QC research fingerprint over it does not move.
    legacy = copy.deepcopy(raw)
    for record in legacy["rounds"]:
        del record["round_id"], record["item_ids"]
    restored = RequirementsProfile.from_dict(copy.deepcopy(legacy))
    assert _canonical(restored.to_dict()) == _canonical(legacy)
    assert profile_fingerprint(restored) == hashlib.sha256(
        _canonical(legacy).encode("utf-8")
    ).hexdigest()

    # Read leniently: a malformed id or membership degrades to "none".
    hostile = copy.deepcopy(raw)
    hostile["rounds"][0]["round_id"] = 42
    hostile["rounds"][0]["item_ids"] = "r-1"
    hostile["rounds"][1]["item_ids"] = ["r-1", 7, "r-1", "", "r-2"]
    read = RequirementsProfile.from_dict(hostile)
    assert read.rounds[0].round_id == "" and read.rounds[0].item_ids == []
    assert read.rounds[1].item_ids == ["r-1", "r-2"]


def test_a_new_round_carries_its_id_through_append():
    # The engine mints a uuid into the round it stamps at birth …
    client = SequencedFakeClient(
        _scripts_for_rounds(
            {
                "governing_codes": research_response(
                    items=[_engine_item("Rule A.", ["https://a.gov"])],
                    searched_urls=["https://a.gov"],
                )
            },
            {
                "governing_codes": research_response(
                    items=[_engine_item("Rule A.", ["https://a.gov"])],
                    searched_urls=["https://a.gov"],
                )
            },
        )
    )
    runner = ResearchRunner()
    _run_round(runner, client, None)
    _run_round(runner, client, None)
    rounds = runner.profile_result.rounds
    assert [len(r.round_id) for r in rounds] == [32, 32]
    assert rounds[0].round_id != rounds[1].round_id
    only = runner.profile_result.items[0].item_id
    # … and the runner's adopt path (no keyword) kept it; the second round's
    # membership names the item it re-confirmed.
    assert rounds[0].item_ids == [only] and rounds[1].item_ids == [only]
    assert rounds[1].repeat_items == 1

    # An append with no keyword carries fresh's own id and section.
    born = append_research_round(
        None,
        _one_round([_item("r-1", "Rule 1.")], date="2026-08-01"),
        section="21 30 00",
        round_id="c" * 32,
    )
    previous = append_research_round(
        None, _one_round([_item("r-0", "Rule 0.")], date="2026-07-01"), round_id="d" * 32
    )
    adopted = append_research_round(previous, born)
    assert adopted.rounds[-1].round_index == 2
    assert adopted.rounds[-1].round_id == "c" * 32
    assert adopted.rounds[-1].section == "21 30 00"
    assert adopted.rounds[-1].item_ids == ["r-1"]


# ---------------------------------------------------------------------------
# 3.3 Research: replay the unseen rounds
# ---------------------------------------------------------------------------


def test_unseen_rounds_replay_through_append_research_round():
    s1 = append_research_round(
        None,
        _one_round([_item("r-1", "Rule 1.")], date="2026-08-01"),
        section="21 13 13",
        round_id="1" * 32,
    )
    s1 = append_research_round(
        s1,
        _one_round([_item("r-2", "Rule 2.")], date="2026-08-02"),
        section="21 13 13",
        round_id="2" * 32,
    )
    s2 = RequirementsProfile.from_dict(s1.to_dict())  # 21 30 00, seeded with both
    # Both sections research again, the SAME day. 21 30 00 re-finds rule 1
    # from a new source and finds rule 3; 21 13 13 finds rule 4.
    s2 = append_research_round(
        s2,
        _one_round(
            [
                _item(
                    "r-1",
                    "Rule 1.",
                    source_urls=["https://b.gov"],
                    accepted_sources=["https://b.gov"],
                ),
                _item("r-3", "Rule 3."),
            ],
            date="2026-09-10",
        ),
        section="21 30 00",
        round_id="3" * 32,
    )
    s1 = append_research_round(
        s1,
        _one_round([_item("r-4", "Rule 4.")], date="2026-09-10"),
        section="21 13 13",
        round_id="4" * 32,
    )

    merged, report = merge_research_profiles(s1, s2)

    assert [r.round_index for r in merged.rounds] == [1, 2, 3, 4]
    assert [r.round_id for r in merged.rounds] == ["1" * 32, "2" * 32, "4" * 32, "3" * 32]
    assert [r.section for r in merged.rounds] == ["21 13 13"] * 3 + ["21 30 00"]
    ids = [item.item_id for item in merged.items]
    assert sorted(ids) == ["r-1", "r-2", "r-3", "r-4"] and len(ids) == len(set(ids))
    rule_one = merged.item("r-1")
    assert set(rule_one.source_urls) == {"https://example.test/vcc", "https://b.gov"}
    assert rule_one.round_index == 1, "still the round that FIRST found it"
    assert rule_one.research_date == "2026-09-10", "dated by its latest grounding"
    assert report.rounds_added == 1 and report.legacy_rounds == 0
    assert merged.research_date == "2026-09-10"

    # The other direction holds the same four rounds.
    back, _ = merge_research_profiles(s2, s1)
    assert back.round_count == 4
    assert {r.round_id for r in back.rounds} == {r.round_id for r in merged.rounds}
    # A repeated merge is a no-op that hands back its base.
    again, again_report = merge_research_profiles(merged, s2)
    assert again is merged and again_report.rounds_added == 0
    # Nothing was mutated on the way.
    assert s1.round_count == 3 and s2.round_count == 3


def test_a_legacy_round_without_membership_replays_first_found_items_and_says_so():
    base = append_research_round(
        None,
        _one_round([_item("r-1", "Rule 1.")], date="2026-08-01"),
        section="21 13 13",
        round_id="1" * 32,
    )
    grown = append_research_round(
        base,
        _one_round([_item("r-1", "Rule 1."), _item("r-5", "Rule 5.")], date="2026-08-05"),
        section="21 30 00",
    )
    other = grown.to_dict()
    # Round 2 was saved before round identities existed.
    del other["rounds"][1]["item_ids"]
    assert "round_id" not in other["rounds"][1]
    incoming = RequirementsProfile.from_dict(other)

    merged, report = merge_research_profiles(base, incoming)

    assert merged.round_count == 2
    replayed = merged.rounds[1]
    assert replayed.round_id == legacy_round_key(incoming.rounds[1])
    assert replayed.item_ids == [], "membership was never recorded; none is claimed"
    assert replayed.new_items == 1, "only the item it FIRST found was attributable"
    assert [item.item_id for item in merged.items] == ["r-1", "r-5"]
    assert report.legacy_rounds == 1 and report.first_found_only == 1
    notes = " ".join(report.notes())
    assert "predate round identities" in notes and "first reported" in notes
    # The key is stable: merging the same legacy copy again adds nothing.
    again, again_report = merge_research_profiles(merged, incoming)
    assert again is merged and again_report.rounds_added == 0


# ---------------------------------------------------------------------------
# 3.4 Facts
# ---------------------------------------------------------------------------


def test_the_statement_is_the_key_and_scope_conflicts_resolve_to_the_wider():
    # Same statement, same placement: confirmed in place.
    base = [_fact("pf-1", "Water supply is 30 minutes.", status="assumed")]
    incoming = [
        _fact(
            "pf-7",
            "water supply is  30 MINUTES.",
            detail="Owner standard section 4.",
            recorded_in="21 30 00",
        )
    ]
    merged, report = merge_facts(base, incoming)
    assert [f["pid"] for f in merged] == ["pf-1"]
    assert merged[0]["status"] == "confirmed" and merged[0]["detail"] == "Owner standard section 4."
    assert merged[0]["recorded_in"] == "21 13 13", "the existing fact keeps its provenance"
    assert report.confirmed == 1 and report.added == 0

    # Never the reverse: an assumed copy does not demote a confirmed fact.
    demote, _ = merge_facts(
        [_fact("pf-1", "Water supply is 30 minutes.")],
        [_fact("pf-2", "Water supply is 30 minutes.", status="assumed", recorded_in="21 30 00")],
    )
    assert [(f["pid"], f["status"]) for f in demote] == [("pf-1", "confirmed")]

    # Same statement, different scope: the WIDER scope is kept and the other
    # is folded in as superseded, pointing at it.
    base = [
        _fact(
            "pf-1",
            "Data halls are Ordinary Hazard Group 2.",
            scope="section",
            section="21 13 13",
        )
    ]
    incoming = [
        _fact(
            "pf-3",
            "Data halls are Ordinary Hazard Group 2.",
            scope="project",
            recorded_in="21 30 00",
            recorded_at="2026-09-05",
        )
    ]
    merged, report = merge_facts(base, incoming)
    by_pid = {f["pid"]: f for f in merged}
    assert by_pid["pf-2"]["scope"] == "project" and by_pid["pf-2"]["status"] == "confirmed"
    assert by_pid["pf-1"]["status"] == "superseded"
    assert by_pid["pf-1"]["superseded_by"] == "pf-2"
    assert by_pid["pf-1"]["supersede_reason"] == (
        "Merged: the same fact was recorded at project by 21 30 00."
    )
    assert report.folded == 1
    assert any("is kept" in c for c in report.conflicts)
    # A merged ledger never holds a state record() would refuse.
    store = ProjectFactStore()
    store.load({"project_facts": merged})
    assert len(store.active()) == 1


def test_a_supersede_on_either_side_is_terminal():
    live = _fact("pf-1", "Fire pump is diesel.", uid="1" * 32)
    retired = dict(live, status="superseded", supersede_reason="Owner chose electric.")

    # Retired over there → retired here, with that reason.
    merged, report = merge_facts([live], [retired])
    assert merged[0]["status"] == "superseded"
    assert merged[0]["supersede_reason"] == "Owner chose electric."
    assert report.retired == 1

    # Retired here → a live copy over there never brings it back.
    merged, report = merge_facts([retired], [live])
    assert [f["status"] for f in merged] == ["superseded"]
    assert report.added == 0 and report.retired == 0

    # A fact recorded before uids existed, retired in another section with a
    # replacement: the live copy here is retired too, the replacement lands,
    # and both retirements point at it.
    base = [_fact("pf-1", "Fire pump is diesel.")]
    incoming = [
        _fact(
            "pf-4",
            "Fire pump is diesel.",
            status="superseded",
            superseded_by="pf-5",
            supersede_reason="Owner chose electric.",
            recorded_in="21 30 00",
        ),
        _fact("pf-5", "Fire pump is electric, 1500 gpm.", recorded_in="21 30 00"),
    ]
    merged, report = merge_facts(base, incoming)
    by_statement = {}
    for fact in merged:
        by_statement.setdefault(fact["statement"], []).append(fact)
    [replacement] = by_statement["Fire pump is electric, 1500 gpm."]
    assert replacement["status"] == "confirmed"
    diesel = by_statement["Fire pump is diesel."]
    assert all(f["status"] == "superseded" for f in diesel)
    assert all(f["superseded_by"] == replacement["pid"] for f in diesel)
    assert report.retired == 1 and report.added == 1 and report.history_added == 1
    assert any("was retired in 21 30 00" in c for c in report.conflicts)


def test_incoming_pids_are_re_minted_and_links_rewritten():
    base = [_fact("pf-1", "Fact one."), _fact("pf-2", "Fact two.")]
    incoming = [
        _fact("pf-1", "Other A.", recorded_in="21 30 00"),
        _fact(
            "pf-2",
            "Other B.",
            status="superseded",
            superseded_by="pf-4",
            supersede_reason="Replaced.",
            recorded_in="21 30 00",
        ),
        _fact("pf-4", "Other C.", recorded_in="21 30 00"),
    ]
    merged, report = merge_facts(base, incoming)
    by_statement = {f["statement"]: f for f in merged}
    assert [f["pid"] for f in merged] == ["pf-1", "pf-2", "pf-3", "pf-4", "pf-5"]
    assert by_statement["Fact one."]["pid"] == "pf-1", "the extended side keeps its ids"
    assert by_statement["Other A."]["pid"] == "pf-3"
    assert by_statement["Other B."]["pid"] == "pf-4"
    assert by_statement["Other C."]["pid"] == "pf-5"
    assert by_statement["Other B."]["superseded_by"] == "pf-5", "the link followed its fact"
    assert report.re_minted == 3 and report.added == 2 and report.history_added == 1

    # A floor (a store's next_seq) is honored: an id a rolled-back turn
    # consumed is never handed to a carried fact.
    floored, _ = merge_facts(base, [_fact("pf-1", "Other A.")], pid_floor=10)
    assert [f["pid"] for f in floored] == ["pf-1", "pf-2", "pf-10"]


def test_provenance_travels_and_brief_is_used_only_when_a_ref_cannot_resolve():
    existing = _brief(
        reference_docs=[_doc("ref-1", "Owner standard A.")],
        facts=[
            _fact("pf-1", "Existing, cites its document.", source_kind="reference", source_ref="ref-1"),
            # A ref that never resolved is not the merge's to repair.
            _fact("pf-2", "Existing, cites nothing real.", source_kind="reference", source_ref="ref-7"),
        ],
        sections=[_section("21 13 13", "2026-09-01T00:00:00+00:00")],
    )
    incoming = _brief(
        updated_at="2026-09-05T00:00:00+00:00",
        reference_docs=[_doc("ref-1", "Cut sheet B.")],  # a DIFFERENT document
        facts=[
            _fact(
                "pf-1",
                "Stated by the user in 21 30 00.",
                recorded_in="21 30 00",
                recorded_at="2026-09-05",
            ),
            _fact(
                "pf-2",
                "From the cut sheet.",
                source_kind="reference",
                source_ref="ref-1",
                recorded_in="21 30 00",
            ),
            _fact(
                "pf-3",
                "From a research item the brief lacks.",
                source_kind="research",
                source_ref="r-missing",
                recorded_in="21 30 00",
            ),
            _fact(
                "pf-4",
                "From a document nobody holds.",
                source_kind="reference",
                source_ref="ref-9",
                recorded_in="21 30 00",
            ),
        ],
        sections=[_section("21 30 00", "2026-09-05T00:00:00+00:00")],
    )

    merged, report = merge_project_brief(existing, incoming)
    by_statement = {f["statement"]: f for f in merged.facts}

    user = by_statement["Stated by the user in 21 30 00."]
    assert (user["source_kind"], user["recorded_in"], user["recorded_at"]) == (
        "user",
        "21 30 00",
        "2026-09-05",
    ), "a user fact pulled from another section is still a user fact"
    cut_sheet = by_statement["From the cut sheet."]
    assert (cut_sheet["source_kind"], cut_sheet["source_ref"]) == ("reference", "ref-2")
    for statement in ("From a research item the brief lacks.", "From a document nobody holds."):
        fact = by_statement[statement]
        assert (fact["source_kind"], fact["source_ref"]) == ("brief", "21 30 00")
    assert by_statement["Existing, cites its document."]["source_ref"] == "ref-1"
    assert by_statement["Existing, cites nothing real."]["source_kind"] == "reference"
    assert report.facts["refs_unresolved"] == 2
    assert any("cited a source" in w for w in report.warnings)


def test_the_cap_refuses_rather_than_drops():
    base = [_fact(f"pf-{n}", f"Fact number {n}.") for n in range(1, MAX_ACTIVE_FACTS + 1)]
    with pytest.raises(FactsMergeRefused) as refused:
        merge_facts(base, [_fact("pf-1", "One more fact.", recorded_in="21 30 00")])
    assert "limit is 150" in str(refused.value)
    assert refused.value.report.added == 1

    # Through the whole-brief merge: refused with the report, never written.
    with pytest.raises(ProjectBriefMergeRefused) as whole:
        merge_project_brief(
            _brief(facts=base),
            _brief(facts=[_fact("pf-1", "One more fact.", recorded_in="21 30 00")]),
        )
    assert whole.value.report is not None
    assert whole.value.report.facts["added"] == 1

    # Judged on the FINAL state: retiring one and raising its replacement in
    # the same merge fits at the cap.
    with_uid = [dict(f, uid=f"{n:032x}") for n, f in enumerate(base, start=1)]
    retire_one = dict(with_uid[0], status="superseded", supersede_reason="Moot.")
    merged, _report = merge_facts(
        with_uid, [retire_one, _fact("pf-900", "Its replacement.", recorded_in="21 30 00")]
    )
    assert sum(1 for f in merged if f["status"] != "superseded") == MAX_ACTIVE_FACTS


def test_an_in_place_edit_travels_by_uid_and_the_later_edit_wins():
    """Deviation (recorded in the phase file): an edit changes the statement,
    so the statement alone cannot recognise an edited fact across a fork.
    The uid does, and between two edited copies the later edit wins."""
    store = ProjectFactStore()
    store.record(
        {"statement": "Riser rooms are heated.", "scope": "project", "status": "assumed"},
        recorded_in="21 13 13",
        recorded_at="2026-09-04",
    )
    original = store.snapshot()[0]
    assert len(original["uid"]) == 32 and "edited_at" not in original
    assert store.update("pf-1", {"statement": "Riser rooms are heated to 40 °F."}) == "ok"
    edited = store.snapshot()[0]
    assert edited["uid"] == original["uid"] and edited["edited_at"]

    merged, report = merge_facts([original], [edited])
    assert [f["statement"] for f in merged] == ["Riser rooms are heated to 40 °F."]
    assert report.updated == 1 and report.added == 0
    # The older copy never undoes the newer edit.
    merged, report = merge_facts([edited], [original])
    assert [f["statement"] for f in merged] == ["Riser rooms are heated to 40 °F."]
    assert report.updated == 0
    # Two edits: the later one wins, whichever side it arrives from.
    later = dict(edited, statement="Riser rooms are heated to 50 °F.", edited_at="2099-01-01T00:00:00+00:00")
    assert [f["statement"] for f in merge_facts([edited], [later])[0]] == [
        "Riser rooms are heated to 50 °F."
    ]


# ---------------------------------------------------------------------------
# 3.2 References
# ---------------------------------------------------------------------------


def test_a_present_document_keeps_its_rid_and_a_new_colliding_one_is_re_minted():
    existing = _brief(reference_docs=[_doc("ref-1", "Text A."), _doc("ref-2", "Text B.")])
    incoming = _brief(
        reference_docs=[
            _doc("ref-1", "Text B."),  # the same document as ref-2 here
            _doc("ref-2", "Text C."),  # a new document whose id is taken
        ],
        facts=[
            _fact("pf-1", "Cites B.", source_kind="reference", source_ref="ref-1"),
            _fact("pf-2", "Cites C.", source_kind="reference", source_ref="ref-2"),
        ],
    )

    merged, report = merge_project_brief(existing, incoming)

    assert [(d["rid"], d["text"]) for d in merged.reference_docs] == [
        ("ref-1", "Text A."),
        ("ref-2", "Text B."),
        ("ref-3", "Text C."),
    ]
    assert report.references == {
        "added": 1,
        "already_present": 1,
        "re_minted": 1,
        "dropped": [],
    }
    by_statement = {f["statement"]: f for f in merged.facts}
    assert by_statement["Cites B."]["source_ref"] == "ref-2"
    assert by_statement["Cites C."]["source_ref"] == "ref-3"
    assert report.facts["refs_rewritten"] == 2


def test_a_duplicate_rid_is_a_merge_failure_never_written(tmp_path):
    # On either side: a pull reads the brief as the INCOMING side.
    twins = [_doc("ref-1", "One."), _doc("ref-1", "Two.")]
    for existing, incoming in ((twins, []), ([], twins)):
        with pytest.raises(ProjectBriefMergeRefused):
            merge_project_brief(
                _brief(reference_docs=existing), _brief(reference_docs=incoming)
            )

    client = _client()
    session, section_file = _project_folder(client, tmp_path)
    _home(session, section_file)
    brief_path = tmp_path / BRIEF_NAME
    on_disk = json.loads(brief_path.read_text(encoding="utf-8"))
    copied = dict(on_disk["reference_docs"][0], title="A hand-edited twin")
    on_disk["reference_docs"].append(copied)  # two documents answer to ref-1
    brief_path.write_text(json.dumps(on_disk), encoding="utf-8")
    before = brief_path.read_bytes()

    with pytest.raises(ProjectBriefMergeRefused):
        merge_project_brief(parse_project_brief(before), _brief(project_id=on_disk["project_id"]))

    resp = client.post("/api/project/brief/refresh")
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "merge_refused"
    assert "two reference documents under one id" in resp.json()["error"]
    assert brief_path.read_bytes() == before


def test_cap_drops_are_named():
    held = [_doc(f"ref-{n}", f"Held document {n}.") for n in range(1, MAX_REFERENCE_DOCS)]
    new = [
        _doc("ref-1", "New document one.", title="Fits"),
        _doc("ref-2", "New document two.", title="Does not fit"),
    ]
    merged, report = merge_project_brief(_brief(reference_docs=held), _brief(reference_docs=new))
    assert len(merged.reference_docs) == MAX_REFERENCE_DOCS
    assert merged.reference_docs[-1]["title"] == "Fits"
    assert report.references["dropped"] == ["Does not fit"]
    assert report.references["added"] == 1
    assert any("Does not fit" in w for w in report.warnings)

    # What the extended side ALREADY holds is never dropped, even past the
    # token cap (a legacy file): a merge is append-only.
    heavy = [_doc("ref-1", "Heavy.", tokens=MAX_REFERENCE_TOKENS + 5)]
    merged, report = merge_project_brief(
        _brief(reference_docs=heavy), _brief(reference_docs=[_doc("ref-2", "Light.")])
    )
    assert [d["text"] for d in merged.reference_docs] == ["Heavy."]
    assert report.references["dropped"] == ["Document ref-2"]


# ---------------------------------------------------------------------------
# D4: the profile and the editions
# ---------------------------------------------------------------------------


def test_an_override_disagreement_is_a_warning_and_a_fact():
    existing = _brief(
        edition_overrides={"NFPA 13": {"edition": "2022", "basis": "AHJ adopted the 2021 VCC."}},
        sections=[_section("21 13 13", "2026-09-01T00:00:00+00:00")],
    )
    incoming = _brief(
        updated_at="2026-09-05T00:00:00+00:00",
        edition_overrides={"NFPA 13": {"edition": "2025", "basis": "Owner standard 4.2."}},
        sections=[_section("21 30 00", "2026-09-05T00:00:00+00:00")],
    )

    merged, report = merge_project_brief(existing, incoming)

    statement = (
        "Sections disagree on the NFPA 13 edition: 2022 and 2025 are both recorded. "
        "Resolve before issue."
    )
    [fact] = [f for f in merged.facts if f["statement"] == statement]
    assert (fact["scope"], fact["status"], fact["source_kind"]) == ("project", "assumed", "brief")
    assert fact["source_ref"] == "project brief; section 21 30 00"
    assert fact["detail"] == (
        "The project brief records 2022 (basis: AHJ adopted the 2021 VCC.); "
        "section 21 30 00 records 2025 (basis: Owner standard 4.2.)."
    )
    assert fact["recorded_in"] == "21 30 00"
    assert [d["kind"] for d in report.setup] == ["edition"]
    assert report.conflicts and "Recorded as a project fact" in report.conflicts[0]
    # The newest export's edition wins the brief's override.
    assert merged.edition_overrides["NFPA 13"]["edition"] == "2025"

    # A pull (the section on the "existing" side) names the two sides the
    # same way, so the statement — the duplicate key — is the same fact.
    pulled, pull_report = merge_project_brief(incoming, existing, section_side="existing", apply_setup=False)
    assert [f["statement"] for f in pulled.facts if f["source_kind"] == "brief"] == [statement]
    assert pulled.edition_overrides["NFPA 13"]["edition"] == "2025", "a pull never applies setup"
    assert pull_report.setup[0]["kept"] == "the section"

    # Idempotent: one disagreement, one fact.
    again, again_report = merge_project_brief(merged, incoming)
    assert [f["statement"] for f in again.facts].count(statement) == 1
    assert again_report.facts["added"] == 0

    # Two sections saving in turn: "newest export wins" flips the brief's own
    # edition each time, and the disagreement is still ONE fact.
    other_section = _brief(
        updated_at="2099-01-01T00:00:00+00:00",
        edition_overrides={"NFPA 13": {"edition": "2022", "basis": "AHJ adopted the 2021 VCC."}},
        sections=[_section("21 13 13", "2026-09-09T00:00:00+00:00")],
    )
    ping, _ = merge_project_brief(merged, other_section)
    assert ping.edition_overrides["NFPA 13"]["edition"] == "2022"
    newer_again = _brief(
        updated_at="2099-02-01T00:00:00+00:00",
        edition_overrides={"NFPA 13": {"edition": "2025", "basis": "Owner standard 4.2."}},
        sections=[_section("21 30 00", "2026-09-12T00:00:00+00:00")],
    )
    pong, _ = merge_project_brief(ping, newer_again)
    assert pong.edition_overrides["NFPA 13"]["edition"] == "2025"
    conflicts = [f for f in pong.facts if f["source_kind"] == "brief"]
    assert [f["statement"] for f in conflicts] == [statement]


def test_newest_export_wins_the_profile_with_a_field_warning():
    older = _brief(
        profile={
            "city": "Ashburn",
            "state_or_province": "VA",
            "country": "US",
            "client_name": "Client X",
        },
        sections=[_section("21 13 13", "2026-09-01T00:00:00+00:00")],
    )
    newer = _brief(
        updated_at="2026-09-05T00:00:00+00:00",
        profile={"city": "Leesburg", "state_or_province": "VA", "country": "US", "client_name": ""},
        sections=[_section("21 30 00", "2026-09-05T00:00:00+00:00")],
    )

    merged, report = merge_project_brief(older, newer)
    assert merged.profile["city"] == "Leesburg", "the newest export wins"
    assert merged.profile["client_name"] == "Client X", "an empty field never erases one"
    [difference] = report.setup
    assert (difference["field"], difference["brief"], difference["section"]) == (
        "city",
        "Ashburn",
        "Leesburg",
    )
    assert any("'Ashburn'" in w and "'Leesburg'" in w for w in report.warnings)

    # The older export does not win just by arriving second.
    kept, _ = merge_project_brief(newer, older)
    assert kept.profile["city"] == "Leesburg"


# ---------------------------------------------------------------------------
# The whole merge
# ---------------------------------------------------------------------------


def _fork() -> tuple[ProjectBrief, ProjectBrief]:
    research = append_research_round(
        None,
        _one_round([_item("r-1", "Rule 1.")], date="2026-08-01"),
        section="21 13 13",
        round_id="1" * 32,
    )
    grown = append_research_round(
        RequirementsProfile.from_dict(research.to_dict()),
        _one_round([_item("r-2", "Rule 2.")], date="2026-09-05"),
        section="21 30 00",
        round_id="2" * 32,
    )
    existing = _brief(
        research_profile=research.to_dict(),
        reference_docs=[_doc("ref-1", "Owner standard.")],
        facts=[_fact("pf-1", "Fact one.", uid="1" * 32)],
        edition_overrides={"NFPA 13": {"edition": "2022", "basis": "AHJ."}},
        sections=[_section("21 13 13", "2026-09-01T00:00:00+00:00")],
    )
    incoming = _brief(
        updated_at="2026-09-05T00:00:00+00:00",
        research_profile=grown.to_dict(),
        reference_docs=[_doc("ref-1", "Owner standard."), _doc("ref-2", "Cut sheet.")],
        facts=[
            _fact("pf-1", "Fact one.", uid="1" * 32),
            _fact("pf-2", "Fact two.", recorded_in="21 30 00", source_kind="reference", source_ref="ref-2"),
        ],
        edition_overrides={"NFPA 13": {"edition": "2025", "basis": "Owner."}},
        sections=[_section("21 30 00", "2026-09-05T00:00:00+00:00")],
    )
    return existing, incoming


def test_merge_is_idempotent():
    existing, incoming = _fork()
    first, report = merge_project_brief(existing, incoming, now="2026-09-06T00:00:00+00:00")
    assert report.changed and report.assets_changed
    assert first.updated_at == "2026-09-06T00:00:00+00:00"
    assert first.created_at == "2026-09-01T00:00:00+00:00"

    second, second_report = merge_project_brief(first, incoming, now="2026-09-07T00:00:00+00:00")
    assert second.to_dict() == first.to_dict(), "nothing new: not even updated_at moves"
    assert second_report.changed is False and second_report.assets_changed is False
    assert merge_project_brief(first, first)[0].to_dict() == first.to_dict()
    # The inputs were never mutated.
    assert existing.to_dict() == _fork()[0].to_dict()
    assert incoming.to_dict() == _fork()[1].to_dict()


def test_different_projects_refuse():
    with pytest.raises(ProjectBriefMismatchError):
        merge_project_brief(_brief(), _brief(project_id=OTHER_PROJECT))


def test_a_section_record_that_only_moved_its_stamp_is_not_news():
    """Every save rebuilds this section's registry record with a fresh
    ``exported_at``; a record that differs from the file's ONLY by that stamp
    must not rewrite (or reorder) the brief."""
    existing = _brief(
        sections=[
            _section("21 13 13", "2026-09-01T00:00:00+00:00"),
            _section("21 30 00", "2026-09-02T00:00:00+00:00"),
        ]
    )
    restamped = _brief(sections=[_section("21 13 13", "2026-09-09T00:00:00+00:00")])
    merged, report = merge_project_brief(existing, restamped)
    assert report.changed is False and merged.to_dict() == existing.to_dict()

    renamed = dict(_section("21 13 13", "2026-09-09T00:00:00+00:00"), file_name="moved.baspec")
    merged, report = merge_project_brief(existing, _brief(sections=[renamed]))
    assert report.changed is True
    assert [s["number"] for s in merged.sections] == ["21 30 00", "21 13 13"], "export order"
    assert merged.sections[-1]["file_name"] == "moved.baspec"


# ---------------------------------------------------------------------------
# The routes
# ---------------------------------------------------------------------------


def test_merge_route_returns_merged_bytes_and_a_report():
    client = _client()
    session = _rich_session(client)
    exported = client.get("/api/project/brief")
    assert exported.status_code == 200
    # The file on disk has moved on: another section added a fact.
    other_branch = json.loads(exported.content)
    other_branch["facts"].append(
        _fact("pf-9", "Recorded by the other branch.", recorded_in="21 30 00")
    )
    # … and this section recorded one of its own since the export.
    assert (
        client.post("/api/project-facts", json={"statement": "Recorded here since."}).status_code
        == 200
    )

    resp = client.post(
        "/api/project/brief/merge",
        files={"file": ("existing.basproject", json.dumps(other_branch).encode(), "application/json")},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    merged = parse_project_brief(body["brief"].encode("utf-8"))
    statements = [f["statement"] for f in merged.facts]
    assert "Recorded by the other branch." in statements, "the other branch survives"
    assert "Recorded here since." in statements
    assert body["report"]["facts"]["added"] == 1
    assert body["filename"].endswith(".basproject")
    assert body["pull_available"] is True, "this section lacks the other branch's fact"
    assert session.project_link["project_id"] == merged.project_id

    # Another project's brief: a 409 the shell turns into a question.
    stranger = dict(other_branch, project_id=OTHER_PROJECT)
    resp = client.post(
        "/api/project/brief/merge",
        files={"file": ("x.basproject", json.dumps(stranger).encode(), "application/json")},
    )
    assert resp.status_code == 409 and resp.json()["code"] == "different_project"
    # Not a brief at all: a 400 the shell also asks about.
    resp = client.post(
        "/api/project/brief/merge",
        files={"file": ("x.basproject", b"{not a brief", "application/json")},
    )
    assert resp.status_code == 400 and resp.json()["code"] == "brief_unreadable"
    # A streaming turn: refused like the export itself.
    session.turn_active = True
    try:
        resp = client.post(
            "/api/project/brief/merge",
            files={"file": ("x.basproject", exported.content, "application/json")},
        )
    finally:
        session.turn_active = False
    assert resp.status_code == 409 and resp.json()["code"] == "turn_active"


def test_refresh_writes_the_home_brief_atomically_and_updates_the_link(tmp_path, monkeypatch):
    client = _client()
    session, section_file = _project_folder(client, tmp_path)
    _home(session, section_file)
    brief_path = tmp_path / BRIEF_NAME
    assert (
        client.post(
            "/api/project-facts", json={"statement": "Fire pump is electric, 1500 gpm."}
        ).status_code
        == 200
    )

    resp = client.post("/api/project/brief/refresh")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["written"] is True and body["pull_available"] is False
    on_disk = parse_project_brief(brief_path.read_bytes())
    assert "Fire pump is electric, 1500 gpm." in [f["statement"] for f in on_disk.facts]
    assert body["brief_updated_at"] == on_disk.updated_at
    assert session.project_link["brief_updated_at"] == on_disk.updated_at
    assert not list(tmp_path.glob(".buildaspec-brief-*"))

    # Nothing new: nothing is written, not even a timestamp.
    before = brief_path.read_bytes()
    again = client.post("/api/project/brief/refresh")
    assert again.status_code == 200 and again.json()["written"] is False
    assert brief_path.read_bytes() == before

    # A write that fails leaves the old file byte-identical, and says so.
    assert client.post("/api/project-facts", json={"statement": "One more."}).status_code == 200

    def refuse(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(project_brief.os, "replace", refuse)
    failed = client.post("/api/project/brief/refresh")
    monkeypatch.undo()
    assert failed.status_code == 500, failed.text
    assert failed.json()["code"] == "write_failed"
    assert "No space left" in failed.json()["error"]
    assert brief_path.read_bytes() == before
    assert not list(tmp_path.glob(".buildaspec-brief-*")), "no temp file outlives a failure"
    assert session.project_link["brief_updated_at"] == on_disk.updated_at


def test_write_brief_atomically_replaces_whole_or_not_at_all(tmp_path, monkeypatch):
    target = tmp_path / "p.basproject"
    target.write_bytes(b"old")
    write_brief_atomically(str(target), b"new")
    assert target.read_bytes() == b"new"

    def boom(*_args, **_kwargs):
        raise OSError("disk gone")

    monkeypatch.setattr(project_brief.os, "fsync", boom)
    with pytest.raises(OSError):
        write_brief_atomically(str(target), b"newer")
    assert target.read_bytes() == b"new"
    assert [p.name for p in tmp_path.iterdir()] == ["p.basproject"]


def test_refresh_and_pull_refuse_busy_tour_and_no_home(tmp_path):
    client = _client()
    session, section_file = _project_folder(client, tmp_path)
    brief_path = tmp_path / BRIEF_NAME
    before = brief_path.read_bytes()

    # No home: the brief is only ever written in the folder it was found in.
    for route in ("/api/project/brief/refresh", "/api/project/pull"):
        resp = client.post(route)
        assert resp.status_code == 409 and resp.json()["code"] == "no_project_home", route

    _home(session, section_file)
    # Running work: a merge reads the session and must not race a commit.
    session.turn_active = True
    try:
        for route in ("/api/project/brief/refresh", "/api/project/pull"):
            resp = client.post(route)
            assert resp.status_code == 409 and resp.json()["code"] == "workspace_busy", route
    finally:
        session.turn_active = False

    # A tour: the user's project is parked; neither direction touches it.
    sessions.workspace_manager().begin_tutorial(request_id="brief-merge-in-a-tour")
    try:
        for route in ("/api/project/brief/refresh", "/api/project/pull"):
            resp = client.post(route)
            assert resp.status_code == 409 and resp.json()["code"] == "tutorial_active", route
    finally:
        sessions.reset_session()
    assert brief_path.read_bytes() == before


def _sibling_moves_on(brief_path: Path) -> None:
    """Rewrite the brief as a sibling section's save would: a new research
    round, a new document, a new fact, a moved profile field and a different
    NFPA 13 edition."""
    brief = parse_project_brief(brief_path.read_bytes())
    research = RequirementsProfile.from_dict(brief.research_profile)
    grown = append_research_round(
        research,
        _one_round([_item("r-sib", "Sibling rule.")], date="2026-09-10"),
        section="21 30 00",
        round_id="5" * 32,
    )
    brief.research_profile = grown.to_dict()
    # The sibling attached a cut sheet: ref-2 in ITS numbering.
    brief.reference_docs.append(_doc("ref-2", "Fire pump cut sheet.", title="Pump cut sheet"))
    brief.facts.append(
        _fact(
            "pf-4",
            "Fire pump is electric, 1500 gpm.",
            recorded_in="21 30 00",
            source_kind="reference",
            source_ref="ref-2",
        )
    )
    brief.profile = dict(brief.profile, city="Leesburg")
    brief.edition_overrides = {
        **brief.edition_overrides,
        "NFPA 13": {"edition": "2025", "basis": "Owner standard 4.2."},
    }
    brief.sections.append(_section("21 30 00", "2099-01-01T00:00:00+00:00"))
    brief.updated_at = "2099-01-01T00:00:00+00:00"
    write_brief_atomically(str(brief_path), brief_bytes(brief))


def test_pull_installs_only_the_three_append_assets_and_reports_the_rest(tmp_path):
    from backend.qc.apply import matches_current_inputs
    from tests.fakes import audit_grade_qc_result

    client = _client()
    session, section_file = _project_folder(client, tmp_path)
    _home(session, section_file)
    # This section attached a memo of its own since the brief was written —
    # ref-2 HERE, a different document from the sibling's ref-2.
    session.references.add(
        filename="memo.txt", text="Section 1 memo.", block_count=1, kind="txt", token_count=50
    )
    result = audit_grade_qc_result(session, [])
    session.qc.result = result
    session.qc.status = "complete"
    assert matches_current_inputs(session, result, block=True) is True
    readiness_before = client.get("/api/readiness").json()
    document_before = session.doc.to_dict()
    _sibling_moves_on(tmp_path / BRIEF_NAME)

    offered = client.get("/api/project/sections").json()
    assert offered["pull_available"] is True
    assert offered["pull_summary"] == {"available": True, "rounds": 1, "references": 1, "facts": 2}

    resp = client.post("/api/project/pull")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["installed"] == {"rounds": 1, "references": 1, "facts": 2}
    # Research: the sibling's round, replayed after this section's own.
    assert session.research.profile_result.round_count == 2
    assert session.research.profile_result.item("r-sib") is not None
    assert session.research.status == "complete"
    # References: the cut sheet, re-minted past THIS store's counter (its
    # ref-2 is the memo); the memo keeps its id.
    assert [(d.rid, d.title) for d in session.references.docs] == [
        ("ref-1", "Owner fire protection standard"),
        ("ref-2", "memo.txt"),
        ("ref-3", "Pump cut sheet"),
    ]
    # Facts: the sibling's, citing the cut sheet by its NEW id, and the D4
    # conflict fact the edition disagreement produced.
    statements = {f.statement: f for f in session.facts.items}
    pump = statements["Fire pump is electric, 1500 gpm."]
    assert (pump.source_kind, pump.source_ref, pump.recorded_in) == ("reference", "ref-3", "21 30 00")
    conflict = next(f for f in session.facts.items if f.source_kind == "brief")
    assert conflict.statement.startswith("Sections disagree on the NFPA 13 edition")
    assert conflict.status == "assumed"
    # The rest is REPORTED, never applied: the document is the section's own.
    kinds = {(d["kind"], d["field"]) for d in body["report"]["setup"]}
    assert kinds == {("profile", "city"), ("edition", "NFPA 13")}
    assert session.doc.to_dict() == document_before
    assert session.doc.doc.project_profile["city"] == "Ashburn"
    assert session.doc.doc.edition_overrides["NFPA 13"]["edition"] == "2022"
    # Readiness unchanged; the retained Final QC now reads stale (its inputs moved).
    readiness_after = client.get("/api/readiness").json()
    assert readiness_after["ready"] == readiness_before["ready"]
    research_check = {c["id"]: c["ok"] for c in readiness_after["checks"]}["research_complete"]
    assert research_check == {c["id"]: c["ok"] for c in readiness_before["checks"]}["research_complete"]
    assert matches_current_inputs(session, result, block=True) is False
    # The pulled round was carried, not run here: the registry keeps the
    # section's own count, and readiness says where the round came from.
    assert session.project_link["research_rounds_at_seed"] == 1
    assert session.project_link["brief_updated_at"] == "2099-01-01T00:00:00+00:00"
    parenthetical, _nudge = _carried_research_note(session, session.research.profile_result)
    assert "1 of 2 rounds carried from 21 30 00" in parenthetical

    # Pulled: nothing more to offer, and a second pull installs nothing.
    assert client.get("/api/project/sections").json()["pull_available"] is False
    again = client.post("/api/project/pull")
    assert again.status_code == 200
    assert again.json()["installed"] == {"rounds": 0, "references": 0, "facts": 0}


def test_pull_availability_is_a_dry_run_not_a_timestamp(tmp_path):
    """A sibling's save rewrites its registry record without adding an asset;
    that is not a change worth pulling."""
    client = _client()
    session, section_file = _project_folder(client, tmp_path)
    _home(session, section_file)
    brief_path = tmp_path / BRIEF_NAME
    brief = parse_project_brief(brief_path.read_bytes())
    brief.sections.append(_section("21 30 00", "2099-01-01T00:00:00+00:00"))
    brief.updated_at = "2099-01-01T00:00:00+00:00"
    write_brief_atomically(str(brief_path), brief_bytes(brief))

    listing = client.get("/api/project/sections").json()
    assert listing["pull_available"] is False
    assert listing["pull_summary"]["available"] is False


def test_a_save_refreshes_the_home_brief_and_never_fails_on_it(tmp_path, monkeypatch):
    """D2 through the native shell: the save of a homed section refreshes its
    brief; a brief that cannot be refreshed is reported beside a successful
    save, never instead of one."""
    _fake_webview(monkeypatch)
    client = _client()
    session, section_file = _project_folder(client, tmp_path)
    _home(session, section_file)
    session.save_target = str(section_file)
    assert client.post("/api/project-facts", json={"statement": "Saved, then shared."}).status_code == 200
    window = _FakeWindow(dialog_path=None)
    controller = _controller_with(window)

    saved = controller.save_project()

    assert saved["ok"] is True and window.dialog_calls == []
    assert saved["brief_refreshed"] is True and saved["brief_written"] is True
    assert saved["brief_error"] == "" and saved["pull_available"] is False
    assert saved["brief_report"]["facts"]["added"] == 1
    on_disk = parse_project_brief((tmp_path / BRIEF_NAME).read_bytes())
    assert "Saved, then shared." in [f["statement"] for f in on_disk.facts]
    record = next(s for s in on_disk.sections if s["number"] == "21 13 13")
    assert record["file_name"] == section_file.name

    # The brief vanished from the folder: the save still succeeds.
    (tmp_path / BRIEF_NAME).unlink()
    saved = controller.save_project()
    assert saved["ok"] is True
    assert saved["brief_refreshed"] is False and saved["brief_written"] is False
    assert saved["home"] is None, "no brief beside the file: no project folder"


def test_a_refresh_waits_for_another_in_this_process(tmp_path):
    """One read-merge-write at a time: a save-time refresh and a click on
    Update project brief must not interleave into a lost update."""
    from backend import app as app_module

    client = _client()
    session, section_file = _project_folder(client, tmp_path)
    _home(session, section_file)
    assert client.post("/api/project-facts", json={"statement": "First writer."}).status_code == 200
    started = threading.Event()
    done = threading.Event()
    with app_module._BRIEF_FILE_LOCK:
        worker = threading.Thread(
            target=lambda: (started.set(), app_module.refresh_project_brief(), done.set())
        )
        worker.start()
        assert started.wait(timeout=5)
        assert not done.wait(timeout=0.3), "the refresh must wait for the lock"
    worker.join(timeout=10)
    assert done.is_set()
    on_disk = parse_project_brief((tmp_path / BRIEF_NAME).read_bytes())
    assert "First writer." in [f["statement"] for f in on_disk.facts]


def test_a_brief_rewritten_within_the_same_second_still_offers_the_pull(tmp_path):
    """Found by a smoke run: a brief's updated_at is how a section recognises
    the exact brief it last agreed with, and at second resolution a sibling's
    refresh landing in the same second as this section's sync read as "the
    same brief" — the pull offer was silently withheld. Everything below runs
    well inside one second."""
    from tests.test_project_home import _two_section_folder

    client = _client()
    second, first_file, _second_file, _home_dict = _two_section_folder(client, tmp_path)
    assert second.project_home is not None
    assert client.post("/api/project-facts", json={"statement": "Added by 21 30 00."}).status_code == 200
    assert client.post("/api/project/brief/refresh").json()["written"] is True

    opened = client.post("/api/project/open-section", json={"number": "21 13 13"})
    assert opened.status_code == 200, opened.text
    listing = client.get("/api/project/sections").json()
    assert listing["pull_available"] is True
    assert listing["pull_summary"]["facts"] == 1
