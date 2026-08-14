"""Offline evaluation helpers for VQASee path-guidance datasets.

The evaluator intentionally uses a small JSONL manifest schema so open datasets
(ScanNet, ADE20K, BDD100K, Mapillary, or local diagnostic captures) can be
adapted into one common format without committing large image/video assets.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

PATH_STATUSES = {"candidateOpen", "caution", "blocked", "unknown"}
FOCUS_DIRECTIONS = {"left", "center", "right", "unknown"}
RISK_STATUSES = {"caution", "blocked"}


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


def _clean_status(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    return value if value in PATH_STATUSES else "unknown"


def _clean_direction(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    return value if value in FOCUS_DIRECTIONS else "unknown"


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


def _confusion_key(truth: str, prediction: str) -> str:
    return f"{truth}->{prediction}"


def evaluate_path_guidance(
    manifest_rows: list[dict[str, Any]],
    prediction_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate path-guidance predictions against structured ground truth.

    Manifest rows may include either:

    - `ground_truth`: expected path guidance fields; and optionally
    - `prediction`: predicted path guidance fields.

    If `prediction_rows` is provided, it overrides row-level `prediction` using
    `frame_id` lookup. This supports model outputs stored separately from dataset
    manifests.
    """
    prediction_by_frame = _prediction_lookup(prediction_rows or [])
    status_confusion: Counter[str] = Counter()
    direction_confusion: Counter[str] = Counter()
    scene_counts: Counter[str] = Counter()
    risk_misses: list[str] = []
    false_blocks: list[str] = []
    missing_predictions: list[str] = []
    labeled = 0
    exact_status_matches = 0
    exact_direction_matches = 0
    unknown_predictions = 0

    for row in manifest_rows:
        frame_id = _frame_id(row)
        truth = _path_payload(row, "ground_truth")
        if not truth:
            continue
        labeled += 1
        scene = str(row.get("split") or row.get("scene") or "unknown")
        scene_counts[scene] += 1
        prediction_row = prediction_by_frame.get(frame_id, row)
        prediction = _path_payload(prediction_row, "prediction") or _path_payload(prediction_row, "path_guidance")
        if not prediction:
            missing_predictions.append(frame_id)
            prediction = {}

        for field in ["near_path_status", "left_front_status", "right_front_status"]:
            expected = _clean_status(truth.get(field))
            actual = _clean_status(prediction.get(field))
            status_confusion[_confusion_key(expected, actual)] += 1
            if expected == actual:
                exact_status_matches += 1
            if actual == "unknown":
                unknown_predictions += 1
            if expected in RISK_STATUSES and actual == "candidateOpen":
                risk_misses.append(f"{frame_id}:{field}")
            if expected == "candidateOpen" and actual in RISK_STATUSES:
                false_blocks.append(f"{frame_id}:{field}")

        expected_direction = _clean_direction(truth.get("focus_direction"))
        actual_direction = _clean_direction(prediction.get("focus_direction"))
        direction_confusion[_confusion_key(expected_direction, actual_direction)] += 1
        if expected_direction == actual_direction:
            exact_direction_matches += 1

    total_status_fields = labeled * 3
    return {
        "frame_count": len(manifest_rows),
        "labeled_frames": labeled,
        "scene_counts": dict(scene_counts),
        "status_accuracy": round(exact_status_matches / total_status_fields, 4) if total_status_fields else None,
        "focus_direction_accuracy": round(exact_direction_matches / labeled, 4) if labeled else None,
        "unknown_prediction_rate": round(unknown_predictions / total_status_fields, 4) if total_status_fields else None,
        "risk_miss_count": len(risk_misses),
        "false_block_count": len(false_blocks),
        "missing_prediction_count": len(missing_predictions),
        "status_confusion": dict(status_confusion),
        "direction_confusion": dict(direction_confusion),
        "risk_misses": risk_misses[:100],
        "false_blocks": false_blocks[:100],
        "missing_predictions": missing_predictions[:100],
        "recommendations": recommendations(
            labeled=labeled,
            risk_misses=risk_misses,
            false_blocks=false_blocks,
            missing_predictions=missing_predictions,
            unknown_predictions=unknown_predictions,
            total_status_fields=total_status_fields,
        ),
    }


def recommendations(
    *,
    labeled: int,
    risk_misses: list[str],
    false_blocks: list[str],
    missing_predictions: list[str],
    unknown_predictions: int,
    total_status_fields: int,
) -> list[str]:
    recs: list[str] = []
    if not labeled:
        return ["Add ground_truth labels before evaluating path guidance."]
    if missing_predictions:
        recs.append("Run LocalPathGuidanceSignal on all labeled frames; some frames have no predictions.")
    if risk_misses:
        recs.append("Prioritize recall: blocked/caution ground-truth regions were predicted candidateOpen.")
    if false_blocks:
        recs.append("Review false-block cases; the overlay may be too conservative or YOLO/segmentation is over-triggering.")
    if total_status_fields and unknown_predictions / total_status_fields > 0.25:
        recs.append("Unknown rate is high; add depth/segmentation coverage or improve image-quality handling.")
    if not recs:
        recs.append("No high-priority issue detected in this manifest; expand dataset diversity.")
    return recs
