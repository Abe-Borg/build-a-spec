"""Chat-authored figures: store units + the create_figure tool loop.

Covers the ``FigureStore`` (validation, turn atomicity, persistence, CSV),
the ``create_figure`` tool end-to-end through ``/api/chat`` (the SSE figure
event, the token-discipline tool result, rollback on failure, self-correction
on a bad payload), and the figure REST surface (list / CSV / delete). Mirrors
the hermetic fake-client convention of ``test_full_draft.py``.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend import sessions
from backend.app import create_app
from backend.figures import Figure, FigureError, FigureStore
from tests.fakes import FakeClient, text_turn, tool_turn


def _client() -> TestClient:
    return TestClient(create_app())


def _parse_sse(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def _patch_client(monkeypatch, fake: FakeClient) -> None:
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)


_MERMAID = "graph TD; A[City main]-->B[Backflow]-->C[Riser];"
_MERMAID_FIGURE = {
    "kind": "mermaid",
    "title": "Sprinkler riser schematic",
    "caption": "Wet-pipe riser, city main to system riser.",
    "alt_text": "Flow from the city main through a backflow preventer to the riser.",
    "source": _MERMAID,
}
_TABLE_FIGURE = {
    "kind": "table",
    "title": "Sprinkler design schedule",
    "columns": ["Area", "Density (gpm/ft²)", "Remote area (ft²)"],
    "rows": [
        ["Office", "0.10", "1500"],
        ["Storage", "0.20", "2000"],
    ],
}


def _figure_turn(figure_input: dict, *, close: str = "Done — see above.") -> FakeClient:
    """A two-round turn: create one figure, then a closing text round."""
    return FakeClient(
        [
            tool_turn(
                ["Here is a figure. "],
                figure_input,
                tool_id="toolu_fig",
                name="create_figure",
            ),
            text_turn([close]),
        ]
    )


# ---------------------------------------------------------------------------
# FigureStore units
# ---------------------------------------------------------------------------


def test_create_assigns_monotonic_ids_and_stores_fields():
    store = FigureStore()
    f1 = store.create(_MERMAID_FIGURE, message_index=2)
    f2 = store.create(_TABLE_FIGURE, message_index=3)
    assert (f1.fid, f2.fid) == ("fig-1", "fig-2")
    assert f1.kind == "mermaid" and f1.source == _MERMAID
    assert f1.message_index == 2
    assert f1.created_at  # stamped
    assert f2.columns == _TABLE_FIGURE["columns"]
    assert f2.rows == _TABLE_FIGURE["rows"]


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "flowchart", "title": "x"},  # unknown kind
        {"kind": "mermaid", "title": "  "},  # empty title
        {"kind": "mermaid", "title": "x"},  # mermaid without source
        {"kind": "svg", "title": "x", "source": "not svg markup"},  # no <svg>
        {"kind": "table", "title": "x"},  # table without columns
        {"kind": "table", "title": "x", "columns": ["A"], "rows": "nope"},  # bad rows
    ],
)
def test_create_rejects_malformed_payloads(payload):
    with pytest.raises(FigureError):
        FigureStore().create(payload)


def test_table_rows_are_padded_and_truncated_to_column_count():
    store = FigureStore()
    fig = store.create(
        {
            "kind": "table",
            "title": "t",
            "columns": ["A", "B", "C"],
            "rows": [["1"], ["1", "2", "3", "4"]],
        }
    )
    assert fig.rows == [["1", "", ""], ["1", "2", "3"]]


def test_turn_commit_keeps_additions():
    store = FigureStore()
    store.begin_turn()
    store.create(_MERMAID_FIGURE)
    store.commit_turn()
    assert len(store.figures) == 1


def test_turn_rollback_drops_additions_without_reusing_ids():
    store = FigureStore()
    store.begin_turn()
    first = store.create(_MERMAID_FIGURE)
    assert first.fid == "fig-1"
    store.rollback_turn()
    assert store.figures == []
    # The id is not recycled — the next figure is fig-2 (monotonic ids).
    store.begin_turn()
    second = store.create(_MERMAID_FIGURE)
    store.commit_turn()
    assert second.fid == "fig-2"


def test_begin_turn_self_heals_an_abandoned_prior_turn():
    store = FigureStore()
    store.begin_turn()
    store.create(_MERMAID_FIGURE)  # never committed/rolled back
    store.begin_turn()  # self-heals: drops the abandoned addition
    assert store.figures == []


def test_to_dict_from_dict_round_trip():
    store = FigureStore()
    store.create(_MERMAID_FIGURE)
    store.create(_TABLE_FIGURE)
    data = store.to_dict()
    reloaded = FigureStore()
    reloaded.load(data)
    assert [f.fid for f in reloaded.figures] == ["fig-1", "fig-2"]
    assert reloaded.figures[0].source == _MERMAID
    # next_seq clears the restored max so a new figure never collides.
    assert reloaded.create(_MERMAID_FIGURE).fid == "fig-3"


def test_load_degrades_gracefully_on_garbage():
    store = FigureStore()
    store.load(None)
    assert store.figures == []
    store.load({"figures": "not a list"})
    assert store.figures == []
    store.load({"figures": [{"kind": "bogus"}, {"no": "fid"}]})
    assert store.figures == []


def test_to_csv_renders_table_rows():
    fig = FigureStore().create(_TABLE_FIGURE)
    csv_text = fig.to_csv()
    assert "Area,Density (gpm/ft²),Remote area (ft²)" in csv_text
    assert "Office,0.10,1500" in csv_text


def test_to_csv_neutralizes_formula_injection():
    """A model-authored cell that would execute as a spreadsheet formula is
    prefixed with a single quote (CWE-1236); benign cells are untouched."""
    fig = FigureStore().create(
        {
            "kind": "table",
            "title": "t",
            "columns": ["=danger()", "Note"],
            "rows": [
                ["=SUM(A1:A2)", "+1"],
                ["@cmd", "-5"],
                ["Office", "0.10"],
            ],
        }
    )
    csv_text = fig.to_csv()
    for dangerous in ("'=danger()", "'=SUM(A1:A2)", "'+1", "'@cmd", "'-5"):
        assert dangerous in csv_text
    # A benign numeric/text cell is not mangled.
    assert "Office,0.10" in csv_text
    assert "'Office" not in csv_text


def test_context_stubs_name_figures_but_hide_source():
    store = FigureStore()
    store.create(_MERMAID_FIGURE)
    stubs = store.context_stubs()
    assert "fig-1" in stubs
    assert "Sprinkler riser schematic" in stubs
    # Token discipline: the heavy source never appears in the context stub.
    assert _MERMAID not in stubs


# ---------------------------------------------------------------------------
# create_figure through the chat loop
# ---------------------------------------------------------------------------


def test_create_figure_emits_event_persists_and_hides_source_from_the_model(
    monkeypatch,
):
    client = _client()
    _patch_client(monkeypatch, _figure_turn(_MERMAID_FIGURE))

    resp = client.post("/api/chat", json={"message": "Draw the riser."})
    events = _parse_sse(resp.text)

    # A live "drawing" status and the figure event both fire.
    assert any(
        e["type"] == "status" and e.get("kind") == "drawing" for e in events
    )
    figure_events = [e for e in events if e["type"] == "figure"]
    assert len(figure_events) == 1
    figure = figure_events[0]["figure"]
    assert figure["fid"] == "fig-1"
    assert figure["kind"] == "mermaid"
    assert figure["source"] == _MERMAID  # full source reaches the chat
    assert events[-1]["type"] == "turn_complete"

    # The figure persisted (survives the turn commit).
    figures = client.get("/api/figures").json()["figures"]
    assert [f["fid"] for f in figures] == ["fig-1"]
    assert client.get("/api/doc").json()["figures"][0]["fid"] == "fig-1"

    # Token discipline: the heavy source lives ONLY in the figure store —
    # it must not appear ANYWHERE in committed history (not the compact tool
    # result, and not the create_figure tool_use input, which is elided at
    # commit like a fetched PDF). Either would re-bill on every later turn.
    history = sessions.get_session().history
    history_json = json.dumps(history)
    assert "fig-1" in history_json  # the compact reference survives
    assert _MERMAID not in history_json  # the source does not

    tool_uses = [
        block
        for message in history
        for block in (message.get("content") or [])
        if isinstance(block, dict)
        and block.get("type") == "tool_use"
        and block.get("name") == "create_figure"
    ]
    assert tool_uses, "the create_figure tool_use should be in history"
    assert "source" not in tool_uses[0]["input"]
    assert tool_uses[0]["input"].get("_elided")


def test_figure_source_stays_out_of_the_next_turns_context(monkeypatch):
    client = _client()
    # Turn 1 creates the figure.
    _patch_client(monkeypatch, _figure_turn(_MERMAID_FIGURE))
    client.post("/api/chat", json={"message": "Draw the riser."})

    # Turn 2: capture the request the model receives.
    fake2 = FakeClient([text_turn(["Noted."])])
    _patch_client(monkeypatch, fake2)
    client.post("/api/chat", json={"message": "What's next?"})

    request = fake2.messages.last_request
    context = ""
    for message in request["messages"]:
        for block in message.get("content", []):
            if isinstance(block, dict) and "PROJECT CONTEXT" in block.get("text", ""):
                context = block["text"]
    # The stub tells the model the figure exists; the source never appears.
    assert "fig-1" in context
    assert "Sprinkler riser schematic" in context
    assert _MERMAID not in context


def test_failed_turn_rolls_back_a_provisional_figure(monkeypatch):
    client = _client()
    fake = FakeClient(
        [
            tool_turn(
                ["Drawing… "],
                _MERMAID_FIGURE,
                tool_id="toolu_fig",
                name="create_figure",
            ),
            RuntimeError("boom"),  # the round after the figure blows up
        ]
    )
    _patch_client(monkeypatch, fake)

    events = _parse_sse(client.post("/api/chat", json={"message": "Draw."}).text)
    assert any(e["type"] == "error" for e in events)
    # Turn atomicity spans the figure store: nothing persisted.
    assert client.get("/api/figures").json()["figures"] == []


def test_invalid_create_figure_is_correctable_not_a_turn_failure(monkeypatch):
    client = _client()
    fake = FakeClient(
        [
            # kind=mermaid but no source → rejected as an is_error tool result.
            tool_turn(
                ["Trying… "],
                {"kind": "mermaid", "title": "Riser"},
                tool_id="toolu_bad",
                name="create_figure",
            ),
            text_turn(["Never mind the diagram."]),
        ]
    )
    _patch_client(monkeypatch, fake)

    events = _parse_sse(client.post("/api/chat", json={"message": "Draw."}).text)
    assert not any(e["type"] == "error" for e in events)
    assert not any(e["type"] == "figure" for e in events)
    assert events[-1]["type"] == "turn_complete"
    assert client.get("/api/figures").json()["figures"] == []


# ---------------------------------------------------------------------------
# Figure REST surface: CSV / delete / persistence
# ---------------------------------------------------------------------------


def test_table_figure_downloads_as_csv(monkeypatch):
    client = _client()
    _patch_client(monkeypatch, _figure_turn(_TABLE_FIGURE))
    client.post("/api/chat", json={"message": "Make a schedule."})

    resp = client.get("/api/figure/fig-1/csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    assert "Office,0.10,1500" in resp.text


def test_csv_endpoint_rejects_non_table_and_unknown(monkeypatch):
    client = _client()
    _patch_client(monkeypatch, _figure_turn(_MERMAID_FIGURE))
    client.post("/api/chat", json={"message": "Draw."})

    assert client.get("/api/figure/fig-1/csv").status_code == 400  # mermaid, not table
    assert client.get("/api/figure/nope/csv").status_code == 404


def test_delete_figure_removes_it_and_guards(monkeypatch):
    client = _client()
    _patch_client(monkeypatch, _figure_turn(_MERMAID_FIGURE))
    client.post("/api/chat", json={"message": "Draw."})

    assert client.delete("/api/figure/nope").status_code == 404

    # Guarded while a turn owns the store.
    sessions.get_session().turn_active = True
    try:
        assert client.delete("/api/figure/fig-1").status_code == 409
    finally:
        sessions.get_session().turn_active = False

    ok = client.delete("/api/figure/fig-1")
    assert ok.status_code == 200
    assert ok.json()["figures"] == []
    assert client.get("/api/figures").json()["figures"] == []


def test_figures_survive_project_save_and_load(monkeypatch):
    client = _client()
    _patch_client(monkeypatch, _figure_turn(_MERMAID_FIGURE))
    client.post("/api/chat", json={"message": "Draw the riser."})

    project = client.get("/api/project/save").json()
    assert project["figures"]["figures"][0]["fid"] == "fig-1"

    sessions.reset_session()
    assert client.get("/api/figures").json()["figures"] == []

    loaded = client.post("/api/project/load", json=project)
    assert loaded.status_code == 200
    restored = client.get("/api/figures").json()["figures"]
    assert restored[0]["fid"] == "fig-1"
    assert restored[0]["source"] == _MERMAID


def test_empty_figure_store_is_omitted_from_the_project_file():
    client = _client()
    project = client.get("/api/project/save").json()
    assert "figures" not in project  # no key when there is nothing to save
