"""Research rounds APPEND — pressing Research again never overwrites.

The user may run research more than once in a session (to widen coverage
after the interview turns something up, or to retry a dimension that
failed). Everything below pins the same promise from a different height:
the merge itself, what a second round does to the drafting context, what
the runner does with the meter and with a round that fails, and the whole
thing over the HTTP surface and back out of a project file.
"""
from __future__ import annotations

import json
import threading
import time

from fastapi.testclient import TestClient

from backend import sessions
from backend.app import create_app
from backend.project_profile import ProjectProfile
from backend.research import (
    RequirementsProfile,
    ResearchItem,
    ResearchRunner,
    append_research_round,
)
from backend.research.engine import DimensionStatus, _mint_item_id
from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE
from tests.fakes import FakeClient, SequencedFakeClient, research_response, text_turn, tool_turn
from tests.test_research_api import _PROFILE_EDITS, _parse_sse
from tests.test_research_engine import DIM_KEYS, _item

PROFILE = ProjectProfile("Ashburn", "VA", "USA", "ExampleCo")


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _ritem(
    item_id: str,
    requirement: str,
    *,
    dimension_id: str = "governing_codes",
    category: str = "governing_code",
    **overrides,
) -> ResearchItem:
    fields = {
        "topic": "topic",
        "authority": "",
        "code_reference": "",
        "source_urls": [],
        "accepted_sources": [],
        "grounded": False,
        "confidence": 0.5,
        "actionability": "spec_requirement",
        "notes": "",
    }
    fields.update(overrides)
    return ResearchItem(
        item_id=item_id,
        dimension_id=dimension_id,
        category=category,
        requirement=requirement,
        **fields,  # type: ignore[arg-type]
    )


def _round(
    *,
    items: list[ResearchItem],
    statuses: list[DimensionStatus] | None = None,
    date: str = "2026-07-21",
) -> RequirementsProfile:
    """A single fan-out's raw result, as the engine hands it to the runner."""
    return RequirementsProfile(
        items=list(items),
        dimension_statuses=statuses
        or [DimensionStatus(dimension_id="governing_codes", status="completed")],
        research_date=date,
        project=PROFILE.to_dict(),
    )


# ---------------------------------------------------------------------------
# The merge itself
# ---------------------------------------------------------------------------


def test_a_second_round_adds_to_the_first_instead_of_replacing_it():
    first = append_research_round(None, _round(items=[_ritem("r-a", "Rule A.")]))
    second = append_research_round(
        first, _round(items=[_ritem("r-b", "Rule B.")], date="2026-07-27")
    )

    assert [i.requirement for i in second.items] == ["Rule A.", "Rule B."]
    assert second.round_count == 2
    assert [r.round_index for r in second.rounds] == [1, 2]
    assert [r.research_date for r in second.rounds] == ["2026-07-21", "2026-07-27"]
    assert (second.rounds[1].new_items, second.rounds[1].repeat_items) == (1, 0)
    # Each item keeps the round that found it, and the profile date is the
    # latest round's.
    assert [(i.round_index, i.research_date) for i in second.items] == [
        (1, "2026-07-21"),
        (2, "2026-07-27"),
    ]
    assert second.research_date == "2026-07-27"


def test_a_refound_requirement_is_confirmed_in_place_not_duplicated():
    weak = _ritem(
        "r-a",
        "Rule A.",
        confidence=0.4,
        grounded=False,
        source_urls=["https://a.gov"],
        topic="",
    )
    strong = _ritem(
        "r-a",
        "Rule A.",
        confidence=0.9,
        grounded=True,
        source_urls=["https://b.gov"],
        accepted_sources=["https://b.gov"],
        authority="State Fire Marshal",
        topic="codes",
    )
    first = append_research_round(None, _round(items=[weak]))
    second = append_research_round(
        first, _round(items=[strong], date="2026-07-27")
    )

    assert len(second.items) == 1
    merged = second.items[0]
    # Evidence only ever strengthens.
    assert merged.grounded is True
    assert merged.confidence == 0.9
    assert merged.source_urls == ["https://a.gov", "https://b.gov"]
    assert merged.accepted_sources == ["https://b.gov"]
    assert merged.authority == "State Fire Marshal"
    assert merged.topic == "codes"
    # Found in round 1; last confirmed in round 2.
    assert merged.round_index == 1
    assert merged.research_date == "2026-07-27"
    assert (second.rounds[1].new_items, second.rounds[1].repeat_items) == (0, 1)


def test_an_ungrounded_reassertion_does_not_redate_the_evidence():
    """A round that re-states an item without grounding it confirms nothing.

    The union above keeps round 1's accepted sources and its `grounded`
    flag, so advancing the date too would present that older evidence as
    freshly verified — the exact overstatement [UNVERIFIED] exists to
    prevent.
    """
    grounded = _ritem(
        "r-a",
        "Rule A.",
        grounded=True,
        source_urls=["https://a.gov"],
        accepted_sources=["https://a.gov"],
        confidence=0.8,
    )
    # Round 2 cites something the server tools never retrieved.
    ungrounded = _ritem(
        "r-a",
        "Rule A.",
        grounded=False,
        source_urls=["https://never-retrieved.example"],
        accepted_sources=[],
        confidence=0.6,
    )
    first = append_research_round(None, _round(items=[grounded]))
    second = append_research_round(
        first, _round(items=[ungrounded], date="2026-07-27")
    )

    merged = second.items[0]
    assert merged.grounded is True
    assert merged.accepted_sources == ["https://a.gov"]
    # The evidence is still round 1's, so the date is too.
    assert merged.research_date == "2026-07-21"
    assert "as of 2026-07-21" in second.render_text()
    # It still counts as re-found for the round's own tally.
    assert second.rounds[1].repeat_items == 1

    # And the reverse: fresh grounding DOES advance the date.
    third = append_research_round(
        second,
        _round(
            items=[
                _ritem(
                    "r-a",
                    "Rule A.",
                    grounded=True,
                    accepted_sources=["https://c.gov"],
                )
            ],
            date="2026-08-01",
        ),
    )
    assert third.items[0].research_date == "2026-08-01"


def test_the_previous_profile_is_never_mutated_by_a_later_round():
    """The conversation thread may be rendering it while the merge runs."""
    first = append_research_round(
        None, _round(items=[_ritem("r-a", "Rule A.", confidence=0.4)])
    )
    before = first.to_dict()

    append_research_round(
        first,
        _round(
            items=[
                _ritem("r-a", "Rule A.", confidence=0.9, grounded=True),
                _ritem("r-b", "Rule B."),
            ],
            date="2026-07-27",
        ),
    )

    assert first.to_dict() == before
    assert len(first.items) == 1 and first.items[0].confidence == 0.4


def test_cumulative_dimension_view_sums_spend_but_not_findings():
    shared = _ritem("r-a", "Rule A.", grounded=True)
    first = append_research_round(
        None,
        _round(
            items=[shared],
            statuses=[
                DimensionStatus(
                    dimension_id="governing_codes",
                    status="completed",
                    title="Governing codes",
                    web_search_requests=9,
                    input_tokens=100,
                )
            ],
        ),
    )
    second = append_research_round(
        first,
        _round(
            items=[shared, _ritem("r-b", "Rule B.")],
            statuses=[
                DimensionStatus(
                    dimension_id="governing_codes",
                    status="completed",
                    title="Governing codes",
                    web_search_requests=4,
                    input_tokens=50,
                )
            ],
            date="2026-07-27",
        ),
    )

    status = second.dimension_statuses[0]
    # Billed work happened twice; the requirement exists once.
    assert status.web_search_requests == 13
    assert status.input_tokens == 150
    assert status.item_count == 2
    assert status.grounded_count == 1
    assert second.usage_total()["input_tokens"] == 150


def test_a_dimension_that_failed_this_round_keeps_its_earlier_findings():
    first = append_research_round(
        None,
        _round(
            items=[_ritem("r-a", "Rule A.")],
            statuses=[
                DimensionStatus(dimension_id="governing_codes", status="completed"),
                DimensionStatus(
                    dimension_id="ahj_requirements",
                    status="failed",
                    error="timed out",
                ),
            ],
        ),
    )
    second = append_research_round(
        first,
        _round(
            items=[
                _ritem(
                    "r-c", "AHJ rule.", dimension_id="ahj_requirements",
                    category="ahj_requirement",
                )
            ],
            statuses=[
                DimensionStatus(
                    dimension_id="governing_codes",
                    status="failed",
                    error="rate limited",
                ),
                DimensionStatus(
                    dimension_id="ahj_requirements", status="completed"
                ),
            ],
            date="2026-07-27",
        ),
    )

    by_id = {s.dimension_id: s for s in second.dimension_statuses}
    # Completed in round 1, failed in round 2: the findings are still real,
    # so cumulatively it is complete — with the latest failure still stated.
    assert by_id["governing_codes"].status == "completed"
    assert by_id["governing_codes"].error == "rate limited"
    assert by_id["ahj_requirements"].status == "completed"
    assert second.failed_dimensions == 0
    # The round's own record keeps the unmerged truth.
    assert [s.status for s in second.rounds[1].dimension_statuses] == [
        "failed",
        "completed",
    ]
    assert second.rounds[1].failed_dimensions == 1


# ---------------------------------------------------------------------------
# What the drafting context sees
# ---------------------------------------------------------------------------


def test_one_round_renders_exactly_as_it_always_has():
    profile = append_research_round(
        None, _round(items=[_ritem("r-a", "Rule A.", accepted_sources=["https://a.gov"], grounded=True)])
    )
    text = profile.render_text()
    assert (
        "Generated by location/client research (1 of 1 dimensions completed), "
        "researched 2026-07-21. Edition and process facts are as-of that date."
    ) in text
    assert "rounds" not in text
    assert "as of" not in text


def test_more_than_one_round_dates_every_item_it_renders():
    first = append_research_round(None, _round(items=[_ritem("r-a", "Rule A.")]))
    second = append_research_round(
        first, _round(items=[_ritem("r-b", "Rule B.")], date="2026-07-27")
    )
    text = second.render_text()

    assert "over 2 rounds" in text
    assert "latest round researched 2026-07-27" in text
    assert (
        "Each item is dated by the round that last grounded it in a "
        "retrieved source" in text
    )
    # An item from round 1 is not claimed as-of today.
    line_a = next(line for line in text.splitlines() if "Rule A." in line)
    line_b = next(line for line in text.splitlines() if "Rule B." in line)
    assert "as of 2026-07-21" in line_a
    assert "as of 2026-07-27" in line_b
    assert second.render_text() == text  # still deterministic


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_rounds_survive_a_serialization_round_trip():
    first = append_research_round(None, _round(items=[_ritem("r-a", "Rule A.")]))
    second = append_research_round(
        first, _round(items=[_ritem("r-b", "Rule B.")], date="2026-07-27")
    )

    again = RequirementsProfile.from_dict(json.loads(json.dumps(second.to_dict())))
    assert again is not None
    assert again.round_count == 2
    assert [(i.round_index, i.research_date) for i in again.items] == [
        (1, "2026-07-21"),
        (2, "2026-07-27"),
    ]
    assert again.render_text() == second.render_text()


def test_a_profile_saved_before_rounds_existed_becomes_round_one():
    """Old project files carry no rounds — they are one round, and appending
    to them must number the next one 2, not 1."""
    legacy = RequirementsProfile.from_dict(
        {
            "items": [
                {
                    "item_id": "r-a",
                    "dimension_id": "governing_codes",
                    "category": "governing_code",
                    "requirement": "Rule A.",
                    "confidence": 0.5,
                }
            ],
            "dimension_statuses": [
                {"dimension_id": "governing_codes", "status": "completed"}
            ],
            "research_date": "2026-07-21",
            "project": PROFILE.to_dict(),
        }
    )
    assert legacy is not None
    assert legacy.round_count == 1
    # Items are back-dated to the round that produced them.
    assert legacy.items[0].round_index == 1
    assert legacy.items[0].research_date == "2026-07-21"
    # …and it renders unchanged, because one round is still one round.
    assert "as of" not in legacy.render_text()

    appended = append_research_round(
        legacy, _round(items=[_ritem("r-b", "Rule B.")], date="2026-07-27")
    )
    assert appended.round_count == 2
    assert [i.item_id for i in appended.items] == ["r-a", "r-b"]


def test_a_malformed_collection_degrades_instead_of_raising():
    """`from_dict` promises garbage degrades to None, and the project-load
    endpoint only translates ProjectPackageError/ValueError — so a scalar
    where a list belongs must not escape as a TypeError (a 500 that blocks
    the whole project from opening)."""
    good = {
        "items": [
            {
                "item_id": "r-a",
                "dimension_id": "governing_codes",
                "category": "governing_code",
                "requirement": "Rule A.",
            }
        ],
        "dimension_statuses": [
            {"dimension_id": "governing_codes", "status": "completed"}
        ],
        "research_date": "2026-07-21",
    }
    for key in ("rounds", "dimension_statuses", "items"):
        for bad in (1, "x", {"round_index": 1}):
            payload = {**good, key: bad}
            profile = RequirementsProfile.from_dict(payload)  # must not raise
            if key == "items":
                # Statuses alone still make a usable (item-less) profile.
                assert profile is not None and profile.items == []
            else:
                assert profile is not None
                assert [i.item_id for i in profile.items] == ["r-a"]
    # Everything malformed at once is simply not a profile.
    assert RequirementsProfile.from_dict({"items": 1, "dimension_statuses": 1}) is None


def test_a_stopped_round_is_still_tagged_with_its_round_number():
    runner = ResearchRunner()
    runner.profile_result = append_research_round(
        None, _round(items=[_ritem("r-a", "Rule A.")])
    )

    release = threading.Event()

    class _Blocking:
        def __init__(self) -> None:
            self.messages = self

        def stream(self, **_request):
            release.wait(timeout=10)
            raise RuntimeError("aborted")

    settled = threading.Event()
    assert runner.start(
        module=HYPERSCALE_FIRE,
        project_profile=PROFILE,
        client=_Blocking(),
        model="claude-sonnet-5",
        max_tokens=4096,
        on_settled=settled.set,
    )
    # Stop before any dimension has emitted — the terminal event is the
    # only entry in this round's log, so it has to name the round itself.
    assert runner.stop() is True
    failed = [e for e in runner.events if e["type"] == "research_failed"]
    assert len(failed) == 1
    assert failed[0]["round"] == 2

    release.set()
    assert settled.wait(timeout=10)


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------


def _scripts_for_rounds(*rounds: dict[str, list]) -> dict[str, list]:
    """Queue one scripted turn per round for every fire-module dimension."""
    scripts: dict[str, list] = {key: [] for key in DIM_KEYS.values()}
    for per_round in rounds:
        for dim_id, key in DIM_KEYS.items():
            scripts[key].append(
                per_round.get(
                    dim_id,
                    research_response(items=[], searched_urls=["https://x.gov"]),
                )
            )
    return scripts


def _run_round(runner: ResearchRunner, client, usage_sink) -> None:
    settled = threading.Event()
    assert runner.start(
        module=HYPERSCALE_FIRE,
        project_profile=PROFILE,
        client=client,
        model="claude-sonnet-5",
        max_tokens=4096,
        on_settled=settled.set,
        usage_sink=usage_sink,
    )
    assert settled.wait(timeout=10)


def test_runner_accumulates_rounds_and_meters_each_one_once():
    client = SequencedFakeClient(
        _scripts_for_rounds(
            {
                "governing_codes": research_response(
                    items=[_item("Rule A.", ["https://a.gov"])],
                    searched_urls=["https://a.gov"],
                    tokens={"input": 100},
                )
            },
            {
                "governing_codes": research_response(
                    items=[_item("Rule B.", ["https://b.gov"])],
                    searched_urls=["https://b.gov"],
                    tokens={"input": 40},
                )
            },
        )
    )
    runner = ResearchRunner()
    billed: list[dict] = []

    _run_round(runner, client, billed.append)
    assert runner.status == "complete"
    assert [i.requirement for i in runner.profile_result.items] == ["Rule A."]

    _run_round(runner, client, billed.append)
    assert runner.status == "complete"
    profile = runner.profile_result
    assert [i.requirement for i in profile.items] == ["Rule A.", "Rule B."]
    assert profile.round_count == 2

    # The meter is fed each round's OWN spend. The cumulative profile total
    # is 140; billing it per round would charge 100 then 140.
    assert [u["input_tokens"] for u in billed] == [100, 40]
    assert profile.usage_total()["input_tokens"] == 140

    # The event log is this round's; the profile is the accumulation.
    types = [e["type"] for e in runner.events]
    assert types.count("research_complete") == 1
    complete = next(e for e in runner.events if e["type"] == "research_complete")
    assert complete["round"] == 2
    assert complete["item_count"] == 2  # cumulative
    assert complete["new_item_count"] == 1  # this round's contribution


def test_a_failed_round_keeps_the_rounds_that_succeeded():
    client = SequencedFakeClient(
        _scripts_for_rounds(
            {
                "governing_codes": research_response(
                    items=[_item("Rule A.", ["https://a.gov"])],
                    searched_urls=["https://a.gov"],
                )
            },
            # Round 2: every dimension dies -> ResearchFanoutError.
            {dim: RuntimeError("network down") for dim in DIM_KEYS},
        )
    )
    runner = ResearchRunner()
    _run_round(runner, client, lambda _u: None)
    assert runner.status == "complete"

    _run_round(runner, client, lambda _u: None)
    assert runner.status == "failed"
    # The failure is loud, and says what survived it.
    assert "Earlier research rounds are unchanged" in runner.error
    assert [i.requirement for i in runner.profile_result.items] == ["Rule A."]
    assert runner.profile_result.round_count == 1
    # A snapshot still carries the retained profile for the drawer/report.
    assert runner.snapshot()["profile"]["items"][0]["requirement"] == "Rule A."


def test_stopping_a_later_round_discards_only_that_round():
    release = threading.Event()

    class _Blocking:
        def __init__(self) -> None:
            self.messages = self

        def stream(self, **_request):
            release.wait(timeout=10)
            raise RuntimeError("aborted")

    client = SequencedFakeClient(
        _scripts_for_rounds(
            {
                "governing_codes": research_response(
                    items=[_item("Rule A.", ["https://a.gov"])],
                    searched_urls=["https://a.gov"],
                )
            }
        )
    )
    runner = ResearchRunner()
    _run_round(runner, client, lambda _u: None)
    assert runner.profile_result.round_count == 1

    settled = threading.Event()
    assert runner.start(
        module=HYPERSCALE_FIRE,
        project_profile=PROFILE,
        client=_Blocking(),
        model="claude-sonnet-5",
        max_tokens=4096,
        on_settled=settled.set,
    )
    assert runner.stop() is True
    assert runner.status == "failed"
    assert "this round's progress was discarded" in runner.error
    assert "Earlier research rounds are unchanged" in runner.error

    # The abandoned thread unwinds; it must not touch the retained profile.
    release.set()
    assert settled.wait(timeout=10)
    assert [i.requirement for i in runner.profile_result.items] == ["Rule A."]
    assert runner.profile_result.round_count == 1


# ---------------------------------------------------------------------------
# End to end over the API + project file
# ---------------------------------------------------------------------------


def _client() -> TestClient:
    client = TestClient(create_app())
    client.post("/api/session/reset", json={"module_id": "hyperscale_fire"})
    return client


def _record_profile(client: TestClient, monkeypatch) -> None:
    fake = FakeClient([tool_turn(["Recorded."], _PROFILE_EDITS), text_turn(["Done."])])
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    resp = client.post("/api/chat", json={"message": "Ashburn VA, ExampleCo"})
    assert _parse_sse(resp.text)[-1]["type"] == "turn_complete"


def _wait_terminal(client: TestClient, timeout_s: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        snapshot = client.get("/api/research/status").json()
        if snapshot["status"] in ("complete", "failed"):
            return snapshot
        time.sleep(0.02)
    raise AssertionError("research never reached a terminal state")


def test_pressing_research_twice_accumulates_over_the_api(monkeypatch):
    client = _client()
    _record_profile(client, monkeypatch)

    # Round 2 re-finds the round-1 requirement verbatim (so the content
    # hash collides — the real join key) and adds one of its own. The
    # site_environment finding exists ONLY in round 1: nothing but
    # accumulation can carry it through the second round.
    repeat = _item("VCC 2021 governs.", ["https://dhcd.virginia.gov/vcc"])
    fake = SequencedFakeClient(
        _scripts_for_rounds(
            {
                "governing_codes": research_response(
                    items=[repeat],
                    searched_urls=[],  # ungrounded: nothing was retrieved
                ),
                "site_environment": research_response(
                    items=[
                        _item(
                            "SDC B at the site.",
                            ["https://usgs.gov/ws"],
                            category="site_environment",
                        )
                    ],
                    searched_urls=["https://usgs.gov/ws"],
                ),
            },
            {
                "governing_codes": research_response(
                    items=[repeat, _item("New rule.", ["https://x.gov"])],
                    searched_urls=["https://dhcd.virginia.gov/vcc", "https://x.gov"],
                )
            },
        )
    )
    monkeypatch.setattr("backend.app.get_client", lambda: fake)

    assert client.post("/api/research/start").json()["ok"] is True
    first = _wait_terminal(client)
    assert first["status"] == "complete"
    assert len(first["profile"]["items"]) == 2
    assert {i["requirement"] for i in first["profile"]["items"]} == {
        "VCC 2021 governs.",
        "SDC B at the site.",
    }
    assert next(
        i for i in first["profile"]["items"]
        if i["requirement"] == "VCC 2021 governs."
    )["grounded"] is False

    assert client.post("/api/research/start").json()["ok"] is True
    second = _wait_terminal(client)
    assert second["status"] == "complete"

    profile = second["profile"]
    items = {i["requirement"]: i for i in profile["items"]}
    assert set(items) == {
        "VCC 2021 governs.",
        "SDC B at the site.",  # round 1 only — kept, not overwritten
        "New rule.",
    }
    assert items["SDC B at the site."]["round_index"] == 1
    # The re-found item kept its id and its round, and gained the grounding
    # round 2 established for it.
    assert items["VCC 2021 governs."]["item_id"] == _mint_item_id(
        "governing_codes", "governing_code", "VCC 2021 governs."
    )
    assert items["VCC 2021 governs."]["round_index"] == 1
    assert items["VCC 2021 governs."]["grounded"] is True
    assert items["New rule."]["round_index"] == 2

    assert [r["round_index"] for r in profile["rounds"]] == [1, 2]
    assert profile["rounds"][1]["new_items"] == 1
    assert profile["rounds"][1]["repeat_items"] == 1

    # Both rounds are in the model's context, dated.
    session = sessions.get_session()
    rendered = session.research.profile_result.render_text()
    assert "over 2 rounds" in rendered
    for requirement in ("VCC 2021 governs.", "SDC B at the site.", "New rule."):
        assert requirement in rendered


def test_rounds_survive_save_and_resume_and_keep_counting(monkeypatch):
    client = _client()
    _record_profile(client, monkeypatch)
    fake = SequencedFakeClient(
        _scripts_for_rounds(
            {
                "governing_codes": research_response(
                    items=[_item("Rule A.", ["https://a.gov"])],
                    searched_urls=["https://a.gov"],
                )
            },
            {
                "governing_codes": research_response(
                    items=[_item("Rule B.", ["https://b.gov"])],
                    searched_urls=["https://b.gov"],
                )
            },
        )
    )
    monkeypatch.setattr("backend.app.get_client", lambda: fake)
    for _ in range(2):
        assert client.post("/api/research/start").json()["ok"] is True
        assert _wait_terminal(client)["status"] == "complete"

    saved = client.get("/api/project/save").content
    assert client.post("/api/session/reset", json={"module_id": "hyperscale_fire"}).status_code == 200
    assert client.get("/api/research/status").json()["status"] == "idle"

    resp = client.post(
        "/api/project/load-file",
        files={"file": ("p.baspec", saved, "application/octet-stream")},
    )
    assert resp.status_code == 200, resp.text

    restored = client.get("/api/research/status").json()
    assert restored["status"] == "complete"
    assert [r["round_index"] for r in restored["profile"]["rounds"]] == [1, 2]
    assert {i["requirement"] for i in restored["profile"]["items"]} == {
        "Rule A.",
        "Rule B.",
    }

    # A third round on the resumed session continues the numbering.
    client3 = SequencedFakeClient(
        _scripts_for_rounds(
            {
                "governing_codes": research_response(
                    items=[_item("Rule C.", ["https://c.gov"])],
                    searched_urls=["https://c.gov"],
                )
            }
        )
    )
    monkeypatch.setattr("backend.app.get_client", lambda: client3)
    assert client.post("/api/research/start").json()["ok"] is True
    final = _wait_terminal(client)["profile"]
    assert [r["round_index"] for r in final["rounds"]] == [1, 2, 3]
    assert {i["requirement"] for i in final["items"]} == {
        "Rule A.",
        "Rule B.",
        "Rule C.",
    }
