"""Batch 10 session-surface tests: the module picker's backend — reset with
an optional body, the modules listing, the session discipline in per-turn
context (never the stable prompt), and project-file persistence with the
open-catalog invariant."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend.app import create_app
from backend import sessions
from backend.spec_doc.project import load_project
from tests.fakes import (
    FakeClient,
    request_context_text,
    text_turn,
)


def _client() -> TestClient:
    return TestClient(create_app())


def _parse_sse(body: str) -> list[dict]:
    events = []
    for line in body.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


def _patch_client(monkeypatch, fake: FakeClient) -> None:
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)


def _reset_generic(client: TestClient, discipline: str = "Electrical") -> dict:
    resp = client.post(
        "/api/session/reset",
        json={"module_id": "generic", "discipline": discipline},
    )
    assert resp.status_code == 200
    return resp.json()


# ---------------------------------------------------------------------------
# POST /api/session/reset — the optional body
# ---------------------------------------------------------------------------


def test_bodyless_reset_keeps_module_and_discipline():
    client = _client()
    _reset_generic(client)
    # The historical contract: a bodyless POST (no content-type at all)
    # resets state but keeps the active module and discipline.
    resp = client.post("/api/session/reset")
    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "module_id": "generic",
        "module": sessions.get_session().module.display_name,
        "discipline": "Electrical",
        "project_context": "",
    }
    session = sessions.get_session()
    assert session.module.module_id == "generic"
    assert session.discipline == "Electrical"


def test_reset_with_body_switches_module_and_reports_it():
    client = _client()
    data = _reset_generic(client, discipline="Mechanical (HVAC)")
    assert data["module_id"] == "generic"
    assert data["discipline"] == "Mechanical (HVAC)"
    health = client.get("/api/health").json()
    assert health["module_id"] == "generic"
    assert health["discipline"] == "Mechanical (HVAC)"

    # Switching back to the curated module clears the discipline (the
    # invariant: non-empty discipline ⇒ open-catalog module active).
    resp = client.post(
        "/api/session/reset",
        json={"module_id": "hyperscale_fire", "discipline": "Electrical"},
    )
    assert resp.json()["module_id"] == "hyperscale_fire"
    assert resp.json()["discipline"] == ""
    assert sessions.get_session().discipline == ""


def test_unknown_module_id_degrades_to_default():
    client = _client()
    resp = client.post(
        "/api/session/reset",
        json={"module_id": "no-such-module", "discipline": "Electrical"},
    )
    data = resp.json()
    # The neutral default is now the generic open-catalog module, so an unknown
    # id degrades to it AND the discipline sticks (the invariant is satisfied).
    assert data["module_id"] == "generic"
    assert data["discipline"] == "Electrical"


def test_blank_module_id_keeps_current_module():
    client = _client()
    _reset_generic(client)
    resp = client.post(
        "/api/session/reset", json={"module_id": "", "discipline": "Plumbing"}
    )
    assert resp.json()["module_id"] == "generic"
    assert resp.json()["discipline"] == "Plumbing"


def test_discipline_is_sanitized():
    client = _client()
    data = _reset_generic(
        client, discipline="  Fire \n Protection\t &   Suppression  "
    )
    assert data["discipline"] == "Fire Protection & Suppression"
    long = "x" * 200
    assert len(_reset_generic(client, discipline=long)["discipline"]) == 80


# ---------------------------------------------------------------------------
# GET /api/modules
# ---------------------------------------------------------------------------


def test_modules_endpoint_lists_the_registry_in_order():
    data = _client().get("/api/modules").json()
    assert data["ok"] is True
    mods = data["modules"]
    # The generic any-discipline module leads and is the neutral default.
    assert [m["module_id"] for m in mods] == ["generic", "hyperscale_fire"]
    generic, fire = mods
    assert generic["default"] is True and generic["generic"] is True
    assert fire["default"] is False and fire["generic"] is False
    assert all(
        m["display_name"].strip() and m["description"].strip() for m in mods
    )


# ---------------------------------------------------------------------------
# Discipline in the PROJECT CONTEXT block (never the stable prompt)
# ---------------------------------------------------------------------------


def test_discipline_line_rides_context_not_the_stable_block(monkeypatch):
    client = _client()
    _reset_generic(client)
    fake = FakeClient([text_turn(["Understood."])])
    _patch_client(monkeypatch, fake)
    resp = client.post("/api/chat", json={"message": "Hello"})
    assert _parse_sse(resp.text)[-1]["type"] == "turn_complete"

    request = fake.messages.last_request
    context = request_context_text(request)
    assert "PROJECT DISCIPLINE: Electrical" in context
    # The unpinned standards posture rides the same dynamic block.
    assert "pins NO default editions" in context
    assert "(none recorded yet)" in context
    # The cached stable block carries policy, never the session value.
    stable = request["system"][0]["text"]
    assert "PROJECT DISCIPLINE: Electrical" not in stable
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_open_catalog_without_discipline_asks_for_it(monkeypatch):
    client = _client()
    _reset_generic(client, discipline="")
    fake = FakeClient([text_turn(["Hi."])])
    _patch_client(monkeypatch, fake)
    client.post("/api/chat", json={"message": "Hello"})
    context = request_context_text(fake.messages.last_request)
    assert "PROJECT DISCIPLINE: [not yet stated]" in context


def test_curated_module_context_has_no_discipline_line(monkeypatch):
    client = _client()
    # The default is now the generic open-catalog module (which DOES render a
    # discipline line); select a curated module to exercise the no-line path.
    client.post("/api/session/reset", json={"module_id": "hyperscale_fire"})
    fake = FakeClient([text_turn(["Hi."])])
    _patch_client(monkeypatch, fake)
    client.post("/api/chat", json={"message": "Hello"})
    context = request_context_text(fake.messages.last_request)
    assert context and "PROJECT DISCIPLINE" not in context


# ---------------------------------------------------------------------------
# Project-file persistence
# ---------------------------------------------------------------------------


def test_project_round_trip_preserves_module_and_discipline(monkeypatch):
    client = _client()
    _reset_generic(client, discipline="Structural")
    saved = client.get("/api/project/save").json()
    assert saved["module_id"] == "generic"
    assert saved["discipline"] == "Structural"

    # Move the session to the curated default, then load the file back.
    client.post("/api/session/reset", json={"module_id": "hyperscale_fire"})
    assert sessions.get_session().module.module_id == "hyperscale_fire"
    resp = client.post("/api/project/load", json=saved)
    assert resp.status_code == 200
    session = sessions.get_session()
    assert session.module.module_id == "generic"
    assert session.discipline == "Structural"

    # And the restored discipline reaches the next turn's context.
    fake = FakeClient([text_turn(["Back."])])
    _patch_client(monkeypatch, fake)
    client.post("/api/chat", json={"message": "Where were we?"})
    assert "PROJECT DISCIPLINE: Structural" in request_context_text(
        fake.messages.last_request
    )


def test_old_project_file_without_discipline_loads_empty():
    client = _client()
    # Save from a curated session so the file carries a curated module_id
    # (the neutral default is now generic).
    client.post("/api/session/reset", json={"module_id": "hyperscale_fire"})
    saved = client.get("/api/project/save").json()
    saved.pop("discipline", None)  # a pre-Batch-8 file
    session = sessions.get_session()
    load_project(saved, session)
    assert session.discipline == ""
    assert session.module.module_id == "hyperscale_fire"


def test_legacy_file_without_module_id_loads_the_fire_module():
    # A pre-module-id project file (no module_id key at all) was authored in the
    # only module that then existed — the fire module. It must still load there,
    # not silently switch to the now-neutral generic default. A present-but-
    # unknown id, by contrast, degrades to the default (tested separately).
    client = _client()
    saved = client.get("/api/project/save").json()
    saved.pop("module_id", None)
    session = sessions.get_session()
    load_project(saved, session)
    assert session.module.module_id == "hyperscale_fire"


def test_load_enforces_the_open_catalog_invariant():
    # A (hand-edited or future-build) file pairing a curated module with a
    # discipline loads with the discipline cleared, never kept silently.
    client = _client()
    saved = client.get("/api/project/save").json()
    saved["module_id"] = "hyperscale_fire"
    saved["discipline"] = "Electrical"
    session = sessions.get_session()
    load_project(saved, session)
    assert session.module.module_id == "hyperscale_fire"
    assert session.discipline == ""

    # The generic pairing is preserved (and sanitized).
    saved["module_id"] = "generic"
    saved["discipline"] = "  Fire \n Alarm "
    load_project(saved, session)
    assert session.module.module_id == "generic"
    assert session.discipline == "Fire Alarm"


# ---------------------------------------------------------------------------
# project_context — the optional priming field (any module, cleared on reset)
# ---------------------------------------------------------------------------


def test_reset_with_body_sets_sanitizes_and_echoes_project_context():
    client = _client()
    data = client.post(
        "/api/session/reset",
        json={
            "module_id": "generic",
            "discipline": "Electrical",
            "project_context": "  A 12-story\n office  tower.  ",
        },
    ).json()
    assert data["project_context"] == "A 12-story office tower."
    # Health echoes the sanitized value too.
    assert (
        client.get("/api/health").json()["project_context"]
        == "A 12-story office tower."
    )
    # Bounded to a sentence or two, not a pasted paragraph.
    long = client.post(
        "/api/session/reset",
        json={
            "module_id": "generic",
            "discipline": "Electrical",
            "project_context": "x" * 800,
        },
    ).json()
    assert len(long["project_context"]) == 400


def test_bodyless_reset_clears_project_context():
    # The deliberate asymmetry with discipline: a bodyless reset KEEPS module
    # and discipline but CLEARS the per-project priming text.
    client = _client()
    client.post(
        "/api/session/reset",
        json={
            "module_id": "generic",
            "discipline": "Electrical",
            "project_context": "A data center.",
        },
    )
    assert sessions.get_session().project_context == "A data center."
    client.post("/api/session/reset")  # bodyless
    assert sessions.get_session().project_context == ""
    assert sessions.get_session().discipline == "Electrical"


def test_project_context_rides_context_not_stable_for_any_module(monkeypatch):
    client = _client()
    client.post(
        "/api/session/reset",
        json={
            "module_id": "generic",
            "discipline": "Electrical",
            "project_context": "A 12-story office tower.",
        },
    )
    fake = FakeClient([text_turn(["Ok."])])
    _patch_client(monkeypatch, fake)
    client.post("/api/chat", json={"message": "Hi"})
    request = fake.messages.last_request
    assert "A 12-story office tower." in request_context_text(request)
    # Never in the cached stable prompt.
    assert "PROJECT DESCRIPTION" not in request["system"][0]["text"]

    # Curated module — project_context is NOT gated by open_catalog.
    client.post(
        "/api/session/reset",
        json={
            "module_id": "hyperscale_fire",
            "project_context": "A hyperscale campus.",
        },
    )
    fake2 = FakeClient([text_turn(["Ok."])])
    _patch_client(monkeypatch, fake2)
    client.post("/api/chat", json={"message": "Hi"})
    assert "A hyperscale campus." in request_context_text(
        fake2.messages.last_request
    )


def test_project_context_survives_project_round_trip():
    client = _client()
    client.post(
        "/api/session/reset",
        json={
            "module_id": "generic",
            "discipline": "Electrical",
            "project_context": "A 12-story office tower.",
        },
    )
    saved = client.get("/api/project/save").json()
    assert saved["project_context"] == "A 12-story office tower."
    # A fresh session clears it; loading the file restores it.
    client.post("/api/session/reset")
    assert sessions.get_session().project_context == ""
    session = sessions.get_session()
    load_project(saved, session)
    assert session.project_context == "A 12-story office tower."
    # Old files without the key degrade to "".
    saved.pop("project_context", None)
    load_project(saved, session)
    assert session.project_context == ""
