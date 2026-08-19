"""Save asks once per session, then overwrites — the way a save button works.

Before this, every save went through the native Save dialog: the user named
the file and picked its folder each time, and a session saved five times left
five timestamped files with no way to say "the same one again". Now the FIRST
save of a session establishes a target, later saves write it in place with no
dialog, and the dialog moves behind "Save as…".

What the tests here are really guarding is the other half of that: an
overwrite is silent, so the target must never outlive the session that chose
it. It is ``SessionState.save_target``, which means the reset and the project
load that clear every other field clear it too — and the field sweep in
``test_session_wipe`` is what keeps that true for the next person. A target
that survived a New session would silently overwrite the project the user
just walked away from.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend import sessions
from backend.app import create_app
from backend.llm.conversation import SessionState
from backend.spec_doc.project import load_project
from backend.spec_doc.project_package import parse_project_package
from tests.test_close_prompt import (
    _FakeWindow,
    _controller_with,
    _fake_webview,
)


def _draft(text: str) -> None:
    """Put something in the active session's document, so a save has content
    worth comparing between writes."""
    session = sessions.get_session()
    session.doc.begin_turn()
    session.doc.apply_edits(
        [{"action": "add_article", "target_id": "pt1", "text": text}]
    )
    session.doc.commit_turn()


def _articles(path) -> list[str]:
    """The article titles a saved package actually contains."""
    parsed = parse_project_package(path.read_bytes())
    versions = parsed.project["doc"]["versions"]
    latest = versions[-1]
    return [
        article["title"]
        for part in latest["parts"]
        for article in part["articles"]
    ]


# --- the headline behavior -------------------------------------------------


def test_the_first_save_asks_and_the_second_overwrites_without_asking(
    tmp_path, monkeypatch
):
    _fake_webview(monkeypatch)
    target = tmp_path / "the-project.baspec"
    window = _FakeWindow(dialog_path=str(target))
    controller = _controller_with(window)
    _draft("SUMMARY")

    first = controller.save_project()

    assert first["ok"] is True
    assert first["target"] == str(target)
    assert len(window.dialog_calls) == 1, "the first save asks where"

    second = controller.save_project()

    assert second["ok"] is True
    assert second["target"] == str(target)
    assert len(window.dialog_calls) == 1, (
        "the second save must overwrite the established file, not ask again"
    )


def test_an_overwrite_writes_the_session_as_it_is_now(tmp_path, monkeypatch):
    # The point of a save button: it saves what is on screen NOW. An overwrite
    # that re-wrote the first save's bytes would be worse than useless.
    _fake_webview(monkeypatch)
    target = tmp_path / "the-project.baspec"
    controller = _controller_with(_FakeWindow(dialog_path=str(target)))
    _draft("SUMMARY")
    controller.save_project()
    assert _articles(target) == ["SUMMARY"]

    _draft("REFERENCES")
    assert controller.save_project()["ok"] is True

    assert _articles(target) == ["SUMMARY", "REFERENCES"]


def test_save_as_always_asks_and_becomes_the_new_target(tmp_path, monkeypatch):
    _fake_webview(monkeypatch)
    first_file = tmp_path / "first.baspec"
    second_file = tmp_path / "second.baspec"
    window = _FakeWindow(dialog_path=str(first_file))
    controller = _controller_with(window)
    _draft("SUMMARY")
    controller.save_project()

    window._dialog_path = str(second_file)
    _draft("REFERENCES")
    saved_as = controller.save_project_as()

    assert saved_as["ok"] is True
    assert len(window.dialog_calls) == 2, "Save as… always asks"
    assert second_file.exists()
    assert _articles(first_file) == ["SUMMARY"], "the old file is left alone"

    # …and from here plain Save overwrites the file Save as… chose.
    _draft("SUBMITTALS")
    assert controller.save_project()["ok"] is True
    assert len(window.dialog_calls) == 2
    assert _articles(second_file) == ["SUMMARY", "REFERENCES", "SUBMITTALS"]
    assert _articles(first_file) == ["SUMMARY"]


def test_save_as_opens_in_the_current_targets_folder(tmp_path, monkeypatch):
    # Same folder, but still a fresh timestamped filename: confirming the
    # dialog should write a NEW file, or "Save as…" is just Save with extra
    # steps.
    _fake_webview(monkeypatch)
    folder = tmp_path / "projects"
    folder.mkdir()
    window = _FakeWindow(dialog_path=str(folder / "first.baspec"))
    controller = _controller_with(window)
    controller.save_project()

    controller.save_project_as()

    _args, kwargs = window.dialog_calls[-1]
    assert kwargs["directory"] == str(folder)
    assert kwargs["save_filename"] != "first.baspec"
    assert kwargs["save_filename"].endswith(".baspec")


# --- the target belongs to the session, and dies with it -------------------


def test_a_new_session_forgets_where_it_was_saving(tmp_path, monkeypatch):
    # The one that matters. "New session" replaces everything; a target that
    # survived it would make the next Save silently overwrite the project the
    # user just discarded.
    _fake_webview(monkeypatch)
    target = tmp_path / "the-old-project.baspec"
    window = _FakeWindow(dialog_path=str(target))
    controller = _controller_with(window)
    _draft("SUMMARY")
    controller.save_project()
    assert _articles(target) == ["SUMMARY"]

    client = TestClient(create_app())
    assert client.post("/api/session/reset", json={}).status_code == 200
    _draft("A DIFFERENT PROJECT")

    window._dialog_path = str(tmp_path / "the-new-project.baspec")
    assert controller.save_project()["ok"] is True

    assert len(window.dialog_calls) == 2, "a fresh session asks again"
    assert _articles(target) == ["SUMMARY"], "the old project is untouched"


def test_opening_a_project_forgets_where_the_previous_one_was_saving():
    # Loading a file is the same replacement as a reset. Deliberately it does
    # NOT adopt the opened file as the target either: the rule is that a save
    # establishes where a session saves, and nothing else does.
    session = SessionState()
    session.save_target = "/somewhere/the-previous-project.baspec"

    load_project(sessions.project_payload(SessionState()), session)

    assert session.save_target == ""
    assert sessions.project_save_target(session) is None


def test_a_tutorial_copy_never_becomes_the_file_save_overwrites(
    tmp_path, monkeypatch
):
    # A tutorial workspace is a disposable practice copy. The native save
    # refuses it outright (the panel downloads the copy instead), so the tour
    # can neither establish a target nor overwrite the real project's file.
    _fake_webview(monkeypatch)
    target = tmp_path / "the-real-project.baspec"
    window = _FakeWindow(dialog_path=str(target))
    controller = _controller_with(window)
    _draft("SUMMARY")
    controller.save_project()
    original = sessions.get_session()

    manager = sessions.workspace_manager()
    manager.begin_tutorial(request_id="save-target-contract")
    try:
        refused = controller.save_project()
        assert refused["ok"] is False
        assert refused["error"]
        assert sessions.project_save_target(sessions.get_session()) is None
    finally:
        manager.finish_tutorial(manager.current().workspace_id)

    assert _articles(target) == ["SUMMARY"], "the tour wrote nothing"
    assert sessions.project_save_target(original) == {
        "path": str(target),
        "name": "the-real-project.baspec",
    }


def test_a_reset_during_the_save_dialog_does_not_bind_the_fresh_session(
    tmp_path, monkeypatch
):
    # The file the user named is still written — they asked for it, and it
    # holds the session they were looking at. What must not happen is the
    # REPLACEMENT session inheriting it as a silent overwrite target.
    _fake_webview(monkeypatch)
    target = tmp_path / "named-then-replaced.baspec"

    class _ResettingWindow(_FakeWindow):
        def create_file_dialog(self, *args, **kwargs):
            sessions.get_session().reset()  # a New session, mid-dialog
            return super().create_file_dialog(*args, **kwargs)

    controller = _controller_with(_ResettingWindow(dialog_path=str(target)))
    _draft("SUMMARY")

    assert controller.save_project()["ok"] is True

    assert target.exists()
    assert sessions.project_save_target(sessions.get_session()) is None


# --- failure, and what the rest of the app sees ----------------------------


def test_a_target_that_can_no_longer_be_written_asks_again(
    tmp_path, monkeypatch
):
    # Moved, deleted, read-only, a disconnected network drive. The user asked
    # for a save; the honest answer to "that file is gone" is to ask where it
    # goes now, not to fail a button that used to work.
    _fake_webview(monkeypatch)
    folder = tmp_path / "removable"
    folder.mkdir()
    gone = folder / "on-a-usb-stick.baspec"
    window = _FakeWindow(dialog_path=str(gone))
    controller = _controller_with(window)
    controller.save_project()
    gone.unlink()
    folder.rmdir()  # the whole location is unreachable now

    rescued = tmp_path / "rescued.baspec"
    window._dialog_path = str(rescued)
    result = controller.save_project()

    assert result["ok"] is True
    assert result["target"] == str(rescued)
    assert len(window.dialog_calls) == 2
    assert rescued.exists()
    # …and the rescue is where it saves from now on.
    assert controller.save_project()["ok"] is True
    assert len(window.dialog_calls) == 2


def test_the_document_payload_states_where_the_session_saves(
    tmp_path, monkeypatch
):
    # The panel draws a plain Save or the split button off this, and must not
    # derive it locally: a client that kept its own copy would offer to
    # overwrite a file the session has already been reset away from.
    _fake_webview(monkeypatch)
    client = TestClient(create_app())

    assert client.get("/api/doc").json()["project_save_target"] is None

    target = tmp_path / "the-project.baspec"
    controller = _controller_with(_FakeWindow(dialog_path=str(target)))
    controller.save_project()

    assert client.get("/api/doc").json()["project_save_target"] == {
        "path": str(target),
        "name": "the-project.baspec",
    }

    assert client.post("/api/session/reset", json={}).status_code == 200
    assert client.get("/api/doc").json()["project_save_target"] is None


def test_the_saved_file_is_never_told_where_it_came_from(
    tmp_path, monkeypatch
):
    # A .baspec is a file people copy and share. The path it was written from
    # says nothing about where its next reader should save it, so it stays out
    # of the package entirely.
    _fake_webview(monkeypatch)
    target = tmp_path / "the-project.baspec"
    controller = _controller_with(_FakeWindow(dialog_path=str(target)))
    _draft("SUMMARY")
    controller.save_project()

    assert "save_target" not in parse_project_package(target.read_bytes()).project
    assert str(target).encode() not in target.read_bytes()


def test_save_and_close_overwrites_the_established_file(tmp_path, monkeypatch):
    # The close prompt's "Save & close" is the same save. Once the session has
    # a file, closing writes it without a dialog in the way.
    _fake_webview(monkeypatch)
    target = tmp_path / "the-project.baspec"
    window = _FakeWindow(dialog_path=str(target))
    controller = _controller_with(window)
    _draft("SUMMARY")
    controller.save_project()

    _draft("REFERENCES")
    controller.save_and_close()

    assert len(window.dialog_calls) == 1
    assert _articles(target) == ["SUMMARY", "REFERENCES"]
    assert window.destroyed is True
