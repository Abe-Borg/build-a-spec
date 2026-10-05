"""QC output/thinking ceilings bind every phase and transport, not caches."""
from __future__ import annotations

import copy
import importlib
import io
from dataclasses import replace

import pytest
from docx import Document

from backend import settings
from backend.qc import engine
from backend.spec_doc.docx_export import build_qc_memo
from backend.spec_modules import DEFAULT_MODULE
from tests.fakes import (
    SequencedFakeClient,
    pause_response,
    qc_consolidation_response,
    qc_findings_response,
    qc_verdict_response,
)
from tests.test_qc import _finding, _qc_scripts, _section
from tests.test_qc_consolidation import _group

_KNOBS = (
    "BUILD_A_SPEC_QC_MAX_TOKENS",
    "BUILD_A_SPEC_QC_LENS_MAX_TOKENS",
    "BUILD_A_SPEC_QC_CONSOLIDATION_MAX_TOKENS",
    "BUILD_A_SPEC_QC_VERIFIER_MAX_TOKENS",
)
_DEFAULTS = {"lens": 64_000, "consolidation": 32_000, "verifier": 32_000}


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, (64_000, 32_000, 32_000)),
        ({_KNOBS[0]: "16000"}, (16_000, 16_000, 16_000)),
        ({_KNOBS[0]: "1"}, (1, 1, 1)),
        ({_KNOBS[1]: "96000"}, (96_000, 32_000, 32_000)),
        ({_KNOBS[2]: "8000", _KNOBS[3]: "48000"}, (64_000, 8_000, 48_000)),
        (dict.fromkeys(_KNOBS, "8000"), (8_000, 8_000, 8_000)),
        ({_KNOBS[0]: "8000", _KNOBS[3]: "96000"}, (8_000, 8_000, 8_000)),
        ({_KNOBS[1]: "bad", _KNOBS[2]: "bad", _KNOBS[3]: "bad"}, (64_000, 32_000, 32_000)),
        ({_KNOBS[1]: "0", _KNOBS[2]: "-1", _KNOBS[3]: "0"}, (1, 1, 1)),
    ],
)
def test_phase_settings_preserve_the_global_cap(monkeypatch, env, expected):
    try:
        with monkeypatch.context() as scoped:
            for name in _KNOBS:
                scoped.delenv(name, raising=False)
            for name, value in env.items():
                scoped.setenv(name, value)
            importlib.reload(settings)
            assert (
                settings.QC_LENS_MAX_TOKENS,
                settings.QC_CONSOLIDATION_MAX_TOKENS,
                settings.QC_VERIFIER_MAX_TOKENS,
            ) == expected
    finally:
        importlib.reload(settings)


@pytest.fixture
def ceilings(monkeypatch):
    monkeypatch.setattr(settings, "QC_LENS_MAX_TOKENS", 64_000)
    monkeypatch.setattr(settings, "QC_CONSOLIDATION_MAX_TOKENS", 32_000)
    monkeypatch.setattr(settings, "QC_VERIFIER_MAX_TOKENS", 32_000)
    monkeypatch.setattr(settings, "QC_CONSOLIDATION", True)
    monkeypatch.setattr(settings, "QC_BATCH_VERIFICATION", False)
    # Eight seats make a real streamed warm lead eligible in a small fixture.
    monkeypatch.setattr(settings, "QC_VERIFIERS_STANDARD", 8)
    monkeypatch.setattr(engine, "_WARM_LEAD_MIN_SEATS_NO_WEB", 8)


def _client(*, stop_reason="tool_use"):
    scripts = _qc_scripts(
        completeness=[
            pause_response(),
            qc_findings_response(
                "completeness",
                findings=[_finding("Edition gap", "Stale edition", severity="medium")],
            ),
        ],
        provenance_hygiene=[
            qc_findings_response(
                "provenance_hygiene",
                findings=[_finding("Edition assumption", "Stale edition", severity="medium")],
            ),
        ],
    )
    scripts["[[QC-CONSOLIDATE:"] = [
        pause_response(),
        qc_consolidation_response([
            _group(
                [0, 1], title="Edition gap", issue="Stale edition",
                rationale="Both claims describe the same stale edition.",
                why="The same edition defect.",
            ),
        ]),
    ]
    scripts["[[QC-VERIFY:"] = [pause_response()] + [
        qc_verdict_response(True, stop_reason=stop_reason) for _ in range(8)
    ]
    return SequencedFakeClient(scripts)


def _run(client, store, *, batch=False, lead=False, max_tokens=128_000, **kwargs):
    return engine.run_final_qc(
        store.doc, None, DEFAULT_MODULE, client,
        model=settings.QC_MODEL,
        max_tokens=max_tokens,
        version_index=store.index,
        started_at="2026-10-05T10:00:00-07:00",
        finished_at="2026-10-05T10:01:00-07:00",
        batch_verification=batch,
        batch_warm_lead=lead,
        warm_wait_seconds=1 if lead else 0,
        refusal_fallback=False,
        **kwargs,
    )


@pytest.mark.parametrize(("batch", "lead"), [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize("max_tokens", [128_000, 4_096])
@pytest.mark.parametrize("limits", [_DEFAULTS, {"lens": 48_000, "consolidation": 12_000, "verifier": 24_000}])
def test_every_phase_and_continuation_sends_its_ceiling(ceilings, monkeypatch, batch, lead, max_tokens, limits):
    for phase, setting in (
        ("lens", "QC_LENS_MAX_TOKENS"),
        ("consolidation", "QC_CONSOLIDATION_MAX_TOKENS"),
        ("verifier", "QC_VERIFIER_MAX_TOKENS"),
    ):
        monkeypatch.setattr(settings, setting, limits[phase])
    client = _client()
    result = _run(client, _section(), batch=batch, lead=lead, max_tokens=max_tokens)
    assert result.is_complete()
    assert len(result.findings) == 1
    expected = {phase: min(max_tokens, cap) for phase, cap in limits.items()}
    assert result.input_manifest["configuration"]["phase_max_tokens"] == expected
    phases = {"lens": [], "consolidation": [], "verifier": []}
    for request in client.requests:
        messages = str(request["messages"])
        phase = (
            "lens" if "[[QC-LENS:" in messages
            else "consolidation" if "[[QC-CONSOLIDATE:" in messages
            else "verifier"
        )
        phases[phase].append(request)
        assert request["max_tokens"] == expected[phase]
    assert {phase: len(requests) for phase, requests in phases.items()} == {
        "lens": 6, "consolidation": 2, "verifier": 9,
    }
    assert all(any(len(r["messages"]) > 1 for r in requests) for requests in phases.values())
    if batch:
        # An eligible lead's two requests stream, then seven seats ride batches.
        batched = [r["params"] for batch_requests in client.batches.created for r in batch_requests]
        assert len(batched) == (7 if lead else 9)
        assert all(r["max_tokens"] == expected["verifier"] for r in batched)
    else:
        assert client.batches.created == []


def test_limits_are_pinned_before_fanout_and_retained_in_exports(ceilings, monkeypatch):
    store = _section()
    def change_settings(event):
        if event["type"] == "qc_started":
            monkeypatch.setattr(settings, "QC_LENS_MAX_TOKENS", 8_000)
            monkeypatch.setattr(settings, "QC_CONSOLIDATION_MAX_TOKENS", 8_000)
            monkeypatch.setattr(settings, "QC_VERIFIER_MAX_TOKENS", 8_000)

    client = _client()
    result = _run(client, store, event_sink=change_settings)
    assert result.is_complete()
    assert {r["max_tokens"] for r in client.requests} == {64_000, 32_000}
    restored = engine.QCResult.from_dict(result.to_dict())
    assert restored is not None and restored.is_complete()
    assert restored.input_manifest["configuration"]["phase_max_tokens"] == _DEFAULTS
    assert not restored.matches_inputs(store.index, store.doc, None, DEFAULT_MODULE)
    memo = Document(io.BytesIO(build_qc_memo(restored.to_dict(), store.doc, stale=True)))
    text = "\n".join(p.text for p in memo.paragraphs)
    assert "Maximum output tokens (lens review): 64,000" in text
    assert "Maximum output tokens (candidate grouping): 32,000" in text
    assert "Maximum output tokens (verifier seats): 32,000" in text


@pytest.mark.parametrize("setting", ["QC_LENS_MAX_TOKENS", "QC_CONSOLIDATION_MAX_TOKENS", "QC_VERIFIER_MAX_TOKENS"])
def test_each_phase_ceiling_affects_freshness(ceilings, monkeypatch, setting):
    store = _section()
    result = _run(_client(), store)
    assert result.matches_inputs(store.index, store.doc, None, DEFAULT_MODULE)
    monkeypatch.setattr(settings, setting, 16_000)
    assert not result.matches_inputs(store.index, store.doc, None, DEFAULT_MODULE)


def test_pre_ceiling_reports_remain_readable_but_stale(ceilings):
    store = _section()
    payload = _run(_client(), store).to_dict()
    del payload["input_manifest"]["configuration"]["phase_max_tokens"]
    payload["input_fingerprint"] = engine.qc_input_fingerprint(payload["input_manifest"])
    restored = engine.QCResult.from_dict(payload)
    assert restored is not None and restored.is_complete()
    assert not restored.matches_inputs(store.index, store.doc, None, DEFAULT_MODULE)


@pytest.mark.parametrize("limits", [
    None,
    {"lens": 64_000},
    {"lens": True, "consolidation": 32_000, "verifier": 32_000},
    {"lens": 0, "consolidation": 32_000, "verifier": 32_000},
    {"lens": 64_000, "consolidation": 32_000, "verifier": 128_001},
])
def test_invalid_persisted_phase_limits_are_rejected(ceilings, limits):
    payload = copy.deepcopy(_run(_client(), _section()).to_dict())
    payload["input_manifest"]["configuration"]["phase_max_tokens"] = limits
    payload["input_fingerprint"] = engine.qc_input_fingerprint(payload["input_manifest"])
    assert engine.QCResult.from_dict(payload) is None


def test_changing_ceiling_preserves_prompt_cache_lineage():
    spec = engine._verifier_call_spec(
        finding=_finding("Edition gap", "Stale edition"),
        lens=next(lens for lens in engine.QC_LENSES if not lens.web),
        section_render="The full section", module=DEFAULT_MODULE,
        model=settings.QC_MODEL, max_tokens=128_000, effort="medium",
    )
    assert engine._spec_lineage_key(spec) == engine._spec_lineage_key(
        replace(spec, max_tokens=32_000)
    )


@pytest.mark.parametrize("batch", [False, True])
def test_cutoff_seats_cannot_uphold_a_finding(ceilings, batch):
    result = _run(_client(stop_reason="max_tokens"), _section(), batch=batch)
    assert result.execution_status == "partial"
    assert result.findings == []
    assert len(result.inconclusive) == 1
    assert not result.is_complete()
    assert all(v.status == "failed" for v in result.inconclusive[0].verdicts)
