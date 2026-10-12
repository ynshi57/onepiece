"""Research-only SP map alignment and frozen-map developer-query localization.

Sensor consistency is not metric truth. SuperPoint is not licensed for product
redistribution. All dev-query failures remain; no query refines the training map.
"""
from __future__ import annotations
import argparse,collections,copy,json,sys,time
from pathlib import Path
import numpy as np
from learned_feature_pairs import TimedWorker,digest,verify_resources
from resect_training_views import landmark_groups,classify_candidate

AX=np.diag([1.,-1.,-1.])

def angle(a,b):return float(np.degrees(np.arccos(np.clip((np.trace(a.T@b)-1)/2,-1,1))))
def align_sensor(centers,rotations,raw,maximum_angle=5.,maximum_center=.15):
    """Frozen research cutoffs, raw sensor agreement only; rejects degenerate scale."""
    a=np.asarray(centers);raw=np.asarray(raw);b=raw[:,:3,3]
    deltas=np.asarray([t[:3,:3]@s.T for s,t in zip(rotations,raw)])
    distances=np.array([[angle(x,y) for y in deltas] for x in deltas]);medoid=int(np.argmin(np.median(distances,axis=1)));keep=distances[medoid]<=maximum_angle
    if keep.sum()<3:raise ValueError('insufficient_orientation_consensus')
    u,_,v=np.linalg.svd(deltas[keep].sum(0));R=u@np.diag([1,1,np.linalg.det(u@v)])@v
    def fit(selected):
        aa=a[selected];bb=b[selected];ac=aa-aa.mean(0);bc=bb-bb.mean(0);energy=float(np.sum(ac*ac))
        if energy<1e-8:raise ValueError('degenerate_center_baseline')
        scale=float(np.sum((ac@R.T)*bc)/energy)
        if not np.isfinite(scale) or scale<=0:raise ValueError('invalid_positive_scale')
        return scale,bb.mean(0)-scale*R@aa.mean(0)
    scale,t=fit(keep);errors=np.linalg.norm(scale*a@R.T+t-b,axis=1);keep &= errors<=maximum_center
    if keep.sum()<3:raise ValueError('insufficient_center_consensus')
    scale,t=fit(keep);errors=np.linalg.norm(scale*a@R.T+t-b,axis=1);angles=np.asarray([angle(R@s,r[:3,:3]) for s,r in zip(rotations,raw)]);keep &= (angles<=maximum_angle)&(errors<=maximum_center)
    return R,scale,t,keep,angles,errors

def associate(votes,groups):
    matches=[];ambiguous=[]
    for qi,items in sorted(votes.items()):
        if len({groups[pid] for pid,_ in items})!=1:
            ambiguous.append(dict(query=qi,reason='conflicting_training_landmark_identity',votes=items));continue
        pid=collections.Counter(pid for pid,_ in items).most_common(1)[0][0]
        matches.append(dict(query=qi,pointId=pid,group=groups[pid],supportingTrainFrames=sorted({f for _,f in items})))
    return matches,ambiguous

def run(args):
    import pycolmap
    source,data,out=map(Path,(args.source,args.data,args.output));out.mkdir(parents=True,exist_ok=False)
    inputs=json.loads((source/'inputs.json').read_text());train=inputs['trainingIndices'];queries=inputs['evaluationExcluded']
    if inputs['featureIdentity']!='SuperPoint' or set(train)&set(queries):raise ValueError('invalid_feature_identity_or_partition')
    document=json.loads((data/'document.json').read_text());docsha=digest(data/'document.json');provenance=verify_resources(args.resources)
    extraction_manifest=json.loads((source.parent/'result.json').read_text())
    if extraction_manifest['provenance']!=provenance or extraction_manifest['inputDocumentSHA256']!=docsha or extraction_manifest['featureWidth']!=640 or extraction_manifest['maxKeypoints']!=1024:
        raise ValueError('training_query_extractor_provenance_mismatch')
    feature_receipts={str(idx):digest(source/f'features-{idx}.npz') for idx in train}
    rec=pycolmap.Reconstruction(str(source/'models'/'0'));receipts={p.name:digest(p) for p in (source/'models'/'0').iterdir() if p.is_file()};indices=[];centers=[];rotations=[];raw=[];views=[]
    for iid in sorted(rec.reg_image_ids()):
        im=rec.images[iid];idx=int(Path(im.name).stem)
        if idx not in train or idx in queries:raise ValueError('map_contains_query_or_nontraining_frame')
        pose=im.cam_from_world();r=pose.rotation.matrix();centers.append(-r.T@pose.translation);rotations.append(r.T@AX);indices.append(idx);raw.append(np.asarray(document['keyframes'][idx]['cameraToWorld']).reshape(4,4,order='F'))
    R,scale,t,keep,angles,errors=align_sensor(centers,rotations,raw);retained={idx for idx,k in zip(indices,keep) if k};revised=copy.deepcopy(document);rows=[]
    for idx in train:
        if idx not in indices:reason='unregistered_training_map';ok=False
        else:
            j=indices.index(idx);ok=bool(keep[j]);reason='retained_sensor_consistency_candidate' if ok else 'orientation_prior_conflict' if angles[j]>5 else 'center_prior_conflict'
        row=dict(frameIndex=idx,retained=ok,reason=reason)
        if idx in indices:row.update(orientationPriorDifferenceDegrees=float(angles[j]),centerPriorDifferenceMapUnits=float(errors[j]))
        rows.append(row)
        if ok:
            c=np.eye(4);c[:3,:3]=R@rotations[j];c[:3,3]=scale*R@centers[j]+t;f=revised['keyframes'][idx];f['cameraToWorld']=c.ravel(order='F').tolist();f['pose']['position']=dict(zip('xyz',c[:3,3].tolist()));f['pose']['forward']=dict(zip('xyz',(-c[:3,2]).tolist()));f['experimentalPoseSource']=dict(type='training_only_SP_LG_SfM_sensor_aligned',component=0,scaleNotGroundTruth=True)
        else:revised['keyframes'][idx]['experimentalPoseSource']=dict(type='raw_AR_unregistered_or_rejected',geometryVerified=False,reason=reason)
    assert all(revised['keyframes'][i]==document['keyframes'][i] for i in queries)
    (out/'document.json').write_text(json.dumps(revised));alignment=dict(productAccepted=False,globalMetricGroundTruth=False,trainingIndices=train,evaluationIndices=queries,trainingAttempts=rows,unregisteredIndices=sorted(set(train)-retained),components=[dict(component=0,registeredIndices=indices,rejectedRegistrationIndices=sorted(set(indices)-retained),scaleToAR=scale,rotation=R.tolist(),translation=t.tolist())],criteria=dict(maximumOrientationPriorDegrees=5,maximumCenterPriorMapUnits=.15,uncalibratedResearchConsistencyOnly=True))
    (out/'alignment.json').write_text(json.dumps(alignment,indent=2))
    for iid in rec.reg_image_ids():
        im=rec.images[iid];idx=int(Path(im.name).stem)
        if idx not in retained:continue
        f=np.load(source/f'features-{idx}.npz');ids=[p.point3D_id if p.point3D_id in rec.points3D else None for p in im.points2D]
        if len(f['points'])!=len(ids):raise ValueError('frozen_feature_identity_mismatch')
        views.append((idx,f['points'],f['descriptors'],ids))
    groups=landmark_groups(rec,views);(out/'landmark-groups.json').write_text(json.dumps({str(k):v for k,v in groups.items()}))
    seeds=[]
    for pid,p in rec.points3D.items():
        observed={int(Path(rec.images[e.image_id].name).stem) for e in p.track.elements if int(Path(rec.images[e.image_id].name).stem) in retained}
        if len(observed)>=2:seeds.append(dict(pointId=int(pid),xyz=(scale*R@p.xyz+t).tolist(),rgb=np.asarray(p.color).tolist(),trainingViews=sorted(observed),reprojectionPixels=float(p.error),measurementTruth=False))
    (out/'sparse-seed.json').write_text(json.dumps(dict(coordinateSystem='AR_sensor_prior_aligned_not_metric_truth',points=seeds,productAccepted=False)))
    print(json.dumps(dict(alignmentRetained=len(retained),attempts=len(train),seedPoints=len(seeds))),flush=True)
    if args.align_only:return
    features=out/'query-features';features.mkdir();matchesdir=out/'query-matches';matchesdir.mkdir();extractor=TimedWorker(args.resources,'feature');matcher=TimedWorker(args.resources,'match');records=[];start=time.monotonic();pycolmap.set_random_seed(42)
    try:
        for idx in queries:
            qstart=time.monotonic();frame=document['keyframes'][idx];qpath=features/f'{idx}.npz';image=data/frame['imageFile'];key=digest(image)+json.dumps(provenance,sort_keys=True)
            extraction=extractor.call(dict(image=str(image),expectedSize=[frame['width'],frame['height']],output=str(qpath),cacheKey=key),45)
            row=dict(frameIndex=idx,extraction=extraction,pairAttempts=[],productAccepted=False,queryRole='repeated_development_query_not_final_holdout')
            if extraction['status']!='extracted':row['status']='feature_extraction_failed';records.append(row);continue
            q=np.load(qpath);pts=q['points'];votes=collections.defaultdict(list)
            for tid,tpts,_,tids in views:
                mpath=matchesdir/f'{idx}-{tid}.npz';attempt=matcher.call(dict(featureA=str(qpath),featureB=str(source/f'features-{tid}.npz'),output=str(mpath),frameA=frame,frameB=document['keyframes'][tid],indices=[idx,tid]),45);attempt.update(trainingFrameIndex=tid,matchFile=str(mpath) if mpath.exists() else None);row['pairAttempts'].append(attempt)
                if attempt['status']=='matched':
                    for qi,ti in np.load(mpath)['matches']:
                        pid=tids[ti]
                        if pid is not None:votes[int(qi)].append((int(pid),tid))
            associated,ambiguous=associate(votes,groups);fitgroups={};scoring=[]
            for m in sorted(associated,key=lambda x:(-len(x['supportingTrainFrames']),x['query'])):
                m['pixel']=pts[m['query']].tolist()
                if m['group']%2==0:fitgroups.setdefault(m['group'],m)
                else:scoring.append(m)
            fit=list(fitgroups.values());fitq=np.array([m['query'] for m in fit],int);k=np.asarray(frame['intrinsics']).reshape(3,3,order='F');camera=pycolmap.Camera(model='PINHOLE',width=frame['width'],height=frame['height'],params=[k[0,0],k[1,1],k[0,2]+.5,k[1,2]+.5]);pose_result=None
            row.update(fitMatches=fit,fitLandmarkCount=len(fit),scoringCandidateCount=len(scoring),ambiguous=ambiguous,solverReturnedPose=False,scoring=[])
            if len(fit)>=12:
                eo=pycolmap.AbsolutePoseEstimationOptions();eo.ransac.max_error=3;eo.estimate_focal_length=False;ro=pycolmap.AbsolutePoseRefinementOptions();ro.refine_focal_length=False;ro.refine_extra_params=False
                try:
                    pose_result=pycolmap.estimate_and_refine_absolute_pose(np.asarray([m['pixel'] for m in fit])+.5,np.asarray([rec.points3D[m['pointId']].xyz for m in fit]),camera,eo,ro,return_covariance=True)
                except (ValueError,RuntimeError) as exc:
                    row['solverErrorType']=type(exc).__name__
            row['solverReturnedPose']=pose_result is not None
            if pose_result:
                pose=pose_result['cam_from_world'];qr=pose.rotation.matrix();qt=pose.translation;row['fitInlierCount']=int(np.asarray(pose_result['inlier_mask']).sum());row['cameraFromSfMWorld']=pose.matrix().tolist();c=np.eye(4);c[:3,:3]=R@qr.T@AX;c[:3,3]=scale*R@(-qr.T@qt)+t;row['inferredCameraToWorldAR']=c.tolist();scored=set()
                for m in scoring:
                    score={**m,'status':'failed'};qi=m['query']
                    if m['group'] in scored:reason='repeated_scoring_landmark_group'
                    elif len(fitq) and np.any(np.linalg.norm(pts[fitq]-pts[qi],axis=1)<=64):reason='scoring_patch_overlaps_localization_patch'
                    else:
                        scored.add(m['group']);xyz=qr@rec.points3D[m['pointId']].xyz+qt
                        if xyz[2]<=0:reason='negative_depth'
                        else:
                            uv=k@xyz;uv=uv[:2]/uv[2];err=float(np.linalg.norm(uv-pts[qi]));reason='within_3px' if err<=3 else 'exceeds_3px';score.update(reprojectionErrorPixels=err,predictedPixel=uv.tolist(),status='passed' if err<=3 else 'failed')
                    score['reason']=reason;row['scoring'].append(score)
            else:row['scoring']=[{**m,'status':'failed','reason':'pose_solver_failed'} for m in scoring]
            row.update(status=classify_candidate(row),scoringPassed=sum(s['status']=='passed' for s in row['scoring']),elapsedSeconds=time.monotonic()-qstart);records.append(row);(out/'query-progress.json').write_text(json.dumps(records,indent=2));print(json.dumps({k:row.get(k) for k in ['frameIndex','status','fitLandmarkCount','fitInlierCount','scoringPassed','elapsedSeconds']}),flush=True)
    finally:extractor.close();matcher.close()
    result=dict(records=records,trainingIndices=train,developmentQueryIndices=queries,trainingMapFrozen=True,mapReceipts=receipts,featureReceipts=feature_receipts,sourceFeatureIdentity='SuperPoint',provenance=provenance,trainingDocumentSHA256=docsha,elapsedSeconds=time.monotonic()-start,productAccepted=False,finalHoldout=False,statusCounts=dict(collections.Counter(r['status'] for r in records)))
    result['frozenMapHashesUnchanged']=all(digest(source/'models'/'0'/name)==sha for name,sha in receipts.items());result['frozenFeatureHashesUnchanged']=all(digest(source/f'features-{idx}.npz')==sha for idx,sha in feature_receipts.items());(out/'result.json').write_text(json.dumps(result,indent=2))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('source','data','resources','output'):p.add_argument('--'+k,required=True)
    p.add_argument('--align-only',action='store_true');run(p.parse_args())
if __name__=='__main__':main()
