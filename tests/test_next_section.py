"""Next section in one click (v1.20.0): the brief built in memory and seeded
in one transaction — no file relay — pre-filled from the module's sibling
catalog minus the sections the project already drafted.

What the tests pin:
- the in-memory seed is state-for-state the same session as export-the-file-
  then-start-from-it (the two paths must never drift, so one is asserted
  against the other rather than against a hand-written expectation);
- the picked section header lands in version 0, so the new session opens on a
  named, empty section with no undo step behind it;
- the options payload flags what is done rather than hiding it, and reads the
  effective discipline, the manifest and the link;
- the 409/400/404 matrix mirrors brief/start's;
- the catalog is empty (not absent) on the open-catalog module.
"""
from __future__ import annotations

import pytest

from backend import sessions
from backend.project_brief import (
    MAX_NEXT_SECTION_NUMBER_CHARS,
    MAX_NEXT_SECTION_TITLE_CHARS,
    ProjectBriefError,
    clean_next_section_header,
    next_section_catalog,
    sections_drafted,
)
from backend.spec_modules.generic import GENERIC
from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE
from tests.test_project_brief import (
    PROFILE,
    _brief_bytes_from_a_rich_section,
    _client,
    _rich_session,
    _upload,
)


def _projection(session) -> dict:
    """Everything a seed installs, as comparable data. The document's own
    header is left out (the two paths name the section differently by
    design) and so is the link's ``brief_updated_at`` stamp (a clock)."""
    doc = session.doc.doc.to_dict()
    doc.pop("section", None)
    link = dict(session.project_link or {})
    # The id is minted per export and the stamps are clocks: neither says
    # anything about WHAT was carried, which is the claim under test.
    link.pop("brief_updated_at", None)
    link.pop("project_id", None)
    for record in link.get("sections", []):
        record.pop("exported_at", None)
    research = session.research.profile_result
    references = []
    for ref in session.references.docs:
        record = ref.to_dict()
        record.pop("added_at", None)
        references.append(record)
    return {
        "doc": doc,
        "versions": len(session.doc.versions),
        "index": session.doc.index,
        "module": session.module.module_id,
        "discipline": session.discipline,
        "research": research.to_dict() if research is not None else None,
        "research_status": session.research.status,
        "references": references,
        "facts": session.facts.to_dict(),
        "link": link,
        "history": list(session.history),
        "template_origin": session.template_origin,
    }


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_the_catalog_flags_what_the_project_already_drafted():
    listed = next_section_catalog(HYPERSCALE_FIRE, ["21 13 13", " 21  30 00 "])
    by_number = {entry["number"]: entry for entry in listed}
    assert [e["number"] for e in listed] == [
        e.number for e in HYPERSCALE_FIRE.section_catalog
    ], "declaration order, nothing filtered"
    assert by_number["21 13 13"]["done"] is True
    assert by_number["21 30 00"]["done"] is True, "whitespace-folded before comparing"
    assert by_number["21 13 16"]["done"] is False
    assert by_number["21 30 00"]["title"] == "Fire Pumps"
    assert by_number["21 30 00"]["scope_note"].startswith("Electric or diesel")
    # The open-catalog module declares nothing: an empty list, never an error.
    assert next_section_catalog(GENERIC, ["21 13 13"]) == []


def test_sections_drafted_reads_the_registry_then_this_section_never_unnumbered():
    client = _client()
    session = _rich_session(client)
    assert sections_drafted(session) == ["21 13 13"]
    session.project_link = {
        "project_id": "a" * 32,
        "name": "P",
        "brief_updated_at": "",
        "seeded_from": [],
        "research_rounds_at_seed": 0,
        "sections": [
            {"number": "21 05 00", "title": "Common Work Results"},
            {"number": "(unnumbered)", "title": ""},
            {"number": "21 13 13", "title": "dup of this section"},
        ],
    }
    assert sections_drafted(session) == ["21 05 00", "21 13 13"]


def test_the_typed_header_is_folded_and_bounded():
    assert clean_next_section_header("  21  30 00 ", " Fire\n Pumps ") == ("21 30 00", "Fire Pumps")
    assert clean_next_section_header(None, "") == ("", "")
    with pytest.raises(ProjectBriefError, match="section number is too long"):
        clean_next_section_header("2" * (MAX_NEXT_SECTION_NUMBER_CHARS + 1), "")
    with pytest.raises(ProjectBriefError, match="section title is too long"):
        clean_next_section_header("", "t" * (MAX_NEXT_SECTION_TITLE_CHARS + 1))


# ---------------------------------------------------------------------------
# The options route
# ---------------------------------------------------------------------------


def test_the_options_route_describes_this_project_without_touching_it():
    client = _client()
    session = _rich_session(client)
    before = _projection(session)

    resp = client.get("/api/project/next-section")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["project"] is None, "not linked until something exports"
    assert body["current"] == {"number": "21 13 13", "title": "Wet-Pipe Sprinkler Systems"}
    assert body["module_id"] == "generic" and body["open_catalog"] is True
    assert body["discipline"] == "Fire Suppression"
    assert body["done"] == ["21 13 13"]
    assert body["catalog"] == []
    manifest = body["manifest"]
    assert manifest["facts"]["active"] == 2 and manifest["research"]["rounds"] == 1
    assert [r["rid"] for r in manifest["references"]] == ["ref-1"]
    assert _projection(session) == before, "a preview stamps nothing"


def test_the_options_route_lists_the_curated_catalog_and_the_link_after_an_export():
    client = _client()
    resp = client.post(
        "/api/session/reset", json={"module_id": "hyperscale_fire", "discipline": ""}
    )
    assert resp.status_code == 200, resp.text
    _rich_session(client)
    exported = client.get("/api/project/brief")
    assert exported.status_code == 200, exported.text

    body = client.get("/api/project/next-section").json()

    assert body["module_id"] == "hyperscale_fire" and body["open_catalog"] is False
    assert body["project"]["name"] == "Client X · Data Center · Ashburn, Virginia"
    assert body["project"]["project_id"] == sessions.get_session().project_link["project_id"]
    done = {e["number"]: e["done"] for e in body["catalog"]}
    assert done["21 13 13"] is True
    assert done["21 30 00"] is False
    assert body["done"] == ["21 13 13"]


# ---------------------------------------------------------------------------
# The seed route
# ---------------------------------------------------------------------------


def test_next_section_seeds_the_same_session_the_file_relay_does():
    # Path A: the file relay that shipped in v1.17.0.
    client = _client()
    payload = _brief_bytes_from_a_rich_section(client)
    resp = client.post(
        "/api/project/brief/start",
        files=_upload("p.basproject", payload),
        data={"discipline": "Fire Suppression"},
    )
    assert resp.status_code == 200, resp.text
    via_file = _projection(sessions.get_session())
    sessions.reset_session()

    # Path B: one click, no file.
    client = _client()
    _rich_session(client)
    resp = client.post(
        "/api/project/next-section",
        json={"number": "21 30 00", "title": "Fire Pumps"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    via_session = _projection(sessions.get_session())

    assert via_session == via_file
    seed = body["seed"]
    assert seed["source"] == "session"
    assert seed["section"] == {"number": "21 30 00", "title": "Fire Pumps"}
    assert seed["facts_restored"] == 3 and seed["research_rounds"] == 1
    assert seed["references_restored"] == 1 and seed["warnings"] == []
    assert seed["name"] == "Client X · Data Center · Ashburn, Virginia"
    bundle = body["session"]
    assert bundle["chat"] == [] and bundle["workspace_scope"] == "original"
    assert bundle["project_link"]["seeded_from"] == ["21 13 13"]
    assert bundle["project_link"]["project_id"] == seed["project_id"]


def test_the_picked_header_opens_a_named_empty_section_with_no_undo_behind_it():
    client = _client()
    _rich_session(client)

    resp = client.post(
        "/api/project/next-section",
        json={"number": "21 30 00", "title": "Fire Pumps"},
    )

    assert resp.status_code == 200, resp.text
    session = sessions.get_session()
    doc = session.doc.doc
    assert (doc.number, doc.title) == ("21 30 00", "Fire Pumps")
    assert not any(part.articles for part in doc.parts), "an empty page …"
    # … that counts as content: a named section is what the master-import
    # gate refuses (has_body_content), which is why the dialog offers "leave
    # it unnamed" for a section that will start from an office master.
    assert doc.has_body_content()
    assert session.doc.index == 0 and len(session.doc.versions) == 1
    assert session.doc.versions[0]["section"] == {"number": "21 30 00", "title": "Fire Pumps"}
    assert doc.project_profile == PROFILE.to_dict()
    assert doc.project_identity["discipline"] == "Fire Suppression"
    payload = client.get("/api/doc").json()
    assert payload["doc"]["section"] == {"number": "21 30 00", "title": "Fire Pumps"}
    assert payload["project_link"]["seeded_from"] == ["21 13 13"]
    assert sessions.has_unsaved_progress(session)
    # And the catalog now shows both as done.
    done = sections_drafted(session)
    assert done == ["21 13 13", "21 30 00"]


def test_a_bodyless_post_seeds_an_unnamed_section_on_the_projects_own_setup():
    client = _client()
    _rich_session(client)

    resp = client.post("/api/project/next-section")

    assert resp.status_code == 200, resp.text
    seed = resp.json()["seed"]
    assert seed["section"] == {"number": "", "title": ""}
    assert seed["module_id"] == "generic" and seed["discipline"] == "Fire Suppression"
    doc = sessions.get_session().doc.doc
    assert doc.number == "" and doc.title == ""
    assert not doc.has_body_content(), "an unnamed seed is still a valid import target"
    assert doc.project_identity == {"project_type": "Data Center", "discipline": "Fire Suppression"}


def test_discipline_and_module_overrides_reach_the_seed():
    client = _client()
    _rich_session(client)

    resp = client.post(
        "/api/project/next-section",
        json={"discipline": "Electrical", "module_id": "hyperscale_fire"},
    )

    assert resp.status_code == 200, resp.text
    seed = resp.json()["seed"]
    assert seed["module_id"] == "hyperscale_fire"
    assert seed["discipline"] == "Electrical"
    session = sessions.get_session()
    assert session.module.module_id == "hyperscale_fire"
    assert session.doc.doc.project_identity["discipline"] == "Electrical"


def test_a_template_pairs_and_the_pick_names_the_section_over_it():
    client = _client()
    _rich_session(client)

    resp = client.post(
        "/api/project/next-section",
        json={
            "number": "21 30 00",
            "title": "Fire Pumps",
            "template_id": "curated:hyperscale-fire-starter",
        },
    )

    assert resp.status_code == 200, resp.text
    seed = resp.json()["seed"]
    assert seed["template"]["template_id"] == "curated:hyperscale-fire-starter"
    assert seed["module_id"] == "hyperscale_fire"
    assert any("template's module" in w for w in seed["warnings"]), seed["warnings"]
    session = sessions.get_session()
    doc = session.doc.doc
    assert doc.has_body_content()  # the template's body …
    assert (doc.number, doc.title) == ("21 30 00", "Fire Pumps")  # … under the pick
    assert doc.project_profile == PROFILE.to_dict()  # … and the project's setup
    assert session.doc.index == 0 and len(session.doc.versions) == 1

    sessions.reset_session()
    client = _client()
    _rich_session(client)
    missing = client.post(
        "/api/project/next-section", json={"template_id": "curated:does-not-exist"}
    )
    assert missing.status_code == 404, missing.text
    assert sessions.get_session().doc.doc.number == "21 13 13", "nothing replaced"


def test_a_template_without_a_pick_keeps_its_own_header():
    client = _client()
    _rich_session(client)

    resp = client.post(
        "/api/project/next-section",
        json={"template_id": "curated:hyperscale-fire-starter"},
    )

    assert resp.status_code == 200, resp.text
    doc = sessions.get_session().doc.doc
    from backend.templates import get_template_catalog

    template, _ = get_template_catalog().get("curated:hyperscale-fire-starter")
    assert (doc.number, doc.title) == (
        template["document"]["section"]["number"],
        template["document"]["section"]["title"],
    )


def test_the_route_refuses_a_bad_header_busy_work_and_a_tour():
    client = _client()
    session = _rich_session(client)
    before = _projection(session)

    too_long = client.post(
        "/api/project/next-section",
        json={"number": "2" * (MAX_NEXT_SECTION_NUMBER_CHARS + 1)},
    )
    assert too_long.status_code == 400, too_long.text
    assert "section number is too long" in too_long.json()["error"]

    token = session.claim_model_turn()
    try:
        resp = client.post("/api/project/next-section", json={"number": "21 30 00"})
        assert resp.status_code == 409, resp.text
        assert resp.json()["code"] == "workspace_busy"
        options = client.get("/api/project/next-section")
        assert options.status_code == 200, "the preview is a read; it still answers"
    finally:
        session.release_model_turn(token[0] if isinstance(token, tuple) else token)
    assert _projection(session) == before, "a refusal replaces nothing"

    original = sessions.get_workspace()
    started = client.post(
        "/api/tutorial/start",
        json={
            "request_id": "next-section-refuses-in-a-tour",
            "source": "showcase",
            "workspace_id": original.workspace_id,
            "generation": original.generation,
        },
    )
    assert started.status_code == 200, started.text
    try:
        for method in ("get", "post"):
            resp = getattr(client, method)("/api/project/next-section")
            assert resp.status_code == 409, (method, resp.text)
            assert resp.json()["code"] == "tutorial_active"
    finally:
        sessions.reset_session()


def test_a_number_the_project_already_drafted_is_refused_not_greyed_only():
    """The catalog greys a drafted section, but the typed path and a direct
    caller bypass the catalog — and build_project_brief upserts the registry
    by number, so a second "21 13 13" would replace the first's record on
    the next export (Codex, PR #174). Refused server-side, in one rule."""
    client = _client()
    session = _rich_session(client)
    before = _projection(session)

    # The open section's own number …
    resp = client.post(
        "/api/project/next-section", json={"number": " 21  13 13 ", "title": "Again"}
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "section_already_drafted"
    assert "21 13 13" in resp.json()["error"]
    assert _projection(session) == before, "a refusal replaces nothing"

    # … and a number from the link's registry, after a real handoff.
    seeded = client.post(
        "/api/project/next-section", json={"number": "21 30 00", "title": "Fire Pumps"}
    )
    assert seeded.status_code == 200, seeded.text
    again = client.post(
        "/api/project/next-section", json={"number": "21 13 13", "title": "Wet-Pipe"}
    )
    assert again.status_code == 409, again.text
    assert again.json()["code"] == "section_already_drafted"
    still = sessions.get_session()
    assert still.doc.doc.number == "21 30 00", "the fire-pump section is still open"
    assert sections_drafted(still) == ["21 13 13", "21 30 00"]
    # A different number is still fine, and an unnamed page always is.
    fine = client.post("/api/project/next-section", json={"number": "21 12 00"})
    assert fine.status_code == 200, fine.text
    unnamed = client.post("/api/project/next-section")
    assert unnamed.status_code == 200, unnamed.text


def test_the_new_section_exports_a_brief_that_lists_both_sections():
    client = _client()
    _rich_session(client)
    first = client.post(
        "/api/project/next-section", json={"number": "21 30 00", "title": "Fire Pumps"}
    )
    assert first.status_code == 200, first.text
    project_id = first.json()["seed"]["project_id"]

    options = client.get("/api/project/next-section").json()
    assert options["project"]["project_id"] == project_id
    assert options["done"] == ["21 13 13", "21 30 00"]
    manifest = options["manifest"]
    assert [s["number"] for s in manifest["sections"]] == ["21 13 13", "21 30 00"]

    # A third section from the second: the lineage keeps extending, and the
    # project id never changes.
    third = client.post(
        "/api/project/next-section", json={"number": "21 12 00", "title": "Standpipes"}
    )
    assert third.status_code == 200, third.text
    assert third.json()["seed"]["project_id"] == project_id
    link = sessions.get_session().project_link
    assert link["seeded_from"] == ["21 13 13", "21 30 00"]
    assert [s["number"] for s in link["sections"]] == ["21 13 13", "21 30 00"]
