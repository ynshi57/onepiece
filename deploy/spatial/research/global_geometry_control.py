"""Conditional development P/PK control on an already jointly built SfM map.
Score landmarks never enter this optimization; original SfM used their images.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation


def groups(points, radius):
    parent = list(range(len(points)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for a, b in sorted(cKDTree(points).query_pairs(radius)):
        a, b = find(a), find(b)
        if a != b: parent[max(a,b)] = min(a,b)
    return np.array([find(i) for i in range(len(points))])



def pixel_groups(camera_ids, point_ids, xy, count, radius=8.):
    parent=list(range(count))
    def find(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]];i=parent[i]
        return i
    for c in np.unique(camera_ids):
        obs=np.flatnonzero(camera_ids==c)
        for a,b in sorted(cKDTree(xy[obs]).query_pairs(radius)):
            a,b=find(int(point_ids[obs[a]])),find(int(point_ids[obs[b]]))
            if a!=b:parent[max(a,b)]=min(a,b)
    return np.array([find(i) for i in range(count)])


def validate_partition(training, evaluation, count):
    for name,values in [('training',training),('evaluation',evaluation)]:
        if any(isinstance(x,bool) or not isinstance(x,int) or x<0 or x>=count for x in values):raise ValueError('invalid_'+name+'_index')
        if len(values)!=len(set(values)):raise ValueError('duplicate_'+name+'_index')
    if set(training)&set(evaluation):raise ValueError('training_evaluation_overlap')


def project(points, camera_ids, rotations, translations, intrinsics):
    xyz = np.einsum('nij,nj->ni', rotations[camera_ids], points) + translations[camera_ids]
    depth = xyz[:,2]
    safe = np.where(np.abs(depth)>1e-8, depth, 1e-8)
    k = intrinsics[camera_ids]
    return xyz[:,:2]/safe[:,None]*k[:,:2]+k[:,2:], depth


def shared_k(k, parameters):
    result = k.copy();result[:,:2] *= np.exp(parameters[:2]);result[:,2:] += parameters[2:]
    return result


def triangulate(xy, camera_ids, rotations, translations, k):
    rows=[]
    for pixel, c in zip(xy,camera_ids):
        q=(pixel-k[c,2:])/k[c,:2];p=np.column_stack((rotations[c],translations[c]))
        rows.extend([q[0]*p[2]-p[0],q[1]*p[2]-p[1]])
    a=np.array(rows);_,s,v=np.linalg.svd(a)
    h=v[-1]
    if len(s)<4 or abs(h[3])<1e-10 or s[-2]<1e-8: raise ValueError('degenerate_triangulation')
    x=h[:3]/h[3]
    if np.any(project(np.repeat(x[None],len(xy),axis=0),camera_ids,rotations,translations,k)[1]<=0): raise ValueError('behind_camera')
    return x


def fit_arm(points, camera_ids, point_ids, xy, rotations, translations, k, fit_ids, fixed, *, free_k, cpu_budget=180, max_evaluations=40):
    movable=np.array([i for i in range(len(k)) if i not in fixed]);fit_ids=np.array(fit_ids)
    ci={int(v):i for i,v in enumerate(movable)};pi={int(v):i for i,v in enumerate(fit_ids)}
    use=np.array([int(p) in pi for p in point_ids]);oc=camera_ids[use];op=point_ids[use];observed=xy[use]
    ncam=len(movable)*6;npoint=len(fit_ids)*3;n=ncam+npoint+(4 if free_k else 0)
    initial=np.concatenate([np.column_stack((Rotation.from_matrix(rotations[movable]).as_rotvec(),translations[movable])).ravel(),points[fit_ids].ravel(),np.zeros(4) if free_k else np.empty(0)])
    sparsity=lil_matrix((len(oc)*2,n),dtype=int)
    for j,(c,p) in enumerate(zip(oc,op)):
        if int(c) in ci:sparsity[2*j:2*j+2,ci[int(c)]*6:ci[int(c)]*6+6]=1
        index=ncam+pi[int(p)]*3;sparsity[2*j:2*j+2,index:index+3]=1
        if free_k:sparsity[2*j:2*j+2,-4:]=1
    start=time.process_time();wall_start=time.monotonic();calls=0;last=initial.copy()
    def decode(x):
        r=rotations.copy();t=translations.copy();p=points.copy();camera=x[:ncam].reshape(-1,6)
        r[movable]=Rotation.from_rotvec(camera[:,:3]).as_matrix();t[movable]=camera[:,3:];p[fit_ids]=x[ncam:ncam+npoint].reshape(-1,3)
        return p,r,t,shared_k(k,x[-4:]) if free_k else k.copy()
    def residual(x):
        nonlocal calls,last
        if time.process_time()-start>cpu_budget:raise TimeoutError('cpu_budget_exceeded')
        calls+=1;last=x.copy();p,r,t,kk=decode(x);pred,depth=project(p[op],oc,r,t,kk)
        delta=pred-observed;delta[depth<=0]+=10000
        return delta.ravel()
    lower=np.full(n,-np.inf);upper=np.full(n,np.inf)
    if free_k:lower[-4:]=[-np.log(1.05),-np.log(1.05),-10,-10];upper[-4:]=[np.log(1.05),np.log(1.05),10,10]
    def objective(x):
        pp,rr,tt,kk=decode(x);pred,depth=project(pp[op],oc,rr,tt,kk)
        delta=pred-observed;delta[depth<=0]+=10000;z=np.abs(delta.ravel())
        return float(.5*np.sum(np.where(z<=2,z*z,4*z-4)))
    initial_objective=objective(initial);optimality=None;reason=None
    try:
        result=least_squares(residual,initial,jac_sparsity=sparsity.tocsr(),loss='huber',f_scale=2.,x_scale='jac',max_nfev=max_evaluations,bounds=(lower,upper),tr_solver='lsmr')
        optimality=float(result.optimality);reason=str(result.message)
        solution=result.x;status='converged' if result.success else 'evaluation_budget_exhausted';nfev=result.nfev
    except TimeoutError:solution=initial;status='cpu_budget_exceeded_no_accepted_solution';nfev=None;reason='hard CPU budget exceeded; no accepted partial solution returned'
    p,r,t,kk=decode(solution)
    return p,r,t,kk,dict(status=status,accepted=status=='converged',initialFitObjective=initial_objective,finalFitObjective=objective(solution),solverOptimality=optimality,solverReason=reason,wallSeconds=time.monotonic()-wall_start,cpuSeconds=time.process_time()-start,residualCalls=calls,nfev=nfev,sharedKParameters=solution[-4:].tolist() if free_k else None)


def score(points, camera_ids, point_ids, xy, rotations, translations, k, score_ids):
    records=[];per_camera={};overlay=[]
    for p in score_ids:
        idx=np.flatnonzero(point_ids==p);order=np.argsort(camera_ids[idx]);idx=idx[order]
        train=idx[::2];held=idx[1::2];record=dict(pointIndex=int(p),observations=len(idx),triangulationObservations=len(train),scoringObservations=len(held))
        try:
            if len(train)<2 or len(held)<1:raise ValueError('insufficient_disjoint_observations')
            point=triangulate(xy[train],camera_ids[train],rotations,translations,k)
            pred,depth=project(np.repeat(point[None],len(held),axis=0),camera_ids[held],rotations,translations,k)
            errors=np.linalg.norm(pred-xy[held],axis=1)
            obs_records=[dict(observationIndex=int(j),camera=int(camera_ids[j]),status='measured' if z>0 else 'failed',reason=None if z>0 else 'scoring_behind_camera',errorPixels=float(e) if z>0 else None) for j,z,e in zip(held,depth,errors)]
            record.update(status='measured' if np.all(depth>0) else 'partially_failed',errorsPixels=errors[depth>0].tolist(),scoringObservationEvidence=obs_records)
            for j,err,q,z in zip(held,errors,pred,depth):
                if z<=0:continue
                per_camera.setdefault(int(camera_ids[j]),[]).append(float(err));overlay.append(dict(camera=int(camera_ids[j]),observed=xy[j].tolist(),predicted=q.tolist(),pointIndex=int(p)))
        except ValueError as e:record.update(status='failed',reason=str(e),scoringObservationEvidence=[dict(observationIndex=int(j),camera=int(camera_ids[j]),status='failed',reason=str(e),errorPixels=None) for j in held])
        records.append(record)
    return records,per_camera,overlay


def run(args):
    run_wall=time.monotonic();run_cpu=time.process_time()
    import pycolmap
    document=json.loads(Path(args.document).read_text());partition=json.loads(Path(args.partition).read_text());train=set(partition['trainingIndices']);excluded=set(partition['evaluationExcluded'])
    validate_partition(partition['trainingIndices'],partition['evaluationExcluded'],len(document['keyframes']))
    rec=pycolmap.Reconstruction(args.map);images=sorted(rec.images.values(),key=lambda x:x.name);indices=[int(Path(i.name).stem) for i in images]
    if set(indices)-train:raise ValueError('nontraining_camera_in_map')
    if len(indices)!=len(set(indices)):raise ValueError('duplicate_registered_frame')
    for i in images:
        c=rec.cameras[i.camera_id]
        if str(c.model).split('.')[-1]!='PINHOLE' or len(c.params)!=4 or not np.isfinite(c.params).all() or np.any(c.params[:2]<=0):raise ValueError('invalid_pinhole_camera')
        f=document['keyframes'][int(Path(i.name).stem)]
        if (c.width,c.height)!=(f['width'],f['height']):raise ValueError('map_native_image_dimension_mismatch')
    by_image={i.image_id:j for j,i in enumerate(images)};ids=sorted(rec.points3D);points=np.array([rec.points3D[p].xyz for p in ids]);camera_ids=[];point_ids=[];xy=[]
    matrices=np.array([i.cam_from_world().matrix() for i in images]);rotations=matrices[:,:,:3];translations=matrices[:,:,3];k=np.array([rec.cameras[i.camera_id].params for i in images])
    # COLMAP pixels use edge origin: its .5 principal-point convention is retained end to end.
    observation_identities=set()
    for j,p in enumerate(ids):
        for obs in rec.points3D[p].track.elements:
            point2d=rec.images[obs.image_id].points2D[obs.point2D_idx];camera=rec.cameras[rec.images[obs.image_id].camera_id]
            if point2d.point3D_id!=p:raise ValueError('track_point_identity_mismatch')
            if not (0<=point2d.xy[0]<camera.width and 0<=point2d.xy[1]<camera.height):raise ValueError('observation_outside_native_image')
            identity=(obs.image_id,obs.point2D_idx)
            if identity in observation_identities:raise ValueError('duplicate_image_keypoint_identity')
            observation_identities.add(identity)
            camera_ids.append(by_image[obs.image_id]);point_ids.append(j);xy.append(rec.images[obs.image_id].points2D[obs.point2D_idx].xy)
    camera_ids=np.array(camera_ids);point_ids=np.array(point_ids);xy=np.array(xy)
    if not np.isfinite(points).all() or not np.isfinite(xy).all() or not np.isfinite(matrices).all():raise ValueError('nonfinite_map')
    identities=list(zip(camera_ids.tolist(),point_ids.tolist()))
    if len(identities)!=len(set(identities)):raise ValueError('duplicate_track_camera_observation')
    radius=8.;group=pixel_groups(camera_ids,point_ids,xy,len(points),radius);score_ids=np.flatnonzero(group%5==0);fit_ids=np.flatnonzero(group%5!=0)
    centers=-np.einsum('nji,nj->ni',rotations,translations);a=0;b=int(np.argmax(np.linalg.norm(centers-centers[a],axis=1)));baseline=float(np.linalg.norm(centers[b]-centers[a]))
    if a==b or baseline<=1e-8:raise ValueError('degenerate_fixed_camera_baseline')
    fixed=[a,b]
    out=Path(args.output);out.mkdir(parents=True,exist_ok=False)
    result=dict(scope='conditional development: original SfM used all these images; not independent holdout',inputDocumentSHA256=hashlib.sha256(Path(args.document).read_bytes()).hexdigest(),partitionSHA256=hashlib.sha256(Path(args.partition).read_bytes()).hexdigest(),mapSHA256={x.name:hashlib.sha256(x.read_bytes()).hexdigest() for x in sorted(Path(args.map).iterdir()) if x.is_file()},solver=dict(loss='huber',fScalePixels=2.,maxEvaluations=args.max_evaluations,cpuBudgetSeconds=args.cpu_budget,sharedKBounds=[[-float(np.log(1.05)),-float(np.log(1.05)),-10,-10],[float(np.log(1.05)),float(np.log(1.05)),10,10]],jacobian='scipy sparse finite difference'),trainingIndices=sorted(train),evaluationExcluded=sorted(excluded),registeredIndices=indices,unregistered=[dict(frameIndex=i,status='unregistered_failure') for i in sorted(train-set(indices))],groupRadiusPixels=radius,grouping='union full landmark tracks with any same-training-image observations within8px',fixedBaselineMapUnits=baseline,groups=group.tolist(),fitPointIndices=fit_ids.tolist(),scorePointIndices=score_ids.tolist(),fixedCameraIndices=[indices[x] for x in fixed],scale='SfM gauge only, not physical ground truth',frozenObservations=[dict(observationIndex=j,camera=int(c),pointIndex=int(p),pixel=q.tolist()) for j,(c,p,q) in enumerate(zip(camera_ids,point_ids,xy))],arms={})
    for arm in ['raw','P','PK']:
        if arm=='raw':p,r,t,kk=points,rotations,translations,k;meta=dict(status='original_sfm')
        else:p,r,t,kk,meta=fit_arm(points,camera_ids,point_ids,xy,rotations,translations,k,fit_ids,fixed,free_k=arm=='PK',cpu_budget=args.cpu_budget,max_evaluations=args.max_evaluations)
        tracks,per,over=score(p,camera_ids,point_ids,xy,r,t,kk,score_ids)
        pred,_=project(p[point_ids],camera_ids,r,t,kk);errors=np.linalg.norm(pred-xy,axis=1)
        cameras=[dict(frameIndex=indices[c],fitErrorsPixels=errors[(camera_ids==c)&np.isin(point_ids,fit_ids)].tolist(),scoreErrorsPixels=per.get(c,[])) for c in range(len(indices))]
        fit_tracks=[dict(pointIndex=int(j),group=int(group[j]),status='fit_landmark',observations=int(np.sum(point_ids==j)),errorsPixels=errors[point_ids==j].tolist()) for j in fit_ids]
        result['arms'][arm]=dict(metadata=meta,tracks=tracks,fitTracks=fit_tracks,cameras=cameras,overlays=over,points=p.tolist(),rotations=r.tolist(),translations=t.tolist(),intrinsics=kk.tolist())
        (out/'result.json').write_text(json.dumps(result,indent=2))
    paired=[]
    evidence={arm:{o['observationIndex']:o for row in result['arms'][arm]['tracks'] for o in row['scoringObservationEvidence']} for arm in result['arms']}
    for identity in sorted(evidence['raw']):
        paired.append(dict(observationIndex=identity,arms={arm:rows[identity] for arm,rows in evidence.items()},allMeasured=all(rows[identity]['status']=='measured' for rows in evidence.values())))
    result['pairedScoreObservations']=paired
    (out/'result.json').write_text(json.dumps(result,indent=2))
    # Same observed pixels and score groups on real source photographs in all arms.
    from PIL import Image,ImageDraw
    counts={c:sum(x['camera']==c for x in result['arms']['raw']['overlays']) for c in range(len(indices))}
    for c in sorted(counts,key=counts.get,reverse=True)[:3]:
        f=document['keyframes'][indices[c]];native=Image.open(Path(args.document).parent/f['imageFile']).convert('RGB');panels=[]
        for arm in ['raw','P','PK']:
            image=native.copy();draw=ImageDraw.Draw(image)
            for row in result['arms'][arm]['overlays']:
                if row['camera']!=c:continue
                a=np.array(row['observed'])-.5;b=np.array(row['predicted'])-.5;draw.ellipse((a[0]-3,a[1]-3,a[0]+3,a[1]+3),outline='green');draw.line([tuple(a),tuple(b)],fill='red',width=2)
            image.thumbnail((640,480));ImageDraw.Draw(image).text((8,8),f'{"originalSfM" if arm=="raw" else arm} frame {indices[c]} conditional score',fill='red');ImageDraw.Draw(image).text((8,24),f'failed tracks {sum(x["status"]!="measured" for x in result["arms"][arm]["tracks"])}; solver {result["arms"][arm]["metadata"]["status"]}',fill='red');panels.append(image)
        merged=Image.new('RGB',(sum(x.width for x in panels),max(x.height for x in panels)),'white');x=0
        for image in panels:merged.paste(image,(x,0));x+=image.width
        merged.save(out/f'photo-control-{indices[c]}.png')
    result['totalWallSeconds']=time.monotonic()-run_wall;result['totalCPUSeconds']=time.process_time()-run_cpu
    result['sourceSHA256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (out/'result.json').write_text(json.dumps(result,indent=2))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--map',required=True);parser.add_argument('--document',required=True);parser.add_argument('--partition',required=True);parser.add_argument('--output',required=True);parser.add_argument('--cpu-budget',type=float,default=180);parser.add_argument('--max-evaluations',type=int,default=40)
    run(parser.parse_args())
