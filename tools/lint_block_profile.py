#!/usr/bin/env python3
"""Measure the LINT REPORT block a real document puts in every chat turn.

This is step 4 of the revision-2 review plan (retired; its acceptance
criteria live in ``docs/review-results/2026-09-09/EXECUTION_RECORD.md``).
That step is CONDITIONAL by design: compaction is only worth writing if a
representative real document produces a block over roughly 5k characters
of which exact ``(rule, severity, message)`` grouping would remove more
than half. This script measures it. It changes nothing.

The block sits in the uncached tail of every turn, so its cost is real but
small — forty identical lines are roughly 2,500 tokens at Sonnet 5 rates.
The stronger argument, if the numbers support one, is keeping the report
readable for the model in the twenty-plus occurrence regime.

READ-ONLY. It never imports the client factory, never builds a session,
never makes a model request, and never prints provision text, lint match
excerpts, element references or filenames into the pasteable output.
Rule names, counts and character totals are all that travel; the local
console names the file so you can tell your own inputs apart.

Usage (Windows):

    .venv\\Scripts\\python tools\\lint_block_profile.py ^
        "C:\\path\\some-project.baspec"

    .venv\\Scripts\\python tools\\lint_block_profile.py ^
        "C:\\specs\\*.baspec" --out lint-measurement.md

Paste the block it prints into
``docs/review-results/<date>/measurements.md``.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.spec_doc.linting import lint_document  # noqa: E402
from backend.spec_doc.model import SpecSection  # noqa: E402
from backend.spec_doc.project_package import parse_project_file  # noqa: E402
from backend.spec_doc.source_format import SourceFormatMap  # noqa: E402
from backend.spec_modules.registry import get_module  # noqa: E402

# The block exactly as ``backend/llm/conversation.py`` renders it. Copied
# rather than imported because the rendering is inline in the turn-context
# builder, which needs a live session; ``_assert_renderer_unchanged`` below
# checks the copy against the source so a drifted renderer fails loudly
# instead of quietly measuring a block the app no longer emits.
_HEADER = (
    "LINT REPORT (deterministic, advisory — stale-edition findings "
    "are drafting errors to fix):"
)

# The plan's illustrative grouped form, using the existing formats rather
# than a new numbering scheme.
_GROUP_INDENT = "  "

# The recorded implementation contract scopes the proposed compaction to
# these two rules to begin with ("Start with `stale_edition` and
# `unrecorded_edition`"). Measuring grouping across EVERY rule would credit
# the change with savings the scoped renderer would not deliver — and on a
# document with many repeated `duplicate_provision` or placeholder findings
# that difference is easily the whole verdict. So the threshold is judged on
# the scoped number and the all-rules figure is reported beside it as the
# upper bound it is.
_SCOPED_RULES = frozenset({"stale_edition", "unrecorded_edition"})

# chars -> tokens is an ESTIMATE, and this is its rule: the same len/4 the
# app uses everywhere it has no real count to hand. It is not a tokenizer.
_CHARS_PER_TOKEN = 4


def _assert_renderer_unchanged() -> list[str]:
    """Confirm the copied format still matches conversation.py."""
    source = (_REPO_ROOT / "backend" / "llm" / "conversation.py").read_text("utf-8")
    problems: list[str] = []
    if "LINT REPORT (deterministic, advisory — stale-edition findings " not in source:
        problems.append("the LINT REPORT header literal")
    if "f\"- [{issue.get('rule')}] {where}: {issue.get('message')} \"" not in source:
        problems.append("the per-issue line format")
    if "f\"(element {issue.get('element_id')})\"" not in source:
        problems.append("the trailing element reference")
    if 'issue.get("ref") or issue.get("element_id") or ""' not in source:
        problems.append("the ref-or-element fallback")
    return problems


def _legacy_line(issue: dict[str, Any]) -> str:
    where = issue.get("ref") or issue.get("element_id") or ""
    return (
        f"- [{issue.get('rule')}] {where}: {issue.get('message')} "
        f"(element {issue.get('element_id')})"
    )


def _group_lines(issues: list[dict[str, Any]]) -> list[str]:
    """The grouped rendering for one (rule, severity, message) group."""
    first = issues[0]
    locations = "; ".join(
        f"{issue.get('ref') or issue.get('element_id') or ''} "
        f"(element {issue.get('element_id')})"
        for issue in issues
    )
    return [
        f"- [{first.get('rule')}] {first.get('message')}",
        f"{_GROUP_INDENT}Affected citations ({len(issues)}): {locations}.",
    ]


@dataclass
class DocProfile:
    artifact: str = ""
    module_id: str = ""
    issue_count: int = 0
    by_rule: Counter = field(default_factory=Counter)
    legacy_chars: int = 0
    # Grouping only the rules the proposed change would actually compact.
    grouped_chars: int = 0
    # Grouping every rule — the ceiling, not the proposal.
    grouped_chars_all: int = 0
    groups_total: int = 0
    groups_merged: int = 0
    largest_group: int = 0
    largest_scoped_group: int = 0
    groups_declined: int = 0
    scoped_issue_count: int = 0
    unstructured: bool = False
    chrome_lines: int = 0
    note: str = ""

    @property
    def removed_chars(self) -> int:
        return max(0, self.legacy_chars - self.grouped_chars)

    @property
    def removed_share(self) -> float:
        if self.legacy_chars <= 0:
            return 0.0
        return self.removed_chars / self.legacy_chars

    @property
    def removed_share_all_rules(self) -> float:
        if self.legacy_chars <= 0:
            return 0.0
        return max(0, self.legacy_chars - self.grouped_chars_all) / self.legacy_chars

    @property
    def meets_threshold(self) -> bool:
        """The plan's working threshold, stated as the judgment call it is.

        Judged on the SCOPED saving: the question is whether the change the
        contract describes earns its keep, not whether some larger change
        might.
        """
        return self.legacy_chars > 5000 and self.removed_share > 0.5


def _document_from(project: dict[str, Any]) -> SpecSection | None:
    store = project.get("doc")
    if not isinstance(store, dict):
        return None
    versions = store.get("versions")
    index = store.get("index")
    if not isinstance(versions, list) or not versions:
        return None
    if isinstance(index, bool) or not isinstance(index, int):
        index = len(versions) - 1
    if not 0 <= index < len(versions):
        index = len(versions) - 1
    try:
        return SpecSection.from_dict(versions[index])
    except Exception:  # noqa: BLE001 - a tool, not a service
        return None


def _is_unstructured(project: dict[str, Any]) -> bool:
    """Mirror ``SessionState.import_is_unstructured``.

    False unless the import recorded no spec shape AND the imported
    baseline is still the live one — undoing past an import and editing
    truncates it, and what remains gets the normal spec presentation back.
    """
    report = project.get("import_report")
    if not isinstance(report, dict):
        return False
    if report.get("spec_shape_detected") is not False:
        return False
    store = project.get("doc")
    if not isinstance(store, dict):
        return False
    baseline = store.get("baseline_index")
    versions = store.get("versions")
    if isinstance(baseline, bool) or not isinstance(baseline, int):
        return False
    if not isinstance(versions, list):
        return False
    return 0 <= baseline < len(versions)


def _preserved_chrome(project: dict[str, Any], has_source: bool) -> tuple[str, ...]:
    """The header/footer and front-matter lines the stale-identifier lint reads.

    Gated on retained source bytes exactly as the turn-context builder
    gates it, so the measurement sees the same rule set a real turn does.
    """
    if not has_source:
        return ()
    raw = project.get("format_map")
    if not isinstance(raw, dict):
        return ()
    try:
        return tuple(SourceFormatMap.from_dict(raw).preserved_chrome())
    except Exception:  # noqa: BLE001
        return ()


@dataclass
class GroupedRender:
    """One hypothetical rendering of the block."""

    chars: int = 0
    groups: int = 0
    merged: int = 0
    declined: int = 0
    largest: int = 0


def _render_grouped(
    issues: list[dict[str, Any]], rules: frozenset[str] | None
) -> GroupedRender:
    """Render the block with grouping applied to ``rules`` only.

    ``rules`` of ``None`` groups everything — the ceiling. Issues outside
    the set keep their legacy lines in place, which is what the scoped
    renderer described by the contract would actually emit.
    """
    # Exact equality on the triple: no normalization, first-seen order,
    # every element id and reference preserved.
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    order: list[tuple[str, str, str]] = []
    for issue in issues:
        key = (
            str(issue.get("rule") or ""),
            str(issue.get("severity") or ""),
            str(issue.get("message") or ""),
        )
        if key not in grouped:
            order.append(key)
        grouped[key].append(issue)

    out = GroupedRender(groups=len(order))
    rendered = [_HEADER]
    for key in order:
        members = grouped[key]
        out.largest = max(out.largest, len(members))
        legacy_form = [_legacy_line(issue) for issue in members]
        eligible = rules is None or key[0] in rules
        if len(members) < 2 or not eligible:
            rendered.extend(legacy_form)
            continue
        group_form = _group_lines(members)
        # Grouping is used ONLY where it is strictly shorter; a short
        # message can make the grouped form longer than the lines it
        # replaces, and singleton rendering stays byte-identical.
        if len("\n".join(group_form)) < len("\n".join(legacy_form)):
            rendered.extend(group_form)
            out.merged += 1
        else:
            rendered.extend(legacy_form)
            out.declined += 1
    out.chars = len("\n".join(rendered))
    return out


def _profile(path: Path) -> DocProfile:
    data = path.read_bytes()
    profile = DocProfile(artifact=hashlib.sha256(data).hexdigest()[:12])
    try:
        parsed = parse_project_file(data)
    except Exception as exc:  # noqa: BLE001
        profile.note = f"could not be parsed ({exc.__class__.__name__}: {exc})"
        return profile
    project = parsed.project
    section = _document_from(project)
    if section is None:
        profile.note = "carries no readable document version"
        return profile

    module_id = str(project.get("module_id") or "")
    module = get_module(module_id)
    profile.module_id = getattr(module, "module_id", module_id) or module_id
    profile.unstructured = _is_unstructured(project)
    chrome = _preserved_chrome(project, parsed.source_docx_bytes is not None)
    profile.chrome_lines = len(chrome)

    issues = lint_document(
        section,
        module,
        unstructured_import=profile.unstructured,
        preserved_chrome=chrome,
    )
    profile.issue_count = len(issues)
    for issue in issues:
        profile.by_rule[str(issue.get("rule") or "unknown")] += 1
    if not issues:
        return profile

    legacy = [_HEADER] + [_legacy_line(issue) for issue in issues]
    profile.legacy_chars = len("\n".join(legacy))
    profile.scoped_issue_count = sum(
        1 for issue in issues if str(issue.get("rule") or "") in _SCOPED_RULES
    )

    scoped = _render_grouped(issues, _SCOPED_RULES)
    every_rule = _render_grouped(issues, None)
    profile.grouped_chars = scoped.chars
    profile.grouped_chars_all = every_rule.chars
    profile.groups_total = every_rule.groups
    profile.groups_merged = scoped.merged
    profile.groups_declined = scoped.declined
    profile.largest_group = every_rule.largest
    profile.largest_scoped_group = scoped.largest
    return profile


def _markdown(profiles: list[DocProfile], drift: list[str]) -> str:
    lines: list[str] = []
    lines.append(f"# LINT REPORT block profile — measured {date.today().isoformat()}")
    lines.append("")
    lines.append(
        f"Produced by `tools/lint_block_profile.py` over "
        f"{len(profiles)} document(s). Documents are identified by a "
        "SHA-256 prefix; no provision text, lint match excerpt, element "
        "reference or filename appears below."
    )
    lines.append("")
    if drift:
        lines.append(
            "> **The renderer has drifted.** This script could not find "
            + ", ".join(drift)
            + " in `backend/llm/conversation.py`, so the character counts "
            "below describe a block the app may no longer emit. Re-sync "
            "the copied format before trusting them."
        )
        lines.append("")
    lines.append(
        "Character counts are of the block as the turn-context builder "
        "emits it. The token column is an ESTIMATE at "
        f"`len / {_CHARS_PER_TOKEN}` — the same rule the app uses where it "
        "has no real count — not a tokenizer."
    )
    lines.append("")
    lines.append(
        "The **Removed** column is the SCOPED saving — grouping applied only "
        f"to {', '.join(sorted('`' + r + '`' for r in _SCOPED_RULES))}, the "
        "rules the proposed change starts with. **All rules** is what "
        "grouping every rule would remove: a ceiling for a wider change, not "
        "a promise of this one, and the column to distrust if a document "
        "repeats one non-scoped finding many times."
    )
    lines.append("")
    lines.append(
        "| Document | Module | Issues | In scope | Merged | Legacy chars | "
        "Grouped chars | Removed | All rules | ~Tokens saved |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for profile in profiles:
        if profile.note:
            continue
        saved = profile.removed_chars // _CHARS_PER_TOKEN
        lines.append(
            f"| `{profile.artifact}` | `{profile.module_id or 'unknown'}` | "
            f"{profile.issue_count} | {profile.scoped_issue_count} | "
            f"{profile.groups_merged} | {profile.legacy_chars:,} | "
            f"{profile.grouped_chars:,} | "
            f"{profile.removed_share * 100:.1f}% | "
            f"{profile.removed_share_all_rules * 100:.1f}% | ~{saved:,} |"
        )
    lines.append("")

    for profile in profiles:
        if profile.note:
            lines.append(f"- `{profile.artifact}`: {profile.note}")
            continue
        lines.append(f"### `{profile.artifact}`")
        lines.append("")
        lines.append(
            f"- Module `{profile.module_id or 'unknown'}`; "
            f"unstructured import: {profile.unstructured}; "
            f"{profile.chrome_lines} preserved chrome line(s) fed to the "
            "stale-identifier rule."
        )
        if profile.issue_count == 0:
            lines.append("- No lint findings: the block is not emitted at all.")
            lines.append("")
            continue
        lines.append(
            f"- {profile.scoped_issue_count} of {profile.issue_count} finding(s) "
            "are in the rules the proposed change would compact."
        )
        lines.append(
            f"- Largest single `(rule, severity, message)` group: "
            f"{profile.largest_group} occurrence(s) across all rules, "
            f"{profile.largest_scoped_group} within the scoped rules."
        )
        if profile.groups_declined:
            lines.append(
                f"- {profile.groups_declined} multi-member group(s) rendered "
                "legacy lines anyway, because grouping them would have been "
                "longer."
            )
        lines.append("- Occurrences by rule:")
        for rule, count in profile.by_rule.most_common():
            lines.append(f"  - `{rule}`: {count}")
        verdict = (
            "**meets** the threshold"
            if profile.meets_threshold
            else "does **not** meet the threshold"
        )
        lines.append(
            f"- Against the plan's working threshold (>5,000 chars AND >50% "
            f"removable by the SCOPED change), this document {verdict}: "
            f"{profile.legacy_chars:,} chars, {profile.removed_share * 100:.1f}% "
            f"removable in scope ({profile.removed_share_all_rules * 100:.1f}% "
            "if every rule were grouped)."
        )
        lines.append("")

    if lines and lines[-1] != "":
        lines.append("")

    measured = [p for p in profiles if not p.note and p.issue_count]
    lines.append("## Decision")
    lines.append("")
    if not measured:
        lines.append(
            "Nothing measurable: none of these documents produced lint "
            "findings, so there is no block to compact."
        )
    else:
        qualifying = [p for p in measured if p.meets_threshold]
        worst = max(measured, key=lambda p: p.legacy_chars)
        lines.append(
            f"Largest block measured: **{worst.legacy_chars:,} characters** "
            f"(~{worst.legacy_chars // _CHARS_PER_TOKEN:,} estimated tokens), "
            f"of which **{worst.removed_share * 100:.1f}%** would be removed by "
            "the scoped change "
            f"({worst.removed_share_all_rules * 100:.1f}% if grouping were "
            "extended to every rule)."
        )
        lines.append("")
        if qualifying:
            lines.append(
                f"→ **{len(qualifying)} of {len(measured)} document(s) meet the "
                "threshold.** Compaction is worth writing. Scope it to the "
                "lint text inserted into `_turn_context_text` — not "
                "`lint_document`, raw ids, frontend rows, the lint SSE "
                "payload, readiness, or exports — starting with "
                "`stale_edition` and `unrecorded_edition`, and keep singleton "
                "rendering byte-identical."
            )
        else:
            wider = [p for p in measured if p.removed_share_all_rules > 0.5]
            lines.append(
                "→ **No document meets the threshold.** Defer the compaction "
                "and keep this measurement as the reason. The dollar case is "
                "small either way; re-measure if a real master ever pushes "
                "the block past it."
            )
            if wider:
                lines.append("")
                lines.append(
                    f"Note that {len(wider)} document(s) WOULD clear 50% if "
                    "grouping were extended past the two scoped rules. That is "
                    "an argument for revisiting the scope, not for claiming "
                    "the scoped change earns its keep — decide it deliberately "
                    "rather than by letting the measurement drift."
                )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Measure the LINT REPORT context block on real project files. "
            "Read-only; emits rule names and counts, never provision text."
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

    drift = _assert_renderer_unchanged()
    if drift:
        print(
            "WARNING: backend/llm/conversation.py no longer matches the "
            "format this script copies (" + ", ".join(drift) + ").",
            file=sys.stderr,
        )

    profiles: list[DocProfile] = []
    for path in expanded:
        if not path.is_file():
            print(f"  skip  {path}  (not a file)", file=sys.stderr)
            continue
        profile = _profile(path)
        # Local orientation only: the filename never reaches the markdown.
        status = profile.note or f"{profile.issue_count} lint finding(s)"
        print(f"  read  {path.name}  → {profile.artifact}  {status}", file=sys.stderr)
        profiles.append(profile)

    if not profiles:
        print("No readable project files. Nothing measured.", file=sys.stderr)
        return 1

    text = _markdown(profiles, drift)
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
