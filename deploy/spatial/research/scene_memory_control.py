"""Offline RGB -> learned semantic regions -> saved/reloaded scene observations.

Research only. Regions are not instances; no 3D location or object count inferred.
Uses an existing local SegFormer cache/runtime; never installs or downloads.
"""
from __future__ import annotations
import argparse
import colorsys
import html
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'server-vqa'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.spatial_scene_memory import build_observation_set, save, load, query
from semantic_model_control import gravity_turns, stream_native


def regions(labels, scores, frame, names, model_id):
    """Keep every 4-connected class region, including single-pixel failures/noise."""
    if labels.shape != scores.shape or labels.ndim != 2 or not np.isfinite(scores).all():
        raise ValueError('invalid semantic maps')
    h, w = labels.shape
    observations = []
    for label_id in np.unique(labels):
        components, count = ndimage.label(labels == label_id)
        sizes = np.bincount(components.ravel())
        sums = np.bincount(components.ravel(), weights=scores.ravel())
        for component, box in enumerate(ndimage.find_objects(components), 1):
            if box is None:
                continue
            ys, xs = box
            observations.append({key: frame[key] for key in
                                 ('scanID', 'frameID', 'epoch', 'frameTime', 'capturedAt', 'imageWidth', 'imageHeight')} | dict(
                observationID=f"{frame['frameID']}:{int(label_id)}:{component}",
                modelID=model_id, labelID=int(label_id), label=names[int(label_id)],
                pixelBBox=[xs.start/w, ys.start/h, xs.stop/w, ys.stop/h],
                modelScore=float(sums[component]/sizes[component]), pixelCount=int(sizes[component]),
                regionKind='semanticRegionCandidate', entityID=None, worldPosition=None))
    return observations


def run(args):
    if args.runtime:
        sys.path.insert(0, str(Path(args.runtime).resolve()))
    import torch
    scan, modeldir, out = map(Path, (args.scan, args.model, args.output))
    doc = json.loads((scan/'document.json').read_text())
    indices = list(map(int, args.indices.split(',')))
    if not indices or len(set(indices)) != len(indices) or any(i < 0 or i >= len(doc['keyframes']) for i in indices):
        raise ValueError('invalid fixed frame indices')
    if not 128 <= args.resolution <= 640 or args.threads < 1:
        raise ValueError('invalid bounded inference budget')
    out.mkdir(parents=True, exist_ok=False)
    torch.set_num_threads(args.threads)
    start = time.monotonic()
    # Local weights are explicitly identified as a local cache, not a new verified release.
    identities = {name: hashlib.sha256((modeldir/name).read_bytes()).hexdigest()
                  for name in ('pytorch_model.bin', 'config.json', 'preprocessor_config.json')}
    model_id = 'local-segformer:' + identities['pytorch_model.bin']
    model = None
    setup_failure = None
    try:
        from transformers import SegformerConfig, SegformerForSemanticSegmentation, SegformerImageProcessor
        if json.loads((modeldir/'config.json').read_text()).get('model_type') != 'segformer':
            raise ValueError('expected SegFormer architecture')
        config = SegformerConfig.from_json_file(str(modeldir/'config.json'))
        model = SegformerForSemanticSegmentation(config).eval().cpu()
        model.load_state_dict(torch.load(modeldir/'pytorch_model.bin', map_location='cpu', weights_only=True), strict=True)
        processor = SegformerImageProcessor.from_pretrained(str(modeldir), local_files_only=True)
        names = {int(k): str(v) for k, v in config.id2label.items()}
    except Exception as exc:
        setup_failure = type(exc).__name__ + ': ' + str(exc)
    setup_seconds = time.monotonic()-start
    frames, observations, timing, panels = [], [], [], []
    for index in indices:
        f = doc['keyframes'][index]
        frame = dict(scanID=doc['id'], frameID=f['id'], epoch=f['pose']['epoch'], frameTime=f['time'],
                     capturedAt=None, status='failed', failure='not processed', imageWidth=f['width'], imageHeight=f['height'])
        tick = time.monotonic()
        try:
            if setup_failure:
                raise RuntimeError(setup_failure)
            with Image.open(scan/f['imageFile']) as source:
                image = source.convert('RGB')
            if image.size != (f['width'], f['height']):
                raise ValueError('source dimensions mismatch')
            turns, _, _ = gravity_turns(f['cameraToWorld'])
            working = image.copy()
            working.thumbnail((args.resolution, args.resolution))
            upright = working.rotate(90*turns, expand=True)
            pixels = processor(images=upright, return_tensors='pt')['pixel_values']
            infer_start = time.monotonic()
            with torch.inference_mode():
                logits = model(pixel_values=pixels).logits
                labels, scores = stream_native(logits, (upright.height, upright.width))
            inference_seconds = time.monotonic()-infer_start
            native_labels = Image.fromarray(labels).rotate(-90*turns, expand=True).resize(image.size, Image.Resampling.NEAREST)
            native_scores = Image.fromarray(scores.astype('float32')).rotate(-90*turns, expand=True).resize(image.size, Image.Resampling.NEAREST)
            frame.update(status='processed', failure=None)
            current = regions(np.asarray(native_labels), np.asarray(native_scores), frame, names, model_id)
            native_labels.save(out/f'{index:05d}-labels.png')
            native_scores.save(out/f'{index:05d}-model-scores.tiff')
            palette = np.array([tuple(int(v*255) for v in colorsys.hsv_to_rgb((i*.61803398875)%1, .7, .95)) for i in range(len(names))], dtype=np.uint8)
            overlay = Image.fromarray((.55*np.asarray(upright)+.45*palette[labels]).astype('uint8'))
            # All regions remain in JSON; legend summarizes classes, not object counts.
            values, sizes = np.unique(labels, return_counts=True)
            largest = sorted(zip(values, sizes), key=lambda x: -x[1])[:8]
            caption = ' / '.join(names[int(k)] for k, _ in largest)
            panel = Image.new('RGB', (upright.width*2, upright.height+60), 'white')
            panel.paste(upright, (0, 0)); panel.paste(overlay, (upright.width, 0))
            draw = ImageDraw.Draw(panel)
            draw.text((6, upright.height+4), f'Frame {index}: original | learned class regions; no instance or 3D position', fill='black')
            draw.text((6, upright.height+24), caption, fill='black')
            name = f'{index:05d}-panel.jpg'; panel.save(out/name)
            observations.extend(current)
            panels.append((index, name, caption))
            timing.append(dict(frameIndex=index, frameID=f['id'], inferenceSeconds=inference_seconds,
                               elapsedSeconds=time.monotonic()-tick, regionCandidates=len(current)))
        except Exception as exc:
            frame.update(status='failed', failure=type(exc).__name__+': '+str(exc))
            timing.append(dict(frameIndex=index, frameID=f['id'], elapsedSeconds=time.monotonic()-tick, failure=frame['failure']))
        frames.append(frame)
        print(index, frame['status'], round(timing[-1]['elapsedSeconds'], 2), flush=True)
    data = build_observation_set(scan_id=doc['id'], total_frame_count=len(doc['keyframes']),
                                requested_frame_ids=[f['frameID'] for f in frames], frames=frames, observations=observations)
    save(out/'observations.json', data)
    restored = load(out/'observations.json')
    if restored != data:
        raise ValueError('persisted observations changed')
    queries = {name: query(restored, name) for name in ('chair', 'table', 'door', 'windowpane', 'cabinet', 'wall')}
    (out/'queries.json').write_text(json.dumps(queries, ensure_ascii=False, indent=2))
    report = dict(status=('failed' if not any(f['status']=='processed' for f in frames) else
                          'complete' if all(f['status']=='processed' for f in frames) else 'complete_with_failures'),
                  totalFrameCount=len(doc['keyframes']), requestedFrameCount=len(frames), unprocessedFrameCount=len(doc['keyframes'])-len(frames),
                  modelID=model_id, localModelFiles=identities, device='CPU', setupSeconds=setup_seconds, frameTiming=timing, resolution=args.resolution,
                  scoreMeaning='uncalibrated; nearest resized from bounded segmentation map; not accuracy',
                  geometryRecovered=False, instanceIdentityRecovered=False, productAccepted=False, persistenceRoundtrip=True)
    (out/'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    page = '<meta charset="utf-8"><title>空间观察原型</title><style>body{font:17px system-ui;max-width:1100px;margin:32px auto}img{max-width:100%}</style><h1>空间观察原型</h1><p>真实模型的类别区域候选，不是物品数量或三维位置。本次请求 '+str(len(frames))+' 帧，其余未处理。错误、未知与失败全部保留。</p>'
    page += ''.join(f'<h2>照片 {i}</h2><img src="{name}" alt="原图与模型区域"><p>{html.escape(caption)}</p>' for i,name,caption in panels)
    page += '<h2>未能处理</h2>'+''.join(f'<p>{html.escape(f["frameID"])}: {html.escape(f["failure"])}</p>' for f in frames if f['failure'])
    (out/'index.html').write_text(page)
    print(json.dumps({k:report[k] for k in ('status','totalFrameCount','requestedFrameCount','unprocessedFrameCount','persistenceRoundtrip')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('scan', 'model', 'output'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--runtime')
    parser.add_argument('--indices', default='0,45,90,135,180,225')
    parser.add_argument('--resolution', type=int, default=640)
    parser.add_argument('--threads', type=int, default=2)
    run(parser.parse_args())
