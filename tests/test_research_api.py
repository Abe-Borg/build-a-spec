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
    project = json.loads(client.get("/api/project/save").content)
    client.post("/api/session/reset")
    loaded = client.post("/api/project/load", json=project).json()
    para = loaded["doc"]["parts"][0]["articles"][0]["paragraphs"][0]
    assert para["source_item_id"] == "r-abc123def456"


def test_research_profile_survives_project_round_trip(monkeypatch):
    client = _client()
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

    project = json.loads(client.get("/api/project/save").content)
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
