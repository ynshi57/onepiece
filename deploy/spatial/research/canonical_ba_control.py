"""Shared canonical PINHOLE control; conditional development, not calibration.
Canonical residuals reweight native axes. SOFT_L1 is a 2D-block Ceres loss,
not the earlier scalar-Huber objective. Native files are never changed.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
from global_geometry_control import project,score


def canonical_pixels(xy,k,reference):
    return (np.asarray(xy)-k[2:])/k[:2]*reference[:2]+reference[2:]


def native_pixels(xy,k,reference):
    return (np.asarray(xy)-reference[2:])/reference[:2]*k[:2]+k[2:]


def adjusted_native_k(native,reference,shared):
    result=native.copy();result[:,:2]*=shared[:2]/reference[:2]
    result[:,2:]+=native[:,:2]*(shared[2:]-reference[2:])/reference[:2]
    return result


def within_candidate_bounds(reference,shared):
    ratio=shared[:2]/reference[:2]
    return bool(np.all(np.isfinite(shared)) and np.all(ratio>=1/1.05) and np.all(ratio<=1.05) and np.all(np.abs(shared[2:]-reference[2:])<=10))


def clone_canonical(original,images,points,fit_ids,reference):
    import pycolmap
    result=pycolmap.Reconstruction();first=original.cameras[images[0].camera_id]
    result.add_camera(pycolmap.Camera(camera_id=1,model='PINHOLE',width=first.width,height=first.height,params=reference))
    sensor=pycolmap.sensor_t(type=pycolmap.SensorType.CAMERA,id=1)
    rig=pycolmap.Rig(rig_id=1);rig.add_ref_sensor(sensor);result.add_rig(rig)
    for image in images:
        frame=pycolmap.Frame(frame_id=image.frame_id,rig_id=1,rig_from_world=image.cam_from_world())
        frame.add_data_id(pycolmap.data_t(sensor_id=sensor,id=image.image_id));result.add_frame(frame)
        k=original.cameras[image.camera_id].params
        keypoints=canonical_pixels(np.array([p.xy for p in image.points2D]),k,reference)
        new=pycolmap.Image(name=image.name,keypoints=keypoints,camera_id=1,image_id=image.image_id);new.frame_id=image.frame_id;result.add_image(new)
    point_mapping={}
    for j in fit_ids:
        original_point=original.points3D[points[j]]
        point_mapping[j]=result.add_point3D(original_point.xyz,pycolmap.Track(original_point.track.elements),original_point.color)
    for image in images:
        if not np.allclose(result.images[image.image_id].cam_from_world().matrix(),image.cam_from_world().matrix(),atol=1e-12):raise ValueError('canonical_frame_pose_changed')
    return result,point_mapping


def block_soft_l1_cost(delta,scale=2):
    return float(np.sum(scale**2*(np.sqrt(1+np.sum(delta**2,axis=1)/scale**2)-1)))


def run(args):
    import pycolmap
    wall=time.monotonic();cpu=time.process_time();frozen=json.loads(Path(args.frozen).read_text())
    for name,sha in frozen['mapSHA256'].items():
        if hashlib.sha256((Path(args.map)/name).read_bytes()).hexdigest()!=sha:raise ValueError('frozen_map_changed')
    original=pycolmap.Reconstruction(args.map);images=sorted(original.images.values(),key=lambda x:x.name);indices=[int(Path(x.name).stem) for x in images]
    if indices!=frozen['registeredIndices']:raise ValueError('camera_identity_changed')
    ids=sorted(original.points3D);points=np.array([original.points3D[p].xyz for p in ids]);matrix=np.array([i.cam_from_world().matrix() for i in images]);r=matrix[:,:,:3];t=matrix[:,:,3];k=np.array([original.cameras[i.camera_id].params for i in images]);reference=np.median(k,axis=0)
    obs=frozen['frozenObservations'];ci=np.array([x['camera'] for x in obs]);pi=np.array([x['pointIndex'] for x in obs]);xy=np.array([x['pixel'] for x in obs]);fit=frozen['fitPointIndices'];held=frozen['scorePointIndices'];fixed=[indices.index(i) for i in frozen['fixedCameraIndices']];used=np.isin(pi,fit)
    canonical=np.array([canonical_pixels(q,k[c],reference) for q,c in zip(xy,ci)]);roundtrip=np.array([native_pixels(q,k[c],reference) for q,c in zip(canonical,ci)])
    error=float(np.max(np.abs(roundtrip-xy)))
    if error>1e-9:raise ValueError('canonical_roundtrip_failed')
    before,depth=project(points[pi],ci,r,t,k);refk=np.repeat(reference[None],len(k),axis=0);before_canonical,_=project(points[pi],ci,r,t,refk)
    if not np.allclose(before_canonical,np.array([canonical_pixels(q,k[c],reference) for q,c in zip(before,ci)]),atol=1e-9):raise ValueError('canonical_projection_mapping_failed')
    result=dict(scope='conditional development shared canonical K; not independent holdout or production calibration',parameterization='native focal_i *= shared_focal/reference_focal; principal_i += native_focal_i*(shared_principal-reference_principal)/reference_focal',weighting='canonical axes scale native residuals by reference_focal/native_focal_i; differs from native-pixel constant principal offsets',loss='Ceres SOFT_L1 2px canonical 2D residual block',referenceK=reference.tolist(),roundtripMaximumPixels=error,initialBehindObservations=int(np.sum(depth[used]<=0)),fixedCameraIndices=frozen['fixedCameraIndices'],fixedBaselineMapUnits=frozen['fixedBaselineMapUnits'],fitPoints=len(fit),fitObservations=int(np.sum(used)),unregistered=frozen['unregistered'],candidateBounds=dict(focalRatio=[1/1.05,1.05],canonicalPrincipalOffsetPixels=[-10,10],solverBoundsAvailable=False),arms={})
    out=Path(args.output);out.mkdir(parents=True,exist_ok=False)
    rawtracks,rawcams,rawover=score(points,ci,pi,xy,r,t,k,held)
    result['arms']['originalSfM']=dict(metadata=dict(status='original_sfm'),scoreTracks=rawtracks,perCamera=[dict(frameIndex=indices[c],errorsPixels=rawcams.get(c,[])) for c in range(len(images))],overlays=rawover)
    for arm in ['P_fixedCanonicalK','PK_sharedCanonicalK']:
        clone,mapping=clone_canonical(original,images,ids,fit,reference);cfg=pycolmap.BundleAdjustmentConfig()
        for image in images:cfg.add_image(image.image_id)
        for c in fixed:cfg.set_constant_rig_from_world_pose(images[c].frame_id)
        if arm.startswith('P_'):cfg.set_constant_cam_intrinsics(1)
        options=pycolmap.BundleAdjustmentOptions();options.loss_function_type=pycolmap.LossFunctionType.SOFT_L1;options.loss_function_scale=2
        options.refine_focal_length=arm.startswith('PK_');options.refine_principal_point=arm.startswith('PK_');options.refine_extra_params=False;options.refine_sensor_from_rig=False;options.print_summary=False
        options.solver_options.max_num_iterations=40;options.solver_options.max_solver_time_in_seconds=180;options.solver_options.num_threads=1;options.solver_options.linear_solver_type=pycolmap.pyceres.LinearSolverType.SPARSE_SCHUR
        start=time.monotonic();cc=time.process_time();summary=pycolmap.create_default_bundle_adjuster(options,cfg,clone).solve();seconds=time.monotonic()-start;spent=time.process_time()-cc
        shared=clone.cameras[1].params.copy();newk=adjusted_native_k(k,reference,shared);updated=points.copy()
        for j,new_id in mapping.items():updated[j]=clone.points3D[new_id].xyz
        transforms=np.array([clone.images[i.image_id].cam_from_world().matrix() for i in images]);rr=transforms[:,:,:3];tt=transforms[:,:,3]
        if not np.allclose(transforms[fixed],matrix[fixed],atol=1e-12):raise ValueError('fixed_gauge_changed')
        if arm.startswith('P_') and not np.array_equal(shared,reference):raise ValueError('fixed_shared_k_changed')
        pred,zz=project(updated[pi[used]],ci[used],rr,tt,np.repeat(shared[None],len(k),axis=0));initial_cost=block_soft_l1_cost(before_canonical[used]-canonical[used]);final_cost=block_soft_l1_cost(pred-canonical[used])
        if not np.isclose(initial_cost,summary.initial_cost,rtol=1e-10) or not np.isclose(final_cost,summary.final_cost,rtol=1e-9):raise ValueError('native_objective_mismatch')
        converged=str(summary.termination_type).split('.')[-1]=='CONVERGENCE';bounded=within_candidate_bounds(reference,shared);behind=int(np.sum(zz<=0));accepted=converged and bounded and behind==0
        tracks,per,over=score(updated,ci,pi,xy,rr,tt,newk,held)
        result['arms'][arm]=dict(metadata=dict(accepted=accepted,converged=converged,candidateWithinBounds=bounded,rejectionReasons=(["unconverged"] if not converged else [])+(["shared_k_out_of_bounds"] if not bounded else [])+(["behind_camera"] if behind else []),termination=str(summary.termination_type),message=summary.message,sharedK=shared.tolist(),focalRatio=(shared[:2]/reference[:2]).tolist(),normalizedPrincipalOffset=((shared[2:]-reference[2:])/reference[:2]).tolist(),canonicalPrincipalOffset=(shared[2:]-reference[2:]).tolist(),initialCost=initial_cost,finalCost=final_cost,behindObservations=behind,wallSeconds=seconds,cpuSeconds=spent,report=summary.FullReport()),scoreTracks=tracks,perCamera=[dict(frameIndex=indices[c],errorsPixels=per.get(c,[])) for c in range(len(images))],overlays=over,points=updated.tolist(),rotations=rr.tolist(),translations=tt.tolist(),nativeK=newk.tolist(),fitResidualsCanonical=(pred-canonical[used]).tolist())
        (out/'result.json').write_text(json.dumps(result,indent=2))
    evidence={arm:{o['observationIndex']:o for track in v['scoreTracks'] for o in track['scoringObservationEvidence']} for arm,v in result['arms'].items()}
    result['pairedScoreObservations']=[dict(observationIndex=j,allMeasured=all(v[j]['status']=='measured' for v in evidence.values()),arms={a:v[j] for a,v in evidence.items()}) for j in sorted(evidence['originalSfM'])]
    result['totalWallSeconds']=time.monotonic()-wall;result['totalCPUSeconds']=time.process_time()-cpu;result['sourceSHA256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest();result['frozenSHA256']=hashlib.sha256(Path(args.frozen).read_bytes()).hexdigest()
    (out/'result.json').write_text(json.dumps(result,indent=2));return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--map',required=True);parser.add_argument('--frozen',required=True);parser.add_argument('--output',required=True);run(parser.parse_args())
