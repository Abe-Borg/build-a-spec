"""The FINAL QC REVIEW block for the chat model's per-turn context.

Final QC was built as its own channel and, until this module, its results
never reached the chat model at all — the interview could not answer "what
did the review find?" about a report sitting one panel away. This renders
the RETAINED result compactly into the dynamic PROJECT CONTEXT (never the
stable system prompt — the cache rule), the same posture as
``research.engine.research_context_block``: pure, no locks, whole-record
trimming with a disclosed count, and the structured result untouched.

Token discipline: only what the model can act on travels — finding ids
(the ``apply_qc_fixes`` tool input), severity, anchor ref, title, a
one-line issue, and the fix class shared with the apply gate
(``apply.finding_fix_class``). Rationale, sources, seat ledgers and raw
operation JSON stay in the panel and the report, where they belong.
"""
from __future__ import annotations

from typing import Any

from .apply import FIX_CLASS_SAFE, finding_fix_class
from .schema import SEVERITY_RANK

# Ceiling on the rendered block inside the per-turn PROJECT CONTEXT.
# Estimated tokens (len/4 — no tokenizer dependency); a runaway guard, not a
# budget: a report has to be pathological to hit it.
QC_CONTEXT_MAX_TOKENS = 20_000


def _estimate_tokens(text: str) -> int:
    return len(text) // 4


def _one_line(text: str, limit: int = 240) -> str:
    """Fold whitespace to one line; cut long prose with a visible mark."""
    folded = " ".join(str(text or "").split())
    if len(folded) <= limit:
        return folded
    return folded[: limit - 1].rstrip() + "…"


def _finding_line(finding: Any) -> str:
    ref = str(
        getattr(finding, "reviewed_ref", "")
        or getattr(finding, "element_id", "")
        or "section-level"
    )
    if finding_fix_class(finding) == FIX_CLASS_SAFE:
        fix = (
            "fix: verified safe fix, "
            f"{len(finding.proposed_ops)} op(s) — apply via apply_qc_fixes "
            "on the user's explicit approval"
        )
    else:
        fix = (
            "fix: advisory only — no panel-approved operations; remedy by "
            "ordinary drafting if the user asks"
        )
    return (
        f"- [{finding.severity}] {finding.finding_id} {ref}: "
        f"{_one_line(finding.title)} — {_one_line(finding.issue)} ({fix})"
    )


def _disputed_line(finding: Any) -> str:
    ref = str(
        getattr(finding, "reviewed_ref", "")
        or getattr(finding, "element_id", "")
        or "section-level"
    )
    return (
        f"- [{finding.severity}] {finding.finding_id} {ref}: "
        f"{_one_line(finding.title)} — {_one_line(finding.issue)}"
    )


def _render(
    result: Any,
    open_findings: list[Any],
    open_disputed: list[Any],
    *,
    stale: bool,
    trimmed: int,
    latest_attempt_note: str,
) -> str:
    version_display = (
        f"v{result.version_index + 1} (stored index {result.version_index})"
    )
    lines = [
        "FINAL QC REVIEW (retained Final QC result — what the last review "
        "found; the full report lives in the Final QC panel):",
        (
            f"Run {result.run_id or '(unrecorded)'} · execution "
            f"{result.execution_status} · reviewed document version "
            f"{version_display}"
            + (f" · finished {result.finished_at}" if result.finished_at else "")
        ),
    ]
    if stale:
        lines.append(
            "Freshness: STALE — the document changed after this review. "
            "Findings may no longer anchor where they say, and fixes CANNOT "
            "be applied until Final QC is re-run; describe them as the last "
            "review's findings, never as verified against the current text."
        )
    else:
        lines.append(
            "Freshness: CURRENT — this review describes the document as it "
            "stands."
        )
    if open_findings:
        lines.append("")
        lines.append(
            f"OPEN FINDINGS ({len(open_findings)} — ids are apply_qc_fixes "
            "inputs; never apply without the user's explicit approval in "
            "this conversation):"
        )
        lines.extend(_finding_line(f) for f in open_findings)
    else:
        lines.append("")
        lines.append(
            "OPEN FINDINGS: none — every surviving finding has been applied "
            "or dismissed."
        )
    if open_disputed:
        lines.append("")
        lines.append(
            f"OPEN DISPUTED CANDIDATES ({len(open_disputed)} — the complete "
            "verifier panel disagreed; NEVER applyable; the user adjudicates "
            "each in the Final QC panel, by dismissing with a reason or "
            "re-running):"
        )
        lines.extend(_disputed_line(f) for f in open_disputed)
    applied = sum(1 for f in result.findings if f.status == "applied")
    dismissed = sum(1 for f in result.findings if f.status == "dismissed")
    dismissed += sum(1 for f in result.disputed if f.status == "dismissed")
    lines.append("")
    lines.append(
        f"Also on this report: {applied} applied · {dismissed} dismissed · "
        f"{len(result.refuted)} refuted · {len(result.inconclusive)} "
        "inconclusive (audit records — details in the Final QC report)."
    )
    if trimmed:
        lines.append(
            f"NOTE: {trimmed} finding(s) were trimmed from this rendering "
            "for length — this list is PARTIAL; the full set is in the "
            "Final QC panel."
        )
    if latest_attempt_note:
        lines.append(latest_attempt_note)
    return "\n".join(lines)


def qc_review_context_block(
    result: Any,
    *,
    current_version_index: int,
    current_section: Any,
    latest_attempt_note: str = "",
    max_tokens: int = QC_CONTEXT_MAX_TOKENS,
) -> tuple[str, int]:
    """Render the retained QC result for chat context, capped.

    Returns ``(block_text, trimmed_finding_count)``. When the rendering
    exceeds ``max_tokens`` (estimated), whole findings are dropped from the
    RENDERING only — lowest severity first, later-listed first among ties —
    and the block discloses the omission. Open disputed candidates trim
    LAST: they block issue readiness and need the user's decision, so they
    are the lines that must survive. The structured result keeps every
    finding either way (the panel and both report downloads are unabridged).
    """
    stale = not result.matches_version(current_version_index, current_section)
    open_findings = [f for f in result.findings if f.status == "open"]
    open_disputed = [f for f in result.disputed if f.status == "open"]
    trimmed = 0
    candidate = _render(
        result,
        open_findings,
        open_disputed,
        stale=stale,
        trimmed=trimmed,
        latest_attempt_note=latest_attempt_note,
    )
    while (
        _estimate_tokens(candidate) > max_tokens
        and (open_findings or open_disputed)
    ):
        pool = open_findings if open_findings else open_disputed
        lowest = min(
            range(len(pool)),
            key=lambda i: (SEVERITY_RANK.get(pool[i].severity, 0), -i),
        )
        pool.pop(lowest)
        trimmed += 1
        candidate = _render(
            result,
            open_findings,
            open_disputed,
            stale=stale,
            trimmed=trimmed,
            latest_attempt_note=latest_attempt_note,
        )
    return candidate, trimmed
