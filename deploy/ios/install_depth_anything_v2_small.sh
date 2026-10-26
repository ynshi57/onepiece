#!/usr/bin/env bash
# Optional public Core ML model only; no private image input or PyTorch conversion.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
"${PYTHON_BIN:-python3}" - "${ROOT_DIR}" "$@" <<'PY'
import argparse, hashlib, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
root=Path(sys.argv[1])
p=argparse.ArgumentParser(description='Install pinned Apple Depth Anything V2 Small F16, verify, compile and bundle. --check is offline and never installs.')
p.add_argument('--check',action='store_true')
p.add_argument('--source',type=Path,default=root/'build/spatial-learned-depth/DepthAnythingV2SmallF16.mlpackage',help='Verified package cache; missing files download from the pinned official revision')
p.add_argument('--output',type=Path,default=root/'ios-vqa-app/VQASee/OptionalModelResources')
args=p.parse_args(sys.argv[2:])
repo='apple/coreml-depth-anything-v2-small';revision='cfef6f6f2a70783dedc0bfae40cecbc2052285d3'
name='DepthAnythingV2SmallF16';compiled=args.output/(name+'.mlmodelc');receipt=args.output/('.'+name+'.installation.json')
files={
 'Data/com.apple.CoreML/model.mlmodel':(399433,'44ac97a3efcfd52113183fb2862ff59cd0368e9ec2e30a90a54980dd11407042'),
 'Data/com.apple.CoreML/weights/weight.bin':(49419072,'fa60d9b6a155734f59029ebb882fd54e549bfaee3539c1a9cbd2cbbab64a0fed'),
 'Manifest.json':(617,'2883ae290c48fe916dc5ececac03a7d847fa277165a49ef5652fa1d2b9cb55f7')}
def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()
def tree(path):return {str(f.relative_to(path)):sha(f) for f in path.rglob('*') if f.is_file()}
def legal():
 license=args.output/(name+'.LICENSE');notice=args.output/(name+'.NOTICE')
 if sha(license)!='c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4':raise ValueError('Missing or modified complete Apache-2.0 license')
 text=notice.read_text()
 if revision not in text or 'Apache' not in text or repo not in text:raise ValueError('Missing model source/license attribution notice')
def check():
 legal()
 r=json.loads(receipt.read_text())
 if r['repository']!=repo or r['revision']!=revision or r['sourceFiles']!={k:{'bytes':v[0],'sha256':v[1]} for k,v in files.items()}:raise ValueError('Installed identity differs from the pinned official model')
 metadata=json.loads((compiled/'metadata.json').read_text())[0]
 inputs=metadata['inputSchema'];outputs=metadata['outputSchema']
 if len(inputs)!=1 or inputs[0]['name']!='image' or inputs[0]['width']!='518' or inputs[0]['height']!='392' or inputs[0]['colorspace']!='RGB':raise ValueError('Unexpected compiled image input contract')
 if len(outputs)!=1 or outputs[0]['name']!='depth' or outputs[0]['width']!='518' or outputs[0]['height']!='392' or outputs[0]['colorspace']!='Grayscale16Half':raise ValueError('Unexpected compiled relative-depth output contract')
 actual=tree(compiled)
 if not actual or actual!=r['compiledFileSHA256']:raise ValueError('Compiled model missing or modified')
 print('Verified optional compiled model:',compiled)
try:
 if args.check:
  check();sys.exit(0)
 args.output.mkdir(parents=True,exist_ok=True)
 canonical=root/'ios-vqa-app/VQASee/OptionalModelResources'
 for suffix in ('.LICENSE','.NOTICE'):
  original=canonical/(name+suffix);destination=args.output/(name+suffix)
  if original.resolve()!=destination.resolve():shutil.copyfile(original,destination)
 legal()
 # Idempotent offline fast path: existing model must pass the complete receipt check.
 if compiled.exists() and receipt.exists():
  check();sys.exit(0)
 if shutil.which('xcrun') is None:raise RuntimeError('Full Xcode required: install Xcode and select it with xcode-select')
 subprocess.run(['xcrun','--find','coremlcompiler'],check=True,capture_output=True)
 for relative,(size,digest) in files.items():
  destination=args.source/relative
  if not (destination.is_file() and destination.stat().st_size==size and sha(destination)==digest):
   destination.parent.mkdir(parents=True,exist_ok=True);part=destination.with_name(destination.name+'.partial')
   subprocess.run(['curl','--fail','--silent','--show-error','--location','--proto','=https','--proto-redir','=https',
    '--connect-timeout','10','--max-time','180',f'https://huggingface.co/{repo}/resolve/{revision}/{name}.mlpackage/{relative}','-o',str(part)],check=True,timeout=185)
   if part.stat().st_size!=size or sha(part)!=digest:raise ValueError('Official content/size identity check failed: '+relative)
   part.replace(destination)
 manifest=json.loads((args.source/'Manifest.json').read_text())
 entry=manifest['itemInfoEntries'][manifest['rootModelIdentifier']]
 if entry['path']!='com.apple.CoreML/model.mlmodel':raise ValueError('Package manifest points to an unexpected model')
 # Compile away from the synchronized resource group; publish only a complete directory.
 (root/'build/spatial-learned-depth').mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory(prefix='compile-',dir=root/'build/spatial-learned-depth') as temp:
  subprocess.run(['xcrun','coremlcompiler','compile',str(args.source),temp],check=True,timeout=180)
  generated=Path(temp)/(name+'.mlmodelc')
  if not generated.is_dir() or not tree(generated):raise ValueError('Compiler did not produce the expected model')
  if compiled.exists():raise ValueError('Existing model has no valid receipt; explicitly move it aside and reinstall')
  publishing=args.output/('.'+name+'.installing')
  if publishing.exists():raise ValueError('Incomplete previous installation staging exists; explicitly move it aside and reinstall')
  shutil.copytree(generated,publishing)
  publishing.replace(compiled)
 r=dict(repository=repo,revision=revision,sourceFiles={k:{'bytes':v[0],'sha256':v[1]} for k,v in files.items()},compiledFileSHA256=tree(compiled),relativeDepthOnly=True)
 partial=receipt.with_suffix('.json.partial');partial.write_text(json.dumps(r,indent=2));partial.replace(receipt)
 check()
except Exception as exc:
 print(f'Optional depth model unavailable: {exc}. Repair: bash deploy/ios/install_depth_anything_v2_small.sh',file=sys.stderr)
 sys.exit(1)
PY
