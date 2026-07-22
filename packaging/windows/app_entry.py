"""Frozen-app entry point for the Windows PyInstaller build.

Cloned from Claude-Spec-Critic ``packaging/windows/app_entry.py``.
PyInstaller freezes a *script*, so this thin wrapper calls the app's
``main``. Two headless flags let the release workflow smoke-test the
frozen executable without opening a window:

    BuildASpec.exe --version     print the version and exit
    BuildASpec.exe --selfcheck   import the heavy modules — proving
                                 PyInstaller bundled every hidden import —
                                 and exit 0 (non-zero on any import error)

The build is windowed (``console=False``), so ``sys.stdout`` may be
``None``; ``_emit`` also writes to the file named by
``BUILD_A_SPEC_SELFCHECK_OUT`` (set by CI) so the smoke step can read the
outcome regardless.
"""
from __future__ import annotations

import os
import sys


def _emit(message: str) -> None:
    try:
        if sys.stdout is not None:
            print(message)
    except Exception:
        pass
    out = os.environ.get("BUILD_A_SPEC_SELFCHECK_OUT")
    if out:
        try:
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(message + "\n")
        except OSError:
            pass


def _print_version() -> int:
    from backend import settings

    _emit(settings.VERSION)
    return 0


def _selfcheck() -> int:
    try:
        from backend import settings  # noqa: F401
        from backend.app import app  # noqa: F401 - the FastAPI surface froze
        from backend.research import engine  # noqa: F401 - the research engine froze
        from backend.compliance import checker  # noqa: F401 - the audit froze
        from backend import updates  # noqa: F401 - the updater froze
        from backend.spec_doc import importer  # noqa: F401 - docx import froze
        import webview  # noqa: F401 - pulls the pywebview backend

        if not settings.FRONTEND_DIST.is_dir():
            _emit(
                "SELFCHECK FAILED: bundled frontend not found at "
                f"{settings.FRONTEND_DIST}"
            )
            return 1
    except Exception:
        import traceback

        _emit("SELFCHECK FAILED:\n" + traceback.format_exc())
        return 1
    from backend import settings as _settings

    _emit(f"BuildASpec {_settings.VERSION} selfcheck ok")
    return 0


def _boot_check() -> int:
    """Actually start the backend the way the app does and confirm it serves
    ``/api/health``, then exit. Unlike ``--selfcheck`` (imports only), this
    exercises the real uvicorn startup — catching windowed-mode boot crashes
    such as the ``None`` std-stream logging failure that a pure import check
    can't see. Runs headless (no window)."""
    try:
        from main import _start_backend, _wait_for_health

        _start_backend()
        healthy = _wait_for_health(timeout_s=30.0)
    except Exception:
        import traceback

        _emit("BOOT CHECK FAILED:\n" + traceback.format_exc())
        return 1
    if not healthy:
        _emit("BOOT CHECK FAILED: backend did not become healthy")
        return 1
    from backend import settings as _settings

    _emit(f"BuildASpec {_settings.VERSION} boot ok")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--version" in args:
        return _print_version()
    if "--selfcheck" in args:
        return _selfcheck()
    if "--boot-check" in args:
        return _boot_check()
    from main import main as app_main

    app_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
