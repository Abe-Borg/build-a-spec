"""Stale document outlines stay out of saved chat history (compaction plan,
Phase 1), and the history's makeup is measurable without reading its text.

Every edit result hands the model the whole document outline — its id map
between calls WITHIN a turn. Once the turn commits, that outline is stale
and every later turn's PROJECT CONTEXT carries the full current document
instead, so committing it only makes every later request larger. These
tests pin both halves: the model still gets the outline mid-turn, and the
saved history (and a project saved by an older build, once opened) does
not keep it.
"""
from __future__ import annotations

import copy
import json
import logging

from fastapi.testclient import TestClient

from backend import sessions
from backend.app import create_app
from backend.llm.history_hygiene import (
    OUTLINE_CATEGORY,
    REJECTED_BATCH_DOCUMENT_HEADER,
    STALE_OUTLINE_NOTE,
    count_stale_outlines,
    elide_stale_outlines,
    history_composition,
)
from backend.spec_doc.model import outline
from tests.fakes import FakeClient, request_context_text, text_turn, tool_turn
from tests.test_app import _SEED_EDITS, _parse_sse, _patch_client

# Provision text from _SEED_EDITS. It also rides the edit call's own input,
# so it proves nothing about outlines on its own; see _OUTLINE_PROBE.
_PROVISION = "Section includes wet-pipe systems per NFPA 13-2025."
# Only an outline renders an element id in this form (the edit input says
# "target_id", the applied records say "id": …), so its presence in saved
# history is exactly "an outline survived".
_OUTLINE_PROBE = "[id: pt1.a1.p1]"


def _client() -> TestClient:
    return TestClient(create_app())


def _chat(client: TestClient, message: str) -> list[dict]:
    resp = client.post("/api/chat", json={"message": message})
    events = _parse_sse(resp.text)
    assert events[-1]["type"] == "turn_complete", events[-1]
    return events


def _tool_results(history: list[dict]) -> list[dict]:
    return [
        block
        for message in history
        if message["role"] == "user"
        for block in message["content"]
        if block.get("type") == "tool_result"
    ]


def _seed(client: TestClient, monkeypatch) -> FakeClient:
    fake = FakeClient([tool_turn(["Drafting."], _SEED_EDITS), text_turn(["Done."])])
    _patch_client(monkeypatch, fake)
    _chat(client, "Start 21 13 13")
    return fake


# ---------------------------------------------------------------------------
# Through the chat engine
# ---------------------------------------------------------------------------


def test_a_committed_edit_keeps_what_it_did_and_drops_the_outline(monkeypatch):
    client = _client()
    fake = _seed(client, monkeypatch)

    # Mid-turn, the model got the full outline: its id map between calls.
    continuation = fake.messages.requests[1]["messages"][-1]["content"][0]
    assert continuation["type"] == "tool_result"
    in_turn = json.loads(continuation["content"])
    assert _PROVISION in in_turn["outline"]
    assert _OUTLINE_PROBE in in_turn["outline"]

    # Saved: what the edit DID survives; the snapshot of the document doesn't.
    (saved,) = _tool_results(sessions.get_session().history)
    record = json.loads(saved["content"])
    assert [op["id"] for op in record["applied"]] == [
        "sec",
        "pt1.a1",
        "pt1.a1.p1",
        "pt1.a1.p2",
    ]
    assert record["outline"] == STALE_OUTLINE_NOTE
    assert _PROVISION not in saved["content"]

    # The next turn re-sends the note, never the old outline — and its
    # PROJECT CONTEXT carries the whole current document with every id.
    follow_up = FakeClient([text_turn(["Sure."])])
    _patch_client(monkeypatch, follow_up)
    _chat(client, "What's next?")
    request = follow_up.messages.last_request
    history_part = json.dumps(request["messages"][:-1], ensure_ascii=False)
    assert _OUTLINE_PROBE not in history_part
    assert STALE_OUTLINE_NOTE in history_part
    context = request_context_text(request)
    assert _PROVISION in context and _OUTLINE_PROBE in context


def test_a_rejected_batch_keeps_its_error_and_drops_the_outline(monkeypatch):
    client = _client()
    _seed(client, monkeypatch)

    fake = FakeClient(
        [
            tool_turn(
                [],
                {"edits": [{"action": "delete", "target_id": "zzz"}]},
                tool_id="toolu_bad",
            ),
            text_turn(["Let me fix that."]),
        ]
    )
    _patch_client(monkeypatch, fake)
    _chat(client, "Delete the missing thing")

    # The model needed the document to self-correct, and got it.
    continuation = fake.messages.requests[1]["messages"][-1]["content"][0]
    assert continuation["is_error"] is True
    assert REJECTED_BATCH_DOCUMENT_HEADER in continuation["content"]
    assert _PROVISION in continuation["content"]

    history = sessions.get_session().history
    saved = next(
        block for block in _tool_results(history) if block["tool_use_id"] == "toolu_bad"
    )
    assert saved["is_error"] is True
    assert saved["content"].startswith("Edit batch rejected (nothing was applied): ")
    assert "zzz" in saved["content"]
    assert saved["content"].endswith(STALE_OUTLINE_NOTE)
    assert REJECTED_BATCH_DOCUMENT_HEADER not in saved["content"]
    assert _PROVISION not in saved["content"]


def test_projects_saved_before_the_elision_are_trimmed_when_opened(
    monkeypatch, caplog
):
    client = _client()
    _seed(client, monkeypatch)
    session = sessions.get_session()
    full_outline = outline(session.doc.doc)
    project = json.loads(json.dumps(sessions.project_payload(session)))

    # What an older build saved: the outline still inside the result.
    for message in project["history"]:
        for block in message["content"]:
            if block.get("type") == "tool_result":
                record = json.loads(block["content"])
                record["outline"] = full_outline
                block["content"] = json.dumps(record, ensure_ascii=False)
    assert _OUTLINE_PROBE in json.dumps(project["history"], ensure_ascii=False)

    client.post("/api/session/reset")
    with caplog.at_level(logging.INFO, logger="buildaspec.project"):
        loaded = client.post("/api/project/load", json=project)
    assert loaded.status_code == 200, loaded.text
    assert "Dropped 1 stale document outline(s)" in caplog.text

    history = sessions.get_session().history
    (saved,) = _tool_results(history)
    assert json.loads(saved["content"])["outline"] == STALE_OUTLINE_NOTE
    # Nothing the user sees changed: tool plumbing never reaches the chat.
    assert [m["role"] for m in loaded.json()["chat"]] == ["user", "assistant"]
    assert loaded.json()["chat"][1]["text"] == "Drafting.\n\nDone."
    # The next save writes the trimmed form.
    resaved = json.dumps(
        sessions.project_payload(sessions.get_session())["history"],
        ensure_ascii=False,
    )
    assert _OUTLINE_PROBE not in resaved
    assert STALE_OUTLINE_NOTE in resaved


# ---------------------------------------------------------------------------
# The elision itself
# ---------------------------------------------------------------------------

_LONG_OUTLINE = "SECTION 21 13 13 — X  [id: sec]\n" + "\n".join(
    f"    {i}. (assumed) Provision text number {i} of the section.  [id: pt1.a1.p{i}]"
    for i in range(1, 20)
)


def _history() -> list[dict]:
    """One of every result shape the elision must tell apart."""
    return [
        {"role": "user", "content": [{"type": "text", "text": "Draft it."}]},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Drafting."},
                {"type": "tool_use", "id": "t-edit", "name": "apply_spec_edits",
                 "input": {"edits": []}},
                {"type": "tool_use", "id": "t-qc", "name": "apply_qc_fixes",
                 "input": {"finding_ids": ["qc-1"]}},
                {"type": "tool_use", "id": "t-small", "name": "apply_spec_edits",
                 "input": {"edits": []}},
                {"type": "tool_use", "id": "t-odd", "name": "apply_spec_edits",
                 "input": {"edits": []}},
                {"type": "tool_use", "id": "t-ref", "name": "read_reference_doc",
                 "input": {"reference_id": "ref-1"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t-edit",
                 "content": json.dumps({"applied": [{"action": "replace", "id": "sec"}],
                                        "outline": _LONG_OUTLINE}, ensure_ascii=False)},
                {"type": "tool_result", "tool_use_id": "t-qc",
                 "content": json.dumps({"outcomes": {"qc-1": "applied"},
                                        "applied_operations": 1,
                                        "outline": _LONG_OUTLINE}, ensure_ascii=False)},
                # An empty document's outline is shorter than the note:
                # replacing it would make the history larger.
                {"type": "tool_result", "tool_use_id": "t-small",
                 "content": json.dumps({"applied": [], "outline": "(empty)"})},
                # Not a shape this tool produces: left exactly alone.
                {"type": "tool_result", "tool_use_id": "t-odd",
                 "content": "something unrecognizable"},
                # Not an outline-bearing tool, even with an "outline" key.
                {"type": "tool_result", "tool_use_id": "t-ref",
                 "content": json.dumps({"outline": _LONG_OUTLINE})},
            ],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "Done."}]},
    ]


def test_elision_is_copy_on_write_idempotent_and_scoped():
    history = _history()
    before = copy.deepcopy(history)
    assert count_stale_outlines(history) == 2

    trimmed = elide_stale_outlines(history)
    assert trimmed is not history
    assert history == before, "the input must never be mutated"

    results = {b["tool_use_id"]: b["content"] for b in trimmed[2]["content"]}
    edit = json.loads(results["t-edit"])
    assert edit == {"applied": [{"action": "replace", "id": "sec"}],
                    "outline": STALE_OUTLINE_NOTE}
    qc = json.loads(results["t-qc"])
    assert qc["outcomes"] == {"qc-1": "applied"} and qc["applied_operations"] == 1
    assert qc["outline"] == STALE_OUTLINE_NOTE
    assert results["t-small"] == before[2]["content"][2]["content"]
    assert results["t-odd"] == "something unrecognizable"
    assert results["t-ref"] == before[2]["content"][4]["content"]
    # Untouched messages are the same objects, not copies.
    assert trimmed[0] is history[0] and trimmed[1] is history[1]
    assert trimmed[3] is history[3]

    # A second pass finds nothing — and says so by returning the same list.
    assert count_stale_outlines(trimmed) == 0
    assert elide_stale_outlines(trimmed) is trimmed
    # A history with no outline-bearing tool costs nothing.
    plain = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    assert elide_stale_outlines(plain) is plain


# ---------------------------------------------------------------------------
# Measuring it
# ---------------------------------------------------------------------------


def test_history_composition_partitions_the_history_and_carries_no_text():
    history = _history()
    history.append(
        {
            "role": "assistant",
            "content": [
                {"type": "server_tool_use", "id": "srv-1", "name": "web_fetch",
                 "input": {"url": "https://example.test/secret-page"}},
                {"type": "web_fetch_tool_result", "tool_use_id": "srv-1",
                 "content": {"type": "web_fetch_result",
                             "url": "https://example.test/secret-page",
                             "content": {"type": "document",
                                         "source": {"type": "text", "data": "PAGE " * 50}}}},
                {"type": "text", "text": "Per the page, yes.",
                 "citations": [{"type": "char_location", "cited_text": "PAGE"}]},
            ],
        }
    )
    composition = history_composition(history)
    by_name = {c["category"]: c for c in composition["categories"]}

    assert composition["messages"] == 5
    assert sum(c["chars"] for c in composition["categories"]) == composition["chars"]
    assert composition["estimated_tokens"] == composition["chars"] // 4
    assert composition["stale_outlines"] == 2
    assert by_name[OUTLINE_CATEGORY]["blocks"] == 2
    for expected in (
        "user text",
        "assistant text",
        "citations",
        "tool call: apply_spec_edits",
        "tool result: apply_spec_edits",
        "tool result: apply_qc_fixes",
        "tool result: read_reference_doc",
        "server tool call: web_fetch",
        "fetched web pages",
    ):
        assert expected in by_name, expected
    chars = [c["chars"] for c in composition["categories"]]
    assert chars == sorted(chars, reverse=True)

    # Sizes only: nothing a user wrote, fetched or drafted is in it.
    dumped = json.dumps(composition)
    for private in ("Draft it", "Provision text number", "secret-page", "PAGE PAGE"):
        assert private not in dumped

    # After the elision the outline category is gone and nothing else moved.
    after = history_composition(elide_stale_outlines(history))
    assert OUTLINE_CATEGORY not in {c["category"] for c in after["categories"]}
    assert after["stale_outlines"] == 0
    assert after["chars"] == composition["chars"] - by_name[OUTLINE_CATEGORY]["chars"]


def test_diagnostics_reports_history_makeup_without_text(monkeypatch):
    client = _client()
    _seed(client, monkeypatch)
    data = client.get("/api/diagnostics").json()
    makeup = data["session"]["history_composition"]
    assert makeup["messages"] == 4
    assert makeup["stale_outlines"] == 0
    names = {c["category"] for c in makeup["categories"]}
    assert {"user text", "assistant text", "tool call: apply_spec_edits",
            "tool result: apply_spec_edits"} <= names
    assert _PROVISION not in json.dumps(makeup)
    assert _OUTLINE_PROBE not in json.dumps(makeup)


def test_the_profiler_reports_sizes_not_text(monkeypatch, tmp_path, capsys):
    from tools import chat_history_profile

    client = _client()
    _seed(client, monkeypatch)
    session = sessions.get_session()
    project = json.loads(json.dumps(sessions.project_payload(session)))
    for message in project["history"]:
        for block in message["content"]:
            if block.get("type") == "tool_result":
                record = json.loads(block["content"])
                record["outline"] = outline(session.doc.doc)
                block["content"] = json.dumps(record, ensure_ascii=False)
    path = tmp_path / "client-name-project.json"
    path.write_text(json.dumps(project), encoding="utf-8")
    out = tmp_path / "measurement.md"

    assert chat_history_profile.main([str(path), "--out", str(out)]) == 0
    report = out.read_text(encoding="utf-8")
    assert "| Turns | As saved" in report
    assert "Stale document outlines: 1" in report
    assert "tool result: apply_spec_edits" in report
    # Named by hash only; no filename, provision text or chat text.
    assert "client-name" not in report
    assert _PROVISION not in report
    assert _OUTLINE_PROBE not in report
    assert "Start 21 13 13" not in report
