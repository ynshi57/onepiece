"""Training-only model-prior seeds and separate heldout image-track validation.

A fitted monocular prior is neither a verified surface nor measured wall geometry.
"""
from pathlib import Path
from collections import Counter
import copy
import math
import numpy as np
from PIL import Image

from .spatial_reconstruction import camera, project, reconstruct
from .spatial_depth_prior import fit_inverse_depth
from .spatial_bundle_adjustment import _exp, _log


def infer_heldout_pose_corrections(document, training_ba, training_indices, evaluation_indices):
    """Apply timestamp-interpolated training BA deltas to untouched eval poses.

    This is a separate trajectory hypothesis, never a fit to evaluation RGB,
    feature tracks, or reprojection errors. Unbracketed ends use the nearest
    training correction and are reported explicitly.
    """
    train=set(training_indices);evaluation=set(evaluation_indices)
    if train & evaluation:raise ValueError('training_evaluation_overlap')
    if training_ba['summary']['heldoutFrameIndices']:
        raise ValueError('training_ba_contains_internal_heldout')
    frames=document['keyframes'];by_epoch={};used=set()
    for record in training_ba['cameraFrames']:
        local=record['frameIndex']
        if local<0 or local>=len(training_indices):raise ValueError('invalid_training_ba_frame')
        index=training_indices[local]
        if index in used or record['heldout'] or index not in train:raise ValueError('invalid_training_ba_scope')
        used.add(index);raw=camera(frames[index]);optimized=np.asarray(record['cameraToWorld'],float).reshape(4,4,order='F')
        if not np.isfinite(optimized).all():raise ValueError('nonfinite_training_pose')
        delta=optimized@np.linalg.inv(raw.camera_to_world)
        by_epoch.setdefault(raw.epoch,[]).append((raw.time,index,delta))
    if used!=train:raise ValueError('missing_training_pose_correction')
    for samples in by_epoch.values():samples.sort(key=lambda item:item[0])
    revised=copy.deepcopy(document);diagnostics=[]
    for index in sorted(evaluation):
        raw=camera(frames[index]);samples=by_epoch.get(raw.epoch,[])
        if not samples:raise ValueError('no_training_pose_in_epoch')
        before=[s for s in samples if s[0]<=raw.time];after=[s for s in samples if s[0]>=raw.time]
        left=before[-1] if before else samples[0];right=after[0] if after else samples[-1]
        if left[1]==right[1]:
            alpha=0.;method='nearest_boundary_training_delta'
        else:
            alpha=(raw.time-left[0])/(right[0]-left[0]);method='timestamp_interpolated_training_delta'
        r0,r1=left[2][:3,:3],right[2][:3,:3]
        rotation=r0@_exp(alpha*_log(r0.T@r1))
        translation=(1-alpha)*left[2][:3,3]+alpha*right[2][:3,3]
        delta=np.eye(4);delta[:3,:3]=rotation;delta[:3,3]=translation
        corrected=delta@raw.camera_to_world
        frame=revised['keyframes'][index];frame['cameraToWorld']=corrected.ravel(order='F').tolist()
        frame['pose']['position']=dict(zip('xyz',map(float,corrected[:3,3])))
        frame['pose']['forward']=dict(zip('xyz',map(float,-corrected[:3,2])))
        diagnostics.append({'evaluationFrameIndex':index,'method':method,
            'trainingSupportFrames':[left[1],right[1]],'timeInterpolationFraction':float(alpha),
            'centerShiftMeters':float(np.linalg.norm(corrected[:3,3]-raw.center)),
            'rotationShiftDegrees':math.degrees(float(np.linalg.norm(_log(rotation))))})
    return {'document':revised,'diagnostics':diagnostics,'status':'inferred_pose_correction_not_ground_truth',
            'evaluationRGBOrFeaturesUsedForPose':False,'productAccepted':False}


def inverse_at(fit, relative, pixel, cam):
    x,y=pixel
    return (fit['a']*float(relative)+fit['b']
            +fit.get('c',0.)*(x/cam.width-.5)+fit.get('d',0.)*(y/cam.height-.5))


def spatial_joint_training_calibration(document, relative_by_frame, seed_result,
                                       training_tracks, training_indices, *, iterations=8):
    """Opt-in planar inverse-depth correction with strong spatial slope priors."""
    return joint_training_calibration(document,relative_by_frame,seed_result,
                                      training_tracks,training_indices,iterations=iterations,
                                      spatial_terms=True)


def joint_training_calibration(document, relative_by_frame, seed_result, training_tracks,
                               training_indices, *, iterations=8, spatial_terms=False):
    """Fit eight inverse-depth calibrations against training RGB tracks only.

    The initial per-view fit is an AR-scale prior, not metric ground truth. Every
    source-to-other-training-view projection remains in the objective, including
    outliers. Heldout observations and poses are forbidden here.
    """
    selected=sorted(relative_by_frame)
    train=set(training_indices)
    if (set(selected)!=set(map(int,seed_result['calibrations'])) or not set(selected).issubset(train)
            or len(selected)<6 or len(selected)>10 or not 1<=iterations<=20):
        raise ValueError('invalid_training_joint_scope')
    cams={i:camera(document['keyframes'][i]) for i in train}
    if any(c.epoch!=seed_result['epoch'] for c in cams.values()):
        raise ValueError('different_training_epoch')
    for i in selected:
        if np.shape(relative_by_frame[i])!=(cams[i].height,cams[i].width):
            raise ValueError('depth_camera_size_mismatch')
    fits={i:seed_result['calibrations'][i] if i in seed_result['calibrations'] else seed_result['calibrations'][str(i)] for i in selected}
    if any(f['a']<=0 or not np.isfinite([f['a'],f['b']]).all() for f in fits.values()):
        raise ValueError('invalid_initial_calibration')
    if spatial_terms and any(f.get('anchorCount',0)<30 for f in fits.values()):
        raise ValueError('insufficient_spatial_training_anchors')
    # The input tracks must be built from a training-only subdocument; local
    # indices are mapped back before this call. Reject leaked eval observations.
    samples=[];rejections=Counter()
    for track in training_tracks:
        observations=track['observations']
        if any(o['frameIndex'] not in train for o in observations):
            raise ValueError('evaluation_track_forbidden')
        for source in observations:
            i=source['frameIndex']
            if i not in relative_by_frame:continue
            x,y=source['pixel'];depth=relative_by_frame[i]
            if not np.isfinite([x,y]).all() or not 0<=x<cams[i].width or not 0<=y<cams[i].height:
                rejections['invalid_source_pixel']+=max(0,len(observations)-1);continue
            d=float(depth[min(depth.shape[0]-1,int(round(y))),min(depth.shape[1]-1,int(round(x)))])
            if not np.isfinite(d):
                rejections['nonfinite_relative_depth']+=max(0,len(observations)-1);continue
            for target in observations:
                if target is source:continue
                samples.append((i,np.asarray(source['pixel'],float),d,target['frameIndex'],np.asarray(target['pixel'],float)))
    if not samples:raise ValueError('no_training_cross_view_tracks')
    # A source ray projected into another camera can be written as A+B*inverse.
    # Cache those camera-only terms once; finite-difference iterations then
    # evaluate exactly the same perspective projection without matrix inverses.
    source_slots=[];relative_values=[];normalized_xy=[];a_terms=[];b_terms=[];target_pixels=[];target_intrinsics=[]
    inverse_cameras={i:np.linalg.inv(cams[i].world_to_camera) for i in selected}
    for i,pixel,d,t,observed in samples:
        source=cams[i];target=cams[t];world=inverse_cameras[i]
        ray=np.linalg.solve(source.k,np.r_[pixel,1.]);ray/=ray[2]
        a_terms.append(target.world_to_camera[:3,:3]@world[:3,:3]@ray)
        b_terms.append(target.world_to_camera[:3,:3]@world[:3,3]+target.world_to_camera[:3,3])
        source_slots.append(selected.index(i));relative_values.append(d)
        normalized_xy.append([pixel[0]/source.width-.5,pixel[1]/source.height-.5])
        target_pixels.append(observed);target_intrinsics.append(target.k)
    source_slots=np.asarray(source_slots,int);relative_values=np.asarray(relative_values)
    normalized_xy=np.asarray(normalized_xy);a_terms=np.asarray(a_terms);b_terms=np.asarray(b_terms)
    target_pixels=np.asarray(target_pixels);target_intrinsics=np.asarray(target_intrinsics)
    initial=np.array([[fits[i]['a'],fits[i]['b']] for i in selected],float)
    # Scaling b by median initial inverse depth makes the prior comparable to a.
    scales=np.array([max(.05,float(np.median(initial[j,0]*relative_by_frame[i]+initial[j,1]))) for j,i in enumerate(selected)])
    params=np.zeros((len(selected),4 if spatial_terms else 2),float)
    def residuals(p):
        calibration=np.column_stack((initial[:,0]*np.exp(p[:,0]),initial[:,1]+scales*p[:,1]))
        inverse=calibration[source_slots,0]*relative_values+calibration[source_slots,1]
        if spatial_terms:
            inverse+=scales[source_slots]*(p[source_slots,2]*normalized_xy[:,0]
                                           +p[source_slots,3]*normalized_xy[:,1])
        q=a_terms+b_terms*inverse[:,None]
        valid=np.isfinite(inverse)&(inverse>.05)&np.isfinite(q).all(axis=1)&(q[:,2]>1e-8)
        homogeneous=np.einsum('nij,nj->ni',target_intrinsics,q)
        predicted=homogeneous[:,:2]/np.where(valid,homogeneous[:,2],1.)[:,None]
        residual=predicted-target_pixels
        residual[~valid]=[100.,100.]
        bad=int((~valid).sum())
        rows=residual.ravel().tolist()
        # AR-scale prior and positive-depth barrier are live objective terms.
        prior_width=np.array([.20,.20,.05,.05] if spatial_terms else [.20,.20])
        rows.extend((p/prior_width).ravel().tolist())
        for j,i in enumerate(selected):
            depth=relative_by_frame[i][::96,::96]
            inverse_grid=calibration[j,0]*depth+calibration[j,1]
            if spatial_terms:
                yy,xx=np.mgrid[:depth.shape[0],:depth.shape[1]]
                inverse_grid=inverse_grid+scales[j]*(p[j,2]*(96*xx/cams[i].width-.5)
                                                     +p[j,3]*(96*yy/cams[i].height-.5))
            minimum=float(np.min(inverse_grid))
            rows.append(100.*max(0.,.05-minimum))
        return np.asarray(rows),bad
    def cost(r):
        abs_r=np.abs(r);return float(np.sum(np.where(abs_r<=3.,.5*r*r,3.*abs_r-4.5)))
    trace=[];before,bad_before=residuals(params);initial_cost=cost(before)
    for iteration in range(iterations):
        base,bad=residuals(params);jac=np.empty((len(base),params.size))
        for k in range(params.size):
            probe=params.copy();probe.flat[k]+=1e-4
            jac[:,k]=(residuals(probe)[0]-base)/1e-4
        weight=np.minimum(1.,3./np.maximum(np.abs(base),1e-12))
        h=(jac.T*weight)@jac+np.eye(params.size)*1e-3
        gradient=jac.T@(weight*base)
        try:step=np.linalg.solve(h,-gradient).reshape(params.shape)
        except np.linalg.LinAlgError:
            trace.append({'iteration':iteration,'accepted':False,'reason':'singular_normal_equations'});break
        accepted=False
        for factor in (1.,.5,.25,.125,.0625):
            candidate=params+np.clip(step*factor,-.10,.10)
            trial,trial_bad=residuals(candidate)
            if cost(trial)<cost(base) and trial_bad<=bad:
                params=candidate;accepted=True;break
        trace.append({'iteration':iteration,'accepted':accepted,'objective':cost(residuals(params)[0]),
                      'nonprojectableCount':residuals(params)[1]})
        if not accepted:break
    final,bad_after=residuals(params)
    calibrated={}
    for j,i in enumerate(selected):
        fit=dict(fits[i]);fit.update(a=float(initial[j,0]*np.exp(params[j,0])),
            b=float(initial[j,1]+scales[j]*params[j,1]),jointTrainingOptimized=True)
        if spatial_terms:
            fit.update(c=float(scales[j]*params[j,2]),d=float(scales[j]*params[j,3]),
                       spatialCorrection='centered_image_plane_linear')
        calibrated[i]=fit
    return {'calibrations':calibrated,'trainingFrameIndices':list(training_indices),
        'trainingPairCount':len(samples),'rejectedTrainingPairReasons':dict(rejections),
        'initialNonprojectableCount':bad_before,'optimizedNonprojectableCount':bad_after,
        'initialTrainingObjective':initial_cost,'optimizedTrainingObjective':cost(final),
        'steps':trace,'evaluationUsed':False,'productAccepted':False,
        'scaleVerified':False,'positiveDepthConstraint':'objective_penalty_and_rejected_step',
        'spatialTerms':bool(spatial_terms),'spatialSlopePriorFraction':.05 if spatial_terms else None}


def backproject(cam,pixel,depth):
    if not np.isfinite(depth) or depth<=0:raise ValueError('nonpositive_prior_depth')
    ray=np.linalg.solve(cam.k,np.r_[pixel,1.])
    local=ray*(depth/ray[2])
    return (np.linalg.inv(cam.world_to_camera)@np.r_[local,1.])[:3]


def remap_training_seeds(document, seed_result, relative_by_frame, calibrations):
    """Move only existing training seeds under calibrated depth/poses; keep failures."""
    if not set(relative_by_frame).issubset(set(seed_result['trainingFrameIndices'])):
        raise ValueError('evaluation_depth_forbidden')
    updated=dict(seed_result);updated['calibrations']=calibrations
    points=[];rejected=[]
    for point in seed_result['points']:
        i=point['sourceFrameIndex'];pixel=point['sourcePixel']
        fit=calibrations.get(i,calibrations.get(str(i)))
        if i not in relative_by_frame or fit is None:
            rejected.append({'sourceFrameIndex':i,'sourcePixel':pixel,'reason':'training_calibration_unavailable'});continue
        d=relative_by_frame[i];x,y=pixel
        try:
            cam=camera(document['keyframes'][i])
            inverse=inverse_at(fit,d[int(y),int(x)],pixel,cam)
            position=backproject(cam,pixel,1/inverse)
            points.append({**point,'position':dict(zip('xyz',map(float,position)))})
        except (ValueError,ZeroDivisionError,np.linalg.LinAlgError) as exc:
            rejected.append({'sourceFrameIndex':i,'sourcePixel':pixel,'reason':str(exc)})
    updated.update(points=points,pointCount=len(points),jointRemapRejectedSeeds=rejected,
                   jointRemapRejectedSeedCount=len(rejected),productAccepted=False,verifiedSurface=False)
    return updated


def select_training_prior_frames(anchors,training_indices,maximum_views=8):
    if not 6<=maximum_views<=10:raise ValueError('six_to_ten_prior_views_required')
    allowed=set(training_indices);counts=Counter()
    for anchor in anchors:
        if anchor['frameIndex'] not in allowed or not set(anchor['trainingSupportFrames']).issubset(allowed):
            raise ValueError('evaluation_anchor_forbidden')
        counts[anchor['frameIndex']]+=1
    # Selection happens before prediction and uses no eval result or monocular output.
    selected=sorted(sorted(allowed,key=lambda i:(-counts[i],i))[:maximum_views])
    return {'frameIndices':selected,'selectionPolicy':'training_anchor_count_before_prediction',
            'candidateAnchorCounts':{str(i):counts[i] for i in sorted(allowed)}}


def build_training_seeds(document,directory,relative_by_frame,anchors,training_indices,*,epoch,stride=96):
    if not 32<=stride<=256:raise ValueError('invalid_prior_grid_stride')
    train=set(training_indices)
    if not set(relative_by_frame).issubset(train):raise ValueError('evaluation_depth_forbidden')
    points=[];views=[];calibrations={}
    for index,depth in sorted(relative_by_frame.items()):
        entry={'frameIndex':index,'candidateCount':0,'acceptedCount':0,'rejectedCount':0}
        try:
            frame=document['keyframes'][index]
            if frame['pose']['epoch']!=epoch:raise ValueError('different_training_epoch')
            cam=camera(frame)
            if np.shape(depth)!=(cam.height,cam.width):raise ValueError('depth_camera_size_mismatch')
            entry['candidateCount']=len(range(stride//2,cam.height,stride))*len(range(stride//2,cam.width,stride))
            fit=fit_inverse_depth(depth,anchors,frame_index=index,training_frame_indices=training_indices)
            with Image.open(Path(directory)/frame['imageFile']) as image:
                if image.size!=(cam.width,cam.height):raise ValueError('image_camera_size_mismatch')
                rgb=np.asarray(image.convert('RGB'))
            calibrations[index]=fit
            rejected=[]
            for y in range(stride//2,cam.height,stride):
                for x in range(stride//2,cam.width,stride):
                    try:
                        inverse=fit['a']*float(depth[y,x])+fit['b']
                        position=backproject(cam,[x,y],1/inverse)
                        points.append({'position':dict(zip('xyz',map(float,position))),
                            'colorRGB':rgb[y,x].tolist(),'epoch':epoch,'sourceFrameIndex':index,
                            'sourcePixel':[x,y],'source':'model_prior','verifiedSurface':False,
                            'confidenceAvailable':False,'metricScaleIndependentlyVerified':False})
                        entry['acceptedCount']+=1
                    except (ValueError,np.linalg.LinAlgError) as exc:
                        rejected.append({'pixel':[x,y],'reason':str(exc)});entry['rejectedCount']+=1
            entry.update(status='model_prior_seeded',calibration=fit,rejectedSamples=rejected)
        except (ValueError,OSError,np.linalg.LinAlgError) as exc:
            entry.update(status='failed',reason=str(exc),rejectedCount=entry['candidateCount'])
        views.append(entry)
    return {'status':'training_model_prior_experiment','productAccepted':False,'epoch':epoch,
        'points':points,'pointCount':len(points),'views':views,'calibrations':calibrations,
        'trainingFrameIndices':list(training_indices),'selectedViewCount':len(relative_by_frame),
        'successfulViewCount':sum(v['status']=='model_prior_seeded' for v in views),
        'candidatePointCount':sum(v['candidateCount'] for v in views),
        'rejectedPointCount':sum(v['rejectedCount'] for v in views),'gridStride':stride,
        'evaluationUsedForFittingOrSeeds':False,'verifiedSurface':False,
        'limitations':['monocular_model_prior','raw_ARKit_scale_and_poses_unverified',
                       'not_measured_wall_geometry','no_model_confidence','no_multiview_fusion']}


def validate_heldout(document,directory,seed_result,relative_by_frame,evaluation_indices,*,reprojection_threshold=3.,occlusion_fraction=.05,
                     frozen_validation_tracks=None,frozen_validation_indices=None):
    """Eval RGB forms image-only tracks after seeds freeze; never returns replacement seeds.

    Z-buffer is prior self-occlusion consistency, not independent real-depth ground truth.
    """
    if reprojection_threshold!=3. or occlusion_fraction!=.05:raise ValueError('fixed_validation_thresholds_required')
    train=set(seed_result['trainingFrameIndices']);evaluation=set(evaluation_indices)
    if train & evaluation:raise ValueError('training_evaluation_overlap')
    if not set(relative_by_frame).issubset(train):raise ValueError('evaluation_depth_forbidden')
    indices=sorted(train|evaluation)
    if any(document['keyframes'][i]['pose']['epoch']!=seed_result['epoch'] for i in indices):raise ValueError('different_validation_epoch')
    if (frozen_validation_tracks is None)!=(frozen_validation_indices is None):
        raise ValueError('incomplete_frozen_validation_tracks')
    if frozen_validation_tracks is not None:
        if list(frozen_validation_indices)!=indices:raise ValueError('validation_frame_indices_changed')
        validation={'featureTracks':frozen_validation_tracks}
    else:
        validation=reconstruct({**document,'keyframes':[document['keyframes'][i] for i in indices]},Path(directory))
    cams={i:camera(document['keyframes'][i]) for i in indices}
    points=np.array([[p['position'][a] for a in 'xyz'] for p in seed_result['points']],dtype=float).reshape(-1,3)
    zbuffers={}
    for index in evaluation:
        cam=cams[index];buffer={}
        if len(points):
            local=points@cam.world_to_camera[:3,:3].T+cam.world_to_camera[:3,3]
            valid=local[:,2]>.05;local=local[valid]
            uv=(local@cam.k.T)[:,:2]/local[:,2:3]
            for pixel,z in zip(uv,local[:,2]):
                x,y=pixel
                if 0<=x<cam.width and 0<=y<cam.height:
                    cell=(int(x)//16,int(y)//16);buffer[cell]=min(float(z),buffer.get(cell,float('inf')))
        zbuffers[index]=buffer
    records=[];selected=set(relative_by_frame);fits={int(k):v for k,v in seed_result['calibrations'].items()}
    for track in validation.get('featureTracks',[]):
        observations=[{**o,'frameIndex':indices[o['frameIndex']]} for o in track['observations']]
        heldout=[o for o in observations if o['frameIndex'] in evaluation]
        sources=[o for o in observations if o['frameIndex'] in selected]
        for target in heldout:
            if not sources:
                records.append({'trackId':track['id'],'evaluationFrameIndex':target['frameIndex'],
                    'status':'failed','reason':'no_selected_training_prior_observation'})
            for source in sources:
                entry={'trackId':track['id'],'sourceFrameIndex':source['frameIndex'],
                    'evaluationFrameIndex':target['frameIndex'],'status':'failed'}
                try:
                    index=source['frameIndex'];fit=fits.get(index)
                    if fit is None:raise ValueError('training_prior_unavailable')
                    depth=relative_by_frame[index];x,y=source['pixel'];yy=min(depth.shape[0]-1,max(0,int(round(y))));xx=min(depth.shape[1]-1,max(0,int(round(x))))
                    position=backproject(cams[index],source['pixel'],1/inverse_at(fit,depth[yy,xx],source['pixel'],cams[index]))
                    predicted,z=project(cams[target['frameIndex']],position)
                    error=float(np.linalg.norm(predicted-np.asarray(target['pixel'])))
                    entry.update(reprojectionErrorPixels=error,predictedPixel=predicted.tolist(),observedPixel=target['pixel'],positiveDepth=True)
                    cam=cams[target['frameIndex']]
                    if not 0<=predicted[0]<cam.width or not 0<=predicted[1]<cam.height:raise ValueError('projection_outside_heldout_image')
                    buffer=zbuffers[target['frameIndex']]
                    cell=(int(predicted[0])//16,int(predicted[1])//16);nearest=buffer.get(cell)
                    entry['priorSelfOcclusion']=('unknown_no_grid_support' if nearest is None else 'consistent' if z<=nearest*(1+occlusion_fraction) else 'occluded_by_other_model_prior')
                    if nearest is not None:entry['priorSelfOcclusionRelativeExcess']=max(0.,float(z/nearest-1))
                    entry['reprojectionPassed']=error<=reprojection_threshold
                    if error>reprojection_threshold:raise ValueError('heldout_reprojection_failed')
                    entry.update(status='passed_reprojection')
                except (ValueError,ZeroDivisionError,np.linalg.LinAlgError) as exc:entry['reason']=str(exc)
                records.append(entry)
    summaries=[]
    for index in sorted(evaluation):
        entries=[r for r in records if r['evaluationFrameIndex']==index];passed=sum(r['status']=='passed_reprojection' for r in entries)
        summaries.append({'frameIndex':index,'candidateCount':len(entries),'passedReprojectionCount':passed,
            'failedCount':len(entries)-passed,'status':'evaluated' if entries else 'failed_no_feature_track_observations',
            'reasonCounts':dict(Counter(r.get('reason','passed_reprojection') for r in entries))})
    errors=[r['reprojectionErrorPixels'] for r in records if 'reprojectionErrorPixels' in r]
    return {'status':'heldout_model_prior_validation','productAccepted':False,'evaluationFrameCount':len(evaluation),
        'candidateCount':len(records),'passedReprojectionCount':sum(r['status']=='passed_reprojection' for r in records),
        'failedCount':sum(r['status']!='passed_reprojection' for r in records),'allProjectedMedianErrorPixels':float(np.median(errors)) if errors else None,
        'allProjectedP95ErrorPixels':float(np.percentile(errors,95)) if errors else None,
        'reprojectionThresholdPixels':reprojection_threshold,'selfOcclusionFraction':occlusion_fraction,'zBufferCellPixels':16,
        'selfOcclusionCounts':dict(Counter(r.get('priorSelfOcclusion','unavailable') for r in records)),
        'frames':summaries,'records':records,'evaluationUsedForFittingOrSeeds':False,
        'limitations':['image_track_correspondences_can_be_wrong','raw_ARKit_poses_unverified',
            'zbuffer_is_model_prior_self_consistency_not_measured_occlusion_truth','no_independent_metric_geometry_truth']}
