#!/usr/bin/env python3
"""Measure what a saved project's chat history is made of.

This is the measurement half of the chat-history compaction plan
(``docs/plans/CHAT_HISTORY_COMPACTION_2026-09-22.md``). The plan's numbers
came from a synthetic section; this script replaces them with real ones.
For each project file it reports the saved conversation by category —
the history every chat turn re-sends — and how much of it this build's
saved-history trims remove: stale document outlines (Phase 1) and, while
``BUILD_A_SPEC_ELIDE_FETCHED_PAGES`` is on, the text of fetched web pages
(Phase 2 — off by default until its live canary passes). Each runs at
commit and again when an older file is opened. It also shows how much web
research the history still keeps once they have run.

READ-ONLY. It never imports the client factory, never builds a session,
never makes a model request, and never prints conversation text, tool
inputs, provision wording, URLs or filenames into the pasteable output.
Category names, counts and character totals are all that travel;
artifacts are identified by a SHA-256 prefix, and the local console line
names the file so you can tell your own inputs apart.

Usage (Windows):

    .venv\\Scripts\\python tools\\chat_history_profile.py ^
        "C:\\path\\some-project.baspec"

    .venv\\Scripts\\python tools\\chat_history_profile.py ^
        "C:\\specs\\*.baspec" --out history-measurement.md
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import os
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend import settings  # noqa: E402
from backend.llm.history_hygiene import (  # noqa: E402
    CHARS_PER_TOKEN,
    FETCHED_PAGE_CATEGORY,
    OUTLINE_CATEGORY,
    elide_fetched_page_text,
    elide_stale_outlines,
    history_composition,
)
from backend.spec_doc.project_package import parse_project_file  # noqa: E402

# Web research the history still keeps once the trims have run: search
# results (encrypted, so they can only ever leave inside a condensed span —
# Phase 3) and what is left of each fetched page (its URL, title and note
# while the page-text trim is on; the whole page while it is off).
_WEB_CATEGORIES = ("fetched web pages", "web search results")


@dataclass
class HistoryProfile:
    artifact: str = ""
    turns: int = 0
    saved: dict[str, Any] = field(default_factory=dict)
    trimmed: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    @property
    def removed_chars(self) -> int:
        return max(0, self.saved.get("chars", 0) - self.trimmed.get("chars", 0))


def _user_turns(history: list[Any]) -> int:
    """User turns you typed: user messages carrying text, not tool results."""
    count = 0
    for message in history:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            count += 1
        elif isinstance(content, list) and any(
            isinstance(block, dict) and block.get("type") == "text"
            for block in content
        ):
            count += 1
    return count


def _profile(path: Path) -> HistoryProfile:
    data = path.read_bytes()
    profile = HistoryProfile(artifact=hashlib.sha256(data).hexdigest()[:12])
    try:
        parsed = parse_project_file(data)
    except Exception as exc:  # noqa: BLE001 - a tool, not a service
        profile.note = f"could not be parsed ({exc.__class__.__name__})"
        return profile
    history = parsed.project.get("history")
    if not isinstance(history, list) or not history:
        profile.note = "carries no conversation history"
        return profile
    profile.turns = _user_turns(history)
    profile.saved = history_composition(history)
    # The same trims, in the same order, that opening the file applies; the
    # page-text trim only while its switch is on, exactly as in the app.
    trimmed = elide_stale_outlines(history)
    if settings.ELIDE_FETCHED_PAGE_TEXT:
        trimmed = elide_fetched_page_text(trimmed)
    profile.trimmed = history_composition(trimmed)
    return profile


def _share(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "0.0%"


def _category_chars(composition: dict[str, Any], name: str) -> int:
    for entry in composition.get("categories", []):
        if entry.get("category") == name:
            return int(entry.get("chars", 0))
    return 0


def _markdown(profiles: list[HistoryProfile]) -> str:
    lines: list[str] = []
    lines.append(f"# Chat history profile — measured {date.today().isoformat()}")
    lines.append("")
    lines.append(
        f"Produced by `tools/chat_history_profile.py` over {len(profiles)} "
        "project file(s). Files are identified by a SHA-256 prefix; no "
        "conversation text, tool input, URL or filename appears below."
    )
    lines.append("")
    lines.append(
        "Sizes are the serialized history as saved. The token columns are an "
        f"ESTIMATE at `len / {CHARS_PER_TOKEN}` (the app's rule where it has no "
        "real count); they overstate base64 and encrypted payloads such as "
        "web search results. **As saved** is what an older build re-sent every "
        "turn; **Now** is what this build sends once the file is opened."
    )
    lines.append("")
    lines.append(
        "| File | Turns | As saved (~tokens) | Stale outlines | Fetched pages | "
        "Removed | Now (~tokens) | Web research kept |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for profile in profiles:
        if profile.note:
            continue
        saved = profile.saved
        trimmed = profile.trimmed
        web = sum(_category_chars(trimmed, name) for name in _WEB_CATEGORIES)
        lines.append(
            f"| `{profile.artifact}` | {profile.turns} | "
            f"~{saved.get('estimated_tokens', 0):,} | "
            f"{saved.get('stale_outlines', 0)} | "
            f"{saved.get('fetched_page_texts', 0)} | "
            f"{_share(profile.removed_chars, saved.get('chars', 0))} | "
            f"~{trimmed.get('estimated_tokens', 0):,} | "
            f"{_share(web, trimmed.get('chars', 0))} |"
        )
    lines.append("")

    for profile in profiles:
        if profile.note:
            lines.append(f"- `{profile.artifact}`: {profile.note}")
            continue
        lines.append(f"### `{profile.artifact}`")
        lines.append("")
        lines.append(
            f"- {profile.saved.get('messages', 0)} messages, "
            f"{profile.turns} typed user turn(s)."
        )
        lines.append(
            f"- Stale document outlines: {profile.saved.get('stale_outlines', 0)} "
            f"(~{_category_chars(profile.saved, OUTLINE_CATEGORY) // CHARS_PER_TOKEN:,} "
            "tokens) — removed when the file is opened."
        )
        page_fate = (
            "removed when the file is opened"
            if settings.ELIDE_FETCHED_PAGE_TEXT
            else "kept: the page-text trim is switched off "
            "(BUILD_A_SPEC_ELIDE_FETCHED_PAGES)"
        )
        lines.append(
            "- Fetched web pages still carrying their text: "
            f"{profile.saved.get('fetched_page_texts', 0)} "
            f"(~{_category_chars(profile.saved, FETCHED_PAGE_CATEGORY) // CHARS_PER_TOKEN:,} "
            f"tokens) — {page_fate}."
        )
        lines.append("- By category, as this build sends it:")
        total = profile.trimmed.get("chars", 0)
        for entry in profile.trimmed.get("categories", []):
            lines.append(
                f"  - {entry['category']}: {entry['chars']:,} chars "
                f"(~{entry['estimated_tokens']:,} tokens, "
                f"{_share(entry['chars'], total)}, {entry['blocks']} block(s))"
            )
        lines.append("")

    measured = [p for p in profiles if not p.note]
    lines.append("## Reading it")
    lines.append("")
    if not measured:
        lines.append("Nothing measurable: no file carried a conversation history.")
    else:
        largest = max(measured, key=lambda p: p.saved.get("chars", 0))
        lines.append(
            f"Largest history: ~{largest.saved.get('estimated_tokens', 0):,} "
            f"estimated tokens as saved, ~{largest.trimmed.get('estimated_tokens', 0):,} "
            f"after the trims this build applies ({_share(largest.removed_chars, largest.saved.get('chars', 0))} "
            "removed)."
        )
        lines.append("")
        lines.append(
            "- **Web research kept** is what the trims leave: search "
            "results, whose encrypted content the API needs intact, plus what "
            "is left of each fetched page (its URL, title and a short note "
            "while the page-text trim is on; the whole page while it is off). "
            "Only Phase 3's condensing can remove search results."
        )
        lines.append(
            "- What remains after the trims is conversation — the part Phase 3 "
            "condenses once it passes the trigger the owner set (decision D1 "
            "in the plan)."
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Measure what saved chat histories are made of. Read-only; emits "
            "category names and sizes, never conversation text."
        )
    )
    parser.add_argument(
        "paths", nargs="+", help=".baspec packages or legacy .json project files."
    )
    parser.add_argument(
        "--out", default="", help="Write the markdown block here instead of stdout."
    )
    args = parser.parse_args(argv)

    expanded: list[Path] = []
    for raw in args.paths:
        matches = sorted(glob.glob(os.path.expanduser(raw)))
        if matches:
            expanded.extend(Path(m) for m in matches)
        else:
            expanded.append(Path(os.path.expanduser(raw)))

    profiles: list[HistoryProfile] = []
    for path in expanded:
        if not path.is_file():
            print(f"  skip  {path}  (not a file)", file=sys.stderr)
            continue
        profile = _profile(path)
        # Local orientation only: the filename never reaches the markdown.
        status = profile.note or (
            f"~{profile.saved.get('estimated_tokens', 0):,} tokens as saved"
        )
        print(f"  read  {path.name}  → {profile.artifact}  {status}", file=sys.stderr)
        profiles.append(profile)

    if not profiles:
        print("No readable project files. Nothing measured.", file=sys.stderr)
        return 1

    text = _markdown(profiles)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"\nWrote {args.out}", file=sys.stderr)
    else:
        print(text)

    for forbidden in ("backend.llm.client", "anthropic"):
        if forbidden in sys.modules:
            print(
                f"WARNING: {forbidden} was imported; this script is supposed "
                "to make no model requests. Report this.",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
