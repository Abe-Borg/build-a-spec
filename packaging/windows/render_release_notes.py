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

``--since`` is the last version that already had its own release page.
Pass it and both renderings cover every entry above it, so a version that
never got its own tag still reaches the two surfaces a user reads BEFORE
deciding to update. Omit it and the output is byte for byte what it was
when this only ever rendered one entry.

``--released`` is how the workflow supplies that bound without having to
work it out in PowerShell: hand it the tag names of the PUBLISHED releases
and it picks the greatest one below ``--version``. A tag is deliberately
not good enough — a tag build that failed after the tag was pushed leaves a
tag behind with no release page, and taking it as the bound would skip that
version's notes, which is the gap the span exists to close.

Usage:
    python packaging/windows/render_release_notes.py \
        --version 1.7.0 \
        --notes-out release-notes.txt \
        --body-out release-body.md \
        [--since 1.6.0 | --released v1.6.0,v1.5.0]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_INSTALL_NOTES = Path(__file__).resolve().parent / "release_install_notes.md"


def _strip_v(value: str) -> str:
    value = value.strip()
    return value[1:] if value.startswith("v") else value


def previous_released_version(version: str, released: list[str]) -> str:
    """Greatest PUBLISHED release strictly below ``version``, or ``""``.

    The caller passes what actually published, never what is merely tagged.
    Entries outside the version grammar are skipped rather than raising: the
    list comes from an API and one odd tag name must not cost the release
    its notes. Selection is here rather than in the workflow because a
    workflow step cannot be tested until the tag build that runs it.
    """
    from backend.updates import parse_version

    try:
        current = parse_version(version)
    except ValueError:
        return ""
    best, best_key = "", None
    for raw in released:
        candidate = _strip_v(raw)
        if not candidate:
            continue
        try:
            key = parse_version(candidate)
        except ValueError:
            continue
        if key >= current:
            continue
        if best_key is None or key > best_key:
            best, best_key = candidate, key
    return best


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--notes-out", required=True)
    parser.add_argument("--body-out", required=True)
    parser.add_argument(
        "--since",
        default="",
        help=(
            "last version that already had its own release page; entries "
            "above it are covered too. A leading 'v' is accepted so a git "
            "tag can be passed straight through."
        ),
    )
    parser.add_argument(
        "--released",
        default="",
        help=(
            "tag names of the PUBLISHED releases, comma- or whitespace-"
            "separated; the greatest one below --version becomes the bound. "
            "Ignored when --since is given explicitly."
        ),
    )
    args = parser.parse_args(argv)

    sys.path.insert(0, str(_REPO_ROOT))
    from backend.release_notes import (
        manifest_summary,
        markdown_notes,
        notes_for_release,
    )

    since = _strip_v(args.since)
    released = [part for part in re.split(r"[,\s]+", args.released) if part]
    if not since and released:
        since = previous_released_version(args.version, released)
        if not since:
            # Every published release is at or above this version, or none
            # parses. Either way there is no span to describe and the single
            # entry is the honest output — but say which, because a release
            # page that quietly narrowed looks identical to a correct one.
            print(
                f"no published release below {args.version} in "
                f"{len(released)} candidate(s); describing it alone"
            )
    if since:
        from backend.updates import parse_version

        try:
            parse_version(since)
        except ValueError:
            # notes_for_release falls back to this version alone, which is
            # the safe direction — but a bound nobody can read is a mistake
            # in the caller, not a decision, so it must not pass in silence.
            print(
                f"WARNING: --since {since!r} is not a version; "
                f"describing {args.version} alone",
                file=sys.stderr,
            )

    covered = [note.version for note in notes_for_release(args.version, after=since)]
    summary = manifest_summary(args.version, after=since)
    body = markdown_notes(args.version, after=since)
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
    print(f"covering: {', '.join(covered)}" + (f" (since {since})" if since else ""))
    print(f"wrote {args.notes_out} ({len(summary)} chars)")
    print(f"wrote {args.body_out} ({len(body)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
