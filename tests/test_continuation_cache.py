"""Continuations read their own cache (Research/QC cost Tier 1, Chunk 4).

A research area — or a streamed Final QC call carrying web tools — can pause
mid-turn (``pause_turn``) and is resumed by re-sending the whole
conversation. Nothing after the shared block carried a breakpoint, so every
re-sent turn billed as uncached input. With ``settings.CONTINUATION_CACHE``
on, a request that RESUMES a paused turn carries one top-level automatic
breakpoint at the shortest TTL, so it can read the entries the API already
wrote after the previous request's server-tool results.

The per-engine "on" and "off is today's request" pairs live beside the
engines' other continuation tests (``tests/test_research_engine.py``,
``tests/test_qc_live_events.py``). This file holds what spans both: the
breakpoint guard, the batched transport's exemption, the container riding
beside the tail, the grouping call and the streamed lead reaching the tail,
the retained Final QC result staying current (the plan's F3), and the
shipped default.
"""
from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path

from backend import settings
from backend.qc import engine as qc_engine
from backend.qc.engine import run_final_qc
from backend.research import run_requirements_research
from backend.research import engine as research_engine
from backend.spec_modules import DEFAULT_MODULE as QC_MODULE
from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE as RESEARCH_MODULE
from tests.fakes import (
    SequencedFakeClient,
    pause_response,
    qc_findings_response,
    qc_verdict_response,
    research_response,
    singleton_consolidation_for,
    user_text,
)
from tests.test_qc_batch_warm_lead import _lineage_scripts, _medium, _titles
from tests.test_qc_live_events import (
    _finding,
    _lens_requests,
    _pausing_qc_scripts,
    _scripts as _qc_scripts,
    _store,
)
from tests.test_qc_warm_launch import _store as _two_paragraph_store
from tests.test_qc_warm_launch import _two_bucket_scripts
from tests.test_research_engine import (
    DIM_KEYS,
    PROFILE,
    _item,
    _pausing_scripts,
    _scripts as _research_scripts,
)

_TAIL = {"type": "ephemeral"}
# TTLs must be non-increasing through a request; a missing ``ttl`` is the
# 5-minute default.
_TTL_RANK = {"1h": 2, "5m": 1, None: 1}


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------


class _StreamLog(SequencedFakeClient):
    """Keeps the STREAMED requests apart from the batched ones.

    ``SequencedFakeClient.requests`` also holds every batched seat's params
    (the batch fake resolves them through the same scripts), so a test about
    which transport sent what needs its own record of the streamed calls.
    """

    def __init__(self, scripts) -> None:
        super().__init__(scripts)
        self.streamed_requests: list[dict] = []

    def stream(self, **request):
        captured = dict(request)
        captured["messages"] = list(request.get("messages") or [])
        self.streamed_requests.append(captured)
        return super().stream(**request)


def _research(client, *, continuation_cache: bool | None = True):
    return run_requirements_research(
        RESEARCH_MODULE,
        PROFILE,
        client,
        model="claude-sonnet-5",
        max_tokens=4096,
        continuation_cache=continuation_cache,
    )


def _qc(
    client,
    *,
    store=None,
    batch: bool = False,
    lead: bool = False,
    warm: float = 0,
    continuation_cache: bool | None = True,
):
    store = store or _store()
    return run_final_qc(
        store.doc,
        None,
        QC_MODULE,
        client,
        model=settings.QC_MODEL,
        max_tokens=4096,
        version_index=store.index,
        started_at="2026-09-23T10:00:00-07:00",
        finished_at="2026-09-23T10:01:00-07:00",
        run_id="qc-continuation-cache-test",
        batch_verification=batch,
        batch_warm_lead=lead,
        warm_wait_seconds=warm,
        continuation_cache=continuation_cache,
    )


def _fixed_clocks(monkeypatch) -> None:
    """Both engines read the date into their cached prefixes; pin it."""
    fixed = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(qc_engine, "current_datetime", lambda *_a, **_k: fixed)
    monkeypatch.setattr(research_engine, "current_datetime", lambda *_a, **_k: fixed)


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, default=repr)


def _batched_params(client: SequencedFakeClient) -> list[dict]:
    return [request["params"] for batch in client.batches.created for request in batch]


# ---------------------------------------------------------------------------
# Scenarios the guard sweeps (each also carries its own assertions)
# ---------------------------------------------------------------------------


def _paused_batched_seat_scripts() -> dict[str, list[object]]:
    """One medium compliance finding whose first batched seat pauses once."""
    title = "Batched pause candidate"
    scripts = _qc_scripts(
        code_compliance=[
            qc_findings_response("code_compliance", findings=[_finding(title)])
        ]
    )
    scripts[title] = [
        pause_response(searched_urls=["https://example.test/batch"]),
        qc_verdict_response(True),
        qc_verdict_response(True),
    ]
    return scripts


# The grouping request's own marker (``_WatchedClient`` in
# tests/test_qc_warm_launch.py prefixes ``bucket:`` when it NAMES a call;
# the request text carries the bucket id alone).
_P1_BUCKET = "[[QC-CONSOLIDATE:element:pt1.a1.p1]]"


def _paused_grouping_scripts() -> dict[str, list[object]]:
    """Two eligible buckets; the first one's grouping call pauses once."""
    scripts = _two_bucket_scripts()
    scripts[_P1_BUCKET] = [
        pause_response(searched_urls=["https://example.test/group"]),
        # The identity partition of a two-candidate bucket (local indexes).
        singleton_consolidation_for(
            '<candidate index="0"></candidate><candidate index="1"></candidate>'
        ),
    ]
    return scripts


def _paused_lead_scripts(monkeypatch) -> tuple[dict[str, list[object]], str]:
    """One no-web lineage of eight seats: one lead, whose first turn pauses."""
    monkeypatch.setattr(qc_engine, "_WARM_LEAD_MIN_SEATS_WEB", 8)
    monkeypatch.setattr(qc_engine, "_WARM_LEAD_MIN_SEATS_NO_WEB", 8)
    doc = _titles("Doc gap", 4)
    scripts = _lineage_scripts(doc=_medium(doc))
    # The lead streams before its batch exists, so it takes its title's first
    # turn. Its continuation and the batched second seat then take the two
    # verdicts in either order — they are identical, so the race is moot.
    scripts[doc[0]] = [
        pause_response(searched_urls=["https://example.test/lead"]),
        qc_verdict_response(True),
        qc_verdict_response(True),
    ]
    return scripts, doc[0]


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def _blocks(request: dict):
    """(where, block) for every block that can carry a marker, in render
    order: tools, then system, then every message's content."""
    for tool in request.get("tools") or []:
        yield "tools", tool
    for block in request.get("system") or []:
        yield "system", block
    for message in request.get("messages") or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            for block in content:
                yield "messages", block


def _marker(block):
    if isinstance(block, dict):
        return block.get("cache_control")
    return getattr(block, "cache_control", None)


def _assert_within_the_rules(request: dict) -> None:
    explicit = [
        marker for _where, block in _blocks(request) if (marker := _marker(block))
    ]
    tail = request.get("cache_control")
    assert len(explicit) <= 3, f"more than three explicit markers: {explicit}"
    assert len(explicit) + (1 if tail else 0) <= 4
    ttls = [marker.get("ttl") for marker in explicit]
    if tail:
        # Only ever on a continuation, and always the shortest TTL.
        assert request["messages"][-1]["role"] == "assistant"
        assert tail == _TAIL
        # The documented 400: an explicit marker on the LAST block whose TTL
        # differs from the tail's. Re-sent paused content never carries one.
        last_block = request["messages"][-1]["content"][-1]
        assert _marker(last_block) is None
        ttls.append(tail.get("ttl"))
    ranks = [_TTL_RANK[ttl] for ttl in ttls]
    assert ranks == sorted(ranks, reverse=True), f"TTLs out of order: {ttls}"


def test_no_request_exceeds_four_breakpoints(monkeypatch) -> None:
    """Every request every fixture here captures, with the switch ON.

    At most three explicit markers; the tail only on a continuation, never
    beside an explicit marker on the last block; TTLs non-increasing through
    the request, the tail included. A fake client accepts any request, so
    this is the only place a malformed one would be caught before a provider
    returned a 400 for every paused call in a run.
    """
    monkeypatch.setattr(settings, "QC_MAX_WORKERS", 1)
    captured: list[dict] = []

    research = SequencedFakeClient(_pausing_scripts())
    _research(research)
    captured += research.requests

    streamed = SequencedFakeClient(_pausing_qc_scripts())
    _qc(streamed)
    captured += streamed.requests

    grouping = SequencedFakeClient(_paused_grouping_scripts())
    _qc(grouping, store=_two_paragraph_store())
    captured += grouping.requests

    batched = _StreamLog(_paused_batched_seat_scripts())
    _qc(batched, batch=True)
    captured += batched.streamed_requests + _batched_params(batched)

    lead_scripts, _lead_title = _paused_lead_scripts(monkeypatch)
    led = _StreamLog(lead_scripts)
    _qc(led, batch=True, lead=True, warm=45)
    captured += led.streamed_requests + _batched_params(led)

    for request in captured:
        _assert_within_the_rules(request)

    # Not vacuous: tails were really there, after both TTL families.
    tails = [request for request in captured if "cache_control" in request]
    assert len(tails) >= 7
    tail_families = {
        tuple(marker.get("ttl") for _w, b in _blocks(r) if (marker := _marker(b)))
        for r in tails
    }
    assert (None, None, None) in tail_families
    assert ("1h", "1h", "1h") in tail_families


def test_the_guard_refuses_what_the_provider_would() -> None:
    """The guard itself, against the shapes it exists to catch."""
    base = {
        "tools": [{"name": "t", "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
        "system": [{"text": "s", "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"text": "p", "cache_control": {"type": "ephemeral", "ttl": "1h"}},
                    {"text": "q"},
                ],
            },
            {"role": "assistant", "content": [{"type": "text", "text": "r"}]},
        ],
    }
    _assert_within_the_rules({**base, "cache_control": _TAIL})

    def refused(request: dict) -> bool:
        try:
            _assert_within_the_rules(request)
        except AssertionError:
            return True
        return False

    # A 1-hour tail after 5-minute markers is the order the API rejects.
    five_minute = json.loads(json.dumps(base).replace(', "ttl": "1h"', ""))
    assert refused({**five_minute, "cache_control": {"type": "ephemeral", "ttl": "1h"}})
    # An explicit marker on the last block beside the tail.
    marked_last = json.loads(json.dumps(base))
    marked_last["messages"][-1]["content"][-1]["cache_control"] = {"type": "ephemeral"}
    assert refused({**marked_last, "cache_control": _TAIL})
    # A tail on a first request.
    first = {**base, "messages": base["messages"][:1], "cache_control": _TAIL}
    assert refused(first)
    # A fourth explicit marker.
    crowded = json.loads(json.dumps(base))
    crowded["messages"][0]["content"][1]["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
    assert refused(crowded)


# ---------------------------------------------------------------------------
# The batched transport never carries it
# ---------------------------------------------------------------------------


def test_batched_continuations_carry_no_automatic_breakpoint(monkeypatch) -> None:
    """Batch rounds are minutes apart: a tail there would buy nothing.

    With the switch ON, a batched seat that pauses continues in a second
    round whose params carry no top-level ``cache_control`` — and every
    batch the phase submits is byte for byte the batch the switch-off run
    submits.
    """
    _fixed_clocks(monkeypatch)
    on = _StreamLog(_paused_batched_seat_scripts())
    off = _StreamLog(_paused_batched_seat_scripts())
    _qc(on, batch=True, continuation_cache=True)
    _qc(off, batch=True, continuation_cache=False)

    assert len(on.batches.created) == 2
    (resumed,) = on.batches.created[1]
    assert resumed["params"]["messages"][-1]["role"] == "assistant"
    assert all("cache_control" not in params for params in _batched_params(on))
    assert _canonical(on.batches.created) == _canonical(off.batches.created)
    # Phase 1 streamed and never paused here, so nothing carried a tail.
    assert all("cache_control" not in request for request in on.streamed_requests)


def test_a_streamed_lead_resumes_with_the_tail_and_its_batch_does_not(
    monkeypatch,
) -> None:
    """A lead (cost Tier 1, Chunk 3) is an ordinary streamed call.

    Its continuation carries the tail, 5 minutes after its 1-hour markers;
    the batch of the rest of its lineage carries none.
    """
    scripts, lead_title = _paused_lead_scripts(monkeypatch)
    client = _StreamLog(scripts)
    _qc(client, batch=True, lead=True, warm=45)

    lead_requests = [
        request
        for request in client.streamed_requests
        if "[[QC-VERIFY:" in user_text(request["messages"])
    ]
    assert [lead_title in user_text(r["messages"]) for r in lead_requests] == [
        True,
        True,
    ]
    first, continuation = lead_requests
    assert "cache_control" not in first
    assert continuation["messages"][-1]["role"] == "assistant"
    assert continuation["cache_control"] == _TAIL
    assert [
        marker.get("ttl")
        for _w, block in _blocks(continuation)
        if (marker := _marker(block))
    ] == ["1h", "1h", "1h"]
    assert client.batches.created
    assert all("cache_control" not in params for params in _batched_params(client))


# ---------------------------------------------------------------------------
# Every streamed call gets it: the grouping call too
# ---------------------------------------------------------------------------


def test_a_paused_grouping_call_resumes_with_the_tail() -> None:
    """Consolidation runs through the same streamed call, so it resumes alike.

    The paused bucket's continuation carries the tail; its first request and
    the other bucket's call do not.
    """
    client = SequencedFakeClient(_paused_grouping_scripts())
    result = _qc(client, store=_two_paragraph_store())
    assert result.consolidation.status == "complete"

    grouping = [
        request
        for request in client.requests
        if "[[QC-CONSOLIDATE:" in user_text(request["messages"])
    ]
    paused = [r for r in grouping if _P1_BUCKET in user_text(r["messages"])]
    other = [r for r in grouping if _P1_BUCKET not in user_text(r["messages"])]
    assert len(paused) == 2 and len(other) == 1
    assert "cache_control" not in paused[0]
    assert paused[1]["messages"][-1]["role"] == "assistant"
    assert paused[1]["cache_control"] == _TAIL
    assert "cache_control" not in other[0]


# ---------------------------------------------------------------------------
# The container rides beside it
# ---------------------------------------------------------------------------


def _assert_cached_blocks_unchanged(first: dict, continuation: dict) -> None:
    """Tools, system and the opening user turn — every explicit marker and
    the prefix they cache — are byte-identical across the resume."""
    for key in ("tools", "system"):
        assert _canonical(continuation[key]) == _canonical(first[key])
    assert _canonical(continuation["messages"][0]) == _canonical(first["messages"][0])


def test_the_container_and_the_breakpoint_ride_side_by_side() -> None:
    """Both top-level request arguments, neither inside a block.

    Research and QC alike: a paused turn that supplied a container resumes
    with the container AND the tail beside the request, and the blocks that
    carry the cached prefix are exactly the first request's.
    """
    research = SequencedFakeClient(
        _research_scripts(
            governing_codes=[
                pause_response(
                    searched_urls=["https://a.gov/one"], container="cont_research_1"
                ),
                research_response(
                    items=[_item("Resumed.", ["https://a.gov/one"])],
                    searched_urls=["https://a.gov/one"],
                ),
            ]
        )
    )
    _research(research)
    first, continuation = [
        req
        for req in research.requests
        if DIM_KEYS["governing_codes"] in user_text(req["messages"])
    ]
    assert "container" not in first and "cache_control" not in first
    assert continuation["container"] == "cont_research_1"
    assert continuation["cache_control"] == _TAIL
    _assert_cached_blocks_unchanged(first, continuation)

    qc = SequencedFakeClient(
        _qc_scripts(
            code_compliance=[
                qc_findings_response(
                    "code_compliance",
                    findings=[],
                    stop_reason="pause_turn",
                    container="cont_qc_1",
                ),
                qc_findings_response("code_compliance", findings=[]),
            ]
        )
    )
    _qc(qc)
    first, continuation = _lens_requests(qc, "code_compliance")
    assert "container" not in first and "cache_control" not in first
    assert continuation["container"] == "cont_qc_1"
    assert continuation["cache_control"] == _TAIL
    _assert_cached_blocks_unchanged(first, continuation)


# ---------------------------------------------------------------------------
# The plan's F3, the setting, the default
# ---------------------------------------------------------------------------


def test_a_retained_result_stays_current_across_the_switch(monkeypatch) -> None:
    """The plan's F3: how a resume is cached is not a review input.

    Checked on both verifier transports. The TRANSPORT is a review input
    (``configuration.batch_verification``), and the staleness check rebuilds
    the manifest with the live setting, so each run is compared under the
    transport it actually used — only the continuation switch moves.
    """
    _fixed_clocks(monkeypatch)
    monkeypatch.setattr(settings, "QC_MAX_WORKERS", 1)
    store = _store()
    for batch in (True, False):
        monkeypatch.setattr(settings, "QC_BATCH_VERIFICATION", batch)
        on = SequencedFakeClient(_pausing_qc_scripts())
        cached = _qc(on, store=store, batch=batch)
        plain = _qc(
            SequencedFakeClient(_pausing_qc_scripts()),
            store=store,
            batch=batch,
            continuation_cache=False,
        )

        # Not vacuous: the switch-on run really resumed with the tail.
        assert any("cache_control" in request for request in on.requests)
        assert cached.input_fingerprint == plain.input_fingerprint
        assert _canonical(cached.input_manifest) == _canonical(plain.input_manifest)
        manifest = _canonical(cached.input_manifest).lower()
        assert "continuation_cache" not in manifest
        assert "cache_control" not in manifest
        for switch in (False, True):
            monkeypatch.setattr(settings, "CONTINUATION_CACHE", switch)
            assert cached.matches_inputs(store.index, store.doc, None, QC_MODULE)
            assert plain.matches_inputs(store.index, store.doc, None, QC_MODULE)


def test_the_setting_reaches_the_research_round(monkeypatch) -> None:
    """``continuation_cache=None`` reads ``settings.CONTINUATION_CACHE``."""
    for switch, tails in ((True, 2), (False, 0)):
        monkeypatch.setattr(settings, "CONTINUATION_CACHE", switch)
        client = SequencedFakeClient(_pausing_scripts())
        _research(client, continuation_cache=None)
        assert sum("cache_control" in req for req in client.requests) == tails


def test_the_setting_reaches_the_qc_run(monkeypatch) -> None:
    """``continuation_cache=None`` reads it for Final QC too."""
    monkeypatch.setattr(settings, "QC_MAX_WORKERS", 1)
    for switch, tails in ((True, 3), (False, 0)):
        monkeypatch.setattr(settings, "CONTINUATION_CACHE", switch)
        client = SequencedFakeClient(_pausing_qc_scripts())
        _qc(client, continuation_cache=None)
        assert sum("cache_control" in req for req in client.requests) == tails


def test_continuation_cache_ships_switched_off() -> None:
    """Read from the source, so no developer's environment can move it.

    The default flips only on a recorded M3 pass (the plan's Chunk 4
    "Flip"), which replaces this test with ``..._ships_switched_on``.
    """
    source = Path(settings.__file__).read_text(encoding="utf-8")
    calls = [
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "CONTINUATION_CACHE"
            for target in node.targets
        )
    ]
    assert len(calls) == 1
    call = calls[0]
    assert isinstance(call, ast.Call)
    assert isinstance(call.func, ast.Name) and call.func.id == "_bool_env"
    assert [ast.literal_eval(arg) for arg in call.args] == [
        "BUILD_A_SPEC_CONTINUATION_CACHE",
        False,
    ]
