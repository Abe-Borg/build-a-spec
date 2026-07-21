"""Sanitize assistant content before a ``pause_turn`` continuation resume.

Ported ≈verbatim from Claude-Spec-Critic ``src/core/resend_sanitizer.py``.

The ``web_fetch`` server tool returns fetched PDFs as ``document`` blocks
with a base64 ``application/pdf`` source inside the
``web_fetch_tool_result`` block. The pause_turn contract re-sends the
assistant content verbatim, which turns those fetched documents into
*inbound* PDFs on the continuation request — and the Messages API enforces
its per-request PDF page limit on inbound content regardless of the fact
that it produced the bytes itself. Fetching a large code document (a full
building code easily exceeds 600 pages) and then pausing would kill the
continuation with HTTP 400 without this guard.

:func:`sanitize_messages_for_resend` counts the pages of every fetched
base64 PDF across the conversation's assistant messages and, only when the
total exceeds the API limit, replaces the largest offenders' PDF payloads
with a short plain-text elision note until the total fits. A conversation
with no fetched PDFs — or whose PDFs fit — is returned as the *same list
object*, byte-identical. Never raises — an unparseable PDF is treated as
un-countable and elided first. Dependency: ``pypdf`` (lazy import).
"""
from __future__ import annotations

import base64
import copy
import dataclasses
import io
from typing import Any

# Mirror of the Messages API's per-request PDF page ceiling (total across
# every PDF in the request, not per document).
MAX_RESEND_PDF_PAGES = 600

_ELISION_NOTE = (
    "[Fetched PDF content elided before continuation resume: {detail} "
    "The API accepts at most {limit} PDF pages per request, so this "
    "document's pages could not be re-sent. Its findings from the earlier "
    "turn remain above; re-fetch the source URL if more content is needed.]"
)


def _get(node: Any, key: str) -> Any:
    if isinstance(node, dict):
        return node.get(key)
    return getattr(node, key, None)


def _find_pdf_sources(content: Any) -> list[Any]:
    """Base64-PDF ``source`` nodes inside fetched documents, document order."""
    sources: list[Any] = []
    for block in content or []:
        if _get(block, "type") != "web_fetch_tool_result":
            continue
        result = _get(block, "content")
        if result is None:
            continue
        document = _get(result, "content") or _get(result, "document")
        if document is None:
            continue
        source = _get(document, "source")
        if source is None:
            continue
        if (
            _get(source, "type") == "base64"
            and _get(source, "media_type") == "application/pdf"
            and _get(source, "data")
        ):
            sources.append(source)
    return sources


def _pdf_page_count(b64_data: Any) -> int | None:
    try:
        from pypdf import PdfReader

        raw = base64.b64decode(b64_data)
        return len(PdfReader(io.BytesIO(raw)).pages)
    except Exception:  # noqa: BLE001 — un-countable is a valid outcome
        return None


def _to_plain_block(block: Any) -> Any:
    """Best-effort conversion of a content block to a mutable plain dict."""
    if isinstance(block, dict):
        return copy.deepcopy(block)
    dump = getattr(block, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json", exclude_none=True)
        except TypeError:
            try:
                return dump()
            except Exception:  # noqa: BLE001
                return block
        except Exception:  # noqa: BLE001
            return block
    if dataclasses.is_dataclass(block) and not isinstance(block, type):
        try:
            return dataclasses.asdict(block)
        except Exception:  # noqa: BLE001
            return block
    return block


def _elide_source(source: dict, *, pages: int | None) -> None:
    detail = (
        f"this document is {pages} pages."
        if pages is not None
        else "this document's page count could not be determined."
    )
    note = _ELISION_NOTE.format(detail=detail, limit=MAX_RESEND_PDF_PAGES)
    source.clear()
    source.update({"type": "text", "media_type": "text/plain", "data": note})


def elide_all_pdf_sources(messages: list[dict]) -> list[dict]:
    """Elide EVERY fetched base64-PDF payload, regardless of page count.

    Used at interview-turn commit (Build-a-Spec native, not in the Spec
    Critic source): a fetched PDF that stays in committed history would be
    re-sent — and re-billed — on every later request, balloon the project
    file at save time, and count against the inbound PDF page limit
    forever after. The model's extracted findings live in its text; the
    source URL survives in the elision note's sibling blocks, so it can
    always re-fetch. Returns the same list object when nothing needed
    eliding.
    """
    found = False
    sanitized = list(messages)
    for msg_idx, message in enumerate(messages):
        if _get(message, "role") != "assistant":
            continue
        sources = _find_pdf_sources(_get(message, "content"))
        if not sources:
            continue
        found = True
        new_content = [
            _to_plain_block(b) for b in (_get(message, "content") or [])
        ]
        for source in _find_pdf_sources(new_content):
            if isinstance(source, dict):
                _elide_source(source, pages=_pdf_page_count(source.get("data")))
        sanitized[msg_idx] = {"role": "assistant", "content": new_content}
    return sanitized if found else messages


def sanitize_messages_for_resend(messages: list[dict]) -> list[dict]:
    """Ensure a continuation resume request fits the API's PDF page limit.

    Returns ``messages`` unchanged (same object) when nothing needs
    eliding. When eliding, returns a new list rebuilding only the affected
    messages; the input and underlying response objects are never mutated.
    """
    found: list[dict[str, Any]] = []
    for msg_idx, message in enumerate(messages):
        if _get(message, "role") != "assistant":
            continue
        for source_idx, source in enumerate(
            _find_pdf_sources(_get(message, "content"))
        ):
            found.append(
                {
                    "msg_idx": msg_idx,
                    "source_idx": source_idx,
                    "pages": _pdf_page_count(_get(source, "data")),
                }
            )
    if not found:
        return messages

    to_elide = [entry for entry in found if entry["pages"] is None]
    counted = [entry for entry in found if entry["pages"] is not None]
    counted_total = sum(entry["pages"] for entry in counted)
    for entry in sorted(counted, key=lambda e: e["pages"], reverse=True):
        if counted_total <= MAX_RESEND_PDF_PAGES:
            break
        to_elide.append(entry)
        counted_total -= entry["pages"]
    if not to_elide:
        return messages

    elide_by_msg: dict[int, dict[int, int | None]] = {}
    for entry in to_elide:
        elide_by_msg.setdefault(entry["msg_idx"], {})[entry["source_idx"]] = (
            entry["pages"]
        )

    sanitized = list(messages)
    for msg_idx, source_map in elide_by_msg.items():
        original = messages[msg_idx]
        new_content = [
            _to_plain_block(b) for b in (_get(original, "content") or [])
        ]
        for source_idx, source in enumerate(_find_pdf_sources(new_content)):
            if source_idx in source_map and isinstance(source, dict):
                _elide_source(source, pages=source_map[source_idx])
        sanitized[msg_idx] = {"role": "assistant", "content": new_content}
    return sanitized
