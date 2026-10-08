"""Final QC reasons at a different depth in each phase.

Thinking bills as output at the QC model's output rate, and phase 2 is ~90%
of a run's calls — so one effort value applied to the whole fan-out spends
the review's budget where it buys least. A lens GENERATES (reads the section
cold and decides what is wrong with it); a verifier seat ADJUDICATES one
already-stated claim with the same document in front of it. They are priced
the same and are not the same work.

What must stay true is that the split is recorded, not silent: both depths
ride the hashed input manifest and both render in the report, so a reader
can always see how deeply each phase actually reasoned.
"""
from __future__ import annotations

from backend import settings
from backend.qc.engine import run_final_qc
from backend.qc.schema import QC_LENSES
from backend.spec_doc.model import DocumentStore
from backend.spec_modules import DEFAULT_MODULE
from tests.fakes import (
    SequencedFakeClient,
    qc_findings_response,
    qc_verdict_response,
)

_LENS_KEYS = {lens.lens_id: f"[[QC-LENS:{lens.lens_id}]]" for lens in QC_LENSES}


def _store() -> DocumentStore:
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


def _scripts(title: str = "Effort finding") -> dict[str, list[object]]:
    scripts: dict[str, list[object]] = {
        key: [qc_findings_response(lens_id, findings=[])]
        for lens_id, key in _LENS_KEYS.items()
    }
    scripts[_LENS_KEYS["code_compliance"]] = [
        qc_findings_response(
            "code_compliance",
            findings=[
                {
                    "title": title,
                    "severity": "medium",
                    "element_id": "pt1.a1.p1",
                    "issue": "Issue.",
                    "rationale": "Rationale.",
                    "source_urls": [],
                    "proposed_ops": None,
                }
            ],
        )
    ]
    scripts[title] = [qc_verdict_response(True), qc_verdict_response(True)]
    return scripts


def _run(client, **kwargs):
    store = _store()
    return run_final_qc(
        store.doc,
        None,
        DEFAULT_MODULE,
        client,
        model=settings.QC_MODEL,
        max_tokens=4096,
        version_index=store.index,
        started_at="2026-08-19T10:00:00-07:00",
        finished_at="2026-08-19T10:01:00-07:00",
        # Streaming, so every request lands in `client.requests` with the
        # effort it was sent at; the batch path is covered separately.
        batch_verification=False,
        **kwargs,
    )


def _efforts(client) -> dict[str, set[str]]:
    lens, verifier = set(), set()
    for request in client.requests:
        text = str(request.get("messages"))
        effort = request["output_config"]["effort"]
        if "[[QC-VERIFY:" in text:
            verifier.add(effort)
        elif "[[QC-LENS:" in text:
            lens.add(effort)
    return {"lens": lens, "verifier": verifier}


def test_each_phase_is_sent_at_its_own_effort():
    client = SequencedFakeClient(_scripts())
    _run(client, lens_effort="high", verifier_effort="low")
    assert _efforts(client) == {"lens": {"high"}, "verifier": {"low"}}


def _models(client) -> dict[str, set[str]]:
    lens, verifier = set(), set()
    for request in client.requests:
        text = str(request.get("messages"))
        if "[[QC-VERIFY:" in text:
            verifier.add(request["model"])
        elif "[[QC-LENS:" in text:
            lens.add(request["model"])
    return {"lens": lens, "verifier": verifier}


def test_the_shipped_defaults_run_opus_lenses_at_medium_and_sonnet_seats_at_high():
    """The defaults are pinned directly.

    The lens phase shipped at "high" from the split until the 5.5 prompting
    upgrade (P55-3, decision D4) re-based it for Opus 5.5, whose "medium"
    matches or exceeds Opus 5's "high". The seats moved to their own model,
    Claude Sonnet 5.5, at its default "high" (owner decision, 2026-10-08):
    a seat's cost is reading the shared section, so its per-token price is
    the lever, and the extra thinking is cheap. Run the way the app runs it.
    """
    assert settings.QC_MODEL == settings.MODEL_OPUS_55
    assert settings.QC_LENS_EFFORT == "medium"
    assert settings.QC_VERIFIER_MODEL == settings.MODEL_SONNET_55
    assert settings.QC_VERIFIER_EFFORT == "high"
    client = SequencedFakeClient(_scripts())
    result = _run(client, verifier_model=settings.QC_VERIFIER_MODEL)
    assert _efforts(client) == {"lens": {"medium"}, "verifier": {"high"}}
    assert _models(client) == {
        "lens": {settings.MODEL_OPUS_55},
        "verifier": {settings.MODEL_SONNET_55},
    }
    assert result.verifier_model == settings.MODEL_SONNET_55
    assert result.input_manifest["configuration"]["verifier_model"] == (
        settings.MODEL_SONNET_55
    )


def test_a_direct_caller_naming_one_model_gets_one_model():
    """``model`` alone sets both phases, the way one ``effort`` does."""
    client = SequencedFakeClient(_scripts())
    result = _run(client)
    assert _models(client) == {
        "lens": {settings.QC_MODEL},
        "verifier": {settings.QC_MODEL},
    }
    assert result.verifier_model == settings.QC_MODEL
    assert not result.mixed_models()


def test_the_seat_model_is_hashed_and_reported():
    sonnet = _run(
        SequencedFakeClient(_scripts()), verifier_model=settings.MODEL_SONNET_55
    )
    opus = _run(SequencedFakeClient(_scripts()))
    # A panel of another model is not the same review.
    assert sonnet.input_fingerprint != opus.input_fingerprint
    assert sonnet.mixed_models()


def test_the_seat_effort_follows_the_seat_model(monkeypatch):
    """Sonnet 5.5 seats default to "high"; any other seat model keeps the
    Opus 5.5 level ("medium"), so pinning the seats back to Opus restores the
    depth they ran at before. An explicit global effort still wins."""
    import importlib

    try:
        monkeypatch.setenv("BUILD_A_SPEC_QC_VERIFIER_MODEL", settings.MODEL_OPUS_55)
        reloaded = importlib.reload(settings)
        assert reloaded.QC_VERIFIER_MODEL == settings.MODEL_OPUS_55
        assert reloaded.QC_VERIFIER_EFFORT == "medium"

        monkeypatch.delenv("BUILD_A_SPEC_QC_VERIFIER_MODEL")
        monkeypatch.setenv("BUILD_A_SPEC_QC_MODEL", "claude-opus-5")
        reloaded = importlib.reload(settings)
        # One explicitly named QC model moves the seats with it.
        assert reloaded.QC_VERIFIER_MODEL == "claude-opus-5"
        assert reloaded.QC_VERIFIER_EFFORT == "medium"

        monkeypatch.setenv("BUILD_A_SPEC_QC_VERIFIER_MODEL", settings.MODEL_SONNET_55)
        monkeypatch.setenv("BUILD_A_SPEC_QC_EFFORT", "low")
        reloaded = importlib.reload(settings)
        assert reloaded.QC_VERIFIER_MODEL == settings.MODEL_SONNET_55
        assert reloaded.QC_VERIFIER_EFFORT == "low"
    finally:
        for name in (
            "BUILD_A_SPEC_QC_VERIFIER_MODEL",
            "BUILD_A_SPEC_QC_MODEL",
            "BUILD_A_SPEC_QC_EFFORT",
        ):
            monkeypatch.delenv(name, raising=False)
        importlib.reload(settings)


def test_one_effort_still_sets_both_phases():
    """A caller passing a single effort meant it for the whole run.

    Every pre-split caller and test does exactly this, so it cannot become
    a half-applied setting.
    """
    client = SequencedFakeClient(_scripts())
    _run(client, effort="xhigh")
    assert _efforts(client) == {"lens": {"xhigh"}, "verifier": {"xhigh"}}


def test_a_specific_phase_effort_overrides_the_shared_one():
    client = SequencedFakeClient(_scripts())
    _run(client, effort="xhigh", verifier_effort="low")
    assert _efforts(client) == {"lens": {"xhigh"}, "verifier": {"low"}}


def test_an_explicit_global_env_effort_is_not_silently_raised(monkeypatch):
    """`BUILD_A_SPEC_QC_EFFORT=low` must not leave the seats at "medium".

    The verifier default is a default, not a floor. An operator who dialled
    the whole run down asked for less depth everywhere, and quietly running
    the phase that dominates the bill *above* what they set would be the
    opposite of what they configured.
    """
    import importlib

    monkeypatch.setenv("BUILD_A_SPEC_QC_EFFORT", "low")
    reloaded = importlib.reload(settings)
    try:
        assert reloaded.QC_LENS_EFFORT == "low"
        assert reloaded.QC_VERIFIER_EFFORT == "low"
    finally:
        monkeypatch.delenv("BUILD_A_SPEC_QC_EFFORT", raising=False)
        importlib.reload(settings)


def test_both_depths_are_recorded_and_hashed():
    result = _run(
        SequencedFakeClient(_scripts()), lens_effort="high", verifier_effort="low"
    )
    configuration = result.input_manifest["configuration"]
    assert configuration["effort"] == "high"
    assert configuration["verifier_effort"] == "low"
    assert result.effort == "high"
    assert result.verifier_effort == "low"

    deeper = _run(
        SequencedFakeClient(_scripts()), lens_effort="high", verifier_effort="high"
    )
    # A review whose seats adjudicated at a different depth is not the same
    # review, so it must not read as like-for-like.
    assert result.input_fingerprint != deeper.input_fingerprint


def test_both_depths_survive_a_serialization_round_trip():
    from backend.qc.engine import QCResult

    result = _run(
        SequencedFakeClient(_scripts()), lens_effort="high", verifier_effort="low"
    )
    restored = QCResult.from_dict(result.to_dict())
    assert restored is not None
    assert restored.effort == "high"
    assert restored.verifier_effort == "low"


def test_a_report_written_before_the_split_still_loads():
    """Absent on an older record, and it must degrade rather than vanish.

    `from_dict` returning None discards an entire paid report, so a new
    manifest field that a pre-split record cannot carry has to reconcile as
    consistent instead of failing the integrity check.
    """
    from backend.qc.engine import QCResult, qc_input_fingerprint

    payload = _run(SequencedFakeClient(_scripts())).to_dict()
    payload.pop("verifier_effort")
    payload["input_manifest"]["configuration"].pop("verifier_effort")
    # A genuine pre-split record hashed the manifest it actually had, so the
    # fingerprint has to be recomputed here. Leaving the newer hash in place
    # would build a record no version of this app could have written, and
    # would be caught by the manifest/fingerprint check rather than by the
    # backwards-compatibility the test is about.
    payload["input_fingerprint"] = qc_input_fingerprint(payload["input_manifest"])

    restored = QCResult.from_dict(payload)
    assert restored is not None
    assert restored.verifier_effort == ""


def test_a_tampered_verifier_effort_is_still_refused():
    """Absent reconciles; present-and-different is tampering."""
    from backend.qc.engine import QCResult

    payload = _run(
        SequencedFakeClient(_scripts()), lens_effort="high", verifier_effort="low"
    ).to_dict()
    payload["verifier_effort"] = "max"

    assert QCResult.from_dict(payload) is None
