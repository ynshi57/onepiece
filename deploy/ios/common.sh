#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IOS_DIR="${ROOT_DIR}/ios-vqa-app"
LOG_DIR="${ROOT_DIR}/deploy/logs"

if [ -x "/opt/homebrew/opt/ruby/bin/ruby" ]; then
  export PATH="/opt/homebrew/opt/ruby/bin:${PATH}"
fi

DEVELOPER_DIR_DEFAULT="/Applications/Xcode.app/Contents/Developer"
export DEVELOPER_DIR="${DEVELOPER_DIR:-${DEVELOPER_DIR_DEFAULT}}"

SCHEME="${SCHEME:-VQASee}"
CONFIGURATION="${CONFIGURATION:-Release}"
SDK="${SDK:-iphoneos}"
PROJECT_PATH_DEFAULT=""
if [ -z "${WORKSPACE:-}" ] && [ -z "${PROJECT_PATH:-}" ]; then
  shopt -s nullglob
  project_candidates=("${IOS_DIR}"/*.xcodeproj "${IOS_DIR}"/*/*.xcodeproj)
  shopt -u nullglob
  for candidate in "${project_candidates[@]}"; do
    if [[ "${candidate}" != *"/vendor/"* ]]; then
      PROJECT_PATH_DEFAULT="${candidate}"
      break
    fi
  done
fi
PROJECT_PATH="${PROJECT_PATH:-${WORKSPACE:-${PROJECT_PATH_DEFAULT:-${IOS_DIR}/${SCHEME}/${SCHEME}.xcodeproj}}}"
ARCHIVE_PATH="${ARCHIVE_PATH:-${ROOT_DIR}/build/${SCHEME}.xcarchive}"
EXPORT_PATH="${EXPORT_PATH:-${ROOT_DIR}/build/export}"
EXPORT_OPTIONS_PLIST="${EXPORT_OPTIONS_PLIST:-${ROOT_DIR}/deploy/ios/ExportOptions.plist}"

mkdir -p "${LOG_DIR}"

XCODE_TARGET_ARGS=()
if [[ "${PROJECT_PATH}" == *.xcworkspace ]]; then
  XCODE_TARGET_ARGS=(-workspace "${PROJECT_PATH}")
else
  XCODE_TARGET_ARGS=(-project "${PROJECT_PATH}")
fi

require_cmd() {
  local name="$1"
  if ! command -v "${name}" >/dev/null 2>&1; then
    echo "Missing command: ${name}"
    return 1
  fi
}

timestamp() {
  date "+%Y-%m-%d %H:%M:%S"
}

log() {
  echo "[$(timestamp)] $*"
}

ensure_ios_project_exists() {
  if [ ! -d "${PROJECT_PATH}" ]; then
    echo "iOS project not found: ${PROJECT_PATH}"
    echo "Please initialize the iOS project in Xcode first."
    return 1
  fi
}
