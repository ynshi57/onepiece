#!/usr/bin/env bash
# Install the OUTDOOR (Cityscapes) Fast-SCNN traversability model into the VQASee app.
#
# Phase 1 of the traversable-region model swap: replaces the indoor floor model
# (Tanishjain9/fast-scnn-floor-segmentation) — which mislabels outdoor scenes
# (e.g. sky as walkable) — with Cityscapes-trained Fast-SCNN weights. Same
# architecture, correct outdoor domain. On CamVid this took region IoU 0.63->0.77,
# precision 0.80->0.97, and region false-go frames 118->0.
#
# Weights source: Tramac/Fast-SCNN-pytorch weights/fast_scnn_citys.pth (GitHub,
# ~4.7MB, 19 Cityscapes classes). The converter reduces the 19 classes to VQASee's
# 2-channel {notTrav, trav} contract (traversable = road+sidewalk) and bakes in
# ImageNet normalization. See convert_fast_scnn_cityscapes_pth_to_coreml.py.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
CACHE_DIR="${HOME}/.cache/vqasee/models"
CKPT="${CACHE_DIR}/fast_scnn_citys.pth"
COREML_OUT="${CACHE_DIR}/VQASeeTraversabilitySegmentation.mlpackage"
CONVERT_PY="${ROOT_DIR}/deploy/ios/convert_fast_scnn_cityscapes_pth_to_coreml.py"
INSTALL_SCRIPT="${ROOT_DIR}/deploy/ios/install_traversability_segmentation_model.sh"
# Pinned raw URL so the download is reproducible; mirror via CITYS_WEIGHTS_URL if
# GitHub raw is blocked on your network.
WEIGHTS_URL="${CITYS_WEIGHTS_URL:-https://raw.githubusercontent.com/Tramac/Fast-SCNN-pytorch/master/weights/fast_scnn_citys.pth}"

if [ ! -d "${VENV_DIR}" ]; then
  echo "Missing virtualenv: ${VENV_DIR}" >&2
  echo "Run: bash deploy/ios/install_deps.sh  (needs torch + coremltools)" >&2
  exit 1
fi
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

python - <<'PY'
import importlib.util, sys
missing = [n for n in ("torch", "coremltools") if importlib.util.find_spec(n) is None]
if missing:
    sys.exit("Missing Python packages: " + ", ".join(missing) + "\nInstall: pip install torch coremltools")
PY

mkdir -p "${CACHE_DIR}"

if [ ! -f "${CKPT}" ]; then
  echo "Downloading Cityscapes Fast-SCNN weights -> ${CKPT}"
  if ! curl -fSL -o "${CKPT}" "${WEIGHTS_URL}"; then
    cat >&2 <<EOF

Failed to download Cityscapes weights from:
  ${WEIGHTS_URL}
This is usually a network/proxy issue, not a VQASee code issue. Try:
  1) A network/VPN that can reach raw.githubusercontent.com
  2) Set a mirror: CITYS_WEIGHTS_URL=<your-mirror-url> bash $0
  3) Manually place fast_scnn_citys.pth at ${CKPT} and re-run.
EOF
    exit 1
  fi
else
  echo "Using cached weights: ${CKPT}"
fi

echo "Converting to Core ML (19-class -> 2-channel traversable contract)..."
python "${CONVERT_PY}" "${CKPT}" "${COREML_OUT}"

echo "Compiling + installing into the app..."
bash "${INSTALL_SCRIPT}" "${COREML_OUT}"

echo "Done. Rebuild/rerun the perception harness to re-evaluate:"
echo "  (cd ios-vqa-app/perception-harness && swift build)"
echo "  .build/debug/PerceptionHarness --manifest docs/datasets/camvid-manifest.jsonl --out docs/datasets/camvid-ios-harness.jsonl"
