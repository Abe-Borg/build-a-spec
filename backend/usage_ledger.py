"""Session-scoped billed-usage ledger (WI4 cost & usage meter).

Answers exactly one question — *what has THIS session spent* — across the
interview, research, and audit surfaces (Batch 4 adds ``qc``). Reset and
project load clear it; the trace files remain the permanent, cross-session
record. Deliberately NOT persisted in project files: a resumed project's
meter starts at zero for the new session.

Cost is an *estimate* from list pricing (``settings.PRICING``), labeled as
such in the UI. Thinking tokens are billed as output tokens and are already
counted inside ``output_tokens`` — they are surfaced for visibility, never
added to the dollar estimate a second time.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from . import settings


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def usage_to_dict(usage: Any) -> dict[str, int]:
    """Flatten an SDK ``usage`` object (or a dict) into plain token counts.

    Mirrors the interview loop's ``_merge_usage`` for the research/audit
    call sites, which have a single ``response.usage`` to read.
    """
    out: dict[str, int] = {}
    if usage is None:
        return out
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    ):
        value = _get(usage, key)
        if isinstance(value, (int, float)) and value:
            out[key] = int(value)
    details = _get(usage, "output_tokens_details")
    thinking = _get(details, "thinking_tokens") if details is not None else None
    if isinstance(thinking, (int, float)) and thinking:
        out["thinking_tokens"] = int(thinking)
    server = _get(usage, "server_tool_use")
    for key in ("web_search_requests", "web_fetch_requests"):
        value = _get(server, key) if server is not None else None
        if isinstance(value, (int, float)) and value:
            out[key] = int(value)
    return out


# Which model each spend category runs on — resolved live so an env override
# (e.g. BUILD_A_SPEC_RESEARCH_MODEL) is priced correctly.
def _category_models() -> dict[str, str]:
    return {
        "interview": settings.INTERVIEW_MODEL,
        "research": settings.RESEARCH_MODEL,
        "audit": settings.RESEARCH_MODEL,
        "qc": settings.QC_MODEL,
    }


def _rates(model: str) -> dict[str, float]:
    return settings.PRICING.get(model, settings.PRICING[settings.MODEL_SONNET_5])


@dataclass
class UsageLedger:
    """Per-category billed-usage accumulator + a turn counter.

    Thread-safe: research and audit fold their run totals in from daemon
    threads while the API layer reads the snapshot on request threads.
    """

    categories: dict[str, dict[str, int]] = field(default_factory=dict)
    turns: int = 0
    _lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False
    )

    def add(self, category: str, usage: Any, *, count_turn: bool = False) -> None:
        """Fold one call's usage into ``category``; optionally count a turn.

        ``usage`` may be a plain dict (the interview's aggregated totals) or
        an SDK usage object (research/audit). Empty usage is a no-op — and
        does not count a turn, so a no-key failure never inflates the count.
        """
        data = usage if isinstance(usage, dict) else usage_to_dict(usage)
        data = {k: int(v) for k, v in data.items() if isinstance(v, (int, float)) and v}
        if not data:
            return
        with self._lock:
            bucket = self.categories.setdefault(category, {})
            for key, value in data.items():
                bucket[key] = bucket.get(key, 0) + value
            if count_turn:
                self.turns += 1

    def _totals(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for bucket in self.categories.values():
            for key, value in bucket.items():
                out[key] = out.get(key, 0) + value
        return out

    def _estimate_category(self, category: str, bucket: dict[str, int]) -> float:
        rates = _rates(_category_models().get(category, settings.INTERVIEW_MODEL))
        return (
            bucket.get("input_tokens", 0) * rates["input"]
            + bucket.get("output_tokens", 0) * rates["output"]
            + bucket.get("cache_read_input_tokens", 0) * rates["cache_read"]
            + bucket.get("cache_creation_input_tokens", 0) * rates["cache_write"]
            + bucket.get("web_search_requests", 0) * settings.WEB_SEARCH_COST
        )

    def _estimated_cost(self) -> dict[str, Any]:
        by_category = {
            cat: round(self._estimate_category(cat, bucket), 6)
            for cat, bucket in self.categories.items()
        }
        return {
            "by_category": by_category,
            "total": round(sum(by_category.values()), 6),
        }

    def _cache_saved(self) -> float:
        """Estimated savings from cache reads vs paying full input price."""
        saved = 0.0
        for category, bucket in self.categories.items():
            rates = _rates(
                _category_models().get(category, settings.INTERVIEW_MODEL)
            )
            saved += bucket.get("cache_read_input_tokens", 0) * (
                rates["input"] - rates["cache_read"]
            )
        return round(saved, 6)

    def snapshot(self) -> dict[str, Any]:
        """The UI-shaped payload for ``GET /api/usage``."""
        with self._lock:
            return {
                "categories": {k: dict(v) for k, v in self.categories.items()},
                "totals": self._totals(),
                "turns": self.turns,
                "estimated_cost_usd": self._estimated_cost(),
                "cache_saved_usd": self._cache_saved(),
            }

    def reset(self) -> None:
        with self._lock:
            self.categories = {}
            self.turns = 0
