"""requirements.txt is an exact lock generated from requirements.in.

The intent (names and accepted ranges) lives in requirements.in; every
install — CI, the Windows release build, a source checkout — uses the lock,
so a build is reproducible and a dependency upgrade arrives as a reviewed diff
instead of whatever PyPI served that day. The same holds for the release
build's tooling (packaging/windows/build-requirements.in -> .txt). Hermetic:
pure file reads.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_NAME = r"[A-Za-z0-9][A-Za-z0-9._-]*"


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_lines(path: Path) -> list[str]:
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            lines.append(line)
    return lines


def _pins(path: Path) -> dict[str, str]:
    pins = {}
    for line in _requirement_lines(path):
        spec = line.split(";", 1)[0].strip()
        match = re.fullmatch(rf"({_NAME})==(\S+)", spec)
        assert match, f"{path.name}: {line!r} is not an exact pin"
        name = _canonical(match.group(1))
        assert name not in pins, f"{path.name} pins {name} twice"
        pins[name] = match.group(2)
    return pins


def _declared(path: Path) -> list[str]:
    names = []
    for line in _requirement_lines(path):
        if line.startswith("-"):
            continue
        match = re.match(rf"({_NAME})", line)
        assert match, f"{path.name}: cannot read {line!r}"
        names.append(_canonical(match.group(1)))
    return names


def test_the_lock_pins_every_line_exactly():
    pins = _pins(REPO_ROOT / "requirements.txt")
    assert len(pins) > 30


def test_every_declared_dependency_is_in_the_lock():
    pins = _pins(REPO_ROOT / "requirements.txt")
    declared = _declared(REPO_ROOT / "requirements.in")
    assert declared, "requirements.in declares nothing"
    missing = [name for name in declared if name not in pins]
    assert missing == [], (
        f"requirements.in declares {missing} but requirements.txt does not pin them; "
        "regenerate the lock (the command is at the top of requirements.in)"
    )


def test_an_exact_intent_pin_is_the_locked_version():
    """ruff is pinned in the intent itself; the lock must agree."""
    pins = _pins(REPO_ROOT / "requirements.txt")
    for line in _requirement_lines(REPO_ROOT / "requirements.in"):
        match = re.fullmatch(rf"({_NAME})==(\S+)", line.split(";", 1)[0].strip())
        if match:
            assert pins[_canonical(match.group(1))] == match.group(2), line


def test_the_lock_says_how_it_was_made():
    text = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "uv pip compile requirements.in --universal --python-version 3.11" in text
    # Universal: the Windows-only chain is pinned behind its markers, so the
    # release build freezes the versions CI tested.
    assert re.search(r"^pythonnet==\S+ ; sys_platform == 'win32'$", text, re.MULTILINE)


def test_the_build_tooling_is_locked_and_agrees_with_the_app_lock():
    pkg = REPO_ROOT / "packaging" / "windows"
    build = _pins(pkg / "build-requirements.txt")
    for name in _declared(pkg / "build-requirements.in"):
        assert name in build, f"build-requirements.txt does not pin {name}"
    assert {"pyinstaller", "pyinstaller-hooks-contrib"} <= build.keys()
    app = _pins(REPO_ROOT / "requirements.txt")
    for name, version in build.items():
        if name in app:
            assert app[name] == version, (
                f"{name} is {app[name]} in requirements.txt but {version} in "
                "build-requirements.txt; regenerate it with -c requirements.txt"
            )


def test_installs_use_the_locks():
    workflows = REPO_ROOT / ".github" / "workflows"
    release = (workflows / "release.yml").read_text(encoding="utf-8")
    ci = (workflows / "ci.yml").read_text(encoding="utf-8")
    assert "pip install -r requirements.txt" in ci
    assert "pip install -r requirements.txt" in release
    assert "pip install -r packaging/windows/build-requirements.txt" in release
    # No floating install of a bundled package beside the lock.
    for floating in ("pip install pyinstaller", "pip install pythonnet"):
        assert floating not in release, f"release.yml still runs `{floating}`"
