#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-9100}"

if [ ! -d "${VENV_DIR}" ]; then
  echo "Python virtualenv not found: ${VENV_DIR}"
  echo "Run: bash deploy/ios/install_deps.sh"
  exit 1
fi

source "${VENV_DIR}/bin/activate"

if [ "${RELAY_PAIRING_TOKEN:-dev-pairing-token}" = "dev-pairing-token" ]; then
  echo "WARNING: using default RELAY_PAIRING_TOKEN=dev-pairing-token."
  echo "For any public relay, set a long random RELAY_PAIRING_TOKEN first."
fi

echo "Starting VQASee Relay on ${HOST}:${PORT}"
echo "Client endpoint: ws(s)://<relay-host>:${PORT}/ws/client"
echo "Worker endpoint: ws(s)://<relay-host>:${PORT}/ws/worker"

exec uvicorn relay_app.main:app --app-dir "${ROOT_DIR}/relay-server" --reload --host "${HOST}" --port "${PORT}"
