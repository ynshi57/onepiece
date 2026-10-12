#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TASK_BUILD="${VQASEE_SIFT_BUILD_DIR:-$TASK_ROOT/native/spatial_sift/build}"
command -v cmake >/dev/null || { echo '缺少 CMake，请安装 CMake 后重新运行。' >&2; exit 1; }
command -v clang++ >/dev/null || { echo '缺少编译器，请安装 Xcode Command Line Tools。' >&2; exit 1; }
test -f "$TASK_ROOT/native/spatial_sift/vendor/opencv/modules/features2d/src/sift.dispatch.cpp" || { echo '缺少固定版本 OpenCV 源码；请恢复 native/spatial_sift/vendor/opencv。' >&2; exit 1; }
cmake -S "$TASK_ROOT/native/spatial_sift" -B "$TASK_BUILD" -DCMAKE_BUILD_TYPE=Release
cmake --build "$TASK_BUILD" --target spatial_sift --parallel "${VQASEE_BUILD_JOBS:-4}"
echo "源码 SIFT 已构建：$TASK_BUILD"
