#!/usr/bin/env bash
# Run the FastAPI dev server and the Vite dev server together.
# Ctrl-C stops both. Must run from the repo root: the API's relative
# data_dir ("data/") and the web proxy target both assume it.
set -euo pipefail

cd "$(dirname "$0")/.."

cleanup() {
  trap - TERM INT
  kill 0 2>/dev/null || true
}
trap cleanup TERM INT EXIT

uv run uvicorn openearth_api.main:app --reload --port 8000 &
pnpm --dir apps/web dev &

wait
