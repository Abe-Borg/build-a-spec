"""Hermetic evidence for the owner-run comparison, never live API calls."""
from __future__ import annotations

import copy
import itertools
import json
import os
import stat
import tempfile
from types import SimpleNamespace

import pytest

from backend import sessions, settings
from backend.llm.compaction import (
    SUMMARY_HEADINGS,
    CompactionError,
    CompactionRecord,
    transcript_digest,
)
from backend.llm.conversation import SessionState
from backend.spec_doc.project import save_project
from backend.spec_doc.project_package import build_project_package
from backend.usage_ledger import estimate_usage_cost
from tests.fakes import _FakeStreamCtx, bad_request, text_turn, token_usage
from tools import compaction_json_eval as evaluator


def _fields():
    fields = dict.fromkeys(SUMMARY_HEADINGS, "None.")
    fields["Exact details"] = "42 gpm; NFPA 13-2022; https://example.test/spec"
    fields["Decisions the ledgers are missing"] = "Turn 2: retain the selected pump."
    return fields


def _markdown():
    return "\n\n".join(f"## {heading}\n{value}" for heading, value in _fields().items())


@pytest.fixture
def saved_session(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CHAT_COMPACTION_THRESHOLD", 20_000)
    clock = itertools.count()
    monkeypatch.setattr(evaluator.time, "perf_counter", lambda: next(clock) / 100)

    def make(label="private-project", *, package=False, re_compaction=False, turns=8):
        session = SessionState()
        for turn in range(turns):
            for role in ("user", "assistant"):
                text = (f"{label} turn {turn + 1} {role} " * 300)[:4000]
                session.history.append({"role": role, "content": [{"type": "text", "text": text}]})
        compaction = None
        if re_compaction:
            compaction = CompactionRecord(
                summary=_markdown(), keep_from=2, covers_turns=1,
                digest=transcript_digest(session.history, 2),
                created_at="2026-09-30T00:00:00+00:00",
                model=settings.INTERVIEW_MODEL, tokens_before=2000, tokens_after=200,
            ).to_dict()
        project = save_project(session.history, session.doc, compaction=compaction)
        path = tmp_path / f"{label}{'.baspec' if package else '.json'}"
        path.write_bytes(build_project_package(project) if package else json.dumps(project).encode())
        return path

    return make


class EvalClient:
    def __init__(self, *, warm=None, markdown=None, json_turn=None):
        self.messages = self
        self.requests = []
        self.options = None
        self.warm = warm or SimpleNamespace(
            content=[], stop_reason="max_tokens",
            usage=token_usage(input=100, cache_write=10_000, cache_write_1h=10_000),
        )
        self.markdown = markdown or text_turn(
            [f"<summary>{_markdown()}</summary>"],
            usage=token_usage(input=100, output=200, cache_read=10_000),
        )
        self.json_turn = json_turn or text_turn(
            [json.dumps(_fields())], usage=token_usage(input=100, output=200, cache_read=10_000),
        )

    def with_options(self, **options):
        self.options = options
        return self

    def create(self, **request):
        self.requests.append(("warm", request))
        if isinstance(self.warm, Exception):
            raise self.warm
        return self.warm

    def stream(self, **request):
        arm = "json" if "format" in request["output_config"] else "markdown"
        self.requests.append((arm, request))
        turn = self.json_turn if arm == "json" else self.markdown
        if isinstance(turn, Exception):
            raise turn
        return _FakeStreamCtx(turn)


def _case(path, max_tokens=None):
    return evaluator.prepare_case(path, max_tokens or settings.CHAT_COMPACTION_MAX_TOKENS)


@pytest.mark.parametrize("package", [False, True])
@pytest.mark.parametrize("re_compaction", [False, True])
def test_real_loader_and_builder_preserve_prefix_and_retention(saved_session, package, re_compaction):
    path = saved_session(package=package, re_compaction=re_compaction)
    raw = path.read_bytes()
    active = sessions.get_session()
    active_before = copy.deepcopy(active.history)
    case = _case(path)
    request = case.request
    candidate = evaluator.json_request(request)
    assert path.read_bytes() == raw
    assert active.history == active_before and active.compaction is None
    assert case.re_compaction is re_compaction
    assert case.near_threshold and case.covers_turns == 5
    assert candidate["messages"][:-1] == request["messages"][:-1]
    for key in ("model", "system", "tools", "thinking", "max_tokens"):
        assert candidate[key] == request[key]
    assert candidate["output_config"]["effort"] == request["output_config"]["effort"]
    assert candidate["output_config"]["format"]["schema"] == evaluator.SUMMARY_SCHEMA
    assert "tool_choice" not in candidate and "container" not in candidate
    tail = candidate["messages"][-1]["content"][0]
    assert "cache_control" not in tail
    assert "Do not call any tools" in tail["text"]
    assert "Be sure to preserve:" in tail["text"]
    assert "No project facts are recorded yet." in tail["text"]
    assert "Nothing is waiting on the user." in tail["text"]
    assert "last 3 exchanges" in tail["text"]
    assert ("yours replaces it" in tail["text"]) is re_compaction
    marked = [i for i, message in enumerate(candidate["messages"]) if isinstance(message["content"], list)
              and any(block.get("cache_control") for block in message["content"])]
    assert marked == [len(candidate["messages"]) - 4]  # Before the final typed exchange.


@pytest.mark.parametrize("stop", ["end_turn", "stop_sequence"])
def test_json_normalizes_to_legacy_summary_without_losing_exact_details(stop):
    summary = evaluator.json_summary([{"type": "text", "text": json.dumps(_fields())}], stop)
    assert summary == _markdown()


@pytest.mark.parametrize("text,code", [
    ("{}", "invalid_fields"),
    ("[]", "invalid_fields"),
    (json.dumps({**_fields(), "extra": "x"}), "invalid_fields"),
    (json.dumps({**_fields(), SUMMARY_HEADINGS[0]: 4}), "invalid_fields"),
    (json.dumps({**_fields(), SUMMARY_HEADINGS[0]: "  "}), "invalid_fields"),
    ('{"Exact details":"a","Exact details":"b"}', "invalid_json"),
    ("\x60\x60\x60json\n{}\n\x60\x60\x60", "invalid_json"),
    (json.dumps(_fields()) + " trailing", "invalid_json"),
    ("not json", "invalid_json"),
])
def test_json_rejects_unusable_summaries(text, code):
    with pytest.raises(CompactionError) as caught:
        evaluator.json_summary([{"type": "text", "text": text}], "end_turn")
    assert caught.value.code == code


@pytest.mark.parametrize("stop,block,code", [
    ("max_tokens", "text", "incomplete"), ("pause_turn", "text", "incomplete"),
    ("refusal", "text", "refused"), ("end_turn", "tool_use", "tool_call"),
    ("end_turn", "server_tool_use", "tool_call"),
])
def test_json_honors_completion_and_tool_guards(stop, block, code):
    with pytest.raises(CompactionError) as caught:
        evaluator.json_summary([{"type": block, "text": json.dumps(_fields())}], stop)
    assert caught.value.code == code


def test_json_bounds_normalized_output(monkeypatch):
    monkeypatch.setattr(evaluator, "MAX_SUMMARY_CHARS", len(_markdown()) - 1)
    with pytest.raises(CompactionError, match="Runaway"):
        evaluator.json_summary([{"type": "text", "text": json.dumps(_fields())}], "end_turn")


def test_metrics_bill_cache_subtotals_once_and_include_tools(saved_session):
    request = _case(saved_session()).request
    usage = token_usage(input=100, output=300, thinking=100, cache_read=2000,
                        cache_write=3000, cache_write_1h=2000, searches=1)
    result = evaluator.metrics(SimpleNamespace(usage=usage), request, 2.0, 0.5)
    assert result["usage_complete"]
    assert result["estimated_cost_usd"] == estimate_usage_cost(request["model"], {
        "input_tokens": 100, "output_tokens": 300, "cache_read_input_tokens": 2000,
        "cache_creation_input_tokens": 3000, "cache_creation_1h_input_tokens": 2000,
        "web_search_requests": 1,
    })
    assert result["cache_read_fraction"] == pytest.approx(2000 / 5100)


@pytest.mark.parametrize("change", ["missing", "negative", "bool", "missing_1h", "impossible_1h", "unknown_price"])
def test_unknown_or_invalid_measurements_never_become_free_success(saved_session, change):
    request = _case(saved_session()).request
    usage = token_usage(input=100, output=200, cache_write=3000, cache_write_1h=2000)
    if change == "missing":
        del usage.input_tokens
    elif change == "negative":
        usage.output_tokens = -1
    elif change == "bool":
        usage.input_tokens = True
    elif change == "missing_1h":
        del usage.cache_creation
    elif change == "impossible_1h":
        usage.cache_creation.ephemeral_1h_input_tokens = 4000
    else:
        request["model"] = "unpriced-model"
    result = evaluator.metrics(SimpleNamespace(usage=usage), request, 2.0, 0.5)
    assert result["estimated_cost_usd"] is None


def test_billed_invalid_output_survives_in_checkpoint(saved_session, tmp_path):
    case = _case(saved_session())
    folder = tmp_path / "output"
    folder.mkdir()
    client = EvalClient(json_turn=text_turn(["{}"], usage=token_usage(input=100, output=20, cache_read=10_000)))
    report = evaluator.run_cases(client, [case], folder)
    failed = report["cases"][0]["arms"]["json"]
    assert failed["status"] == "invalid_summary"
    assert failed["parse_error"] == "invalid_fields"
    assert failed["estimated_cost_usd"] > 0
    assert report["calls_recorded"] == 3 and report["billing_complete"]
    assert report["estimated_total_cost_usd"] == pytest.approx(
        sum(record["estimated_cost_usd"] for record in [report["cases"][0]["warm"], *report["cases"][0]["arms"].values()])
    )
    assert json.loads((folder / "results.json").read_text()) == report
    assert not (folder / f"{case.artifact}-json.md").exists()


def test_unexpected_content_failure_retains_bill(saved_session, monkeypatch):
    request = _case(saved_session()).request
    monkeypatch.setattr(evaluator.conversation, "_content_blocks_to_dicts",
                        lambda blocks: (_ for _ in ()).throw(ValueError("private body")))
    record, summary = evaluator.run_arm(EvalClient(), request, "markdown")
    assert record["status"] == "invalid_summary" and record["estimated_cost_usd"] > 0
    assert record["parse_error"] == "unexpected_content" and summary is None
    assert "private body" not in json.dumps(record)


@pytest.mark.parametrize("failure", ["warm", "markdown", "json"])
def test_request_errors_stop_without_retries_or_private_error_body(saved_session, tmp_path, failure):
    folder = tmp_path / "output"
    folder.mkdir()
    cases = [_case(saved_session(str(index))) for index in range(2)]
    kwargs = {("json_turn" if failure == "json" else failure): bad_request("PRIVATE provider body")}
    client = EvalClient(**kwargs)
    report = evaluator.run_cases(client, cases, folder)
    assert len(client.requests) == {"warm": 1, "markdown": 2, "json": 3}[failure]
    assert report["estimated_total_cost_usd"] is None and not report["billing_complete"]
    assert "PRIVATE" not in (folder / "results.json").read_text()
    assert evaluator.assess(folder)["assessment"] == "insufficient_or_regressed"


@pytest.mark.parametrize("change", ["output", "no_cache", "content", "wrong_stop", "tool"])
def test_invalid_warm_aborts_before_summaries(saved_session, tmp_path, change):
    warm = EvalClient().warm
    if change == "output":
        warm.usage.output_tokens = 1
    elif change == "no_cache":
        warm.usage = token_usage(input=100)
    elif change == "content":
        warm.content = [{"type": "text", "text": "unexpected"}]
    elif change == "wrong_stop":
        warm.stop_reason = "end_turn"
    else:
        warm.usage.server_tool_use.web_search_requests = 1
    folder = tmp_path / "output"
    folder.mkdir()
    client = EvalClient(warm=warm)
    report = evaluator.run_cases(client, [_case(saved_session())], folder)
    assert report["cases"][0]["warm"]["status"] == "invalid_warm"
    assert len(client.requests) == 1


@pytest.mark.parametrize("arm", ["warm", "json"])
def test_interruption_keeps_attempted_billing_unknown(saved_session, tmp_path, arm):
    class InterruptedClient(EvalClient):
        def create(self, **request):
            if arm == "warm":
                raise KeyboardInterrupt
            return super().create(**request)

        def stream(self, **request):
            if "format" in request["output_config"]:
                raise KeyboardInterrupt
            return super().stream(**request)

    folder = tmp_path / "output"
    folder.mkdir()
    with pytest.raises(KeyboardInterrupt):
        evaluator.run_cases(InterruptedClient(), [_case(saved_session())], folder)
    report = json.loads((folder / "results.json").read_text())
    row = report["cases"][0]
    record = row["warm"] if arm == "warm" else row["arms"]["json"]
    assert record["status"] == "in_flight" and not report["run_complete"]
    assert not report["billing_complete"] and report["estimated_total_cost_usd"] is None
    assert report["estimated_known_cost_usd"] >= 0
    assert "missing_or_failed_calls" in evaluator.assess(folder)["reasons"]


def test_cli_requires_explicit_run_and_new_output_directory(saved_session, tmp_path, monkeypatch, capsys):
    path = saved_session()
    before = path.read_bytes()
    client = EvalClient()
    factories = []
    monkeypatch.setattr(evaluator, "get_client", lambda: factories.append(True) or client)
    output = tmp_path / "new-output"
    assert evaluator.main([str(path), "--out-dir", str(output)]) == 0
    assert not output.exists() and not factories
    assert "No requests sent." in capsys.readouterr().out
    assert evaluator.main([str(path), "--run"]) == 2
    assert not factories
    assert evaluator.main([str(path), "--run", "--out-dir", str(tmp_path)]) == 2
    assert not factories
    assert evaluator.main([str(path), "--run", "--out-dir", str(output)]) == 0
    assert len(client.requests) == 3
    assert client.options["max_retries"] == 0 and client.options["timeout"].read == 180
    warm, markdown, candidate = [request for _, request in client.requests]
    assert warm == {**markdown, "max_tokens": 0} and "format" not in warm["output_config"]
    assert candidate["max_tokens"] == markdown["max_tokens"] == settings.CHAT_COMPACTION_MAX_TOKENS
    assert path.read_bytes() == before
    stdout = capsys.readouterr().out
    public = (output / "results.json").read_text()
    assert str(path) not in stdout + public and "42 gpm" not in stdout + public
    review = json.loads((output / "review.json").read_text())
    assert review["cases"][evaluator._digest(before)]["source"] == str(path)
    assert "42 gpm" in (output / f"{evaluator._digest(before)}-json.md").read_text()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits and umask; Windows uses folder ACLs")
@pytest.mark.parametrize("umask", [0o000, 0o022, 0o077])
def test_private_captures_are_owner_only_from_creation(saved_session, tmp_path, monkeypatch, umask):
    source = saved_session()
    output = tmp_path / "nested" / "private-output"
    client = EvalClient()
    directory_modes = []
    temporary_modes = []
    real_temporary = tempfile.NamedTemporaryFile

    def get_client():
        directory_modes.append(stat.S_IMODE(output.stat().st_mode))
        return client

    def inspect_temporary(*args, **kwargs):
        handle = real_temporary(*args, **kwargs)
        # Observe the file before any confidential content is written.
        temporary_modes.append(stat.S_IMODE(os.fstat(handle.fileno()).st_mode))
        assert os.fstat(handle.fileno()).st_size == 0
        return handle

    monkeypatch.setattr(evaluator, "get_client", get_client)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", inspect_temporary)
    previous_umask = os.umask(umask)
    try:
        result = evaluator.main([str(source), "--run", "--out-dir", str(output)])
    finally:
        os.umask(previous_umask)
    assert result == 0
    assert directory_modes == [0o700]
    assert temporary_modes and set(temporary_modes) == {0o600}
    captures = list(output.iterdir())
    assert len(captures) == 4
    assert {stat.S_IMODE(path.stat().st_mode) for path in captures} == {0o600}
    assert not list(output.glob("*.tmp"))


def test_preflight_validates_all_selected_inputs_before_client(saved_session, tmp_path, monkeypatch):
    good = saved_session()
    bad = saved_session("short", turns=1)
    monkeypatch.setattr(evaluator, "get_client", lambda: pytest.fail("No paid client before complete preflight"))
    folder = tmp_path / "output"
    assert evaluator.main([str(good), str(bad), "--run", "--out-dir", str(folder)]) == 2
    assert not folder.exists()


@pytest.mark.parametrize("args", [
    ["--max-sessions", "0"], ["--max-sessions", "11"],
    ["--max-tokens", "255"], ["--max-tokens", "64001"],
])
def test_cli_budget_caps_before_client(saved_session, monkeypatch, args):
    monkeypatch.setattr(evaluator, "get_client", lambda: pytest.fail("No API client"))
    assert evaluator.main([str(saved_session()), "--run", *args]) == 2


def test_resaved_and_repackaged_histories_deduplicate_before_session_cap(saved_session, tmp_path, monkeypatch):
    original = saved_session("same")
    project = json.loads(original.read_text())
    project["saved_at"] = "2026-09-30T23:59:59+00:00"
    copy_path = tmp_path / "copy.json"
    copy_path.write_text(json.dumps(project))
    packaged = tmp_path / "copy.baspec"
    packaged.write_bytes(build_project_package(project))
    different = saved_session("different")
    client = EvalClient()
    monkeypatch.setattr(evaluator, "get_client", lambda: client)
    folder = tmp_path / "output"
    assert evaluator.main([str(original), str(copy_path), str(packaged), str(different),
                           "--max-sessions", "2", "--run", "--out-dir", str(folder)]) == 0
    assert len(client.requests) == 6
    assert [arm for arm, _ in client.requests] == ["warm", "markdown", "json", "warm", "json", "markdown"]
    assert len(evaluator._paths([str(tmp_path / "*.baspec"), str(original), str(original)])) == 2


@pytest.fixture
def reviewed_run(saved_session, tmp_path):
    folder = tmp_path / "output"
    folder.mkdir()
    cases = [_case(saved_session(str(index))) for index in range(5)]
    evaluator.run_cases(EvalClient(), cases, folder)
    review_path = folder / "review.json"
    review = json.loads(review_path.read_text())
    review["cache_conditions_reviewed"] = True
    review["cache_conditions_notes"] = "Fake-only test: fresh prefixes; schema coldness is not claimed."
    for entry in review["cases"].values():
        entry["checks"] = dict.fromkeys(evaluator._FIDELITY_CHECKS, True)
    evaluator._write(review_path, review)
    return folder


def test_assessment_requires_review_and_never_adopts(reviewed_run, monkeypatch, capsys):
    monkeypatch.setattr(evaluator, "get_client", lambda: pytest.fail("Assessment is offline"))
    result = evaluator.assess(reviewed_run)
    assert result == {"adoption": "pending", "assessment": "ready_for_owner_decision", "reasons": [], "sessions": 5}
    assert evaluator.main(["--assess", str(reviewed_run)]) == 0
    assert '"adoption": "pending"' in capsys.readouterr().out
    review_path = reviewed_run / "review.json"
    review = json.loads(review_path.read_text())
    review["cache_conditions_reviewed"] = None
    for entry in review["cases"].values():
        entry["checks"] = dict.fromkeys(evaluator._FIDELITY_CHECKS)
    evaluator._write(review_path, review)
    assert set(evaluator.assess(reviewed_run)["reasons"]) == {"cache_conditions_review_pending", "fidelity_review_pending"}


@pytest.mark.parametrize("change,reason", [
    ("cache", "cache_regression"), ("cost", "cost_regression"), ("latency", "latency_regression"),
    ("incomplete", "incomplete_measurements"), ("no_cost", "incomplete_measurements"),
    ("nan", "incomplete_measurements"), ("cache_gt_one", "incomplete_measurements"),
    ("missing_time", "first_output_timing_missing"), ("bad_time", "first_output_timing_missing"),
    ("baseline_miss", "baseline_cache_miss"), ("failed", "missing_or_failed_calls"),
    ("short_dataset", "need_5_to_10_distinct_sessions"), ("duplicate", "need_5_to_10_distinct_sessions"),
    ("small_session", "sessions_not_near_threshold"), ("pilot", "pilot_output_budget"),
    ("interrupted", "missing_or_failed_calls"),
])
def test_one_bad_pair_blocks_positive_assessment(reviewed_run, change, reason):
    report_path = reviewed_run / "results.json"
    report = json.loads(report_path.read_text())
    case = report["cases"][0]
    candidate = case["arms"]["json"]
    if change == "cache":
        candidate["cache_read_fraction"] -= 0.03
    elif change == "cost":
        candidate["estimated_cost_usd"] *= 1.06
    elif change == "latency":
        candidate["elapsed_seconds"] *= 1.11
    elif change == "incomplete":
        candidate["usage_complete"] = False
    elif change == "no_cost":
        candidate["estimated_cost_usd"] = None
    elif change == "nan":
        candidate["elapsed_seconds"] = float("nan")
    elif change == "cache_gt_one":
        candidate["cache_read_fraction"] = 1.1
    elif change == "missing_time":
        candidate["first_output_seconds"] = None
    elif change == "bad_time":
        candidate["first_output_seconds"] = -1
    elif change == "baseline_miss":
        case["arms"]["markdown"]["usage"]["cache_read_input_tokens"] = 0
    elif change == "failed":
        candidate["status"] = "invalid_summary"
    elif change == "short_dataset":
        report["cases"].pop()
    elif change == "duplicate":
        report["cases"][1]["history_sha256"] = case["history_sha256"]
    elif change == "small_session":
        case["near_threshold"] = False
    elif change == "interrupted":
        report["run_complete"] = False
    else:
        report["production_output_budget"] = False
    evaluator._write(report_path, report)
    result = evaluator.assess(reviewed_run)
    assert result["adoption"] == "pending" and result["assessment"] == "insufficient_or_regressed"
    assert reason in result["reasons"]


@pytest.mark.parametrize("change,reason", [
    ("source", "source_changed_since_run"), ("summary", "summary_changed_since_run"),
    ("fidelity", "fidelity_regression"),
])
def test_review_is_bound_to_source_and_generated_summaries(reviewed_run, change, reason):
    review_path = reviewed_run / "review.json"
    review = json.loads(review_path.read_text())
    artifact, entry = next(iter(review["cases"].items()))
    if change == "source":
        from pathlib import Path
        Path(entry["source"]).write_text("{}")
    elif change == "summary":
        (reviewed_run / f"{artifact}-json.md").write_text("edited")
    else:
        entry["checks"][evaluator._FIDELITY_CHECKS[0]] = False
        evaluator._write(review_path, review)
    assert reason in evaluator.assess(reviewed_run)["reasons"]
