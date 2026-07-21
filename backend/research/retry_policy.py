"""Retry and failure-classification policy for research streaming calls.

Ported from Claude-Spec-Critic ``src/verification/retry_policy.py`` — the
realtime subset only (the batch-wave tracker, batch failure classifier, and
continuation-mode lookup stay behind; Build-a-Spec has no batch pipeline).
Semantics preserved exactly: typed-SDK-first classification with the
message-substring heuristic demoted to a last resort for generic
exceptions, per-class backoff multipliers, and ``INVALID_REQUEST`` never
retried (the request shape would have to change to get a different answer).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from anthropic import (
    APIConnectionError,
    APIError,
    APIStatusError,
    InternalServerError,
    RateLimitError,
)


class FailureClass(str, Enum):
    """Closed taxonomy of API failure classes (str-valued for cheap telemetry)."""

    RATE_LIMIT = "rate_limit"
    SERVER_ERROR = "server_error"
    CONNECTION = "connection"
    INVALID_REQUEST = "invalid_request"
    PARSE_ERROR = "parse_error"
    PAUSE_TURN = "pause_turn"
    UNKNOWN = "unknown"


_RETRYABLE_REALTIME = frozenset(
    {FailureClass.RATE_LIMIT, FailureClass.SERVER_ERROR, FailureClass.CONNECTION}
)


def is_retryable_failure_class(failure_class: FailureClass) -> bool:
    return failure_class in _RETRYABLE_REALTIME


# Connection-error message substrings — last resort only, for generic
# exceptions that escaped the SDK's translation layer.
_CONNECTION_PATTERNS = (
    "peer closed connection",
    "incomplete chunked read",
    "connection reset",
    "connection closed",
    "timed out",
    "timeout",
    "broken pipe",
    "remotedisconnected",
    "connectionreset",
    "server disconnected",
    "eof occurred",
    "incomplete read",
)


def classify_exception(exc: BaseException) -> FailureClass:
    """Classify an exception into a :class:`FailureClass` (typed-SDK-first)."""
    if isinstance(exc, RateLimitError):
        return FailureClass.RATE_LIMIT
    if isinstance(exc, InternalServerError):
        return FailureClass.SERVER_ERROR
    if isinstance(exc, APIStatusError):
        status = getattr(exc, "status_code", None)
        if status == 529 or exc.__class__.__name__ == "OverloadedError":
            return FailureClass.SERVER_ERROR
        if isinstance(status, int) and 500 <= status < 600:
            return FailureClass.SERVER_ERROR
        if isinstance(status, int) and 400 <= status < 500:
            return FailureClass.INVALID_REQUEST
        return FailureClass.UNKNOWN
    if isinstance(exc, APIConnectionError):
        return FailureClass.CONNECTION
    if isinstance(exc, APIError):
        return FailureClass.INVALID_REQUEST
    msg = str(exc).lower()
    if any(pat in msg for pat in _CONNECTION_PATTERNS):
        return FailureClass.CONNECTION
    return FailureClass.UNKNOWN


@dataclass(frozen=True)
class RetryPolicy:
    """Closed bundle for an app-level retry loop."""

    max_attempts: int = 3
    base_backoff_seconds: float = 5.0
    rate_limit_multiplier: float = 2.0
    server_error_multiplier: float = 2.0
    connection_multiplier: float = 1.0


DEFAULT_REALTIME_RETRY_POLICY = RetryPolicy(
    max_attempts=3, base_backoff_seconds=5.0
)


def compute_backoff_seconds(
    policy: RetryPolicy, *, attempt: int, failure_class: FailureClass
) -> float:
    """Seconds to sleep before ``attempt`` (0-indexed): base * mult ** attempt."""
    base = max(0.0, float(policy.base_backoff_seconds))
    if failure_class is FailureClass.RATE_LIMIT:
        multiplier = policy.rate_limit_multiplier
    elif failure_class is FailureClass.SERVER_ERROR:
        multiplier = policy.server_error_multiplier
    elif failure_class is FailureClass.CONNECTION:
        multiplier = policy.connection_multiplier
    else:
        multiplier = 1.0
    return base * (multiplier ** max(0, int(attempt)))
