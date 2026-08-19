"""The apply_qc_fixes chat tool + the shared QC apply machinery.

The HTTP apply route and the chat tool must run ONE implementation of the
eligibility/freshness/dry-run policy (backend/qc/apply.py); the first tests
here pin the app.py aliases to that single implementation so a refactor that
quietly forks the policy fails loudly rather than drifting.
"""
from __future__ import annotations

import backend.app as app_module
from backend.qc import apply as qc_apply


def test_the_route_and_the_tool_share_one_apply_implementation() -> None:
    """app.py's historical names are aliases of backend/qc/apply.py."""
    assert app_module._qc_source_guard is qc_apply.build_source_guard
    assert (
        app_module._qc_matches_current_inputs is qc_apply.matches_current_inputs
    )
    assert (
        app_module._qc_result_is_audit_complete
        is qc_apply.result_is_audit_complete
    )
    assert (
        app_module._dry_run_qc_apply_findings is qc_apply.dry_run_apply_findings
    )
    assert app_module._source_baseline is qc_apply.source_baseline
    assert app_module._UNSAMPLED is qc_apply.UNSAMPLED


def test_fix_class_matches_the_apply_gates_eligibility_condition() -> None:
    """finding_fix_class is exactly the apply gate's safe-fix condition."""

    class _F:
        ops_semantic_status = "approved"
        ops_valid = True
        proposed_ops = [{"action": "replace"}]

    safe = _F()
    assert qc_apply.finding_fix_class(safe) == qc_apply.FIX_CLASS_SAFE

    rejected = _F()
    rejected.ops_semantic_status = "rejected"
    assert qc_apply.finding_fix_class(rejected) == qc_apply.FIX_CLASS_ADVISORY

    invalid = _F()
    invalid.ops_valid = False
    assert qc_apply.finding_fix_class(invalid) == qc_apply.FIX_CLASS_ADVISORY

    empty = _F()
    empty.proposed_ops = []
    assert qc_apply.finding_fix_class(empty) == qc_apply.FIX_CLASS_ADVISORY
