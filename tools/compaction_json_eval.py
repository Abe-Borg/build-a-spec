"""Owner-run Markdown/JSON compaction comparison; no production adoption.

Default: prepare a plan from local .baspec / legacy .json sessions, send
nothing. --run requires a new --out-dir and sends at most three requests
per session (at most ten sessions): a non-streaming max_tokens=0 cache warm,
then the production Markdown summary and an experimental JSON summary.
Arm order alternates. SDK retries and display fallbacks are disabled.

Public results contain hashes, counts, timings and list-price estimates.
The output directory also holds PRIVATE summaries and source paths for
manual fidelity review. --assess reads these locally, sending nothing.
Use artifacts/ for local evidence; keep private files out of commits.
"""
from __future__ import annotations

import argparse
import copy
import glob
import hashlib
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend import settings  # noqa: E402
from backend.llm import conversation  # noqa: E402
from backend.llm.client import bounded_request_options, get_client  # noqa: E402
from backend.llm.compaction import (  # noqa: E402
    MAX_SUMMARY_CHARS,
    MIN_CONDENSE_FRACTION,
    SUMMARY_HEADINGS,
    CompactionError,
    compacted_view,
    estimate_tokens,
    extract_summary,
    message_chars,
    view_spec_for,
)
from backend.spec_doc.project import load_project  # noqa: E402
from backend.spec_doc.project_package import MAX_PACKAGE_BYTES, parse_project_file  # noqa: E402
from backend.usage_ledger import estimate_usage_cost, model_rates  # noqa: E402

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {heading: {"type": "string"} for heading in SUMMARY_HEADINGS},
    "required": list(SUMMARY_HEADINGS),
    "additionalProperties": False,
}
_USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
_FIDELITY_CHECKS = (
    "decisions_and_reasons", "exact_values_names_editions_links",
    "preferences_constraints_corrections", "open_promised_and_current_state",
    "missing_ledger_decisions_and_turn_tags", "kept_turns_and_fresh_context_not_repeated",
)
# Trial limits, not provider guarantees. Every pair must satisfy them;
# a passing report is still only evidence for the owner's decision.
_LIMITS = {"cache_loss_percentage_points": 2.0, "cost_increase_fraction": 0.05, "latency_increase_fraction": 0.10}


class EvaluationError(ValueError):
    """A CLI explanation containing only fixed diagnostic text."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class Case:
    artifact: str
    history_sha256: str
    source: Path
    request: dict
    tokens_before: int
    covers_turns: int
    re_compaction: bool
    near_threshold: bool


def prepare_case(path: Path, max_tokens: int) -> Case:
    with path.open("rb") as handle:
        raw = handle.read(MAX_PACKAGE_BYTES + 1)
    if len(raw) > MAX_PACKAGE_BYTES:
        raise EvaluationError("Input exceeds the package byte limit.")
    parsed = parse_project_file(raw)
    session = conversation.SessionState()
    load_project(parsed.project, session)
    view, _ = compacted_view(session.history, view_spec_for(session.compaction))
    tokens = estimate_tokens(sum(message_chars(message) for message in view), session.tokens_per_char)
    with session.session_state_guard():
        inputs = conversation._compaction_plan_locked(
            session, model=settings.INTERVIEW_MODEL,
            keep_turns=settings.CHAT_COMPACTION_KEEP_TURNS,
            tokens_before=tokens, trigger="background", min_fraction=MIN_CONDENSE_FRACTION,
        )
    if inputs is None:
        raise EvaluationError("History has no eligible compaction cut; select another saved session.")
    request = conversation._build_compaction_request(inputs)
    request["max_tokens"] = max_tokens
    json_request(request)  # Validate the experimental shape before any paid call.
    return Case(
        artifact=_digest(raw), history_sha256=_digest(json.dumps(session.history, sort_keys=True, ensure_ascii=False).encode("utf-8")),
        source=path.resolve(), request=request,
        tokens_before=tokens, covers_turns=inputs.covers_turns,
        re_compaction=session.compaction is not None,
        near_threshold=0.8 * settings.CHAT_COMPACTION_THRESHOLD <= tokens <= conversation._backstop_tokens(),
    )


def json_request(baseline: dict) -> dict:
    """Only the uncached instruction and output format differ from baseline."""
    last = baseline["messages"][-1]
    if last["role"] != "user" or len(last["content"]) != 1 or last["content"][0].get("cache_control"):
        raise EvaluationError("Unexpected production summary instruction shape; update the evaluator before running.")
    text = last["content"][0]["text"]
    start = text.index("Put the summary inside <summary></summary> tags,")
    end = text.index("Be sure to preserve:", start)
    instruction = (
        text[:start]
        + "Return one JSON object matching the supplied schema. Its eight named "
        "string fields contain the corresponding summary sections. Do not wrap "
        "the object in Markdown or <summary> tags, or repeat the field name as "
        "a heading inside its value. Write \"None.\" for an empty section.\n\n"
        + text[end:]
    )
    return {
        **baseline,
        "messages": [*baseline["messages"][:-1], {"role": "user", "content": [{"type": "text", "text": instruction}]}],
        "output_config": {**baseline["output_config"], "format": {"type": "json_schema", "schema": copy.deepcopy(SUMMARY_SCHEMA)}},
    }


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        result[key] = value
    return result


def json_summary(content: list[dict], stop: str) -> str:
    """Validate JSON and render the existing eight-heading Markdown format."""
    if stop == "refusal":
        raise CompactionError("Refused.", code="refused")
    if any(block.get("type") in {"tool_use", "server_tool_use"} for block in content):
        raise CompactionError("Used a tool.", code="tool_call")
    if stop not in {"end_turn", "stop_sequence"}:
        raise CompactionError("Incomplete.", code="incomplete")
    text = "\n".join(str(block.get("text") or "") for block in content if block.get("type") == "text")
    if len(text) > MAX_SUMMARY_CHARS * 6:
        raise CompactionError("Runaway JSON.", code="too_long")
    try:
        value = json.loads(text, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError) as exc:
        raise CompactionError("Malformed JSON.", code="invalid_json") from exc
    if not isinstance(value, dict) or set(value) != set(SUMMARY_HEADINGS) or any(
        not isinstance(value[heading], str) or not value[heading].strip() for heading in SUMMARY_HEADINGS
    ):
        raise CompactionError("Wrong summary fields.", code="invalid_fields")
    summary = "\n\n".join(f"## {heading}\n{value[heading].strip()}" for heading in SUMMARY_HEADINGS)
    if len(summary) > MAX_SUMMARY_CHARS:
        raise CompactionError("Runaway summary.", code="too_long")
    return summary


def _get(node, key):
    return node.get(key) if isinstance(node, dict) else getattr(node, key, None)


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _hour_cache(request: dict) -> bool:
    blocks = [*request.get("system", []), *request.get("tools", [])]
    for message in request["messages"]:
        if isinstance(message["content"], list):
            blocks.extend(message["content"])
    return any(isinstance(block, dict) and (block.get("cache_control") or {}).get("ttl") == "1h" for block in blocks)


def metrics(response, request: dict, elapsed: float, first_output: float | None) -> dict:
    raw = _get(response, "usage")
    usage = {key: _count(_get(raw, key)) for key in _USAGE_KEYS}
    creation = _get(raw, "cache_creation")
    one_hour = _count(_get(creation, "ephemeral_1h_input_tokens"))
    # Missing provider counts are unknown, never zero-cost observations.
    complete = all(value is not None for value in usage.values())
    if complete and usage["cache_creation_input_tokens"]:
        complete = one_hour is not None or not _hour_cache(request)
    if one_hour is not None:
        complete = complete and one_hour <= (usage["cache_creation_input_tokens"] or 0)
        usage["cache_creation_1h_input_tokens"] = one_hour
    for key in ("web_search_requests", "web_fetch_requests"):
        value = _count(_get(_get(raw, "server_tool_use"), key))
        if value is not None:
            usage[key] = value
    cost = estimate_usage_cost(request["model"], usage) if complete and request["model"] in settings.PRICING else None
    total = sum(usage[key] for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")) if complete else 0
    return {
        "usage": usage, "usage_complete": complete, "estimated_cost_usd": cost,
        "cache_read_fraction": usage["cache_read_input_tokens"] / total if total else None,
        "elapsed_seconds": elapsed, "first_output_seconds": first_output,
        "stop_reason": str(_get(response, "stop_reason") or ""),
    }


def run_arm(client, request: dict, arm: str) -> tuple[dict, str | None]:
    start = time.perf_counter()
    first_output = None
    try:
        if arm == "warm":
            response = client.messages.create(**request)
        else:
            with client.messages.stream(**request) as stream:
                for event in stream:
                    delta = _get(event, "delta")
                    if first_output is None and _get(delta, "type") in {"text_delta", "thinking_delta"}:
                        first_output = time.perf_counter() - start
                response = stream.get_final_message()
        record = metrics(response, request, time.perf_counter() - start, first_output)
    except Exception as exc:  # noqa: BLE001 - never print provider bodies or private input
        return {"status": "request_error", "error_type": type(exc).__name__, "usage_complete": False,
                "estimated_cost_usd": None, "elapsed_seconds": time.perf_counter() - start}, None
    if arm == "warm":
        cached = (record["usage"].get("cache_read_input_tokens") or 0) + (record["usage"].get("cache_creation_input_tokens") or 0)
        valid = record["usage_complete"] and cached > 0 and record["usage"]["output_tokens"] == 0 and not _get(response, "content") and record["stop_reason"] == "max_tokens" and not any(record["usage"].get(key) for key in ("web_search_requests", "web_fetch_requests"))
        record["status"] = "ok" if valid else "invalid_warm"
        return record, None
    parse_start = time.perf_counter()
    try:
        content = conversation._content_blocks_to_dicts(_get(response, "content"))
        summary = (extract_summary if arm == "markdown" else json_summary)(content, record["stop_reason"])
        record["status"] = "ok"
        record["summary_sha256"] = _digest(summary.encode("utf-8"))
        record["parse_seconds"] = time.perf_counter() - parse_start
        record["elapsed_seconds"] = time.perf_counter() - start
        return record, summary
    except Exception as exc:  # noqa: BLE001 - retain billed measurements even if normalization fails
        record["status"] = "invalid_summary"
        record["parse_error"] = exc.code if isinstance(exc, CompactionError) else "unexpected_content"
        record["parse_seconds"] = time.perf_counter() - parse_start
        record["elapsed_seconds"] = time.perf_counter() - start
        return record, None


def _write(path: Path, value: dict) -> None:
    # Checkpoints do not expose a half-written report after interruption.
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _save_report(folder: Path, report: dict) -> None:
    calls = [record for case in report["cases"] for record in [case.get("warm"), *case["arms"].values()] if record]
    costs = [record.get("estimated_cost_usd") for record in calls]
    report["calls_recorded"] = len(calls)
    report["billing_complete"] = all(cost is not None for cost in costs)
    report["estimated_known_cost_usd"] = round(sum(cost for cost in costs if cost is not None), 6)
    report["estimated_total_cost_usd"] = round(sum(costs), 6) if report["billing_complete"] else None
    _write(folder / "results.json", report)


def run_cases(client, cases: list[Case], folder: Path) -> dict:
    report = {
        "version": 1, "adoption": "pending", "model": settings.INTERVIEW_MODEL,
        "rates": model_rates(settings.INTERVIEW_MODEL) if settings.INTERVIEW_MODEL in settings.PRICING else None,
        "limits": dict(_LIMITS), "production_output_budget": all(case.request["max_tokens"] == settings.CHAT_COMPACTION_MAX_TOKENS for case in cases),
        "threshold_tokens": settings.CHAT_COMPACTION_THRESHOLD, "keep_turns": settings.CHAT_COMPACTION_KEEP_TURNS,
        "effort": settings.INTERVIEW_EFFORT, "cache_ttl": settings.CHAT_CACHE_TTL,
        "schema_sha256": _digest(json.dumps(SUMMARY_SCHEMA, sort_keys=True).encode("utf-8")),
        "protocol": "max_tokens=0 warm; alternating Markdown/JSON order; no retries",
        "planned_sessions": len(cases), "requests_max": 3 * len(cases), "run_complete": False, "cases": [],
    }
    review = {"cache_conditions_reviewed": None, "cache_conditions_notes": "", "cases": {}}
    _save_report(folder, report)
    _write(folder / "review.json", review)
    for index, case in enumerate(cases):
        row = {"artifact": case.artifact, "history_sha256": case.history_sha256, "tokens_before_estimate": case.tokens_before,
               "covers_turns": case.covers_turns, "re_compaction": case.re_compaction,
               "near_threshold": case.near_threshold, "arms": {}}
        report["cases"].append(row)
        # Persist the attempt before sending it: interruption must not make an
        # unmeasured, potentially billed request look like a free observation.
        row["warm"] = {"status": "in_flight", "usage_complete": False, "estimated_cost_usd": None}
        _save_report(folder, report)
        warm, _ = run_arm(client, {**case.request, "max_tokens": 0}, "warm")
        row["warm"] = warm
        _save_report(folder, report)
        if warm["status"] != "ok":
            break
        order = ["markdown", "json"] if index % 2 == 0 else ["json", "markdown"]
        row["order"] = order
        row["first_json_in_run"] = index == 0
        review["cases"][case.artifact] = {
            "source": str(case.source), "checks": dict.fromkeys(_FIDELITY_CHECKS),
        }
        _write(folder / "review.json", review)
        for arm in order:
            request = case.request if arm == "markdown" else json_request(case.request)
            row["arms"][arm] = {"status": "in_flight", "usage_complete": False, "estimated_cost_usd": None}
            _save_report(folder, report)
            record, summary = run_arm(client, request, arm)
            row["arms"][arm] = record
            _save_report(folder, report)
            if summary is not None:
                (folder / f"{case.artifact}-{arm}.md").write_text(summary + "\n", encoding="utf-8")
            if record["status"] == "request_error":
                return report
    report["run_complete"] = len(report["cases"]) == len(cases) and all(
        len(row["arms"]) == 2 for row in report["cases"]
    )
    _save_report(folder, report)
    return report


def assess(folder: Path) -> dict:
    """Offline gates; successful measurements never change app behavior."""
    report = json.loads((folder / "results.json").read_text(encoding="utf-8"))
    review = json.loads((folder / "review.json").read_text(encoding="utf-8"))
    reasons: set[str] = set()
    cases = report.get("cases", [])
    if report.get("version") != 1 or not isinstance(cases, list):
        raise ValueError("Unknown evaluation report format.")
    if not 5 <= len(cases) <= 10 or len({case["artifact"] for case in cases}) != len(cases) or len({case["history_sha256"] for case in cases}) != len(cases):
        reasons.add("need_5_to_10_distinct_sessions")
    if report.get("production_output_budget") is not True:
        reasons.add("pilot_output_budget")
    if report.get("run_complete") is not True:
        reasons.add("missing_or_failed_calls")
    if review.get("cache_conditions_reviewed") is not True or not isinstance(review.get("cache_conditions_notes"), str) or not review["cache_conditions_notes"].strip():
        reasons.add("cache_conditions_review_pending")
    for case in cases:
        artifact = case["artifact"]
        if len(artifact) != 64 or any(char not in "0123456789abcdef" for char in artifact):
            raise ValueError("Invalid artifact hash.")
        if case.get("near_threshold") is not True:
            reasons.add("sessions_not_near_threshold")
        warm = case.get("warm", {})
        arms = case.get("arms", {})
        if warm.get("status") != "ok" or any(arms.get(arm, {}).get("status") != "ok" for arm in ("markdown", "json")):
            reasons.add("missing_or_failed_calls")
            continue
        baseline, candidate = arms["markdown"], arms["json"]
        if any(record.get("usage_complete") is not True or not all(
            type(record.get(key)) in (int, float) and math.isfinite(record[key]) and record[key] >= 0
            for key in ("estimated_cost_usd", "cache_read_fraction", "elapsed_seconds")
        ) or record["cache_read_fraction"] > 1 for record in (warm, baseline, candidate)) or baseline["estimated_cost_usd"] <= 0 or baseline["elapsed_seconds"] <= 0:
            reasons.add("incomplete_measurements")
            continue
        if any(type(record.get("first_output_seconds")) not in (int, float) or not math.isfinite(record["first_output_seconds"]) or not 0 <= record["first_output_seconds"] <= record["elapsed_seconds"] for record in (baseline, candidate)):
            reasons.add("first_output_timing_missing")
        if baseline["usage"].get("cache_read_input_tokens", 0) <= 0:
            reasons.add("baseline_cache_miss")
        if candidate["cache_read_fraction"] < baseline["cache_read_fraction"] - _LIMITS["cache_loss_percentage_points"] / 100:
            reasons.add("cache_regression")
        if candidate["estimated_cost_usd"] > baseline["estimated_cost_usd"] * (1 + _LIMITS["cost_increase_fraction"]):
            reasons.add("cost_regression")
        if candidate["elapsed_seconds"] > baseline["elapsed_seconds"] * (1 + _LIMITS["latency_increase_fraction"]):
            reasons.add("latency_regression")
        entry = review.get("cases", {}).get(artifact, {})
        checks = [entry.get("checks", {}).get(check) for check in _FIDELITY_CHECKS]
        if any(check is False for check in checks):
            reasons.add("fidelity_regression")
        elif not all(check is True for check in checks):
            reasons.add("fidelity_review_pending")
        source = Path(entry["source"])
        with source.open("rb") as handle:
            raw = handle.read(MAX_PACKAGE_BYTES + 1)
        if _digest(raw) != artifact:
            reasons.add("source_changed_since_run")
        for arm in ("markdown", "json"):
            record = arms[arm]
            summary = (folder / f"{artifact}-{arm}.md").read_text(encoding="utf-8").removesuffix("\n")
            if _digest(summary.encode("utf-8")) != record["summary_sha256"]:
                reasons.add("summary_changed_since_run")
    return {"adoption": "pending", "assessment": "ready_for_owner_decision" if not reasons else "insufficient_or_regressed",
            "reasons": sorted(reasons), "sessions": len(cases)}


def _paths(raw_paths: list[str]) -> list[Path]:
    paths = []
    for raw in raw_paths:
        path = Path(raw).expanduser()
        matches = sorted(path.glob("*.baspec")) + sorted(path.glob("*.json")) if path.is_dir() else [Path(match) for match in sorted(glob.glob(str(path)))]
        if not matches:
            raise EvaluationError("No input matched; select local saved sessions or a quoted glob.")
        paths.extend(matches)
    return list(dict.fromkeys(path.resolve() for path in paths))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="Local sessions, directories, or quoted globs.")
    parser.add_argument("--run", action="store_true", help="Owner only: execute up to three paid requests per session.")
    parser.add_argument("--out-dir", type=Path, help="New PRIVATE output directory, required for --run.")
    parser.add_argument("--assess", type=Path, help="Assess an existing output directory offline.")
    parser.add_argument("--max-sessions", type=int, default=10)
    parser.add_argument("--max-tokens", type=int, default=settings.CHAT_COMPACTION_MAX_TOKENS, help="Same ceiling for both summaries; a lower value is a pilot.")
    args = parser.parse_args(argv)
    try:
        if args.assess:
            if args.run or args.paths or args.out_dir:
                raise EvaluationError("Assessment cannot be combined with a run or inputs.")
            print(json.dumps(assess(args.assess), indent=2))
            return 0
        if not args.paths or not 1 <= args.max_sessions <= 10 or not 256 <= args.max_tokens <= settings.CHAT_COMPACTION_MAX_TOKENS:
            raise EvaluationError(f"Use inputs, 1–10 sessions, and a 256–{settings.CHAT_COMPACTION_MAX_TOKENS} token ceiling.")
        cases = []
        seen = set()
        for path in _paths(args.paths):
            case = prepare_case(path, args.max_tokens)
            if case.history_sha256 not in seen:
                cases.append(case)
                seen.add(case.history_sha256)
            if len(cases) == args.max_sessions:
                break
        plan = {"model": settings.INTERVIEW_MODEL, "requests_max": 3 * len(cases),
                "summary_max_tokens_each": args.max_tokens, "adoption": "pending",
                "cases": [{"artifact": case.artifact, "history_tokens_estimate": case.tokens_before,
                           "covers_turns": case.covers_turns, "near_threshold": case.near_threshold} for case in cases]}
        if settings.INTERVIEW_MODEL in settings.PRICING:
            rates = model_rates(settings.INTERVIEW_MODEL)
            input_estimate = sum(estimate_tokens(sum(message_chars(case.request[key]) for key in ("system", "tools", "messages")), None) for case in cases)
            plan["cold_list_cost_estimate_usd"] = round(
                3 * input_estimate * max(rates["input"], rates["cache_write"], rates["cache_write_1h"])
                + 2 * len(cases) * args.max_tokens * rates["output"], 2,
            )
            plan["cost_note"] = "Rough cold-input/full-output scenario, not a billing cap or invoice. Cache warm is also billed."
        print(json.dumps(plan, indent=2))
        if not args.run:
            print("No requests sent. Owner selects sessions locally and uses --run with a new --out-dir.")
            return 0
        if args.out_dir is None:
            raise EvaluationError("--run requires a new --out-dir for private local review files.")
        args.out_dir.mkdir(parents=True, exist_ok=False)
        client = get_client().with_options(**bounded_request_options(180.0))
        report = run_cases(client, cases, args.out_dir)
        print(json.dumps({"calls_recorded": sum(1 + len(case["arms"]) for case in report["cases"]),
                          "adoption": "pending", "next": "Review private summaries and review.json, then --assess."}, indent=2))
        return 0 if all(case.get("warm", {}).get("status") == "ok" and all(case["arms"].get(arm, {}).get("status") == "ok" for arm in ("markdown", "json")) for case in report["cases"]) and len(report["cases"]) == len(cases) else 1
    except EvaluationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI diagnostics contain no private data or provider error body
        print(f"Evaluation could not complete ({type(exc).__name__}).", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
