"""Per-install UI preferences: how the document panel's panel tray is laid out.

The document panel stacks up to ten collapsible panels under the paper
(Review, Research, Final QC, Issues, Open items, Waiting on you, Project,
Project facts, Standards, Documents). The user can fold the whole tray away
and choose which panels appear in it, and that is a layout choice they expect
the app to remember.

Browser storage cannot remember it. pywebview runs its WebView in private
mode (``webview.start``'s default, which ``main.py`` keeps), and the packaged
app binds a fresh loopback port on every launch, so ``localStorage`` starts
empty each time the app opens. This module is where the choice is kept
instead: a small JSON file in the app config directory, beside the API key
file and the update state.

The backend does not know the panel vocabulary — the frontend owns it
(``frontend/src/lib/panelTray.ts``). It stores a bounded list of short ids
and the frontend ignores any id it does not know, so adding or renaming a
panel is a frontend-only change and an old file can never break a new build.

Reading is lenient (a missing, unreadable, oversized or malformed file is the
defaults, never an error: this is cosmetic state). Writing is atomic (the
shared temp-file-and-replace in ``project_brief``), so a crash or a full disk
leaves the previous file whole.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

PREFERENCES_FILENAME = "ui_preferences.json"
FORMAT_VERSION = 1
# A panel id: lowercase, starts with a letter, letters/digits/hyphens. The
# frontend's ids fit comfortably; anything else is not ours and is dropped.
PANEL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
# Ten panels today. The bound is a runaway guard for a hand-edited file, not
# a product limit.
MAX_HIDDEN_PANELS = 32
# The file holds a boolean and a short list. Anything this large was not
# written by the app, and reading it whole would be the only way to fail.
MAX_FILE_BYTES = 64 * 1024


@dataclass(frozen=True)
class UiPreferences:
    """The panel tray's layout: folded away, and which panels are left out."""

    panels_folded: bool = False
    hidden_panels: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "panels_folded": self.panels_folded,
            "hidden_panels": list(self.hidden_panels),
        }


def default_preferences_path() -> Path:
    """``ui_preferences.json`` in the app config directory."""
    from .app_paths import app_config_dir

    return app_config_dir() / PREFERENCES_FILENAME


def sanitize_hidden_panels(values: Iterable[Any]) -> tuple[str, ...]:
    """Well-formed ids only, each once, in order, at most MAX_HIDDEN_PANELS."""
    kept: list[str] = []
    for value in values:
        if not isinstance(value, str) or not PANEL_ID_PATTERN.fullmatch(value):
            continue
        if value in kept:
            continue
        kept.append(value)
        if len(kept) == MAX_HIDDEN_PANELS:
            break
    return tuple(kept)


def preferences_from_dict(raw: Any) -> UiPreferences:
    """Lenient: whatever a field cannot be read as falls back to its default."""
    if not isinstance(raw, dict):
        return UiPreferences()
    folded = raw.get("panels_folded")
    hidden = raw.get("hidden_panels")
    return UiPreferences(
        # Only a real boolean: ``"false"`` is truthy, and a string reading as
        # "folded" would hide every panel over a typo.
        panels_folded=folded if isinstance(folded, bool) else False,
        hidden_panels=(
            sanitize_hidden_panels(hidden) if isinstance(hidden, list) else ()
        ),
    )


def load_preferences(path: str | Path | None = None) -> UiPreferences:
    """The saved preferences, or the defaults when there are none to read."""
    target = Path(path) if path is not None else default_preferences_path()
    try:
        if target.stat().st_size > MAX_FILE_BYTES:
            return UiPreferences()
        # utf-8-sig: a file hand-edited in an older Notepad starts with a BOM.
        raw = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return UiPreferences()
    return preferences_from_dict(raw)


def save_preferences(
    preferences: UiPreferences, path: str | Path | None = None
) -> None:
    """Replace the file atomically. Raises ``OSError`` when it cannot be written."""
    from .project_brief import write_brief_atomically

    target = Path(path) if path is not None else default_preferences_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": FORMAT_VERSION, **preferences.to_dict()}
    write_brief_atomically(
        os.fspath(target),
        (json.dumps(payload, indent=2) + "\n").encode("utf-8"),
        prefix=".buildaspec-ui-",
    )
