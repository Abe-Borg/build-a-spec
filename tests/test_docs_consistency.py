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


# Every place that tells someone to run a command on Windows: the five docs
# the venv-name test reads, the live plans (the compaction plan, the
# project workspace, and the research/QC cost program's plan and progress
# file) and the execution record whose commands are still to be run, the
# four profilers' usage docstrings and the PyInstaller spec's build steps. The deep-dive remediation plans are
# finished and keep their commands as written (the Batch 8 decision), and
# CLAUDE.md is append-only history: only its Commands section is read, so a
# note can still quote the broken form it records.
_WINDOWS_COMMAND_DOCS = (
    "README.md",
    "docs/RELEASE_WINDOWS.md",
    "docs/DOCX_FIDELITY.md",
    "docs/DOCX_FIDELITY_CORPUS.md",
    "docs/DOCX_RENDERER_WINDOWS.md",
    "docs/plans/CHAT_HISTORY_COMPACTION_2026-09-22.md",
    "docs/plans/RESEARCH_QC_COST_TIER1_2026-09-23.md",
    "docs/plans/RESEARCH_QC_COST_TIER1_PROGRESS.md",
    "docs/plans/project-workspace/README.md",
    "docs/review-results/2026-09-09/EXECUTION_RECORD.md",
    "tools/chat_history_profile.py",
    "tools/fetch_elision_canary.py",
    "tools/lint_block_profile.py",
    "tools/qc_export_cost_profile.py",
    "packaging/windows/build-a-spec.spec",
)

# A venv path that starts a token. After a separator it is part of a longer
# path (`.\.venv\...`, `C:\work\.venv\...`), which PowerShell runs fine.
# `[\\/]+` also matches the doubled backslashes of a Python string.
_BARE_VENV_PATH = re.compile(r"(?<![\\/\w])\.venv[\\/]+Scripts[\\/]")

# The first word of a command line that is a relative Windows path with no
# `.\` or `..\` in front (`dist\BuildASpec\BuildASpec.exe`).
_BARE_RELATIVE_COMMAND = re.compile(r"^(?!\.\.?\\)[\w.-]+\\")

# Code fences whose lines are Windows commands. A fence labelled for any
# other language (`python`, `ts`, `json`, ...) is not read.
_COMMAND_FENCES = {"", "bat", "batch", "cmd", "powershell", "pwsh", "ps1"}


def _windows_command_sources() -> list[tuple[str, str]]:
    sources = [
        (label, (REPO_ROOT / label).read_text(encoding="utf-8"))
        for label in _WINDOWS_COMMAND_DOCS
    ]
    claude = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    commands = re.search(r"^## Commands\n(.*?)(?=^## )", claude, re.M | re.S)
    assert commands, "CLAUDE.md no longer has a Commands section"
    sources.append(("CLAUDE.md (Commands)", commands.group(1)))
    return sources


def _fenced_command_lines(text: str) -> list[str]:
    lines: list[str] = []
    fence: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            fence = line[3:].strip().lower() if fence is None else None
            continue
        if fence in _COMMAND_FENCES and line:
            lines.append(line)
    return lines


def test_the_docs_windows_commands_run_in_powershell() -> None:
    """PowerShell, the default Windows terminal and the owner's, does not
    run a program from a relative path that lacks a leading `.\\`: it reads
    `.venv` in `.venv\\Scripts\\python` as a module name and stops with
    "The module '.venv' could not be loaded" (CouldNotAutoLoadModule).
    `.\\.venv\\Scripts\\...` runs in both PowerShell and Command Prompt, so
    every documented venv command carries it; a frozen-app or other
    relative command at the start of a command-fence line does too. And no
    example continues a line with `^`: that is Command Prompt syntax, and
    PowerShell passes the caret on as an argument and runs the next line as
    a command of its own."""
    for label, text in _windows_command_sources():
        bare_venv = [ln.strip() for ln in text.splitlines() if _BARE_VENV_PATH.search(ln)]
        assert not bare_venv, (
            f"{label} runs a venv command without the leading .\\ "
            f"(PowerShell cannot run it): {bare_venv}"
        )
        carets = [ln.strip() for ln in text.splitlines() if re.search(r"\s\^$", ln.rstrip())]
        assert not carets, (
            f"{label} continues a command with ^, which only Command Prompt "
            f"reads as a continuation — put it on one line: {carets}"
        )
        bare_commands = [
            ln
            for ln in _fenced_command_lines(text)
            if _BARE_RELATIVE_COMMAND.match(ln.split()[0])
        ]
        assert not bare_commands, (
            f"{label} starts a command with a relative path and no .\\ "
            f"(PowerShell cannot run it): {bare_commands}"
        )


def test_the_release_runbook_describes_the_live_import_contract() -> None:
    """The v1.14.0 import lands detached: no permission sweep, no intent
    dialog, no "Preserve original formatting" choice. QA rows that told a
    tester to verify those were unperformable for two releases."""
    assert not re.search(
        r"import intent dialog|Preserve original formatting|permission sweep",
        RUNBOOK,
        re.IGNORECASE,
    ), "docs/RELEASE_WINDOWS.md still describes the retired import contract"
