#!/usr/bin/env python3
"""Research-only frozen-component resection of unreliable TRAINING views.

Uses the source-built SIFT bridge plus experimental pycolmap. Never modifies AR
poses or evaluates final product accuracy. CLI partitions JSON has explicit
training_indices/evaluation_indices/target_indices; all targets must be train.
"""
from __future__ import annotations
import argparse
import collections
import hashlib
import json
from pathlib import Path
import sys
import time
import numpy as np


def validate_partitions(payload):
    training=list(payload['training_indices']);evaluation=list(payload['evaluation_indices']);targets=list(payload['target_indices'])
    if any(len(set(x))!=len(x) for x in (training,evaluation,targets)):
        raise ValueError('repeated_partition_index')
    if set(training)&set(evaluation):raise ValueError('training_evaluation_partition_overlap')
    if not set(targets).issubset(training):raise ValueError('target_outside_training_partition')
    return training,evaluation,targets


def classify_candidate(row):
    """Keep solver candidates distinct from independent consistency support."""
    if not row.get('solverReturnedPose'):return 'pose_solver_failed'
    if row.get('fitInlierCount',0)<12:return 'weak_localization_inlier_support'
    checked=[x for x in row['scoring'] if x['reason'] in ('within_3px','exceeds_3px','negative_depth')]
    if len(checked)<12:return 'insufficient_independent_scoring_support'
    if sum(x['reason']=='within_3px' for x in checked)/len(checked)<.8:return 'independent_scoring_conflict'
    return 'independently_consistent_resection_candidate_not_product_accepted'


def landmark_groups(rec,views):
    parents={pid:pid for pid in rec.points3D}
    def root(pid):
        while parents[pid]!=pid:parents[pid]=parents[parents[pid]];pid=parents[pid]
        return pid
    for _,pts,_,pointids in views:
        valid=[j for j,pid in enumerate(pointids) if pid is not None]
        for a,j in enumerate(valid):
            for k in valid[a+1:]:
                if np.linalg.norm(pts[j]-pts[k])<=8:
                    x,y=root(pointids[j]),root(pointids[k]);parents[max(x,y)]=min(x,y)
    return {pid:root(pid) for pid in parents}


def run(source:Path,data:Path,alignment_path:Path,partitions:dict,out:Path):
    import pycolmap
    from PIL import Image
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'server-vqa'))
    from app.spatial_sift_native import extract
    from app.spatial_image_quality import sift_matches
    training,evaluation,targets=validate_partitions(partitions)
    document_bytes=(data/'document.json').read_bytes();document_digest=hashlib.sha256(document_bytes).hexdigest()
    document=json.loads(document_bytes);alignment=json.loads(alignment_path.read_text())
    allowed=set(training);evalset=set(evaluation)
    if any(i<0 or i>=len(document['keyframes']) for i in training+evaluation):raise ValueError('partition_index_out_of_range')
    out.mkdir(parents=True,exist_ok=False);start=time.monotonic();pycolmap.set_random_seed(42)
    maps=[];map_receipts=[]
    for comp in (0,1):
        map_path=source/'models'/str(comp)
        rec=pycolmap.Reconstruction(str(map_path));meta=next(x for x in alignment['components'] if x['component']==comp)
        bad=set(meta['rejectedRegistrationIndices']);views=[]
        for iid in rec.reg_image_ids():
            im=rec.images[iid];index=int(Path(im.name).stem)
            if index not in allowed or index in evalset:raise ValueError('frozen_map_contains_non_training_view')
            if index in targets and index not in bad:raise ValueError('target_already_reliably_registered_in_map')
            if index in bad:continue
            features=np.load(source/f'features-{index}.npz');pts=features['points'];desc=features['descriptors']
            pointids=[p.point3D_id if p.point3D_id in rec.points3D else None for p in im.points2D]
            if len(pts)!=len(pointids):raise ValueError('frozen_feature_point_identity_mismatch')
            views.append((index,pts,desc,pointids))
        groups=landmark_groups(rec,views);maps.append((rec,meta,views,groups))
        hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(map_path.iterdir()) if p.is_file()}
        map_receipts.append({'component':comp,'filesSHA256':hashes,'registeredViewCount':len(views),
                             'excludedRegisteredViews':sorted(bad),'landmarkCount':len(groups),
                             'wholeTrackGroupCount':len(set(groups.values()))})
        (out/f'landmark-partition-{comp}.json').write_text(json.dumps({'component':comp,
          'grouping':'training_pixel_neighbors_8px_union_complete_3D_tracks','groups':{str(pid):gid for pid,gid in groups.items()}},indent=2))
    records=[]
    for index in targets:
        frame_start=time.monotonic();f=document['keyframes'][index]
        with Image.open(data/f['imageFile']) as image:
            image=image.convert('L');native=np.array(image.size,dtype=float);image.thumbnail((1280,1280));scale=np.array(image.size)/native
            keys,desc=extract(np.asarray(image),maximum=1500);pts=(keys[:,:2]+.5)/scale-.5
            patch=np.maximum(64.,3*keys[:,2]/min(scale))
        k=np.array(f['intrinsics']).reshape(3,3,order='F')
        camera=pycolmap.Camera(model='PINHOLE',width=f['width'],height=f['height'],params=[k[0,0],k[1,1],k[0,2]+.5,k[1,2]+.5])
        component_rows=[]
        for comp,(rec,meta,views,groups) in enumerate(maps):
            component_start=time.monotonic();votes=collections.defaultdict(list)
            for frame,tpts,tdesc,tids in views:
                for qi,ti in sift_matches(desc,tdesc):
                    pid=tids[ti]
                    if pid is not None:votes[int(qi)].append((int(pid),frame))
            matches=[];ambiguous=[]
            for qi,items in votes.items():
                gs={groups[pid] for pid,_ in items}
                if len(gs)!=1:
                    ambiguous.append({'query':qi,'reason':'conflicting_training_landmark_identity','votes':items});continue
                counts=collections.Counter(pid for pid,_ in items);pid=counts.most_common(1)[0][0]
                matches.append({'query':qi,'pointId':pid,'group':groups[pid],'pixel':pts[qi].tolist(),
                                'supportingTrainFrames':sorted({frame for _,frame in items})})
            fit_raw=[m for m in matches if m['group']%2==0];scoring=[m for m in matches if m['group']%2==1]
            fit_by_group={}
            for m in sorted(fit_raw,key=lambda m:(-len(m['supportingTrainFrames']),m['query'])):fit_by_group.setdefault(m['group'],m)
            fit=list(fit_by_group.values());fitqueries=np.array([m['query'] for m in fit],dtype=int);result=None;solver_error=None
            if len(fit)>=12:
                eo=pycolmap.AbsolutePoseEstimationOptions();eo.ransac.max_error=3.;eo.estimate_focal_length=False
                ro=pycolmap.AbsolutePoseRefinementOptions();ro.refine_focal_length=False;ro.refine_extra_params=False
                try:
                    result=pycolmap.estimate_and_refine_absolute_pose(np.array([m['pixel'] for m in fit])+.5,
                      np.array([rec.points3D[m['pointId']].xyz for m in fit]),camera,eo,ro,return_covariance=True)
                except (ValueError,RuntimeError) as exc:solver_error=str(exc)
            row={'component':comp,'queryFeatureCount':len(pts),'unassociatedQueryCount':len(pts)-len(votes),
                 'fitLandmarkCount':len(fit),'fitRepeatedGroupCandidates':len(fit_raw)-len(fit),
                 'scoringCandidateCount':len(scoring),'ambiguous':ambiguous,'fitMatches':fit,
                 'solverReturnedPose':result is not None,'solverError':solver_error,'scoring':[],
                 'productAccepted':False,'cameraPosePublished':False}
            if result:
                pose=result['cam_from_world'];R=pose.rotation.matrix();t=pose.translation
                row['fitInlierCount']=int(np.asarray(result['inlier_mask']).sum())
                row['cameraFromSfMWorld']=pose.matrix().tolist();row['poseCovariance']=np.asarray(result.get('covariance',[])).tolist()
                scored_groups=set()
                for m in sorted(scoring,key=lambda m:(-len(m['supportingTrainFrames']),m['query'])):
                    score={**m,'status':'failed'};q=m['query']
                    if m['group'] in scored_groups:
                        score['reason']='repeated_scoring_landmark_group'
                    elif len(fitqueries) and np.any(np.linalg.norm(pts[fitqueries]-pts[q],axis=1)<=np.maximum(patch[fitqueries],patch[q])):
                        score['reason']='scoring_patch_overlaps_localization_patch'
                    else:
                        scored_groups.add(m['group'])
                        xyz=R@rec.points3D[m['pointId']].xyz+t
                        if xyz[2]<=0:score['reason']='negative_depth'
                        else:
                            uv=k@xyz;uv=uv[:2]/uv[2];err=float(np.linalg.norm(uv-pts[q]))
                            score.update(reprojectionErrorPixels=err,predictedPixel=uv.tolist(),
                              reason='within_3px' if err<=3 else 'exceeds_3px',status='passed' if err<=3 else 'failed')
                    row['scoring'].append(score)
            else:row['scoring']=[{**m,'status':'failed','reason':'pose_solver_failed'} for m in scoring]
            row.update(status=classify_candidate(row),scoringPassed=sum(x['status']=='passed' for x in row['scoring']),
                       scoringReasonCounts=dict(collections.Counter(x['reason'] for x in row['scoring'])),
                       elapsedSeconds=time.monotonic()-component_start)
            component_rows.append(row)
        # Both components and all target failures remain; no fit-score winner is published.
        entry={'frameIndex':index,'components':component_rows,'elapsedSeconds':time.monotonic()-frame_start,
               'originalARModified':False,'productAccepted':False}
        records.append(entry)
        print(index,[(r['status'],r['fitLandmarkCount'],r.get('fitInlierCount',0),r['scoringPassed'],r['scoringCandidateCount']) for r in component_rows],flush=True)
        (out/'progress.json').write_text(json.dumps(records,indent=2))
    result={'records':records,'targetCount':len(targets),'componentAttemptCount':len(targets)*2,
            'statusCounts':dict(collections.Counter(c['status'] for r in records for c in r['components'])),
            'trainingIndices':training,'evaluationIndices':evaluation,'targetIndices':targets,
            'partitionsSHA256':hashlib.sha256(json.dumps(partitions,sort_keys=True).encode()).hexdigest(),
            'mapReceipts':map_receipts,'inputDocumentSHA256':document_digest,
            'documentUnchangedSHA256Check':hashlib.sha256((data/'document.json').read_bytes()).hexdigest()==document_digest,
            'alignmentSHA256':hashlib.sha256(alignment_path.read_bytes()).hexdigest(),
            'scriptSHA256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'pycolmapVersion':pycolmap.__version__,'elapsedSeconds':time.monotonic()-start,
            'evaluationRGBUsed':False,'trainingMapFrozen':True,'originalARModified':False,'productAccepted':False,
            'criteria':{'minimumFitLandmarks':12,'minimumFitInliers':12,'minimumIndependentScoringCandidates':12,
                        'maximumErrorPixels':3,'minimumIndependentScorePassFraction':.8,
                        'candidateThresholdsAreNotProductAcceptance':True}}
    (out/'result.json').write_text(json.dumps(result,indent=2));return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source','data','alignment','partitions','output'):parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args();run(args.source,args.data,args.alignment,json.loads(args.partitions.read_text()),args.output)

if __name__=='__main__':main()
