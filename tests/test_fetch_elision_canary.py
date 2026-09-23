"""The Phase 2 live canary, checked without a network or a key.

``tools/fetch_elision_canary.py --run`` is one of the two paid exceptions
CLAUDE.md allows. These tests never let it reach a provider: they pin that
it sends nothing without ``--run``, that the one request it would send is
the shape a real commit produces (page text replaced, citation into the
original kept, the production chat tools), and that it reports acceptance
and refusal plainly.
"""
from __future__ import annotations

import pytest

from backend import settings
from backend.llm import conversation
from backend.llm.history_hygiene import FETCHED_PAGE_NOTE
from tests.fakes import FakeClient, bad_request, text_turn
from tools import fetch_elision_canary as canary


def _fetch_document(request: dict) -> dict:
    for message in request["messages"]:
        for block in message["content"]:
            if block.get("type") == "web_fetch_tool_result":
                return block["content"]["content"]
    raise AssertionError("no fetched page in the canary request")


def _citation(request: dict) -> dict:
    for message in request["messages"]:
        for block in message["content"]:
            if block.get("citations"):
                return block["citations"][0]
    raise AssertionError("no citation in the canary request")


def test_the_canary_sends_nothing_without_run(monkeypatch, capsys):
    def no_client():
        raise AssertionError("the canary built a client without --run")

    monkeypatch.setattr(canary, "get_client", no_client)
    assert canary.main([]) == 0
    assert "No request sent" in capsys.readouterr().out


def test_the_canary_request_is_the_shape_a_commit_produces():
    built = canary.build_request(max_tokens=512)
    request = built.request

    document = _fetch_document(request)
    assert document["source"]["data"] == FETCHED_PAGE_NOTE.format(url=canary._URL)
    assert document["title"] == canary._TITLE
    assert document["citations"] == {"enabled": True}
    citation = _citation(request)
    assert citation["type"] == "char_location"
    assert citation["cited_text"] == canary._CITED
    # The point of the check: the citation now points past the end of the
    # text the document still holds.
    assert citation["start_char_index"] > built.saved_chars
    assert built.saved_chars < built.page_chars

    assert request["model"] == settings.INTERVIEW_MODEL
    assert request["tools"] == conversation._chat_tools()
    fetch_tool = next(t for t in request["tools"] if t["name"] == "web_fetch")
    assert fetch_tool["citations"] == {"enabled": True}
    assert request["thinking"] == {"type": "adaptive"}
    assert request["output_config"] == {"effort": "low"}
    assert [m["role"] for m in request["messages"]] == ["user", "assistant", "user"]

    control = canary.build_request(max_tokens=512, control=True)
    assert _fetch_document(control.request)["source"]["data"] == canary._PAGE
    assert control.saved_chars == control.page_chars


def test_the_canary_refuses_to_send_an_unelided_page_as_elided(monkeypatch):
    # If commit ever stopped trimming page text, a pass would prove nothing.
    monkeypatch.setattr(conversation, "elide_fetched_page_text", lambda messages: messages)
    with pytest.raises(AssertionError, match="did not replace"):
        canary.build_request(max_tokens=512)


def test_the_canary_reports_acceptance(monkeypatch, capsys):
    fake = FakeClient([text_turn(["OK"])])
    monkeypatch.setattr(canary, "get_client", lambda: fake)

    assert canary.main(["--run"]) == 0
    assert len(fake.messages.requests) == 1
    assert fake.messages.requests[0] == canary.build_request(max_tokens=1024).request
    assert "Fetch elision canary passed" in capsys.readouterr().out


def test_the_canary_reports_a_refusal_plainly(monkeypatch, capsys):
    fake = FakeClient([bad_request("citation out of range")])
    monkeypatch.setattr(canary, "get_client", lambda: fake)

    assert canary.main(["--run"]) == 1
    err = capsys.readouterr().err
    assert "citation out of range" in err
    assert "REFUSED" in err and "--control" in err


def test_the_canary_bounds_its_output_ceiling(monkeypatch):
    monkeypatch.setattr(canary, "get_client", lambda: FakeClient([]))
    assert canary.main(["--run", "--max-tokens", "100"]) == 2
