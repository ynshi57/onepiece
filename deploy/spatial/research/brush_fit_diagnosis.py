"""Re-run an existing Brush experiment with one changed variable and score training fit.

Reuses the source run's exported dataset (same images, poses, seed points) so the
only differences are the explicit CLI overrides. Training frames listed in
``--fit-frames`` are rendered alongside the held-out frames; they are still part of
training, so their scores measure fit, not generalization.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'server-vqa'))
from app.spatial_brush import edge_metrics, expected_render_size, validate_model, verify_binary_receipt  # noqa: E402


def score(dataset, artifacts, steps, resolution, frames, role):
    rows = []
    for frame in frames:
        stem = Path(frame['file_path']).stem
        prediction = artifacts / f'eval_{steps}' / f'{stem}.png'
        if not prediction.is_file():
            raise RuntimeError('render_missing_' + stem)
        with Image.open(prediction) as image:
            predicted = np.asarray(image.convert('RGB'), dtype=np.float32) / 255
            size = image.size
        with Image.open(dataset / frame['file_path']) as image:
            if size != expected_render_size(image.size, resolution):
                raise RuntimeError('render_size_invalid_' + stem)
            truth = np.asarray(image.convert('RGB').resize(size, Image.Resampling.LANCZOS), dtype=np.float32) / 255
        mse = float(np.mean((truth - predicted) ** 2))
        rows.append(dict(originalFrameIndex=int(stem), role=role, mse=mse,
                         psnr=float(-10 * np.log10(max(mse, 1e-12))), **edge_metrics(truth, predicted)))
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True, help='finished spatial_brush run directory')
    parser.add_argument('--output', required=True)
    parser.add_argument('--binary', required=True)
    parser.add_argument('--fit-frames', default='', help='comma separated training frame indices to also render')
    parser.add_argument('--steps', type=int)
    parser.add_argument('--max-splats', type=int)
    parser.add_argument('--growth-stop-iter', type=int)
    parser.add_argument('--refine-every', type=int)
    args = parser.parse_args()

    source, output = Path(args.source), Path(args.output)
    if output.exists():
        raise SystemExit('output_already_exists')
    receipt = verify_binary_receipt(args.binary)
    control = json.loads((source / 'result.json').read_text())
    if control.get('status') != 'trained_official_experiment':
        raise SystemExit('source_run_not_finished')
    steps = args.steps or control['steps']
    max_splats = args.max_splats or control['maxSplats']
    resolution = control['resolution']

    dataset = output / 'dataset'
    dataset.mkdir(parents=True)
    (dataset / 'images').symlink_to((source / 'dataset/images').resolve())
    for name in ('transforms_train.json', 'training-seed.ply'):
        (dataset / name).write_bytes((source / 'dataset' / name).read_bytes())
    train = json.loads((dataset / 'transforms_train.json').read_text())
    heldout = json.loads((source / 'dataset/transforms_val.json').read_text())
    wanted = {int(i) for i in args.fit_frames.split(',') if i}
    fit = [f for f in train['frames'] if int(Path(f['file_path']).stem) in wanted]
    if len(fit) != len(wanted):
        raise SystemExit('fit_frames_not_in_training_set')
    (dataset / 'transforms_val.json').write_text(json.dumps({**heldout, 'frames': heldout['frames'] + fit}))

    artifacts = output / 'artifacts'
    artifacts.mkdir()
    interval = max(10, steps // 2)
    command = [args.binary, str(dataset.resolve()), '--total-steps', str(steps), '--max-resolution', str(resolution),
               '--max-splats', str(max_splats), '--sh-degree', str(control['shDegree']),
               '--growth-select-fraction', str(control['growthFraction']),
               '--scale-loss-weight', str(control['scaleLossWeight']), '--seed', str(control['seed']),
               '--eval-every', str(interval), '--eval-save-to-disk',
               '--export-every', str(interval), '--export-path', str(artifacts.resolve())]
    overrides = {}
    for flag, value in (('--growth-stop-iter', args.growth_stop_iter), ('--refine-every', args.refine_every)):
        if value is not None:
            command += [flag, str(value)]
            overrides[flag] = value
    if steps != control['steps']:
        overrides['--total-steps'] = steps
    if max_splats != control['maxSplats']:
        overrides['--max-splats'] = max_splats

    result = dict(status='training', productAccepted=False, sourceRun=str(source.resolve()),
                  binarySHA256=receipt['binarySHA256'], command=command, overrides=overrides,
                  fitFrames=sorted(wanted), poseSource=control['poseSource'])
    (output / 'result.json').write_text(json.dumps(result, indent=1))
    started = time.monotonic()
    with (output / 'trainer.log').open('w') as log:
        process = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, cwd=output, timeout=4 * 3600)
    result['elapsedSeconds'] = time.monotonic() - started
    if process.returncode:
        result.update(status='failed', reason=f'brush_trainer_exit_{process.returncode}')
        (output / 'result.json').write_text(json.dumps(result, indent=1))
        raise SystemExit(result['reason'])
    result['gaussians'] = validate_model(artifacts / f'export_{steps}.ply', maximum_splats=max_splats,
                                         sh_degree=control['shDegree'])
    result['evaluation'] = (score(dataset, artifacts, steps, resolution, heldout['frames'], 'heldout')
                            + score(dataset, artifacts, steps, resolution, fit, 'training_fit'))
    result['status'] = 'trained_diagnostic_experiment'
    (output / 'result.json').write_text(json.dumps(result, indent=1))
    for role in ('heldout', 'training_fit'):
        values = [r['psnr'] for r in result['evaluation'] if r['role'] == role]
        if values:
            print(role, len(values), 'mean PSNR', round(float(np.mean(values)), 3))
    print('gaussians', result['gaussians'], 'seconds', round(result['elapsedSeconds']))


if __name__ == '__main__':
    main()
