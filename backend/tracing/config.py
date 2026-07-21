"""Tracing configuration: env parsing, capture level, trace dir.

Ported ≈verbatim from Claude-Spec-Critic ``src/tracing/config.py`` with
the ``BUILD_A_SPEC_*`` identity. Default ON for the main flag (traces are
local-only and the recorder is cheap); deep mode default OFF and implies
trace enabled.
"""
from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_state_dir

LEVEL_OFF = "off"
LEVEL_DEFAULT = "default"
LEVEL_DEEP = "deep"

ENV_TRACE = "BUILD_A_SPEC_TRACE"
ENV_TRACE_DEEP = "BUILD_A_SPEC_TRACE_DEEP"
ENV_TRACE_DIR = "BUILD_A_SPEC_TRACE_DIR"

_DISABLE_TOKENS = frozenset({"0", "false", "no", "off"})


def _env_flag_disabled(name: str) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return False
    return raw.strip().lower() in _DISABLE_TOKENS


def _env_flag_enabled(name: str) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return False
    return raw.strip().lower() not in _DISABLE_TOKENS and raw.strip() != ""


def trace_enabled() -> bool:
    """Default ON. Disable with ``BUILD_A_SPEC_TRACE=0``; deep implies on."""
    if trace_deep_enabled():
        return True
    return not _env_flag_disabled(ENV_TRACE)


def trace_deep_enabled() -> bool:
    """Default OFF. Enable with ``BUILD_A_SPEC_TRACE_DEEP=1``."""
    return _env_flag_enabled(ENV_TRACE_DEEP)


def current_capture_level() -> str:
    if not trace_enabled():
        return LEVEL_OFF
    if trace_deep_enabled():
        return LEVEL_DEEP
    return LEVEL_DEFAULT


def default_trace_root() -> Path:
    """``<state dir>/BuildASpec/traces`` (override: ``BUILD_A_SPEC_TRACE_DIR``)."""
    override = os.environ.get(ENV_TRACE_DIR)
    if override:
        return Path(os.path.expanduser(os.path.expandvars(override)))
    return Path(user_state_dir("BuildASpec", appauthor=False)) / "traces"


def trace_dir_for_run(run_id: str) -> Path:
    return default_trace_root() / run_id
