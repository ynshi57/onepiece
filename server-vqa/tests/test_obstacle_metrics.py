"""Unit tests for obstacle detection metrics (CamVid coverage proxy).

Honesty guardrails these tests protect:
- the Vision (origin lower-left) box -> top-left grid coordinate flip is correct,
- empty-denominator rates are None (never fabricated),
- a frame where GT obstacles are NOT covered by any box counts as a real miss,
- the gate blocks a safety regression (fewer obstacles seen).
"""

from __future__ import annotations

import numpy as np

from app.obstacle_metrics import (
    boxes_to_grid,
    evaluate_obstacles,
    gate_obstacle,
    obstacle_scores,
)
from app.region_grid import grid_to_wire


def _wire(grid: np.ndarray) -> dict:
    return grid_to_wire(grid.astype(np.uint8))


def test_boxes_to_grid_flips_vision_y_to_top_left_rows():
    # A box hugging the BOTTOM in Vision coords (y=0, small h) must land on the
    # BOTTOM rows of the top-left grid, not the top rows.
    rows, cols = 4, 4
    bottom_box = [(0.0, 0.0, 1.0, 0.25)]  # full width, bottom quarter (Vision y up)
    grid = boxes_to_grid(bottom_box, rows, cols)
    assert grid[rows - 1, :].all()  # bottom row set
    assert not grid[0, :].any()  # top row untouched

    top_box = [(0.0, 0.75, 1.0, 0.25)]  # top quarter in Vision coords
    g2 = boxes_to_grid(top_box, rows, cols)
    assert g2[0, :].all()
    assert not g2[rows - 1, :].any()


def test_obstacle_scores_perfect_and_partial_coverage():
    rows, cols = 4, 4
    gt = np.zeros((rows, cols), dtype=bool)
    gt[rows - 1, :] = True  # obstacle along the bottom row
    # A box covering exactly the bottom row (Vision bottom quarter).
    s = obstacle_scores(gt, [(0.0, 0.0, 1.0, 0.25)])
    assert s["coverage_recall"] == 1.0
    assert s["box_precision"] == 1.0

    # A box that misses entirely (top quarter): recall 0, precision 0.
    s2 = obstacle_scores(gt, [(0.0, 0.75, 1.0, 0.25)])
    assert s2["coverage_recall"] == 0.0
    assert s2["box_precision"] == 0.0


def test_obstacle_scores_empty_denominators_are_none():
    rows, cols = 4, 4
    gt_empty = np.zeros((rows, cols), dtype=bool)
    # No GT obstacle -> recall None; a box present -> precision defined (0 overlap).
    s = obstacle_scores(gt_empty, [(0.0, 0.0, 0.5, 0.5)])
    assert s["coverage_recall"] is None
    assert s["box_precision"] == 0.0
    # GT present, no predicted box -> precision None (no fabrication).
    gt = np.zeros((rows, cols), dtype=bool)
    gt[0, 0] = True
    s2 = obstacle_scores(gt, [])
    assert s2["box_precision"] is None
    assert s2["coverage_recall"] == 0.0


def test_evaluate_obstacles_counts_misses_and_skips_only_obstacle_kinds():
    rows, cols = 4, 4
    gt = np.zeros((rows, cols), dtype=bool)
    gt[rows - 1, :] = True
    gt_wire = _wire(gt)

    covered = {"objects": [{"kind": "car", "box": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.25}}]}
    # A 'laneMarking' is NOT an obstacle kind -> ignored -> this frame is a miss.
    non_obstacle = {"objects": [{"kind": "laneMarking", "box": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.25}}]}

    report = evaluate_obstacles(
        [
            ("f_hit", gt_wire, covered["objects"]),
            ("f_miss", gt_wire, non_obstacle["objects"]),
        ]
    )
    assert report["scored"] == 2
    assert report["obstacle_frames"] == 2
    assert report["obstacle_miss_frames"] == 1  # the non-obstacle-kind frame
    assert report["mean_coverage_recall"] == 0.5  # (1.0 + 0.0) / 2


def test_evaluate_obstacles_surfaces_malformed_gt_as_skipped():
    report = evaluate_obstacles([("bad", {"cols": 0, "rows": 0, "cells": []}, [])])
    assert report["skipped"] == 1
    assert report["scored"] == 0


def test_gate_obstacle_blocks_recall_drop_and_more_misses():
    baseline = {"metrics": {"mean_coverage_recall": 0.80, "obstacle_miss_frames": 3}}
    worse = {"mean_coverage_recall": 0.60, "obstacle_miss_frames": 7}
    passed, reasons = gate_obstacle(worse, baseline)
    assert passed is False
    assert any("coverage_recall" in r for r in reasons)
    assert any("obstacle_miss_frames" in r for r in reasons)

    same = {"mean_coverage_recall": 0.80, "obstacle_miss_frames": 3}
    ok, _ = gate_obstacle(same, baseline)
    assert ok is True
