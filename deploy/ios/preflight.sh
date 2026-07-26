#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"

log "Running iOS preflight checks"

require_cmd xcodebuild
require_cmd plutil

if [ ! -d "${DEVELOPER_DIR}" ]; then
  echo "DEVELOPER_DIR not found: ${DEVELOPER_DIR}"
  exit 1
fi

log "Using DEVELOPER_DIR=${DEVELOPER_DIR}"
log "xcodebuild version:"
xcodebuild -version | tee "${LOG_DIR}/xcode-version.log"

if [ -f "${EXPORT_OPTIONS_PLIST}" ]; then
  plutil -lint "${EXPORT_OPTIONS_PLIST}" | tee "${LOG_DIR}/export-options-lint.log"
else
  log "ExportOptions.plist not found yet: ${EXPORT_OPTIONS_PLIST}"
fi

if [ -f "${IOS_DIR}/Gemfile" ]; then
  require_cmd bundle
  log "Bundler detected for fastlane flow"
fi

if [ -d "${PROJECT_PATH}" ]; then
  log "Found iOS project: ${PROJECT_PATH}"
else
  log "No iOS project found yet. Initialize via Xcode when ready."
fi

log "Preflight checks passed"
