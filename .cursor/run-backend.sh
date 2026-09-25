#!/usr/bin/env bash
# Long-running FastAPI backend for Build-a-Spec on 127.0.0.1:8756.
#
# This serves the JSON API and, when frontend/dist exists, the built SPA at "/".
# It runs the importable ASGI app (backend.app:app), which is created WITHOUT
# the desktop-security handshake main.py adds — the right surface for a headless
# dev/agent environment. The native pywebview window (`python main.py`) is the
# Windows-primary shipping shell and is not started here; there is no GUI here.
set -euo pipefail

cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

PORT="${BUILD_A_SPEC_PORT:-8756}"
exec .venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port "$PORT"
