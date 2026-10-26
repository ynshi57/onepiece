#!/usr/bin/env bash
# Snapshot Codex/Cursor indoor scene-hypothesis handoff (logs + discussion tail).
# Usage: bash deploy/ios/monitor_codex_scene_handoff.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DISC="$ROOT/CODEX_INDOOR_SCENE_DISCUSSION.md"
echo "=== Codex scene handoff monitor $(date '+%Y-%m-%d %H:%M:%S %z') ==="
echo "Discussion: $DISC ($(wc -l < "$DISC" | tr -d ' ') lines, mtime $(stat -f '%Sm' -t '%Y-%m-%d %H:%M' "$DISC" 2>/dev/null || stat -c '%y' "$DISC" 2>/dev/null | cut -c1-16))"
echo
for log in \
  /tmp/vqasee-scene-preview-ui-final.log \
  /tmp/vqasee-scene-preview-ui-retest.log \
  /tmp/vqasee-scene-preview-ui.log \
  /tmp/vqasee-scene-publish-regression.log \
  /tmp/vqasee-scene-cursor-scene-regression.log \
  /tmp/vqasee-scene-derived-retest.log \
  /tmp/vqasee-scene-publish-device-rebuild.log \
  /tmp/vqasee-scene-publish-device-build.log; do
  if [[ -f "$log" ]]; then
    result=$(grep -E '\*\* TEST (SUCCEEDED|FAILED) \*\*|Testing failed:' "$log" 2>/dev/null | tail -1 || true)
    [[ -z "$result" ]] && result="(no final TEST line yet — may be in progress)"
    echo "LOG $(basename "$log") | mtime $(stat -f '%Sm' -t '%H:%M:%S' "$log" 2>/dev/null || stat -c '%y' "$log" | cut -c12-19) | $result"
  fi
done
echo
echo "--- Last 8 non-empty lines of discussion (Codex/Cursor) ---"
grep -n '^## ' "$DISC" | tail -5
echo
tail -n 12 "$DISC" | sed '/^$/d'
echo
echo "--- UI attachments ---"
if [[ -d /tmp/vqasee-scene-ui-attachments ]]; then
  ls -lt /tmp/vqasee-scene-ui-attachments/*.png 2>/dev/null | head -5 || true
fi
