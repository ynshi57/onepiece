#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QWEN_MODEL="${QWEN_MODEL:-qwen2.5vl:3b}"
USE_OLLAMA="${USE_OLLAMA:-0}"

PORT="${PORT:-9000}"
DIAGNOSTICS_URL="http://127.0.0.1:${PORT}/diagnostics/ui"
OPEN_DIAGNOSTICS="${OPEN_DIAGNOSTICS:-}"

ask_open_diagnostics() {
  if [ -n "${OPEN_DIAGNOSTICS}" ]; then
    return 0
  fi
  if [ ! -t 0 ]; then
    OPEN_DIAGNOSTICS="0"
    return 0
  fi
  echo
  printf "是否在后端启动后打开 VQASee 诊断标注台？(y/N) "
  local answer
  read -r answer || answer=""
  case "${answer}" in
    y|Y|yes|YES|Yes)
      OPEN_DIAGNOSTICS="1"
      ;;
    *)
      OPEN_DIAGNOSTICS="0"
      ;;
  esac
}

open_diagnostics_when_ready() {
  if [ "${OPEN_DIAGNOSTICS}" != "1" ]; then
    echo "诊断标注台：${DIAGNOSTICS_URL}（需要时手动打开）"
    return 0
  fi
  (
    for _ in $(seq 1 60); do
      if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
        echo "Opening diagnostics UI: ${DIAGNOSTICS_URL}"
        open "${DIAGNOSTICS_URL}" >/dev/null 2>&1 || true
        exit 0
      fi
      sleep 1
    done
    echo "诊断标注台未自动打开：后端 60 秒内未就绪。请手动打开 ${DIAGNOSTICS_URL}"
  ) &
}

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

ask_open_diagnostics

echo "Preparing local Qwen service (USE_OLLAMA=${USE_OLLAMA})..."
USE_OLLAMA="${USE_OLLAMA}" MODEL="${QWEN_MODEL}" bash "${ROOT_DIR}/start_qwen_local.sh" start

echo "Starting backend with Qwen enabled (base=${RESOLVED_API_BASE})..."
open_diagnostics_when_ready
QWEN_API_BASE_URL="${RESOLVED_API_BASE}" QWEN_MODEL="${QWEN_MODEL}" PORT="${PORT}" bash "${ROOT_DIR}/start_backend.sh"
