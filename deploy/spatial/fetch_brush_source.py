"""Fetch fixed official Brush build inputs; verify every Git blob before patching.

The single local scene.rs patch is accepted only with its pinned checksum and
patched target checksum. --check-only never performs a network request.
"""
from concurrent.futures import ThreadPoolExecutor
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / 'native/brush/source-manifest.json'
DEFAULT_SOURCE = ROOT / 'server-vqa/models/spatial-brush/source'
REVISION = '3edecbb2fe79d3e2c87eeab85b15e0b1dd10d486'
LPIPS_SHA256 = 'abb257f9aa223154f3ed476674dbfc0b6daee5b2e07618bd208918ee9600e2e7'
GENERATED_SHADER_SHA256 = {
    'crates/brush-prefix-sum/src/shaders/mod.rs': '23b44506d5a7382501cbe43f8188261e53b8f4e38d1d2f06ddd256537c85b264',
    'crates/brush-kernel/src/shaders/mod.rs': '808140009f261f83341f00a470192c9938e8dd2dce5082dfee6ecbe8c3ea2cee',
    'crates/brush-render-bwd/src/shaders/mod.rs': '5bfa9bf7f17f16fb8da511a4a3f5fc2342b975cfb5a38c0e6d33364e1ce864d4',
    'crates/brush-sort/src/shaders/mod.rs': '45acde16a4a3771fbc4d8db92494ac285802dd3cade417e9f01ff6e54b221769',
    'crates/brush-render/src/shaders/mod.rs': '9d071d76fe3495b714c130918f99f3b0bce62ff3742982c62264e78e8676ae0f',
}


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _manifest():
    manifest = json.loads(MANIFEST.read_text())
    if (manifest.get('repository') != 'https://github.com/ArthurBrussee/brush'
            or manifest.get('revision') != REVISION or len(manifest.get('files', [])) != 164
            or manifest.get('lpipsSHA256') != LPIPS_SHA256):
        raise ValueError('untrusted_brush_manifest')
    files = manifest['files']
    paths = [item['path'] for item in files]
    if len(paths) != len(set(paths)) or paths != sorted(paths):
        raise ValueError('invalid_brush_file_list')
    for item in files:
        relative = Path(item['path'])
        if (relative.is_absolute() or '..' in relative.parts or item.get('type') != 'blob'
                or item.get('mode') not in {'100644', '100755'} or item.get('size', 0) <= 0
                or len(item.get('sha', '')) != 40):
            raise ValueError('invalid_brush_source_entry')
    weight = next((item for item in files if item['path'] == 'crates/lpips/burn_mapped.bin'), None)
    if weight is None or weight['size'] != 29433386 or weight['sha'] != '9ee45efe2a11660f5993cd0aa26b9adaa32be3fa':
        raise ValueError('untrusted_brush_lpips_entry')
    patch = manifest['patch']
    if (patch['path'] != 'native/brush/patches/0001-small-image-header.patch'
            or patch['target'] != 'crates/brush-dataset/src/scene.rs'):
        raise ValueError('untrusted_brush_patch_manifest')
    patch_file = ROOT / patch['path']
    if _sha256(patch_file) != patch['sha256']:
        raise ValueError('brush_patch_digest_mismatch')
    return manifest


def _verified(path, item):
    if not path.is_file() or path.is_symlink() or path.stat().st_size != item['size']:
        return False
    data = path.read_bytes() if item['size'] < 1024 * 1024 else None
    if data is None:
        digest = hashlib.sha1()
        digest.update(f"blob {item['size']}\0".encode())
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        blob = digest.hexdigest()
    else:
        blob = hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest()
    if blob != item['sha']:
        return False
    return item['path'] != 'crates/lpips/burn_mapped.bin' or _sha256(path) == LPIPS_SHA256


def _patched(path, item, manifest):
    patch = manifest['patch']
    return (item['path'] == patch['target'] and path.is_file() and not path.is_symlink()
            and _sha256(path) == patch['patchedSHA256'])


def fetch(source_root=DEFAULT_SOURCE, *, check_only=False, require_patch=False):
    manifest = _manifest()
    source_root = Path(source_root).resolve()
    if not check_only and not shutil.which('curl'):
        raise RuntimeError('curl_required_for_official_brush_source')
    def check(item):
        path = source_root / item['path']
        if not path.resolve().is_relative_to(source_root):
            raise ValueError('invalid_brush_source_path')
        if _verified(path, item):
            if not check_only:
                path.with_name(path.name + '.pending').unlink(missing_ok=True)
            if require_patch and item['path'] == manifest['patch']['target']:
                raise ValueError('brush_patch_not_applied')
            return
        if _patched(path, item, manifest):
            if not require_patch and check_only:
                return
            if require_patch:
                return
            # A patched checkout is already complete; do not download the
            # original merely to apply the same verified patch again.
            return
        if check_only:
            raise ValueError('brush_source_missing_or_unverified:' + item['path'])
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.pending')
        url = f'https://raw.githubusercontent.com/ArthurBrussee/brush/{REVISION}/{item["path"]}'
        for attempt in range(3):
            result = subprocess.run(['curl', '--fail', '--silent', '--show-error', '--location',
                                     '--connect-timeout', '10', '--max-time', '180', url,
                                     '--output', str(temporary)], capture_output=True)
            if result.returncode == 0 and _verified(temporary, item):
                temporary.replace(path)
                print(json.dumps({'verifiedSource': item['path']}), flush=True)
                return
            temporary.unlink(missing_ok=True)
            if attempt < 2:
                time.sleep(.5 * (attempt + 1))
        raise RuntimeError('official_brush_source_fetch_failed:' + item['path'])
    if check_only:
        for item in manifest['files']:
            check(item)
    else:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(check, manifest['files']))
    known = {item['path'] for item in manifest['files']}
    for path in source_root.rglob('*'):
        relative = str(path.relative_to(source_root))
        if path.is_symlink() and not relative.startswith('target/'):
            raise ValueError('symlinked_brush_source_input:' + relative)
        if not path.is_file():
            continue
        if relative in GENERATED_SHADER_SHA256:
            if _sha256(path) != GENERATED_SHADER_SHA256[relative]:
                raise ValueError('unverified_generated_shader:' + relative)
            continue
        if relative in known or relative.startswith('target/'):
            continue
        raise ValueError('unlisted_brush_source_file:' + relative)
    print(json.dumps({'verifiedRevision': REVISION, 'fileCount': len(manifest['files']),
                      'patchRequired': require_patch, 'networkAllowed': not check_only}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--require-patch', action='store_true')
    args = parser.parse_args()
    fetch(args.source_root, check_only=args.check_only, require_patch=args.require_patch)


if __name__ == '__main__':
    main()
