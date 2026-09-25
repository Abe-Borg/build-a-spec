#!/usr/bin/env bash
# Long-running Vite dev server for the Build-a-Spec frontend (hot reload).
# Vite proxies "/api" to the backend on 127.0.0.1:8756 (see vite.config.ts),
# so run this alongside the `backend` terminal.
set -euo pipefail

cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

# shellcheck source=.cursor/node-path.sh
. "$(dirname -- "$0")/node-path.sh"

cd frontend
exec npm run dev -- --host 127.0.0.1 --port 5173
