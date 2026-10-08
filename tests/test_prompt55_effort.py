"""The 5.5 prompting upgrade, P55-3: draft passes finish in one turn, and
effort is re-based.

Two findings of the review of Anthropic's Sonnet 5.5 and Opus 5.5 prompting
guides:

- F3. At ``medium`` effort Sonnet 5.5 is "more likely to stop and check in
  with the user before it finishes" a long agentic task, and the guide says
  to "start at `medium` for well-specified tasks and move to `high` for
  harder or longer ones". The two whole-section passes (full draft, adapt
  imported) are the app's longest multistep turns, so their directives now
  say to carry the pass through in one turn, and every round of a READY
  pass runs at ``settings.DRAFT_PASS_EFFORT`` (default ``high``) instead of
  the interview's ``medium``. The effort is decided once per turn and the
  turn's trace records it.
- F6. Opus 5.5 recalibrated its levels: its ``medium`` "matches or exceeds
  Claude Opus 5 at `high`". Final QC's lens effort was chosen for Opus 5, so
  ``QC_EFFORT`` now defaults to ``medium`` (decision D4); the verifier's
  default is unchanged. Effort is a hashed QC input, so a result retained at
  ``high`` reads stale once — and the release note says so, which is only
  honest if it is true (pinned below).
"""
from __future__ import annotations

import ast
import importlib
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import sessions, settings
from backend.app import create_app
from backend.llm import conversation
from backend.llm import prompts
from backend.llm.conversation import turn_effort
from backend.llm.prompts import (
    ADAPT_IMPORTED_DIRECTIVE,
    FULL_DRAFT_DIRECTIVE,
    adapt_imported_directive,
    adapt_prerequisites_directive,
    draft_prerequisites,
    draft_prerequisites_directive,
    full_draft_directive,
)
from backend.qc.engine import run_final_qc
from backend.qc.schema import QC_LENSES
from backend.spec_doc.model import DocumentStore
from backend.spec_modules import DEFAULT_MODULE
from backend.tracing import config as trace_config
from backend.tracing import recorder as recorder_module
from backend.tracing.recorder import set_recorder
from tests.fakes import (
    FakeClient,
    SequencedFakeClient,
    qc_findings_response,
    qc_verdict_response,
    text_turn,
    tool_turn,
)

_SETTINGS_SOURCE = Path(settings.__file__).read_text(encoding="utf-8")
_CARRY = prompts._CARRY_THE_PASS_THROUGH


def _ready():
    return draft_prerequisites(
        section_number="21 13 13",
        section_title="WET-PIPE SPRINKLER SYSTEMS",
        project_type="Hyperscale Data Center",
        country="US",
    )


def _client() -> TestClient:
    return TestClient(create_app())


def _parse_sse(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def _efforts(requests: list[dict]) -> list[str]:
    return [request["output_config"]["effort"] for request in requests]


def _three_round_turn() -> list:
    """An edit, a chip staging and the closing message: three rounds."""
    return [
        tool_turn(
            ["Laying down PART 1. "],
            {
                "edits": [
                    {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"}
                ]
            },
            tool_id="toolu_e1",
        ),
        tool_turn(
            [],
            {"prompts": ["Keep going"]},
            tool_id="toolu_e2",
            name="suggest_prompts",
        ),
        text_turn(["Done. Two questions remain?"]),
    ]


def _chat(monkeypatch, client: TestClient, message: str, fake) -> list[dict]:
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    resp = client.post("/api/chat", json={"message": message})
    events = _parse_sse(resp.text)
    assert events[-1]["type"] == "turn_complete", events[-1]
    return events


def _assign_target(name: str) -> ast.Call:
    """The call a module-level ``NAME = _effort_env(...)`` assigns."""
    tree = ast.parse(_SETTINGS_SOURCE)
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
        ):
            assert isinstance(node.value, ast.Call)
            return node.value
    raise AssertionError(f"{name} is not assigned in settings.py")


# ---------------------------------------------------------------------------
# P55-3.1 — both whole-section directives carry the pass through
# ---------------------------------------------------------------------------


_WHOLE_SECTION = {
    "full draft (constant)": lambda: FULL_DRAFT_DIRECTIVE,
    "full draft (anchored)": lambda: full_draft_directive(_ready()),
    "adapt (constant)": lambda: ADAPT_IMPORTED_DIRECTIVE,
    "adapt (anchored)": lambda: adapt_imported_directive(_ready()),
}


@pytest.mark.parametrize("build", _WHOLE_SECTION.values(), ids=_WHOLE_SECTION.keys())
def test_both_whole_section_directives_carry_the_pass_through(build):
    text = build()
    assert f"- {_CARRY}" in text
    # Before the closing bullet, which keeps the ordering sentence last
    # (P55-2 pins it in every directive).
    assert text.index(_CARRY) < text.index("When the last edit is in")
    assert text.index(_CARRY) < text.index(prompts._REPLY_AFTER_TOOL_CALLS)


def test_the_carry_through_line_says_what_the_guide_says():
    """Adapted from the Sonnet 5.5 guide's "Keep working until everything the
    user asked for is done, and only stop to ask when you can't go on
    without the user"."""
    assert "in this one turn" in _CARRY
    assert "keep working until everything above is done" in _CARRY
    assert "Do not stop after a PART or an article to ask whether to continue" in _CARRY
    assert "Stop early only for a question you genuinely cannot default" in _CARRY
    assert "finish everything that does not depend on it first" in _CARRY


@pytest.mark.parametrize(
    "text",
    [
        draft_prerequisites_directive(draft_prerequisites()),
        adapt_prerequisites_directive(draft_prerequisites()),
    ],
    ids=["full-draft prerequisites", "adapt prerequisites"],
)
def test_a_collecting_turn_does_not_carry_it(text):
    """A turn that only asks for three facts forbids drafting; telling it to
    carry a pass through would contradict that."""
    assert _CARRY not in text


# ---------------------------------------------------------------------------
# P55-3.2 — DRAFT_PASS_EFFORT for every round of a READY pass, and only those
# ---------------------------------------------------------------------------


def test_the_shipped_draft_pass_effort_is_one_level_above_the_interview():
    call = _assign_target("DRAFT_PASS_EFFORT")
    assert getattr(call.func, "id", "") == "_effort_env"
    assert [arg.value for arg in call.args] == [
        "BUILD_A_SPEC_DRAFT_PASS_EFFORT",
        "high",
    ]
    # The boost only means something while the two differ.
    assert settings.DRAFT_PASS_EFFORT == "high"
    assert settings.INTERVIEW_EFFORT == "medium"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("xhigh", "xhigh"), ("  LOW ", "low"), ("bogus", "high"), ("", "high")],
)
def test_the_knob_reads_a_level_and_falls_back_to_high(monkeypatch, value, expected):
    monkeypatch.setenv("BUILD_A_SPEC_DRAFT_PASS_EFFORT", value)
    reloaded = importlib.reload(settings)
    try:
        assert reloaded.DRAFT_PASS_EFFORT == expected
    finally:
        monkeypatch.delenv("BUILD_A_SPEC_DRAFT_PASS_EFFORT", raising=False)
        importlib.reload(settings)


@pytest.mark.parametrize(
    "text",
    [
        FULL_DRAFT_DIRECTIVE,
        full_draft_directive(_ready()),
        ADAPT_IMPORTED_DIRECTIVE,
        adapt_imported_directive(_ready()),
        "\n  " + full_draft_directive(_ready()),
    ],
    ids=["full constant", "full anchored", "adapt constant", "adapt anchored", "leading space"],
)
def test_a_ready_pass_turn_runs_at_the_draft_pass_effort(text):
    assert turn_effort(text) == settings.DRAFT_PASS_EFFORT


@pytest.mark.parametrize(
    "text",
    [
        "Add a provision about hangers.",
        draft_prerequisites_directive(draft_prerequisites()),
        adapt_prerequisites_directive(
            draft_prerequisites(section_number="21 13 13", section_title="WET-PIPE")
        ),
        # Mentioning a pass is not running one: the match is the prefix.
        "Please do this: " + FULL_DRAFT_DIRECTIVE,
        FULL_DRAFT_DIRECTIVE[:120],
        "",
    ],
    ids=[
        "ordinary",
        "full-draft prerequisites",
        "adapt prerequisites",
        "directive not at the start",
        "a truncated directive",
        "empty",
    ],
)
def test_every_other_turn_runs_at_the_interview_effort(text):
    assert turn_effort(text) == settings.INTERVIEW_EFFORT


def test_the_decision_reads_the_settings_when_the_turn_starts(monkeypatch):
    """Setting the two knobs equal switches the boost off without a code
    change."""
    monkeypatch.setattr(settings, "DRAFT_PASS_EFFORT", settings.INTERVIEW_EFFORT)
    assert turn_effort(FULL_DRAFT_DIRECTIVE) == settings.INTERVIEW_EFFORT
    monkeypatch.setattr(settings, "DRAFT_PASS_EFFORT", "xhigh")
    assert turn_effort(FULL_DRAFT_DIRECTIVE) == "xhigh"


def test_every_round_of_a_full_draft_turn_carries_the_draft_effort(monkeypatch):
    """End to end: the button's own directive, through the ordinary chat."""
    from tests.test_full_draft import _establish_all

    client = _client()
    _establish_all(client)
    plan = client.post("/api/draft/full").json()
    assert plan["ready"] is True

    fake = FakeClient(_three_round_turn())
    _chat(monkeypatch, client, plan["message"], fake)
    assert _efforts(fake.messages.requests) == ["high", "high", "high"]


def test_every_round_of_an_adapt_turn_carries_the_draft_effort(monkeypatch):
    client = _client()
    fake = FakeClient(_three_round_turn())
    _chat(monkeypatch, client, adapt_imported_directive(_ready()), fake)
    assert _efforts(fake.messages.requests) == ["high", "high", "high"]


def test_an_ordinary_turn_carries_the_interview_effort(monkeypatch):
    client = _client()
    fake = FakeClient(_three_round_turn())
    _chat(monkeypatch, client, "Draft PART 1 for me, please.", fake)
    assert _efforts(fake.messages.requests) == ["medium", "medium", "medium"]


def test_a_collecting_turn_carries_the_interview_effort(monkeypatch):
    """The click on a blank page buys the prerequisites turn, which only asks
    — nothing there to think harder about."""
    client = _client()
    plan = client.post("/api/draft/full").json()
    assert plan["ready"] is False

    fake = FakeClient(
        [
            tool_turn(
                [], {"prompts": ["United States"]}, name="suggest_prompts"
            ),
            text_turn(["Which section is this?"]),
        ]
    )
    _chat(monkeypatch, client, plan["message"], fake)
    assert _efforts(fake.messages.requests) == ["medium", "medium"]


def test_the_boost_switched_off_sends_a_draft_pass_at_the_interview_effort(
    monkeypatch,
):
    monkeypatch.setattr(settings, "DRAFT_PASS_EFFORT", settings.INTERVIEW_EFFORT)
    client = _client()
    fake = FakeClient(_three_round_turn())
    _chat(monkeypatch, client, full_draft_directive(_ready()), fake)
    assert _efforts(fake.messages.requests) == ["medium"] * 3


# ---------------------------------------------------------------------------
# P55-3.3 — decided once per turn, and recorded in the turn's trace
# ---------------------------------------------------------------------------


class _SettingsFlipClient:
    """A fake that changes both effort knobs the moment the first request is
    sent. A turn that re-decided its effort per round would send its later
    rounds at the new value."""

    def __init__(self, turns: list, monkeypatch):
        self._inner = FakeClient(turns)
        self._monkeypatch = monkeypatch
        self.messages = self
        self.requests = self._inner.messages.requests

    def stream(self, **request):
        ctx = self._inner.messages.stream(**request)
        if len(self.requests) == 1:
            self._monkeypatch.setattr(settings, "DRAFT_PASS_EFFORT", "low")
            self._monkeypatch.setattr(settings, "INTERVIEW_EFFORT", "low")
        return ctx


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (full_draft_directive(_ready()), "high"),
        ("Draft PART 1 for me, please.", "medium"),
    ],
    ids=["draft pass", "ordinary"],
)
def test_the_effort_is_decided_once_per_turn(monkeypatch, message, expected):
    client = _client()
    fake = _SettingsFlipClient(_three_round_turn(), monkeypatch)
    _chat(monkeypatch, client, message, fake)
    assert _efforts(fake.requests) == [expected] * 3


def test_the_request_inputs_carry_the_turn_effort():
    """The builder reads the captured value, never the settings."""
    session = sessions.get_session()
    request = conversation._build_chat_request(
        conversation._ChatRequestInputs(
            history=[],
            new_messages=[
                {"role": "user", "content": [{"type": "text", "text": "hi"}]}
            ],
            module=session.module,
            model=settings.INTERVIEW_MODEL,
            max_tokens=1024,
            effort="xhigh",
        )
    )
    assert request["output_config"] == {"effort": "xhigh"}


@pytest.fixture
def trace_env(monkeypatch, tmp_path):
    """Opt tracing back in against a tmp dir (the test_tracing pattern)."""
    monkeypatch.setenv(trace_config.ENV_TRACE, "1")
    monkeypatch.setenv(trace_config.ENV_TRACE_DIR, str(tmp_path / "traces"))
    set_recorder(None)
    yield
    rec = recorder_module.get_recorder()
    if rec is not None:
        rec.stop()
    set_recorder(None)


def _wait_events(predicate, timeout: float = 3.0) -> list[dict]:
    rec = recorder_module.get_recorder()
    assert rec is not None, "expected a live recorder"
    path = rec.trace_dir / "events.jsonl"
    deadline = time.monotonic() + timeout
    events: list[dict] = []
    while time.monotonic() < deadline:
        if path.exists():
            events = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if predicate(events):
                return events
        time.sleep(0.02)
    return events


def test_the_trace_records_the_effort_each_turn_ran_at(monkeypatch, trace_env):
    client = _client()
    fake = FakeClient(
        [text_turn(["Drafted?"]), text_turn(["Answered?"])]
    )
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    client.post("/api/chat", json={"message": full_draft_directive(_ready())})
    client.post("/api/chat", json={"message": "What else?"})

    events = _wait_events(
        lambda evs: sum(1 for e in evs if e["type"] == "prompt_refs") >= 2
    )
    recorded = [e["effort"] for e in events if e["type"] == "prompt_refs"]
    assert recorded == [settings.DRAFT_PASS_EFFORT, settings.INTERVIEW_EFFORT]
    # A level name, never text.
    assert all(value in settings.EFFORT_LEVELS for value in recorded)


def test_the_condensing_summary_keeps_the_interview_effort(monkeypatch):
    """The summary fork reads the cache the ORDINARY turns wrote, so its
    top-level effort stays the interview's even while the boost differs (a
    summary written right after a boosted turn misses the cache once — D3).
    Its own depth rides a per-message effort change instead
    (``tests/test_compaction_effort.py``)."""
    monkeypatch.setattr(settings, "DRAFT_PASS_EFFORT", "xhigh")
    session = sessions.get_session()
    history = [
        {"role": "user", "content": [{"type": "text", "text": "Turn 1"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Reply 1"}]},
    ]
    request = conversation._build_compaction_request(
        conversation._CompactionInputs(
            history=history,
            view_spec=None,
            module=session.module,
            model=settings.INTERVIEW_MODEL,
            keep_from=0,
            covers_turns=0,
            kept_turns=0,
            instruction="Summarize.",
            generation=session.generation,
            tokens_per_char=None,
            tokens_before=0,
            trigger="routine",
        )
    )
    assert request["output_config"] == {"effort": settings.INTERVIEW_EFFORT}
    assert conversation.summary_effort(request) == settings.COMPACTION_EFFORT


# ---------------------------------------------------------------------------
# P55-3.4 — Final QC's lens effort re-based; the verifier's unchanged
# ---------------------------------------------------------------------------


def test_the_shipped_qc_effort_is_medium():
    call = _assign_target("QC_EFFORT")
    assert getattr(call.func, "id", "") == "_effort_env"
    assert [arg.value for arg in call.args] == ["BUILD_A_SPEC_QC_EFFORT", "medium"]


def test_the_verifier_default_follows_the_seat_model():
    """Still behind the explicitly-set global; otherwise the seat model's own
    level — "high" on Sonnet 5.5 (owner decision, 2026-10-08), "medium" (the
    Opus 5.5 level P55-3 set) on any other seat model."""
    call = _assign_target("QC_VERIFIER_EFFORT")
    assert getattr(call.func, "id", "") == "_effort_env"
    name, default = call.args
    assert name.value == "BUILD_A_SPEC_QC_VERIFIER_EFFORT"
    assert isinstance(default, ast.IfExp)
    fallback = default.orelse
    assert isinstance(fallback, ast.Call)
    assert ast.unparse(fallback) == (
        "_QC_VERIFIER_EFFORT_BY_MODEL.get(QC_VERIFIER_MODEL, 'medium')"
    )
    assert settings._QC_VERIFIER_EFFORT_BY_MODEL == {settings.MODEL_SONNET_55: "high"}


def test_the_lens_phases_default_to_medium_and_sonnet_seats_to_high():
    assert settings.QC_EFFORT == "medium"
    assert settings.QC_LENS_EFFORT == "medium"
    assert settings.QC_VERIFIER_MODEL == settings.MODEL_SONNET_55
    assert settings.QC_VERIFIER_EFFORT == "high"


_LENS_KEYS = {lens.lens_id: f"[[QC-LENS:{lens.lens_id}]]" for lens in QC_LENSES}


def _qc_store() -> DocumentStore:
    store = DocumentStore()
    store.begin_turn()
    store.apply_edits(
        [
            {
                "action": "replace",
                "target_id": "sec",
                "text": "WET-PIPE SPRINKLER SYSTEMS",
                "numbering": "21 13 13",
            },
            {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "text": "Comply with NFPA 13-2019 throughout.",
                "status": "assumed",
            },
        ]
    )
    store.commit_turn()
    return store


def _finding(title: str) -> dict:
    return {
        "title": title,
        "severity": "medium",
        "element_id": "pt1.a1.p1",
        "issue": "Issue.",
        "rationale": "Rationale.",
        "source_urls": [],
        "proposed_ops": None,
    }


def _qc_scripts() -> dict[str, list[object]]:
    """Two lenses flag the same provision, so the run has lens calls, one
    consolidation call and verifier seats — all three kinds of request."""
    scripts: dict[str, list[object]] = {
        key: [qc_findings_response(lens_id, findings=[])]
        for lens_id, key in _LENS_KEYS.items()
    }
    scripts[_LENS_KEYS["code_compliance"]] = [
        qc_findings_response("code_compliance", findings=[_finding("Stale edition")])
    ]
    scripts[_LENS_KEYS["completeness"]] = [
        qc_findings_response("completeness", findings=[_finding("Edition gap")])
    ]
    for title in ("Stale edition", "Edition gap"):
        scripts[title] = [qc_verdict_response(True), qc_verdict_response(True)]
    return scripts


def _run_qc(client, **kwargs):
    store = _qc_store()
    return run_final_qc(
        store.doc,
        None,
        DEFAULT_MODULE,
        client,
        model=settings.QC_MODEL,
        max_tokens=4096,
        version_index=store.index,
        started_at="2026-09-30T10:00:00-07:00",
        finished_at="2026-09-30T10:01:00-07:00",
        **kwargs,
    ), store


def _qc_efforts(client) -> dict[str, set[str]]:
    kinds: dict[str, set[str]] = {"lens": set(), "consolidation": set(), "verifier": set()}
    for request in client.requests:
        text = str(request.get("messages"))
        effort = request["output_config"]["effort"]
        if "[[QC-VERIFY:" in text:
            kinds["verifier"].add(effort)
        elif "[[QC-CONSOLIDATE:" in text:
            kinds["consolidation"].add(effort)
        elif "[[QC-LENS:" in text:
            kinds["lens"].add(effort)
    return kinds


def test_a_lens_and_a_grouping_call_go_at_medium_and_a_sonnet_seat_at_high():
    client = SequencedFakeClient(_qc_scripts())
    result, _store = _run_qc(
        client,
        batch_verification=False,
        verifier_model=settings.QC_VERIFIER_MODEL,
    )
    assert _qc_efforts(client) == {
        "lens": {"medium"},
        "consolidation": {"medium"},
        "verifier": {"high"},
    }
    assert result.effort == "medium"
    assert result.verifier_effort == "high"
    configuration = result.input_manifest["configuration"]
    assert configuration["effort"] == "medium"
    assert configuration["verifier_effort"] == "high"
    assert configuration["verifier_model"] == settings.MODEL_SONNET_55


@pytest.mark.parametrize(
    ("env", "lens", "verifier"),
    [
        ({"BUILD_A_SPEC_QC_EFFORT": "high"}, "high", "high"),
        ({"BUILD_A_SPEC_QC_LENS_EFFORT": "xhigh"}, "xhigh", "high"),
        ({"BUILD_A_SPEC_QC_VERIFIER_EFFORT": "low"}, "medium", "low"),
        (
            {"BUILD_A_SPEC_QC_EFFORT": "low", "BUILD_A_SPEC_QC_LENS_EFFORT": "high"},
            "high",
            "low",
        ),
    ],
    ids=["global", "lens only", "verifier only", "global plus lens"],
)
def test_every_override_keeps_working(monkeypatch, env, lens, verifier):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    reloaded = importlib.reload(settings)
    try:
        assert reloaded.QC_LENS_EFFORT == lens
        assert reloaded.QC_VERIFIER_EFFORT == verifier
    finally:
        for name in env:
            monkeypatch.delenv(name, raising=False)
        importlib.reload(settings)


def test_a_result_retained_at_high_reads_stale_against_the_new_default(monkeypatch):
    """The release note says a retained Final QC result reads stale once;
    this is what makes that sentence true. The check rebuilds the manifest
    from the LIVE settings, so a run made at the old "high" lens default no
    longer matches, while a run made at today's defaults still does."""
    # The freshness check reads the live transport too; pin it to the one
    # these runs used, so effort is the only thing that can differ.
    monkeypatch.setattr(settings, "QC_BATCH_VERIFICATION", False)

    def current(result, store) -> bool:
        return result.matches_inputs(store.index, store.doc, None, DEFAULT_MODULE)

    before, store = _run_qc(
        SequencedFakeClient(_qc_scripts()),
        batch_verification=False,
        lens_effort="high",
        verifier_effort="medium",
    )
    assert before.effort == "high"
    assert current(before, store) is False

    after, store = _run_qc(SequencedFakeClient(_qc_scripts()), batch_verification=False)
    assert current(after, store) is True
