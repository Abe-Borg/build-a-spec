"""The app's client speaks API version 2026-10-21 (anthropic 2.x).

anthropic 2.0.0 is the first Python SDK release that sends
``anthropic-version: 2026-10-21`` by default, and it sends the API key as
``Authorization: Bearer <key>`` where 1.x sent ``X-Api-Key``. The app sets
neither header itself: the version is the SDK's default and the key is the
one credential ``backend.llm.client`` passes. These tests send one request
through each client the app builds into an in-process transport and read the
headers that went out — no network, no real key. The request rules the new
version adds are pinned in ``tests/fakes.request_shape_problems``.
"""
from __future__ import annotations

import json

import anthropic
import httpx2
import pytest

from backend.llm import client as llm_client

_KEY = "test-key-hermetic-wire"


def _probe(client: anthropic.Anthropic) -> httpx2.Request:
    """Send one token count through ``client`` and return what went out."""
    sent: list[httpx2.Request] = []

    def transport(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(200, json={"input_tokens": 7})

    wired = client.with_options(
        http_client=anthropic.DefaultHttpxClient(
            transport=httpx2.MockTransport(transport)
        )
    )
    counted = wired.messages.count_tokens(
        model="claude-opus-5-5",
        messages=[{"role": "user", "content": "Hello"}],
    )
    assert counted.input_tokens == 7
    (request,) = sent
    return request


@pytest.fixture()
def live_client(monkeypatch):
    """The cached chat/research/QC client, built for ``_KEY``."""
    monkeypatch.setattr(llm_client, "load_api_key", lambda: _KEY)
    llm_client.reset_client_cache()
    yield llm_client.get_client()
    llm_client.reset_client_cache()


@pytest.mark.parametrize("factory", ["live", "probe"])
def test_every_client_the_app_builds_sends_2026_10_21_and_a_bearer_key(
    factory, request
):
    client = (
        request.getfixturevalue("live_client")
        if factory == "live"
        else llm_client.build_probe_client(_KEY)
    )
    sent = _probe(client)
    assert sent.headers["anthropic-version"] == "2026-10-21"
    assert sent.headers["authorization"] == f"Bearer {_KEY}"
    assert "x-api-key" not in sent.headers
    # The body is the request as given: no sampling parameter rides along.
    body = json.loads(sent.content)
    assert body == {
        "model": "claude-opus-5-5",
        "messages": [{"role": "user", "content": "Hello"}],
    }


def test_the_bounded_override_keeps_the_version_and_the_key(live_client):
    """Final QC's settlement window narrows a client with ``with_options``;
    the narrowed copy is still one credential on the same version."""
    bounded = live_client.with_options(**llm_client.bounded_request_options(30))
    sent = _probe(bounded)
    assert sent.headers["anthropic-version"] == "2026-10-21"
    assert sent.headers["authorization"] == f"Bearer {_KEY}"
    assert "x-api-key" not in sent.headers
