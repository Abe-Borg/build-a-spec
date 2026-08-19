"""The import-time intent choice: starting point vs preserved original.

``POST /api/import/master`` used to mean exactly one thing — source-preserving
mode, permission sweep and all — which is the wrong contract for the most
common reason a user imports a spec: "use this as a starting point and build
me a proper section from it." The endpoint now takes ``detach`` (a form
field): ``true`` performs the import and the Edit-freely detach in ONE
transaction, so the document is fully editable from the first payload and the
minutes-long O(n²) permission sweep never starts. The default is
byte-compatible with every pre-intent client.

Everything detaching promises to keep — the exact original download, the
redline vs master, the saved project — is asserted here THROUGH the flag, not
just through the post-hoc detach route the older suite covers.
"""
from __future__ import annotations

import io

import pytest
from docx import Document
from fastapi.testclient import TestClient

from backend import sessions
from backend.app import create_app
from tests.conftest import settle_capability_sweep
from tests.docx_fidelity_helpers import DOCX_MEDIA_TYPE


@pytest.fixture
def client():
    sessions.reset_session()
    with TestClient(create_app()) as api:
        yield api
    sessions.reset_session()


def _master_bytes() -> bytes:
    document = Document()
    for text in (
        "SECTION 23 05 48",
        "VIBRATION AND SEISMIC CONTROLS FOR HVAC",
        "PART 1 - GENERAL",
        "1.1 SECTION INCLUDES",
        "A. Vibration isolation devices for HVAC equipment.",
        "B. Seismic restraints for HVAC equipment.",
        "PART 2 - PRODUCTS",
        "2.1 VIBRATION ISOLATORS",
        "A. Spring isolators: freestanding, laterally stable.",
        "END OF SECTION 23 05 48",
    ):
        document.add_paragraph(text)
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _import(client: TestClient, *, detach: bool | str | None) -> dict:
    data = {} if detach is None else {"detach": str(detach)}
    response = client.post(
        "/api/import/master",
        files={"file": ("23 05 48.docx", _master_bytes(), DOCX_MEDIA_TYPE)},
        data=data,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _first_paragraph_id(payload: dict) -> str:
    for part in payload["doc"]["parts"]:
        for article in part["articles"]:
            if article["paragraphs"]:
                return article["paragraphs"][0]["id"]
    raise AssertionError("the imported master has no paragraph")


# ---------------------------------------------------------------------------
# The starting-point contract
# ---------------------------------------------------------------------------


def test_a_starting_point_import_is_fully_editable_immediately(client):
    """The headline: no sweep to wait out, no gate to argue with.

    Every one of these ops is refused or held on an attached master — the
    heading categorically, add_article categorically, and the paragraph while
    the sweep is pending. Here they all succeed on the import's OWN payload,
    with no settle call anywhere.
    """
    payload = _import(client, detach=True)
    assert payload["source_detached"] is True
    assert payload["source_capabilities"] is None
    assert payload["preservation_ready"] is False
    assert payload["source_available"] is True
    assert payload["baseline_index"] is not None

    uid = _first_paragraph_id(payload)
    for ops in (
        [{"action": "replace", "target_id": "sec", "text": "NEW TITLE",
          "numbering": "23 05 48"}],
        [{"action": "add_article", "target_id": "pt3", "text": "EXECUTION"}],
        [{"action": "replace", "target_id": uid,
          "text": "Rewritten provision text."}],
        [{"action": "set_status", "target_id": uid, "status": "confirmed"}],
    ):
        result = client.post("/api/doc/edit", json={"ops": ops})
        assert result.status_code == 200, (ops, result.text)


def test_a_starting_point_import_never_starts_the_permission_sweep(client):
    """The sweep is minutes of O(n²) work producing a memo with no readers.

    Detaching inside the import transaction means the scope is already
    inactive when the warm would have started — so it must not start.
    """
    _import(client, detach=True)
    session = sessions.get_workspace().session
    assert session._capability_warm is None
    assert session._capability_warm_next is None


def test_a_starting_point_import_keeps_the_original_and_the_redline(client):
    """Detach drops the promise, never the evidence — through the flag too."""
    source = _master_bytes()
    response = client.post(
        "/api/import/master",
        files={"file": ("m.docx", source, DOCX_MEDIA_TYPE)},
        data={"detach": "true"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    original = client.get("/api/import/original")
    assert original.status_code == 200
    assert original.content == source

    uid = _first_paragraph_id(payload)
    edit = client.post(
        "/api/doc/edit",
        json={"ops": [{"action": "replace", "target_id": uid,
                       "text": "Materially different text."}]},
    )
    assert edit.status_code == 200, edit.text
    redline = client.get("/api/export/docx", params={"redline": "master"})
    assert redline.status_code == 200, redline.text
    assert "REDLINE" in redline.headers["content-disposition"]

    source_mode = client.get("/api/export/docx", params={"mode": "source"})
    assert source_mode.status_code == 409
    assert "detached from its Word original" in source_mode.json()["error"]


def test_a_starting_point_import_survives_save_and_reload(client):
    """Detached-at-import must be indistinguishable from import-then-detach.

    The loader skips the per-version preservation boundary for detached
    projects; a flag set in the import transaction rides the same store
    field, so the round trip has nothing new to do — pinned so it stays true.
    """
    source = _master_bytes()
    response = client.post(
        "/api/import/master",
        files={"file": ("m.docx", source, DOCX_MEDIA_TYPE)},
        data={"detach": "true"},
    )
    assert response.status_code == 200, response.text
    uid = _first_paragraph_id(response.json())
    assert client.post(
        "/api/doc/edit",
        json={"ops": [{"action": "replace", "target_id": "sec",
                       "text": "RETITLED", "numbering": "23 05 48"}]},
    ).status_code == 200

    package = client.get("/api/project/save")
    assert package.status_code == 200, package.text
    assert client.post("/api/session/reset").status_code == 200

    loaded = client.post(
        "/api/project/load-file",
        files={"file": ("p.baspec", package.content,
                        "application/octet-stream")},
    )
    assert loaded.status_code == 200, loaded.text

    payload = client.get("/api/doc").json()
    assert payload["source_capabilities"] is None
    assert payload["source_detached"] is True
    assert payload["doc"]["section"]["title"] == "RETITLED"
    assert client.get("/api/import/original").content == source
    assert client.post(
        "/api/doc/edit",
        json={"ops": [{"action": "replace", "target_id": uid,
                       "text": "Still freely editable."}]},
    ).status_code == 200


# ---------------------------------------------------------------------------
# The default stays the pre-intent contract, and bad input is refused loudly
# ---------------------------------------------------------------------------


def test_the_default_import_still_attaches_and_warms(client):
    """No ``detach`` field means exactly what every pre-intent client meant.

    Attached, source-preserving, sweep warming — the asynchrony itself is
    pinned by test_import_responsiveness; this pins that the new field's
    ABSENCE changes nothing.
    """
    payload = _import(client, detach=None)
    assert payload["source_detached"] is False
    capabilities = payload["source_capabilities"]
    assert capabilities is not None
    assert capabilities["status"] in {"pending", "ready"}
    session = sessions.get_workspace().session
    assert (
        session._capability_warm is not None
        or session._capability_memo_hit(session._capability_lookup_key())
        is not None
    )
    settle_capability_sweep()


def test_an_explicit_false_is_the_default_too(client):
    payload = _import(client, detach=False)
    assert payload["source_detached"] is False
    assert payload["source_capabilities"] is not None
    settle_capability_sweep()


def test_a_malformed_detach_value_is_refused_not_read_as_a_choice(client):
    """Garbage must never silently pick either contract for the user."""
    response = client.post(
        "/api/import/master",
        files={"file": ("m.docx", _master_bytes(), DOCX_MEDIA_TYPE)},
        data={"detach": "banana"},
    )
    assert response.status_code == 422
    # Nothing was imported: the document is still blank.
    payload = client.get("/api/doc").json()
    assert payload["baseline_index"] is None
    assert payload["source_detached"] is False


def test_undoing_to_empty_and_reimporting_attached_rearms_preservation(client):
    """Detach-at-import must not latch: a later attached import is attached.

    ``adopt_imported`` clears the flag by design; this proves the flag path
    composes with it end to end through the endpoint.
    """
    _import(client, detach=True)
    # Walk back to the always-present empty snapshot so a second import is
    # legal again (import requires a blank document).
    while client.get("/api/doc").json()["doc"]["version"]["index"] > 0:
        assert client.post("/api/doc/undo").status_code == 200

    payload = _import(client, detach=None)
    assert payload["source_detached"] is False
    assert payload["source_capabilities"] is not None
    settle_capability_sweep()
