"""Final QC on Fable 5 (Batch 4): the spare-no-expense pre-issue review.

A user-triggered lens fan-out + adversarial verification pass over one draft
section, producing verified findings each with a ready-to-apply fix, in an
accept/dismiss queue. The ``code_compliance`` + ``completeness`` lenses
strictly supersede the Phase 5 compliance audit (deprecated; endpoints
retained). See ``engine.py`` for the pipeline notes.
"""
from .engine import QCFanoutError, QCFinding, QCResult, QCVerdict, run_final_qc
from .runner import QCRunner

__all__ = [
    "QCFanoutError",
    "QCFinding",
    "QCResult",
    "QCRunner",
    "QCVerdict",
    "run_final_qc",
]
