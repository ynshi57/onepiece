#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"

ensure_ios_project_exists

if [ ! -f "${EXPORT_OPTIONS_PLIST}" ]; then
  echo "Missing ExportOptions plist: ${EXPORT_OPTIONS_PLIST}"
  echo "Copy deploy/ios/ExportOptions.plist.template to deploy/ios/ExportOptions.plist and fill values."
  exit 1
fi

mkdir -p "$(dirname "${ARCHIVE_PATH}")" "${EXPORT_PATH}"

log "Archiving iOS app"
xcodebuild \
  "${XCODE_TARGET_ARGS[@]}" \
  -scheme "${SCHEME}" \
  -configuration "${CONFIGURATION}" \
  -archivePath "${ARCHIVE_PATH}" \
  archive | tee "${LOG_DIR}/ios-archive.log"

log "Exporting IPA"
xcodebuild \
  -exportArchive \
  -archivePath "${ARCHIVE_PATH}" \
  -exportPath "${EXPORT_PATH}" \
  -exportOptionsPlist "${EXPORT_OPTIONS_PLIST}" | tee "${LOG_DIR}/ios-export.log"

log "Archive and export completed: ${EXPORT_PATH}"
