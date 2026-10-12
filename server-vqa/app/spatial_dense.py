"""Bounded CPU plane-sweep photometric depth experiment using saved monocular views.

NCC is computed over real warped pixels; no monocular learned depth, Manhattan
wall completion or unknown-region fill. Held-out target images only evaluate
training-selected depth. Photometric support is not geometric ground truth.
"""
from __future__ import annotations
from collections import Counter
from pathlib import Path
import time
import numpy as np
from PIL import Image
from .spatial_reconstruction import camera, resize_intrinsics


def _box_sum(image, radius=2):
    pad=[(0,0)]*(image.ndim-2)+[(radius,radius),(radius,radius)]
    padded=np.pad(image,pad,mode='constant')
    integral=np.pad(padded,[(0,0)]*(image.ndim-2)+[(1,0),(1,0)]).cumsum(axis=-2).cumsum(axis=-1)
    width=radius*2+1
    return integral[...,width:,width:]-integral[...,:-width,width:]-integral[...,width:,:-width]+integral[...,:-width,:-width]


def _ncc(reference, warped, valid):
    count=25.
    refmean=_box_sum(reference)/count
    targetmean=_box_sum(warped)/count
    refvar=np.maximum(0,_box_sum(reference*reference)/count-refmean**2)
    targetvar=np.maximum(0,_box_sum(warped*warped)/count-targetmean**2)
    covariance=_box_sum(warped*reference)/count-targetmean*refmean
    denominator=np.sqrt(refvar*targetvar)
    supported=(_box_sum(valid.astype(float))>=25-1e-6)&(refvar>.0004)&(targetvar>.0004)
    correlation=np.divide(covariance,denominator,out=np.zeros_like(covariance),where=denominator>1e-12)
    return np.where(supported,1-np.clip(correlation,-1,1),np.inf),supported


def _bilinear(image,u,v):
    h,w=image.shape
    valid=np.isfinite(u)&np.isfinite(v)&(u>=0)&(v>=0)&(u<w-1)&(v<h-1)
    safe_u=np.clip(np.nan_to_num(u,nan=0,posinf=0,neginf=0),0,w-1)
    safe_v=np.clip(np.nan_to_num(v,nan=0,posinf=0,neginf=0),0,h-1)
    ix=np.floor(safe_u).astype(int);iy=np.floor(safe_v).astype(int)
    jx=np.minimum(ix+1,w-1);jy=np.minimum(iy+1,h-1)
    dx=safe_u-ix;dy=safe_v-iy
    out=(1-dx)*(1-dy)*image[iy,ix]+dx*(1-dy)*image[iy,jx]+(1-dx)*dy*image[jy,ix]+dx*dy*image[jy,jx]
    return out,valid


def _load(frame,directory,width):
    name=frame['imageFile']
    if Path(name).name!=name: raise ValueError('unsafe_image_path')
    with Image.open(directory/name) as image:
        if image.size!=(frame['width'],frame['height']): raise ValueError('image_dimensions_mismatch')
        height=max(1,round(image.height*width/image.width))
        rgb=np.asarray(image.convert('RGB').resize((width,height),Image.Resampling.LANCZOS),dtype=float)/255.
    gray=rgb@np.array([.299,.587,.114])
    return rgb,gray


def _warp(cam_ref,cam_target,rays,depths,target_gray):
    shape=(len(depths),)+rays.shape[1:]
    depth_volume=depths[:,None,None] if depths.ndim==1 else depths[None,:,:]
    local=rays[None,:,:,:]*depth_volume[:,None,:,:]
    world=np.einsum('ij,djhw->dihw',cam_ref.world_to_camera[:3,:3].T,local-cam_ref.world_to_camera[:3,3][None,:,None,None])
    q=np.einsum('ij,djhw->dihw',cam_target.world_to_camera[:3,:3],world)+cam_target.world_to_camera[:3,3][None,:,None,None]
    h,w=target_gray.shape
    k=resize_intrinsics(cam_target.k,cam_target.width,cam_target.height,w,h)
    pixels=np.einsum('ij,djhw->dihw',k,q)
    with np.errstate(divide='ignore',invalid='ignore'):
        u=pixels[:,0]/pixels[:,2];v=pixels[:,1]/pixels[:,2]
    warped,valid=_bilinear(target_gray,u,v)
    return warped,valid&(q[:,2]>1e-6)


def _view_geometry(reference, target):
    """Pose-only overlap estimate; never reads heldout RGB or depth scores."""
    yy, xx = np.meshgrid(np.linspace(0, reference.height-1, 7),
                         np.linspace(0, reference.width-1, 9), indexing='ij')
    rays = np.linalg.solve(reference.k, np.stack([xx, yy, np.ones_like(xx)]).reshape(3,-1))
    local = np.concatenate([rays * depth for depth in [.5, 1., 2., 4., 8.]],axis=1)
    world = reference.world_to_camera[:3,:3].T @ (local-reference.world_to_camera[:3,3,None])
    q = target.world_to_camera[:3,:3] @ world + target.world_to_camera[:3,3,None]
    pixels = target.k @ q
    with np.errstate(divide='ignore',invalid='ignore'):
        uv = pixels[:2] / pixels[2]
    visible = (q[2]>.05)&np.isfinite(uv).all(axis=0)&(uv[0]>=0)&(uv[0]<target.width)&(uv[1]>=0)&(uv[1]<target.height)
    forward = reference.world_to_camera[:3,:3].T @ np.array([0.,0.,1.])
    delta = target.center-reference.center
    lateral = float(np.linalg.norm(delta - forward*np.dot(delta,forward)))
    overlap = float(visible.mean())
    return {'overlapEstimate':overlap,'lateralBaselineMeters':lateral,
            'baselineMeters':float(np.linalg.norm(delta)),
            'score':overlap*min(lateral,.3)}


def reconstruct_dense(document,directory:Path,*,maximum_frames=12,image_width=128,
                      depth_count=48,minimum_depth_meters=.3,maximum_depth_meters=12.,
                      minimum_baseline_meters=.05,maximum_points=20000,view_selection='temporal'):
    if (not 1<=maximum_frames<=24 or not 64<=image_width<=256 or not 16<=depth_count<=96
        or not 0<minimum_depth_meters<maximum_depth_meters or not 1<=maximum_points<=50000):
        raise ValueError('invalid_dense_limits')
    if view_selection not in {'temporal','pose_overlap'}: raise ValueError('invalid_view_selection')
    started=time.perf_counter();frames=document.get('keyframes',[])
    cameras={};image_cache={};diagnostics=[];results=[];points=[];reasons=Counter();capacity_excluded=0
    for i,frame in enumerate(frames):
        try:cameras[i]=camera(frame)
        except (ValueError,KeyError,np.linalg.LinAlgError) as exc:diagnostics.append({'frameIndex':i,'reason':str(exc)})
    def image(i):
        if i not in image_cache:image_cache[i]=_load(frames[i],directory,image_width)
        return image_cache[i]
    selected=np.unique(np.linspace(0,max(0,len(frames)-1),min(maximum_frames,len(frames)),dtype=int))
    selected_pixel_count=sum(image_width*max(1,round(int(frames[i]['height'])*image_width/int(frames[i]['width'])))
                             for i in selected if int(frames[i].get('width',0))>0 and int(frames[i].get('height',0))>0)
    depths=1/np.linspace(1/minimum_depth_meters,1/maximum_depth_meters,depth_count)
    for index in selected:
        index=int(index)
        if index not in cameras:continue
        cam=cameras[index]
        neighbours=[i for i,c in cameras.items() if i!=index and c.epoch==cam.epoch
                    and minimum_baseline_meters<=np.linalg.norm(c.center-cam.center)<=1.5
                    and abs(c.time-cam.time)<=12.]
        neighbours.sort(key=lambda i:abs(cameras[i].time-cam.time))
        if len(neighbours)<3:
            diagnostics.append({'frameIndex':index,'reason':'insufficient_three_view_baseline','neighbourCount':len(neighbours)});continue
        # Third distinct view is held out; neither its NCC nor its pixels choose depth.
        holdout=neighbours[2]  # Identical fixed holdout across selection experiments.
        view_candidates=[{'frameIndex':i,**_view_geometry(cam,cameras[i])} for i in neighbours if i!=holdout]
        training=(neighbours[:2] if view_selection=='temporal' else
                  [v['frameIndex'] for v in sorted(view_candidates,key=lambda v:(-v['score'],v['frameIndex']))[:2]])
        try:rgb,reference=image(index); targets=[image(i)[1] for i in training]; validation=image(holdout)[1]
        except (OSError,ValueError,KeyError) as exc:
            diagnostics.append({'frameIndex':index,'reason':str(exc)});continue
        h,w=reference.shape
        yy,xx=np.mgrid[:h,:w];k=resize_intrinsics(cam.k,cam.width,cam.height,w,h)
        rays=np.linalg.solve(k,np.stack([xx,yy,np.ones_like(xx)]).reshape(3,-1)).reshape(3,h,w)
        costs=[]
        for i,target in zip(training,targets):
            warped,valid=_warp(cam,cameras[i],rays,depths,target)
            cost,_=_ncc(reference,warped,valid);costs.append(cost)
        # Both training views must support the same depth, not the best one only.
        cost=np.maximum(costs[0],costs[1])
        best_index=np.argmin(cost,axis=0)
        best=np.take_along_axis(cost,best_index[None],axis=0)[0]
        alternatives=cost.copy()
        for offset in [-1,0,1]:
            candidates=best_index+offset;good=(candidates>=0)&(candidates<depth_count)
            alternatives[candidates[good],yy[good],xx[good]]=np.inf
        second=np.min(alternatives,axis=0)
        with np.errstate(invalid='ignore'):margin=second-best
        texture=_box_sum(reference*reference)/25-(_box_sum(reference)/25)**2
        finite=np.isfinite(best)
        accepted=finite&(best<=.15)&(margin>=.03)&(best_index>0)&(best_index<depth_count-1)&(texture>.0004)
        pre_refinement_candidates=accepted.copy()
        # Training-only sub-bin inverse-depth interpolation; heldout never chooses depth.
        lower=np.take_along_axis(cost,np.maximum(0,best_index-1)[None],axis=0)[0]
        upper=np.take_along_axis(cost,np.minimum(depth_count-1,best_index+1)[None],axis=0)[0]
        with np.errstate(invalid='ignore',divide='ignore'):
            curvature=lower-2*best+upper
            subbin=.5*(lower-upper)/curvature
        subbin=np.where(np.isfinite(subbin)&(curvature>1e-6),np.clip(subbin,-.5,.5),0.)
        inv_step=(1/maximum_depth_meters-1/minimum_depth_meters)/(depth_count-1)
        depth=1/(1/depths[best_index]+subbin*inv_step)
        refined_training=[]
        for i,target in zip(training,targets):
            warped,valid=_warp(cam,cameras[i],rays,depth,target)
            refined_cost,_=_ncc(reference,warped,valid);refined_training.append(refined_cost[0])
        refined_best=np.maximum(refined_training[0],refined_training[1])
        accepted&=np.isfinite(refined_best)&(refined_best<=.15)
        best=refined_best
        # Ray intersection must have enough actual angular support in BOTH training views.
        local=rays*depth[None]
        world=np.einsum('ij,jhw->ihw',cam.world_to_camera[:3,:3].T,local-cam.world_to_camera[:3,3,None,None])
        a=world-cam.center[:,None,None];a/=np.linalg.norm(a,axis=0)[None]
        min_parallax=np.full((h,w),180.)
        for i in training:
            b=world-cameras[i].center[:,None,None];b/=np.linalg.norm(b,axis=0)[None]
            angle=np.degrees(np.arccos(np.clip(np.sum(a*b,axis=0),-1,1)))
            min_parallax=np.minimum(min_parallax,angle)
        accepted&=min_parallax>=1.
        # Evaluate selected depth by taking the corresponding cost from a third warped image.
        warped,valid=_warp(cam,cameras[holdout],rays,depth,validation)
        validation_cost,_=_ncc(reference,warped,valid)
        validation_best=validation_cost[0]
        heldout_pass=accepted&np.isfinite(validation_best)&(validation_best<=.20)
        selected_count=int(accepted.sum());valid_count=int(heldout_pass.sum())
        reason_counts={'texturelessOrInvalid':int((~finite|(texture<=.0004)).sum()),
                       'trainingPhotometricFailed':int((finite&(best>.15)).sum()),
                       'ambiguousDepth':int((finite&(margin<.03)).sum()),
                       'depthRangeBoundary':int(((best_index==0)|(best_index==depth_count-1)).sum()),
                       'refinementTrainingRejected':int((pre_refinement_candidates&(~np.isfinite(refined_best)|(refined_best>.15))).sum()),
                       'insufficientParallax':int((min_parallax<1.).sum()),
                       'heldoutPhotometricFailed':selected_count-valid_count}
        reasons.update(reason_counts)
        depth_output=np.where(heldout_pass,depth,np.nan)
        # No opaque dense fills: explicit null depth outside verified photometric samples.
        serial_depth=[[float(z) if np.isfinite(z) else None for z in row] for row in depth_output]
        results.append({'frameIndex':index,'trainingFrameIndices':training,'heldoutFrameIndex':holdout,
                        'viewSelection':view_selection,'viewCandidates':view_candidates,
                        'width':w,'height':h,'evaluatedPixelCount':w*h,
                        'preRefinementTrainingCandidateCount':int(pre_refinement_candidates.sum()),'trainingCandidateCount':selected_count,
                        'heldoutPassCount':valid_count,'heldoutFailCount':selected_count-valid_count,
                        'depthMeters':serial_depth,'rejectionCountsOverlap':reason_counts,
                        'semanticVerified':False,'geometricAccuracyVerified':False})
        for y,x in zip(*np.where(heldout_pass)):
            if len(points)>=maximum_points:capacity_excluded+=1;continue
            xyz=world[:,y,x]
            points.append({'id':f'dense-{index}-{int(x)}-{int(y)}','epoch':cam.epoch,
                           'position':dict(zip('xyz',xyz.tolist())),'color':np.rint(rgb[y,x]*255).astype(int).tolist(),
                           'frameIndex':index,'pixel':[float((x+.5)*cam.width/w-.5),float((y+.5)*cam.height/h-.5)],
                           'depthMeters':float(depth[y,x]),
                           'depthSamplingHalfIntervalMeters':float((depths[best_index[y,x]+1]-depths[best_index[y,x]-1])/2),
                           'trainingNCC':float(1-best[y,x]),
                           'heldoutNCC':float(1-validation_best[y,x]),'minimumParallaxDegrees':float(min_parallax[y,x]),
                           'photometricSupported':True,'verifiedInThirdView':False,
                           'geometricAccuracyVerified':False,'semanticLabel':None})
    return {'version':'monocular-plane-sweep-photometric-v3','status':'photometric_samples' if points else 'insufficient_evidence',
            'originalPreserved':True,'defaultEnabled':False,'points':points,'depthFrames':results,'frameDiagnostics':diagnostics,
            'summary':{'selectedFrameCount':len(selected),'processedFrameCount':len(results),'pointCount':len(points),
                       'evaluatedPixelCount':selected_pixel_count,
                       'processedPixelCount':sum(f['evaluatedPixelCount'] for f in results),
                       'unprocessedPixelCount':selected_pixel_count-sum(f['evaluatedPixelCount'] for f in results),
                       'unknownPixelCount':selected_pixel_count-sum(f['heldoutPassCount'] for f in results),
                       'preRefinementTrainingCandidateCount':sum(f['preRefinementTrainingCandidateCount'] for f in results),
                       'trainingCandidateCount':sum(f['trainingCandidateCount'] for f in results),
                       'heldoutPassCount':sum(f['heldoutPassCount'] for f in results),
                       'heldoutFailCount':sum(f['heldoutFailCount'] for f in results),
                       'capacityExcludedPointCount':capacity_excluded,'rejectionCountsOverlap':dict(reasons)},
            'parameters':{'viewSelection':view_selection,'imageWidth':image_width,'depthCount':depth_count,'depthRangeMeters':[minimum_depth_meters,maximum_depth_meters],
                          'trainingNCCMinimum':.85,'heldoutNCCMinimum':.80,'depthUniquenessMargin':.03,
                          'minimumParallaxDegrees':1.,'minimumPatchVariance':.0004},
            'limitations':['Unknown/textureless pixels have null depth, not completed walls.',
                           'Training and heldout cameras still inherit AR pose and unverified scale.',
                           'Photometric consistency does not certify depth, wall identity, glass or dynamic exclusion.',
                           'Rolling shutter, exposure changes, reflection and pose error can fail this reconstruction.',
                           'This is bounded offline CPU geometry, not real-time VIO or complete room mapping.'],
            'elapsedMilliseconds':(time.perf_counter()-started)*1000}
