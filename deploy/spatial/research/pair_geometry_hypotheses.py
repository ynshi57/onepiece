"""Diagnostic bounded K-only vs pair-pose controls. Never writes corrected poses.

Uses G1's identical spatial fit/score correspondence partition. Pair-wise solutions
are not globally consistent trajectories. Bounds define a local falsification
experiment, not a calibrated camera or evidence of a physical root cause.
"""
import argparse
import json
from pathlib import Path
import sys
import time
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation
sys.path.insert(0,str(Path(__file__).resolve().parent))
from pair_geometry_control import camera, recorded_geometry, skew, sha, summary


def errors(f,a,b):
    x,y=np.c_[a,np.ones(len(a))],np.c_[b,np.ones(len(b))]
    fx,fy=x@f.T,y@f
    denominator=np.sqrt(fx[:,0]**2+fx[:,1]**2+fy[:,0]**2+fy[:,1]**2)
    if np.any(denominator < 1e-14):
        raise ValueError('undefined_sampson_derivative')
    return np.sum(y*fx,axis=1)/denominator


def modified_k(k,p):
    result=k.copy()
    result[0,0]*=np.exp(p[0]);result[1,1]*=np.exp(p[1])
    result[0,2]+=p[2];result[1,2]+=p[3]
    return result


def f_for(k1,k2,r,t):
    return np.linalg.inv(k2).T@skew(t)@r@np.linalg.inv(k1)


def run(document_path,control_path,output):
    start=time.monotonic();doc=json.loads(document_path.read_text());control=json.loads(control_path.read_text())
    if sha(document_path)!=control['inputDocumentSHA256']:
        raise ValueError('document_changed')
    expected=list(zip(control['trainingIndices'],control['trainingIndices'][1:]))
    if [(r['frameA'],r['frameB']) for r in control['records']] != expected:
        raise ValueError('control_pairs_do_not_match_frozen_order')
    if set(control['trainingIndices']) & set(control['evaluationExcluded']):
        raise ValueError('control_partition_overlap')
    pairs=[];records=[]
    boundary=len(expected)//2
    for index,row in enumerate(control['records']):
        # No camera/feature from the K-fit block may appear in the K-score block.
        role='fit' if index < boundary-1 else 'gap' if index==boundary-1 else 'score'
        record=dict(frameA=row['frameA'],frameB=row['frameB'],sharedKRole=role);records.append(record)
        if row['status']!='measured' or row.get('baselineARUnits',0)<1e-6 or row['fittingCount']<8 or row['scoringCount']<1:
            record['status']='unmeasurable_preserved';record['sourceStatus']=row['status'];continue
        ca,cb=[camera(doc['keyframes'][row[k]]) for k in ('frameA','frameB')]
        rel=cb.world_to_camera@np.linalg.inv(ca.world_to_camera)
        a,b=np.array(row['pointsA']),np.array(row['pointsB']);fit=np.array(row['fittingMask'],bool)
        if (a.ndim!=2 or a.shape[1]!=2 or b.shape!=a.shape or fit.shape!=(len(a),)
            or any(type(x) is not bool for x in row['fittingMask'])
            or not np.isfinite(a).all() or not np.isfinite(b).all()
            or int(fit.sum())!=row['fittingCount'] or int((~fit).sum())!=row['scoringCount']
            or ca.epoch!=cb.epoch or cb.time<=ca.time
            or np.any(a<0) or np.any(a>=[ca.width,ca.height])
            or np.any(b<0) or np.any(b>=[cb.width,cb.height])):
            record.update(status='invalid_control_input');continue
        pairs.append(dict(ca=ca,cb=cb,r=rel[:3,:3],t=rel[:3,3]/np.linalg.norm(rel[:3,3]),a=a,b=b,fit=fit,record=record))
    def k_residual(p):
        return np.concatenate([errors(f_for(modified_k(q['ca'].k,p),modified_k(q['cb'].k,p),q['r'],q['t']),q['a'][q['fit']],q['b'][q['fit']]) for q in pairs if q['record']['sharedKRole']=='fit'])
    if not pairs:raise ValueError('no_measurable_pairs')
    bounds=np.array([np.log(1.05),np.log(1.05),10.,10.])
    shared=None;shared_failure=None
    try:
        shared=least_squares(k_residual,np.zeros(4),bounds=(-bounds,bounds),loss='soft_l1',f_scale=3.,max_nfev=100)
    except ValueError as error:
        shared_failure=str(error)
    for q in pairs:
        t=q['t'];basis=np.eye(3)[np.argmin(np.abs(t))];u=np.cross(t,basis);u/=np.linalg.norm(u);v=np.cross(t,u)
        def pose_f(p):
            new_t=t+p[3]*u+p[4]*v;new_t/=np.linalg.norm(new_t)
            return f_for(q['ca'].k,q['cb'].k,Rotation.from_rotvec(p[:3]).as_matrix()@q['r'],new_t)
        def pose_residual(p):return errors(pose_f(p),q['a'][q['fit']],q['b'][q['fit']])
        limits=np.array([np.deg2rad(10)]*3+[1.,1.])
        masks=~q['fit'];record=q['record'];record.update(status='baselines_measured',scoringCount=int(masks.sum()),fittingCount=int(q['fit'].sum()))
        matrices=dict(raw=f_for(q['ca'].k,q['cb'].k,q['r'],t))
        if shared is not None:
            matrices['sharedK']=f_for(modified_k(q['ca'].k,shared.x),modified_k(q['cb'].k,shared.x),q['r'],t)
        for name,f in matrices.items():
            try:record[name]=summary(np.abs(errors(f,q['a'],q['b'])),masks)
            except ValueError as error:record[name]=dict(status='undefined',reason=str(error),requestedCount=int(masks.sum()))
        try:
            solved=least_squares(pose_residual,np.zeros(5),bounds=(-limits,limits),loss='soft_l1',f_scale=3.,max_nfev=100)
            record['localPose']=summary(np.abs(errors(pose_f(solved.x),q['a'],q['b'])),masks)
            record['status']='measured' if solved.success else 'local_solver_not_converged'
            record.update(localPoseParameters=solved.x.tolist(),localPoseSolverSuccess=bool(solved.success),
                localPoseEvaluations=solved.nfev,localPoseAtBounds=bool(np.any(np.abs(solved.x)>limits*.99)),
                localPoseF=pose_f(solved.x).tolist(),localFitJacobianSingularValues=np.linalg.svd(solved.jac,compute_uv=False).tolist())
        except ValueError as error:
            q['record'].update(status='solver_failed',reason=str(error))
    result=dict(method='bounded_K_vs_pair_pose_diagnostic_v1',inputDocumentSHA256=sha(document_path),controlSHA256=sha(control_path),
        sharedKParameters=shared.x.tolist() if shared is not None else None,sharedKParameterOrder=['log_fx_multiplier','log_fy_multiplier','cx_delta_pixels','cy_delta_pixels'],
        sharedKBounds=bounds.tolist(),sharedKSolverSuccess=bool(shared.success) if shared is not None else False,
        sharedKAtBounds=bool(np.any(np.abs(shared.x)>bounds*.99)) if shared is not None else None,
        sharedKFailure=shared_failure,sharedKPartition='first_temporal_block_fit_one_pair_gap_second_block_score_no_shared_camera',
        fittingWeight='equal_per_match_not_equal_per_pair',
        localPoseBounds='rotation_vector_each_component_plus_minus_10_degrees_translation_tangent_each_plus_minus_1',
        requestedPairCount=len(control['records']),records=records,elapsedSeconds=time.monotonic()-start,
        productAccepted=False,posesModified=False,
        warning='Pairwise fitted poses are not a consistent SLAM map; local bounds and correlated matches limit attribution. Unmeasurable pairs retained. Neither relative translation scale nor metric accuracy is measured.')
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:result[k] for k in ('requestedPairCount','sharedKParameters','sharedKAtBounds','elapsedSeconds')}))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--document',required=True,type=Path);p.add_argument('--control',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();run(a.document,a.control,a.output)
