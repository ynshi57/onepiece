#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"

version_lt() {
  [ "$(printf '%s\n' "$1" "$2" | sort -V | head -n 1)" != "$2" ]
}

if [ -x "/opt/homebrew/opt/ruby/bin/ruby" ]; then
  export PATH="/opt/homebrew/opt/ruby/bin:${PATH}"
fi

log "Installing Python dependencies for local VQA server"
python3 -m venv "${ROOT_DIR}/.venv"
source "${ROOT_DIR}/.venv/bin/activate"
python -m pip install --upgrade pip
pip install -r "${ROOT_DIR}/server-vqa/requirements-dev.txt"

log "Preparing Ruby dependencies for fastlane (local vendor/bundle)"
if [ ! -f "${IOS_DIR}/Gemfile" ]; then
  echo "Gemfile missing in ${IOS_DIR}. Please ensure ios-vqa-app files are in place."
  exit 1
fi

RUBY_VERSION="$(ruby -e 'print RUBY_VERSION')"
if version_lt "${RUBY_VERSION}" "3.2.0"; then
  echo "Ruby ${RUBY_VERSION} is too old for modern fastlane dependencies."
  echo "Please run:"
  echo "  brew install ruby"
  echo "  echo 'export PATH=\"/opt/homebrew/opt/ruby/bin:\$PATH\"' >> ~/.zshrc"
  echo "  source ~/.zshrc"
  echo "Then re-run: bash deploy/ios/install_deps.sh"
  exit 2
fi

(
  cd "${IOS_DIR}"
  export BUNDLE_USER_HOME="${IOS_DIR}/.bundle-home"
  bundle config set --local path "vendor/bundle"
  bundle install
)

log "Dependency installation completed"
