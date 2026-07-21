"""URL grounding and web-tool evidence collection for research.

Two Claude-Spec-Critic sources merged, per the port plan ("extract the
needed helpers rather than dragging the whole verifier over"):

- ``src/verification/source_grounding.py`` — ported ≈verbatim:
  :func:`normalize_url`, :func:`validate_cited_sources`,
  :class:`SearchedSource`, :func:`dedupe_searched_sources`. Grounding is
  the accepted-vs-cited split: a cited URL is *accepted* only when it
  matches (post-normalization) a URL the server tools actually retrieved
  in the conversation. Grounding proves retrieval, not truth — an item
  without any accepted citation renders ``[UNVERIFIED]``.
- ``src/verification/verifier.py`` — the evidence collectors
  (:func:`collect_search_evidence_detailed`,
  :func:`collect_fetch_evidence_detailed`), the per-message server-tool
  use counters, and stop-reason classification, extracted with their
  dict-or-SDK-object duck typing intact.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# ---------------------------------------------------------------------------
# URL normalization
# ---------------------------------------------------------------------------

# Tracking parameters that mean the same URL when stripped. Conservative —
# too aggressive a filter could collapse two semantically different pages.
_TRACKING_QUERY_KEYS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "gclid", "fbclid", "mc_cid", "mc_eid", "msclkid",
    "ref", "ref_src", "ref_url", "_ga", "_gl",
})

_EQUIVALENT_SCHEMES = frozenset({"http", "https"})


def _strip_default_port(host: str, scheme: str) -> str:
    if not host or ":" not in host:
        return host
    bare, _, port = host.rpartition(":")
    if not bare:
        return host
    if scheme == "https" and port in ("80", "443"):
        return bare
    if scheme == "http" and port == "80":
        return bare
    return host


def _strip_trailing_slash(path: str) -> str:
    if path == "/":
        return ""
    if len(path) > 1 and path.endswith("/"):
        return path[:-1]
    return path


def normalize_url(url: str | None) -> str:
    """Canonical form of ``url`` for grounding comparison.

    http/https fold to https; host lowercased, default ports and trailing
    host dot dropped; single trailing slash removed; query params decoded,
    sorted, tracking params dropped; fragment dropped. Falsy/non-string
    input returns ``""``.
    """
    if not url or not isinstance(url, str):
        return ""
    cleaned = url.strip()
    if not cleaned:
        return ""
    if cleaned.startswith("<") and cleaned.endswith(">"):
        cleaned = cleaned[1:-1].strip()
    trailing_punct = set(",;)]}.'\"")
    while cleaned and cleaned[-1] in trailing_punct:
        cleaned = cleaned[:-1].rstrip()
    if not cleaned:
        return ""
    try:
        parts = urlsplit(cleaned)
    except ValueError:
        return ""
    scheme = (parts.scheme or "").lower()
    if not scheme and not parts.netloc and parts.path:
        return normalize_url("https://" + cleaned)
    if scheme in _EQUIVALENT_SCHEMES:
        scheme = "https"
    host = (parts.netloc or "").lower()
    if "@" in host:
        host = host.split("@", 1)[1]
    host = host.rstrip(".")
    host = _strip_default_port(host, scheme)
    path = _strip_trailing_slash(parts.path or "")
    raw_query = parts.query or ""
    if raw_query:
        items = parse_qsl(raw_query, keep_blank_values=True)
        kept = [(k, v) for k, v in items if k.lower() not in _TRACKING_QUERY_KEYS]
        kept.sort()
        query = urlencode(kept)
    else:
        query = ""
    return urlunsplit((scheme, host, path, query, ""))


# Rejection reason sentinels (surface in the research drawer / diagnostics).
REJECT_UNGROUNDED = "ungrounded"
REJECT_MALFORMED = "malformed"
REJECT_EMPTY = "empty"


@dataclass(frozen=True)
class CitedSourceVerdict:
    url: str
    normalized: str
    accepted: bool
    reason: str = ""


@dataclass(frozen=True)
class SourceGroundingOutcome:
    accepted: tuple[str, ...] = ()
    rejected: tuple[dict, ...] = ()
    verdicts: tuple[CitedSourceVerdict, ...] = ()

    def has_any_grounded_citation(self) -> bool:
        return len(self.accepted) > 0


def validate_cited_sources(
    cited: Iterable[str] | None,
    searched: Iterable[str] | None,
) -> SourceGroundingOutcome:
    """Validate model-cited URLs against the URLs the API actually retrieved.

    Accepted URLs are returned in their original (model-supplied) form so
    displays render the model's exact citation; normalization is an
    internal comparison detail. With no retrieved URLs at all, every cited
    URL rejects as ``ungrounded``.
    """
    cited_list = list(cited or [])
    searched_set = {normalize_url(u) for u in (searched or []) if normalize_url(u)}

    accepted_original: list[str] = []
    rejected_records: list[dict] = []
    verdicts: list[CitedSourceVerdict] = []
    seen_normalized: set[str] = set()

    for raw in cited_list:
        if not isinstance(raw, str) or not raw.strip():
            verdicts.append(
                CitedSourceVerdict(
                    url=str(raw or ""), normalized="", accepted=False,
                    reason=REJECT_EMPTY,
                )
            )
            rejected_records.append({"url": str(raw or ""), "reason": REJECT_EMPTY})
            continue
        normalized = normalize_url(raw)
        if not normalized:
            verdicts.append(
                CitedSourceVerdict(
                    url=raw, normalized="", accepted=False, reason=REJECT_MALFORMED
                )
            )
            rejected_records.append({"url": raw, "reason": REJECT_MALFORMED})
            continue
        if normalized in searched_set:
            if normalized in seen_normalized:
                continue
            seen_normalized.add(normalized)
            accepted_original.append(raw)
            verdicts.append(
                CitedSourceVerdict(
                    url=raw, normalized=normalized, accepted=True
                )
            )
        else:
            verdicts.append(
                CitedSourceVerdict(
                    url=raw, normalized=normalized, accepted=False,
                    reason=REJECT_UNGROUNDED,
                )
            )
            rejected_records.append({"url": raw, "reason": REJECT_UNGROUNDED})

    return SourceGroundingOutcome(
        accepted=tuple(accepted_original),
        rejected=tuple(rejected_records),
        verdicts=tuple(verdicts),
    )


@dataclass(frozen=True)
class SearchedSource:
    """A single URL a web server tool actually retrieved (+ title if any)."""

    url: str
    title: str = ""

    @property
    def normalized(self) -> str:
        return normalize_url(self.url)


def dedupe_searched_sources(
    sources: Iterable["SearchedSource | dict | str | None"],
) -> list[SearchedSource]:
    """Collapse equivalent retrieved URLs to one record (first wins)."""
    seen: dict[str, SearchedSource] = {}
    ordered: list[SearchedSource] = []
    for raw in sources or []:
        if raw is None:
            continue
        if isinstance(raw, SearchedSource):
            record = raw
        elif isinstance(raw, dict):
            url = str(raw.get("url") or "")
            if not url:
                continue
            record = SearchedSource(url=url, title=str(raw.get("title") or ""))
        elif isinstance(raw, str):
            if not raw.strip():
                continue
            record = SearchedSource(url=raw, title="")
        else:
            continue
        key = record.normalized
        if not key or key in seen:
            continue
        seen[key] = record
        ordered.append(record)
    return ordered


# ---------------------------------------------------------------------------
# Message evidence collectors (extracted from verifier.py)
# ---------------------------------------------------------------------------


def _maybe_attr(item, name: str):
    """Attribute lookup over SDK Pydantic objects and plain dicts."""
    value = getattr(item, name, None)
    if value is None and isinstance(item, dict):
        value = item.get(name)
    return value


def collect_search_evidence_detailed(message) -> tuple[list[SearchedSource], int, int]:
    """Pull searched sources out of a message's content blocks.

    Returns ``(sources, success_block_count, error_item_count)``. Only
    blocks containing at least one usable result count as successful — an
    error-only block does not.
    """
    detailed: list[SearchedSource] = []
    success_count = 0
    error_count = 0
    for block in _maybe_attr(message, "content") or []:
        block_type = _maybe_attr(block, "type")
        if block_type == "web_search_tool_result":
            block_content = _maybe_attr(block, "content")
            if block_content is None:
                block_content = _maybe_attr(block, "results")
            if isinstance(block_content, list):
                block_had_valid_result = False
                for item in block_content:
                    item_type = _maybe_attr(item, "type")
                    if item_type == "web_search_tool_result_error":
                        error_count += 1
                        continue
                    if item_type not in (None, "web_search_result"):
                        continue
                    block_had_valid_result = True
                    url = _maybe_attr(item, "url")
                    if url:
                        title = _maybe_attr(item, "title") or ""
                        detailed.append(
                            SearchedSource(url=str(url), title=str(title))
                        )
                if block_had_valid_result:
                    success_count += 1
            elif _maybe_attr(block_content, "type") == "web_search_tool_result_error":
                error_count += 1
        elif block_type == "web_search_tool_result_error":
            error_count += 1
    return detailed, success_count, error_count


def collect_fetch_evidence_detailed(message) -> tuple[list[SearchedSource], int, int]:
    """Pull fetched URLs out of a message's content blocks.

    The fetched URL is what the model passed to ``web_fetch`` (the paired
    ``server_tool_use`` block's ``input.url``); result documents that echo
    a URL are picked up too.
    """
    detailed: list[SearchedSource] = []
    success_count = 0
    error_count = 0
    for block in _maybe_attr(message, "content") or []:
        block_type = _maybe_attr(block, "type")
        if block_type == "server_tool_use":
            if _maybe_attr(block, "name") == "web_fetch":
                tool_input = _maybe_attr(block, "input") or {}
                fetched_url = (
                    tool_input.get("url") if isinstance(tool_input, dict) else None
                )
                if fetched_url:
                    detailed.append(SearchedSource(url=str(fetched_url)))
        elif block_type == "web_fetch_tool_result":
            block_content = _maybe_attr(block, "content")
            if isinstance(block_content, dict):
                inner_type = block_content.get("type") or _maybe_attr(
                    block_content, "type"
                )
                if inner_type == "web_fetch_tool_result_error":
                    error_count += 1
                else:
                    success_count += 1
                    doc = block_content.get("document")
                    url = doc.get("url") if isinstance(doc, dict) else None
                    if not url:
                        url = block_content.get("url")
                    if url and not any(s.url == str(url) for s in detailed):
                        detailed.append(SearchedSource(url=str(url)))
            elif _maybe_attr(block_content, "type") == "web_fetch_tool_result_error":
                error_count += 1
            elif block_content is not None:
                success_count += 1
        elif block_type == "web_fetch_tool_result_error":
            error_count += 1
    return detailed, success_count, error_count


def web_search_count(message) -> int:
    usage = getattr(message, "usage", None)
    server_tool_use = getattr(usage, "server_tool_use", None) if usage else None
    return int(getattr(server_tool_use, "web_search_requests", 0) or 0)


def web_fetch_count(message) -> int:
    usage = getattr(message, "usage", None)
    server_tool_use = getattr(usage, "server_tool_use", None) if usage else None
    return int(getattr(server_tool_use, "web_fetch_requests", 0) or 0)


# ---------------------------------------------------------------------------
# Stop-reason classification
# ---------------------------------------------------------------------------

STOP_CLASS_COMPLETE = "complete"
STOP_CLASS_PAUSE = "pause"
STOP_CLASS_INCOMPLETE = "incomplete"


def classify_stop_reason(stop_reason) -> str:
    """``end_turn``/``tool_use`` → complete; ``pause_turn`` → pause; else incomplete."""
    if stop_reason in ("end_turn", "tool_use"):
        return STOP_CLASS_COMPLETE
    if stop_reason == "pause_turn":
        return STOP_CLASS_PAUSE
    return STOP_CLASS_INCOMPLETE
