"""Every integer knob has a floor, and the floor is a contract.

Batch 5 of the 2026-09-02 program diagnosis. ``_int_env`` used to parse and
fall back to the default on garbage — and nothing else. Five knobs wrapped
their own ``max(N, …)`` and thirteen were bare, so
``BUILD_A_SPEC_QC_VERIFIERS_STANDARD=0`` built a zero-seat panel that
``panel_outcome`` read as unanimously upheld. The helper now clamps to a
per-knob ``minimum`` and complains, and an ``ast`` walk keeps every call
site declaring one.
"""
from __future__ import annotations

import ast
import importlib
import logging
from pathlib import Path

from backend import settings
from backend.settings import _int_env

KNOB = "BUILD_A_SPEC_TEST_ONLY_KNOB"
LOGGER = "buildaspec.settings"


def test_a_valid_override_is_taken_verbatim(monkeypatch, caplog):
    monkeypatch.setenv(KNOB, " 12 ")
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert _int_env(KNOB, 3, minimum=1) == 12
    assert caplog.text == ""


def test_an_unset_knob_is_the_default_and_says_nothing(monkeypatch, caplog):
    monkeypatch.delenv(KNOB, raising=False)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert _int_env(KNOB, 3, minimum=1) == 3
    monkeypatch.setenv(KNOB, "   ")
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert _int_env(KNOB, 3, minimum=1) == 3
    assert caplog.text == ""


def test_garbage_falls_back_to_the_default_and_warns(monkeypatch, caplog):
    monkeypatch.setenv(KNOB, "lots")
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert _int_env(KNOB, 3, minimum=1) == 3
    # Silent degradation leaves an operator believing the override took
    # effect; the warning names the knob and the value it rejected.
    assert KNOB in caplog.text
    assert "'lots'" in caplog.text


def test_a_value_under_the_floor_is_clamped_and_warns(monkeypatch, caplog):
    monkeypatch.setenv(KNOB, "0")
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert _int_env(KNOB, 3, minimum=1) == 1
    assert KNOB in caplog.text
    assert "floor of 1" in caplog.text
    monkeypatch.setenv(KNOB, "-7")
    assert _int_env(KNOB, 3, minimum=2) == 2
    # At the floor is fine, and quiet.
    caplog.clear()
    monkeypatch.setenv(KNOB, "2")
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert _int_env(KNOB, 3, minimum=2) == 2
    assert caplog.text == ""


def test_no_floor_passes_a_negative_through(monkeypatch):
    """The contract for a future knob that genuinely wants none."""
    monkeypatch.setenv(KNOB, "-7")
    assert _int_env(KNOB, 3) == -7


def test_every_shipped_knob_declares_a_floor():
    """A knob added bare fails here, not in production at value zero."""
    tree = ast.parse(Path(settings.__file__).read_text(encoding="utf-8"))
    bare: list[str] = []
    calls = 0
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_int_env"
        ):
            continue
        calls += 1
        if not any(keyword.arg == "minimum" for keyword in node.keywords):
            first = node.args[0] if node.args else None
            bare.append(
                first.value if isinstance(first, ast.Constant) else "<dynamic>"
            )
    assert calls >= 18, "the knob inventory shrank — re-check the sweep"
    assert bare == [], f"integer knobs with no floor: {bare}"


def test_the_shipped_floors_hold_through_a_reload(monkeypatch):
    """The five folded wrappers kept their floors; the dangerous bare one
    gained the floor that matters. Module state is global, so the reload is
    undone in ``finally`` (the ``test_qc_phase_effort`` idiom)."""
    monkeypatch.setenv("BUILD_A_SPEC_QC_VERIFIERS_STANDARD", "0")
    monkeypatch.setenv("BUILD_A_SPEC_QC_VERIFIERS_CRITICAL", "-1")
    monkeypatch.setenv("BUILD_A_SPEC_QC_BATCH_MAX_WAIT_SECONDS", "1")
    monkeypatch.setenv("BUILD_A_SPEC_QC_CONSOLIDATION_MAX_BUCKET", "1")
    monkeypatch.setenv("BUILD_A_SPEC_QC_MAX_WORKERS", "0")
    monkeypatch.setenv("BUILD_A_SPEC_MAX_TOKENS", "0")
    try:
        reloaded = importlib.reload(settings)
        assert reloaded.QC_VERIFIERS_STANDARD == 1
        assert reloaded.QC_VERIFIERS_CRITICAL == 1
        assert reloaded.QC_BATCH_MAX_WAIT_SECONDS == 60
        assert reloaded.QC_CONSOLIDATION_MAX_BUCKET == 2
        assert reloaded.QC_MAX_WORKERS == 1
        assert reloaded.INTERVIEW_MAX_TOKENS == 1
    finally:
        for name in (
            "BUILD_A_SPEC_QC_VERIFIERS_STANDARD",
            "BUILD_A_SPEC_QC_VERIFIERS_CRITICAL",
            "BUILD_A_SPEC_QC_BATCH_MAX_WAIT_SECONDS",
            "BUILD_A_SPEC_QC_CONSOLIDATION_MAX_BUCKET",
            "BUILD_A_SPEC_QC_MAX_WORKERS",
            "BUILD_A_SPEC_MAX_TOKENS",
        ):
            monkeypatch.delenv(name, raising=False)
        importlib.reload(settings)
    assert settings.QC_VERIFIERS_STANDARD == 2
    assert settings.QC_BATCH_MAX_WAIT_SECONDS == 7200
