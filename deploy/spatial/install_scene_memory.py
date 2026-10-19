"""Restore fixed research packages from official wheels into an isolated directory.

Uses curl for HTTPS when the local pip index cannot enumerate releases. No model
or private image is transferred; the main Python environment is not modified.
"""
import argparse
import concurrent.futures
import hashlib
import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit
from packaging.tags import sys_tags
from packaging.utils import parse_wheel_filename

ROOT = Path(__file__).resolve().parents[2]


def install(target):
    target = Path(target).resolve()
    target.mkdir(parents=True, exist_ok=True)
    cache = target.parent / 'spatial-memory-wheels'
    cache.mkdir(exist_ok=True)
    tags = {tag: index for index, tag in enumerate(sys_tags())}
    pins = [line.strip() for line in (ROOT/'server-vqa/requirements-spatial-memory.txt').read_text().splitlines()
            if line.strip() and not line.startswith('#')]

    probe = [sys.executable,'-c',
             'import sys;sys.path.insert(0,sys.argv[1]);import torch,numpy,scipy,PIL,requests;'
             'from transformers import SegformerConfig,SegformerForSemanticSegmentation,SegformerImageProcessor;'
             'print("CPU scene-memory runtime ready")',str(target)]
    installed = {dist.metadata['Name'].lower().replace('_','-'):dist.version
                 for dist in importlib.metadata.distributions(path=[str(target)])}
    if all(installed.get(name)==version for name,version in (pin.split('==') for pin in pins)):
        ready = subprocess.run(probe, capture_output=True, text=True)
        if ready.returncode == 0:
            print(ready.stdout.strip())
            return
        print('Cached runtime incomplete; restoring fixed wheels',flush=True)

    def fetch(pin):
        name, version = pin.split('==')
        metadata = cache/f'{name}-{version}.json'
        subprocess.run(['curl','--fail','--silent','--show-error','--connect-timeout','5','--max-time','25',
                        '-L',f'https://pypi.org/pypi/{name}/{version}/json','-o',str(metadata)],check=True)
        options = []
        for item in json.loads(metadata.read_text())['urls']:
            if item['filename'].endswith('.whl'):
                wheel_tags = parse_wheel_filename(item['filename'])[3]
                ranks = [tags[tag] for tag in wheel_tags if tag in tags]
                if ranks:
                    options.append((min(ranks),item))
        if not options:
            raise RuntimeError(f'No official compatible wheel for {pin}; no source build attempted')
        item = min(options,key=lambda pair:pair[0])[1]
        if urlsplit(item['url']).hostname != 'files.pythonhosted.org':
            raise ValueError('Unexpected wheel host')
        wheel = cache/item['filename']
        if not wheel.exists() or hashlib.sha256(wheel.read_bytes()).hexdigest()!=item['digests']['sha256']:
            partial = wheel.with_suffix(wheel.suffix+'.partial')
            subprocess.run(['curl','--fail','--silent','--show-error','--connect-timeout','5','--max-time','45',
                            '-L',item['url'],'-o',str(partial)],check=True)
            if hashlib.sha256(partial.read_bytes()).hexdigest()!=item['digests']['sha256']:
                raise ValueError(f'Wheel integrity check failed: {pin}')
            partial.replace(wheel)
        print('Ready',pin,flush=True)
        return str(wheel)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        wheels = list(pool.map(fetch,pins))
    subprocess.run([sys.executable,'-m','pip','install','--disable-pip-version-check','--no-index','--no-deps',
                    '--upgrade','--target',str(target),*wheels],check=True)
    subprocess.run(probe,check=True)



if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target',required=True)
    install(parser.parse_args().target)
