"""Output parsing and the fact harvest, hardened (the 5.5 prompting upgrade, P55-1).

Four independent hardenings from Anthropic's Claude Sonnet 5.5 prompting
guide, none touching a request's shape:

- **A tool name that differs only in letter case.** The guide: the model
  "occasionally calls a declared tool by a name that differs only in letter
  case … Accept the call when the match is unambiguous … Or return a
  tool_result with is_error: true that states the exact expected name." The
  fan-outs' output-tool extractor accepts it (an exact name still wins); the
  chat, whose saved history is trimmed by exact tool names, answers with the
  exact name instead of dispatching.
- **A missing key names the key.** The one chat tool whose missing-key error
  did not say which key it wanted (``read_reference_doc``) now does.
- **The tagged-JSON fallback takes the last complete value.** The guide:
  "Don't take everything from the first { to the last }. The model
  occasionally writes a draft before its final JSON." Every fallback did
  exactly that; one helper replaces all five patterns.
- **The fact harvest thinks first and never accepts a cut-off answer.** The
  guide's "Think the problem through before you answer." ends its system
  prompt, a ``max_tokens`` stop fails even when it holds a payload, and the
  call's ceiling is its own knob.

Hermetic: every model call is a scripted fake.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import sessions, settings
from backend.compliance import checker as compliance_checker
from backend.harvest import (
    HARVEST_SYSTEM_PROMPT,
    HARVEST_TOOL_NAME,
    HarvestError,
    build_harvest_request,
    run_harvest,
)
from backend.llm import conversation
from backend.qc import engine as qc_engine
from backend.research import engine as research_engine
from backend.research.schema import (
    _TAGGED_JSON_MAX_ATTEMPTS,
    extract_tool_use_block,
    last_tagged_json_object,
)
from tests.fakes import (
    FakeClient,
    harvest_proposal,
    raw_turn,
    text_block,
    text_turn,
    token_usage,
    tool_turn,
    tool_use_block,
)
from tests.test_app import _SEED_EDITS, _parse_sse, _patch_client
from tests.test_app import _client as _app_client
from tests.test_harvest import _conversation, _patch_harvest
from tests.test_project_brief import _client as _brief_client
from tests.test_retry_resume import _QcHarness, _ResearchHarness


def _tool_use(name, tool_input, *, as_dict=False):
    if as_dict:
        return {"type": "tool_use", "id": "toolu_x", "name": name, "input": tool_input}
    return tool_use_block("toolu_x", name, tool_input)


def _response(*blocks, as_dict=False):
    if as_dict:
        return {"content": list(blocks)}
    return SimpleNamespace(content=list(blocks))


def _text_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=[text_block(text)])


# ---------------------------------------------------------------------------
# P55-1.1 — the output-tool extractor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("as_dict", [False, True], ids=["sdk-objects", "dicts"])
def test_an_exact_name_wins_over_a_later_case_insensitive_one(as_dict):
    """A casefold match is a fallback: an exact block anywhere in the reply
    wins, even when the mis-cased one comes after it."""
    response = _response(
        _tool_use("submit_qc_findings", {"which": "exact"}, as_dict=as_dict),
        _tool_use("Submit_QC_Findings", {"which": "casefold"}, as_dict=as_dict),
        as_dict=as_dict,
    )
    assert extract_tool_use_block(response, "submit_qc_findings") == {"which": "exact"}


@pytest.mark.parametrize("as_dict", [False, True], ids=["sdk-objects", "dicts"])
def test_a_case_insensitive_name_is_accepted_and_the_last_one_wins(as_dict):
    response = _response(
        _tool_use("SUBMIT_QC_FINDINGS", {"which": "first"}, as_dict=as_dict),
        _tool_use("Submit_Qc_Findings", {"which": "last"}, as_dict=as_dict),
        as_dict=as_dict,
    )
    assert extract_tool_use_block(response, "submit_qc_findings") == {"which": "last"}


@pytest.mark.parametrize("as_dict", [False, True], ids=["sdk-objects", "dicts"])
def test_no_match_is_none_and_a_different_name_is_never_accepted(as_dict):
    response = _response(
        _tool_use("submit_qc_verdict", {"which": "other tool"}, as_dict=as_dict),
        _tool_use("submit_qc_findingz", {"which": "near miss"}, as_dict=as_dict),
        as_dict=as_dict,
    )
    assert extract_tool_use_block(response, "submit_qc_findings") is None
    assert extract_tool_use_block(_response(as_dict=as_dict), "submit_qc_findings") is None


def test_the_last_exact_block_still_wins_and_a_non_dict_input_is_skipped():
    """The pre-existing contract, unchanged: reverse order, and a block whose
    input is not an object is passed over rather than returned."""
    response = _response(
        _tool_use("submit_qc_findings", {"which": "first"}),
        _tool_use("submit_qc_findings", {"which": "last"}),
        _tool_use("submit_qc_findings", ["not", "an", "object"]),
    )
    assert extract_tool_use_block(response, "submit_qc_findings") == {"which": "last"}


@pytest.fixture(params=[_ResearchHarness(), _QcHarness()], ids=["research", "qc"])
def harness(request):
    return request.param


def test_a_mis_cased_output_tool_call_completes_the_call(harness):
    """One assertion set over both engines (copy, don't import): a research
    area and a Final QC lens whose final reply names the output tool in the
    wrong case record their payload instead of failing as "no payload"."""
    final = harness.final()
    block = next(b for b in final.content if b.type == "tool_use")
    block.name = block.name.upper()
    call = harness.run([final])
    assert call.status == "completed", call.error
    assert call.error == ""


# ---------------------------------------------------------------------------
# P55-1.2 — the chat names the exact tool, and never dispatches a mis-cased one
# ---------------------------------------------------------------------------


def _tool_results(history):
    return [
        block
        for message in history
        if message["role"] == "user" and isinstance(message.get("content"), list)
        for block in message["content"]
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]


def test_a_mis_cased_chat_tool_is_told_its_exact_name_and_is_not_run(monkeypatch):
    fake = FakeClient(
        [
            tool_turn([], _SEED_EDITS, tool_id="toolu_1", name="Apply_Spec_Edits"),
            tool_turn([], _SEED_EDITS, tool_id="toolu_2", name="apply_spec_edits"),
            text_turn(["Done."]),
        ]
    )
    _patch_client(monkeypatch, fake)
    events = _parse_sse(_app_client().post("/api/chat", json={"message": "go"}).text)
    assert events[-1]["type"] == "turn_complete"
    # Only the corrected call edited the document.
    patches = [e for e in events if e["type"] == "doc_patch"]
    assert len(patches) == 1
    results = _tool_results(sessions.get_session().history)
    first, second = results
    assert first["tool_use_id"] == "toolu_1" and first["is_error"] is True
    assert "case-sensitive" in first["content"]
    assert "`apply_spec_edits`" in first["content"]
    assert "Nothing was run" in first["content"]
    assert second["tool_use_id"] == "toolu_2" and not second.get("is_error")
    assert sessions.get_session().doc.doc.number == "21 13 13"


def test_an_unknown_chat_tool_is_told_every_tool_it_can_call(monkeypatch):
    fake = FakeClient(
        [
            tool_turn([], {"x": 1}, name="make_coffee"),
            text_turn(["Sorry."]),
        ]
    )
    _patch_client(monkeypatch, fake)
    _parse_sse(_app_client().post("/api/chat", json={"message": "go"}).text)
    (result,) = _tool_results(sessions.get_session().history)
    assert result["is_error"] is True
    assert result["content"].startswith("Unknown tool: make_coffee.")
    declared = conversation._chat_client_tool_names()
    assert set(declared) == {
        tool["name"] for tool in conversation._chat_tools()
    } - {"web_search", "web_fetch"}
    for name in declared:
        assert f"`{name}`" in result["content"]
    # Server tools are the API's to run; a client call can never name one.
    assert "web_search" not in result["content"]
    assert "web_fetch" not in result["content"]


def test_a_read_reference_doc_call_without_ref_id_names_the_key():
    session = sessions.get_session()
    session.references.add(
        filename="owner.txt", text="An owner standard.", block_count=1, kind="txt"
    )
    result, _ = conversation._run_tool(
        session, {"type": "tool_use", "id": "toolu_r", "name": "read_reference_doc", "input": {}}
    )
    assert result["is_error"] is True
    assert "`ref_id` is required" in result["content"]
    assert "Attached documents: ref-1." in result["content"]
    # A wrong id keeps its own message.
    result, _ = conversation._run_tool(
        session,
        {"type": "tool_use", "id": "toolu_r", "name": "read_reference_doc", "input": {"ref_id": "ref-9"}},
    )
    assert "no reference document with id 'ref-9'" in result["content"]


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("apply_spec_edits", "'edits'"),
        ("create_figure", "'kind'"),
        ("suggest_prompts", "'prompts'"),
        ("track_followups", "'add'"),
        ("record_project_facts", "'record'"),
        ("apply_qc_fixes", "finding_ids"),
    ],
)
def test_every_chat_tool_names_the_key_an_empty_call_is_missing(name, key):
    """The audit behind P55-1.2: every chat client tool called with no input
    at all answers with an ``is_error`` naming what it expected. Only
    ``read_reference_doc`` needed a fix (above); these already did, and this
    keeps them doing it. (``recall_conversation`` names its keys too, but
    only once something is condensed — its own tests cover that.)"""
    result, _ = conversation._run_tool(
        sessions.get_session(),
        {"type": "tool_use", "id": "toolu_e", "name": name, "input": {}},
        qc_apply_staging=conversation._QcApplyStaging(),
    )
    assert result["is_error"] is True
    assert key in result["content"]


# ---------------------------------------------------------------------------
# P55-1.3 — the last complete tagged JSON value
# ---------------------------------------------------------------------------


def test_a_draft_then_the_final_block_parses_as_the_final():
    text = (
        'Draft: <qc_json>{"findings": ["draft"]}</qc_json>\n'
        'On reflection: <qc_json>{"findings": ["final"]}</qc_json>'
    )
    assert last_tagged_json_object(text, "qc_json") == {"findings": ["final"]}


def test_a_final_block_that_does_not_parse_falls_back_to_the_draft():
    text = (
        '<qc_json>{"findings": ["draft"]}</qc_json>'
        '<qc_json>{"findings": ["final", }</qc_json>'
    )
    assert last_tagged_json_object(text, "qc_json") == {"findings": ["draft"]}


def test_an_unclosed_draft_does_not_swallow_the_final_block():
    text = '<qc_json>{"findings": ["draft", \n<qc_json>{"findings": ["final"]}</qc_json>'
    assert last_tagged_json_object(text, "qc_json") == {"findings": ["final"]}


def test_nested_braces_and_whitespace_parse():
    text = '<qc_json>\n  {"a": {"b": {"c": [1, {"d": 2}]}}, "e": "}"}\n</qc_json>'
    assert last_tagged_json_object(text, "qc_json") == {
        "a": {"b": {"c": [1, {"d": 2}]}},
        "e": "}",
    }


def test_a_closing_tag_quoted_inside_the_json_does_not_cut_it_short():
    """Each opening is tried against every closing after it, so a value
    that quotes the closing tag still parses whole."""
    text = '<qc_json>{"note": "wrap the payload in </qc_json> tags"}</qc_json>'
    assert last_tagged_json_object(text, "qc_json") == {
        "note": "wrap the payload in </qc_json> tags"
    }


def test_pathological_nesting_is_none_rather_than_a_crash():
    depth = 200_000
    text = "<qc_json>" + '{"a":' * depth + "1" + "}" * depth + "</qc_json>"
    assert last_tagged_json_object(text, "qc_json") is None


@pytest.mark.parametrize(
    "text",
    [
        "",
        "no tags at all",
        "<qc_json>[1, 2, 3]</qc_json>",
        "<qc_json>not json</qc_json>",
        '<other_json>{"a": 1}</other_json>',
        '<qc_json>{"a": 1}',
    ],
)
def test_nothing_parses_to_none(text):
    assert last_tagged_json_object(text, "qc_json") is None


def test_a_reply_that_repeats_the_tag_endlessly_is_bounded():
    """The runaway bound: past the cap no further pair is tried."""
    noise = "<qc_json>x</qc_json>" * (_TAGGED_JSON_MAX_ATTEMPTS + 10)
    assert last_tagged_json_object('<qc_json>{"a": 1}</qc_json>' + noise, "qc_json") is None
    assert last_tagged_json_object(noise + '<qc_json>{"a": 1}</qc_json>', "qc_json") == {"a": 1}


def test_research_reads_the_final_block_newest_response_first():
    older = _text_response('<research_json>{"items": ["older"]}</research_json>')
    newest = _text_response(
        '<research_json>{"items": ["draft"]}</research_json> then '
        '<research_json>{"items": ["final"]}</research_json>'
    )
    payload, how = research_engine._parse_research_payload([older, newest])
    assert (payload, how) == ({"items": ["final"]}, "text_fallback")
    assert research_engine._RESEARCH_JSON_TAG == "research_json"


@pytest.mark.parametrize(
    ("constant", "tag"),
    [
        ("_FINDINGS_JSON_TAG", "qc_json"),
        ("_VERDICT_JSON_TAG", "qc_verdict_json"),
        ("_CONSOLIDATION_JSON_TAG", "qc_consolidation_json"),
    ],
)
def test_each_final_qc_fallback_reads_the_final_block(constant, tag):
    assert getattr(qc_engine, constant) == tag
    reply = _text_response(
        f'<{tag}>{{"v": "draft"}}</{tag}> revised: <{tag}>{{"v": "final"}}</{tag}>'
    )
    assert qc_engine._parse([reply], "submit_x", getattr(qc_engine, constant)) == {
        "v": "final"
    }


def test_final_qc_prefers_the_tool_over_any_tagged_text():
    reply = _response(
        text_block('<qc_json>{"v": "text"}</qc_json>'),
        _tool_use("submit_qc_findings", {"v": "tool"}),
    )
    assert qc_engine._parse([reply], "submit_qc_findings", qc_engine._FINDINGS_JSON_TAG) == {
        "v": "tool"
    }


def test_the_compliance_audit_reads_the_final_block():
    reply = _text_response(
        '<compliance_json>{"v": "draft"}</compliance_json>'
        '<compliance_json>{"v": "final"}</compliance_json>'
    )
    assert compliance_checker._parse_audit_payload(reply) == ({"v": "final"}, "text_fallback")
    assert compliance_checker._COMPLIANCE_JSON_TAG == "compliance_json"


def test_no_greedy_tagged_json_pattern_is_left_anywhere():
    """The five patterns are gone, and no new one took their place."""
    root = Path(__file__).resolve().parents[1] / "backend"
    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if r"(\{.*\})" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


# ---------------------------------------------------------------------------
# P55-1.4 / P55-1.5 — the fact harvest
# ---------------------------------------------------------------------------


def test_the_harvest_system_prompt_ends_with_the_guides_line():
    assert HARVEST_SYSTEM_PROMPT.endswith("\n\nThink the problem through before you answer.")


def _cut_off_reply():
    return raw_turn(
        [
            tool_use_block(
                "toolu_harvest",
                HARVEST_TOOL_NAME,
                {"proposals": [harvest_proposal("A partial list.")]},
            )
        ],
        stop_reason="max_tokens",
        usage=token_usage(input=2_000, output=900),
    )


def test_a_cut_off_harvest_is_refused_even_with_a_payload_and_carries_its_usage():
    session = sessions.get_session()
    _conversation(session, 1)
    inputs = build_harvest_request(session)
    fake = FakeClient([_cut_off_reply()])
    with pytest.raises(HarvestError) as info:
        run_harvest(fake, inputs, model=settings.INTERVIEW_MODEL, effort="medium")
    assert info.value.code == "harvest_cut_off"
    assert "cut off" in str(info.value)
    assert "run the harvest again" in str(info.value)
    assert info.value.usage.output_tokens == 900
    assert fake.messages.requests[0]["max_tokens"] == settings.HARVEST_MAX_TOKENS


def test_the_route_refuses_a_cut_off_harvest_meters_it_and_shows_nothing(monkeypatch):
    client = _brief_client()
    session = sessions.get_session()
    _conversation(session, 1)
    fake = _patch_harvest(monkeypatch, _cut_off_reply())
    resp = client.post("/api/project/facts/harvest")
    assert resp.status_code == 502
    body = resp.json()
    assert body["code"] == "harvest_cut_off"
    assert "proposals" not in body and "token" not in body
    assert len(fake.messages.requests) == 1, "never retried on its own"
    assert fake.messages.requests[0]["max_tokens"] == settings.HARVEST_MAX_TOKENS
    usage = client.get("/api/usage").json()["categories"]["harvest"]
    assert usage["input_tokens"] == 2_000 and usage["output_tokens"] == 900
    assert session.facts.items == []
    assert session.last_harvest_bubble == 0


def _harvest_ceiling_assignments() -> dict[str, ast.AST]:
    source = Path(settings.__file__).read_text(encoding="utf-8")
    found: dict[str, ast.AST] = {}
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id
            in (
                "HARVEST_MAX_TOKENS",
                "_HARVEST_MAX_TOKENS_DEFAULT",
                "_HARVEST_MAX_TOKENS_FLOOR",
            )
        ):
            found[node.targets[0].id] = node.value
    return found


def _is_min_of(node: ast.AST, constant: str) -> bool:
    """``min(<constant>, INTERVIEW_MAX_TOKENS)``, as written in settings.py."""
    return (
        isinstance(node, ast.Call)
        and getattr(node.func, "id", "") == "min"
        and [getattr(arg, "id", None) for arg in node.args]
        == [constant, "INTERVIEW_MAX_TOKENS"]
    )


def test_the_harvest_ceiling_ships_at_64k_with_a_floor():
    """Read from the source, so no developer environment can move it: 64k and
    a floor of 4096, both capped by the interview's ceiling (Codex, PR #236)."""
    found = _harvest_ceiling_assignments()
    assert found["_HARVEST_MAX_TOKENS_DEFAULT"].value == 64_000
    assert found["_HARVEST_MAX_TOKENS_FLOOR"].value == 4_096
    call = found["HARVEST_MAX_TOKENS"]
    assert getattr(call.func, "id", "") == "_int_env"
    name, default = call.args
    assert name.value == "BUILD_A_SPEC_HARVEST_MAX_TOKENS"
    assert _is_min_of(default, "_HARVEST_MAX_TOKENS_DEFAULT")
    (floor,) = [k.value for k in call.keywords if k.arg == "minimum"]
    assert _is_min_of(floor, "_HARVEST_MAX_TOKENS_FLOOR")


def _reload_with(monkeypatch, **env: str):
    for name in ("BUILD_A_SPEC_HARVEST_MAX_TOKENS", "BUILD_A_SPEC_MAX_TOKENS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return importlib.reload(settings)


def test_the_harvest_ceiling_reads_its_knob_and_clamps_to_the_floor(monkeypatch):
    try:
        assert _reload_with(
            monkeypatch, BUILD_A_SPEC_HARVEST_MAX_TOKENS="100"
        ).HARVEST_MAX_TOKENS == 4096
        assert _reload_with(
            monkeypatch, BUILD_A_SPEC_HARVEST_MAX_TOKENS="20000"
        ).HARVEST_MAX_TOKENS == 20_000
    finally:
        _reload_with(monkeypatch)
    assert settings.HARVEST_MAX_TOKENS == 64_000


def test_a_lower_interview_ceiling_still_caps_the_harvest(monkeypatch, caplog):
    """The harvest inherited ``BUILD_A_SPEC_MAX_TOKENS`` before it had a knob
    of its own, so an operator's lower global cap keeps binding it — and the
    floor never lifts it back above that cap (Codex, PR #236). Only an
    explicit harvest override goes above the global cap."""
    try:
        assert _reload_with(
            monkeypatch, BUILD_A_SPEC_MAX_TOKENS="32000"
        ).HARVEST_MAX_TOKENS == 32_000
        caplog.clear()
        with caplog.at_level("WARNING", logger="buildaspec.settings"):
            reloaded = _reload_with(monkeypatch, BUILD_A_SPEC_MAX_TOKENS="2000")
        assert reloaded.HARVEST_MAX_TOKENS == 2_000
        assert not [
            r for r in caplog.records if "BUILD_A_SPEC_HARVEST_MAX_TOKENS" in r.getMessage()
        ], "an unset harvest knob is never reported as below its floor"
        # Explicit wins, in both directions the floor allows — and an explicit
        # value under the floor IS reported (the control that makes the
        # silence above mean something).
        assert _reload_with(
            monkeypatch,
            BUILD_A_SPEC_MAX_TOKENS="32000",
            BUILD_A_SPEC_HARVEST_MAX_TOKENS="100000",
        ).HARVEST_MAX_TOKENS == 100_000
        caplog.clear()
        with caplog.at_level("WARNING", logger="buildaspec.settings"):
            reloaded = _reload_with(
                monkeypatch,
                BUILD_A_SPEC_MAX_TOKENS="32000",
                BUILD_A_SPEC_HARVEST_MAX_TOKENS="100",
            )
        assert reloaded.HARVEST_MAX_TOKENS == 4096
        assert [
            r for r in caplog.records if "BUILD_A_SPEC_HARVEST_MAX_TOKENS" in r.getMessage()
        ]
    finally:
        _reload_with(monkeypatch)
    assert settings.HARVEST_MAX_TOKENS == 64_000
    assert settings.INTERVIEW_MAX_TOKENS == settings.MODEL_MAX_OUTPUT_TOKENS
