"""Opt-in alternating camera/landmark BA, with globally isolated validation frames.

Training pixels only enter normal equations and acceptance. Fixed heldout poses
are an ARKit consistency reference, not physical trajectory ground truth. Raw
frames/tracks are immutable. This experiment is not a substitute for VIO/SLAM.
"""
from __future__ import annotations
from collections import Counter
import hashlib
import math
import time
import numpy as np
from .spatial_reconstruction import camera, triangulate, project, Camera, _AXES


def _skew(v):
    x,y,z=v
    return np.array([[0,-z,y],[z,0,-x],[-y,x,0.]])


def _exp(w):
    angle=np.linalg.norm(w)
    k=_skew(w)
    if angle<1e-9:
        return np.eye(3)+k+.5*k@k
    return np.eye(3)+math.sin(angle)/angle*k+(1-math.cos(angle))/angle**2*k@k


def _log(r):
    angle=math.acos(float(np.clip((np.trace(r)-1)/2,-1,1)))
    v=np.array([r[2,1]-r[1,2],r[0,2]-r[2,0],r[1,0]-r[0,1]])
    return .5*v if angle<1e-7 else angle/(2*math.sin(angle))*v


def _prediction(cam, rotation, shift, point):
    q0=cam.world_to_camera[:3,:3]@point+cam.world_to_camera[:3,3]
    rotated=rotation@q0
    q=rotated+shift
    if q[2]<=1e-6 or not np.isfinite(q).all():
        return None
    h=cam.k@q
    pixel=h[:2]/h[2]
    d=(cam.k[:2,:]*h[2]-h[:2,None]*cam.k[2,:])/h[2]**2
    return pixel, d@rotation@cam.world_to_camera[:3,:3], np.column_stack((d@-_skew(rotated),d))


def _huber(errors, threshold):
    # 2D pixel residual magnitude, not separate scalar components.
    norm=np.linalg.norm(errors)
    return 1. if norm<=threshold else threshold/max(norm,1e-12)


def bundle_adjust(document, feature_tracks, *, iterations=8, maximum_tracks=2500,
                  holdout_stride=7, maximum_reprojection_pixels=3.,
                  translation_prior_meters=.02, rotation_prior_radians=.01,
                  training_indices=None, evaluation_indices=None):
    """Bounded experimental global training optimization; no heldout feedback.

    Explicit partitions must both be supplied; outside observations are rejected.
    Otherwise legacy stride-1 modulo stride frames are reserved BEFORE initialization and
    sampling. Camera 0 per epoch is fixed to retain AR's world gauge. Original
    AR metric priors constrain monocular scale; scale remains unverified.
    """
    if (not 1<=iterations<=20 or not 1<=maximum_tracks<=10000 or holdout_stride<3
        or not 0<maximum_reprojection_pixels<=3. or min(translation_prior_meters,rotation_prior_radians)<=0):
        raise ValueError('invalid_bundle_adjustment_limits')
    started=time.perf_counter()
    frames=document.get('keyframes',[])
    explicit = training_indices is not None or evaluation_indices is not None
    if explicit:
        if training_indices is None or evaluation_indices is None:
            raise ValueError('both_partitions_required')
        def partition(values):
            values = list(values)
            if any(type(i) is not int or not 0 <= i < len(frames) for i in values):
                raise ValueError('invalid_partition_index')
            if len(set(values)) != len(values):
                raise ValueError('duplicate_partition_index')
            return set(values)
        training = partition(training_indices)
        evaluation = partition(evaluation_indices)
        if not training or training & evaluation:
            raise ValueError('empty_training_or_overlapping_partitions')
    else:
        evaluation = {i for i in range(len(frames)) if i % holdout_stride == holdout_stride-1}
        training = set(range(len(frames))) - evaluation
    participating = training | evaluation
    seen_tracks = set()
    for track in feature_tracks:
        if track['id'] in seen_tracks:
            raise ValueError('duplicate_track_identity')
        seen_tracks.add(track['id'])
        seen_frames = set()
        for obs in track['observations']:
            index = obs['frameIndex']
            if type(index) is not int or not 0 <= index < len(frames):
                raise ValueError('invalid_observation_frame_index')
            if index not in participating:
                raise ValueError('observation_outside_declared_partition')
            if index in seen_frames:
                raise ValueError('duplicate_track_frame_observation')
            seen_frames.add(index)
    cameras={}
    invalid={}
    for index,frame in enumerate(frames):
        if index not in participating: continue
        try: cameras[index]=camera(frame)
        except (ValueError,KeyError,np.linalg.LinAlgError) as exc: invalid[index]=str(exc)
    heldout=evaluation & set(cameras)
    train_cameras=training & set(cameras)
    anchors={min(i for i in train_cameras if cameras[i].epoch==e) for e in {cameras[i].epoch for i in train_cameras}}
    diagnostics=[]
    eligible=[]
    for track in feature_tracks:
        obs=track['observations']
        train=[o for o in obs if o['frameIndex'] in train_cameras]
        test=[o for o in obs if o['frameIndex'] in heldout]
        entry={'trackID':track['id'],'trainingFrames':[o['frameIndex'] for o in train],
               'heldoutFrames':[o['frameIndex'] for o in test],
               'allObservationCount':len(obs),
               'invalidCameraObservationFrames':[o['frameIndex'] for o in obs if o['frameIndex'] in invalid]}
        diagnostics.append(entry)
        try:
            point,_,_,_,parallax=triangulate([cameras[o['frameIndex']] for o in train],
                [np.asarray(o['pixel'],dtype=float) for o in train],
                maximum_reprojection_pixels=float('inf'),minimum_parallax_degrees=1.)
            if len({cameras[o['frameIndex']].epoch for o in train})!=1:
                raise ValueError('different_epoch')
            eligible.append({'id':track['id'],'point':point,'initial':point.copy(),
                             'train':train,'test':test,'diagnostic':entry,'epoch':cameras[train[0]['frameIndex']].epoch})
            entry['initializationStatus']='eligible'
        except (ValueError,np.linalg.LinAlgError) as exc:
            entry['initializationStatus']='rejected'
            entry['reason']=str(exc)
    # Deterministic cap does not inspect holdout pixels/error or optimize selection.
    eligible.sort(key=lambda p:hashlib.sha256(p['id'].encode()).digest())
    selected=eligible[:maximum_tracks]
    for item in eligible[maximum_tracks:]: item['diagnostic']['optimizationStatus']='capacity_not_selected'
    rotations={i:np.eye(3) for i in cameras}
    shifts={i:np.zeros(3) for i in cameras}
    by_camera={i:[] for i in cameras if i not in heldout and i not in anchors}
    for item in selected:
        item['diagnostic']['optimizationStatus']='selected'
        for o in item['train']:
            if o['frameIndex'] in by_camera: by_camera[o['frameIndex']].append((item,o))
    prior=np.array([1/rotation_prior_radians**2]*3+[1/translation_prior_meters**2]*3)
    step_log=[]

    def objective(items=selected):
        value=0.
        for item in items:
            for o in item['train']:
                i=o['frameIndex']; pred=_prediction(cameras[i],rotations[i],shifts[i],item['point'])
                if pred is None: return float('inf')
                norm=np.linalg.norm(pred[0]-np.asarray(o['pixel']))
                value+=norm**2 if norm<=3 else 6*norm-9
        for i in by_camera:
            delta=np.r_[_log(rotations[i]),shifts[i]]
            value+=float(np.dot(prior,delta**2))
        return float(value)

    initial_objective=objective()
    for iteration in range(iterations):
        before=objective()
        old_points=[p['point'].copy() for p in selected]
        old_rot={i:r.copy() for i,r in rotations.items()}
        old_shift={i:s.copy() for i,s in shifts.items()}
        # Alternating block coordinate Gauss-Newton: each 3D landmark then 6D pose.
        for item in selected:
            h=np.eye(3)*1e-5; g=np.zeros(3)
            for o in item['train']:
                i=o['frameIndex']; pred=_prediction(cameras[i],rotations[i],shifts[i],item['point'])
                if pred is None: continue
                residual=pred[0]-np.asarray(o['pixel']); jac=pred[1]; weight=_huber(residual,3.)
                h+=weight*jac.T@jac; g+=weight*jac.T@residual
            try: step=np.linalg.solve(h,-g)
            except np.linalg.LinAlgError: continue
            length=np.linalg.norm(step)
            if length>.05: step*=.05/length
            item['point']+=step
        for i,observations in by_camera.items():
            if len(observations)<6: continue
            h=np.diag(prior)+np.eye(6)*1e-5
            g=prior*np.r_[_log(rotations[i]),shifts[i]]
            for item,o in observations:
                pred=_prediction(cameras[i],rotations[i],shifts[i],item['point'])
                if pred is None: continue
                residual=pred[0]-np.asarray(o['pixel']); jac=pred[2]; weight=_huber(residual,3.)
                h+=weight*jac.T@jac; g+=weight*jac.T@residual
            try: step=np.linalg.solve(h,-g)
            except np.linalg.LinAlgError: continue
            factor=min(1.,.005/max(np.linalg.norm(step[:3]),1e-12),.01/max(np.linalg.norm(step[3:]),1e-12))
            step*=factor
            rotations[i]=_exp(step[:3])@rotations[i]; shifts[i]+=step[3:]
        after=objective()
        accepted=math.isfinite(after) and after<before
        step_log.append({'iteration':iteration,'before':before,'after':after,'accepted':accepted})
        if not accepted:
            for p,old in zip(selected,old_points): p['point']=old
            rotations=old_rot; shifts=old_shift
            break
    finite_before=[]; finite_after=[]; passes_before=passes_after=attempts=nonprojectable_before=nonprojectable_after=0
    optimized_points=[]
    for item in selected:
        before=[]; after=[]
        for o in item['test']:
            i=o['frameIndex']; attempts+=1
            try:
                raw,_=project(cameras[i],item['initial']); error=float(np.linalg.norm(raw-np.asarray(o['pixel'])))
                before.append(error); finite_before.append(error); passes_before+=error<=maximum_reprojection_pixels
            except ValueError: before.append(None); nonprojectable_before+=1
            try:
                raw,_=project(cameras[i],item['point']); error=float(np.linalg.norm(raw-np.asarray(o['pixel'])))
                after.append(error); finite_after.append(error); passes_after+=error<=maximum_reprojection_pixels
            except ValueError: after.append(None); nonprojectable_after+=1
        training_errors=[]
        for o in item['train']:
            i=o['frameIndex']; pred=_prediction(cameras[i],rotations[i],shifts[i],item['point'])
            training_errors.append(float(np.linalg.norm(pred[0]-np.asarray(o['pixel']))) if pred else None)
        training_pass=all(e is not None and e<=maximum_reprojection_pixels for e in training_errors)
        validation_pass=bool(after) and all(e is not None and e<=maximum_reprojection_pixels for e in after)
        item['diagnostic'].update(initialHoldoutErrorsPixels=before,optimizedHoldoutErrorsPixels=after,
                                  trainingErrorsPixels=training_errors,trainingPassed=training_pass,holdoutPassed=validation_pass)
        optimized_points.append({'id':item['id'],'epoch':item['epoch'],'position':dict(zip('xyz',item['point'].tolist())),
            'originalPosition':dict(zip('xyz',item['initial'].tolist())),
            'trainingPassed':training_pass,'verifiedInThirdView':training_pass and validation_pass,
            'observations':[dict(o,usedForTriangulation=True) for o in item['train']]+[dict(o,usedForTriangulation=False) for o in item['test']]})
    output_cameras=[]
    for i,cam in cameras.items():
        w2c=cam.world_to_camera.copy()
        w2c[:3,:3]=rotations[i]@cam.world_to_camera[:3,:3]
        w2c[:3,3]=rotations[i]@cam.world_to_camera[:3,3]+shifts[i]
        c2w=np.linalg.inv(w2c)@_AXES
        output_cameras.append({'frameIndex':i,'epoch':cam.epoch,'heldout':i in heldout,'anchor':i in anchors,
            'originalCameraToWorld':cam.camera_to_world.ravel(order='F').tolist(),
            'cameraToWorld':c2w.ravel(order='F').tolist(),
            'centerShiftMeters':float(np.linalg.norm(c2w[:3,3]-cam.center)),
            'rotationShiftDegrees':math.degrees(float(np.linalg.norm(_log(rotations[i]))))})
    improved=(passes_after>passes_before and nonprojectable_after<=nonprojectable_before
              and bool(finite_before) and bool(finite_after)
              and np.percentile(finite_after,95)<=np.percentile(finite_before,95))
    return {'version':'global-holdout-alternating-ba-v1','status':'experiment_only','defaultEnabled':False,
        'validationImproved':bool(improved),'originalPreserved':True,'holdoutUsedForTraining':False,'scaleVerified':False,
        'partition':{'source':'explicit_indices' if explicit else 'legacy_holdout_stride',
                     'trainingIndices':sorted(training),'evaluationIndices':sorted(evaluation),
                     'nonparticipatingFrameIndices':sorted(set(range(len(frames)))-participating),
                     'observationPolicy':'reject_outside_partition_and_duplicate_track_frame',
                     'trackDiscoveryProvenance':'caller_supplied_not_independently_audited'},
        'invalidCameraFrames':invalid,'cameraFrames':output_cameras,'points':optimized_points,'trackDiagnostics':diagnostics,'stepDiagnostics':step_log,
        'summary':{'inputTrackCount':len(feature_tracks),'eligibleTrackCount':len(eligible),'selectedTrackCount':len(selected),
                   'capacityExcludedTrackCount':max(0,len(eligible)-len(selected)),
                   'initializationRejectedReasons':dict(Counter(d.get('reason') for d in diagnostics if d['initializationStatus']=='rejected')),
                   'potentialHeldoutObservationCount':sum(len(d['heldoutFrames']) for d in diagnostics),
                   'holdoutObservationCount':attempts,'holdoutEvaluationScope':'all selected initialized tracks; ineligible and capacity exclusions separately counted','initialHoldoutPassCount':int(passes_before),'optimizedHoldoutPassCount':int(passes_after),
                   'initialNonprojectableCount':nonprojectable_before,'optimizedNonprojectableCount':nonprojectable_after,
                   'initialFiniteP95Pixels':float(np.percentile(finite_before,95)) if finite_before else None,
                   'optimizedFiniteP95Pixels':float(np.percentile(finite_after,95)) if finite_after else None,
                   'initialTrainingObjective':initial_objective,'optimizedTrainingObjective':objective(),
                   'heldoutFrameIndices':sorted(heldout),'anchorFrameIndices':sorted(anchors)},
        'parameters':{'holdoutStride':holdout_stride,'iterations':iterations,'maximumTracks':maximum_tracks,
                      'maximumReprojectionPixels':maximum_reprojection_pixels,'translationPriorMeters':translation_prior_meters,
                      'rotationPriorRadians':rotation_prior_radians},
        'limitations':['Image-only track discovery used all frames, but heldout pixels never enter training optimization.',
                       'Raw AR heldout poses are a consistency reference, not independent true trajectory or scale.',
                       'Capacity exclusions and all initialization/validation failures remain reported.',
                       'No IMU factors, loop closure or dynamic/glass semantics are solved by this module.'],
        'elapsedMilliseconds':(time.perf_counter()-started)*1000}
