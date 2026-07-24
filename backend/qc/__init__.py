"""Final QC on Fable 5 (Batch 4): the spare-no-expense pre-issue review.

A user-triggered lens fan-out + adversarial verification pass over one draft
section, producing a versioned, auditable report plus an accept/dismiss fix
queue. The ``code_compliance`` + ``completeness`` lenses
strictly supersede the Phase 5 compliance audit (deprecated; endpoints
retained). See ``engine.py`` for the pipeline notes.
"""
from .engine import (
    QCFanoutError,
    QCDispositionEvent,
    QCFinding,
    QCReviewedCheck,
    QCResult,
    QCSourceRecord,
    QCVerdict,
    build_qc_input_manifest,
    qc_input_fingerprint,
    qc_version_fingerprint,
    run_final_qc,
)
from .runner import QCRunner

__all__ = [
    "QCFanoutError",
    "QCDispositionEvent",
    "QCFinding",
    "QCReviewedCheck",
    "QCResult",
    "QCRunner",
    "QCSourceRecord",
    "QCVerdict",
    "build_qc_input_manifest",
    "qc_input_fingerprint",
    "qc_version_fingerprint",
    "run_final_qc",
]
