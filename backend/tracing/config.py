"""Tracing configuration: env parsing, capture level, trace dir.

Ported ≈verbatim from Claude-Spec-Critic ``src/tracing/config.py`` with
the ``BUILD_A_SPEC_*`` identity. Default ON for the main flag (traces are
local-only and the recorder is cheap); deep mode default OFF and implies
trace enabled.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path

from platformdirs import user_state_dir

LEVEL_OFF = "off"
LEVEL_DEFAULT = "default"
LEVEL_DEEP = "deep"

ENV_TRACE = "BUILD_A_SPEC_TRACE"
ENV_TRACE_DEEP = "BUILD_A_SPEC_TRACE_DEEP"
ENV_TRACE_DIR = "BUILD_A_SPEC_TRACE_DIR"
ENV_TRACE_MAX_RUNS = "BUILD_A_SPEC_TRACE_MAX_RUNS"
ENV_TRACE_MAX_AGE_DAYS = "BUILD_A_SPEC_TRACE_MAX_AGE_DAYS"
ENV_TRACE_MAX_MIB = "BUILD_A_SPEC_TRACE_MAX_MIB"

# Traces deliberately retain prompt/document material, so an always-on
# recorder also needs an always-on storage bound.  These defaults preserve a
# useful month of recent launches while preventing an unattended installation
# from growing without limit.  A zero value explicitly disables one bound.
DEFAULT_TRACE_MAX_RUNS = 100
DEFAULT_TRACE_MAX_AGE_DAYS = 30
DEFAULT_TRACE_MAX_MIB = 512

_DISABLE_TOKENS = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class TraceRetentionPolicy:
    """Independent trace-retention ceilings.

    A run is removed when it exceeds the age ceiling, or when removing the
    oldest eligible runs is required to meet the count/aggregate-byte ceiling.
    Zero disables the corresponding ceiling.  The active run and a live
    process's unfinished run are never eligible for pruning.
    """

    max_runs: int = DEFAULT_TRACE_MAX_RUNS
    max_age_days: int = DEFAULT_TRACE_MAX_AGE_DAYS
    max_bytes: int = DEFAULT_TRACE_MAX_MIB * 1024 * 1024

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


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


def _env_nonnegative_int(name: str, default: int) -> int:
    """Read a bounded-storage integer without letting bad env break startup."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


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


def trace_retention_policy() -> TraceRetentionPolicy:
    """Return the configured local-trace storage ceilings.

    Environment values are intentionally simple integers.  Invalid or
    negative values fall back to the safe default; zero is an explicit opt-out
    for that one ceiling.
    """
    max_mib = _env_nonnegative_int(ENV_TRACE_MAX_MIB, DEFAULT_TRACE_MAX_MIB)
    return TraceRetentionPolicy(
        max_runs=_env_nonnegative_int(
            ENV_TRACE_MAX_RUNS, DEFAULT_TRACE_MAX_RUNS
        ),
        max_age_days=_env_nonnegative_int(
            ENV_TRACE_MAX_AGE_DAYS, DEFAULT_TRACE_MAX_AGE_DAYS
        ),
        max_bytes=max_mib * 1024 * 1024,
    )
