"""Training-only line evidence; local 3D segments are candidates, not walls.

The extractor is the repository's source-built OpenCV LSD bridge. No cv2 wheel
is imported by the backend and no evaluation image enters this function.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence
import ctypes
import math
import time

import numpy as np
from PIL import Image

from .spatial_reconstruction import Camera, camera, resize_intrinsics
from .spatial_sift_native import library


@dataclass(frozen=True)
class LineStructureConfig:
    image_width: int = 640
    minimum_line_pixels: float = 60.
    source_neighbors: int = 2
    minimum_baseline_meters: float = .03
    maximum_baseline_meters: float = .50
    maximum_view_angle_degrees: float = 25.
    minimum_view_overlap_samples: int = 2
    minimum_intersection_degrees: float = 2.
    maximum_intersection_degrees: float = 60.
    minimum_depth_meters: float = .15
    maximum_depth_meters: float = 6.
    minimum_source_overlap_meters: float = .12
    source_projection_tolerance_pixels: float = 4.
    third_angle_degrees: float = 5.
    third_orthogonal_pixels: float = 8.
    third_overlap_pixels: float = 40.
    appearance_side_offset_pixels: float = 6.
    maximum_appearance_difference: float = .08
    minimum_side_contrast: float = .10
    maximum_joint_median_residual_pixels: float = 3.
    maximum_joint_p90_residual_pixels: float = 6.
    duplicate_angle_degrees: float = 5.
    duplicate_distance_meters: float = .05
    duplicate_overlap_meters: float = .10
    maximum_native_segments: int = 4096


def _validate_config(config: LineStructureConfig) -> None:
    values = asdict(config)
    if not all(math.isfinite(float(v)) for v in values.values()):
        raise ValueError("non_finite_line_configuration")
    if (config.image_width < 32 or config.source_neighbors < 1
            or config.maximum_native_segments < 1 or config.minimum_line_pixels <= 0
            or config.minimum_baseline_meters <= 0
            or config.maximum_baseline_meters <= config.minimum_baseline_meters
            or config.minimum_depth_meters <= 0
            or config.maximum_depth_meters <= config.minimum_depth_meters
            or config.minimum_source_overlap_meters <= 0
            or min(config.source_projection_tolerance_pixels,
                   config.third_orthogonal_pixels, config.third_overlap_pixels,
                   config.appearance_side_offset_pixels,
                   config.maximum_appearance_difference) <= 0
            or config.minimum_side_contrast < 0
            or min(config.maximum_joint_median_residual_pixels,
                   config.maximum_joint_p90_residual_pixels,
                   config.duplicate_angle_degrees,
                   config.duplicate_distance_meters,
                   config.duplicate_overlap_meters) <= 0):
        raise ValueError("invalid_line_configuration")


def _native_lines(gray: np.ndarray, capacity: int) -> np.ndarray:
    source = np.ascontiguousarray(gray, dtype=np.uint8)
    if source.ndim != 2 or min(source.shape) < 8:
        raise ValueError("invalid_line_image")
    lib = library()
    try:
        function = lib.spatial_lsd
        version = lib.spatial_lsd_abi_version
    except AttributeError as exc:
        raise RuntimeError("native_lsd_missing: 运行 bash deploy/spatial/build_sift.sh") from exc
    version.argtypes, version.restype = [], ctypes.c_int
    if version() != 1:
        raise RuntimeError("native_lsd_abi_mismatch: 请重新编译源码并重启后端")
    function.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.c_int,
                         ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.POINTER(ctypes.c_float), ctypes.c_char_p, ctypes.c_int]
    function.restype = ctypes.c_int
    output = np.empty((capacity, 4), dtype=np.float32)
    error = ctypes.create_string_buffer(1024)
    count = function(source.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
                     source.shape[1], source.shape[0], source.strides[0], capacity,
                     output.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), error, len(error))
    if count < 0 or count > capacity:
        raise RuntimeError("native_lsd_failed: " + error.value.decode("utf-8", "replace"))
    return output[:count]


def _project(cam: Camera, intrinsic: np.ndarray, xyz: np.ndarray):
    local = xyz @ cam.world_to_camera[:3, :3].T + cam.world_to_camera[:3, 3]
    z = local[:, 2]
    homogeneous = local @ intrinsic.T
    with np.errstate(divide="ignore", invalid="ignore"):
        pixels = homogeneous[:, :2] / homogeneous[:, 2, None]
    return pixels, z


def _distance_to_segment(points: np.ndarray, segment: np.ndarray):
    start, end = segment
    direction = end - start
    fraction = np.clip(((points - start) @ direction) / (direction @ direction), 0., 1.)
    return np.linalg.norm(points - (start + fraction[:, None] * direction), axis=1)


def _pose_overlap(a, b, config: LineStructureConfig) -> bool:
    ca, ka, _, _ = a
    cb, kb, _, _ = b
    baseline = np.linalg.norm(ca.center - cb.center)
    if not config.minimum_baseline_meters <= baseline <= config.maximum_baseline_meters:
        return False
    forward_a = ca.world_to_camera[:3, :3].T @ np.array([0., 0., 1.])
    forward_b = cb.world_to_camera[:3, :3].T @ np.array([0., 0., 1.])
    angle = math.degrees(math.acos(float(np.clip(forward_a @ forward_b, -1., 1.))))
    if angle > config.maximum_view_angle_degrees:
        return False
    # Five rays at one metre are checked in both directions; this is only a
    # source-pair proposal gate, never proof of shared physical structure.
    samples = np.array([[.5, .5], [.2, .2], [.8, .2], [.2, .8], [.8, .8]])
    def count_inside(source, target):
        src_cam, src_k, width, height = source
        dst_cam, dst_k, dst_width, dst_height = target
        pixels = samples * [width, height]
        rays = np.linalg.solve(src_k, np.c_[pixels, np.ones(len(pixels))].T).T
        xyz = rays @ src_cam.world_to_camera[:3, :3] + src_cam.center
        uv, depth = _project(dst_cam, dst_k, xyz)
        return int(np.sum((depth > 0) & (uv[:, 0] >= 0) & (uv[:, 0] < dst_width)
                          & (uv[:, 1] >= 0) & (uv[:, 1] < dst_height)))
    return (count_inside(a, b) >= config.minimum_view_overlap_samples
            and count_inside(b, a) >= config.minimum_view_overlap_samples)


def _plane(cam: Camera, intrinsic: np.ndarray, segment: np.ndarray):
    homogeneous = np.cross(np.r_[segment[0], 1.], np.r_[segment[1], 1.])
    normal = cam.world_to_camera[:3, :3].T @ intrinsic.T @ homogeneous
    return normal / np.linalg.norm(normal)


def _source_geometry(a, b, line_a, line_b, config: LineStructureConfig):
    cam_a, k_a, _, _ = a
    cam_b, k_b, _, _ = b
    normal_a, normal_b = _plane(cam_a, k_a, line_a), _plane(cam_b, k_b, line_b)
    angle = math.degrees(math.acos(float(np.clip(abs(normal_a @ normal_b), 0., 1.))))
    if not config.minimum_intersection_degrees <= angle <= config.maximum_intersection_degrees:
        return None, "weak_or_inconsistent_intersection"
    direction = np.cross(normal_a, normal_b)
    direction /= np.linalg.norm(direction)
    center = np.linalg.solve(np.stack((normal_a, normal_b, direction)),
                             np.array((normal_a @ cam_a.center, normal_b @ cam_b.center,
                                       direction @ ((cam_a.center + cam_b.center) / 2))))
    # A fixed, finite search keeps negative and far depths explicitly rejected.
    samples = np.linspace(-config.maximum_depth_meters, config.maximum_depth_meters, 241)
    xyz = center + samples[:, None] * direction
    supported = np.ones(len(samples), dtype=bool)
    for cam, intrinsic, segment in ((cam_a, k_a, line_a), (cam_b, k_b, line_b)):
        uv, depth = _project(cam, intrinsic, xyz)
        supported &= ((depth >= config.minimum_depth_meters)
                      & (depth <= config.maximum_depth_meters)
                      & np.isfinite(uv).all(axis=1)
                      & (_distance_to_segment(uv, segment)
                         <= config.source_projection_tolerance_pixels))
    positions = np.flatnonzero(supported)
    if len(positions) < 2:
        return None, "no_positive_depth_source_overlap"
    # Distinct supported islands can come from a grazing projection. Keep the
    # longest contiguous island and retain all failed source pairs upstream.
    islands = np.split(positions, np.flatnonzero(np.diff(positions) > 1) + 1)
    interval = max(islands, key=len)
    endpoints = np.stack((xyz[interval[0]], xyz[interval[-1]]))
    length = float(np.linalg.norm(endpoints[1] - endpoints[0]))
    if length < config.minimum_source_overlap_meters:
        return None, "source_overlap_too_short"
    return {"endpoints": endpoints, "intersectionDegrees": angle,
            "sourceOverlapMeters": length}, None


def _appearance(data, xyz: np.ndarray, *, near_segment: np.ndarray | None = None,
                config: LineStructureConfig):
    cam, intrinsic, width, height, _, rgb = data
    uv, depth = _project(cam, intrinsic, xyz)
    direction = uv[-1] - uv[0]
    norm = float(np.linalg.norm(direction))
    if norm < 1 or not np.isfinite(norm):
        return None
    sideways = np.array([-direction[1], direction[0]]) / norm
    valid = np.isfinite(uv).all(axis=1) & (depth > config.minimum_depth_meters)
    if near_segment is not None:
        valid &= (_distance_to_segment(uv, near_segment)
                  <= config.third_orthogonal_pixels)
    colors = []
    for sign in (-1, 1):
        positions = np.rint(uv + sign * config.appearance_side_offset_pixels * sideways).astype(int)
        inside = (valid & (positions[:, 0] >= 0) & (positions[:, 0] < width)
                  & (positions[:, 1] >= 0) & (positions[:, 1] < height))
        if inside.sum() < 6:
            return None
        colors.append(np.median(rgb[positions[inside, 1], positions[inside, 0]], axis=0) / 255.)
    return np.asarray(colors)


def _appearance_difference(a: np.ndarray, b: np.ndarray) -> float:
    return min(float(np.mean(np.abs(a - b))),
               float(np.mean(np.abs(a - b[::-1]))))


def _chromaticity_difference(a: np.ndarray, b: np.ndarray) -> float:
    """Compare the two sides after removing a shared brightness multiplier."""
    if a.shape != (2, 3) or b.shape != (2, 3):
        raise ValueError("invalid_line_side_colors")
    a_sum, b_sum = a.sum(axis=1, keepdims=True), b.sum(axis=1, keepdims=True)
    if min(float(a_sum.min()), float(b_sum.min())) <= 1e-6:
        return math.inf
    return _appearance_difference(a / a_sum, b / b_sum)


def _training_chromaticity_limit(training_result: dict) -> tuple[float, int]:
    """Calibrate only on already supported training views, before reading eval RGB."""
    differences = []
    for candidate in training_result["candidates"]:
        source = candidate.get("sourceAppearance")
        if not source:
            continue
        reference = np.asarray(source["sideColors"][0], dtype=float)
        for check in candidate.get("thirdViewChecks", []):
            for match in check.get("lineMatches", []):
                if match.get("reason") == "appearance_supported":
                    observed = np.asarray(match["appearance"], dtype=float)
                    differences.append(_chromaticity_difference(reference, observed))
    if len(differences) < 30 or not np.isfinite(differences).all():
        raise ValueError("insufficient_training_appearance_calibration")
    return float(np.percentile(differences, 90)), len(differences)


def _third_check(data, endpoints: np.ndarray, config: LineStructureConfig):
    cam, intrinsic, width, height, lines, _ = data
    predicted, depth = _project(cam, intrinsic, endpoints)
    vector = predicted[1] - predicted[0]
    length = float(np.linalg.norm(vector))
    in_view = (bool(np.isfinite(predicted).all()) and bool(np.isfinite(depth).all())
               and float(depth.min()) >= config.minimum_depth_meters
               and length >= config.third_overlap_pixels
               and float(predicted[:, 0].max()) >= 0
               and float(predicted[:, 0].min()) < width
               and float(predicted[:, 1].max()) >= 0
               and float(predicted[:, 1].min()) < height)
    result = {"predictedVisible": in_view, "predictedSegment": predicted.tolist()
              if np.isfinite(predicted).all() else None,
              "depthMeters": depth.tolist() if np.isfinite(depth).all() else None,
              "lineMatches": [], "reason": "outside_view_or_nonpositive_depth"}
    if not in_view:
        return result
    unit = vector / length
    for line_id, segment in enumerate(lines):
        side = segment[1] - segment[0]
        side_length = float(np.linalg.norm(side))
        angle = math.degrees(math.acos(float(np.clip(abs(side @ unit) / side_length, 0., 1.))))
        if angle > config.third_angle_degrees:
            continue
        displacement = np.abs(np.cross(vector, segment - predicted[0]) / length)
        along = (segment - predicted[0]) @ unit
        overlap = max(0., min(float(along.max()), length) - max(float(along.min()), 0.))
        if displacement.max() <= config.third_orthogonal_pixels and overlap >= config.third_overlap_pixels:
            samples = endpoints[0] + np.linspace(.05, .95, 24)[:, None] * (endpoints[1] - endpoints[0])
            observed = _appearance(data, samples, near_segment=segment, config=config)
            result["lineMatches"].append({"lineId": line_id,
                                           "angleDegrees": angle,
                                           "orthogonalMaxPixels": float(displacement.max()),
                                           "overlapPixels": overlap,
                                           "appearance": observed.tolist() if observed is not None else None})
    result["reason"] = "geometric_line_candidate" if result["lineMatches"] else "no_line_match"
    return result


def _joint_line_fit(candidate: dict, prepared: dict, config: LineStructureConfig):
    """Least-squares 3D line from source plus appearance-supported train views."""
    first, second = candidate["sourceFrames"]
    source_ids = candidate["sourceLineIds"]
    observations = [(first, source_ids[0]), (second, source_ids[1])]
    for check in candidate["thirdViewChecks"]:
        supported = [match for match in check["lineMatches"]
                     if match.get("reason") == "appearance_supported"]
        if supported:
            winner = min(supported, key=lambda item:
                         (item["appearanceDifference"], -item["overlapPixels"], item["lineId"]))
            observations.append((check["frameIndex"], winner["lineId"]))
    normals, offsets = [], []
    for frame_index, line_id in observations:
        cam, intrinsic, _, _, lines, _ = prepared[frame_index]
        normal = _plane(cam, intrinsic, lines[line_id])
        normals.append(normal)
        offsets.append(normal @ cam.center)
    matrix = np.asarray(normals)
    _, _, vectors = np.linalg.svd(matrix, full_matrices=False)
    direction = vectors[-1]
    original = np.asarray(candidate["localGeometry"]["endpointsWorld"])
    original_vector = original[1] - original[0]
    if direction @ original_vector < 0:
        direction = -direction
    midpoint = original.mean(axis=0)
    point = np.linalg.lstsq(np.vstack((matrix, direction)),
                            np.r_[offsets, direction @ midpoint], rcond=None)[0]
    half_span = float(np.linalg.norm(original_vector)) / 2
    endpoints = np.stack((point - direction * half_span, point + direction * half_span))
    residuals = []
    depths = []
    for frame_index, line_id in observations:
        cam, intrinsic, _, _, lines, _ = prepared[frame_index]
        projected, depth = _project(cam, intrinsic, endpoints)
        depths.extend(depth.tolist())
        if not np.isfinite(projected).all():
            return {"reason": "joint_nonfinite_projection", "observations": observations}
        predicted_line = np.cross(np.r_[projected[0], 1.], np.r_[projected[1], 1.])
        scale = float(np.linalg.norm(predicted_line[:2]))
        if scale <= 1e-9:
            return {"reason": "joint_degenerate_projection", "observations": observations}
        observed = lines[line_id]
        distances = np.abs(np.c_[observed, np.ones(2)] @ predicted_line) / scale
        residuals.append({"frameIndex": frame_index, "lineId": line_id,
                          "endpointResidualPixels": distances.tolist(),
                          "meanResidualPixels": float(distances.mean())})
    all_residuals = np.asarray([v for item in residuals for v in item["endpointResidualPixels"]])
    median = float(np.median(all_residuals))
    p90 = float(np.percentile(all_residuals, 90))
    if min(depths) < config.minimum_depth_meters or max(depths) > config.maximum_depth_meters:
        reason = "joint_depth_out_of_range"
    elif (median > config.maximum_joint_median_residual_pixels
          or p90 > config.maximum_joint_p90_residual_pixels):
        reason = "joint_reprojection_mismatch"
    else:
        reason = "joint_line_supported"
    return {"reason": reason, "endpointsWorld": endpoints.tolist(),
            "medianResidualPixels": median, "p90ResidualPixels": p90,
            "sourceEndpointShiftMeters": float(np.linalg.norm(endpoints - original, axis=1).max()),
            "observations": residuals}


def _same_local_line(a: dict, b: dict, config: LineStructureConfig) -> bool:
    first = np.asarray(a["endpointsWorld"], dtype=float)
    second = np.asarray(b["endpointsWorld"], dtype=float)
    direction_a = first[1] - first[0]
    direction_b = second[1] - second[0]
    direction_a /= np.linalg.norm(direction_a)
    direction_b /= np.linalg.norm(direction_b)
    angle = math.degrees(math.acos(float(np.clip(abs(direction_a @ direction_b), 0., 1.))))
    if angle > config.duplicate_angle_degrees:
        return False
    offset = second.mean(axis=0) - first.mean(axis=0)
    distance = float(np.linalg.norm(offset - (offset @ direction_a) * direction_a))
    if distance > config.duplicate_distance_meters:
        return False
    interval_a = np.sort(first @ direction_a)
    interval_b = np.sort(second @ direction_a)
    overlap = min(interval_a[1], interval_b[1]) - max(interval_a[0], interval_b[0])
    return bool(overlap >= config.duplicate_overlap_meters)


def reconstruct_line_structure(document: dict, image_root: Path,
                               training_frame_indices: Sequence[int], *,
                               config: LineStructureConfig = LineStructureConfig()) -> dict:
    """Return all line candidates and training-only support; never infer a wall."""
    _validate_config(config)
    indices = sorted(int(i) for i in training_frame_indices)
    frames = document["keyframes"]
    if len(indices) != len(set(indices)) or any(i < 0 or i >= len(frames) for i in indices):
        raise ValueError("invalid_training_frame_indices")
    root = Path(image_root).resolve()
    started = time.monotonic()
    prepared = {}
    frame_records = []
    for index in indices:
        frame = frames[index]
        cam = camera(frame)
        image_path = (root / frame["imageFile"]).resolve()
        if not image_path.is_relative_to(root) or not image_path.is_file():
            raise ValueError("invalid_training_image_path")
        width = config.image_width
        height = max(1, round(cam.height * width / cam.width))
        with Image.open(image_path) as image:
            if image.size != (cam.width, cam.height):
                raise ValueError("training_image_dimensions_mismatch")
            gray = np.asarray(image.convert("L").resize((width, height), Image.Resampling.LANCZOS))
            rgb = np.asarray(image.convert("RGB").resize((width, height), Image.Resampling.LANCZOS))
        native = _native_lines(gray, config.maximum_native_segments)
        lengths = np.linalg.norm(native[:, 2:] - native[:, :2], axis=1)
        long_lines = native[lengths >= config.minimum_line_pixels].reshape(-1, 2, 2).astype(float)
        intrinsic = resize_intrinsics(cam.k, cam.width, cam.height, width, height)
        prepared[index] = (cam, intrinsic, width, height, long_lines, rgb)
        frame_records.append({"frameIndex": index, "epoch": cam.epoch,
                              "detectedLineCount": len(native), "longLineCount": len(long_lines),
                              "longLines": long_lines.tolist()})
    pairs, candidates, local = [], [], []
    for offset, first in enumerate(indices):
        for second in indices[offset + 1:offset + 1 + config.source_neighbors]:
            first_data, second_data = prepared[first], prepared[second]
            if first_data[0].epoch != second_data[0].epoch:
                pairs.append({"frames": [first, second], "reason": "different_epoch"})
                continue
            if not _pose_overlap(first_data[:4], second_data[:4], config):
                pairs.append({"frames": [first, second], "reason": "insufficient_pose_overlap"})
                continue
            pairs.append({"frames": [first, second], "reason": "tested",
                          "linePairCount": len(first_data[4]) * len(second_data[4])})
            pair_candidates = []
            for first_id, first_line in enumerate(first_data[4]):
                for second_id, second_line in enumerate(second_data[4]):
                    geometry, rejection = _source_geometry(first_data[:4], second_data[:4],
                                                            first_line, second_line, config)
                    candidate = {"id": f"line-{first}-{first_id}-{second}-{second_id}",
                                 "sourceFrames": [first, second],
                                 "sourceLineIds": [first_id, second_id],
                                 "reason": rejection or "third_view_pending",
                                 "thirdViewChecks": []}
                    if geometry is not None:
                        endpoints = geometry.pop("endpoints")
                        candidate["localGeometry"] = {**geometry, "endpointsWorld": endpoints.tolist()}
                        samples = endpoints[0] + np.linspace(.05, .95, 24)[:, None] * (endpoints[1] - endpoints[0])
                        appearance_a = _appearance(first_data, samples, near_segment=first_line, config=config)
                        appearance_b = _appearance(second_data, samples, near_segment=second_line, config=config)
                        if appearance_a is None or appearance_b is None:
                            candidate["reason"] = "source_appearance_unavailable"
                        else:
                            contrast_a = float(np.linalg.norm(appearance_a[0] - appearance_a[1]))
                            contrast_b = float(np.linalg.norm(appearance_b[0] - appearance_b[1]))
                            difference = _appearance_difference(appearance_a, appearance_b)
                            candidate["sourceAppearance"] = {"difference": difference,
                                "sideContrast": [contrast_a, contrast_b],
                                "sideColors": [appearance_a.tolist(), appearance_b.tolist()]}
                            if min(contrast_a, contrast_b) < config.minimum_side_contrast:
                                candidate["reason"] = "low_source_side_contrast"
                            elif difference > config.maximum_appearance_difference:
                                candidate["reason"] = "source_appearance_mismatch"
                            else:
                                candidate["reason"] = "source_appearance_candidate"
                    pair_candidates.append(candidate)
            # Appearance score gives one-to-one source assignments. Keep every
            # losing candidate in output with its explicit rejection reason.
            eligible = [c for c in pair_candidates if c["reason"] == "source_appearance_candidate"]
            best_a = {}
            best_b = {}
            for c in eligible:
                aid, bid = c["sourceLineIds"]
                rank = (c["sourceAppearance"]["difference"], c["id"])
                if aid not in best_a or rank < best_a[aid][0]:
                    best_a[aid] = (rank, c["id"])
                if bid not in best_b or rank < best_b[bid][0]:
                    best_b[bid] = (rank, c["id"])
            for candidate in pair_candidates:
                if candidate["reason"] != "source_appearance_candidate":
                    candidates.append(candidate)
                    continue
                first_id, second_id = candidate["sourceLineIds"]
                if (best_a[first_id][1] != candidate["id"]
                        or best_b[second_id][1] != candidate["id"]):
                    candidate["reason"] = "nonmutual_source_line_match"
                    candidates.append(candidate)
                    continue
                endpoints = np.asarray(candidate["localGeometry"]["endpointsWorld"])
                reference = np.asarray(candidate["sourceAppearance"]["sideColors"][0])
                for third in indices:
                    if third in (first, second):
                        continue
                    if prepared[third][0].epoch != first_data[0].epoch:
                        candidate["thirdViewChecks"].append({"frameIndex": third,
                            "predictedVisible": False, "lineMatches": [], "reason": "different_epoch"})
                        continue
                    check = _third_check(prepared[third], endpoints, config)
                    geometric = check["lineMatches"]
                    supported = []
                    for match in geometric:
                        observed = np.asarray(match["appearance"]) if match["appearance"] is not None else None
                        if observed is None:
                            match["reason"] = "third_appearance_unavailable"
                            continue
                        contrast = float(np.linalg.norm(observed[0] - observed[1]))
                        difference = _appearance_difference(reference, observed)
                        match["appearanceDifference"] = difference
                        match["sideContrast"] = contrast
                        if contrast < config.minimum_side_contrast:
                            match["reason"] = "low_third_side_contrast"
                        elif difference > config.maximum_appearance_difference:
                            match["reason"] = "third_appearance_mismatch"
                        else:
                            match["reason"] = "appearance_supported"
                            supported.append(match)
                    check["appearanceSupportedLineIds"] = [m["lineId"] for m in supported]
                    check["reason"] = ("line_supported" if supported else
                                       "third_appearance_mismatch" if geometric else check["reason"])
                    candidate["thirdViewChecks"].append({"frameIndex": third, **check})
                support = sum(bool(check.get("appearanceSupportedLineIds"))
                              for check in candidate["thirdViewChecks"])
                candidate["thirdViewSupportCount"] = support
                candidate["reason"] = "local_line_supported" if support else "no_third_view_support"
                if support:
                    fitted = _joint_line_fit(candidate, prepared, config)
                    candidate["jointFit"] = fitted
                    if fitted["reason"] != "joint_line_supported":
                        candidate["reason"] = fitted["reason"]
                    else:
                        local.append({"candidateId": candidate["id"],
                                      "sourceFrames": [first, second],
                                      "endpointsWorld": fitted["endpointsWorld"],
                                      "supportingTrainingViewCount": 2 + support,
                                      "jointMedianResidualPixels": fitted["medianResidualPixels"],
                                      "jointP90ResidualPixels": fitted["p90ResidualPixels"],
                                      "completeWall": False,
                                      "metricAccuracyVerified": False})
                candidates.append(candidate)
    # Spatially identical lines may be independently proposed by many adjacent
    # source pairs. Keep one representative and retain every duplicate with a
    # reason and pointer to its representative in the candidate evidence.
    supported_count = len(local)
    ranked = sorted(local, key=lambda item: (-item["supportingTrainingViewCount"],
                                            item["jointMedianResidualPixels"], item["candidateId"]))
    representatives = []
    by_id = {item["id"]: item for item in candidates}
    for item in ranked:
        duplicate = next((other for other in representatives
                          if _same_local_line(item, other, config)), None)
        if duplicate is None:
            representatives.append(item)
        else:
            by_id[item["candidateId"]]["reason"] = "duplicate_local_line"
            by_id[item["candidateId"]]["duplicateOf"] = duplicate["candidateId"]
    return {"status": "training_only_local_line_candidates", "productAccepted": False,
            "completeWall": False, "metricAccuracyVerified": False,
            "trainingFrameIndices": indices, "evaluationRGBUsed": False,
            "config": asdict(config), "frames": frame_records, "cameraPairs": pairs,
            "candidateCount": len(candidates), "candidates": candidates,
            "supportedCandidateCount": supported_count,
            "localLineCount": len(representatives), "localLines": representatives,
            "elapsedSeconds": time.monotonic() - started}


def validate_line_structure(document: dict, image_root: Path, training_result: dict,
                            evaluation_frame_indices: Sequence[int], *,
                            appearance_mode: str = "absolute_rgb") -> dict:
    """Read independent RGB only after training result is frozen; never refit it."""
    if training_result.get("status") != "training_only_local_line_candidates":
        raise ValueError("invalid_frozen_line_training_result")
    config = LineStructureConfig(**training_result["config"])
    _validate_config(config)
    if appearance_mode not in ("absolute_rgb", "normalized_chromaticity"):
        raise ValueError("invalid_evaluation_appearance_mode")
    if appearance_mode == "normalized_chromaticity":
        appearance_limit, calibration_count = _training_chromaticity_limit(training_result)
    else:
        appearance_limit, calibration_count = config.maximum_appearance_difference, None
    indices = sorted(int(i) for i in evaluation_frame_indices)
    frames = document["keyframes"]
    train = set(training_result["trainingFrameIndices"])
    if (len(indices) != len(set(indices)) or any(i < 0 or i >= len(frames) for i in indices)
            or train.intersection(indices)):
        raise ValueError("evaluation_frames_overlap_training_or_invalid")
    root = Path(image_root).resolve()
    frozen_candidates = {candidate["id"]: candidate for candidate in training_result["candidates"]}
    prepared = {}
    frame_records = []
    started = time.monotonic()
    for index in indices:
        frame = frames[index]
        cam = camera(frame)
        image_path = (root / frame["imageFile"]).resolve()
        if not image_path.is_relative_to(root) or not image_path.is_file():
            raise ValueError("invalid_evaluation_image_path")
        width = config.image_width
        height = max(1, round(cam.height * width / cam.width))
        with Image.open(image_path) as image:
            if image.size != (cam.width, cam.height):
                raise ValueError("evaluation_image_dimensions_mismatch")
            gray = np.asarray(image.convert("L").resize((width, height), Image.Resampling.LANCZOS))
            rgb = np.asarray(image.convert("RGB").resize((width, height), Image.Resampling.LANCZOS))
        native = _native_lines(gray, config.maximum_native_segments)
        lengths = np.linalg.norm(native[:, 2:] - native[:, :2], axis=1)
        long_lines = native[lengths >= config.minimum_line_pixels].reshape(-1, 2, 2).astype(float)
        intrinsic = resize_intrinsics(cam.k, cam.width, cam.height, width, height)
        prepared[index] = (cam, intrinsic, width, height, long_lines, rgb)
        frame_records.append({"frameIndex": index, "epoch": cam.epoch,
                              "detectedLineCount": len(native), "longLineCount": len(long_lines),
                              "longLines": long_lines.tolist()})
    checks = []
    for line in training_result["localLines"]:
        source = frozen_candidates[line["candidateId"]]
        reference = np.asarray(source["sourceAppearance"]["sideColors"][0])
        source_epoch = camera(frames[line["sourceFrames"][0]]).epoch
        endpoints = np.asarray(line["endpointsWorld"], dtype=float)
        for index in indices:
            if prepared[index][0].epoch != source_epoch:
                checks.append({"candidateId": line["candidateId"], "frameIndex": index,
                               "predictedVisible": False, "lineMatches": [],
                               "reason": "different_epoch", "passed": False})
                continue
            check = _third_check(prepared[index], endpoints, config)
            supported = []
            for match in check["lineMatches"]:
                observed = np.asarray(match["appearance"]) if match["appearance"] is not None else None
                if observed is None:
                    match["reason"] = "evaluation_appearance_unavailable"
                    continue
                contrast = float(np.linalg.norm(observed[0] - observed[1]))
                difference = (_chromaticity_difference(reference, observed)
                              if appearance_mode == "normalized_chromaticity"
                              else _appearance_difference(reference, observed))
                match["appearanceDifference"] = difference
                match["sideContrast"] = contrast
                if contrast < config.minimum_side_contrast:
                    match["reason"] = "low_evaluation_side_contrast"
                elif difference > appearance_limit:
                    match["reason"] = "evaluation_appearance_mismatch"
                else:
                    match["reason"] = "appearance_supported"
                    supported.append(match)
            check["reason"] = ("line_supported" if supported else
                               "evaluation_appearance_mismatch" if check["lineMatches"]
                               else check["reason"])
            checks.append({"candidateId": line["candidateId"], "frameIndex": index,
                           **check, "passed": bool(supported)})
    from collections import Counter
    reasons = dict(Counter(check["reason"] for check in checks))
    return {"status": "frozen_line_structure_evaluation", "productAccepted": False,
            "completeWall": False, "metricAccuracyVerified": False,
            "trainingCandidateCount": len(training_result["localLines"]),
            "evaluationFrameIndices": indices,
            "appearanceMode": appearance_mode,
            "appearanceLimit": appearance_limit,
            "appearanceLimitSource": ("training_supported_views_p90"
                                      if calibration_count is not None else "frozen_training_config"),
            "appearanceCalibrationTrainingMatchCount": calibration_count,
            "pairCount": len(checks), "geometricallyVisibleCount": sum(c["predictedVisible"] for c in checks),
            "appearanceSupportedCount": sum(c["passed"] for c in checks),
            "reasonCounts": reasons, "frames": frame_records, "checks": checks,
            "elapsedSeconds": time.monotonic() - started}
