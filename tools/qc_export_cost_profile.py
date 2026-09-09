#!/usr/bin/env python3
"""Read a saved Final QC export and report where its money went.

This is step 2 of the revision-2 review plan (retired; its acceptance
criteria live in ``docs/review-results/2026-09-09/EXECUTION_RECORD.md``).
It answers one question the repository cannot answer for itself: does the
batched verifier phase actually READ its shared cached prefix, or does it
pay to write one per seat?

READ-ONLY, AND DELIBERATELY NARROW. It never imports the client factory,
never builds a session, never makes a model request, and never emits
document text, provision wording, prompts, reference-document contents,
source URLs, finding titles, or credentials. What it prints is counts,
token totals, rates and ratios. Artifacts are identified in the pasteable
output by a SHA-256 prefix rather than a filename, because a filename
routinely carries a client's name; the local console line names the file
so you can tell them apart on your own machine.

Usage (Windows):

    .venv\\Scripts\\python tools\\qc_export_cost_profile.py ^
        "C:\\path\\FINAL QC REPORT 210500.json" ^
        "C:\\path\\some-project.baspec"

    .venv\\Scripts\\python tools\\qc_export_cost_profile.py ^
        "C:\\path\\*.json" --out measurements.md

Accepts, in any mix:
  * ``GET /api/qc/export.json`` downloads (the ``{report, current_state}``
    envelope; ``last_successful_report`` is read too when it is a
    different run).
  * ``.baspec`` packages and legacy ``.json`` project files, whose
    ``qc_result`` is the retained report.

Runs are deduplicated by ``run_id``, so exporting the same run twice — or
a project plus an export of the same review — counts it once.

Paste the block it prints into
``docs/review-results/<date>/measurements.md``.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import os
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


def _lens_web_flags() -> dict[str, bool]:
    """Read each lens's ``web`` flag by PARSING ``backend/qc/schema.py``.

    Importing that module would pull in the web-tool builders and, through
    them, the API client — which this script must never load. Parsing the
    source with ``ast`` gets the same two facts with no import at all, and
    an added or renamed lens shows up here rather than being silently
    bucketed wrong.
    """
    import ast

    source = (_REPO_ROOT / "backend" / "qc" / "schema.py").read_text("utf-8")
    flags: dict[str, bool] = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
        if name != "QCLens":
            continue
        lens_id = ""
        web = True  # the dataclass default
        for keyword in node.keywords:
            if keyword.arg == "lens_id" and isinstance(keyword.value, ast.Constant):
                lens_id = str(keyword.value.value)
            elif keyword.arg == "web" and isinstance(keyword.value, ast.Constant):
                web = bool(keyword.value.value)
        if lens_id:
            flags[lens_id] = web
    if not flags:
        raise SystemExit(
            "Could not read the lens table from backend/qc/schema.py; "
            "refusing to guess which seats carry web tools."
        )
    return flags


# The five token classes, kept disjoint. ``cache_creation_input_tokens`` is
# the provider's TOTAL across TTL classes and already contains the one-hour
# subtotal, so the five-minute slice is the difference (see Chunk 4.1).
_INPUT = "input_tokens"
_OUTPUT = "output_tokens"
_READ = "cache_read_input_tokens"
_WRITE = "cache_creation_input_tokens"
_WRITE_1H = "cache_creation_1h_input_tokens"

# A verifier seat's tools are chosen from its finding's lens: only
# ``code_compliance`` carries web search and fetch, and tools render ahead
# of system and messages — so seats on a compliance finding sit in a
# DIFFERENT cached prefix from every other seat. Counting them together
# reads the cache share low for a structural reason rather than a defect.
# (Erratum to the v1.8.0 note, recorded in CLAUDE.md.)
_LENS_WEB = _lens_web_flags()
_WEB_LENSES = {lens_id for lens_id, web in _LENS_WEB.items() if web}
_NON_WEB_LENSES = {lens_id for lens_id, web in _LENS_WEB.items() if not web}


def _int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if isinstance(value, float) and not math.isfinite(value):
        return 0
    return max(0, int(value))


def _float(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    if not math.isfinite(float(value)):
        return 0.0
    return float(value)


@dataclass
class Tokens:
    """Disjoint token slices plus the counters that price them."""

    uncached_input: int = 0
    write_5m: int = 0
    write_1h: int = 0
    cache_read: int = 0
    output: int = 0
    api_requests: int = 0
    model_responses: int = 0
    uncollected_requests: int = 0
    records: int = 0
    records_with_read: int = 0
    records_with_write: int = 0
    recorded_cost_usd: float = 0.0

    def add_usage(self, usage: Any, *, recorded_cost: float = 0.0) -> None:
        self.records += 1
        self.recorded_cost_usd += recorded_cost
        if not isinstance(usage, dict):
            return
        total_write = _int(usage.get(_WRITE))
        one_hour = min(_int(usage.get(_WRITE_1H)), total_write)
        read = _int(usage.get(_READ))
        self.uncached_input += _int(usage.get(_INPUT))
        self.write_1h += one_hour
        self.write_5m += total_write - one_hour
        self.cache_read += read
        self.output += _int(usage.get(_OUTPUT))
        if read > 0:
            self.records_with_read += 1
        if total_write > 0:
            self.records_with_write += 1

    @property
    def total_write(self) -> int:
        return self.write_5m + self.write_1h

    @property
    def input_side(self) -> int:
        """Everything billed against the input side of the ledger."""
        return self.uncached_input + self.total_write + self.cache_read

    def cache_read_share(self) -> float | None:
        """R / (I + W + R).

        A TOKEN-WEIGHTED share of the input side. It is NOT a request hit
        rate: one call reading a large prefix and one writing it register
        very differently here, which is the point.
        """
        denominator = self.input_side
        if denominator <= 0:
            return None
        return self.cache_read / denominator

    def cost(self, rates: dict[str, float], multiplier: float) -> dict[str, float]:
        priced = {
            "input": self.uncached_input * rates.get("input", 0.0),
            "cache_write_5m": self.write_5m * rates.get("cache_write", 0.0),
            "cache_write_1h": self.write_1h
            * rates.get("cache_write_1h", rates.get("cache_write", 0.0)),
            "cache_read": self.cache_read * rates.get("cache_read", 0.0),
            "output": self.output * rates.get("output", 0.0),
        }
        return {key: value * multiplier for key, value in priced.items()}


@dataclass
class RunProfile:
    run_id: str
    artifacts: list[str] = field(default_factory=list)
    schema_version: int = 0
    protocol_version: str = ""
    application_version: str = ""
    execution_status: str = ""
    model: str = ""
    effort: str = ""
    verifier_effort: str = ""
    started_at: str = ""
    batch_verification: Any = None
    consolidation_enabled: Any = None
    rates: dict[str, float] = field(default_factory=dict)
    rate_source: str = ""
    recorded_total_cost: float = 0.0
    batch_usage_capture: str = ""
    unassigned_batch_results: int = 0
    # bucket label -> (Tokens, multiplier)
    buckets: dict[str, tuple[Tokens, float]] = field(default_factory=dict)
    candidate_count: int = 0
    seats_without_known_lens: int = 0

    def bucket(self, label: str, multiplier: float) -> Tokens:
        existing = self.buckets.get(label)
        if existing is None:
            existing = (Tokens(), multiplier)
            self.buckets[label] = existing
        return existing[0]


def _manifest_value(manifest: Any, *path: str) -> Any:
    node: Any = manifest
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _rates_for(report: dict, run: RunProfile) -> None:
    basis = report.get("cost_basis")
    rates = _manifest_value(basis, "rates_per_token")
    if isinstance(rates, dict) and rates:
        run.rates = {
            key: _float(value)
            for key, value in rates.items()
            if isinstance(key, str)
        }
        rate_model = str(_manifest_value(basis, "rate_model") or "")
        used_fallback = bool(_manifest_value(basis, "used_fallback_rate"))
        run.rate_source = (
            f"the report's own cost_basis (rate model {rate_model or 'unnamed'}"
            f"{'; a fallback rate' if used_fallback else ''})"
        )
        return
    # A record written before cost_basis existed. Price it from the table
    # this build ships, and SAY so — the run may have been billed at rates
    # this build no longer carries.
    model = str(report.get("model") or "")
    table = settings.PRICING.get(model)
    if table is None:
        table = settings.PRICING[settings.MODEL_SONNET_5]
        run.rate_source = (
            f"this build's PRICING table, falling back to "
            f"{settings.MODEL_SONNET_5} (the report named no priced model)"
        )
    else:
        run.rate_source = f"this build's PRICING table for {model}"
    run.rates = dict(table)


def _profile_run(report: dict, artifact: str) -> RunProfile:
    run = RunProfile(run_id=str(report.get("run_id") or ""))
    run.artifacts.append(artifact)
    run.schema_version = _int(report.get("schema_version"))
    run.protocol_version = str(report.get("protocol_version") or "")
    run.execution_status = str(report.get("execution_status") or "")
    run.model = str(report.get("model") or "")
    run.effort = str(report.get("effort") or "")
    run.verifier_effort = str(report.get("verifier_effort") or "")
    run.started_at = str(report.get("started_at") or "")
    run.recorded_total_cost = _float(report.get("estimated_cost_usd"))
    run.batch_usage_capture = str(report.get("batch_usage_capture") or "")
    run.unassigned_batch_results = _int(report.get("unassigned_batch_results"))

    manifest = report.get("input_manifest")
    run.application_version = str(
        _manifest_value(manifest, "application_version") or ""
    )
    run.batch_verification = _manifest_value(
        manifest, "configuration", "batch_verification"
    )
    run.consolidation_enabled = _manifest_value(
        manifest, "configuration", "consolidation_enabled"
    )
    _rates_for(report, run)

    # Phase 1 — one bucket per lens, never discounted (phase 1 always
    # streams, so its tokens are billed at list price).
    for lens in report.get("lens_statuses") or []:
        if not isinstance(lens, dict):
            continue
        lens_id = str(lens.get("lens_id") or "unknown")
        bucket = run.bucket(f"lens:{lens_id}", 1.0)
        bucket.add_usage(
            lens.get("usage_totals"),
            recorded_cost=_float(lens.get("estimated_cost_usd")),
        )
        bucket.api_requests += _int(lens.get("api_request_count"))
        bucket.model_responses += _int(lens.get("model_response_count"))

    # The grouping call, when the run had one.
    consolidation = report.get("consolidation")
    if isinstance(consolidation, dict) and consolidation.get("usage_totals"):
        bucket = run.bucket("consolidation", 1.0)
        bucket.add_usage(
            consolidation.get("usage_totals"),
            recorded_cost=_float(consolidation.get("estimated_cost_usd")),
        )
        bucket.api_requests += _int(consolidation.get("api_request_count"))
        bucket.model_responses += _int(consolidation.get("model_response_count"))

    # Phase 2 — every seat on every candidate, across all FOUR raw
    # collections. Accounting is structural, not semantic: a seat on a
    # refuted or inconclusive candidate was billed exactly like one on a
    # survivor (Chunk 5.3's lesson).
    for collection in ("findings", "refuted", "disputed", "inconclusive"):
        for finding in report.get(collection) or []:
            if not isinstance(finding, dict):
                continue
            run.candidate_count += 1
            lens_id = str(finding.get("lens_id") or "")
            if lens_id in _WEB_LENSES:
                lineage = "web-tooled"
            elif lens_id in _NON_WEB_LENSES:
                lineage = "no-web"
            else:
                lineage = "lens-unknown"
            for verdict in finding.get("verdicts") or []:
                if not isinstance(verdict, dict):
                    continue
                if lineage == "lens-unknown":
                    run.seats_without_known_lens += 1
                multiplier = _float(verdict.get("cost_multiplier"))
                if multiplier <= 0:
                    multiplier = 1.0
                billing = "batched" if multiplier < 1.0 else "list-price"
                bucket = run.bucket(f"seat:{billing}:{lineage}", multiplier)
                bucket.add_usage(
                    verdict.get("usage_totals"),
                    recorded_cost=_float(verdict.get("estimated_cost_usd")),
                )
                bucket.api_requests += _int(verdict.get("api_request_count"))
                bucket.model_responses += _int(verdict.get("model_response_count"))
                bucket.uncollected_requests += _int(
                    verdict.get("uncollected_requests")
                )
    return run


def _reports_in(payload: Any) -> list[dict]:
    """Every QC report inside one parsed artifact."""
    found: list[dict] = []
    if not isinstance(payload, dict):
        return found
    for key in ("report", "last_successful_report", "qc_result"):
        candidate = payload.get(key)
        if isinstance(candidate, dict) and candidate.get("run_id"):
            found.append(candidate)
    # A bare QCResult handed straight in.
    if not found and payload.get("run_id") and payload.get("lens_statuses"):
        found.append(payload)
    return found


def _load(path: Path) -> tuple[str, list[dict], str]:
    """Return (artifact id, reports, note) for one file."""
    data = path.read_bytes()
    artifact = hashlib.sha256(data).hexdigest()[:12]
    text_head = data[:1].decode("utf-8", "ignore")
    if text_head == "{":
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            return artifact, [], f"not readable as JSON ({exc.__class__.__name__})"
        reports = _reports_in(payload)
        if reports:
            return artifact, reports, ""
        # A legacy JSON project file.
    try:
        parsed = parse_project_file(data)
    except Exception as exc:  # noqa: BLE001 - a tool, not a service
        return artifact, [], f"could not be parsed ({exc.__class__.__name__}: {exc})"
    reports = _reports_in(parsed.project)
    if not reports:
        return artifact, [], "carries no Final QC report"
    return artifact, reports, ""


def _pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def _usd(value: float) -> str:
    return f"${value:,.2f}"


def _run_markdown(run: RunProfile) -> list[str]:
    lines: list[str] = []
    lines.append(f"### Run `{run.run_id[:8] or 'unnamed'}`")
    lines.append("")
    lines.append(f"- Artifacts: {', '.join(f'`{a}`' for a in run.artifacts)}")
    lines.append(
        f"- Schema/protocol: {run.schema_version} / "
        f"`{run.protocol_version or 'unrecorded'}`"
    )
    lines.append(
        f"- App version at run: "
        f"`{run.application_version or 'unrecorded'}`; "
        f"execution status: `{run.execution_status or 'unrecorded'}`; "
        f"started {run.started_at or 'unrecorded'}"
    )
    lines.append(
        f"- Model `{run.model or 'unrecorded'}`, lens effort "
        f"`{run.effort or 'unrecorded'}`, verifier effort "
        f"`{run.verifier_effort or 'not recorded (pre-split)'}`"
    )
    lines.append(
        f"- Batched verification: `{run.batch_verification}` · "
        f"consolidation: `{run.consolidation_enabled}`"
    )
    lines.append(f"- Rates from {run.rate_source}.")
    lines.append("")

    header = (
        "| Bucket | Calls | Uncached in | 5m write | 1h write | Cache read | "
        "Output | Read share | Cost |"
    )
    lines.append(header)
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")

    computed_total = 0.0
    for label in sorted(run.buckets):
        tokens, multiplier = run.buckets[label]
        priced = tokens.cost(run.rates, multiplier)
        subtotal = sum(priced.values())
        computed_total += subtotal
        suffix = " ×½" if multiplier < 1.0 else ""
        lines.append(
            f"| `{label}`{suffix} | {tokens.records} | "
            f"{tokens.uncached_input:,} | {tokens.write_5m:,} | "
            f"{tokens.write_1h:,} | {tokens.cache_read:,} | "
            f"{tokens.output:,} | {_pct(tokens.cache_read_share())} | "
            f"{_usd(subtotal)} |"
        )

    totals = Tokens()
    output_cost = 0.0
    for label in run.buckets:
        tokens, multiplier = run.buckets[label]
        totals.uncached_input += tokens.uncached_input
        totals.write_5m += tokens.write_5m
        totals.write_1h += tokens.write_1h
        totals.cache_read += tokens.cache_read
        totals.output += tokens.output
        totals.records += tokens.records
        totals.uncollected_requests += tokens.uncollected_requests
        output_cost += tokens.cost(run.rates, multiplier)["output"]
    lines.append(
        f"| **run total** | {totals.records} | {totals.uncached_input:,} | "
        f"{totals.write_5m:,} | {totals.write_1h:,} | {totals.cache_read:,} | "
        f"{totals.output:,} | {_pct(totals.cache_read_share())} | "
        f"{_usd(computed_total)} |"
    )
    lines.append("")

    lines.append(
        f"- Output is **{_pct(output_cost / computed_total) if computed_total else 'n/a'}** "
        f"of the run's estimated cost."
    )
    recorded = run.recorded_total_cost
    if recorded > 0:
        drift = abs(recorded - computed_total) / recorded
        agreement = "agrees" if drift <= 0.01 else f"DISAGREES by {_pct(drift)}"
        lines.append(
            f"- Recomputed {_usd(computed_total)} vs the report's recorded "
            f"{_usd(recorded)} — {agreement}."
        )
    else:
        lines.append(
            "- The report recorded no run-level estimate to check against."
        )

    # Phase 1: did the four web-toolless lenses read the shared prefix?
    shared = [
        run.buckets[f"lens:{lens_id}"][0]
        for lens_id in sorted(_NON_WEB_LENSES)
        if f"lens:{lens_id}" in run.buckets
    ]
    if shared:
        read = sum(1 for t in shared if t.records_with_read)
        wrote = sum(1 for t in shared if t.records_with_write)
        lines.append(
            f"- Phase 1, the {len(shared)} web-toolless lenses: **{read} read** "
            f"the shared prefix, **{wrote} wrote** one."
        )

    # Phase 2, per lineage.
    for lineage in ("web-tooled", "no-web", "lens-unknown"):
        for billing in ("batched", "list-price"):
            key = f"seat:{billing}:{lineage}"
            tokens = run.buckets.get(key, (None, 1.0))[0]
            if tokens is None or not tokens.records:
                continue
            lines.append(
                f"- Phase 2 {billing} `{lineage}` seats: "
                f"**{tokens.records_with_read} of {tokens.records}** read a "
                f"cached prefix; token-weighted read share "
                f"{_pct(tokens.cache_read_share())}."
            )
    if run.seats_without_known_lens:
        lines.append(
            f"- {run.seats_without_known_lens} seat(s) sit on a finding whose "
            "lens this build does not declare, so their cache lineage is "
            "unknown and they are bucketed separately rather than guessed."
        )

    # Capture completeness — never inferred.
    capture = run.batch_usage_capture
    if not capture:
        lines.append(
            "- Batch cost capture: **UNKNOWN** — this record predates the "
            "disclosure, so nothing here says whether every batched request "
            "was read back. Do not infer completeness from the totals above."
        )
    else:
        lines.append(
            f"- Batch cost capture: `{capture}`; "
            f"{totals.uncollected_requests} uncollected seat request(s), "
            f"{run.unassigned_batch_results} unassigned result row(s)."
        )
    lines.append("")
    return lines


def _verdict(runs: list[RunProfile]) -> list[str]:
    """Apply the plan's three-branch decision rule."""
    lines = ["## Decision", ""]
    batched_seats = 0
    batched_with_read = 0
    streaming_runs: list[RunProfile] = []
    for run in runs:
        seen_batched = False
        for label, (tokens, _multiplier) in run.buckets.items():
            if not label.startswith("seat:batched:"):
                continue
            seen_batched = True
            batched_seats += tokens.records
            batched_with_read += tokens.records_with_read
        if not seen_batched and any(
            label.startswith("seat:") for label in run.buckets
        ):
            streaming_runs.append(run)

    if batched_seats:
        share = batched_with_read / batched_seats
        lines.append(
            f"Batched verifier seats that read a cached prefix: "
            f"**{batched_with_read} of {batched_seats}** ({_pct(share)})."
        )
        lines.append("")
        if share >= 0.5:
            lines.append(
                "→ **Most batched seats read the cache.** The batched default "
                "stands and the batch-cache-reuse investigation is closed."
            )
        else:
            lines.append(
                "→ **Batched seats show near-zero reads.** The batched default "
                "still stands on the output term — output dominates the bill — "
                "but the measured input-side gap is real, and batch cache "
                "reuse becomes a concrete, budgeted proposal rather than a "
                "deferred idea. Any paid comparison must state request count, "
                "synthetic payload, output allowance, timeout, maximum spend "
                "and cleanup before it is authorized."
            )
    else:
        lines.append(
            "No batched verifier seats in these artifacts, so the batching "
            "question is not answered here. Export a run made with "
            "`BUILD_A_SPEC_QC_BATCH_VERIFICATION` left at its default."
        )
    lines.append("")
    if streaming_runs:
        lines.append(
            f"**{len(streaming_runs)} run(s) verified over the streaming "
            "transport** are included above. Their phase-2 figures are the "
            "measured comparison the $5.61 hypothetical was standing in for; "
            "report them beside the batched ones and retire the estimate."
        )
    else:
        lines.append(
            "No pre-batching (streaming phase-2) run is present. One saved "
            "between v1.8.0 and v1.11.0 would give the direct comparison and "
            "let the $5.61 hypothetical be retired rather than carried."
        )
    lines.append("")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Profile the cost shape of one or more saved Final QC runs. "
            "Read-only; emits counts and ratios, never document text."
        )
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="QC export .json files, .baspec packages, or legacy .json projects.",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Write the markdown block here instead of stdout.",
    )
    args = parser.parse_args(argv)

    expanded: list[Path] = []
    for raw in args.paths:
        matches = sorted(glob.glob(os.path.expanduser(raw)))
        if matches:
            expanded.extend(Path(m) for m in matches)
        else:
            expanded.append(Path(os.path.expanduser(raw)))

    runs: dict[str, RunProfile] = {}
    skipped: list[str] = []
    for path in expanded:
        if not path.is_file():
            print(f"  skip  {path}  (not a file)", file=sys.stderr)
            skipped.append("a path that is not a file")
            continue
        artifact, reports, note = _load(path)
        if note:
            print(f"  skip  {path.name}  ({note})", file=sys.stderr)
            skipped.append(note)
            continue
        # Local orientation only: the filename never reaches the markdown.
        print(f"  read  {path.name}  → artifact {artifact}", file=sys.stderr)
        for report in reports:
            run_id = str(report.get("run_id") or "")
            existing = runs.get(run_id)
            if existing is not None:
                if artifact not in existing.artifacts:
                    existing.artifacts.append(artifact)
                continue
            runs[run_id] = _profile_run(report, artifact)

    if not runs:
        print(
            "No Final QC run found in the given files. Nothing measured.",
            file=sys.stderr,
        )
        return 1

    ordered = sorted(runs.values(), key=lambda r: r.started_at)
    lines: list[str] = []
    lines.append(f"# Final QC cost profile — measured {date.today().isoformat()}")
    lines.append("")
    lines.append(
        f"Produced by `tools/qc_export_cost_profile.py` from "
        f"{len(ordered)} run(s) across {len({a for r in ordered for a in r.artifacts})} "
        "artifact(s). Artifacts are identified by a SHA-256 prefix; no "
        "document text, provision wording, source URL, finding title or "
        "filename appears below."
    )
    lines.append("")
    lines.append(
        "A **read share** is `cache_read / (uncached_input + cache_write + "
        "cache_read)` — a token-weighted share of the input side, not a "
        "request hit rate."
    )
    lines.append("")
    lines.append(
        "Phase 2 seats are split by billing rate AND by cache lineage: only "
        "`code_compliance` findings give their seats web tools, and tools "
        "render ahead of system and messages, so those seats sit in a "
        "different cached prefix from every other seat."
    )
    lines.append("")
    lines.append("## Runs")
    lines.append("")
    for run in ordered:
        lines.extend(_run_markdown(run))
    lines.extend(_verdict(ordered))
    if skipped:
        lines.append(
            f"{len(skipped)} input(s) were skipped; see the console for why."
        )
        lines.append("")

    text = "\n".join(lines)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"\nWrote {args.out}", file=sys.stderr)
    else:
        print(text)

    # The privacy claim in this docstring, checked rather than asserted: a
    # tool that reads QC exports must never have pulled in the API client.
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
