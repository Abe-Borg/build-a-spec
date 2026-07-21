"""Release-time version consistency gate.

Cloned in spirit from Claude-Spec-Critic ``packaging/windows/
check_release_version.py``: a release tagged ``v0.5.0`` must ship an app
that reports 0.5.0 everywhere — ``backend/settings.py`` (the updater
compares against this) and ``frontend/package.json``. A mismatch would
make the shipped app permanently see itself as out of date (or never see
the next update).

Usage:
    python packaging/windows/check_release_version.py --tag v0.5.0
    python packaging/windows/check_release_version.py          # consistency only
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def settings_version() -> str:
    text = (_REPO_ROOT / "backend" / "settings.py").read_text(encoding="utf-8")
    match = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("could not find VERSION in backend/settings.py")
    return match.group(1)


def package_json_version() -> str:
    data = json.loads(
        (_REPO_ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    )
    return str(data.get("version", ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check release version consistency.")
    parser.add_argument("--tag", default="", help="release tag, e.g. v0.5.0")
    args = parser.parse_args(argv)

    backend = settings_version()
    frontend = package_json_version()
    problems: list[str] = []
    if backend != frontend:
        problems.append(
            f"backend/settings.py VERSION ({backend}) != "
            f"frontend/package.json version ({frontend})"
        )
    if args.tag:
        tag_version = args.tag.lstrip("v")
        if tag_version != backend:
            problems.append(
                f"release tag ({args.tag}) != backend VERSION ({backend})"
            )
    if problems:
        for problem in problems:
            print(f"VERSION MISMATCH: {problem}", file=sys.stderr)
        return 1
    print(f"version consistency ok: {backend}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
