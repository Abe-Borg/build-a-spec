"""Generate the ``latest.json`` update manifest for a Windows release.

Cloned ≈verbatim from Claude-Spec-Critic ``packaging/windows/
make_manifest.py``. Run at release time after the installer is built; it
computes the installer's SHA-256 and writes the tiny manifest the
installed app fetches to discover updates (``backend/updates.py``'s
``parse_manifest`` is the consumer — ``tests/test_updates.py`` round-trips
this maker through it so the two can never drift).

Usage:
    python packaging/windows/make_manifest.py \
        --version 0.5.0 \
        --installer dist/installer/BuildASpecSetup.exe \
        --url https://github.com/Abe-Borg/build-a-spec/releases/download/v0.5.0/BuildASpecSetup.exe \
        --out latest.json \
        --notes "See the release page for details." \
        --published-at 2026-07-21

Pure standard library — runs in a clean release environment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

SCHEMA_VERSION = 1


def sha256_of(path: str | Path, *, chunk: int = 1 << 20) -> str:
    """Stream ``path`` and return its lowercase SHA-256 hex digest."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def build_manifest(
    *,
    version: str,
    installer: str | Path,
    url: str,
    notes: str = "",
    published_at: str = "",
) -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "version": version,
        "url": url,
        "sha256": sha256_of(installer),
        "notes": notes,
        "published_at": published_at,
    }


def write_manifest(
    *,
    version: str,
    installer: str | Path,
    url: str,
    out_path: str | Path,
    notes: str = "",
    published_at: str = "",
) -> dict:
    manifest = build_manifest(
        version=version,
        installer=installer,
        url=url,
        notes=notes,
        published_at=published_at,
    )
    Path(out_path).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate latest.json for a release."
    )
    parser.add_argument("--version", required=True, help="MAJOR.MINOR.PATCH[rcN]")
    parser.add_argument("--installer", required=True, help="path to the built .exe")
    parser.add_argument("--url", required=True, help="https download URL")
    parser.add_argument("--out", required=True, help="output path for latest.json")
    parser.add_argument("--notes", default="", help="short release notes")
    parser.add_argument("--published-at", default="", help="ISO date of the release")
    args = parser.parse_args(argv)

    manifest = write_manifest(
        version=args.version,
        installer=args.installer,
        url=args.url,
        out_path=args.out,
        notes=args.notes,
        published_at=args.published_at,
    )
    print(f"wrote {args.out}: v{manifest['version']} sha256={manifest['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
