"""Pinned official DAv2 Small relative depth experiment; never metric wall truth."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import resource
import sys
import time

import numpy as np
from PIL import Image

SOURCE_COMMIT = 'a561b849ebae10a6f5ef49e26c83cbbcd36c71bf'
WEIGHTS_SHA256 = '715fade13be8f229f8a70cc02066f656f2423a59effd0579197bbf57860e1378'
WEIGHTS_BYTES = 99218434
SOURCE_BLOBS = {'LICENSE': '261eeb9e9f8b2b4b0d119366dda99c6fd7d35c64', 'depth_anything_v2/dinov2.py': '83d250818c721c6df3b30d3f4352945527701615', 'depth_anything_v2/dinov2_layers/__init__.py': '8120f4bc83066cb3f825ce32daa3b437f88486f1', 'depth_anything_v2/dinov2_layers/attention.py': '815a2bf53dbec496f6a184ed7d03bcecb7124262', 'depth_anything_v2/dinov2_layers/block.py': '25488f57cc0ad3c692f86b62555f6668e2a66db1', 'depth_anything_v2/dinov2_layers/drop_path.py': 'af05625984dd14682cc96a63bf0c97bab1f123b1', 'depth_anything_v2/dinov2_layers/layer_scale.py': 'ca5daa52bd81d3581adeb2198ea5b7dba2a3aea1', 'depth_anything_v2/dinov2_layers/mlp.py': '5e4b315f972f9a9f54aef1e4ef4e81b52976f018', 'depth_anything_v2/dinov2_layers/patch_embed.py': '574abe41175568d700a389b8b96d1ba554914779', 'depth_anything_v2/dinov2_layers/swiglu_ffn.py': 'b3324b266fb0a50ccf8c3a0ede2ae10ac4dfa03e', 'depth_anything_v2/dpt.py': '18d3e6f8b5c973aea5d4927ef783d43053ba6429', 'depth_anything_v2/util/blocks.py': '382ea183a40264056142afffc201c992a2b01d37', 'depth_anything_v2/util/transform.py': 'b14aacd44ea086b01725a9ca68bb49eadcf37d73'}
INPUT_SIZE = 518
DEFAULT_SOURCE_ROOT = Path(__file__).resolve().parents[2] / 'native/depth-anything-v2'
DEFAULT_WEIGHTS = Path(os.environ.get('VQASEE_SPATIAL_DEPTH_WEIGHTS',
    str(Path(__file__).resolve().parents[1] / 'models/depth-anything-v2-small/depth_anything_v2_vits.pth')))


def verify_source(source_root):
    source_root=Path(source_root).resolve()
    manifest=json.loads((source_root/'source-manifest.json').read_text())
    if manifest.get('revision')!=SOURCE_COMMIT or manifest.get('repository')!='https://github.com/DepthAnything/Depth-Anything-V2':
        raise ValueError('untrusted_depth_source_revision')
    if {item['path']:item['sha'] for item in manifest['files']}!=SOURCE_BLOBS or len(manifest['files'])!=len(SOURCE_BLOBS):
        raise ValueError('untrusted_depth_source_manifest')
    expected=set()
    for item in manifest['files']:
        path=(source_root/'upstream'/item['path']).resolve()
        if not path.is_relative_to(source_root/'upstream') or path.is_symlink():
            raise ValueError('invalid_depth_source_path')
        data=path.read_bytes()
        sha=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        if len(data)!=item['size'] or sha!=item['sha']:
            raise ValueError('depth_source_blob_mismatch: '+item['path'])
        expected.add(item['path'])
    actual={str(p.relative_to(source_root/'upstream')) for p in (source_root/'upstream').rglob('*.py')}
    if actual!={p for p in expected if p.endswith('.py')}:
        raise ValueError('unverified_depth_python_source')
    return manifest


def verify_weights(path):
    path=Path(path)
    if path.stat().st_size!=WEIGHTS_BYTES:
        raise ValueError('depth_weights_size_mismatch')
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            digest.update(chunk)
    if digest.hexdigest()!=WEIGHTS_SHA256:
        raise ValueError('depth_weights_sha256_mismatch')
    return digest.hexdigest()


def load_model(weights=DEFAULT_WEIGHTS,source_root=DEFAULT_SOURCE_ROOT):
    verify_source(source_root);verify_weights(weights)
    upstream=str((Path(source_root)/'upstream').resolve())
    for name,module in list(sys.modules.items()):
        if name=='depth_anything_v2' or name.startswith('depth_anything_v2.'):
            location=getattr(module,'__file__',None)
            if location and not Path(location).resolve().is_relative_to(Path(upstream)):
                raise ValueError('depth_source_import_conflict')
            if any(not Path(folder).resolve().is_relative_to(Path(upstream)) for folder in getattr(module,'__path__',[])):
                raise ValueError('depth_source_import_conflict')
    sys.path.insert(0,upstream)
    import torch
    torch.set_num_threads(4)
    from depth_anything_v2.dpt import DepthAnythingV2
    model=DepthAnythingV2(encoder='vits',features=64,out_channels=[48,96,192,384]).cpu()
    model.load_state_dict(torch.load(weights,map_location='cpu',weights_only=True),strict=True)
    model.eval()
    model._vqasee_verified_source_commit=SOURCE_COMMIT
    model._vqasee_verified_weights_sha256=WEIGHTS_SHA256
    return model


def predict_relative(model,image_path):
    """Official preprocessing/forward on CPU, batch one. Large output means relatively near."""
    if getattr(model,'_vqasee_verified_source_commit',None)!=SOURCE_COMMIT or getattr(model,'_vqasee_verified_weights_sha256',None)!=WEIGHTS_SHA256:
        raise ValueError('unverified_depth_model')
    import cv2
    import torch
    if next(model.parameters()).device.type!='cpu':raise ValueError('depth_model_must_run_cpu')
    import torch.nn.functional as functional
    from torchvision.transforms import Compose
    from depth_anything_v2.util.transform import Resize,NormalizeImage,PrepareForNet
    start=time.monotonic();cpu_start=time.process_time()
    image=cv2.imread(str(image_path))
    if image is None:raise ValueError('depth_image_unreadable')
    height,width=image.shape[:2]
    transform=Compose([Resize(width=INPUT_SIZE,height=INPUT_SIZE,resize_target=False,
        keep_aspect_ratio=True,ensure_multiple_of=14,resize_method='lower_bound',image_interpolation_method=cv2.INTER_CUBIC),
        NormalizeImage(mean=[.485,.456,.406],std=[.229,.224,.225]),PrepareForNet()])
    array=transform({'image':cv2.cvtColor(image,cv2.COLOR_BGR2RGB)/255.})['image']
    tensor=torch.from_numpy(array).unsqueeze(0).to('cpu')
    with torch.inference_mode():
        output=model(tensor)
        depth=functional.interpolate(output[:,None],(height,width),mode='bilinear',align_corners=True)[0,0].cpu().numpy().astype(np.float32)
    if depth.shape!=(height,width) or not np.isfinite(depth).all():raise ValueError('invalid_relative_depth_output')
    rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_bytes=int(rss if sys.platform=='darwin' else rss*1024)
    return depth,{'cpuSeconds':time.monotonic()-start,'processCPUSeconds':time.process_time()-cpu_start,'processPeakRSSBytes':peak_bytes,
        'inputSize':INPUT_SIZE,'cpuThreads':torch.get_num_threads(),'tensorShape':list(tensor.shape),'device':'cpu','batchSize':1,
        'units':'relative_inverse_depth_not_meters','confidenceAvailable':False,
        'notMeasuredWallGeometry':True,'sourceCommit':SOURCE_COMMIT,'weightsSHA256':WEIGHTS_SHA256}


def color_relative(depth):
    """Per-image display normalization only; full raw float array is saved separately."""
    span=float(np.ptp(depth));value=(depth-float(depth.min()))/span if span>0 else np.zeros_like(depth)
    # Blue far, yellow/red near; no fabricated confidence channel.
    return np.uint8(np.stack([np.clip(2*value,0,1),np.clip(2-abs(4*value-2),0,1),np.clip(2*(1-value),0,1)],axis=-1)*255)


def training_anchors(document,directory,training_frame_indices,epoch):
    """Retriangulate image-only tracks, never use final third-view-verified points."""
    from .spatial_reconstruction import reconstruct,camera,triangulate
    indices=list(training_frame_indices)
    if len(set(indices))!=len(indices) or any(i<0 or i>=len(document['keyframes']) for i in indices):
        raise ValueError('invalid_training_frame_indices')
    frames=[document['keyframes'][i] for i in indices]
    if any(f['pose']['epoch']!=epoch for f in frames):raise ValueError('different_training_epoch')
    seed=reconstruct({**document,'keyframes':frames},Path(directory))
    anchors=[];failures=[]
    for track in seed.get('featureTracks',[]):
        observations=track['observations']
        try:
            cams=[camera(frames[o['frameIndex']]) for o in observations]
            pixels=[np.asarray(o['pixel']) for o in observations]
            _,_,depths,errors,_=triangulate(cams,pixels)
            support=[indices[o['frameIndex']] for o in observations]
            for obs,depth,error in zip(observations,depths,errors):
                anchors.append({'frameIndex':indices[obs['frameIndex']],'pixel':obs['pixel'],
                    'depthMeters':float(depth),'reprojectionErrorPixels':float(error),
                    'trainingSupportFrames':support,'trackId':track['id'],'source':'training_image_track_retriangulation'})
        except (ValueError,np.linalg.LinAlgError) as exc:
            failures.append({'trackId':track['id'],'reason':str(exc)})
    return {'anchors':anchors,'failures':failures,'trainingFrameIndices':indices,
        'usedFinalVerifiedPoints':False,'evaluationRGBUsed':False,'rawARKitPosesUnverified':True}


def fit_inverse_depth(relative_depth,anchors,*,frame_index,training_frame_indices):
    """Training-only robust 1/z=a*d+b fit. Reject unsupported extrapolation and degeneracy."""
    training=set(training_frame_indices)
    if frame_index not in training:raise ValueError('evaluation_frame_forbidden')
    depth=np.asarray(relative_depth,dtype=np.float64)
    if depth.ndim!=2 or not np.isfinite(depth).all():raise ValueError('invalid_relative_depth')
    height,width=depth.shape;values=[];inverse=[];pixels=[]
    for anchor in anchors:
        if anchor['frameIndex'] not in training:raise ValueError('evaluation_anchor_forbidden')
        if anchor['frameIndex']!=frame_index:continue
        if anchor.get('source')!='training_image_track_retriangulation' or not set(anchor.get('trainingSupportFrames',[])).issubset(training) or len(set(anchor.get('trainingSupportFrames',[])))<2 or frame_index not in anchor.get('trainingSupportFrames',[]):
            raise ValueError('non_training_anchor_forbidden')
        x,y=anchor['pixel'];z=anchor['depthMeters']
        if not np.isfinite([x,y,z]).all() or not 0<=x<width or not 0<=y<height or z<=0:
            raise ValueError('invalid_training_anchor')
        values.append(depth[min(height-1,int(round(y))),min(width-1,int(round(x)))]);inverse.append(1/z);pixels.append([x/width,y/height])
    if len(values)<12:raise ValueError('insufficient_training_anchors')
    d=np.asarray(values);inv=np.asarray(inverse);xy=np.asarray(pixels)
    if np.ptp(d)<=1e-6*max(1.,abs(d).max()):raise ValueError('constant_or_near_zero_relative_span')
    if np.ptp(inv)<=1e-6*max(1.,abs(inv).max()):raise ValueError('near_zero_metric_span')
    span=np.ptp(xy,axis=0);eigen=np.linalg.eigvalsh(np.cov(xy.T))
    if min(span)<.25 or np.prod(span)<.08 or eigen[0]<.01*eigen[1]:raise ValueError('spatially_local_training_anchors')
    design=np.stack([d,np.ones_like(d)],axis=1);weights=np.ones(len(d))
    for _ in range(10):
        a,b=np.linalg.lstsq(design*np.sqrt(weights[:,None]),inv*np.sqrt(weights),rcond=None)[0]
        residual=np.abs(design@np.array([a,b])-inv)
        scale=max(1e-8,float(np.median(residual))*1.4826)
        weights=np.minimum(1.,1.345*scale/np.maximum(residual,1e-12))
    if a<=0:raise ValueError('nonpositive_depth_scale')
    if min(a*float(depth.min())+b,a*float(depth.max())+b)<=1e-6:raise ValueError('nonpositive_extrapolated_inverse_depth')
    normalized_error=float(np.median(residual)/np.median(inv))
    if normalized_error>.2:raise ValueError('training_anchor_fit_inconsistent')
    return {'status':'fitted_training_prior_not_measured_geometry','a':float(a),'b':float(b),
        'anchorCount':len(d),'normalizedMedianInverseDepthError':normalized_error,
        'relativeSpan':float(np.ptp(d)),'anchorNormalizedSpatialSpan':span.tolist(),
        'evaluationUsed':False,'confidenceAvailable':False,'rawARKitPosesUnverified':True}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['document','directory','output']:parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--weights',type=Path,default=DEFAULT_WEIGHTS)
    parser.add_argument('--source-root',type=Path,default=DEFAULT_SOURCE_ROOT)
    parser.add_argument('--frame-indices',help='Exactly three fixed original frame indices, comma separated')
    args=parser.parse_args(argv)
    document=json.loads(args.document.read_text());frames=document['keyframes']
    indices=[int(v) for v in args.frame_indices.split(',')] if args.frame_indices else [0,len(frames)//2,len(frames)-1]
    if len(indices)!=3 or len(set(indices))!=3 or any(i<0 or i>=len(frames) for i in indices):raise ValueError('exactly_three_distinct_frames_required')
    if args.output.exists():raise ValueError('depth_output_already_exists')
    args.output.mkdir(parents=True)
    result={'status':'relative_depth_experiment','productAccepted':False,'selectedFrameIndices':indices,
        'sourceCommit':SOURCE_COMMIT,'weightsSHA256':WEIGHTS_SHA256,'inputSize':INPUT_SIZE,'device':'cpu','batchSize':1,
        'selectionPolicy':'fixed_original_frame_indices_before_inference',
        'units':'relative_not_meters','confidenceAvailable':False,'notMeasuredWallGeometry':True,'frames':[]}
    try:
        started=time.monotonic();model=load_model(args.weights,args.source_root)
        result['modelLoadSeconds']=time.monotonic()-started
        for index in indices:
            frame=frames[index];entry={'frameIndex':index,'epoch':frame['pose']['epoch'],'imageFile':frame['imageFile']}
            try:
                image_path=(args.directory/frame['imageFile']).resolve()
                if not image_path.is_relative_to(args.directory.resolve()):raise ValueError('image_path_outside_scan')
                depth,metadata=predict_relative(model,image_path)
                prefix=f'frame-{index:03d}'
                with Image.open(image_path) as image:image.convert('RGB').save(args.output/(prefix+'-original.png'))
                Image.fromarray(color_relative(depth)).save(args.output/(prefix+'-relative.png'))
                np.savez_compressed(args.output/(prefix+'-relative.npz'),relative_depth=depth)
                entry.update(status='predicted_relative',originalImage=prefix+'-original.png',relativeImage=prefix+'-relative.png',
                    relativeArray=prefix+'-relative.npz',relativeMinimum=float(depth.min()),relativeMaximum=float(depth.max()),**metadata)
            except Exception as exc:entry.update(status='failed',reason=str(exc))
            result['frames'].append(entry)
            (args.output/'result.json').write_text(json.dumps(result,indent=2))
        if any(f['status']=='failed' for f in result['frames']):result['status']='relative_depth_experiment_with_failures'
    except Exception as exc:
        result.update(status='failed',reason=str(exc))
        result['frames']=[{'frameIndex':i,'status':'failed','reason':'model_unavailable: '+str(exc)} for i in indices]
    (args.output/'result.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))
    return 1 if result['status'] in {'failed','relative_depth_experiment_with_failures'} else 0


if __name__=='__main__':raise SystemExit(main())
