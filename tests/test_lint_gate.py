"""The lint gate, under pytest.

CI runs ``ruff check .`` as its own step (before the suite), but the release
workflow does not, and a contributor's habit is ``pytest -q`` — so the same
check runs here wherever the pinned ``ruff`` wheel is installed (it is in
``requirements.txt`` beside pytest). Without the wheel the test SKIPS and says
so rather than failing a venv that never installed it; with it, a local run
reports exactly the red a PR would. The ruff wheel ships ``ruff/__main__.py``
(an exec of the bundled binary), so ``python -m ruff`` is the portable spelling
on Windows and Linux alike.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_the_tree_is_ruff_clean():
    if importlib.util.find_spec("ruff") is None:
        pytest.skip("the ruff wheel is not installed in this environment")
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "."],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "ruff check . reported findings:\n" + result.stdout + result.stderr
    )
