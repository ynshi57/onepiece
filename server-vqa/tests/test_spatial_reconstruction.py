"""Analytic camera geometry plus explicitly injected correspondence tests.

Injected correspondences test triangulation/holdout logic, not SIFT quality.
The actual 104-photo source-SIFT run is documented separately.
"""
from copy import deepcopy
import math

import numpy as np
import pytest

from app import spatial_reconstruction as reconstruction


def frame(index, x=0., epoch=1):
    k = np.array([[500., 0, 320], [0, 510., 240], [0, 0, 1]])
    t = np.eye(4)
    t[0, 3] = x
    return {"id": f"frame-{index}", "imageFile": f"frame-{index}.jpg", "time": 10. + index,
            "width": 640, "height": 480, "intrinsics": k.ravel(order="F").tolist(),
            "cameraToWorld": t.ravel(order="F").tolist(),
            "pose": {"time": 10. + index, "epoch": epoch,
                     "position": {"x": x, "y": 0., "z": 0.},
                     "forward": {"x": 0., "y": 0., "z": -1.}}}


def test_native_pixel_camera_axes_and_display_are_independent():
    value = frame(0)
    value.update(displayOrientation="portrait", displayTransform=[0, 1, -2, 0, 1.5, 0])
    cam = reconstruction.camera(value)
    pixel, depth = reconstruction.project(cam, np.array([.3, .2, -3]))
    np.testing.assert_allclose(pixel, [370., 206.])
    assert depth == 3
    value["displayOrientation"] = "landscapeLeft"
    np.testing.assert_allclose(reconstruction.camera(value).k, cam.k)
    np.testing.assert_allclose(reconstruction.camera(value).world_to_camera, cam.world_to_camera)


def test_known_nonplanar_world_point_triangulates_in_metres():
    cams = [reconstruction.camera(frame(0)), reconstruction.camera(frame(1, .3))]
    truth = np.array([.12, -.24, -2.7])
    pixels = [reconstruction.project(cam, truth)[0] for cam in cams]
    position, projections, depths, errors, angle = reconstruction.triangulate(cams, pixels)
    np.testing.assert_allclose(position, truth, atol=1e-10)
    assert max(errors) < 1e-10
    assert min(depths) > 0
    assert angle > 1
    for actual, expected in zip(projections, pixels):
        np.testing.assert_allclose(actual, expected, atol=1e-10)


def test_negative_depth_is_rejected_even_when_algebraic_projection_matches():
    cams = [reconstruction.camera(frame(0)), reconstruction.camera(frame(1, .3))]
    behind = np.array([.1, .2, 3., 1.])
    pixels = []
    for cam in cams:
        projected = cam.k @ (cam.world_to_camera @ behind)[:3]
        pixels.append(projected[:2] / projected[2])
    with pytest.raises(ValueError, match="negative_depth"):
        reconstruction.triangulate(cams, pixels)


def test_small_parallax_and_cross_epoch_rejected():
    a, b = reconstruction.camera(frame(0)), reconstruction.camera(frame(1, .001))
    truth = np.array([.1, .2, -10.])
    with pytest.raises(ValueError, match="small_parallax"):
        reconstruction.triangulate([a, b], [reconstruction.project(c, truth)[0] for c in [a, b]])
    other = reconstruction.camera(frame(1, .3, epoch=2))
    with pytest.raises(ValueError, match="different_epoch"):
        reconstruction.triangulate([a, other], [reconstruction.project(c, truth)[0] for c in [a, other]])


def test_bad_matching_reprojection_cannot_be_called_a_point():
    cams = [reconstruction.camera(frame(0)), reconstruction.camera(frame(1, .3))]
    pixels = [reconstruction.project(c, np.array([.1, .2, -3.]))[0] for c in cams]
    pixels[1] += [0., 30.]
    with pytest.raises(ValueError, match="high_reprojection_error"):
        reconstruction.triangulate(cams, pixels)


def test_camera_rejects_time_and_pose_transform_mismatch():
    value = frame(0)
    value["pose"]["time"] += .01
    with pytest.raises(ValueError, match="time_mismatch"):
        reconstruction.camera(value)
    value = frame(0)
    value["pose"]["forward"]["x"] = .1
    with pytest.raises(ValueError, match="forward_mismatch"):
        reconstruction.camera(value)
    value = frame(0)
    value["cameraToWorld"][0] = 2
    with pytest.raises(ValueError, match="invalid_camera_transform"):
        reconstruction.camera(value)


def injected_correspondences(monkeypatch, frames, *, holdout_offset=.5):
    from app import spatial_sift_native
    monkeypatch.setattr(spatial_sift_native, "library", lambda: None)
    rng = np.random.default_rng(9)
    truth = np.c_[rng.uniform(-.6, .6, 36), rng.uniform(-.4, .4, 36), rng.uniform(-4., -2., 36)]
    points = [np.array([reconstruction.project(reconstruction.camera(f), p)[0] for p in truth]) for f in frames]
    if len(points) >= 3:
        points[-1] = points[-1] + [0, holdout_offset]
    # Identity descriptors are injected, not evidence of real image matching performance.
    descriptors = np.eye(128, dtype=np.float32)[:len(truth)] * 200
    def extract(path, expected_size=None):
        index = int(path.stem.split("-")[-1])
        return points[index], descriptors
    monkeypatch.setattr(reconstruction, "sift_features", extract)
    return truth


def test_third_view_is_held_out_of_fit_and_input_is_immutable(tmp_path, monkeypatch):
    frames = [frame(0), frame(1, .25), frame(2, .6)]
    truth = injected_correspondences(monkeypatch, frames, holdout_offset=.5)
    document = {"keyframes": frames, "surfaces": []}
    original = deepcopy(document)
    result = reconstruction.reconstruct(document, tmp_path, pair_offsets=(1,))
    assert document == original
    assert result["summary"]["pointCount"] == len(truth)
    assert result["summary"]["verifiedPointCount"] == len(truth)
    assert result["summary"]["medianHoldoutPixels"] == pytest.approx(.5, abs=1e-8)
    for point in result["points"]:
        feature = point["observations"][0]["featureIndex"]
        np.testing.assert_allclose(list(point["position"].values()), truth[feature], atol=1e-9)
        assert point["holdoutFrameIndex"] == 2
        assert point["supportFrames"] == [0, 1]
        assert point["observations"][-1]["usedForTriangulation"] is False
    assert result["poseOptimized"] is False
    assert result["scaleVerified"] is False


def test_two_view_points_are_explicitly_unverified(tmp_path, monkeypatch):
    frames = [frame(0), frame(1, .3)]
    injected_correspondences(monkeypatch, frames)
    result = reconstruction.reconstruct({"keyframes": frames}, tmp_path)
    assert result["summary"]["pointCount"] > 0
    assert result["summary"]["verifiedPointCount"] == 0
    assert all(not p["verifiedInThirdView"] and p["holdoutFrameIndex"] is None for p in result["points"])


def test_low_baseline_pairs_and_missing_sift_stay_in_denominator(tmp_path, monkeypatch):
    frames = [frame(0), frame(1, .001), frame(2, .002)]
    injected_correspondences(monkeypatch, frames, holdout_offset=0)
    result = reconstruction.reconstruct({"keyframes": frames}, tmp_path, pair_offsets=(1,))
    assert result["summary"]["selectedPairCount"] == 2
    assert result["summary"]["pointCount"] == 0
    assert result["summary"]["rawPairRejectedReasons"] == {"low_baseline": 2}
    assert result["summary"]["holdoutTrainingRejectedCount"] == 36
    from app import spatial_sift_native
    def missing():
        raise RuntimeError("native_sift_not_built")
    monkeypatch.setattr(spatial_sift_native, "library", missing)
    result = reconstruction.reconstruct({"keyframes": frames}, tmp_path, pair_offsets=(1,))
    assert result["status"] == "not_available"
    assert result["summary"]["selectedPairCount"] == 2
    assert len(result["pairs"]) == 2
    assert all(p["status"] == "rejected" for p in result["pairs"])
    assert len(result["frames"]) == 3


def test_output_point_budget_is_explicit_and_never_silent(tmp_path, monkeypatch):
    frames = [frame(0), frame(1, .3)]
    injected_correspondences(monkeypatch, frames)
    result = reconstruction.reconstruct({"keyframes": frames}, tmp_path, maximum_points=2)
    assert result["status"] == "capacity_limited"
    assert result["limits"]["pointCapacityExceeded"] is True
    assert result["summary"]["pointCount"] == 2
    assert result["summary"]["rejectedTrackCount"] > 0
    assert all(t["reason"] == "point_capacity_exceeded" for t in result["rejectedTracks"])


def test_plane_envelope_uses_verified_spatial_support_not_semantic_labels():
    rng = np.random.default_rng(12)
    points = []
    for i, (x, y) in enumerate(rng.uniform(-1, 1, (70, 2))):
        points.append({"id": f"point-{i}", "position": {"x": float(x), "y": float(y), "z": -3.},
            "epoch": 1, "verifiedInThirdView": True,
            "observations": [{"frameIndex": f} for f in (0, 1, 2)]})
    # Many unverified points must not extend the support envelope.
    points.extend({"id": f"unverified-{i}", "position": {"x": 100. + i, "y": 100., "z": -3.},
        "epoch": 1, "verifiedInThirdView": False, "observations": []} for i in range(100))
    surfaces = [{"id": "observed-patch", "epoch": 1, "observed": True, "vertices": [
        {"x": -1., "y": -1., "z": -3.}, {"x": 1., "y": -1., "z": -3.},
        {"x": 1., "y": 1., "z": -3.}, {"x": -1., "y": 1., "z": -3.}]}]
    boundaries, diagnostics = reconstruction.geometric_boundaries(points, surfaces)
    assert len(boundaries) == 1
    plane = boundaries[0]
    assert plane["uniqueSpatialSupportCount"] >= 30
    assert all(abs(v["x"]) <= 1 and abs(v["y"]) <= 1 for v in plane["vertices"])
    assert plane["p95PointPlaneMeters"] < 1e-9
    assert plane["nearbyUnverifiedPointCount"] == 100
    assert plane["semanticLabel"] is None
    assert plane["semanticVerified"] is False
    assert plane["completeBoundary"] is False
    assert plane["observedSurfaceAssociations"][0]["withinObservedBoundaryFraction"] == 1
    assert diagnostics["eligibleVerifiedPointCount"] == 70
    assert all(not p.startswith("unverified") for p in plane["supportPointIDs"])


def test_duplicate_or_collinear_points_cannot_inflate_a_plane():
    duplicate = [{"id": f"point-{i}", "position": {"x": 0., "y": 0., "z": -3.},
                  "epoch": 1, "verifiedInThirdView": True, "observations": []} for i in range(100)]
    assert reconstruction.geometric_boundaries(duplicate, [])[0] == []
    line = [{"id": f"point-{i}", "position": {"x": i * .03, "y": 0., "z": -3.},
             "epoch": 1, "verifiedInThirdView": True, "observations": []} for i in range(100)]
    assert reconstruction.geometric_boundaries(line, [])[0] == []


def test_holdout_pose_failure_does_not_select_image_tracks(tmp_path, monkeypatch):
    frames = [frame(0), frame(1, .25), frame(2, .6)]
    injected_correspondences(monkeypatch, frames, holdout_offset=20)
    result = reconstruction.reconstruct({"keyframes": frames}, tmp_path, pair_offsets=(1,))
    summary = result["summary"]
    assert summary["candidateHoldoutTrackCount"] == 36
    assert summary["holdoutAttemptCount"] == 36
    assert summary["holdoutPassCount"] == 0
    assert summary["holdoutFailCount"] == 36
    assert summary["finiteHoldoutResidualCount"] == 36
    assert summary["medianHoldoutPixels"] == pytest.approx(20, abs=1e-8)
    assert summary["p95HoldoutPixels"] == pytest.approx(20, abs=1e-8)
    assert summary["passedOnlyP95HoldoutPixels"] is None
    assert summary["pointCount"] == 0
    assert len(result["holdoutDiagnostics"]) == 36
    assert all(d["status"] == "failed" and d["sourceTrainingFrames"] == [0, 1] for d in result["holdoutDiagnostics"])
    assert all(d["errorPixels"] == pytest.approx(20, abs=1e-8) for d in result["holdoutDiagnostics"])


def test_color_comes_from_original_jpeg_pixels(tmp_path):
    from PIL import Image
    image = Image.new('RGB', (8, 6), (19, 71, 143))
    image.save(tmp_path / 'color.jpg', quality=100, subsampling=0)
    point = {'observations': [{'frameIndex': 0, 'pixel': [3., 2.], 'usedForTriangulation': True}]}
    reconstruction.sample_point_colors([point], [{'imageFile': 'color.jpg', 'width': 8, 'height': 6}], tmp_path)
    with Image.open(tmp_path / 'color.jpg') as decoded:
        np.testing.assert_allclose(point['colorRGB'], decoded.getpixel((3, 2)), atol=0)
    assert point['colorSource']['displayRotationApplied'] is False
    assert point['colorSource']['pixel'] == [3., 2.]


def test_noisy_training_refinement_reduces_pixel_error_with_fixed_cameras():
    frames = [frame(i, x) for i, x in enumerate([0., .15, .45, .8])]
    # Unequal focal scales make normalized DLT distinct from native-pixel SSE.
    for i, value in enumerate(frames):
        k = np.array(value['intrinsics']).reshape(3, 3, order='F')
        k[0, 0], k[1, 1] = 450. + i * 200., 700. - i * 90.
        value['intrinsics'] = k.ravel(order='F').tolist()
    cams = [reconstruction.camera(f) for f in frames]
    truth = np.array([.18, .12, -3.4])
    noise = np.array([[.8, -.7], [-.5, 1.1], [1.3, .4], [-1., -.6]])
    pixels = [reconstruction.project(c, truth)[0] + n for c, n in zip(cams, noise)]
    before = [(c.k.copy(), c.world_to_camera.copy()) for c in cams]
    baseline = reconstruction.triangulate(cams, pixels, refine=False)
    statistics = []
    optimized = reconstruction.triangulate(cams, pixels, refine=True, refinement_stats=statistics)
    assert sum(e * e for e in optimized[3]) < sum(e * e for e in baseline[3]) - .01
    assert statistics[0]['acceptedSteps'] > 0
    assert max(optimized[3]) <= 3.
    for c, (k, t) in zip(cams, before):
        np.testing.assert_array_equal(c.k, k)
        np.testing.assert_array_equal(c.world_to_camera, t)
    residual, analytic = reconstruction._pixel_objective(cams, pixels, optimized[0])
    numerical = np.column_stack([(reconstruction._pixel_objective(cams, pixels,
        optimized[0] + np.eye(3)[axis] * 1e-6)[0] - residual) / 1e-6 for axis in range(3)])
    np.testing.assert_allclose(analytic, numerical, rtol=2e-5, atol=2e-5)


def test_holdout_pixel_perturbation_cannot_change_refined_training_point(tmp_path, monkeypatch):
    frames = [frame(0), frame(1, .25), frame(2, .6)]
    injected_correspondences(monkeypatch, frames, holdout_offset=.5)
    a = reconstruction.reconstruct({'keyframes': frames}, tmp_path, pair_offsets=(1,), refine_points=True)
    injected_correspondences(monkeypatch, frames, holdout_offset=2.)
    b = reconstruction.reconstruct({'keyframes': frames}, tmp_path, pair_offsets=(1,), refine_points=True)
    assert a['pointRefinement']['holdoutUsed'] is False
    assert a['pointRefinement'] == b['pointRefinement']
    assert len(a['points']) == len(b['points']) == 36
    for left, right in zip(a['points'], b['points']):
        assert left['position'] == right['position']
        assert left['holdoutErrorPixels'] != right['holdoutErrorPixels']
    baseline = reconstruction.reconstruct({'keyframes': frames}, tmp_path, pair_offsets=(1,), refine_points=False)
    assert baseline['pointRefinement']['enabled'] is False
    assert baseline['pointRefinement']['attemptedTrackCount'] == 0


def test_refinement_solver_failure_preserves_initial_point_and_records_rejections(monkeypatch):
    cams = [reconstruction.camera(frame(0)), reconstruction.camera(frame(1, .3))]
    initial = np.array([.18, .12, -3.4])
    pixels = [reconstruction.project(c, initial)[0] + [.8, -.7] for c in cams]
    def singular_solver(*args, **kwargs):
        raise np.linalg.LinAlgError('injected numerical solver failure')
    monkeypatch.setattr(np.linalg, 'solve', singular_solver)
    point, statistics = reconstruction.refine_point(cams, pixels, initial)
    np.testing.assert_array_equal(point, initial)
    assert statistics['acceptedSteps'] == 0
    assert statistics['rejectedSteps'] > 0
    assert statistics['finalTrainingSSEPixels'] == statistics['initialTrainingSSEPixels']
    assert statistics['initialTrainingSSEPixels'] > 0


def test_refinement_negative_depth_step_preserves_initial_point(monkeypatch):
    cams = [reconstruction.camera(frame(0)), reconstruction.camera(frame(1, .3))]
    initial = np.array([.18, .12, -3.4])
    pixels = [reconstruction.project(c, initial)[0] + [.8, -.7] for c in cams]
    # This candidate crosses behind both cameras and must never become the result.
    monkeypatch.setattr(np.linalg, 'solve', lambda *args, **kwargs: np.array([0., 0., 8.]))
    point, statistics = reconstruction.refine_point(cams, pixels, initial)
    np.testing.assert_array_equal(point, initial)
    assert statistics['acceptedSteps'] == 0
    assert statistics['rejectedSteps'] > 0
    assert statistics['finalTrainingSSEPixels'] == statistics['initialTrainingSSEPixels']
    assert all(reconstruction.project(c, point)[1] > 0 for c in cams)


def test_point_refinement_is_experimental_and_disabled_by_default(tmp_path, monkeypatch):
    frames = [frame(0), frame(1, .25), frame(2, .6)]
    injected_correspondences(monkeypatch, frames)
    result = reconstruction.reconstruct({'keyframes': frames}, tmp_path, pair_offsets=(1,))
    assert result['pointRefinement']['enabled'] is False
    assert result['pointRefinement']['attemptedTrackCount'] == 0
    cams = [reconstruction.camera(f) for f in frames[:2]]
    truth = np.array([.18, .12, -3.4])
    pixels = [reconstruction.project(c, truth)[0] + n for c, n in zip(cams, [[.8, -.7], [-.5, 1.1]])]
    def unexpected_refinement(*args, **kwargs):
        pytest.fail('Default triangulation must not invoke experimental refinement')
    monkeypatch.setattr(reconstruction, 'refine_point', unexpected_refinement)
    default = reconstruction.triangulate(cams, pixels)
    baseline = reconstruction.triangulate(cams, pixels, refine=False)
    np.testing.assert_array_equal(default[0], baseline[0])
