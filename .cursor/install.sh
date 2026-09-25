#!/usr/bin/env bash
# Idempotent repository bootstrap for Build-a-Spec (Python 3.11+ FastAPI
# backend + React 18 / Vite frontend). Safe to run repeatedly; it never
# rewrites lockfiles or upgrades pinned dependencies.
set -euo pipefail

# Repo root, however this script is invoked.
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

# --- Backend: Python virtualenv + pinned deps ---------------------------------
# The stock Debian/Ubuntu python3 has no bundled ensurepip, so `python3 -m venv`
# cannot bootstrap pip. Install python3-venv once if it is missing (guarded, so
# a machine/snapshot that already has it does no apt work). Non-interactive.
if ! python3 -c 'import ensurepip' >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq python3-venv
fi

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
# requirements.txt pins the dev/test deps (pytest, httpx, ruff==0.15.8) too.
.venv/bin/pip install -r requirements.txt

# --- Frontend: Node deps + a production build the backend can serve -----------
# shellcheck source=.cursor/node-path.sh
. "$(dirname -- "$0")/node-path.sh"

cd frontend
npm ci
# Build frontend/dist so `python -m uvicorn backend.app:app` serves the real
# SPA at "/". The dev terminal uses Vite hot-reload instead; this is the
# fallback the packaged app also relies on. Idempotent.
npm run build

echo "Build-a-Spec environment ready."
