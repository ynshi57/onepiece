"""Shared region-of-interest logic for VQASee path guidance.

Both the dataset importer (ground truth from dataset masks) and the offline
traversability predictor (prediction from a model's traversable mask) must use
the *same* ROI definitions and coverage->status thresholds. Keeping them in one
place avoids two implementations drifting apart, which would make evaluation
compare apples to oranges.

Critical: ground truth and prediction must come from *different sources* (a
dataset mask vs. a model output). This module only defines how a binary
traversable mask maps to path-guidance fields; it does not decide which mask is
"truth".
"""

from __future__ import annotations

import numpy as np

# ROIs are (x, y, width, height) in normalized image coordinates where y is
# measured from the bottom of the image upward (0 = bottom edge).
NEAR_ROI = (0.25, 0.00, 0.50, 0.58)
LEFT_ROI = (0.00, 0.05, 0.42, 0.62)
RIGHT_ROI = (0.58, 0.05, 0.42, 0.62)

# Coverage of traversable pixels within an ROI maps to a path status.
CANDIDATE_OPEN_MIN = 0.60
CAUTION_MIN = 0.28


def roi_coverage(mask: np.ndarray, roi: tuple[float, float, float, float]) -> float | None:
    """Fraction of traversable (truthy) pixels inside an ROI, or None if empty."""
    height, width = mask.shape[:2]
    x, y, w, h = roi
    x0 = max(0, min(width - 1, int(x * width)))
    x1 = max(x0 + 1, min(width, int((x + w) * width)))
    y_top = 1.0 - y - h
    y_bottom = 1.0 - y
    y0 = max(0, min(height - 1, int(y_top * height)))
    y1 = max(y0 + 1, min(height, int(y_bottom * height)))
    crop = mask[y0:y1, x0:x1]
    if crop.size == 0:
        return None
    return round(float(crop.mean()), 4)


def status_from_coverage(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value >= CANDIDATE_OPEN_MIN:
        return "candidateOpen"
    if value >= CAUTION_MIN:
        return "caution"
    return "blocked"


def focus_direction(near: str, left: str, right: str) -> str:
    risk = {"blocked", "caution"}
    if near in risk:
        return "center"
    if left in risk and right not in risk:
        return "left"
    if right in risk and left not in risk:
        return "right"
    if left in risk and right in risk:
        return "left"
    return "unknown"


def path_guidance_from_mask(mask: np.ndarray) -> dict[str, object]:
    """Map a binary traversable mask to path-guidance fields + raw coverage.

    Returns near/left/right status, focus direction, and the per-ROI coverage so
    callers can inspect why a status was chosen.
    """
    near_cov = roi_coverage(mask, NEAR_ROI)
    left_cov = roi_coverage(mask, LEFT_ROI)
    right_cov = roi_coverage(mask, RIGHT_ROI)
    near_status = status_from_coverage(near_cov)
    left_status = status_from_coverage(left_cov)
    right_status = status_from_coverage(right_cov)
    return {
        "near_path_status": near_status,
        "left_front_status": left_status,
        "right_front_status": right_status,
        "focus_direction": focus_direction(near_status, left_status, right_status),
        "coverage": {"near_path": near_cov, "left_front": left_cov, "right_front": right_cov},
    }
