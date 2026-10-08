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

Two senses of "estimate" meet here and must not be confused. The DOLLAR
figure has always been an estimate (list prices, not an invoice) computed
from exact provider TOKEN COUNTS. A turn the user stops is the one case
where a token count itself is not exact: the stream closes before the
provider's final usage delta, so the shortfall is measured from the
accumulated content and recorded under
``ESTIMATED_OUTPUT_TOKENS_KEY`` — beside ``output_tokens``, never inside
it. ``includes_estimated_output`` on the snapshot says whether any such
component is present, so a surface can disclose it.

Note that ``usage_pricing_snapshot`` deliberately says nothing about this:
it is consumed as Final QC's persisted, shape-validated ``cost_basis``, and
a QC run cannot produce an estimated component (its fan-out always reads a
final message). Adding a field there would break every new audit record's
validation to describe something that can never happen in it.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Mapping

from . import settings


# The stopped-turn output estimate, kept strictly apart from provider data.
#
# ``output_tokens`` always holds exactly what the provider reported — it is
# the one number reconcilable against an invoice, so a heuristic must never
# be blended into it. When a user stops a turn the stream closes before the
# provider's final usage delta arrives, so the accumulated content is
# measured instead and the SHORTFALL (estimate minus reported) is recorded
# under this key. The two are disjoint: best-available total = sum of both;
# provider truth = ``output_tokens`` alone.
ESTIMATED_OUTPUT_TOKENS_KEY = "estimated_output_tokens"

# Disclosure flag on a turn/round usage RECORD (the SSE payload and the
# trace event) — never on a ledger bucket, where it would be counted as an
# integer. See :meth:`UsageLedger.add`.
USAGE_ESTIMATED_KEY = "usage_estimated"

# Batch requests a Final QC run submitted whose result it never read — a
# Stop or the phase ceiling landed first, the results stream failed part
# way, a row never came back, or a row came back that belonged to no seat.
# A COUNT OF REQUESTS, deliberately not tokens: the provider may have billed
# them and the app cannot say how much, so the meter discloses the gap
# rather than inventing a figure. Every pricing helper reads named token
# keys, so this can never be charged for.
UNCOLLECTED_BATCH_REQUESTS_KEY = "uncollected_batch_requests"

# Additive pricing metadata, never additional billed tokens. The request
# count distinguishes a sum of short requests from an unclassified request;
# the subtotals are slices of their ordinary provider counters.
PRICING_REQUEST_COUNT_KEY = "pricing_request_count"
PRICING_UNCLASSIFIED_REQUEST_COUNT_KEY = "pricing_unclassified_request_count"
PRICED_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_creation_1h_input_tokens",
    "cache_read_input_tokens",
    ESTIMATED_OUTPUT_TOKENS_KEY,
)
LONG_CONTEXT_USAGE_KEYS = tuple("long_context_" + key for key in PRICED_TOKEN_KEYS)
PRICING_USAGE_KEYS = (
    PRICING_REQUEST_COUNT_KEY,
    PRICING_UNCLASSIFIED_REQUEST_COUNT_KEY,
    *LONG_CONTEXT_USAGE_KEYS,
)


def input_token_count(usage: Mapping[str, Any]) -> int:
    """One prompt's complete input, counting cache creation only once."""
    return sum(
        max(0, int(usage.get(key, 0) or 0))
        for key in (
            "input_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        )
    )


def _sampling_iterations(
    iterations: Any, usage: Mapping[str, int], *, model: str
) -> list[dict[str, int]] | None:
    """Complete sampling usage that reconciles to the provider's totals.

    ``usage.iterations`` describes each prompt in a server-tool loop. Its
    compaction/advisor entries are billed separately and are not slices of
    top-level usage, so only message entries can classify these counters.
    """
    if not isinstance(iterations, (list, tuple)):
        return None
    sampled: list[dict[str, int]] = []
    provider_keys = PRICED_TOKEN_KEYS[:-1]
    for entry in iterations:
        kind = _get(entry, "type")
        if kind in ("advisor_message", "compaction"):
            continue
        served_model = _get(entry, "model")
        if kind != "message" or (served_model is not None and served_model != model):
            return None
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        ):
            value = _get(entry, key)
            if value is None and key.startswith("cache_"):
                continue
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                return None
        sampled.append(usage_to_dict(entry))
    if not sampled or any(
        sum(entry.get(key, 0) for entry in sampled) != usage.get(key, 0)
        for key in provider_keys
    ):
        return None
    return sampled


def annotate_request_usage(
    model: str, usage: Mapping[str, int], *, iterations: Any = None
) -> dict[str, int]:
    """Classify ONE response's sampling prompts before usage is summed."""
    out = dict(usage)
    tier = settings.LONG_CONTEXT_PRICING.get(model)
    if not tier or not out or PRICING_REQUEST_COUNT_KEY in out:
        return out
    out[PRICING_REQUEST_COUNT_KEY] = 1
    if iterations is not None:
        sampled = _sampling_iterations(iterations, out, model=model)
        if sampled is not None:
            for entry in sampled:
                if input_token_count(entry) > tier["input_threshold_tokens"]:
                    for key, long_key in zip(
                        PRICED_TOKEN_KEYS, LONG_CONTEXT_USAGE_KEYS
                    ):
                        if entry.get(key):
                            out[long_key] = out.get(long_key, 0) + entry[key]
            # Unreported stopped output belongs to the LAST sample's
            # prompt, rather than the sum of the loop's input counters.
            if (
                out.get(ESTIMATED_OUTPUT_TOKENS_KEY)
                and input_token_count(sampled[-1]) > tier["input_threshold_tokens"]
            ):
                out["long_context_estimated_output_tokens"] = out[
                    ESTIMATED_OUTPUT_TOKENS_KEY
                ]
            return out
        out[PRICING_UNCLASSIFIED_REQUEST_COUNT_KEY] = 1
    elif input_token_count(out) > tier["input_threshold_tokens"] and (
        out.get("web_search_requests") or out.get("web_fetch_requests")
    ):
        # Without the loop's samples, their combined input is only an
        # upper bound on any prompt's size. Disclose the approximation.
        out[PRICING_UNCLASSIFIED_REQUEST_COUNT_KEY] = 1
    if input_token_count(out) > tier["input_threshold_tokens"]:
        for key, long_key in zip(PRICED_TOKEN_KEYS, LONG_CONTEXT_USAGE_KEYS):
            if out.get(key):
                out[long_key] = out[key]
    return out


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def usage_to_dict(
    usage: Any, *, model: str | None = None, estimated_output_tokens: int = 0
) -> dict[str, int]:
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
    if usage is None and not estimated_output_tokens:
        return out
    if estimated_output_tokens:
        out[ESTIMATED_OUTPUT_TOKENS_KEY] = estimated_output_tokens
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
    return (
        annotate_request_usage(model, out, iterations=_get(usage, "iterations"))
        if model
        else out
    )


# Which model each spend category runs on — resolved live so an env override
# (e.g. BUILD_A_SPEC_RESEARCH_MODEL) is priced correctly.
def _category_models() -> dict[str, str]:
    return {
        "interview": settings.INTERVIEW_MODEL,
        "research": settings.RESEARCH_MODEL,
        "audit": settings.RESEARCH_MODEL,
        "qc": settings.QC_MODEL,
        # Final QC's verification phase, when it ran on the Message Batches
        # API. Same model and same rate table as "qc" — a SEPARATE bucket
        # because those tokens were billed at a different multiplier, and a
        # single bucket could only ever be priced at one of the two.
        "qc_batched": settings.QC_MODEL,
        # Final QC's verifier seats, when they run on their own model
        # (``settings.QC_VERIFIER_MODEL``, Sonnet 5.5 since 2026-10-08): their
        # own buckets, because a bucket is priced at ONE model and the seats'
        # tokens filed under ``qc`` would be billed at the lenses' rates. A
        # run whose seats share the lenses' model files them under ``qc`` /
        # ``qc_batched`` as before.
        "qc_verifier": settings.QC_VERIFIER_MODEL,
        "qc_verifier_batched": settings.QC_VERIFIER_MODEL,
        "template": settings.INTERVIEW_MODEL,
        # The fact harvest's one call (Project workspace Phase 4). Its own
        # bucket rather than "interview" so the Settings table says what the
        # spend was for. Its independent model can be overridden separately.
        "harvest": settings.HARVEST_MODEL,
        # Condensing a long conversation into a summary (compaction plan
        # Phase 3). It forks the chat request — same model, so it can read
        # the chat's cache — and is billed at the interview model's rates.
        # Its own bucket because it can run without a click, and the
        # Settings table is where that spend has to be visible.
        "compaction": settings.INTERVIEW_MODEL,
    }


def _category_multipliers() -> dict[str, float]:
    """Per-category rate multipliers. Absent means full list price."""
    return {
        "qc_batched": settings.BATCH_COST_MULTIPLIER,
        "qc_verifier_batched": settings.BATCH_COST_MULTIPLIER,
    }


def _rates(model: str) -> dict[str, float]:
    return settings.PRICING.get(model, settings.PRICING[settings.MODEL_SONNET_55])


def model_rates(model: str, usage: Mapping[str, int] | None = None) -> dict[str, float]:
    """Per-token list rates for ``model``: ``input``, ``output``,
    ``cache_read``, ``cache_write`` (the 5-minute entry) and
    ``cache_write_1h``.

    The same lookup the ledger prices with, unknown-model fallback included,
    handed out as a copy so a caller cannot edit ``settings.PRICING``. The
    cost self-checks read it (``cost_checks.observe_continuation``) rather
    than keeping a table of their own that could drift from this one.
    """
    tier = settings.LONG_CONTEXT_PRICING.get(model)
    if (
        tier
        and usage is not None
        and input_token_count(usage) > tier["input_threshold_tokens"]
    ):
        return dict(tier["rates_per_token"])
    return dict(_rates(model))


def pricing_usage_slices(
    usage: Mapping[str, int], *, threshold: int | None = None
) -> tuple[dict[str, int], dict[str, int]]:
    """Return disjoint ordinary/premium usage, preserving request boundaries.

    Unannotated usage is one request. Aggregators of a tiered model MUST
    annotate each response before merging; their sum alone cannot recover
    the individual prompts' price tiers.
    """
    if threshold is None:
        return dict(usage), {}
    if usage.get(PRICING_REQUEST_COUNT_KEY, 0):
        long = {
            key: max(0, min(usage.get(long_key, 0), usage.get(key, 0)))
            for key, long_key in zip(PRICED_TOKEN_KEYS, LONG_CONTEXT_USAGE_KEYS)
        }
    elif input_token_count(usage) > threshold:
        long = {key: usage.get(key, 0) for key in PRICED_TOKEN_KEYS}
    else:
        long = {}
    short = dict(usage)
    for key, value in long.items():
        short[key] = short.get(key, 0) - value
    return short, long


def estimate_cost_from_rates(
    usage: Mapping[str, int],
    rates: Mapping[str, float],
    *,
    long_context_pricing: Mapping[str, Any] | None = None,
    multiplier: float = 1.0,
    web_search_rate: float = 0.0,
    web_fetch_rate: float = 0.0,
) -> float:
    """Price additive usage with supplied immutable rates (live or saved)."""
    short, long = pricing_usage_slices(
        usage,
        threshold=(
            long_context_pricing["input_threshold_tokens"]
            if long_context_pricing
            else None
        ),
    )
    total = 0.0
    for counts, token_rates in (
        (short, rates),
        (
            long,
            long_context_pricing["rates_per_token"] if long_context_pricing else rates,
        ),
    ):
        five_minute, one_hour = cache_write_split(counts)
        total += (
            counts.get("input_tokens", 0) * token_rates["input"]
            + (
                counts.get("output_tokens", 0)
                + counts.get(ESTIMATED_OUTPUT_TOKENS_KEY, 0)
            )
            * token_rates["output"]
            + counts.get("cache_read_input_tokens", 0) * token_rates["cache_read"]
            + five_minute * token_rates["cache_write"]
            + one_hour * token_rates.get("cache_write_1h", token_rates["cache_write"])
        )
    # Tool fees stay outside the token tier and are charged exactly once.
    total += usage.get("web_search_requests", 0) * web_search_rate
    total += usage.get("web_fetch_requests", 0) * web_fetch_rate
    return round(multiplier * total, 6)


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


def estimate_usage_cost(
    model: str, usage: dict[str, int], *, multiplier: float = 1.0
) -> float:
    """Estimate one recorded run's cost from its model and usage snapshot.

    The result is deliberately labeled an estimate everywhere it is exposed:
    it uses the app's list-price table, while the provider invoice remains the
    authority. Thinking tokens already live inside ``output_tokens`` and are
    therefore not added a second time — and neither is the one-hour cache
    subtotal, which lives inside the cache-creation total.

    ``multiplier`` scales every charge, for usage billed at something other
    than list price — today only the Message Batches API's 50%. It is a
    property of HOW the call was sent, not of the rate table, so the rates
    themselves stay untouched and a report can still show both.

    ``estimated_output_tokens`` is the opposite case and IS added: it is the
    output a stopped turn produced BEYOND what the provider reported (the
    stream was closed before the final usage delta), so it is disjoint from
    ``output_tokens`` by construction. It is charged at the same output
    rate — a token the model wrote costs the same whether or not the
    provider got to tell us about it.
    """
    return estimate_cost_from_rates(
        usage,
        _rates(model),
        long_context_pricing=settings.LONG_CONTEXT_PRICING.get(model),
        multiplier=multiplier,
        web_search_rate=settings.WEB_SEARCH_COST,
    )


def usage_pricing_snapshot(model: str) -> dict[str, Any]:
    """Return the exact configured rates used to estimate a run's cost."""
    rate_model = (
        model if model in settings.PRICING else settings.MODEL_SONNET_55
    )
    rates = _rates(model)
    snapshot = {
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
    tier = settings.LONG_CONTEXT_PRICING.get(model)
    if tier:
        snapshot["long_context_pricing"] = {
            "input_threshold_tokens": tier["input_threshold_tokens"],
            "rates_per_token": dict(tier["rates_per_token"]),
        }
        snapshot["authority"] += (
            " Price tiers follow individual sampling prompts when complete "
            "usage.iterations reconcile to the reported totals. Responses "
            "marked pricing_unclassified_request_count use their combined "
            "input as a conservative approximation."
        )
    return snapshot


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

        Booleans are rejected rather than counted. A turn's usage record
        carries the ``usage_estimated`` disclosure flag beside its counts,
        and ``bool`` is a subclass of ``int`` in Python — so without this a
        flagged turn would silently accumulate a ``usage_estimated: 1``,
        then ``2``, then ``3`` in the bucket and render as a token count.
        The ledger's own disclosure is derived from
        ``estimated_output_tokens`` instead (see :meth:`snapshot`), which
        cannot drift from the number it describes.
        """
        model = _category_models().get(category, settings.INTERVIEW_MODEL)
        data = (
            {**usage_to_dict(usage), **usage}
            if isinstance(usage, dict)
            else usage_to_dict(usage, model=model)
        )
        data = {
            k: int(v)
            for k, v in data.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v
        }
        if not data:
            return
        data = annotate_request_usage(model, data, iterations=_get(usage, "iterations"))
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
            _category_models().get(category, settings.INTERVIEW_MODEL),
            bucket,
            multiplier=_category_multipliers().get(category, 1.0),
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
            model = _category_models().get(category, settings.INTERVIEW_MODEL)
            tier = settings.LONG_CONTEXT_PRICING.get(model)
            short, long = pricing_usage_slices(
                bucket, threshold=tier["input_threshold_tokens"] if tier else None
            )
            # Discounted usage saved a discounted amount: the counterfactual
            # is paying full INPUT price on the same batched request, not
            # list price. Scaling both sides keeps the comparison honest.
            multiplier = _category_multipliers().get(category, 1.0)
            for counts, rates in (
                (short, _rates(model)),
                (long, tier["rates_per_token"] if tier else _rates(model)),
            ):
                saved += (
                    multiplier
                    * counts.get("cache_read_input_tokens", 0)
                    * (rates["input"] - rates["cache_read"])
                )
        return round(saved, 6)

    def snapshot(self) -> dict[str, Any]:
        """The UI-shaped payload for ``GET /api/usage``.

        ``includes_estimated_output`` is DERIVED from the counter rather
        than tracked alongside it, so the flag and the number it discloses
        can never disagree.
        """
        with self._lock:
            totals = self._totals()
            return {
                "categories": {k: dict(v) for k, v in self.categories.items()},
                "totals": totals,
                "turns": self.turns,
                "estimated_cost_usd": self._estimated_cost(),
                "cache_saved_usd": self._cache_saved(),
                "includes_estimated_output": bool(
                    totals.get(ESTIMATED_OUTPUT_TOKENS_KEY, 0)
                ),
                # Derived from its counter for the same reason: the flag and
                # the number it discloses can never disagree.
                "includes_uncollected_charges": bool(
                    totals.get(UNCOLLECTED_BATCH_REQUESTS_KEY, 0)
                ),
                # Detached snapshots predating tier metadata cannot recover
                # request sizes. Disclose that their premium is approximate.
                "includes_estimated_pricing": any(
                    _category_models().get(category, settings.INTERVIEW_MODEL)
                    in settings.LONG_CONTEXT_PRICING
                    and (
                        not bucket.get(PRICING_REQUEST_COUNT_KEY)
                        or bool(bucket.get(PRICING_UNCLASSIFIED_REQUEST_COUNT_KEY))
                    )
                    for category, bucket in self.categories.items()
                ),
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
                    model = _category_models().get(category, settings.INTERVIEW_MODEL)
                    if model in settings.LONG_CONTEXT_PRICING and not bucket.get(
                        PRICING_REQUEST_COUNT_KEY
                    ):
                        # Boundaries are unavailable: retain a conservative
                        # one-request approximation, and disclose it even
                        # after later exact usage is added to this bucket.
                        bucket = annotate_request_usage(model, bucket)
                        bucket[PRICING_UNCLASSIFIED_REQUEST_COUNT_KEY] = 1
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
