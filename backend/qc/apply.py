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
from .op_conflicts import (
    canonical_qc_operation,
    plan_qc_operation_batch,
    qc_operation_identity,
)

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
            # Attached documents are review inputs now, so detaching or
            # replacing one makes a retained report stale exactly the way a
            # document edit does.
            reference_docs=list(session.references.docs),
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


class QCChatApplyError(ValueError):
    """A whole-call refusal of the apply_qc_fixes chat tool.

    Becomes an ``is_error`` tool result for the model to relay or correct —
    never a turn failure (the apply_spec_edits posture).
    """


#: The chat tool through which the model applies verified Final QC fixes —
#: static bytes on purpose: tools precede the system prompt in the cached
#: prefix. The description restates the approval contract because the tool
#: definition is what the model reads at the moment of use.
APPLY_QC_FIXES_TOOL: dict[str, Any] = {
    "name": "apply_qc_fixes",
    "description": (
        "Apply verified Final QC fixes by finding id — ONLY after the user "
        "has explicitly approved applying them in this conversation. Pass "
        "every approved finding id in ONE call, and make it the FIRST "
        "action of the turn: any earlier document edit this turn makes the "
        "review stale and this tool refuses. Each finding's exact "
        "panel-approved operations are applied as part of this turn's one "
        "undoable step and its audit disposition is recorded on commit. "
        "Findings without a verified safe fix are skipped and reported in "
        "the result — never re-type a QC fix through apply_spec_edits."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "finding_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "The approved Final QC finding ids (from the FINAL QC "
                    "REVIEW context block), all in one call."
                ),
            }
        },
        "required": ["finding_ids"],
    },
}


def _chat_finding_ids(raw_input: Any) -> list[str]:
    if not isinstance(raw_input, dict):
        raise QCChatApplyError(
            "apply_qc_fixes expects an object with a finding_ids list."
        )
    raw_ids = raw_input.get("finding_ids")
    if not isinstance(raw_ids, list):
        raise QCChatApplyError("finding_ids must be a list of finding ids.")
    ids = [i.strip() for i in raw_ids if isinstance(i, str) and i.strip()]
    if not ids:
        raise QCChatApplyError(
            "Name at least one finding id from the FINAL QC REVIEW block."
        )
    return ids


def stage_chat_apply(session, raw_input: Any) -> dict[str, Any]:
    """Validate and dry-run an apply_qc_fixes call against the live turn.

    Runs under the turn's ``owned_model_turn_guard`` (the dispatcher's
    lock), against the turn's PROVISIONAL tree — which is exactly what makes
    the apply-first contract self-enforcing: ``matches_current_inputs``
    fingerprints ``session.doc.doc``, so any edit earlier in the turn reads
    as a mismatch and the call is refused, the same answer the HTTP route
    gives a stale result. The freshness read is non-blocking on purpose: a
    pending capability sweep must read conservatively as stale rather than
    park the whole turn behind a minutes-long derivation (the chat-freeze
    class of bug), and the refusal says the panel's Apply — which settles
    the sweep — is the way through.

    Returns the staged batch: merged per-finding ``outcomes``, the
    disposition ``skipped_events``, the ``applied_ids`` the caller may mark
    applied at commit, the canonical ``combined_ops`` to run through the
    session's transactional apply, and ``result_ref`` — the exact result
    object the outcomes describe, re-checked by identity at commit.
    """
    finding_ids = _chat_finding_ids(raw_input)
    result = getattr(session.qc, "result", None)
    if result is None:
        raise QCChatApplyError(
            "No Final QC result is retained for this session — run Final QC "
            "from the panel first."
        )
    if session.qc.status == "running" or session.qc.is_settling:
        raise QCChatApplyError(
            "A Final QC run is active or settling — fixes cannot be applied "
            "until it finishes."
        )
    if not result_is_audit_complete(result):
        raise QCChatApplyError(
            "The retained Final QC result does not meet the current "
            "audit-completeness contract, so its fixes are not actionable. "
            "Re-run Final QC from the panel."
        )
    if not matches_current_inputs(session, result, block=False):
        raise QCChatApplyError(
            "The Final QC review no longer matches the document and inputs "
            "as they stand — either the document changed after the review "
            "(including an edit earlier in THIS turn: apply_qc_fixes must "
            "be the turn's first action), another review input moved, or "
            "the imported-source permission check has not settled yet. "
            "Nothing was applied. Re-run Final QC to re-verify the "
            "findings, or use the Final QC panel's Apply, which waits out "
            "the permission check."
        )
    outcomes, skipped_events, eligible = select_apply_candidates(
        result, finding_ids
    )
    working = SpecSection.from_dict(session.doc.doc.to_dict())
    batch = plan_qc_operation_batch(working, eligible)
    if batch.conflicts:
        conflicting_ids = sorted(
            {
                finding_id
                for conflict in batch.conflicts
                for finding_id in conflict["finding_ids"]
            }
        )
        write_keys = sorted(
            {
                write_key
                for conflict in batch.conflicts
                for write_key in conflict["write_keys"]
            }
        )
        raise QCChatApplyError(
            "The selected Final QC fixes contain conflicting operations; "
            "nothing was applied. Conflicting finding ids: "
            + ", ".join(conflicting_ids)
            + " (write keys: "
            + ", ".join(write_keys)
            + "). Retry with a non-conflicting subset, or leave the "
            "conflict to the user in the Final QC panel."
        )
    combined_ops, applied_ids, stale_errors, _counts, _validated = (
        dry_run_apply_findings(working, eligible)
    )
    for finding_id in applied_ids:
        outcomes[finding_id] = "applied"
    for finding_id in stale_errors:
        outcomes[finding_id] = "stale"
        skipped_events.append(
            (
                finding_id,
                "apply_stale",
                "The proposed operations no longer applied cleanly in "
                "the selected batch; nothing from this finding was "
                "applied.",
            )
        )
    return {
        "outcomes": outcomes,
        "skipped_events": skipped_events,
        "applied_ids": applied_ids,
        "combined_ops": combined_ops,
        # Per-finding (finding_id, proposed_ops) for the applied set — the
        # dispatcher maps each finding's canonical op identities onto the
        # live apply's echoes to capture its fix-survival evidence.
        "eligible": eligible,
        "result_ref": result,
    }


def finding_evidence_keys(
    eligible: list[tuple[str, list[dict[str, Any]]]],
    applied_ids: list[str],
    combined_ops: list[dict[str, Any]],
    echoes: list[dict[str, Any]],
) -> dict[str, list[tuple[str, str]]]:
    """Map each applied finding to the evidence keys its own ops establish.

    ``echoes`` are the live apply's returned records, 1:1 with
    ``combined_ops`` — the deduped canonical batch. A duplicate-only finding
    (all its ops were another finding's identical ops) maps onto the SAME
    echoes through the shared operation identity, so its applied record is
    voided by exactly the later edits that void the finding it duplicated.
    """
    echo_by_identity = {
        qc_operation_identity(op): echo
        for op, echo in zip(combined_ops, echoes)
    }
    applied = set(applied_ids)
    keys: dict[str, list[tuple[str, str]]] = {}
    for finding_id, proposed_ops in eligible:
        if finding_id not in applied:
            continue
        finding_keys: list[tuple[str, str]] = []
        for op in proposed_ops:
            identity = qc_operation_identity(canonical_qc_operation(op))
            echo = echo_by_identity.get(identity)
            if echo is not None:
                finding_keys.extend(evidence_keys_for_echo(echo))
        keys[finding_id] = list(dict.fromkeys(finding_keys))
    return keys


def _element_own_state(section: Any, uid: str) -> Any:
    """The node's OWN audited fields (never its children), or None if absent.

    A tiny local walker rather than an import of ``spec_doc.model._find``
    (private, and the copy-don't-import posture): the shape it reads is the
    stable serialized vocabulary. Children are deliberately excluded — a
    later edit adding a sub-paragraph under a fixed provision has not
    changed what the fix wrote.
    """
    if uid == "sec":
        return {"kind": "sec", "number": section.number, "title": section.title}
    for part in section.parts:
        if part.uid == uid:
            return {"kind": "pt", "title": part.title}
        for article in part.articles:
            if article.uid == uid:
                return {"kind": "a", "title": article.title}
            stack = list(article.paragraphs)
            while stack:
                p = stack.pop()
                if p.uid == uid:
                    return {
                        "kind": "p",
                        "text": p.text,
                        "status": p.status,
                        "source_item_id": p.source_item_id,
                    }
                stack.extend(p.children)
    return None


def _element_position(section: Any, uid: str) -> Any:
    """(parent uid, index among siblings) for a body node, or None."""
    for part in section.parts:
        for index, article in enumerate(part.articles):
            if article.uid == uid:
                return (part.uid, index)
            stack = [(article.uid, i, p) for i, p in enumerate(article.paragraphs)]
            while stack:
                parent_uid, index_in_parent, p = stack.pop()
                if p.uid == uid:
                    return (parent_uid, index_in_parent)
                stack.extend(
                    (p.uid, i, child) for i, child in enumerate(p.children)
                )
    return None


def evidence_keys_for_echo(echo: dict[str, Any]) -> list[tuple[str, str]]:
    """The fix-survival evidence keys one applied-op echo establishes.

    Applied echoes carry ``action`` + the affected ``id`` (server-assigned
    for adds). Content ops watch the element's own fields; a ``move``
    additionally watches its position; section-metadata ops watch exactly
    the map entry they wrote. A ``delete``'s evidence is the element's
    absence — the same ``field`` key, whose captured value is None.
    """
    action = str(echo.get("action", "") or "")
    uid = str(echo.get("id", "") or "")
    if action == "set_standard_edition":
        return [("std", str(echo.get("standard", "") or ""))]
    if action == "set_standard_suppressed":
        return [("sup", str(echo.get("standard", "") or ""))]
    if action == "set_project_identity":
        return [("identity", "sec")]
    if action == "set_project_profile":
        return [("profile", "sec")]
    if not uid:
        return []
    if action == "move":
        return [("field", uid), ("pos", uid)]
    return [("field", uid)]


# Distinguishes "suppression entry absent" from "present with empty reason".
_ABSENT = "\x00absent"


def evidence_value(section: Any, key: tuple[str, str]) -> Any:
    """Current value of one evidence key against ``section``."""
    kind, ref = key
    if kind == "std":
        entry = section.edition_overrides.get(ref)
        return dict(entry) if isinstance(entry, dict) else entry
    if kind == "sup":
        return (
            section.suppressed_standards[ref]
            if ref in section.suppressed_standards
            else _ABSENT
        )
    if kind == "identity":
        return dict(section.project_identity)
    if kind == "profile":
        return dict(section.project_profile)
    if kind == "pos":
        return _element_position(section, ref)
    return _element_own_state(section, ref)



def capture_fix_evidence(
    section: Any, echoes: list[dict[str, Any]]
) -> dict[tuple[str, str], Any]:
    """Snapshot what a batch of applied fixes wrote, keyed for later replay.

    Taken from the tree IMMEDIATELY after the fixes applied; the commit
    block re-reads the same keys from the final tree and marks a finding
    applied only when every key it owns still matches — a later edit in the
    same turn that changed a fixed provision voids that finding's applied
    record rather than minting an audited success for a remedy the
    committed document does not contain.
    """
    evidence: dict[tuple[str, str], Any] = {}
    for echo in echoes:
        for key in evidence_keys_for_echo(echo):
            evidence[key] = evidence_value(section, key)
    return evidence


def fix_survives(
    section: Any,
    evidence: dict[tuple[str, str], Any],
    keys: list[tuple[str, str]],
) -> bool:
    """Whether every evidence key a finding owns still reads as captured."""
    return all(evidence_value(section, key) == evidence.get(key) for key in keys)


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
