#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"

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

echo "Connecting worker to relay: ${RELAY_WORKER_URL:-ws://127.0.0.1:9100/ws/worker}"
echo "Worker ID: ${WORKER_ID:-local-mac-worker}"

export PYTHONPATH="${ROOT_DIR}/server-vqa:${PYTHONPATH:-}"
exec python -m app.worker_client
