#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QWEN_API_BASE_URL="${QWEN_API_BASE_URL:-http://127.0.0.1:11434}"
QWEN_MODEL="${QWEN_MODEL:-qwen2.5vl:3b}"

echo "Preparing local Qwen service..."
QWEN_API_BASE_URL="${QWEN_API_BASE_URL}" MODEL="${QWEN_MODEL}" bash "${ROOT_DIR}/start_qwen_local.sh"

echo "Starting backend with Qwen enabled..."
QWEN_API_BASE_URL="${QWEN_API_BASE_URL}" QWEN_MODEL="${QWEN_MODEL}" bash "${ROOT_DIR}/start_backend.sh"
