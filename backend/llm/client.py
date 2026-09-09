"""Anthropic client factory.

One place constructs the SDK client so tests can monkeypatch a fake. The
client is rebuilt when the stored key changes (saving a key through the UI
takes effect without a restart). The SDK's transport knobs — its own request
retries and the per-request timeout — are passed explicitly from
``settings`` (``SDK_MAX_RETRIES`` / ``API_TIMEOUT_SECONDS``) rather than
left to the SDK's silent defaults, and both constructors share the one
``_client_options()`` so the probe client cannot drift from the live one.
"""
from __future__ import annotations

import threading

import anthropic

from .. import settings
from ..api_key_store import load_api_key


class MissingApiKeyError(RuntimeError):
    """No Anthropic API key is configured (env, keyring, or key file)."""


# The one friendly message every 401 surface (chat, research, QC) shows
# instead of the raw SDK exception text.
AUTH_ERROR_MESSAGE = "Your Anthropic API key is invalid or has expired."


def is_authentication_error(exc: BaseException) -> bool:
    """True for a 401 from the Anthropic API (bad/expired/revoked key)."""
    return isinstance(exc, anthropic.AuthenticationError)


# The SDK's default connect timeout. Kept separate from the configurable
# read timeout on purpose: ``timeout=<number>`` would apply the read value
# to connecting as well, so a black-holed TCP connect would sit for the
# whole read budget (ten minutes by default) before the SDK's first retry.
_CONNECT_TIMEOUT_SECONDS = 5.0


def _client_options() -> dict:
    """Transport kwargs for every client this module builds, read at call
    time so a changed setting takes effect on the next rebuild."""
    return {
        "max_retries": settings.SDK_MAX_RETRIES,
        "timeout": anthropic.Timeout(
            float(settings.API_TIMEOUT_SECONDS), connect=_CONNECT_TIMEOUT_SECONDS
        ),
    }


def bounded_request_options(read_timeout_seconds: float) -> dict:
    """Per-request overrides for a call that must finish inside a deadline.

    ``client.with_options(**bounded_request_options(30))`` turns the SDK's own
    retries off and shortens the read timeout, so one call can spend at most
    roughly its timeout of the caller's window. Without it a single request
    rides the module defaults — ``SDK_MAX_RETRIES`` attempts at
    ``API_TIMEOUT_SECONDS`` each — and any bound measured in a couple of
    minutes is fiction. The connect timeout stays the SDK's own, for the
    reason in ``_CONNECT_TIMEOUT_SECONDS``: folding it into the read budget
    would make a black-holed connect the longest call of all.

    Used by Final QC's post-cancellation settlement window, which collects
    results the provider already produced before a Stop.
    """
    return {
        "max_retries": 0,
        "timeout": anthropic.Timeout(
            float(read_timeout_seconds), connect=_CONNECT_TIMEOUT_SECONDS
        ),
    }


_lock = threading.Lock()
_cached_client: anthropic.Anthropic | None = None
_cached_key: str = ""


def get_client() -> anthropic.Anthropic:
    """Return a client for the currently configured key, caching per-key."""
    global _cached_client, _cached_key
    key = load_api_key()
    if not key:
        raise MissingApiKeyError(
            "No Anthropic API key configured. Enter one in the app, set "
            "ANTHROPIC_API_KEY, or drop a key file in the config directory."
        )
    with _lock:
        if _cached_client is None or key != _cached_key:
            _cached_client = anthropic.Anthropic(api_key=key, **_client_options())
            _cached_key = key
        return _cached_client


def reset_client_cache() -> None:
    """Drop the cached client (tests, key rotation)."""
    global _cached_client, _cached_key
    with _lock:
        _cached_client = None
        _cached_key = ""


def build_probe_client(api_key: str) -> anthropic.Anthropic:
    """A throwaway (never-cached) client for validating a candidate key.

    The settings panel's "Test" flow constructs one of these with the
    candidate (or stored) key and makes the cheapest authenticated call it
    can. Kept out of the per-key cache so testing a bad key never poisons
    the live client.
    """
    return anthropic.Anthropic(api_key=api_key, **_client_options())
