"""Fixed failure probes at two resolutions. No registration/accuracy claims."""
import argparse
import json
from pathlib import Path
from learned_feature_pairs import TimedWorker, digest

PAIRS = [(152,155),(162,163),(165,166),(166,167),(167,169),(165,219),(214,215)]


def run(args):
    root=Path(args.data); document=json.loads((root/'document.json').read_text())
    selected = [tuple(map(int,pair.split(':'))) for pair in args.pairs.split(',')] if args.pairs else PAIRS
    if any(len(pair)!=2 or min(pair)<0 or max(pair)>=len(document['keyframes']) for pair in selected):raise ValueError('invalid_pair')
    output=Path(args.output);output.mkdir(parents=True,exist_ok=False)
    result=dict(scope='selected failure probes; counts are not accuracy',pairs=selected,
                sourceSHA256=digest(root/'document.json'),scriptSHA256=digest(__file__),evaluationUse='diagnostic only; do not reuse exposed frames as independent holdout',productAccepted=False,arms={})
    for width in (640,1280):
        folder=output/str(width);folder.mkdir();features={};pairs=[]
        worker=TimedWorker(args.resources,'feature')
        try:
            for index in sorted({i for pair in selected for i in pair}):
                frame=document['keyframes'][index];image=root/frame['imageFile']
                key=json.dumps(dict(imageSHA256=digest(image),maxSide=width,maxKeypoints=1024),sort_keys=True)
                features[index]=worker.call(dict(image=str(image),expectedSize=[frame['width'],frame['height']],maxSide=width,output=str(folder/f'{index}.npz'),cacheKey=key),60)
                (folder/'features.json').write_text(json.dumps(features,indent=2))
        finally:worker.close()
        worker=TimedWorker(args.resources,'match')
        try:
            for a,b in selected:
                if any(features[i]['status']!='extracted' for i in (a,b)):
                    row=dict(status='feature_failed')
                else:
                    row=worker.call(dict(featureA=str(folder/f'{a}.npz'),featureB=str(folder/f'{b}.npz'),frameA=document['keyframes'][a],frameB=document['keyframes'][b],output=str(folder/f'{a}-{b}.npz')),60)
                pairs.append(dict(frameA=a,frameB=b,**row))
                (folder/'pairs.json').write_text(json.dumps(pairs,indent=2))
        finally:worker.close()
        result['arms'][str(width)]=dict(features=features,pairs=pairs)
        (output/'result.json').write_text(json.dumps(result,indent=2))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',required=True);p.add_argument('--resources',required=True);p.add_argument('--output',required=True);p.add_argument('--pairs');run(p.parse_args())
