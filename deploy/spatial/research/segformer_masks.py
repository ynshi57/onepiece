"""Research-only fixed-weight semantic masks. No physical geometry or acceptance inferred.
Requires isolated transformers 4.44.2, torch, safetensors, Pillow, numpy.
Model license must be reviewed before any product distribution.
"""
import argparse, hashlib, json, time
from pathlib import Path
import numpy as np
import torch
from PIL import Image, ImageDraw
from safetensors.torch import load_file
from transformers import SegformerConfig, SegformerForSemanticSegmentation, SegformerImageProcessor

WEIGHT_SHA = '6ae39addd01de6b1b8bde2cf677d43a5cd733424b8d186de3f95d1c51fee23f9'

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--scan', type=Path, required=True)
    p.add_argument('--model', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--indices', required=True)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    weights = a.model / 'model.safetensors'
    if hashlib.sha256(weights.read_bytes()).hexdigest() != WEIGHT_SHA:
        raise ValueError('Official fixed-version weight checksum mismatch')
    config = SegformerConfig.from_json_file(str(a.model / 'config.json'))
    model = SegformerForSemanticSegmentation(config)
    model.load_state_dict(load_file(str(weights)), strict=True)
    model.eval(); torch.set_num_threads(4)
    doc = json.loads((a.scan / 'document.json').read_text())
    names = {str(v): int(k) for k, v in config.id2label.items()}
    targets = {names[n]: c for n, c in [('wall', (190, 110, 240)), ('floor', (30, 210, 200)), ('windowpane', (250, 175, 40)), ('door', (70, 130, 250)), ('mirror', (250, 70, 140))]}
    processor = SegformerImageProcessor.from_pretrained(str(a.model), local_files_only=True)
    requested = list(map(int, a.indices.split(',')))
    if len(requested) != len(set(requested)):
        raise ValueError('Repeated requested indices')
    rows = []
    for idx in requested:
        f = doc['keyframes'][idx]
        image = Image.open(a.scan / f['imageFile']).convert('RGB')
        # Portrait camera capture: world gravity projected into native image axes.
        rotation = np.array(f['cameraToWorld']).reshape(4, 4, order='F')[:3, :3]
        down = rotation.T @ np.array([0., -1., 0.])
        angle = np.degrees(np.arctan2(-down[0], -down[1]))
        # PIL positive angle rotates counterclockwise. Preserve all pixels with expand.
        observable = bool(np.linalg.norm(down[:2]) >= .1)
        turns = int(round(angle / 90)) % 4 if observable else 0
        upright = image.rotate(90 * turns, expand=True)
        rgb = np.asarray(upright.resize((512, 512), Image.Resampling.BILINEAR), dtype=np.float32) / 255
        inp = torch.from_numpy(((rgb - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]).astype(np.float32).transpose(2, 0, 1)[None])
        official = processor(images=upright, return_tensors='pt')['pixel_values']
        preprocessing_error = float((official - inp).abs().max())
        if preprocessing_error > 1e-5:
            raise ValueError(f'Official preprocessing mismatch: {preprocessing_error}')
        started = time.monotonic()
        with torch.inference_mode():
            logits = model(pixel_values=inp).logits
            logits = torch.nn.functional.interpolate(logits, size=(upright.height, upright.width), mode='bilinear', align_corners=False)
            probs = logits.softmax(1); confidence, labels = probs.max(1)
        elapsed = time.monotonic() - started
        mask = labels[0].numpy().astype(np.uint8); conf = confidence[0].numpy()
        Image.fromarray(mask).rotate(-90 * turns, expand=True).save(a.output / f'{idx}-native-labels.png')
        Image.fromarray(conf.astype(np.float32), mode='F').rotate(-90 * turns, expand=True).save(a.output / f'{idx}-native-score.tiff')
        colored = np.zeros((*mask.shape, 3), np.uint8)
        for label, color in targets.items(): colored[mask == label] = color
        overlay = np.asarray(upright).copy(); chosen = np.isin(mask, list(targets))
        overlay[chosen] = (.55 * overlay[chosen] + .45 * colored[chosen]).astype(np.uint8)
        panel = Image.new('RGB', (768, 310), 'white')
        for col, im in enumerate([upright, Image.fromarray(overlay), Image.fromarray(np.where((conf < .5)[..., None], [255, 60, 60], np.asarray(upright)).astype(np.uint8))]):
            im.thumbnail((256, 288)); panel.paste(im, (256 * col, 0))
        ImageDraw.Draw(panel).text((5, 290), f'{idx} original | semantic candidates | red: confidence<0.5; {elapsed:.2f}s', fill='black')
        panel.save(a.output / f'{idx}-panel.png')
        rows.append(dict(frame=idx, quarterTurns=turns, orientationStatus='gravity_observed' if observable else 'orientation_unobservable_native_fallback', preprocessingMaxError=preprocessing_error, inferenceSeconds=elapsed, classes={config.id2label[k]: float((mask == k).mean()) for k in targets}, uncertainFraction=float((conf < .5).mean()), productAccepted=False))
        print(rows[-1], flush=True)
        (a.output / 'result.json').write_text(json.dumps(dict(revision='489d5cd81a0b59fab9b7ea758d3548ebe99677da', weightSHA256=WEIGHT_SHA, records=rows, requestedIndices=requested, status='complete' if len(rows)==len(requested) else 'running', allFramesRetained=len(rows)==len(requested), geometryRecovered=False, independentlyAnnotated=False, productAccepted=False), indent=2))
if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        import sys
        if '--output' in sys.argv and not isinstance(error, FileExistsError):
            destination = Path(sys.argv[sys.argv.index('--output') + 1]) / 'result.json'
            if destination.parent.exists():
                state = json.loads(destination.read_text()) if destination.exists() else {}
                requested = list(map(int, sys.argv[sys.argv.index('--indices') + 1].split(','))) if '--indices' in sys.argv else []
                completed = {r['frame'] for r in state.get('records', [])}
                state.update(status='failed', allFramesRetained=False, productAccepted=False, requestedIndices=requested, failedOrUnprocessedIndices=[i for i in requested if i not in completed], error=str(error))
                destination.write_text(json.dumps(state, indent=2))
        raise
