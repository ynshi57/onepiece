#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-9000}"
HOST="${HOST:-127.0.0.1}"
OPEN_DIAGNOSTICS="${OPEN_DIAGNOSTICS:-1}"
# Auto-reload on source edits so you don't have to restart this script after every
# code change. Dev-only convenience; set RELOAD=0 to disable (e.g. for production).
RELOAD="${RELOAD:-1}"
DIAGNOSTICS_URL="http://${HOST}:${PORT}/diagnostics/ui"

cd "${ROOT_DIR}"

if [ ! -d ".venv" ]; then
  echo "Missing .venv. Create/install dependencies first." >&2
  echo "Suggested: python3 -m venv .venv && source .venv/bin/activate && pip install -r server-vqa/requirements-dev.txt" >&2
  exit 1
fi

source .venv/bin/activate

open_when_ready() {
  if [ "${OPEN_DIAGNOSTICS}" != "1" ]; then
    return 0
  fi
  (
    for _ in $(seq 1 60); do
      if curl -fsS "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
        echo "Opening VQASee diagnostics platform: ${DIAGNOSTICS_URL}"
        open "${DIAGNOSTICS_URL}" >/dev/null 2>&1 || true
        exit 0
      fi
      sleep 1
    done
    echo "Diagnostics platform did not become ready in 60s. Open manually: ${DIAGNOSTICS_URL}" >&2
  ) &
}

echo "Starting VQASee diagnostics/evolution platform only."
echo "Qwen warmup disabled; this script does NOT start local Qwen."
echo "URL: ${DIAGNOSTICS_URL}"

reload_args=()
if [ "${RELOAD}" = "1" ]; then
  # Watch only the backend source dir; auto-restart the worker on .py edits so a
  # browser refresh reflects code changes without re-running this script.
  reload_args=(--reload --reload-dir "${ROOT_DIR}/server-vqa/app")
  echo "Auto-reload: ON (watching server-vqa/app). Set RELOAD=0 to disable."
else
  echo "Auto-reload: OFF (set RELOAD=1 to enable)."
fi

open_when_ready
QWEN_WARMUP_ON_STARTUP=0 PORT="${PORT}" uvicorn --app-dir server-vqa app.main:app \
  --host "${HOST}" --port "${PORT}" ${reload_args[@]+"${reload_args[@]}"}
