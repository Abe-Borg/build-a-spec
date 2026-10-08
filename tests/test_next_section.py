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
- the catalog is empty (not absent) on the open-catalog module;
- a section that never joined a project joins one BEFORE the save gate saves
  it (``POST /api/project/link``), so the file it leaves behind carries the
  project id the next section is seeded with — the stamp is idempotent and
  refused in a tour and while a turn streams.
"""
from __future__ import annotations

import pytest

from backend import sessions
from backend.project_brief import (
    MAX_NEXT_SECTION_NUMBER_CHARS,
    MAX_NEXT_SECTION_TITLE_CHARS,
    ProjectBriefError,
    clean_next_section_header,
    merge_project_brief,
    next_section_catalog,
    parse_project_brief,
    project_sections_block,
    sections_drafted,
)
from backend.spec_doc.project_package import parse_project_package
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
    facts = session.facts.to_dict()
    # A fact's uid (Project workspace Phase 3) is minted per record, like the
    # project id per export: the two paths each record their own rich
    # section, so their uids differ by construction and say nothing about
    # what was carried. Every other field is compared.
    for fact in facts["project_facts"]:
        fact.pop("uid", None)
    return {
        "doc": doc,
        "versions": len(session.doc.versions),
        "index": session.doc.index,
        "module": session.module.module_id,
        "discipline": session.discipline,
        "research": research.to_dict() if research is not None else None,
        "research_status": session.research.status,
        "references": references,
        "facts": facts,
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


# ---------------------------------------------------------------------------
# Joining the project before the save gate saves (POST /api/project/link)
# ---------------------------------------------------------------------------


def _saved_link(path) -> dict | None:
    """The project link a saved ``.baspec`` actually carries on disk."""
    return parse_project_package(path.read_bytes()).project.get("project_link")


def test_a_section_that_never_joined_a_project_saves_the_id_its_next_section_gets(
    tmp_path,
):
    """The orphan this route exists to prevent. Next section → used to save
    the outgoing section (the frontend's save gate) BEFORE the start route
    minted its project id, so the file kept no link while the next section
    was seeded with one: reopened, the old section had no folder and no
    Project panel, and a brief it exported later minted a second id the
    merge refuses to join. The click now stamps first, the gate's Save writes
    the link, and the start route reuses its id."""
    client = _client()
    session = _rich_session(client)
    assert session.project_link is None, "a section that never exported a brief"

    # What the frontend does: stamp, then the gate's Save, then the start.
    stamp = client.post("/api/project/link")
    assert stamp.status_code == 200, stamp.text
    assert stamp.json()["stamped"] is True
    section_file = tmp_path / "21 13 13.baspec"
    section_file.write_bytes(sessions.project_package(session)[0])
    seeded = client.post(
        "/api/project/next-section", json={"number": "21 30 00", "title": "Fire Pumps"}
    )
    assert seeded.status_code == 200, seeded.text

    saved = _saved_link(section_file)
    assert saved is not None, "the file the gate saved carries the link"
    project_id = seeded.json()["seed"]["project_id"]
    assert saved["project_id"] == project_id, "…and it is the next section's project"
    assert sessions.get_session().project_link["project_id"] == project_id
    assert stamp.json()["project"]["project_id"] == project_id

    # The proof that matters: the two sections are one project. The next
    # section's brief and the reopened section's brief share an id, and the
    # merge every save and pull runs joins them instead of refusing.
    next_brief = parse_project_brief(client.get("/api/project/brief").content)
    loaded = client.post(
        "/api/project/load-file",
        files={"file": (section_file.name, section_file.read_bytes(), "application/zip")},
    )
    assert loaded.status_code == 200, loaded.text
    reopened_brief = parse_project_brief(client.get("/api/project/brief").content)
    assert reopened_brief.project_id == next_brief.project_id == project_id
    merged, _report = merge_project_brief(next_brief, reopened_brief)
    assert {s["number"] for s in merged.sections} == {"21 13 13", "21 30 00"}


def test_the_link_stamp_is_the_export_s_stamp_once_and_a_no_op_after():
    client = _client()
    session = _rich_session(client)
    before = _projection(session)

    first = client.post("/api/project/link")
    assert first.status_code == 200, first.text
    assert first.json()["stamped"] is True
    link = dict(session.project_link)
    # Exactly what a brief export stamps: the project's id and name, and this
    # section in the registry — nothing seeded, nothing carried.
    assert first.json()["project"] == {
        "project_id": link["project_id"],
        "name": "Client X · Data Center · Ashburn, Virginia",
    }
    assert link["seeded_from"] == [] and link["research_rounds_at_seed"] == 0
    assert [s["number"] for s in link["sections"]] == ["21 13 13"]
    # Nothing but the link moved, and the cached project block did not: a
    # numbered section never lists itself among the project's other sections.
    after = _projection(session)
    after.pop("link")
    expected = dict(before)
    expected.pop("link")
    assert after == expected, "a stamp changes nothing but the link"
    assert project_sections_block(session.project_link, session.doc.doc.number) == ""

    # Pressing Next section → again (or after cancelling the gate) mints
    # nothing new: the link is left exactly as it is, clocks included.
    again = client.post("/api/project/link")
    assert again.status_code == 200, again.text
    assert again.json()["stamped"] is False
    assert again.json()["project"]["project_id"] == link["project_id"]
    assert session.project_link == link

    # An export afterwards keeps the stamped project, as a second export would.
    exported = parse_project_brief(client.get("/api/project/brief").content)
    assert exported.project_id == link["project_id"]


def test_the_link_stamp_leaves_an_existing_project_alone():
    client = _client()
    _rich_session(client)
    seeded = client.post(
        "/api/project/next-section", json={"number": "21 30 00", "title": "Fire Pumps"}
    )
    assert seeded.status_code == 200, seeded.text
    session = sessions.get_session()
    link = dict(session.project_link)

    resp = client.post("/api/project/link")

    assert resp.status_code == 200, resp.text
    assert resp.json()["stamped"] is False
    assert resp.json()["project"]["project_id"] == seeded.json()["seed"]["project_id"]
    assert session.project_link == link, "a seeded section's link is never re-stamped"


def test_the_link_stamp_refuses_a_streaming_turn_and_a_tour():
    client = _client()
    session = _rich_session(client)

    token = session.claim_model_turn()
    try:
        resp = client.post("/api/project/link")
        assert resp.status_code == 409, resp.text
        assert resp.json()["code"] == "turn_active"
        assert "current reply" in resp.json()["error"]
    finally:
        session.release_model_turn(token[0] if isinstance(token, tuple) else token)
    assert session.project_link is None, "a refusal stamps nothing"

    original = sessions.get_workspace()
    started = client.post(
        "/api/tutorial/start",
        json={
            "request_id": "project-link-refuses-in-a-tour",
            "source": "showcase",
            "workspace_id": original.workspace_id,
            "generation": original.generation,
        },
    )
    assert started.status_code == 200, started.text
    try:
        resp = client.post("/api/project/link")
        assert resp.status_code == 409, resp.text
        assert resp.json()["code"] == "tutorial_active"
        assert original.session.project_link is None, "the project behind the tour is untouched"
    finally:
        sessions.reset_session()
