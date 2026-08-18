import numpy as np

from app.path_roi import path_guidance_from_mask, status_from_coverage
from app.traversability_predictor import (
    Capability,
    TraversabilityPredictor,
    predict_manifest,
    prediction_from_traversable_mask,
    probe_capability,
)


def test_path_roi_status_thresholds():
    assert status_from_coverage(None) == "unknown"
    assert status_from_coverage(0.9) == "candidateOpen"
    assert status_from_coverage(0.4) == "caution"
    assert status_from_coverage(0.05) == "blocked"


def test_fully_traversable_mask_is_candidate_open():
    mask = np.ones((100, 100), dtype=bool)
    guidance = path_guidance_from_mask(mask)
    assert guidance["near_path_status"] == "candidateOpen"
    assert guidance["left_front_status"] == "candidateOpen"
    assert guidance["right_front_status"] == "candidateOpen"


def test_blocked_near_center_sets_focus_center():
    mask = np.ones((100, 100), dtype=bool)
    # Wipe out the bottom-center near ROI so it is not traversable.
    mask[55:100, 25:75] = False
    result = prediction_from_traversable_mask(mask)
    prediction = result["prediction"]
    assert prediction["near_path_status"] in {"caution", "blocked"}
    assert prediction["focus_direction"] == "center"
    assert prediction["prediction_source"] == "offline_proxy_traversability_onnx"


def test_probe_capability_reports_unsupported_without_model(tmp_path):
    missing_model = tmp_path / "does-not-exist.onnx"
    cap = probe_capability(missing_model)
    # onnxruntime may or may not be installed in CI; either way, no model => unsupported.
    assert cap.capability == "unsupported"
    assert cap.model_path == str(missing_model)


def test_mask_from_logits_multiclass_argmax():
    # (N, C, H, W): class 0 = floor/traversable. Make top half class 1, bottom class 0.
    logits = np.zeros((1, 2, 4, 4), dtype=np.float32)
    logits[0, 1, 0:2, :] = 5.0  # top rows -> class 1
    logits[0, 0, 2:4, :] = 5.0  # bottom rows -> class 0 (traversable)
    mask = TraversabilityPredictor._mask_from_logits(logits)
    assert mask.shape == (4, 4)
    assert mask[3, 0]  # bottom row traversable
    assert not mask[0, 0]  # top row not traversable


class _StubPredictor:
    """Duck-typed predictor that mimics an active model for batch tests."""

    def __init__(self, prediction):
        self._prediction = prediction

    def capability(self):
        return Capability(capability="active", reason="ok", model_path="stub", onnxruntime_available=True)

    def predict_image(self, image_path):
        if image_path == "MISSING":
            raise FileNotFoundError(image_path)
        return {"prediction": dict(self._prediction), "coverage": {"near_path": 0.9}}


def test_predict_manifest_active_predictor_fills_predictions():
    rows = [
        {"frame_id": "a", "image_path": "/tmp/a.jpg"},
        {"frame_id": "b"},  # missing image_path -> recorded error, not dropped silently
        {"frame_id": "c", "image_path": "MISSING"},  # missing file -> error
    ]
    prediction = {
        "near_path_status": "candidateOpen",
        "left_front_status": "candidateOpen",
        "right_front_status": "candidateOpen",
        "focus_direction": "unknown",
    }
    result = predict_manifest(rows, _StubPredictor(prediction))
    assert result["capability"]["capability"] == "active"
    assert result["predicted"] == 1
    assert result["predictions"][0]["frame_id"] == "a"
    error_frames = {error["frame_id"] for error in result["errors"]}
    assert error_frames == {"b", "c"}


def test_predict_manifest_unsupported_returns_no_predictions():
    predictor = TraversabilityPredictor(model_path="/no/such/model.onnx")
    result = predict_manifest([{"frame_id": "a", "image_path": "/tmp/a.jpg"}], predictor)
    assert result["capability"]["capability"] == "unsupported"
    assert result["predicted"] == 0
    assert result["predictions"] == []
