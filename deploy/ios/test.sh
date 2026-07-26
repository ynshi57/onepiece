#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"

ensure_ios_project_exists

DESTINATION="${DESTINATION:-platform=iOS Simulator,name=iPhone 17}"

log "Running iOS tests on destination: ${DESTINATION}"
xcodebuild \
  "${XCODE_TARGET_ARGS[@]}" \
  -scheme "${SCHEME}" \
  -configuration Debug \
  -destination "${DESTINATION}" \
  test | tee "${LOG_DIR}/ios-test.log"

log "Test run completed"
