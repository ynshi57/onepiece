#!/usr/bin/env bash
set -euo pipefail

# Local Qwen VQA runtime manager.
#
# By default this launches `llama-server` (the binary bundled in Ollama.app)
# DIRECTLY, so we can pass `--image-min-tokens`. Ollama derives that flag from
# the model's baked vision config (1024 for qwen2.5vl) and exposes no way to
# lower it; lowering it is the single biggest prefill-latency lever on a 16GB
# Mac (~5s -> ~2s prefill with no measurable quality loss for scene use).
#
# Ollama is still used purely as the model *downloader* (`ollama pull` writes
# the blob store we read). To fall back to the old Ollama-managed runtime
# (image-min-tokens locked at 1024), set USE_OLLAMA=1.
#
# Subcommands: start (default) | stop | status | supervise
#   supervise: run in foreground, restarting llama-server if it crashes.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="${ROOT_DIR}/server-vqa"

MODEL="${MODEL:-qwen2.5vl:3b}"
USE_OLLAMA="${USE_OLLAMA:-0}"

# --- direct llama-server config ---------------------------------------------
OLLAMA_MODELS_DIR="${OLLAMA_MODELS_DIR:-${HOME}/.ollama/models}"
LLAMA_SERVER_BIN="${LLAMA_SERVER_BIN:-/Applications/Ollama.app/Contents/Resources/llama-server}"
LLAMA_HOST="${LLAMA_HOST:-127.0.0.1}"
LLAMA_PORT="${LLAMA_PORT:-11435}"
IMAGE_MIN_TOKENS="${IMAGE_MIN_TOKENS:-256}"
IMAGE_MAX_TOKENS="${IMAGE_MAX_TOKENS:-512}"
LLAMA_LOG="${LLAMA_LOG:-/tmp/qwen-llama-server.log}"
LLAMA_PIDFILE="${LLAMA_PIDFILE:-/tmp/qwen-llama-server.pid}"

# API base the backend should talk to (direct runtime).
DIRECT_API_BASE="http://${LLAMA_HOST}:${LLAMA_PORT}"

# --- ollama fallback config -------------------------------------------------
OLLAMA_API_BASE="${QWEN_API_BASE_URL:-http://127.0.0.1:11434}"
INSTALL_URL="https://ollama.com/download/mac"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if [ -x "${ROOT_DIR}/.venv/bin/python" ]; then
  PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
fi

log() { echo "[qwen-local] $*"; }

health_ok() {
  local base="$1"
  curl -fsS "${base}/health" >/dev/null 2>&1 \
    || curl -fsS "${base}/v1/models" >/dev/null 2>&1
}

wait_for_health() {
  local base="$1"
  local max="${2:-40}"
  local attempts=0
  while [ "${attempts}" -lt "${max}" ]; do
    if health_ok "${base}"; then
      return 0
    fi
    attempts=$((attempts + 1))
    sleep 1
  done
  return 1
}

running_pid() {
  # Echo the live llama-server PID from the pidfile, or nothing.
  if [ -f "${LLAMA_PIDFILE}" ]; then
    local pid
    pid="$(cat "${LLAMA_PIDFILE}" 2>/dev/null || true)"
    if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
      echo "${pid}"
    fi
  fi
}

# Resolve the null-delimited llama-server argv from the tested Python helper.
build_llama_argv() {
  ( cd "${SERVER_DIR}" && "${PYTHON_BIN}" -m app.local_runtime args \
      --model "${MODEL}" \
      --models-dir "${OLLAMA_MODELS_DIR}" \
      --binary "${LLAMA_SERVER_BIN}" \
      --host "${LLAMA_HOST}" \
      --port "${LLAMA_PORT}" \
      --image-min-tokens "${IMAGE_MIN_TOKENS}" \
      --image-max-tokens "${IMAGE_MAX_TOKENS}" )
}

ensure_model_downloaded() {
  # Use Ollama only to fetch weights; it does not need to keep running after.
  # The tested Python helper is the single source of truth for "is it present".
  if ( cd "${SERVER_DIR}" && "${PYTHON_BIN}" -m app.local_runtime resolve \
        --model "${MODEL}" --models-dir "${OLLAMA_MODELS_DIR}" >/dev/null 2>&1 ); then
    return 0
  fi
  log "Model ${MODEL} not found in ${OLLAMA_MODELS_DIR}; pulling via Ollama..."
  if ! command -v ollama >/dev/null 2>&1; then
    log "ERROR: ollama CLI not found; needed once to download the model."
    log "Install from ${INSTALL_URL}, run 'ollama pull ${MODEL}', then rerun."
    return 1
  fi
  ollama pull "${MODEL}"
}

warmup() {
  local base="$1"
  # A real 32x32 JPEG. llama-server's image decoder (stb_image via mtmd) rejects
  # a bare 1x1 PNG ("failed to decode image bytes"), so we prime with a valid
  # baseline JPEG. Priming loads the vision path so the first camera frame does
  # not pay cold-load. Best-effort (No Silent Failures: failure is logged), and
  # bounded by --max-time so a hung model cannot wedge startup.
  local tiny="/9j/4AAQSkZJRgABAQAASABIAAD/4QBMRXhpZgAATU0AKgAAAAgAAYdpAAQAAAABAAAAGgAAAAAAA6ABAAMAAAABAAEAAKACAAQAAAABAAAAIKADAAQAAAABAAAAIAAAAAD/7QA4UGhvdG9zaG9wIDMuMAA4QklNBAQAAAAAAAA4QklNBCUAAAAAABDUHYzZjwCyBOmACZjs+EJ+/8AAEQgAIAAgAwEiAAIRAQMRAf/EAB8AAAEFAQEBAQEBAAAAAAAAAAABAgMEBQYHCAkKC//EALUQAAIBAwMCBAMFBQQEAAABfQECAwAEEQUSITFBBhNRYQcicRQygZGhCCNCscEVUtHwJDNicoIJChYXGBkaJSYnKCkqNDU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6g4SFhoeIiYqSk5SVlpeYmZqio6Slpqeoqaqys7S1tre4ubrCw8TFxsfIycrS09TV1tfY2drh4uPk5ebn6Onq8fLz9PX29/j5+v/EAB8BAAMBAQEBAQEBAQEAAAAAAAABAgMEBQYHCAkKC//EALURAAIBAgQEAwQHBQQEAAECdwABAgMRBAUhMQYSQVEHYXETIjKBCBRCkaGxwQkjM1LwFWJy0QoWJDThJfEXGBkaJicoKSo1Njc4OTpDREVGR0hJSlNUVVZXWFlaY2RlZmdoaWpzdHV2d3h5eoKDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uLj5OXm5+jp6vLz9PX29/j5+v/bAEMAAgICAgICAwICAwUDAwMFBgUFBQUGCAYGBgYGCAoICAgICAgKCgoKCgoKCgwMDAwMDA4ODg4ODw8PDw8PDw8PD//bAEMBAgICBAQEBwQEBxALCQsQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEP/dAAQAAv/aAAwDAQACEQMRAD8AKKKKACiiigD/0CiiigAooooA/9k="
  log "Warming up (loading weights)..."
  if curl -fsS --max-time 60 "${base}/v1/chat/completions" -H "Content-Type: application/json" \
      -d "{\"model\":\"${MODEL}\",\"max_tokens\":1,\"messages\":[{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"ok\"},{\"type\":\"image_url\",\"image_url\":{\"url\":\"data:image/jpeg;base64,${tiny}\"}}]}]}" \
      >/dev/null 2>&1; then
    log "Warmup complete."
  else
    log "Warmup request failed (non-fatal); the first frame will be slower."
  fi
}

# --- llama-server lifecycle -------------------------------------------------

start_llama_server() {
  local existing
  existing="$(running_pid)"
  if [ -n "${existing}" ]; then
    log "llama-server already running (pid ${existing}) on ${DIRECT_API_BASE}."
    return 0
  fi

  if [ ! -x "${LLAMA_SERVER_BIN}" ]; then
    log "ERROR: llama-server binary not found/executable at ${LLAMA_SERVER_BIN}."
    log "Set LLAMA_SERVER_BIN, or install Ollama.app which bundles it."
    return 1
  fi

  # Resolve argv (blob paths) via the tested helper into a bash array.
  local argv=()
  while IFS= read -r -d '' token; do
    argv+=("${token}")
  done < <(build_llama_argv)

  if [ "${#argv[@]}" -eq 0 ]; then
    log "ERROR: failed to resolve llama-server arguments (see errors above)."
    return 1
  fi

  log "Launching llama-server: image-min-tokens=${IMAGE_MIN_TOKENS} max=${IMAGE_MAX_TOKENS} port=${LLAMA_PORT}"
  nohup "${argv[@]}" >"${LLAMA_LOG}" 2>&1 &
  local pid=$!
  echo "${pid}" >"${LLAMA_PIDFILE}"

  if ! wait_for_health "${DIRECT_API_BASE}" 60; then
    log "ERROR: llama-server did not become healthy; last log lines:"
    tail -n 15 "${LLAMA_LOG}" || true
    return 1
  fi
  log "llama-server healthy (pid ${pid}) at ${DIRECT_API_BASE}."
}

stop_llama_server() {
  local pid
  pid="$(running_pid)"
  if [ -z "${pid}" ]; then
    log "No running llama-server (per ${LLAMA_PIDFILE})."
    rm -f "${LLAMA_PIDFILE}"
    return 0
  fi
  log "Stopping llama-server (pid ${pid})..."
  kill "${pid}" 2>/dev/null || true
  local waited=0
  while kill -0 "${pid}" 2>/dev/null && [ "${waited}" -lt 10 ]; do
    sleep 1
    waited=$((waited + 1))
  done
  if kill -0 "${pid}" 2>/dev/null; then
    log "Still alive after ${waited}s; sending SIGKILL."
    kill -9 "${pid}" 2>/dev/null || true
  fi
  rm -f "${LLAMA_PIDFILE}"
  log "Stopped."
}

supervise_llama_server() {
  # Foreground supervisor: restart on crash, log every restart. Ctrl-C to exit.
  log "Supervising llama-server (restart-on-crash). Ctrl-C to stop."
  trap 'stop_llama_server; exit 0' INT TERM
  local restarts=0
  while true; do
    if [ -z "$(running_pid)" ]; then
      if [ "${restarts}" -gt 0 ]; then
        log "llama-server exited; restart #${restarts}. Recent log:"
        tail -n 10 "${LLAMA_LOG}" || true
      fi
      start_llama_server || { log "Restart failed; retrying in 5s."; sleep 5; }
      restarts=$((restarts + 1))
    fi
    sleep 3
  done
}

status_llama_server() {
  local pid
  pid="$(running_pid)"
  if [ -n "${pid}" ]; then
    log "RUNNING pid=${pid} at ${DIRECT_API_BASE} (image-min-tokens=${IMAGE_MIN_TOKENS})"
    return 0
  fi
  log "NOT running (${DIRECT_API_BASE})"
  return 1
}

print_backend_env() {
  local base="$1"
  cat <<EOF

Local Qwen service ready.

Use these env vars when starting backend:
QWEN_API_BASE_URL=${base}
QWEN_MODEL=${MODEL}
EOF
}

# --- Ollama fallback runtime ------------------------------------------------

run_ollama_runtime() {
  export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:--1}"
  log "USE_OLLAMA=1: using Ollama-managed runtime (image-min-tokens locked at 1024)."

  if ! command -v ollama >/dev/null 2>&1; then
    log "Ollama not found. Install from ${INSTALL_URL} and rerun."
    return 1
  fi
  if ! health_ok "${OLLAMA_API_BASE}"; then
    if [ -d "/Applications/Ollama.app" ]; then
      log "Starting Ollama app..."
      open -a "/Applications/Ollama.app"
    else
      log "Starting 'ollama serve'..."
      nohup ollama serve >/tmp/ollama-serve.log 2>&1 &
    fi
    if ! wait_for_health "${OLLAMA_API_BASE}" 20; then
      log "ERROR: Ollama API not responding at ${OLLAMA_API_BASE}."
      return 1
    fi
  fi
  log "Pulling model ${MODEL}..."
  ollama pull "${MODEL}"
  warmup "${OLLAMA_API_BASE}"
  print_backend_env "${OLLAMA_API_BASE}"
}

# --- entrypoint -------------------------------------------------------------

COMMAND="${1:-start}"

if [ "${USE_OLLAMA}" = "1" ]; then
  case "${COMMAND}" in
    start) run_ollama_runtime ;;
    stop)  log "USE_OLLAMA=1: leave Ollama running via its own app; nothing to stop." ;;
    status) health_ok "${OLLAMA_API_BASE}" && log "Ollama RUNNING at ${OLLAMA_API_BASE}" || { log "Ollama NOT running"; exit 1; } ;;
    *) log "Unknown command '${COMMAND}' (start|stop|status)"; exit 2 ;;
  esac
  exit 0
fi

case "${COMMAND}" in
  start)
    ensure_model_downloaded
    start_llama_server
    warmup "${DIRECT_API_BASE}"
    print_backend_env "${DIRECT_API_BASE}"
    ;;
  stop)
    stop_llama_server
    ;;
  status)
    status_llama_server
    ;;
  supervise)
    ensure_model_downloaded
    supervise_llama_server
    ;;
  *)
    log "Unknown command '${COMMAND}' (start|stop|status|supervise)"
    exit 2
    ;;
esac
