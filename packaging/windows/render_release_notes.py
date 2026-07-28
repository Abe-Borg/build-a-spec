"""Render the release-time views of ``backend/release_notes.py``.

One entry, three audiences:

- the **app** reads the bundled entry directly (What's-new modal);
- a **not-yet-updated app** reads ``manifest_summary`` out of
  ``latest.json``'s ``notes`` field, so it can say what the update contains
  before installing it;
- the **GitHub Release page** gets ``markdown_notes`` followed by the static
  install/SmartScreen instructions in ``release_install_notes.md``.

Split out of the release workflow rather than inlined as ``python -c``:
PowerShell here-strings must terminate at column zero, which a YAML block
scalar cannot do, and quoting a multi-line program through two shells is
exactly the kind of thing that breaks silently at tag time.

Usage:
    python packaging/windows/render_release_notes.py \
        --version 1.7.0 \
        --notes-out release-notes.txt \
        --body-out release-body.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_INSTALL_NOTES = Path(__file__).resolve().parent / "release_install_notes.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--notes-out", required=True)
    parser.add_argument("--body-out", required=True)
    args = parser.parse_args(argv)

    sys.path.insert(0, str(_REPO_ROOT))
    from backend.release_notes import manifest_summary, markdown_notes

    summary = manifest_summary(args.version)
    body = markdown_notes(args.version)
    if not body.strip():
        # The test suite already fails a version with no entry, so reaching
        # here means the gate was bypassed. Refuse rather than publish a
        # release page with no notes on it.
        print(
            f"ERROR: backend/release_notes.py has no entry for {args.version}",
            file=sys.stderr,
        )
        return 1

    if _INSTALL_NOTES.exists():
        body = body.rstrip() + "\n\n" + _INSTALL_NOTES.read_text(encoding="utf-8")

    Path(args.notes_out).write_text(summary, encoding="utf-8")
    Path(args.body_out).write_text(body, encoding="utf-8")
    print(f"wrote {args.notes_out} ({len(summary)} chars)")
    print(f"wrote {args.body_out} ({len(body)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
