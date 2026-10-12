"""Frozen training-covisibility localization control using cached SP/LG matches.

Candidate selection uses fit groups only. All global scoring conflicts/failures
remain. Does not use HLoc's loose 12px or nearest-camera fallback.
"""
from __future__ import annotations
import argparse,collections,json,time
from pathlib import Path
import numpy as np
from learned_feature_pairs import digest
from learned_map_query import associate
from learned_query_diagnosis import project
from resect_training_views import classify_candidate

def frozen_neighborhoods(views,limit=8):
    if limit<2:raise ValueError('neighborhood_limit_requires_two_views')
    ids=sorted(views);sets={i:{p for p in views[i] if p is not None} for i in ids};neighborhoods=[]
    for i in ids:
        neighbors=sorted((j for j in ids if j!=i),key=lambda j:(-len(sets[i]&sets[j]),j));chosen=[i]+[j for j in neighbors[:limit-1] if sets[i]&sets[j]];neighborhoods.append(tuple(sorted(chosen)))
    return sorted(set(neighborhoods))

def choose_fit_candidate(candidates):
    solved=[(i,c) for i,c in enumerate(candidates) if c.get('solverReturnedPose')]
    if not solved:return None
    return max(solved,key=lambda item:(item[1]['fitInlierCount'],-item[1]['fitInlierMedianErrorPixels'],-item[0]))[0]

def run(args):
    import pycolmap
    source,data,query,out=map(Path,(args.source,args.data,args.query,args.output));out.mkdir(parents=True,exist_ok=False);start=time.monotonic();document=json.loads((data/'document.json').read_text());baseline=json.loads((query/'result.json').read_text());
    if digest(data/'document.json')!=baseline['trainingDocumentSHA256']:raise ValueError('query_document_digest_mismatch')
    alignment=json.loads((query/'alignment.json').read_text())['components'][0];rec=pycolmap.Reconstruction(str(source/'models'/'0'));groups={int(k):v for k,v in json.loads((query/'landmark-groups.json').read_text()).items()};receipts={p.name:digest(p) for p in (source/'models'/'0').iterdir() if p.is_file()};views={}
    if receipts!=baseline['mapReceipts']:raise ValueError('frozen_map_receipt_mismatch')
    for iid in sorted(rec.reg_image_ids()):
        im=rec.images[iid];idx=int(Path(im.name).stem)
        if idx in alignment['rejectedRegistrationIndices']:continue
        views[idx]=[p.point3D_id if p.point3D_id in rec.points3D else None for p in im.points2D]
    neighborhoods=frozen_neighborhoods(views,args.neighbors);(out/'training-neighborhoods.json').write_text(json.dumps(dict(source='frozen_training_track_covisibility_only',neighborhoods=neighborhoods,queryUsedForNeighborhoods=False),indent=2));rows=[];pycolmap.set_random_seed(42)
    for old in baseline['records']:
        qstart=time.monotonic();idx=old['frameIndex'];frame=document['keyframes'][idx];pts=np.load(query/'query-features'/f'{idx}.npz')['points'];votes_by_train={};all_votes=collections.defaultdict(list)
        for attempt in old['pairAttempts']:
            tid=attempt['trainingFrameIndex'];votes=[]
            if attempt['status']=='matched':
                for qi,ti in np.load(attempt['matchFile'])['matches']:
                    pid=views[tid][ti]
                    if pid is not None:votes.append((int(qi),int(pid)));all_votes[int(qi)].append((int(pid),tid))
            votes_by_train[tid]=votes
        k=np.asarray(frame['intrinsics']).reshape(3,3,order='F');camera=pycolmap.Camera(model='PINHOLE',width=frame['width'],height=frame['height'],params=[k[0,0],k[1,1],k[0,2]+.5,k[1,2]+.5]);candidates=[]
        for neighborhood in neighborhoods:
            votes=collections.defaultdict(list)
            for tid in neighborhood:
                for qi,pid in votes_by_train[tid]:
                    if groups[pid]%2==0:votes[qi].append((pid,tid))
            associated,ambiguous=associate(votes,groups);fit_by_group={}
            for m in sorted(associated,key=lambda m:(-len(m['supportingTrainFrames']),m['query'])):fit_by_group.setdefault(m['group'],m)
            fit=list(fit_by_group.values());c=dict(trainingNeighborhood=neighborhood,fitLandmarkCount=len(fit),fitMatches=fit,fitAmbiguous=ambiguous,solverReturnedPose=False)
            if len(fit)>=12:
                eo=pycolmap.AbsolutePoseEstimationOptions();eo.ransac.max_error=3.;eo.estimate_focal_length=False;ro=pycolmap.AbsolutePoseRefinementOptions();ro.refine_focal_length=False;ro.refine_extra_params=False
                try:result=pycolmap.estimate_and_refine_absolute_pose(np.asarray([pts[m['query']] for m in fit])+.5,np.asarray([rec.points3D[m['pointId']].xyz for m in fit]),camera,eo,ro)
                except (ValueError,RuntimeError) as exc:result=None;c['solverErrorType']=type(exc).__name__
                if result:
                    pose=result['cam_from_world'].matrix();inliers=np.asarray(result['inlier_mask']);errors=[project(rec.points3D[m['pointId']].xyz,pose,k,pts[m['query']]).get('errorPixels',float('inf')) for m,ok in zip(fit,inliers) if ok];c.update(solverReturnedPose=True,fitInlierCount=int(inliers.sum()),fitInlierMedianErrorPixels=float(np.median(errors)),cameraFromSfMWorld=pose.tolist())
            candidates.append(c)
        winner=choose_fit_candidate(candidates);row=dict(frameIndex=idx,candidates=candidates,selectedCandidateIndex=winner,selectionEvidence='maximum_fit_inliers_then_fit_median_error_no_scoring_used',productAccepted=False,scoring=[],solverReturnedPose=winner is not None)
        scoring_votes={qi:[(p,t) for p,t in items if groups[p]%2==1] for qi,items in all_votes.items()};scoring_votes={qi:v for qi,v in scoring_votes.items() if v};scoring,ambiguous=associate(scoring_votes,groups);row['scoringAmbiguous']=ambiguous;row['scoringCandidateCount']=len(scoring)
        if winner is not None:
            c=candidates[winner];fit=c['fitMatches'];fitq=np.asarray([m['query'] for m in fit],int);row.update(fitMatches=fit,fitLandmarkCount=len(fit),fitInlierCount=c['fitInlierCount'],cameraFromSfMWorld=c['cameraFromSfMWorld']);scored=set()
            for m in sorted(scoring,key=lambda m:(-len(m['supportingTrainFrames']),m['query'])):
                qi=m['query'];s={**m,'pixel':pts[qi].tolist(),'status':'failed'}
                if m['group'] in scored:reason='repeated_scoring_landmark_group'
                elif len(fitq) and np.any(np.linalg.norm(pts[fitq]-pts[qi],axis=1)<=64):reason='scoring_patch_overlaps_localization_patch'
                else:
                    scored.add(m['group']);p=project(rec.points3D[m['pointId']].xyz,c['cameraFromSfMWorld'],k,pts[qi]);reason='negative_depth' if p['reason']=='negative_depth' else 'within_3px' if p['within3Pixels'] else 'exceeds_3px';s.update(p,status='passed' if p['within3Pixels'] else 'failed');
                    if 'errorPixels' in p:s['reprojectionErrorPixels']=p['errorPixels']
                s['reason']=reason;row['scoring'].append(s)
        else:row['scoring']=[{**m,'status':'failed','reason':'pose_solver_failed'} for m in scoring]
        row.update(status=classify_candidate(row),scoringPassed=sum(m['status']=='passed' for m in row['scoring']),elapsedSeconds=time.monotonic()-qstart);rows.append(row);(out/'progress.json').write_text(json.dumps(rows,indent=2));print(json.dumps({k:row.get(k) for k in ['frameIndex','status','fitInlierCount','scoringPassed','elapsedSeconds']}),flush=True)
    result=dict(records=rows,trainingNeighborhoodCount=len(neighborhoods),developmentQueries=baseline['developmentQueryIndices'],trainingMapFrozen=True,mapHashesUnchanged=all(digest(source/'models'/'0'/name)==sha for name,sha in receipts.items()),productAccepted=False,finalHoldout=False,elapsedSeconds=time.monotonic()-start,statusCounts=dict(collections.Counter(r['status'] for r in rows)),criteria=dict(maximumPixels=3,minimumFitInliers=12,minimumIndependentScoring=12,minimumScorePassFraction=.8,minimumQueryPatchSeparationPixels=64,uncalibratedDevelopmentControl=True));(out/'result.json').write_text(json.dumps(result,indent=2));return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('source','data','query','output'):p.add_argument('--'+k,required=True)
    p.add_argument('--neighbors',type=int,default=8);run(p.parse_args())
if __name__=='__main__':main()
