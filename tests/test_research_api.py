"""Research over the API surface: profile op, start/status/stream lifecycle,
context splice, provenance links, and project round-trip."""
from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from backend.app import create_app
from backend import sessions
from tests.fakes import (
    FakeClient,
    SequencedFakeClient,
    request_context_text,
    research_response,
    text_turn,
    tool_turn,
)
from tests.test_research_engine import DIM_KEYS, _item, _scripts


def _client() -> TestClient:
    return TestClient(create_app())


def _select_fire(client: TestClient) -> None:
    """Select the curated fire module before a real research fan-out.

    Research routes scripted turns by the fire module's dimension-message
    substrings (``DIM_KEYS``); the neutral registry default is now the generic
    module, whose dimension messages are discipline-parameterized and would not
    match — and it also gates ``/api/research/start`` on a stated discipline.
    """
    client.post("/api/session/reset", json={"module_id": "hyperscale_fire"})


def _parse_sse(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def _patch_chat_client(monkeypatch, fake) -> None:
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)


def _patch_research_client(monkeypatch, fake) -> None:
    monkeypatch.setattr("backend.app.get_client", lambda: fake)


_PROFILE_EDITS = {
    "edits": [
        {
            "action": "set_project_profile",
            "target_id": "sec",
            "city": "Ashburn",
            "state": "Virginia",
            "country": "USA",
            "client": "ExampleCo",
        }
    ]
}


def _record_profile(client: TestClient, monkeypatch) -> None:
    fake = FakeClient(
        [tool_turn(["Recorded."], _PROFILE_EDITS), text_turn(["Done."])]
    )
    _patch_chat_client(monkeypatch, fake)
    resp = client.post(
        "/api/chat", json={"message": "Ashburn VA, client ExampleCo"}
    )
    assert _parse_sse(resp.text)[-1]["type"] == "turn_complete"


def _wait_terminal(client: TestClient, timeout_s: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        snapshot = client.get("/api/research/status").json()
        if snapshot["status"] in ("complete", "failed"):
            return snapshot
        time.sleep(0.05)
    raise AssertionError("research did not settle in time")


def test_profile_op_normalizes_and_gates_research(monkeypatch):
    client = _client()
    # Incomplete profile → 400.
    resp = client.post("/api/research/start")
    assert resp.status_code == 400
    assert "incomplete" in resp.json()["error"]

    _record_profile(client, monkeypatch)
    doc = client.get("/api/doc").json()
    assert doc["doc"]["project_profile"] == {
        "city": "Ashburn",
        "state_or_province": "VA",
        "country": "US",
        "client_name": "ExampleCo",
    }
    assert doc["profile_complete"] is True
    assert doc["research_status"] == "idle"
    # The applied op reported completeness.
    history = sessions.get_session().history
    tool_result = history[2]["content"][0]
    assert '"complete": true' in tool_result["content"]


_PARTIAL_PROFILE_EDITS = {
    "edits": [
        {
            "action": "set_project_profile",
            "target_id": "sec",
            "city": "Ashburn",
            "state": "Virginia",
        }
    ]
}


def test_profile_status_block_lists_every_missing_field_before_recording(
    monkeypatch,
):
    client = _client()
    fake = FakeClient([text_turn(["Let's get started."])])
    _patch_chat_client(monkeypatch, fake)
    client.post("/api/chat", json={"message": "hello"})

    context = request_context_text(fake.messages.last_request)
    assert "PROJECT PROFILE (city, state/province, country, client):" in context
    assert "- city: [not yet recorded]" in context
    assert "- state/province: [not yet recorded]" in context
    assert "- country: [not yet recorded]" in context
    assert "- client: [not yet recorded]" in context
    assert (
        "Incomplete — missing city, state/province, country, client."
        in context
    )


def test_profile_status_block_narrows_as_fields_arrive_then_completes(
    monkeypatch,
):
    """The PROJECT PROFILE block (rendered every turn) is what lets the
    model chase a still-missing field incrementally instead of only once:
    it narrows the "missing" list turn by turn and reports "Complete." the
    moment the last field lands, whichever entry path (chat or the panel
    form) supplied it."""
    client = _client()
    fake = FakeClient(
        [tool_turn(["Noted."], _PARTIAL_PROFILE_EDITS), text_turn(["Continuing."])]
    )
    _patch_chat_client(monkeypatch, fake)
    resp = client.post("/api/chat", json={"message": "Ashburn, Virginia"})
    assert _parse_sse(resp.text)[-1]["type"] == "turn_complete"

    fake2 = FakeClient([text_turn(["Still missing some."])])
    _patch_chat_client(monkeypatch, fake2)
    client.post("/api/chat", json={"message": "continue"})
    context = request_context_text(fake2.messages.last_request)
    assert "- city: Ashburn" in context
    assert "- state/province: VA" in context
    assert "- country: [not yet recorded]" in context
    assert "- client: [not yet recorded]" in context
    assert "Incomplete — missing country, client." in context

    _record_profile(client, monkeypatch)
    fake3 = FakeClient([text_turn(["All set."])])
    _patch_chat_client(monkeypatch, fake3)
    client.post("/api/chat", json={"message": "continue"})
    final_context = request_context_text(fake3.messages.last_request)
    assert "- city: Ashburn" in final_context
    assert "- state/province: VA" in final_context
    assert "- country: US" in final_context
    assert "- client: ExampleCo" in final_context
    assert "Complete." in final_context
    assert "Incomplete" not in final_context


def test_profile_op_rejects_unknown_country(monkeypatch):
    fake = FakeClient(
        [
            tool_turn(
                [],
                {
                    "edits": [
                        {
                            "action": "set_project_profile",
                            "target_id": "sec",
                            "country": "France",
                        }
                    ]
                },
            ),
            text_turn(["Sorry."]),
        ]
    )
    _patch_chat_client(monkeypatch, fake)
    client = _client()
    client.post("/api/chat", json={"message": "France"})
    tool_result = sessions.get_session().history[2]["content"][0]
    assert tool_result["is_error"] is True
    assert "country" in tool_result["content"]
    assert sessions.get_session().doc.doc.project_profile == {}


def test_research_lifecycle_stream_and_context_splice(monkeypatch):
    client = _client()
    _select_fire(client)
    _record_profile(client, monkeypatch)

    research_fake = SequencedFakeClient(
        _scripts(
            governing_codes=[
                research_response(
                    items=[
                        _item(
                            "2021 VCC governs; NFPA 13-2019 referenced.",
                            ["https://dhcd.virginia.gov/vcc"],
                        )
                    ],
                    queries=["virginia construction code edition"],
                    searched_urls=["https://dhcd.virginia.gov/vcc"],
                )
            ]
        )
    )
    _patch_research_client(monkeypatch, research_fake)

    assert client.post("/api/research/start").json()["ok"] is True
    snapshot = _wait_terminal(client)
    assert snapshot["status"] == "complete"
    assert snapshot["profile"]["items"][0]["grounded"] is True
    kinds = [e["type"] for e in snapshot["events"]]
    assert kinds[0] == "research_started"
    assert kinds[-1] == "research_complete"
    assert kinds.count("dimension_complete") == 4
    # Workers narrate live: every dimension announces itself, and the
    # governing_codes search query rides the log with its real text.
    assert kinds.count("dimension_started") == 4
    search = next(e for e in snapshot["events"] if e["type"] == "dimension_search")
    assert search["query"] == "virginia construction code edition"
    assert search["dimension_id"] == "governing_codes"

    # The SSE stream replays the full run and closes with the sentinel.
    stream = client.get("/api/research/stream")
    events = _parse_sse(stream.text)
    assert events[0]["type"] == "research_started"
    assert events[-1] == {"type": "stream_end", "status": "complete"}

    # The doc payload reflects the terminal state.
    assert client.get("/api/doc").json()["research_status"] == "complete"

    # The next chat turn's dynamic block carries the profile facts.
    chat_fake = FakeClient([text_turn(["Noted."])])
    _patch_chat_client(monkeypatch, chat_fake)
    client.post("/api/chat", json={"message": "continue"})
    dynamic = request_context_text(chat_fake.messages.last_request)
    assert "PROJECT REQUIREMENTS PROFILE" in dynamic
    assert "2021 VCC governs" in dynamic
    # Stable prompt stayed free of run-specific research data (cacheable —
    # it may MENTION the profile in its policy text, but never carry facts).
    stable = chat_fake.messages.last_request["system"][0]["text"]
    assert "2021 VCC governs" not in stable


def test_research_double_start_conflicts_and_total_failure_surfaces(monkeypatch):
    client = _client()
    _select_fire(client)
    _record_profile(client, monkeypatch)

    all_fail = SequencedFakeClient(
        {key: [RuntimeError("dead")] for key in DIM_KEYS.values()}
    )
    _patch_research_client(monkeypatch, all_fail)
    assert client.post("/api/research/start").json()["ok"] is True
    snapshot = _wait_terminal(client)
    assert snapshot["status"] == "failed"
    assert "All 4" in snapshot["error"]
    assert "profile" not in snapshot

    # A failed run can be relaunched (fresh fake with working scripts).
    _patch_research_client(
        monkeypatch, SequencedFakeClient(_scripts())
    )
    assert client.post("/api/research/start").json()["ok"] is True
    assert _wait_terminal(client)["status"] == "complete"


def test_source_item_id_provenance_round_trips(monkeypatch):
    edits = {
        "edits": [
            {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "text": "Comply with the 2021 VCC.",
                "status": "confirmed",
                "source_item_id": "r-abc123def456",
            },
        ]
    }
    fake = FakeClient([tool_turn(["Drafting."], edits), text_turn(["Done."])])
    _patch_chat_client(monkeypatch, fake)
    client = _client()
    client.post("/api/chat", json={"message": "draft it"})

    para = client.get("/api/doc").json()["doc"]["parts"][0]["articles"][0][
        "paragraphs"
    ][0]
    assert para["source_item_id"] == "r-abc123def456"

    # Survives the project round-trip.
    project = json.loads(json.dumps(sessions.project_payload(sessions.get_session())))
    client.post("/api/session/reset")
    loaded = client.post("/api/project/load", json=project).json()
    para = loaded["doc"]["parts"][0]["articles"][0]["paragraphs"][0]
    assert para["source_item_id"] == "r-abc123def456"


def test_research_profile_survives_project_round_trip(monkeypatch):
    client = _client()
    _select_fire(client)
    _record_profile(client, monkeypatch)
    _patch_research_client(
        monkeypatch,
        SequencedFakeClient(
            _scripts(
                governing_codes=[
                    research_response(
                        items=[_item("Grounded fact.", ["https://a.gov"])],
                        searched_urls=["https://a.gov"],
                    )
                ]
            )
        ),
    )
    client.post("/api/research/start")
    _wait_terminal(client)

    project = json.loads(json.dumps(sessions.project_payload(sessions.get_session())))
    assert project["requirements_profile"]["items"]

    client.post("/api/session/reset")
    assert client.get("/api/research/status").json()["status"] == "idle"

    client.post("/api/project/load", json=project)
    snapshot = client.get("/api/research/status").json()
    assert snapshot["status"] == "complete"
    assert snapshot["events"][0].get("restored") is True
    assert any(
        i["requirement"] == "Grounded fact." for i in snapshot["profile"]["items"]
    )

    # And the restored profile reaches the next turn's context.
    chat_fake = FakeClient([text_turn(["Hi."])])
    _patch_chat_client(monkeypatch, chat_fake)
    client.post("/api/chat", json={"message": "resume"})
    assert "Grounded fact." in request_context_text(
        chat_fake.messages.last_request
    )


def test_session_reset_abandons_running_research(monkeypatch):
    client = _client()
    _select_fire(client)
    _record_profile(client, monkeypatch)

    import threading

    release = threading.Event()

    class _BlockingClient:
        """Blocks every dimension until released, then fails."""

        def __init__(self):
            self.messages = self

        def stream(self, **_request):
            release.wait(timeout=5)
            raise RuntimeError("aborted")

    _patch_research_client(monkeypatch, _BlockingClient())
    assert client.post("/api/research/start").json()["ok"] is True
    assert client.get("/api/research/status").json()["status"] == "running"

    old_runner = sessions.get_session().research
    client.post("/api/session/reset")
    # The fresh session shows a fresh, idle runner immediately.
    assert client.get("/api/research/status").json()["status"] == "idle"
    release.set()
    # The abandoned run settles into the OLD runner without touching the
    # fresh session.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not old_runner.is_terminal:
        time.sleep(0.05)
    assert old_runner.status == "failed"
    assert client.get("/api/research/status").json()["status"] == "idle"


# ---------------------------------------------------------------------------
# Batch 10: session discipline threading + the generic-module backstop
# ---------------------------------------------------------------------------

# Substrings unique to each GENERIC-module dimension message. Three of the
# four embed the session discipline — routing scripted turns on them is
# itself the proof that {discipline} rendered into every dimension prompt.
_GENERIC_DIM_KEYS = {
    "governing_codes": "governing construction codes for Electrical work",
    "ahj_requirements": "authority having jurisdiction over Electrical work",
    "client_standards": "publishes design and construction standards",
    "site_environment": "site and environmental factors",
}


def test_research_start_requires_discipline_for_generic_module(monkeypatch):
    client = _client()
    resp = client.post("/api/session/reset", json={"module_id": "generic"})
    assert resp.json()["discipline"] == ""
    _record_profile(client, monkeypatch)
    resp = client.post("/api/research/start")
    assert resp.status_code == 400
    assert "discipline" in resp.json()["error"].lower()


def test_generic_research_threads_discipline_into_every_dimension(monkeypatch):
    client = _client()
    client.post(
        "/api/session/reset",
        json={"module_id": "generic", "discipline": "Electrical"},
    )
    _record_profile(client, monkeypatch)

    scripts = {
        key: [research_response(items=[], searched_urls=["https://x.gov"])]
        for key in _GENERIC_DIM_KEYS.values()
    }
    fake = SequencedFakeClient(scripts)
    _patch_research_client(monkeypatch, fake)
    assert client.post("/api/research/start").json()["ok"] is True
    snapshot = _wait_terminal(client)
    # All four dimensions completed — every message matched its
    # discipline-bearing routing key.
    assert snapshot["status"] == "complete"
    statuses = snapshot["profile"]["dimension_statuses"]
    assert all(s["status"] == "completed" for s in statuses)
    # And the fan-out headers named the discipline explicitly (four
    # dimension requests — never vacuous).
    assert len(fake.requests) == 4
    assert all(
        "Discipline: Electrical." in req["messages"][0]["content"]
        for req in fake.requests
    )


def test_generic_research_prefers_document_identity_over_legacy_discipline(
    monkeypatch,
):
    client = _client()
    client.post(
        "/api/session/reset",
        json={"module_id": "generic", "discipline": "Electrical"},
    )
    identity = client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "set_project_identity",
                    "target_id": "sec",
                    "discipline": "Plumbing",
                }
            ]
        },
    )
    assert identity.status_code == 200, identity.text
    _record_profile(client, monkeypatch)

    keys = {
        "governing construction codes for Plumbing work",
        "authority having jurisdiction over Plumbing work",
        "publishes design and construction standards",
        "site and environmental factors",
    }
    fake = SequencedFakeClient(
        {
            key: [research_response(items=[], searched_urls=["https://x.gov"])]
            for key in keys
        }
    )
    _patch_research_client(monkeypatch, fake)
    assert client.post("/api/research/start").json()["ok"] is True
    assert _wait_terminal(client)["status"] == "complete"
    assert len(fake.requests) == 4
    assert all(
        "Discipline: Plumbing." in req["messages"][0]["content"]
        and "Discipline: Electrical." not in req["messages"][0]["content"]
        for req in fake.requests
    )


# ---------------------------------------------------------------------------
# Readiness follows declared coverage, not the runner's status (Chunk 3.2)
# ---------------------------------------------------------------------------


def _readiness_research(client: TestClient) -> dict:
    checks = {c["id"]: c for c in client.get("/api/readiness").json()["checks"]}
    return checks["research_complete"]


def _install_profile(client: TestClient, statuses: list[tuple[str, str]]) -> None:
    """Put a cumulative profile on the session's runner, as a round would."""
    from backend.research.engine import DimensionStatus, RequirementsProfile

    session = sessions.get_workspace().session
    session.research.status = "complete"
    session.research.profile_result = RequirementsProfile(
        items=[],
        dimension_statuses=[
            DimensionStatus(
                dimension_id=did,
                status=status,
                title=next(
                    (
                        d.title
                        for d in session.module.research_dimensions
                        if d.dimension_id == did
                    ),
                    did,
                ),
            )
            for did, status in statuses
        ],
        research_date="2026-07-21",
    )


def test_one_completed_dimension_no_longer_passes_readiness():
    """The false pass this chunk removes.

    A round reports ``complete`` when ANY dimension completes, so three of
    four could have failed while readiness said research was done.
    """
    client = _client()
    _select_fire(client)
    _install_profile(
        client,
        [
            ("governing_codes", "completed"),
            ("ahj_requirements", "failed"),
            ("client_standards", "failed"),
            ("site_environment", "failed"),
        ],
    )
    check = _readiness_research(client)
    assert check["ok"] is False
    # Named, not counted — and it says the one thing the user can do.
    assert "Authority-having-jurisdiction requirements" in check["detail"]
    assert "1 of 4" in check["detail"]
    assert "Press Research again" in check["detail"]


def test_every_dimension_completed_reads_as_complete():
    client = _client()
    _select_fire(client)
    _install_profile(
        client,
        [
            (d.dimension_id, "completed")
            for d in sessions.get_workspace().session.module.research_dimensions
        ],
    )
    check = _readiness_research(client)
    assert check["ok"] is True
    assert check["detail"] == "Requirements research complete."


def test_a_later_round_restores_readiness_and_a_failed_rerun_does_not_revoke_it():
    """Cumulative statuses decide, never the latest round's events."""
    from backend.research.engine import (
        DimensionStatus,
        RequirementsProfile,
        append_research_round,
    )

    client = _client()
    _select_fire(client)
    session = sessions.get_workspace().session
    dims = [d.dimension_id for d in session.module.research_dimensions]

    def _round(failed: set[str], date: str) -> RequirementsProfile:
        return RequirementsProfile(
            items=[],
            dimension_statuses=[
                DimensionStatus(
                    dimension_id=did,
                    status="failed" if did in failed else "completed",
                    title=did,
                )
                for did in dims
            ],
            research_date=date,
        )

    session.research.status = "complete"
    first = append_research_round(None, _round({"site_environment"}, "2026-07-21"))
    session.research.profile_result = first
    assert _readiness_research(client)["ok"] is False

    # Round 2 covers the gap.
    second = append_research_round(first, _round(set(), "2026-07-27"))
    session.research.profile_result = second
    assert _readiness_research(client)["ok"] is True

    # Round 3 fails a dimension that already completed. Its findings are
    # still in the profile, so readiness must not be revoked.
    third = append_research_round(second, _round({"governing_codes"}, "2026-08-01"))
    session.research.profile_result = third
    assert _readiness_research(client)["ok"] is True


def test_a_complete_runner_with_no_profile_fails_closed():
    client = _client()
    _select_fire(client)
    session = sessions.get_workspace().session
    session.research.status = "complete"
    session.research.profile_result = None
    check = _readiness_research(client)
    assert check["ok"] is False
    assert "no profile was recorded" in check["detail"]


def test_a_self_contradicting_record_fails_closed_without_blocking_the_project():
    """A corrupt or hand-edited project file can produce two statuses for one
    dimension. That is not evidence research happened — but it must not stop
    the project from opening (the lenient-loader posture)."""
    from backend.research.engine import DimensionStatus, RequirementsProfile

    client = _client()
    _select_fire(client)
    session = sessions.get_workspace().session
    session.research.status = "complete"
    session.research.profile_result = RequirementsProfile(
        items=[],
        dimension_statuses=[
            DimensionStatus(dimension_id=d.dimension_id, status="completed", title="x")
            for d in session.module.research_dimensions
        ]
        + [DimensionStatus(dimension_id="governing_codes", status="failed", title="x")],
        research_date="2026-07-21",
    )
    check = _readiness_research(client)
    assert check["ok"] is False
    assert "recorded more than once" in check["detail"]
    # Fail closed for READINESS only: the rest of the surface is unaffected,
    # and the runner's own status is untouched. (The loader's own tolerance
    # of malformed research is covered in test_research_rounds.py.)
    doc = client.get("/api/doc")
    assert doc.status_code == 200 and doc.json()["research_status"] == "complete"


def test_a_declared_optional_gap_passes_but_says_so_with_its_reason(monkeypatch):
    from dataclasses import replace as dc_replace

    from backend.spec_modules.base import ResearchDimension
    from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE

    client = _client()
    _select_fire(client)
    session = sessions.get_workspace().session
    optional = ResearchDimension(
        "seismic_optional",
        "Seismic bracing practice",
        "Research {city} seismic practice.",
        required=False,
        optional_rationale="rarely governs in this jurisdiction",
    )
    session.module = dc_replace(
        HYPERSCALE_FIRE,
        research_dimensions=HYPERSCALE_FIRE.research_dimensions + (optional,),
    )
    _install_profile(
        client,
        [
            *[(d.dimension_id, "completed") for d in HYPERSCALE_FIRE.research_dimensions],
            ("seismic_optional", "failed"),
        ],
    )
    check = _readiness_research(client)
    assert check["ok"] is True
    assert "4 of 5" in check["detail"]
    assert "Seismic bracing practice" in check["detail"]
    assert "rarely governs in this jurisdiction" in check["detail"]


def test_a_legacy_profile_with_no_rounds_still_reads_as_complete():
    """Pre-round profiles synthesize completed statuses on load, so a project
    saved before any of this must not suddenly read as missing coverage."""
    from backend.research.engine import RequirementsProfile

    client = _client()
    _select_fire(client)
    session = sessions.get_workspace().session
    session.research.status = "complete"
    session.research.profile_result = RequirementsProfile.from_dict(
        {
            "items": [],
            "dimension_statuses": [
                {"dimension_id": d.dimension_id, "status": "completed"}
                for d in session.module.research_dimensions
            ],
            "research_date": "2026-07-21",
        }
    )
    assert _readiness_research(client)["ok"] is True


# ---------------------------------------------------------------------------
# Round scope over HTTP: retry the areas that never completed
# ---------------------------------------------------------------------------


def test_the_status_payload_names_the_areas_that_never_completed(monkeypatch):
    client = _client()
    _select_fire(client)
    _record_profile(client, monkeypatch)

    # Before any round: every declared area is a gap, none completed.
    coverage = client.get("/api/research/status").json()["coverage"]
    assert coverage["total"] == 4
    assert coverage["completed"] == []
    assert [g["dimension_id"] for g in coverage["gaps"]] == [
        d.dimension_id
        for d in sessions.get_workspace().session.module.research_dimensions
    ]
    assert all(g["required"] is True for g in coverage["gaps"])

    boom = RuntimeError("kaput")
    _patch_research_client(
        monkeypatch,
        SequencedFakeClient(
            _scripts(ahj_requirements=[boom], client_standards=[boom])
        ),
    )
    assert client.post("/api/research/start").json()["ok"] is True
    assert _wait_terminal(client)["status"] == "complete"

    coverage = client.get("/api/research/status").json()["coverage"]
    assert sorted(coverage["completed"]) == [
        "governing_codes",
        "site_environment",
    ]
    # Named, not counted — that is what makes a retry button possible.
    assert [g["dimension_id"] for g in coverage["gaps"]] == [
        "ahj_requirements",
        "client_standards",
    ]
    assert [g["title"] for g in coverage["gaps"]] == [
        "Authority-having-jurisdiction requirements",
        "Owner / client and insurer standards",
    ]


def test_a_gaps_round_researches_only_the_incomplete_areas(monkeypatch):
    client = _client()
    _select_fire(client)
    _record_profile(client, monkeypatch)

    boom = RuntimeError("kaput")
    _patch_research_client(
        monkeypatch,
        SequencedFakeClient(
            _scripts(ahj_requirements=[boom], client_standards=[boom])
        ),
    )
    assert client.post("/api/research/start").json()["ok"] is True
    assert _wait_terminal(client)["status"] == "complete"

    # Only the two failed dimensions are scripted: a client that asked about
    # the settled ones would raise "no script matches the request", so the
    # scoping is proved by the run completing at all.
    retry = SequencedFakeClient(
        {
            DIM_KEYS["ahj_requirements"]: [
                research_response(items=[], searched_urls=["https://ahj.gov"])
            ],
            DIM_KEYS["client_standards"]: [
                research_response(items=[], searched_urls=["https://c.gov"])
            ],
        }
    )
    _patch_research_client(monkeypatch, retry)
    resp = client.post("/api/research/start", json={"scope": "gaps"})
    assert resp.json()["ok"] is True
    snapshot = _wait_terminal(client)
    assert snapshot["status"] == "complete"

    roster = next(
        e for e in snapshot["events"] if e["type"] == "research_started"
    )
    assert roster["dimensions"] == ["ahj_requirements", "client_standards"]
    assert roster["declared_dimension_count"] == 4
    assert len(retry.requests) == 2

    # The gaps closed, and the settled areas were never re-asked.
    assert client.get("/api/research/status").json()["coverage"]["gaps"] == []
    assert _readiness_research(client)["ok"] is True


def test_a_gaps_round_with_nothing_to_retry_is_refused_with_the_reason(
    monkeypatch,
):
    client = _client()
    _select_fire(client)
    _record_profile(client, monkeypatch)
    _patch_research_client(monkeypatch, SequencedFakeClient(_scripts()))
    assert client.post("/api/research/start").json()["ok"] is True
    assert _wait_terminal(client)["status"] == "complete"

    resp = client.post("/api/research/start", json={"scope": "gaps"})
    assert resp.status_code == 400
    # Refused with the reason, not silently downgraded to a full round — a
    # full round is the more expensive action and must stay a deliberate one.
    assert "nothing to retry" in resp.json()["error"]
    assert "4 of 4" in resp.json()["error"]


def test_an_unknown_scope_is_refused_and_an_absent_body_runs_everything(
    monkeypatch,
):
    client = _client()
    _select_fire(client)
    _record_profile(client, monkeypatch)

    resp = client.post("/api/research/start", json={"scope": "some_areas"})
    assert resp.status_code == 400
    assert "Unknown research scope" in resp.json()["error"]

    # The historical contract: no body at all is a full round.
    full = SequencedFakeClient(_scripts())
    _patch_research_client(monkeypatch, full)
    assert client.post("/api/research/start").json()["ok"] is True
    assert _wait_terminal(client)["status"] == "complete"
    assert len(full.requests) == 4
