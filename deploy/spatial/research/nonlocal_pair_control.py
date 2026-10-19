"""Add bounded AR-proposed nonlocal pairs; AR is retrieval only, not truth.
Retains every baseline pair and frame. All-frame diagnostic, no holdout claim.
"""
import argparse
import json
import math
import time
from pathlib import Path

import numpy as np
from learned_feature_pairs import TimedWorker, digest, run_map_with_deadline, verify_resources


def propose(frames, indices, existing, neighbors=3):
    existing={tuple(sorted(p)) for p in existing};result=set()
    for a in indices:
        fa=frames[a];ta=np.asarray(fa['cameraToWorld']).reshape(4,4,order='F');rank=[]
        for b in indices:
            fb=frames[b];pair=tuple(sorted((a,b)))
            if a==b or pair in existing or fa['pose']['epoch']!=fb['pose']['epoch'] or abs(fa['time']-fb['time'])<10:continue
            tb=np.asarray(fb['cameraToWorld']).reshape(4,4,order='F')
            distance=float(np.linalg.norm(ta[:3,3]-tb[:3,3]))
            va,vb=ta[:3,2],tb[:3,2]
            cosine=float(np.dot(va,vb)/(np.linalg.norm(va)*np.linalg.norm(vb)))
            angle=math.acos(np.clip(cosine,-1,1))
            if distance<=1 and angle<=math.pi/4:
                rank.append((distance+.5*angle,b,pair))
        result.update(p for _,_,p in sorted(rank)[:neighbors])
    return sorted(result)


def run(args):
    data=Path(args.data).resolve();baseline=json.loads(Path(args.baseline).read_text());doc=json.loads((data/'document.json').read_text())
    if digest(data/'document.json')!=baseline['inputDocumentSHA256']:raise ValueError('source_changed')
    if verify_resources(args.resources)!=baseline['provenance']:raise ValueError('model_changed')
    indices=baseline['trainingIndices'];old=[(r['frameA'],r['frameB']) for r in baseline['pairs']]
    added=propose(doc['keyframes'],indices,old)
    out=Path(args.output);out.mkdir(parents=True,exist_ok=False);(out/'features').mkdir();(out/'matches').mkdir()
    for r in baseline['features']:
        if r['status']!='extracted':raise ValueError('baseline_feature_failure')
        (out/'features'/f"{r['frameIndex']}.npz").symlink_to(Path(r['featureFile']).resolve())
    for r in baseline['pairs']:
        if r['status']!='matched' or digest(r['matchFile'])!=r['matchSHA256']:raise ValueError('baseline_matches_changed')
        (out/'matches'/f"{r['frameA']}-{r['frameB']}.npz").symlink_to(Path(r['matchFile']).resolve())
    start=time.monotonic();result=dict(scope='all-frame diagnostic; pose is retrieval proposal only',baselineSHA256=digest(args.baseline),productAccepted=False,policy=dict(maxDistanceMeters=1,maxForwardAngleDegrees=45,minTimeGapSeconds=10,neighborsPerFrame=3),requestedAddedPairs=len(added),pairs=[])
    def save():(out/'result.json').write_text(json.dumps(result,indent=2))
    save();worker=TimedWorker(args.resources,'match',threads=1)
    try:
        for a,b in added:
            path=out/'matches'/f'{a}-{b}.npz'
            r=worker.call(dict(featureA=str(out/'features'/f'{a}.npz'),featureB=str(out/'features'/f'{b}.npz'),frameA=doc['keyframes'][a],frameB=doc['keyframes'][b],output=str(path)),60)
            r.update(frameA=a,frameB=b,matchFile=str(path),matchSHA256=digest(path) if path.exists() else None);result['pairs'].append(r);save()
            if len(result['pairs'])%25==0:print(json.dumps(dict(completed=len(result['pairs']),total=len(added))),flush=True)
    finally:worker.close()
    if all(r['status']=='matched' for r in result['pairs']):
        result['map']=run_map_with_deadline(dict(data=str(data),output=str(out/'map'),training=indices,evaluation=[],pairs=old+added,feature_cache=str(out/'features'),sift_control='',method='superpoint_lightglue'),180)
    else:result['map']=dict(status='matching_failed',productAccepted=False)
    result['elapsedSeconds']=time.monotonic()-start;save()


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for key in ('data','baseline','resources','output'):p.add_argument('--'+key,required=True)
    run(p.parse_args())
