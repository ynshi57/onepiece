"""Frozen training line refinement in one explicitly supplied SfM component.

Finite support is intersected across original training observations. Neither
semantic walls nor metric accuracy is asserted; unavailable lines stay visible.
"""
from __future__ import annotations

from collections import Counter
import numpy as np

from .spatial_line_structure import LineStructureConfig, _plane, _project, _distance_to_segment, _validate_config
from .spatial_reconstruction import Camera, resize_intrinsics


def _fit(observations, prepared, config):
    normals = np.asarray([_plane(prepared[f][0], prepared[f][1], s)
                          for f, _, s in observations])
    _, singular, vectors = np.linalg.svd(normals, full_matrices=False)
    # Two independent back-projection planes are necessary to determine a line.
    if singular[1] <= 1e-8 or singular[1] / singular[0] < .01:
        return {"reason": "degenerate_backprojection_planes"}
    direction = vectors[-1]
    offsets = np.asarray([n @ prepared[f][0].center
                          for n, (f, _, _) in zip(normals, observations)])
    point = np.linalg.lstsq(np.vstack((normals, direction)), np.r_[offsets, 0.], rcond=None)[0]
    intervals = []
    for frame, line_id, segment in observations:
        cam, intrinsic = prepared[frame]
        values = []
        for pixel in segment:
            ray = cam.world_to_camera[:3, :3].T @ np.linalg.solve(intrinsic, np.r_[pixel, 1.])
            ray /= np.linalg.norm(ray)
            matrix = np.column_stack((direction, -ray))
            if np.linalg.svd(matrix, compute_uv=False)[-1] < 1e-6:
                return {"reason": "grazing_endpoint_ray", "frameIndex": frame, "lineId": line_id}
            t, ray_depth = np.linalg.lstsq(matrix, cam.center - point, rcond=None)[0]
            if ray_depth <= 0:
                return {"reason": "negative_endpoint_ray", "frameIndex": frame, "lineId": line_id}
            values.append(float(t))
        intervals.append({"frameIndex": frame, "lineId": line_id,
                          "interval": sorted(values)})
    lower = max(x["interval"][0] for x in intervals)
    upper = min(x["interval"][1] for x in intervals)
    evidence = {"finiteTrainingIntervals": intervals, "finiteOverlapMeters": float(upper - lower)}
    if upper - lower < config.minimum_source_overlap_meters:
        return {**evidence, "reason": "insufficient_finite_training_overlap"}
    endpoints = point + np.array([lower, upper])[:, None] * direction
    samples = point + np.linspace(lower, upper, 33)[:, None] * direction
    residuals, checks = [], []
    for frame, line_id, segment in observations:
        cam, intrinsic = prepared[frame]
        uv, depth = _project(cam, intrinsic, samples)
        if not np.isfinite(uv).all() or not np.isfinite(depth).all():
            return {**evidence, "reason": "nonfinite_projection"}
        finite_distances = _distance_to_segment(uv, segment)
        projected_line = np.cross(np.r_[uv[0], 1.], np.r_[uv[-1], 1.])
        norm = np.linalg.norm(projected_line[:2])
        if norm < 1e-8:
            return {**evidence, "reason": "degenerate_projected_line"}
        distances = np.abs(np.c_[segment, np.ones(2)] @ projected_line) / norm
        residuals.extend(distances.tolist())
        checks.append({"frameIndex": frame, "lineId": line_id,
                       "endpointResidualPixels": distances.tolist(),
                       "maximumFiniteSegmentDistancePixels": float(finite_distances.max()),
                       "minimumDepthMeters": float(depth.min()), "maximumDepthMeters": float(depth.max())})
    median, p90 = float(np.median(residuals)), float(np.percentile(residuals, 90))
    if any(c["minimumDepthMeters"] < config.minimum_depth_meters or
           c["maximumDepthMeters"] > config.maximum_depth_meters for c in checks):
        reason = "component_depth_out_of_range"
    elif median > config.maximum_joint_median_residual_pixels or p90 > config.maximum_joint_p90_residual_pixels:
        reason = "component_reprojection_mismatch"
    elif any(c["maximumFiniteSegmentDistancePixels"] > config.source_projection_tolerance_pixels for c in checks):
        reason = "component_finite_segment_coverage_mismatch"
    else:
        reason = "component_finite_line_supported"
    return {**evidence, "reason": reason, "endpointsWorld": endpoints.tolist(),
            "medianResidualPixels": median, "p90ResidualPixels": p90, "observations": checks}


def refine_component_lines(document: dict, training_result: dict,
                           camera_by_frame: dict[int, Camera], component_id: int | str) -> dict:
    """Refit every frozen local candidate; mapping must contain one component only.

    Cameras must already share the caller's documented coordinate/scale convention.
    No evaluation image, feature extraction, correspondence search or pose fitting
    occurs here. Mapping membership is explicit and is never inferred from poses.
    """
    if training_result.get("status") != "training_only_local_line_candidates":
        raise ValueError("expected_frozen_training_line_result")
    if training_result.get("evaluationRGBUsed") is not False:
        raise ValueError("training_result_evaluation_rgb_not_excluded")
    training = set(training_result["trainingFrameIndices"])
    if not set(camera_by_frame).issubset(training):
        raise ValueError("component_camera_outside_frozen_training_partition")
    config = LineStructureConfig(**training_result["config"])
    _validate_config(config)
    records = {f["frameIndex"]: f for f in training_result["frames"]}
    candidates = {c["id"]: c for c in training_result["candidates"]}
    prepared = {}
    for index, cam in camera_by_frame.items():
        frame = document["keyframes"][index]
        if (cam.width, cam.height) != (frame["width"], frame["height"]):
            raise ValueError("component_camera_image_dimensions_mismatch")
        width = min(config.image_width, cam.width)
        height = max(1, round(cam.height * width / cam.width))
        prepared[index] = (cam, resize_intrinsics(cam.k, cam.width, cam.height, width, height))
    results = []
    for original in training_result["localLines"]:
        candidate = candidates[original["candidateId"]]
        frozen = candidate["jointFit"]["observations"]
        if any(o["frameIndex"] not in training for o in frozen):
            raise ValueError("frozen_observation_outside_training_partition")
        missing = [o["frameIndex"] for o in frozen if o["frameIndex"] not in prepared]
        item = {"candidateId": original["candidateId"], "rawARGeometry": original,
                "missingComponentObservationFrames": missing,
                "frozenObservationCount": len(frozen), "completeWall": False,
                "metricAccuracyVerified": False}
        if any(f not in prepared for f in candidate["sourceFrames"]):
            fitted = {"reason": "source_observation_not_in_component"}
        else:
            observations = []
            for o in frozen:
                frame, line_id = o["frameIndex"], o["lineId"]
                if frame in prepared:
                    segment = np.asarray(records[frame]["longLines"][line_id], dtype=float)
                    observations.append((frame, line_id, segment))
            fitted = ({"reason": "fewer_than_three_component_observations"} if len(observations) < 3
                      else _fit(observations, prepared, config))
        results.append({**item, **fitted})
    counts = Counter(x["reason"] for x in results)
    return {"status": "frozen_component_line_refinement", "componentId": component_id,
            "candidateCount": len(results), "supportedCount": counts["component_finite_line_supported"],
            "reasonCounts": dict(counts), "lines": results, "evaluationRGBUsed": False,
            "correspondencesReselected": False, "productAccepted": False,
            "completeWall": False, "metricAccuracyVerified": False,
            "coordinateSource": "caller_supplied_single_component_cameras"}
