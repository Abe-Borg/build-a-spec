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
from typing import Any, Mapping

from . import settings


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def usage_to_dict(usage: Any) -> dict[str, int]:
    """Flatten an SDK ``usage`` object (or a dict) into plain token counts.

    Mirrors the interview loop's ``_merge_usage`` for the research/audit
    call sites, which have a single ``response.usage`` to read.

    ``cache_creation_1h_input_tokens`` is the provider's
    ``usage.cache_creation.ephemeral_1h_input_tokens`` subtotal, flattened
    to a plain key so it rides every ledger, trace and audit record like
    any other count. It is a SLICE of ``cache_creation_input_tokens``, not
    an addition to it — see :func:`estimate_usage_cost`.
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
    creation = _get(usage, "cache_creation")
    one_hour = (
        _get(creation, "ephemeral_1h_input_tokens") if creation is not None else None
    )
    if isinstance(one_hour, (int, float)) and one_hour:
        out["cache_creation_1h_input_tokens"] = int(one_hour)
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
        "template": settings.INTERVIEW_MODEL,
    }


def _rates(model: str) -> dict[str, float]:
    return settings.PRICING.get(model, settings.PRICING[settings.MODEL_SONNET_5])


def cache_write_split(usage: Mapping[str, Any]) -> tuple[int, int]:
    """Split cache-creation tokens into (5-minute, 1-hour) disjoint slices.

    The provider's ``cache_creation_input_tokens`` is the TOTAL across TTL
    classes and already contains the one-hour subtotal; the two are priced
    at different rates, so they must be charged over disjoint slices or the
    one-hour tokens are billed twice.

    Live provider values are clamped into ``[0, total]`` rather than
    trusted: a malformed subtotal should skew an estimate, never invert it
    into a negative charge. Persisted audit records get no such courtesy —
    :mod:`backend.qc.engine` rejects an impossible subtotal as inconsistent
    accounting instead of quietly clamping a report's saved arithmetic.
    """
    total = usage.get("cache_creation_input_tokens", 0)
    total = int(total) if isinstance(total, (int, float)) else 0
    one_hour = usage.get("cache_creation_1h_input_tokens", 0)
    one_hour = int(one_hour) if isinstance(one_hour, (int, float)) else 0
    one_hour = max(0, min(one_hour, max(0, total)))
    return max(0, total) - one_hour, one_hour


def estimate_usage_cost(model: str, usage: dict[str, int]) -> float:
    """Estimate one recorded run's cost from its model and usage snapshot.

    The result is deliberately labeled an estimate everywhere it is exposed:
    it uses the app's list-price table, while the provider invoice remains the
    authority. Thinking tokens already live inside ``output_tokens`` and are
    therefore not added a second time — and neither is the one-hour cache
    subtotal, which lives inside the cache-creation total.
    """
    rates = _rates(model)
    five_minute, one_hour = cache_write_split(usage)
    return round(
        usage.get("input_tokens", 0) * rates["input"]
        + usage.get("output_tokens", 0) * rates["output"]
        + usage.get("cache_read_input_tokens", 0) * rates["cache_read"]
        + five_minute * rates["cache_write"]
        + one_hour * rates.get("cache_write_1h", rates["cache_write"])
        + usage.get("web_search_requests", 0) * settings.WEB_SEARCH_COST,
        6,
    )


def usage_pricing_snapshot(model: str) -> dict[str, Any]:
    """Return the exact configured rates used to estimate a run's cost."""
    rate_model = (
        model if model in settings.PRICING else settings.MODEL_SONNET_5
    )
    rates = _rates(model)
    return {
        "currency": "USD",
        "requested_model": model,
        "rate_model": rate_model,
        "used_fallback_rate": rate_model != model,
        "rates_per_token": dict(rates),
        "web_search_per_request": settings.WEB_SEARCH_COST,
        "web_fetch_per_request": 0.0,
        "thinking_token_treatment": (
            "Thinking tokens are included in output_tokens and are not "
            "charged a second time."
        ),
        "cache_write_treatment": (
            "Cache creation is priced per TTL class. The provider's "
            "one-hour subtotal (cache_creation_1h_input_tokens) is included "
            "in cache_creation_input_tokens and is charged once, at the "
            "cache_write_1h rate; the remainder is charged at the "
            "five-minute cache_write rate."
        ),
        "authority": (
            "Application list-price estimate; provider billing records are "
            "authoritative."
        ),
    }


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
        return estimate_usage_cost(
            _category_models().get(category, settings.INTERVIEW_MODEL), bucket
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

    def load_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        """Restore the additive counters used by a detached workspace clone."""
        raw_categories = snapshot.get("categories")
        raw_turns = snapshot.get("turns")
        categories: dict[str, dict[str, int]] = {}
        if isinstance(raw_categories, Mapping):
            for category, raw_bucket in raw_categories.items():
                if not isinstance(category, str) or not isinstance(raw_bucket, Mapping):
                    continue
                bucket = {
                    str(key): int(value)
                    for key, value in raw_bucket.items()
                    if isinstance(key, str)
                    and isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and value >= 0
                }
                if bucket:
                    categories[category] = bucket
        turns = (
            int(raw_turns)
            if isinstance(raw_turns, (int, float))
            and not isinstance(raw_turns, bool)
            and raw_turns >= 0
            else 0
        )
        with self._lock:
            self.categories = categories
            self.turns = turns

    def merge_delta(
        self,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
    ) -> None:
        """Merge non-negative spend accrued by a disposable child workspace."""
        before_categories = before.get("categories")
        after_categories = after.get("categories")
        if not isinstance(before_categories, Mapping):
            before_categories = {}
        if not isinstance(after_categories, Mapping):
            after_categories = {}
        deltas: dict[str, dict[str, int]] = {}
        for category, raw_bucket in after_categories.items():
            if not isinstance(category, str) or not isinstance(raw_bucket, Mapping):
                continue
            prior = before_categories.get(category)
            if not isinstance(prior, Mapping):
                prior = {}
            bucket: dict[str, int] = {}
            for key, raw_value in raw_bucket.items():
                if (
                    not isinstance(key, str)
                    or not isinstance(raw_value, (int, float))
                    or isinstance(raw_value, bool)
                ):
                    continue
                old = prior.get(key, 0)
                old_value = (
                    int(old)
                    if isinstance(old, (int, float)) and not isinstance(old, bool)
                    else 0
                )
                delta = max(0, int(raw_value) - old_value)
                if delta:
                    bucket[key] = delta
            if bucket:
                deltas[category] = bucket
        before_turns = before.get("turns", 0)
        after_turns = after.get("turns", 0)
        turn_delta = max(
            0,
            (int(after_turns) if isinstance(after_turns, (int, float)) else 0)
            - (int(before_turns) if isinstance(before_turns, (int, float)) else 0),
        )
        with self._lock:
            for category, delta_bucket in deltas.items():
                bucket = self.categories.setdefault(category, {})
                for key, value in delta_bucket.items():
                    bucket[key] = bucket.get(key, 0) + value
            self.turns += turn_delta
