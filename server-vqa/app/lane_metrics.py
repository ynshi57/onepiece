"""Lane-marking segmentation metrics (offline, pixel-mask based).

CamVid gives lane markings as thin per-pixel labels (LaneMkgsDriv/NonDriv, ~0.67%
of pixels). We can only train/measure lane-marking PIXEL segmentation here — NOT
lane geometry/polylines/ego-lane (that needs CULane/TuSimple-style instance labels
+ a row-anchor model like UFLD/CLRNet; see the T2/lane decision doc).

Because lanes are thin, a strict per-pixel IoU is harsh (a 1-px lateral offset
counts as fully wrong). So we report BOTH:
- strict pixel ``iou`` / ``recall`` / ``precision`` (no mercy), and
- tolerance-band ``recall_tol`` / ``precision_tol``: a predicted lane pixel counts
  as a hit if a GT lane pixel lies within ``tol`` pixels (and vice versa). This is
  the standard way thin-structure detectors are scored; ``tol`` is reported so the
  number is never silently inflated.

Dilation is numpy-only (no scipy dependency): iterated 4/8-neighbour OR.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np

# A lane frame is flagged as a real MISS when strict recall is under this floor,
# so aggregate frame counts surface "lanes present but not found".
LANE_MISS_RECALL_FLOOR = 0.20

LANE_BASELINE_KEYS = (
    "mean_iou",
    "mean_recall",
    "mean_precision",
    "mean_recall_tol",
    "mean_precision_tol",
    "lane_miss_frames",
    "scored",
)


def dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    """Binary dilation by ``radius`` using iterated 8-neighbour OR (numpy only)."""
    out = np.asarray(mask, dtype=bool)
    for _ in range(max(0, int(radius))):
        nb = out.copy()
        nb[:-1, :] |= out[1:, :]
        nb[1:, :] |= out[:-1, :]
        nb[:, :-1] |= out[:, 1:]
        nb[:, 1:] |= out[:, :-1]
        nb[:-1, :-1] |= out[1:, 1:]
        nb[1:, 1:] |= out[:-1, :-1]
        nb[:-1, 1:] |= out[1:, :-1]
        nb[1:, :-1] |= out[:-1, 1:]
        out = nb
    return out


def lane_scores(pred: np.ndarray, gt: np.ndarray, *, tol: int = 2) -> dict[str, Optional[float]]:
    """Per-frame lane metrics between predicted and GT lane masks (bool, same shape).

    Rates whose denominator is empty are ``None`` (no fabrication): a frame with no
    GT lane has ``recall=None``; a frame with no predicted lane has ``precision=None``.
    """
    pred = np.asarray(pred, dtype=bool)
    gt = np.asarray(gt, dtype=bool)
    if pred.shape != gt.shape:
        raise ValueError(f"pred shape {pred.shape} != gt {gt.shape}")
    pred_area = int(pred.sum())
    gt_area = int(gt.sum())
    inter = int(np.logical_and(pred, gt).sum())
    union = int(np.logical_or(pred, gt).sum())
    gt_dil = dilate(gt, tol)
    pred_dil = dilate(pred, tol)
    tp_recall_tol = int(np.logical_and(pred_dil, gt).sum())   # GT pixels near a pred
    tp_prec_tol = int(np.logical_and(pred, gt_dil).sum())      # pred pixels near a GT
    return {
        "iou": (inter / union) if union else None,
        "recall": (inter / gt_area) if gt_area else None,
        "precision": (inter / pred_area) if pred_area else None,
        "recall_tol": (tp_recall_tol / gt_area) if gt_area else None,
        "precision_tol": (tp_prec_tol / pred_area) if pred_area else None,
    }


def evaluate_lane(pairs: list[tuple[str, np.ndarray, np.ndarray]], *, tol: int = 2) -> dict[str, Any]:
    """Aggregate lane metrics over ``(frame_id, pred_mask, gt_mask)`` triples.

    Only frames that HAVE a GT lane contribute to recall/IoU means (a frame with no
    lane is not evidence of lane skill either way). ``lane_miss_frames`` counts GT-lane
    frames whose strict recall falls below ``LANE_MISS_RECALL_FLOOR``."""
    ious, recalls, precisions, recalls_tol, precisions_tol = [], [], [], [], []
    lane_miss = 0
    scored = 0
    for _fid, pred, gt in pairs:
        s = lane_scores(pred, gt, tol=tol)
        scored += 1
        if s["iou"] is not None:
            ious.append(s["iou"])
        if s["recall"] is not None:
            recalls.append(s["recall"])
            if s["recall"] < LANE_MISS_RECALL_FLOOR:
                lane_miss += 1
        if s["precision"] is not None:
            precisions.append(s["precision"])
        if s["recall_tol"] is not None:
            recalls_tol.append(s["recall_tol"])
        if s["precision_tol"] is not None:
            precisions_tol.append(s["precision_tol"])

    def mean(v: list[float]) -> Optional[float]:
        return (sum(v) / len(v)) if v else None

    return {
        "scored": scored,
        "lane_frames": len(recalls),
        "lane_miss_frames": lane_miss,
        "mean_iou": mean(ious),
        "mean_recall": mean(recalls),
        "mean_precision": mean(precisions),
        "mean_recall_tol": mean(recalls_tol),
        "mean_precision_tol": mean(precisions_tol),
        "tol": tol,
    }


_EPS = 1e-3


def gate_lane(current: dict[str, Any], baseline: dict[str, Any]) -> tuple[bool, list[str]]:
    """Return (passed, reasons). Lane recall (strict + tolerance) and IoU must not
    regress; more missed-lane frames is a regression. Accepts a flat metrics dict or
    a saved ``{"metrics": {...}}`` payload."""
    metrics = baseline.get("metrics")
    if isinstance(metrics, dict):
        baseline = metrics
    reasons: list[str] = []

    def not_lower(key: str) -> None:
        cur, base = current.get(key), baseline.get(key)
        if cur is not None and base is not None and cur < base - _EPS:
            reasons.append(f"{key} regressed: {base:.4f} -> {cur:.4f}")

    not_lower("mean_recall")
    not_lower("mean_recall_tol")
    not_lower("mean_iou")

    cur_miss = int(current.get("lane_miss_frames", 0) or 0)
    base_miss = int(baseline.get("lane_miss_frames", 0) or 0)
    if cur_miss > base_miss:
        reasons.append(f"lane_miss_frames regressed: {base_miss} -> {cur_miss}")

    return (len(reasons) == 0, reasons)
