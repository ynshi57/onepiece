#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_MODEL_DIR="${ROOT_DIR}/ios-vqa-app/VQASee/VQASee"
COREML_COMPILER="/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/coremlcompiler"
MODEL_INPUT="${1:-}"
OUTPUT_NAME="VQASeeTraversabilitySegmentation.mlmodelc"

if [ -z "${MODEL_INPUT}" ]; then
  cat >&2 <<EOF
Usage: bash deploy/ios/install_traversability_segmentation_model.sh /path/to/model.mlpackage-or-mlmodel

Expected model contract:
- single-channel output mask (pixel buffer or MLMultiArray)
- higher values mean more traversable/floor-like
- model will be installed as ${OUTPUT_NAME}
EOF
  exit 2
fi

if [ ! -e "${MODEL_INPUT}" ]; then
  echo "Model not found: ${MODEL_INPUT}" >&2
  exit 1
fi

if [ ! -x "${COREML_COMPILER}" ]; then
  echo "coremlcompiler not found at ${COREML_COMPILER}" >&2
  exit 1
fi

TMP_OUT="${TMPDIR:-/tmp}/vqasee-traversability-compiled"
rm -rf "${TMP_OUT}"
mkdir -p "${TMP_OUT}" "${APP_MODEL_DIR}"

"${COREML_COMPILER}" compile "${MODEL_INPUT}" "${TMP_OUT}"
compiled="$(find "${TMP_OUT}" -maxdepth 1 -name '*.mlmodelc' -type d | head -1)"
if [ -z "${compiled}" ]; then
  echo "coremlcompiler did not produce an .mlmodelc" >&2
  exit 1
fi

rm -rf "${APP_MODEL_DIR}/${OUTPUT_NAME}"
cp -R "${compiled}" "${APP_MODEL_DIR}/${OUTPUT_NAME}"

echo "Installed ${APP_MODEL_DIR}/${OUTPUT_NAME}"
du -sh "${APP_MODEL_DIR}/${OUTPUT_NAME}" || true
