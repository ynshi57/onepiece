#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODEL="${MODEL:-qwen2.5vl:3b}"
API_BASE="${QWEN_API_BASE_URL:-http://127.0.0.1:11434}"
INSTALL_URL="https://ollama.com/download/mac"

# Keep the model resident in memory so the first real frame does not pay a
# multi-second cold reload. Callers can override (e.g. "24h") or set "0" to
# disable. Exported so a CLI-launched `ollama serve` below inherits it.
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:--1}"

check_api() {
  curl -fsS "${API_BASE}/api/tags" >/dev/null 2>&1
}

install_ollama_if_needed() {
  if command -v ollama >/dev/null 2>&1; then
    return 0
  fi

  echo "Ollama not found. Trying Homebrew cask install..."
  if command -v brew >/dev/null 2>&1; then
    if brew install --cask ollama; then
      echo "Installed Ollama via Homebrew."
      return 0
    fi
    echo "Homebrew install failed."
  else
    echo "Homebrew not found."
  fi

  echo "Trying official installer script..."
  if curl -fsSL https://ollama.com/install.sh | sh; then
    echo "Installed Ollama via official installer."
    return 0
  fi

  echo "Official installer failed."
  echo "Please install manually from: ${INSTALL_URL}"
  echo "Then rerun: bash ${ROOT_DIR}/start_qwen_local.sh"
  return 1
}

wait_for_api() {
  local attempts=0
  local max_attempts=20
  while [ "${attempts}" -lt "${max_attempts}" ]; do
    if check_api; then
      return 0
    fi
    attempts=$((attempts + 1))
    sleep 1
  done
  return 1
}

start_ollama_if_needed() {
  if check_api; then
    return 0
  fi

  if [ -d "/Applications/Ollama.app" ]; then
    echo "Starting Ollama app..."
    open -a "/Applications/Ollama.app"
    if wait_for_api; then
      return 0
    fi
  fi

  echo "Starting Ollama service via CLI..."
  if command -v ollama >/dev/null 2>&1; then
    nohup ollama serve >/tmp/ollama-serve.log 2>&1 &
    if wait_for_api; then
      return 0
    fi
  fi

  echo "Ollama service not responding at ${API_BASE}"
  echo "If this persists, open Ollama app manually once and rerun."
  exit 1
}

install_ollama_if_needed
start_ollama_if_needed

echo "Pulling model ${MODEL} (first run may take a while)..."
ollama pull "${MODEL}"

warmup_model() {
  # Fire one tiny multimodal request so the vision weights are loaded into
  # memory before the first camera frame arrives. Best-effort: a failure here
  # only means the first real frame is slower, so we log and continue.
  local tiny_png_b64="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
  echo "Warming up ${MODEL} (loading weights into memory)..."
  if curl -fsS "${API_BASE}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${MODEL}\",\"max_tokens\":1,\"messages\":[{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"ok\"},{\"type\":\"image_url\",\"image_url\":{\"url\":\"data:image/png;base64,${tiny_png_b64}\"}}]}]}" \
    >/dev/null 2>&1; then
    echo "Warmup complete."
  else
    echo "Warmup request failed (non-fatal); the first frame will be slower."
  fi
}

warmup_model

cat <<EOF
Local Qwen service ready.

Use these env vars when starting backend:
QWEN_API_BASE_URL=${API_BASE}
QWEN_MODEL=${MODEL}
EOF
