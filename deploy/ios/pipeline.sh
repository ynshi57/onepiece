#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"

RUN_TESTS=1
RUN_UPLOAD=1

for arg in "$@"; do
  case "${arg}" in
    --skip-tests)
      RUN_TESTS=0
      ;;
    --skip-upload)
      RUN_UPLOAD=0
      ;;
    *)
      echo "Unsupported argument: ${arg}"
      echo "Supported: --skip-tests --skip-upload"
      exit 1
      ;;
  esac
done

"$(cd "$(dirname "$0")" && pwd)/preflight.sh"
"$(cd "$(dirname "$0")" && pwd)/build.sh"

if [ "${RUN_TESTS}" -eq 1 ]; then
  "$(cd "$(dirname "$0")" && pwd)/test.sh"
fi

"$(cd "$(dirname "$0")" && pwd)/archive.sh"

if [ "${RUN_UPLOAD}" -eq 1 ]; then
  "$(cd "$(dirname "$0")" && pwd)/release_testflight.sh"
fi

log "Pipeline completed"
