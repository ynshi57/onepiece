"""Offline server-side traversability predictor for open datasets.

Open datasets (CamVid, BDD100K, ...) ship images + masks but no VQASee
prediction. The real product predictor (LocalPathGuidanceSignal) only runs
on-device via Core ML and cannot batch thousands of frames on the Mac. To close
the evaluation loop offline we run the *same family* of floor/traversability
segmentation model (Fast-SCNN, exported to ONNX) on the server, then map its
traversable mask to path-guidance fields via the shared ``path_roi`` logic.

Honesty rules (aligned with docs/model-lab RGB-only route, Phase 1):
- This is an OFFLINE PROXY predictor, not the shipping on-device model. Its
  metrics are a relative trend signal, not iPhone ground truth.
- If ``onnxruntime`` is not installed or the model asset is missing, the
  predictor reports ``capability = "unsupported"`` with a clear reason. It never
  fabricates predictions and never silently skips frames.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from app.path_roi import path_guidance_from_mask

# Default place to look for the converted floor-segmentation ONNX model. The
# conversion pipeline lives in deploy/ios/convert_floor_segmentation_onnx_to_coreml.sh.
DEFAULT_MODEL_ENV = "VQASEE_TRAVERSABILITY_ONNX"
DEFAULT_MODEL_RELATIVE = "models/vqasee_traversability_segmentation.onnx"

# Traversable class threshold applied to the model's per-pixel floor probability.
TRAVERSABLE_THRESHOLD = 0.5


@dataclass(frozen=True)
class Capability:
    capability: str  # "active" | "unsupported"
    reason: str
    model_path: str | None
    onnxruntime_available: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "reason": self.reason,
            "model_path": self.model_path,
            "onnxruntime_available": self.onnxruntime_available,
            "predictor": "offline_proxy_traversability_onnx",
        }


def _onnxruntime_available() -> bool:
    try:
        import onnxruntime  # noqa: F401  (probe only)

        return True
    except Exception:  # pragma: no cover - import failure is environment-specific
        return False


def resolve_model_path(model_path: str | os.PathLike[str] | None = None) -> Path | None:
    if model_path:
        candidate = Path(model_path).expanduser()
        return candidate
    configured = os.getenv(DEFAULT_MODEL_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    server_root = Path(__file__).resolve().parents[1]
    return server_root / DEFAULT_MODEL_RELATIVE


def probe_capability(model_path: str | os.PathLike[str] | None = None) -> Capability:
    """Report whether the offline predictor can actually run. No pretending."""
    ort_ok = _onnxruntime_available()
    resolved = resolve_model_path(model_path)
    resolved_str = str(resolved) if resolved else None
    if not ort_ok:
        return Capability(
            capability="unsupported",
            reason="onnxruntime 未安装；请先 `pip install onnxruntime` 才能在服务端跑离线预测器。",
            model_path=resolved_str,
            onnxruntime_available=False,
        )
    if resolved is None or not resolved.is_file():
        return Capability(
            capability="unsupported",
            reason=(
                f"未找到通行性分割 ONNX 模型（查找路径：{resolved_str}）。"
                f"请转换并放置模型，或设置环境变量 {DEFAULT_MODEL_ENV}。"
            ),
            model_path=resolved_str,
            onnxruntime_available=True,
        )
    return Capability(
        capability="active",
        reason="ok",
        model_path=resolved_str,
        onnxruntime_available=True,
    )


def prediction_from_traversable_mask(mask: np.ndarray) -> dict[str, Any]:
    """Pure core: binary traversable mask -> path-guidance prediction fields.

    This is deliberately model-independent so it can be tested with synthetic
    masks without onnxruntime or a real model asset.
    """
    guidance = path_guidance_from_mask(mask)
    coverage = guidance.pop("coverage", {})
    prediction = dict(guidance)
    prediction["prediction_source"] = "offline_proxy_traversability_onnx"
    return {"prediction": prediction, "coverage": coverage}


class TraversabilityPredictor:
    """Loads a floor-segmentation ONNX model and predicts path guidance.

    The ONNX session is created lazily on first use. Callers should check
    ``capability().capability == "active"`` before predicting; ``predict_image``
    raises ``PredictorUnavailable`` rather than returning a fake result.
    """

    def __init__(self, model_path: str | os.PathLike[str] | None = None, *, threshold: float = TRAVERSABLE_THRESHOLD):
        self._model_path = resolve_model_path(model_path)
        self._threshold = threshold
        self._session: Any | None = None
        self._input_name: str | None = None

    def capability(self) -> Capability:
        return probe_capability(self._model_path)

    def _ensure_session(self) -> None:
        if self._session is not None:
            return
        cap = self.capability()
        if cap.capability != "active":
            raise PredictorUnavailable(cap)
        import onnxruntime  # local import; only needed when actually predicting

        self._session = onnxruntime.InferenceSession(
            str(self._model_path), providers=["CPUExecutionProvider"]
        )
        self._input_name = self._session.get_inputs()[0].name

    def _infer_traversable_mask(self, image: Image.Image) -> np.ndarray:
        self._ensure_session()
        assert self._session is not None and self._input_name is not None
        shape = self._session.get_inputs()[0].shape
        # Expected NCHW; fall back to a common 512x512 when the model uses dynamic dims.
        height = shape[2] if isinstance(shape[2], int) else 512
        width = shape[3] if isinstance(shape[3], int) else 512
        resized = image.convert("RGB").resize((width, height))
        arr = np.asarray(resized, dtype=np.float32) / 255.0
        tensor = np.transpose(arr, (2, 0, 1))[np.newaxis, ...]
        outputs = self._session.run(None, {self._input_name: tensor})
        logits = np.asarray(outputs[0])
        return self._mask_from_logits(logits)

    @staticmethod
    def _mask_from_logits(logits: np.ndarray, *, threshold: float = TRAVERSABLE_THRESHOLD) -> np.ndarray:
        """Reduce a segmentation output to a boolean traversable mask.

        Supports (N,C,H,W) multi-class (argmax==0 treated as traversable/floor)
        and (N,1,H,W)/(N,H,W) single-channel probability outputs.
        """
        array = np.asarray(logits, dtype=np.float32)
        if array.ndim == 4 and array.shape[1] > 1:
            classes = np.argmax(array[0], axis=0)
            return classes == 0
        if array.ndim == 4 and array.shape[1] == 1:
            return array[0, 0] >= threshold
        if array.ndim == 3:
            return array[0] >= threshold
        if array.ndim == 2:
            return array >= threshold
        raise ValueError(f"unexpected segmentation output shape: {array.shape}")

    def predict_image(self, image_path: str | os.PathLike[str]) -> dict[str, Any]:
        path = Path(image_path).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"image not found: {path}")
        with Image.open(path) as image:
            mask = self._infer_traversable_mask(image)
        return prediction_from_traversable_mask(mask >= 0.5 if mask.dtype != bool else mask)


class PredictorUnavailable(RuntimeError):
    """Raised when prediction is attempted but the predictor is not active."""

    def __init__(self, capability: Capability):
        self.capability = capability
        super().__init__(capability.reason)


def _frame_id(row: dict[str, Any]) -> str:
    value = row.get("frame_id") or row.get("frame") or row.get("image")
    return str(value) if value is not None else ""


def predict_manifest(
    manifest_rows: list[dict[str, Any]],
    predictor: "TraversabilityPredictor",
    *,
    limit: int = 0,
) -> dict[str, Any]:
    """Run the predictor over manifest rows that carry an ``image_path``.

    Returns a structured result: the predictor capability, one prediction row
    per successfully predicted frame (keyed by ``frame_id`` so it can be fed to
    ``evaluate_path_guidance`` as ``prediction_rows``), and explicit per-frame
    errors. Frames without an ``image_path`` are reported as errors, not
    silently dropped.
    """
    cap = predictor.capability()
    if cap.capability != "active":
        return {"capability": cap.as_dict(), "predictions": [], "errors": [], "predicted": 0}

    predictions: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    selected = manifest_rows[:limit] if limit and limit > 0 else manifest_rows
    for row in selected:
        frame_id = _frame_id(row)
        image_path = row.get("image_path")
        if not isinstance(image_path, str) or not image_path.strip():
            errors.append({"frame_id": frame_id, "error": "missing_image_path"})
            continue
        try:
            result = predictor.predict_image(image_path)
        except FileNotFoundError as exc:
            errors.append({"frame_id": frame_id, "error": f"missing_image_file: {exc}"})
            continue
        except Exception as exc:  # noqa: BLE001 - record and continue; never silently drop
            errors.append({"frame_id": frame_id, "error": str(exc)})
            continue
        predictions.append(
            {
                "frame_id": frame_id,
                "prediction": result["prediction"],
                "coverage": result["coverage"],
            }
        )
    return {
        "capability": cap.as_dict(),
        "predictions": predictions,
        "errors": errors,
        "predicted": len(predictions),
    }
