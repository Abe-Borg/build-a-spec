"""App-specific filesystem paths and filenames.

Ported from Spec Critic's ``src/core/app_paths.py`` (same conventions,
Build-a-Spec identity): persistent state and config live in the platform
config directory, with an executable/source-parent fallback so the legacy
"drop a key file next to the exe" workflow works once this is packaged.
"""
from __future__ import annotations

import sys
from pathlib import Path

from platformdirs import user_config_dir

API_KEY_FILENAME = "build_a_spec_api_key.txt"


def app_config_dir() -> Path:
    d = Path(user_config_dir("BuildASpec", appauthor=False))
    d.mkdir(parents=True, exist_ok=True)
    return d


def executable_dir() -> Path:
    """Directory containing the running source/executable.

    Fallback location for the API key file so a key file dropped next to
    the packaged .exe (or the repo root when running from source) is found.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def api_key_paths() -> list[Path]:
    """Candidate locations to read the API key from, in priority order."""
    return [
        app_config_dir() / API_KEY_FILENAME,
        executable_dir() / API_KEY_FILENAME,
    ]
