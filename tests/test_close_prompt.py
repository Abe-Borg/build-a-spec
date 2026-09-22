"""Save-progress-on-close: the testable seams of the native close flow.

The pywebview integration itself (the real `closing` event, `evaluate_js`,
native dialogs) needs a GUI and is out of reach of the hermetic suite. What
*is* testable — and what actually carries the behavior — is exercised here:
the unsaved-progress predicate, the shared save-payload helpers, and the
`_CloseController` decision logic driven by a fake window.
"""
from __future__ import annotations

import enum
import io
import re
import sys
import time
import types

import pytest

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
        # pywebview's yes/no dialog (Project workspace Phase 3: an export onto
        # a file that is not this project's brief asks before replacing it).
        self.confirm_answer = False
        self.confirm_calls: list[tuple[str, str]] = []

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

    def create_confirmation_dialog(self, title: str, message: str) -> bool:
        self.confirm_calls.append((title, message))
        return self.confirm_answer

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
    assert controller.save_project_brief()["ok"] is False
    assert controller.open_file("project") is None
    assert controller.bind_project_home("f" * 32, 0)["ok"] is False
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


def test_open_file_mints_a_token_only_for_a_project_open_and_bounds_the_map(
    tmp_path, monkeypatch
):
    """A token names a picked path the frontend never holds (Project workspace
    Phase 2). Only a project open can live in a project folder, so only it
    mints one; the map keeps the newest few and evicts the oldest first."""
    _fake_webview(monkeypatch)
    project = tmp_path / "p.baspec"
    project.write_bytes(b"PK\x03\x04 project")
    window = _FakeWindow(dialog_path=str(project))
    controller = _controller_with(window)

    tokens = [
        controller.open_file("project")["token"]
        for _ in range(main._RECENT_OPENS_LIMIT + 2)
    ]

    assert all(len(token) == 32 for token in tokens)
    assert len(set(tokens)) == len(tokens)
    assert len(controller._recent_opens) == main._RECENT_OPENS_LIMIT
    assert tokens[0] not in controller._recent_opens, "the oldest goes first"
    assert tokens[-1] in controller._recent_opens
    assert controller._recent_opens[tokens[-1]] == (str(project), "project")
    # A master import, an attachment or a brief pick names no section folder.
    for kind in ("docx", "reference", "project_brief", "template"):
        assert controller.open_file(kind)["token"] == "", kind
    # An evicted token is simply unknown.
    evicted = controller.bind_project_home(tokens[0], 0)
    assert evicted["ok"] is False and evicted["home"] is None


def test_bind_project_home_refuses_an_unknown_token_and_binds_only_the_loaded_session(
    tmp_path, monkeypatch
):
    """The token is single-use and the generation is the LOAD's: a session
    replaced since the load is never handed the folder (the
    remember_project_save_target posture)."""
    from backend.project_brief import brief_bytes, build_project_brief

    _fake_webview(monkeypatch)
    session = sessions.get_session()
    session.project_link = {
        "project_id": "b" * 32,
        "name": "P",
        "brief_updated_at": "",
        "seeded_from": [],
        "research_rounds_at_seed": 0,
        "sections": [],
    }
    (tmp_path / "p.basproject").write_bytes(
        brief_bytes(build_project_brief(session, ready=False))
    )
    section = tmp_path / "21 13 13.baspec"
    section.write_bytes(b"PK\x03\x04 section")
    controller = _controller_with(_FakeWindow(dialog_path=str(section)))

    unknown = controller.bind_project_home("f" * 32, session.generation)
    assert unknown["ok"] is False and unknown["error"]

    stale = controller.open_file("project")["token"]
    fresh = controller.open_file("project")["token"]
    loaded_generation = session.generation
    session.invalidate_model_turn()  # something replaced the session since
    refused = controller.bind_project_home(stale, loaded_generation)
    assert refused["ok"] is False and refused["home"] is None
    assert session.project_home is None

    bound = controller.bind_project_home(fresh, session.generation)
    assert bound == {
        "ok": True,
        "home": {"folder": str(tmp_path), "brief_name": "p.basproject"},
        "error": "",
    }
    assert session.project_home["brief_path"] == str(tmp_path / "p.basproject")
    # Single-use: a token cannot re-bind later, after the session moved on.
    assert controller.bind_project_home(fresh, session.generation)["ok"] is False


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
        main._PROJECT_BRIEF_OPEN_FILE_TYPES,
        main._PROJECT_BRIEF_SAVE_FILE_TYPES,
    ):
        for entry in group:
            assert re.match(_PYWEBVIEW_FILE_FILTER, entry), (
                f"{entry!r} is not a valid pywebview file filter "
                "(hyphens in the description make create_file_dialog raise)"
            )


# --- Open in Word -----------------------------------------------------------


def _fake_backend():
    return types.SimpleNamespace(host="127.0.0.1", port=1, api_token="token")


def test_open_in_word_exports_through_the_local_route_and_launches(monkeypatch, tmp_path):
    """The bridge fetches the export with the shell's own token, writes a
    fresh temp file, and hands it to the system's default .docx app."""
    fetched: list[str] = []
    launched: list[str] = []

    def fake_fetch(backend, path):
        assert backend.api_token == "token"
        fetched.append(path)
        return b"PK\x03\x04docx", "Section 21 05 00 - COMMON WORK.docx"

    monkeypatch.setattr(main, "_fetch_backend_bytes", fake_fetch)
    monkeypatch.setattr(main, "_launch_file", lambda p: launched.append(str(p)))
    monkeypatch.setattr(main.tempfile, "gettempdir", lambda: str(tmp_path))

    controller = main._CloseController(None, backend=_fake_backend())
    result = controller.open_in_word("preserved")

    assert result["ok"] is True, result
    assert fetched == ["/api/export/docx?mode=preserved"]
    assert launched == [result["path"]]
    written = tmp_path / "BuildASpec"
    files = list(written.glob("*.docx"))
    assert len(files) == 1
    assert files[0].read_bytes() == b"PK\x03\x04docx"
    assert files[0].name.startswith("Section 21 05 00 - COMMON WORK ")
    assert result["name"] == files[0].name


def test_open_in_word_never_reuses_a_file_word_may_hold_open(monkeypatch, tmp_path):
    """Two clicks in the same second are two files (Codex review, PR #145).

    A timestamp-derived name collided: Word holds the first file open, so the
    second write failed on Windows — or overwrote the file behind the first
    Word window elsewhere. The name is minted atomically by ``mkstemp``.
    """
    monkeypatch.setattr(
        main, "_fetch_backend_bytes", lambda b, p: (b"first", "Section 21 05 00.docx")
    )
    monkeypatch.setattr(main, "_launch_file", lambda p: None)
    monkeypatch.setattr(main.tempfile, "gettempdir", lambda: str(tmp_path))
    controller = main._CloseController(None, backend=_fake_backend())

    first = controller.open_in_word("preserved")
    monkeypatch.setattr(
        main, "_fetch_backend_bytes", lambda b, p: (b"second", "Section 21 05 00.docx")
    )
    second = controller.open_in_word("preserved")

    assert first["ok"] and second["ok"], (first, second)
    assert first["path"] != second["path"]
    assert open(first["path"], "rb").read() == b"first"
    assert open(second["path"], "rb").read() == b"second"
    assert first["name"].startswith("Section 21 05 00 ")
    assert first["name"].endswith(".docx")


def test_open_in_word_reports_the_servers_own_refusal(monkeypatch, tmp_path):
    def failing_fetch(backend, path):
        raise RuntimeError("Formatting-preserving export is unavailable: no map.")

    monkeypatch.setattr(main, "_fetch_backend_bytes", failing_fetch)
    monkeypatch.setattr(main, "_launch_file", lambda p: (_ for _ in ()).throw(AssertionError("must not launch")))
    monkeypatch.setattr(main.tempfile, "gettempdir", lambda: str(tmp_path))

    controller = main._CloseController(None, backend=_fake_backend())
    result = controller.open_in_word("preserved")
    assert result["ok"] is False
    assert "no map" in result["error"]
    assert not (tmp_path / "BuildASpec").exists() or not list(
        (tmp_path / "BuildASpec").glob("*.docx")
    )


def test_open_in_word_refuses_unknown_modes_and_a_browser_session():
    controller = main._CloseController(None, backend=_fake_backend())
    assert controller.open_in_word("evil")["ok"] is False
    no_backend = main._CloseController(None)
    result = no_backend.open_in_word("preserved")
    assert result["ok"] is False
    assert "desktop app" in result["error"]


def test_filename_from_disposition_prefers_the_encoded_form():
    assert (
        main._filename_from_disposition(
            "attachment; filename=\"fallback.docx\"; filename*=UTF-8''Section%2021%2005%2000.docx"
        )
        == "Section 21 05 00.docx"
    )
    assert main._filename_from_disposition('attachment; filename="plain.docx"') == "plain.docx"
    assert main._filename_from_disposition("") == main._OPEN_IN_WORD_FALLBACK_NAME
    assert main._filename_from_disposition('attachment; filename="../x/evil"') == "evil.docx"


# --- Project brief (native save) ----------------------------------------------


def test_save_project_brief_writes_the_fetched_bytes_through_a_native_dialog(
    monkeypatch, tmp_path
):
    """The bridge fetches the brief from its own backend with the shell's
    token and writes exactly those bytes where the Save dialog points."""
    _fake_webview(monkeypatch)
    fetched: list[tuple[str, dict]] = []

    def fake_fetch(backend, path, **kwargs):
        assert backend.api_token == "token"
        fetched.append((path, kwargs))
        return b'{"kind": "buildaspec-project-brief"}', "buildaspec-project-client-x.basproject"

    monkeypatch.setattr(main, "_fetch_backend_bytes", fake_fetch)
    target = tmp_path / "chosen.basproject"
    window = _FakeWindow(dialog_path=str(target))
    controller = main._CloseController(None, backend=_fake_backend())
    controller._bind(window)

    result = controller.save_project_brief()

    assert result["ok"] is True, result
    assert result["cancelled"] is False and result["error"] == ""
    assert result["target"] == str(target)
    assert target.read_bytes() == b'{"kind": "buildaspec-project-brief"}'
    assert fetched == [
        (
            "/api/project/brief",
            {"fallback_name": main._PROJECT_BRIEF_FALLBACK_NAME, "suffix": ".basproject"},
        )
    ]
    (_, kwargs), = window.dialog_calls
    assert kwargs["save_filename"] == "buildaspec-project-client-x.basproject"
    joined = " ".join(kwargs["file_types"])
    assert ".basproject" in joined and ".baspec" not in joined
    assert not list(tmp_path.glob(".buildaspec-brief-*"))  # no temp file left behind


def test_save_project_brief_cancelled_dialog_is_a_cancel_not_an_error(monkeypatch):
    _fake_webview(monkeypatch)
    monkeypatch.setattr(
        main, "_fetch_backend_bytes", lambda b, p, **kw: (b"{}", "x.basproject")
    )
    window = _FakeWindow(dialog_path=None)
    controller = main._CloseController(None, backend=_fake_backend())
    controller._bind(window)

    result = controller.save_project_brief()

    assert result == main._CloseController._save_result(False, cancelled=True)
    assert result["cancelled"] is True and result["error"] == ""


def test_save_project_brief_reports_the_servers_own_refusal(monkeypatch):
    """A 409 from the route (a streaming turn, a tour) reaches the user in
    the server's words, and no dialog opens for a file that will not come."""
    _fake_webview(monkeypatch)

    def failing_fetch(backend, path, **kwargs):
        raise RuntimeError("Wait for the current reply to finish before exporting a project brief.")

    monkeypatch.setattr(main, "_fetch_backend_bytes", failing_fetch)
    window = _FakeWindow(dialog_path="/never/asked.basproject")
    controller = main._CloseController(None, backend=_fake_backend())
    controller._bind(window)

    result = controller.save_project_brief()

    assert result["ok"] is False and result["cancelled"] is False
    assert "current reply" in result["error"]
    assert window.dialog_calls == []


def test_save_project_brief_refuses_a_browser_session_and_a_tour(monkeypatch):
    no_backend = main._CloseController(None)
    result = no_backend.save_project_brief()
    assert result["ok"] is False and "desktop app" in result["error"]

    from backend import sessions

    _fake_webview(monkeypatch)
    monkeypatch.setattr(
        main,
        "_fetch_backend_bytes",
        lambda b, p, **kw: (_ for _ in ()).throw(AssertionError("must not fetch")),
    )
    window = _FakeWindow(dialog_path="/never/asked.basproject")
    controller = main._CloseController(None, backend=_fake_backend())
    controller._bind(window)
    sessions.workspace_manager().begin_tutorial(request_id="brief-save-in-a-tour")
    try:
        result = controller.save_project_brief()
    finally:
        sessions.reset_session()
    assert result["ok"] is False and result["cancelled"] is False
    assert "Return to your project" in result["error"]
    assert window.dialog_calls == []


def test_export_onto_an_existing_brief_merges(monkeypatch, tmp_path):
    """Project workspace Phase 3: an export onto a brief that already exists
    MERGES instead of overwriting — that file may hold the other branch of a
    fork. A file that is another project's brief (or no brief at all) is
    replaced only after the user says so."""
    import json

    from backend.project_brief import parse_project_brief
    from tests.test_project_brief import _client, _rich_session

    _fake_webview(monkeypatch)
    client = _client()
    _rich_session(client)

    def fetch(backend, path, **kwargs):
        resp = client.get(path)
        assert resp.status_code == 200, resp.text
        return resp.content, "buildaspec-project.basproject"

    posted: list[str] = []
    from backend.app import _BRIEF_FILE_LOCK

    def post(backend, path, *, payload, filename="upload.bin"):
        assert backend.api_token == "token"
        # Read → merge → write runs under the save-time refresh's lock …
        assert _BRIEF_FILE_LOCK.locked(), "the export merge must hold the brief lock"
        posted.append(path)
        resp = client.post(path, files={"file": (filename, payload, "application/json")})
        return resp.status_code, resp.json()

    monkeypatch.setattr(main, "_fetch_backend_bytes", fetch)
    monkeypatch.setattr(main, "_post_backend_file", post)
    target = tmp_path / "project.basproject"
    window = _FakeWindow(dialog_path=str(target))
    # … while the question is asked with it released: a native modal must
    # never hold a lock another save waits on.
    locked_while_asking: list[bool] = []
    answer_confirm = window.create_confirmation_dialog

    def confirm(title, message):
        locked_while_asking.append(_BRIEF_FILE_LOCK.locked())
        return answer_confirm(title, message)

    window.create_confirmation_dialog = confirm
    controller = main._CloseController(None, backend=_fake_backend())
    controller._bind(window)

    # A fresh path: the plain export, no merge.
    first = controller.save_project_brief()
    assert first["ok"] is True and posted == []
    assert first["brief_refreshed"] is False

    # The other branch writes into the file …
    other_branch = json.loads(target.read_text(encoding="utf-8"))
    other_branch["facts"].append(
        {
            "pid": "pf-9",
            "statement": "Recorded by the other branch.",
            "scope": "project",
            "status": "confirmed",
            "source_kind": "user",
            "recorded_in": "21 30 00",
        }
    )
    target.write_text(json.dumps(other_branch), encoding="utf-8")

    # … and the next export onto it keeps that work.
    merged = controller.save_project_brief()
    assert merged["ok"] is True, merged
    assert posted == ["/api/project/brief/merge"]
    assert merged["brief_refreshed"] is True and merged["brief_written"] is True
    assert merged["pull_available"] is True
    statements = [f["statement"] for f in parse_project_brief(target.read_bytes()).facts]
    assert "Recorded by the other branch." in statements
    assert "Data halls are Ordinary Hazard Group 2." in statements
    assert window.confirm_calls == []

    # Another project's brief: asked, and "no" leaves the file untouched.
    stranger = dict(other_branch, project_id="f" * 32)
    target.write_text(json.dumps(stranger), encoding="utf-8")
    before = target.read_bytes()
    declined = controller.save_project_brief()
    assert declined["ok"] is False and declined["cancelled"] is True
    assert target.read_bytes() == before
    (title, message) = window.confirm_calls[-1]
    assert "DIFFERENT project" in message
    # "Yes" replaces it with this project's brief.
    window.confirm_answer = True
    replaced = controller.save_project_brief()
    assert replaced["ok"] is True
    assert parse_project_brief(target.read_bytes()).project_id != "f" * 32

    # Not a brief at all: asked the same way.
    target.write_bytes(b"not a brief")
    window.confirm_answer = False
    kept = controller.save_project_brief()
    assert kept["cancelled"] is True and target.read_bytes() == b"not a brief"
    assert "not a project brief" in window.confirm_calls[-1][1]
    assert locked_while_asking == [False, False, False]


def test_open_file_project_brief_filter_offers_a_brief_or_a_sibling_project(
    tmp_path, monkeypatch
):
    _fake_webview(monkeypatch)
    target = tmp_path / "project.basproject"
    target.write_bytes(b"{}")
    window = _FakeWindow(dialog_path=str(target))
    controller = _controller_with(window)

    result = controller.open_file("project_brief")

    assert result is not None and result["name"] == "project.basproject"
    (_, kwargs), = window.dialog_calls
    joined = " ".join(kwargs.get("file_types", ()))
    assert ".basproject" in joined and ".baspec" in joined
    assert ".bastemplate" not in joined


def test_the_disposition_helper_keeps_a_brief_a_brief_and_word_a_docx():
    """The keyword defaults are what keep ``open_in_word`` byte-identical:
    without them a ``.basproject`` would have been saved as ``….basproject.docx``."""
    assert (
        main._filename_from_disposition(
            'attachment; filename="buildaspec-project-x.basproject"',
            fallback=main._PROJECT_BRIEF_FALLBACK_NAME,
            suffix=".basproject",
        )
        == "buildaspec-project-x.basproject"
    )
    assert (
        main._filename_from_disposition(
            "", fallback=main._PROJECT_BRIEF_FALLBACK_NAME, suffix=".basproject"
        )
        == main._PROJECT_BRIEF_FALLBACK_NAME
    )
    # The Word default is untouched — and it is why the keyword exists.
    assert main._filename_from_disposition('attachment; filename="x.basproject"') == "x.basproject.docx"
    assert main._filename_from_disposition("") == main._OPEN_IN_WORD_FALLBACK_NAME


def test_post_backend_file_sends_a_multipart_body_the_merge_route_reads(monkeypatch):
    """The shell hand-builds its multipart upload (urllib has no encoder), so
    the body it would send is replayed through the real route: a brief goes
    in, a merged brief comes out — the route parsed the part."""
    import json
    import urllib.error

    from backend.app import _DESKTOP_TOKEN_HEADER
    from tests.test_project_brief import _client, _rich_session

    client = _client()
    _rich_session(client)
    exported = client.get("/api/project/brief").content
    captured: dict = {}

    class _Answer:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            request = captured["request"]
            resp = client.post(
                "/api/project/brief/merge",
                content=request.data,
                headers={"Content-Type": request.get_header("Content-type")},
            )
            captured["status"] = resp.status_code
            return resp.content

    def fake_urlopen(request, timeout=0):
        captured["request"] = request
        return _Answer()

    monkeypatch.setattr(main.urllib.request, "urlopen", fake_urlopen)
    status, answer = main._post_backend_file(
        _fake_backend(),
        "/api/project/brief/merge",
        payload=exported,
        filename="C:\\Users\\me\\Client X.basproject",
    )
    request = captured["request"]
    assert request.get_method() == "POST"
    assert request.full_url == "http://127.0.0.1:1/api/project/brief/merge"
    assert request.get_header(_DESKTOP_TOKEN_HEADER.capitalize()) == "token"
    assert b'filename="CUsersmeClientX.basproject"' in request.data, "no path or space in the header"
    assert captured["status"] == 200 and status == 200
    assert answer["ok"] is True and json.loads(answer["brief"])["kind"] == "buildaspec-project-brief"

    # An error response is an ANSWER (the caller decides what a 409 means) …
    def refusing(request, timeout=0):
        raise urllib.error.HTTPError(
            request.full_url, 409, "Conflict", {}, io.BytesIO(b'{"ok": false, "code": "different_project"}')
        )

    monkeypatch.setattr(main.urllib.request, "urlopen", refusing)
    assert main._post_backend_file(_fake_backend(), "/x", payload=b"") == (
        409,
        {"ok": False, "code": "different_project"},
    )

    # … while an unreachable server, or a non-JSON answer, is an error.
    def unreachable(request, timeout=0):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(main.urllib.request, "urlopen", unreachable)
    with pytest.raises(RuntimeError, match="could not be reached"):
        main._post_backend_file(_fake_backend(), "/x", payload=b"")
