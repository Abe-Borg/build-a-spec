"""The durable record of applied Final QC fixes (Redline on your original,
Phase 3).

An applied fix used to leave only a disposition event on its finding, and the
next successful QC run replaces the retained result — so after "apply, then
re-run", nothing could say which change a QC fix made or why. The log is
written wherever ``mark_applied`` is (the panel route and the chat commit),
for exactly the same finding ids, keeps the display facts and the
fix-survival evidence, rides the project file, and is never carried in a
project brief nor fed to Final QC.
"""
from __future__ import annotations

import json

from backend import sessions
from backend.project_brief import build_project_brief
from backend.qc.apply import matches_current_inputs
from backend.qc.engine import QCSourceRecord
from backend.qc.fix_log import (
    MAX_FIX_LOG_ENTRIES,
    covered_uids,
    entry_covers,
    sanitize_fix_log,
)
from backend.spec_doc.model import DocumentStore, SpecSection, apply_edits
from tests.fakes import FakeClient, text_turn, tool_turn
from tests.test_qc_chat_apply import (
    _FIXED_TEXT,
    _client,
    _finding,
    _parse_sse,
    _patch_client,
    _safe_fix_finding,
    _seed_and_install,
)

_SOURCE_URL = "https://www.nfpa.org/codes-and-standards/nfpa-13"


def _sourced_fix(finding_id: str = "qc-safe000fix1"):
    finding = _safe_fix_finding(finding_id)
    finding.accepted_sources = [_SOURCE_URL]
    finding.source_urls = [_SOURCE_URL, "https://example.com/cited-only"]
    finding.source_checks = [
        QCSourceRecord(url=_SOURCE_URL, title="NFPA 13", accepted=True),
        QCSourceRecord(
            url="https://example.com/cited-only", title="Cited", accepted=False
        ),
    ]
    return finding


def _stale_finding():
    return _finding(
        "qc-stale00001",
        [
            {
                "action": "replace",
                "target_id": "pt1.a1.p9",  # names nothing: stale in the batch
                "text": "Never applied.",
                "status": "confirmed",
            }
        ],
    )


def test_the_panel_apply_logs_each_applied_finding_and_nothing_else():
    client = _client()
    _seed_and_install(client, [_sourced_fix(), _stale_finding()])
    response = client.post(
        "/api/qc/apply", json={"finding_ids": ["qc-safe000fix1", "qc-stale00001"]}
    )
    assert response.status_code == 200, response.text
    assert response.json()["outcomes"] == {
        "qc-safe000fix1": "applied",
        "qc-stale00001": "stale",
    }
    log = sessions.get_session().qc_fix_log
    assert [entry["finding_id"] for entry in log] == ["qc-safe000fix1"]
    entry = log[0]
    assert entry["title"] == "Finding qc-safe000fix1"
    assert entry["severity"] == "high"
    assert entry["lens_id"] == "coordination_consistency"
    assert entry["lens_title"]  # the lens's own title, not its id
    assert entry["lens_title"] != entry["lens_id"]
    assert entry["issue"] == "Chat-apply test issue."
    # Accepted sources only — never merely cited — with their titles.
    assert entry["sources"] == [{"url": _SOURCE_URL, "title": "NFPA 13"}]
    assert entry["applied_at"] and entry["run_id"]
    assert covered_uids(entry) == ["pt1.a1.p1"]
    assert entry["evidence"] == [
        {
            "key": ["field", "pt1.a1.p1"],
            "value": {
                "kind": "p",
                "text": _FIXED_TEXT,
                "status": "confirmed",
                "source_item_id": "",
            },
            "content": True,
        }
    ]


def test_the_chat_commit_logs_only_the_fixes_that_survived_it(monkeypatch):
    client = _client()
    _seed_and_install(client, [_sourced_fix()])
    fake = FakeClient(
        [
            tool_turn(
                ["Applying… "],
                {"finding_ids": ["qc-safe000fix1"]},
                tool_id="toolu_qc1",
                name="apply_qc_fixes",
            ),
            text_turn(["Applied."]),
        ]
    )
    _patch_client(monkeypatch, fake)
    events = _parse_sse(client.post("/api/chat", json={"message": "yes"}).text)
    assert events[-1]["type"] == "turn_complete"
    log = sessions.get_session().qc_fix_log
    assert [entry["finding_id"] for entry in log] == ["qc-safe000fix1"]
    assert log[0]["sources"] == [{"url": _SOURCE_URL, "title": "NFPA 13"}]
    assert log[0]["evidence"][0]["value"]["text"] == _FIXED_TEXT


def test_a_fix_voided_later_in_its_own_turn_is_never_logged(monkeypatch):
    client = _client()
    _seed_and_install(client, [_sourced_fix()])
    fake = FakeClient(
        [
            tool_turn(
                ["Applying… "],
                {"finding_ids": ["qc-safe000fix1"]},
                tool_id="toolu_qc1",
                name="apply_qc_fixes",
            ),
            tool_turn(
                ["And your rewording… "],
                {
                    "edits": [
                        {
                            "action": "replace",
                            "target_id": "pt1.a1.p1",
                            "text": "A completely different provision.",
                            "status": "confirmed",
                        }
                    ]
                },
                tool_id="toolu_ed1",
            ),
            text_turn(["Done."]),
        ]
    )
    _patch_client(monkeypatch, fake)
    events = _parse_sse(client.post("/api/chat", json={"message": "go"}).text)
    assert events[-1]["type"] == "turn_complete"
    assert sessions.get_session().qc_fix_log == []


def test_of_two_fixes_applied_in_a_turn_only_the_survivor_is_logged(monkeypatch):
    """Two fixes applied, then the turn rewords the provision one of them
    fixed: the commit voids that one (it stays open) and logs exactly the
    other — the log follows ``mark_applied``'s ids, never the staged ones."""
    client = _client()
    edition_fix = _finding(
        "qc-edition0fix",
        [
            {
                "action": "set_standard_edition",
                "target_id": "sec",
                "standard": "NFPA 13",
                "edition": "2022",
                "basis": "AHJ adoption letter",
            }
        ],
    )
    _seed_and_install(client, [_sourced_fix(), edition_fix])
    fake = FakeClient(
        [
            tool_turn(
                ["Applying… "],
                {"finding_ids": ["qc-safe000fix1", "qc-edition0fix"]},
                tool_id="toolu_qc1",
                name="apply_qc_fixes",
            ),
            tool_turn(
                ["And your rewording… "],
                {
                    "edits": [
                        {
                            "action": "replace",
                            "target_id": "pt1.a1.p1",
                            "text": "A completely different provision.",
                            "status": "confirmed",
                        }
                    ]
                },
                tool_id="toolu_ed1",
            ),
            text_turn(["Done."]),
        ]
    )
    _patch_client(monkeypatch, fake)
    events = _parse_sse(client.post("/api/chat", json={"message": "go"}).text)
    assert events[-1]["type"] == "turn_complete"
    log = sessions.get_session().qc_fix_log
    assert [entry["finding_id"] for entry in log] == ["qc-edition0fix"]
    assert covered_uids(log[0]) == []  # a standards fix names no element


def test_a_rolled_back_turn_logs_nothing(monkeypatch):
    client = _client()
    _seed_and_install(client, [_sourced_fix()])
    fake = FakeClient(
        [
            tool_turn(
                ["Applying… "],
                {"finding_ids": ["qc-safe000fix1"]},
                tool_id="toolu_qc1",
                name="apply_qc_fixes",
            ),
            RuntimeError("boom"),
        ]
    )
    _patch_client(monkeypatch, fake)
    events = _parse_sse(client.post("/api/chat", json={"message": "yes"}).text)
    assert any(e["type"] == "error" for e in events)
    assert sessions.get_session().qc_fix_log == []


def test_the_log_survives_a_save_and_reload_and_a_rerun_of_qc():
    client = _client()
    _seed_and_install(client, [_sourced_fix()])
    assert client.post(
        "/api/qc/apply", json={"finding_ids": ["qc-safe000fix1"]}
    ).json()["ok"]
    session = sessions.get_session()
    logged = json.loads(json.dumps(session.qc_fix_log))
    # A re-run replaces the retained result; the log does not care.
    session.qc.result = None
    saved = client.get("/api/project/save")
    assert saved.status_code == 200
    assert client.post("/api/session/reset").json()["ok"]
    assert sessions.get_session().qc_fix_log == []
    loaded = client.post(
        "/api/project/load-file",
        files={"file": ("p.baspec", saved.content, "application/octet-stream")},
    )
    assert loaded.status_code == 200, loaded.text
    assert sessions.get_session().qc_fix_log == logged


def test_a_project_without_the_key_clears_the_outgoing_log():
    client = _client()
    _seed_and_install(client, [_sourced_fix()])
    empty = client.get("/api/project/save").content  # no fix applied: no key
    assert client.post(
        "/api/qc/apply", json={"finding_ids": ["qc-safe000fix1"]}
    ).json()["ok"]
    assert sessions.get_session().qc_fix_log
    client.post(
        "/api/project/load-file",
        files={"file": ("p.baspec", empty, "application/octet-stream")},
    )
    assert sessions.get_session().qc_fix_log == []


def test_the_reader_is_lenient_and_bounded():
    good = {
        "finding_id": "qc-a",
        "title": "A",
        "sources": [{"url": " https://a.example/ ", "title": "A"}, {"url": ""}, 7],
        "evidence": [
            {"key": ["field", "pt1.a1.p1"], "value": ["tuple", "as", "list"]},
            {"key": "not-a-pair"},
            "junk",
        ],
    }
    loaded = sanitize_fix_log([good, {"title": "no id"}, "junk", None])
    assert [entry["finding_id"] for entry in loaded] == ["qc-a"]
    assert loaded[0]["sources"] == [{"url": "https://a.example/", "title": "A"}]
    assert loaded[0]["evidence"] == [
        # An entry that does not say reads as content (the conservative way
        # for a record written before the flag existed).
        {"key": ["field", "pt1.a1.p1"], "value": ["tuple", "as", "list"], "content": True}
    ]
    flagged = sanitize_fix_log(
        [
            {
                "finding_id": "qc-b",
                "evidence": [{"key": ["field", "x"], "value": None, "content": False}],
            }
        ]
    )
    assert flagged[0]["evidence"][0]["content"] is False
    assert covered_uids(flagged[0]) == []
    assert sanitize_fix_log({"not": "a list"}) == []
    assert MAX_FIX_LOG_ENTRIES == 500  # a runaway guard, pinned
    many = [{"finding_id": f"qc-{i}"} for i in range(MAX_FIX_LOG_ENTRIES + 5)]
    kept = sanitize_fix_log(many)
    assert len(kept) == MAX_FIX_LOG_ENTRIES
    assert kept[0]["finding_id"] == "qc-5"  # the oldest went


def test_the_log_is_not_in_the_brief_and_not_a_qc_input():
    client = _client()
    result = _seed_and_install(client, [_sourced_fix()])
    session = sessions.get_session()
    assert matches_current_inputs(session, result, block=True)
    session.qc_fix_log = sanitize_fix_log(
        [{"finding_id": "qc-brief-probe", "title": "Probe", "issue": "x"}]
    )
    # Not a review input: the retained result is exactly as current as it
    # was, so a log entry can never flip a report stale.
    assert matches_current_inputs(session, result, block=True)
    with session.session_state_guard():
        brief = build_project_brief(session, ready=True).to_dict()
    assert "qc_fix_log" not in brief
    assert "qc-brief-probe" not in json.dumps(brief)


def _section(ops) -> SpecSection:
    section, _ = apply_edits(DocumentStore().doc, ops)
    return section


def test_an_entry_covers_an_element_only_while_it_reads_as_the_fix_left_it():
    base = _section(
        [
            {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
            {"action": "add_paragraph", "target_id": "pt1.a1", "text": "One."},
            {"action": "add_paragraph", "target_id": "pt1.a1", "text": "Two."},
        ]
    )
    entry = sanitize_fix_log(
        [
            {
                "finding_id": "qc-x",
                "evidence": [
                    {
                        "key": ["field", "pt1.a1.p1"],
                        "value": {
                            "kind": "p",
                            "text": "One.",
                            "status": "assumed",
                            "source_item_id": "",
                        },
                    }
                ],
            }
        ]
    )[0]
    assert entry_covers(entry, base, "pt1.a1.p1")
    # Confirming it (status) or linking a source does not erase the claim.
    confirmed, _ = apply_edits(
        base,
        [
            {"action": "set_status", "target_id": "pt1.a1.p1", "status": "confirmed"},
            {
                "action": "replace",
                "target_id": "pt1.a1.p1",
                "text": "One.",
                "source_item_id": "r-abc",
            },
        ],
    )
    assert entry_covers(entry, confirmed, "pt1.a1.p1")
    # A later edit of the text does.
    edited, _ = apply_edits(
        base, [{"action": "replace", "target_id": "pt1.a1.p1", "text": "Uno."}]
    )
    assert not entry_covers(entry, edited, "pt1.a1.p1")
    # A uid the entry never wrote is never covered.
    assert not entry_covers(entry, base, "pt1.a1.p2")

    # A deletion covers only while the element stays absent.
    deleted = sanitize_fix_log(
        [
            {
                "finding_id": "qc-del",
                "evidence": [{"key": ["field", "pt1.a1.p2"], "value": None}],
            }
        ]
    )[0]
    gone, _ = apply_edits(base, [{"action": "delete", "target_id": "pt1.a1.p2"}])
    assert entry_covers(deleted, gone, "pt1.a1.p2")
    assert not entry_covers(deleted, base, "pt1.a1.p2")

    # A move covers only at exactly the position the fix put it.
    moved_to_front, _ = apply_edits(
        base, [{"action": "move", "target_id": "pt1.a1.p2", "position": 0}]
    )
    move = sanitize_fix_log(
        [
            {
                "finding_id": "qc-move",
                "evidence": [
                    {
                        "key": ["field", "pt1.a1.p2"],
                        "value": {
                            "kind": "p",
                            "text": "Two.",
                            "status": "assumed",
                            "source_item_id": "",
                        },
                    },
                    {"key": ["pos", "pt1.a1.p2"], "value": ["pt1.a1", 0]},
                ],
            }
        ]
    )[0]
    assert entry_covers(move, moved_to_front, "pt1.a1.p2")
    assert not entry_covers(move, base, "pt1.a1.p2")  # back where it was

    # A metadata-only fix names no body element at all.
    standards = sanitize_fix_log(
        [
            {
                "finding_id": "qc-std",
                "evidence": [{"key": ["std", "NFPA 13"], "value": {"edition": "2025"}}],
            }
        ]
    )[0]
    assert covered_uids(standards) == []


def test_a_status_or_source_only_fix_never_claims_the_element():
    """Codex, PR #211: a QC fix that only confirms a provision (``set_status``)
    or re-points its source writes nothing a reader sees. Its evidence still
    guards survival, but the entry must not cover the element — else a
    redline would credit an earlier, unrelated edit to that fix. (A fix that
    rewrites the words still claims them: the panel test above.)"""
    fixes = (
        _finding(
            "qc-status0fix",
            [{"action": "set_status", "target_id": "pt1.a1.p1", "status": "confirmed"}],
        ),
        _finding(
            "qc-source0fix",
            [
                {
                    "action": "replace",
                    "target_id": "pt1.a1.p1",
                    "source_item_id": "r-1",
                    "status": "confirmed",
                }
            ],
        ),
    )
    for fix in fixes:
        client = _client()
        _seed_and_install(client, [fix])
        response = client.post("/api/qc/apply", json={"finding_ids": [fix.finding_id]})
        assert response.status_code == 200, response.text
        assert response.json()["outcomes"] == {fix.finding_id: "applied"}
        entry = sessions.get_session().qc_fix_log[-1]
        assert entry["finding_id"] == fix.finding_id
        assert [item["content"] for item in entry["evidence"]] == [False]
        assert covered_uids(entry) == []
        assert not entry_covers(entry, sessions.get_session().doc.doc, "pt1.a1.p1")
