"""All-correspondence diagnosis of frozen SP/LG developer localization failures.

Never changes a map or localization thresholds; train self-reprojection checks
implementation consistency only, not product or metric ground truth.
"""
from __future__ import annotations
import argparse,collections,json,time
from pathlib import Path
import numpy as np
from learned_feature_pairs import digest
from learned_map_query import AX,associate

def statistics(values):
    a=np.asarray(values,float);valid=a[np.isfinite(a)]
    return dict(count=len(a),finiteCount=len(valid),nonfiniteCount=len(a)-len(valid),median=float(np.median(valid)) if len(valid) else None,p95=float(np.percentile(valid,95)) if len(valid) else None,maximum=float(valid.max()) if len(valid) else None)

def project(xyz,camera_from_world,k,pixel):
    xyz=np.asarray(camera_from_world)[:3,:3]@np.asarray(xyz)+np.asarray(camera_from_world)[:3,3]
    if not np.isfinite(xyz).all():return dict(reason='nonfinite_geometry',within3Pixels=False)
    if xyz[2]<=0:return dict(reason='negative_depth',depth=float(xyz[2]),within3Pixels=False)
    uv=k@xyz;uv=uv[:2]/uv[2];e=float(np.linalg.norm(uv-pixel))
    return dict(reason='projected',depth=float(xyz[2]),predictedPixel=uv.tolist(),errorPixels=e,within3Pixels=e<=3)

def raw_ar_in_map(frame,alignment):
    raw=np.asarray(frame['cameraToWorld']).reshape(4,4,order='F');R=np.asarray(alignment['rotation']);t=np.asarray(alignment['translation']);s=alignment['scaleToAR'];c=np.eye(4);c[:3,:3]=R.T@raw[:3,:3]@AX;c[:3,3]=R.T@(raw[:3,3]-t)/s
    return np.linalg.inv(c)

def summary(rows,pose):
    projections=[r[pose] for r in rows];finite=[p['errorPixels'] for p in projections if 'errorPixels' in p]
    return dict(attemptCount=len(rows),reasonCounts=dict(collections.Counter(p['reason'] for p in projections)),errorPixels=statistics(finite),within3Pixels=sum(p['within3Pixels'] for p in projections),passedFractionAllAttempts=sum(p['within3Pixels'] for p in projections)/len(rows) if rows else None)

def run(args):
    import pycolmap
    source,data,query,out=map(Path,(args.source,args.data,args.query,args.output));out.mkdir(parents=True,exist_ok=False);started=time.monotonic();document=json.loads((data/'document.json').read_text());inputs=json.loads((source/'inputs.json').read_text());result=json.loads((query/'result.json').read_text());alignment=json.loads((query/'alignment.json').read_text())['components'][0];rejected=set(alignment['rejectedRegistrationIndices']);rec=pycolmap.Reconstruction(str(source/'models'/'0'));groups={int(k):v for k,v in json.loads((query/'landmark-groups.json').read_text()).items()};receipts={p.name:digest(p) for p in (source/'models'/'0').iterdir() if p.is_file()};views={};selfchecks=[];allself=[]
    for iid in sorted(rec.reg_image_ids()):
        image=rec.images[iid];idx=int(Path(image.name).stem);features=np.load(source/f'features-{idx}.npz');pts=features['points'];ids=[p.point3D_id if p.point3D_id in rec.points3D else None for p in image.points2D]
        if len(pts)!=len(ids):raise ValueError('point_identity_size_mismatch')
        expected=np.asarray([p.xy for p in image.points2D])-.5
        archive_difference=np.linalg.norm(expected-pts,axis=1)
        k=np.asarray(document['keyframes'][idx]['intrinsics']).reshape(3,3,order='F');pose=image.cam_from_world().matrix();rows=[]
        for j,pid in enumerate(ids):
            if pid is None:continue
            p=project(rec.points3D[pid].xyz,pose,k,pts[j]);rows.append(dict(featureIndex=j,pointId=int(pid),pixel=pts[j].tolist(),projection=p));allself.append(p)
        selfchecks.append(dict(frameIndex=idx,nativeArchiveVsStoredCornerMinusHalf=statistics(archive_difference),attemptCount=len(rows),projectionErrorPixels=statistics([r['projection']['errorPixels'] for r in rows if 'errorPixels' in r['projection']]),rows=rows))
        if idx not in rejected:views[idx]=(pts,ids)
    (out/'training-self-check.json').write_text(json.dumps(dict(implementationConsistencyOnly=True,productAccepted=False,frames=selfchecks,allAttempts=len(allself),projectionReasonCounts=dict(collections.Counter(p['reason'] for p in allself)),projectionErrorPixels=statistics([p['errorPixels'] for p in allself if 'errorPixels' in p]))))
    groupmembers=collections.defaultdict(list)
    for pid,gid in groups.items():groupmembers[gid].append(pid)
    groupstats=[]
    for gid,pids in groupmembers.items():
        xyz=np.asarray([rec.points3D[p].xyz for p in pids]);spread=float(np.max(np.linalg.norm(xyz-xyz.mean(0),axis=1)))
        groupstats.append(dict(group=gid,pointCount=len(pids),maximumRadiusSfMUnits=spread,maximumRadiusARPriorUnits=spread*alignment['scaleToAR']))
    records=[];allassociated=[];allraw=[]
    for record in result['records']:
        idx=record['frameIndex'];q=np.load(query/'query-features'/f'{idx}.npz');pts=q['points'];votes=collections.defaultdict(list);rawrows=[];k=np.asarray(document['keyframes'][idx]['intrinsics']).reshape(3,3,order='F');arpose=raw_ar_in_map(document['keyframes'][idx],alignment);pnppose=np.asarray(record['cameraFromSfMWorld']) if record.get('solverReturnedPose') else None
        for attempt in record['pairAttempts']:
            if attempt['status']!='matched':continue
            tid=attempt['trainingFrameIndex'];_,ids=views[tid]
            for qi,ti in np.load(attempt['matchFile'])['matches']:
                pid=ids[ti]
                if pid is None:continue
                qi,ti,pid=int(qi),int(ti),int(pid);votes[qi].append((pid,tid));row=dict(query=qi,trainingFrameIndex=tid,trainingFeatureIndex=ti,pointId=pid,group=groups[pid],pixel=pts[qi].tolist());row['originalAR']=project(rec.points3D[pid].xyz,arpose,k,pts[qi]);row['inferredPnP']=project(rec.points3D[pid].xyz,pnppose,k,pts[qi]) if pnppose is not None else dict(reason='pose_solver_failed',within3Pixels=False);rawrows.append(row)
        associated,ambiguous=associate(votes,groups);associatedrows=[]
        for m in associated:
            qi,pid=m['query'],m['pointId'];row={**m,'pixel':pts[qi].tolist(),'votes':votes[qi],'distinctPointIdentityCount':len({p for p,_ in votes[qi]})};row['originalAR']=project(rec.points3D[pid].xyz,arpose,k,pts[qi]);row['inferredPnP']=project(rec.points3D[pid].xyz,pnppose,k,pts[qi]) if pnppose is not None else dict(reason='pose_solver_failed',within3Pixels=False);associatedrows.append(row)
        fitgroups={m['group'] for m in record.get('fitMatches',[])};scoregroups={m['group'] for m in record.get('scoring',[])}
        row=dict(frameIndex=idx,status=record['status'],rawMatchVotes=rawrows,associated=associatedrows,ambiguous=ambiguous,queryFeatureCount=len(pts),unmatchedToMapFeatureCount=len(pts)-len(votes),rawVoteCount=len(rawrows),associatedCount=len(associated),ambiguousCount=len(ambiguous),distinctAssociatedGroups=len({m['group'] for m in associated}),repeatedAssociatedGroupCount=len(associated)-len({m['group'] for m in associated}),fitScoringGroupIntersection=sorted(fitgroups&scoregroups),supportingTrainViewHistogram=dict(collections.Counter(len(m['supportingTrainFrames']) for m in associated)),sameGroupMultiplePointIdentityCount=sum(m['distinctPointIdentityCount']>1 for m in associatedrows),associatedOriginalAR=summary(associatedrows,'originalAR'),associatedInferredPnP=summary(associatedrows,'inferredPnP'),rawVotesOriginalAR=summary(rawrows,'originalAR'),rawVotesInferredPnP=summary(rawrows,'inferredPnP'))
        fit=record.get('fitMatches',[]);xyz=np.asarray([rec.points3D[m['pointId']].xyz for m in fit]);uv=np.asarray([m['pixel'] for m in fit])
        ev=np.linalg.eigvalsh((xyz-xyz.mean(0)).T@(xyz-xyz.mean(0))/len(xyz)) if len(xyz)>1 else np.zeros(3)
        row['fitGeometry']=dict(landmarkCount=len(xyz),covarianceEigenvaluesSfMUnits=ev.tolist(),smallestToLargestEigenvalue=float(ev[0]/ev[-1]) if ev[-1]>0 else None,pixelBounds=np.ptp(uv,axis=0).tolist() if len(uv) else [],degeneracyDiagnosticsNotAcceptance=True)
        if record.get('solverReturnedPose'):
            supported=[m for m in fit if project(rec.points3D[m['pointId']].xyz,pnppose,k,m['pixel'])['within3Pixels']]
            positions=np.asarray([rec.points3D[m['pointId']].xyz for m in supported])
            se=np.linalg.eigvalsh((positions-positions.mean(0)).T@(positions-positions.mean(0))/len(positions)) if len(positions)>1 else np.zeros(3)
            row['postRefinementFitWithin3pxGeometry']=dict(count=len(supported),covarianceEigenvalues=se.tolist(),smallestToLargestEigenvalue=float(se[0]/se[-1]) if se[-1]>0 else None,notOriginalRansacMask=True)
        records.append(row);allassociated.extend(associatedrows);allraw.extend(rawrows)
        print(json.dumps({k:row[k] for k in ['frameIndex','associatedCount','ambiguousCount','associatedOriginalAR','associatedInferredPnP']}),flush=True)
    output=dict(records=records,allAssociatedOriginalAR=summary(allassociated,'originalAR'),allAssociatedInferredPnP=summary(allassociated,'inferredPnP'),allRawVotesOriginalAR=summary(allraw,'originalAR'),allRawVotesInferredPnP=summary(allraw,'inferredPnP'),groups=groupstats,groupCardinalityHistogram=dict(collections.Counter(len(v) for v in groupmembers.values())),groupingIsExperimentalNotGuaranteedIndependent=True,allQueriesRetained=len(records)==len(inputs['evaluationExcluded']),mapHashesUnchanged=all(digest(source/'models'/'0'/p)==sha for p,sha in receipts.items()),productAccepted=False,elapsedSeconds=time.monotonic()-started)
    (out/'result.json').write_text(json.dumps(output,indent=2));return output

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('source','data','query','output'):p.add_argument('--'+key,required=True)
    run(p.parse_args())
if __name__=='__main__':main()
