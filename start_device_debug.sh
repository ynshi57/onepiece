#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${TASK_ROOT}"
if [ ! -x .venv/bin/python ]; then
  echo '请先运行 bash setup_mac.sh --skip-models --skip-qwen --skip-ios' >&2
  exit 1
fi
exec .venv/bin/python server-vqa/tools/start_device_debug.py "$@"
