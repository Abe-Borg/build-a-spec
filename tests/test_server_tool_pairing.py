"""The server-tool pairing invariant, as a pure unit.

A dangling ``server_tool_use`` makes every later request in the session a
400, so these pin both directions: what must be removed, and — more easily
broken — what must survive.
"""
from __future__ import annotations

from types import SimpleNamespace

from backend.llm.server_tool_pairing import (
    count_unpaired_server_tool_uses,
    without_unpaired_server_tool_uses,
)


def _use(use_id: str, name: str = "web_search") -> dict:
    return {
        "type": "server_tool_use",
        "id": use_id,
        "name": name,
        "input": {"query": "q"},
    }


def _result(use_id: str, block_type: str = "web_search_tool_result") -> dict:
    return {"type": block_type, "tool_use_id": use_id, "content": []}


def _assistant(*blocks) -> dict:
    return {"role": "assistant", "content": list(blocks)}


def _types(messages: list) -> list[list[str]]:
    return [[b.get("type") for b in m["content"]] for m in messages]


def test_a_complete_pair_in_one_message_is_untouched():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        _assistant(_use("srvtoolu_1"), _result("srvtoolu_1")),
    ]
    # Same object back: copy-on-write means the overwhelmingly common case
    # allocates nothing.
    assert without_unpaired_server_tool_uses(messages) is messages
    assert count_unpaired_server_tool_uses(messages) == 0


def test_a_pair_split_across_a_pause_turn_boundary_survives():
    """The reason this cannot be a per-message filter.

    After a pause the use sits in the paused assistant message and its
    result arrives in the next one. A message-at-a-time filter sees a
    dangling use and deletes real retrieval evidence.
    """
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        _assistant(_use("srvtoolu_1")),
        _assistant(_result("srvtoolu_1"), {"type": "text", "text": "done"}),
    ]
    assert without_unpaired_server_tool_uses(messages) is messages


def test_an_unpaired_use_is_removed():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        _assistant(
            {"type": "text", "text": "Looking that up."},
            _use("srvtoolu_gone"),
        ),
    ]
    scrubbed = without_unpaired_server_tool_uses(messages)
    assert scrubbed is not messages
    assert _types(scrubbed) == [["text"], ["text"]]
    assert count_unpaired_server_tool_uses(messages) == 1
    # Copy-on-write: the input list and its messages are not mutated.
    assert len(messages[1]["content"]) == 2


def test_only_the_unpaired_use_goes_when_a_sibling_pair_is_complete():
    messages = [
        _assistant(
            _use("srvtoolu_ok"),
            _result("srvtoolu_ok"),
            _use("srvtoolu_dangling"),
        )
    ]
    scrubbed = without_unpaired_server_tool_uses(messages)
    assert [b.get("id") or b.get("tool_use_id") for b in scrubbed[0]["content"]] == [
        "srvtoolu_ok",
        "srvtoolu_ok",
    ]


def test_a_completed_code_execution_pair_is_preserved():
    """A completed code-execution use must never read as dangling.

    Its result family is not one of the web ones, so an enumerate-the-web-
    families implementation would delete a perfectly valid call.
    """
    for family in (
        "bash_code_execution_tool_result",
        "text_editor_code_execution_tool_result",
        # A family this build has never heard of.
        "some_future_code_execution_tool_result",
    ):
        messages = [
            _assistant(
                _use("srvtoolu_exec", name="code_execution"),
                _result("srvtoolu_exec", block_type=family),
            )
        ]
        assert without_unpaired_server_tool_uses(messages) is messages, family


def test_an_orphaned_server_result_is_removed_with_its_lost_use():
    """An unpaired result is as invalid on the wire as an unpaired use."""
    messages = [
        _assistant(
            {"type": "text", "text": "here"},
            _result("srvtoolu_vanished"),
        )
    ]
    scrubbed = without_unpaired_server_tool_uses(messages)
    assert _types(scrubbed) == [["text"]]


def test_client_tool_blocks_are_none_of_this_helpers_business():
    """``tool_result`` (client) is a different pairing, handled elsewhere."""
    messages = [
        _assistant({"type": "tool_use", "id": "toolu_1", "name": "apply_spec_edits"}),
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}
            ],
        },
    ]
    assert without_unpaired_server_tool_uses(messages) is messages
    # And a dangling CLIENT tool_use is left alone too — the truncation path
    # owns that one, and double-handling it here would be a second opinion
    # nobody asked for.
    dangling_client = [_assistant({"type": "tool_use", "id": "toolu_x", "name": "n"})]
    assert without_unpaired_server_tool_uses(dangling_client) is dangling_client


def test_unknown_blocks_and_citations_are_left_alone():
    messages = [
        _assistant(
            {"type": "text", "text": "cited", "citations": [{"url": "https://x"}]},
            {"type": "mystery_block", "payload": "?"},
            # Carries a tool_use_id but is not a recognized result family:
            # unmappable, so kept rather than guessed at.
            {"type": "mystery_reference", "tool_use_id": "srvtoolu_unknown"},
        )
    ]
    assert without_unpaired_server_tool_uses(messages) is messages


def test_an_unmappable_result_without_an_id_is_kept():
    messages = [_assistant({"type": "web_search_tool_result", "content": []})]
    assert without_unpaired_server_tool_uses(messages) is messages


def test_a_pending_pause_turn_call_is_protected_on_the_outgoing_path():
    """The pause contract beats the pairing invariant, and must.

    A ``pause_turn`` pauses *because* a server tool has not finished, so the
    trailing ``server_tool_use`` is legitimately result-less — and it is the
    block the provider looks for to resume. Scrubbing it deletes the resume
    signal, aborting the work the continuation exists to finish.
    """
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "look it up"}]},
        _assistant(
            {"type": "text", "text": "Searching."}, _use("srvtoolu_pending")
        ),
    ]
    assert (
        without_unpaired_server_tool_uses(
            messages, protect_trailing_assistant=True
        )
        is messages
    )
    # Without the exemption it would be stripped — that is the whole point
    # of the flag, so pin the difference rather than just the fixed state.
    assert without_unpaired_server_tool_uses(messages) is not messages


def test_the_exemption_covers_only_the_trailing_message():
    """Poison earlier in the conversation is still caught."""
    messages = [
        _assistant(_use("srvtoolu_old_poison")),
        {"role": "user", "content": [{"type": "text", "text": "next"}]},
        _assistant({"type": "text", "text": "hi"}, _use("srvtoolu_pending")),
    ]
    scrubbed = without_unpaired_server_tool_uses(
        messages, protect_trailing_assistant=True
    )
    assert _types(scrubbed) == [
        ["text"],  # emptied → placeholder
        ["text"],
        ["text", "server_tool_use"],  # the live pause, untouched
    ]


def test_the_exemption_does_nothing_when_the_turn_is_not_a_pause():
    """A trailing USER message means no pause is in flight."""
    messages = [
        _assistant(_use("srvtoolu_dangling")),
        {"role": "user", "content": [{"type": "text", "text": "next"}]},
    ]
    scrubbed = without_unpaired_server_tool_uses(
        messages, protect_trailing_assistant=True
    )
    assert _types(scrubbed) == [["text"], ["text"]]


def test_an_emptied_assistant_message_gets_the_callers_placeholder():
    messages = [_assistant(_use("srvtoolu_gone"))]
    scrubbed = without_unpaired_server_tool_uses(
        messages, placeholder="[Generation stopped by user.]"
    )
    assert scrubbed[0]["content"] == [
        {"type": "text", "text": "[Generation stopped by user.]"}
    ]


def test_sdk_style_objects_are_read_and_their_kept_blocks_are_not_rebuilt():
    """Research and QC re-send ``response.content`` as SDK block objects.

    The pause_turn contract is "verbatim", so surviving blocks must come
    back as the same objects — converting them to dicts here would quietly
    change what the continuation sends.
    """
    keeper = SimpleNamespace(type="text", text="kept")
    pair_use = SimpleNamespace(type="server_tool_use", id="srvtoolu_1", name="web_search")
    pair_result = SimpleNamespace(
        type="web_search_tool_result", tool_use_id="srvtoolu_1", content=[]
    )
    dangling = SimpleNamespace(type="server_tool_use", id="srvtoolu_2", name="web_search")
    messages = [
        {"role": "assistant", "content": [keeper, pair_use, pair_result, dangling]}
    ]
    scrubbed = without_unpaired_server_tool_uses(messages)
    content = scrubbed[0]["content"]
    assert content == [keeper, pair_use, pair_result]
    assert content[0] is keeper and content[1] is pair_use
