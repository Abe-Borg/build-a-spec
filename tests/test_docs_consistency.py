"""The docs describe the code that ships.

Every pin here is a claim the README or the release runbook makes that the
code can silently outgrow: an env knob the code reads but the Configuration
table never names, the Node version CI actually pins, the venv name the
setup instructions create, and the import contract the release QA rows
tell a tester to verify. Each one drifted at least once before this file
existed (2026-09 docs audit); a grep found it, and a grep is what this is.
Hermetic — pure file reads.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
README = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
RUNBOOK = (REPO_ROOT / "docs" / "RELEASE_WINDOWS.md").read_text(encoding="utf-8")


def _app_env_knobs() -> set[str]:
    """Every BUILD_A_SPEC_* name the shipped app reads.

    Scans ``backend/`` and ``main.py`` only: ``tools/`` and
    ``packaging/`` carry developer- and CI-only knobs (the DOCX renderer
    harness, the frozen-app self-check output path) that the user-facing
    Configuration table deliberately does not list.
    """
    names: set[str] = set()
    sources = [REPO_ROOT / "main.py", *(REPO_ROOT / "backend").rglob("*.py")]
    for path in sources:
        for name in re.findall(
            r"BUILD_A_SPEC_[A-Z0-9_]+", path.read_text(encoding="utf-8")
        ):
            # A docstring naming a FAMILY (`BUILD_A_SPEC_QC_VERIFIERS_*`)
            # leaves a trailing underscore; that is prose, not a knob.
            if not name.endswith("_"):
                names.add(name)
    return names


def test_the_readme_documents_every_app_env_knob() -> None:
    """Ten knobs were missing from the Configuration table at the audit —
    the whole batched-verification and per-phase-effort surface among them,
    and AUTO_DEBRIEF, which fires a billed model turn without a click."""
    knobs = _app_env_knobs()
    assert knobs, "the scan found no knobs at all — the regex or the paths broke"
    # Whole-name match: `BUILD_A_SPEC_QC_EFFORT` must not be satisfied by a
    # row for `BUILD_A_SPEC_QC_EFFORT_X` (the first revert-proof of this test
    # passed for exactly that reason).
    missing = sorted(
        name
        for name in knobs
        if not re.search(rf"(?<![A-Z0-9_]){re.escape(name)}(?![A-Z0-9_])", README)
    )
    assert not missing, (
        "env knobs the app reads but README.md never names: "
        + ", ".join(missing)
        + " — add a row to the Configuration table"
    )


def test_the_docs_name_the_node_the_ci_pins() -> None:
    """`npm test` runs `node --test` over the .ts sources and depends on
    type stripping, so the Node floor is whatever ci.yml pins — the README
    and the runbook said 20+ for two major versions after CI moved to 22."""
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    match = re.search(r"node-version:\s*'(\d+)'", ci)
    assert match, "ci.yml no longer pins a Node major version"
    pinned = match.group(1)
    for label, text in (("README.md", README), ("docs/RELEASE_WINDOWS.md", RUNBOOK)):
        assert f"Node {pinned}+" in text, f"{label} must name Node {pinned}+"
        stale = {m for m in re.findall(r"Node (\d+)\+", text) if m != pinned}
        assert not stale, f"{label} still names Node {sorted(stale)}+"


def test_the_docs_use_the_venv_name_the_setup_creates() -> None:
    """Setup runs `python -m venv .venv`; no runbook command may then
    invoke `venv\\Scripts\\python`, which does not exist. The README and the
    release runbook carry the setup; the three DOCX docs only invoke it."""
    undotted = re.compile(r"(?<![.\w])venv\\Scripts")
    for label, text in (("README.md", README), ("docs/RELEASE_WINDOWS.md", RUNBOOK)):
        assert "python -m venv .venv" in text, f"{label} setup no longer creates .venv"
    invoking = [
        "README.md",
        "docs/RELEASE_WINDOWS.md",
        "docs/DOCX_FIDELITY.md",
        "docs/DOCX_FIDELITY_CORPUS.md",
        "docs/DOCX_RENDERER_WINDOWS.md",
    ]
    for label in invoking:
        text = (REPO_ROOT / label).read_text(encoding="utf-8")
        hits = [ln for ln in text.splitlines() if undotted.search(ln)]
        assert not hits, f"{label} invokes an undotted venv: {hits}"


def test_the_release_runbook_describes_the_live_import_contract() -> None:
    """The v1.14.0 import lands detached: no permission sweep, no intent
    dialog, no "Preserve original formatting" choice. QA rows that told a
    tester to verify those were unperformable for two releases."""
    assert not re.search(
        r"import intent dialog|Preserve original formatting|permission sweep",
        RUNBOOK,
        re.IGNORECASE,
    ), "docs/RELEASE_WINDOWS.md still describes the retired import contract"
