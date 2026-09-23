"""The Phase 2 live canary, checked without a network or a key.

``tools/fetch_elision_canary.py --run`` is one of the two paid exceptions
CLAUDE.md allows. These tests never let it reach a provider: they pin that
it sends nothing without ``--run``, that the one request it would send is
the shape a real commit produces (page text replaced by a note carrying the
passage the reply quoted, the citation into the original text gone, the
production chat tools, the same repair every chat request takes), and that
it reports acceptance and refusal plainly. Its first run (2026-09-23) was
refused on the earlier shape, which kept that citation.
"""
from __future__ import annotations

import copy

import pytest

from backend import settings
from backend.llm import conversation
from backend.llm.history_hygiene import (
    FETCHED_PAGE_NOTE,
    FETCHED_PAGE_NOTE_PREFIX,
    QUOTED_PASSAGES_HEADER,
)
from tests.fakes import FakeClient, bad_request, text_turn
from tools import fetch_elision_canary as canary


def _fetch_document(request: dict) -> dict:
    for message in request["messages"]:
        for block in message["content"]:
            if block.get("type") == "web_fetch_tool_result":
                return block["content"]["content"]
    raise AssertionError("no fetched page in the canary request")


def _citations(request: dict) -> list[dict]:
    return [
        citation
        for message in request["messages"]
        for block in message["content"]
        for citation in block.get("citations") or []
    ]


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
    assert document["source"]["data"] == (
        f"{FETCHED_PAGE_NOTE.format(url=canary._URL)}\n{QUOTED_PASSAGES_HEADER}\n"
        f'- "{canary._CITED}"'
    )
    assert document["title"] == canary._TITLE
    assert document["citations"] == {"enabled": True}
    # The point of the check: the citation the first run was refused on
    # (its span past the end of the note) is gone, and what it quoted is in
    # the note, where the model can still read it.
    assert _citations(request) == []
    assert built.cited_start > built.saved_chars
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
    (citation,) = _citations(control.request)
    assert citation["cited_text"] == canary._CITED
    assert citation["start_char_index"] == control.cited_start


def test_the_canary_forces_the_trim_on_whatever_the_switch_says(monkeypatch):
    """1.21.0 ships the page-text trim switched off until this canary passes,
    so the canary must test the shape turning it on would produce: the
    switch (``settings.ELIDE_FETCHED_PAGE_TEXT``) never reaches it."""
    monkeypatch.setattr(settings, "ELIDE_FETCHED_PAGE_TEXT", False)
    built = canary.build_request(max_tokens=512)
    assert _fetch_document(built.request)["source"]["data"].startswith(
        FETCHED_PAGE_NOTE_PREFIX
    )


def test_the_canary_refuses_to_send_an_unelided_page_as_elided(monkeypatch):
    # If commit ever stopped trimming page text, a pass would prove nothing.
    monkeypatch.setattr(
        conversation, "elide_fetched_page_text", lambda messages, **_kwargs: messages
    )
    with pytest.raises(AssertionError, match="did not replace"):
        canary.build_request(max_tokens=512)


def test_the_canary_refuses_to_send_the_shape_its_first_run_was_refused_on(
    monkeypatch,
):
    """A commit that trimmed the page but kept the citation into it is the
    shape the provider refused on 2026-09-23. Sending it again would only
    repeat that answer, so the canary will not call it the new shape."""

    def phase_two_trim(messages, **_kwargs):
        trimmed = []
        for message in messages:
            content = []
            for block in message.get("content") or []:
                if block.get("type") == "web_fetch_tool_result":
                    block = copy.deepcopy(block)
                    block["content"]["content"]["source"]["data"] = FETCHED_PAGE_NOTE.format(
                        url=canary._URL
                    )
                content.append(block)
            trimmed.append({**message, "content": content})
        return trimmed

    monkeypatch.setattr(conversation, "elide_fetched_page_text", phase_two_trim)
    with pytest.raises(AssertionError, match="did not fold"):
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
