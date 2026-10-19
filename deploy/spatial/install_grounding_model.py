"""Bounded, optional official Grounding DINO CPU research model acquisition.

No private images are read. Resolves the official repository's current revision
once, then pins all requests to that revision. No retries or alternate mirrors.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

REPOSITORY = 'IDEA-Research/grounding-dino-tiny'
TOTAL_SECONDS = 420


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install(target):
    target = Path(target).resolve()
    target.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + TOTAL_SECONDS

    def download(url, destination, weight=False, expected_sha=None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Grounding model installation total time budget exhausted')
        temporary = destination.with_name(destination.name + '.partial')
        try:
            subprocess.run(['curl', '--fail', '--silent', '--show-error', '--location',
                            '--proto', '=https', '--proto-redir', '=https', '--connect-timeout', '5',
                            '--max-time', str(min(180 if weight else 120, remaining)), url, '-o', str(temporary)],
                           check=True, timeout=min(185 if weight else 125, remaining + 2))
            if expected_sha and sha256(temporary) != expected_sha:
                temporary.unlink(missing_ok=True)
                raise ValueError("Official LFS SHA256 mismatch before publication")
            os.replace(temporary, destination)
        finally:
            if not weight:
                temporary.unlink(missing_ok=True)

    receipt_path = target / 'installation.json'
    receipt = dict(repository=REPOSITORY, status='failed', modelUse='offline CPU research; not App', files={})
    try:
        metadata_path = target / 'official-model-api.json'
        download(f'https://huggingface.co/api/models/{REPOSITORY}?blobs=true', metadata_path)
        metadata = json.loads(metadata_path.read_text())
        revision = metadata['sha']
        if not re.fullmatch(r'[0-9a-f]{40}', revision):
            raise ValueError('Invalid official revision')
        receipt['revision'] = revision
        siblings = {item['rfilename']: item for item in metadata['siblings']}
        required = ['config.json', 'preprocessor_config.json', 'tokenizer_config.json',
                    'tokenizer.json', 'model.safetensors']
        optional = ['special_tokens_map.json', 'vocab.txt', 'added_tokens.json']
        if not all(name in siblings for name in required):
            raise ValueError('Official repository missing required model/processor/tokenizer files')
        names = required + [name for name in optional if name in siblings]
        weight_sha = siblings['model.safetensors'].get('lfs', {}).get('sha256')
        if not isinstance(weight_sha, str) or not re.fullmatch(r'[0-9a-f]{64}', weight_sha):
            raise ValueError('Official API did not provide model LFS SHA256')
        for name in names:
            destination = target / name
            expected = siblings[name].get('lfs', {}).get('sha256')
            if not (expected and destination.exists() and sha256(destination) == expected):
                download(f'https://huggingface.co/{REPOSITORY}/resolve/{revision}/{name}', destination, weight=name == 'model.safetensors', expected_sha=expected)
            digest = sha256(destination)
            if expected and digest != expected:
                destination.unlink()
                raise ValueError(f'Official LFS SHA256 mismatch: {name}')
            receipt['files'][name] = dict(sha256=digest, officialLfsSHA256=expected, bytes=destination.stat().st_size)
        receipt['status'] = 'download_verified'
        print('Grounding research model:', target, 'revision:', revision, flush=True)
    except Exception as exc:
        receipt['failure'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        receipt['timeBudgetSeconds'] = TOTAL_SECONDS
        partial = receipt_path.with_suffix('.json.partial')
        partial.write_text(json.dumps(receipt, indent=2))
        os.replace(partial, receipt_path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', required=True)
    install(parser.parse_args().target)
