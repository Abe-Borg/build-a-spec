"""Runtime settings for Build-a-Spec.

Model ids mirror Spec Critic's current stack (``api_config.py`` in the
Claude-Spec-Critic repo): Sonnet 5 for interactive interview/drafting turns.
Heavier drafting passes may escalate to Opus 4.8 in a later phase. Every
value is env-overridable with the same degrade-gracefully posture as Spec
Critic — a bad value falls back to the default rather than crashing.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Build-a-Spec"
VERSION = "0.5.0"

# --- Models -----------------------------------------------------------------

MODEL_SONNET_5 = "claude-sonnet-5"
MODEL_OPUS_48 = "claude-opus-4-8"

INTERVIEW_MODEL_DEFAULT = MODEL_SONNET_5
INTERVIEW_MODEL = (
    os.environ.get("BUILD_A_SPEC_INTERVIEW_MODEL", "").strip()
    or INTERVIEW_MODEL_DEFAULT
)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


INTERVIEW_MAX_TOKENS = _int_env("BUILD_A_SPEC_MAX_TOKENS", 8192)

# --- Research (Phase 4) -----------------------------------------------------

RESEARCH_MODEL = (
    os.environ.get("BUILD_A_SPEC_RESEARCH_MODEL", "").strip()
    or MODEL_SONNET_5
)
RESEARCH_MAX_TOKENS = _int_env("BUILD_A_SPEC_RESEARCH_MAX_TOKENS", 8192)

# --- Server -----------------------------------------------------------------

HOST = "127.0.0.1"
PORT = _int_env("BUILD_A_SPEC_PORT", 8756)

# Vite dev server (used by main.py when BUILD_A_SPEC_DEV=1).
DEV_FRONTEND_URL = "http://localhost:5173"


def dev_mode() -> bool:
    return os.environ.get("BUILD_A_SPEC_DEV", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


# --- Paths ------------------------------------------------------------------


def _resolve_frontend_dist() -> Path:
    """The built frontend, source checkout or frozen app.

    In the PyInstaller build (``packaging/windows/build-a-spec.spec``) the
    Vite output is bundled at ``frontend/dist`` relative to the bundle
    root (``sys._MEIPASS`` for the extracted resources), so the packaged
    app serves the same files the source checkout does.
    """
    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return bundle_root / "frontend" / "dist"
    return Path(__file__).resolve().parent.parent / "frontend" / "dist"


REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIST = _resolve_frontend_dist()
