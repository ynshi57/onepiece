"""Persist local VLM frame inventories as unverified, image-level observations.

No masks, model probabilities, object identities, or 3D locations are inferred.
"""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'server-vqa'))
from app.spatial_scene_memory import build_observation_set,save,load,query


def convert(document,result):
    if result['scanID']!=document['id'] or result['totalFrameCount']!=len(document['keyframes']):
        raise ValueError('source scan mismatch')
    if len(result['rows'])!=result['requestedFrameCount']:
        raise ValueError('incomplete request accounting')
    frames,observations=[],[]
    for row in result['rows']:
        if 'cropBBoxNative' in row:
            raise ValueError('cropped inventory requires crop-aware provenance; keep separate from frame memory')
        index=row['frameIndex']
        if type(index) is not int or not 0<=index<len(document['keyframes']):raise ValueError('invalid source frame index')
        source=document['keyframes'][index]
        if row['frameID']!=source['id'] or row['epoch']!=source['pose']['epoch'] or row['frameTime']!=source['time']:
            raise ValueError('source frame provenance mismatch')
        if row.get('entityID') is not None or row.get('worldPosition') is not None:
            raise ValueError('inventory cannot establish identity or 3D')
        if row['status'] not in ('processed','failed'):raise ValueError('invalid frame status')
        frame=dict(scanID=document['id'],frameID=source['id'],epoch=source['pose']['epoch'],frameTime=source['time'],
                   capturedAt=row.get('capturedAt'),status=row['status'],failure=row.get('failure') if row['status']=='failed' else None,
                   imageWidth=source['width'],imageHeight=source['height'])
        if row['status']=='processed':
            if row.get('finishReason')!='stop':raise ValueError('incomplete model response')
            for number,item in enumerate(row['objects']):
                if set(item)!={'name','evidence','uncertain'}:raise ValueError('invalid inventory item')
                observations.append({key:frame[key] for key in ('scanID','frameID','epoch','frameTime','capturedAt','imageWidth','imageHeight')} | dict(
                    observationID=f"{source['id']}:inventory:{number}",modelID=result['model'],label=item['name'],
                    labelID=None,pixelBBox=None,modelScore=None,pixelCount=None,regionKind='semanticFrameCandidate',
                    evidence=item['evidence'],uncertain=item['uncertain'],entityID=None,worldPosition=None))
        elif row.get('objects'):
            raise ValueError('failed model response must not contain usable inventory')
        frames.append(frame)
    return build_observation_set(scan_id=document['id'],total_frame_count=len(document['keyframes']),
                                 requested_frame_ids=[f['frameID'] for f in frames],frames=frames,observations=observations)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--document',required=True);parser.add_argument('--inventory',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args()
    data=convert(json.loads(Path(args.document).read_text()),json.loads(Path(args.inventory).read_text()))
    destination=Path(args.output)
    if destination.exists():raise ValueError('do not overwrite prior evidence')
    save(destination,data)
    restored=load(destination)
    if restored!=data:raise ValueError('persistence changed observations')
    queries={label:query(restored,label) for label in sorted({o['label'] for o in restored['observations']})}
    destination.with_suffix('.queries.json').write_text(json.dumps(queries,ensure_ascii=False,indent=2))
    print('Persisted and queried',len(restored['observations']),'frame-level hypotheses; identity and 3D remain unknown')
