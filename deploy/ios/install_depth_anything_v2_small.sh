#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MODEL_DIR="${ROOT_DIR}/ios-vqa-app/VQASee/VQASee"
MODEL_NAME="DepthAnythingV2SmallF16.mlpackage"
HF_REPO="apple/coreml-depth-anything-v2-small"

if command -v hf >/dev/null 2>&1; then
  HF_CMD=(hf download)
elif command -v huggingface-cli >/dev/null 2>&1; then
  # Older installations still expose huggingface-cli. Newer HF warns that this
  # command is deprecated; prefer `hf` when available.
  HF_CMD=(huggingface-cli download)
else
  echo "Neither 'hf' nor 'huggingface-cli' was found." >&2
  echo "Install with: brew install huggingface-cli" >&2
  echo "or: python3 -m pip install -U 'huggingface_hub[cli]'" >&2
  exit 1
fi

mkdir -p "${MODEL_DIR}"
"${HF_CMD[@]}" \
  --local-dir "${MODEL_DIR}" \
  "${HF_REPO}" \
  --include "${MODEL_NAME}/*"

if [ ! -d "${MODEL_DIR}/${MODEL_NAME}" ]; then
  echo "Download finished but ${MODEL_NAME} was not found under ${MODEL_DIR}." >&2
  echo "Check whether the upstream repo changed file names: ${HF_REPO}" >&2
  exit 1
fi

echo "Installed ${MODEL_DIR}/${MODEL_NAME}"
du -sh "${MODEL_DIR}/${MODEL_NAME}" || true
