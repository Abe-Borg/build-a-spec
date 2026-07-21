"""Anthropic client factory.

One place constructs the SDK client so tests can monkeypatch a fake and a
later phase can layer capability config / retries the way Spec Critic's
``api_config.py`` does. The client is rebuilt when the stored key changes
(saving a key through the UI takes effect without a restart).
"""
from __future__ import annotations

import threading

import anthropic

from ..api_key_store import load_api_key


class MissingApiKeyError(RuntimeError):
    """No Anthropic API key is configured (env, keyring, or key file)."""


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
            _cached_client = anthropic.Anthropic(api_key=key)
            _cached_key = key
        return _cached_client


def reset_client_cache() -> None:
    """Drop the cached client (tests, key rotation)."""
    global _cached_client, _cached_key
    with _lock:
        _cached_client = None
        _cached_key = ""
