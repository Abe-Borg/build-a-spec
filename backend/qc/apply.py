"""Shared Final-QC apply machinery: eligibility, freshness, and dry-run.

Extracted from ``backend/app.py`` (verbatim-in-substance) so the chat tool in
``llm/conversation.py`` can reach the SAME implementation the HTTP apply
route uses — app.py imports conversation, so the shared logic has to live
below both. app.py keeps assignment aliases under its historical private
names (``_qc_source_guard = apply.build_source_guard`` …) because route
bodies resolve them as module globals and several tests monkeypatch or
import them from ``backend.app``; the aliases are the seam, this module is
the one implementation.

Nothing here takes a lock or performs I/O beyond what the session object it
is handed does; callers own their own locking discipline (the route holds
``session_state_guard``, the chat tool runs under ``owned_model_turn_guard``).
"""
from __future__ import annotations

from typing import Any

from .. import settings
from ..spec_doc.model import SpecSection, apply_edits
from ..spec_doc import SpecEditError
from ..spec_doc.source_mapping import SourceBodyMap
from ..spec_doc.source_patch import (
    SourcePatchError,
    source_capability_summary,
)
from .engine import (
    QC_PROTOCOL_VERSION,
    QC_REPORT_SCHEMA_VERSION,
    QCSourceGuard,
)
from .op_conflicts import canonical_qc_operation, qc_operation_identity

# Sentinel distinguishing "the caller did not pre-sample" from a genuine
# pre-sampled None (a session that is not source-backed).
UNSAMPLED: Any = object()

#: The one "can this finding's fix be executed" vocabulary. The apply
#: eligibility gate, the chat context block, and the debrief facts all read
#: it from here so their wording can never drift from what apply enforces.
FIX_CLASS_SAFE = "safe_fix"
FIX_CLASS_ADVISORY = "advisory"


def _effective_discipline(session) -> str:
    """Versioned project discipline, falling back to legacy session state.

    Mirrors ``llm.conversation.effective_discipline`` byte-for-byte in
    behavior; copied rather than imported because conversation.py imports
    this package, so importing it back would be a cycle. Both read the same
    two duck-typed fields and the contract is pinned by the discipline tests.
    """
    identity = getattr(session.doc.doc, "project_identity", {}) or {}
    current = str(identity.get("discipline", "") or "").strip()
    return current or session.discipline


def source_baseline(session) -> SpecSection | None:
    """Return the immutable imported semantic baseline, when still present."""
    index = session.doc.baseline_index
    if index is None or not 0 <= index < len(session.doc.versions):
        return None
    try:
        return SpecSection.from_dict(session.doc.versions[index])
    except (TypeError, ValueError):  # pragma: no cover - store validates on load
        return None


def build_source_guard(
    session, *, block: bool = False, capability_report: Any = UNSAMPLED
) -> QCSourceGuard | None:
    """Capture immutable source inputs for the exact QC document snapshot.

    Presence means the runner must enforce source preservation. An active
    source-backed session with malformed or missing context still returns a
    required, incomplete guard so proposal validation fails closed.

    ``block`` is set only when starting a run, which must reason from real
    permissions. The freshness comparisons that reach here from the hot
    ``/api/readiness`` and ``/api/qc/status`` polls leave it False: they must
    never wait on the sweep, and a not-yet-derived capability summary simply
    reads as "the recorded inputs no longer match", i.e. stale. That is the
    conservative answer, and after an import (which requires an empty
    document) or a body change (which makes a prior QC stale anyway) it is
    also the correct one.

    ``capability_report`` lets a caller that must derive SEVERAL facts from
    ONE capability state pre-sample it and thread it through — the background
    warm publishes under its own lock, not the session guard, so two samples
    inside one request can straddle the publish and disagree (the QC export's
    coherence bug, caught in review on PR #116).
    """
    # Use the same active-branch/source-less boundary as manual/model edits
    # and the capability payload. In particular, a legacy JSON project may
    # retain an import baseline while intentionally carrying neither source
    # bytes nor a source map; QC for that project remains semantic-only.
    if capability_report is UNSAMPLED:
        capability_report = session.source_edit_capabilities(block=block)
    if capability_report is None:
        return None

    source_map = getattr(session, "source_docx_map", None)
    baseline_index = session.doc.baseline_index
    baseline_valid = (
        not isinstance(baseline_index, bool)
        and isinstance(baseline_index, int)
        and 0 <= baseline_index < len(session.doc.versions)
    )
    if baseline_valid and session.doc.index < baseline_index:
        # The import was undone. Match SessionState.apply_doc_edits: a new
        # pre-import branch is not constrained by the abandoned source.
        return None
    baseline = source_baseline(session) if baseline_valid else None
    context = None
    if baseline is not None:
        try:
            context = session.ensure_source_patch_context(baseline=baseline)
        except SourcePatchError:
            # A required guard with no context fails closed in the QC engine.
            pass
    capability_summary = source_capability_summary(
        capability_report,
        session.doc.doc,
    )
    return QCSourceGuard(
        required=True,
        source_bytes=(
            session.source_docx_bytes
            if isinstance(session.source_docx_bytes, bytes)
            else None
        ),
        source_map=(
            source_map if isinstance(source_map, SourceBodyMap) else None
        ),
        baseline=baseline,
        context=context,
        capability_summary=capability_summary,
    )


def matches_current_inputs(
    session, result, *, block: bool = False, source_guard: Any = UNSAMPLED
) -> bool:
    """Server-authoritative freshness over document + all QC inputs.

    ``block`` decides whether the imported-source half of the comparison may
    wait for the background permission sweep. Set it on paths that ACT on the
    answer — applying fixes — because a not-yet-derived capability summary
    reads as a mismatch, i.e. "stale", and refusing a fix for a sweep that
    simply has not finished would be wrong. Leave it False on the hot
    ``/api/readiness`` and ``/api/qc/status`` polls, where waiting minutes on
    a large master is exactly the freeze this design removes and the
    conservative "assume stale" answer is harmless.

    ``source_guard`` lets a caller thread ONE pre-built guard through every
    comparison it makes, so all its answers describe the same capability
    state (see ``build_source_guard``); ``block`` is ignored when it is given.
    """
    return bool(
        result is not None
        and result.matches_inputs(
            session.doc.index,
            session.doc.doc,
            session.research.profile_result,
            session.module,
            _effective_discipline(session),
            (
                build_source_guard(session, block=block)
                if source_guard is UNSAMPLED
                else source_guard
            ),
            model=settings.QC_MODEL,
            max_tokens=settings.QC_MAX_TOKENS,
        )
    )


def result_is_audit_complete(result) -> bool:
    """Whether a retained result meets the actionable Final-QC contract."""
    return bool(
        result is not None
        and result.schema_version == QC_REPORT_SCHEMA_VERSION
        and result.protocol_version == QC_PROTOCOL_VERSION
        and result.input_fingerprint
        and result.input_manifest
        and result.execution_status == "complete"
        and result.is_complete()
    )


def finding_fix_class(finding) -> str:
    """Classify one finding's fix: executable safe fix vs advisory-only.

    ``FIX_CLASS_SAFE`` is exactly the apply gate's eligibility condition
    (panel-approved semantics + mechanical validity + a non-empty operation
    set); everything else is advisory — the finding is real but its remedy
    needs a human or an ordinary drafting edit. Disposition status (open /
    applied / dismissed) is deliberately NOT part of the class: it describes
    what happened to the finding, not what its fix is.
    """
    if (
        getattr(finding, "ops_semantic_status", "") == "approved"
        and getattr(finding, "ops_valid", False)
        and getattr(finding, "proposed_ops", None)
    ):
        return FIX_CLASS_SAFE
    return FIX_CLASS_ADVISORY


def select_apply_candidates(
    result, finding_ids: list[str]
) -> tuple[
    dict[str, str],
    list[tuple[str, str, str]],
    list[tuple[str, list[dict[str, Any]]]],
]:
    """Split requested finding ids into apply candidates and recorded skips.

    The per-finding eligibility policy of ``POST /api/qc/apply``, shared with
    the ``apply_qc_fixes`` chat tool so the two paths cannot drift. Returns
    ``(outcomes, skipped_events, eligible_findings)`` where ``outcomes`` maps
    every requested id to its (so far) outcome, ``skipped_events`` carries
    the disposition-outcome records for the skipped ones, and
    ``eligible_findings`` is the ordered ``(finding_id, proposed_ops)`` list
    for conflict planning and the dry-run.
    """
    outcomes: dict[str, str] = {}
    skipped_events: list[tuple[str, str, str]] = []
    eligible_findings: list[tuple[str, list[dict[str, Any]]]] = []
    selected_ids = list(dict.fromkeys(finding_ids))
    for finding_id in selected_ids:
        finding = result.finding(finding_id)
        if finding is None:
            outcomes[finding_id] = "unknown"
            continue
        if finding_fix_class(finding) != FIX_CLASS_SAFE:
            outcomes[finding_id] = "no_ops"
            skipped_events.append(
                (
                    finding_id,
                    "apply_no_ops",
                    "No semantically approved and mechanically validated "
                    "operations were available.",
                )
            )
            continue
        if finding.status == "applied":
            outcomes[finding_id] = "already_applied"
            skipped_events.append(
                (
                    finding_id,
                    "apply_already_applied",
                    "Finding was already marked applied.",
                )
            )
            continue
        if finding.status != "open":
            outcomes[finding_id] = "not_open"
            skipped_events.append(
                (
                    finding_id,
                    "apply_not_open",
                    f"Finding disposition is {finding.status!r}; only open "
                    "findings may be applied.",
                )
            )
            continue
        eligible_findings.append((finding_id, finding.proposed_ops))
    return outcomes, skipped_events, eligible_findings


def dry_run_apply_findings(
    working: SpecSection,
    findings: list[tuple[str, list[dict[str, Any]]]],
) -> tuple[
    list[dict[str, Any]],
    list[str],
    dict[str, str],
    dict[str, int],
    SpecSection,
]:
    """Dry-run ordered findings with the same dedupe used by mutation.

    Returns canonical operations, findings that can be dispositioned as
    applied, per-finding stale errors, and each finding's contribution to the
    unique operation batch. A duplicate-only finding is still applyable: the
    shared operation executes once while every finding that owns it can be
    dispositioned.
    """
    combined_ops: list[dict[str, Any]] = []
    applyable_ids: list[str] = []
    stale_errors: dict[str, str] = {}
    operation_counts: dict[str, int] = {}
    successful_identities: set[str] = set()
    for finding_id, proposed_ops in findings:
        normalized = [
            canonical_qc_operation(operation) for operation in proposed_ops
        ]
        local_identities: set[str] = set()
        novel_ops: list[dict[str, Any]] = []
        for operation in normalized:
            identity = qc_operation_identity(operation)
            if (
                identity in successful_identities
                or identity in local_identities
            ):
                continue
            local_identities.add(identity)
            novel_ops.append(operation)
        try:
            if novel_ops:
                working, _applied = apply_edits(working, novel_ops)
        except SpecEditError as exc:
            stale_errors[finding_id] = str(exc)
            operation_counts[finding_id] = 0
            continue
        combined_ops.extend(novel_ops)
        successful_identities.update(local_identities)
        applyable_ids.append(finding_id)
        operation_counts[finding_id] = len(novel_ops)
    return (
        combined_ops,
        applyable_ids,
        stale_errors,
        operation_counts,
        working,
    )
