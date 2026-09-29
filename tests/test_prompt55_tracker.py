"""The 5.5 prompting upgrade tracker says where the program stands, and cannot say it wrong.

``docs/plans/prompt55/PROMPT55_TRACKER.md`` is the only record of the "5.5
prompting upgrade" program's progress, and every session edits it by hand: it
ticks its checklist, marks its row done as its pull request's last change,
fills in the previous row's merge commit, and names the next session. A
malformed edit would hand the next session a wrong picture — a session
skipped, a checklist that no longer matches the plan, a "complete" program
with work left, or a handoff that no longer says how many sessions remain. So
its shape is checked on every pull request, and every failure message says
what to fix.

The session list is read from the plan's ``## P55-<n> — <title>`` headings,
because a session may be split (``P55-7b``, per the tracker's "Splitting a
session"); the eight original sessions must always be there, in order, and
the closeout must stay on the last one.

The checks read only the markdown. Fenced code blocks are skipped (the
completion banner's lines begin with ``#``), and a marker counts only on a
line of its own, so prose can name one inline without tripping them. Adapted
from ``tests/test_tier1_finish_tracker.py`` (copied, not imported: each
program's tracker keeps its own rules).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FOLDER = REPO_ROOT / "docs" / "plans" / "prompt55"
TRACKER = FOLDER / "PROMPT55_TRACKER.md"
PLAN = FOLDER / "PROMPT55_PLAN.md"
PLANS_INDEX = REPO_ROOT / "docs" / "plans" / "README.md"

BASE_SESSIONS = tuple(f"P55-{n}" for n in range(1, 9))
STATUSES = ("not started", "done", "blocked")
EMPTY_CELLS = {"", "—", "-"}

SESSION_ID = r"P55-\d+[a-z]?"
TITLE = "# The 5.5 prompting upgrade — tracker"
STATUS_LINE = re.compile(r"<!-- PROMPT55-STATUS: (IN PROGRESS|COMPLETE) -->")
BANNER_OPEN = "<!-- PROMPT55-BANNER -->"
BANNER_CLOSE = "<!-- /PROMPT55-BANNER -->"
COMPLETE_LINE = "**ALL WORK IN THE 5.5 PROMPTING UPGRADE IS COMPLETE.**"
SESSIONS_LEFT_LINE = "About N sessions left, counting the one this prompt starts."
NEXT_LINE = re.compile(r"^\*\*Next session:\*\* (.+?)\s*$")
TRACKER_SESSION_HEADING = re.compile(rf"^### ({SESSION_ID}) — (.+?)\s*$")
PLAN_SESSION_HEADING = re.compile(rf"^## ({SESSION_ID}) — (.+?)\s*$")
CHECK_ITEM = re.compile(rf"^- \[( |x|X)\] ({SESSION_ID}\.\d+)\b(.*)$")
PLAN_ITEM = re.compile(rf"^- \*\*({SESSION_ID}\.\d+)\*\*")
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


def _section(lines: list[str], heading: str, *, where: str = "the tracker") -> list[str]:
    """The lines under ``heading``, up to the next heading of its level or higher."""
    level = len(heading) - len(heading.lstrip("#"))
    matches = [i for i, line in enumerate(lines) if line.strip() == heading]
    assert len(matches) == 1, (
        f"{where} must have exactly one {heading!r} heading; found {len(matches)}"
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


def _plan_sessions() -> list[tuple[str, str]]:
    """Every session the plan specifies, as (ID, title), in plan order."""
    return [
        (match.group(1), match.group(2))
        for line in _outside_fences(_read(PLAN))
        if (match := PLAN_SESSION_HEADING.match(line))
    ]


def _sessions() -> list[str]:
    return [session for session, _title in _plan_sessions()]


def _plan_items() -> list[str]:
    return [
        match.group(1)
        for line in _outside_fences(_read(PLAN))
        if (match := PLAN_ITEM.match(line))
    ]


def _status_rows() -> list[dict[str, str]]:
    table = [
        line
        for line in _section(_outside_fences(_read(TRACKER)), "## Status")
        if line.startswith("|")
    ]
    assert len(table) >= 2, "the Status section has no table"
    header, _separator, *rows = table
    assert _cells(header) == ["ID", "Title", "Status", "PR", "Merge commit"], (
        "the Status table's columns must stay: ID | Title | Status | PR | Merge commit"
    )
    parsed = []
    for row in rows:
        cells = _cells(row)
        assert len(cells) == 5, f"a Status row must have five cells: {row!r}"
        parsed.append(
            {
                "id": cells[0].replace("*", "").strip(),
                "title": cells[1].strip(),
                "status": cells[2].replace("*", "").replace("`", "").strip(),
                "pr": cells[3].strip(),
                "merge": cells[4].strip(),
            }
        )
    return parsed


def _checklists() -> tuple[dict[str, list[tuple[str, bool, str]]], dict[str, str]]:
    items: dict[str, list[tuple[str, bool, str]]] = {}
    titles: dict[str, str] = {}
    current: str | None = None
    for line in _section(_outside_fences(_read(TRACKER)), "## Checklists"):
        heading = TRACKER_SESSION_HEADING.match(line)
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


def _session_of(item_id: str) -> str:
    return item_id.rsplit(".", 1)[0]


def _base_of(session_id: str) -> str:
    return re.sub(r"[a-z]$", "", session_id)


def _marker() -> str:
    found = [
        match.group(1)
        for line in _outside_fences(_read(TRACKER))
        if (match := STATUS_LINE.fullmatch(line.strip()))
    ]
    assert len(found) == 1, (
        "the tracker needs exactly one status line of its own: "
        "<!-- PROMPT55-STATUS: IN PROGRESS --> or ... COMPLETE -->"
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
    for path in (TRACKER, PLAN):
        assert path.is_file(), f"{path.relative_to(REPO_ROOT)} is missing"
    for path in (TRACKER, PLAN, Path(__file__)):
        assert "prompt55" in path.name.lower(), (
            f"{path.name} must carry 'prompt55' in its name (the tracker's R13)"
        )


def test_the_plans_index_and_the_plan_point_here() -> None:
    """A session that starts from the plans index or the plan must find this
    tracker, which is the only record of where the program stands."""
    assert "prompt55/PROMPT55_TRACKER.md" in _read(PLANS_INDEX), (
        "docs/plans/README.md no longer links prompt55/PROMPT55_TRACKER.md"
    )
    assert "PROMPT55_TRACKER.md" in _read(PLAN), "the plan no longer names its tracker"


def test_the_tracker_has_one_status_line() -> None:
    assert _marker() in {"IN PROGRESS", "COMPLETE"}


def test_the_plan_keeps_every_original_session_in_order() -> None:
    """A split adds P55-<n>b after P55-<n>; it never drops, renames or
    reorders one of the eight sessions."""
    sessions = _sessions()
    assert len(sessions) == len(set(sessions)), f"the plan repeats a session: {sessions}"
    bases = [session for session in sessions if session == _base_of(session)]
    assert bases == list(BASE_SESSIONS), (
        f"the plan's sessions must include {list(BASE_SESSIONS)}, in that order; "
        f"it has {sessions}"
    )
    previous_base = None
    for session in sessions:
        base = _base_of(session)
        if session != base:
            assert base == previous_base, (
                f"{session} must come directly after {base} (or its earlier split), "
                f"per the tracker's 'Splitting a session'"
            )
        previous_base = base


def test_every_plan_session_has_its_parts() -> None:
    """Each session section carries acceptance items and an As built heading
    the session fills in."""
    lines = _outside_fences(_read(PLAN))
    for session, title in _plan_sessions():
        body = _section(lines, f"## {session} — {title}", where="the plan")
        headings = {line.strip() for line in body if line.startswith("### ")}
        assert "### Acceptance" in headings, f"the plan's {session} lost its Acceptance heading"
        assert "### As built" in headings, f"the plan's {session} lost its As built heading"
        items = [m.group(1) for line in body if (m := PLAN_ITEM.match(line))]
        assert items, f"the plan's {session} has no acceptance items (- **{session}.1** ...)"
        assert all(_session_of(item) == session for item in items), (
            f"the plan's {session} section lists another session's items: {items}"
        )
        numbers = [int(item.rsplit(".", 1)[1]) for item in items]
        assert numbers == list(range(1, len(items) + 1)), (
            f"the plan's {session} items must be numbered 1..{len(items)} in order: {items}"
        )


def test_the_closeout_stays_on_the_last_session() -> None:
    last_session, last_title = _plan_sessions()[-1]
    body = _section(
        _outside_fences(_read(PLAN)), f"## {last_session} — {last_title}", where="the plan"
    )
    text = " ".join(line.strip() for line in body)
    assert "closeout" in text.lower() and "COMPLETE" in text, (
        f"the closeout (the CLAUDE.md closing section and the tracker's COMPLETE state) "
        f"must stay on the last session, {last_session}"
    )


def test_the_status_table_lists_every_session_in_order() -> None:
    rows = _status_rows()
    assert [row["id"] for row in rows] == _sessions(), (
        f"the Status rows must be exactly the plan's sessions, in order: {_sessions()}"
    )
    for row in rows:
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


def _latest_started() -> tuple[int, str] | None:
    """The last session that has begun, and how the tracker shows it.

    A session has begun once its row is done or blocked, or, for the first
    session that is not done, once it has ticked an item. Its reconcile step
    (Session procedure, step 2.3) comes before any of that, so from then on
    every done row before it must carry its merge commit.
    """
    rows = _status_rows()
    items, _titles = _checklists()
    latest: tuple[int, str] | None = None
    for i, row in enumerate(rows):
        if row["status"] in {"done", "blocked"}:
            latest = (i, f"is {row['status']}")
        else:
            if any(ticked for _item, ticked, _rest in items.get(row["id"], [])):
                latest = (i, "has ticked items")
            break
    return latest


def test_finished_rows_name_their_pull_request_and_merge() -> None:
    rows = _status_rows()
    latest = _latest_started()
    for i, row in enumerate(rows):
        if row["status"] in {"done", "blocked"}:
            assert PR_CELL.fullmatch(row["pr"]), (
                f"{row['id']} is {row['status']}, so its PR cell must name the pull "
                f"request (PR #123), not {row['pr']!r}"
            )
        else:
            assert row["pr"] in EMPTY_CELLS, f"{row['id']} is not started, yet names a PR"
        if row["status"] == "done" and latest is not None and i < latest[0]:
            later = rows[latest[0]]["id"]
            assert MERGE_CELL.fullmatch(row["merge"]), (
                f"{row['id']} is done and {later} {latest[1]}, so {later}'s reconcile "
                f"step (Session procedure, step 2.3) had to fill in {row['id']}'s merge "
                f"commit first, not leave {row['merge']!r}"
            )
        if row["status"] != "done":
            assert row["merge"] in EMPTY_CELLS, f"{row['id']} is not done, yet has a merge commit"


def test_titles_agree_across_the_tracker_and_the_plan() -> None:
    rows = {row["id"]: row["title"] for row in _status_rows()}
    _items, checklist_titles = _checklists()
    spec_titles = dict(_plan_sessions())
    for session in _sessions():
        assert checklist_titles.get(session) == rows.get(session) == spec_titles[session], (
            f"{session}'s title must read the same in the Status row, its checklist "
            f"heading and its plan heading: {rows.get(session)!r}, "
            f"{checklist_titles.get(session)!r}, {spec_titles[session]!r}"
        )


def test_checklists_mirror_the_acceptance_criteria() -> None:
    items, _titles = _checklists()
    sessions = _sessions()
    assert list(items) == sessions, (
        f"the Checklists section must hold one '### <ID> — <title>' list per session, "
        f"in order: {sessions}"
    )
    plan_items = _plan_items()
    for session in sessions:
        tracker_ids = [item_id for item_id, _ticked, _rest in items[session]]
        assert len(tracker_ids) == len(set(tracker_ids)), f"{session}'s checklist repeats an item"
        for item_id in tracker_ids:
            assert _session_of(item_id) == session, f"{item_id} sits under {session}'s checklist"
        spec_ids = [item_id for item_id in plan_items if _session_of(item_id) == session]
        assert spec_ids, f"{session}'s plan section has no acceptance items"
        assert tracker_ids == spec_ids, (
            f"{session}'s checklist must list its acceptance items, in order: "
            f"{spec_ids}, not {tracker_ids}"
        )


def test_ticks_match_each_sessions_status() -> None:
    items, _titles = _checklists()
    statuses = {row["id"]: row["status"] for row in _status_rows()}
    sessions = _sessions()
    first_open = next((s for s in sessions if statuses[s] != "done"), None)
    for session in sessions:
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


def test_the_handoff_says_how_many_sessions_are_left() -> None:
    """Abraham asked for it: after each merge, the next prompt, then — outside
    the prompt, as the very next sentence — about how many sessions remain."""
    handoff = " ".join(
        line.strip()
        for line in _section(
            _outside_fences(_read(TRACKER)), "## After the pull request merges: the handoff"
        )
    )
    assert SESSIONS_LEFT_LINE in handoff, (
        f"the handoff section must give the sessions-left sentence verbatim: "
        f"{SESSIONS_LEFT_LINE!r}"
    )
    for phrase in ("Immediately after the fenced block", "outside it", "not `done`"):
        assert phrase in handoff, (
            f"the handoff section must keep saying where the sentence goes and how N "
            f"is counted (missing {phrase!r})"
        )


def test_the_handoff_prompt_asks_for_the_count_and_the_big_letters() -> None:
    lines = _read(TRACKER).splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.strip() == "## The handoff prompt"), None
    )
    assert start is not None, "the tracker lost its 'The handoff prompt' section"
    opening = next(i for i in range(start, len(lines)) if lines[i].strip() == "```text")
    closing = next(i for i in range(opening + 1, len(lines)) if lines[i].strip() == "```")
    prompt = " ".join(lines[opening + 1 : closing])
    for phrase in (
        "docs/plans/prompt55/PROMPT55_TRACKER.md",
        "docs/plans/prompt55/PROMPT55_PLAN.md",
        "exactly ONE session",
        "about how many sessions are left",
        "big letters",
        "I expect this session to be: <ID> — <title>.",
    ):
        assert phrase in prompt, f"the handoff prompt template lost {phrase!r}"
    first = [
        line.strip().strip("`")
        for line in _section(_outside_fences(_read(TRACKER)), "## The handoff prompt")
        if re.fullmatch(rf"`{SESSION_ID} — .+`", line.strip())
    ]
    first_session, first_title = _plan_sessions()[0]
    assert first == [f"{first_session} — {first_title}"], (
        f"the handoff prompt section must name the first session as "
        f"`{first_session} — {first_title}`"
    )


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
        "a COMPLETE tracker carries one banner block and one completion line, at the "
        "top (the next test checks where)"
    )


def test_the_top_of_the_tracker_reads_in_a_fixed_order() -> None:
    """The first lines say where the program stands, so their order is fixed:
    the title, the status line, then (COMPLETE only) the banner block and the
    completion line, then the Next-session line. Blank lines may separate
    them and nothing else may; inside the banner's code block, not even a
    blank line may."""
    marker = _marker()
    # (what the line must be, what to call it, whether blank lines may precede it)
    expected: list[tuple[str | re.Pattern[str], str, bool]] = [
        (TITLE, "the title", True),
        (f"<!-- PROMPT55-STATUS: {marker} -->", "the status line", True),
    ]
    if marker == "COMPLETE":
        expected += [
            (BANNER_OPEN, "the banner's opening comment", True),
            ("```text", "the fence opening the banner", True),
            *[
                (line, f"line {n} of the banner", False)
                for n, line in enumerate(_canonical_banner(), start=1)
            ],
            ("```", "the fence closing the banner", False),
            (BANNER_CLOSE, "the banner's closing comment", True),
            (COMPLETE_LINE, "the completion line", True),
        ]
    expected.append((NEXT_LINE, "the Next-session line", True))
    order = (
        "the title, the status line, "
        + ("the banner block, the completion line, " if marker == "COMPLETE" else "")
        + "the Next-session line"
    )
    raw = _read(TRACKER).splitlines()
    i = 0
    for want, label, blank_ok in expected:
        while blank_ok and i < len(raw) and not raw[i].strip():
            i += 1
        assert i < len(raw), f"the tracker ends before {label}; its top must read: {order}"
        line = raw[i].rstrip()
        found = want.match(line) is not None if isinstance(want, re.Pattern) else line == want
        assert found, (
            f"line {i + 1} reads {line!r} where {label} belongs: the tracker's top must "
            f"read {order}, in that order, with only blank lines between them (and none "
            "inside the banner's code block)"
        )
        i += 1


def test_the_completion_banner_is_big_and_plain() -> None:
    """Big letters (the owner asked for them, emphatically), in characters
    every Windows console and font renders."""
    banner = _canonical_banner()
    assert len(banner) >= 5, "the completion banner lost its height"
    assert max(len(line) for line in banner) >= 40, "the completion banner lost its width"
    assert all(set(line) <= {"#", " "} for line in banner), (
        "the completion banner uses only '#' and spaces"
    )


def test_the_completion_message_heading_matches_the_completion_line() -> None:
    done_section = " ".join(
        line.strip()
        for line in _section(
            _outside_fences(_read(TRACKER)), "## When every session is done"
        )
    )
    heading = "# ALL WORK IN THE 5.5 PROMPTING UPGRADE IS COMPLETE"
    assert f"`{heading}`" in done_section, (
        f"the completion message must open (after the banner) with {heading!r}"
    )
    assert COMPLETE_LINE.strip("*").rstrip(".") == heading.lstrip("# "), (
        "the completion line and the completion message's heading must say the same thing"
    )
    assert f"`{COMPLETE_LINE}`" in done_section, (
        "the COMPLETE-state instructions must give the completion line verbatim"
    )
