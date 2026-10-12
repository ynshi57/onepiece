"""Isolated CPU SegFormer B2/B0 control, semantic hypotheses not wall truth.

Uses existing verified research runtime; never downloads/installs at inference.
Native class-streamed bilinear logits match official postprocessing without a
150-class native probability allocation competing with the GPU experiment.
"""
from __future__ import annotations
import argparse,hashlib,json,sys,time
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw

SPECS={'b2':dict(repo='nvidia/segformer-b2-finetuned-ade-512-512',revision='de01bae28967510f9ddd496c60a969357195400c',weight='pytorch_model.bin',size=110000905,sha256='187ca07bea003a5717c63d04ea90b07f33cd033c0ebf44b4b89fce5070d6c8f3'), 'b0':dict(repo='nvidia/segformer-b0-finetuned-ade-512-512',revision='489d5cd81a0b59fab9b7ea758d3548ebe99677da',weight='model.safetensors',sha256='6ae39addd01de6b1b8bde2cf677d43a5cd733424b8d186de3f95d1c51fee23f9')}

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def gravity_turns(c2w):
    down=np.asarray(c2w).reshape(4,4,order='F')[:3,:3].T@np.array([0.,-1.,0.]);observable=bool(np.linalg.norm(down[:2])>=.1);angle=float(np.degrees(np.arctan2(-down[0],-down[1])));return (int(round(angle/90))%4 if observable else 0),observable,down.tolist()

def stream_native(logits,size,chunk=4):
    import torch
    import torch.nn.functional as F
    if logits.ndim!=4 or logits.shape[0]!=1 or chunk<=0:raise ValueError('single_batch_logits_and_positive_chunk_required')
    if logits.device.type!='cpu' or logits.shape[1]>256 or not torch.isfinite(logits).all():raise ValueError('finite_cpu_uint8_class_logits_required')
    maximum=torch.full(size,-float('inf'));denominator=torch.zeros(size);labels=torch.zeros(size,dtype=torch.uint8)
    for begin in range(0,logits.shape[1],chunk):
        expanded=F.interpolate(logits[:,begin:begin+chunk],size=size,mode='bilinear',align_corners=False)[0];local,index=expanded.max(0);new=torch.maximum(maximum,local);denominator=denominator*torch.exp(maximum-new)+torch.exp(expanded-new[None]).sum(0);chosen=local>maximum;labels[chosen]=(index[chosen]+begin).to(torch.uint8);maximum=new
    confidence=1/denominator
    return labels.numpy(),confidence.numpy()

def verify_model(model,metadata,variant):
    spec=SPECS[variant];weight=model/spec['weight'];payload=json.loads(metadata.read_text())
    if payload['sha']!=spec['revision']:raise ValueError('model_revision_mismatch')
    if sha(weight)!=spec['sha256'] or ('size' in spec and weight.stat().st_size!=spec['size']):raise ValueError('fixed_model_digest_mismatch')
    for name in ('config.json','preprocessor_config.json'):
        b=(model/name).read_bytes();row=next(r for r in payload['siblings'] if r['rfilename']==name);actual=hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
        if actual!=row['blobId']:raise ValueError('official_configuration_gitblob_mismatch')
    return spec

def run(args):
    import torch,transformers
    from transformers import SegformerConfig,SegformerForSemanticSegmentation,SegformerImageProcessor
    from safetensors.torch import load_file
    if transformers.__version__!='4.44.2':raise ValueError('isolated_transformers_4_44_2_required')
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'server-vqa'));from app.spatial_splat_dataset import partition_epoch_frames
    scan,modeldir,out=map(Path,(args.scan,args.model,args.output));spec=verify_model(modeldir,Path(args.metadata),args.variant);out.mkdir(parents=True,exist_ok=False);doc=json.loads((scan/'document.json').read_text());train,dev=partition_epoch_frames(doc,3);training=[i for i,_ in train];evaluation=[i for i,_ in dev]
    if set(training)&set(evaluation):raise ValueError('partition_overlap')
    requested=training if args.partition=='training' else evaluation
    if args.indices:
        chosen=list(map(int,args.indices.split(',')))
        if len(set(chosen))!=len(chosen) or not set(chosen).issubset(requested):raise ValueError('indices_outside_declared_partition')
        requested=chosen
    torch.set_num_threads(args.threads);config=SegformerConfig.from_json_file(str(modeldir/'config.json'));model=SegformerForSemanticSegmentation(config).eval().cpu();state=load_file(str(modeldir/spec['weight'])) if spec['weight'].endswith('safetensors') else torch.load(modeldir/spec['weight'],map_location='cpu',weights_only=True);model.load_state_dict(state,strict=True);del state
    processor=SegformerImageProcessor.from_pretrained(str(modeldir),local_files_only=True);names={str(v):int(k) for k,v in config.id2label.items()};targets={names[n]:c for n,c in [('wall',(190,110,240)),('floor',(30,210,200)),('windowpane',(250,175,40)),('door',(70,130,250)),('mirror',(250,70,140)),('box',(255,60,60)),('painting',(60,200,60))] if n in names};records=[];begin=time.monotonic()
    result=dict(model=spec,license='NVIDIA_SegFormer_research_evaluation_noncommercial_only',sourceRuntime='transformers_4.44.2_CPU',sourceFilesSHA256={n:sha(Path(transformers.__file__).parent/'models'/'segformer'/n) for n in ('image_processing_segformer.py','modeling_segformer.py')},threads=args.threads,partition=args.partition,trainingIndices=training,evaluationExcluded=evaluation,requestedIndices=requested,records=records,productAccepted=False,geometryRecovered=False,independentlyAnnotated=False,inputDocumentSHA256=sha(scan/'document.json'))
    def save(): (out/'result.json').write_text(json.dumps(result,indent=2))
    save()
    for idx in requested:
        start=time.monotonic();f=doc['keyframes'][idx];row=dict(frame=idx,status='failed',productAccepted=False)
        try:
            with Image.open(scan/f['imageFile']) as img:image=img.convert('RGB')
            if image.size!=(f['width'],f['height']):raise ValueError('native_dimensions_mismatch')
            turns,observable,down=gravity_turns(f['cameraToWorld']);upright=image.rotate(90*turns,expand=True);official=processor(images=upright,return_tensors='pt')['pixel_values'];rgb=np.asarray(upright.resize((512,512),Image.Resampling.BILINEAR),np.float32)/255;manual=torch.from_numpy(((rgb-[.485,.456,.406])/[.229,.224,.225]).astype(np.float32).transpose(2,0,1)[None]);pre_error=float((official-manual).abs().max())
            if pre_error>1e-5:raise ValueError('official_preprocessing_mismatch')
            inference_start=time.monotonic()
            with torch.inference_mode():logits=model(pixel_values=official).logits;mask,confidence=stream_native(logits,(upright.height,upright.width))
            inference=time.monotonic()-inference_start;native=Image.fromarray(mask).rotate(-90*turns,expand=True);nativeconf=Image.fromarray(confidence,mode='F').rotate(-90*turns,expand=True)
            if native.size!=image.size or nativeconf.size!=image.size:raise ValueError('native_inverse_rotation_dimensions_mismatch')
            native.save(out/f'{idx}-native-labels.png');nativeconf.save(out/f'{idx}-native-score.tiff');color=np.zeros((*mask.shape,3),np.uint8)
            for label,c in targets.items():color[mask==label]=c
            pixels=np.asarray(upright).copy();selected=np.isin(mask,list(targets));overlay=pixels.copy();overlay[selected]=(.55*pixels[selected]+.45*color[selected]).astype(np.uint8);panel=Image.new('RGB',(768,310),'white')
            for column,p in enumerate([upright,Image.fromarray(overlay),Image.fromarray(np.where((confidence<.5)[...,None],[255,60,60],pixels).astype(np.uint8))]):p.thumbnail((256,288));panel.paste(p,(256*column,0))
            ImageDraw.Draw(panel).text((5,290),f'{idx} {args.variant}: original | model labels | uncertain; geometry NOT verified',fill='black');panel.save(out/f'{idx}-panel.png')
            row.update(status='complete',quarterTurns=turns,orientationObservable=observable,nativeARGravity=down,preprocessingMaxError=pre_error,inferenceSeconds=inference,classes={config.id2label[k]:float((mask==k).mean()) for k in targets},uncertainFraction=float((confidence<.5).mean()),imageSHA256=sha(scan/f['imageFile']))
        except Exception as exc:row['failureType']=type(exc).__name__;row['reason']=str(exc)
        row['elapsedSeconds']=time.monotonic()-start;records.append(row);result.update(status='running',allFramesRetained=len(records)==len(requested));save();print(json.dumps(row),flush=True)
    result.update(status='complete_with_failures' if any(r['status']!='complete' for r in records) else 'complete',elapsedSeconds=time.monotonic()-begin,allFramesRetained=len(records)==len(requested));save()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('scan','model','metadata','output'):p.add_argument('--'+name,required=True)
    p.add_argument('--variant',choices=SPECS,default='b2');p.add_argument('--partition',choices=['training','development'],required=True);p.add_argument('--indices');p.add_argument('--threads',type=int,default=1);a=p.parse_args()
    if a.threads<=0:raise ValueError('positive_thread_count_required')
    run(a)
if __name__=='__main__':main()
