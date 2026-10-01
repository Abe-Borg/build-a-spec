"""Valid fetched PDFs exercise QC's real 600-page resend boundary.

The P55-6 tests use an unreadable PDF to trigger a deterministic edit. These
tests use page-counted PDFs, SDK response blocks, and the actual Opus 5.5
compliance lens. They verify request construction, not provider enforcement
of thinking signatures; the owner-run binding canary covers that separately.
"""
from __future__ import annotations

import base64
import copy
import io
from functools import lru_cache

import pytest
from anthropic.types import (
    RedactedThinkingBlock,
    ServerToolUseBlock,
    ThinkingBlock,
    WebFetchToolResultBlock,
)
from pypdf import PdfReader, PdfWriter

from backend import settings
from backend.research.resend_sanitizer import sanitize_messages_for_resend
from backend.research.schema import PRESERVED_THINKING_BETA
from tests.fakes import SequencedFakeClient, qc_verdict_response, research_response
from tests.test_qc_batch_verification import _one_finding_scripts
from tests.test_qc_batch_verification import _run as _run_batched_qc
from tests.test_retry_resume import _QcHarness


@pytest.fixture(autouse=True)
def _opus_55(monkeypatch):
    # Exercise the report's model even when the developer uses an override.
    monkeypatch.setattr(settings, "QC_MODEL", settings.MODEL_OPUS_55)


@lru_cache(maxsize=8)
def _pdf_data(pages: int) -> str:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    buffer = io.BytesIO()
    writer.write(buffer)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _opaque(label: str) -> list:
    return [
        ThinkingBlock(
            type="thinking", thinking=f"Reading {label}.\n  Café.\n",
            signature=f"opaque-signature-{label}==\n",
        ),
        RedactedThinkingBlock(type="redacted_thinking", data=f"opaque-{label}=="),
    ]


def _fetch(pages: int, label: str) -> list:
    tool_id = f"srvtoolu_pdf_{label}"
    url = f"https://example.gov/{label}.pdf"
    return [
        ServerToolUseBlock(
            type="server_tool_use", id=tool_id, name="web_fetch", input={"url": url}
        ),
        WebFetchToolResultBlock.model_validate({
            "type": "web_fetch_tool_result", "tool_use_id": tool_id,
            "content": {
                "type": "web_fetch_result", "url": url,
                "retrieved_at": "2026-09-30T12:00:00Z",
                "content": {
                    "type": "document", "title": f"{label} code book",
                    "source": {
                        "type": "base64", "media_type": "application/pdf",
                        "data": _pdf_data(pages),
                    },
                },
            },
        }),
    ]


def _pause(pages: int | None = None, *, label: str):
    content = _opaque(label)
    if pages is not None:
        content.extend(_fetch(pages, label))
    return research_response(
        items=None, extra_blocks=content, stop_reason="pause_turn",
        fetches=int(pages is not None), tokens={"input": 11, "output": 7},
    )


def _plain(block):
    return block.model_dump(mode="json", exclude_none=True) if hasattr(block, "model_dump") else block


def _source(messages: list[dict], label: str) -> dict:
    return next(
        _plain(block)["content"]["content"]["source"]
        for message in messages if message["role"] == "assistant"
        for block in message["content"]
        if _plain(block).get("tool_use_id") == f"srvtoolu_pdf_{label}"
    )


def _binding(request: dict) -> bool:
    return (
        request["thinking"].get("block_binding")
        == {"prefix_mismatch_behavior": "drop_block"}
        and PRESERVED_THINKING_BETA in request.get("extra_headers", {}).get("anthropic-beta", "")
    )


def _assert_opaque(messages: list[dict], label: str) -> None:
    expected = [_plain(block) for block in _opaque(label)]
    (content,) = [
        [_plain(block) for block in message["content"]]
        for message in messages if message["role"] == "assistant"
        if any(_plain(block).get("signature") == expected[0]["signature"] for block in message["content"])
    ]
    assert content[:2] == expected


@pytest.mark.parametrize("pages", [599, 600, 601])
def test_real_pdf_boundary_preserves_sdk_thinking_and_original_response(pages):
    assert len(PdfReader(io.BytesIO(base64.b64decode(_pdf_data(pages)))).pages) == pages
    original = [{"role": "user", "content": "Read the code book."}, {
        "role": "assistant", "content": [*_opaque("book"), *_fetch(pages, "book")],
    }]
    before = copy.deepcopy(original)
    sanitized = sanitize_messages_for_resend(original)
    assert original == before
    _assert_opaque(sanitized, "book")
    if pages <= 600:
        assert sanitized is original
        assert _source(sanitized, "book")["data"] == _pdf_data(pages)
    else:
        assert sanitized is not original
        assert sanitized[0] is original[0]
        source = _source(sanitized, "book")
        assert source["type"] == "text"
        assert "601 pages" in source["data"]
        result = sanitized[1]["content"][-1]
        assert result["content"]["url"] == "https://example.gov/book.pdf"
        assert result["content"]["content"]["title"] == "book code book"
        assert result["content"]["retrieved_at"] == "2026-09-30T12:00:00Z"
        assert result["tool_use_id"] == "srvtoolu_pdf_book"


@pytest.mark.parametrize("new_pages, edited", [(200, False), (201, True)])
def test_cumulative_boundary_can_edit_an_older_message_before_later_thinking(new_pages, edited):
    original = [
        {"role": "user", "content": "Read both code books."},
        {"role": "assistant", "content": [*_opaque("older"), *_fetch(400, "older")]},
        {"role": "assistant", "content": [*_opaque("later"), *_fetch(new_pages, "later")]},
    ]
    before = copy.deepcopy(original)
    sanitized = sanitize_messages_for_resend(original)
    assert original == before
    assert (sanitized is not original) is edited
    assert sanitized[2] is original[2]
    assert _source(sanitized, "later")["data"] == _pdf_data(new_pages)
    older = _source(sanitized, "older")
    assert older["type"] == ("text" if edited else "base64")
    if edited:
        assert "400 pages" in older["data"]
    _assert_opaque(sanitized, "older")
    _assert_opaque(sanitized, "later")


@pytest.mark.parametrize("cache", [False, True], ids=["no-tail", "cached-tail"])
@pytest.mark.parametrize("pages", [600, 601])
def test_streamed_compliance_resends_real_pdf_and_keeps_binding_for_later_rounds(pages, cache):
    harness = _QcHarness()
    first = _pause(pages, label="book")
    before = copy.deepcopy(first.content)
    call = harness.run([first, _pause(label="later"), harness.final()], continuation_cache=cache)
    assert call.status == "completed"
    assert len(call.requests) == call.api_requests == call.responses == 3
    assert call.input_tokens == 22
    assert first.content == before
    opening, *resumed = call.requests
    assert opening["model"] == settings.MODEL_OPUS_55
    assert any(tool.get("name") == "web_fetch" for tool in opening["tools"])
    assert not _binding(opening)
    for request in resumed:
        assert _binding(request) is (pages > 600)
        assert _source(request["messages"], "book")["type"] == ("text" if pages > 600 else "base64")
        _assert_opaque(request["messages"], "book")
        for key in ("model", "max_tokens", "system", "tools", "output_config"):
            assert request[key] == opening[key]
        if cache:
            assert request["cache_control"] == {"type": "ephemeral"}
        else:
            assert "cache_control" not in request
    _assert_opaque(resumed[-1]["messages"], "later")


def test_streamed_cumulative_edit_marks_later_thinking_and_subsequent_continuations():
    harness = _QcHarness()
    older, later = _pause(400, label="older"), _pause(201, label="later")
    before = copy.deepcopy([older.content, later.content])
    call = harness.run([older, later, _pause(label="last"), harness.final()])
    assert call.status == "completed"
    opening, under_limit, edited, sticky = call.requests
    assert not _binding(opening) and not _binding(under_limit)
    assert _source(under_limit["messages"], "older")["type"] == "base64"
    for request in (edited, sticky):
        assert _binding(request)
        assert _source(request["messages"], "older")["type"] == "text"
        assert _source(request["messages"], "later")["data"] == _pdf_data(201)
        _assert_opaque(request["messages"], "older")
        _assert_opaque(request["messages"], "later")
    assert [older.content, later.content] == before


def test_real_pdf_elision_also_marks_a_missing_output_tool_reminder():
    harness = _QcHarness()
    reply = _pause(601, label="book")
    reply.stop_reason = "end_turn"
    call = harness.run([reply, harness.final()])
    assert call.status == "completed"
    opening, reminder = call.requests
    assert not _binding(opening)
    assert _binding(reminder)
    assert reminder["messages"][-1]["role"] == "user"
    assert _source(reminder["messages"], "book")["type"] == "text"
    _assert_opaque(reminder["messages"], "book")


@pytest.mark.parametrize("pages, later_pages, source_types", [
    (600, None, ["base64", "base64"]),
    (601, None, ["text", "text"]),
    (400, 201, ["base64", "text"]),
])
def test_batch_continuations_sanitize_real_pdf_without_stream_only_binding_controls(pages, later_pages, source_types):
    # Code compliance always streams. This scripted verifier history tests
    # the separate batch sanitizer defensively, not a live verifier fetch.
    older, later = _pause(pages, label="book"), _pause(later_pages, label="later")
    before = copy.deepcopy([older.content, later.content])
    client = SequencedFakeClient(_one_finding_scripts(verdicts=[
        older, qc_verdict_response(True), later, qc_verdict_response(True),
    ]))
    result = _run_batched_qc(client)
    assert result.execution_status == "complete"
    rounds = client.batches.created
    assert len(rounds) == 3
    opening = rounds[0][0]["params"]
    for batch in rounds:
        for item in batch:
            request = item["params"]
            assert "block_binding" not in request["thinking"]
            assert "extra_headers" not in request
            assert request["model"] == settings.MODEL_OPUS_55
    for batch, source_type in zip(rounds[1:], source_types, strict=True):
        (item,) = batch
        request = item["params"]
        assert _source(request["messages"], "book")["type"] == source_type
        _assert_opaque(request["messages"], "book")
        for key in ("model", "max_tokens", "system", "tools", "output_config"):
            assert request[key] == opening[key]
    _assert_opaque(rounds[-1][0]["params"]["messages"], "later")
    if later_pages is not None:
        assert _source(rounds[-1][0]["params"]["messages"], "later")["data"] == _pdf_data(later_pages)
    assert [older.content, later.content] == before
