"""Frozen training-only semantic evidence for SfM landmarks, not recovered walls."""
import argparse, collections, json
from pathlib import Path
import numpy as np
from PIL import Image
import pycolmap

def main():
    p=argparse.ArgumentParser()
    for key in ('sfm','masks','alignment','output'): p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args(); a.output.mkdir(parents=True,exist_ok=False)
    meta=json.loads(a.alignment.read_text()); evidence=json.loads((a.masks/'result.json').read_text())
    if evidence.get('status')!='complete' or not evidence.get('allFramesRetained'): raise ValueError('Incomplete training masks')
    requested=set(evidence['requestedIndices']); evaluation=set(meta['evaluationIndices'])
    completed=[r['frame'] for r in evidence['records']]
    if len(completed)!=len(set(completed)) or set(completed)!=requested: raise ValueError('Mask record identity mismatch')
    inputs=json.loads((a.sfm/'inputs.json').read_text())
    if set(inputs['trainingIndices'])!=requested: raise ValueError('Frozen SfM training inputs mismatch')
    if requested & evaluation: raise ValueError('Evaluation RGB leaked into semantic training inputs')
    components=[]
    for c in meta['components']:
        rec=pycolmap.Reconstruction(str(a.sfm/'models'/str(c['component']))); bad=set(c['rejectedRegistrationIndices']); votes=collections.defaultdict(list)
        for iid in rec.reg_image_ids():
            im=rec.images[iid]; idx=int(Path(im.name).stem)
            if idx in bad: continue
            if idx not in requested: raise ValueError(f'Missing training mask {idx}')
            labels=np.asarray(Image.open(a.masks/f'{idx}-native-labels.png'))
            scores=np.asarray(Image.open(a.masks/f'{idx}-native-score.tiff'))
            camera=rec.cameras[im.camera_id]
            if labels.shape!=(camera.height,camera.width) or scores.shape!=labels.shape or not np.isfinite(scores).all(): raise ValueError('Native mask dimensions or scores invalid')
            points=np.load(a.sfm/f'features-{idx}.npz')['points']
            if len(points)!=len(im.points2D): raise ValueError('Feature identity mismatch')
            stored=np.asarray([p.xy for p in im.points2D])
            if not np.allclose(stored,points+.5,rtol=0,atol=.001): raise ValueError('Feature ordering or pixel convention mismatch')
            for j,pt in enumerate(im.points2D):
                if pt.point3D_id not in rec.points3D: continue
                x,y=np.rint(points[j]).astype(int)
                if not(0<=y<labels.shape[0] and 0<=x<labels.shape[1]): raise ValueError('Feature outside native mask')
                votes[pt.point3D_id].append(dict(frame=idx,label=int(labels[y,x]),modelScore=float(scores[y,x])))
        rows=[]
        for pid,point in rec.points3D.items():
            observations=votes.get(pid,[]); labels={v['label'] for v in observations}; views={v['frame'] for v in observations}
            # Diagnostic confidence threshold, never a product acceptance gate.
            reliable=bool(len(views)>=3 and len(labels)==1 and all(v['modelScore']>=.5 for v in observations))
            rows.append(dict(pointId=int(pid),sfmPosition=point.xyz.tolist(),observations=observations,status='multi_view_semantic_candidate' if reliable else 'unknown_or_conflicting',candidateLabel=next(iter(labels)) if reliable else None,productAccepted=False))
        counts=collections.Counter(r['candidateLabel'] for r in rows if r['candidateLabel'] is not None)
        components.append(dict(component=c['component'],allLandmarks=len(rows),candidatesByLabel=dict(counts),landmarks=rows))
        print(c['component'],len(rows),dict(counts),flush=True)
    (a.output/'result.json').write_text(json.dumps(dict(components=components,trainingOnly=True,evaluationExcluded=True,metricGroundTruth=False,landmarkCountsNotIndependentGeometricSupport=True,completeSurfacesRecovered=False,productAccepted=False),indent=2))
if __name__=='__main__':main()
