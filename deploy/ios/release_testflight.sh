#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"

ensure_ios_project_exists

if [ ! -f "${IOS_DIR}/Gemfile" ]; then
  echo "Gemfile not found in ${IOS_DIR}. Please run deploy/ios/install_deps.sh first."
  exit 1
fi

if [ ! -f "${IOS_DIR}/fastlane/Fastfile" ]; then
  echo "fastlane/Fastfile not found. Please complete fastlane setup."
  exit 1
fi

log "Running fastlane beta lane"
(
  cd "${IOS_DIR}"
  bundle exec fastlane beta
) | tee "${LOG_DIR}/ios-testflight.log"

log "TestFlight upload flow finished"
