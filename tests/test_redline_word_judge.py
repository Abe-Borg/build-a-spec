r"""Real Word judges the redline on your original (optional, Windows only).

Redline on your original, Phase 2 (``docs/plans/REDLINE_ON_ORIGINAL_
2026-09-22.md``). The app proves its own promise before it hands a redline
over: Accept All gives *Export Word (keeps your formatting)*, Reject All
gives the upload back. That proof uses the app's own XML resolver. Here REAL
Microsoft Word resolves the same files, both ways, through the hidden-Word
automation's resolve mode, and ``tests/word_judge.py`` compares what Word
saved with the same Word's save of the formatted export and of the upload.

What runs: every targeted markup shape the redline writes, and every corpus
master under the corpus sweep's own scripted edit mixes
(``tests/test_redline_original.py``) — the redlines that suite proves are the
redlines Word judges. One owned, hidden Word per group.

Without ``BUILD_A_SPEC_WORD_JUDGE=1`` the whole module collects as clean
skips and does no work, the way ``tests/test_docx_visual_regression.py``
does. With it, on Windows with Microsoft Word installed::

    $env:BUILD_A_SPEC_WORD_JUDGE = "1"
    .\.venv\Scripts\python -m pytest -q tests\test_redline_word_judge.py

Every file Word was handed and saved stays under ``artifacts\word-judge\``
(``BUILD_A_SPEC_WORD_JUDGE_DIR`` moves it), with ``report.json`` rewritten
after every group, so a run that stops part way still says what it found.
"""
from __future__ import annotations

import pytest

from tests import word_judge as judge

pytestmark = pytest.mark.skipif(
    not judge.judge_is_configured(),
    reason=judge.JUDGE_SKIP_REASON,
)

_GROUPS = judge.judge_groups()


@pytest.fixture(scope="module")
def report() -> judge.JudgeReport:
    return judge.JudgeReport.from_environment()


@pytest.mark.parametrize("group", _GROUPS, ids=[group.group_id for group in _GROUPS])
def test_word_resolves_the_redline_on_your_original_both_ways(group, report):
    """Word opens each redline, reads every change as Build-a-Spec's, leaves
    none behind, and — up to what its own save writes — Accept All gives
    the formatted export and Reject All the upload, losing only the
    bookmarks a moved copy carried (D-6)."""
    from tools.render_docx_word import resolve_docx

    workspace = report.run_dir / group.slug
    batch = judge.build_judge_cases(group, workspace / "cases")
    if group.group_id.startswith("targeted/"):
        refused = [case for case in batch.cases if isinstance(case, judge.RefusedCase)]
        assert not refused, f"a targeted shape was refused: {refused}"
    verdict = judge.judge_batch(batch, workspace / "word", resolve=resolve_docx)
    report.record_group(verdict)
    assert not verdict.failures, (
        "\n".join(verdict.failures) + f"\nThe full report: {report.path}"
    )


def test_word_and_the_oracle_resolve_words_own_tracked_moves_alike(report):
    """Word's own tracked moves (the Word-saved corpus sample, PR A's
    producer recipe): Word's Accept All and Reject All of the file it wrote
    must match the app's oracle — the resolver Phase 2's native moves will
    lean on. Where the moved bookmark lands each way is recorded as evidence
    for the D-6 decision."""
    from tests.docx_corpus import build_case, corpus_cases
    from tools.render_docx_word import resolve_docx

    case = next(
        (case for case in corpus_cases() if case.case_id == judge.TRACKED_MOVE_CASE_ID),
        None,
    )
    if case is None:
        pytest.skip(
            f"the corpus has no {judge.TRACKED_MOVE_CASE_ID} fixture yet: run the "
            "TrackedMove producer recipe (docs/DOCX_FIDELITY_CORPUS.md)"
        )
    workspace = report.run_dir / "tracked-move-sample"
    sample = build_case(case, workspace / "cases")
    result = judge.judge_tracked_move_sample(
        sample, workspace / "word", resolve=resolve_docx
    )
    report.record_sample(result)
    assert not result["problems"], (
        "\n".join(result["problems"]) + f"\nThe full report: {report.path}"
    )
