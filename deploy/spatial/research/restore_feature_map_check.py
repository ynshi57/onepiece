"""New-room epoch2 frozen references / epoch3 restoration developer queries.

Research SP/LG only. Different-room saved photos are explicit negatives, never
joined into the reference world. Raw AR and saved world maps are not truth.
"""
from __future__ import annotations
import argparse,json,sys,time
from pathlib import Path
import numpy as np
from learned_feature_pairs import TimedWorker,digest,verify_resources

def partitions(document):
    frames=document['keyframes'];reference=[i for i,f in enumerate(frames) if f['pose']['epoch']==2];query=[i for i,f in enumerate(frames) if f['pose']['epoch']==3]
    if len(reference)!=157 or len(query)!=22 or set(reference)&set(query) or len(reference)+len(query)!=len(frames):raise ValueError('expected_persisted_157_reference_22_query_partition')
    return reference,query

def uniform_indices(indices,count=24):
    if len(indices)<=count:return list(indices)
    return [indices[int(j)] for j in np.linspace(0,len(indices)-1,count).round().astype(int)]

def verify_receipt(scan,receipt):
    r=json.loads(receipt.read_text())
    for row in r['files']:
        path=scan/row['path']
        if path.stat().st_size!=row['size'] or digest(path)!=row['sha256']:raise ValueError('frozen_original_scan_changed')
    return r

def extract_all(args):
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'server-vqa'));from app.spatial_splat_dataset import partition_epoch_frames
    scan,negative,out=map(Path,(args.scan,args.negative_scan,args.output));out.mkdir(parents=True,exist_ok=True);receipt=verify_receipt(scan,Path(args.receipt));doc=json.loads((scan/'document.json').read_text());old=json.loads((negative/'document.json').read_text());reference,query=partitions(doc);oldtrain,_=partition_epoch_frames(old,3);negative_indices=uniform_indices([i for i,_ in oldtrain],22);provenance=verify_resources(args.resources);features=out/'features';features.mkdir(exist_ok=True);querydir=out/'query-features';querydir.mkdir(exist_ok=True)
    result=dict(referenceIndices=reference,restorationQueryIndices=query,negativeIndices=negative_indices,negativeMeaning='actual_saved_different_room_not_artificial_pose_perturbation',inputDocumentSHA256=digest(scan/'document.json'),negativeDocumentSHA256=digest(negative/'document.json'),sourceReceipt=receipt,provenance=provenance,featureAttempts=[],productAccepted=False,worldMapIsNotIndependentTruth=True,finalHoldout=False)
    def save():(out/'feature-result.json').write_text(json.dumps(result,indent=2))
    worker=TimedWorker(args.resources,'feature',threads=1);start=time.monotonic()
    try:
        for kind,indices,data,document in [('reference',reference,scan,doc),('restoration_query',query,scan,doc),('different_room_negative',negative_indices,negative,old)]:
            for i in indices:
                frame=document['keyframes'][i];path=features/f'{i}.npz' if kind=='reference' else querydir/f'{kind}-{i}.npz';key=digest(data/frame['imageFile'])+json.dumps(provenance,sort_keys=True);cache=False
                if path.exists():
                    with np.load(path) as f:cache=str(f['cacheKey'])==key
                if cache:row=dict(status='extracted',cached=True,keypointCount=len(np.load(path)['points']),elapsedSeconds=0)
                else:row=worker.call(dict(image=str(data/frame['imageFile']),expectedSize=[frame['width'],frame['height']],output=str(path),cacheKey=key),45)
                row.update(kind=kind,frameIndex=i,featureFile=str(path) if row['status']=='extracted' else None,imageSHA256=digest(data/frame['imageFile']));result['featureAttempts'].append(row);save();print(json.dumps(row),flush=True)
    finally:worker.close()
    result.update(elapsedSeconds=time.monotonic()-start,all179OriginalImagesRetained=sum(r['kind']!='different_room_negative' for r in result['featureAttempts'])==179,all22NegativeImagesRetained=sum(r['kind']=='different_room_negative' for r in result['featureAttempts'])==22);save();return result

def track_groups_grid(rec,views,radius=8.):
    """Whole-track union with training-only pixel neighbors, no scipy dependency."""
    parents={int(pid):int(pid) for pid in rec.points3D}
    def root(pid):
        while parents[pid]!=pid:parents[pid]=parents[parents[pid]];pid=parents[pid]
        return pid
    for idx,points,ids in views:
        cells={}
        for j,pid in enumerate(ids):
            if pid is None:continue
            pid=int(pid);pt=points[j];cell=tuple(np.floor(pt/radius).astype(int))
            for dx in (-1,0,1):
                for dy in (-1,0,1):
                    for other,xy in cells.get((cell[0]+dx,cell[1]+dy),[]):
                        if np.sum((pt-xy)**2)<=radius**2:
                            a,b=root(pid),root(other);parents[max(a,b)]=min(a,b)
            cells.setdefault(cell,[]).append((pid,pt))
    return {pid:root(pid) for pid in parents}

def select_fit_budget(matches,points,training_support,maximum=64,minimum_pixels=128.):
    """Training-ranked fit only; scoring data never used. All rejects retained."""
    chosen=[];unused=[]
    for m in sorted(matches,key=lambda m:(-training_support[m['pointId']][0],training_support[m['pointId']][1],m['group'],m['query'])):
        if len(chosen)>=maximum:unused.append(dict(m,unusedReason='fixed_fit_budget'))
        elif any(np.linalg.norm(points[m['query']]-points[x['query']])<minimum_pixels for x in chosen):unused.append(dict(m,unusedReason='fixed_fit_pixel_separation'))
        else:chosen.append(m)
    return chosen,unused

def map_query(args):
    import pycolmap,collections
    from learned_feature_pairs import reconstruct_pair_map,plan_pairs
    from learned_map_query import associate,align_sensor,AX,angle
    from learned_query_diagnosis import project
    from resect_training_views import classify_candidate
    scan,negative,out=map(Path,(args.scan,args.negative_scan,args.output));verify_receipt(scan,Path(args.receipt));doc=json.loads((scan/'document.json').read_text());old=json.loads((negative/'document.json').read_text());features=json.loads((out/'feature-result.json').read_text());reference,query=partitions(doc)
    if features['provenance']!=verify_resources(args.resources) or features['inputDocumentSHA256']!=digest(scan/'document.json') or features['negativeDocumentSHA256']!=digest(negative/'document.json'):raise ValueError('feature_provenance_or_document_changed')
    if any(r['status']!='extracted' for r in features['featureAttempts']):raise ValueError('feature_failures_retained_retry_required_before_mapping')
    pairs=plan_pairs(reference,query,offsets=(1,2,4,7));matchdir=out/'matches';matchdir.mkdir(exist_ok=True);trainrows=[];worker=TimedWorker(args.resources,'match',threads=1);start=time.monotonic()
    try:
        for a,b in pairs:
            path=matchdir/f'{a}-{b}.npz';row=worker.call(dict(featureA=str(out/'features'/f'{a}.npz'),featureB=str(out/'features'/f'{b}.npz'),output=str(path),frameA=doc['keyframes'][a],frameB=doc['keyframes'][b],indices=[a,b]),45);row.update(frameA=a,frameB=b,matchFile=str(path) if path.exists() else None);trainrows.append(row)
            (out/'training-pair-result.json').write_text(json.dumps(dict(referenceIndices=reference,excludedQueryIndices=query,pairPolicy='reference_temporal_offsets_1_2_4_7',requestedPairCount=len(pairs),pairs=trainrows,productAccepted=False),indent=2));print(json.dumps(dict(trainingPair=[a,b],status=row['status'],matchCount=row.get('matchCount'))),flush=True)
    finally:worker.close()
    if any(r['status']!='matched' for r in trainrows):raise ValueError('training_pair_failures_retained_retry_required_before_mapping')
    mapped=reconstruct_pair_map(data=str(scan),output=str(out/'reference-map'),training=reference,evaluation=query,pairs=pairs,feature_cache=str(out/'features'),sift_control='',method='superpoint_lightglue');print(json.dumps(dict(mapComponents=[c['registeredCount'] for c in mapped['components']])),flush=True)
    maps=[];registered=[];mapreceipts={};alignments={}
    for c in mapped['components']:
        rec=pycolmap.Reconstruction(c['modelPath']);views=[]
        for iid in rec.reg_image_ids():
            image=rec.images[iid];idx=int(Path(image.name).stem)
            if idx not in reference or idx in query:raise ValueError('query_image_leaked_into_reference_map')
            f=np.load(out/'features'/f'{idx}.npz');ids=[p.point3D_id if p.point3D_id in rec.points3D else None for p in image.points2D]
            if len(ids)!=len(f['points']) or not np.allclose(np.asarray([p.xy for p in image.points2D]),f['points']+.5,atol=.001,rtol=0):raise ValueError('frozen_feature_or_pixel_identity_mismatch')
            views.append((idx,f['points'],ids));registered.append(idx)
        try:
            centers=[];rotations=[];raw=[]
            for iid in rec.reg_image_ids():
                image=rec.images[iid];idx=int(Path(image.name).stem);pose=image.cam_from_world();r=pose.rotation.matrix();centers.append(-r.T@pose.translation);rotations.append(r.T@AX);raw.append(np.asarray(doc['keyframes'][idx]['cameraToWorld']).reshape(4,4,order='F'))
            ra,scale,translation,keep,angles,errors=align_sensor(centers,rotations,raw)
            alignments[c['id']]=dict(status='training_sensor_consistency_prior_not_truth',rotation=ra.tolist(),scale=scale,translation=translation.tolist(),orientationDegrees=angles.tolist(),centerErrors=errors.tolist(),trainingRegistrationIndices=[int(Path(rec.images[iid].name).stem) for iid in rec.reg_image_ids()],kept=keep.tolist(),queryUsed=False)
        except ValueError as exc:alignments[c['id']]=dict(status='training_alignment_failed',reason=str(exc),queryUsed=False)
        maps.append((c['id'],rec,views,track_groups_grid(rec,views)))
        for path in Path(c['modelPath']).iterdir():
            if path.is_file():mapreceipts[str(path)]=digest(path)
    chosen=uniform_indices(sorted(set(registered)),24);(out/'reference-selection.json').write_text(json.dumps(dict(indices=chosen,policy='uniform_capture_order_registered_training_only_maximum_24_same_for_positive_and_negative',queryRGBUsedForSelection=False),indent=2));querymatches=out/'query-matches';querymatches.mkdir(exist_ok=True);featureby={(r['kind'],r['frameIndex']):r for r in features['featureAttempts']};records=[];matcher=TimedWorker(args.resources,'match',threads=1);pycolmap.set_random_seed(42)
    try:
        for kind,indices,data,document in [('restoration_query',query,scan,doc),('different_room_negative',features['negativeIndices'],negative,old)]:
            for idx in indices:
                qstart=time.monotonic();frame=document['keyframes'][idx];path=Path(featureby[(kind,idx)]['featureFile']);pts=np.load(path)['points'];attempts=[];matches={}
                for tid in chosen:
                    target=querymatches/f'{kind}-{idx}-{tid}.npz';row=matcher.call(dict(featureA=str(path),featureB=str(out/'features'/f'{tid}.npz'),output=str(target),frameA=frame,frameB=doc['keyframes'][tid],indices=[idx,tid]),45);row.update(referenceFrameIndex=tid,matchFile=str(target) if target.exists() else None);attempts.append(row)
                    if row['status']=='matched':matches[tid]=np.load(target)['matches']
                componentrows=[];k=np.asarray(frame['intrinsics']).reshape(3,3,order='F');camera=pycolmap.Camera(model='PINHOLE',width=frame['width'],height=frame['height'],params=[k[0,0],k[1,1],k[0,2]+.5,k[1,2]+.5])
                for component,rec,views,groups in maps:
                    votes=collections.defaultdict(list)
                    for tid,tpts,tids in views:
                        for qi,ti in matches.get(tid,[]):
                            pid=tids[ti]
                            if pid is not None:votes[int(qi)].append((int(pid),tid))
                    associated,ambiguous=associate(votes,groups);fitgroups={};scoring=[]
                    for m in sorted(associated,key=lambda m:(-len(m['supportingTrainFrames']),m['query'])):
                        m['pixel']=pts[m['query']].tolist()
                        if m['group']%2==0:fitgroups.setdefault(m['group'],m)
                        else:scoring.append(m)
                    fit,unusedfit=select_fit_budget(list(fitgroups.values()),pts,{pid:(p.track.length(),p.error) for pid,p in rec.points3D.items()});pose_result=None;row=dict(component=component,fitCandidateCount=len(fitgroups),unusedFitMatches=unusedfit,fitSelectionPolicy='training_track_length_error_then_128px_query_fit_spread_max64_no_scoring_selection',fitMatches=fit,fitLandmarkCount=len(fit),scoringCandidateCount=len(scoring),ambiguous=ambiguous,solverReturnedPose=False,scoring=[],productAccepted=False)
                    alignment=alignments[component];arpose=None
                    if kind=='restoration_query' and alignment['status']=='training_sensor_consistency_prior_not_truth':
                        raw=np.asarray(frame['cameraToWorld']).reshape(4,4,order='F');ra=np.asarray(alignment['rotation']);translation=np.asarray(alignment['translation']);scale=alignment['scale'];ar_c2w=np.eye(4);ar_c2w[:3,:3]=ra.T@raw[:3,:3]@AX;ar_c2w[:3,3]=ra.T@(raw[:3,3]-translation)/scale;arpose=np.linalg.inv(ar_c2w)
                    row['originalARAssociatedScoring']=[dict(m,projection=project(rec.points3D[m['pointId']].xyz,arpose,k,m['pixel'])) for m in scoring] if arpose is not None else []
                    row['originalARProjectionUnavailableReason']=None if arpose is not None else 'different_room_no_shared_world' if kind=='different_room_negative' else alignment['status']
                    if len(fit)>=12:
                        eo=pycolmap.AbsolutePoseEstimationOptions();eo.ransac.max_error=3.;eo.estimate_focal_length=False;ro=pycolmap.AbsolutePoseRefinementOptions();ro.refine_focal_length=False;ro.refine_extra_params=False
                        try:pose_result=pycolmap.estimate_and_refine_absolute_pose(np.asarray([m['pixel'] for m in fit])+.5,np.asarray([rec.points3D[m['pointId']].xyz for m in fit]),camera,eo,ro)
                        except (ValueError,RuntimeError) as exc:row['solverErrorType']=type(exc).__name__
                    if pose_result:
                        pose=pose_result['cam_from_world'];R=pose.rotation.matrix();t=pose.translation
                        if arpose is not None:
                            inferred=np.eye(4);inferred[:3,:3]=ra@R.T@AX;inferred[:3,3]=scale*ra@(-R.T@t)+translation;row['inferredVsOriginalAR']=dict(orientationDegrees=angle(inferred[:3,:3],raw[:3,:3]),centerDifferenceMapUnits=float(np.linalg.norm(inferred[:3,3]-raw[:3,3])),sensorPriorNotGroundTruth=True)
                        fitq=np.asarray([m['query'] for m in fit],int);row.update(solverReturnedPose=True,fitInlierCount=int(np.asarray(pose_result['inlier_mask']).sum()),cameraFromReferenceSfM=pose.matrix().tolist());scored=set()
                        for m in scoring:
                            score={**m,'status':'failed'};qi=m['query']
                            if m['group'] in scored:reason='repeated_scoring_landmark_group'
                            elif len(fitq) and np.any(np.linalg.norm(pts[fitq]-pts[qi],axis=1)<=64):reason='scoring_patch_overlaps_localization_patch'
                            else:
                                scored.add(m['group']);xyz=R@rec.points3D[m['pointId']].xyz+t
                                if xyz[2]<=0:reason='negative_depth'
                                else:
                                    uv=k@xyz;uv=uv[:2]/uv[2];error=float(np.linalg.norm(uv-pts[qi]));reason='within_3px' if error<=3 else 'exceeds_3px';score.update(reprojectionErrorPixels=error,predictedPixel=uv.tolist(),status='passed' if error<=3 else 'failed')
                            score['reason']=reason;row['scoring'].append(score)
                    else:row['scoring']=[{**m,'status':'failed','reason':'pose_solver_failed'} for m in scoring]
                    row.update(status=classify_candidate(row),scoringPassed=sum(s['status']=='passed' for s in row['scoring']));componentrows.append(row)
                record=dict(kind=kind,frameIndex=idx,status='components_attempted_no_camera_published' if componentrows else 'no_reference_map_available',pairAttempts=attempts,components=componentrows,elapsedSeconds=time.monotonic()-qstart,productAccepted=False);records.append(record);(out/'query-progress.json').write_text(json.dumps(records,indent=2));print(json.dumps(dict(queryKind=kind,frameIndex=idx,components=[(r['status'],r.get('fitInlierCount',0),r['scoringPassed']) for r in componentrows])),flush=True)
    finally:matcher.close()
    result=dict(records=records,referenceMap=mapped,trainingOnlyARAlignments=alignments,referenceIndices=reference,restorationQueryIndices=query,differentRoomNegativeIndices=features['negativeIndices'],referenceSelection=chosen,trainingPairsAttempted=len(trainrows),allOriginal179FeatureAttemptsRetained=features['all179OriginalImagesRetained'],all22RestorationQueriesRetained=sum(r['kind']=='restoration_query' for r in records)==22,all22DifferentRoomNegativesRetained=sum(r['kind']=='different_room_negative' for r in records)==22,frozenMapHashesUnchanged=all(digest(path)==sha for path,sha in mapreceipts.items()),rawAROrWorldMapUsedAsGroundTruth=False,trainedOnRestorationQueryRGB=False,trainedOnNegativeRoomRGB=False,sourceScanHashesUnchanged=bool(verify_receipt(scan,Path(args.receipt))),independentModelComputationNotGuaranteed='LightGlue_global_attention_correlates_feature_groups',productAccepted=False,finalHoldout=False,elapsedSeconds=time.monotonic()-start);(out/'result.json').write_text(json.dumps(result,indent=2))

def diagnose_original_ar(args):
    import pycolmap
    from learned_map_query import align_sensor,AX,angle
    from learned_query_diagnosis import project
    scan,out=map(Path,(args.scan,args.output));doc=json.loads((scan/'document.json').read_text());result=json.loads((out/'result.json').read_text());alignments={};maps={}
    for component in result['referenceMap']['components']:
        rec=pycolmap.Reconstruction(component['modelPath']);centers=[];rotations=[];raw=[];indices=[]
        for iid in rec.reg_image_ids():
            im=rec.images[iid];idx=int(Path(im.name).stem);pose=im.cam_from_world();r=pose.rotation.matrix();centers.append(-r.T@pose.translation);rotations.append(r.T@AX);raw.append(np.asarray(doc['keyframes'][idx]['cameraToWorld']).reshape(4,4,order='F'));indices.append(idx)
        try:
            ra,scale,translation,keep,angles,errors=align_sensor(centers,rotations,raw);alignments[component['id']]=dict(status='training_sensor_consistency_prior_not_truth',rotation=ra.tolist(),scale=scale,translation=translation.tolist(),trainingIndices=indices,kept=keep.tolist(),orientationDegrees=angles.tolist(),centerErrors=errors.tolist(),queryUsed=False)
        except ValueError as exc:alignments[component['id']]=dict(status='training_alignment_failed',reason=str(exc),queryUsed=False)
        maps[component['id']]=rec
    records=[]
    for record in result['records']:
        idx=record['frameIndex'];rows=[]
        for component in record['components']:
            identity=component['component'];alignment=alignments[identity];row=dict(component=identity,originalARScoring=[],productAccepted=False)
            if record['kind']!='restoration_query':row['reason']='different_room_no_shared_world'
            elif alignment['status']!='training_sensor_consistency_prior_not_truth':row['reason']=alignment['status']
            else:
                frame=doc['keyframes'][idx];k=np.asarray(frame['intrinsics']).reshape(3,3,order='F');raw=np.asarray(frame['cameraToWorld']).reshape(4,4,order='F');ra=np.asarray(alignment['rotation']);scale=alignment['scale'];translation=np.asarray(alignment['translation']);ar=np.eye(4);ar[:3,:3]=ra.T@raw[:3,:3]@AX;ar[:3,3]=ra.T@(raw[:3,3]-translation)/scale;arpose=np.linalg.inv(ar);rec=maps[identity]
                for m in component['scoring']:
                    row['originalARScoring'].append(dict(pointId=m['pointId'],group=m['group'],query=m['query'],pixel=m['pixel'],PnPScoringReason=m['reason'],originalARProjection=project(rec.points3D[m['pointId']].xyz,arpose,k,m['pixel']),PnPProjectionErrorPixels=m.get('reprojectionErrorPixels')))
                if component['solverReturnedPose']:
                    pose=np.asarray(component['cameraFromReferenceSfM']);R=pose[:3,:3];t=pose[:3,3];inferred=np.eye(4);inferred[:3,:3]=ra@R.T@AX;inferred[:3,3]=scale*ra@(-R.T@t)+translation;row['inferredVsOriginalAR']=dict(orientationDegrees=angle(inferred[:3,:3],raw[:3,:3]),centerDifferenceMapUnits=float(np.linalg.norm(inferred[:3,3]-raw[:3,3])),sensorPriorNotGroundTruth=True)
            rows.append(row)
        records.append(dict(kind=record['kind'],frameIndex=idx,components=rows))
    evidence=dict(trainingOnlyAlignments=alignments,records=records,queryRGBUsedForAlignment=False,originalWorldMapNotGroundTruth=True,independentPhotographicIdentityDoesNotCertifyOriginalARPose=True,productAccepted=False,sourceResultSHA256=digest(out/'result.json'));(out/'original-AR-diagnosis.json').write_text(json.dumps(evidence,indent=2));return evidence

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('scan','negative-scan','resources','receipt','output'):p.add_argument('--'+key,required=True)
    p.add_argument('--stage',choices=['features','map-query','ar-diagnosis'],default='features');args=p.parse_args()
    if args.stage=='features':extract_all(args)
    elif args.stage=='map-query':map_query(args)
    else:diagnose_original_ar(args)
if __name__=='__main__':main()
