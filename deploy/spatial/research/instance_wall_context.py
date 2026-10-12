"""Frozen wall-label identities + GDINO/MobileSAM CPU context, not measured walls.

All original candidate identities and all selected image failures remain. A box
alone, a missed object, or a semantic label cannot certify architectural support.
"""
from __future__ import annotations
import argparse,collections,json,sys,time
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw
from learned_feature_pairs import digest
from semantic_model_control import gravity_turns

DINO_REVISION='a2bb814dd30d776dcf7e30523b00659f4f141c71'
DINO_SHA='1a2412ef99bd74bcd3c2a246fa1e48581f8889a1300c9051974741314fc042f3'
SAM_REVISION='f706ad9c4eb7f219c00d9050e46328518ffb65d2'
CAPTION='wooden easel . cardboard box . chair . wall .'

def native_point_upright(xy,turns,width,height):
    x,y=xy
    return [(x,y),(y,width-1-x),(width-1-x,height-1-y),(height-1-y,x)][turns%4]

def category(phrase):
    text=phrase.lower();categories=[]
    for name in ('easel','box','chair','wall'):
        if name in text.split():categories.append(name)
    return categories[0] if len(categories)==1 else 'ambiguous_or_unrecognized_phrase'

def point_context(evidence):
    furniture=[r for r in evidence if r['category'] in ('easel','box','chair')]
    if any(bool(r['alternativeMaskContainsPoint']) and all(r['alternativeMaskContainsPoint']) for r in furniture):return 'furniture_conflict_model_evidence'
    if any(any(r['alternativeMaskContainsPoint']) for r in furniture):return 'unknown_instance_boundary_disagreement'
    if any(r['category']=='ambiguous_or_unrecognized_phrase' and any(r['alternativeMaskContainsPoint']) for r in evidence):return 'unknown_ambiguous_detected_context'
    if any(r['category']=='wall' and bool(r['alternativeMaskContainsPoint']) and all(r['alternativeMaskContainsPoint']) for r in evidence):return 'architectural_context_candidate_not_measured'
    return 'unknown_no_positive_architectural_instance_support'

def aggregate(observations):
    if any(r['context']=='furniture_conflict_model_evidence' for r in observations):return 'furniture_conflict_model_evidence'
    views={r['frame'] for r in observations}
    if len(views)>=3 and all(r['context']=='architectural_context_candidate_not_measured' for r in observations):return 'architectural_context_candidate_not_measured'
    return 'unknown'

def check_artifacts(dino,sam):
    if digest(dino/'model.safetensors')!=DINO_SHA or (dino/'model.safetensors').stat().st_size!=689359096:raise ValueError('fixed_dino_weight_mismatch')
    for row in json.loads((dino/'config-verification.json').read_text()):
        if digest(dino/row['path'])!=row['sha256']:raise ValueError('fixed_dino_configuration_mismatch')
    receipt=json.loads((sam/'source-verification.json').read_text())
    if receipt['revision']!=SAM_REVISION:raise ValueError('fixed_sam_revision_mismatch')
    for row in receipt['files']:
        if digest(sam/row['path'])!=row['sha256']:raise ValueError('fixed_sam_source_or_weight_mismatch')
    dependency=json.loads((sam/'dependency-verification.json').read_text())
    if dependency['version']!='0.9.16':raise ValueError('fixed_timm_dependency_required')
    return receipt,dependency

def restore_shared_parameter_aliases(model,state):
    """Safetensors omits tied aliases; fill only identical Parameter objects."""
    import torch
    groups=collections.defaultdict(list)
    for name,param in model.named_parameters(remove_duplicate=False):groups[id(param)].append(name)
    filled=[]
    for names in groups.values():
        available=[name for name in names if name in state]
        if not available:continue
        first=available[0]
        if any(not torch.equal(state[first],state[name]) for name in available[1:]):raise ValueError('shared_parameter_checkpoint_conflict')
        for name in names:
            if name not in state:state[name]=state[first];filled.append(dict(alias=name,source=first,proof='identical_parameter_object'))
    model.load_state_dict(state,strict=True)
    return filled

def run(args):
    import torch,pycolmap,transformers
    from transformers import AutoProcessor,GroundingDinoConfig,GroundingDinoForObjectDetection
    from safetensors.torch import load_file
    if transformers.__version__!='4.44.2':raise ValueError('fixed_isolated_transformers_required')
    scan,source,b0,b2,dino,sam,out=map(Path,(args.scan,args.source,args.b0,args.b2,args.dino,args.sam,args.output));out.mkdir(parents=True,exist_ok=False);doc=json.loads((scan/'document.json').read_text());inputs=json.loads((source/'inputs.json').read_text());indices=[int(i) for i in args.indices.split(',')]
    if len(set(indices))!=len(indices) or not set(indices).issubset(inputs['trainingIndices']) or set(indices)&set(inputs['evaluationExcluded']):raise ValueError('training_only_unique_control_indices_required')
    original=json.loads((b0/'result.json').read_text())['components'][0]['landmarks'];strong=json.loads((b2/'result.json').read_text())['components'][0]['landmarks'];a={r['pointId']:r for r in original};b={r['pointId']:r for r in strong}
    rec=pycolmap.Reconstruction(str(source/'models'/'0'))
    if set(a)!=set(b) or set(a)!=set(rec.points3D):raise ValueError('fixed_landmark_identity_mismatch')
    ids=sorted(i for i in a if a[i]['candidateLabel']==0 or b[i]['candidateLabel']==0);landmarks=[dict(pointId=i,B0WallCandidate=a[i]['candidateLabel']==0,B2WallCandidate=b[i]['candidateLabel']==0,observations=[],context='unknown',physicalWallVerified=False) for i in ids];byid={r['pointId']:r for r in landmarks};sam_receipt,dependency=check_artifacts(dino,sam);torch.set_num_threads(1)
    sys.path.insert(0,str(sam/'python-deps'));sys.path.insert(0,str(sam));from mobile_sam import sam_model_registry,SamPredictor
    mobile=sam_model_registry['vit_t'](checkpoint=None).eval().cpu();mobile.load_state_dict(torch.load(sam/'weights'/'mobile_sam.pt',map_location='cpu',weights_only=True),strict=True);predictor=SamPredictor(mobile)
    config=GroundingDinoConfig.from_json_file(str(dino/'config.json'));config.disable_custom_kernels=True;model=GroundingDinoForObjectDetection(config).eval().cpu();shared_aliases=restore_shared_parameter_aliases(model,load_file(str(dino/'model.safetensors')));processor=AutoProcessor.from_pretrained(str(dino),local_files_only=True);processor.image_processor.size={'shortest_edge':640,'longest_edge':960}
    frames=[];start=time.monotonic();result=dict(trainingIndices=inputs['trainingIndices'],evaluationExcluded=inputs['evaluationExcluded'],requestedIndices=indices,caption=CAPTION,boxThreshold=.35,textThreshold=.25,detectorInput=dict(shortestEdge=640,longestEdge=960),CPUThreads=1,DINORevision=DINO_REVISION,DINOWeightSHA256=DINO_SHA,checkpointSharedParameterAliases=shared_aliases,MobileSAMReceipt=sam_receipt,timm=dependency,modelLicenses='official_GroundingDINO_and_MobileSAM_Apache_2_0',landmarks=landmarks,frames=frames,fixedCandidateCount=len(ids),B0CandidateCount=sum(r['B0WallCandidate'] for r in landmarks),B2CandidateCount=sum(r['B2WallCandidate'] for r in landmarks),productAccepted=False,metricGroundTruth=False,allCandidateIdentitiesRetained=True,finalHoldout=False)
    def save():(out/'result.json').write_text(json.dumps(result,indent=2))
    save();models={int(Path(im.name).stem):im for im in rec.images.values()}
    for idx in indices:
        frame_start=time.monotonic();row=dict(frame=idx,status='failed',detections=[],productAccepted=False)
        try:
            f=doc['keyframes'][idx];image=Image.open(scan/f['imageFile']).convert('RGB')
            if image.size!=(f['width'],f['height']):raise ValueError('native_dimensions_mismatch')
            turns,observable,_=gravity_turns(f['cameraToWorld']);upright=image.rotate(90*turns,expand=True);row.update(quarterTurns=turns,orientationObservable=observable);inp=processor(images=upright,text=CAPTION,return_tensors='pt');detect_start=time.monotonic()
            with torch.inference_mode():prediction=model(**inp)
            row['detectorSeconds']=time.monotonic()-detect_start;np.savez_compressed(out/f'{idx}-raw-detections.npz',logits=prediction.logits[0].numpy(),boxes=prediction.pred_boxes[0].numpy(),input_ids=inp['input_ids'][0].numpy());detected=processor.post_process_grounded_object_detection(prediction,inp['input_ids'],box_threshold=.35,text_threshold=.25,target_sizes=[(upright.height,upright.width)])[0];del prediction
            sam_start=time.monotonic();predictor.set_image(np.asarray(upright));observed=[(int(p.point3D_id),p.xy-.5) for p in models[idx].points2D if p.point3D_id in byid];evidence=collections.defaultdict(list);overlay=np.asarray(upright).copy();drawboxes=[]
            for di,(box,score,phrase) in enumerate(zip(detected['boxes'].numpy(),detected['scores'].numpy(),detected['labels'])):
                with torch.inference_mode():masks,ious,logits=predictor.predict(box=box,multimask_output=True)
                native=[np.asarray(Image.fromarray(mask.astype(np.uint8)).rotate(-90*turns,expand=True),dtype=bool) for mask in masks];kind=category(phrase);chosen=int(np.argmax(ious));maskfiles=[]
                for mi,mask in enumerate(native):
                    path=out/f'{idx}-detection-{di}-alternative-{mi}.png';Image.fromarray(mask.astype(np.uint8)*255).save(path);maskfiles.append(str(path))
                detection=dict(index=di,phrase=phrase,category=kind,boxUpright=box.tolist(),modelScore=float(score),predictedMaskIoUs=ious.tolist(),selectedByPredictedIoU=chosen,allAlternativeNativeMaskFiles=maskfiles,maskIoUsAreNotMeasuredAccuracy=True);row['detections'].append(detection)
                for pid,xy in observed:
                    x,y=np.rint(xy).astype(int)
                    if not 0<=x<image.width or not 0<=y<image.height:raise ValueError('landmark_native_pixel_outside_image')
                    evidence[pid].append(dict(detection=di,category=kind,phrase=phrase,modelScore=float(score),alternativeMaskContainsPoint=[bool(mask[y,x]) for mask in native],boxOnlyIsNotPhysicalSupport=True))
                color=np.array([230,60,60] if kind in ('easel','box','chair') else [70,130,250] if kind=='wall' else [240,190,40]);mask=masks[chosen];overlay[mask]=(.75*overlay[mask]+.25*color).astype(np.uint8);drawboxes.append((box,phrase,float(score)))
            row['samSeconds']=time.monotonic()-sam_start
            for pid,xy in observed:
                info=dict(frame=idx,nativePixel=xy.tolist(),instanceEvidence=evidence[pid],context=point_context(evidence[pid]),physicalWallVerified=False);byid[pid]['observations'].append(info)
            panel=Image.fromarray(overlay);draw=ImageDraw.Draw(panel)
            for box,phrase,score in drawboxes:draw.rectangle(tuple(box),outline='yellow',width=3);draw.text((float(box[0]),float(box[1])),f'{phrase} {score:.2f}',fill='yellow')
            panel.thumbnail((720,960));panel.save(out/f'{idx}-context-panel.png');row.update(status='complete',observedCandidateCount=len(observed),candidateContextCounts=dict(collections.Counter(point_context(evidence[pid]) for pid,_ in observed)))
        except Exception as exc:row.update(reason=str(exc),failureType=type(exc).__name__)
        row['elapsedSeconds']=time.monotonic()-frame_start;frames.append(row)
        for landmark in landmarks:landmark['context']=aggregate(landmark['observations'])
        result.update(status='running',allRequestedFramesRetained=len(frames)==len(indices),contextCounts=dict(collections.Counter(r['context'] for r in landmarks)));save();print(json.dumps({k:row.get(k) for k in ['frame','status','candidateContextCounts','detectorSeconds','samSeconds','elapsedSeconds','reason']}),flush=True)
    result.update(status='complete' if all(f['status']=='complete' for f in frames) else 'complete_with_failures',elapsedSeconds=time.monotonic()-start,allRequestedFramesRetained=len(frames)==len(indices));save()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('scan','source','b0','b2','dino','sam','output'):p.add_argument('--'+name,required=True)
    p.add_argument('--indices',default='185,187,188,226,230,158');run(p.parse_args())
if __name__=='__main__':main()
