"""Offline evaluation helpers for VQASee path-guidance datasets.

The evaluator uses a small JSONL manifest schema so open datasets can be adapted
into one common format without committing large image/video assets.

Three-zone ROI statuses (near/left/right) are not scored. Labeled frames are
those with a ``traversable_grid`` (or leftover non-empty ``ground_truth``).
Region IoU / guidance-line gates live in ``region_grid`` and the harness eval
tool — this helper only counts coverage of labels and predictions.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON at {path}:{line_number}: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"row {line_number} in {path} is not an object")
        rows.append(value)
    return rows


def _path_payload(row: dict[str, Any], key: str) -> dict[str, Any]:
    value = row.get(key)
    return value if isinstance(value, dict) else {}


def _frame_id(row: dict[str, Any]) -> str:
    value = row.get("frame_id") or row.get("frame") or row.get("image")
    return str(value) if value is not None else ""


def _prediction_lookup(predictions: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for row in predictions:
        frame_id = _frame_id(row)
        if frame_id:
            lookup[frame_id] = row
    return lookup


def evaluate_path_guidance(
    manifest_rows: list[dict[str, Any]],
    prediction_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Count labeled frames and missing predictions. Does not score three-zone ROI."""
    prediction_by_frame = _prediction_lookup(prediction_rows or [])
    scene_counts: Counter[str] = Counter()
    missing_predictions: list[str] = []
    labeled = 0

    for row in manifest_rows:
        frame_id = _frame_id(row)
        truth = _path_payload(row, "ground_truth")
        has_region_grid = isinstance(row.get("traversable_grid"), dict)
        if not truth and not has_region_grid:
            continue
        labeled += 1
        scene = str(row.get("split") or row.get("scene") or "unknown")
        scene_counts[scene] += 1
        if frame_id in prediction_by_frame or "prediction" in row or "path_guidance" in row:
            prediction_row = prediction_by_frame.get(frame_id, row)
            prediction = _path_payload(prediction_row, "prediction") or _path_payload(prediction_row, "path_guidance")
            has_pred_grid = isinstance(prediction.get("traversable_grid"), dict) or (
                prediction_row is not row and isinstance(prediction_row.get("traversable_grid"), dict)
            )
            if not prediction and not has_pred_grid:
                missing_predictions.append(frame_id)
        else:
            missing_predictions.append(frame_id)

    return {
        "frame_count": len(manifest_rows),
        "labeled_frames": labeled,
        "scene_counts": dict(scene_counts),
        "missing_prediction_count": len(missing_predictions),
        "missing_predictions": missing_predictions[:100],
        "recommendations": recommendations(
            labeled=labeled,
            missing_predictions=missing_predictions,
        ),
    }


def recommendations(*, labeled: int, missing_predictions: list[str]) -> list[str]:
    recs: list[str] = []
    if not labeled:
        return ["Add traversable_grid labels before evaluating path guidance."]
    if missing_predictions:
        recs.append("Run LocalPathGuidanceSignal on all labeled frames; some frames have no predictions.")
    if not recs:
        recs.append("Coverage looks complete; score region IoU and guidance lines with the harness eval tool.")
    return recs
