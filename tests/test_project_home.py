"""The project has a home (Project workspace Phase 2): a project folder and
the Project panel.

A project lives in one folder — its ``.basproject`` beside one ``.baspec``
per section (decision D1). What the tests pin:

- discovery: a home needs a project link AND, in the section file's folder,
  a brief carrying the same project id; symlinked and unreadable candidates
  are skipped, never raised; the first match in name order wins;
- a home is never persisted — not in a saved project, not in a brief, and a
  reload has none — which is what lets a folder be moved or shared whole;
- the sections route joins the registry (the link with the brief on disk,
  newest export per number) with which files sit beside the brief, lists the
  folder's unregistered section files, and adds the open section as a row of
  its own when the registry does not list it yet;
- open-section runs the exact load-file path — state-for-state the session an
  upload of the same bytes produces — carries the home when the sibling
  belongs to the same project, and refuses before anything is replaced: an
  escape from the folder, a missing file, an unknown number, no home, a tour
  and running work;
- next-section carries the home across its seed;
- the native shell: a save discovers (or loses) the home, the first save of
  a section that arrived with a home opens in its folder, a brief exported
  beside the saved section makes the home at once, and ``bind_project_home``
  binds a natively opened file's folder only for the session its load
  produced.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path

from backend import project_brief, sessions
from backend.llm.conversation import SessionState
from backend.project_brief import (
    brief_bytes,
    build_project_brief,
    merge_section_registries,
    read_brief_on_disk,
)
from backend.spec_doc.project import load_project
from backend.spec_doc.project_package import parse_project_package
from tests.test_close_prompt import _FakeWindow, _controller_with, _fake_webview
from tests.test_project_brief import _client, _rich_session

BRIEF_NAME = "Client X data center.basproject"


def _project_folder(client, folder: Path) -> tuple[SessionState, Path]:
    """Section 21 13 13 saved in ``folder`` beside its exported brief — the
    state a user reaches by saving the section and exporting the brief next
    to it. Returns the live session (linked, no home yet) and its file."""
    session = _rich_session(client)
    section_file = folder / "21 13 13.baspec"
    # The registry records the section file's basename from the save target.
    session.save_target = str(section_file)
    exported = client.get("/api/project/brief")
    assert exported.status_code == 200, exported.text
    (folder / BRIEF_NAME).write_bytes(exported.content)
    section_file.write_bytes(sessions.project_package(session)[0])
    return session, section_file


def _home(session: SessionState, anchor: Path) -> dict[str, str]:
    home = sessions.discover_project_home(session, str(anchor))
    assert home is not None, "the fixture folder must be this project's home"
    assert sessions.remember_project_home(session, home)
    return home


def _two_section_folder(client, folder: Path):
    """21 13 13 and 21 30 00 in one folder, the brief listing both — built
    through the real routes: save, export, Next section →, save, export."""
    first, first_file = _project_folder(client, folder)
    home = _home(first, first_file)
    resp = client.post(
        "/api/project/next-section", json={"number": "21 30 00", "title": "Fire Pumps"}
    )
    assert resp.status_code == 200, resp.text
    second = sessions.get_session()
    second_file = folder / "21 30 00.baspec"
    second.save_target = str(second_file)
    exported = client.get("/api/project/brief")
    assert exported.status_code == 200, exported.text
    (folder / BRIEF_NAME).write_bytes(exported.content)
    second_file.write_bytes(sessions.project_package(second)[0])
    return second, first_file, second_file, home


def _projection(session: SessionState) -> dict:
    """What a load installs, as comparable data — the home and the save
    target excluded, since those are exactly what differs by route."""
    research = session.research.profile_result
    return {
        "doc": session.doc.to_dict(),
        "module": session.module.module_id,
        "discipline": session.discipline,
        "research": research.to_dict() if research is not None else None,
        "research_status": session.research.status,
        "references": session.references.to_dict(),
        "facts": session.facts.to_dict(),
        "followups": session.followups.to_dict(),
        "figures": session.figures.to_dict(),
        "link": copy.deepcopy(session.project_link),
        "history": list(session.history),
        "template_origin": session.template_origin,
        "import_report": session.import_report,
    }


def _link(project_id: str, *records: dict) -> dict:
    return {
        "project_id": project_id,
        "name": "Test project",
        "brief_updated_at": "",
        "seeded_from": [],
        "research_rounds_at_seed": 0,
        "sections": list(records),
    }


def _record(number: str, title: str, *, exported_at: str = "", file_name: str = "") -> dict:
    return {
        "number": number,
        "title": title,
        "module_id": "generic",
        "discipline": "",
        "article_titles": [],
        "ready": False,
        "exported_at": exported_at,
        "file_name": file_name,
        "fact_count": 0,
        "research_rounds": 0,
    }


def _write_brief(folder: Path, name: str, project_id: str, *records: dict) -> Path:
    session = SessionState()
    session.project_link = _link(project_id, *records)
    brief = build_project_brief(session, ready=False)
    brief.sections = [dict(r) for r in records]
    path = folder / name
    path.write_bytes(brief_bytes(brief))
    return path


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discovery_needs_a_link_and_a_matching_brief(tmp_path, monkeypatch):
    anchor = tmp_path / "21 13 13.baspec"
    anchor.write_bytes(b"section")
    project_id = "c" * 32
    session = SessionState()

    # No link: a section that never exported or seeded belongs to no project,
    # however many briefs sit beside its file.
    _write_brief(tmp_path, "c-match.basproject", project_id)
    assert sessions.discover_project_home(session, str(anchor)) is None

    # A link, and a brief of ANOTHER project only.
    session.project_link = _link(project_id)
    (tmp_path / "c-match.basproject").unlink()
    _write_brief(tmp_path, "b-other.basproject", "d" * 32)
    assert sessions.discover_project_home(session, str(anchor)) is None

    # The matching one wins; a malformed candidate ahead of it in name order
    # is skipped, never raised; the first match in name order wins.
    (tmp_path / "a-malformed.basproject").write_bytes(b"{not json")
    _write_brief(tmp_path, "c-match.basproject", project_id)
    _write_brief(tmp_path, "d-match-too.basproject", project_id)
    home = sessions.discover_project_home(session, str(anchor))
    assert home == {
        "folder": str(tmp_path),
        "brief_path": str(tmp_path / "c-match.basproject"),
        "brief_name": "c-match.basproject",
        "project_id": project_id,
    }

    # A folder that is not there is simply no home.
    assert sessions.discover_project_home(session, str(tmp_path / "gone" / "x.baspec")) is None

    # A symlinked candidate is never followed, even one sorting first and
    # pointing at a matching brief.
    try:
        os.symlink(tmp_path / "c-match.basproject", tmp_path / "0-link.basproject")
    except (OSError, NotImplementedError):
        symlinked = False
    else:
        symlinked = True
    if symlinked:
        assert sessions.discover_project_home(session, str(anchor))["brief_name"] == (
            "c-match.basproject"
        )
        assert read_brief_on_disk(str(tmp_path / "0-link.basproject")) is None

    # A candidate past the brief cap is refused from its size alone.
    monkeypatch.setattr(project_brief, "MAX_PROJECT_BRIEF_BYTES", 16)
    assert read_brief_on_disk(str(tmp_path / "c-match.basproject")) is None
    assert sessions.discover_project_home(session, str(anchor)) is None


def test_remembering_a_home_is_generation_checked_and_link_checked(tmp_path):
    session = SessionState()
    session.project_link = _link("e" * 32)
    home = {
        "folder": str(tmp_path),
        "brief_path": str(tmp_path / "p.basproject"),
        "brief_name": "p.basproject",
        "project_id": "e" * 32,
    }
    sampled = session.generation
    session.invalidate_model_turn()  # a reset or load raced the disk read
    assert sessions.remember_project_home(session, home, generation=sampled) is False
    assert session.project_home is None

    assert sessions.remember_project_home(session, home, generation=session.generation)
    assert session.project_home == home
    assert sessions.project_home_payload(session) == {
        "folder": str(tmp_path),
        "brief_name": "p.basproject",
    }

    # A home is never stored beside a link naming a different project.
    session.project_link = _link("f" * 32)
    assert sessions.remember_project_home(session, home)
    assert session.project_home is None


def test_a_home_is_never_persisted(tmp_path):
    client = _client()
    session, section_file = _project_folder(client, tmp_path)
    _home(session, section_file)
    folder = str(tmp_path).encode()

    payload, _name = sessions.project_package(session)
    parsed = parse_project_package(payload)
    assert "project_home" not in parsed.project
    assert folder not in payload

    brief = brief_bytes(build_project_brief(session, ready=False))
    assert folder not in brief
    # The one file reference a brief carries is the section's BASENAME.
    assert b"21 13 13.baspec" in brief

    fresh = SessionState()
    load_project(parsed.project, fresh)
    assert fresh.project_home is None

    # And the live session forgets it on load and on reset, like save_target.
    load_project(parsed.project, session)
    assert session.project_home is None
    _home(session, section_file)
    session.reset()
    assert session.project_home is None


def test_the_document_payload_states_the_home(tmp_path):
    client = _client()
    assert client.get("/api/doc").json()["project_home"] is None
    session, section_file = _project_folder(client, tmp_path)
    _home(session, section_file)
    assert client.get("/api/doc").json()["project_home"] == {
        "folder": str(tmp_path),
        "brief_name": BRIEF_NAME,
    }
    assert client.post("/api/session/reset", json={}).status_code == 200
    assert client.get("/api/doc").json()["project_home"] is None


# ---------------------------------------------------------------------------
# The sections route
# ---------------------------------------------------------------------------


def test_registries_merge_by_number_newest_export_winning_ties_to_the_link():
    link = [
        _record("21 13 13", "link title", exported_at="2026-09-01T00:00:00+00:00"),
        _record("21 30 00", "link pumps", exported_at="2026-09-02T00:00:00+00:00"),
        _record("21 12 00", "no file yet", exported_at="2026-09-05T00:00:00+00:00"),
    ]
    disk = [
        _record(
            "21 13 13",
            "disk title (newer)",
            exported_at="2026-09-03T00:00:00+00:00",
            file_name="a.baspec",
        ),
        _record("21 30 00", "disk pumps", exported_at="2026-09-02T00:00:00+00:00"),
        _record(
            "21 12 00",
            "older, but names the file",
            exported_at="2026-09-04T00:00:00+00:00",
            file_name="standpipes.baspec",
        ),
        _record("22 00 00", "only on disk"),
    ]
    merged = merge_section_registries(link, disk)
    by_number = {r["number"]: r for r in merged}
    assert [r["number"] for r in merged] == ["21 13 13", "21 30 00", "21 12 00", "22 00 00"]
    assert by_number["21 13 13"]["title"] == "disk title (newer)"
    assert by_number["21 30 00"]["title"] == "link pumps", "a tie goes to the link"
    # The newer record wins, and borrows the file the older one named.
    assert by_number["21 12 00"]["title"] == "no file yet"
    assert by_number["21 12 00"]["file_name"] == "standpipes.baspec"
    assert link[2]["file_name"] == "", "the merge never mutates its inputs"


def test_the_sections_route_joins_registry_and_folder(tmp_path):
    client = _client()
    session, section_file = _project_folder(client, tmp_path)
    home = _home(session, section_file)
    # The link names a second section whose file is not in the folder …
    session.project_link["sections"].append(
        _record(
            "21 30 00",
            "Fire Pumps",
            exported_at="2026-09-10T00:00:00+00:00",
            file_name="21 30 00.baspec",
        )
    )
    # … and the brief on disk lists a third the link never saw.
    on_disk = read_brief_on_disk(home["brief_path"])
    _write_brief(
        tmp_path,
        BRIEF_NAME,
        home["project_id"],
        *on_disk.sections,
        _record("21 12 00", "Standpipes", exported_at="2026-09-11T00:00:00+00:00", file_name="standpipes.baspec"),
    )
    (tmp_path / "standpipes.baspec").write_bytes(b"placeholder")
    # A section file no record names, and one that is only a link to one.
    (tmp_path / "an old copy.baspec").write_bytes(b"placeholder")
    try:
        os.symlink(section_file, tmp_path / "shortcut.baspec")
    except (OSError, NotImplementedError):
        pass

    body = client.get("/api/project/sections").json()

    assert body["ok"] is True
    assert body["home"] == {"folder": str(tmp_path), "brief_name": BRIEF_NAME}
    assert body["project"]["project_id"] == home["project_id"]
    assert body["current_number"] == "21 13 13"
    rows = {row["number"]: row for row in body["sections"]}
    assert list(rows) == ["21 13 13", "21 30 00", "21 12 00"]
    assert rows["21 13 13"]["is_current"] and rows["21 13 13"]["present"]
    assert not rows["21 30 00"]["present"] and not rows["21 30 00"]["is_current"]
    assert rows["21 12 00"]["present"]
    assert all(row["in_registry"] for row in rows.values())
    assert body["unregistered"] == ["an old copy.baspec"]
    assert body["brief_updated_at"]
    assert body["warnings"] == []


def test_the_open_section_is_a_row_even_before_the_registry_lists_it(tmp_path):
    client = _client()
    second, _first_file, _second_file, _home_dict = _two_section_folder(client, tmp_path)
    # A third section, just started: named, homed, in no registry yet.
    resp = client.post(
        "/api/project/next-section", json={"number": "21 12 00", "title": "Standpipes"}
    )
    assert resp.status_code == 200, resp.text
    third = sessions.get_session()
    assert third.project_home is not None

    body = client.get("/api/project/sections").json()

    rows = body["sections"]
    assert [r["number"] for r in rows] == ["21 13 13", "21 30 00", "21 12 00"]
    current = rows[-1]
    assert current["is_current"] and current["in_registry"] is False
    assert current["title"] == "Standpipes" and current["present"] is False
    assert all(r["present"] for r in rows[:2])


def test_the_sections_route_answers_without_a_home_and_says_when_the_brief_is_gone(tmp_path):
    client = _client()
    session, section_file = _project_folder(client, tmp_path)
    body = client.get("/api/project/sections").json()
    assert body["home"] is None
    assert [r["number"] for r in body["sections"]] == ["21 13 13"]
    assert body["sections"][0]["present"] is False, "no folder, so nothing is judged"
    assert body["unregistered"] == []

    _home(session, section_file)
    (tmp_path / BRIEF_NAME).unlink()
    body = client.get("/api/project/sections").json()
    assert body["home"] is not None
    assert any("could not be read" in w for w in body["warnings"])
    assert [r["number"] for r in body["sections"]] == ["21 13 13"]


# ---------------------------------------------------------------------------
# open-section
# ---------------------------------------------------------------------------


def test_open_section_runs_the_real_load_path(tmp_path):
    client = _client()
    second, first_file, _second_file, home = _two_section_folder(client, tmp_path)
    assert second.doc.doc.number == "21 30 00"

    resp = client.post("/api/project/open-section", json={"number": "21 13 13"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["section"] == "21 13 13" and body["home_kept"] is True
    assert body["project_home"] == {"folder": str(tmp_path), "brief_name": BRIEF_NAME}
    assert body["doc"]["section"]["number"] == "21 13 13"
    assert body["chat"], "the transcript comes back like a load-file"
    opened = sessions.get_session()
    assert opened.project_home == home, "the home survives the load"
    assert opened.save_target == "", "an open never establishes a save target"
    via_section = _projection(opened)

    # The same bytes through the upload path: the same session, minus the home.
    sessions.reset_session()
    client = _client()
    uploaded = client.post(
        "/api/project/load-file",
        files={"file": ("21 13 13.baspec", first_file.read_bytes(), "application/zip")},
    )
    assert uploaded.status_code == 200, uploaded.text
    via_upload = sessions.get_session()
    assert _projection(via_upload) == via_section
    assert via_upload.project_home is None
    assert "home_kept" not in uploaded.json()

    # And back again, by the other number.
    opened_again = _client()
    sessions.remember_project_home(via_upload, home)
    back = opened_again.post("/api/project/open-section", json={"number": "21 30 00"})
    assert back.status_code == 200, back.text
    assert sessions.get_session().doc.doc.number == "21 30 00"


def test_open_section_of_a_file_from_another_project_drops_the_home(tmp_path):
    client = _client()
    session, section_file = _project_folder(client, tmp_path)
    home = _home(session, section_file)
    # A file in the folder that belongs to no project at all.
    stray = SessionState()
    stray.doc.begin_turn()
    stray.doc.apply_edits(
        [{"action": "replace", "target_id": "sec", "text": "Stray", "numbering": "21 99 99"}]
    )
    stray.doc.commit_turn()
    (tmp_path / "stray.baspec").write_bytes(sessions.project_package(stray)[0])
    session.project_link["sections"].append(
        _record("21 99 99", "Stray", file_name="stray.baspec")
    )
    assert home

    resp = client.post("/api/project/open-section", json={"number": "21 99 99"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["home_kept"] is False
    assert sessions.get_session().project_home is None
    assert sessions.get_session().doc.doc.number == "21 99 99"


def test_open_section_refuses_escapes_and_absences(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    client = _client()
    session, section_file = _project_folder(client, folder)
    before = _projection(session)

    # No home yet.
    resp = client.post("/api/project/open-section", json={"number": "21 13 13"})
    assert resp.status_code == 409 and resp.json()["code"] == "no_project_home"

    home = _home(session, section_file)
    outside = tmp_path / "other.baspec"
    outside.write_bytes(section_file.read_bytes())  # a real project, one level up
    session.project_link["sections"].extend(
        [
            _record("99 00 01", "Escape", file_name="../other.baspec"),
            _record("99 00 02", "Absolute", file_name=str(outside)),
            _record("99 00 03", "Gone", file_name="gone.baspec"),
            _record("99 00 04", "Unsaved"),
        ]
    )
    for number, status, code in (
        ("99 00 01", 400, "outside_project_folder"),
        ("99 00 02", 400, "outside_project_folder"),
        ("99 00 03", 404, "section_file_missing"),
        ("99 00 04", 404, "section_file_missing"),
        ("00 00 00", 404, "section_not_found"),
    ):
        resp = client.post("/api/project/open-section", json={"number": number})
        assert (resp.status_code, resp.json().get("code")) == (status, code), (number, resp.text)

    # A symlink inside the folder that resolves outside it is an escape too.
    try:
        os.symlink(outside, folder / "shortcut.baspec")
    except (OSError, NotImplementedError):
        pass
    else:
        session.project_link["sections"].append(
            _record("99 00 05", "Shortcut", file_name="shortcut.baspec")
        )
        resp = client.post("/api/project/open-section", json={"number": "99 00 05"})
        assert resp.status_code == 400 and resp.json()["code"] == "outside_project_folder"

    # A blank number.
    assert client.post("/api/project/open-section", json={"number": " "}).status_code == 400

    # Running work.
    claimed = session.claim_model_turn()
    try:
        resp = client.post("/api/project/open-section", json={"number": "21 13 13"})
        assert resp.status_code == 409 and resp.json()["code"] == "workspace_busy"
    finally:
        session.release_model_turn(claimed[0])

    # Nothing was replaced by any refusal.
    after = _projection(session)
    after["link"]["sections"] = after["link"]["sections"][:1]
    assert after == before
    assert session.project_home == home
    assert sessions.get_session() is session

    # And a tour refuses before anything is read.
    original = sessions.get_workspace()
    started = client.post(
        "/api/tutorial/start",
        json={
            "request_id": "open-section-refuses-in-a-tour",
            "source": "showcase",
            "workspace_id": original.workspace_id,
            "generation": original.generation,
        },
    )
    assert started.status_code == 200, started.text
    try:
        resp = client.post("/api/project/open-section", json={"number": "21 13 13"})
        assert resp.status_code == 409 and resp.json()["code"] == "tutorial_active"
    finally:
        sessions.reset_session()


def test_work_that_starts_while_a_section_is_read_is_not_killed_by_the_swap(
    tmp_path, monkeypatch
):
    """The entry check alone is a window, not a gate: staging runs for
    seconds on a worker thread, and ``load_project`` invalidates whatever
    turn owns the session. A turn claimed during staging must survive — the
    commit re-checks running work under its own guard and refuses."""
    from backend import app as app_module

    client = _client()
    second, _first_file, _second_file, home = _two_section_folder(client, tmp_path)
    before = _projection(second)
    claimed: list = []
    real_stage = app_module._stage_project_load

    def stage_while_a_turn_starts(payload):
        claimed.append(second.claim_model_turn())
        return real_stage(payload)

    monkeypatch.setattr(app_module, "_stage_project_load", stage_while_a_turn_starts)
    try:
        resp = client.post("/api/project/open-section", json={"number": "21 13 13"})
        assert resp.status_code == 409, resp.text
        assert resp.json()["code"] == "workspace_busy"
        assert "chat" in resp.json()["error"]
        # The turn still owns the session: nothing invalidated it.
        assert claimed and claimed[0] is not None
        assert second.turn_active and second._active_turn_token is claimed[0][0]
    finally:
        if claimed and claimed[0] is not None:
            second.release_model_turn(claimed[0][0])

    assert sessions.get_session() is second
    assert _projection(second) == before
    assert second.project_home == home


def test_a_section_file_that_grows_past_the_bound_mid_read_is_refused(
    tmp_path, monkeypatch
):
    """The size is judged on the bytes actually read, not only on the
    ``getsize`` that preceded the read."""
    from backend import app as app_module

    client = _client()
    second, first_file, _second_file, _home_ = _two_section_folder(client, tmp_path)
    before = _projection(second)
    bound = 1024
    assert first_file.stat().st_size > bound, "the fixture must exceed the patched bound"
    monkeypatch.setattr(app_module, "MAX_PACKAGE_BYTES", bound)
    real_getsize = os.path.getsize

    def reports_a_smaller_size(path):
        # The file "grew" after its size was sampled.
        if os.path.realpath(path) == os.path.realpath(first_file):
            return 1
        return real_getsize(path)

    monkeypatch.setattr(app_module.os.path, "getsize", reports_a_smaller_size)

    resp = client.post("/api/project/open-section", json={"number": "21 13 13"})

    assert resp.status_code == 413, resp.text
    assert "too large" in resp.json()["error"]
    assert sessions.get_session() is second
    assert _projection(second) == before


def test_the_sections_route_answers_in_a_tutorial_practice_copy():
    """The tour's panel reads its seeded registry: no home, no disk."""
    client = _client()
    original = sessions.get_workspace()
    started = client.post(
        "/api/tutorial/start",
        json={
            "request_id": "project-panel-in-a-tour",
            "source": "showcase",
            "workspace_id": original.workspace_id,
            "generation": original.generation,
        },
    ).json()
    scenario = client.post(
        "/api/tutorial/scenario/start",
        json={
            "tutorial_id": started["tutorial_id"],
            "workspace_id": started["session"]["workspace_id"],
            "generation": started["session"]["generation"],
            "chapter": "structural",
        },
    )
    assert scenario.status_code == 200, scenario.text
    try:
        body = client.get("/api/project/sections").json()
        assert body["ok"] is True and body["home"] is None
        assert [r["number"] for r in body["sections"]] == ["21 13 13", "21 30 00"]
        assert not any(r["is_current"] for r in body["sections"]), "the header is blank"
        assert scenario.json()["session"]["project_link"]["name"].startswith("Tutorial")
    finally:
        sessions.reset_session()


# ---------------------------------------------------------------------------
# next-section
# ---------------------------------------------------------------------------


def test_next_section_keeps_the_home(tmp_path):
    client = _client()
    session, section_file = _project_folder(client, tmp_path)
    home = _home(session, section_file)

    resp = client.post(
        "/api/project/next-section", json={"number": "21 30 00", "title": "Fire Pumps"}
    )

    assert resp.status_code == 200, resp.text
    seeded = sessions.get_session()
    assert seeded.project_home == home
    assert seeded.save_target == "", "the new section has not been saved anywhere yet"
    assert resp.json()["session"]["project_home"] == {
        "folder": str(tmp_path),
        "brief_name": BRIEF_NAME,
    }


def test_next_section_without_a_home_starts_without_one():
    client = _client()
    _rich_session(client)
    resp = client.post("/api/project/next-section", json={"number": "21 30 00"})
    assert resp.status_code == 200, resp.text
    assert sessions.get_session().project_home is None


# ---------------------------------------------------------------------------
# The native shell
# ---------------------------------------------------------------------------


def test_a_native_save_beside_the_brief_finds_the_home_and_one_elsewhere_loses_it(
    tmp_path, monkeypatch
):
    _fake_webview(monkeypatch)
    folder = tmp_path / "project"
    folder.mkdir()
    client = _client()
    session, section_file = _project_folder(client, folder)
    session.save_target = ""  # saved by the fixture, not by this session
    window = _FakeWindow(dialog_path=str(section_file))
    controller = _controller_with(window)

    saved = controller.save_project()

    assert saved["ok"] is True
    assert saved["home"] == {"folder": str(folder), "brief_name": BRIEF_NAME}
    assert session.project_home["brief_path"] == str(folder / BRIEF_NAME)

    # Save as… somewhere with no brief: the section no longer lives in its
    # project folder, and the home says so.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    window._dialog_path = str(elsewhere / "copy.baspec")
    moved = controller.save_project_as()
    assert moved["ok"] is True and moved["home"] is None
    assert session.project_home is None


def test_the_first_save_of_a_homed_section_opens_in_the_project_folder(tmp_path, monkeypatch):
    _fake_webview(monkeypatch)
    client = _client()
    session, section_file = _project_folder(client, tmp_path)
    _home(session, section_file)
    resp = client.post(
        "/api/project/next-section", json={"number": "21 30 00", "title": "Fire Pumps"}
    )
    assert resp.status_code == 200, resp.text
    target = tmp_path / "21 30 00.baspec"
    window = _FakeWindow(dialog_path=str(target))
    controller = _controller_with(window)

    saved = controller.save_project()

    (_args, kwargs), = window.dialog_calls
    assert kwargs["directory"] == str(tmp_path)
    assert saved["home"] == {"folder": str(tmp_path), "brief_name": BRIEF_NAME}


def _saved_link(path: Path) -> dict | None:
    """The project link a saved ``.baspec`` actually carries on disk."""
    return parse_project_package(path.read_bytes()).project.get("project_link")


def test_a_brief_export_never_binds_the_folder_the_next_save_does(
    tmp_path, monkeypatch
):
    """Caught in review on PR #176 (Codex). Exporting a brief stamps the
    project link in the LIVE session only, so a section saved BEFORE its first
    export has no link on disk. Binding the folder at the export (the first
    cut did, for a brief written beside the saved file) made the panel promise
    a folder the file could never find again: close the app, reopen the
    section, and the link — and with it the folder, the panel and sibling
    Open — was gone. The export binds nothing; the save that follows writes
    the link AND finds the folder, and the reopened file finds it too."""
    import main

    _fake_webview(monkeypatch)
    client = _client()
    session = _rich_session(client)
    assert session.project_link is None, "a section that never exported"
    section_file = tmp_path / "21 13 13.baspec"
    window = _FakeWindow(dialog_path=str(section_file))
    controller = main._CloseController(
        None, backend=type("B", (), {"host": "127.0.0.1", "port": 1, "api_token": "t"})()
    )
    controller._bind(window)

    # 1. Saved first: no link, so no project and no folder.
    first = controller.save_project()
    assert first["ok"] is True and first["home"] is None
    assert _saved_link(section_file) is None

    # 2. The brief exported beside it. The route stamps the live link (the
    #    shell fetches exactly this route); the file on disk has none.
    exported = client.get("/api/project/brief")
    assert exported.status_code == 200
    monkeypatch.setattr(
        main,
        "_fetch_backend_bytes",
        lambda backend, path, **kw: (exported.content, BRIEF_NAME),
    )
    window._dialog_path = str(tmp_path / BRIEF_NAME)
    result = controller.save_project_brief()
    assert result["ok"] is True
    assert session.project_link is not None, "the export stamped the live link"
    assert session.project_home is None, "the export binds no folder"
    assert result["home"] is None
    assert client.get("/api/doc").json()["project_home"] is None
    assert _saved_link(section_file) is None, "…because the file still has no link"

    # 3. The next save writes the link and finds the folder in one step.
    second = controller.save_project()
    assert second["ok"] is True
    assert second["home"] == {"folder": str(tmp_path), "brief_name": BRIEF_NAME}
    assert session.project_home["folder"] == str(tmp_path)
    project_id = session.project_link["project_id"]
    assert _saved_link(section_file)["project_id"] == project_id

    # A brief exported ANYWHERE ELSE leaves the section's folder alone.
    other = tmp_path / "other"
    other.mkdir()
    window._dialog_path = str(other / BRIEF_NAME)
    assert controller.save_project_brief()["ok"] is True
    assert session.project_home["folder"] == str(tmp_path)

    # 4. Close and reopen — the scenario the export-time binding broke. The
    #    reopened file carries the link, so its folder is found again.
    sessions.reset_session()
    client = _client()
    window._dialog_path = str(section_file)
    picked = controller.open_file("project")
    loaded = client.post(
        "/api/project/load-file",
        files={"file": (picked["name"], section_file.read_bytes(), "application/zip")},
    )
    assert loaded.status_code == 200, loaded.text
    bound = controller.bind_project_home(picked["token"], loaded.json()["generation"])
    assert bound["ok"] is True
    assert bound["home"] == {"folder": str(tmp_path), "brief_name": BRIEF_NAME}


def test_a_natively_opened_file_binds_its_folder_for_the_session_its_load_produced(
    tmp_path, monkeypatch
):
    _fake_webview(monkeypatch)
    folder = tmp_path / "project"
    folder.mkdir()
    client = _client()
    _session, section_file = _project_folder(client, folder)
    sessions.reset_session()
    client = _client()
    window = _FakeWindow(dialog_path=str(section_file))
    controller = _controller_with(window)

    picked = controller.open_file("project")
    loaded = client.post(
        "/api/project/load-file",
        files={"file": (picked["name"], section_file.read_bytes(), "application/zip")},
    )
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["project_home"] is None, "a load alone never has a home"

    bound = controller.bind_project_home(picked["token"], loaded.json()["generation"])

    assert bound == {
        "ok": True,
        "home": {"folder": str(folder), "brief_name": BRIEF_NAME},
        "error": "",
    }
    assert client.get("/api/doc").json()["project_home"]["folder"] == str(folder)

    # A moved folder still resolves: nothing about the old location was kept.
    moved = tmp_path / "moved"
    folder.rename(moved)
    moved_file = moved / section_file.name
    window._dialog_path = str(moved_file)
    again = controller.open_file("project")
    reloaded = client.post(
        "/api/project/load-file",
        files={"file": (again["name"], moved_file.read_bytes(), "application/zip")},
    )
    assert reloaded.status_code == 200
    rebound = controller.bind_project_home(again["token"], reloaded.json()["generation"])
    assert rebound["home"] == {"folder": str(moved), "brief_name": BRIEF_NAME}
