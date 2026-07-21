"""Compliance audit of the draft against the researched profile (Phase 5).

Trust model ported from Claude-Spec-Critic ``src/compliance/`` — grounded
items control, unverified items advise, process advisories are excluded.
See ``checker.py`` for the port notes.
"""
from .checker import (
    COVERAGE_STATUSES,
    ComplianceAuditError,
    run_compliance_audit,
)
from .runner import AuditRunner

__all__ = [
    "AuditRunner",
    "COVERAGE_STATUSES",
    "ComplianceAuditError",
    "run_compliance_audit",
]
