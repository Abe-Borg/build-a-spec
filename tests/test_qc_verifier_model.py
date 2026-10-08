"""Final QC's verifier seats run on their own model.

Owner decision (2026-10-08): the five lenses and the grouping calls stay on
``settings.QC_MODEL`` (Claude Opus 5.5); the verifier seats run on
``settings.QC_VERIFIER_MODEL`` (Claude Sonnet 5.5, at "high"). A seat's cost
is reading the shared section, so its per-token price is the lever.

What must stay true when one run spans two models:

- every seat request goes to the seat model, every other request to the run's;
- each record is priced by the snapshot of the model that ran it, the run's
  total is the sum of its records, and a report whose seat pricing was
  tampered with is refused on load;
- the seat model is a hashed input, so a report made with other seats reads
  stale, and every report from before the split reads stale once;
- the session meter files seats in their own buckets, priced at their model;
- the refusal fallback is asked per model (Haiku 5.5 has none);
- the report says which model ran the seats, and which rates priced a call
  another model answered.
"""
from __future__ import annotations

import copy
import dataclasses

from backend import settings
from backend.qc.engine import (
    UNCOLLECTED_BATCH_REQUESTS_KEY,
    QCResult,
    qc_input_fingerprint,
    run_final_qc,
)
from backend.qc.schema import QC_LENSES
from backend.spec_doc import docx_export
from backend.spec_doc.model import DocumentStore
from backend.spec_modules import DEFAULT_MODULE
from tests.fakes import (
    SequencedFakeClient,
    qc_findings_response,
    qc_verdict_response,
)

_LENS_KEYS = {lens.lens_id: f"[[QC-LENS:{lens.lens_id}]]" for lens in QC_LENSES}
_OPUS = settings.MODEL_OPUS_55
_SONNET = settings.MODEL_SONNET_55
_LENS_TOKENS = {"input": 4000, "output": 800}
_SEAT_TOKENS = {"input": 500, "output": 200, "cache_read": 1000}


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


def _scripts(*, seat_turns: list[object] | None = None) -> dict[str, list[object]]:
    title = "Seat model finding"
    scripts: dict[str, list[object]] = {
        key: [qc_findings_response(lens_id, findings=[], tokens=_LENS_TOKENS)]
        for lens_id, key in _LENS_KEYS.items()
    }
    scripts[_LENS_KEYS["coordination_consistency"]] = [
        qc_findings_response(
            "coordination_consistency",
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
            tokens=_LENS_TOKENS,
        )
    ]
    scripts[title] = seat_turns or [
        qc_verdict_response(True, tokens=_SEAT_TOKENS),
        qc_verdict_response(True, tokens=_SEAT_TOKENS),
    ]
    return scripts


def _run(client, store=None, **kwargs) -> tuple[QCResult, DocumentStore]:
    store = store or _store()
    kwargs.setdefault("batch_verification", False)
    result = run_final_qc(
        store.doc,
        None,
        DEFAULT_MODULE,
        client,
        model=_OPUS,
        max_tokens=4096,
        version_index=store.index,
        started_at="2026-10-08T10:00:00-07:00",
        finished_at="2026-10-08T10:01:00-07:00",
        **kwargs,
    )
    return result, store


def _seat_requests(client) -> list[dict]:
    return [r for r in client.requests if "[[QC-VERIFY:" in str(r.get("messages"))]


def _other_requests(client) -> list[dict]:
    return [r for r in client.requests if "[[QC-VERIFY:" not in str(r.get("messages"))]


def test_seats_go_to_the_seat_model_and_everything_else_to_the_run_model():
    client = SequencedFakeClient(_scripts())
    result, _store = _run(client, verifier_model=_SONNET)
    seats = _seat_requests(client)
    assert len(seats) == 2
    assert {r["model"] for r in seats} == {_SONNET}
    assert {r["model"] for r in _other_requests(client)} == {_OPUS}
    assert result.model == _OPUS
    assert result.verifier_model == _SONNET
    assert result.mixed_models()
    assert result.verifier_cost_basis["requested_model"] == _SONNET
    assert result.cost_basis["requested_model"] == _OPUS


def test_each_record_is_priced_by_its_own_model_and_the_total_is_their_sum():
    result, _store = _run(SequencedFakeClient(_scripts()), verifier_model=_SONNET)
    sonnet = settings.PRICING[_SONNET]
    opus = settings.PRICING[_OPUS]
    seat_cost = round(
        500 * sonnet["input"] + 200 * sonnet["output"] + 1000 * sonnet["cache_read"],
        6,
    )
    lens_cost = round(4000 * opus["input"] + 800 * opus["output"], 6)
    seats = [v for f in [*result.findings, *result.disputed, *result.refuted] for v in f.verdicts]
    assert [seat.estimated_cost_usd for seat in seats] == [seat_cost, seat_cost]
    assert {status.estimated_cost_usd for status in result.lens_statuses} == {lens_cost}
    records = [
        *result.lens_statuses,
        *([result.consolidation] if result.consolidation is not None else []),
        *seats,
    ]
    assert result.estimated_cost_usd == round(
        sum(record.estimated_cost_usd for record in records), 6
    )
    # Not what one model's rates over the merged usage would say.
    assert result.estimated_cost_usd < round(
        5 * lens_cost + 2 * round(
            500 * opus["input"] + 200 * opus["output"] + 1000 * opus["cache_read"], 6
        ),
        6,
    )


def test_a_mixed_model_report_round_trips_and_refuses_tampered_seat_pricing():
    result, _store = _run(SequencedFakeClient(_scripts()), verifier_model=_SONNET)
    payload = result.to_dict()
    assert payload["verifier_model"] == _SONNET
    restored = QCResult.from_dict(copy.deepcopy(payload))
    assert restored is not None
    assert restored.verifier_model == _SONNET
    assert restored.verifier_cost_basis == result.verifier_cost_basis
    assert restored.estimated_cost_usd == result.estimated_cost_usd

    # Seats priced at the lenses' rates are a claim the record cannot back.
    swapped = copy.deepcopy(payload)
    swapped["verifier_cost_basis"] = copy.deepcopy(payload["cost_basis"])
    assert QCResult.from_dict(swapped) is None

    # A seat model the hashed manifest does not name is tampering too.
    renamed = copy.deepcopy(payload)
    renamed["verifier_model"] = "claude-sonnet-5"
    assert QCResult.from_dict(renamed) is None

    # The snapshot is required once a seat model is named.
    missing = copy.deepcopy(payload)
    del missing["verifier_cost_basis"]
    assert QCResult.from_dict(missing) is None


def test_a_one_model_run_keeps_the_old_shape_of_pricing():
    """A direct caller that names one model gets one model: the total is the
    one-model estimate over the merged usage, as every older report claims."""
    result, _store = _run(SequencedFakeClient(_scripts()))
    assert result.verifier_model == _OPUS
    assert not result.mixed_models()
    restored = QCResult.from_dict(copy.deepcopy(result.to_dict()))
    assert restored is not None
    assert result.usage_by_meter_category().keys() == {"qc"}


def test_a_report_from_before_the_split_loads_and_prices_its_seats_by_its_own_basis():
    result, _store = _run(SequencedFakeClient(_scripts()))
    legacy = result.to_dict()
    # A pre-split report has no seat model anywhere: not on the record, not
    # in the hashed manifest.
    del legacy["verifier_model"]
    del legacy["verifier_cost_basis"]
    del legacy["input_manifest"]["configuration"]["verifier_model"]
    # A genuine pre-split record hashed the manifest it actually had.
    legacy["input_fingerprint"] = qc_input_fingerprint(legacy["input_manifest"])
    restored = QCResult.from_dict(copy.deepcopy(legacy))
    assert restored is not None
    assert restored.verifier_model == ""
    assert restored.seat_model() == _OPUS
    assert "verifier_model" not in restored.to_dict()


def test_the_seat_model_is_hashed_so_other_seats_read_stale(monkeypatch):
    monkeypatch.setattr(settings, "QC_BATCH_VERIFICATION", False)
    sonnet, store = _run(SequencedFakeClient(_scripts()), verifier_model=_SONNET)

    def current(result, *, verifier_model=None) -> bool:
        return result.matches_inputs(
            store.index,
            store.doc,
            None,
            DEFAULT_MODULE,
            model=_OPUS,
            verifier_model=verifier_model,
        )

    assert current(sonnet, verifier_model=_SONNET) is True
    assert current(sonnet, verifier_model=_OPUS) is False

    # A report made before the seats had their own model is rebuilt with
    # today's seat model, so it reads stale once.
    opus, store = _run(SequencedFakeClient(_scripts()), store=store)
    legacy = opus.to_dict()
    del legacy["verifier_model"]
    del legacy["verifier_cost_basis"]
    del legacy["input_manifest"]["configuration"]["verifier_model"]
    legacy["input_fingerprint"] = qc_input_fingerprint(legacy["input_manifest"])
    restored = QCResult.from_dict(legacy)
    assert restored is not None
    assert restored.matches_inputs(
        store.index, store.doc, None, DEFAULT_MODULE, model=_OPUS
    ) is False


def test_the_meter_files_streamed_and_batched_seats_under_their_own_buckets():
    streamed, _store = _run(SequencedFakeClient(_scripts()), verifier_model=_SONNET)
    assert streamed.usage_by_meter_category().keys() == {"qc", "qc_verifier"}
    assert streamed.usage_by_meter_category()["qc_verifier"]["input_tokens"] == 1000

    batched, _store = _run(
        SequencedFakeClient(_scripts()),
        verifier_model=_SONNET,
        batch_verification=True,
        warm_wait_seconds=0,
    )
    assert batched.usage_by_meter_category().keys() == {"qc", "qc_verifier_batched"}

    # The count of batch requests the run could not collect rides the bucket
    # the seats' tokens went to, so the warning reaches the meter with them.
    gaps = dataclasses.replace(batched, unassigned_batch_results=2)
    seats = gaps.usage_by_meter_category()["qc_verifier_batched"]
    assert seats[UNCOLLECTED_BATCH_REQUESTS_KEY] == 2
    assert UNCOLLECTED_BATCH_REQUESTS_KEY not in gaps.usage_by_meter_category()["qc"]


def test_sonnet_seats_ask_for_the_refusal_fallback_and_haiku_seats_do_not():
    client = SequencedFakeClient(_scripts())
    _run(client, verifier_model=_SONNET, refusal_fallback=True)
    for request in _seat_requests(client):
        assert request["extra_body"]["fallbacks"] == "default"

    client = SequencedFakeClient(_scripts())
    _run(client, verifier_model=settings.MODEL_HAIKU_55, refusal_fallback=True)
    seats = _seat_requests(client)
    assert seats and {r["model"] for r in seats} == {settings.MODEL_HAIKU_55}
    for request in seats:
        assert "fallbacks" not in dict(request.get("extra_body") or {})
    # The lenses, on the run's model, still ask for it.
    lenses = [r for r in _other_requests(client) if "[[QC-LENS:" in str(r["messages"])]
    assert lenses
    for request in lenses:
        assert request["extra_body"]["fallbacks"] == "default"


def test_the_word_report_names_both_models_and_the_rates_a_rescued_call_used():
    result, _store = _run(SequencedFakeClient(_scripts()), verifier_model=_SONNET)
    payload = result.to_dict()

    seat = payload["findings"][0]["verdicts"][0] if payload["findings"] else (
        (payload["disputed"] or payload["refuted"])[0]["verdicts"][0]
    )
    seat["served_by_model"] = "claude-sonnet-5"
    count, limitation = docx_export.qc_refusal_fallback(payload)
    assert count == 1
    assert limitation.endswith(f"estimated at {_SONNET} rates.")

    payload["lens_statuses"][0]["served_by_model"] = "claude-opus-5"
    count, limitation = docx_export.qc_refusal_fallback(payload)
    assert count == 2
    assert limitation.endswith(f"estimated at {_OPUS} and {_SONNET} rates.")
