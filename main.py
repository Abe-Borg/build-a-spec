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
        webview.create_window(
            settings.APP_NAME,
            url,
            width=1440,
            height=900,
            min_size=(1100, 700),
            text_select=True,
        )
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
