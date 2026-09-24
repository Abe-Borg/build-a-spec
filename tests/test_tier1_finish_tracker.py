"""The Tier 1 finish tracker says where the program stands, and cannot say it wrong.

``docs/plans/tier1-finish/TIER1_FINISH_TRACKER.md`` is the only record of the
"Tier 1 finish" program's progress (finishing Tier 1 Chunks 3 and 4), and
every session edits it by hand: it ticks its checklist, marks its row done as
its pull request's last change, fills in the previous row's merge commit, and
names the next session. A malformed edit would hand the next session a wrong
picture — a session skipped, a checklist that no longer matches its plan, a
"complete" program with work left. So its shape is checked on every pull
request, and every failure message says what to fix.

The checks read only the markdown. Fenced code blocks are skipped (the
completion banner's lines begin with ``#``), and a marker counts only on a
line of its own, so prose can name one inline without tripping them.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FOLDER = REPO_ROOT / "docs" / "plans" / "tier1-finish"
TRACKER = FOLDER / "TIER1_FINISH_TRACKER.md"
CHUNK4_PLAN = FOLDER / "TIER1_FINISH_CHUNK4_CONTINUATION_TAIL_PLAN.md"
CHUNK3_PLAN = FOLDER / "TIER1_FINISH_CHUNK3_WARM_LEAD_PLAN.md"

SESSIONS = ("CT-1", "CT-2", "CT-3", "WL-1", "WL-2", "FIN-1")
PLAN_LABEL = {"CT": "Chunk 4", "WL": "Chunk 3", "FIN": "both"}
SPEC_FILE = {"CT": CHUNK4_PLAN, "WL": CHUNK3_PLAN, "FIN": TRACKER}
STATUSES = ("not started", "done", "blocked")
EMPTY_CELLS = {"", "—", "-"}

STATUS_LINE = re.compile(r"<!-- TIER1-FINISH-STATUS: (IN PROGRESS|COMPLETE) -->")
BANNER_OPEN = "<!-- TIER1-FINISH-BANNER -->"
BANNER_CLOSE = "<!-- /TIER1-FINISH-BANNER -->"
COMPLETE_LINE = "**ALL WORK IN BOTH TIER 1 FINISH PLANS IS COMPLETE.**"
NEXT_LINE = re.compile(r"^\*\*Next session:\*\* (.+?)\s*$")
SESSION_HEADING = re.compile(r"^### ((?:CT|WL|FIN)-\d+) — (.+?)\s*$")
CHECK_ITEM = re.compile(r"^- \[( |x|X)\] ((?:CT|WL|FIN)-\d+\.\d+)\b(.*)$")
PR_CELL = re.compile(r"PR #\d+")
MERGE_CELL = re.compile(r"`?[0-9a-f]{7,40}`?")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _outside_fences(text: str) -> list[str]:
    """Every line outside fenced code blocks (the fence lines dropped too).

    A fence opens with three or more backticks or tildes and closes with at
    least as many of the same character and nothing after them — the
    CommonMark rule, so a longer outer fence can hold a shorter inner one.
    """
    lines: list[str] = []
    fence: tuple[str, int] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        match = re.match(r"^(`{3,}|~{3,})(.*)$", stripped)
        if fence is None:
            if match:
                fence = (match.group(1)[0], len(match.group(1)))
                continue
            lines.append(line)
        elif (
            match
            and match.group(1)[0] == fence[0]
            and len(match.group(1)) >= fence[1]
            and not match.group(2).strip()
        ):
            fence = None
    return lines


def _section(lines: list[str], heading: str) -> list[str]:
    """The lines under ``heading``, up to the next heading of its level or higher."""
    level = len(heading) - len(heading.lstrip("#"))
    matches = [i for i, line in enumerate(lines) if line.strip() == heading]
    assert len(matches) == 1, (
        f"the tracker must have exactly one {heading!r} heading; found {len(matches)}"
    )
    out: list[str] = []
    for line in lines[matches[0] + 1 :]:
        found = re.match(r"^(#{1,6}) ", line)
        if found and len(found.group(1)) <= level:
            break
        out.append(line)
    return out


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _status_rows() -> list[dict[str, str]]:
    table = [
        line
        for line in _section(_outside_fences(_read(TRACKER)), "## Status")
        if line.startswith("|")
    ]
    assert len(table) >= 2, "the Status section has no table"
    header, _separator, *rows = table
    assert _cells(header) == ["ID", "Plan", "Title", "Status", "PR", "Merge commit"], (
        "the Status table's columns must stay: ID | Plan | Title | Status | PR | Merge commit"
    )
    parsed = []
    for row in rows:
        cells = _cells(row)
        assert len(cells) == 6, f"a Status row must have six cells: {row!r}"
        parsed.append(
            {
                "id": cells[0].replace("*", "").strip(),
                "plan": cells[1].strip(),
                "title": cells[2].strip(),
                "status": cells[3].replace("*", "").replace("`", "").strip(),
                "pr": cells[4].strip(),
                "merge": cells[5].strip(),
            }
        )
    return parsed


def _checklists() -> tuple[dict[str, list[tuple[str, bool, str]]], dict[str, str]]:
    items: dict[str, list[tuple[str, bool, str]]] = {}
    titles: dict[str, str] = {}
    current: str | None = None
    for line in _section(_outside_fences(_read(TRACKER)), "## Checklists"):
        heading = SESSION_HEADING.match(line)
        if heading:
            current = heading.group(1)
            assert current not in items, f"the Checklists section repeats {current}"
            titles[current] = heading.group(2)
            items[current] = []
            continue
        item = CHECK_ITEM.match(line)
        if item:
            assert current is not None, f"a checklist item sits outside a session: {line!r}"
            items[current].append((item.group(2), item.group(1).lower() == "x", item.group(3)))
    return items, titles


def _spec_items(prefix: str) -> list[str]:
    pattern = re.compile(rf"^- \*\*({prefix}-\d+\.\d+)\*\*")
    return [
        match.group(1)
        for line in _outside_fences(_read(SPEC_FILE[prefix]))
        if (match := pattern.match(line))
    ]


def _spec_titles(prefix: str) -> dict[str, str]:
    pattern = re.compile(rf"^## ({prefix}-\d+) — (.+?)\s*$")
    return {
        match.group(1): match.group(2)
        for line in _outside_fences(_read(SPEC_FILE[prefix]))
        if (match := pattern.match(line))
    }


def _session_of(item_id: str) -> str:
    return item_id.rsplit(".", 1)[0]


def _prefix_of(session_id: str) -> str:
    return session_id.split("-", 1)[0]


def _marker() -> str:
    found = [
        match.group(1)
        for line in _outside_fences(_read(TRACKER))
        if (match := STATUS_LINE.fullmatch(line.strip()))
    ]
    assert len(found) == 1, (
        "the tracker needs exactly one status line of its own: "
        "<!-- TIER1-FINISH-STATUS: IN PROGRESS --> or ... COMPLETE -->"
    )
    return found[0]


def _canonical_banner() -> list[str]:
    lines = _read(TRACKER).splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.strip() == "## When every session is done"),
        None,
    )
    assert start is not None, "the tracker lost its 'When every session is done' section"
    opening = next(i for i in range(start, len(lines)) if lines[i].strip() == "```text")
    closing = next(i for i in range(opening + 1, len(lines)) if lines[i].strip() == "```")
    return [line.rstrip() for line in lines[opening + 1 : closing]]


def test_the_program_files_exist_under_their_own_names() -> None:
    for path in (TRACKER, CHUNK4_PLAN, CHUNK3_PLAN):
        assert path.is_file(), f"{path.relative_to(REPO_ROOT)} is missing"


def test_the_older_records_point_here() -> None:
    """A session that starts from the Tier 1 files or the plans index must
    find this program, not the retired flip procedure."""
    link = "tier1-finish/TIER1_FINISH_TRACKER.md"
    for label in (
        "docs/plans/RESEARCH_QC_COST_TIER1_PROGRESS.md",
        "docs/plans/RESEARCH_QC_COST_TIER1_2026-09-23.md",
        "docs/plans/README.md",
    ):
        assert link in _read(REPO_ROOT / label), f"{label} no longer points at {link}"


def test_the_tracker_has_one_status_line() -> None:
    assert _marker() in {"IN PROGRESS", "COMPLETE"}


def test_the_status_table_lists_every_session_in_order() -> None:
    rows = _status_rows()
    assert [row["id"] for row in rows] == list(SESSIONS), (
        f"the Status rows must be exactly {list(SESSIONS)}, in that order"
    )
    for row in rows:
        assert row["plan"] == PLAN_LABEL[_prefix_of(row["id"])], (
            f"{row['id']}'s Plan cell must read {PLAN_LABEL[_prefix_of(row['id'])]!r}"
        )
        assert row["title"], f"{row['id']} has no title"
        assert row["status"] in STATUSES, (
            f"{row['id']}'s status {row['status']!r} is not one of {STATUSES}"
        )


def test_statuses_change_in_order() -> None:
    statuses = [row["status"] for row in _status_rows()]
    first_open = next(
        (i for i, status in enumerate(statuses) if status != "done"), len(statuses)
    )
    rest = statuses[first_open:]
    if rest and rest[0] == "blocked":
        rest = rest[1:]
    assert all(status == "not started" for status in rest), (
        "every done row comes first, then at most one blocked row, then only "
        f"'not started' rows; the table reads {statuses}"
    )


def test_finished_rows_name_their_pull_request_and_merge() -> None:
    rows = _status_rows()
    done = [i for i, row in enumerate(rows) if row["status"] == "done"]
    for i, row in enumerate(rows):
        if row["status"] in {"done", "blocked"}:
            assert PR_CELL.fullmatch(row["pr"]), (
                f"{row['id']} is {row['status']}, so its PR cell must name the pull "
                f"request (PR #123), not {row['pr']!r}"
            )
        else:
            assert row["pr"] in EMPTY_CELLS, f"{row['id']} is not started, yet names a PR"
        if row["status"] == "done" and done and i != done[-1]:
            assert MERGE_CELL.fullmatch(row["merge"]), (
                f"{row['id']} is done and a later session is done too, so the later "
                f"session had to fill in {row['id']}'s merge commit (reconcile, step 2.3)"
            )
        if row["status"] != "done":
            assert row["merge"] in EMPTY_CELLS, f"{row['id']} is not done, yet has a merge commit"


def test_titles_agree_across_the_tracker_and_the_plans() -> None:
    rows = {row["id"]: row["title"] for row in _status_rows()}
    _items, checklist_titles = _checklists()
    spec_titles: dict[str, str] = {}
    for prefix in PLAN_LABEL:
        spec_titles.update(_spec_titles(prefix))
    for session in SESSIONS:
        assert session in spec_titles, (
            f"{session} has no '## {session} — <title>' section in "
            f"{SPEC_FILE[_prefix_of(session)].name}"
        )
        assert checklist_titles.get(session) == rows[session] == spec_titles[session], (
            f"{session}'s title must read the same in the Status row, its checklist "
            f"heading and its spec heading: {rows[session]!r}, "
            f"{checklist_titles.get(session)!r}, {spec_titles[session]!r}"
        )


def test_checklists_mirror_the_acceptance_criteria() -> None:
    items, _titles = _checklists()
    assert list(items) == list(SESSIONS), (
        f"the Checklists section must hold one '### <ID> — <title>' list per session, "
        f"in order: {list(SESSIONS)}"
    )
    for session in SESSIONS:
        tracker_ids = [item_id for item_id, _ticked, _rest in items[session]]
        assert len(tracker_ids) == len(set(tracker_ids)), f"{session}'s checklist repeats an item"
        for item_id in tracker_ids:
            assert _session_of(item_id) == session, f"{item_id} sits under {session}'s checklist"
        spec_ids = [
            item_id
            for item_id in _spec_items(_prefix_of(session))
            if _session_of(item_id) == session
        ]
        assert spec_ids, f"{session}'s spec has no acceptance items (- **{session}.1** ...)"
        assert tracker_ids == spec_ids, (
            f"{session}'s checklist must list its acceptance items, in order: "
            f"{spec_ids}, not {tracker_ids}"
        )


def test_ticks_match_each_sessions_status() -> None:
    items, _titles = _checklists()
    statuses = {row["id"]: row["status"] for row in _status_rows()}
    first_open = next((s for s in SESSIONS if statuses[s] != "done"), None)
    for session in SESSIONS:
        for item_id, ticked, rest in items[session]:
            if ticked:
                assert re.search(r"evidence:\s*\S", rest), (
                    f"{item_id} is ticked without evidence: append '— evidence: <where it "
                    f"is proven>'"
                )
            if statuses[session] == "done":
                assert ticked, f"{session} is done, but {item_id} is not ticked"
            elif session != first_open:
                assert not ticked, (
                    f"{item_id} is ticked, but only the session in progress "
                    f"({first_open}) may tick before its row is done"
                )


def test_the_next_session_line_names_the_first_unfinished_session() -> None:
    lines = [
        match.group(1)
        for line in _outside_fences(_read(TRACKER))
        if (match := NEXT_LINE.match(line.strip()))
    ]
    assert len(lines) == 1, "the tracker needs exactly one '**Next session:** ...' line"
    rows = _status_rows()
    first_open = next((row for row in rows if row["status"] != "done"), None)
    if first_open is None:
        expected = "none — the program is complete"
    elif first_open["status"] == "blocked":
        expected = f"none — {first_open['id']} is blocked; Abraham decides"
    else:
        expected = f"{first_open['id']} — {first_open['title']}"
    assert lines[0] == expected, f"the Next-session line must read {expected!r}"


def test_the_completion_banner_appears_exactly_when_every_session_is_done() -> None:
    outside = [line.strip() for line in _outside_fences(_read(TRACKER))]
    all_done = all(row["status"] == "done" for row in _status_rows())
    complete = _marker() == "COMPLETE"
    assert complete == all_done, (
        "the status line reads COMPLETE exactly when every row is done "
        f"(COMPLETE={complete}, every row done={all_done})"
    )
    opens = outside.count(BANNER_OPEN)
    closes = outside.count(BANNER_CLOSE)
    completes = outside.count(COMPLETE_LINE)
    if not complete:
        assert (opens, closes, completes) == (0, 0, 0), (
            "the completion banner and the completion line belong only to a "
            "COMPLETE tracker"
        )
        return
    assert (opens, closes, completes) == (1, 1, 1), (
        "a COMPLETE tracker carries one banner block and one completion line at the top"
    )
    raw = _read(TRACKER).splitlines()
    start = next(i for i, line in enumerate(raw) if line.strip() == BANNER_OPEN)
    end = next(i for i, line in enumerate(raw) if line.strip() == BANNER_CLOSE)
    status_heading = next(i for i, line in enumerate(raw) if line.strip() == "## Status")
    assert start < end < status_heading, "the banner block sits above the Status table"
    block = raw[start + 1 : end]
    assert block and block[0].strip() == "```text" and block[-1].strip() == "```", (
        "the banner block holds the banner in one ```text code block"
    )
    assert [line.rstrip() for line in block[1:-1]] == _canonical_banner(), (
        "the banner block must hold the completion banner exactly as the "
        "'When every session is done' section gives it"
    )


def test_the_completion_banner_is_big_and_plain() -> None:
    """Big letters (the owner asked for them), in characters every Windows
    console and font renders."""
    banner = _canonical_banner()
    assert len(banner) >= 5, "the completion banner lost its height"
    assert max(len(line) for line in banner) >= 40, "the completion banner lost its width"
    assert all(set(line) <= {"#", " "} for line in banner), (
        "the completion banner uses only '#' and spaces"
    )
