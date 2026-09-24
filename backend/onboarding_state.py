"""Per-install tutorial completion: which version of the guided tour was finished.

The empty chat offers the guided tutorial as its first starter chip, and the
chip reads differently for someone who has already taken it ("Take the full
interactive tutorial again", no pulse) than for someone who has not. That
needs the answer to "did this user finish the tour?" to outlive the launch.

Browser storage cannot keep it, for the same reason it cannot keep the panel
tray's layout (``backend/ui_preferences.py``): pywebview runs its WebView in
private mode (``webview.start``'s default, which ``main.py`` keeps), and the
packaged app binds a fresh loopback port on every launch, so ``localStorage``
starts empty each time the app opens. The answer lives here instead: a small
JSON file in the app config directory, beside ``ui_preferences.json``.

It is a file of its own, deliberately, and not a key in
``ui_preferences.json``. ``PUT /api/ui/preferences`` REPLACES that file with
the panel layout, and the frontend sends it through an ordered save chain;
sharing the file would make every layout save erase the completion and every
completion write erase the layout, unless both went through a merge under a
lock. Two independent files have two independent writers and nothing to
merge.

The backend does not know what a version means — the frontend owns that
(``ONBOARDING_COMPLETION_VERSION`` in
``frontend/src/lib/onboardingCompletion.ts``, bumped when the tour changes
enough that a returning user should be invited again). It stores the integer
it is given.

Reading is lenient (a missing, unreadable, oversized or malformed file means
"not completed", never an error: this is cosmetic state). Writing is atomic
(the shared temp-file-and-replace in ``project_brief``), so a crash or a full
disk leaves the previous file whole.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ONBOARDING_FILENAME = "onboarding_state.json"
FORMAT_VERSION = 1
# A completion version is a small positive integer the frontend bumps by one
# when the tour changes. The ceiling is a guard for a hand-edited file, not a
# product limit.
MAX_COMPLETION_VERSION = 1_000_000
# The file holds one integer. Anything this large was not written by the app.
MAX_FILE_BYTES = 16 * 1024


@dataclass(frozen=True)
class OnboardingState:
    """The tour version this install last finished; None when it never has."""

    completed_version: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"completed_version": self.completed_version}


def default_onboarding_path() -> Path:
    """``onboarding_state.json`` in the app config directory."""
    from .app_paths import app_config_dir

    return app_config_dir() / ONBOARDING_FILENAME


def valid_completion_version(value: Any) -> bool:
    """A real integer (never a bool — ``True`` is an ``int``) in range."""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 1 <= value <= MAX_COMPLETION_VERSION
    )


def state_from_dict(raw: Any) -> OnboardingState:
    """Lenient: anything that cannot be read as a version is "not completed"."""
    if not isinstance(raw, dict):
        return OnboardingState()
    version = raw.get("completed_version")
    return OnboardingState(
        completed_version=version if valid_completion_version(version) else None
    )


def load_onboarding_state(path: str | Path | None = None) -> OnboardingState:
    """The saved completion, or "not completed" when there is none to read."""
    target = Path(path) if path is not None else default_onboarding_path()
    try:
        if target.stat().st_size > MAX_FILE_BYTES:
            return OnboardingState()
        # utf-8-sig: a file hand-edited in an older Notepad starts with a BOM.
        raw = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return OnboardingState()
    return state_from_dict(raw)


def save_onboarding_state(
    state: OnboardingState, path: str | Path | None = None
) -> None:
    """Replace the file atomically. Raises ``OSError`` when it cannot be written."""
    from .project_brief import write_brief_atomically

    target = Path(path) if path is not None else default_onboarding_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": FORMAT_VERSION, **state.to_dict()}
    write_brief_atomically(
        os.fspath(target),
        (json.dumps(payload, indent=2) + "\n").encode("utf-8"),
        prefix=".buildaspec-onboarding-",
    )
