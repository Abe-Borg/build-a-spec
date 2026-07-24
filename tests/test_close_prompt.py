"""Save-progress-on-close: the testable seams of the native close flow.

The pywebview integration itself (the real `closing` event, `evaluate_js`,
native dialogs) needs a GUI and is out of reach of the hermetic suite. What
*is* testable — and what actually carries the behavior — is exercised here:
the unsaved-progress predicate, the shared save-payload helpers, and the
`_CloseController` decision logic driven by a fake window.
"""
from __future__ import annotations

import enum
import re
import sys
import time
import types

from backend import sessions
from backend.llm.conversation import SessionState
from backend.spec_doc.project_package import parse_project_package

import main


# --- progress predicate + save-payload helpers -----------------------------


def _session_with_history() -> SessionState:
    session = SessionState()
    session.history.append(
        {"role": "user", "content": [{"type": "text", "text": "hello"}]}
    )
    return session


def test_has_unsaved_progress_empty_is_false():
    assert sessions.has_unsaved_progress(SessionState()) is False


def test_has_unsaved_progress_with_history():
    assert sessions.has_unsaved_progress(_session_with_history()) is True


def test_has_unsaved_progress_with_doc_content():
    session = SessionState()
    session.doc.doc.number = "21 13 13"
    assert sessions.has_unsaved_progress(session) is True


def test_has_unsaved_progress_with_only_figures():
    # A chat-authored figure is content worth saving even with no history and
    # a blank document — the save gate depends on this (a figure must never be
    # discarded silently just because it isn't "in the document").
    session = SessionState()
    session.figures.create(
        {"kind": "mermaid", "title": "Riser", "source": "graph TD; A-->B"}
    )
    assert not session.history
    assert session.doc.doc.is_empty()
    assert sessions.has_unsaved_progress(session) is True


def test_project_payload_shape_and_stem_default():
    session = SessionState()
    payload = sessions.project_payload(session)
    assert payload["kind"] == "buildaspec-project"
    assert payload["format"] == 1
    assert "history" in payload and "doc" in payload
    # An empty section has no number -> the fallback stem.
    assert sessions.project_default_stem(session) == "draft"


def test_project_default_stem_from_section_number():
    session = SessionState()
    session.doc.doc.number = "21 13 13"
    assert sessions.project_default_stem(session) == "211313"


_TIMESTAMP_RE = r"\d{4}-\d{2}-\d{2}-\d{6}"


def test_project_default_filename_is_timestamped():
    session = SessionState()
    session.doc.doc.number = "21 13 13"
    filename = sessions.project_default_filename(session)
    assert re.fullmatch(rf"buildaspec-211313-{_TIMESTAMP_RE}\.baspec", filename)


def test_project_default_filename_fallback_stem():
    filename = sessions.project_default_filename(SessionState())
    assert re.fullmatch(rf"buildaspec-draft-{_TIMESTAMP_RE}\.baspec", filename)


def test_project_default_filename_distinguishes_same_day_saves():
    # Two saves of the same section, even moments apart, must not collide —
    # the native Save dialog defaulting to a stale filename would otherwise
    # risk silently overwriting the earlier save (Codex review on PR #24).
    session = SessionState()
    first = sessions.project_default_filename(session)
    time.sleep(1.1)  # timestamp resolution is whole seconds
    second = sessions.project_default_filename(session)
    assert first != second


# --- _CloseController driven by a fake pywebview window ---------------------


class _FakeEvent:
    def __init__(self) -> None:
        self.handlers: list = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self


class _FakeEvents:
    def __init__(self) -> None:
        self.closing = _FakeEvent()


class _FakeWindow:
    """Records the controller's calls; no GUI involved."""

    def __init__(self, evaluate_return=True, dialog_path=None) -> None:
        self.events = _FakeEvents()
        self.destroyed = False
        self.evaluated: list[str] = []
        self.dialog_calls: list = []
        self._evaluate_return = evaluate_return
        self._dialog_path = dialog_path

    def evaluate_js(self, js: str):
        self.evaluated.append(js)
        if isinstance(self._evaluate_return, Exception):
            raise self._evaluate_return
        return self._evaluate_return

    def destroy(self) -> None:
        self.destroyed = True

    def create_file_dialog(self, *args, **kwargs):
        self.dialog_calls.append((args, kwargs))
        return self._dialog_path


def _controller_with(window: _FakeWindow) -> main._CloseController:
    controller = main._CloseController()
    controller._bind(window)
    # _bind subscribes the closing handler.
    assert controller._on_closing in window.events.closing.handlers
    return controller


def test_on_closing_after_confirmation_lets_it_close():
    window = _FakeWindow()
    controller = _controller_with(window)
    controller._allow_close = True
    assert controller._on_closing() is None


def test_on_closing_no_progress_does_not_prompt():
    # conftest's autouse fixture leaves the module-level session empty.
    window = _FakeWindow()
    controller = _controller_with(window)
    assert controller._on_closing() is None
    assert window.evaluated == []


def test_on_closing_with_progress_vetoes_and_asks_frontend():
    sessions.get_session().history.append(
        {"role": "user", "content": [{"type": "text", "text": "hi"}]}
    )
    window = _FakeWindow(evaluate_return=True)
    controller = _controller_with(window)
    assert controller._on_closing() is False  # veto the native close
    # The frontend prompt runs on a worker thread; wait for it.
    for _ in range(200):
        if window.evaluated:
            break
        time.sleep(0.01)
    assert window.evaluated, "expected the frontend prompt to be dispatched"
    assert window.destroyed is False  # handled -> stay open until the choice


def test_ask_frontend_handled_stays_open():
    window = _FakeWindow(evaluate_return=True)
    controller = _controller_with(window)
    controller._ask_frontend()
    assert window.evaluated
    assert window.destroyed is False


def test_ask_frontend_unhandled_never_traps_the_user():
    window = _FakeWindow(evaluate_return=False)
    controller = _controller_with(window)
    controller._ask_frontend()
    assert window.destroyed is True


def test_ask_frontend_evaluate_error_never_traps_the_user():
    window = _FakeWindow(evaluate_return=RuntimeError("boom"))
    controller = _controller_with(window)
    controller._ask_frontend()
    assert window.destroyed is True


def test_discard_and_close_closes_without_saving():
    window = _FakeWindow()
    controller = _controller_with(window)
    controller.discard_and_close()
    assert controller._allow_close is True
    assert window.destroyed is True
    assert window.dialog_calls == []  # no save dialog


class _FakeFileDialog(enum.IntEnum):
    """Mirrors pywebview's ``FileDialog`` enum; values are sentinels only."""

    OPEN = 10
    FOLDER = 20
    SAVE = 30


def _fake_webview(monkeypatch) -> None:
    module = types.ModuleType("webview")
    module.FileDialog = _FakeFileDialog
    monkeypatch.setitem(sys.modules, "webview", module)


def test_save_and_close_writes_file_then_closes(tmp_path, monkeypatch):
    _fake_webview(monkeypatch)
    target = tmp_path / "buildaspec-draft.baspec"
    window = _FakeWindow(dialog_path=str(target))
    controller = _controller_with(window)

    controller.save_and_close()

    assert target.exists()
    parsed = parse_project_package(target.read_bytes())
    assert parsed.project["kind"] == "buildaspec-project"
    assert parsed.source_docx_bytes is None
    assert window.destroyed is True
    assert controller._allow_close is True


def test_save_and_close_cancelled_dialog_stays_open(monkeypatch):
    _fake_webview(monkeypatch)
    window = _FakeWindow(dialog_path=None)  # user backed out of Save
    controller = _controller_with(window)

    controller.save_and_close()

    assert window.dialog_calls, "the Save dialog should have been offered"
    assert window.destroyed is False
    assert controller._allow_close is False


def test_save_project_writes_file_but_keeps_window_open(tmp_path, monkeypatch):
    # The in-app save gate (New session / Open project): save WITHOUT closing.
    _fake_webview(monkeypatch)
    target = tmp_path / "buildaspec-draft.baspec"
    window = _FakeWindow(dialog_path=str(target))
    controller = _controller_with(window)

    assert controller.save_project() is True

    assert target.exists()
    parsed = parse_project_package(target.read_bytes())
    assert parsed.project["kind"] == "buildaspec-project"
    # Unlike save_and_close, the window is never destroyed.
    assert window.destroyed is False
    assert controller._allow_close is False


def test_save_project_cancelled_dialog_returns_false(monkeypatch):
    # A cancelled Save-As returns False so the frontend keeps the session
    # (a mis-click behind "Save" must never discard the work).
    _fake_webview(monkeypatch)
    window = _FakeWindow(dialog_path=None)
    controller = _controller_with(window)

    assert controller.save_project() is False
    assert window.dialog_calls, "the Save dialog should have been offered"
    assert window.destroyed is False


# --- native Open (open_file): HTML file inputs are unreliable in the webview --


def test_open_file_returns_name_and_bytes(tmp_path, monkeypatch):
    # The Open/Import buttons in the native shell read the picked file here and
    # hand its exact bytes to JS (base64) for the ordinary upload path.
    import base64

    _fake_webview(monkeypatch)
    project = tmp_path / "buildaspec-prev.baspec"
    payload = b"PK\x03\x04 pretend .baspec bytes \x00\x01\x02"
    project.write_bytes(payload)
    window = _FakeWindow(dialog_path=str(project))
    controller = _controller_with(window)

    result = controller.open_file("project")

    assert result is not None
    assert result["name"] == "buildaspec-prev.baspec"
    assert base64.b64decode(result["data_b64"]) == payload
    # The Open dialog (not the Save dialog) was used.
    (args, kwargs), = window.dialog_calls
    assert args[0] == sys.modules["webview"].FileDialog.OPEN
    assert kwargs.get("allow_multiple") is False


def test_open_file_project_vs_docx_filter(tmp_path, monkeypatch):
    _fake_webview(monkeypatch)
    target = tmp_path / "x.docx"
    target.write_bytes(b"docx")
    window = _FakeWindow(dialog_path=str(target))
    controller = _controller_with(window)

    controller.open_file("docx")
    (_, kwargs), = window.dialog_calls
    joined = " ".join(kwargs.get("file_types", ()))
    assert ".docx" in joined and ".baspec" not in joined


def test_open_file_cancelled_returns_none(monkeypatch):
    _fake_webview(monkeypatch)
    window = _FakeWindow(dialog_path=None)  # user backed out of the Open dialog
    controller = _controller_with(window)

    assert controller.open_file("project") is None


def test_open_file_tuple_result_is_supported(tmp_path, monkeypatch):
    # Some pywebview backends return a 1-tuple of paths rather than a string.
    _fake_webview(monkeypatch)
    project = tmp_path / "p.baspec"
    project.write_bytes(b"data")
    window = _FakeWindow(dialog_path=(str(project),))
    controller = _controller_with(window)

    result = controller.open_file("project")
    assert result is not None and result["name"] == "p.baspec"


def test_open_file_unreadable_path_returns_none(tmp_path, monkeypatch):
    _fake_webview(monkeypatch)
    missing = tmp_path / "does-not-exist.baspec"
    window = _FakeWindow(dialog_path=str(missing))
    controller = _controller_with(window)

    assert controller.open_file("project") is None


# pywebview validates every create_file_dialog `file_types` entry through
# `webview.util.parse_file_type` BEFORE opening the dialog, and its description
# grammar accepts only word characters and spaces — a hyphen raises ValueError,
# which the controller turns into "cancelled", silently killing Open/Save/
# Import. This regex is copied verbatim from pywebview (stable across >=5.3) so
# the app's filters are pinned parser-valid without importing the GUI package.
_PYWEBVIEW_FILE_FILTER = r"^([\w ]+)\((\*(?:\.(?:\w+|\*))*(?:;\*(?:\.(?:\w+|\*))*)*)\)$"


def test_native_file_filters_are_pywebview_valid():
    for group in (
        main._PROJECT_OPEN_FILE_TYPES,
        main._PROJECT_SAVE_FILE_TYPES,
        main._DOCX_OPEN_FILE_TYPES,
    ):
        for entry in group:
            assert re.match(_PYWEBVIEW_FILE_FILTER, entry), (
                f"{entry!r} is not a valid pywebview file filter "
                "(hyphens in the description make create_file_dialog raise)"
            )
