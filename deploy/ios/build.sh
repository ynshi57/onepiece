#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"

ensure_ios_project_exists

DYNAMIC_DESTINATION="${DESTINATION:-}"
SIGNING_ARGS=()
if [ "${SDK}" = "iphonesimulator" ]; then
  DYNAMIC_DESTINATION="${DYNAMIC_DESTINATION:-platform=iOS Simulator,name=iPhone 17}"
  SIGNING_ARGS=(CODE_SIGNING_ALLOWED=NO)
else
  DYNAMIC_DESTINATION="${DYNAMIC_DESTINATION:-generic/platform=iOS}"
fi

log "Starting iOS build"
xcodebuild \
  "${XCODE_TARGET_ARGS[@]}" \
  -scheme "${SCHEME}" \
  -configuration "${CONFIGURATION}" \
  -sdk "${SDK}" \
  -destination "${DYNAMIC_DESTINATION}" \
  clean build \
  "${SIGNING_ARGS[@]}" | tee "${LOG_DIR}/ios-build.log"

log "Build completed"
