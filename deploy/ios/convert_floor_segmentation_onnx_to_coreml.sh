#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
WORK_DIR="${TMPDIR:-/tmp}/vqasee-floor-segmentation"
MODEL_REPO="Tanishjain9/fast-scnn-floor-segmentation"
COREML_OUT="${WORK_DIR}/VQASeeTraversabilitySegmentation.mlpackage"
INSTALL_SCRIPT="${ROOT_DIR}/deploy/ios/install_traversability_segmentation_model.sh"

if [ ! -d "${VENV_DIR}" ]; then
  echo "Missing virtualenv: ${VENV_DIR}" >&2
  echo "Run: bash deploy/ios/install_deps.sh" >&2
  exit 1
fi
source "${VENV_DIR}/bin/activate"

if command -v hf >/dev/null 2>&1; then
  HF_CMD=(hf download)
elif command -v huggingface-cli >/dev/null 2>&1; then
  HF_CMD=(huggingface-cli download)
else
  echo "Neither 'hf' nor 'huggingface-cli' was found." >&2
  echo "Install with: brew install huggingface-cli" >&2
  echo "or: python3 -m pip install -U 'huggingface_hub[cli]'" >&2
  exit 1
fi

rm -rf "${WORK_DIR}"
mkdir -p "${WORK_DIR}"

echo "Downloading Fast-SCNN floor segmentation ONNX from ${MODEL_REPO}..."
if [ -n "${HF_ENDPOINT:-}" ]; then
  echo "Using HF_ENDPOINT=${HF_ENDPOINT}"
fi
if ! "${HF_CMD[@]}" \
  --local-dir "${WORK_DIR}" \
  "${MODEL_REPO}" \
  --include "*.onnx*" \
  --include "*.pth"; then
  cat >&2 <<EOF

Failed to download from Hugging Face.
This is usually a network/DNS/proxy issue, not a VQASee code issue.

Try one of these:
  1) Use a network/VPN/proxy that can reach https://huggingface.co
  2) If you are in a network where Hugging Face is blocked, try a mirror explicitly:
       HF_ENDPOINT=https://hf-mirror.com bash deploy/ios/convert_floor_segmentation_onnx_to_coreml.sh
  3) Manually download the ONNX from ${MODEL_REPO}, then run the conversion/install step separately.

Quick checks:
  curl -I https://huggingface.co
  curl -I https://hf-mirror.com
EOF
  exit 1
fi

onnx_path="$(find "${WORK_DIR}" -name '*.onnx' -type f | head -1)"
if [ -z "${onnx_path}" ]; then
  echo "No ONNX file was downloaded from ${MODEL_REPO}." >&2
  echo "Inspect repo files manually and update this script." >&2
  exit 1
fi

echo "Downloaded ONNX: ${onnx_path}"
pth_path="$(find "${WORK_DIR}" -name '*.pth' -type f | head -1)"
if [ -f "${onnx_path}.data" ]; then
  echo "Downloaded external weights: ${onnx_path}.data"
else
  echo "Warning: ${onnx_path}.data not found. Falling back to PyTorch checkpoint conversion if available." >&2
  if [ -n "${pth_path}" ]; then
    echo "Using checkpoint: ${pth_path}"
    python "${ROOT_DIR}/deploy/ios/convert_fast_scnn_floor_pth_to_coreml.py" "${pth_path}" "${COREML_OUT}"
    bash "${INSTALL_SCRIPT}" "${COREML_OUT}"
    exit 0
  fi
fi

python - <<'PY'
import importlib.util
missing = [name for name in ["onnx", "coremltools"] if importlib.util.find_spec(name) is None]
if missing:
    raise SystemExit("Missing Python packages: " + ", ".join(missing) + "\nInstall: python -m pip install onnx coremltools")
import coremltools as ct
print("coremltools", ct.__version__)
if not hasattr(ct.converters, "onnx"):
    raise SystemExit(
        "coremltools in this environment has no ONNX converter.\n"
        "Downloaded the ONNX model successfully, but conversion cannot continue.\n"
        "Options:\n"
        "  1) Use a compatible onnx-coreml / coremltools conversion environment.\n"
        "  2) Re-export the PyTorch model as a TorchScript/MLProgram and convert with coremltools.convert.\n"
        "  3) Provide an already converted .mlpackage to:\n"
        "     bash deploy/ios/install_traversability_segmentation_model.sh /path/to/model.mlpackage\n"
    )
PY

# This block is intentionally only reached in environments where the legacy ONNX
# converter is available.
ONNX_PATH="${onnx_path}" COREML_OUT="${COREML_OUT}" python - <<'PY'
from pathlib import Path
import os
import coremltools as ct
onnx_path = Path(os.environ["ONNX_PATH"])
out = Path(os.environ["COREML_OUT"])
model = ct.converters.onnx.convert(model=str(onnx_path), minimum_ios_deployment_target="16")
model.save(str(out))
print(f"Saved {out}")
PY

bash "${INSTALL_SCRIPT}" "${COREML_OUT}"
