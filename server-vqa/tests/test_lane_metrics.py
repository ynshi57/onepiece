"""Tests for lane-marking segmentation metrics (thin-class honest scoring)."""

from __future__ import annotations

import numpy as np

from app.lane_metrics import (
    dilate,
    evaluate_lane,
    gate_lane,
    lane_scores,
)


def _vline(h, w, x, thickness=1):
    m = np.zeros((h, w), dtype=bool)
    m[:, x : x + thickness] = True
    return m


def test_dilate_expands_by_radius():
    m = np.zeros((5, 5), dtype=bool)
    m[2, 2] = True
    d1 = dilate(m, 1)
    assert d1[1:4, 1:4].all()          # 3x3 block around center
    assert not d1[0, 0]                 # corner beyond radius 1 stays off
    assert dilate(m, 0)[2, 2] and int(dilate(m, 0).sum()) == 1


def test_perfect_lane_scores_one():
    gt = _vline(20, 20, 10)
    s = lane_scores(gt.copy(), gt.copy(), tol=2)
    assert s["iou"] == 1.0
    assert s["recall"] == 1.0
    assert s["precision"] == 1.0


def test_one_pixel_offset_kills_strict_iou_but_tolerance_recovers():
    """A 1-px lateral shift: strict IoU/recall are ~0 (thin class is unforgiving),
    but the tolerance-band recall/precision recover — the whole reason we report both."""
    gt = _vline(20, 20, 10)
    pred = _vline(20, 20, 12)  # shifted 2 px, no overlap
    s = lane_scores(pred, gt, tol=2)
    assert s["iou"] == 0.0
    assert s["recall"] == 0.0
    assert s["recall_tol"] == 1.0       # every GT pixel has a pred within 2 px
    assert s["precision_tol"] == 1.0


def test_empty_gt_yields_none_recall_not_zero():
    gt = np.zeros((10, 10), dtype=bool)
    pred = _vline(10, 10, 5)
    s = lane_scores(pred, gt, tol=1)
    assert s["recall"] is None          # no lane to recall -> undefined, not 0
    assert s["precision"] == 0.0        # predicted lane where there is none


def test_evaluate_counts_missed_lane_frames_and_skips_no_lane_frames():
    gt = _vline(20, 20, 10)
    good = (_vline(20, 20, 10), gt)
    missed = (np.zeros((20, 20), dtype=bool), gt)   # predicts nothing -> recall 0 -> miss
    no_lane = (_vline(20, 20, 5), np.zeros((20, 20), dtype=bool))
    agg = evaluate_lane([("a", *good), ("b", *missed), ("c", *no_lane)], tol=2)
    assert agg["scored"] == 3
    assert agg["lane_frames"] == 2       # only frames WITH gt lane count for recall
    assert agg["lane_miss_frames"] == 1  # the 'missed' frame
    assert agg["mean_recall"] == 0.5     # (1.0 + 0.0) / 2


def test_gate_blocks_recall_regression():
    base = {"metrics": {"mean_recall": 0.5, "mean_recall_tol": 0.7, "mean_iou": 0.3, "lane_miss_frames": 10}}
    worse = {"mean_recall": 0.4, "mean_recall_tol": 0.7, "mean_iou": 0.3, "lane_miss_frames": 10}
    passed, reasons = gate_lane(worse, base)
    assert passed is False
    assert any("mean_recall" in r for r in reasons)


def test_gate_passes_when_improved():
    base = {"metrics": {"mean_recall": 0.5, "mean_recall_tol": 0.7, "mean_iou": 0.3, "lane_miss_frames": 10}}
    better = {"mean_recall": 0.6, "mean_recall_tol": 0.8, "mean_iou": 0.35, "lane_miss_frames": 6}
    passed, reasons = gate_lane(better, base)
    assert passed is True
    assert reasons == []
