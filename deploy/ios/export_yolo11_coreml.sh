#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
APP_MODEL_DIR="${ROOT_DIR}/ios-vqa-app/VQASee/VQASee"
WORK_DIR="${TMPDIR:-/tmp}/vqasee-yolo-export"
COMPILED_DIR="${TMPDIR:-/tmp}/vqasee-yolo-compiled"
COREML_COMPILER="/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/coremlcompiler"

if [ ! -d "${VENV_DIR}" ]; then
  echo "Missing virtualenv: ${VENV_DIR}" >&2
  echo "Run: bash deploy/ios/install_deps.sh" >&2
  exit 1
fi

source "${VENV_DIR}/bin/activate"

python - <<'PY'
missing = []
for name in ["ultralytics", "coremltools"]:
    try:
        __import__(name)
    except Exception:
        missing.append(name)
if missing:
    raise SystemExit("Missing Python packages: " + ", ".join(missing) + "\nRun: python -m pip install ultralytics coremltools")
PY

if [ ! -x "${COREML_COMPILER}" ]; then
  echo "coremlcompiler not found at ${COREML_COMPILER}" >&2
  exit 1
fi

rm -rf "${WORK_DIR}" "${COMPILED_DIR}"
mkdir -p "${WORK_DIR}" "${COMPILED_DIR}"

(
  cd "${WORK_DIR}"
  # Detection model with NMS: Vision can expose VNRecognizedObjectObservation boxes.
  yolo export model=yolo11n.pt format=coreml nms=True imgsz=640
  "${COREML_COMPILER}" compile "${WORK_DIR}/yolo11n.mlpackage" "${COMPILED_DIR}"
)

rm -rf "${APP_MODEL_DIR}/YOLO11nObject.mlmodelc"
cp -R "${COMPILED_DIR}/yolo11n.mlmodelc" "${APP_MODEL_DIR}/YOLO11nObject.mlmodelc"

echo "Exported ${APP_MODEL_DIR}/YOLO11nObject.mlmodelc"
du -sh "${APP_MODEL_DIR}/YOLO11nObject.mlmodelc"
