"""Evidence-backed sparse triangulation in raw ARKit coordinates, never semantic walls.

SIFT is the existing source-built extractor. Image-only F/H support precedes
triangulation using immutable input K/T. Reprojection checks are fitting checks,
not an independent scale/drift ground truth or a pose optimization.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import math
import time

import numpy as np

from .spatial_image_quality import cv_support, sift_features, sift_matches
from .spatial_structure import recover_structure

ALGORITHM_VERSION = "sparse-sift-structure-support-v6"
_AXES = np.diag([1., -1., -1., 1.])


def resize_intrinsics(k, source_width, source_height, target_width, target_height):
    """PIL pixel-center resize: target = (source + .5) * scale - .5."""
    if min(source_width, source_height, target_width, target_height) <= 0:
        raise ValueError("invalid_resize_dimensions")
    sx, sy = target_width / source_width, target_height / source_height
    pixel_transform = np.array([[sx, 0., (sx - 1.) / 2.],
                                [0., sy, (sy - 1.) / 2.], [0., 0., 1.]])
    return pixel_transform @ np.asarray(k, dtype=float)


@dataclass(frozen=True)
class Camera:
    k: np.ndarray
    camera_to_world: np.ndarray
    world_to_camera: np.ndarray
    epoch: int
    time: float
    width: int
    height: int

    @property
    def center(self):
        return self.camera_to_world[:3, 3]


def camera(frame: dict) -> Camera:
    """Column-major native K/T. Display orientation is deliberately not applied."""
    k = np.asarray(frame["intrinsics"], dtype=float).reshape(3, 3, order="F")
    t = np.asarray(frame["cameraToWorld"], dtype=float).reshape(4, 4, order="F")
    timestamp = float(frame["time"])
    pose = frame["pose"]
    width, height = int(frame["width"]), int(frame["height"])
    if not np.isfinite(k).all() or not np.isfinite(t).all() or not math.isfinite(timestamp):
        raise ValueError("non_finite_camera_parameters")
    if k[0, 0] <= 0 or k[1, 1] <= 0 or not np.allclose(k[2], [0, 0, 1], atol=1e-6):
        raise ValueError("invalid_intrinsics")
    rotation = t[:3, :3]
    if (not np.allclose(t[3], [0, 0, 0, 1], atol=1e-5)
            or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-4)
            or abs(np.linalg.det(rotation) - 1) > 1e-4):
        raise ValueError("invalid_camera_transform")
    if width <= 0 or height <= 0 or not math.isfinite(float(pose["time"])) or abs(timestamp - float(pose["time"])) > 1e-6:
        raise ValueError("image_pose_time_mismatch")
    position = np.array([pose["position"][axis] for axis in "xyz"], dtype=float)
    if not np.isfinite(position).all() or np.linalg.norm(position - t[:3, 3]) > .001:
        raise ValueError("camera_pose_position_mismatch")
    forward = np.array([pose["forward"][axis] for axis in "xyz"], dtype=float)
    if not np.isfinite(forward).all() or np.linalg.norm(forward + t[:3, 2]) > .001:
        raise ValueError("camera_pose_forward_mismatch")
    epoch = int(pose["epoch"])
    if epoch < 0 or epoch != pose["epoch"]:
        raise ValueError("invalid_epoch")
    return Camera(k, t, _AXES @ np.linalg.inv(t), epoch, timestamp, width, height)


def project(cam: Camera, position: np.ndarray):
    """Native JPEG pixel coordinates and positive CV camera depth in metres."""
    local = cam.world_to_camera @ np.r_[position, 1.]
    if not np.isfinite(local).all() or local[2] <= 1e-8:
        raise ValueError("negative_depth")
    pixel = cam.k @ local[:3]
    return pixel[:2] / pixel[2], float(local[2])


def _world_ray(cam: Camera, pixel):
    ray = np.linalg.solve(cam.k, np.r_[pixel, 1.])
    ray = cam.world_to_camera[:3, :3].T @ ray
    return ray / np.linalg.norm(ray)


def _pixel_objective(cameras, pixels, position):
    """Pixel residuals and analytic world-coordinate Jacobian; immutable K/T."""
    residuals, jacobians = [], []
    for cam, pixel in zip(cameras, pixels):
        local = cam.world_to_camera @ np.r_[position, 1.]
        if not np.isfinite(local).all() or local[2] <= 1e-8:
            raise ValueError("negative_depth")
        q = cam.k @ local[:3]
        projection = q[:2] / q[2]
        derivative = (cam.k[:2] * q[2] - q[:2, None] * cam.k[2]) / q[2] ** 2
        residuals.extend(projection - pixel)
        jacobians.extend(derivative @ cam.world_to_camera[:3, :3])
    return np.asarray(residuals), np.asarray(jacobians)


def refine_point(cameras, pixels, position, *, maximum_iterations=15):
    """Damped Gauss-Newton, training observations only; never erase an outlier.

    Accept only strictly smaller finite pixel SSE with positive depth. If a step
    is invalid/ill-conditioned, retain the preceding point and record its outcome.
    This optimizes one point, not camera poses, scale or bundle adjustment.
    """
    point = np.asarray(position, dtype=float).copy()
    residual, jacobian = _pixel_objective(cameras, pixels, point)
    initial = cost = float(residual @ residual)
    damping, accepted, rejected = 1e-3, 0, 0
    for _ in range(maximum_iterations):
        normal = jacobian.T @ jacobian
        scale = np.maximum(np.diag(normal), 1e-12)
        try:
            step = np.linalg.solve(normal + damping * np.diag(scale), -jacobian.T @ residual)
            candidate = point + step
            candidate_residual, candidate_jacobian = _pixel_objective(cameras, pixels, candidate)
            candidate_cost = float(candidate_residual @ candidate_residual)
        except (ValueError, np.linalg.LinAlgError):
            candidate_cost = float("inf")
        if math.isfinite(candidate_cost) and candidate_cost < cost:
            decrease = cost - candidate_cost
            point, residual, jacobian = candidate, candidate_residual, candidate_jacobian
            cost, accepted = candidate_cost, accepted + 1
            damping = max(damping / 3., 1e-12)
            if np.linalg.norm(step) <= 1e-9 * (1. + np.linalg.norm(point)) or decrease <= 1e-12 * (1. + cost):
                break
        else:
            rejected += 1
            damping *= 10.
            if damping > 1e12:
                break
    return point, {"initialTrainingSSEPixels": initial, "finalTrainingSSEPixels": cost,
                   "acceptedSteps": accepted, "rejectedSteps": rejected}


def triangulate(cameras: list[Camera], pixels: list[np.ndarray], *,
                minimum_parallax_degrees=1., maximum_reprojection_pixels=3.,
                refine=False, refinement_stats=None):
    """Normalized DLT plus optional pixel refinement; every training view must pass."""
    if len(cameras) < 2 or len(cameras) != len(pixels):
        raise ValueError("insufficient_observations")
    if len({cam.epoch for cam in cameras}) != 1:
        raise ValueError("different_epoch")
    rays = [_world_ray(cam, pixel) for cam, pixel in zip(cameras, pixels)]
    angles = [math.degrees(math.acos(float(np.clip(a @ b, -1, 1))))
              for i, a in enumerate(rays) for b in rays[i + 1:]]
    parallax = max(angles, default=0.)
    if parallax < minimum_parallax_degrees:
        raise ValueError("small_parallax")
    rows = []
    for cam, pixel in zip(cameras, pixels):
        normalized = np.linalg.solve(cam.k, np.r_[pixel, 1.])
        normalized /= normalized[2]
        p = cam.world_to_camera[:3]
        rows.extend([normalized[0] * p[2] - p[0], normalized[1] * p[2] - p[1]])
    _, singular, right = np.linalg.svd(np.asarray(rows), full_matrices=False)
    point = right[-1]
    if (abs(point[3]) < 1e-10 or singular[-2] < 1e-10
            or not np.isfinite(point).all()):
        raise ValueError("triangulation_at_infinity_or_degenerate")
    position = point[:3] / point[3]
    # Experimental: fixed real holdout comparison did not justify default enablement.
    if refine:
        position, statistics = refine_point(cameras, pixels, position)
        if refinement_stats is not None:
            refinement_stats.append(statistics)
    projected, depths, errors = [], [], []
    for cam, pixel in zip(cameras, pixels):
        prediction, depth = project(cam, position)
        error = float(np.linalg.norm(prediction - pixel))
        if not math.isfinite(error) or error > maximum_reprojection_pixels:
            raise ValueError("high_reprojection_error")
        projected.append(prediction)
        depths.append(depth)
        errors.append(error)
    return position, projected, depths, errors, parallax


class _Tracks:
    """Identity is (frame index, feature index), never proximity in 3D."""
    def __init__(self):
        self.parent = {}
        self.members = {}

    def find(self, key):
        if key not in self.parent:
            self.parent[key] = key
            self.members[key] = {key[0]: key[1]}
        root = key
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[key] != key:
            previous = self.parent[key]
            self.parent[key] = root
            key = previous
        return root

    def join(self, a, b):
        left, right = self.find(a), self.find(b)
        if left == right:
            return True
        if any(frame in self.members[left] and self.members[left][frame] != feature
               for frame, feature in self.members[right].items()):
            return False
        if len(self.members[left]) < len(self.members[right]):
            left, right = right, left
        self.parent[right] = left
        self.members[left].update(self.members.pop(right))
        return True



def _convex_hull(points):
    values = sorted(set(map(tuple, points)))
    if len(values) < 3:
        return values
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for point in values:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    for point in reversed(values):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def _inside_polygon(points, polygon):
    inside = np.zeros(len(points), dtype=bool)
    for a, b in zip(polygon, np.roll(polygon, -1, axis=0)):
        if abs(b[1] - a[1]) < 1e-12:
            continue
        crossing = (a[1] > points[:, 1]) != (b[1] > points[:, 1])
        boundary_x = (b[0] - a[0]) * (points[:, 1] - a[1]) / (b[1] - a[1]) + a[0]
        inside ^= crossing & (points[:, 0] < boundary_x)
    return inside


def geometric_boundaries(points: list[dict], surfaces: list[dict], *,
                         minimum_support=30, distance_threshold=.03,
                         minimum_span=.3, maximum_candidates=8):
    """Convex envelopes of verified point-plane support, never semantic/complete boundaries."""
    verified = [p for p in points if p["verifiedInThirdView"]]
    output, rejected = [], []
    rng = np.random.default_rng(51)
    for epoch in sorted({p["epoch"] for p in verified}):
        evidence = [p for p in verified if p["epoch"] == epoch]
        cloud = np.array([[p["position"][axis] for axis in "xyz"] for p in evidence])
        # Independent spatial cells stop duplicate SIFT orientations inflating plane confidence.
        if not len(cloud):
            continue
        _, representatives = np.unique(np.floor(cloud / .02).astype(np.int64), axis=0, return_index=True)
        active = sorted(representatives.tolist())
        attempts = 0
        while len(active) >= minimum_support and len(output) < maximum_candidates and attempts < 16:
            attempts += 1
            xyz = cloud[active]
            best = np.zeros(len(xyz), dtype=bool)
            for _ in range(256):
                sample = xyz[rng.choice(len(xyz), 3, replace=False)]
                normal = np.cross(sample[1] - sample[0], sample[2] - sample[0])
                length = np.linalg.norm(normal)
                if length < 1e-8:
                    continue
                normal /= length
                mask = np.abs((xyz - sample[0]) @ normal) <= distance_threshold
                if mask.sum() > best.sum():
                    best = mask
            if best.sum() < minimum_support:
                rejected.append({"epoch": epoch, "reason": "insufficient_verified_plane_support",
                                 "bestSpatialSupportCount": int(best.sum())})
                break
            sample = xyz[best]
            center = sample.mean(axis=0)
            _, _, basis = np.linalg.svd(sample - center, full_matrices=False)
            normal, u, v = basis[-1], basis[0], basis[1]
            representative_distances = np.abs((xyz - center) @ normal)
            fit = representative_distances <= distance_threshold
            selected = np.array(active)[fit]
            active = [i for i, keep in zip(active, fit) if not keep]
            if len(selected) < minimum_support:
                rejected.append({"epoch": epoch, "reason": "insufficient_refined_plane_support",
                                 "bestSpatialSupportCount": len(selected)})
                continue
            flat = (cloud[selected] - center) @ np.array([u, v]).T
            span = np.ptp(flat, axis=0)
            if max(span) < minimum_span or min(span) < .15:
                rejected.append({"epoch": epoch, "reason": "line_or_insufficient_span",
                                 "spanMeters": span.tolist(), "spatialSupportCount": len(selected)})
                continue
            # Only the verified spatial representatives shape the envelope.
            hull = np.array(_convex_hull(flat))
            vertices = center + hull[:, 0, None] * u + hull[:, 1, None] * v
            fit_points = [evidence[i] for i in selected]
            fit_cloud = cloud[selected]
            distances = np.abs((fit_cloud - center) @ normal)
            nearby_unverified = sum(not p["verifiedInThirdView"] and p["epoch"] == epoch
                and abs((np.array([p["position"][axis] for axis in "xyz"]) - center) @ normal) <= distance_threshold
                for p in points)
            associations = []
            for surface in surfaces:
                if surface.get("epoch") != epoch or not surface.get("observed", True):
                    continue
                source = np.array([[vertex[axis] for axis in "xyz"] for vertex in surface.get("vertices", [])], dtype=float)
                if len(source) < 3 or not np.isfinite(source).all():
                    continue
                source_center = source.mean(axis=0)
                _, source_singular, source_basis = np.linalg.svd(source - source_center, full_matrices=False)
                if source_singular[1] < 1e-6:
                    continue
                source_normal = source_basis[-1]
                angle = math.degrees(math.acos(float(np.clip(abs(normal @ source_normal), 0, 1))))
                residual = np.abs((fit_cloud - source_center) @ source_normal)
                if angle > 15 or np.median(residual) > .15:
                    continue
                source_uv = (source - source_center) @ source_basis[:2].T
                support_uv = (fit_cloud - source_center) @ source_basis[:2].T
                associations.append({"surfaceId": surface["id"], "normalAngleDegrees": angle,
                    "medianDistanceMeters": float(np.median(residual)),
                    "p95DistanceMeters": float(np.percentile(residual, 95)),
                    "withinObservedBoundaryFraction": float(_inside_polygon(support_uv, source_uv).mean())})
            area = abs(float(np.sum(hull[:, 0] * np.roll(hull[:, 1], -1) - hull[:, 1] * np.roll(hull[:, 0], -1)))) / 2
            output.append({"id": f"geometry-{len(output)}", "epoch": epoch,
                "normal": dict(zip("xyz", normal.tolist())), "offsetMeters": -float(normal @ center),
                "vertices": [dict(zip("xyz", vertex.tolist())) for vertex in vertices],
                "supportPointIDs": [point["id"] for point in fit_points],
                "verifiedSupportCount": len(fit_points), "uniqueSpatialSupportCount": len(selected),
                "verifiedSupportFraction": 1., "nearbyUnverifiedPointCount": int(nearby_unverified),
                "distinctFrameCount": len({o["frameIndex"] for p in fit_points for o in p["observations"]}),
                "medianPointPlaneMeters": float(np.median(distances)),
                "p95PointPlaneMeters": float(np.percentile(distances, 95)),
                "spanMeters": span.tolist(), "supportEnvelopeAreaSquareMeters": area,
                "boundaryKind": "convex_support_envelope", "semanticLabel": None,
                "semanticVerified": False, "completeBoundary": False,
                "source": "triangulated_third_view_holdout_verified_points",
                "observedSurfaceAssociations": associations})
    return output, {"eligibleVerifiedPointCount": len(verified), "candidateCount": len(output),
        "maximumCandidates": maximum_candidates, "minimumSpatialSupport": minimum_support,
        "spatialCellMeters": .02, "maximumPointPlaneMeters": distance_threshold,
        "minimumMajorSpanMeters": minimum_span, "minimumMinorSpanMeters": .15,
        "ransacIterations": 256, "rejectedCandidates": rejected,
        "capacityLimited": len(output) >= maximum_candidates}

def sample_point_colors(points, frames, directory):
    """Read one original JPEG at a time; measured RGB never changes geometry."""
    from PIL import Image
    buckets = {}
    for point in points:
        source = next(obs for obs in point["observations"] if obs["usedForTriangulation"])
        buckets.setdefault(source["frameIndex"], []).append((point, source))
    for index, entries in buckets.items():
        path = directory / frames[index]["imageFile"]
        try:
            with Image.open(path) as image:
                image = image.convert("RGB")
                if image.size != (frames[index]["width"], frames[index]["height"]):
                    raise ValueError("color_image_dimensions_mismatch")
                for point, source in entries:
                    x, y = (int(round(v)) for v in source["pixel"])
                    if not (0 <= x < image.width and 0 <= y < image.height):
                        point["colorSamplingError"] = "color_pixel_outside_image"
                        continue
                    patch = np.asarray(image.crop((max(0, x-1), max(0, y-1),
                        min(image.width, x+2), min(image.height, y+2))))
                    point["colorRGB"] = np.rint(patch.mean(axis=(0, 1))).astype(int).tolist()
                    point["colorSource"] = {"frameIndex": index, "pixel": source["pixel"],
                        "method": "original_jpeg_3x3_mean", "displayRotationApplied": False}
        except (OSError, ValueError) as exc:
            for point, _ in entries:
                point["colorSamplingError"] = str(exc)


def reconstruct(document: dict, directory: Path, *, maximum_pairs=320,
                pair_offsets=(1, 3, 6), minimum_matches=12,
                minimum_inlier_fraction=.5, minimum_baseline_meters=.03,
                minimum_parallax_degrees=1., maximum_reprojection_pixels=3.,
                maximum_points=20000, refine_points=False, progress: Callable[[str], None] | None = None):
    started = time.perf_counter()
    frames = document.get("keyframes", [])
    if (len(frames) > 240 or not 1 <= maximum_pairs <= 720
            or not 1 <= maximum_points <= 20000 or minimum_matches < 8
            or not 0 < minimum_inlier_fraction <= 1
            or not all(math.isfinite(x) and x > 0 for x in
                       (minimum_baseline_meters, minimum_parallax_degrees, maximum_reprojection_pixels))
            or not pair_offsets or any(not isinstance(x, int) or x <= 0 for x in pair_offsets)):
        raise ValueError("invalid_reconstruction_limits")
    candidate_pairs = sorted({(i, i + offset) for i in range(len(frames))
                              for offset in pair_offsets if i + offset < len(frames)})
    if len(candidate_pairs) > maximum_pairs:
        selected = np.unique(np.linspace(0, len(candidate_pairs) - 1, maximum_pairs, dtype=int))
        selected_pairs = [candidate_pairs[i] for i in selected]
    else:
        selected_pairs = candidate_pairs
    result = {
        "algorithmVersion": ALGORITHM_VERSION, "status": "insufficient_geometry",
        "pointRefinement": {"enabled": refine_points, "method": "training-only damped Gauss-Newton in native pixels", "holdoutUsed": False},
        "originalPreserved": True, "scaleVerified": False, "poseOptimized": False,
        "semanticStructureVerified": False, "points": [], "pairs": [], "frameProjections": [],
        "rejectedTracks": [], "frames": [], "geometricBoundaries": [], "featureTracks": [],
        "coordinateConvention": "Raw ARKit world metres, +Y up; native JPEG pixels. CV axes = diag(1,-1,-1) * inverse(cameraToWorld). Display transforms are presentation only.",
        "limitations": ["Point scale is inherited from raw ARKit poses, not independently verified.",
                         "Two-view reprojection is fitting consistency only. Tracks with at least three views reserve their final frame for independent holdout projection, excluded from DLT and point refinement.",
                         "No bundle adjustment, distortion calibration, semantic walls or complete room boundaries."],
        "thresholds": {"minimumMatches": minimum_matches, "minimumInlierFraction": minimum_inlier_fraction,
                       "minimumBaselineMeters": minimum_baseline_meters,
                       "minimumParallaxDegrees": minimum_parallax_degrees,
                       "maximumReprojectionPixels": maximum_reprojection_pixels,
                       "imageRansacThresholdNativePixels": 3., "pairOffsets": list(pair_offsets)},
        "limits": {"maximumFrames": 240, "maximumPairs": maximum_pairs, "maximumPoints": maximum_points,
                   "candidatePairCount": len(candidate_pairs),
                   "pairCoverageLimited": len(candidate_pairs) > len(selected_pairs), "pointCapacityExceeded": False},
    }
    directory = Path(directory).resolve()
    cameras, feature_data, failures = {}, {}, {}
    extraction_started = time.perf_counter()
    dependency_error = None
    try:
        from .spatial_sift_native import library
        library()
    except RuntimeError as exc:
        dependency_error = str(exc).split(":", 1)[0]
    for index, frame in enumerate(frames):
        record = {"frameIndex": index, "time": frame.get("time"), "status": "failed",
                  "displayOrientationKnown": frame.get("displayOrientation") is not None}
        try:
            if dependency_error:
                raise RuntimeError(dependency_error)
            cameras[index] = camera(frame)
            path = directory / frame["imageFile"]
            if path.resolve().parent != directory:
                raise ValueError("unsafe_image_path")
            feature_data[index] = sift_features(path, expected_size=(frame["width"], frame["height"]))
            record.update(status="extracted", featureCount=len(feature_data[index][0]))
        except (OSError, KeyError, ValueError, TypeError, RuntimeError, np.linalg.LinAlgError) as exc:
            failures[index] = str(exc)
            record["reason"] = str(exc)
        result["frames"].append(record)
        if progress and (index + 1) % 16 == 0:
            progress(f"SIFT frames {index + 1}/{len(frames)}")
    extraction_ms = (time.perf_counter() - extraction_started) * 1000
    tracks = _Tracks()
    pair_match_refs = []
    rejected_matches = Counter()
    pair_started = time.perf_counter()
    for pair_index, (ia, ib) in enumerate(selected_pairs):
        item = {"frameA": ia, "frameB": ib, "status": "rejected", "matches": [],
                "startTime": frames[ia].get("time"), "endTime": frames[ib].get("time")}
        try:
            if ia in failures or ib in failures:
                raise ValueError("frame_unavailable: " + failures.get(ia, failures.get(ib, "")))
            a, b = cameras[ia], cameras[ib]
            if a.epoch != b.epoch:
                raise ValueError("different_epoch")
            if b.time <= a.time:
                raise ValueError("non_increasing_time")
            baseline = float(np.linalg.norm(a.center - b.center))
            item["baselineMeters"] = baseline
            pa, da = feature_data[ia]
            pb, db = feature_data[ib]
            matches = sift_matches(da, db)
            item["matchCount"] = len(matches)
            for fa, fb in matches:
                item["matches"].append({"a": pa[fa].tolist(), "b": pb[fb].tolist(),
                                        "inlier": False, "accepted": False})
            if len(matches) < minimum_matches:
                raise ValueError("insufficient_texture_or_matches")
            support, method = cv_support(pa[matches[:, 0]], pb[matches[:, 1]])
            item.update(imageIndependentInlierCount=int(support.sum()),
                        imageIndependentInlierFraction=float(support.mean()),
                        imageIndependentSupportMethod=method)
            for match, supported in zip(item["matches"], support):
                match["inlier"] = bool(supported)
            if support.sum() < minimum_matches or support.mean() < minimum_inlier_fraction:
                raise ValueError("unreliable_independent_support")
            # Image-only identities form tracks BEFORE any AR pose or triangulation check.
            # A later held-out view therefore cannot be selected by its raw AR reprojection fit.
            errors = []
            for match_index, (fa, fb) in enumerate(matches):
                evidence = item["matches"][match_index]
                if not support[match_index]:
                    evidence["reason"] = "independent_image_outlier"
                    rejected_matches[evidence["reason"]] += 1
                    continue
                if not tracks.join((ia, int(fa)), (ib, int(fb))):
                    evidence["reason"] = "track_identity_conflict"
                    rejected_matches[evidence["reason"]] += 1
                    continue
                evidence["imageTrackIdentityAccepted"] = True
                pair_match_refs.append((evidence, (ia, int(fa))))
                # Two-view K/T fit remains a diagnosis only. It never gates track formation.
                try:
                    if baseline < minimum_baseline_meters:
                        raise ValueError("low_baseline")
                    _, _, _, residual, _ = triangulate([a, b], [pa[fa], pb[fb]],
                        minimum_parallax_degrees=minimum_parallax_degrees,
                        maximum_reprojection_pixels=maximum_reprojection_pixels, refine=False)
                    evidence["rawPairTriangulationValid"] = True
                    evidence["rawPairReprojectionErrorPixels"] = max(residual)
                    errors.extend(residual)
                except (ValueError, np.linalg.LinAlgError) as exc:
                    evidence["rawPairTriangulationValid"] = False
                    evidence["rawPairRejectionReason"] = str(exc)
            item["rawPairValidTriangulationCount"] = sum(m.get("rawPairTriangulationValid", False) for m in item["matches"])
            item["rawPairStatus"] = "valid" if errors else "rejected"
            if errors:
                item["rawPairMedianReprojectionPixels"] = float(np.median(errors))
            else:
                item["rawPairRejectionReason"] = "low_baseline" if baseline < minimum_baseline_meters else "no_valid_triangulations"
            item["status"] = "image_supported"
        except (OSError, KeyError, ValueError, TypeError, RuntimeError, np.linalg.LinAlgError) as exc:
            item["reason"] = str(exc)
        result["pairs"].append(item)
        if progress and (pair_index + 1) % 32 == 0:
            progress(f"Geometric pairs {pair_index + 1}/{len(selected_pairs)}")
    pair_ms = (time.perf_counter() - pair_started) * 1000
    point_started = time.perf_counter()
    point_ids = {}
    residuals = []
    refinement_statistics = []
    holdout_errors = []
    result["holdoutDiagnostics"] = []
    holdout_attempts = holdout_passes = holdout_failures = 0
    candidate_holdout_tracks = training_rejected_holdout_tracks = 0
    by_frame = {i: [] for i in range(len(frames))}
    for root, members in sorted(tracks.members.items()):
        if len(members) < 2:
            continue
        indices = sorted(members)
        observations = [feature_data[i][0][members[i]] for i in indices]
        result["featureTracks"].append({"id": f"track-{root[0]}-{root[1]}",
            "epoch": cameras[indices[0]].epoch,
            "observations": [{"frameIndex": i, "featureIndex": members[i], "pixel": pixel.tolist()}
                             for i, pixel in zip(indices, observations)]})
        has_holdout = len(indices) >= 3
        if has_holdout:
            candidate_holdout_tracks += 1
        holdout_attempted = False
        try:
            training_indices = indices[:-1] if has_holdout else indices
            training_pixels = observations[:-1] if has_holdout else observations
            training_cameras = [cameras[i] for i in training_indices]
            training_baseline = max((np.linalg.norm(a.center - b.center)
                for i, a in enumerate(training_cameras) for b in training_cameras[i + 1:]), default=0.)
            if training_baseline < minimum_baseline_meters:
                raise ValueError("low_training_baseline")
            position, projected, depths, errors, parallax = triangulate(
                training_cameras, training_pixels,
                minimum_parallax_degrees=minimum_parallax_degrees,
                maximum_reprojection_pixels=maximum_reprojection_pixels, refine=refine_points,
                refinement_stats=refinement_statistics)
            holdout_error = None
            if has_holdout:
                holdout_attempts += 1
                holdout_attempted = True
                diagnostic = {"frameIndex": indices[-1], "observedPixel": observations[-1].tolist(),
                    "predictedPixel": None, "errorPixels": None, "status": "failed",
                    "reason": "negative_depth", "sourceTrainingFrames": training_indices,
                    "sourceObservations": [[i, members[i]] for i in indices],
                    "trainingPixels": [pixel.tolist() for pixel in training_pixels],
                    "trainingPosition": position.tolist()}
                result["holdoutDiagnostics"].append(diagnostic)
                prediction, depth = project(cameras[indices[-1]], position)
                holdout_error = float(np.linalg.norm(prediction - observations[-1]))
                diagnostic.update(predictedPixel=prediction.tolist(), errorPixels=holdout_error,
                    reason="holdout_reprojection_failed")
                if math.isfinite(holdout_error):
                    holdout_errors.append(holdout_error)
                if not math.isfinite(holdout_error) or holdout_error > maximum_reprojection_pixels:
                    holdout_failures += 1
                    result["rejectedTracks"].append({
                        "sourceObservations": [[i, members[i]] for i in indices],
                        "reason": "holdout_reprojection_failed", "holdoutFrameIndex": indices[-1],
                        "holdoutErrorPixels": holdout_error})
                    continue
                diagnostic.update(status="passed", reason=None)
                holdout_passes += 1
                projected.append(prediction)
                depths.append(depth)
                errors.append(holdout_error)
            if len(result["points"]) >= maximum_points:
                result["limits"]["pointCapacityExceeded"] = True
                raise ValueError("point_capacity_exceeded")
            point_id = f"point-{len(result['points'])}"
            evidence = [{"frameIndex": i, "featureIndex": members[i], "pixel": pixel.tolist(),
                         "projectedPixel": prediction.tolist(), "depthMeters": depth,
                         "reprojectionErrorPixels": error,
                         "usedForTriangulation": not has_holdout or i != indices[-1]}
                        for i, pixel, prediction, depth, error in zip(indices, observations, projected, depths, errors)]
            result["points"].append({"id": point_id, "position": dict(zip("xyz", position.tolist())),
                                     "epoch": cameras[indices[0]].epoch, "observations": evidence,
                                     "parallaxDegrees": parallax,
                                     "verifiedInThirdView": has_holdout,
                                     "supportFrames": training_indices,
                                     "holdoutFrameIndex": indices[-1] if has_holdout else None,
                                     "holdoutErrorPixels": holdout_error})
            point_ids[root] = point_id
            residuals.extend(errors)
            for entry in evidence:
                by_frame[entry["frameIndex"]].append({"pointId": point_id,
                    "pixel": entry["projectedPixel"], "observedPixel": entry["pixel"],
                    "reprojectionErrorPixels": entry["reprojectionErrorPixels"], "depthMeters": entry["depthMeters"],
                    "usedForTriangulation": entry["usedForTriangulation"], "verifiedInThirdView": has_holdout})
        except (ValueError, np.linalg.LinAlgError) as exc:
            if has_holdout and not holdout_attempted:
                training_rejected_holdout_tracks += 1
            elif has_holdout and str(exc) == "negative_depth":
                holdout_failures += 1
            result["rejectedTracks"].append({"sourceObservations": [[i, members[i]] for i in indices],
                                             "reason": str(exc)})
    for evidence, key in pair_match_refs:
        root = tracks.find(key)
        if root in point_ids:
            evidence["pointId"] = point_ids[root]
            evidence["accepted"] = True
        else:
            evidence["accepted"] = False
            evidence["reason"] = "multi_view_track_rejected"
            rejected_matches["multi_view_track_rejected"] += 1
    # Final pair acceptance counts/errors follow committed tracks, not provisional DLT candidates.
    points_by_id = {point["id"]: point for point in result["points"]}
    for pair in result["pairs"]:
        pair["pointCount"] = sum("pointId" in m for m in pair["matches"])
        pair["verifiedPointCount"] = sum(points_by_id[m["pointId"]]["verifiedInThirdView"]
                                         for m in pair["matches"] if "pointId" in m)
        final_errors = [obs["reprojectionErrorPixels"] for m in pair["matches"] if "pointId" in m
                        for obs in points_by_id[m["pointId"]]["observations"]
                        if obs["frameIndex"] in (pair["frameA"], pair["frameB"])]
        if "medianReprojectionPixels" in pair:
            pair["provisionalMedianReprojectionPixels"] = pair.pop("medianReprojectionPixels")
        if final_errors:
            pair["medianReprojectionPixels"] = float(np.median(final_errors))
        if pair["pointCount"]:
            pair["status"] = "reconstructed"
        elif pair["status"] == "image_supported":
            pair.update(status="rejected", reason="multi_view_tracks_rejected")
    result["frameProjections"] = [{"frameIndex": i, "time": frame.get("time"),
        "width": frame.get("width"), "height": frame.get("height"), "projections": by_frame[i]}
        for i, frame in enumerate(frames)]
    result["pointRefinement"].update(
        attemptedTrackCount=len(refinement_statistics),
        improvedTrackCount=sum(s["finalTrainingSSEPixels"] < s["initialTrainingSSEPixels"] for s in refinement_statistics),
        initialTrainingSSEPixels=sum(s["initialTrainingSSEPixels"] for s in refinement_statistics),
        finalTrainingSSEPixels=sum(s["finalTrainingSSEPixels"] for s in refinement_statistics),
        acceptedSteps=sum(s["acceptedSteps"] for s in refinement_statistics),
        rejectedSteps=sum(s["rejectedSteps"] for s in refinement_statistics))
    result["summary"] = {"frameCount": len(frames), "featureFrameCount": len(feature_data),
        "selectedPairCount": len(selected_pairs),
        "reconstructedPairCount": sum(p["status"] == "reconstructed" for p in result["pairs"]),
        "pointCount": len(result["points"]), "observationCount": len(residuals),
        "verifiedPointCount": sum(p["verifiedInThirdView"] for p in result["points"]),
        "unverifiedTwoViewPointCount": sum(not p["verifiedInThirdView"] for p in result["points"]),
        "candidateHoldoutTrackCount": candidate_holdout_tracks,
        "holdoutTrainingRejectedCount": training_rejected_holdout_tracks,
        "holdoutAttemptCount": holdout_attempts, "holdoutPassCount": holdout_passes,
        "holdoutFailCount": holdout_failures, "finiteHoldoutResidualCount": len(holdout_errors),
        "medianHoldoutPixels": float(np.median(holdout_errors)) if holdout_errors else None,
        "p95HoldoutPixels": float(np.percentile(holdout_errors, 95)) if holdout_errors else None,
        "passedOnlyMedianHoldoutPixels": float(np.median([p["holdoutErrorPixels"] for p in result["points"] if p["verifiedInThirdView"]])) if any(p["verifiedInThirdView"] for p in result["points"]) else None,
        "passedOnlyP95HoldoutPixels": float(np.percentile([p["holdoutErrorPixels"] for p in result["points"] if p["verifiedInThirdView"]], 95)) if any(p["verifiedInThirdView"] for p in result["points"]) else None,
        "rawPairRejectedReasons": dict(Counter(p.get("rawPairRejectionReason", "unknown") for p in result["pairs"] if p.get("rawPairStatus") == "rejected")),
        "rejectedPairReasons": dict(Counter(p.get("reason", "unknown") for p in result["pairs"] if p["status"] != "reconstructed")),
        "rejectedMatchReasons": dict(rejected_matches), "rejectedTrackCount": len(result["rejectedTracks"]),
        "rejectedTrackReasons": dict(Counter(t["reason"] for t in result["rejectedTracks"])) ,
        "medianReprojectionPixels": float(np.median(residuals)) if residuals else None,
        "p95ReprojectionPixels": float(np.percentile(residuals, 95)) if residuals else None,
        "maximumReprojectionPixels": max(residuals) if residuals else None}
    if result["points"]:
        result["status"] = "sparse_geometry"
    if dependency_error:
        result.update(status="not_available", reason=dependency_error)
    if result["limits"]["pointCapacityExceeded"]:
        result.update(status="capacity_limited", reason="point_capacity_exceeded")
    sample_point_colors(result["points"], frames, directory)
    boundary_started = time.perf_counter()
    result["geometricBoundaries"], result["boundaryDiagnostics"] = geometric_boundaries(
        result["points"], document.get("surfaces", []))
    result["summary"]["geometricBoundaryCount"] = len(result["geometricBoundaries"])
    result["structuralGeometry"] = recover_structure(result["points"], result["geometricBoundaries"])
    result["timings"] = {"featureExtractionMilliseconds": extraction_ms,
        "pairGeometryMilliseconds": pair_ms,
        "multiViewMilliseconds": (boundary_started - point_started) * 1000,
        "boundaryExtractionMilliseconds": (time.perf_counter() - boundary_started) * 1000,
        "elapsedMilliseconds": (time.perf_counter() - started) * 1000}
    return result
