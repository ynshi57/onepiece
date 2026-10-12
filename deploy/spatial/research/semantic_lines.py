"""All source LSD segments retained, with training-only semantic boundary evidence."""
import argparse,json
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw
from app.spatial_line_structure import _native_lines

def main():
 p=argparse.ArgumentParser()
 for k in ('scan','masks','output'):p.add_argument('--'+k,type=Path,required=True)
 a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
 masks=json.loads((a.masks/'result.json').read_text());doc=json.loads((a.scan/'document.json').read_text())
 if masks.get('status')!='complete':raise ValueError('Incomplete masks')
 records=[]
 for idx in masks['requestedIndices']:
  f=doc['keyframes'][idx];rgb=Image.open(a.scan/f['imageFile']).convert('RGB');labels=np.asarray(Image.open(a.masks/f'{idx}-native-labels.png'));score=np.asarray(Image.open(a.masks/f'{idx}-native-score.tiff'))
  if labels.shape!=(rgb.height,rgb.width) or score.shape!=labels.shape:raise ValueError('Native dimensions mismatch')
  small=rgb.copy();small.thumbnail((640,640));scale=np.array(small.size)/np.array(rgb.size);segments=_native_lines(np.asarray(small.convert('L')),4096);rows=[];draw=ImageDraw.Draw(small)
  for line in segments:
   start,end=line[:2],line[2:];length=float(np.linalg.norm(end-start));direction=(end-start)/max(length,1e-9);normal=np.array([-direction[1],direction[0]]);samples=[]
   for t in np.linspace(.1,.9,9):
    pair=[]
    for side in (-1,1):
     xy=((start*(1-t)+end*t+side*6*normal)+.5)/scale-.5;x,y=np.rint(xy).astype(int)
     pair.append(None if not(0<=y<labels.shape[0] and 0<=x<labels.shape[1]) else dict(label=int(labels[y,x]),modelScore=float(score[y,x])))
    samples.append(pair)
   multiscale=[]
   for radius in (2,4,8,16):
    hist=[{},{}]; center={}; low=[0,0]; totals=[0,0]
    for t in np.linspace(.1,.9,17):
     origin=start*(1-t)+end*t
     for side_index,side in enumerate((-1,1)):
      for band in (-1,0,1):
       xy=(origin+side*(radius+band)*normal+.5)/scale-.5;x,y=np.rint(xy).astype(int)
       if 0<=y<labels.shape[0] and 0<=x<labels.shape[1]:
        k=int(labels[y,x]);hist[side_index][k]=hist[side_index].get(k,0)+1;low[side_index]+=int(score[y,x]<.5);totals[side_index]+=1
     for band in (-1,0,1):
      xy=(origin+band*normal+.5)/scale-.5;x,y=np.rint(xy).astype(int)
      if 0<=y<labels.shape[0] and 0<=x<labels.shape[1]:
       k=int(labels[y,x]);center[k]=center.get(k,0)+1
    dominant=[max(h,key=h.get) if h else None for h in hist]
    multiscale.append(dict(radiusWorkingPixels=radius,sideHistograms=hist,sideTotals=totals,lowScoreCounts=low,centerHistogram=center,dominantLabels=dominant))
   relations=[set(m['dominantLabels'])=={0,3} for m in multiscale]
   third=sum(v for k,v in multiscale[0]['centerHistogram'].items() if k not in (0,3)); center_total=sum(multiscale[0]['centerHistogram'].values())
   diagnosis='third_class_occluded' if center_total and third/center_total>.2 else 'multiscale_conflicting' if any(relations) and not all(relations) else 'side_consistent_not_geometry_verified' if all(relations) else 'unknown'
   support=sum(bool(l and r and l['modelScore']>=.5 and r['modelScore']>=.5 and {l['label'],r['label']}=={0,3}) for l,r in samples)
   candidate=bool(length>=60 and support>=6)
   rows.append(dict(nativeSegment=(((line.reshape(2,2)+.5)/scale)-.5).tolist(),lengthWorkingPixels=length,sideSamples=samples,wallFloorVotes=support,multiscale=multiscale,diagnosticStatus=diagnosis,status='wall_floor_boundary_candidate' if candidate else 'unconfirmed',productAccepted=False))
   if candidate:draw.line(tuple(map(float,line)),fill=(255,170,0),width=3)
  small.rotate(90*next(r['quarterTurns'] for r in masks['records'] if r['frame']==idx),expand=True).save(a.output/f'{idx}-lines.png')
  records.append(dict(frame=idx,allSegments=len(rows),boundaryCandidates=sum(r['status']=='wall_floor_boundary_candidate' for r in rows),segments=rows))
 (a.output/'result.json').write_text(json.dumps(dict(records=records,allSegmentsRetained=True,geometryRecovered=False,productAccepted=False),indent=2));print('frames',len(records),'segments',sum(r['allSegments'] for r in records),'candidates',sum(r['boundaryCandidates'] for r in records))
if __name__=='__main__':main()
