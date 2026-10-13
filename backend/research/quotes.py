"""Research item quotes: is the passage on a page the worker fetched?

The prompt-review follow-up, item 7 (2026-10-13). A research item says what
a requirement is in the worker's own words, and grounding proves only that a
cited URL was retrieved — so "a page said this" and "this is what the page
said" looked the same. Each item now carries a ``quote``: the passage the
worker copied, word for word, from a page it fetched. This module checks
that passage against the text of the pages the conversation actually
fetched, once, inside the round (the text exists nowhere after it).

What can be checked, and what cannot:

- **Fetched pages** (``web_fetch_tool_result`` → ``web_fetch_result`` →
  a ``document`` block): plain-text sources directly, PDFs through pypdf
  (bounded; an unreadable PDF is simply not checked). Fetched text is cut
  at the fetch tool's ``max_content_tokens``, so a quote from past the cut
  reads as not found — never as found.
- **Search results** are encrypted (``encrypted_content``): a quote taken
  from one cannot be checked, and the schema tells the worker not to quote
  one.

Only the item's own ACCEPTED sources are searched: a quote found on some
other page the conversation read does not support this item's citation.
The match is tolerant of what copying does to text — whitespace, letter
case, curly quotes and dashes, markdown emphasis — and of a gap the worker
marked with an ellipsis (each fragment must appear, in order). It is not
tolerant of paraphrase: that is the point.
"""
from __future__ import annotations

import base64
import binascii
import io
import logging
import re
from typing import Any, Iterable

from .grounding import normalize_url

_log = logging.getLogger("buildaspec.research")

# A PDF's pages are extracted only up to here: a code book fetched in full
# would otherwise cost seconds of text extraction for a sentence or two.
_PDF_MAX_PAGES = 300
# Fewer characters than this, after folding, is not a quote: "Sprinklers"
# appears on every page about sprinklers.
_MIN_QUOTE_CHARS = 20

_FOLD = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        " ": " ",
        "*": None,
        "_": None,
        "`": None,
        "#": None,
    }
)
_ELLIPSIS = re.compile(r"\.\.\.+|…")


def _attr(item: Any, name: str) -> Any:
    """Attribute lookup over SDK objects and plain dicts."""
    value = getattr(item, name, None)
    if value is None and isinstance(item, dict):
        value = item.get(name)
    return value


def _fold(text: str) -> str:
    return " ".join(str(text or "").translate(_FOLD).casefold().split())


def _pdf_text(data: str) -> str:
    try:
        raw = base64.b64decode(data, validate=False)
    except (binascii.Error, ValueError):
        return ""
    try:
        from pypdf import PdfReader  # lazy: only research rounds that quote a PDF

        reader = PdfReader(io.BytesIO(raw))
        parts: list[str] = []
        for index, page in enumerate(reader.pages):
            if index >= _PDF_MAX_PAGES:
                break
            parts.append(page.extract_text() or "")
        return "\n".join(parts)
    except Exception:  # noqa: BLE001 - an unreadable PDF is simply not checked
        _log.info("Research quote check: a fetched PDF could not be read.")
        return ""


def _document_text(document: Any) -> str:
    source = _attr(document, "source")
    kind = _attr(source, "type")
    data = _attr(source, "data")
    if kind == "text" and isinstance(data, str):
        return data
    if kind == "base64" and isinstance(data, str):
        if str(_attr(source, "media_type") or "").lower() == "application/pdf":
            return _pdf_text(data)
        return ""
    if kind == "content":
        blocks = _attr(source, "content") or []
        return "\n".join(
            str(_attr(block, "text") or "")
            for block in blocks
            if _attr(block, "type") == "text"
        )
    return ""


def fetched_page_texts(responses: Iterable[Any]) -> dict[str, str]:
    """``normalized url -> text`` for every page the responses fetched.

    The text is filed under the result's own URL AND the URL the paired
    ``server_tool_use`` asked for, when they differ (a redirect, a
    canonical URL): grounding accepts either, so an item citing the
    requested URL must find the page too (Codex review on PR #324). The
    pairing runs across every response, because a ``pause_turn`` can leave
    a call in one response and its result in the next. A page fetched twice
    keeps both texts. Error results and documents without readable text
    are skipped.
    """
    responses = list(responses)
    requested: dict[str, str] = {}
    for response in responses:
        for block in _attr(response, "content") or []:
            if (
                _attr(block, "type") == "server_tool_use"
                and _attr(block, "name") == "web_fetch"
            ):
                tool_input = _attr(block, "input") or {}
                url = tool_input.get("url") if isinstance(tool_input, dict) else None
                if url and _attr(block, "id"):
                    requested[str(_attr(block, "id"))] = str(url)
    texts: dict[str, str] = {}
    for response in responses:
        for block in _attr(response, "content") or []:
            if _attr(block, "type") != "web_fetch_tool_result":
                continue
            result = _attr(block, "content")
            if result is None or _attr(result, "type") == "web_fetch_tool_result_error":
                continue
            keys: list[str] = []
            for url in (
                _attr(result, "url"),
                requested.get(str(_attr(block, "tool_use_id") or "")),
            ):
                key = normalize_url(str(url)) or str(url).strip() if url else ""
                if key and key not in keys:
                    keys.append(key)
            document = _attr(result, "content") or _attr(result, "document")
            if not keys or document is None:
                continue
            text = _document_text(document)
            if not text.strip():
                continue
            for key in keys:
                texts[key] = f"{texts[key]}\n{text}" if key in texts else text
    return texts


def _quote_fragments(quote: str) -> list[str]:
    """The folded fragments of ``quote`` between its ellipses."""
    fragments = [
        _fold(part).strip(" \"'") for part in _ELLIPSIS.split(quote or "")
    ]
    return [fragment for fragment in fragments if fragment]


def _fragments_on(fragments: list[str], folded_page: str) -> bool:
    if (
        not folded_page
        or sum(len(fragment) for fragment in fragments) < _MIN_QUOTE_CHARS
    ):
        return False
    position = 0
    for fragment in fragments:
        index = folded_page.find(fragment, position)
        if index < 0:
            return False
        position = index + len(fragment)
    return True


def quote_found_in(quote: str, page_text: str) -> bool:
    """Whether ``quote`` is on the page, copying artefacts aside.

    Each fragment between ellipses must appear, in order; the fragments
    together must be at least :data:`_MIN_QUOTE_CHARS` long.
    """
    return _fragments_on(_quote_fragments(quote), _fold(page_text))


def verify_item_quotes(items: Iterable[Any], responses: Iterable[Any]) -> None:
    """Set each item's ``quote_source``: the accepted source whose fetched
    text contains its quote, or "" when none does (or it has no quote).

    Runs once per research area, after grounding has set
    ``accepted_sources``, over the responses of the conversation the model
    actually saw — the same set grounding pools.
    """
    pages: dict[str, str] | None = None
    folded: dict[str, str] = {}
    for item in items:
        item.quote_source = ""
        if not item.quote or not item.accepted_sources:
            continue
        if pages is None:
            pages = fetched_page_texts(responses)
        fragments = _quote_fragments(item.quote)
        for url in item.accepted_sources:
            key = normalize_url(url) or url.strip()
            if key not in pages:
                continue
            if key not in folded:
                folded[key] = _fold(pages[key])
            if _fragments_on(fragments, folded[key]):
                item.quote_source = url
                break
