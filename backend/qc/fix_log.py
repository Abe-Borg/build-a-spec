"""The durable record of applied Final QC fixes (Redline on your original,
Phase 3).

An applied fix used to leave only a disposition event on its finding (action,
time, document version, fingerprint) — never the elements it changed — and
the next successful Final QC run replaces the retained result outright. So
after the ordinary "apply the fixes, then re-run QC", the applied findings'
titles and sources were gone, and nothing could say which change in the
document a QC fix made.

This log closes that gap. One entry per finding, written in the SAME places
``QCRunner.mark_applied`` is called — the panel's apply route and the chat
turn's commit block — with the same finding ids, so a rolled-back turn writes
nothing and a fix voided later in its own turn is never logged. Each entry
holds the display facts a reader needs (title, severity, lens, issue, the
accepted sources with their titles, when it was applied, which run) and the
fix-survival evidence the apply path already computes
(:func:`backend.qc.apply.finding_evidence_keys` +
:func:`~backend.qc.apply.capture_fix_evidence`), JSON-normalized.

The evidence is what keeps the log honest after the fact: an entry says an
element carries its fix only while the element still reads as the fix left
it (:func:`entry_covers`). An undo, a later edit or a re-run that moved the
text on therefore never yields a claim about a remedy the document no longer
holds, and a redo brings it back.

The log is session state: persisted as an optional project key, cleared by a
reset, never carried in a project brief and never an input to Final QC.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .apply import evidence_value

#: A runaway guard, not a quality limit: an entry is a few hundred bytes and
#: a section's fixes number in the tens. Past it the OLDEST entries go — the
#: ones least likely to still describe the document.
MAX_FIX_LOG_ENTRIES = 500

#: Evidence of an element's own fields that the log ignores when deciding
#: whether a fix still holds: confirming a fixed provision in the review walk
#: (status) or re-pointing its source must not erase its QC comment.
_IGNORED_FIELDS = frozenset({"status", "source_item_id"})

_TEXT_FIELDS = ("finding_id", "title", "severity", "lens_id", "lens_title", "issue")


def now_iso() -> str:
    """When an entry is written: UTC, to the second (the disposition events'
    own format)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalized(value: Any) -> Any:
    """``value`` as JSON would round-trip it: tuples become lists. What is
    stored and what is compared go through here, so the two never differ in
    shape alone."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _finding_sources(finding) -> list[dict[str, str]]:
    """The finding's ACCEPTED sources (grounding accepted them — never merely
    cited), each with the title a source check recorded for it."""
    titles: dict[str, str] = {}
    for check in getattr(finding, "source_checks", None) or []:
        url = str(getattr(check, "url", "") or "")
        if url and getattr(check, "accepted", None) is True:
            titles.setdefault(url, str(getattr(check, "title", "") or ""))
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for url in getattr(finding, "accepted_sources", None) or []:
        url = str(url or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        sources.append({"url": url, "title": titles.get(url, "")})
    return sources


#: The fields an operation writes that are NOT what a reader sees: a fix made
#: of nothing else never claims the element's words as its own.
_METADATA_FIELDS = frozenset({"status", "source_item_id"})
_OP_ENVELOPE = frozenset({"action", "target_id", "position"})


def _writes_content(op: Any) -> bool:
    """Does this operation write something a reader sees at its target? A
    ``set_status`` never does; a ``replace`` does only when it carries more
    than status or a source link. Every other action (add, delete, move)
    changes content by definition."""
    if not isinstance(op, dict):
        return True
    action = str(op.get("action", "") or "")
    if action == "set_status":
        return False
    if action == "replace":
        written = {k for k, v in op.items() if v is not None} - _OP_ENVELOPE
        return bool(written - _METADATA_FIELDS)
    return True


def _metadata_only_uids(finding) -> set[str]:
    """The elements this finding's own operations touched without writing any
    content — a status confirmation, a re-pointed source. Its evidence for
    them still guards survival, but it must never be read as the author of
    those elements' words (Codex, PR #211)."""
    metadata: set[str] = set()
    content: set[str] = set()
    for op in getattr(finding, "proposed_ops", None) or []:
        target = str(op.get("target_id", "") or "") if isinstance(op, dict) else ""
        if not target:
            continue
        (content if _writes_content(op) else metadata).add(target)
    return metadata - content


def fix_log_entries(
    result,
    finding_ids: list[str],
    evidence: dict[tuple[str, str], Any],
    evidence_keys: dict[str, list[tuple[str, str]]],
    *,
    applied_at: str,
) -> list[dict[str, Any]]:
    """One log entry per applied finding in ``finding_ids``.

    ``evidence`` / ``evidence_keys`` are exactly what the apply path captured
    the moment the fixes applied. A finding the result does not hold as a
    survivor (only survivors are applied) writes nothing."""
    lens_titles = {
        str(getattr(status, "lens_id", "") or ""): str(
            getattr(status, "title", "") or ""
        )
        for status in getattr(result, "lens_statuses", None) or []
    }
    survivors = {f.finding_id: f for f in getattr(result, "findings", None) or []}
    entries: list[dict[str, Any]] = []
    for finding_id in dict.fromkeys(finding_ids):
        finding = survivors.get(finding_id)
        if finding is None:
            continue
        keys = evidence_keys.get(finding_id, [])
        metadata_only = _metadata_only_uids(finding)
        entries.append(
            {
                "finding_id": finding_id,
                "title": str(finding.title or ""),
                "severity": str(finding.severity or ""),
                "lens_id": str(finding.lens_id or ""),
                "lens_title": lens_titles.get(str(finding.lens_id or ""), ""),
                "issue": str(finding.issue or ""),
                "sources": _finding_sources(finding),
                "applied_at": str(applied_at or ""),
                "run_id": str(getattr(result, "run_id", "") or ""),
                "evidence": [
                    {
                        "key": [kind, ref],
                        "value": normalized(evidence.get((kind, ref))),
                        # Did the fix write what a reader sees here? Only
                        # then may a redline comment credit it.
                        "content": not (kind == "field" and ref in metadata_only),
                    }
                    for kind, ref in keys
                ],
            }
        )
    return entries


def _sanitize_entry(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    entry: dict[str, Any] = {}
    for key in _TEXT_FIELDS:
        entry[key] = str(raw.get(key, "") or "")
    if not entry["finding_id"]:
        return None
    sources = []
    for source in raw.get("sources") or []:
        if isinstance(source, dict) and str(source.get("url", "") or "").strip():
            sources.append(
                {
                    "url": str(source["url"]).strip(),
                    "title": str(source.get("title", "") or ""),
                }
            )
    entry["sources"] = sources
    entry["applied_at"] = str(raw.get("applied_at", "") or "")
    entry["run_id"] = str(raw.get("run_id", "") or "")
    evidence = []
    for item in raw.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if (
            isinstance(key, list)
            and len(key) == 2
            and all(isinstance(part, str) for part in key)
        ):
            evidence.append(
                {
                    "key": list(key),
                    "value": normalized(item.get("value")),
                    "content": item.get("content") is not False,
                }
            )
    entry["evidence"] = evidence
    return entry


def sanitize_fix_log(raw: Any) -> list[dict[str, Any]]:
    """The lenient project-file reader: anything that is not a list of
    well-formed entries loads as far as it is well-formed — a malformed entry
    is dropped, never a reason to refuse the project — and the newest
    :data:`MAX_FIX_LOG_ENTRIES` are kept."""
    if not isinstance(raw, list):
        return []
    entries = [entry for entry in map(_sanitize_entry, raw) if entry is not None]
    return entries[-MAX_FIX_LOG_ENTRIES:]


def _comparable(value: Any) -> Any:
    """An element's own-field evidence, minus what the log ignores."""
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if k not in _IGNORED_FIELDS}
    return value


def covered_uids(entry: dict[str, Any]) -> list[str]:
    """The body elements whose CONTENT an entry's fix wrote (its ``field``
    keys), in the order the fix wrote them. Standards, identity and profile
    keys name no body element, and an element the fix only re-statused or
    re-sourced is not the fix's to claim — so a metadata-only fix covers
    nothing."""
    return list(
        dict.fromkeys(
            item["key"][1]
            for item in entry.get("evidence", ())
            if item["key"][0] == "field"
            and item["key"][1]
            and item.get("content") is not False
        )
    )


def entry_covers(entry: dict[str, Any], section, uid: str) -> bool:
    """Does ``section`` still hold, at ``uid``, what this fix left there?

    * a paragraph: its text; an article or PART: its title; the section: its
      number and title — status and source link ignored;
    * a deletion: the element is still absent;
    * a move: the element is also still at exactly the position the fix put
      it — the same parent and the same index among its siblings (the
      strictness the chat commit's own survival check uses).
    """
    field = pos = None
    for item in entry.get("evidence", ()):
        kind, ref = item["key"]
        if ref != uid:
            continue
        if kind == "field" and item.get("content") is False:
            return False  # re-statused or re-sourced: not the fix's words
        if kind == "field":
            field = item
        elif kind == "pos":
            pos = item
    if field is None:
        return False
    current = normalized(evidence_value(section, ("field", uid)))
    if _comparable(current) != _comparable(field["value"]):
        return False
    if pos is not None:
        return normalized(evidence_value(section, ("pos", uid))) == pos["value"]
    return True


__all__ = [
    "MAX_FIX_LOG_ENTRIES",
    "covered_uids",
    "entry_covers",
    "fix_log_entries",
    "normalized",
    "now_iso",
    "sanitize_fix_log",
]
