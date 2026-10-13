"""Trace missing cameras through frozen matches to map support; never edit map.
Optional PnP uses existing training evidence only, never acceptance.
"""
import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np


def unique_support(candidates):
    """Reject ambiguous feature->landmark and landmark->feature assignments."""
    single = {q: next(iter(ids)) for q, ids in candidates.items() if len(ids) == 1}
    reverse = defaultdict(list)
    for q, point in single.items():
        reverse[point].append(q)
    return {q: point for q, point in single.items() if len(reverse[point]) == 1}


def run(args):
    import pycolmap
    source = Path(args.pairs)
    data = json.loads(source.read_text())
    reconstruction = pycolmap.Reconstruction(args.map)
    registered = {int(Path(i.name).stem): i for i in reconstruction.images.values()}
    features = {f['frameIndex']: f for f in data['features']}
    if len(features) != len(data['features']):
        raise ValueError('duplicate feature identity')
    for index, image in registered.items():
        with np.load(features[index]['featureFile'], allow_pickle=False) as loaded:
            native = loaded['points'] + .5
        actual = np.array([point.xy for point in image.points2D])
        if native.shape != actual.shape or not np.allclose(native, actual, atol=1e-6, rtol=0):
            raise ValueError('map_feature_identity_changed')
    document = json.loads(Path(args.document).read_text()) if args.document else None
    if document and hashlib.sha256(Path(args.document).read_bytes()).hexdigest() != data["inputDocumentSHA256"]:
        raise ValueError("source_document_changed")
    rows = []
    for index in data['trainingIndices']:
        if index in registered:
            continue
        candidates = defaultdict(set)
        linked_pairs = 0
        raw_matches = 0
        verified_matches = 0
        for pair in data['pairs']:
            a, b = pair['frameA'], pair['frameB']
            if index not in (a, b):
                continue
            other = b if a == index else a
            if other not in registered:
                continue
            raw_matches += pair.get('matchCount', 0)
            matches = pair['geometry'].get('inlierMatches', [])
            verified_matches += len(matches)
            linked_pairs += bool(matches)
            image = registered[other]
            for qa, qb in matches:
                query, reference = (qa, qb) if a == index else (qb, qa)
                p = image.points2D[reference]
                if p.has_point3D():
                    candidates[query].add(int(p.point3D_id))
        support = unique_support(candidates)
        row = dict(frameIndex=index, keypoints=features[index]['keypointCount'],
                   rawMatchesToRegistered=raw_matches, verifiedMatchesToRegistered=verified_matches,
                   verifiedNeighborCount=linked_pairs, mappedQueryFeatures=len(candidates),
                   unambiguousLandmarks=len(support), ambiguousOrDuplicateFeatures=len(candidates)-len(support),
                   status='insufficient_2d_3d_support', productAccepted=False)
        # This report deliberately does not lower mapper gates or insert PnP poses.
        row['support'] = [{'feature': q, 'landmark': p} for q, p in sorted(support.items())]
        if support:
            row['status'] = 'has_2d_3d_support_requires_pose_validation'
        if document and len(support) >= 4:
            frame = document['keyframes'][index]
            kk = frame['intrinsics']
            camera = pycolmap.Camera(model='PINHOLE', width=frame['width'], height=frame['height'],
                                     params=[kk[0], kk[4], kk[6]+.5, kk[7]+.5])
            with np.load(features[index]['featureFile'], allow_pickle=False) as loaded:
                points = loaded['points']
            xy = np.array([points[q]+.5 for q in sorted(support)])
            xyz = np.array([reconstruction.points3D[support[q]].xyz for q in sorted(support)])
            options = pycolmap.AbsolutePoseEstimationOptions()
            options.ransac.max_error = 3.
            pycolmap.set_random_seed(42)
            pose = pycolmap.estimate_and_refine_absolute_pose(xy, xyz, camera, options)
            row['pnp'] = dict(found=pose is not None, scope='in-sample diagnostic only', maxErrorPixels=3.)
            if pose:
                projected = camera.img_from_cam(pose['cam_from_world'] * xyz)
                residuals = np.linalg.norm(projected-xy, axis=1)
                row['pnp'].update(inliers=int(pose['num_inliers']), residualsPixels=residuals.tolist(),
                                  medianAllPixels=float(np.median(residuals)))
        rows.append(row)
    result = dict(scope='training-only evidence audit; no recovered camera or metric acceptance',
                  sourceSHA256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  mapSHA256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(args.map).glob('*.bin')},
                  trainingCount=len(data['trainingIndices']), registeredCount=len(registered),
                  missingCount=len(rows), missing=rows, productAccepted=False)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('x') as f:
        json.dump(result, f, indent=2)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--pairs', required=True)
    parser.add_argument('--document')
    parser.add_argument('--map', required=True)
    parser.add_argument('--output', required=True)
    run(parser.parse_args())
