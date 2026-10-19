"""Replay a complete saved epoch without dropping bridge frames for evaluation.
All images participate: output is reconstruction/diagnosis, never holdout accuracy.
Original photos are immutable; failed attempts remain in receipts.
"""
import argparse
import json
import time
from pathlib import Path

from learned_feature_pairs import TimedWorker, digest, plan_pairs, run_map_with_deadline, verify_resources


def run(args):
    data=Path(args.data).resolve();doc=json.loads((data/'document.json').read_text())
    indices=[i for i,f in enumerate(doc['keyframes']) if f['pose']['epoch']==args.epoch]
    if len(indices)<2:raise ValueError('epoch_requires_at_least_two_photos')
    out=Path(args.output);out.mkdir(parents=True,exist_ok=False)
    (out/'features').mkdir();(out/'matches').mkdir()
    provenance=verify_resources(args.resources)
    pairs=plan_pairs(indices,[],offsets=(1,2,4,7))
    result=dict(scope='all epoch frames used; no independent evaluation or metric acceptance',
                inputDocumentSHA256=digest(data/'document.json'),provenance=provenance,
                trainingIndices=indices,evaluationExcluded=[],features=[],pairs=[],
                requestedPairs=len(pairs),featureWidth=640,maxKeypoints=1024,productAccepted=False)
    start=time.monotonic()
    def save():
        result['elapsedSeconds']=time.monotonic()-start
        (out/'result.json').write_text(json.dumps(result,indent=2))
    worker=TimedWorker(args.resources,'feature',threads=1)
    try:
        for index in indices:
            frame=doc['keyframes'][index];image=data/frame['imageFile']
            if image.resolve().parent!=data:raise ValueError('unsafe_photo_path')
            key=json.dumps(dict(imageSHA256=digest(image),provenance=provenance,maxSide=640,maxKeypoints=1024),sort_keys=True)
            path=out/'features'/f'{index}.npz'
            row=worker.call(dict(image=str(image),expectedSize=[frame['width'],frame['height']],cacheKey=key,output=str(path)),60)
            row.update(frameIndex=index,featureFile=str(path) if row['status']=='extracted' else None,imageSHA256=digest(image))
            result['features'].append(row);save()
            print(json.dumps(dict(feature=index,status=row['status'],points=row.get('keypointCount'))),flush=True)
    finally:worker.close()
    features={r['frameIndex']:r for r in result['features']}
    worker=TimedWorker(args.resources,'match',threads=1)
    try:
        for a,b in pairs:
            path=out/'matches'/f'{a}-{b}.npz'
            if any(features[i]['status']!='extracted' for i in (a,b)):
                row=dict(status='feature_unavailable')
            else:
                row=worker.call(dict(featureA=features[a]['featureFile'],featureB=features[b]['featureFile'],frameA=doc['keyframes'][a],frameB=doc['keyframes'][b],output=str(path)),60)
            row.update(frameA=a,frameB=b,matchFile=str(path) if path.exists() else None,matchSHA256=digest(path) if path.exists() else None)
            result['pairs'].append(row);save()
            if len(result['pairs'])%50==0:print(json.dumps(dict(completedPairs=len(result['pairs']),total=len(pairs))),flush=True)
    finally:worker.close()
    if all(r['status']=='matched' for r in result['pairs']):
        result['map']=run_map_with_deadline(dict(data=str(data),output=str(out/'map'),training=indices,evaluation=[],pairs=pairs,feature_cache=str(out/'features'),sift_control='',method='superpoint_lightglue'),args.map_timeout)
    else:result['map']=dict(status='matching_incomplete',productAccepted=False)
    result['allRequestedFramesRetained']=len(result['features'])==len(indices)
    result['allRequestedPairsRetained']=len(result['pairs'])==len(pairs)
    result['sourceSHA256']=digest(__file__);save()


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('data','resources','output'):p.add_argument('--'+name,required=True)
    p.add_argument('--epoch',type=int,required=True);p.add_argument('--map-timeout',type=float,default=180)
    run(p.parse_args())
