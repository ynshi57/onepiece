"""Expose one development frame in separate diagnostic reconstruction.
Original split/cache/map are immutable; this is NOT a holdout improvement.
"""
import argparse
import json
import shutil
import numpy as np
from pathlib import Path
from learned_feature_pairs import TimedWorker,digest,run_map_with_deadline,verify_resources


def run(args):
    source=Path(args.baseline);baseline=json.loads(source.read_text());data=Path(args.data)
    if digest(data/'document.json')!=baseline['inputDocumentSHA256']:raise ValueError('document_changed')
    if args.frame not in baseline['evaluationExcluded']:raise ValueError('expected_development_frame')
    doc=json.loads((data/'document.json').read_text())
    if verify_resources(args.resources)!=baseline['provenance']:raise ValueError('model_provenance_changed')
    with np.load(args.feature,allow_pickle=False) as f:
        key=json.loads(str(f['cacheKey']))
        if key!={'imageSHA256':digest(data/doc['keyframes'][args.frame]['imageFile']),'maxSide':640,'maxKeypoints':1024}:raise ValueError('bridge_feature_contract_changed')
    out=Path(args.output);out.mkdir(parents=True,exist_ok=False);(out/'features').mkdir();(out/'matches').mkdir()
    for f in baseline['features']:
        shutil.copyfile(f['featureFile'],out/'features'/f"{f['frameIndex']}.npz")
    shutil.copyfile(args.feature,out/'features'/f'{args.frame}.npz')
    for p in baseline['pairs']:
        if digest(p['matchFile'])!=p['matchSHA256']:raise ValueError('match_cache_changed')
        shutil.copyfile(p['matchFile'],out/'matches'/f"{p['frameA']}-{p['frameB']}.npz")
    doc=json.loads((data/'document.json').read_text());worker=TimedWorker(args.resources,'match');rows=[]
    try:
        for index in baseline['trainingIndices']:
            a,b=sorted((index,args.frame))
            r=worker.call(dict(featureA=str(out/'features'/f'{a}.npz'),featureB=str(out/'features'/f'{b}.npz'),frameA=doc['keyframes'][a],frameB=doc['keyframes'][b],output=str(out/'matches'/f'{a}-{b}.npz')),60)
            rows.append(dict(frameA=a,frameB=b,**r));(out/'added-pairs.json').write_text(json.dumps(rows,indent=2))
    finally:worker.close()
    receipt=dict(scope='development-frame exposure diagnostic, never independent evaluation',exposedFrame=args.frame,baselineSHA256=digest(source),productAccepted=False,allAddedPairsRetained=len(rows)==len(baseline['trainingIndices']))
    if all(p['status']=='matched' for p in rows):
        pairs=[(p['frameA'],p['frameB']) for p in baseline['pairs']+rows]
        receipt['map']=run_map_with_deadline(dict(data=str(data),output=str(out/'map'),training=sorted(baseline['trainingIndices']+[args.frame]),evaluation=[i for i in baseline['evaluationExcluded'] if i!=args.frame],pairs=pairs,feature_cache=str(out/'features'),sift_control='',method='superpoint_lightglue'),180)
    else:receipt['map']=dict(status='matching_failed',productAccepted=False)
    (out/'result.json').write_text(json.dumps(receipt,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for key in ('baseline','data','feature','resources','output'):p.add_argument('--'+key,required=True)
    p.add_argument('--frame',type=int,required=True);run(p.parse_args())
