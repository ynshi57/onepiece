#!/usr/bin/env bash
# Optional fixed-source Brush CLI experiment; never product geometry acceptance.
set -euo pipefail
trap 'echo "[spatial-brush] 安装或检查失败：保留原始失败信息。请核对Rust>=1.88、固定源码、补丁、LPIPS权重及构建日志。" >&2' ERR

BRUSH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BRUSH_MODEL_ROOT="${BRUSH_ROOT}/server-vqa/models/spatial-brush"
BRUSH_SOURCE="${VQASEE_SPATIAL_BRUSH_SOURCE_ROOT:-${BRUSH_MODEL_ROOT}/source}"
BRUSH_BINARY="${VQASEE_SPATIAL_BRUSH_BINARY:-${BRUSH_MODEL_ROOT}/target/release/brush_app}"
BRUSH_PYTHON="${VQASEE_SPATIAL_BRUSH_PYTHON:-${BRUSH_ROOT}/.venv/bin/python}"
BRUSH_CARGO="${VQASEE_SPATIAL_BRUSH_CARGO:-cargo}"
BRUSH_RUSTC="${VQASEE_SPATIAL_BRUSH_RUSTC:-rustc}"
export RUSTUP_AUTO_INSTALL=0
BRUSH_CHECK=0
if [ "${1:-}" = "--check-only" ]; then BRUSH_CHECK=1; shift; fi
if [ "$#" -ne 0 ]; then echo '[spatial-brush] Usage: install_brush.sh [--check-only]' >&2; exit 2; fi
if [ ! -x "${BRUSH_PYTHON}" ]; then
  if command -v python3 >/dev/null 2>&1; then BRUSH_PYTHON=python3
  else echo '[spatial-brush] 缺少Python3；先运行setup_mac.sh core profile或安装Python3.11。' >&2; exit 1; fi
fi
if ! command -v "${BRUSH_CARGO}" >/dev/null 2>&1 || ! command -v "${BRUSH_RUSTC}" >/dev/null 2>&1; then
  echo '[spatial-brush] 缺少cargo/rustc。请从 https://rustup.rs 安装官方Rust，或用Homebrew安装rustup-init；确保rustc>=1.88后重跑。安装器不执行未经校验的curl|sh。' >&2
  exit 1
fi
if ! "${BRUSH_CARGO}" --version >/dev/null 2>&1; then
  echo '[spatial-brush] cargo无法运行；检查Rust工具链与RUSTUP_HOME后重跑。' >&2; exit 1
fi
if ! BRUSH_RUST_VERSION="$("${BRUSH_RUSTC}" --version 2>&1)"; then
  echo '[spatial-brush] rustc无法运行；检查已安装的Rust工具链与RUSTUP_HOME，至少需要1.88。' >&2; exit 1
fi
if ! "${BRUSH_PYTHON}" - "${BRUSH_RUST_VERSION}" <<'PY'
import re,sys
match=re.fullmatch(r'rustc (\d+)\.(\d+)\.(\d+)(?:[-+][^ ]+)?(?: \(.+\))?',sys.argv[1])
if match is None or tuple(map(int,match.group(1,2)))<(1,88):
    raise SystemExit('Brush需要rustc>=1.88；请通过官方rustup更新工具链后重跑：'+sys.argv[1])
PY
then exit 1; fi

if [[ "${BRUSH_BINARY}" != */release/brush_app ]]; then
  echo '[spatial-brush] VQASEE_SPATIAL_BRUSH_BINARY须指向target/release/brush_app。' >&2; exit 1
fi
BRUSH_TARGET="${BRUSH_BINARY%/release/brush_app}"
BRUSH_RECEIPT="${BRUSH_TARGET}/brush-install-receipt.json"
BRUSH_FETCH="${BRUSH_ROOT}/deploy/spatial/fetch_brush_source.py"
BRUSH_PATCH="${BRUSH_ROOT}/native/brush/patches/0001-small-image-header.patch"

if [ "${BRUSH_CHECK}" = 0 ]; then
  "${BRUSH_PYTHON}" "${BRUSH_FETCH}" --source-root "${BRUSH_SOURCE}"
  if ! "${BRUSH_PYTHON}" "${BRUSH_FETCH}" --source-root "${BRUSH_SOURCE}" --check-only --require-patch >/dev/null 2>&1; then
    if ! (cd "${BRUSH_SOURCE}" && git apply --check "${BRUSH_PATCH}" && git apply "${BRUSH_PATCH}"); then
      echo '[spatial-brush] 官方源码补丁不适用；禁止编译身份不明的修改。' >&2; exit 1
    fi
  fi
  "${BRUSH_PYTHON}" "${BRUSH_FETCH}" --source-root "${BRUSH_SOURCE}" --check-only --require-patch
  # Brush writes shader modules into its source tree from build.rs. Cargo can
  # reuse a target cache from another verified checkout without rerunning that
  # build script, leaving a new checkout without the generated modules.
  for BRUSH_CRATE in brush-prefix-sum brush-kernel brush-render-bwd brush-sort brush-render; do
    if [ ! -f "${BRUSH_SOURCE}/crates/${BRUSH_CRATE}/src/shaders/mod.rs" ]; then
      touch "${BRUSH_SOURCE}/crates/${BRUSH_CRATE}/build.rs"
    fi
  done
  mkdir -p "${BRUSH_TARGET}"
  (cd "${BRUSH_SOURCE}" && CARGO_TARGET_DIR="${BRUSH_TARGET}" "${BRUSH_CARGO}" build --locked --release -p brush-app --bin brush_app)
  if [ ! -x "${BRUSH_BINARY}" ]; then
    echo '[spatial-brush] cargo成功返回但brush_app不存在或不可执行。' >&2; exit 1
  fi
  "${BRUSH_PYTHON}" - "${BRUSH_ROOT}" "${BRUSH_BINARY}" "${BRUSH_RECEIPT}" "${BRUSH_RUST_VERSION}" <<'PY'
import hashlib,json,pathlib,sys
root,binary,receipt=map(pathlib.Path,sys.argv[1:4]);rust_version=sys.argv[4]
manifest=json.loads((root/'native/brush/source-manifest.json').read_text())
hash_state=hashlib.sha256()
with binary.open('rb') as source:
    for chunk in iter(lambda:source.read(1024*1024),b''):hash_state.update(chunk)
digest=hash_state.hexdigest()
receipt.parent.mkdir(parents=True,exist_ok=True)
receipt.write_text(json.dumps({'sourceCommit':manifest['revision'],'sourcePatch':manifest['patch']['path'],
    'sourcePatchSHA256':manifest['patch']['sha256'],'binarySHA256':digest,
    'rustcVersion':rust_version,'binaryPath':str(binary.resolve())},indent=2)+'\n')
PY
fi

"${BRUSH_PYTHON}" "${BRUSH_FETCH}" --source-root "${BRUSH_SOURCE}" --check-only --require-patch
"${BRUSH_PYTHON}" - "${BRUSH_ROOT}" "${BRUSH_BINARY}" "${BRUSH_RECEIPT}" <<'PY'
import hashlib,json,pathlib,sys
root,binary,receipt=map(pathlib.Path,sys.argv[1:4])
manifest=json.loads((root/'native/brush/source-manifest.json').read_text())
if not binary.is_file() or not receipt.is_file():raise SystemExit('Brush二进制或安装回执缺失；运行 --with-spatial-brush 构建')
proof=json.loads(receipt.read_text())
hash_state=hashlib.sha256()
with binary.open('rb') as source:
    for chunk in iter(lambda:source.read(1024*1024),b''):hash_state.update(chunk)
digest=hash_state.hexdigest()
if (proof.get('sourceCommit')!=manifest['revision'] or proof.get('sourcePatch')!=manifest['patch']['path']
        or proof.get('sourcePatchSHA256')!=manifest['patch']['sha256']
        or proof.get('binarySHA256')!=digest or proof.get('binaryPath')!=str(binary.resolve())):
    raise SystemExit('Brush源码/补丁/二进制身份不一致；重新运行 --with-spatial-brush')
print('[spatial-brush] 固定官方源码+本地补丁、LPIPS权重、Rust版本与二进制回执检查通过；训练画质仍需真实留出图验收。')
PY
if ! "${BRUSH_BINARY}" --help >/dev/null 2>&1; then
  echo '[spatial-brush] brush_app 无法执行 --help；检查目标架构或重新构建。' >&2; exit 1
fi
