"""Release-time version consistency gate.

Cloned in spirit from Claude-Spec-Critic ``packaging/windows/
check_release_version.py``: a release tagged ``v0.5.0`` must ship an app
that reports 0.5.0 everywhere — ``backend/settings.py`` (the updater
compares against this), ``frontend/package.json``, and the ``README.md``
headline (the ``**vX.Y.Z**`` on its first prose line — the one version
claim a reader sees before installing anything, and the one that drifted
ten releases behind before this check existed). A mismatch would make the
shipped app permanently see itself as out of date (or never see the next
update), or the README describe a version nobody ships.

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


def readme_headline_version() -> str:
    """The version the README's headline claims (``**vX.Y.Z**``), or ``""``.

    Only the first few lines are scanned: the body legitimately names every
    past version in its "Shipped in vX.Y.Z" history, and the headline is the
    one place that must name the CURRENT one.
    """
    text = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    head = "\n".join(text.splitlines()[:5])
    match = re.search(r"\*\*v(\d+\.\d+\.\d+)\*\*", head)
    return match.group(1) if match else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check release version consistency.")
    parser.add_argument("--tag", default="", help="release tag, e.g. v0.5.0")
    args = parser.parse_args(argv)

    backend = settings_version()
    frontend = package_json_version()
    readme = readme_headline_version()
    problems: list[str] = []
    if backend != frontend:
        problems.append(
            f"backend/settings.py VERSION ({backend}) != "
            f"frontend/package.json version ({frontend})"
        )
    if readme != backend:
        problems.append(
            f"README.md headline version ({readme or 'missing'}) != "
            f"backend VERSION ({backend})"
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
