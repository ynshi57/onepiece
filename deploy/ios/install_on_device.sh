#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"

ensure_ios_project_exists
require_cmd xcodebuild
require_cmd xcrun
require_cmd python3

DEVICE_ID="${DEVICE_ID:-}"
DEVICE_NAME="${DEVICE_NAME:-}"
DERIVED_DATA_PATH="${DERIVED_DATA_PATH:-${ROOT_DIR}/build/DerivedDataDevice}"
ALLOW_PROVISIONING_UPDATES="${ALLOW_PROVISIONING_UPDATES:-1}"

if [ -z "${DEVICE_ID}" ]; then
  DEVICE_JSON_PATH="${LOG_DIR}/device-list.json"
  xcrun devicectl list devices --json-output "${DEVICE_JSON_PATH}" >/dev/null
  if [ -n "${DEVICE_NAME}" ]; then
    DEVICE_ID="$(python3 - "${DEVICE_JSON_PATH}" "${DEVICE_NAME}" <<'PY'
import json
import sys

path = sys.argv[1]
name = sys.argv[2].lower()
with open(path, "r", encoding="utf-8") as fp:
    data = json.load(fp)

result = ""
for device in data.get("result", {}).get("devices", []):
    if not device.get("hardwareProperties", {}).get("udid"):
        continue
    if device.get("deviceProperties", {}).get("name", "").lower() == name:
        result = device["hardwareProperties"]["udid"]
        break

print(result)
PY
)"
  else
    DEVICE_ID="$(python3 - "${DEVICE_JSON_PATH}" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as fp:
    data = json.load(fp)

result = ""
for device in data.get("result", {}).get("devices", []):
    hw = device.get("hardwareProperties", {})
    dev = device.get("deviceProperties", {})
    if dev.get("platform") != "iOS":
        continue
    if dev.get("name", "").lower().startswith("iphone"):
        result = hw.get("udid", "")
        if result:
            break

print(result)
PY
)"
  fi
fi

if [ -z "${DEVICE_ID}" ]; then
  echo "No iPhone device detected."
  echo "Set DEVICE_ID manually, for example:"
  echo "DEVICE_ID=<your-iphone-udid> bash deploy/ios/install_on_device.sh"
  echo ""
  echo "You can inspect connected devices via:"
  echo "xcrun devicectl list devices"
  exit 1
fi

mkdir -p "${DERIVED_DATA_PATH}"

PROVISIONING_ARGS=()
if [ "${ALLOW_PROVISIONING_UPDATES}" = "1" ]; then
  PROVISIONING_ARGS=(-allowProvisioningUpdates)
fi

log "Building app for iPhone device: ${DEVICE_ID}"
xcodebuild \
  "${XCODE_TARGET_ARGS[@]}" \
  -scheme "${SCHEME}" \
  -configuration Debug \
  -sdk iphoneos \
  -destination "id=${DEVICE_ID}" \
  -derivedDataPath "${DERIVED_DATA_PATH}" \
  build \
  "${PROVISIONING_ARGS[@]}" | tee "${LOG_DIR}/ios-device-build.log"

APP_PATH="${DERIVED_DATA_PATH}/Build/Products/Debug-iphoneos/${SCHEME}.app"
if [ ! -d "${APP_PATH}" ]; then
  echo "Built app not found: ${APP_PATH}"
  exit 1
fi

log "Installing app to device"
xcrun devicectl device install app --device "${DEVICE_ID}" "${APP_PATH}" | tee "${LOG_DIR}/ios-device-install.log"

BUNDLE_ID="$(/usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" "${APP_PATH}/Info.plist" 2>/dev/null || true)"
if [ -n "${BUNDLE_ID}" ]; then
  log "Launching app: ${BUNDLE_ID}"
  xcrun devicectl device process launch --device "${DEVICE_ID}" "${BUNDLE_ID}" --terminate-existing | tee "${LOG_DIR}/ios-device-launch.log"
else
  log "Install completed. Bundle identifier not found; launch manually from iPhone home screen."
fi

log "Device install flow completed"
