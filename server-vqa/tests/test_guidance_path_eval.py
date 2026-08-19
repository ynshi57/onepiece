"""Tests for line-level guidance-path evaluation + gate."""

from __future__ import annotations

from app.guidance_path import GuidanceLine, GuidancePath, GuidancePoint, PATH_STATUS_INSUFFICIENT
from app.guidance_path_eval import evaluate_guidance_paths, gate_guidance, score_frame


def _line(xs_ys_hw):
    return GuidancePath(
        status="ok",
        coverage=1.0,
        lines=[GuidanceLine(points=[GuidancePoint(x=x, y=y, half_width=hw) for x, y, hw in xs_ys_hw], confidence=1.0)],
    )


def test_identical_lines_have_zero_deviation_full_hit():
    gt = _line([(0.5, 0.0, 0.1), (0.5, 0.3, 0.1), (0.5, 0.6, 0.1)])
    pred = _line([(0.5, 0.0, 0.1), (0.5, 0.3, 0.1), (0.5, 0.6, 0.1)])
    s = score_frame("f", gt, pred)
    assert s.mean_deviation == 0.0
    assert s.hit_rate == 1.0
    assert s.over_extension == 0.0
    assert not s.false_go and not s.missed_path


def test_lateral_offset_within_corridor_still_hits():
    gt = _line([(0.5, 0.0, 0.1), (0.5, 0.6, 0.1)])
    pred = _line([(0.55, 0.0, 0.1), (0.55, 0.6, 0.1)])
    s = score_frame("f", gt, pred)
    assert abs(s.mean_deviation - 0.05) < 1e-6
    assert s.hit_rate == 1.0  # 0.05 <= half_width 0.1


def test_offset_outside_corridor_misses():
    gt = _line([(0.5, 0.0, 0.05), (0.5, 0.6, 0.05)])
    pred = _line([(0.7, 0.0, 0.05), (0.7, 0.6, 0.05)])
    s = score_frame("f", gt, pred)
    assert s.hit_rate == 0.0


def test_over_extension_is_flagged():
    gt = _line([(0.5, 0.0, 0.1), (0.5, 0.3, 0.1)])          # truth stops at y=0.3
    pred = _line([(0.5, 0.0, 0.1), (0.5, 0.3, 0.1), (0.5, 0.6, 0.1)])  # pred goes to 0.6
    s = score_frame("f", gt, pred)
    assert s.over_extension > 0.0


def test_false_go_and_missed_path_flags():
    gt_ok = _line([(0.5, 0.0, 0.1), (0.5, 0.6, 0.1)])
    insufficient = GuidancePath(status=PATH_STATUS_INSUFFICIENT, coverage=0.0)
    assert score_frame("a", insufficient, gt_ok).false_go is True   # pred ok, gt none
    assert score_frame("b", gt_ok, insufficient).missed_path is True  # gt ok, pred none


def test_gate_blocks_false_go_regression():
    baseline = {"false_go_frames": 1, "over_extension": 0.1, "mean_deviation": 0.05, "hit_rate": 0.8}
    worse = {"false_go_frames": 3, "over_extension": 0.1, "mean_deviation": 0.05, "hit_rate": 0.8}
    ok, reasons = gate_guidance(worse, baseline)
    assert not ok
    assert any("false_go" in r for r in reasons)


def test_gate_passes_when_not_worse():
    baseline = {"false_go_frames": 2, "over_extension": 0.2, "mean_deviation": 0.06, "hit_rate": 0.7}
    better = {"false_go_frames": 1, "over_extension": 0.15, "mean_deviation": 0.05, "hit_rate": 0.75}
    ok, reasons = gate_guidance(better, baseline)
    assert ok, reasons


def test_evaluate_aggregate():
    gt = _line([(0.5, 0.0, 0.1), (0.5, 0.6, 0.1)])
    pred = _line([(0.5, 0.0, 0.1), (0.5, 0.6, 0.1)])
    report = evaluate_guidance_paths([("f1", gt, pred), ("f2", gt, pred)])
    assert report["frames"] == 2
    assert report["both_ok"] == 2
    assert report["mean_deviation"] == 0.0
    assert report["hit_rate"] == 1.0
