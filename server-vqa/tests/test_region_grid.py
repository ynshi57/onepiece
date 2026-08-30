"""Tests for the traversable-region grid: the shared walkable-area raster used to
score "how close is the region the iPhone perceives to the annotated truth".

Covers the downsample (orientation + majority), per-frame IoU/recall/precision,
aggregation buckets, the regression gate, and a text-level contract keeping the
grid schema/resolution in sync with the Swift producer."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from app.region_grid import (
    GRID_COLS,
    GRID_ROWS,
    LANE_GRID_COLS,
    LANE_GRID_ROWS,
    REGION_BASELINE_KEYS,
    downsample_mask_to_grid,
    evaluate_region_grids,
    gate_region,
    lane_presence_grid,
    grid_from_wire,
    grid_to_wire,
    region_scores,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SEG_SWIFT = REPO_ROOT / "ios-vqa-app" / "VQASee" / "VQASee" / "LocalSegmentation.swift"
PERCEPTION_SWIFT = REPO_ROOT / "ios-vqa-app" / "VQASee" / "VQASee" / "LocalPerception.swift"
HARNESS_SWIFT = REPO_ROOT / "ios-vqa-app" / "perception-harness" / "Sources" / "PerceptionHarness" / "main.swift"


def test_grid_resolution_matches_swift_producer():
    """The Python GT grid MUST be the same resolution as the Swift predicted grid,
    or IoU compares mismatched rasters and silently skips every frame."""
    swift = SEG_SWIFT.read_text(encoding="utf-8")
    assert f"gridCols = {GRID_COLS}" in swift
    assert f"gridRows = {GRID_ROWS}" in swift


def test_grid_wire_schema_in_sync_with_swift():
    perception = PERCEPTION_SWIFT.read_text(encoding="utf-8")
    # TraversableGrid.toWire keys must match grid_to_wire / grid_from_wire.
    for key in ["cols", "rows", "cells"]:
        assert f'"{key}"' in perception, f"grid wire key {key!r} missing from Swift"
    # The harness must actually emit the region under this exact key.
    assert '"traversable_grid"' in HARNESS_SWIFT.read_text(encoding="utf-8")


def test_downsample_shape_and_top_row_orientation():
    # Top half traversable, bottom half blocked. Row 0 must stay the TOP, so the
    # top grid rows are traversable and the bottom rows are not.
    mask = np.zeros((100, 80), dtype=bool)
    mask[:50, :] = True
    grid = downsample_mask_to_grid(mask)
    assert grid.shape == (GRID_ROWS, GRID_COLS)
    assert grid[0].all(), "top image row should map to top grid row (traversable)"
    assert not grid[-1].any(), "bottom image row should map to bottom grid row (blocked)"


def test_downsample_is_majority_vote():
    # A cell that is >50% traversable is 1; a sparse cell is 0.
    mask = np.zeros((GRID_ROWS, GRID_COLS), dtype=bool)
    mask[0, :] = True  # exactly one cell row fully traversable
    grid = downsample_mask_to_grid(mask)
    assert grid[0].all()
    assert grid[1:].sum() == 0


def test_wire_roundtrip():
    cells = np.zeros((GRID_ROWS, GRID_COLS), dtype=np.uint8)
    cells[5:10, 3:7] = 1
    wire = grid_to_wire(cells)
    assert wire["cols"] == GRID_COLS and wire["rows"] == GRID_ROWS
    back = grid_from_wire(wire)
    assert back is not None
    assert np.array_equal(back, cells.astype(bool))


def test_grid_from_wire_rejects_malformed():
    assert grid_from_wire(None) is None
    assert grid_from_wire({"cols": 2, "rows": 2, "cells": [1, 0, 1]}) is None  # wrong length
    assert grid_from_wire({"cols": 0, "rows": 0, "cells": []}) is None
    assert grid_from_wire({"cols": 2, "rows": 1, "cells": [1, 0]}) is not None


def test_region_scores_perfect_disjoint_partial():
    a = np.zeros((4, 4), dtype=bool)
    a[:2, :] = True
    # perfect
    s = region_scores(a.copy(), a.copy())
    assert s["iou"] == 1.0 and s["recall"] == 1.0 and s["precision"] == 1.0
    # disjoint
    b = np.zeros((4, 4), dtype=bool)
    b[2:, :] = True
    s = region_scores(b, a)
    assert s["iou"] == 0.0 and s["recall"] == 0.0 and s["precision"] == 0.0
    # partial: pred is all-True, gt is top half -> recall 1, precision 0.5
    full = np.ones((4, 4), dtype=bool)
    s = region_scores(full, a)
    assert s["recall"] == 1.0
    assert abs(s["precision"] - 0.5) < 1e-9


def test_region_scores_empty_denominators_are_none_not_fabricated():
    empty = np.zeros((4, 4), dtype=bool)
    some = np.zeros((4, 4), dtype=bool)
    some[0, 0] = True
    # no predicted walkable -> precision undefined (None), recall 0
    s = region_scores(empty, some)
    assert s["precision"] is None
    assert s["recall"] == 0.0
    # both empty -> iou 1 (agree there's nothing), recall/precision None
    s = region_scores(empty, empty)
    assert s["iou"] == 1.0 and s["recall"] is None and s["precision"] is None


def _grid(rows, cols, ones):
    g = np.zeros((rows, cols), dtype=np.uint8)
    for (r, c) in ones:
        g[r, c] = 1
    return grid_to_wire(g)


def test_evaluate_counts_false_go_and_miss():
    gt = np.zeros((2, 2), dtype=np.uint8)
    gt[0, 0] = 1
    gt[0, 1] = 1  # truth: top row walkable
    # frame A: predicts the bottom row -> precision 0 (false_go), recall 0 (miss)
    predA = np.zeros((2, 2), dtype=np.uint8)
    predA[1, 0] = 1
    predA[1, 1] = 1
    # frame B: predicts exactly the truth -> both ok
    predB = gt.copy()
    pairs = [
        ("A", grid_to_wire(gt), grid_to_wire(predA)),
        ("B", grid_to_wire(gt), grid_to_wire(predB)),
    ]
    rep = evaluate_region_grids(pairs)
    assert rep["scored"] == 2
    assert rep["region_false_go_frames"] == 1
    assert rep["region_miss_frames"] == 1
    assert rep["both_ok"] == 1


def test_evaluate_skips_shape_mismatch_not_silently():
    gt = grid_to_wire(np.ones((2, 2), dtype=np.uint8))
    bad = {"cols": 3, "rows": 3, "cells": [1] * 9}
    rep = evaluate_region_grids([("x", gt, bad)])
    assert rep["scored"] == 0 and rep["skipped"] == 1


def test_gate_region_passes_when_equal_flags_regressions():
    base = {
        "mean_iou": 0.62,
        "mean_recall": 0.64,
        "mean_precision": 0.80,
        "region_false_go_frames": 118,
        "region_miss_frames": 158,
    }
    ok, reasons = gate_region(dict(base), dict(base))
    assert ok and reasons == []

    # false_go increase = safety regression
    worse = dict(base, region_false_go_frames=140)
    ok, reasons = gate_region(worse, base)
    assert not ok and any("false_go" in r for r in reasons)

    # precision drop = regression
    worse = dict(base, mean_precision=0.70)
    ok, reasons = gate_region(worse, base)
    assert not ok and any("mean_precision" in r for r in reasons)


def test_gate_region_unwraps_saved_baseline_payload():
    metrics = {
        "mean_iou": 0.62,
        "mean_recall": 0.64,
        "mean_precision": 0.80,
        "region_false_go_frames": 118,
    }
    payload = {"name": "camvid-ios-region", "metrics": metrics}
    # Regressing precision against the WRAPPED baseline must still fail (proves it
    # unwraps instead of comparing against absent keys and passing vacuously).
    ok, reasons = gate_region(dict(metrics, mean_precision=0.60), payload)
    assert not ok and any("mean_precision" in r for r in reasons)


def test_baseline_keys_are_snapshot_by_the_report():
    # Every gate-relevant key must exist in a real report, or saving the baseline
    # would drop it and the gate would silently no-op.
    rep = evaluate_region_grids([("a", grid_to_wire(np.ones((2, 2), np.uint8)), grid_to_wire(np.ones((2, 2), np.uint8)))])
    for key in REGION_BASELINE_KEYS:
        assert key in rep, f"baseline key {key!r} not produced by evaluate_region_grids"


def test_lane_presence_grid_preserves_thin_lines_that_majority_erases():
    """A thin diagonal line (a single-pixel-wide lane) must SURVIVE any-pixel
    presence but be ERASED by majority-vote downsampling — the whole reason a
    separate lane raster exists."""
    # Source much larger than either grid, so a 1-px line is a tiny MINORITY of
    # every coarse cell (majority erases) yet still touches fine cells (any-pixel
    # keeps). This mirrors real CamVid lanes: thin against a 720p-ish frame.
    h, w = 480, 640
    mask = np.zeros((h, w), dtype=bool)
    for i in range(h):
        mask[i, i * w // h] = True  # 1-px-wide diagonal spanning the frame

    fine = lane_presence_grid(mask)
    assert fine.shape == (LANE_GRID_ROWS, LANE_GRID_COLS)
    assert int(fine.sum()) >= LANE_GRID_ROWS // 2  # the thin line survives

    coarse = downsample_mask_to_grid(mask)  # majority vote at 64x48
    assert int(coarse.sum()) == 0  # thin line is wiped out by majority


def test_lane_presence_grid_any_pixel_sets_cell():
    # One lit pixel anywhere in the source sets exactly the cell it maps to.
    mask = np.zeros((96, 128), dtype=bool)
    mask[0, 0] = True
    grid = lane_presence_grid(mask, rows=4, cols=4)
    assert grid[0, 0] == 1
    assert int(grid.sum()) == 1
