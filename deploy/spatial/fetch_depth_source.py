"""Fetch only fixed-revision official depth source, checking Git blob identities."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import time
import subprocess
import shutil

ROOT = Path(__file__).resolve().parents[2]


def fetch_sources():
    if not shutil.which('curl'): raise RuntimeError('curl_required_for_official_source_fetch')
    base = ROOT/'native/depth-anything-v2'
    manifest = json.loads((base/'source-manifest.json').read_text())
    revision = manifest['revision']
    if len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision):
        raise ValueError('invalid_source_revision')
    def verified(data, item):
        return len(data) == item['size'] and hashlib.sha1(
            f'blob {len(data)}\0'.encode()+data).hexdigest() == item['sha']
    def fetch(item):
        relative = Path(item['path'])
        if relative.is_absolute() or '..' in relative.parts: raise ValueError('invalid_source_path')
        target = base/'upstream'/relative
        if target.is_file() and verified(target.read_bytes(), item): return
        for attempt in range(3):
            try:
                url = f'https://raw.githubusercontent.com/DepthAnything/Depth-Anything-V2/{revision}/{item["path"]}'
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name+'.pending')
                result = subprocess.run(['curl','--fail','--silent','--show-error','--location',
                    '--connect-timeout','10','--max-time','40',url,'--output',str(temporary)],
                    capture_output=True)
                if result.returncode: raise RuntimeError('source_transport_failed')
                data = temporary.read_bytes()
                if not verified(data, item): raise ValueError('source_identity_mismatch')
                temporary.replace(target)
                print(json.dumps({'verifiedSource':item['path']}),flush=True)
                return
            except Exception:
                if attempt == 2: raise RuntimeError('official_source_fetch_failed:'+item['path']) from None
                time.sleep(.5*(attempt+1))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(fetch,manifest['files']))
    print(json.dumps({'verifiedRevision':revision,'fileCount':len(manifest['files'])}),flush=True)


if __name__ == '__main__':
    fetch_sources()
