"""Machine-local QC transport, independent of projects and panel layout.

Lenient reads fall back to batch. Writes are strict and atomic, using the
same config directory and temp-file replacement as onboarding_state.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from . import settings

PREFERENCES_FILENAME = "qc_preferences.json"
MAX_FILE_BYTES = 16 * 1024
PREFERENCES_LOCK = threading.Lock()


def default_preferences_path() -> Path:
    from .app_paths import app_config_dir

    return app_config_dir() / PREFERENCES_FILENAME


def load_batch_verification(path: str | Path | None = None) -> bool:
    try:
        target = Path(path) if path is not None else default_preferences_path()
        if target.stat().st_size > MAX_FILE_BYTES:
            return True
        raw = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return True
    value = raw.get("batch_verification") if isinstance(raw, dict) else None
    return value if isinstance(value, bool) else True


def save_batch_verification(value: bool, path: str | Path | None = None) -> None:
    from .project_brief import write_brief_atomically

    if not isinstance(value, bool):
        raise ValueError("batch_verification must be a boolean")
    target = Path(path) if path is not None else default_preferences_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "batch_verification": value}
    write_brief_atomically(
        os.fspath(target),
        (json.dumps(payload, indent=2) + "\n").encode("utf-8"),
        prefix=".buildaspec-qc-preferences-",
    )


def resolve_batch_verification(requested: bool | None = None) -> bool:
    override = settings.qc_batch_verification_override()
    if override is not None:
        return override
    return load_batch_verification() if requested is None else requested


def preference_payload() -> dict:
    return {
        "ok": True,
        "batch_verification": resolve_batch_verification(),
        "locked": settings.qc_batch_verification_override() is not None,
    }
