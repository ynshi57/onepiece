#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QWEN_MODEL="${QWEN_MODEL:-qwen2.5vl:3b}"
USE_OLLAMA="${USE_OLLAMA:-0}"

# Pick the API base that matches the runtime start_qwen_local.sh will launch:
# - direct llama-server (default): 127.0.0.1:${LLAMA_PORT:-11435}
# - Ollama fallback (USE_OLLAMA=1): 127.0.0.1:11434
if [ -n "${QWEN_API_BASE_URL:-}" ]; then
  RESOLVED_API_BASE="${QWEN_API_BASE_URL}"
elif [ "${USE_OLLAMA}" = "1" ]; then
  RESOLVED_API_BASE="http://127.0.0.1:11434"
else
  RESOLVED_API_BASE="http://127.0.0.1:${LLAMA_PORT:-11435}"
fi

echo "Preparing local Qwen service (USE_OLLAMA=${USE_OLLAMA})..."
USE_OLLAMA="${USE_OLLAMA}" MODEL="${QWEN_MODEL}" bash "${ROOT_DIR}/start_qwen_local.sh" start

echo "Starting backend with Qwen enabled (base=${RESOLVED_API_BASE})..."
QWEN_API_BASE_URL="${RESOLVED_API_BASE}" QWEN_MODEL="${QWEN_MODEL}" bash "${ROOT_DIR}/start_backend.sh"
