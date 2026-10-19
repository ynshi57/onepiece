"""M2 pilot: link learned regions to existing multi-view 3D surface evidence.

Every feature and conflict is retained. This does not locate an object center,
recover a complete object, establish identity or validate metric accuracy.
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw
sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'server-vqa'))
from app.spatial_scene_memory import load
from semantic_model_control import gravity_turns


def native_pixel(corner_xy,map_size,native_size):
    """COLMAP corner pixels -> archived native JPEG pixel centers, including resize."""
    return np.asarray(corner_xy,dtype=float)*np.asarray(native_size)/np.asarray(map_size)-.5


def sample_label(mask,pixel):
    if not np.isfinite(pixel).all():return None
    x,y=np.floor(np.asarray(pixel)+.5).astype(int)
    return int(mask[y,x]) if 0<=x<mask.shape[1] and 0<=y<mask.shape[0] else None


def run(args):
    import pycolmap
    started=time.monotonic()
    scan,out=Path(args.scan),Path(args.output);out.mkdir(parents=True,exist_ok=False)
    doc=json.loads((scan/'document.json').read_text())
    config=json.loads(Path(args.config).read_text())
    label_id=next(int(k) for k,v in config['id2label'].items() if v==args.label)
    rec=pycolmap.Reconstruction(args.sfm)
    images={int(Path(rec.images[image_id].name).stem):rec.images[image_id]
            for image_id in rec.reg_image_ids()}
    indices=list(map(int,args.indices.split(',')))
    if args.anchor not in indices or len(set(indices))!=len(indices):raise ValueError('invalid requested views')
    masks={};feature_rows={};lookup={};view_status=[]
    roots=[Path(value) for value in args.labels]
    source_sets={root:load(root/'observations.json') for root in roots}
    config_digest=hashlib.sha256(Path(args.config).read_bytes()).hexdigest()
    for index in indices:
        frame=doc['keyframes'][index];native_size=(frame['width'],frame['height'])
        paths=[root/f'{index:05d}-labels.png' for root in roots if (root/f'{index:05d}-labels.png').exists()]
        if len(paths)!=1:raise ValueError('one explicit semantic label map per requested view required')
        source_root=paths[0].parent
        source_set=source_sets[source_root]
        if source_set['scanID']!=doc['id']:raise ValueError('semantic source scan mismatch')
        source_frame=next((row for row in source_set['frames'] if row['frameID']==frame['id']),None)
        if source_frame is None or source_frame['status']!='processed' or source_frame['epoch']!=frame['pose']['epoch'] or source_frame['frameTime']!=frame['time']:
            raise ValueError('semantic frame provenance mismatch')
        source_report=json.loads((source_root/'result.json').read_text())
        if source_report['localModelFiles']['config.json']!=config_digest:
            raise ValueError('semantic label taxonomy mismatch')
        if not any(row['frameIndex']==index and row['frameID']==frame['id'] for row in source_report['frameTiming']):
            raise ValueError('semantic label filename/frame mapping mismatch')
        with Image.open(paths[0]) as source:
            if source.size!=native_size:raise ValueError('semantic map/native image mismatch')
            masks[index]=np.asarray(source).copy()
        if index not in images:
            view_status.append(dict(frameIndex=index,status='unregistered',failure='no source SfM camera'))
            continue
        image=images[index];camera=rec.cameras[image.camera_id]
        rows=[];lookup[index]={}
        for number,p2 in enumerate(image.points2D):
            pixel=native_pixel(p2.xy,(camera.width,camera.height),native_size)
            pid=int(p2.point3D_id)
            row=dict(featureIndex=number,pixel=pixel.tolist(),modelLabelID=sample_label(masks[index],pixel),pointID=pid if pid in rec.points3D else None)
            row['geometryStatus']='triangulated_source_point' if row['pointID'] is not None else 'no_triangulated_point'
            rows.append(row)
            if row['pointID'] is not None:lookup[index].setdefault(pid,[]).append(row)
        feature_rows[index]=rows
        view_status.append(dict(frameIndex=index,status='registered',totalFeatures=len(rows),triangulatedFeatures=sum(r['pointID'] is not None for r in rows)))
    anchor_rows=feature_rows.get(args.anchor,[])
    point_ids=sorted({r['pointID'] for r in anchor_rows if r['modelLabelID']==label_id and r['pointID'] is not None})
    supports=[]
    for pid in point_ids:
        point=rec.points3D[pid];observations=[]
        for index in indices:
            for row in lookup.get(index,{}).get(pid,[]):
                observations.append(dict(frameIndex=index,**row))
        seen_views={o['frameIndex'] for o in observations}
        conflicts=[o for o in observations if o['modelLabelID']!=label_id]
        status='cross_view_class_conflict' if conflicts else 'cross_view_class_consistent' if len(seen_views)>=2 else 'single_requested_view'
        supports.append(dict(pointID=pid,xyzSfM=point.xyz.tolist(),inMapReprojectionError=float(point.error),
                             fullTrackViews=len(point.track.elements),requestedViewObservations=observations,status=status))
    report=dict(status='surface_support_candidates' if supports else 'no_supported_position',scanID=doc['id'],anchorFrameIndex=args.anchor,requestedIndices=indices,label=args.label,labelID=label_id,
                sourceMap=str(Path(args.sfm).resolve()),coordinateFrame='source SfM local frame; scale and AR transform not independently validated',
                views=view_status,allFeatureOutcomes=feature_rows,surfaceSupport=supports,
                objectCenter=None,entityID=None,worldPosition=None,metricAccuracyVerified=False,
                geometryUsedAllSourceImages=True,independentGeometryTest=False,productAccepted=False,
                sourceIdentityVerified=False,semanticArtifactIdentityVerified=False,pixelConventionVerified=False,
                provenanceLimitations=['Historical SfM inputs are not bound to original image bytes',
                                       'Pixel convention follows source metadata, not independent verification',
                                       'Semantic metadata matches; label artifact and input bytes lack execution-time binding'],
                elapsedSeconds=time.monotonic()-started)
    (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    panels=[]
    for index in indices:
        frame=doc['keyframes'][index]
        with Image.open(scan/frame['imageFile']) as source:image=source.convert('RGB')
        draw=ImageDraw.Draw(image)
        for support in supports:
            color={'cross_view_class_consistent':'#00d090','single_requested_view':'#ffb000','cross_view_class_conflict':'#f02040'}[support['status']]
            for obs in support['requestedViewObservations']:
                if obs['frameIndex']==index:
                    x,y=obs['pixel'];draw.ellipse((x-6,y-6,x+6,y+6),fill=color,outline='black')
        turns,_,_=gravity_turns(frame['cameraToWorld']);image=image.rotate(90*turns,expand=True);image.thumbnail((480,640))
        panel=Image.new('RGB',(image.width,image.height+45),'white');panel.paste(image,(0,0))
        ImageDraw.Draw(panel).text((5,image.height+5),f'{index}: 3D surface support; NOT object center',fill='black')
        panels.append(panel)
    canvas=Image.new('RGB',(sum(p.width for p in panels),max(p.height for p in panels)),'white');x=0
    for panel in panels:canvas.paste(panel,(x,0));x+=panel.width
    canvas.save(out/'support.jpg')
    print('Surface support points',len(supports),{status:sum(s['status']==status for s in supports) for status in ('cross_view_class_consistent','single_requested_view','cross_view_class_conflict')})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('scan','sfm','config','output'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--labels',nargs='+',required=True)
    parser.add_argument('--indices',default='220,223,225');parser.add_argument('--anchor',type=int,default=225);parser.add_argument('--label',default='box')
    run(parser.parse_args())
