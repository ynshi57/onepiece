"""Traversable region grid: the shared, coarse "walkable area" raster.

Two producers speak this one schema so the closed loop can score them fairly:

- the iPhone offline harness emits a PREDICTED grid (segmentation model output,
  `LocalSegmentation.traversableGrid` in Swift), and
- dataset manifests carry a GROUND-TRUTH grid, downsampled from the same semantic
  traversability mask that the GT guidance line is traced from.

Both are ``{"cols": C, "rows": R, "cells": [0/1 ...]}``, row-major, **row 0 = TOP**
of the image (image space, top-left origin), so they overlay aligned with the frame
and with each other. This lets us upgrade the loop from "3-box status agreement" to
a real per-cell region IoU / recall / precision — i.e. *how close is the region the
iPhone perceives as walkable to the annotated truth*.

Resolution (64x48) mirrors ``LocalSegmentation.gridCols/gridRows`` on device; keep
the two in sync (the harness contract test asserts the emitted shape).
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np

GRID_COLS = 64
GRID_ROWS = 48

# Lane grids are FINER and use ANY-pixel presence, not majority vote: lane markings
# are thin (~0.67% of pixels) so a majority-vote coarse cell erases them entirely.
# Dims match the on-device predicted lane grid (LaneGrid.gridCols/gridRows = 128x96)
# so GT and prediction align for closed-loop lane scoring.
LANE_GRID_COLS = 128
LANE_GRID_ROWS = 96

# Frame-level thresholds for the safety-oriented buckets. Precision below this
# means most of what the iPhone called walkable is NOT walkable in truth — a
# region-level "false go" (it would invite you onto non-traversable ground).
# Recall below this means it missed most of the real walkable area.
REGION_PRECISION_FLOOR = 0.5
REGION_RECALL_FLOOR = 0.5

REGION_BASELINE_KEYS = (
    "mean_iou",
    "mean_recall",
    "mean_precision",
    "region_false_go_frames",
    "region_miss_frames",
    "scored",
)


def downsample_mask_to_grid(mask: np.ndarray) -> np.ndarray:
    """Reduce a full-res boolean traversability mask to a (GRID_ROWS, GRID_COLS)
    uint8 grid by majority vote per cell. Row 0 stays the TOP of the image.

    Vectorized via ``np.add.reduceat`` on integral segments so it is cheap enough
    to run at manifest-build time for every frame."""
    if mask.ndim != 2:
        raise ValueError(f"mask must be 2-D, got shape {mask.shape}")
    h, w = mask.shape
    if h == 0 or w == 0:
        return np.zeros((GRID_ROWS, GRID_COLS), dtype=np.uint8)
    ys = (np.arange(GRID_ROWS + 1) * h) // GRID_ROWS
    xs = (np.arange(GRID_COLS + 1) * w) // GRID_COLS
    # Guard against zero-width segments on tiny images: reduceat needs strictly
    # increasing start indices, so clamp any degenerate cell to a single pixel.
    ys = np.maximum.accumulate(np.minimum(ys, h))
    xs = np.maximum.accumulate(np.minimum(xs, w))
    m = mask.astype(np.int64)
    row_sums = np.add.reduceat(m, ys[:-1], axis=0)
    block_sums = np.add.reduceat(row_sums, xs[:-1], axis=1)
    areas = np.outer(np.diff(ys), np.diff(xs)).clip(min=1)
    return (block_sums * 2 >= areas).astype(np.uint8)


def lane_presence_grid(
    mask: np.ndarray,
    *,
    rows: int = LANE_GRID_ROWS,
    cols: int = LANE_GRID_COLS,
) -> np.ndarray:
    """Reduce a full-res thin-class mask (lane markings) to a (rows, cols) uint8 grid
    where a cell is set if ANY pixel in it is set. Unlike ``downsample_mask_to_grid``
    (majority vote), this preserves thin structures — the right rasterization for a
    lane-marking class that would otherwise vanish under a majority threshold.

    Row 0 stays the TOP of the image. Vectorized via ``np.add.reduceat``."""
    if mask.ndim != 2:
        raise ValueError(f"mask must be 2-D, got shape {mask.shape}")
    h, w = mask.shape
    if h == 0 or w == 0:
        return np.zeros((rows, cols), dtype=np.uint8)
    ys = (np.arange(rows + 1) * h) // rows
    xs = (np.arange(cols + 1) * w) // cols
    ys = np.maximum.accumulate(np.minimum(ys, h))
    xs = np.maximum.accumulate(np.minimum(xs, w))
    m = mask.astype(np.int64)
    row_sums = np.add.reduceat(m, ys[:-1], axis=0)
    block_sums = np.add.reduceat(row_sums, xs[:-1], axis=1)
    return (block_sums > 0).astype(np.uint8)


def grid_to_wire(cells: np.ndarray) -> dict[str, Any]:
    """Serialize a (rows, cols) grid to the on-wire schema (matches Swift)."""
    rows, cols = cells.shape
    return {"cols": int(cols), "rows": int(rows), "cells": [int(v) for v in cells.reshape(-1)]}


def grid_from_wire(grid: Any) -> Optional[np.ndarray]:
    """Parse a wire grid into a bool (rows, cols) array. None if malformed —
    callers surface "no region" explicitly rather than scoring garbage."""
    if not isinstance(grid, dict):
        return None
    try:
        cols = int(grid.get("cols", 0))
        rows = int(grid.get("rows", 0))
    except (TypeError, ValueError):
        return None
    cells = grid.get("cells")
    if cols <= 0 or rows <= 0 or not isinstance(cells, list) or len(cells) != cols * rows:
        return None
    try:
        return np.asarray(cells, dtype=np.int16).reshape(rows, cols) > 0
    except (TypeError, ValueError):
        return None


def region_scores(pred: np.ndarray, gt: np.ndarray) -> dict[str, Optional[float]]:
    """Per-frame region agreement between predicted and GT walkable grids.

    - iou: overall region overlap.
    - recall: share of the truth's walkable cells the iPhone also marks walkable
      (low = it MISSES walkable ground).
    - precision: share of the iPhone's walkable cells that are truly walkable
      (low = it HALLUCINATES walkable ground — the region-level false-go risk).
    Recall/precision are None when the respective denominator is empty (no truth
    walkable / no predicted walkable) so aggregation never fabricates a value."""
    if pred.shape != gt.shape:
        raise ValueError(f"grid shape mismatch: pred {pred.shape} vs gt {gt.shape}")
    inter = int(np.logical_and(pred, gt).sum())
    union = int(np.logical_or(pred, gt).sum())
    gt_area = int(gt.sum())
    pred_area = int(pred.sum())
    return {
        "iou": (inter / union) if union else 1.0,
        "recall": (inter / gt_area) if gt_area else None,
        "precision": (inter / pred_area) if pred_area else None,
    }


def evaluate_region_grids(pairs: list[tuple[str, Any, Any]]) -> dict[str, Any]:
    """Aggregate region IoU/recall/precision over (frame_id, gt_grid, pred_grid).

    Malformed or shape-mismatched grids are counted as ``skipped`` (surfaced, never
    silently dropped). Returns means plus the safety-oriented frame buckets."""
    ious: list[float] = []
    recalls: list[float] = []
    precisions: list[float] = []
    false_go = 0  # precision below floor: claims walkable where truth isn't
    miss = 0  # recall below floor: misses real walkable area
    both_ok = 0
    scored = 0
    skipped = 0
    for _fid, gt_raw, pred_raw in pairs:
        gt = grid_from_wire(gt_raw)
        pred = grid_from_wire(pred_raw)
        if gt is None or pred is None or gt.shape != pred.shape:
            skipped += 1
            continue
        s = region_scores(pred, gt)
        scored += 1
        ious.append(float(s["iou"]))
        if s["recall"] is not None:
            recalls.append(float(s["recall"]))
            if s["recall"] < REGION_RECALL_FLOOR:
                miss += 1
        if s["precision"] is not None:
            precisions.append(float(s["precision"]))
            if s["precision"] < REGION_PRECISION_FLOOR:
                false_go += 1
        if (s["recall"] is None or s["recall"] >= REGION_RECALL_FLOOR) and (
            s["precision"] is None or s["precision"] >= REGION_PRECISION_FLOOR
        ):
            both_ok += 1

    def mean(values: list[float]) -> Optional[float]:
        return (sum(values) / len(values)) if values else None

    return {
        "scored": scored,
        "skipped": skipped,
        "both_ok": both_ok,
        "region_false_go_frames": false_go,
        "region_miss_frames": miss,
        "mean_iou": mean(ious),
        "mean_recall": mean(recalls),
        "mean_precision": mean(precisions),
    }


_EPS_RATE = 0.01


def gate_region(current: dict[str, Any], baseline: dict[str, Any]) -> tuple[bool, list[str]]:
    """Return (passed, reasons). A regression in any safety-relevant region metric
    fails the gate.

    Safety-critical first: ``region_false_go_frames`` (claiming walkable where the
    truth is blocked) must NOT increase. Then the region means must not drop.

    Accepts either a flat metrics dict or a saved baseline payload ``{"metrics":
    {...}}`` — unwrapping the latter so a persisted baseline actually gates instead
    of comparing against absent (None) values."""
    metrics = baseline.get("metrics")
    if isinstance(metrics, dict):
        baseline = metrics
    reasons: list[str] = []

    cur_fg = int(current.get("region_false_go_frames", 0) or 0)
    base_fg = int(baseline.get("region_false_go_frames", 0) or 0)
    if cur_fg > base_fg:
        reasons.append(f"region_false_go_frames regressed: {base_fg} -> {cur_fg}")

    def worse_down(key: str) -> None:
        cur, base = current.get(key), baseline.get(key)
        if cur is None or base is None:
            return
        if cur < base - _EPS_RATE:
            reasons.append(f"{key} regressed: {base:.4f} -> {cur:.4f}")

    worse_down("mean_iou")
    worse_down("mean_recall")
    worse_down("mean_precision")

    return (len(reasons) == 0, reasons)
