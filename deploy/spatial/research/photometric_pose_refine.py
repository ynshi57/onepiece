"""Frozen-splat photometric camera refine. Diagnostic, not a published score.

Refines SE(3) of saved cameras against a frozen Brush PLY using DC colour only.
Held-out RGB may enter this diagnostic; it must not enter training. A jump in
PSNR here is pose/eval contamination, not a better 3D model. If a smear stays
after the camera moves, the Gaussians themselves are smeared.
"""
import argparse
import json
from pathlib import Path
import sys
import time

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'server-vqa'))
from app.spatial_bundle_adjustment import _exp  # noqa: E402
from app.spatial_brush import validate_model  # noqa: E402
from app.spatial_reconstruction import resize_intrinsics  # noqa: E402

SH_C0 = 0.28209479177387814
GL_TO_CV = np.diag([1.0, -1.0, -1.0, 1.0])


def load_ply(path, maximum_splats=100000):
    path = Path(path)
    count = validate_model(path, maximum_splats=maximum_splats)
    data = path.read_bytes()
    end = data.index(b'end_header\n')
    names = [line.split()[-1] for line in data[:end].decode('ascii').splitlines()
             if line.startswith('property ')]
    values = np.frombuffer(data[end + len(b'end_header\n'):], dtype='<f4').reshape(count, len(names))
    fields = {name: values[:, i] for i, name in enumerate(names)}
    quat = np.column_stack([fields[f'rot_{i}'] for i in range(4)])
    quat /= np.linalg.norm(quat, axis=1, keepdims=True).clip(1e-8)
    return dict(means=np.column_stack([fields[k] for k in 'xyz']),
                log_scales=np.column_stack([fields[f'scale_{i}'] for i in range(3)]),
                quat=quat,
                colors=np.clip(.5 + SH_C0 * np.column_stack([fields[f'f_dc_{i}'] for i in range(3)]), 0, 1),
                opacity=1 / (1 + np.exp(-np.clip(fields['opacity'], -80, 80))))


def world_to_cv_camera(transform_matrix):
    """Nerfstudio/Brush OpenGL c2w -> OpenCV world-to-camera."""
    c2w = np.asarray(transform_matrix, float)
    return np.linalg.inv(c2w @ GL_TO_CV)


def se3(xi):
    matrix = np.eye(4)
    matrix[:3, :3] = _exp(xi[:3])
    matrix[:3, 3] = xi[3:]
    return matrix


def _rotation_matrices(quat):
    w, x, y, z = quat.T
    return np.stack([
        1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y),
        2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x),
        2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y),
    ], axis=1).reshape(-1, 3, 3)


def render(model, w2c, fl_x, fl_y, cx, cy, width, height, *, limit=12000, background=0.0):
    """Depth-sorted 2D Gaussians, DC only. Black background matches Brush preview."""
    xyz = model['means'] @ w2c[:3, :3].T + w2c[:3, 3]
    depth = xyz[:, 2]
    visible = depth > 0.05
    if not visible.any():
        return np.full((height, width, 3), background, np.float32)
    uv_all = np.column_stack((fl_x * xyz[:, 0] / depth.clip(0.05) + cx,
                              fl_y * xyz[:, 1] / depth.clip(0.05) + cy))
    margin = 48
    in_view = visible & (uv_all[:, 0] > -margin) & (uv_all[:, 0] < width + margin) & (
        uv_all[:, 1] > -margin) & (uv_all[:, 1] < height + margin)
    keep = np.where(in_view)[0]
    if len(keep) > limit:
        keep = keep[np.argpartition(depth[keep], limit - 1)[:limit]]
    keep = keep[np.argsort(depth[keep])]
    xyz, depth = xyz[keep], depth[keep]
    uv = np.column_stack((fl_x * xyz[:, 0] / depth + cx, fl_y * xyz[:, 1] / depth + cy))
    rot = _rotation_matrices(model['quat'][keep])
    # A rotated anisotropic Gaussian needs R diag(scale^2) R^T.
    # Without parentheses the broadcast/matmul order forms a non-symmetric
    # matrix and can discard visible splats as negative covariance.
    covariance_diagonal = np.exp(2 * model['log_scales'][keep])[:, :, None] * np.eye(3)
    cov = rot @ covariance_diagonal @ np.swapaxes(rot, 1, 2)
    cov_cam = w2c[:3, :3] @ cov @ w2c[:3, :3].T
    j = np.zeros((len(keep), 2, 3))
    j[:, 0, 0] = fl_x / depth
    j[:, 0, 2] = -fl_x * xyz[:, 0] / depth ** 2
    j[:, 1, 1] = fl_y / depth
    j[:, 1, 2] = -fl_y * xyz[:, 1] / depth ** 2
    cov2 = j @ cov_cam @ np.swapaxes(j, 1, 2)
    cov2[:, 0, 0] += 0.3
    cov2[:, 1, 1] += 0.3
    det = cov2[:, 0, 0] * cov2[:, 1, 1] - cov2[:, 0, 1] * cov2[:, 1, 0]
    finite = (np.isfinite(det) & (det > 1e-8) & (cov2[:, 0, 0] > 0) & (cov2[:, 1, 1] > 0)
              & np.isfinite(cov2).all(axis=(1, 2)))
    xyz, depth, uv = xyz[finite], depth[finite], uv[finite]
    cov2, det = cov2[finite], det[finite]
    colors, alpha0 = model['colors'][keep][finite], model['opacity'][keep][finite]
    if not len(uv):
        return np.full((height, width, 3), background, np.float32)
    inv = np.stack([cov2[:, 1, 1], -cov2[:, 0, 1], -cov2[:, 1, 0], cov2[:, 0, 0]], axis=1).reshape(-1, 2, 2)
    inv /= det[:, None, None]
    radius = 3 * np.sqrt(np.maximum(cov2[:, 0, 0], cov2[:, 1, 1])).clip(1, 24)
    image = np.full((height, width, 3), background, np.float32)
    trans = np.ones((height, width), np.float32)
    yy, xx = np.mgrid[0:height, 0:width]
    for i in range(len(uv)):
        if i % 256 == 0 and float(trans.mean()) < 0.02:
            break
        r = int(radius[i])
        u, v = uv[i]
        x0, x1 = max(0, int(u) - r), min(width, int(u) + r + 1)
        y0, y1 = max(0, int(v) - r), min(height, int(v) + r + 1)
        if x1 <= x0 or y1 <= y0:
            continue
        dx = xx[y0:y1, x0:x1] - u
        dy = yy[y0:y1, x0:x1] - v
        power = inv[i, 0, 0] * dx * dx + (inv[i, 0, 1] + inv[i, 1, 0]) * dx * dy + inv[i, 1, 1] * dy * dy
        alpha = np.clip(alpha0[i] * np.exp(-0.5 * power), 0, 0.99)
        weight = alpha * trans[y0:y1, x0:x1]
        image[y0:y1, x0:x1] += weight[:, :, None] * colors[i] - weight[:, :, None] * background
        trans[y0:y1, x0:x1] *= (1 - alpha)
    return np.clip(image, 0, 1)


def psnr(truth, pred):
    mse = float(np.mean((truth - pred) ** 2))
    return -10 * np.log10(max(mse, 1e-12))


def load_frame(dataset, frame, resolution=None):
    with Image.open(Path(dataset) / frame['file_path']) as image:
        rgb = np.asarray(image.convert('RGB'), np.float32) / 255
        width, height = image.size
    if resolution:
        scale = min(1.0, resolution / max(width, height))
        size = (max(1, int(width * scale + .5)), max(1, int(height * scale + .5)))
        if size != (width, height):
            rgb = np.asarray(Image.fromarray(np.uint8(rgb * 255)).resize(size, Image.Resampling.BILINEAR),
                             np.float32) / 255
            intrinsics = np.array([[frame['fl_x'], 0., frame['cx']],
                                   [0., frame['fl_y'], frame['cy']], [0., 0., 1.]])
            resized = resize_intrinsics(intrinsics, width, height, *size)
            frame = dict(frame)
            frame.update(w=size[0], h=size[1], fl_x=float(resized[0, 0]),
                         fl_y=float(resized[1, 1]), cx=float(resized[0, 2]),
                         cy=float(resized[1, 2]))
            width, height = size
    return frame, rgb, world_to_cv_camera(frame['transform_matrix'])


def refine_camera(model, w2c, frame, truth, *, translation=0.04, rotation=0.03, steps=5,
                  background=0.0):
    """Coordinate descent on se(3). Translation metres, rotation radians."""
    scales = np.array([rotation, rotation, rotation, translation, translation, translation])
    best = np.zeros(6)

    def draw(camera):
        return render(model, camera, frame['fl_x'], frame['fl_y'], frame['cx'], frame['cy'],
                      int(frame['w']), int(frame['h']), background=background)

    rendered = draw(w2c)
    best_psnr = psnr(truth, rendered)
    history = [dict(step=0, psnr=best_psnr, xi=best.tolist())]
    for step in range(1, steps + 1):
        improved = False
        for axis in range(6):
            for sign in (-1.0, 1.0):
                trial = best.copy()
                trial[axis] += sign * scales[axis]
                image = draw(se3(trial) @ w2c)
                score = psnr(truth, image)
                if score > best_psnr + 0.02:
                    best, best_psnr, rendered = trial, score, image
                    improved = True
        history.append(dict(step=step, psnr=best_psnr, xi=best.tolist()))
        if not improved:
            scales *= 0.5
            if scales.max() < 0.002:
                break
    return dict(psnr=best_psnr, xi=best.tolist(), rotationRad=float(np.linalg.norm(best[:3])),
                translationMeters=float(np.linalg.norm(best[3:])), rendered=rendered, history=history)


def diagnostic_frames(train, validation, *, include_fit=False, wanted=()):
    """Fit-diagnosis exports may contain training views in transforms_val."""
    train_by_id = {int(Path(frame['file_path']).stem): frame for frame in train}
    if len(train_by_id) != len(train):
        raise ValueError('duplicate_training_frame_identity')
    selected = []; seen = set()
    for frame in validation:
        index = int(Path(frame['file_path']).stem)
        if index in seen:
            raise ValueError('duplicate_validation_frame_identity')
        seen.add(index)
        role = 'training_fit' if index in train_by_id else 'heldout'
        if role == 'heldout' or include_fit:
            selected.append((frame, role))
    if include_fit:
        missing = set(wanted) - set(train_by_id)
        if missing:
            raise ValueError('fit_frames_not_in_training_set')
        selected.extend((train_by_id[index], 'training_fit') for index in train_by_id
                        if index in wanted and index not in seen)
    return selected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', required=True, help='finished Brush run directory with dataset/ and export ply')
    parser.add_argument('--output', required=True)
    parser.add_argument('--ply')
    parser.add_argument('--resolution', type=int, default=128)
    parser.add_argument('--limit', type=int, default=8000)
    parser.add_argument('--frames', default='heldout', choices=('heldout', 'heldout+fit'))
    parser.add_argument('--fit-frames', default='155,162,169,176,183,190,197,204,211,218,225,232,237')
    args = parser.parse_args()
    run, output = Path(args.run), Path(args.output)
    if output.exists():
        raise SystemExit('output_already_exists')
    ply = Path(args.ply) if args.ply else run / f"artifacts/export_{json.loads((run/'result.json').read_text())['steps']}.ply"
    if not ply.is_file():
        ply = run / 'splats.ply'
    model = load_ply(ply)
    opacity_order = np.argsort(-model['opacity'])[: args.limit]
    model = {key: value[opacity_order] if isinstance(value, np.ndarray) else value for key, value in model.items()}
    val = json.loads((run / 'dataset/transforms_val.json').read_text())['frames']
    train = json.loads((run / 'dataset/transforms_train.json').read_text())['frames']
    wanted = {int(i) for i in args.fit_frames.split(',') if i}
    selected = diagnostic_frames(train, val, include_fit=args.frames == 'heldout+fit', wanted=wanted)
    output.mkdir(parents=True)
    rows = []
    started = time.monotonic()
    for frame, role in selected:
        calibrated, truth, w2c = load_frame(run / 'dataset', frame, resolution=args.resolution)
        before = render(model, w2c, calibrated['fl_x'], calibrated['fl_y'], calibrated['cx'], calibrated['cy'],
                        int(calibrated['w']), int(calibrated['h']))
        refined = refine_camera(model, w2c, calibrated, truth)
        stem = Path(frame['file_path']).stem
        strip = np.concatenate([truth, before, refined['rendered']], axis=1)
        Image.fromarray(np.uint8(np.clip(strip, 0, 1) * 255)).save(output / f'{stem}.png')
        row = dict(originalFrameIndex=int(stem), role=role,
                   psnrBefore=psnr(truth, before), psnrAfter=refined['psnr'],
                   deltaPsnr=refined['psnr'] - psnr(truth, before),
                   rotationRad=refined['rotationRad'], translationMeters=refined['translationMeters'],
                   xi=refined['xi'], image=f'{stem}.png',
                   usedHeldoutRGB=role == 'heldout')
        rows.append({k: v for k, v in row.items() if k != 'xi'} | {'xi': row['xi']})
        print(stem, role, 'before', round(row['psnrBefore'], 2), 'after', round(row['psnrAfter'], 2),
              'Δ', round(row['deltaPsnr'], 2), 't', round(row['translationMeters'], 3),
              'rdeg', round(np.degrees(row['rotationRad']), 2))
    result = dict(status='photometric_pose_diagnostic', productAccepted=False,
                  sourceRun=str(run.resolve()), ply=str(ply.resolve()),
                  resolution=args.resolution, gaussians=len(model['means']),
                  elapsedSeconds=time.monotonic() - started,
                  limitations=['dc_only_renderer_not_brush', 'heldout_rgb_used_for_heldout_rows',
                               'not_a_published_score', 'smear_that_survives_is_in_the_model'],
                  evaluation=rows)
    (output / 'result.json').write_text(json.dumps(result, indent=1))
    held = [r['deltaPsnr'] for r in rows if r['role'] == 'heldout']
    print('heldout mean ΔPSNR', round(float(np.mean(held)), 3) if held else None)


if __name__ == '__main__':
    main()
