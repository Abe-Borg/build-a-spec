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

    def __init__(
        self,
        evaluate_return=True,
        dialog_path=None,
        current_url: str = "http://127.0.0.1:8787/",
    ) -> None:
        self.events = _FakeEvents()
        self.destroyed = False
        self.evaluated: list[str] = []
        self.dialog_calls: list = []
        self._evaluate_return = evaluate_return
        self._dialog_path = dialog_path
        self.current_url = current_url

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

    def get_current_url(self) -> str:
        return self.current_url


def _controller_with(window: _FakeWindow) -> main._CloseController:
    controller = main._CloseController()
    controller._bind(window)
    # _bind subscribes the closing handler.
    assert controller._on_closing in window.events.closing.handlers
    return controller


def test_guarded_bridge_accepts_only_the_exact_live_app_origin():
    window = _FakeWindow(current_url="http://127.0.0.1:8787/app?step=2#panel")
    controller = main._CloseController(("http://127.0.0.1:8787/",))
    controller._bind(window)

    assert controller._trusted_page() is True
    for untrusted_url in (
        "http://127.0.0.1:8788/",
        "http://127.0.0.1.evil.example:8787/",
        "http://user@127.0.0.1:8787/",
        "https://127.0.0.1:8787/",
        "file:///tmp/copied-app.html",
        "about:blank",
    ):
        window.current_url = untrusted_url
        assert controller._trusted_page() is False, untrusted_url


def test_untrusted_page_cannot_use_any_public_native_bridge(monkeypatch):
    window = _FakeWindow(current_url="https://untrusted.example/")
    controller = main._CloseController(("http://127.0.0.1:8787/",))
    controller._bind(window)
    browser_calls: list[str] = []
    monkeypatch.setattr(
        "webbrowser.open", lambda url: browser_calls.append(url) or True
    )

    controller.save_and_close()
    controller.discard_and_close()
    assert controller.save_project()["ok"] is False
    assert controller.save_project_as()["ok"] is False
    assert controller.save_template("personal:" + "a" * 32) is False
    assert controller.open_file("project") is None
    assert controller.open_external_link("https://example.com/") is False

    assert window.destroyed is False
    assert window.dialog_calls == []
    assert browser_calls == []


def test_untrusted_page_native_window_close_skips_the_app_prompt():
    sessions.get_session().history.append(
        {"role": "user", "content": [{"type": "text", "text": "unsaved"}]}
    )
    window = _FakeWindow(current_url="https://untrusted.example/")
    controller = main._CloseController(("http://127.0.0.1:8787/",))
    controller._bind(window)

    assert controller._on_closing() is None
    assert controller._prompting is False
    assert window.evaluated == []


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


def test_native_close_restores_original_before_running_unsaved_prompt():
    original = sessions.get_session()
    original.history.append(
        {"role": "user", "content": [{"type": "text", "text": "real project"}]}
    )
    tutorial = sessions.workspace_manager().begin_tutorial(
        request_id="native-close-contract"
    )
    tutorial.session.history.append(
        {"role": "assistant", "content": [{"type": "text", "text": "tutorial only"}]}
    )

    window = _FakeWindow(evaluate_return=True)
    controller = _controller_with(window)
    assert controller._on_closing() is False
    assert sessions.get_workspace().scope == "original"
    assert sessions.get_session() is original
    assert all("tutorial only" not in str(item) for item in original.history)
    for _ in range(200):
        if window.evaluated:
            break
        time.sleep(0.01)
    assert "tutorial-restored" in window.evaluated[0]


def test_native_close_discards_tutorial_when_retained_original_is_blank():
    original = sessions.get_session()
    tutorial = sessions.workspace_manager().begin_tutorial(
        request_id="native-close-blank-contract"
    )
    tutorial.session.history.append(
        {"role": "assistant", "content": [{"type": "text", "text": "tutorial only"}]}
    )

    window = _FakeWindow()
    controller = _controller_with(window)
    assert controller._on_closing() is None
    assert sessions.get_session() is original
    assert sessions.has_unsaved_progress(original) is False
    assert window.evaluated == []


def test_native_close_never_orphans_running_tutorial_work():
    original = sessions.get_session()
    original.history.append(
        {"role": "user", "content": [{"type": "text", "text": "real project"}]}
    )
    tutorial = sessions.workspace_manager().begin_tutorial(
        request_id="native-close-busy-contract"
    )
    tutorial.session.research.status = "running"

    window = _FakeWindow(evaluate_return=True)
    controller = _controller_with(window)
    assert controller._on_closing() is False
    for _ in range(200):
        if window.evaluated:
            break
        time.sleep(0.01)
    assert sessions.get_workspace().scope == "tutorial"
    assert sessions.get_session() is tutorial.session
    assert window.destroyed is False
    assert "tutorial-busy" in window.evaluated[0]


def test_native_close_vetoes_a_scenario_build_instead_of_waiting_on_it():
    """Native close runs on the UI thread, so it must veto, never block.

    A scenario build can be an unbounded model call (Chapter 6 generates
    its figures live), and it merges its already-billed usage onto the
    tutorial session when it returns. Restoring through it would strand
    that spend; waiting for it would freeze the window. The close asks,
    is told the workspace is transitioning, and hands the user the
    busy prompt.
    """
    import threading

    from backend.llm.conversation import SessionState

    manager = sessions.workspace_manager()
    tutorial = manager.begin_tutorial(request_id="native-close-transition")
    entered, release = threading.Event(), threading.Event()

    def _build(_base):
        entered.set()
        assert release.wait(timeout=5)
        return SessionState()

    result: list = []
    builder = threading.Thread(
        target=lambda: result.append(
            manager.push_scenario(
                tutorial.workspace_id, kind="references", build=_build
            )
        )
    )
    builder.start()
    try:
        assert entered.wait(timeout=5)
        window = _FakeWindow(evaluate_return=True)
        controller = _controller_with(window)
        assert controller._on_closing() is False
        for _ in range(200):
            if window.evaluated:
                break
            time.sleep(0.01)
        assert sessions.get_workspace().scope == "tutorial"
        assert window.destroyed is False
        assert "tutorial-busy" in window.evaluated[0]
    finally:
        release.set()
        builder.join(timeout=5)
    # The build it refused to discard still landed.
    assert result and result[0].scope == "scenario"


def test_unhandled_busy_tutorial_close_stays_vetoed():
    tutorial = sessions.workspace_manager().begin_tutorial(
        request_id="native-close-unhandled-busy"
    )
    tutorial.session.qc.status = "running"
    window = _FakeWindow(evaluate_return=False)
    controller = _controller_with(window)

    assert controller._on_closing() is False
    for _ in range(200):
        if window.evaluated:
            break
        time.sleep(0.01)
    assert window.destroyed is False
    assert sessions.get_workspace().scope == "tutorial"


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

    result = controller.save_project()

    assert result["ok"] is True
    assert result["name"] == "buildaspec-draft.baspec"

    assert target.exists()
    parsed = parse_project_package(target.read_bytes())
    assert parsed.project["kind"] == "buildaspec-project"
    # Unlike save_and_close, the window is never destroyed.
    assert window.destroyed is False
    assert controller._allow_close is False


def test_save_project_cancelled_dialog_returns_false(monkeypatch):
    # A cancelled Save-As reports not-ok so the frontend keeps the session
    # (a mis-click behind "Save" must never discard the work) — and reports
    # it as a CANCELLATION, which the panel stays quiet about, rather than as
    # a failure it would put a red line under.
    _fake_webview(monkeypatch)
    window = _FakeWindow(dialog_path=None)
    controller = _controller_with(window)

    result = controller.save_project()

    assert result["ok"] is False
    assert result["cancelled"] is True
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


def test_open_file_template_uses_scoped_portable_template_filter(tmp_path, monkeypatch):
    _fake_webview(monkeypatch)
    target = tmp_path / "starter.bastemplate"
    target.write_bytes(b"{}")
    window = _FakeWindow(dialog_path=str(target))
    controller = _controller_with(window)

    result = controller.open_file("template")
    assert result is not None and result["name"] == "starter.bastemplate"
    (_, kwargs), = window.dialog_calls
    joined = " ".join(kwargs.get("file_types", ()))
    assert ".bastemplate" in joined
    assert ".baspec" not in joined


def test_save_template_writes_only_catalog_export(tmp_path, monkeypatch):
    _fake_webview(monkeypatch)
    target = tmp_path / "starter.bastemplate"
    window = _FakeWindow(dialog_path=str(target))
    controller = _controller_with(window)

    class _Catalog:
        def export(self, template_id):
            assert template_id == "personal:" + "a" * 32
            return b'{"kind":"buildaspec-spec-template"}', "starter.bastemplate"

    monkeypatch.setattr("backend.templates.get_template_catalog", lambda: _Catalog())
    assert controller.save_template("personal:" + "a" * 32) is True
    assert target.read_bytes() == b'{"kind":"buildaspec-spec-template"}'
    assert window.destroyed is False


def test_open_file_reference_filter_offers_every_supported_type(
    tmp_path, monkeypatch
):
    """The packaged app's picker is the real one — an HTML ``accept`` list
    only covers the browser fallback. A Word-only filter here would hide every
    PDF/text/XML/CSV attachment behind the generic "All files" entry."""
    from backend.reference_extract import REFERENCE_KINDS

    _fake_webview(monkeypatch)
    target = tmp_path / "standard.pdf"
    target.write_bytes(b"pdf")
    window = _FakeWindow(dialog_path=str(target))
    controller = _controller_with(window)

    controller.open_file("reference")
    (_, kwargs), = window.dialog_calls
    joined = " ".join(kwargs.get("file_types", ()))

    for extension in REFERENCE_KINDS:
        assert extension in joined, extension
    assert ".baspec" not in joined


def test_open_file_unknown_kind_degrades_to_the_project_filter(
    tmp_path, monkeypatch
):
    _fake_webview(monkeypatch)
    target = tmp_path / "p.baspec"
    target.write_bytes(b"data")
    window = _FakeWindow(dialog_path=str(target))
    controller = _controller_with(window)

    controller.open_file("something-new")
    (_, kwargs), = window.dialog_calls

    assert kwargs.get("file_types") == main._PROJECT_OPEN_FILE_TYPES


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


def test_open_external_link_launches_system_browser(monkeypatch):
    # A clicked citation/reference link must open in the OS browser, never
    # navigate the app window itself away from the app.
    controller = _controller_with(_FakeWindow())
    calls: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: calls.append(url) or True)

    assert controller.open_external_link("https://example.com/spec?x=1") is True
    assert calls == ["https://example.com/spec?x=1"]


def test_open_external_link_rejects_non_http_schemes(monkeypatch):
    controller = _controller_with(_FakeWindow())
    calls: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: calls.append(url) or True)

    assert controller.open_external_link("javascript:alert(1)") is False
    assert controller.open_external_link("file:///etc/passwd") is False
    assert controller.open_external_link("not a url") is False
    assert controller.open_external_link("") is False
    assert calls == []


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
        main._REFERENCE_OPEN_FILE_TYPES,
        main._TEMPLATE_OPEN_FILE_TYPES,
        main._TEMPLATE_SAVE_FILE_TYPES,
    ):
        for entry in group:
            assert re.match(_PYWEBVIEW_FILE_FILTER, entry), (
                f"{entry!r} is not a valid pywebview file filter "
                "(hyphens in the description make create_file_dialog raise)"
            )
