"""Final-QC lens definitions, output tools, and payload normalization.

Batch 4. The QC pass is a lens fan-out (five independent Fable 5 calls)
followed by an adversarial verification panel per finding; this module owns
the lens briefs, the two strict output tools (``submit_qc_findings`` /
``submit_qc_verdict``), and the parse-time normalization that clamps model
output to the contract.

Schema conventions are copied from :mod:`backend.research.schema` — the
strict-mode subset (every property required, optionals nullable, no numeric
constraints; clamp at parse). ``strict: true`` attaches only for the known
strict-capable models (Fable 5 is one; see ``_STRICT_CAPABLE_MODELS``).
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

SEVERITIES: tuple[str, ...] = ("critical", "high", "medium", "low")
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
            "named two ways). Anchor each finding to the element ids "
            "involved."
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
            "requirements. If no research profile is supplied, skip profile "
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
            "at an [UNVERIFIED] research item. Do NOT propose mass status "
            "upgrades — flag the specific blocks that need a human decision."
        ),
    ),
)

QC_LENS_BY_ID: dict[str, QCLens] = {lens.lens_id: lens for lens in QC_LENSES}


# ---------------------------------------------------------------------------
# proposed_ops item schema (mirrors the apply_spec_edits op vocabulary)
# ---------------------------------------------------------------------------

# The op actions a QC fix may use. set_project_profile is excluded — QC never
# touches the project identity.
QC_OP_ACTIONS: tuple[str, ...] = (
    "add_article",
    "add_paragraph",
    "replace",
    "delete",
    "set_status",
    "set_standard_edition",
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
    },
}


# ---------------------------------------------------------------------------
# submit_qc_findings (phase 1 output)
# ---------------------------------------------------------------------------

QC_FINDINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "findings"],
    "properties": {
        "summary": {
            "type": "string",
            "description": "One or two sentences on what this lens found overall.",
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

QC_VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["upholds", "revised_severity", "note"],
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
            "enum": [*SEVERITIES, None],
            "description": "A corrected severity, or null to keep the original.",
        },
        "note": {
            "type": "string",
            "description": "One-line rationale for the verdict.",
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
        "findings": findings,
    }


def normalize_verdict(payload: dict) -> dict[str, Any]:
    """Clamp a ``submit_qc_verdict`` payload; unknown severity → keep original."""
    revised = str(payload.get("revised_severity") or "").strip().lower()
    if revised not in SEVERITIES:
        revised = ""
    return {
        "upholds": bool(payload.get("upholds")),
        "revised_severity": revised,
        "note": str(payload.get("note") or "").strip(),
    }


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
