"""Fixed-vocabulary grounding experiment; keeps raw queries, failures and source photos."""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

# Both competing seat classes are present; vocabulary fixed across every view.
VOCABULARY = 'a chair. an armchair. a couch. a bed. an easel. a cardboard box. a table. a laptop. a monitor. a book. a backpack. a bottle. an air conditioner. a musical instrument. a door.'


def run(args):
    sys.path.insert(0,str(Path(args.runtime).resolve()))
    out=Path(args.output);out.mkdir(parents=True,exist_ok=False)
    source=Path(args.inputs)
    manifest=json.loads((source/'inputs.json').read_text())
    report=dict(source=manifest,modelPath=str(Path(args.model).resolve()),vocabulary=args.vocabulary,
                boxThreshold=.4,textThreshold=.3,rows=[],productAccepted=False,sourceIdentityVerified=False,
                scope='text-conditioned detection candidates; vocabulary is not a full scene inventory; no identity, absence or 3D established')
    def write(): (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    write()
    started=time.monotonic()
    try:
        import numpy as np
        import torch
        from PIL import Image, ImageDraw
        from transformers import AutoProcessor,AutoModelForZeroShotObjectDetection
        torch.set_num_threads(2)
        processor=AutoProcessor.from_pretrained(args.model,local_files_only=True)
        model=AutoModelForZeroShotObjectDetection.from_pretrained(args.model,local_files_only=True).eval()
        report['loadSeconds']=time.monotonic()-started
    except Exception as exc:
        report['failure']=type(exc).__name__+': '+str(exc)
        report['rows']=[dict(sourceFrame=f,status='failed',failure=report['failure'],objects=[]) for f in manifest['frames']]
        write();raise
    for frame in manifest['frames']:
        began=time.monotonic();row=dict(sourceFrame=frame,status='failed',objects=[],entityID=None,worldPosition=None)
        try:
            name=frame['inputImage'];path=source/name
            if Path(name).name!=name:raise ValueError('input must be a direct file in source directory')
            shutil.copyfile(path,out/name)
            with Image.open(path) as original:image=original.convert('RGB')
            inputs=processor(images=image,text=args.vocabulary,return_tensors='pt')
            with torch.inference_mode():outputs=model(**inputs)
            # Preserve every query before the fixed official presentation thresholds.
            np.savez_compressed(out/(Path(name).stem+'-raw.npz'),logits=outputs.logits.numpy(),pred_boxes=outputs.pred_boxes.numpy(),input_ids=inputs.input_ids.numpy())
            if torch.isnan(outputs.logits).any() or torch.isposinf(outputs.logits).any() or not torch.isfinite(outputs.pred_boxes).all():
                raise ValueError('nonfinite model output; raw queries preserved')
            decoded=processor.post_process_grounded_object_detection(outputs,inputs.input_ids,box_threshold=.4,text_threshold=.3,target_sizes=[image.size[::-1]])[0]
            draw=ImageDraw.Draw(image);candidates=[]
            for box,score,label in zip(decoded['boxes'].tolist(),decoded['scores'].tolist(),decoded['labels']):
                normalized=[v/d for v,d in zip(box,[image.width,image.height,image.width,image.height])]
                if not np.isfinite(box).all() or box[0]>box[2] or box[1]>box[3]:raise ValueError('invalid model box; raw queries preserved')
                box_status='within_input' if all(0<=v<=1 for v in normalized) else 'extends_outside_input'
                candidates.append(dict(label=label,modelScore=score,pixelBBox=normalized,bboxStatus=box_status))
                draw.rectangle(box,outline='red',width=2);draw.text((box[0],box[1]),f'{label} {score:.2f}',fill='red',stroke_width=1,stroke_fill='white')
            image.save(out/(Path(name).stem+'-detections.jpg'))
            row.update(status='processed',objects=candidates,inputSize=list(image.size),rawQueryCount=int(outputs.pred_boxes.shape[1]))
        except Exception as exc:row['failure']=type(exc).__name__+': '+str(exc)
        row['elapsedSeconds']=time.monotonic()-began;report['rows'].append(row);write()
        print(frame['frameIndex'],row['status'],round(row['elapsedSeconds'],2),[o['label'] for o in row['objects']],flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vocabulary',default=VOCABULARY)
    for key in ('inputs','model','runtime','output'):parser.add_argument('--'+key,required=True)
    run(parser.parse_args())
