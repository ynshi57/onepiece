#!/usr/bin/env python3
"""Export official SegFormer B0 ADE20K to an explicitly optional Core ML resource.

RGB image 512x512 -> labels int32 [1,128,128], scores float32 [1,128,128].
Labels are ADE20K zero-based model IDs, not object instances; scores are not
calibrated accuracy. ImageType supplies /255; wrapper applies ImageNet mean/std.
Uses public model files and deterministic synthetic pixels only, never user RGB.
"""
import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import time

REPO = 'nvidia/segformer-b0-finetuned-ade-512-512'
REVISION = '489d5cd81a0b59fab9b7ea758d3548ebe99677da'
WEIGHT_SHA = '6ae39addd01de6b1b8bde2cf677d43a5cd733424b8d186de3f95d1c51fee23f9'
ROOT = Path(__file__).resolve().parents[2]
MODEL_ID = f'SegFormerB0/ADE20K/{REVISION}/CoreML512'


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for part in iter(lambda: handle.read(1024*1024), b''):
            h.update(part)
    return h.hexdigest()



def python_version(interpreter):
    """Probe without importing optional export packages or invoking pip."""
    try:
        result = subprocess.run([str(interpreter), '-c',
            'import json,sys;print(json.dumps(list(sys.version_info[:2])))'],
            check=True, capture_output=True, text=True, timeout=10)
        value = json.loads(result.stdout)
        return tuple(value) if len(value) == 2 else None
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return None


def require_supported_python(interpreter):
    version = python_version(interpreter)
    if version not in ((3, 10), (3, 11), (3, 12)):
        found = '.'.join(map(str, version)) if version else 'unavailable'
        raise SystemExit(f'Indoor semantics export requires Python 3.10–3.12 (torch 2.2.2 wheels); '
            f'{interpreter} is {found}. Install with brew install python@3.11, then pass '
            '--runtime /path/to/python3.11 or a compatible dedicated venv. '
            'An incompatible existing venv is retained; explicitly move it aside before recreating it.')
    return version


def bootstrap_python():
    """Prefer an installed 3.11; do not bootstrap this model with setup's newer Python."""
    for minor in (11, 12, 10):
        name = f'python3.{minor}'
        candidates = ([sys.executable] if sys.version_info[:2] == (3, minor) else []) + [
            shutil.which(name), f'/opt/homebrew/opt/python@3.{minor}/bin/{name}',
            f'/usr/local/opt/python@3.{minor}/bin/{name}',
            f'/Library/Frameworks/Python.framework/Versions/3.{minor}/bin/{name}']
        for candidate in dict.fromkeys(item for item in candidates if item):
            if python_version(candidate) == (3, minor):
                return str(candidate)
    raise SystemExit('No installed Python 3.10–3.12 found for torch 2.2.2. '
        'Run brew install python@3.11 and rerun this installer; no Homebrew installation was attempted.')


def model_notices(output, *, check=False):
    """Versioned legal source is independent of generated model output."""
    for name in ('VQASeeIndoorSemantics.LICENSE', 'VQASeeIndoorSemantics.NOTICE'):
        canonical = ROOT/'deploy/ios/licenses'/name
        if not canonical.is_file():
            raise SystemExit(f'Missing model license source: {canonical}; restore the repository license files.')
        content = canonical.read_bytes()
        if name.endswith('.LICENSE') and (b'NVIDIA Source Code License for SegFormer' not in content or b'3.3 Use Limitation' not in content):
            raise SystemExit('Invalid SegFormer license attachment; restore the original full license.')
        destination = output/name
        if check:
            if not destination.is_file() or destination.read_bytes() != content:
                raise SystemExit(f'Missing or changed model notice: {destination}; rerun this installer.')
        else:
            output.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime', type=Path, help='Python executable, venv directory, or existing package target. Default: dedicated build/.venv-indoor-semantics (installs pinned requirements)')
    p.add_argument('--check', action='store_true', help='Offline stdlib resource/identity check only; no environment install, download or conversion')
    p.add_argument('--work', type=Path, default=ROOT/'build/indoor-semantics')
    p.add_argument('--output', type=Path, default=ROOT/'ios-vqa-app/VQASee/ModelResources')
    args = p.parse_args()
    if args.check:
        model_notices(args.output, check=True)
        manifest_path = args.output/'VQASeeIndoorSemantics.labels.json'
        manifest = json.loads(manifest_path.read_text())
        compiled = args.output/'VQASeeIndoorSemantics.mlmodelc'
        metadata = json.loads((compiled/'metadata.json').read_text())[0]['userDefinedMetadata']
        if manifest['modelID'] != MODEL_ID or manifest['revision'] != REVISION or manifest['weightSHA256'] != WEIGHT_SHA:
            raise ValueError('Unexpected model identity')
        if metadata['modelID'] != manifest['modelID'] or metadata['configSHA256'] != manifest['configSHA256']:
            raise ValueError('Model/manifest identity mismatch')
        if manifest['label2id'] != {v:int(k) for k,v in manifest['id2label'].items()}:
            raise ValueError('Inconsistent label taxonomy')
        files = {str(path.relative_to(compiled)):digest(path) for path in compiled.rglob('*') if path.is_file()}
        if files != manifest['compiledFileSHA256']:
            raise ValueError('Compiled resource content mismatch')
        print('Offline indoor semantics resources verified:', manifest['modelID'], flush=True)
        return
    model_notices(args.output)
    runtime = args.runtime
    if runtime is None:
        runtime = ROOT/'build/.venv-indoor-semantics'
        interpreter = runtime/'bin/python'
        if not interpreter.exists():
            if runtime.exists():
                raise SystemExit(f'Existing dedicated venv has no usable Python: {runtime}. '
                    'Retain or move it aside explicitly, install brew install python@3.11, then rerun.')
            subprocess.run([bootstrap_python(),'-m','venv',str(runtime)],check=True,timeout=60)
        require_supported_python(interpreter) # Fail before imports/pip on a stale 3.13 venv.
        check = subprocess.run([str(interpreter),'-c','import torch,numpy,coremltools,transformers,safetensors,PIL'],capture_output=True)
        if check.returncode:
            subprocess.run([str(interpreter),'-m','pip','install','--timeout','30','--retries','0','-r',str(ROOT/'server-vqa/requirements-indoor-semantics-export.txt')],check=True,timeout=600)
    if runtime.is_dir() and (runtime/'pyvenv.cfg').exists():
        runtime = runtime/'bin/python'
        require_supported_python(runtime)
    elif runtime.is_dir() and (runtime/'bin/python').exists():
        runtime = runtime/'bin/python'
    if runtime.is_file():
        require_supported_python(runtime)
        if os.path.abspath(sys.executable) != os.path.abspath(runtime):
            os.execv(str(runtime),[str(runtime),str(Path(__file__).resolve()),'--runtime',str(runtime), '--work',str(args.work),'--output',str(args.output)])
    else:
        if not runtime.is_dir():
            raise SystemExit(f'Runtime is not a Python interpreter, venv, or package directory: {runtime}')
        require_supported_python(sys.executable) # A package target uses this interpreter's ABI.
        sys.path.insert(0, str(runtime.resolve()))
    import numpy as np
    import torch
    import coremltools as ct
    from PIL import Image
    from safetensors.torch import load_file
    from transformers import SegformerConfig, SegformerForSemanticSegmentation
    args.work.mkdir(parents=True, exist_ok=True)
    source = args.work/'official-source'
    source.mkdir(exist_ok=True)
    for name in ('config.json', 'preprocessor_config.json', 'model.safetensors', 'README.md'):
        destination = source/name
        if name == 'model.safetensors' and destination.exists() and digest(destination) == WEIGHT_SHA:
            continue
        partial = destination.with_suffix(destination.suffix+'.partial')
        subprocess.run(['curl','--fail','--silent','--show-error','--location','--connect-timeout','10','--max-time','120',
                        f'https://huggingface.co/{REPO}/resolve/{REVISION}/{name}','-o',str(partial)],check=True)
        if name == 'model.safetensors' and digest(partial) != WEIGHT_SHA:
            raise ValueError('Official model safetensors identity mismatch')
        partial.replace(destination)
    config = SegformerConfig.from_json_file(str(source/'config.json'))
    preprocessing = json.loads((source/'preprocessor_config.json').read_text())
    if preprocessing.get('image_mean') != [0.485,0.456,0.406] or preprocessing.get('image_std') != [0.229,0.224,0.225]:
        raise ValueError('Unexpected official preprocessing')
    if config.num_labels != 150:
        raise ValueError('Unexpected ADE label count')
    torch.set_num_threads(4)
    net = SegformerForSemanticSegmentation(config).eval().cpu()
    net.load_state_dict(load_file(str(source/'model.safetensors')), strict=True)

    class Export(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.net = net
            self.register_buffer('mean', torch.tensor([.485,.456,.406]).reshape(1,3,1,1))
            self.register_buffer('std', torch.tensor([.229,.224,.225]).reshape(1,3,1,1))
        def forward(self, image):
            logits = self.net((image-self.mean)/self.std, return_dict=False)[0]
            scores, labels = torch.softmax(logits, dim=1).max(dim=1)
            return labels.to(torch.int32), scores

    wrapper = Export().eval()
    example = torch.zeros(1,3,512,512)
    with torch.no_grad():
        traced = torch.jit.trace(wrapper, example, strict=False)
    model = ct.convert(traced, inputs=[ct.ImageType(name='image',shape=example.shape,scale=1/255.0)],
        outputs=[ct.TensorType(name='labels',dtype=np.int32),ct.TensorType(name='scores',dtype=np.float32)],
        minimum_deployment_target=ct.target.iOS16, convert_to='mlprogram', compute_units=ct.ComputeUnit.CPU_ONLY)
    model.short_description = 'Optional ADE20K indoor semantic candidates; no object identity or metric geometry.'
    config_sha = digest(source/'config.json')
    model.user_defined_metadata['configSHA256'] = config_sha
    model.user_defined_metadata['modelID'] = MODEL_ID
    model.user_defined_metadata['source_repository'] = REPO
    model.user_defined_metadata['source_revision'] = REVISION
    model.user_defined_metadata['source_weight_sha256'] = WEIGHT_SHA
    model.user_defined_metadata['coordinate_contract'] = 'Native image resized to 512x512 without rotation; output 128x128 labels. Map pixel centres back, not bounding-box identities.'
    package = args.work/'VQASeeIndoorSemantics.mlpackage'
    model.save(str(package))
    # Independent deterministic inputs; include trace/eager and actual Core ML runtime comparisons.
    fixtures = [('black',np.zeros((512,512,3),dtype=np.uint8)),
                ('asymmetric',np.random.default_rng(1701).integers(0,256,(512,512,3),dtype=np.uint8))]
    checks = []
    for name, rgb in fixtures:
        tensor = torch.from_numpy(rgb.transpose(2,0,1).copy()).float().unsqueeze(0)/255
        with torch.no_grad():
            reference = wrapper(tensor)
            trace_result = traced(tensor)
        began = time.monotonic()
        prediction = model.predict({'image':Image.fromarray(rgb)})
        elapsed = time.monotonic()-began
        labels, scores = np.asarray(prediction['labels']),np.asarray(prediction['scores'])
        if labels.shape != (1,128,128) or scores.shape != (1,128,128) or not np.isfinite(scores).all():
            raise ValueError('Invalid actual Core ML output')
        agreement = float(np.mean(labels == reference[0].numpy()))
        score_error = float(np.max(np.abs(scores-reference[1].numpy())))
        if not np.array_equal(reference[0].numpy(), trace_result[0].numpy()) or agreement < .99 or score_error > .02:
            raise ValueError(f'Conversion parity failed: {name}, agreement={agreement}, scoreError={score_error}')
        checks.append(dict(fixture=name,labelAgreement=agreement,maxScoreError=score_error,cpuElapsedSeconds=elapsed))
    args.output.mkdir(parents=True,exist_ok=True)
    compiled = args.output/'VQASeeIndoorSemantics.mlmodelc'
    staging = args.work/'compiled-staging'
    staging.mkdir(exist_ok=True)
    staged_model = staging/compiled.name
    if staged_model.exists():
        shutil.rmtree(staged_model)
    subprocess.run(['xcrun','coremlcompiler','compile',str(package),str(staging)],check=True)
    compiled_runtime = ct.models.CompiledMLModel(str(staged_model),compute_units=ct.ComputeUnit.CPU_ONLY)
    for name,rgb in fixtures:
        reference = model.predict({'image':Image.fromarray(rgb)})
        actual = compiled_runtime.predict({'image':Image.fromarray(rgb)})
        if not np.array_equal(actual['labels'],reference['labels']) or not np.allclose(actual['scores'],reference['scores'],atol=0.00001,rtol=0):
            raise ValueError('Compiled resource differs from verified package')
    if compiled.exists():
        old_metadata = json.loads((compiled/'metadata.json').read_text())[0]['userDefinedMetadata']
        if old_metadata.get('source_revision') != REVISION or old_metadata.get('source_weight_sha256') != WEIGHT_SHA:
            raise ValueError('Refusing to replace unknown installed resource')
        shutil.rmtree(compiled)
    shutil.copytree(staged_model,compiled)
    labels = {str(k):v for k,v in config.id2label.items()}
    manifest = dict(schemaVersion=1,modelID=MODEL_ID,configSHA256=config_sha,repository=REPO,
        revision=REVISION,weightSHA256=WEIGHT_SHA,id2label=labels,label2id={v:int(k) for k,v in labels.items()},
        inputWidth=512,inputHeight=512,outputWidth=128,outputHeight=128,outputs=dict(labels='labels',scores='scores'),
        compiledFileSHA256={str(path.relative_to(compiled)):digest(path) for path in compiled.rglob('*') if path.is_file()})
    (args.output/'VQASeeIndoorSemantics.labels.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    receipt = dict(repository=REPO,revision=REVISION,weightSHA256=WEIGHT_SHA,
        input=dict(name='image',width=512,height=512,color='RGB',preprocessing='ImageType /255 then ImageNet mean/std'),
        output=dict(labels='Int32 [1,128,128], ADE20K zero-based IDs',scores='Float32 [1,128,128], uncalibrated maximum softmax'),
        syntheticParity=checks,compiledRuntimeParity=True,configSHA256=config_sha,modelID=MODEL_ID,compiledModel=str(compiled),accuracyVerified=False,deviceLatencyVerified=False,
        licenseReviewRequired=True)
    (args.work/'installation.json').write_text(json.dumps(receipt,indent=2))
    print(json.dumps(receipt,indent=2),flush=True)


if __name__ == '__main__':
    main()
