"""Local vertical plane fitted to training pixel tracks, no unseen boundary completion."""
from __future__ import annotations
import math
import hashlib
import json
import numpy as np
from .spatial_reconstruction import Camera

class WallBundleFailure(ValueError):
    """Failed fit with retained geometry/conditioning evidence for callers."""
    def __init__(self, reason, evidence):
        super().__init__(reason)
        self.evidence = {"reason": reason, **evidence, "productAccepted": False}


def fit_vertical_wall_pixels(initial_normal, initial_offset, tracks, cameras: dict[int, Camera], *,
                             component_id, training_indices, evaluation_indices):
    """Fit theta/d with calibrated frozen cameras and complete pixel observations.

    Each track is intersected with the plane along a fixed training source ray.
    Sources are chosen by frame order, not fit quality. No evaluation image input.
    Tracks: [{id, observations:[{frameIndex,pixel}]}]. Units come from camera map.
    """
    training_indices=list(training_indices);evaluation_indices=list(evaluation_indices)
    if (len(set(training_indices))!=len(training_indices) or
            len(set(evaluation_indices))!=len(evaluation_indices)):
        raise ValueError('repeated_partition_indices')
    if set(training_indices)&set(evaluation_indices):
        raise ValueError('training_evaluation_partition_overlap')
    training=set(training_indices)
    if not training or not set(cameras).issubset(training):
        raise ValueError('camera_outside_training_partition')
    frozen={"trainingIndices": sorted(training_indices), "evaluationIndices": sorted(evaluation_indices),
            "initialNormal": np.asarray(initial_normal,float).tolist(), "initialOffset": float(initial_offset),
            "tracks": tracks, "cameras": [{"frameIndex": i, "intrinsics": c.k.tolist(),
                "worldToCamera": c.world_to_camera.tolist(), "epoch": c.epoch}
                for i,c in sorted(cameras.items())]}
    digest=hashlib.sha256(json.dumps(frozen,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    evidence={"trainingIndices": sorted(training_indices), "evaluationIndices": sorted(evaluation_indices),
              "frozenInputSHA256": digest, "evaluationRGBUsed": False,
              "componentId": component_id, "jacobianSingularValues": None,
              "jacobianColumnNorms": None, "jacobianSingularRatio": None,
              "parameterUnits": ["theta_radians", "offset_camera_map_units"]}
    normal=np.asarray(initial_normal,float)
    if normal.shape!=(3,) or not np.isfinite(normal).all() or abs(normal[1])>1e-6 or np.linalg.norm(normal[[0,2]])<1e-8 or not math.isfinite(initial_offset):
        raise ValueError('invalid_vertical_plane_initialization')
    length=np.linalg.norm(normal);normal/=length;offset=float(initial_offset)/length
    if len(tracks)<3 or len({t['id'] for t in tracks})!=len(tracks):raise ValueError('insufficient_or_repeated_tracks')
    prepared=[];epochs=set()
    for track in tracks:
        obs=sorted(track['observations'],key=lambda o:o['frameIndex'])
        if len(obs)<3 or len({o['frameIndex'] for o in obs})!=len(obs):raise ValueError('three_distinct_training_views_required')
        for o in obs:
            if o['frameIndex'] not in training:raise ValueError('observation_outside_training_partition')
            if o['frameIndex'] not in cameras:raise ValueError('observation_outside_frozen_component')
            cam=cameras[o['frameIndex']];pixel=np.asarray(o['pixel'],float)
            if pixel.shape!=(2,) or not np.isfinite(pixel).all() or not(0<=pixel[0]<cam.width and 0<=pixel[1]<cam.height):raise ValueError('invalid_native_pixel_observation')
            epochs.add(cam.epoch)
        source=obs[0];cam=cameras[source['frameIndex']]
        ray=cam.world_to_camera[:3,:3].T@np.linalg.solve(cam.k,np.r_[source['pixel'],1.])
        prepared.append((track['id'],cam.center,ray,obs))
    if len(epochs)!=1:raise ValueError('mixed_map_epochs')
    used=sorted({o['frameIndex'] for t in tracks for o in t['observations']})
    baselines=[float(np.linalg.norm(cameras[a].center-cameras[b].center))
               for j,a in enumerate(used) for b in used[j+1:]]
    angles=[]
    for identity,_,_,obs in prepared:
        rays=[]
        for o in obs:
            cam=cameras[o['frameIndex']]
            ray=cam.world_to_camera[:3,:3].T@np.linalg.solve(cam.k,np.r_[o['pixel'],1.])
            rays.append(ray/np.linalg.norm(ray))
        pair_angles=[math.degrees(math.acos(float(np.clip(a@b,-1.,1.))))
                     for j,a in enumerate(rays) for b in rays[j+1:]]
        angles.append({"trackId": identity, "pairRayAnglesDegrees": pair_angles,
                       "maximumRayAngleDegrees": max(pair_angles)})
    evidence.update(cameraPairBaselines=baselines,minimumCameraBaseline=min(baselines),
                    maximumCameraBaseline=max(baselines),trackRayAngles=angles,
                    baselineScaleSource='caller_camera_map_not_independently_metric_verified')
    def fail(reason):
        raise WallBundleFailure(reason,evidence)
    if max(baselines)<=1e-8:fail('zero_translation_baseline')
    def evaluate(parameters, records=False):
        theta,d=parameters;n=np.array([math.cos(theta),0,math.sin(theta)]);errors=[];output=[]
        for identity,center,ray,obs in prepared:
            denominator=float(n@ray)
            if abs(denominator)<1e-6:fail('plane_ray_grazing')
            distance=-(float(n@center)+d)/denominator
            if distance<=0:fail('plane_behind_source')
            xyz=center+distance*ray
            for o in obs:
                cam=cameras[o['frameIndex']];local=cam.world_to_camera[:3,:3]@xyz+cam.world_to_camera[:3,3]
                if local[2]<=1e-8:fail('plane_point_behind_training_camera')
                q=cam.k@local;pixel=q[:2]/q[2];residual=pixel-np.asarray(o['pixel'])
                errors.extend(residual.tolist())
                if records:output.append(dict(trackId=identity,frameIndex=o['frameIndex'],observedPixel=o['pixel'],predictedPixel=pixel.tolist(),isAnchor=o['frameIndex']==obs[0]['frameIndex'],residualPixels=float(np.linalg.norm(residual)),planePosition=xyz.tolist()))
        return np.asarray(errors),output
    parameters=np.array([math.atan2(normal[2],normal[0]),offset]);initial_errors,_=evaluate(parameters)
    objective=lambda e:float(np.sum(np.where(np.abs(e)<=3,.5*e*e,3*(np.abs(e)-1.5))))
    for iteration in range(30):
        errors,_=evaluate(parameters);j=np.column_stack([(evaluate(parameters+np.eye(2)[a]*1e-6)[0]-errors)/1e-6 for a in (0,1)])
        singular=np.linalg.svd(j,compute_uv=False)
        evidence['jacobianSingularValues']=singular.tolist()
        evidence['jacobianColumnNorms']=np.linalg.norm(j,axis=0).tolist()
        evidence['jacobianSingularRatio']=float(singular[-1]/max(singular[0],1e-12))
        if singular[-1]/max(singular[0],1e-12)<1e-5:fail('unobservable_wall_parameters')
        w=np.minimum(1.,3./np.maximum(np.abs(errors),1e-12));step=np.linalg.lstsq(j*np.sqrt(w[:,None]),-errors*np.sqrt(w),rcond=None)[0]
        if np.linalg.norm(step)<1e-8:break
        moved=False
        for factor in (1.,.5,.25,.125,.0625,.03125):
            trial=parameters+factor*step
            try:te,_=evaluate(trial)
            except ValueError:continue
            if objective(te)<=objective(errors):parameters=trial;moved=True;break
        if not moved:break
    errors,records=evaluate(parameters,True);theta,d=parameters;n=np.array([math.cos(theta),0,math.sin(theta)])
    final_j=np.column_stack([(evaluate(parameters+np.eye(2)[a]*1e-6)[0]-errors)/1e-6 for a in (0,1)])
    final_singular=np.linalg.svd(final_j,compute_uv=False)
    column_norms=np.linalg.norm(final_j,axis=0)
    normalized_singular=np.linalg.svd(final_j/np.maximum(column_norms,1e-12),compute_uv=False)
    evidence.update(finalJacobianSingularValues=final_singular.tolist(),
                    finalJacobianColumnNorms=column_norms.tolist(),
                    finalJacobianSingularRatio=float(final_singular[-1]/max(final_singular[0],1e-12)),
                    finalColumnNormalizedJacobianSingularValues=normalized_singular.tolist())
    if evidence['finalJacobianSingularRatio']<1e-5:fail('unobservable_final_wall_parameters')
    non_anchor=[r['residualPixels'] for r in records if not r['isAnchor']]
    return dict(**evidence,nonAnchorObservationCount=len(non_anchor),
                nonAnchorMedianResidualPixels=float(np.median(non_anchor)),
                nonAnchorP95ResidualPixels=float(np.percentile(non_anchor,95)),
                conditioningVerifiedOnlyForFrozenTrainingCameras=True,
                independentWallAccuracyVerified=False,normal=n.tolist(),offset=float(d),source='frozen_component_training_pixel_plane_fit',allObservations=records,initialRobustPixelObjective=objective(initial_errors),finalRobustPixelObjective=objective(errors),observationCount=len(records),trackCount=len(tracks),medianResidualPixels=float(np.median([r['residualPixels'] for r in records])),p95ResidualPixels=float(np.percentile([r['residualPixels'] for r in records],95)),iterations=iteration+1,completeSurface=False,metricAccuracyVerified=False,productAccepted=False)
