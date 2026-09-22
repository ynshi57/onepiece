#!/usr/bin/env bash
# VQASee one-shot Mac setup.
#
# Assumes the repo is ALREADY cloned; installs everything needed to run VQASee on
# a fresh Mac. Four independent profiles (all on by default):
#
#   core   - Python venv + backend/diagnostics deps + offline perception harness
#   models - Core ML models (traversability segmentation; optional depth)
#   qwen   - local Qwen VQA runtime (Ollama + qwen2.5vl:3b)
#   ios    - full iOS build toolchain (needs full Xcode + Ruby/bundler)
#
# Idempotent and re-runnable. Failures are reported explicitly in a final summary
# (no silent failure); one profile failing does not abort the others.
#
# This is the SINGLE install source of truth. Per AGENTS.md「环境与依赖同步规则」,
# whenever VQASee's dependencies / models / tools / product paths change, this
# script + the matching requirements*.txt + the README install section MUST be
# updated in the same change and re-verified. Do not let it silently rot.
#
# Usage:
#   bash setup_mac.sh [--skip-core] [--skip-models] [--skip-qwen] [--skip-ios]
#                     [--with-depth] [--hf-mirror] [-y|--yes]
#
# Notes:
#   --with-depth  also install the optional DepthAnythingV2 Core ML model
#   --hf-mirror   route Hugging Face downloads through https://hf-mirror.com
#                 (or honor a pre-set HF_ENDPOINT)

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

# --- options ----------------------------------------------------------------
DO_CORE=1
DO_MODELS=1
DO_QWEN=1
DO_IOS=1
WITH_DEPTH=0
HF_MIRROR=0
ASSUME_YES=0

SUMMARY=()

log()  { echo "[setup] $*"; }
warn() { echo "[setup][warn] $*" >&2; }
err()  { echo "[setup][error] $*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }
add_summary() { SUMMARY+=("$1"); }

# version_ge A B  -> true if A >= B (semantic-ish, via sort -V)
version_ge() { [ "$(printf '%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]; }

usage() {
  # Print the leading comment header (line 2 until the first non-comment line),
  # stripping the "# " prefix. Adapts automatically if the header grows.
  awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "${BASH_SOURCE[0]}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --skip-core)   DO_CORE=0 ;;
    --skip-models) DO_MODELS=0 ;;
    --skip-qwen)   DO_QWEN=0 ;;
    --skip-ios)    DO_IOS=0 ;;
    --with-depth)  WITH_DEPTH=1 ;;
    --hf-mirror)   HF_MIRROR=1 ;;
    -y|--yes)      ASSUME_YES=1 ;;
    -h|--help)     usage; exit 0 ;;
    *) err "unknown argument: $1"; usage; exit 2 ;;
  esac
  shift
done

# --- shared helpers ---------------------------------------------------------

ensure_venv() {
  # Create (once) and activate the repo virtualenv. Shared by core/models/ios.
  # Prefer 3.11+ because the backend uses PEP 604 unions and current FastAPI /
  # pydantic wheels assume it. `python3` on some Macs is still 3.9.
  if [ ! -d .venv ]; then
    local py=""
    local cand
    for cand in python3.13 python3.12 python3.11 python3; do
      if have "${cand}"; then
        py="$(command -v "${cand}")"
        break
      fi
    done
    if [ -z "${py}" ]; then
      err "python3 未找到。装 Xcode Command Line Tools（xcode-select --install）或 brew install python@3.11"
      return 1
    fi
    local pyver
    pyver="$("${py}" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    if ! version_ge "${pyver}" "3.11"; then
      err "需要 Python >= 3.11（当前 ${py} 是 ${pyver}）。brew install python@3.11 后重跑。"
      return 1
    fi
    log "创建 venv：${py} (${pyver})"
    "${py}" -m venv .venv || return 1
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate || return 1
  python -m pip install --upgrade pip >/dev/null 2>&1 || true
  return 0
}

coremlcompiler_available() {
  local hard="/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/coremlcompiler"
  [ -x "${hard}" ] || xcrun --find coremlcompiler >/dev/null 2>&1
}

# --- environment detection --------------------------------------------------

detect_env() {
  log "==== 环境检测 ===="
  log "macOS $(sw_vers -productVersion 2>/dev/null || echo '?') · $(uname -m)"

  if have brew; then
    log "Homebrew: $(brew --version 2>/dev/null | head -n1)"
  else
    warn "未检测到 Homebrew。部分 profile（qwen/ios）需要它：见 https://brew.sh"
  fi

  if have python3; then
    log "python3: $(python3 --version 2>&1)"
  else
    warn "python3 未找到（core/models 需要）"
  fi

  if have swift; then
    log "swift: $(swift --version 2>/dev/null | head -n1)"
  else
    warn "swift 未找到（harness 编译需要，装 Xcode CLT: xcode-select --install）"
  fi

  local devdir
  devdir="$(xcode-select -p 2>/dev/null || true)"
  if [[ "${devdir}" == *"/Xcode.app/"* ]]; then
    log "Xcode: 完整 Xcode.app（${devdir}）"
  else
    log "Xcode: 仅 Command Line Tools（${devdir:-未设置}）——iOS 构建与模型转换需完整 Xcode.app"
  fi
  echo
}

# --- profile: core ----------------------------------------------------------

profile_core() {
  log "==== profile: core（后端 + 闭环平台 + 离线 harness）===="
  if ! ensure_venv; then
    add_summary "FAIL|core|venv 创建失败"
    return 1
  fi

  log "安装后端/平台依赖 (requirements-dev.txt)"
  if ! pip install -r server-vqa/requirements-dev.txt; then
    add_summary "FAIL|core|requirements-dev 安装失败"
    return 1
  fi
  # app.main imports app.diagnostic_api at module load, which needs numpy + PIL.
  # They are declared in requirements.txt now; verify explicitly so an older
  # checkout or a partial install fails loudly here instead of much later at
  # `uvicorn app.main:app` time (No Silent Failures).
  if ! python -c "import numpy, PIL" >/dev/null 2>&1; then
    warn "numpy/Pillow 缺失（旧 checkout 或安装不完整）——补装一次"
    if ! pip install numpy Pillow; then
      add_summary "FAIL|core|numpy/Pillow 安装失败，app.main 无法导入"
      deactivate 2>/dev/null || true
      return 1
    fi
  fi

  log "冒烟测试：pytest server-vqa/tests（会导入含 PIL 的 app.main）"
  if pytest server-vqa/tests -q; then
    add_summary "OK|core|venv + 依赖 + pytest 全绿"
  else
    add_summary "FAIL|core|pytest 未通过（平台依赖可能不全）"
    deactivate 2>/dev/null || true
    return 1
  fi
  deactivate 2>/dev/null || true

  if have swift; then
    log "编译离线感知 harness (swift build)"
    if ( cd ios-vqa-app/perception-harness && swift build ) \
       && [ -x ios-vqa-app/perception-harness/.build/debug/PerceptionHarness ]; then
      add_summary "OK|core|perception-harness 编译成功"
    else
      add_summary "FAIL|core|harness 未编译成功"
    fi
  else
    add_summary "SKIP|core|swift 缺失，未编译 harness（xcode-select --install）"
  fi

  if [ -d ios-vqa-app/VQASee/VQASee/YOLO11nObject.mlmodelc ]; then
    add_summary "OK|core|YOLO11nObject.mlmodelc 在位（随仓库提交）"
  else
    add_summary "WARN|core|YOLO 模型缺失（应随仓库；检查 clone 是否完整）"
  fi
  return 0
}

# --- profile: models --------------------------------------------------------

profile_models() {
  log "==== profile: models（Core ML 模型）===="
  if ! ensure_venv; then
    add_summary "FAIL|models|venv 不可用（先跑 core 或修 python3）"
    return 1
  fi

  # torch is required by convert_fast_scnn_cityscapes_pth_to_coreml.py (it loads
  # the .pth and traces the graph before coremltools converts it). It is not in
  # any requirements*.txt because it is only needed for model conversion, so it
  # must be installed here or the segmentation step aborts on a fresh Mac.
  log "安装模型转换依赖 (torch + torchvision + huggingface_hub[cli] + coremltools + onnx)"
  if ! pip install -U torch torchvision "huggingface_hub[cli]" coremltools onnx; then
    add_summary "FAIL|models|模型转换依赖安装失败"
    deactivate 2>/dev/null || true
    return 1
  fi

  # PyTorch ships no macOS x86_64 wheels after 2.2.2, and torch<2.3 is built
  # against the numpy 1.x ABI. Paired with numpy>=2 every conversion dies late
  # with "RuntimeError: Numpy is not available". Detect the broken pairing and
  # pin numpy back; the backend and its tests run fine on numpy 1.26.
  if ! python -c "import torch; torch.zeros(1).numpy()" >/dev/null 2>&1; then
    warn "torch 与 numpy ABI 不兼容（Intel Mac 上 torch 封顶 2.2.2，需 numpy<2）——降级 numpy"
    if ! pip install "numpy<2"; then
      add_summary "FAIL|models|numpy<2 降级失败，模型转换无法进行"
      deactivate 2>/dev/null || true
      return 1
    fi
  fi

  if ! coremlcompiler_available; then
    warn "未找到 coremlcompiler——需要完整 Xcode.app（当前可能只有 CLT）。"
    warn "装好 Xcode 后重跑：bash setup_mac.sh --skip-core --skip-qwen --skip-ios"
    add_summary "SKIP|models|coremlcompiler 缺失（需完整 Xcode），未转换分割模型"
    deactivate 2>/dev/null || true
    return 1
  fi

  if [ "${HF_MIRROR}" = "1" ]; then
    export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
    log "使用 HF 镜像 HF_ENDPOINT=${HF_ENDPOINT}"
  fi

  local seg_dir="ios-vqa-app/VQASee/VQASee/VQASeeTraversabilitySegmentation.mlmodelc"
  if [ -d "${seg_dir}" ]; then
    add_summary "OK|models|通行分割模型已存在（跳过下载）"
  else
    # 户外通行分割：Cityscapes 版 Fast-SCNN（GitHub 直下权重 → 归约到 2 通道
    # 可走契约 + ImageNet 归一化 → Core ML）。取代早期室内地板权重（会把户外
    # 天空/建筑误判为可走）。CamVid 上 region IoU 0.63→0.77、precision 0.80→0.97、
    # 安全关键的 region_false_go 118→0。旧 floor 脚本保留作回退。
    log "下载并转换户外通行分割模型（Cityscapes Fast-SCNN → Core ML）"
    if bash deploy/ios/convert_fast_scnn_cityscapes_to_coreml.sh; then
      add_summary "OK|models|户外通行分割模型（Cityscapes）下载+转换+安装完成"
    else
      add_summary "FAIL|models|分割模型安装失败（多为网络；可设 CITYS_WEIGHTS_URL 镜像）"
    fi
  fi

  if [ "${WITH_DEPTH}" = "1" ]; then
    log "安装可选深度模型 DepthAnythingV2"
    if bash deploy/ios/install_depth_anything_v2_small.sh; then
      add_summary "OK|models|深度模型已安装"
    else
      add_summary "FAIL|models|深度模型安装失败（网络/HF）"
    fi
  else
    add_summary "SKIP|models|深度模型（加 --with-depth 才装）"
  fi

  add_summary "INFO|models|服务器 ONNX 预测器可选：pip install -r server-vqa/requirements-predictor.txt + 放模型到 server-vqa/models/"
  # 角色可通行分割（N=5 多类，行人=人行道 / 机动车=马路+车道）：候选模型，
  # 尚未 bundle 进 App（等 H 打包）。训练是数小时级 CPU 任务，不作默认安装；
  # 需要时手动跑（产出 VQASeeTraversabilitySeg5.mlpackage → coremlcompiler 编译，
  # harness 用 --seg-model + --config docs/datasets/harness-config-{walk,drive}.json
  # 做角色化闭环评测）。依赖：本 profile 已下的 Cityscapes 权重 + camvid manifest。
  add_summary "INFO|models|角色多类分割 mc5：训练 python deploy/ios/finetune_fast_scnn_camvid_multiclass.py ...；shipping 384² 变体 python deploy/ios/export_mc5_coreml.py --size 384（不重训）→ 已作 VQASeeTraversabilitySeg5.mlmodelc 提交进 App（新机走 git 即得，同 YOLO/二值分割）。live 默认关（use_multiclass_segmentation），待罗根真机延迟签字后翻转。"
  if [ -d ios-vqa-app/VQASee/VQASee/VQASeeTraversabilitySeg5.mlmodelc ]; then
    add_summary "OK|models|mc5 角色分割模型在位（随仓库提交，staged 默认关）"
  fi
  if [ -d ios-vqa-app/VQASee/VQASee/VQASeeLaneSegmentation.mlmodelc ]; then
    add_summary "OK|models|车道分割模型在位（随仓库提交，端上默认开，OTA use_lane_segmentation 可关）"
  fi
  add_summary "INFO|models|TwinLiteNet 实验路面模型：python deploy/ios/convert_twinlite_coreml.py → xcrun coremlcompiler compile ~/.cache/vqasee/models/VQASeeTwinLiteNet.mlpackage ~/.cache/vqasee/models/twinlite-compiled → 拷到 ios-vqa-app/VQASee/VQASee/VQASeeTwinLiteNet.mlmodelc。live 默认 road_backend=twinlite（实验叠图，不是行人可走区）。换模型只改 PerceptionConfig.roadBackend / RoadSurfaceBackend，不改 analyzer。"
  if [ -d ios-vqa-app/VQASee/VQASee/VQASeeTwinLiteNet.mlmodelc ]; then
    add_summary "OK|models|TwinLiteNet 实验路面模型在位（~1MB，live 默认开，OTA road_backend 可切 mc5/off）"
  fi
  deactivate 2>/dev/null || true
  return 0
}

# --- profile: qwen ----------------------------------------------------------

profile_qwen() {
  log "==== profile: qwen（本地 Qwen VQA 运行时）===="
  if ! have ollama && [ ! -d /Applications/Ollama.app ]; then
    if have brew; then
      log "安装 Ollama (brew install --cask ollama)"
      if ! brew install --cask ollama; then
        add_summary "FAIL|qwen|Ollama 安装失败，手动装 https://ollama.com/download/mac"
        return 1
      fi
    else
      warn "无 Ollama 且无 brew。手动安装：https://ollama.com/download/mac"
      add_summary "SKIP|qwen|缺 Ollama 且无 brew"
      return 1
    fi
  fi

  if ! have ollama; then
    warn "ollama CLI 不在 PATH（Ollama.app 已装？先打开一次或把其 CLI 加入 PATH）。"
    add_summary "SKIP|qwen|ollama CLI 不可用，未拉取模型"
    return 1
  fi

  log "拉取 qwen2.5vl:3b（数 GB，视网络而定）"
  if ollama pull qwen2.5vl:3b; then
    add_summary "OK|qwen|qwen2.5vl:3b 已就绪"
  else
    add_summary "FAIL|qwen|模型拉取失败（网络？）"
    return 1
  fi

  local llama="/Applications/Ollama.app/Contents/Resources/llama-server"
  if [ -x "${llama}" ]; then
    add_summary "OK|qwen|llama-server 在位（start_qwen_local.sh 可直接用）"
  else
    add_summary "WARN|qwen|未找到 llama-server；start_qwen_local.sh 需设 LLAMA_SERVER_BIN 或 USE_OLLAMA=1"
  fi
  return 0
}

# --- profile: ios -----------------------------------------------------------

profile_ios() {
  log "==== profile: ios（完整构建/真机/TestFlight）===="
  local devdir
  devdir="$(xcode-select -p 2>/dev/null || true)"
  if [[ "${devdir}" != *"/Xcode.app/"* ]]; then
    warn "当前 xcode-select=${devdir:-未设置} 不是完整 Xcode，无法构建 iOS App。"
    warn "装 Xcode.app 后执行：sudo xcode-select -s /Applications/Xcode.app/Contents/Developer"
    add_summary "SKIP|ios|无完整 Xcode（CLT 不能构建 iOS App）"
    return 1
  fi

  for ruby_bin in \
    /opt/homebrew/opt/ruby/bin \
    /usr/local/opt/ruby@3.4/bin \
    /usr/local/opt/ruby/bin
  do
    if [[ -x "${ruby_bin}/ruby" ]]; then
      export PATH="${ruby_bin}:${PATH}"
      break
    fi
  done

  local rubyv
  rubyv="$(ruby -e 'print RUBY_VERSION' 2>/dev/null || echo 0)"
  if ! version_ge "${rubyv}" "3.2.0"; then
    warn "Ruby ${rubyv} 过旧（fastlane 需 >=3.2）。执行 brew install ruby@3.4，并把 /usr/local/opt/ruby@3.4/bin（Intel）或 /opt/homebrew/opt/ruby/bin（Apple Silicon）加入 PATH。"
    add_summary "SKIP|ios|Ruby 版本不足（需 >=3.2）"
    return 1
  fi

  log "安装 iOS 依赖 (deploy/ios/install_deps.sh) 并做 preflight"
  if bash deploy/ios/install_deps.sh && bash deploy/ios/preflight.sh; then
    add_summary "OK|ios|依赖 + preflight 通过（签名/TestFlight 需 Apple 账号，人工）"
  else
    add_summary "FAIL|ios|install_deps 或 preflight 失败"
    return 1
  fi
  return 0
}

# --- summary ----------------------------------------------------------------

print_summary() {
  echo
  echo "==================== VQASee 安装摘要 ===================="
  local line status profile note
  for line in "${SUMMARY[@]+"${SUMMARY[@]}"}"; do
    IFS='|' read -r status profile note <<< "${line}"
    printf "  [%-4s] %-7s %s\n" "${status}" "${profile}" "${note}"
  done
  echo "========================================================"
  cat <<'EOF'

下一步：
  闭环平台：    bash start_diagnostics_platform.sh    # 打开 /diagnostics/ui
  后端(信令)：  bash start_backend.sh
  本地 Qwen：   bash start_qwen_local.sh start
  完整本地栈：  bash start_local_vqa.sh
  离线 harness：cd ios-vqa-app/perception-harness && swift build
EOF
}

# --- orchestrate ------------------------------------------------------------

detect_env

if [ "${DO_CORE}" = 1 ];   then profile_core   || warn "core profile 有失败项（见摘要）"; fi
if [ "${DO_MODELS}" = 1 ]; then profile_models || warn "models profile 有失败项（见摘要）"; fi
if [ "${DO_QWEN}" = 1 ];   then profile_qwen   || warn "qwen profile 有失败项（见摘要）"; fi
if [ "${DO_IOS}" = 1 ];    then profile_ios    || warn "ios profile 有失败项（见摘要）"; fi

print_summary
