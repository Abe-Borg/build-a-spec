"""Per-install appearance: which of the window's colour schemes is chosen.

Settings -> Appearance offers five colour schemes, copied with their colours
from Spec Surgeon (its ``settings.THEMES`` and ``web/shell.BACKGROUNDS``, its
``frontend/src/index.css`` and ``frontend/src/lib/theme.ts``) and adapted to
this app's tokens. The first is the default and is the app's original look.

The choice has to outlive the launch, and browser storage cannot keep it for
the reason it cannot keep the panel tray's layout
(``backend/ui_preferences.py``): pywebview runs its WebView in private mode
and the packaged app binds a fresh loopback port on every launch, so
``localStorage`` starts empty each time. The choice lives here instead: a
small JSON file in the app config directory, beside ``ui_preferences.json``.

It is a file of its own, never a key in ``ui_preferences.json``, for the
reason ``backend/onboarding_state.py`` gives: ``PUT /api/ui/preferences``
replaces that file whole, so sharing it would make every layout save erase
the scheme and every scheme save erase the layout.

``main.py`` reads it once at launch, before the window exists: it opens the
page with ``?theme=<id>`` (an inline script in ``frontend/index.html`` sets
``<html data-theme>`` from it before the first paint) and gives the native
window that scheme's background, so a light scheme never opens on a dark
frame. The page's list is ``frontend/src/lib/theme.ts``, its colours
``frontend/src/index.css``; ``tests/test_appearance.py`` keeps the three in
step.

Reading is lenient (a missing, unreadable, oversized or malformed file, or an
id this build does not know, is the default, never an error: this is
cosmetic state). Writing is atomic (the shared temp-file-and-replace in
``project_brief``), so a crash or a full disk leaves the previous file whole.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

APPEARANCE_FILENAME = "appearance.json"
FORMAT_VERSION = 1
# The window's colour schemes, the first the default.
THEMES = ("clay", "blueprint", "graphite", "carbon", "linen")
DEFAULT_THEME = THEMES[0]
# The window's background before the page paints, per scheme: each scheme's
# --color-bg in frontend/src/index.css.
BACKGROUNDS = {
    "clay": "#262624",
    "blueprint": "#121a26",
    "graphite": "#1b1d1f",
    "carbon": "#0b0b0c",
    "linen": "#f5f1ea",
}
# The file holds one short id. Anything this large was not written by the app.
MAX_FILE_BYTES = 16 * 1024


@dataclass(frozen=True)
class Appearance:
    """The window's colour scheme: one of ``THEMES``."""

    theme: str = DEFAULT_THEME

    def to_dict(self) -> dict[str, Any]:
        return {"theme": self.theme}


def default_appearance_path() -> Path:
    """``appearance.json`` in the app config directory."""
    from .app_paths import app_config_dir

    return app_config_dir() / APPEARANCE_FILENAME


def resolve_theme(value: Any) -> str:
    """A known scheme's id, else the default (a hand-edited or newer file)."""
    return value if isinstance(value, str) and value in THEMES else DEFAULT_THEME


def appearance_from_dict(raw: Any) -> Appearance:
    """Lenient: anything that cannot be read as a scheme is the default."""
    if not isinstance(raw, dict):
        return Appearance()
    return Appearance(theme=resolve_theme(raw.get("theme")))


def load_appearance(path: str | Path | None = None) -> Appearance:
    """The saved scheme, or the default when there is none to read."""
    target = Path(path) if path is not None else default_appearance_path()
    try:
        if target.stat().st_size > MAX_FILE_BYTES:
            return Appearance()
        # utf-8-sig: a file hand-edited in an older Notepad starts with a BOM.
        raw = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return Appearance()
    return appearance_from_dict(raw)


def save_appearance(
    appearance: Appearance, path: str | Path | None = None
) -> None:
    """Replace the file atomically. Raises ``OSError`` when it cannot be written."""
    from .project_brief import write_brief_atomically

    if appearance.theme not in THEMES:
        raise ValueError(f"unknown colour scheme: {appearance.theme!r}")
    target = Path(path) if path is not None else default_appearance_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": FORMAT_VERSION, **appearance.to_dict()}
    write_brief_atomically(
        os.fspath(target),
        (json.dumps(payload, indent=2) + "\n").encode("utf-8"),
        prefix=".buildaspec-appearance-",
    )
