"""Role-conditioned traversability metrics (offline, region-grid based).

These quantify how the iPhone's PREDICTED walkable grid behaves against the
ROLE-conditioned ground-truth layers (primary / caution / lane / obstacle) that
``create_camvid_role_manifest`` writes. The point is safety-oriented defect
measurement, not a single blended score:

- ``primary_recall``     : of the role's PRIMARY surface (sidewalk for a walker),
                           how much does the phone also call walkable. Low = it
                           MISSES the surface this role should use.
- ``road_as_primary_rate``: of what the phone calls walkable, how much is actually
                           the role's CAUTION surface (the carriageway, for a
                           walker). High = it routes a pedestrian onto the road —
                           the core trust/safety defect this whole line of work
                           targets (a.k.a. "Road-as-Sidewalk Error Rate").
- ``lane_coverage``      : of GT lane-marking cells, how many fall inside the
                           predicted walkable region (diagnostic; lane is a thin,
                           separate capability layer).
- ``obstacle_overlap_rate``: of what the phone calls walkable, how much overlaps a
                           physical obstacle. High = it would route INTO a blocker.

Rates whose denominator is empty are ``None`` so aggregation never fabricates a
value (honest handling of frames with no sidewalk / no prediction / no lane).
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np

from app.region_grid import grid_from_wire

# Frame-level safety ceilings. A frame is flagged UNSAFE for this role when the
# phone would put a walker meaningfully onto the road, or into an obstacle.
ROAD_AS_PRIMARY_CEIL = 0.20
OBSTACLE_OVERLAP_CEIL = 0.05

ROLE_BASELINE_KEYS = (
    "mean_primary_recall",
    "mean_road_as_primary_rate",
    "mean_lane_coverage",
    "mean_obstacle_overlap_rate",
    "road_as_primary_frames",
    "obstacle_overlap_frames",
    "scored",
)


def role_region_scores(
    pred: np.ndarray,
    *,
    primary: np.ndarray,
    caution: np.ndarray,
    lane: np.ndarray,
    obstacle: np.ndarray,
) -> dict[str, Optional[float]]:
    """Per-frame role metrics between a predicted walkable grid and GT layers.

    All inputs are boolean ``(rows, cols)`` grids of identical shape."""
    for name, layer in (("primary", primary), ("caution", caution), ("lane", lane), ("obstacle", obstacle)):
        if layer.shape != pred.shape:
            raise ValueError(f"{name} grid shape {layer.shape} != pred {pred.shape}")
    pred_area = int(pred.sum())
    primary_area = int(primary.sum())
    lane_area = int(lane.sum())
    return {
        "primary_recall": (int(np.logical_and(pred, primary).sum()) / primary_area) if primary_area else None,
        "road_as_primary_rate": (int(np.logical_and(pred, caution).sum()) / pred_area) if pred_area else None,
        "lane_coverage": (int(np.logical_and(pred, lane).sum()) / lane_area) if lane_area else None,
        "obstacle_overlap_rate": (int(np.logical_and(pred, obstacle).sum()) / pred_area) if pred_area else None,
    }


def _grids_from_role_wire(role_grids: Any) -> Optional[dict[str, np.ndarray]]:
    """Parse the manifest ``role_grids`` block into bool arrays, or None if any
    required layer is malformed (surfaced as ``skipped``, never scored as garbage)."""
    if not isinstance(role_grids, dict):
        return None
    out: dict[str, np.ndarray] = {}
    for key in ("primary", "caution", "lane", "obstacle"):
        parsed = grid_from_wire(role_grids.get(key))
        if parsed is None:
            return None
        out[key] = parsed
    return out


def evaluate_role_region(pairs: list[tuple[str, Any, Any]]) -> dict[str, Any]:
    """Aggregate role metrics over ``(frame_id, role_grids_wire, pred_grid_wire)``.

    Returns means (over frames where the rate is defined) plus safety frame counts.
    Malformed/shape-mismatched grids are ``skipped`` and surfaced, never dropped."""
    primary_recalls: list[float] = []
    road_rates: list[float] = []
    lane_covs: list[float] = []
    obstacle_rates: list[float] = []
    road_frames = 0  # walker put meaningfully onto the road
    obstacle_frames = 0  # walker put into an obstacle
    scored = 0
    skipped = 0
    for _fid, role_wire, pred_wire in pairs:
        layers = _grids_from_role_wire(role_wire)
        pred = grid_from_wire(pred_wire)
        if layers is None or pred is None or any(layer.shape != pred.shape for layer in layers.values()):
            skipped += 1
            continue
        s = role_region_scores(
            pred,
            primary=layers["primary"],
            caution=layers["caution"],
            lane=layers["lane"],
            obstacle=layers["obstacle"],
        )
        scored += 1
        if s["primary_recall"] is not None:
            primary_recalls.append(float(s["primary_recall"]))
        if s["road_as_primary_rate"] is not None:
            road_rates.append(float(s["road_as_primary_rate"]))
            if s["road_as_primary_rate"] > ROAD_AS_PRIMARY_CEIL:
                road_frames += 1
        if s["lane_coverage"] is not None:
            lane_covs.append(float(s["lane_coverage"]))
        if s["obstacle_overlap_rate"] is not None:
            obstacle_rates.append(float(s["obstacle_overlap_rate"]))
            if s["obstacle_overlap_rate"] > OBSTACLE_OVERLAP_CEIL:
                obstacle_frames += 1

    def mean(values: list[float]) -> Optional[float]:
        return (sum(values) / len(values)) if values else None

    return {
        "scored": scored,
        "skipped": skipped,
        "road_as_primary_frames": road_frames,
        "obstacle_overlap_frames": obstacle_frames,
        "mean_primary_recall": mean(primary_recalls),
        "mean_road_as_primary_rate": mean(road_rates),
        "mean_lane_coverage": mean(lane_covs),
        "mean_obstacle_overlap_rate": mean(obstacle_rates),
    }


_EPS_RATE = 0.01


def gate_role_region(current: dict[str, Any], baseline: dict[str, Any]) -> tuple[bool, list[str]]:
    """Return (passed, reasons). A role-safety regression fails the gate.

    Safety-critical, ceiling metrics (must NOT increase): ``road_as_primary_frames``
    and ``obstacle_overlap_frames`` — more frames routing a walker onto the road or
    into an obstacle is a regression. ``mean_primary_recall`` must not drop.

    Accepts a flat metrics dict or a saved ``{"metrics": {...}}`` payload."""
    metrics = baseline.get("metrics")
    if isinstance(metrics, dict):
        baseline = metrics
    reasons: list[str] = []

    def worse_up(key: str) -> None:
        cur = int(current.get(key, 0) or 0)
        base = int(baseline.get(key, 0) or 0)
        if cur > base:
            reasons.append(f"{key} regressed: {base} -> {cur}")

    worse_up("road_as_primary_frames")
    worse_up("obstacle_overlap_frames")

    cur_r, base_r = current.get("mean_primary_recall"), baseline.get("mean_primary_recall")
    if cur_r is not None and base_r is not None and cur_r < base_r - _EPS_RATE:
        reasons.append(f"mean_primary_recall regressed: {base_r:.4f} -> {cur_r:.4f}")

    return (len(reasons) == 0, reasons)
