"""Same-target L2 SciPy / native Ceres numerical control, never production.
Original scalar-Huber results are a separate experiment. All map images already
entered original SfM, so scoring remains conditional development evidence.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix
from scipy.spatial.transform import Rotation

from global_geometry_control import project, score


def scipy_l2(points, ci, pi, xy, rotations, translations, k, fit_ids, fixed, *, max_evaluations=40, seconds=180):
    movable=np.array([i for i in range(len(k)) if i not in fixed]);fit_ids=np.array(fit_ids)
    cm={int(x):i for i,x in enumerate(movable)};pm={int(x):i for i,x in enumerate(fit_ids)}
    used=np.isin(pi,fit_ids);ci,pi,xy=ci[used],pi[used],xy[used];ncam=len(movable)*6
    initial=np.concatenate([np.column_stack((Rotation.from_matrix(rotations[movable]).as_rotvec(),translations[movable])).ravel(),points[fit_ids].ravel()])
    sparse=lil_matrix((len(ci)*2,len(initial)),dtype=int)
    for j,(c,p) in enumerate(zip(ci,pi)):
        if int(c) in cm:sparse[2*j:2*j+2,cm[int(c)]*6:cm[int(c)]*6+6]=1
        idx=ncam+pm[int(p)]*3;sparse[2*j:2*j+2,idx:idx+3]=1
    def decode(x):
        r=rotations.copy();t=translations.copy();p=points.copy();v=x[:ncam].reshape(-1,6)
        r[movable]=Rotation.from_rotvec(v[:,:3]).as_matrix();t[movable]=v[:,3:];p[fit_ids]=x[ncam:].reshape(-1,3)
        return p,r,t
    cpu=time.process_time();wall=time.monotonic();negative_calls=0;calls=0
    def residual(x):
        nonlocal negative_calls,calls
        if time.process_time()-cpu>seconds:raise TimeoutError('cpu_budget_exceeded')
        p,r,t=decode(x);pred,depth=project(p[pi],ci,r,t,k);negative_calls+=int(np.sum(depth<=0));calls+=1
        # Signed-depth reprojection, without the old SciPy negative-depth penalty.
        return (pred-xy).ravel()
    initial_residual=residual(initial);reason=None;optimality=None
    try:
        result=least_squares(residual,initial,jac_sparsity=sparse.tocsr(),loss='linear',x_scale='jac',tr_solver='lsmr',max_nfev=max_evaluations)
        x=result.x;reason=str(result.message);optimality=float(result.optimality);accepted=bool(result.success);nfev=result.nfev
    except TimeoutError:
        x=initial;reason='CPU timeout: no accepted partial result returned';accepted=False;nfev=None
    p,r,t=decode(x);pred,depth=project(p[pi],ci,r,t,k)
    return p,r,t,dict(accepted=accepted,solverReason=reason,optimality=optimality,nfev=nfev,residualCalls=calls,initialSSE=float(np.sum(initial_residual**2)),finalSSE=float(np.sum((pred-xy)**2)),finalBehindObservations=int(np.sum(depth<=0)),behindCountsAcrossResidualCalls=negative_calls,wallSeconds=time.monotonic()-wall,cpuSeconds=time.process_time()-cpu)


def run(args):
    import pycolmap
    start=time.monotonic();cpu=time.process_time();frozen=json.loads(Path(args.frozen).read_text())
    for name,expected in frozen['mapSHA256'].items():
        if hashlib.sha256((Path(args.map)/name).read_bytes()).hexdigest()!=expected:raise ValueError('frozen_map_changed')
    rec=pycolmap.Reconstruction(args.map);images=sorted(rec.images.values(),key=lambda x:x.name);indices=[int(Path(x.name).stem) for x in images]
    if indices!=frozen['registeredIndices']:raise ValueError('camera_identity_changed')
    point_ids=sorted(rec.points3D);p=np.array([rec.points3D[x].xyz for x in point_ids]);matrices=np.array([x.cam_from_world().matrix() for x in images]);r=matrices[:,:,:3];t=matrices[:,:,3];k=np.array([rec.cameras[x.camera_id].params for x in images])
    obs=frozen['frozenObservations'];ci=np.array([x['camera'] for x in obs]);pi=np.array([x['pointIndex'] for x in obs]);xy=np.array([x['pixel'] for x in obs]);fit=frozen['fitPointIndices'];held=frozen['scorePointIndices'];fixed=[indices.index(x) for x in frozen['fixedCameraIndices']];used=np.isin(pi,fit)
    out=Path(args.output);out.mkdir(parents=True,exist_ok=False)
    result=dict(scope='conditional development; numerical same-target L2 control, not production robustness or independent holdout',problem=dict(loss='linear L2',oldHuberUnchanged=True,cameraCount=len(indices),pointCount=len(p),fitPointCount=len(fit),fitObservationCount=int(np.sum(used)),fixedCameraIndices=frozen['fixedCameraIndices'],fixedBaselineMapUnits=frozen['fixedBaselineMapUnits'],intrinsicsFixed=True,scorePointIndices=held,unregistered=frozen['unregistered'],budgetNote='SciPy40 function evaluations and Ceres40 nonlinear iterations are distinct budgets',maxSeconds=args.seconds),frozenSHA256=hashlib.sha256(Path(args.frozen).read_bytes()).hexdigest(),arms={})
    raw_pred,raw_depth=project(p[pi[used]],ci[used],r,t,k);raw_sse=float(np.sum((raw_pred-xy[used])**2));result['initialSSE']=raw_sse;result['initialBehindObservations']=int(np.sum(raw_depth<=0))
    solutions={'originalSfM':(p,r,t,dict(status='original_sfm',initialSSE=raw_sse,finalSSE=raw_sse))}
    pp,rr,tt,meta=scipy_l2(p,ci,pi,xy,r,t,k,fit,fixed,max_evaluations=40,seconds=args.seconds);solutions['SciPy_L2']=(pp,rr,tt,meta)
    clone=pycolmap.Reconstruction(args.map)
    for j,pid in enumerate(point_ids):
        if j not in set(fit):clone.delete_point3D(pid)
    cfg=pycolmap.BundleAdjustmentConfig()
    for image in images:cfg.add_image(image.image_id);cfg.set_constant_cam_intrinsics(image.camera_id)
    for c in fixed:cfg.set_constant_rig_from_world_pose(images[c].frame_id)
    options=pycolmap.BundleAdjustmentOptions();options.loss_function_type=pycolmap.LossFunctionType.TRIVIAL
    options.refine_focal_length=False;options.refine_principal_point=False;options.refine_extra_params=False;options.refine_sensor_from_rig=False;options.print_summary=False
    options.solver_options.max_num_iterations=40;options.solver_options.max_solver_time_in_seconds=args.seconds;options.solver_options.num_threads=1
    options.solver_options.linear_solver_type=pycolmap.pyceres.LinearSolverType.SPARSE_SCHUR
    cw=time.monotonic();cc=time.process_time();adjuster=pycolmap.create_default_bundle_adjuster(options,cfg,clone);summary=adjuster.solve();wall=time.monotonic()-cw;spent=time.process_time()-cc
    pp=p.copy()
    for j in fit:pp[j]=clone.points3D[point_ids[j]].xyz
    transformed=np.array([clone.images[x.image_id].cam_from_world().matrix() for x in images]);rr=transformed[:,:,:3];tt=transformed[:,:,3]
    kk=np.array([clone.cameras[x.camera_id].params for x in images])
    if not np.array_equal(kk,k):raise ValueError('native_intrinsics_changed')
    if not np.allclose(transformed[fixed],matrices[fixed],atol=1e-12):raise ValueError('native_fixed_gauge_changed')
    pred,depth=project(pp[pi[used]],ci[used],rr,tt,k)
    native=dict(accepted=str(summary.termination_type).split('.')[-1]=='CONVERGENCE',termination=str(summary.termination_type),solverReason=summary.message,solutionUsable=summary.IsSolutionUsable(),initialSSE=2*summary.initial_cost,finalSSE=2*summary.final_cost,independentlyRecomputedFinalSSE=float(np.sum((pred-xy[used])**2)),finalBehindObservations=int(np.sum(depth<=0)),wallSeconds=wall,cpuSeconds=spent,successfulSteps=summary.num_successful_steps,unsuccessfulSteps=summary.num_unsuccessful_steps,linearSolver=str(summary.linear_solver_type_used),report=summary.FullReport())
    if not np.isclose(native['initialSSE'],raw_sse,rtol=1e-10):raise ValueError('native_initial_objective_mismatch')
    if not np.isclose(native['finalSSE'],native['independentlyRecomputedFinalSSE'],rtol=1e-9):raise ValueError('native_final_objective_mismatch')
    solutions['Ceres_L2']=(pp,rr,tt,native)
    for arm,(pp,rr,tt,meta) in solutions.items():
        tracks,cameras,overlay=score(pp,ci,pi,xy,rr,tt,k,held);fit_pred,fit_depth=project(pp[pi[used]],ci[used],rr,tt,k)
        result['arms'][arm]=dict(metadata=meta,scoreTracks=tracks,fitResiduals=(fit_pred-xy[used]).tolist(),scoreOverlays=overlay,perCamera=[dict(frameIndex=indices[c],scoreErrorsPixels=cameras.get(c,[])) for c in range(len(indices))],points=pp.tolist(),rotations=rr.tolist(),translations=tt.tolist())
    evidence={arm:{o['observationIndex']:o for track in v['scoreTracks'] for o in track['scoringObservationEvidence']} for arm,v in result['arms'].items()}
    result['pairedScoreObservations']=[dict(observationIndex=j,allMeasured=all(rows[j]['status']=='measured' for rows in evidence.values()),arms={arm:rows[j] for arm,rows in evidence.items()}) for j in sorted(evidence['originalSfM'])]
    result['sameTargetVerified']=result['initialBehindObservations']==0 and all(v['metadata'].get('finalBehindObservations',0)==0 for v in result['arms'].values())
    result['totalWallSeconds']=time.monotonic()-start;result['totalCPUSeconds']=time.process_time()-cpu;result['sourceSHA256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (out/'result.json').write_text(json.dumps(result,indent=2))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--map',required=True);parser.add_argument('--frozen',required=True);parser.add_argument('--output',required=True);parser.add_argument('--seconds',type=float,default=180);run(parser.parse_args())
