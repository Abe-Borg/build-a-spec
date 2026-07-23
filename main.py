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

import io
import os
import sys
import tempfile
import threading
import time
import urllib.request

import uvicorn

from backend import settings


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


def _start_backend() -> threading.Thread:
    # Must run before uvicorn.Config configures logging (see the docstring).
    _ensure_std_streams()
    config = uvicorn.Config(
        "backend.app:app",
        host=settings.HOST,
        port=settings.PORT,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return thread


def _wait_for_health(timeout_s: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout_s
    url = f"http://{settings.HOST}:{settings.PORT}/api/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


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

    Only the two ``js_api`` methods are public; everything else is
    underscore-prefixed so pywebview does not expose it to JavaScript.
    """

    # The frontend hook the prompt calls; returns whether it was handled so a
    # broken/old page never leaves an unclosable window.
    _REQUEST_CLOSE_JS = (
        "(function(){try{"
        "if(typeof window.buildaspecRequestClose==='function'){"
        "window.buildaspecRequestClose();return true;}"
        "}catch(e){}return false;})()"
    )

    def __init__(self) -> None:
        self._window = None
        self._allow_close = False
        self._prompting = False

    def _bind(self, window) -> None:
        self._window = window
        window.events.closing += self._on_closing

    # --- pywebview 'closing' handler (synchronous, on the UI thread) --------
    def _on_closing(self):
        # Returning False cancels the close (winforms sets args.Cancel); any
        # other value lets it proceed.
        if self._allow_close or self._window is None:
            return None  # a confirmed close (or no window) — let it proceed
        try:
            from backend import sessions

            if not sessions.has_unsaved_progress(sessions.get_session()):
                return None  # nothing to lose — close without nagging
        except Exception:
            return None  # never trap the user on a bookkeeping error
        if not self._prompting:
            self._prompting = True
            threading.Thread(target=self._ask_frontend, daemon=True).start()
        return False  # veto for now; a js_api call finishes the close

    def _ask_frontend(self) -> None:
        handled = False
        try:
            handled = bool(self._window.evaluate_js(self._REQUEST_CLOSE_JS))
        except Exception:
            handled = False
        finally:
            self._prompting = False
        if not handled:
            # No frontend handler (broken/old page) — don't trap the user.
            self._force_close()

    # --- exposed to the window's JS as window.pywebview.api.* --------------
    def save_and_close(self) -> None:
        """Frontend chose 'Save & close': write a project file, then close."""
        if self._save_project_file():
            self._force_close()
        # If the user backed out of the native Save dialog, stay in the app —
        # the close is still vetoed, so nothing else is needed.

    def discard_and_close(self) -> None:
        """Frontend chose 'Don't save': close without writing anything."""
        self._force_close()

    # --- internals ---------------------------------------------------------
    def _force_close(self) -> None:
        self._allow_close = True
        if self._window is not None:
            self._window.destroy()

    def _save_project_file(self) -> bool:
        """Write the current session to a user-chosen project file.

        Returns True if a file was written, False if the user cancelled the
        Save dialog or the write failed.
        """
        import webview

        from backend import sessions

        session = sessions.get_session()
        try:
            payload = sessions.project_package_bytes(session)
            filename = sessions.project_default_filename(session)
        except Exception:
            return False
        target = self._window.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=filename,
            file_types=("Build-a-Spec project (*.baspec)", "All files (*.*)"),
        )
        if not target:
            return False  # user cancelled the Save dialog
        # create_file_dialog returns a path string on some backends, a
        # 1-tuple on others.
        if isinstance(target, (tuple, list)):
            target = target[0]
        temp_path: str | None = None
        try:
            # Write beside the selected target, close it, then atomically
            # replace. A crash or short write cannot leave a half-valid
            # project under the user's chosen filename.
            target_path = os.path.abspath(os.fspath(target))
            target_dir = os.path.dirname(target_path)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target_dir,
                prefix=".buildaspec-save-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = handle.name
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target_path)
            temp_path = None
        except (OSError, TypeError, ValueError):
            return False
        finally:
            if temp_path is not None:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
        return True


def main() -> None:
    _start_backend()
    if not _wait_for_health():
        raise SystemExit(
            "Backend failed to start on "
            f"http://{settings.HOST}:{settings.PORT} — see logs above."
        )

    if settings.dev_mode():
        url = settings.DEV_FRONTEND_URL
    else:
        url = f"http://{settings.HOST}:{settings.PORT}/"
        if not settings.FRONTEND_DIST.is_dir():
            raise SystemExit(
                "frontend/dist not found. Build the UI first:\n"
                "  cd frontend && npm install && npm run build\n"
                "or run in dev mode (BUILD_A_SPEC_DEV=1 with `npm run dev`)."
            )

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
        close_controller = _CloseController()
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
        webview.start()
    except Exception:
        # No usable native webview — plain browser fallback.
        import webbrowser

        webbrowser.open(url)
        print(f"{settings.APP_NAME} running at {url} — Ctrl+C to quit.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
