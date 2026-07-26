#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"

ensure_ios_project_exists

MODE="${1:-patch}"
BUILD_NUMBER="${BUILD_NUMBER:-$(date +%Y%m%d%H%M)}"

require_cmd xcodebuild
require_cmd xcrun

build_settings="$(xcodebuild "${XCODE_TARGET_ARGS[@]}" -scheme "${SCHEME}" -showBuildSettings 2>/dev/null)"
CURRENT_VERSION="$(printf '%s\n' "${build_settings}" | awk -F' = ' '/MARKETING_VERSION/ {print $2; exit}')"
if [ -z "${CURRENT_VERSION}" ]; then
  echo "Unable to determine MARKETING_VERSION for scheme ${SCHEME}."
  exit 1
fi

IFS='.' read -r major minor patch <<< "${CURRENT_VERSION}"

case "${MODE}" in
  major)
    major=$((major + 1))
    minor=0
    patch=0
    ;;
  minor)
    minor=$((minor + 1))
    patch=0
    ;;
  patch)
    patch=$((patch + 1))
    ;;
  *)
    echo "Unsupported mode: ${MODE}. Use major|minor|patch."
    exit 1
    ;;
esac

NEXT_VERSION="${major}.${minor}.${patch}"

(
  cd "$(dirname "${PROJECT_PATH}")"
  xcrun agvtool new-marketing-version "${NEXT_VERSION}" >/dev/null
  xcrun agvtool new-version -all "${BUILD_NUMBER}" >/dev/null
)

log "Version bumped to ${NEXT_VERSION} (${BUILD_NUMBER})"
