#!/usr/bin/env bash
# Optional official DAv2 Small CPU runtime, with fixed source/weight identities.
set -euo pipefail
trap 'echo "[spatial-depth] 安装或检查未通过；请检查Python版本、依赖、curl和网络后重跑 --with-spatial-depth。未验证权重不可执行。" >&2' ERR
SPATIAL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SPATIAL_PYTHON="${VQASEE_SPATIAL_DEPTH_PYTHON:-${SPATIAL_ROOT}/.venv/bin/python}"
SPATIAL_WEIGHT="${VQASEE_SPATIAL_DEPTH_WEIGHTS:-${SPATIAL_ROOT}/server-vqa/models/depth-anything-v2-small/depth_anything_v2_vits.pth}"
SPATIAL_CHECK=0
if [ "${1:-}" = "--check-only" ]; then SPATIAL_CHECK=1; shift; fi
if [ "$#" -ne 0 ]; then echo '[spatial-depth] Usage: install_depth_prior.sh [--check-only]' >&2; exit 2; fi
if [ ! -x "${SPATIAL_PYTHON}" ]; then
  echo '[spatial-depth] 缺少仓库 Python 环境；先运行 setup_mac.sh core profile。' >&2; exit 1
fi
"${SPATIAL_PYTHON}" -c 'import sys; assert (3,9)<=sys.version_info[:2]<(3,13), "该可选CPU运行时需要Python3.9–3.12；请使用Python3.11环境"'
if [ "${SPATIAL_CHECK}" = 0 ]; then
  if ! command -v curl >/dev/null 2>&1; then echo '[spatial-depth] 需要 curl 下载官方源码和权重。' >&2; exit 1; fi
  "${SPATIAL_PYTHON}" -m pip install -r "${SPATIAL_ROOT}/server-vqa/requirements-spatial-depth.txt"
  "${SPATIAL_PYTHON}" "${SPATIAL_ROOT}/deploy/spatial/fetch_depth_source.py"
  "${SPATIAL_PYTHON}" "${SPATIAL_ROOT}/deploy/spatial/download_verified.py" \
    --transport curl --workers 4 --chunk-size 262144 \
    --url 'https://huggingface.co/depth-anything/Depth-Anything-V2-Small/resolve/03876f8651c73a60fe4c2c48294e09fcb6838fcf/depth_anything_v2_vits.pth' \
    --size 99218434 --sha256 715fade13be8f229f8a70cc02066f656f2423a59effd0579197bbf57860e1378 \
    --output "${SPATIAL_WEIGHT}"
fi
"${SPATIAL_PYTHON}" - "${SPATIAL_ROOT}" "${SPATIAL_WEIGHT}" <<'PY'
import hashlib, importlib.metadata, json, pathlib, sys
root=pathlib.Path(sys.argv[1]); weight=pathlib.Path(sys.argv[2])
expected={'torch':'2.2.2','torchvision':'0.17.2','opencv-python-headless':'4.10.0.84'}
for package,version in expected.items():
    try: actual=importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError: raise SystemExit(f"缺少{package}；请运行 --with-spatial-depth 安装profile")
    assert actual.split('+')[0]==version, f'{package}版本不匹配，请重跑安装profile'
import numpy, torch, torchvision, cv2
assert int(numpy.__version__.split('.')[0])<2, 'torch2.2要求NumPy<2'
assert torch.ones(1).numpy()[0]==1
base=root/'native/depth-anything-v2'
manifest=json.loads((base/'source-manifest.json').read_text())
assert manifest['revision']=='a561b849ebae10a6f5ef49e26c83cbbcd36c71bf'
for item in manifest['files']:
    data=(base/'upstream'/item['path']).read_bytes()
    assert len(data)==item['size'] and hashlib.sha1(f'blob {len(data)}\0'.encode()+data).hexdigest()==item['sha'], '官方源码身份校验失败'
sys.path.insert(0,str(base/'upstream'))
from depth_anything_v2.dpt import DepthAnythingV2
assert weight.is_file() and weight.stat().st_size==99218434, '权重缺失或长度不符；请重跑安装profile'
digest=hashlib.sha256()
with weight.open('rb') as stream:
    while block:=stream.read(1024*1024): digest.update(block)
assert digest.hexdigest()=='715fade13be8f229f8a70cc02066f656f2423a59effd0579197bbf57860e1378', '权重SHA256不符，禁止模型执行'
print('[spatial-depth] 依赖、固定官方源码与权重身份检查通过；模型精度、CPU耗时和墙体结构仍需实验验收。')
PY
