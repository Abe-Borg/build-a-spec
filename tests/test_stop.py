"""Stop generation: chat (graceful, commits partial progress like Claude.ai),
requirements research, and Final QC (both lossy — cooperative cancellation
that discards whatever the run had found)."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend import sessions
from backend.app import create_app
from backend.llm.conversation import stream_user_turn
from backend.research.runner import ResearchRunner
from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE
from tests.fakes import (
    FakeClient,
    SequencedFakeClient,
    block_start_event,
    block_stop_event,
    chat_search_blocks,
    research_response,
    text_delta_event,
    text_turn,
    thinking_block,
    token_usage,
    tool_turn,
    tool_use_block,
    raw_turn,
    text_block,
)
from tests.test_qc import _finding, _qc_scripts, _seed_doc  # noqa: F401 (reuse)
from tests.test_research_engine import (  # noqa: F401 (reuse)
    DIM_KEYS,
    PROFILE as RESEARCH_PROFILE,
    _scripts,
)
from tests.test_research_rounds import (  # noqa: F401 (reuse)
    _ReleaseHook,
    _start_blocked,
)


def _client() -> TestClient:
    return TestClient(create_app())


def _parse_sse(body: str) -> list[dict]:
    import json

    return [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


_SEED_EDITS = {
    "edits": [
        {
            "action": "replace",
            "target_id": "sec",
            "text": "WET-PIPE SPRINKLER SYSTEMS",
            "numbering": "21 13 13",
        },
        {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
    ]
}


# ---------------------------------------------------------------------------
# Chat stop
# ---------------------------------------------------------------------------


def test_chat_stop_endpoint_409s_when_idle_and_signals_an_active_turn():
    client = _client()
    resp = client.post("/api/chat/stop")
    assert resp.status_code == 409

    session = sessions.get_session()
    claim = session.claim_model_turn()
    assert claim is not None
    token, _generation = claim
    try:
        resp = client.post("/api/chat/stop")
        assert resp.json()["ok"] is True
        assert session.stop_requested.is_set()
    finally:
        session.finalize_model_turn(token, committed=False)


def test_chat_stop_mid_stream_truncates_the_live_events_but_commits():
    """Stopping partway through a text stream ends the SSE stream early (the
    caller never sees the rest of the scripted text) yet still commits a
    normal turn — no error, no rollback — mirroring Claude.ai's stop button."""
    events = [
        block_start_event(0, "text"),
        text_delta_event(0, "Hello"),
        text_delta_event(0, " world"),
        text_delta_event(0, "!"),
        block_stop_event(0),
    ]
    fake = FakeClient(
        [raw_turn([text_block("Hello world!")], stop_reason="end_turn", events=events)]
    )

    session = sessions.get_session()
    import backend.llm.conversation as conv

    orig_get_client = conv.get_client
    conv.get_client = lambda: fake  # type: ignore[assignment]
    try:
        seen_deltas: list[str] = []
        saw_turn_complete = False
        for event in stream_user_turn(session, "say hi"):
            if event["type"] == "text_delta":
                seen_deltas.append(event["text"])
                if len(seen_deltas) == 1:
                    session.stop_requested.set()
            elif event["type"] == "turn_complete":
                saw_turn_complete = True
            assert event["type"] != "error"
    finally:
        conv.get_client = orig_get_client

    # Only the first delta was seen — the stream stopped, it didn't drain.
    assert seen_deltas == ["Hello"]
    assert saw_turn_complete
    assert session.turn_active is False

    # The turn committed (not rolled back): one new user/assistant pair.
    assert len(session.history) == 2
    assert session.history[0]["role"] == "user"
    assert session.history[1]["role"] == "assistant"

    # The session is still healthy — a follow-up turn streams normally.
    fake2 = FakeClient([text_turn(["Continuing fine."])])
    conv.get_client = lambda: fake2  # type: ignore[assignment]
    try:
        events2 = list(stream_user_turn(session, "and then?"))
    finally:
        conv.get_client = orig_get_client
    assert events2[-1]["type"] == "turn_complete"
    assert len(session.history) == 4


def test_chat_stop_between_rounds_after_tool_dispatch_keeps_history_alternating():
    """Stopping right after a tool_use round (before the model gets to react
    to the tool_result) must not leave a dangling tool_result as the last
    history message — the next turn's user message would collide with it."""
    fake = FakeClient(
        [tool_turn(["Drafting."], _SEED_EDITS), text_turn(["Should not be reached."])]
    )
    session = sessions.get_session()
    import backend.llm.conversation as conv

    orig_get_client = conv.get_client
    conv.get_client = lambda: fake  # type: ignore[assignment]
    try:
        for event in stream_user_turn(session, "draft it"):
            if event["type"] == "doc_patch":
                session.stop_requested.set()
    finally:
        conv.get_client = orig_get_client

    # Round 1 never actually streamed — only one request was made.
    assert len(fake.messages.requests) == 1

    # The doc edit from the completed tool round IS kept (committed, not
    # rolled back).
    assert session.doc.doc.parts[0].articles[0].title == "SUMMARY"

    # History ends on an assistant turn, not the dangling tool_result.
    assert session.history[-1]["role"] == "assistant"
    assert session.history[-1]["content"] == [
        {"type": "text", "text": "[Generation stopped by user.]"}
    ]
    assert session.history[-2]["role"] == "user"  # the tool_result message

    # A follow-up turn still streams fine (roles kept alternating).
    fake2 = FakeClient([text_turn(["ok"])])
    conv.get_client = lambda: fake2  # type: ignore[assignment]
    try:
        events2 = list(stream_user_turn(session, "keep going"))
    finally:
        conv.get_client = orig_get_client
    assert events2[-1]["type"] == "turn_complete"


# ---------------------------------------------------------------------------
# Stopping mid-search: the dangling server_tool_use class
# ---------------------------------------------------------------------------


def _run_turn(session, fake, text: str) -> list[dict]:
    """Drive one turn against ``fake``, restoring the client factory after."""
    import backend.llm.conversation as conv

    orig = conv.get_client
    conv.get_client = lambda: fake  # type: ignore[assignment]
    try:
        return list(stream_user_turn(session, text))
    finally:
        conv.get_client = orig


def _server_tool_uses(messages) -> list[str]:
    return [
        b.get("id")
        for m in messages
        for b in (m.get("content") or [])
        if b.get("type") == "server_tool_use"
    ]


def _outgoing_history_is_valid(request: dict) -> bool:
    """Every ``server_tool_use`` in a captured request has a result."""
    referenced = {
        b.get("tool_use_id")
        for m in request["messages"]
        for b in (m.get("content") or [])
        if isinstance(b, dict) and b.get("tool_use_id")
    }
    return all(
        use_id in referenced
        for use_id in _server_tool_uses(request["messages"])
    )


def _stop_mid_search_turn(session, *, stop_reason: str):
    """A turn whose response opens a web search that never returns a result.

    ``stop_reason="end_turn"`` + a user stop mid-stream is the reported bug
    (stopping while the UI says "Searching the web…"); ``max_tokens`` is the
    same shape reached without any user action.
    """
    search_use = SimpleNamespace(
        type="server_tool_use",
        id="srvtoolu_interrupted",
        name="web_search",
        input={"query": "NFPA 13 obstruction"},
    )
    events = [
        block_start_event(0, "text"),
        text_delta_event(0, "Let me check that."),
        block_stop_event(0),
        block_start_event(1, "server_tool_use", "web_search"),
        block_stop_event(1),
    ]
    return raw_turn(
        [text_block("Let me check that."), search_use],
        stop_reason=stop_reason,
        events=events,
    )


def test_stop_mid_search_never_commits_a_dangling_server_tool_use():
    session = sessions.get_session()
    fake = FakeClient([_stop_mid_search_turn(session, stop_reason="end_turn")])

    import backend.llm.conversation as conv

    orig = conv.get_client
    conv.get_client = lambda: fake  # type: ignore[assignment]
    try:
        for event in stream_user_turn(session, "check that"):
            if event["type"] == "text_delta":
                session.stop_requested.set()
            assert event["type"] != "error"
    finally:
        conv.get_client = orig

    # The search call is gone; the text the user actually saw survives.
    assert _server_tool_uses(session.history) == []
    assert session.history[-1]["role"] == "assistant"
    assert any(
        b.get("type") == "text" for b in session.history[-1]["content"]
    )

    # And the session is usable: the next turn's outgoing history validates
    # and the turn succeeds. Before the scrub this request was a 400.
    fake2 = FakeClient([text_turn(["Still here."])])
    events2 = _run_turn(session, fake2, "and then?")
    assert events2[-1]["type"] == "turn_complete"
    assert _outgoing_history_is_valid(fake2.messages.requests[0])


def test_max_tokens_mid_search_never_commits_a_dangling_server_tool_use():
    session = sessions.get_session()
    fake = FakeClient([_stop_mid_search_turn(session, stop_reason="max_tokens")])
    events = _run_turn(session, fake, "check that")
    assert events[-1]["type"] == "turn_complete"

    assert _server_tool_uses(session.history) == []
    fake2 = FakeClient([text_turn(["Still here."])])
    assert _run_turn(session, fake2, "again")[-1]["type"] == "turn_complete"
    assert _outgoing_history_is_valid(fake2.messages.requests[0])


def test_stop_in_the_gap_after_a_paused_response_drops_the_pending_search():
    """The between-rounds stop path, landing right after a pause_turn.

    The paused message holds a use whose result would have arrived in the
    round the user just cancelled — the one case where the dangling block is
    in an EARLIER message than the one the stop appends, which is why the
    scrub is turn-wide rather than applied to the terminal message.
    """
    session = sessions.get_session()
    paused = raw_turn(
        [
            SimpleNamespace(
                type="server_tool_use",
                id="srvtoolu_paused",
                name="web_search",
                input={"query": "still running"},
            )
        ],
        stop_reason="pause_turn",
    )
    fake = FakeClient([paused, text_turn(["Should not be reached."])])

    import backend.llm.conversation as conv

    orig = conv.get_client
    conv.get_client = lambda: fake  # type: ignore[assignment]
    try:
        for event in stream_user_turn(session, "look it up"):
            # Stop as soon as the paused round's status arrives, so the
            # between-rounds check fires before round 1 streams.
            if event["type"] == "status":
                session.stop_requested.set()
            assert event["type"] != "error"
    finally:
        conv.get_client = orig

    assert _server_tool_uses(session.history) == []
    assert session.history[-1]["role"] == "assistant"
    fake2 = FakeClient([text_turn(["Fine."])])
    assert _run_turn(session, fake2, "next")[-1]["type"] == "turn_complete"
    assert _outgoing_history_is_valid(fake2.messages.requests[0])


def test_a_pending_search_is_resent_verbatim_on_a_pause_resume():
    """The normal (un-stopped) continuation must keep the pending call.

    This is the shape the earlier fixtures never had: a pause whose search
    genuinely has not returned. The provider resumes from that trailing
    ``server_tool_use``, so a scrub that removes it aborts the work.
    """
    session = sessions.get_session()
    pending = SimpleNamespace(
        type="server_tool_use",
        id="srvtoolu_pending",
        name="web_search",
        input={"query": "still running"},
    )
    fake = FakeClient(
        [
            raw_turn([text_block("Searching."), pending], stop_reason="pause_turn"),
            text_turn(["Found it."]),
        ]
    )
    events = _run_turn(session, fake, "look it up")
    assert events[-1]["type"] == "turn_complete"

    # The continuation re-sent the paused content verbatim, pending call
    # included.
    resumed = fake.messages.requests[1]["messages"][-1]
    assert resumed["role"] == "assistant"
    assert [b["type"] for b in resumed["content"]] == ["text", "server_tool_use"]
    assert resumed["content"][1]["id"] == "srvtoolu_pending"

    # Once the turn ends, the still-unanswered call does NOT reach history —
    # committed state has no pause left to resume.
    assert _server_tool_uses(session.history) == []


def test_a_completed_search_survives_the_same_truncation():
    """The scrub must not punish a search that actually finished.

    Same truncation branch as the dangling case above — the only difference
    is that this response's search came back — so it pins that the branch
    removes the unpaired call rather than every server-tool block near a
    cut-off response.
    """
    session = sessions.get_session()
    fake = FakeClient(
        [
            raw_turn(
                [
                    *chat_search_blocks("obstruction rules", ["https://nfpa.org"]),
                    text_block("Obstruction rules confirmed."),
                ],
                stop_reason="max_tokens",
            ),
        ]
    )
    events = _run_turn(session, fake, "check")
    assert events[-1]["type"] == "turn_complete"
    types = {b["type"] for m in session.history for b in m["content"]}
    assert "server_tool_use" in types and "web_search_tool_result" in types
    assert _server_tool_uses(session.history) != []


# ---------------------------------------------------------------------------
# Research stop
# ---------------------------------------------------------------------------


def _record_profile(client: TestClient, monkeypatch) -> None:
    fake = FakeClient(
        [
            tool_turn(
                ["Recorded."],
                {
                    "edits": [
                        {
                            "action": "set_project_profile",
                            "target_id": "sec",
                            "city": "Ashburn",
                            "state": "Virginia",
                            "country": "USA",
                            "client": "ExampleCo",
                        }
                    ]
                },
            ),
            text_turn(["Done."]),
        ]
    )
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    resp = client.post("/api/chat", json={"message": "Ashburn VA, client ExampleCo"})
    assert _parse_sse(resp.text)[-1]["type"] == "turn_complete"


def _wait_terminal(client: TestClient, path: str, timeout_s: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        snapshot = client.get(path).json()
        if snapshot["status"] in ("complete", "failed"):
            return snapshot
        time.sleep(0.05)
    raise AssertionError(f"{path} did not settle in time")


class _Blocking:
    """A client whose every streaming call blocks until released, then fails
    with a non-retryable error (fast, deterministic once unblocked)."""

    def __init__(self, release: threading.Event):
        self._release = release
        self.messages = self

    def stream(self, **_request):
        self._release.wait(timeout=5)
        raise RuntimeError("aborted")


def test_research_stop_discards_running_work_and_allows_immediate_restart(
    monkeypatch,
):
    client = _client()
    # The neutral default is now the generic module, which gates research on a
    # stated discipline; select the curated fire module (no gate) instead.
    client.post("/api/session/reset", json={"module_id": "hyperscale_fire"})
    _record_profile(client, monkeypatch)

    release = threading.Event()
    monkeypatch.setattr("backend.app.get_client", lambda: _Blocking(release))
    assert client.post("/api/research/start").json()["ok"] is True
    assert client.get("/api/research/status").json()["status"] == "running"

    old_runner = sessions.get_session().research
    assert client.post("/api/research/stop").json()["ok"] is True
    snapshot = client.get("/api/research/status").json()
    assert snapshot["status"] == "failed"
    assert "stopped" in snapshot["error"].lower()
    assert "profile" not in snapshot

    # Nothing is running any more — a second stop is a no-op.
    assert client.post("/api/research/stop").status_code == 409

    # Let the abandoned thread's blocked calls fail and unwind; its (also
    # failed) outcome must NOT clobber the already-resolved status/message.
    release.set()
    old_runner._thread.join(timeout=5)
    assert not old_runner._thread.is_alive()
    assert old_runner.status == "failed"
    assert "stopped" in old_runner.error.lower()
    assert old_runner.profile_result is None
    assert client.get("/api/research/status").json()["status"] == "failed"

    # A fresh run starts immediately and completes normally.
    monkeypatch.setattr(
        "backend.app.get_client", lambda: SequencedFakeClient(_scripts())
    )
    assert client.post("/api/research/start").json()["ok"] is True
    assert _wait_terminal(client, "/api/research/status")["status"] == "complete"


def test_a_stop_cannot_cancel_the_round_that_replaces_it():
    """The stop signal belongs to the round the user actually stopped.

    ``stop()`` used to set ``self._cancel_event`` after its compare-and-set
    had released the lock — and a fresh ``start()`` installs its own cancel
    event at exactly that seam. A user who stopped a round and immediately
    started another therefore cancelled the new one, which would then bail
    out of every dimension before spending anything and report itself
    stopped by a user who had asked for the opposite.
    """
    runner = ResearchRunner()
    runner._lock = _ReleaseHook(runner._lock)
    release = threading.Event()
    first_settled, second_settled = threading.Event(), threading.Event()
    _start_blocked(runner, release, first_settled)
    stopped_cancel = runner._cancel_event

    def _start_successor() -> None:
        if runner.status not in ("complete", "failed"):
            return
        runner._lock.on_release = None  # one-shot: start() locks too
        _start_blocked(runner, release, second_settled)

    runner._lock.on_release = _start_successor
    assert runner.stop() is True
    runner._lock.on_release = None

    successor_cancel = runner._cancel_event
    assert successor_cancel is not stopped_cancel, "the successor really started"
    assert runner.status == "running"
    assert stopped_cancel.is_set(), "the stopped round was cancelled"
    assert not successor_cancel.is_set(), "the successor was not"

    release.set()
    assert first_settled.wait(timeout=10)
    assert second_settled.wait(timeout=10)


def test_a_stopped_research_round_still_meters_what_it_already_spent():
    """Stopping is lossy by design — the findings go — but the provider does
    not refund the calls, so the spend still belongs to the session.

    The ordering this pins: ``stop()`` resolves the run synchronously, so by
    the time the worker's fan-out error surfaces, the failure branch's
    compare-and-set LOSES. Metering gated on winning that CAS would drop
    exactly the spend a user who just cancelled is most likely to ask about.
    """
    arrived = threading.Event()
    release = threading.Event()

    class _BarrierClient(SequencedFakeClient):
        """Holds every dimension at its first call until released."""

        def __init__(self, scripts: dict, expected: int) -> None:
            super().__init__(scripts)
            self._expected = expected
            self._gate = threading.Lock()
            self._seen = 0

        def stream(self, **request):
            with self._gate:
                self._seen += 1
                if self._seen == self._expected:
                    arrived.set()
            assert release.wait(timeout=10)
            return super().stream(**request)

    # One billed pause per dimension: the worker banks that response, then
    # checks should_stop() at the top of the continuation loop and bails —
    # so every dimension ends "Cancelled by user." holding real usage.
    client = _BarrierClient(
        {
            key: [
                research_response(
                    searched_urls=["https://a.gov"],
                    stop_reason="pause_turn",
                    tokens={"input": 50},
                )
            ]
            for key in DIM_KEYS.values()
        },
        expected=len(DIM_KEYS),
    )

    runner = ResearchRunner()
    settled = threading.Event()
    billed: list[dict] = []
    assert runner.start(
        module=HYPERSCALE_FIRE,
        project_profile=RESEARCH_PROFILE,
        client=client,
        model="claude-sonnet-5",
        max_tokens=4096,
        on_settled=settled.set,
        usage_sink=billed.append,
    )

    assert arrived.wait(timeout=10)
    # Resolve the run first, exactly as POST /api/research/stop does…
    assert runner.stop() is True
    assert runner.status == "failed"
    # …then let the already-paid-for responses land.
    release.set()
    assert settled.wait(timeout=10)

    # The round is gone.
    assert runner.profile_result is None
    assert "stopped" in runner.error.lower()
    # The bill is not: 4 dimensions × one 50-token paused response.
    assert billed == [{"input_tokens": 200, "web_search_requests": 4}]


# ---------------------------------------------------------------------------
# Stopped-turn output estimate (the provider never sends the final count)
# ---------------------------------------------------------------------------


def _stopped_turn_usage(content, *, snapshot_usage) -> dict:
    """Stop a turn after its first delta; return `turn_complete`'s usage.

    The scripted snapshot usage is what the stop path reads, standing in for
    the small ``message_start`` placeholder the real SDK holds when the
    closing ``message_delta`` never arrives.
    """
    events = [
        block_start_event(0, "text"),
        text_delta_event(0, "partial"),
        block_stop_event(0),
    ]
    fake = FakeClient(
        [
            raw_turn(
                content,
                stop_reason="end_turn",
                events=events,
                snapshot_usage=snapshot_usage,
            )
        ]
    )
    session = sessions.get_session()
    import backend.llm.conversation as conv

    orig_get_client = conv.get_client
    conv.get_client = lambda: fake  # type: ignore[assignment]
    try:
        usage: dict = {}
        for event in stream_user_turn(session, "write something long"):
            if event["type"] == "text_delta":
                session.stop_requested.set()
            elif event["type"] == "turn_complete":
                usage = event["usage"]
            assert event["type"] != "error"
        return usage
    finally:
        conv.get_client = orig_get_client


def test_a_stopped_turn_estimates_the_output_the_provider_never_reported():
    """The headline case: a long reply stopped mid-stream. The provider's
    placeholder says 4 output tokens; ~2000 characters actually arrived."""
    usage = _stopped_turn_usage(
        [text_block("word " * 400)],  # 2000 chars → ~500 tokens
        snapshot_usage=token_usage(input=1000, output=4),
    )

    # The provider's number is untouched — this is the one field that can be
    # reconciled against an invoice.
    assert usage["output_tokens"] == 4
    # The shortfall lives in its own counter, and is disclosed.
    assert usage["estimated_output_tokens"] == 496
    assert usage["usage_estimated"] is True


def test_a_provider_count_larger_than_the_heuristic_adds_nothing():
    """The estimate can only ever ADD. When the provider already reported
    more than the heuristic guesses, the record stays purely reported."""
    usage = _stopped_turn_usage(
        [text_block("short")],  # 5 chars → 2 tokens
        snapshot_usage=token_usage(input=100, output=50),
    )
    assert usage["output_tokens"] == 50
    assert "estimated_output_tokens" not in usage
    assert "usage_estimated" not in usage


def test_the_estimate_counts_thinking_and_tool_input_but_not_tool_results():
    """Thinking summaries and the model's tool-call JSON are output it was
    billed for. A stop most often lands mid tool-input, so missing them
    would under-report exactly the turns this exists for."""
    usage = _stopped_turn_usage(
        [
            thinking_block("t" * 400),
            text_block("x" * 400),
            tool_use_block("toolu_1", "apply_spec_edits", {"edits": "e" * 400}),
        ],
        snapshot_usage=token_usage(input=100, output=1),
    )
    # 400 thinking + 400 text + (name + JSON-serialized input) ≥ 1200 chars,
    # so ≥ 300 tokens estimated, less the 1 the provider reported. Asserted
    # as a floor, not an exact number: this is a heuristic, and pinning a
    # tokenizer-exact figure would make it brittle for no benefit.
    assert usage["output_tokens"] == 1
    assert usage["estimated_output_tokens"] >= 299
    assert usage["usage_estimated"] is True


def test_more_output_never_estimates_fewer_tokens():
    """Monotonicity — the property worth pinning for a heuristic."""
    small = _stopped_turn_usage(
        [text_block("word " * 100)],
        snapshot_usage=token_usage(input=100, output=1),
    )
    large = _stopped_turn_usage(
        [text_block("word " * 400)],
        snapshot_usage=token_usage(input=100, output=1),
    )
    assert (
        large["estimated_output_tokens"] > small["estimated_output_tokens"] > 0
    )


def test_a_normal_turn_carries_no_estimate_at_all():
    """Exact provider usage wins; the estimated counter stays absent."""
    fake = FakeClient(
        [text_turn(["A complete reply."], usage=token_usage(input=100, output=25))]
    )
    session = sessions.get_session()
    import backend.llm.conversation as conv

    orig_get_client = conv.get_client
    conv.get_client = lambda: fake  # type: ignore[assignment]
    try:
        usage: dict = {}
        for event in stream_user_turn(session, "hello"):
            if event["type"] == "turn_complete":
                usage = event["usage"]
    finally:
        conv.get_client = orig_get_client

    assert usage["output_tokens"] == 25
    assert "estimated_output_tokens" not in usage
    assert "usage_estimated" not in usage


def test_the_context_gauge_includes_a_stopped_turns_estimated_output():
    """The gauge pairs the last request's prompt with the reply about to be
    committed, so omitting the estimate would undercount the conversation
    the next turn has to re-send."""
    usage = _stopped_turn_usage(
        [text_block("word " * 400)],
        snapshot_usage=token_usage(input=1000, output=4),
    )
    session = sessions.get_session()
    # prompt 1000 + retained output 4 + estimated 496.
    assert session.last_context_tokens == 1500
    assert usage["estimated_output_tokens"] == 496


def test_the_gauge_excludes_output_the_commit_throws_away():
    """Billing and the gauge want different numbers, and conflating them
    overstates the gauge badly.

    Thinking blocks are stripped by `_committed_messages` and an unexecuted
    `tool_use` is dropped by the truncation branch — both were billed, so
    they belong in the spend estimate, but neither is ever re-sent. A gauge
    that counted them would promise the user thousands of tokens the next
    turn does not carry. (Caught in review on PR #102.)
    """
    usage = _stopped_turn_usage(
        [
            thinking_block("t" * 4000),  # billed, then stripped at commit
            text_block("word " * 100),  # 500 chars → 125 tokens, retained
            tool_use_block(  # billed, then dropped as unexecuted
                "toolu_1", "apply_spec_edits", {"edits": "e" * 4000}
            ),
        ],
        snapshot_usage=token_usage(input=1000, output=4),
    )
    session = sessions.get_session()

    # The BILL counts everything the model authored: >2000 tokens of
    # thinking and tool input on top of the text.
    assert usage["estimated_output_tokens"] > 2000

    # The GAUGE counts only the surviving text: 1000 prompt + 4 reported
    # + (125 - 4) estimated retained = 1125. Emphatically not 1000 + 2000+.
    assert session.last_context_tokens == 1125


# ---------------------------------------------------------------------------
# QC stop
# ---------------------------------------------------------------------------


def test_a_normal_qc_run_never_reports_a_stop_it_did_not_get(monkeypatch):
    """The three backend surfaces that used to claim every run was stopped.

    `settling` was read straight off `_worker_settled`, which is False for
    the whole of any ordinary run — so the double-start 409, the readiness
    `qc_current` detail, and the Review Room's banner all announced a stop
    the first time anyone ran Final QC.
    """
    client = _client()
    _seed_doc(client, monkeypatch)

    release = threading.Event()
    monkeypatch.setattr("backend.app.get_client", lambda: _Blocking(release))
    try:
        assert client.post("/api/qc/start").json()["ok"] is True

        snapshot = client.get("/api/qc/status").json()
        assert snapshot["status"] == "running"
        assert snapshot["settling"] is False

        detail = next(
            check["detail"]
            for check in client.get("/api/readiness").json()["checks"]
            if check["id"] == "qc_current"
        )
        assert "running" in detail.lower()
        assert "stop" not in detail.lower() and "settl" not in detail.lower()

        second = client.post("/api/qc/start")
        assert second.status_code == 409
        assert second.json()["error"] == "Final QC is already running."
    finally:
        release.set()
        sessions.get_session().qc._thread.join(timeout=5)


def test_qc_stop_discards_running_work_and_allows_immediate_restart(monkeypatch):
    client = _client()
    _seed_doc(client, monkeypatch)

    release = threading.Event()
    monkeypatch.setattr("backend.app.get_client", lambda: _Blocking(release))
    assert client.post("/api/qc/start").json()["ok"] is True
    assert client.get("/api/qc/status").json()["status"] == "running"

    old_runner = sessions.get_session().qc
    assert client.post("/api/qc/stop").json()["ok"] is True
    snapshot = client.get("/api/qc/status").json()
    assert snapshot["status"] == "failed"
    assert "stopped" in snapshot["error"].lower()
    assert "result" not in snapshot

    assert client.post("/api/qc/stop").status_code == 409

    release.set()
    old_runner._thread.join(timeout=5)
    assert not old_runner._thread.is_alive()
    assert old_runner.status == "failed"
    assert "stopped" in old_runner.error.lower()
    assert old_runner.result is None
    assert client.get("/api/qc/status").json()["status"] == "failed"

    monkeypatch.setattr(
        "backend.app.get_client", lambda: SequencedFakeClient(_qc_scripts())
    )
    assert client.post("/api/qc/start").json()["ok"] is True
    assert _wait_terminal(client, "/api/qc/status")["status"] == "complete"
