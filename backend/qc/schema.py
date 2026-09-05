"""Final-QC lens definitions, output tools, and payload normalization.

The QC pass is a lens fan-out (five independent Opus 5 calls), a
cross-lens consolidation step, then an adversarial verification panel per
consolidated candidate; this module owns the lens briefs, the three strict
output tools (``submit_qc_findings`` / ``submit_qc_consolidation`` /
``submit_qc_verdict``), and the parse-time normalization that clamps model
output to the contract.

Schema conventions are copied from :mod:`backend.research.schema` — the
strict-mode subset (every property required, optionals nullable, no numeric
constraints; clamp at parse). ``strict: true`` attaches only for the known
strict-capable models (Opus 5 is one; see ``_STRICT_CAPABLE_MODELS``).
``proposed_ops`` mirrors the ``apply_spec_edits`` op vocabulary so a finding
can carry a ready-to-apply fix; the engine dry-runs those ops against a
document snapshot before ever offering them (never trusts them raw).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .. import settings
from ..research.schema import _STRICT_CAPABLE_MODELS

QC_FINDINGS_TOOL_NAME = "submit_qc_findings"
QC_VERDICT_TOOL_NAME = "submit_qc_verdict"
QC_CONSOLIDATION_TOOL_NAME = "submit_qc_consolidation"

SEVERITIES: tuple[str, ...] = ("critical", "high", "medium", "low")
QC_CHECK_OUTCOMES: tuple[str, ...] = ("passed", "finding", "not_applicable")
# Rank for the median-severity math (higher = more severe).
SEVERITY_RANK: dict[str, int] = {"critical": 3, "high": 2, "medium": 1, "low": 0}
_RANK_SEVERITY: dict[int, str] = {v: k for k, v in SEVERITY_RANK.items()}


# ---------------------------------------------------------------------------
# Lens definitions (frozen)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QCLens:
    """One independent review lens in the phase-1 fan-out.

    ``max_searches`` / ``max_fetches`` are per-call web allowances (runaway
    guards, not budgets). Only ``code_compliance`` gets the big search
    allowance — it USES web search to check a standard's actual current
    content rather than recalling it.
    """

    lens_id: str
    title: str
    brief: str
    max_searches: int
    max_fetches: int
    web: bool = True


# The five lenses. code_compliance + completeness strictly supersede the
# Phase 5 compliance audit (see the migration note in the batch plan).
QC_LENSES: tuple[QCLens, ...] = (
    QCLens(
        lens_id="code_compliance",
        title="Code & standard compliance",
        max_searches=settings.QC_MAX_SEARCHES_COMPLIANCE,
        max_fetches=settings.QC_MAX_FETCHES_COMPLIANCE,
        brief=(
            "Verify every standard citation, edition, and technical "
            "requirement in the draft against the editions in effect. USE "
            "web_search to check the standard's ACTUAL current content — do "
            "not recall it from memory; article numbers and requirements are "
            "renumbered across editions. Flag: requirements that contradict "
            "the cited standard, editions that contradict the recorded basis "
            "in <standards_in_effect>, and provisions an authority having "
            "jurisdiction would reject. Cite the URLs you actually retrieved."
        ),
    ),
    QCLens(
        lens_id="coordination_consistency",
        title="PART 1/2/3 coordination & consistency",
        max_searches=settings.QC_MAX_SEARCHES_LENS,
        max_fetches=settings.QC_MAX_FETCHES_LENS,
        web=False,
        brief=(
            "Check PART 1 / PART 2 / PART 3 alignment: every product "
            "specified has submittal requirements; every product has "
            "execution provisions; execution references products that exist; "
            "cross-references resolve; no duplicated or mutually "
            "contradicting provisions; no terminology drift (the same thing "
            "named two ways). Where <attached_reference_documents> are "
            "present, a provision that contradicts one is the same class of "
            "defect — flag it and say which side says what. Anchor each "
            "finding to the element ids involved."
        ),
    ),
    QCLens(
        lens_id="completeness",
        title="Completeness vs. scope & research",
        max_searches=settings.QC_MAX_SEARCHES_LENS,
        max_fetches=settings.QC_MAX_FETCHES_LENS,
        web=False,
        brief=(
            "Judge completeness against the grounded spec_requirements in "
            "<project_requirements_profile> (each controlling item either "
            "represented in the draft or consciously absent), the module's "
            "conventional section scope, and the articles a reviewer would "
            "expect. Flag missing articles and unrepresented controlling "
            "requirements. Judge coverage against "
            "<attached_reference_documents> the same way when they are "
            "present: a requirement stated in an owner standard or "
            "basis-of-design that the draft never addresses is a missing "
            "requirement, and one the project consciously departs from "
            "should say so. If no research profile is supplied, skip profile "
            "coverage and say so — judge scope from section conventions "
            "alone."
        ),
    ),
    QCLens(
        lens_id="enforceability_language",
        title="Enforceability & spec language",
        max_searches=settings.QC_MAX_SEARCHES_LENS,
        max_fetches=settings.QC_MAX_FETCHES_LENS,
        web=False,
        brief=(
            "Review spec-language quality: imperative mood ('Provide', "
            "'Install', 'Submit'); measurable, verifiable criteria; no 'as "
            "required' / 'as needed' / 'etc.' / vague responsibility; no "
            "design-delegation traps; no narrative prose inside the spec. "
            "Flag each offending provision by element id with the concrete "
            "rewrite."
        ),
    ),
    QCLens(
        lens_id="provenance_hygiene",
        title="Provenance hygiene",
        max_searches=settings.QC_MAX_SEARCHES_LENS,
        max_fetches=settings.QC_MAX_FETCHES_LENS,
        web=False,
        brief=(
            "Flag provenance risks a reviewer must not miss: risky 'assumed' "
            "blocks (defaults that would be wrong if the assumption is), "
            "surviving [TBD:...] markers, needs_input blocks, and imported "
            "blocks not yet reviewed; provisions whose source_item_id points "
            "at an [UNVERIFIED] research item, at an attached document "
            "(ref-...) whose text does not actually support the provision, "
            "at an established project fact (pf-...) that has been "
            "superseded or does not say what the provision says, or at an "
            "id that exists in none of those sets. Do NOT propose mass "
            "status upgrades — flag the specific blocks that need a human "
            "decision."
        ),
    ),
)

QC_LENS_BY_ID: dict[str, QCLens] = {lens.lens_id: lens for lens in QC_LENSES}


# ---------------------------------------------------------------------------
# proposed_ops item schema (mirrors the apply_spec_edits op vocabulary)
# ---------------------------------------------------------------------------

# The op actions a QC fix may use. set_project_profile is excluded — QC never
# touches the project identity. set_standard_suppressed IS included: a
# standards-scope fix (e.g. excluding a standard that shouldn't reach
# REFERENCES) is a legitimate QC proposal, and /api/doc/edit accepts it — the
# QC allow-list must mirror the apply_spec_edits vocabulary it reasons from.
QC_OP_ACTIONS: tuple[str, ...] = (
    "add_article",
    "add_paragraph",
    "move",
    "replace",
    "delete",
    "set_status",
    "set_standard_edition",
    "set_standard_suppressed",
)

# Known op keys carried through to the dry-run (nulls dropped at parse). No
# numeric/enum constraints below the action — the transactional apply_edits
# dry-run is the real validator.
_QC_OP_KEYS: tuple[str, ...] = (
    "action",
    "target_id",
    "text",
    "numbering",
    "status",
    "position",
    "source_item_id",
    "standard",
    "edition",
    "basis",
    "title",
    "suppressed",
)

_QC_OP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": list(_QC_OP_KEYS),
    "properties": {
        "action": {"type": "string", "enum": list(QC_OP_ACTIONS)},
        "target_id": {
            "type": "string",
            "description": "An existing element id (pt1 / pt1.a2 / pt1.a2.p3) or 'sec'.",
        },
        "text": {"type": ["string", "null"]},
        "numbering": {"type": ["string", "null"]},
        "status": {"type": ["string", "null"]},
        "position": {"type": ["integer", "null"]},
        "source_item_id": {"type": ["string", "null"]},
        "standard": {"type": ["string", "null"]},
        "edition": {"type": ["string", "null"]},
        "basis": {"type": ["string", "null"]},
        "title": {"type": ["string", "null"]},
        "suppressed": {"type": ["boolean", "null"]},
    },
}


# ---------------------------------------------------------------------------
# submit_qc_findings (phase 1 output)
# ---------------------------------------------------------------------------

QC_FINDINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "reviewed_checks", "findings"],
    "properties": {
        "summary": {
            "type": "string",
            "description": "One or two sentences on what this lens found overall.",
        },
        "reviewed_checks": {
            "type": "array",
            "description": (
                "A concise audit trail of the substantive checks this lens "
                "actually performed, including checks that passed or were "
                "not applicable. This is observable work, not hidden reasoning."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "check",
                    "outcome",
                    "notes",
                    "element_ids",
                    "source_urls",
                ],
                "properties": {
                    "check": {
                        "type": "string",
                        "description": "What was checked, stated as a review task.",
                    },
                    "outcome": {
                        "type": "string",
                        "enum": list(QC_CHECK_OUTCOMES),
                    },
                    "notes": {
                        "type": "string",
                        "description": (
                            "Short result note. Do not include private reasoning or "
                            "chain-of-thought."
                        ),
                    },
                    "element_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Relevant specification element ids, else [].",
                    },
                    "source_urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "URLs actually retrieved for this check, else []."
                        ),
                    },
                },
            },
        },
        "findings": {
            "type": "array",
            "description": "Zero or more distinct defects this lens found.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "title",
                    "severity",
                    "element_id",
                    "issue",
                    "rationale",
                    "source_urls",
                    "proposed_ops",
                ],
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short label for the finding.",
                    },
                    "severity": {
                        "type": "string",
                        "enum": list(SEVERITIES),
                    },
                    "element_id": {
                        "type": ["string", "null"],
                        "description": (
                            "The [id: …] of the offending element, or null "
                            "for a section-level finding."
                        ),
                    },
                    "issue": {
                        "type": "string",
                        "description": "What is wrong.",
                    },
                    "rationale": {
                        "type": "string",
                        "description": (
                            "Why, with citations when web-verified."
                        ),
                    },
                    "source_urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "URLs you actually retrieved that support the "
                            "finding, else []. Never cite a URL you did not "
                            "retrieve."
                        ),
                    },
                    "proposed_ops": {
                        "type": ["array", "null"],
                        "items": _QC_OP_SCHEMA,
                        "description": (
                            "apply_spec_edits operations that fix the issue, "
                            "targeting existing ids; null when there is no "
                            "clean mechanical fix (advisory only)."
                        ),
                    },
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# submit_qc_verdict (phase 2 output)
# ---------------------------------------------------------------------------

# One structured citation backing a refutation. The v4 evidence rule gates a
# critical/high REFUTED outcome on at least one of these validating — a
# search that returned nothing useful is an activity record, not evidence.
QC_REFUTATION_EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["type", "url", "reference"],
    "properties": {
        "type": {
            "type": "string",
            "description": (
                "'source' for a web page you actually retrieved in this "
                "review, or 'document_ref' for a place in the specification "
                "under review or an attached reference document supplied "
                "with it."
            ),
        },
        "url": {
            "type": ["string", "null"],
            "description": (
                "For type 'source': the exact URL you retrieved. It must be "
                "one you actually fetched — an unretrieved URL does not count."
            ),
        },
        "reference": {
            "type": ["string", "null"],
            "description": (
                "For type 'document_ref': the element id in the reviewed "
                "specification (for example 'pt2.a1.p3', or 'sec') that "
                "contradicts the finding — or, when the refutation rests on "
                "a document the user attached, that document's id from "
                "<attached_reference_documents> (for example 'ref-2'). Cite "
                "the attachment id itself, not a page or clause inside it."
            ),
        },
    },
}

QC_VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "upholds",
        "revised_severity",
        "note",
        "ops_adequate",
        "ops_note",
        "refutation_evidence",
    ],
    "properties": {
        "upholds": {
            "type": "boolean",
            "description": (
                "True only if the finding is a real, actionable defect that "
                "survives your refutation attempt."
            ),
        },
        "revised_severity": {
            "type": ["string", "null"],
            "description": (
                "A corrected severity (critical, high, medium, or low), or "
                "null to keep the original."
            ),
        },
        "note": {
            "type": "string",
            "description": "One-line rationale for the verdict.",
        },
        "ops_adequate": {
            "type": "boolean",
            "description": (
                "True only when the complete proposed operation set safely "
                "and fully fixes the upheld finding."
            ),
        },
        "ops_note": {
            "type": "string",
            "description": (
                "One-line rationale for whether the proposed operations are "
                "adequate and safe."
            ),
        },
        "refutation_evidence": {
            "type": "array",
            "items": QC_REFUTATION_EVIDENCE_SCHEMA,
            "description": (
                "When you REFUTE a critical or high finding, cite what "
                "supports the refutation: a source you retrieved, a place "
                "in the reviewed specification, or an attached reference "
                "document supplied with this review. Required in substance for "
                "those refutations — without at least one entry that checks "
                "out, the finding is escalated to a human as disputed rather "
                "than dismissed. Leave empty when you uphold, or when "
                "refuting a medium/low finding."
            ),
        },
    },
}


# ---------------------------------------------------------------------------
# submit_qc_consolidation (cross-lens grouping, between phase 1 and phase 2)
# ---------------------------------------------------------------------------

QC_CONSOLIDATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["groups"],
    "properties": {
        "groups": {
            "type": "array",
            "description": (
                "A partition of the supplied candidate indexes. Every index "
                "must appear in exactly one group. A candidate that shares no "
                "defect with any other is its own single-member group — that "
                "is the normal, expected answer."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "member_indexes",
                    "canonical_title",
                    "canonical_issue",
                    "canonical_rationale",
                    "grouping_rationale",
                    "reconciled_ops",
                ],
                "properties": {
                    "member_indexes": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": (
                            "The candidate indexes in this group, from the "
                            "supplied list only."
                        ),
                    },
                    "canonical_title": {
                        "type": ["string", "null"],
                        "description": (
                            "For a multi-member group: one short label for the "
                            "shared defect. Null for a single-member group — "
                            "its original wording is kept verbatim."
                        ),
                    },
                    "canonical_issue": {
                        "type": ["string", "null"],
                        "description": (
                            "For a multi-member group: the shared defect "
                            "stated once, covering what every member raised. "
                            "Introduce no claim no member made."
                        ),
                    },
                    "canonical_rationale": {
                        "type": ["string", "null"],
                        "description": (
                            "For a multi-member group: why it is a defect, "
                            "drawn only from the members' own rationales."
                        ),
                    },
                    "grouping_rationale": {
                        "type": ["string", "null"],
                        "description": (
                            "For a multi-member group: why one fix would "
                            "dispose of every member."
                        ),
                    },
                    "reconciled_ops": {
                        "type": ["array", "null"],
                        "items": _QC_OP_SCHEMA,
                        "description": (
                            "For a multi-member group whose members proposed "
                            "DIFFERENT operations: one operation set that "
                            "resolves the shared defect once. Null when the "
                            "members already agree, when no clean single fix "
                            "exists, or for a single-member group."
                        ),
                    },
                },
            },
        },
    },
}


def _tool(name: str, description: str, schema: dict[str, Any], model: str | None) -> dict[str, Any]:
    tool: dict[str, Any] = {
        "name": name,
        "description": description,
        "input_schema": schema,
    }
    if model in _STRICT_CAPABLE_MODELS:
        tool["strict"] = True
    return tool


def submit_qc_findings_tool(*, model: str | None = None) -> dict[str, Any]:
    return _tool(
        QC_FINDINGS_TOOL_NAME,
        "Submit this lens's QC findings. Call exactly once as the final step "
        "of your turn.",
        QC_FINDINGS_SCHEMA,
        model,
    )


def submit_qc_verdict_tool(*, model: str | None = None) -> dict[str, Any]:
    return _tool(
        QC_VERDICT_TOOL_NAME,
        "Submit your verdict on the proposed QC finding. Call exactly once.",
        QC_VERDICT_SCHEMA,
        model,
    )


def submit_qc_consolidation_tool(*, model: str | None = None) -> dict[str, Any]:
    return _tool(
        QC_CONSOLIDATION_TOOL_NAME,
        "Submit the grouping of these QC candidates. Call exactly once, and "
        "account for every supplied index exactly once.",
        QC_CONSOLIDATION_SCHEMA,
        model,
    )


# ---------------------------------------------------------------------------
# Normalization (parse-time contract enforcement)
# ---------------------------------------------------------------------------


def _clean_op(raw: object) -> dict[str, Any] | None:
    """Keep only known op keys with non-null values; drop non-dicts / no-action."""
    if not isinstance(raw, dict):
        return None
    action = raw.get("action")
    if not isinstance(action, str) or action not in QC_OP_ACTIONS:
        return None
    op: dict[str, Any] = {}
    for key in _QC_OP_KEYS:
        value = raw.get(key)
        if value is None:
            continue
        op[key] = value
    if not op.get("target_id"):
        return None
    return op


def normalize_findings(payload: dict) -> dict[str, Any]:
    """Clamp a ``submit_qc_findings`` payload to the contract.

    Findings without a title or issue drop; severity clamps to the valid set
    (default ``medium``); ``element_id`` is kept verbatim (null → ``""``, the
    section-level marker); ``proposed_ops`` is cleaned to a list of op dicts
    (or ``[]`` when the model supplied none / an unclean set). The engine
    dry-runs the ops later — this only shapes them.
    """
    reviewed_checks: list[dict[str, Any]] = []
    for raw in payload.get("reviewed_checks") or []:
        if not isinstance(raw, dict):
            continue
        check = str(raw.get("check") or "").strip()
        if not check:
            continue
        outcome = str(raw.get("outcome") or "").strip().lower()
        if outcome not in QC_CHECK_OUTCOMES:
            # A malformed outcome is not evidence of a passed review task.
            # Drop it so v2 coverage fails closed when no valid checks remain.
            continue
        reviewed_checks.append(
            {
                "check": check,
                "outcome": outcome,
                "notes": str(raw.get("notes") or "").strip(),
                "element_ids": [
                    str(value).strip()
                    for value in (raw.get("element_ids") or [])
                    if str(value).strip()
                ],
                "source_urls": [
                    value.strip()
                    for value in (raw.get("source_urls") or [])
                    if isinstance(value, str) and value.strip()
                ],
            }
        )

    findings: list[dict[str, Any]] = []
    for raw in payload.get("findings") or []:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        issue = str(raw.get("issue") or "").strip()
        if not title or not issue:
            continue
        severity = str(raw.get("severity") or "").strip().lower()
        if severity not in SEVERITIES:
            severity = "medium"
        source_urls = [
            u.strip()
            for u in (raw.get("source_urls") or [])
            if isinstance(u, str) and u.strip()
        ]
        raw_ops = raw.get("proposed_ops")
        proposed_ops: list[dict[str, Any]] = []
        if isinstance(raw_ops, list):
            for entry in raw_ops:
                cleaned = _clean_op(entry)
                if cleaned is not None:
                    proposed_ops.append(cleaned)
        findings.append(
            {
                "title": title,
                "severity": severity,
                "element_id": str(raw.get("element_id") or "").strip(),
                "issue": issue,
                "rationale": str(raw.get("rationale") or "").strip(),
                "source_urls": source_urls,
                "proposed_ops": proposed_ops,
            }
        )
    return {
        "summary": str(payload.get("summary") or "").strip(),
        "reviewed_checks": reviewed_checks,
        "findings": findings,
    }


def normalize_verdict(
    payload: dict, *, has_proposed_ops: bool = True
) -> dict[str, Any]:
    """Clamp a ``submit_qc_verdict`` payload to the verifier contract.

    Unknown severities mean "keep original."  A refuting verdict or a
    finding without proposed operations can never approve those operations,
    even if the model emitted an inconsistent ``ops_adequate=true``.

    ``refutation_evidence`` is normalized to a list of well-formed claims
    only; whether each one actually CHECKS OUT is decided later against
    what this seat retrieved and against the reviewed document (see
    :func:`backend.qc.engine.validate_refutation_evidence`). Claims are
    kept even when malformed entries around them are dropped — the payload
    is untrusted model output, and a bad entry must not cost a good one.
    An upholding verdict carries none: the gate exists for refutations.
    """
    upholds = payload.get("upholds")
    if not isinstance(upholds, bool):
        raise ValueError("QC verdict 'upholds' must be a JSON boolean.")
    ops_adequate = payload.get("ops_adequate")
    if not isinstance(ops_adequate, bool):
        raise ValueError("QC verdict 'ops_adequate' must be a JSON boolean.")
    revised = str(payload.get("revised_severity") or "").strip().lower()
    if revised not in SEVERITIES:
        revised = ""
    return {
        "upholds": upholds,
        "revised_severity": revised,
        "note": str(payload.get("note") or "").strip(),
        "ops_adequate": bool(ops_adequate and upholds and has_proposed_ops),
        "ops_note": str(payload.get("ops_note") or "").strip(),
        "refutation_evidence": (
            [] if upholds else normalize_refutation_evidence(
                payload.get("refutation_evidence")
            )
        ),
    }


def normalize_refutation_evidence(raw: object) -> list[dict[str, str]]:
    """Clamp model-supplied refutation citations to well-formed claims.

    Shape only. A ``source`` needs a nonblank url, a ``document_ref`` a
    nonblank reference; anything else is dropped. Duplicates collapse so a
    seat cannot manufacture weight by repeating one citation.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("type") or "").strip().lower()
        if kind == "source":
            value = str(entry.get("url") or "").strip()
        elif kind == "document_ref":
            value = str(entry.get("reference") or "").strip()
        else:
            continue
        if not value or (kind, value) in seen:
            continue
        seen.add((kind, value))
        out.append(
            {"type": kind, "url": value}
            if kind == "source"
            else {"type": kind, "reference": value}
        )
    return out


def normalize_consolidation(payload: dict) -> dict[str, Any]:
    """Clamp a ``submit_qc_consolidation`` payload to well-formed groups.

    SHAPE ONLY. Whether the groups actually partition the supplied candidates,
    and whether each one obeys the hard compatibility rules, is decided by the
    engine against the bucket it asked about — this cannot know either. A
    malformed entry is dropped rather than repaired, because a group whose
    membership we had to guess is exactly the thing that must fall back to
    singletons instead of quietly merging two different defects.

    Non-integer, boolean and negative member indexes drop (``bool`` is an
    ``int`` subclass, and ``true`` reading as candidate 1 would silently
    misgroup). Duplicates within one group collapse; duplicates ACROSS groups
    are the engine's coverage check, not this.
    """
    groups: list[dict[str, Any]] = []
    for raw in payload.get("groups") or []:
        if not isinstance(raw, dict):
            continue
        raw_indexes = raw.get("member_indexes")
        if not isinstance(raw_indexes, list):
            continue
        members: list[int] = []
        for value in raw_indexes:
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                continue
            if value not in members:
                members.append(value)
        if not members:
            continue
        raw_ops = raw.get("reconciled_ops")
        reconciled_ops: list[dict[str, Any]] = []
        if isinstance(raw_ops, list):
            for entry in raw_ops:
                cleaned = _clean_op(entry)
                if cleaned is not None:
                    reconciled_ops.append(cleaned)
        groups.append(
            {
                "member_indexes": sorted(members),
                "canonical_title": str(raw.get("canonical_title") or "").strip(),
                "canonical_issue": str(raw.get("canonical_issue") or "").strip(),
                "canonical_rationale": str(
                    raw.get("canonical_rationale") or ""
                ).strip(),
                "grouping_rationale": str(
                    raw.get("grouping_rationale") or ""
                ).strip(),
                "reconciled_ops": reconciled_ops,
            }
        )
    return {"groups": groups}


def median_severity(severities: list[str]) -> str:
    """Median of a non-empty severity list by rank; ties round toward severe."""
    ranks = sorted(SEVERITY_RANK[s] for s in severities if s in SEVERITY_RANK)
    if not ranks:
        return "medium"
    n = len(ranks)
    mid = n // 2
    if n % 2 == 1:
        rank = ranks[mid]
    else:
        # Even count: average the two middle ranks, round up (toward severe).
        rank = (ranks[mid - 1] + ranks[mid] + 1) // 2
    return _RANK_SEVERITY.get(rank, "medium")
