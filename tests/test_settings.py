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
import time
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
    assert calls >= 21, "the knob inventory shrank — re-check the sweep"
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


def test_a_correction_before_logging_starts_is_held_and_replayed_once(monkeypatch):
    """This module is imported before the activity-log handler exists
    (``main.py`` imports it at the top; ``diagnostics.init_logging`` runs
    inside ``main()``), so a correction is HELD and replayed the moment
    durable logging appears — with its original timestamp — rather than lost
    to a stderr the windowed build has pointed at devnull (Codex, PR #155)."""
    # QC_MAX_WORKERS, not a verifier panel size: this test is about the
    # startup BUFFER, and QC_VERIFIERS_STANDARD now emits a second,
    # consequence warning of its own, which would make the counts below
    # fail for a reason that has nothing to do with what is under test.
    knob = "BUILD_A_SPEC_QC_MAX_WORKERS"
    monkeypatch.setenv(knob, "0")
    landed: list[logging.LogRecord] = []

    class _Sink(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            landed.append(record)

    sink = _Sink()
    root = logging.getLogger()
    try:
        importlib.reload(settings)  # emits while only the buffer listens
        assert settings.pending_startup_records() == 1
        root.addHandler(sink)
        flushed_at = time.time()
        assert settings.flush_startup_log() == 1
        assert len(landed) == 1
        message = landed[0].getMessage()
        assert knob in message and "floor of 1" in message
        assert landed[0].created <= flushed_at  # the import-time stamp survives
        # Idempotent: nothing is replayed twice.
        assert settings.flush_startup_log() == 0
        assert len(landed) == 1
        assert settings.pending_startup_records() == 0
        # After the flush a correction goes straight through, not into a buffer.
        monkeypatch.setenv(KNOB, "0")
        assert settings._int_env(KNOB, 3, minimum=1) == 1
        assert len(landed) == 2
        assert settings.pending_startup_records() == 0
    finally:
        root.removeHandler(sink)
        monkeypatch.delenv(knob, raising=False)
        importlib.reload(settings)


def test_a_settings_reload_keeps_exactly_one_startup_buffer():
    """``importlib.reload`` mints a new class object, so the buffer is matched
    by NAME — an ``isinstance`` check would leave the previous import's
    buffer attached beside the new one, and every later reload would add
    another."""
    logger = logging.getLogger(LOGGER)
    importlib.reload(settings)
    importlib.reload(settings)
    named = [
        h for h in logger.handlers if h.get_name() == settings._STARTUP_BUFFER_NAME
    ]
    assert len(named) == 1


def test_a_one_seat_standard_panel_warns_at_settings_load(monkeypatch):
    """One seat is legal, in range, and changes what a review MEANS.

    ``panel_outcome`` cannot return ``disputed`` from a split at one seat,
    so a lone reviewer's refusal deletes a medium/low finding outright with
    no escalation. The floor deliberately stays at 1 — that is what
    ``test_the_shipped_floors_hold_through_a_reload`` pins, and CLAUDE.md
    records 2 -> 1 as a deferred cost lever — so the only thing between an
    operator and a silently weaker review is this warning. It goes through
    the startup buffer like every other settings correction, because
    ``settings`` is imported long before the activity log exists.
    """
    knob = "BUILD_A_SPEC_QC_VERIFIERS_STANDARD"
    landed: list[logging.LogRecord] = []

    class _Sink(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            landed.append(record)

    sink = _Sink()
    root = logging.getLogger()
    root.addHandler(sink)
    try:
        # In range and legal, so the clamp says nothing: this is the only
        # thing that speaks up, and today nothing does.
        monkeypatch.setenv(knob, "1")
        reloaded = importlib.reload(settings)
        assert reloaded.QC_VERIFIERS_STANDARD == 1
        assert reloaded.pending_startup_records() == 1
        landed.clear()
        assert reloaded.flush_startup_log() == 1
        message = landed[0].getMessage()
        assert knob in message
        assert "cannot split" in message
        assert "disputed" in message

        # Below the floor: the clamp AND the consequence, both held.
        monkeypatch.setenv(knob, "0")
        reloaded = importlib.reload(settings)
        assert reloaded.QC_VERIFIERS_STANDARD == 1
        assert reloaded.pending_startup_records() == 2

        # The shipped default says nothing at all.
        monkeypatch.delenv(knob, raising=False)
        reloaded = importlib.reload(settings)
        assert reloaded.QC_VERIFIERS_STANDARD == 2
        assert reloaded.pending_startup_records() == 0
    finally:
        root.removeHandler(sink)
        monkeypatch.delenv(knob, raising=False)
        importlib.reload(settings)
