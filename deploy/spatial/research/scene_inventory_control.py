"""Bounded local VLM inventory experiment. No boxes, identity or 3D are invented."""
import argparse
import base64
import io
import json
import sys
import time
import urllib.request
from pathlib import Path
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parent))
from semantic_model_control import gravity_turns

PROMPT = '''只根据当前照片列出清楚可见的主要物体，名称尽量具体；不把看不清的东西猜成确定物体，不补充画面外内容。墙、地面、门窗和家具设备均可列出。不要输出坐标、距离、物体数量或跨照片身份。只输出JSON：{"objects":[{"name":"物品名称","evidence":"照片中可见的外观依据","uncertain":false}]}。不确定时uncertain为true。'''


GROUNDED_PROMPT = '''观察照片，列出可见主要物体。先看形状、支撑结构和部件，再判断类别。evidence只写实际可见的颜色、形状、部件，不能重复名称当依据，不能补全被遮挡部分或猜用途。细分类不确定时使用较宽类别并标uncertain=true。不要给坐标、距离、数量、跨图身份。仅返回JSON，顶层objects数组，每项字段name和evidence为字符串、uncertain为布尔值。'''


def parse_inventory(raw):
    choice=raw['choices'][0]
    if choice.get('finish_reason')!='stop':
        raise ValueError('incomplete response')
    parsed=json.loads(choice['message']['content'])
    if set(parsed)!={'objects'} or not isinstance(parsed['objects'],list):
        raise ValueError('invalid inventory schema')
    for item in parsed['objects']:
        if set(item)!={'name','evidence','uncertain'} or not all(isinstance(item[k],str) and item[k].strip() for k in ('name','evidence')) or type(item['uncertain']) is not bool:
            raise ValueError('invalid inventory item')
    return parsed['objects']


def run(args):
    scan,out=Path(args.scan),Path(args.output)
    doc=json.loads((scan/'document.json').read_text())
    indices=list(map(int,args.indices.split(',')))
    if len(set(indices))!=len(indices) or any(i<0 or i>=len(doc['keyframes']) for i in indices):
        raise ValueError('invalid frame selection')
    out.mkdir(parents=True,exist_ok=False)
    rows=[]
    prompt=GROUNDED_PROMPT if getattr(args,'prompt_variant','baseline')=='grounded' else PROMPT
    for index in indices:
        frame=doc['keyframes'][index]
        row=dict(frameIndex=index,frameID=frame['id'],epoch=frame['pose']['epoch'],frameTime=frame['time'],capturedAt=None,status='failed',objects=[],worldPosition=None,entityID=None)
        start=time.monotonic()
        try:
            with Image.open(scan/frame['imageFile']) as source:
                image=source.convert('RGB')
            if image.size!=(frame['width'],frame['height']):raise ValueError('image dimensions mismatch')
            if getattr(args,'crop_label_root',None):
                import numpy as np
                label_path=Path(args.crop_label_root)/f'{index:05d}-labels.png'
                with Image.open(label_path) as source: labels=np.asarray(source)
                if labels.shape!=(frame['height'],frame['width']):raise ValueError('crop mask/native dimensions mismatch')
                ys,xs=np.where(labels==args.crop_label_id)
                if len(xs)==0:raise ValueError('requested learned region has no pixels')
                bbox=[int(xs.min()),int(ys.min()),int(xs.max())+1,int(ys.max())+1]
                row.update(cropBBoxNative=bbox,cropLabelID=args.crop_label_id,cropLabelSource=str(label_path),cropPixelCount=len(xs))
                image=image.crop(bbox)
            turns,_,_=gravity_turns(frame['cameraToWorld'])
            image=image.rotate(90*turns,expand=True);image.thumbnail((640,640))
            buffer=io.BytesIO();image.save(buffer,format='JPEG',quality=90)
            (out/f'{index:05d}-input.jpg').write_bytes(buffer.getvalue())
            row.update(inputImage=f'{index:05d}-input.jpg',rotationQuarterTurns=turns,inputSize=list(image.size))
            payload=dict(model='qwen2.5vl:3b',temperature=0,max_tokens=512,response_format={'type':'json_object'},messages=[{'role':'user','content':[{'type':'text','text':prompt},{'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode(buffer.getvalue()).decode()}}]}])
            request=urllib.request.Request('http://127.0.0.1:11435/v1/chat/completions',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(request,timeout=90) as response:raw=json.load(response)
            (out/f'{index:05d}-raw.json').write_text(json.dumps(raw,ensure_ascii=False,indent=2))
            objects=parse_inventory(raw)
            row.update(status='processed',objects=objects,usage=raw.get('usage'),finishReason=raw['choices'][0]['finish_reason'])
        except Exception as exc:
            row.update(status='failed',failure=type(exc).__name__+': '+str(exc))
        row['elapsedSeconds']=time.monotonic()-start;rows.append(row)
        report=dict(scanID=doc['id'],model='local-qwen2.5vl:3b',prompt=prompt,promptVariant=getattr(args,'prompt_variant','baseline'),scope='single-frame inventory hypotheses; not detection, identity, spatial memory acceptance or independent accuracy test',totalFrameCount=len(doc['keyframes']),requestedFrameCount=len(indices),unrequestedFrameCount=len(doc['keyframes'])-len(indices),rows=rows,productAccepted=False)
        (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(index,row['status'],round(row['elapsedSeconds'],2),json.dumps([o['name'] for o in row['objects']],ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scan',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--crop-label-root');parser.add_argument('--crop-label-id',type=int,default=7)
    parser.add_argument('--prompt-variant',choices=['baseline','grounded'],default='baseline')
    parser.add_argument('--indices',default='0,45,90,135,180,225')
    run(parser.parse_args())
