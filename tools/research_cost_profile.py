#!/usr/bin/env python3
"""Read saved research rounds and report what they cost.

Chunk 1 of the Research and Final QC cost plan
(``docs/plans/RESEARCH_QC_COST_TIER1_2026-09-23.md``). It is the sibling of
``tools/qc_export_cost_profile.py``, and the before-and-after instrument for
the plan's Chunks 4 and 5: the headline it prints — the UNCACHED share of
the input side, over every round it read — is the number those chunks exist
to move, and the baseline the plan's measurement M1 records.

READ-ONLY, AND DELIBERATELY NARROW. It never imports the client factory,
never builds a session, never makes a model request, and never imports
``backend.research.engine`` (which loads the API client): the few lines it
needs from there are copied, not imported. What it prints is counts, token
totals, rates and ratios, plus research-area ids, round dates and section
numbers. It never prints requirement text, source URLs, client or project
names, error messages, or filenames. Artifacts are identified in the
pasteable output by a SHA-256 prefix of their bytes; the local console line
names the file so you can tell them apart on your own machine.

Usage (Windows; each command is one line and runs as written in PowerShell
and in Command Prompt):

    .\\.venv\\Scripts\\python tools\\research_cost_profile.py "C:\\specs\\*.baspec" "C:\\specs\\Project.basproject" --out research-measurement.md

    .\\.venv\\Scripts\\python tools\\research_cost_profile.py "C:\\specs\\21 13 13.baspec" --model claude-opus-5-5

The script expands the globs itself, because PowerShell does not. It
accepts, in any mix:
  * ``.baspec`` packages and legacy ``.json`` project files, whose
    ``requirements_profile`` is the section's research;
  * ``.basproject`` project briefs, whose ``research_profile`` is the
    project's.

A brief and the sections seeded from it share rounds, so every round is
counted once: by its ``round_id``, or — for a round saved without one — by
its section, date and number, hashed exactly as the app hashes a legacy
round when a brief merges research (``legacy_round_key``), so a legacy round
a brief carried under that hash still meets its original.

``--model`` prices every round at another model's list rates; by default
they are priced at the research model this build is configured with.

Paste the block it prints into the progress file's Measurements section
(``docs/plans/RESEARCH_QC_COST_TIER1_PROGRESS.md``, M1 or M3).
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend import settings  # noqa: E402
from backend.spec_doc.project_package import parse_project_file  # noqa: E402

# ``backend.project_brief.PROJECT_BRIEF_KIND``, copied: importing that module
# pulls in the research engine and, through it, the API client. A test pins
# the two equal.
_BRIEF_KIND = "buildaspec-project-brief"

# The per-dimension usage fields a saved ``DimensionStatus`` carries
# (``backend.research.engine._DIMENSION_USAGE_KEYS``). Research writes no
# one-hour cache entries, so there is no one-hour subtotal to split out: every
# cache write is a five-minute write.
_INPUT = "input_tokens"
_OUTPUT = "output_tokens"
_READ = "cache_read_input_tokens"
_WRITE = "cache_creation_input_tokens"
_SEARCHES = "web_search_requests"
_FETCHES = "web_fetch_requests"

# Bounds on what a saved file may put in front of a reader. Every value
# printed below is one of: a count, a token total, a rate, a ratio, or one of
# these four shapes. Anything else a hand-edited file carries in their place
# is shown as a hash or a fixed word, never as itself.
_DIMENSION_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SECTION_RE = re.compile(r"^[0-9][0-9 .\-]{0,23}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HEX_RE = re.compile(r"^[0-9a-f]+$")
# ``backend.research.engine._MAX_ROUND_ID_CHARS``.
_MAX_ROUND_ID_CHARS = 64


def _int(value: Any) -> int:
    """A token count or request counter, never negative, never a bool."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if isinstance(value, float) and not math.isfinite(value):
        return 0
    return max(0, int(value))


def _engine_int(value: Any) -> int:
    """``int(value or 0)``, the way ``RequirementsProfile.from_dict`` reads a
    round index. Used ONLY for the round key, which must hash exactly what
    the app hashes; a value the app could not read reads as 0 here."""
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


# ---------------------------------------------------------------------------
# Round identity (copied from backend.research.engine)
# ---------------------------------------------------------------------------


def _round_id(value: Any) -> str:
    """``engine._round_id_from_raw``: a stripped string, else not recorded."""
    if not isinstance(value, str):
        return ""
    cleaned = value.strip()
    return cleaned if 0 < len(cleaned) <= _MAX_ROUND_ID_CHARS else ""


def legacy_round_key(section: str, research_date: str, round_index: int) -> str:
    """``engine.legacy_round_key``, byte for byte.

    A merge replays a legacy round into a project brief under THIS hash as
    its ``round_id``, so computing the same hash for a legacy round read
    straight from a section's file is what lets the two copies meet.
    """
    marker = repr(("legacy-round", section, research_date, round_index))
    return hashlib.sha256(marker.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Usage and cost
# ---------------------------------------------------------------------------


@dataclass
class Usage:
    """Billed usage for one or more (research area, round) records."""

    records: int = 0
    completed: int = 0
    failed: int = 0
    input: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0
    searches: int = 0
    fetches: int = 0

    @classmethod
    def from_status(cls, raw: dict) -> "Usage":
        completed = raw.get("status") == "completed"
        return cls(
            records=1,
            completed=1 if completed else 0,
            failed=0 if completed else 1,
            input=_int(raw.get(_INPUT)),
            cache_read=_int(raw.get(_READ)),
            cache_write=_int(raw.get(_WRITE)),
            output=_int(raw.get(_OUTPUT)),
            searches=_int(raw.get(_SEARCHES)),
            fetches=_int(raw.get(_FETCHES)),
        )

    def add(self, other: "Usage") -> None:
        self.records += other.records
        self.completed += other.completed
        self.failed += other.failed
        self.input += other.input
        self.cache_read += other.cache_read
        self.cache_write += other.cache_write
        self.output += other.output
        self.searches += other.searches
        self.fetches += other.fetches

    @property
    def input_side(self) -> int:
        """Everything billed against the input side of the ledger."""
        return self.input + self.cache_read + self.cache_write

    def uncached_share(self) -> float | None:
        """I / (I + R + W): how much of the input side paid full price.

        A TOKEN-WEIGHTED share, not a request hit rate. Chunk 4 exists to
        move it down; a continuation that reads its own cache turns full-price
        input into cache reads.
        """
        side = self.input_side
        if side <= 0:
            return None
        return self.input / side

    def cost(self, rates: dict[str, float], search_rate: float) -> dict[str, float]:
        """Mirror ``usage_ledger.estimate_usage_cost`` term for term.

        Research writes only five-minute entries, so every cache write takes
        ``cache_write``. Web fetch is billed by the tokens it returns, never
        per request, so fetches carry no fee of their own.
        """
        return {
            "input": self.input * rates.get("input", 0.0),
            "cache_read": self.cache_read * rates.get("cache_read", 0.0),
            "cache_write": self.cache_write * rates.get("cache_write", 0.0),
            "output": self.output * rates.get("output", 0.0),
            "web_search": self.searches * search_rate,
        }


def estimated_cost(usage: Usage, rates: dict[str, float], search_rate: float) -> float:
    return sum(usage.cost(rates, search_rate).values())


# ---------------------------------------------------------------------------
# Reading the files
# ---------------------------------------------------------------------------


@dataclass
class Round:
    key: str
    kind: str  # "id" | "legacy" | "legacy, cumulative"
    round_id: str
    section: str
    research_date: str
    round_index: int
    # (dimension id as saved, usage) in the order the round recorded them.
    dimensions: list[tuple[str, Usage]] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)


def _dimensions(raw_statuses: Any) -> list[tuple[str, Usage]]:
    out: list[tuple[str, Usage]] = []
    for raw in _as_list(raw_statuses):
        if not isinstance(raw, dict):
            continue
        out.append(
            (str(raw.get("dimension_id", "") or ""), Usage.from_status(raw))
        )
    return out


def rounds_in_profile(profile: Any) -> list[Round]:
    """Every round a saved research profile holds, keyed for deduplication.

    A profile saved before rounds were recorded is ONE legacy round built
    from its top-level ``dimension_statuses``, exactly as
    ``RequirementsProfile.from_dict`` synthesizes it (round 1, the profile's
    date, no section) — so the key matches that file once a later build
    re-saves or a brief carries it.
    """
    if not isinstance(profile, dict):
        return []
    rounds: list[Round] = []
    for raw in _as_list(profile.get("rounds")):
        if not isinstance(raw, dict):
            continue
        section = " ".join(str(raw.get("section", "") or "").split())
        research_date = str(raw.get("research_date", "") or "")
        round_index = _engine_int(raw.get("round_index", 0))
        round_id = _round_id(raw.get("round_id"))
        rounds.append(
            Round(
                key=round_id or legacy_round_key(section, research_date, round_index),
                kind="id" if round_id else "legacy",
                round_id=round_id,
                section=section,
                research_date=research_date,
                round_index=round_index,
                dimensions=_dimensions(raw.get("dimension_statuses")),
            )
        )
    if rounds:
        return rounds
    statuses = _dimensions(profile.get("dimension_statuses"))
    if not statuses:
        return []
    research_date = str(profile.get("research_date", "") or "")
    return [
        Round(
            key=legacy_round_key("", research_date, 1),
            kind="legacy, cumulative",
            round_id="",
            section="",
            research_date=research_date,
            round_index=1,
            dimensions=statuses,
        )
    ]


def _load(path: Path) -> tuple[str, list[Round], str]:
    """Return (artifact id, rounds, note) for one file."""
    data = path.read_bytes()
    artifact = hashlib.sha256(data).hexdigest()[:12]
    profile: Any = None
    # A brief is JSON; one an editor re-saved may lead with a BOM or a blank.
    if data.lstrip(b"\xef\xbb\xbf \t\r\n")[:1] == b"{":
        try:
            payload = json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError) as exc:
            return artifact, [], f"not readable as JSON ({exc.__class__.__name__})"
        if isinstance(payload, dict) and payload.get("kind") == _BRIEF_KIND:
            profile = payload.get("research_profile")
            rounds = rounds_in_profile(profile)
            if not rounds:
                return artifact, [], "a project brief that carries no research"
            return artifact, rounds, ""
        # Otherwise: a legacy JSON project, read by the app's own parser.
    try:
        parsed = parse_project_file(data)
    except Exception as exc:  # noqa: BLE001 - a tool, not a service
        return artifact, [], f"could not be parsed ({exc.__class__.__name__}: {exc})"
    project = parsed.project if isinstance(parsed.project, dict) else {}
    rounds = rounds_in_profile(project.get("requirements_profile"))
    if not rounds:
        return artifact, [], "carries no research"
    return artifact, rounds, ""


# ---------------------------------------------------------------------------
# Rendering (counts and ratios only)
# ---------------------------------------------------------------------------


def _dimension_label(raw: str) -> str:
    """A research-area id, or a hash of whatever a file put in its place."""
    if _DIMENSION_ID_RE.match(raw):
        return raw
    digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:8]
    return f"unrecognized-{digest}"


def _section_label(raw: str) -> str:
    if not raw:
        return "unnumbered"
    return raw if _SECTION_RE.match(raw) else "not a section number"


def _date_label(raw: str) -> str:
    return raw if _DATE_RE.match(raw) else "undated"


def _round_heading(round_: Round) -> str:
    identity = (
        f"id `{round_.round_id[:8]}`"
        if round_.round_id and _HEX_RE.match(round_.round_id)
        else ("id not recognized" if round_.round_id else round_.kind)
    )
    return (
        f"Round {round_.round_index} · section {_section_label(round_.section)} · "
        f"{_date_label(round_.research_date)} · {identity}"
    )


def _pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def _usd(value: float) -> str:
    return f"${value:,.2f}"


def _per_million(rate: float) -> str:
    return f"${rate * 1_000_000:,.2f}"


_TABLE_HEADER = (
    "| {first} | {second} | Searches | Fetches | Uncached in | Cache read | "
    "Cache write | Output | Uncached share | Cost | Output share |"
)
_TABLE_RULE = "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"


def _row(
    label: str,
    second: str,
    usage: Usage,
    rates: dict[str, float],
    search_rate: float,
) -> str:
    priced = usage.cost(rates, search_rate)
    total = sum(priced.values())
    output_share = priced["output"] / total if total > 0 else None
    return (
        f"| {label} | {second} | {usage.searches} | {usage.fetches} | "
        f"{usage.input:,} | {usage.cache_read:,} | {usage.cache_write:,} | "
        f"{usage.output:,} | {_pct(usage.uncached_share())} | {_usd(total)} | "
        f"{_pct(output_share)} |"
    )


def _status_word(usage: Usage) -> str:
    return "completed" if usage.completed else "failed"


def render(
    rounds: list[Round],
    artifacts: list[tuple[str, int, int]],
    *,
    model: str,
    rates: dict[str, float],
    rate_source: str,
    search_rate: float,
    today: str,
) -> str:
    """The pasteable markdown block. ``artifacts`` is (id, rounds, new)."""
    overall = Usage()
    by_dimension: dict[str, Usage] = {}
    dimension_rounds: dict[str, int] = {}
    order: list[str] = []
    for round_ in rounds:
        for dimension_id, usage in round_.dimensions:
            overall.add(usage)
            if dimension_id not in by_dimension:
                by_dimension[dimension_id] = Usage()
                order.append(dimension_id)
            by_dimension[dimension_id].add(usage)
            dimension_rounds[dimension_id] = dimension_rounds.get(dimension_id, 0) + 1
    priced = overall.cost(rates, search_rate)
    total_cost = sum(priced.values())

    lines: list[str] = []
    lines.append(f"# Research cost profile — measured {today}")
    lines.append("")
    lines.append(
        f"Produced by `tools/research_cost_profile.py` from {len(rounds)} "
        f"research round(s) across {len(artifacts)} artifact(s). Artifacts "
        "are identified by a SHA-256 prefix; no requirement text, source URL, "
        "client or project name, error message or filename appears below."
    )
    lines.append("")
    lines.append(
        f"**Uncached share of the input side, all rounds: "
        f"{_pct(overall.uncached_share())}** — {overall.input:,} of "
        f"{overall.input_side:,} input-side tokens were billed at the full "
        "input price. (Chunk 4 of the Tier 1 cost plan exists to lower it.)"
    )
    lines.append("")
    lines.append(
        f"- Estimated cost of these rounds: **{_usd(total_cost)}**; output is "
        f"{_pct(priced['output'] / total_cost) if total_cost > 0 else 'n/a'} "
        f"of it; web search fees {_usd(priced['web_search'])} "
        f"({overall.searches} search(es)); {overall.fetches} fetch(es)."
    )
    lines.append(
        f"- Research areas run: {overall.records} across the rounds; "
        f"{overall.completed} completed, {overall.failed} failed."
    )
    lines.append(
        f"- Priced at `{model}`'s list rates from {rate_source}: input "
        f"{_per_million(rates.get('input', 0.0))}, output "
        f"{_per_million(rates.get('output', 0.0))}, 5-minute cache write "
        f"{_per_million(rates.get('cache_write', 0.0))}, cache read "
        f"{_per_million(rates.get('cache_read', 0.0))} per million tokens; web "
        f"search {_usd(search_rate * 1000)} per 1,000."
    )
    lines.append("")
    lines.append(
        "The **uncached share** is `input / (input + cache read + cache "
        "write)`: a token-weighted share of the input side, not a request "
        "hit rate."
    )
    lines.append("")

    lines.append("## Rounds")
    lines.append("")
    for round_ in rounds:
        lines.append(f"### {_round_heading(round_)}")
        lines.append("")
        if round_.kind == "legacy, cumulative":
            lines.append(
                "- Legacy, cumulative: saved before research rounds were "
                "recorded, so its per-area totals are read as one round."
            )
        elif round_.kind == "legacy":
            lines.append(
                "- Saved without a round id; counted once by its section, "
                "date and number."
            )
        lines.append(
            f"- Artifacts: {', '.join(f'`{a}`' for a in round_.artifacts)}"
        )
        lines.append("")
        lines.append(_TABLE_HEADER.format(first="Research area", second="Status"))
        lines.append(_TABLE_RULE)
        round_total = Usage()
        for dimension_id, usage in round_.dimensions:
            round_total.add(usage)
            lines.append(
                _row(
                    f"`{_dimension_label(dimension_id)}`",
                    _status_word(usage),
                    usage,
                    rates,
                    search_rate,
                )
            )
        lines.append(
            _row(
                "**round total**",
                f"{round_total.completed} of {round_total.records} completed",
                round_total,
                rates,
                search_rate,
            )
        )
        lines.append("")

    lines.append("## All rounds, by research area")
    lines.append("")
    lines.append(
        "One row per research area, summed over every round above — the view "
        "to compare against a later measurement of the same areas."
    )
    lines.append("")
    lines.append(_TABLE_HEADER.format(first="Research area", second="Rounds"))
    lines.append(_TABLE_RULE)
    for dimension_id in order:
        usage = by_dimension[dimension_id]
        failed = f", {usage.failed} failed" if usage.failed else ""
        lines.append(
            _row(
                f"`{_dimension_label(dimension_id)}`",
                f"{dimension_rounds[dimension_id]}{failed}",
                usage,
                rates,
                search_rate,
            )
        )
    lines.append(
        _row("**all rounds**", str(len(rounds)), overall, rates, search_rate)
    )
    lines.append("")

    lines.append("## Artifacts")
    lines.append("")
    for artifact, held, new in artifacts:
        repeated = held - new
        also = f", {repeated} already counted" if repeated else ""
        lines.append(f"- `{artifact}`: {held} round(s){also}.")
    lines.append("")

    lines.append("## Caveats")
    lines.append("")
    lines.append(
        f"- The model that ran a round is not recorded, so every round is "
        f"priced at `{model}`'s current list rates. A round run on another "
        "model, or before a price change, is priced as if it ran today."
    )
    lines.append(
        "- Research records usage per research area per round, with no "
        "per-request counts, so continuations and retries cannot be counted: "
        "only their combined tokens."
    )
    lines.append(
        "- A research area that failed was still billed, and is included."
    )
    lines.append(
        "- Research writes only 5-minute cache entries, so every cache write "
        "is priced at the 5-minute rate."
    )
    lines.append(
        "- Rounds are counted once across files: by round id, or for a round "
        "saved without one, by its section, date and number (the key the app "
        "uses when a project brief merges research)."
    )
    lines.append(
        "- Estimates use list prices; the provider's invoice is the authority."
    )
    lines.append("")
    return "\n".join(lines)


def _pricing_for(model_override: str) -> tuple[str, dict[str, float], str]:
    """(model priced at, its rates, a sentence naming where they came from)."""
    if model_override:
        return (
            model_override,
            dict(settings.PRICING[model_override]),
            "this build's PRICING table (--model)",
        )
    model = settings.RESEARCH_MODEL
    table = settings.PRICING.get(model)
    if table is not None:
        return (
            model,
            dict(table),
            "this build's PRICING table (the research model this build runs)",
        )
    # ``usage_ledger._rates`` does the same for an unpriced model; say so.
    return (
        settings.MODEL_SONNET_5,
        dict(settings.PRICING[settings.MODEL_SONNET_5]),
        f"this build's PRICING table, falling back from the unpriced research "
        f"model `{model}` the way the app's own meter does",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Report what saved research rounds cost, and how much of their "
            "input came from the cache. Read-only; emits counts and ratios, "
            "never requirement text."
        )
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help=".baspec packages, legacy .json projects, or .basproject briefs.",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Write the markdown block here instead of stdout.",
    )
    parser.add_argument(
        "--model",
        default="",
        help=(
            "Price the rounds at this model's list rates instead of the "
            "configured research model's."
        ),
    )
    args = parser.parse_args(argv)
    if args.model and args.model not in settings.PRICING:
        parser.error(
            f"no list rates for model {args.model!r}; priced models: "
            + ", ".join(sorted(settings.PRICING))
        )

    expanded: list[Path] = []
    for raw in args.paths:
        matches = sorted(glob.glob(os.path.expanduser(raw)))
        if matches:
            expanded.extend(Path(m) for m in matches)
        else:
            expanded.append(Path(os.path.expanduser(raw)))

    rounds: dict[str, Round] = {}
    artifacts: list[tuple[str, int, int]] = []
    seen_artifacts: set[str] = set()
    for path in expanded:
        if not path.is_file():
            print(f"  skip  {path}  (not a file)", file=sys.stderr)
            continue
        artifact, found, note = _load(path)
        if note:
            print(f"  skip  {path.name}  ({note})", file=sys.stderr)
            continue
        # Local orientation only: the filename never reaches the markdown.
        print(f"  read  {path.name}  -> artifact {artifact}", file=sys.stderr)
        if artifact in seen_artifacts:
            continue  # the same bytes named twice (a glob and a path)
        seen_artifacts.add(artifact)
        new = 0
        for round_ in found:
            existing = rounds.get(round_.key)
            if existing is not None:
                if artifact not in existing.artifacts:
                    existing.artifacts.append(artifact)
                continue
            round_.artifacts.append(artifact)
            rounds[round_.key] = round_
            new += 1
        artifacts.append((artifact, len(found), new))

    if not rounds:
        print(
            "No readable research found in the given files. Nothing measured.",
            file=sys.stderr,
        )
        return 1

    model, rates, rate_source = _pricing_for(args.model)
    ordered = sorted(
        rounds.values(),
        key=lambda r: (r.research_date, r.section, r.round_index, r.key),
    )
    text = render(
        ordered,
        artifacts,
        model=model,
        rates=rates,
        rate_source=rate_source,
        search_rate=float(settings.WEB_SEARCH_COST),
        today=date.today().isoformat(),
    )
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"\nWrote {args.out}", file=sys.stderr)
    else:
        print(text)

    # The privacy claim in this docstring, checked rather than asserted: a
    # tool that reads saved research must never have pulled in the client.
    for forbidden in ("backend.llm.client", "anthropic"):
        if forbidden in sys.modules:
            print(
                f"WARNING: {forbidden} was imported; this script is supposed "
                "to make no model requests. Report this.",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
