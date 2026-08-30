"""A3: role-conditioned traversability metrics.

Synthetic grids exercise the four defect metrics and the safety frame counts,
plus the honest None handling when a denominator is empty. These are the numbers
T1 uses to quantify how often the current binary prediction routes a walker onto
the road (Road-as-Sidewalk Error Rate) or into an obstacle.
"""

from __future__ import annotations

import numpy as np

from app.region_grid import grid_to_wire
from app.role_metrics import (
    ROAD_AS_PRIMARY_CEIL,
    evaluate_role_region,
    gate_role_region,
    role_region_scores,
)


def _wire(cells: np.ndarray) -> dict:
    return grid_to_wire(cells.astype(np.uint8))


def _blank(rows=4, cols=4) -> np.ndarray:
    return np.zeros((rows, cols), dtype=bool)


def test_road_as_primary_rate_flags_walker_routed_onto_road():
    """Prediction covers both sidewalk (primary) and road (caution). Half of what
    it calls walkable is actually the road -> road_as_primary_rate = 0.5."""
    primary = _blank(); primary[3, 0:2] = True    # sidewalk cells
    caution = _blank(); caution[3, 2:4] = True    # road cells
    lane = _blank()
    obstacle = _blank()
    pred = _blank(); pred[3, 0:4] = True           # phone calls the whole bottom row walkable

    s = role_region_scores(pred, primary=primary, caution=caution, lane=lane, obstacle=obstacle)
    assert s["primary_recall"] == 1.0              # covers all sidewalk
    assert s["road_as_primary_rate"] == 0.5         # half of walkable is road
    assert s["lane_coverage"] is None               # no lane cells -> honest None
    assert s["obstacle_overlap_rate"] == 0.0


def test_obstacle_overlap_detected():
    primary = _blank(); primary[3, 0:3] = True
    caution = _blank()
    lane = _blank()
    obstacle = _blank(); obstacle[3, 3] = True
    pred = _blank(); pred[3, 0:4] = True            # walks through the obstacle cell

    s = role_region_scores(pred, primary=primary, caution=caution, lane=lane, obstacle=obstacle)
    assert s["obstacle_overlap_rate"] == 0.25


def test_empty_prediction_yields_none_rates_not_zero():
    primary = _blank(); primary[3, 0:2] = True
    s = role_region_scores(_blank(), primary=primary, caution=_blank(), lane=_blank(), obstacle=_blank())
    assert s["primary_recall"] == 0.0               # missed all sidewalk (denominator=primary)
    assert s["road_as_primary_rate"] is None        # no predicted walkable -> undefined
    assert s["obstacle_overlap_rate"] is None


def test_evaluate_role_region_counts_unsafe_frames_and_skips_malformed():
    primary = _blank(); primary[3, 0:2] = True
    caution = _blank(); caution[3, 2:4] = True
    lane = _blank()
    obstacle = _blank()
    role_wire = {"primary": _wire(primary), "caution": _wire(caution), "lane": _wire(lane), "obstacle": _wire(obstacle)}

    onto_road = _wire(caution)                              # prediction sits on the road -> rate 1.0
    clean = _wire(primary)                                   # only sidewalk -> safe

    agg = evaluate_role_region([
        ("f_bad", role_wire, onto_road),
        ("f_ok", role_wire, clean),
        ("f_broken", {"primary": {"cols": 1}}, clean),       # malformed -> skipped
    ])
    assert agg["scored"] == 2
    assert agg["skipped"] == 1
    assert agg["road_as_primary_frames"] == 1                # only the onto_road frame
    assert agg["mean_primary_recall"] == 0.5                 # 0.0 (on road) + 1.0 (clean) / 2
    assert agg["mean_road_as_primary_rate"] is not None


def test_gate_role_region_blocks_more_road_frames():
    base = {"road_as_primary_frames": 3, "obstacle_overlap_frames": 0, "mean_primary_recall": 0.8}
    worse = {"road_as_primary_frames": 5, "obstacle_overlap_frames": 0, "mean_primary_recall": 0.8}
    passed, reasons = gate_role_region(worse, base)
    assert not passed
    assert any("road_as_primary_frames" in r for r in reasons)


def test_gate_role_region_passes_when_not_worse():
    base = {"metrics": {"road_as_primary_frames": 3, "obstacle_overlap_frames": 1, "mean_primary_recall": 0.8}}
    same = {"road_as_primary_frames": 3, "obstacle_overlap_frames": 1, "mean_primary_recall": 0.82}
    passed, reasons = gate_role_region(same, base)
    assert passed
    assert reasons == []


def test_road_as_primary_ceiling_is_conservative():
    assert 0.0 < ROAD_AS_PRIMARY_CEIL <= 0.25
