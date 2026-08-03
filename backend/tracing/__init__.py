"""Session tracing: JSONL spans/events + the bundled HTML viewer (Phase 5).

Ported from Claude-Spec-Critic ``src/tracing/`` (config / spans /
redaction / recorder ≈verbatim; ``capture.py`` is the Build-a-Spec-native
hook layer — the analog of its ``capture_hooks.py``). Local-only, env
gated (``BUILD_A_SPEC_TRACE``, default on; ``BUILD_A_SPEC_TRACE_DEEP``
inlines prompts and stream chunks). View a trace by opening
``GET /api/trace/viewer`` and pointing it at the run directory under the
app state dir.
"""
from . import capture
from .capture import trace_startup_failure_state
from .config import (
    TraceRetentionPolicy,
    current_capture_level,
    default_trace_root,
    trace_dir_for_run,
    trace_enabled,
    trace_retention_policy,
)
from .recorder import TraceRecorder, get_recorder, set_recorder
from .retention import prune_trace_runs

__all__ = [
    "TraceRecorder",
    "TraceRetentionPolicy",
    "capture",
    "current_capture_level",
    "default_trace_root",
    "get_recorder",
    "set_recorder",
    "prune_trace_runs",
    "trace_dir_for_run",
    "trace_enabled",
    "trace_retention_policy",
    "trace_startup_failure_state",
]
