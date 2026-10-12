#!/usr/bin/env python3
"""Research-only frozen reprojection trend diagnostics; never correct camera poses.

Input: training_indices, evaluation_indices; cameras (list of frameIndex, K,
worldToCamera, width, height); tracks (id, worldPoint, observations containing
frameIndex/pixel). K/W2C are row-major matrices, native centre pixel convention.
Fitted residual is observed minus projected pixel. Models use [1, normalized row,
normalized squared radius] separately for x/y. Association with these covariates
cannot identify rolling shutter, distortion, pose error or a physical cause.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import numpy as np


def _fit(x, y):
    singular = np.linalg.svd(x, compute_uv=False)
    if len(x) < 8 or len(singular) < 3 or singular[-1] / max(singular[0], 1e-12) < 1e-5:
        return None, singular
    return np.linalg.lstsq(x, y, rcond=None)[0], singular


def diagnose(payload: dict) -> dict:
    train, evaluation = list(payload['training_indices']), list(payload['evaluation_indices'])
    if len(set(train)) != len(train) or len(set(evaluation)) != len(evaluation):
        raise ValueError('repeated_partition_indices')
    if set(train) & set(evaluation):
        raise ValueError('training_evaluation_partition_overlap')
    if not train:
        raise ValueError('empty_training_partition')
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    cameras = {}
    for item in payload['cameras']:
        index = item['frameIndex']
        if index in cameras:
            raise ValueError('repeated_camera_index')
        if index not in set(train) | set(evaluation):
            raise ValueError('camera_outside_declared_partition')
        k, pose = np.asarray(item['K'], float), np.asarray(item['worldToCamera'], float)
        if (k.shape != (3, 3) or pose.shape != (4, 4) or
                not np.isfinite(k).all() or not np.isfinite(pose).all() or
                k[0, 0] <= 0 or k[1, 1] <= 0 or item['width'] <= 0 or item['height'] <= 0 or
                not np.allclose(k[2], [0, 0, 1]) or not np.allclose(pose[3], [0, 0, 0, 1]) or
                not np.allclose(pose[:3,:3].T@pose[:3,:3],np.eye(3),atol=1e-4) or
                abs(np.linalg.det(pose[:3,:3])-1)>1e-4):
            raise ValueError('invalid_native_camera')
        cameras[index] = item
    seen = set()
    records = []
    for track in payload['tracks']:
        identity = str(track['id'])
        if identity in seen:
            raise ValueError('repeated_track_identity')
        seen.add(identity)
        point = np.asarray(track['worldPoint'], float)
        seen_frames = set()
        for obs in track['observations']:
            index = obs['frameIndex']
            if index not in set(train) | set(evaluation):
                raise ValueError('observation_outside_declared_partition')
            item = {'trackId': identity, 'frameIndex': index, 'observedPixel': obs['pixel'],
                    'partition': 'training' if index in train else 'evaluation',
                    'usedInFit': False}
            records.append(item)
            if index in seen_frames:
                item['reason'] = 'duplicate_track_camera_observation'
                continue
            seen_frames.add(index)
            if index not in cameras:
                item['reason'] = 'missing_camera'; continue
            camera = cameras[index]
            pixel = np.asarray(obs['pixel'], float)
            if point.shape != (3,) or not np.isfinite(point).all():
                item['reason'] = 'invalid_world_point'; continue
            if (pixel.shape != (2,) or not np.isfinite(pixel).all() or
                    not (0 <= pixel[0] < camera['width'] and 0 <= pixel[1] < camera['height'])):
                item['reason'] = 'invalid_native_pixel'; continue
            local = np.asarray(camera['worldToCamera']) @ np.r_[point, 1.]
            if local[2] <= 1e-8:
                item['reason'] = 'non_positive_camera_depth'; continue
            q = np.asarray(camera['K']) @ local[:3]
            prediction = q[:2] / q[2]
            residual = pixel - prediction
            if not np.isfinite(prediction).all() or not np.isfinite(residual).all():
                item['reason']='nonfinite_projection'; continue
            k = camera['K']; scale = float(max(camera['width'], camera['height']))
            # Use frozen predicted position as covariate, not observed error-contaminated position.
            row = (prediction[1] - k[1][2]) / camera['height']
            radius2 = ((prediction[0] - k[0][2]) ** 2 + (prediction[1] - k[1][2]) ** 2) / scale ** 2
            item.update(reason='projected', projectedPixel=prediction.tolist(), residualPixels=residual.tolist(),
                        residualNormPixels=float(np.linalg.norm(residual)), normalizedRow=float(row),
                        normalizedRadiusSquared=float(radius2))
    reports = []
    for index in sorted(train):
        rows = [r for r in records if r['frameIndex'] == index and r['reason'] == 'projected' and r['partition'] == 'training']
        x = np.asarray([[1., r['normalizedRow'], r['normalizedRadiusSquared']] for r in rows]).reshape(-1, 3)
        y = np.asarray([r['residualPixels'] for r in rows]).reshape(-1, 2)
        weights, singular = _fit(x, y)
        report = {'frameIndex': index, 'validObservationCount': len(rows), 'distinctTrackCount': len({r['trackId'] for r in rows}),
                  'allObservationCount': sum(r['frameIndex'] == index for r in records),
                  'singularValues': singular.tolist(), 'productAccepted': False}
        reports.append(report)
        if weights is None:
            report['reason'] = 'insufficient_or_degenerate_training_observations'; continue
        for r in rows:
            r['usedInFit'] = True
        report.update(reason='training_covariate_trend_only', featureOrder=['intercept', 'normalized_row', 'normalized_radius_squared'],
                      coefficientsXY=weights.tolist(), parameterUnits='pixel_residual_per_normalized_feature',
                      trainingRawRMSEPixels=float(np.sqrt(np.mean(np.sum(y*y, axis=1)))),
                      trainingTrendRMSEPixels=float(np.sqrt(np.mean(np.sum((y-x@weights)**2, axis=1)))))
        groups = sorted({r['trackId'] for r in rows}); folds=[]
        for group in groups:
            held = np.asarray([r['trackId'] == group for r in rows])
            fit, fit_singular = _fit(x[~held], y[~held])
            fold = {'trackId': group, 'heldObservationCount': int(held.sum()),
                    'fitObservationCount': int((~held).sum()), 'singularValues': fit_singular.tolist()}
            folds.append(fold)
            if fit is None:
                fold['reason'] = 'insufficient_or_degenerate_leave_group_out_fit'; continue
            prediction=x[held]@fit
            fold.update(reason='measured', rawSquaredError=float(np.sum(y[held]**2)),
                        interceptBaselineSquaredError=float(np.sum((y[held]-y[~held].mean(axis=0))**2)),
                        trendSquaredError=float(np.sum((y[held]-prediction)**2)))
        measured = [f for f in folds if f['reason'] == 'measured']
        report['groupLeaveOneOut']={'allGroupCount': len(folds), 'measuredGroupCount': len(measured), 'folds': folds,
                                   'fullyMeasured': len(measured)==len(folds)}
        # Do not report aggregate improvement when any validation fold failed.
        if len(measured)==len(folds):
            count=sum(f['heldObservationCount'] for f in measured)
            raw=np.sqrt(sum(f['rawSquaredError'] for f in measured)/count)
            trend=np.sqrt(sum(f['trendSquaredError'] for f in measured)/count)
            intercept=np.sqrt(sum(f['interceptBaselineSquaredError'] for f in measured)/count)
            report['groupLeaveOneOut'].update(rawRMSEPixels=float(raw),trendRMSEPixels=float(trend),
                                             interceptBaselineRMSEPixels=float(intercept),
                                             improvementPixels=float(raw-trend),improvementOverInterceptPixels=float(intercept-trend))
    return {'status': 'frozen_training_residual_diagnostics', 'inputSHA256': digest,
            'trainingIndices': sorted(train), 'evaluationIndices': sorted(evaluation),
            'evaluationUsedInFit': False, 'allObservations': records,
            'observationCount': len(records), 'reasonCounts': dict(Counter(r['reason'] for r in records)),
            'cameraReports': reports, 'productAccepted': False, 'cameraParametersModified': False,
            'worldPointProvenance': 'caller_supplied_frozen_points_not_retriangulated_or_independently_audited',
            'causeIdentified': False, 'warning': 'Covariate trends do not identify rolling shutter or lens distortion; correlated pose and landmark errors remain possible.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    result=diagnose(json.loads(args.input.read_text()))
    args.output.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'observationCount':result['observationCount'],'reasonCounts':result['reasonCounts'],
                      'causeIdentified':False}))


if __name__=='__main__':
    main()
