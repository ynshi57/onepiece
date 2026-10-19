"""Frozen adjacent-pair diagnostic, not pose correction or metric ground truth.

Compare recorded-camera epipolar geometry with image-only F/H fitted on checkerboard
cells and scored on the complementary cells. All requested pairs and raw matches
remain in the result. F/H are diagnostic: planar scenes and rotation are degenerate
for recovering translation. Development frames never enter this experiment.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'server-vqa'))
from app.spatial_reconstruction import camera


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cache_matches_source(cache_key, expected_sha):
    """Read both legacy SHA-prefixed and structured source identities."""
    value=str(cache_key)
    if value.startswith('{'):
        try:
            parsed=json.loads(value)
        except (ValueError,TypeError):
            return False
        return isinstance(parsed,dict) and parsed.get('imageSHA256')==expected_sha
    return len(expected_sha)==64 and value[:64]==expected_sha


def skew(v):
    x, y, z = v
    return np.array([[0., -z, y], [z, 0., -x], [-y, x, 0.]])


def recorded_geometry(a, b):
    relative = b.world_to_camera @ np.linalg.inv(a.world_to_camera)
    r, t = relative[:3, :3], relative[:3, 3]
    baseline = float(np.linalg.norm(t))
    f = None if baseline < 1e-6 else np.linalg.inv(b.k).T @ skew(t / baseline) @ r @ np.linalg.inv(a.k)
    return f, b.k @ r @ np.linalg.inv(a.k), baseline


def sampson_pixels(f, a, b):
    x, y = np.c_[a, np.ones(len(a))], np.c_[b, np.ones(len(b))]
    fx, fy = x @ f.T, y @ f
    den = fx[:, 0]**2 + fx[:, 1]**2 + fy[:, 0]**2 + fy[:, 1]**2
    return np.divide(np.abs(np.sum(y * fx, axis=1)), np.sqrt(den),
                     out=np.full(len(a), np.nan), where=den > 1e-24)


def transfer_pixels(h, a, b):
    q = np.c_[a, np.ones(len(a))] @ h.T
    result = np.full(len(a), np.nan)
    valid = np.abs(q[:, 2]) > 1e-12
    result[valid] = np.linalg.norm(q[valid, :2] / q[valid, 2:] - b[valid], axis=1)
    return result


def summary(errors, selected):
    values = np.asarray(errors)[selected]
    finite = values[np.isfinite(values)]
    return dict(requestedCount=len(values), measuredCount=len(finite),
                undefinedCount=len(values)-len(finite),
                medianPixels=float(np.median(finite)) if len(finite) else None,
                p95Pixels=float(np.percentile(finite, 95)) if len(finite) else None,
                atMost3Pixels=int(np.sum(finite <= 3.)))


def diagnose_pair(frame_a, frame_b, points_a, points_b, matches):
    import pycolmap
    ca, cb = camera(frame_a), camera(frame_b)
    if ca.epoch != cb.epoch:
        raise ValueError('cross_epoch_pair')
    matches = np.asarray(matches)
    if matches.ndim != 2 or matches.shape[1] != 2 or not np.issubdtype(matches.dtype, np.integer):
        raise ValueError('invalid_match_indices')
    if len(matches) and (np.min(matches) < 0 or np.max(matches[:,0]) >= len(points_a) or np.max(matches[:,1]) >= len(points_b)):
        raise ValueError('match_index_outside_features')
    if len(np.unique(matches, axis=0)) != len(matches):
        raise ValueError('duplicate_match_pairs')
    if len(np.unique(matches[:,0])) != len(matches) or len(np.unique(matches[:,1])) != len(matches):
        raise ValueError('non_unique_feature_identity')
    a, b = np.asarray(points_a)[matches[:,0]], np.asarray(points_b)[matches[:,1]]
    if a.shape != (len(matches),2) or b.shape != a.shape or not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError('invalid_feature_pixels')
    for pixels, cam in ((a,ca),(b,cb)):
        if np.any(pixels < 0) or np.any(pixels >= [cam.width,cam.height]):
            raise ValueError('feature_outside_native_image')
    # Spatial split is fixed before viewing residuals. Neighbouring repeated texture
    # may still correlate; this is not a new-room or independent metric test.
    cells = np.floor(a / np.array([ca.width / 8, ca.height / 6])).astype(int)
    fitting = (cells.sum(axis=1) % 2) == 0
    scoring = ~fitting
    f, rotation_h, baseline = recorded_geometry(ca, cb)
    raw = np.full(len(a), np.nan) if f is None else sampson_pixels(f, a, b)
    rot = transfer_pixels(rotation_h, a, b)
    row = dict(status='measured' if len(a) else 'no_matches', matchCount=len(a),
               fittingCount=int(fitting.sum()), scoringCount=int(scoring.sum()),
               baselineARUnits=baseline, metricScaleIndependentlyVerified=False,
               elapsedCameraSeconds=cb.time-ca.time,
               recordedF=summary(raw, scoring), recordedRotationH=summary(rot, scoring),
               recordedFReason='zero_baseline_undefined' if f is None else 'recorded_camera_geometry',
               fittingMask=fitting.tolist(), pointsA=a.tolist(), pointsB=b.tolist(),
               recordedErrors=[float(x) if np.isfinite(x) else None for x in raw],
               recordedRotationErrors=[float(x) if np.isfinite(x) else None for x in rot],
               occupiedFitCells=len(np.unique(cells[fitting],axis=0)),
               occupiedScoringCells=len(np.unique(cells[scoring],axis=0)),
               geometricIdentifiability='not_certified_planarity_rotation_and_correlated_texture_possible',
               productAccepted=False)
    for name, minimum, solver, key, residual in (
        ('imageF', 8, pycolmap.estimate_fundamental_matrix, 'F', sampson_pixels),
        ('imageH', 4, pycolmap.estimate_homography_matrix, 'H', transfer_pixels)):
        if fitting.sum() < minimum or not scoring.any():
            row[name] = dict(status='insufficient_fit_or_scoring_matches', requestedCount=int(scoring.sum()))
            continue
        options = pycolmap.RANSACOptions()
        options.max_error = 3.
        options.max_num_trials = 10000
        pycolmap.set_random_seed(42)
        fitted = solver(a[fitting], b[fitting], options)
        if fitted is None:
            row[name] = dict(status='solver_failed', requestedCount=int(scoring.sum()))
            continue
        if not np.isfinite(fitted[key]).all():
            row[name] = dict(status='nonfinite_solver_matrix',requestedCount=int(scoring.sum()))
            continue
        errors = residual(fitted[key], a, b)
        row[name] = dict(status='estimated', **summary(errors, scoring),
                         fitInlierCount=int(fitted['num_inliers']), matrix=fitted[key].tolist(),
                         matrixSingularValues=np.linalg.svd(fitted[key],compute_uv=False).tolist(),
                         errors=[float(x) if np.isfinite(x) else None for x in errors])
    return row


def run(document_path, pair_path, output):
    started = time.monotonic()
    document = json.loads(document_path.read_text())
    cached = json.loads(pair_path.read_text())
    if cached.get('inputDocumentSHA256') != sha(document_path):
        raise ValueError('frozen_document_sha_mismatch')
    train, excluded = cached['trainingIndices'], cached['evaluationExcluded']
    if len(set(train)) != len(train) or set(train) & set(excluded):
        raise ValueError('invalid_partition')
    features = {r['frameIndex']: r for r in cached['features']}
    pairs = {(r['frameA'],r['frameB']):r for r in cached['pairs']}
    if len(features) != len(cached['features']) or len(pairs) != len(cached['pairs']):
        raise ValueError('duplicate_cached_identity')
    times=[camera(document['keyframes'][i]).time for i in train]
    if any(b<=a for a,b in zip(times,times[1:])):
        raise ValueError('training_order_not_time_monotonic')
    records=[]
    for a,b in zip(train,train[1:]):
        base=dict(frameA=a,frameB=b)
        entry=pairs.get((a,b))
        try:
            if entry is None or entry.get('status') != 'matched':
                records.append(dict(**base,status='cached_pair_unavailable'));continue
            match_path=Path(entry['matchFile'])
            if entry.get('matchSHA256') and entry['matchSHA256'] != sha(match_path):
                raise ValueError('cached_matches_sha_mismatch')
            pa,pb=Path(features[a]['featureFile']),Path(features[b]['featureFile'])
            with np.load(pa) as fa, np.load(pb) as fb, np.load(match_path) as fm:
                for index, feature in ((a,fa),(b,fb)):
                    frame=document['keyframes'][index]
                    if feature['image_size'].tolist() != [frame['width'],frame['height']]:
                        raise ValueError('cached_feature_native_size_mismatch')
                    if not cache_matches_source(feature['cacheKey'],sha(document_path.parent/frame['imageFile'])):
                        raise ValueError('cached_feature_source_image_mismatch')
                row=diagnose_pair(document['keyframes'][a],document['keyframes'][b],fa['points'],fb['points'],fm['matches'])
            records.append(dict(**base,**row,featureSHA256=[sha(pa),sha(pb)],matchSHA256=sha(match_path)))
        except (ValueError,KeyError,OSError) as error:
            records.append(dict(**base,status='failed',reason=str(error)))
    result=dict(method='frozen_adjacent_spatial_split_geometry_control_v1',
                inputDocumentSHA256=sha(document_path),pairResultSHA256=sha(pair_path),
                trainingIndices=train,evaluationExcluded=excluded,
                selection='every_adjacent_training_pair_before_residuals',
                requestedPairCount=max(0,len(train)-1),records=records,
                statusCounts=dict(Counter(r['status'] for r in records)),
                elapsedSeconds=time.monotonic()-started,productAccepted=False,
                featureIntegrity='source_image_cache_identity_checked_current_feature_SHA_recorded_prior_feature_SHA_not_available',
                warning='Image F/H scores are correspondence consistency, not physical accuracy. F can be degenerate for planar scenes or rotation. No camera was changed; no metric or reentry claim.')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:result[k] for k in ['requestedPairCount','statusCounts','elapsedSeconds']}))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--document',required=True,type=Path)
    parser.add_argument('--pairs',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    run(args.document,args.pairs,args.output)
