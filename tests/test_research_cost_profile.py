"""The research cost profiler reads what saved rounds cost, and nothing else.

``tools/research_cost_profile.py`` (Research and Final QC cost, Tier 1,
Chunk 1) is the before-and-after instrument for the plan's later chunks: it
reports each saved research round's per-area usage, what it cost at list
prices, and the uncached share of the input side. These tests hold it to
the files the app really writes (built through the production save and
brief-export paths, never hand-typed shapes where a real one exists), to the
pricing table, to its deduplication rule, and to its privacy promise.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import project_brief, sessions, settings, usage_ledger
from backend.app import create_app
from backend.research.engine import (
    RequirementsProfile,
    ResearchRound,
    legacy_round_key as engine_legacy_round_key,
    merge_research_profiles,
)
from tests.fakes import FakeClient, SequencedFakeClient, research_response, text_turn, tool_turn
from tests.test_research_api import _parse_sse
from tests.test_research_engine import DIM_KEYS, _item
from tools import research_cost_profile

REPO_ROOT = Path(__file__).resolve().parents[1]

# Probes that must never reach the pasteable report.
_CLIENT = "Zorblatt Hyperscale"
_REQUIREMENT = "Quintessential sprinkler rule seventeen"
_URL = "https://probe-authority.example.gov/secret-page"
_ERROR = "provider exploded while reading secret-page"

# Known per-area usage for the round the fixtures run. governing_codes is
# the one the assertions read in full.
_TOKENS = {
    "governing_codes": {"input": 120_000, "cache_read": 30_000, "cache_write": 50_000, "output": 9_000},
    "ahj_requirements": {"input": 40_000, "cache_read": 10_000, "cache_write": 20_000, "output": 3_000},
    "client_standards": {"input": 20_000, "cache_read": 0, "cache_write": 10_000, "output": 2_000},
    "site_environment": {"input": 10_000, "cache_read": 5_000, "cache_write": 5_000, "output": 1_000},
}


# ---------------------------------------------------------------------------
# Fixtures: real files through the production paths
# ---------------------------------------------------------------------------


def _client() -> TestClient:
    client = TestClient(create_app())
    client.post("/api/session/reset", json={"module_id": "hyperscale_fire"})
    return client


def _record_profile(client: TestClient, monkeypatch) -> None:
    edits = {
        "edits": [
            {
                "action": "set_project_profile",
                "target_id": "sec",
                "city": "Ashburn",
                "state": "Virginia",
                "country": "USA",
                "client": _CLIENT,
            }
        ]
    }
    fake = FakeClient([tool_turn(["Recorded."], edits), text_turn(["Done."])])
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    resp = client.post("/api/chat", json={"message": f"Ashburn VA, {_CLIENT}"})
    assert _parse_sse(resp.text)[-1]["type"] == "turn_complete"


def _wait_terminal(client: TestClient, timeout_s: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        snapshot = client.get("/api/research/status").json()
        if snapshot["status"] in ("complete", "failed"):
            return snapshot
        time.sleep(0.02)
    raise AssertionError("research never reached a terminal state")


def _researched_session(client: TestClient, monkeypatch, *, fail: str = "") -> None:
    """One research round with known usage per area.

    ``fail`` names an area whose reply never calls the output tool, so the
    area fails — and, since that reply was billed, its usage is still
    recorded on its status.
    """
    _record_profile(client, monkeypatch)
    scripts: dict[str, list] = {}
    for dim_id, key in DIM_KEYS.items():
        if dim_id == fail:
            # A reply that never calls the output tool fails the area on its
            # first response (it is not retried), and that response was
            # billed, so its usage is recorded on the status.
            scripts[key] = [
                research_response(
                    items=None,
                    searched_urls=[_URL],
                    stop_reason="end_turn",
                    tokens=_TOKENS[dim_id],
                )
            ]
            continue
        scripts[key] = [
            research_response(
                items=[_item(_REQUIREMENT, [_URL])] if dim_id == "governing_codes" else [],
                searched_urls=[_URL, "https://other.example.gov/a"],
                fetches=1,
                tokens=_TOKENS[dim_id],
            )
        ]
    fake = SequencedFakeClient(scripts)
    monkeypatch.setattr("backend.app.get_client", lambda: fake)
    assert client.post("/api/research/start").json()["ok"] is True
    assert _wait_terminal(client)["status"] == "complete"


def _saved_project(tmp_path: Path) -> Path:
    """The section's ``.baspec``, through the production save path."""
    data, _filename = sessions.project_package(sessions.get_session())
    path = tmp_path / f"{_CLIENT} 21 13 13.baspec"
    path.write_bytes(data)
    return path


def _profile(paths: list[Path], tmp_path: Path, *extra: str) -> str:
    out = tmp_path / "measurement.md"
    assert research_cost_profile.main([*map(str, paths), "--out", str(out), *extra]) == 0
    return out.read_text(encoding="utf-8")


def _row(report: str, first_cell: str, *, after: str = "") -> list[str]:
    """The cells of the first table row whose first cell is ``first_cell``."""
    text = report.split(after, 1)[1] if after else report
    for line in text.splitlines():
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if cells[0] == first_cell:
                return cells
    raise AssertionError(f"no row {first_cell!r} in the report")


def _usd(value: float) -> str:
    return f"${value:,.2f}"


def _expected_cost(tokens: dict[str, int], searches: int, model: str = "") -> float:
    """Term by term from the pricing table — the golden computation."""
    rates = settings.PRICING[model or settings.RESEARCH_MODEL]
    return (
        tokens["input"] * rates["input"]
        + tokens["cache_read"] * rates["cache_read"]
        + tokens["cache_write"] * rates["cache_write"]
        + tokens["output"] * rates["output"]
        + searches * settings.WEB_SEARCH_COST
    )


def _brief_file(tmp_path: Path, name: str, profile: dict | None) -> Path:
    """A minimal brief envelope around a research profile.

    Only for the cases no production path produces on demand (a legacy
    round replayed into a brief); the profiler reads nothing else from it.
    """
    path = tmp_path / name
    path.write_text(
        json.dumps({"kind": project_brief.PROJECT_BRIEF_KIND, "format": 1, "research_profile": profile}),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def test_a_saved_project_reports_each_rounds_dimensions(monkeypatch, tmp_path):
    client = _client()
    _researched_session(client, monkeypatch)
    report = _profile([_saved_project(tmp_path)], tmp_path)

    assert "from 1 research round(s) across 1 artifact(s)" in report
    gov = _TOKENS["governing_codes"]
    cells = _row(report, "`governing_codes`")
    assert cells[1] == "completed"
    assert cells[2:8] == [
        "2",  # searches: two retrieved URLs
        "1",  # fetches
        f"{gov['input']:,}",
        f"{gov['cache_read']:,}",
        f"{gov['cache_write']:,}",
        f"{gov['output']:,}",
    ]
    side = gov["input"] + gov["cache_read"] + gov["cache_write"]
    assert cells[8] == f"{gov['input'] / side * 100:.1f}%"
    assert cells[9] == _usd(_expected_cost(gov, 2))
    for dim_id in DIM_KEYS:
        assert _row(report, f"`{dim_id}`")[1] == "completed"

    # The headline is the whole set's uncached share: the number Chunk 4
    # exists to move and M1 records.
    total_in = sum(t["input"] for t in _TOKENS.values())
    total_side = sum(t["input"] + t["cache_read"] + t["cache_write"] for t in _TOKENS.values())
    assert (
        f"**Uncached share of the input side, all rounds: "
        f"{total_in / total_side * 100:.1f}%** — {total_in:,} of {total_side:,}"
    ) in report
    total_cost = sum(_expected_cost(t, 2) for t in _TOKENS.values())
    assert f"Estimated cost of these rounds: **{_usd(total_cost)}**" in report
    assert _row(report, "**round total**")[1] == "4 of 4 completed"


def test_a_failed_dimension_is_billed_and_included(monkeypatch, tmp_path):
    client = _client()
    _researched_session(client, monkeypatch, fail="site_environment")
    report = _profile([_saved_project(tmp_path)], tmp_path)

    cells = _row(report, "`site_environment`")
    assert cells[1] == "failed"
    # The failed area's billed response is on its row, counted once.
    site = _TOKENS["site_environment"]
    assert cells[4:8] == [f"{site[k]:,}" for k in ("input", "cache_read", "cache_write", "output")]
    assert _row(report, "**round total**")[1] == "3 of 4 completed"
    assert "3 completed, 1 failed" in report
    assert "A research area that failed was still billed, and is included." in report


def test_a_round_shared_by_a_brief_and_its_section_counts_once(monkeypatch, tmp_path):
    client = _client()
    _researched_session(client, monkeypatch)
    section = _saved_project(tmp_path)
    brief_bytes = client.get("/api/project/brief")
    assert brief_bytes.status_code == 200, brief_bytes.text
    brief = tmp_path / f"{_CLIENT}.basproject"
    brief.write_bytes(brief_bytes.content)

    alone = _profile([section], tmp_path)
    both = _profile([section, brief], tmp_path)

    assert "from 1 research round(s) across 2 artifact(s)" in both
    assert "1 round(s), 1 already counted." in both
    # The totals are the section's alone: the shared round is not doubled.
    assert _row(both, "**all rounds**") == _row(alone, "**all rounds**")
    # The same file named twice (a glob and a path) is one artifact.
    twice = _profile([section, section], tmp_path)
    assert "across 1 artifact(s)" in twice


def test_a_legacy_round_a_brief_carried_under_its_hash_counts_once(tmp_path):
    """A round saved without an id is keyed the way the app keys it.

    When a brief merges research, a legacy round is replayed under
    ``legacy_round_key`` as its ``round_id``, and RENUMBERED. Keying the
    section's copy by the bare ``(section, date, index)`` tuple would count
    that round twice; hashing it the engine's way lets the two meet.
    """
    legacy = RequirementsProfile.from_dict(
        {
            "dimension_statuses": [
                {"dimension_id": "governing_codes", "status": "completed", "input_tokens": 700}
            ],
            "research_date": "2026-08-01",
            "rounds": [
                {
                    "round_index": 1,
                    "research_date": "2026-08-01",
                    "section": "21 13 13",
                    "dimension_statuses": [
                        {"dimension_id": "governing_codes", "status": "completed", "input_tokens": 700}
                    ],
                }
            ],
        }
    )
    other = RequirementsProfile.from_dict(
        {
            "dimension_statuses": [
                {"dimension_id": "ahj_requirements", "status": "completed", "input_tokens": 50}
            ],
            "research_date": "2026-09-01",
            "rounds": [
                {
                    "round_index": 1,
                    "research_date": "2026-09-01",
                    "section": "21 30 00",
                    "round_id": "a" * 32,
                    "dimension_statuses": [
                        {"dimension_id": "ahj_requirements", "status": "completed", "input_tokens": 50}
                    ],
                }
            ],
        }
    )
    merged, report = merge_research_profiles(other, legacy)
    assert report.legacy_rounds == 1
    carried = merged.rounds[-1]
    # The premise: the brief's copy is renumbered and carries the hash.
    assert carried.round_index == 2
    assert carried.round_id == engine_legacy_round_key(legacy.rounds[0])

    section_file = _brief_file(tmp_path, "section.basproject", legacy.to_dict())
    brief_file = _brief_file(tmp_path, "project.basproject", merged.to_dict())
    text = _profile([section_file, brief_file], tmp_path)
    assert "from 2 research round(s) across 2 artifact(s)" in text
    assert _row(text, "**all rounds**")[4] == "750"


def test_the_legacy_round_key_is_the_engines():
    for section, date_, index in (("", "", 0), ("21 13 13", "2026-08-01", 1), ("23 05 48", "2026-09-23", 7)):
        record = ResearchRound(round_index=index, research_date=date_, section=section)
        assert research_cost_profile.legacy_round_key(section, date_, index) == engine_legacy_round_key(record)


def test_the_brief_kind_is_the_apps(tmp_path):
    assert research_cost_profile._BRIEF_KIND == project_brief.PROJECT_BRIEF_KIND
    # A brief an editor re-saved with a BOM and a leading newline is still one.
    profile = {"rounds": [{"round_index": 1, "research_date": "2026-09-23", "round_id": "c" * 32, "dimension_statuses": [{"dimension_id": "governing_codes", "status": "completed", "input_tokens": 3}]}]}
    path = tmp_path / "resaved.basproject"
    envelope = {"kind": project_brief.PROJECT_BRIEF_KIND, "format": 1, "research_profile": profile}
    path.write_bytes(b"\xef\xbb\xbf\n" + json.dumps(envelope).encode("utf-8"))
    assert "from 1 research round(s)" in _profile([path], tmp_path)


def test_a_legacy_profile_without_rounds_is_one_cumulative_round(monkeypatch, tmp_path):
    client = _client()
    _researched_session(client, monkeypatch)
    project = json.loads(json.dumps(sessions.project_payload(sessions.get_session())))
    profile = project["requirements_profile"]
    del profile["rounds"]
    legacy_project = tmp_path / "legacy-project.json"
    legacy_project.write_text(json.dumps(project), encoding="utf-8")

    report = _profile([legacy_project], tmp_path)
    assert "from 1 research round(s)" in report
    assert "· legacy, cumulative" in report
    assert "Legacy, cumulative: saved before research rounds were recorded" in report
    gov = _TOKENS["governing_codes"]
    assert _row(report, "`governing_codes`")[4] == f"{gov['input']:,}"

    # The same legacy profile re-saved by a newer build (which synthesizes
    # round 1 from it) is the same round, not a second one.
    resaved = RequirementsProfile.from_dict(profile).to_dict()
    assert "rounds" in resaved and not resaved["rounds"][0].get("round_id")
    brief = _brief_file(tmp_path, "resaved.basproject", resaved)
    both = _profile([legacy_project, brief], tmp_path)
    assert "from 1 research round(s) across 2 artifact(s)" in both


# ---------------------------------------------------------------------------
# Arithmetic
# ---------------------------------------------------------------------------


def test_the_cost_arithmetic_matches_the_pricing_table(tmp_path):
    tokens = {"input": 1_000_000, "cache_read": 2_000_000, "cache_write": 400_000, "output": 50_000}
    searches = 30
    usage = research_cost_profile.Usage(
        records=1,
        completed=1,
        input=tokens["input"],
        cache_read=tokens["cache_read"],
        cache_write=tokens["cache_write"],
        output=tokens["output"],
        searches=searches,
        fetches=4,
    )
    model, rates, _source = research_cost_profile._pricing_for("")
    assert model == settings.RESEARCH_MODEL
    golden = _expected_cost(tokens, searches)
    cost = research_cost_profile.estimated_cost(usage, rates, settings.WEB_SEARCH_COST)
    assert cost == pytest.approx(golden)
    # And the app's own meter agrees term for term (fetches carry no fee).
    assert cost == pytest.approx(
        usage_ledger.estimate_usage_cost(
            settings.RESEARCH_MODEL,
            {
                "input_tokens": tokens["input"],
                "cache_read_input_tokens": tokens["cache_read"],
                "cache_creation_input_tokens": tokens["cache_write"],
                "output_tokens": tokens["output"],
                "web_search_requests": searches,
                "web_fetch_requests": 4,
            },
        )
    )
    shares = usage.cost(rates, settings.WEB_SEARCH_COST)
    assert usage.uncached_share() == pytest.approx(1_000_000 / 3_400_000)

    status = {
        "dimension_id": "governing_codes",
        "status": "completed",
        "input_tokens": tokens["input"],
        "cache_read_input_tokens": tokens["cache_read"],
        "cache_creation_input_tokens": tokens["cache_write"],
        "output_tokens": tokens["output"],
        "web_search_requests": searches,
        "web_fetch_requests": 4,
    }
    brief = _brief_file(
        tmp_path,
        "p.basproject",
        {"rounds": [{"round_index": 1, "research_date": "2026-09-23", "round_id": "b" * 32, "dimension_statuses": [status]}]},
    )
    report = _profile([brief], tmp_path)
    cells = _row(report, "`governing_codes`")
    assert cells[9] == _usd(golden)
    assert cells[10] == f"{shares['output'] / golden * 100:.1f}%"

    # --model prices the same round at another model's list rates.
    opus = _profile([brief], tmp_path, "--model", settings.MODEL_OPUS_55)
    assert _row(opus, "`governing_codes`")[9] == _usd(
        _expected_cost(tokens, searches, settings.MODEL_OPUS_55)
    )
    assert f"Priced at `{settings.MODEL_OPUS_55}`'s list rates" in opus
    with pytest.raises(SystemExit) as refused:
        research_cost_profile.main([str(brief), "--model", "claude-unpriced-9"])
    assert refused.value.code == 2


def test_an_unpriced_research_model_falls_back_the_way_the_meter_does(monkeypatch):
    monkeypatch.setattr(settings, "RESEARCH_MODEL", "claude-unpriced-9")
    model, rates, source = research_cost_profile._pricing_for("")
    assert model == settings.MODEL_SONNET_5
    assert rates == settings.PRICING[settings.MODEL_SONNET_5]
    assert "claude-unpriced-9" in source and "falling back" in source


# ---------------------------------------------------------------------------
# Privacy, isolation and failure
# ---------------------------------------------------------------------------


def test_the_output_carries_no_text_names_or_paths(monkeypatch, tmp_path, capsys):
    client = _client()
    _researched_session(client, monkeypatch, fail="site_environment")
    section = _saved_project(tmp_path)
    # The probes really are in the file the profiler reads.
    saved = sessions.project_payload(sessions.get_session())
    assert _REQUIREMENT in json.dumps(saved)
    assert _URL in json.dumps(saved)
    assert _CLIENT in json.dumps(saved["requirements_profile"])

    # A hand-edited file putting the probes where ids, sections and dates go.
    hostile = _brief_file(
        tmp_path,
        f"{_CLIENT} brief.basproject",
        {
            "rounds": [
                {
                    "round_index": 1,
                    "research_date": _CLIENT,
                    "section": _REQUIREMENT,
                    "round_id": _CLIENT,
                    "dimension_statuses": [
                        {
                            "dimension_id": _URL,
                            "status": _REQUIREMENT,
                            "error": _ERROR,
                            "title": _CLIENT,
                            "input_tokens": 5,
                        }
                    ],
                }
            ]
        },
    )
    report = _profile([section, hostile], tmp_path)
    for probe in (_CLIENT, _REQUIREMENT, _URL, "probe-authority", "secret-page", "Zorblatt", str(tmp_path)):
        assert probe not in report, probe
    assert "unrecognized-" in report
    assert "not a section number" in report
    assert "undated" in report
    # The console line names the file, locally, so the owner can tell them apart.
    assert section.name in capsys.readouterr().err


def test_the_script_never_loads_the_client(tmp_path):
    brief = _brief_file(
        tmp_path,
        "p.basproject",
        {"rounds": [{"round_index": 1, "research_date": "2026-09-23", "dimension_statuses": [{"dimension_id": "governing_codes", "status": "completed", "input_tokens": 10}]}]},
    )
    code = (
        "import sys\n"
        "from tools import research_cost_profile as profiler\n"
        "assert 'backend.llm.client' not in sys.modules, 'client imported'\n"
        "assert 'backend.research.engine' not in sys.modules, 'engine imported'\n"
        f"assert profiler.main([{str(brief)!r}, '--out', {str(tmp_path / 'o.md')!r}]) == 0\n"
        "assert 'backend.llm.client' not in sys.modules, 'client imported by main'\n"
        "assert 'anthropic' not in sys.modules, 'anthropic imported by main'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "WARNING" not in result.stderr


def test_nothing_readable_is_a_clean_error(tmp_path, capsys):
    garbage = tmp_path / "garbage.baspec"
    garbage.write_bytes(b"\x00\x01 not a project")
    no_research = _brief_file(tmp_path, "empty.basproject", None)
    qc_export = tmp_path / "FINAL QC REPORT.json"
    qc_export.write_text(json.dumps({"report": {"run_id": "r1"}}), encoding="utf-8")
    out = tmp_path / "out.md"

    code = research_cost_profile.main(
        [str(garbage), str(no_research), str(qc_export), str(tmp_path / "missing.baspec"), "--out", str(out)]
    )
    assert code != 0
    err = capsys.readouterr().err
    assert "No readable research found in the given files. Nothing measured." in err
    assert "carries no research" in err
    assert not out.exists()
