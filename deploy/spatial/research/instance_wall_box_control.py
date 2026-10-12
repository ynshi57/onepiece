"""Same frozen GDINO boxes, MobileSAM single-box vs multimask context control.

No detector rerun, no score threshold changes, no candidate removal. Model
agreement is only a furniture/architectural-context hypothesis, not physical GT.
"""
from __future__ import annotations
import argparse,collections,copy,json,sys,time
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw
from learned_feature_pairs import digest
from instance_wall_context import aggregate,native_point_upright,SAM_REVISION

def box_context(evidence):
    furniture=[e for e in evidence if e['category'] in ('easel','box','chair')]
    if any(e['singleBoxMaskContainsPoint'] and e['alternativeMaskContainsPoint'][e['selectedByPredictedIoU']] for e in furniture):return 'furniture_conflict_model_evidence'
    if any(e['singleBoxMaskContainsPoint'] or any(e['alternativeMaskContainsPoint']) for e in furniture):return 'unknown_instance_boundary_disagreement'
    if any(e['category']=='ambiguous_or_unrecognized_phrase' and (e['singleBoxMaskContainsPoint'] or any(e['alternativeMaskContainsPoint'])) for e in evidence):return 'unknown_ambiguous_detected_context'
    if any(e['category']=='wall' and e['singleBoxMaskContainsPoint'] and bool(e['alternativeMaskContainsPoint']) and all(e['alternativeMaskContainsPoint']) for e in evidence):return 'architectural_context_candidate_not_measured'
    return 'unknown_no_positive_architectural_instance_support'

def run(args):
    import torch
    source,scan,sam,out=map(Path,(args.source,args.scan,args.sam,args.output));out.mkdir(parents=True,exist_ok=False);original=json.loads((source/'result.json').read_text());result=copy.deepcopy(original);doc=json.loads((scan/'document.json').read_text());receipt=json.loads((sam/'source-verification.json').read_text())
    if receipt['revision']!=SAM_REVISION:raise ValueError('source_revision_mismatch')
    for row in receipt['files']:
        if digest(sam/row['path'])!=row['sha256']:raise ValueError('source_or_weight_digest_mismatch')
    sys.path.insert(0,str(sam/'python-deps'));sys.path.insert(0,str(sam));from mobile_sam import sam_model_registry,SamPredictor
    torch.set_num_threads(1);model=sam_model_registry['vit_t'](checkpoint=None).eval().cpu();model.load_state_dict(torch.load(sam/'weights'/'mobile_sam.pt',map_location='cpu',weights_only=True),strict=True);predictor=SamPredictor(model);start=time.monotonic();controls=[]
    for frame in result['frames']:
        idx=frame['frame'];started=time.monotonic();f=doc['keyframes'][idx];image=Image.open(scan/f['imageFile']).convert('RGB');turns=frame['quarterTurns'];upright=image.rotate(90*turns,expand=True);predictor.set_image(np.asarray(upright));members={}
        for detection in frame['detections']:
            with torch.inference_mode():masks,quality,_=predictor.predict(box=np.asarray(detection['boxUpright']),multimask_output=False)
            if len(masks)!=1:raise ValueError('single_mask_mode_did_not_return_single_mask')
            native=np.asarray(Image.fromarray(masks[0].astype(np.uint8)).rotate(-90*turns,expand=True),dtype=bool);path=out/f'{idx}-det-{detection["index"]}-single-box-mask.png';Image.fromarray(native.astype(np.uint8)*255).save(path);detection.update(singleBoxMaskFile=str(path),singleBoxPredictedIoU=float(quality[0]),singleBoxPredictedIoUIsNotMeasured=True);members[detection['index']]=native
        for landmark in result['landmarks']:
            for obs in landmark['observations']:
                if obs['frame']!=idx:continue
                x,y=np.rint(obs['nativePixel']).astype(int);obs['originalMultimaskContext']=obs['context']
                for e in obs['instanceEvidence']:
                    detection=frame['detections'][e['detection']];e['singleBoxMaskContainsPoint']=bool(members[e['detection']][y,x]);e['selectedByPredictedIoU']=detection['selectedByPredictedIoU']
                obs['context']=box_context(obs['instanceEvidence'])
        controls.append(dict(frame=idx,elapsedSeconds=time.monotonic()-started,status='complete'));print(json.dumps(controls[-1]),flush=True)
    for landmark in result['landmarks']:landmark['originalMultimaskAggregate']=landmark['context'];landmark['context']=aggregate(landmark['observations'])
    result.update(sourceResultSHA256=digest(source/'result.json'),control='single_box_mask_and_model_selected_multimask_agreement_no_threshold_change',contextCounts=dict(collections.Counter(l['context'] for l in result['landmarks'])),singleBoxControls=controls,singleBoxControlElapsedSeconds=time.monotonic()-start);(out/'result.json').write_text(json.dumps(result,indent=2))
    canvas=Image.new('RGB',(1152,1110),'#111')
    for n,frame in enumerate(result['frames']):
        idx=frame['frame'];f=doc['keyframes'][idx];image=Image.open(scan/f['imageFile']).convert('RGB');w,h=image.size;im=image.rotate(90*frame['quarterTurns'],expand=True);scale=384/im.width;im=im.resize((384,round(im.height*scale)));draw=ImageDraw.Draw(im)
        for landmark in result['landmarks']:
            for obs in landmark['observations']:
                if obs['frame']!=idx:continue
                x,y=native_point_upright(obs['nativePixel'],frame['quarterTurns'],w,h);x*=scale;y*=scale;context=obs['context'];color=(255,60,60) if context=='furniture_conflict_model_evidence' else (60,180,255) if context=='architectural_context_candidate_not_measured' else (255,210,60) if 'boundary' in context or 'ambiguous' in context else (170,170,170);draw.ellipse((x-3,y-3,x+3,y+3),outline=color,width=2)
        col=n%3;row=n//3;canvas.paste(im,(col*384,row*555));d=ImageDraw.Draw(canvas);d.text((col*384+5,row*555+515),f'{idx} red:furniture / yellow:boundary / gray:unknown',fill='white');d.text((col*384+5,row*555+531),'blue:model wall context; NO measured physical wall',fill='white')
    canvas.save(out/'candidate-context-montage.png');print(result['contextCounts'],flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('source','scan','sam','output'):p.add_argument('--'+n,required=True)
    run(p.parse_args())
if __name__=='__main__':main()
