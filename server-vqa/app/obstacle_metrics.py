"""Obstacle detection metrics (offline, CamVid-supported proxy).

The on-device obstacle capability is YOLO11n object detection. CamVid has NO
obstacle bounding boxes — only per-pixel semantic labels — so we cannot compute a
true detection mAP here. What we CAN measure honestly is whether the detector's
boxes LAND on the pixels CamVid marks as physical obstacles (car / truck / bus /
pedestrian / bicycle …), using the role manifest's ``role_grids.obstacle`` layer:

- ``coverage_recall`` : of GT obstacle CELLS, how many are covered by at least one
                        predicted obstacle box. Low = the phone does not "see" the
                        obstacles that are there (the safety-critical miss).
- ``box_precision``   : of the predicted obstacle-box AREA, how much lands on a GT
                        obstacle cell. Low = boxes are firing off real obstacles.

This is a PROXY, not mAP: a coarse cell-overlap read-out that is honest about the
lack of GT boxes. Lane geometry / instance IoU are explicitly out of scope.

Coordinate contract: predicted boxes are Vision-normalized ``(x, y, w, h)`` with
origin at the LOWER-LEFT (y up), matching the harness ``objects[].box`` output. The
GT obstacle grid is ``(rows, cols)`` with row 0 = TOP of the image (image space).
``boxes_to_grid`` flips y so the two align before overlap.
"""

from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np

from app.region_grid import grid_from_wire

# COCO/YOLO kinds that count as physical obstacles a walker/rider must avoid. Kept
# in one place so the harness object kinds and this metric stay in sync.
OBSTACLE_KINDS = frozenset(
    {"obstacle", "car", "truck", "bus", "bicycle", "motorcycle", "person", "pedestrian", "animal", "dog", "cat"}
)

# A frame is a real MISS when obstacle coverage recall falls under this floor while
# GT obstacles are present, so aggregate counts surface "obstacles there, not seen".
OBSTACLE_MISS_RECALL_FLOOR = 0.30

OBSTACLE_BASELINE_KEYS = (
    "mean_coverage_recall",
    "mean_box_precision",
    "obstacle_miss_frames",
    "obstacle_frames",
    "scored",
)


def boxes_to_grid(boxes: list[tuple[float, float, float, float]], rows: int, cols: int) -> np.ndarray:
    """Rasterize Vision-normalized boxes (origin lower-left) onto a (rows, cols)
    grid whose row 0 is the TOP of the image. Any cell touched by a box is set."""
    grid = np.zeros((rows, cols), dtype=bool)
    for (x, y, w, h) in boxes:
        if w <= 0 or h <= 0:
            continue
        # x is image-x already; y is from the BOTTOM, so flip to top-left rows.
        top = 1.0 - (y + h)
        bottom = 1.0 - y
        c0 = max(0, min(cols, int(math.floor(x * cols))))
        c1 = max(c0 + 1, min(cols, int(math.ceil((x + w) * cols))))
        r0 = max(0, min(rows, int(math.floor(top * rows))))
        r1 = max(r0 + 1, min(rows, int(math.ceil(bottom * rows))))
        grid[r0:r1, c0:c1] = True
    return grid


def _obstacle_boxes(objects: Any) -> list[tuple[float, float, float, float]]:
    """Pull obstacle-kind boxes from a harness prediction ``objects`` list."""
    out: list[tuple[float, float, float, float]] = []
    if not isinstance(objects, list):
        return out
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        if str(obj.get("kind", "")).lower() not in OBSTACLE_KINDS:
            continue
        box = obj.get("box")
        if not isinstance(box, dict):
            continue
        try:
            x = float(box["x"]); y = float(box["y"]); w = float(box["w"]); h = float(box["h"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append((x, y, w, h))
    return out


def obstacle_scores(gt_obstacle: np.ndarray, pred_boxes: list[tuple[float, float, float, float]]) -> dict[str, Optional[float]]:
    """Per-frame obstacle coverage between GT obstacle cells and predicted boxes.

    Rates whose denominator is empty are ``None`` (no fabrication): a frame with no
    GT obstacle has ``coverage_recall=None``; no predicted box => ``box_precision=None``."""
    rows, cols = gt_obstacle.shape
    pred_grid = boxes_to_grid(pred_boxes, rows, cols)
    inter = int(np.logical_and(gt_obstacle, pred_grid).sum())
    gt_area = int(gt_obstacle.sum())
    pred_area = int(pred_grid.sum())
    return {
        "coverage_recall": (inter / gt_area) if gt_area else None,
        "box_precision": (inter / pred_area) if pred_area else None,
    }


def evaluate_obstacles(pairs: list[tuple[str, Any, Any]]) -> dict[str, Any]:
    """Aggregate obstacle coverage over ``(frame_id, gt_obstacle_wire, objects)``.

    Only frames that HAVE a GT obstacle contribute to recall (a frame with no
    obstacle is not evidence either way). ``obstacle_miss_frames`` counts GT-obstacle
    frames whose coverage recall falls below ``OBSTACLE_MISS_RECALL_FLOOR``. Malformed
    GT grids are ``skipped`` and surfaced, never scored as garbage."""
    recalls: list[float] = []
    precisions: list[float] = []
    miss = 0
    scored = 0
    skipped = 0
    for _fid, gt_wire, objects in pairs:
        gt = grid_from_wire(gt_wire)
        if gt is None:
            skipped += 1
            continue
        s = obstacle_scores(gt, _obstacle_boxes(objects))
        scored += 1
        if s["coverage_recall"] is not None:
            recalls.append(float(s["coverage_recall"]))
            if s["coverage_recall"] < OBSTACLE_MISS_RECALL_FLOOR:
                miss += 1
        if s["box_precision"] is not None:
            precisions.append(float(s["box_precision"]))

    def mean(values: list[float]) -> Optional[float]:
        return (sum(values) / len(values)) if values else None

    return {
        "scored": scored,
        "skipped": skipped,
        "obstacle_frames": len(recalls),
        "obstacle_miss_frames": miss,
        "mean_coverage_recall": mean(recalls),
        "mean_box_precision": mean(precisions),
    }


_EPS = 1e-3


def gate_obstacle(current: dict[str, Any], baseline: dict[str, Any]) -> tuple[bool, list[str]]:
    """Return (passed, reasons). Safety-first: obstacle coverage recall must NOT
    drop and missed-obstacle frames must NOT increase (seeing FEWER obstacles is a
    safety regression). Accepts a flat dict or a saved ``{"metrics": {...}}``."""
    metrics = baseline.get("metrics")
    if isinstance(metrics, dict):
        baseline = metrics
    reasons: list[str] = []

    cur_r, base_r = current.get("mean_coverage_recall"), baseline.get("mean_coverage_recall")
    if cur_r is not None and base_r is not None and cur_r < base_r - _EPS:
        reasons.append(f"mean_coverage_recall regressed: {base_r:.4f} -> {cur_r:.4f}")

    cur_miss = int(current.get("obstacle_miss_frames", 0) or 0)
    base_miss = int(baseline.get("obstacle_miss_frames", 0) or 0)
    if cur_miss > base_miss:
        reasons.append(f"obstacle_miss_frames regressed: {base_miss} -> {cur_miss}")

    return (len(reasons) == 0, reasons)
