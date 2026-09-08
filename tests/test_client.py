"""The Anthropic client's transport knobs are explicit and read at build time.

Both clients this app builds — the cached live one and the throwaway probe —
used to take the SDK's silent defaults for its own retries and its request
timeout. They now pass ``settings.SDK_MAX_RETRIES`` / ``API_TIMEOUT_SECONDS``
(defaults identical to the SDK's), and the timeout is an ``anthropic.Timeout``
that keeps the SDK's 5 s CONNECT timeout beside the configurable read one: a
bare number would apply to connecting too, and a black-holed connect would sit
out the whole read budget before the first retry. Hermetic: constructing an
SDK client makes no network call, and conftest supplies a placeholder key.
"""
from __future__ import annotations

from backend import settings
from backend.llm import client as client_module


def _fresh_live_client():
    client_module.reset_client_cache()
    try:
        return client_module.get_client()
    finally:
        client_module.reset_client_cache()


def test_the_live_client_carries_the_configured_retries_and_timeout():
    client = _fresh_live_client()
    assert client.max_retries == settings.SDK_MAX_RETRIES
    assert client.timeout.read == float(settings.API_TIMEOUT_SECONDS)
    assert client.timeout.write == float(settings.API_TIMEOUT_SECONDS)
    assert client.timeout.connect == client_module._CONNECT_TIMEOUT_SECONDS


def test_the_probe_client_matches_the_live_one():
    probe = client_module.build_probe_client("sk-ant-probe-hermetic")
    live = _fresh_live_client()
    assert probe.max_retries == live.max_retries
    assert probe.timeout == live.timeout


def test_the_shipped_defaults_are_the_sdks_own():
    """Explicit, not different: the defaults reproduce what the SDK did before
    the knobs existed, so no request behaves differently on upgrade."""
    assert settings.SDK_MAX_RETRIES == 2
    assert settings.API_TIMEOUT_SECONDS == 600
    assert client_module._CONNECT_TIMEOUT_SECONDS == 5.0


def test_the_knobs_are_read_when_the_client_is_built(monkeypatch):
    monkeypatch.setattr(settings, "SDK_MAX_RETRIES", 0)
    monkeypatch.setattr(settings, "API_TIMEOUT_SECONDS", 45)
    client = _fresh_live_client()
    assert client.max_retries == 0
    assert client.timeout.read == 45.0
    assert client.timeout.connect == 5.0, "the connect timeout is never the read value"
