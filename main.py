"""Build-a-Spec entry point.

Starts the FastAPI backend on 127.0.0.1 and opens the UI in a native
pywebview window (Edge WebView2 on Windows). Dev mode
(``BUILD_A_SPEC_DEV=1``) points the window at the Vite dev server for hot
reload; otherwise the packaged/built frontend in ``frontend/dist`` is
served by the backend itself.

If pywebview is unavailable (or has no usable GUI backend), falls back to
opening the default browser against the same local server.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
import socket
import sys
import tempfile
from pathlib import Path
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

import uvicorn

from backend import diagnostics, settings


# Native file-dialog filters. pywebview's ``parse_file_type`` validates every
# filter BEFORE the dialog opens and its description grammar accepts only word
# characters and spaces — a hyphen (e.g. in "Build-a-Spec") makes
# ``create_file_dialog`` raise, which the callers turn into "cancelled", so a
# bad filter silently kills Open/Import/Save. Keep these descriptions
# hyphen-free (pinned by ``tests/test_close_prompt.py``). Open accepts legacy
# ``.json`` projects too; Save writes only the current ``.baspec`` format.
_PROJECT_OPEN_FILE_TYPES = (
    "Build a Spec project (*.baspec;*.json)",
    "All files (*.*)",
)
_PROJECT_SAVE_FILE_TYPES = (
    "Build a Spec project (*.baspec)",
    "All files (*.*)",
)
_DOCX_OPEN_FILE_TYPES = ("Word document (*.docx)", "All files (*.*)")
# A reference document is never the spec, so it is not Word-only (see
# ``backend/reference_extract.REFERENCE_KINDS``). The native dialog needs its
# own filter or the packaged app hides every non-Word attachment behind the
# generic "All files" entry — the HTML ``accept`` list only covers dev/browser.
_REFERENCE_OPEN_FILE_TYPES = (
    "Reference document (*.docx;*.pdf;*.txt;*.xml;*.csv)",
    "All files (*.*)",
)
_TEMPLATE_OPEN_FILE_TYPES = (
    "Build a Spec template (*.bastemplate)",
    "All files (*.*)",
)
_TEMPLATE_SAVE_FILE_TYPES = _TEMPLATE_OPEN_FILE_TYPES
_OPEN_FILE_TYPES_BY_KIND = {
    "docx": _DOCX_OPEN_FILE_TYPES,
    "reference": _REFERENCE_OPEN_FILE_TYPES,
    "project": _PROJECT_OPEN_FILE_TYPES,
    "template": _TEMPLATE_OPEN_FILE_TYPES,
}


def _ensure_std_streams() -> None:
    """Guarantee ``sys.stdout``/``sys.stderr`` are real streams.

    A windowed PyInstaller build (``console=False``) runs with no console,
    so both are ``None``. Uvicorn's log formatter calls
    ``sys.stdout.isatty()`` while configuring logging and crashes on
    ``None`` (``AttributeError: 'NoneType' object has no attribute
    'isatty'``); other libraries assume the streams exist too. Point any
    missing stream at ``os.devnull`` — a real stream whose ``isatty()``
    returns ``False`` and whose writes are discarded. Idempotent.
    """
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            try:
                stream = open(os.devnull, "w", encoding="utf-8")
            except OSError:
                stream = io.StringIO()
            setattr(sys, name, stream)
            if getattr(sys, f"__{name}__", None) is None:
                setattr(sys, f"__{name}__", stream)


@dataclass(repr=False)
class _BackendRuntime:
    server: uvicorn.Server
    thread: threading.Thread
    listener: socket.socket
    host: str
    port: int
    boot_nonce: str
    # The per-launch API token. The shell is the one process besides the
    # window that may call this backend, and "Open in Word" needs the export
    # route — the same route, guards and all, the window uses.
    api_token: str = ""


def _desired_backend_port() -> int:
    """Vite's proxy needs the configured port; packaged/browser uses ephemeral."""
    return settings.PORT if settings.dev_mode() else 0


def _reserve_backend_socket(port: int) -> socket.socket:
    """Exclusively reserve the loopback listener before any server probing."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_EXCLUSIVEADDRUSE,
                1,
            )
        listener.bind((settings.HOST, int(port)))
        return listener
    except Exception:
        listener.close()
        raise


def _desktop_origins_and_hosts(port: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
    origins = {
        f"http://{settings.HOST}:{port}",
        f"http://localhost:{port}",
    }
    hosts = {f"{settings.HOST}:{port}", f"localhost:{port}"}
    if settings.dev_mode():
        parsed = urllib.parse.urlsplit(settings.DEV_FRONTEND_URL)
        if parsed.scheme and parsed.netloc:
            origins.add(f"{parsed.scheme}://{parsed.netloc}")
            hosts.add(parsed.netloc)
            frontend_port = parsed.port or (443 if parsed.scheme == "https" else 80)
            if parsed.hostname == "localhost":
                origins.add(f"{parsed.scheme}://127.0.0.1:{frontend_port}")
                hosts.add(f"127.0.0.1:{frontend_port}")
            elif parsed.hostname == "127.0.0.1":
                origins.add(f"{parsed.scheme}://localhost:{frontend_port}")
                hosts.add(f"localhost:{frontend_port}")
    return (
        tuple(sorted(value.lower() for value in origins)),
        tuple(sorted(value.lower() for value in hosts)),
    )


def _start_backend() -> _BackendRuntime:
    # Must run before uvicorn.Config configures logging (see the docstring).
    _ensure_std_streams()
    listener = _reserve_backend_socket(_desired_backend_port())
    host, port = listener.getsockname()[:2]
    boot_nonce = secrets.token_urlsafe(32)
    api_token = secrets.token_urlsafe(48)
    diagnostics.set_runtime_server_identity(
        host=str(host), port=int(port), boot_nonce=boot_nonce
    )
    origins, hosts = _desktop_origins_and_hosts(int(port))
    try:
        from backend.app import DesktopSecurityConfig, create_app

        secured_app = create_app(
            desktop_security=DesktopSecurityConfig(
                boot_nonce=boot_nonce,
                api_token=api_token,
                bound_host=str(host),
                bound_port=int(port),
                allowed_hosts=hosts,
                allowed_origins=origins,
            )
        )
    except Exception:
        listener.close()
        raise
    # log_config=None: uvicorn installs no handlers of its own, so its
    # loggers propagate to the root handler diagnostics.init_logging()
    # attached — the only place output survives in the packaged windowed
    # build (stdout/stderr are devnull there). access_log=False because
    # the app's request middleware is the access log.
    config = uvicorn.Config(
        secured_app,
        host=str(host),
        port=int(port),
        log_level="info",
        log_config=None,
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        name="build-a-spec-backend",
        daemon=True,
    )
    runtime = _BackendRuntime(
        server=server,
        thread=thread,
        listener=listener,
        host=str(host),
        port=int(port),
        boot_nonce=boot_nonce,
        api_token=api_token,
    )
    diagnostics.log_startup_banner()
    thread.start()
    return runtime


def _health_identity_matches(runtime: _BackendRuntime, payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    fingerprint = payload.get("boot_nonce_fingerprint")
    expected_fingerprint = hashlib.sha256(
        runtime.boot_nonce.encode("utf-8")
    ).hexdigest()
    try:
        port = int(payload.get("bound_port"))
    except (TypeError, ValueError):
        return False
    return (
        payload.get("status") == "ok"
        and payload.get("app") == settings.APP_NAME
        and payload.get("version") == settings.VERSION
        and isinstance(fingerprint, str)
        and secrets.compare_digest(fingerprint, expected_fingerprint)
        and port == runtime.port
    )


def _wait_for_health(runtime: _BackendRuntime, timeout_s: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout_s
    url = f"http://{runtime.host}:{runtime.port}/api/health"
    while time.monotonic() < deadline:
        if not runtime.thread.is_alive():
            return False
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                payload = json.loads(resp.read(16_384).decode("utf-8"))
                if resp.status == 200 and _health_identity_matches(runtime, payload):
                    return bool(runtime.thread.is_alive() and runtime.server.started)
                if resp.status == 200:
                    return False
        except Exception:
            time.sleep(0.2)
    return False


def _url_with_boot_fragment(base_url: str, boot_nonce: str) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    fragment = urllib.parse.urlencode({"buildaspec_boot": boot_nonce})
    return urllib.parse.urlunsplit(parsed._replace(fragment=fragment))


def _http_origin(url: str) -> tuple[str, str, int] | None:
    """Return a browser-style HTTP origin tuple, or ``None`` if invalid."""
    if not isinstance(url, str):
        return None
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname
    if (
        scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, hostname.lower(), port


_EXPORT_MODES = frozenset({"preserved", "normalized", "source"})
_OPEN_IN_WORD_FALLBACK_NAME = "Build-a-Spec export.docx"


def _filename_from_disposition(value: str) -> str:
    """The attachment filename an export response names, as a safe basename."""
    import urllib.parse

    name = ""
    for part in (value or "").split(";"):
        part = part.strip()
        if part.lower().startswith("filename*="):
            encoded = part.split("=", 1)[1].strip()
            if "''" in encoded:
                encoded = encoded.split("''", 1)[1]
            name = urllib.parse.unquote(encoded)
            break
    if not name:
        for part in (value or "").split(";"):
            part = part.strip()
            if part.lower().startswith("filename="):
                name = part.split("=", 1)[1].strip().strip('"')
                break
    name = os.path.basename(name.replace("\\", "/")).strip()
    name = "".join(ch for ch in name if ch not in '<>:"/\\|?*' and ord(ch) >= 32)
    if not name or name in {".", ".."}:
        return _OPEN_IN_WORD_FALLBACK_NAME
    if not name.lower().endswith(".docx"):
        name += ".docx"
    return name


def _fetch_backend_bytes(backend: _BackendRuntime, path: str) -> tuple[bytes, str]:
    """GET one API path from THIS launch's backend with its own token.

    Returns ``(payload, filename)``. Raises ``RuntimeError`` with the server's
    own message on an error response, so a 409 ("this project has no
    formatting map") reaches the user in the server's words.
    """
    import json
    import urllib.error
    import urllib.request

    from backend.app import _DESKTOP_TOKEN_HEADER

    url = f"http://{backend.host}:{backend.port}{path}"
    request = urllib.request.Request(
        url,
        headers={_DESKTOP_TOKEN_HEADER: backend.api_token, "Accept": "*/*"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = response.read()
            disposition = response.headers.get("Content-Disposition", "")
    except urllib.error.HTTPError as exc:
        message = f"The export failed ({exc.code})."
        try:
            body = json.loads(exc.read().decode("utf-8", errors="replace"))
            if isinstance(body, dict) and body.get("error"):
                message = str(body["error"])
        except Exception:  # noqa: BLE001 - the status line is the fallback
            pass
        raise RuntimeError(message) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError("The local server could not be reached.") from exc
    return payload, _filename_from_disposition(disposition)


def _launch_file(path: Path) -> None:
    """Open ``path`` with the system's default application (Word for .docx)."""
    opener = getattr(os, "startfile", None)
    if opener is not None:  # Windows, the primary platform
        opener(os.fspath(path))
        return
    import shutil
    import subprocess

    for command in ("xdg-open", "open"):
        executable = shutil.which(command)
        if executable:
            subprocess.Popen([executable, os.fspath(path)])
            return
    raise RuntimeError("No application is registered to open Word files.")


def _write_open_in_word_file(name: str, payload: bytes) -> Path:
    """Write ``payload`` to a NEW file under the user's temp folder.

    Created atomically with a unique name (``mkstemp``), never derived from a
    timestamp: two clicks within a second would otherwise collide, and Word
    holds the first file open — so the second write would fail on Windows,
    or overwrite the file behind the first Word window where it does not.
    The export's own filename survives as the prefix, so the Word title bar
    still reads like the section.
    """
    folder = Path(tempfile.gettempdir()) / "BuildASpec"
    folder.mkdir(parents=True, exist_ok=True)
    stem, suffix = os.path.splitext(name)
    handle, raw_path = tempfile.mkstemp(
        prefix=f"{stem} ", suffix=suffix or ".docx", dir=os.fspath(folder)
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
    except OSError:
        try:
            os.unlink(raw_path)
        except OSError:
            pass
        raise
    return Path(raw_path)


class _CloseController:
    """Offer to save progress when the user closes the native window.

    Wired to pywebview's ``closing`` event. When the session holds unsaved
    work, the native close is vetoed and the frontend is asked to show its
    save-before-leaving dialog; the dialog's choice comes back through the two
    ``js_api`` methods (``save_and_close`` / ``discard_and_close``). A blank,
    untouched session closes with no prompt, and if the frontend can't be
    reached the window is never trapped — it just closes.

    On Windows/WebView2 (the primary target) the ``closing`` handler runs
    synchronously on the UI thread, so it must not call ``evaluate_js``
    itself — that would block the message loop the WebView2 needs to run the
    script, deadlocking the close. The frontend prompt is therefore dispatched
    from a worker thread; by the time it runs, the vetoed close has returned
    and the UI thread is free.

    The bridge also exposes ``save_project`` (the in-app save gate),
    ``open_file`` (native Open/Import, since HTML file inputs are unreliable
    in the webview), and ``open_external_link`` (routes outbound hyperlink
    clicks to the system browser instead of the app window). Only these
    ``js_api`` methods are public; everything else is underscore-prefixed so
    pywebview does not expose it to JavaScript.
    """

    # The frontend hook the prompt calls; returns whether it was handled so a
    # broken/old page never leaves an unclosable window.
    _REQUEST_CLOSE_JS = (
        "(function(){try{"
        "if(typeof window.buildaspecRequestClose==='function'){"
        "window.buildaspecRequestClose();return true;}"
        "}catch(e){}return false;})()"
    )
    _REQUEST_BUSY_TUTORIAL_CLOSE_JS = (
        "(function(){try{"
        "if(typeof window.buildaspecRequestClose==='function'){"
        "window.buildaspecRequestClose('tutorial-busy');return true;}"
        "}catch(e){}return false;})()"
    )
    _REQUEST_RESTORED_TUTORIAL_CLOSE_JS = (
        "(function(){try{"
        "if(typeof window.buildaspecRequestClose==='function'){"
        "window.buildaspecRequestClose('tutorial-restored');return true;}"
        "}catch(e){}return false;})()"
    )

    def __init__(
        self,
        allowed_origins: tuple[str, ...] | None = None,
        backend: _BackendRuntime | None = None,
    ) -> None:
        # ``None`` retains the unguarded seam used by the existing unit fakes.
        # The real desktop launcher always supplies an explicit allowlist.
        # ``backend`` is this launch's own server, for the bridge methods
        # that call it (Open in Word); absent, those report themselves
        # unavailable rather than guessing a port.
        self._backend = backend
        self._allowed_origins = (
            None
            if allowed_origins is None
            else frozenset(
                origin
                for url in allowed_origins
                if (origin := _http_origin(url)) is not None
            )
        )
        self._window = None
        self._allow_close = False
        self._prompting = False
        self._close_reason = "unsaved"

    def _bind(self, window) -> None:
        self._window = window
        window.events.closing += self._on_closing

    def _trusted_page(self) -> bool:
        """Fail closed if the live webview is no longer on an app origin."""
        if self._allowed_origins is None:
            return True
        if self._window is None:
            return False
        try:
            current_url = self._window.get_current_url()
        except Exception:
            return False
        return _http_origin(current_url) in self._allowed_origins

    # --- pywebview 'closing' handler (synchronous, on the UI thread) --------
    def _on_closing(self):
        if self._window is not None and not self._trusted_page():
            # pywebview keeps js_api injected after navigation. An untrusted
            # page may close the containing window, but must never run the
            # app's session inspection or save-before-leaving bridge flow.
            return None
        # Returning False cancels the close (winforms sets args.Cancel); any
        # other value lets it proceed.
        if self._allow_close or self._window is None:
            return None  # a confirmed close (or no window) — let it proceed
        try:
            from backend import sessions

            # A tutorial is a disposable workspace. Native close always
            # returns to the exact retained original before deciding whether
            # the user's real project needs a save prompt.
            manager = sessions.workspace_manager()
            restored_tutorial = manager.current().scope != "original"
            try:
                session = manager.restore_original_for_native_close().session
            except sessions.WorkspaceBusyError:
                self._close_reason = "tutorial-busy"
                if not self._prompting:
                    self._prompting = True
                    threading.Thread(target=self._ask_frontend, daemon=True).start()
                return False
            if not sessions.has_unsaved_progress(session):
                return None  # nothing to lose — close without nagging
            self._close_reason = (
                "tutorial-restored" if restored_tutorial else "unsaved"
            )
        except Exception:
            return None  # never trap the user on a bookkeeping error
        if not self._prompting:
            self._prompting = True
            threading.Thread(target=self._ask_frontend, daemon=True).start()
        return False  # veto for now; a js_api call finishes the close

    def _ask_frontend(self) -> None:
        handled = False
        reason = self._close_reason
        if not self._trusted_page():
            # The page can navigate after the synchronous closing callback but
            # before this worker runs. The user already requested a close, so
            # complete it without evaluating app JavaScript on the new page.
            self._prompting = False
            self._close_reason = "unsaved"
            self._force_close()
            return
        try:
            script = (
                self._REQUEST_BUSY_TUTORIAL_CLOSE_JS
                if reason == "tutorial-busy"
                else self._REQUEST_RESTORED_TUTORIAL_CLOSE_JS
                if reason == "tutorial-restored"
                else self._REQUEST_CLOSE_JS
            )
            handled = bool(self._window.evaluate_js(script))
        except Exception:
            handled = False
        finally:
            self._prompting = False
            self._close_reason = "unsaved"
        if not handled and reason != "tutorial-busy":
            # No frontend handler (broken/old page) — don't trap the user.
            self._force_close()

    # --- exposed to the window's JS as window.pywebview.api.* --------------
    def save_and_close(self) -> None:
        """Frontend chose 'Save & close': write a project file, then close.

        A session that has already saved once overwrites that file without
        asking, exactly like the in-app Save button — the dialog only appears
        when there is nowhere established to write.
        """
        if not self._trusted_page():
            return
        if self._save_project_file()["ok"]:
            self._force_close()
        # If the user backed out of the native Save dialog, stay in the app —
        # the close is still vetoed, so nothing else is needed.

    def discard_and_close(self) -> None:
        """Frontend chose 'Don't save': close without writing anything."""
        if not self._trusted_page():
            return
        self._force_close()

    def save_project(self) -> dict[str, Any]:
        """Save without closing: the panel's Save button, and the save gate
        in front of New session / Open project.

        Asks where to save the FIRST time this session is saved, then
        overwrites that file silently on every later save — the way a save
        button works everywhere else. ``save_project_as`` is how the user
        gets the dialog back.

        Returns the shared save result (see ``_save_result``). The frontend
        proceeds to a reset/load only on ``ok``, so a cancelled or failed
        save never discards the session.
        """
        if not self._trusted_page():
            return self._save_result(False, error="This page cannot save.")
        return self._save_project_file()

    def save_project_as(self) -> dict[str, Any]:
        """'Save as…': always ask where, then make that the file later saves
        overwrite.

        The dialog opens in the folder of the current target (when there is
        one) but still proposes a fresh timestamped filename, so confirming
        it writes a new file rather than quietly landing back on the one Save
        already overwrites.
        """
        if not self._trusted_page():
            return self._save_result(False, error="This page cannot save.")
        return self._save_project_file(force_dialog=True)

    def open_in_word(self, mode: str = "preserved") -> dict[str, Any]:
        """Export the section and open the file with the system's Word.

        The app cannot render Word's layout in its own panel, and a user who
        imported an office master wants to see the real thing. This writes
        the export — formatting-preserving by default — to a fresh file under
        the user's temp folder and hands it to the default .docx application.
        It goes through the same HTTP export route the window uses, with this
        launch's own token, so every guard the route applies (mode, detached
        state, tutorial scope) applies here too.

        Returns ``{"ok", "error", "path", "name"}``; a failure carries the
        server's own message.
        """
        if not self._trusted_page():
            return {"ok": False, "error": "This page cannot export.", "path": "", "name": ""}
        if self._backend is None:
            return {
                "ok": False,
                "error": "Open in Word is available in the desktop app only.",
                "path": "",
                "name": "",
            }
        mode = str(mode or "preserved")
        if mode not in _EXPORT_MODES:
            return {"ok": False, "error": "Unknown export mode.", "path": "", "name": ""}
        from backend import sessions

        workspace = sessions.get_workspace()
        if workspace.scope != "original":
            return {
                "ok": False,
                "error": "Return to your project before opening it in Word.",
                "path": "",
                "name": "",
            }
        try:
            payload, name = _fetch_backend_bytes(
                self._backend, f"/api/export/docx?mode={mode}"
            )
            target = _write_open_in_word_file(name, payload)
            _launch_file(target)
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc), "path": "", "name": ""}
        except OSError as exc:
            return {
                "ok": False,
                "error": f"The export could not be written or opened: {exc}",
                "path": "",
                "name": "",
            }
        return {"ok": True, "error": "", "path": os.fspath(target), "name": target.name}

    def open_external_link(self, url: str) -> bool:
        """Open a URL in the user's default system browser.

        pywebview's window is otherwise just a browser: a clicked link (a
        chat citation, a research/QC report source, the trust dossier's
        outbound references, ...) would navigate the window itself away from
        the app with no way back. The frontend intercepts every such click
        and calls this instead. Only http/https URLs are honored.
        """
        if not self._trusted_page():
            return False
        import urllib.parse
        import webbrowser

        try:
            parsed = urllib.parse.urlsplit(url)
        except ValueError:
            return False
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return False
        return bool(webbrowser.open(url))

    def save_template(self, template_id: str) -> bool:
        """Export one validated catalog entry through a scoped Save dialog."""
        if not self._trusted_page():
            return False
        import webview

        from backend.templates import get_template_catalog

        try:
            payload, filename = get_template_catalog().export(template_id)
        except Exception:
            return False
        target = self._window.create_file_dialog(
            webview.FileDialog.SAVE,
            save_filename=filename,
            file_types=_TEMPLATE_SAVE_FILE_TYPES,
        )
        if not target:
            return False
        if isinstance(target, (tuple, list)):
            target = target[0]
        if not self._trusted_page():
            return False
        return self._atomic_write_target(target, payload, prefix=".buildaspec-template-")

    def open_file(self, kind: str = "project") -> dict[str, str] | None:
        """Frontend's Open / Import buttons (native shell): show a native
        Open dialog and hand the chosen file's bytes back to JS.

        An HTML ``<input type="file">`` does not reliably deliver the selected
        file's *contents* to JavaScript across pywebview's webview backends —
        the dialog opens and a file can be picked, but ``input.files`` arrives
        empty, so the upload silently uploads nothing. The native shell
        therefore opens files the same way it saves them, through
        ``create_file_dialog``; the frontend uploads the returned bytes down
        the ordinary ``/api/project/load-file`` (or import) path, so every
        validation and UI-update step stays shared with the browser flow.

        ``kind`` selects the dialog's file filter (``"project"`` for
        ``.baspec``/``.json``, ``"docx"`` for a master import, ``"reference"``
        for an attachment, which accepts several types); an unknown kind
        degrades to the project filter. Returns ``{"name", "data_b64"}`` for
        the picked file, or ``None`` when the dialog is cancelled or the read
        fails — the frontend then does nothing, exactly like a cancelled HTML
        picker.
        """
        if self._window is None or not self._trusted_page():
            return None
        import base64

        import webview

        file_types = _OPEN_FILE_TYPES_BY_KIND.get(kind, _PROJECT_OPEN_FILE_TYPES)
        try:
            result = self._window.create_file_dialog(
                webview.FileDialog.OPEN,
                allow_multiple=False,
                file_types=file_types,
            )
        except Exception:
            return None
        if not result:
            return None  # user cancelled the Open dialog
        # create_file_dialog returns a path string on some backends, a
        # 1-tuple/list on others (mirrors the FileDialog.SAVE handling).
        target = result[0] if isinstance(result, (tuple, list)) else result
        if not self._trusted_page():
            return None
        try:
            with open(os.fspath(target), "rb") as handle:
                payload = handle.read()
        except (OSError, TypeError, ValueError):
            return None
        if not self._trusted_page():
            return None
        return {
            "name": os.path.basename(os.fspath(target)),
            "data_b64": base64.b64encode(payload).decode("ascii"),
        }

    # --- internals ---------------------------------------------------------
    def _force_close(self) -> None:
        self._allow_close = True
        if self._window is not None:
            self._window.destroy()

    @staticmethod
    def _save_result(
        ok: bool,
        *,
        cancelled: bool = False,
        error: str = "",
        target: str = "",
    ) -> dict[str, Any]:
        """The one shape every save path returns.

        ``cancelled`` is kept apart from ``error`` because the UI must treat
        them differently: backing out of a Save dialog is a decision and
        deserves silence, while a write that failed needs to say so — and the
        two are indistinguishable once both are just ``False``.
        """
        return {
            "ok": bool(ok),
            "cancelled": bool(cancelled),
            "error": error,
            "target": target,
            "name": os.path.basename(target) if target else "",
        }

    def _save_project_file(self, *, force_dialog: bool = False) -> dict[str, Any]:
        """Write the current session to a project file.

        Overwrites ``session.save_target`` when the session has one and the
        caller did not force the dialog; otherwise asks, and remembers the
        answer. A remembered target that cannot be written (moved, deleted,
        read-only, a disconnected drive) falls back to the dialog rather than
        failing: the user asked for a save, and the honest response to "that
        file is gone" is to ask where it should go now.
        """
        import webview

        from backend import sessions
        from backend.spec_doc.project_package import ProjectPackageError

        if not self._trusted_page():
            return self._save_result(False, error="This page cannot save.")
        workspace = sessions.get_workspace()
        if workspace.scope != "original":
            return self._save_result(
                False,
                error="Return to your project before saving it.",
            )
        session = workspace.session
        # Sampled beside the payload: a reset or project load while the Save
        # dialog is up must not bind the replacement session to this file.
        generation = session.generation
        try:
            payload = sessions.project_package_bytes(session)
            filename = sessions.project_default_filename(session)
        except ProjectPackageError as exc:
            return self._save_result(False, error=str(exc))
        except Exception:
            return self._save_result(
                False,
                error="This project could not be packaged for saving.",
            )

        # Where this session already saves itself, and whether this call is allowed
        # to write there without asking. Kept apart: "Save as…" forces the
        # dialog but should still OPEN in the folder the user last chose.
        current = str(getattr(session, "save_target", "") or "")
        remembered = "" if force_dialog else current
        if remembered:
            written = self._resolved_write(remembered, payload)
            if written:
                return self._save_result(
                    True,
                    target=self._bind_save_target(
                        session, written, generation
                    ),
                )
            # Fall through to the dialog: the remembered file is no longer
            # writable, so asking is the only way forward that saves anything.

        target = self._window.create_file_dialog(
            webview.FileDialog.SAVE,
            save_filename=filename,
            file_types=_PROJECT_SAVE_FILE_TYPES,
            **({"directory": os.path.dirname(current)} if current else {}),
        )
        if not target:
            return self._save_result(
                False,
                cancelled=True,
                error="The save was cancelled.",
            )
        # create_file_dialog returns a path string on some backends, a
        # 1-tuple on others.
        if isinstance(target, (tuple, list)):
            target = target[0]
        if not self._trusted_page():
            return self._save_result(False, error="This page cannot save.")
        written = self._resolved_write(target, payload)
        if not written:
            return self._save_result(
                False,
                error="That file could not be written. Try another location.",
            )
        return self._save_result(
            True,
            target=self._bind_save_target(session, written, generation),
        )

    @staticmethod
    def _bind_save_target(session, written: str, generation: int) -> str:
        """Bind ``session`` to the file just written, and report back only a
        target that actually stuck.

        Both write paths go through here so that a reported target means one
        thing: this session will overwrite that file next time. When the guard
        refuses — a reset or project load raced the save — the file is still
        written and the save still succeeded, but there is nothing to report,
        and saying otherwise would leave the panel promising an overwrite the
        very next click would turn back into a dialog.
        """
        from backend import sessions

        bound = sessions.remember_project_save_target(
            session, written, generation=generation
        )
        return written if bound else ""

    @classmethod
    def _resolved_write(cls, target, payload: bytes) -> str:
        """Atomically write ``payload`` to ``target``; return the absolute
        path written, or "" on failure."""
        try:
            target_path = os.path.abspath(os.fspath(target))
        except (TypeError, ValueError):
            return ""
        ok = cls._atomic_write_target(
            target_path, payload, prefix=".buildaspec-save-"
        )
        return target_path if ok else ""

    @staticmethod
    def _atomic_write_target(target, payload: bytes, *, prefix: str) -> bool:
        temp_path: str | None = None
        try:
            target_path = os.path.abspath(os.fspath(target))
            target_dir = os.path.dirname(target_path)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target_dir,
                prefix=prefix,
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = handle.name
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target_path)
            temp_path = None
            return True
        except (OSError, TypeError, ValueError):
            return False
        finally:
            if temp_path is not None:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass


def main() -> None:
    # Logging first: everything after this line — uvicorn, the backend,
    # a GUI failure below — has a durable destination even in the
    # windowed build where stdout/stderr are devnull.
    diagnostics.init_logging()
    try:
        runtime = _start_backend()
    except OSError as exc:
        import logging

        logging.getLogger("buildaspec.main").exception(
            "Could not reserve the exclusive loopback backend socket"
        )
        raise SystemExit(
            "Backend could not reserve its local listener. Close any other "
            "Build-a-Spec instance and try again."
        ) from exc
    if not _wait_for_health(runtime):
        try:
            runtime.listener.close()
        except OSError:
            pass
        raise SystemExit(
            "Backend failed its launch-identity check on the reserved local "
            "listener; see the diagnostics log."
        )

    backend_url = f"http://{runtime.host}:{runtime.port}"
    if settings.dev_mode():
        frontend_base_url = settings.DEV_FRONTEND_URL
    else:
        frontend_base_url = f"{backend_url}/"
        if not settings.FRONTEND_DIST.is_dir():
            raise SystemExit(
                "frontend/dist not found. Build the UI first:\n"
                "  cd frontend && npm install && npm run build\n"
                "or run in dev mode (BUILD_A_SPEC_DEV=1 with `npm run dev`)."
            )
    # Fragments never reach HTTP request lines, logs, traces, or Referer.
    url = _url_with_boot_fragment(frontend_base_url, runtime.boot_nonce)

    try:
        import webview  # pywebview

        # WebView2 blocks file downloads unless explicitly allowed — the
        # panel's "Export .docx" and "Save" buttons depend on this.
        try:
            webview.settings["ALLOW_DOWNLOADS"] = True
        except Exception:
            pass  # older pywebview without the settings dict

        # pywebview disables text selection by default (native-app feel),
        # injecting CSS that blocks selection everywhere except form fields.
        # That leaves the chat transcript unselectable — no copy, no
        # highlight. text_select=True suppresses that injection so plain
        # content is selectable; the app's own `select-none` (e.g. the
        # panel stepper) still governs where selection is intentionally off.
        #
        # The close controller is passed as js_api (exposing save_and_close /
        # discard_and_close) and bound to the window's `closing` event so a
        # window-close offers to save unsaved progress first.
        # pywebview leaves js_api injected after in-window navigation. Bind
        # every native bridge method to this launch's frontend origin.
        close_controller = _CloseController((frontend_base_url,), backend=runtime)
        window = webview.create_window(
            settings.APP_NAME,
            url,
            width=1440,
            height=900,
            min_size=(1100, 700),
            text_select=True,
            js_api=close_controller,
        )
        close_controller._bind(window)
        # debug=True adds the WebView2/WebKit inspector — dev mode only.
        webview.start(debug=settings.dev_mode())
    except Exception:
        # No usable native webview — plain browser fallback. Log WHY: a
        # swallowed GUI failure is indistinguishable from "pywebview not
        # installed", and in the packaged build this log line is the only
        # record of it.
        import logging
        import webbrowser

        logging.getLogger("buildaspec.main").exception(
            "pywebview shell failed; falling back to the default browser"
        )
        webbrowser.open(url)
        logging.getLogger("buildaspec.main").info(
            "browser fallback serving at %s", frontend_base_url
        )
        print(
            f"{settings.APP_NAME} running at {frontend_base_url} "
            "— Ctrl+C to quit."
        )
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
