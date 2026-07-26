#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-9000}"

LOCAL_HOSTNAME="$(scutil --get LocalHostName 2>/dev/null || true)"
if [ -z "${LOCAL_HOSTNAME}" ]; then
  LOCAL_HOSTNAME="$(hostname -s 2>/dev/null || true)"
fi
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || true)"
if [ -z "${LAN_IP}" ]; then
  LAN_IP="$(ipconfig getifaddr en1 2>/dev/null || true)"
fi

if [ ! -d "${VENV_DIR}" ]; then
  echo "Python virtualenv not found: ${VENV_DIR}"
  echo "Run: bash deploy/ios/install_deps.sh"
  exit 1
fi

source "${VENV_DIR}/bin/activate"

if ! python -c "import websockets" >/dev/null 2>&1; then
  echo "Missing websocket runtime dependency: websockets"
  echo "Run: source .venv/bin/activate && pip install -r server-vqa/requirements-dev.txt"
  exit 1
fi

if [ -n "${QWEN_API_BASE_URL:-}" ]; then
  echo "Qwen API enabled: ${QWEN_API_BASE_URL}"
  if [ -n "${QWEN_MODEL:-}" ]; then
    echo "Qwen model: ${QWEN_MODEL}"
  fi
else
  echo "Qwen API not configured; using heuristic fallback."
fi

if [ -n "${LOCAL_HOSTNAME}" ]; then
  echo "Try iPhone URL (hostname): ws://${LOCAL_HOSTNAME}.local:${PORT}/ws/signaling"
fi
if [ -n "${LAN_IP}" ]; then
  echo "Try iPhone URL (LAN IP): ws://${LAN_IP}:${PORT}/ws/signaling"
fi
echo "Bonjour discovery: iOS app can auto-discover this backend on nearby LAN/hotspot."

export VQASEE_SERVICE_PORT="${PORT}"
exec uvicorn app.main:app --app-dir "${ROOT_DIR}/server-vqa" --reload --host "${HOST}" --port "${PORT}"
