"""Line evidence contracts; these do not establish room geometry."""
from pathlib import Path
from dataclasses import asdict
import copy

import numpy as np
import pytest
from PIL import Image

from app.spatial_line_structure import (_native_lines, reconstruct_line_structure,
                                        validate_line_structure,
                                        _appearance_difference, _chromaticity_difference,
                                        _training_chromaticity_limit, _same_local_line,
                                        LineStructureConfig)


def test_source_built_lsd_detects_real_road_edges():
    sample = (Path(__file__).resolve().parents[2] /
              "docs/model-lab/figures/camvid-drive-test-frames/road_0001TP_009240.jpg")
    with Image.open(sample) as image:
        gray = np.asarray(image.convert("L").resize((640, 480), Image.Resampling.LANCZOS))
    segments = _native_lines(gray, 4096)
    assert segments.shape[1] == 4
    assert np.isfinite(segments).all()
    assert (np.linalg.norm(segments[:, 2:] - segments[:, :2], axis=1) >= 60).sum() >= 5


def test_training_indices_and_paths_are_strict(tmp_path):
    assert "cv2" not in reconstruct_line_structure.__globals__
    with pytest.raises(ValueError, match="invalid_training_frame_indices"):
        reconstruct_line_structure({"keyframes": []}, tmp_path, [0])
    with pytest.raises(ValueError, match="invalid_training_frame_indices"):
        reconstruct_line_structure({"keyframes": [{}, {}]}, tmp_path, [0, 0])
    with pytest.raises(ValueError, match="invalid_line_configuration"):
        reconstruct_line_structure({"keyframes": []}, tmp_path, [],
                                   config=LineStructureConfig(minimum_line_pixels=0))


def test_missing_native_lsd_is_exposed(monkeypatch):
    from app import spatial_line_structure
    class OldLibrary:
        pass
    monkeypatch.setattr(spatial_line_structure, "library", lambda: OldLibrary())
    with pytest.raises(RuntimeError, match="native_lsd_missing"):
        _native_lines(np.zeros((64, 64), dtype=np.uint8), 32)


def test_side_order_can_reverse_but_color_still_matters():
    left = np.array([[.9, .9, .9], [.3, .2, .1]])
    assert _appearance_difference(left, left[::-1]) == 0
    assert _appearance_difference(left, np.ones((2, 3)) * .5) > .15


def test_chromaticity_calibration_reads_training_support_only():
    reference = np.array([[.8, .6, .4], [.4, .3, .2]])
    assert _appearance_difference(reference, reference * .6) > .1
    assert _chromaticity_difference(reference, reference * .6) == pytest.approx(0)
    assert _chromaticity_difference(reference, reference[::-1]) == pytest.approx(0)
    matches = [{"reason": "appearance_supported", "appearance": (reference * .6).tolist()}
               for _ in range(30)]
    matches.append({"reason": "third_appearance_mismatch",
                    "appearance": [[0, 1, 0], [1, 0, 0]]})
    frozen = {"candidates": [{"sourceAppearance": {"sideColors": [reference.tolist()]},
                              "thirdViewChecks": [{"lineMatches": matches}]}]}
    snapshot = copy.deepcopy(frozen)
    limit, count = _training_chromaticity_limit(frozen)
    assert count == 30 and limit == pytest.approx(0, abs=1e-12)
    assert frozen == snapshot
    with pytest.raises(ValueError, match="insufficient_training_appearance_calibration"):
        _training_chromaticity_limit({"candidates": []})


def test_spatial_duplicate_keeps_parallel_separate_edges():
    config = LineStructureConfig()
    first = {"endpointsWorld": [[0, 0, 0], [1, 0, 0]]}
    duplicate = {"endpointsWorld": [[.2, .02, 0], [.9, .02, 0]]}
    separate = {"endpointsWorld": [[.2, .12, 0], [.9, .12, 0]]}
    assert _same_local_line(first, duplicate, config)
    assert not _same_local_line(first, separate, config)


def test_full_result_retains_unusable_source_pair_without_wall_claim():
    repo = Path(__file__).resolve().parents[2]
    image = "docs/model-lab/figures/camvid-drive-test-frames/road_0001TP_009240.jpg"
    with Image.open(repo / image) as source:
        width, height = source.size
    native = np.eye(4)
    intrinsic = np.array([[500., 0., width / 2], [0., 500., height / 2], [0., 0., 1.]])
    def frame(time):
        return {"imageFile": image, "width": width, "height": height,
                "time": time, "intrinsics": intrinsic.ravel(order="F").tolist(),
                "cameraToWorld": native.ravel(order="F").tolist(),
                "pose": {"time": time, "epoch": 1,
                         "position": {"x": 0., "y": 0., "z": 0.},
                         "forward": {"x": 0., "y": 0., "z": -1.}}}
    result = reconstruct_line_structure({"keyframes": [frame(1.), frame(2.)]},
                                        repo, [0, 1])
    assert result["cameraPairs"] == [{"frames": [0, 1], "reason": "insufficient_pose_overlap"}]
    assert result["candidateCount"] == result["localLineCount"] == 0
    assert not result["completeWall"] and not result["metricAccuracyVerified"]


def test_frozen_evaluation_rejects_leakage_and_preserves_failure_denominator():
    repo = Path(__file__).resolve().parents[2]
    image = "docs/model-lab/figures/camvid-drive-test-frames/road_0001TP_009240.jpg"
    with Image.open(repo / image) as source:
        width, height = source.size
    native = np.eye(4)
    intrinsic = np.array([[500., 0., width / 2], [0., 500., height / 2], [0., 0., 1.]])
    def frame(time):
        return {"imageFile": image, "width": width, "height": height,
                "time": time, "intrinsics": intrinsic.ravel(order="F").tolist(),
                "cameraToWorld": native.ravel(order="F").tolist(),
                "pose": {"time": time, "epoch": 1,
                         "position": {"x": 0., "y": 0., "z": 0.},
                         "forward": {"x": 0., "y": 0., "z": -1.}}}
    document = {"keyframes": [frame(1.), frame(2.)]}
    frozen = {"status": "training_only_local_line_candidates",
              "trainingFrameIndices": [0], "config": asdict(LineStructureConfig()),
              "candidates": [{"id": "one", "sourceAppearance":
                              {"sideColors": [[[.9, .9, .9], [.1, .1, .1]]]}}],
              "localLines": [{"candidateId": "one", "sourceFrames": [0],
                              "endpointsWorld": [[-1., 0., 2.], [1., 0., 2.]]}]}
    snapshot = copy.deepcopy(frozen)
    with pytest.raises(ValueError, match="evaluation_frames_overlap_training"):
        validate_line_structure(document, repo, frozen, [0])
    result = validate_line_structure(document, repo, frozen, [1])
    assert frozen == snapshot
    assert result["pairCount"] == 1 and len(result["checks"]) == 1
    assert sum(result["reasonCounts"].values()) == 1
    assert not result["metricAccuracyVerified"] and not result["completeWall"]
    assert result["appearanceMode"] == "absolute_rgb"
    with pytest.raises(ValueError, match="insufficient_training_appearance_calibration"):
        validate_line_structure(document, repo, frozen, [1],
                                appearance_mode="normalized_chromaticity")
