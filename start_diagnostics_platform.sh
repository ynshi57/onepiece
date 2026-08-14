#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-9000}"
HOST="${HOST:-127.0.0.1}"
OPEN_DIAGNOSTICS="${OPEN_DIAGNOSTICS:-1}"
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
open_when_ready
QWEN_WARMUP_ON_STARTUP=0 PORT="${PORT}" uvicorn --app-dir server-vqa app.main:app --host "${HOST}" --port "${PORT}"
