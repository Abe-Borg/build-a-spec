"""The owner-run provider diagnostic, verified with fake clients only."""
from __future__ import annotations

import copy

import pytest
from anthropic.types import RedactedThinkingBlock, ThinkingBlock

from backend.llm.client import MissingApiKeyError
from backend.research.schema import PRESERVED_THINKING_BETA
from tests.fakes import FakeClient, bad_request, raw_turn, text_block
from tools import qc_thinking_binding_canary as canary

_PRIVATE = "NEVER_PRINT_THIS_SIGNATURE_OR_BODY"
_DROP = {"type": "thinking_dropped", "reason": "prefix_binding_mismatch", "path": _PRIVATE}


class _Client(FakeClient):
    def __init__(self, turns):
        super().__init__(turns)
        self.options = []

    def with_options(self, **options):
        self.options.append(options)
        return self


def _mint(*, blocks=None, stop="end_turn"):
    return raw_turn(blocks if blocks is not None else [
        ThinkingBlock(type="thinking", thinking=_PRIVATE, signature=_PRIVATE),
        text_block(_PRIVATE),
    ], stop_reason=stop)


def _replay(*, transformations=(), stop="end_turn"):
    return raw_turn([text_block(_PRIVATE)], stop_reason=stop,
                    input_transformations=None if transformations is None else list(transformations))


def _run(monkeypatch, turns, args=None):
    client = _Client(turns)
    monkeypatch.setattr(canary, "get_client", lambda: client)
    return canary.main(args or ["--run"]), client


def _assert_private(capsys):
    output = capsys.readouterr()
    assert _PRIVATE not in output.out + output.err
    return output


def test_default_never_builds_a_client_or_sends_a_request(monkeypatch, capsys):
    def no_client():
        raise AssertionError("client construction without --run")

    monkeypatch.setattr(canary, "get_client", no_client)
    assert canary.main([]) == 0
    assert "No request sent" in capsys.readouterr().out


def test_three_bounded_requests_use_real_blocks_and_production_binding_helper(monkeypatch, capsys):
    minted = _mint()
    before = copy.deepcopy(minted.content)
    result, client = _run(monkeypatch, [minted, _replay(), _replay(transformations=[_DROP], stop="max_tokens")])
    assert result == 0
    assert len(client.messages.requests) == 3
    (options,) = client.options
    assert options["max_retries"] == 0
    assert options["timeout"].read == 60
    assert options["timeout"].connect <= 5
    mint, control, edited = client.messages.requests
    assert mint["model"] == control["model"] == edited["model"] == canary.settings.QC_MODEL
    assert mint["max_tokens"] == 2048
    assert control["max_tokens"] == edited["max_tokens"] == 256
    assert mint["thinking"] == {"type": "adaptive"}
    assert "extra_headers" not in mint
    for request in (control, edited):
        assert request["thinking"] == {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
        assert request["extra_headers"] == {"anthropic-beta": PRESERVED_THINKING_BETA}
        assert request["messages"][1]["content"][0] == before[0].model_dump(mode="json", exclude_none=True)
    assert control["messages"][0] == mint["messages"][0]
    assert edited["messages"][0] != control["messages"][0]
    assert edited["messages"][1:] == control["messages"][1:]
    assert {k: v for k, v in edited.items() if k != "messages"} == {k: v for k, v in control.items() if k != "messages"}
    assert minted.content == before
    assert "passed" in _assert_private(capsys).out


def test_all_signed_and_redacted_blocks_must_be_reported_dropped(monkeypatch, capsys):
    minted = _mint(blocks=[
        ThinkingBlock(type="thinking", thinking=_PRIVATE, signature=_PRIVATE),
        RedactedThinkingBlock(type="redacted_thinking", data=_PRIVATE),
        text_block(_PRIVATE),
    ])
    result, client = _run(monkeypatch, [minted, _replay(), _replay(transformations=[_DROP, _DROP])])
    assert result == 0
    assert len(client.messages.requests) == 3
    assert "prefix_binding_mismatch=2" in _assert_private(capsys).out


@pytest.mark.parametrize("minted", [
    _mint(stop="max_tokens"), _mint(stop="refusal"), _mint(stop="pause_turn"),
    _mint(blocks=[text_block(_PRIVATE)]),
    _mint(blocks=[ThinkingBlock(type="thinking", thinking=_PRIVATE, signature="")]),
])
def test_incomplete_or_unsigned_mint_stops_before_replay(monkeypatch, capsys, minted):
    result, client = _run(monkeypatch, [minted])
    assert result == 1
    assert len(client.messages.requests) == 1
    assert "inconclusive" in _assert_private(capsys).err


@pytest.mark.parametrize("control", [
    _replay(transformations=None), _replay(transformations=[_DROP]),
    _replay(stop="refusal"), _replay(stop="pause_turn"),
])
def test_missing_telemetry_or_changed_control_stops_before_edited_replay(monkeypatch, capsys, control):
    result, client = _run(monkeypatch, [_mint(), control])
    assert result == 1
    assert len(client.messages.requests) == 2
    assert "inconclusive" in _assert_private(capsys).err


@pytest.mark.parametrize("edited", [
    _replay(transformations=None), _replay(),
    _replay(transformations=[{"type": "thinking_mismatch_allowed", "reason": "prefix_binding_mismatch"}]),
    _replay(transformations=[{"type": "thinking_dropped", "reason": "model_binding_mismatch"}]),
    _replay(transformations=[_DROP, _DROP]),
    _replay(transformations=[_DROP], stop="refusal"),
    _replay(transformations=[_DROP], stop="pause_turn"),
])
def test_http_success_without_expected_drop_is_inconclusive(monkeypatch, capsys, edited):
    result, client = _run(monkeypatch, [_mint(), _replay(), edited])
    assert result == 1
    assert len(client.messages.requests) == 3
    assert "inconclusive" in _assert_private(capsys).err


@pytest.mark.parametrize("stage", [0, 1, 2])
def test_provider_error_is_redacted_and_never_retried(monkeypatch, capsys, stage):
    turns = [_mint(), _replay(), _replay(transformations=[_DROP])]
    turns[stage] = bad_request(_PRIVATE)
    result, client = _run(monkeypatch, turns)
    assert result == 1
    assert len(client.messages.requests) == stage + 1
    assert "BadRequestError" in _assert_private(capsys).err


@pytest.mark.parametrize("ceiling", [0, 255, 4097])
def test_bad_ceiling_never_builds_client(monkeypatch, capsys, ceiling):
    def no_client():
        raise AssertionError("client construction with an invalid bound")

    monkeypatch.setattr(canary, "get_client", no_client)
    assert canary.main(["--run", "--max-tokens", str(ceiling)]) == 2
    assert "256 and 4096" in capsys.readouterr().err


def test_missing_key_is_redacted_and_sends_nothing(monkeypatch, capsys):
    def no_key():
        raise MissingApiKeyError(_PRIVATE)

    monkeypatch.setattr(canary, "get_client", no_key)
    assert canary.main(["--run"]) == 2
    assert "No API key configured" in _assert_private(capsys).err
