"""Frozen patch/plane photo consistency experiment; planes are hypotheses, never GT.

Manifest: reference/query {image,intrinsics,worldToCamera,frameIndex}, plane
{normal,offset}, patches [{id,rect:[x,y,width,height]}] in native reference
pixels, trainingFrameIndices, evaluationFrameIndices, patchesFrozenFromTraining=true.
Matrices are nested CV-coordinate rows: +Z forward, +X right, +Y down.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import time
import numpy as np
from PIL import Image, ImageDraw

@dataclass(frozen=True)
class Config:
    width: int = 640
    radius: int = 12
    minimum_texture: float = .03
    minimum_valid_fraction: float = .8
    minimum_correlation: float = .8
    maximum_offset: float = 3.
    minimum_peak_margin: float = .05
    peak_band: float = .02
    maximum_peak_radius: float = 3.


def resize_image(image, k, width):
    original = Image.fromarray(np.asarray(image, dtype=np.float32), mode='F')
    w,h = original.size; nw=min(width,w); nh=max(1,round(h*nw/w))
    resized=np.asarray(original.resize((nw,nh),Image.Resampling.BILINEAR),dtype=float)
    transform=np.array([[nw/w,0,(nw/w-1)/2],[0,nh/h,(nh/h-1)/2],[0,0,1.]])
    return resized,transform@np.asarray(k,float),(nw/w,nh/h)


def bilinear(image, pixels):
    x,y=pixels.T; h,w=image.shape
    valid=np.isfinite(pixels).all(1)&(x>=0)&(y>=0)&(x<w-1)&(y<h-1)
    out=np.full(len(x),np.nan); ids=np.flatnonzero(valid)
    a=np.floor(x[ids]).astype(int);b=np.floor(y[ids]).astype(int)
    dx=x[ids]-a;dy=y[ids]-b
    out[ids]=(image[b,a]*(1-dx)*(1-dy)+image[b,a+1]*dx*(1-dy)
              +image[b+1,a]*(1-dx)*dy+image[b+1,a+1]*dx*dy)
    return out,valid


def plane_warp(pixels, reference_k, reference_w2c, query_k, query_w2c, normal, offset):
    n=np.asarray(normal,float); r=np.asarray(reference_w2c,float);q=np.asarray(query_w2c,float)
    center=-r[:3,:3].T@r[:3,3]
    rays=np.linalg.solve(reference_k,np.c_[pixels,np.ones(len(pixels))].T).T@r[:3,:3]
    denominator=rays@n
    with np.errstate(divide='ignore',invalid='ignore'):
        distance=-(n@center+offset)/denominator
        xyz=center+distance[:,None]*rays
        local=xyz@q[:3,:3].T+q[:3,3]
        projected=local@np.asarray(query_k).T
        uv=projected[:,:2]/projected[:,2,None]
    valid=(np.abs(denominator)>1e-6)&(distance>0)&(local[:,2]>1e-8)&np.isfinite(uv).all(1)
    return uv,valid


def check_patch(reference, query, reference_k, reference_w2c, query_k, query_w2c,
                normal, offset, rect, config=Config()):
    start=time.monotonic();x,y,w,h=map(float,rect)
    if config.radius<1 or config.radius>32 or config.width<32 or not (0<config.minimum_valid_fraction<=1) or config.maximum_peak_radius<=0:
        raise ValueError('invalid_experimental_config')
    if not np.isfinite([x,y,w,h]).all() or w<2 or h<2:raise ValueError('invalid_patch_rect')
    xx,yy=np.meshgrid(np.arange(np.ceil(x),x+w),np.arange(np.ceil(y),y+h))
    pixels=np.c_[xx.ravel(),yy.ravel()]
    values,rv=bilinear(reference,pixels)
    texture=float(np.std(values[rv])) if rv.any() else 0.
    uv,geometry=plane_warp(pixels,reference_k,reference_w2c,query_k,query_w2c,normal,offset)
    shifts=[(dx,dy) for dy in range(-config.radius,config.radius+1) for dx in range(-config.radius,config.radius+1)]
    # Use identical sample support for every displacement: no optimizer may win
    # by dropping difficult pixels as it moves towards the image boundary.
    common=rv&geometry
    for dx,dy in shifts:
        _,valid=bilinear(query,uv+[dx,dy]);common &= valid
    fraction=float(common.mean());search=[]
    for dx,dy in shifts:
        sampled,_=bilinear(query,uv+[dx,dy]);corr=None
        if common.sum()>=16:
            a=values[common]-values[common].mean();b=sampled[common]-sampled[common].mean()
            denom=float(np.linalg.norm(a)*np.linalg.norm(b))
            if denom>1e-12:corr=float(np.clip(a@b/denom,-1,1))
        search.append(dict(offset=[dx,dy],correlation=corr))
    usable=[s for s in search if s['correlation'] is not None]
    predicted=next(s['correlation'] for s in search if s['offset']==[0,0])
    best=max(usable,key=lambda s:s['correlation']) if usable else None
    second=None;peak_radius=None;margin=None
    if best:
        distant=[s for s in usable if np.linalg.norm(np.subtract(s['offset'],best['offset']))>config.maximum_peak_radius]
        second=max((s['correlation'] for s in distant),default=None)
        margin=best['correlation']-second if second is not None else None
        peak_radius=max(np.linalg.norm(np.subtract(s['offset'],best['offset'])) for s in usable if s['correlation']>=best['correlation']-config.peak_band)
    verdict='insufficientEvidence';reason='unavailable_correlation'
    if texture<config.minimum_texture:reason='low_reference_texture'
    elif fraction<config.minimum_valid_fraction:reason='insufficient_common_valid_pixels'
    elif best is not None:
        if best['correlation']<config.minimum_correlation:verdict='inconsistent';reason='no_supported_local_match'
        elif margin is None or margin<config.minimum_peak_margin or peak_radius>config.maximum_peak_radius:reason='ambiguous_match'
        elif np.linalg.norm(best['offset'])>config.maximum_offset:verdict='inconsistent';reason='match_disagrees_with_ar_prediction'
        elif predicted is None or predicted<config.minimum_correlation:verdict='inconsistent';reason='predicted_patch_not_supported'
        else:verdict='supported';reason='experimental_photo_consistency_only'
    return dict(verdict=verdict,reason=reason,texture=texture,validFraction=fraction,
                predictedCorrelation=predicted,best=best,secondCorrelation=second,
                bestSecondMargin=margin,peakRadiusPixels=peak_radius,search=search,
                predictedPixels=uv[geometry].tolist(),elapsedSeconds=time.monotonic()-start,
                productAccepted=False,thresholdsCalibrated=False,planeHitsAreGroundTruth=False)


def run_manifest(manifest, output, config=Config()):
    train=set(manifest['trainingFrameIndices']);evaluation=set(manifest['evaluationFrameIndices'])
    if train&evaluation:raise ValueError('training_evaluation_overlap')
    if manifest.get('patchesFrozenFromTraining') is not True or manifest['reference']['frameIndex'] not in train:
        raise ValueError('training_frozen_reference_required')
    if manifest['query']['frameIndex'] not in evaluation:raise ValueError('query_must_be_declared_evaluation')
    if not manifest['patches'] or len({p['id'] for p in manifest['patches']})!=len(manifest['patches']):raise ValueError('empty_or_duplicate_patch_ids')
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    loaded=[]
    for key in ('reference','query'):
        entry=manifest[key]
        with Image.open(entry['image']) as image:gray=np.asarray(image.convert('L'),float)/255
        k=np.asarray(entry['intrinsics'],float);pose=np.asarray(entry['worldToCamera'],float)
        if k.shape!=(3,3) or pose.shape!=(4,4) or not np.isfinite(k).all() or not np.isfinite(pose).all():raise ValueError('invalid_camera_matrices')
        if not np.allclose(pose[3],[0,0,0,1]) or not np.allclose(pose[:3,:3]@pose[:3,:3].T,np.eye(3),atol=1e-5) or np.linalg.det(pose[:3,:3])<.999:raise ValueError('nonrigid_world_to_camera')
        loaded.append((*resize_image(gray,k,config.width),pose))
    a,ka,scale,ra=loaded[0];b,kb,_,rb=loaded[1]
    plane=manifest['plane'];n=np.asarray(plane['normal'],float);d=float(plane['offset'])
    if n.shape!=(3,) or not np.isfinite(n).all() or np.linalg.norm(n)<1e-8 or not np.isfinite(d):raise ValueError('invalid_plane_hypothesis')
    length=float(np.linalg.norm(n));n=n/length;d=d/length
    results=[];overlay_a=Image.fromarray(np.uint8(np.clip(a*255,0,255))).convert('RGB');overlay_b=Image.fromarray(np.uint8(np.clip(b*255,0,255))).convert('RGB')
    for patch in manifest['patches']:
        x,y,w,h=patch['rect'];rect=[(x+.5)*scale[0]-.5,(y+.5)*scale[1]-.5,w*scale[0],h*scale[1]]
        result=check_patch(a,b,ka,ra,kb,rb,n,d,rect,config);result['id']=patch['id'];results.append(result)
        color={'supported':'green','inconsistent':'red','insufficientEvidence':'orange'}[result['verdict']]
        ImageDraw.Draw(overlay_a).rectangle([rect[0],rect[1],rect[0]+rect[2],rect[1]+rect[3]],outline=color,width=2)
        pixels=np.asarray(result.pop('predictedPixels'))
        if pixels.size:
            lo=pixels.min(0);hi=pixels.max(0)
            if np.isfinite([*lo,*hi]).all():ImageDraw.Draw(overlay_b).rectangle([*lo,*hi],outline=color,width=2)
    overlay_a.save(output/'reference-overlay.png');overlay_b.save(output/'query-overlay.png')
    result=dict(config=asdict(config),patches=results,productAccepted=False,thresholdsCalibrated=False,
                planeHitsAreGroundTruth=False,referenceFrameIndex=manifest['reference']['frameIndex'],
                queryFrameIndex=manifest['query']['frameIndex'],allPatchesRetained=True)
    (output/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('manifest',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();run_manifest(json.loads(args.manifest.read_text()),args.output,Config(**json.loads(args.manifest.read_text()).get('experimentalConfig',{})))
