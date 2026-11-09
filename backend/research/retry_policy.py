"""Retry and failure-classification policy for research streaming calls.

Ported from Claude-Spec-Critic ``src/verification/retry_policy.py`` — the
realtime subset only (the batch-wave tracker, batch failure classifier, and
continuation-mode lookup stay behind; Build-a-Spec has no batch pipeline).
Semantics preserved exactly: typed-SDK-first classification with the
message-substring heuristic demoted to a last resort for generic
exceptions, per-class backoff multipliers, and ``INVALID_REQUEST`` never
retried (the request shape would have to change to get a different answer).

One Build-a-Spec addition, not part of the port: :func:`retry_mode`, the
resume-first rule every retry loop in both engines applies (Research/QC cost
Tier 1, Chunk 5). It lives here, beside the policy it extends, so research,
streamed Final QC and batched Final QC cannot disagree about when a
conversation survives a transient failure.

A second (2026-11-09): a reached spend limit is its own class,
``SPEND_LIMIT``, never retried (see :func:`spend_limit_kind`).
"""
from __future__ import annotations

import re
from collections.abc import Mapping
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
    # The organization reached a spend limit (2026-11-09). Never retried:
    # the provider refuses every request until access resumes.
    SPEND_LIMIT = "spend_limit"


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


# ---------------------------------------------------------------------------
# Spend limits (2026-11-09)
# ---------------------------------------------------------------------------
#
# Anthropic's rate-limits page names two ways an organization's spend stops
# its requests, and neither ends on its own within a run:
#
# - the usage tier's monthly cap: HTTP 429 ``rate_limit_error`` with NO
#   ``retry-after`` header and, on the Messages API,
#   ``error.details.error_code == "enforced_spend_limit_reached"``; the
#   message starts "You have reached your API usage limits" and says when
#   access resumes (the first of next month). Retrying fails until then.
# - a limit the organization set itself in the Console: HTTP 400
#   ``invalid_request_error`` whose message starts "You have reached your
#   specified API usage limits" (or "... specified workspace API usage
#   limits").
#
# As a rate limit the cap was retried (three app attempts, each with the
# SDK's own two) and then reported as raw SDK text. ``SPEND_LIMIT`` is never
# retried, and :func:`spend_limit_message` says what happened in words.
SPEND_LIMIT_CAP = "cap"
SPEND_LIMIT_OWN = "own"
SPEND_CAP_ERROR_CODE = "enforced_spend_limit_reached"
_SPEND_CAP_MESSAGE_PREFIX = "You have reached your API usage limits"
_OWN_SPEND_LIMIT_MESSAGE_PREFIXES = (
    "You have reached your specified API usage limits",
    "You have reached your specified workspace API usage limits",
)
# The provider's own words are quoted, bounded and on one line.
PROVIDER_MESSAGE_MAX_CHARS = 400
_WHITESPACE_RE = re.compile(r"\s+")


def _field(value: object, name: str) -> object:
    """``value[name]`` for a mapping, ``value.name`` otherwise (SDK objects)."""
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def spend_limit_kind_of_error(
    error: object, *, status_code: int | None = None, retry_after: bool = False
) -> str:
    """``SPEND_LIMIT_CAP``, ``SPEND_LIMIT_OWN`` or ``""`` for an API error object.

    ``error`` is the ``{type, message, details}`` object inside the error
    envelope (a dict, or an SDK object such as a batch result's). The
    documented error code decides alone; the message prefixes are the
    fallback, held to the error's type (or status) so an unrelated error
    quoting the phrase never matches, and the cap's prefix only without a
    ``retry-after`` (the cap's 429 never carries one).
    """
    if error is None:
        return ""
    details = _field(error, "details")
    if _field(details, "error_code") == SPEND_CAP_ERROR_CODE:
        return SPEND_LIMIT_CAP
    message = _field(error, "message")
    if not isinstance(message, str):
        return ""
    error_type = _field(error, "type")
    if (
        (status_code == 429 or error_type == "rate_limit_error")
        and not retry_after
        and message.startswith(_SPEND_CAP_MESSAGE_PREFIX)
    ):
        return SPEND_LIMIT_CAP
    if (status_code == 400 or error_type == "invalid_request_error") and (
        message.startswith(_OWN_SPEND_LIMIT_MESSAGE_PREFIXES)
    ):
        return SPEND_LIMIT_OWN
    return ""


def _provider_error(exc: BaseException) -> object:
    """The ``error`` object of an API status error's JSON body, or ``None``."""
    if not isinstance(exc, APIStatusError):
        return None
    return _field(exc.body, "error") if exc.body is not None else None


def spend_limit_kind(exc: BaseException) -> str:
    """``SPEND_LIMIT_CAP``, ``SPEND_LIMIT_OWN`` or ``""`` for an exception."""
    if not isinstance(exc, APIStatusError):
        return ""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    retry_after = bool(headers is not None and headers.get("retry-after"))
    return spend_limit_kind_of_error(
        _provider_error(exc),
        status_code=getattr(exc, "status_code", None),
        retry_after=retry_after,
    )


def provider_error_message(exc: BaseException) -> str:
    """The provider's own ``error.message``, on one line and bounded; ``""``
    when the body carries none (the SDK's ``str(exc)`` is the whole body)."""
    return _one_line(_field(_provider_error(exc), "message"))


def _one_line(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = _WHITESPACE_RE.sub(" ", value).strip()
    if len(text) > PROVIDER_MESSAGE_MAX_CHARS:
        text = text[: PROVIDER_MESSAGE_MAX_CHARS - 1].rstrip() + "…"
    return text


# Every spend-limit text starts with this, so a surface that kept only the
# text (a lens status has no failure class) can still tell it apart.
SPEND_LIMIT_TEXT_PREFIX = "Your Anthropic organization has reached"


def spend_limit_text(kind: str, provider_message: object = "") -> str:
    """The words every surface shows for a reached spend limit."""
    quoted = _one_line(provider_message)
    said = f' Anthropic says: "{quoted}"' if quoted else ""
    if kind == SPEND_LIMIT_CAP:
        return (
            f"{SPEND_LIMIT_TEXT_PREFIX} its monthly API spend "
            "cap, so Anthropic is refusing requests until access resumes."
            f"{said} To continue sooner, request a higher limit on the "
            "Claude Console's Limits page."
        )
    if kind == SPEND_LIMIT_OWN:
        return (
            f"{SPEND_LIMIT_TEXT_PREFIX} the spend limit set for "
            "it in the Claude Console, so Anthropic is refusing requests "
            f"until access resumes.{said} To continue sooner, raise or remove "
            "the limit on the Console's Billing page."
        )
    return ""


def is_spend_limit_text(text: object) -> bool:
    """True for a message :func:`spend_limit_text` wrote."""
    return isinstance(text, str) and text.startswith(SPEND_LIMIT_TEXT_PREFIX)


def spend_limit_message(exc: BaseException) -> str:
    """:func:`spend_limit_text` for an exception; ``""`` when it is not one."""
    kind = spend_limit_kind(exc)
    return spend_limit_text(kind, provider_error_message(exc)) if kind else ""


def classify_exception(exc: BaseException) -> FailureClass:
    """Classify an exception into a :class:`FailureClass` (typed-SDK-first)."""
    if spend_limit_kind(exc):
        return FailureClass.SPEND_LIMIT
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


# How a retry starts (Research/QC cost Tier 1, Chunk 5). The values ride the
# ``{prefix}_retry`` live events as ``mode``, so they are wire vocabulary.
RETRY_MODE_RESUME = "resume"
RETRY_MODE_RESTART = "restart"


def retry_mode(*, progressed: bool, next_attempt: int, attempts: int) -> str:
    """Resume first, restart last: how the retry about to run begins.

    A research dimension or a Final QC call is a conversation — an opening
    request, then a ``pause_turn`` continuation for every pause — and a
    transient failure (rate limit, server error, dropped connection) can land
    after it has made real, paid-for progress. Restarting threw that progress
    away and paid for every finished continuation again.

    ``RETRY_MODE_RESUME`` when both hold:

    - ``progressed``: the conversation has at least one completed response,
      and the failure was a request's own, so the request that failed can be
      sent again exactly as it stood (messages, responses, container and
      continuation count all carry on);
    - ``next_attempt`` (0-based) is not the final attempt of ``attempts``.

    ``RETRY_MODE_RESTART`` otherwise — no progress to keep, or the final
    attempt, which always starts a fresh conversation. That is the whole
    safety argument for having no switch: a conversation that keeps failing
    once resumed still gets the clean attempt it always got, so the new path
    can cost at most the one extra attempt it spent on the resume.

    With the default three attempts, the first retry can resume and the
    second (the final attempt) always restarts. Backoff and attempt counting
    are unchanged either way; only what the next attempt starts from differs.
    """
    final_attempt = max(1, int(attempts)) - 1
    if progressed and int(next_attempt) < final_attempt:
        return RETRY_MODE_RESUME
    return RETRY_MODE_RESTART
