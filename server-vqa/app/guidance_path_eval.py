"""Line-level evaluation for traversable guidance paths.

Compares a PREDICTED guidance line (on-device engine / offline harness) against
the GROUND-TRUTH guidance line (server, from a dataset traversability mask). All
lines share the schema in `app.guidance_path` (bottom-left-origin normalized).

Metrics are named honestly and never hide the safety-critical case:
- mean_deviation   : lateral error where BOTH lines cover the same forward y.
- hit_rate         : fraction of compared samples where pred lies inside the GT
                     corridor (|dx| <= gt.half_width).
- pred_coverage    : fraction of the GT forward span the prediction actually
                     traced (missed path shows up as low coverage).
- over_extension   : fraction of the pred line that extends BEYOND the GT's free
                     space (predicting a path where truth has none) -> the
                     safety-critical "risk" signal.
- direction_error  : difference in overall heading (top.x - bottom.x).
- false_go_frames  : count of frames where pred says "ok" but GT is
                     "insufficient" (claiming a path the truth does not support).
- missed_path_frames: pred "insufficient" while GT is "ok" (failed to see a path).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.guidance_path import GuidanceLine, GuidancePath, PATH_STATUS_OK


def _interp(line: GuidanceLine, y: float) -> tuple[float, float] | None:
    """Interpolate (x, half_width) at forward distance y, or None if out of span.

    Points are ordered by ascending y.
    """
    pts = line.points
    if not pts:
        return None
    if y < pts[0].y - 1e-9 or y > pts[-1].y + 1e-9:
        return None
    for i in range(1, len(pts)):
        a, b = pts[i - 1], pts[i]
        if a.y <= y <= b.y or b.y <= y <= a.y:
            span = b.y - a.y
            t = 0.0 if abs(span) < 1e-9 else (y - a.y) / span
            return (a.x + t * (b.x - a.x), a.half_width + t * (b.half_width - a.half_width))
    return (pts[-1].x, pts[-1].half_width)


def _heading(line: GuidanceLine) -> float:
    return line.points[-1].x - line.points[0].x if len(line.points) >= 2 else 0.0


@dataclass
class FrameGuidanceScore:
    frame_id: str
    gt_status: str
    pred_status: str
    mean_deviation: float | None
    hit_rate: float | None
    pred_coverage: float | None
    over_extension: float | None
    direction_error: float | None
    false_go: bool
    missed_path: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "gt_status": self.gt_status,
            "pred_status": self.pred_status,
            "mean_deviation": self.mean_deviation,
            "hit_rate": self.hit_rate,
            "pred_coverage": self.pred_coverage,
            "over_extension": self.over_extension,
            "direction_error": self.direction_error,
            "false_go": self.false_go,
            "missed_path": self.missed_path,
        }


def score_frame(frame_id: str, gt: GuidancePath, pred: GuidancePath) -> FrameGuidanceScore:
    gt_ok = gt.status == PATH_STATUS_OK and gt.primary is not None
    pred_ok = pred.status == PATH_STATUS_OK and pred.primary is not None

    false_go = pred_ok and not gt_ok
    missed_path = gt_ok and not pred_ok

    if not (gt_ok and pred_ok):
        return FrameGuidanceScore(
            frame_id=frame_id, gt_status=gt.status, pred_status=pred.status,
            mean_deviation=None, hit_rate=None, pred_coverage=None,
            over_extension=None, direction_error=None,
            false_go=false_go, missed_path=missed_path,
        )

    gt_line = gt.primary
    pred_line = pred.primary
    gt_ys = [p.y for p in gt_line.points]
    gt_min_y, gt_max_y = min(gt_ys), max(gt_ys)

    devs: list[float] = []
    hits = 0
    covered = 0
    for p in gt_line.points:
        pred_sample = _interp(pred_line, p.y)
        if pred_sample is None:
            continue
        covered += 1
        gt_x = p.x
        dx = abs(pred_sample[0] - gt_x)
        devs.append(dx)
        if dx <= p.half_width + 1e-9:
            hits += 1

    pred_coverage = covered / len(gt_line.points)
    mean_deviation = sum(devs) / len(devs) if devs else None
    hit_rate = hits / covered if covered else None

    # Over-extension: fraction of pred points that extend beyond the GT free
    # space (forward past gt_max_y). This is the safety signal.
    beyond = sum(1 for p in pred_line.points if p.y > gt_max_y + 1e-6)
    over_extension = beyond / len(pred_line.points)

    direction_error = abs(_heading(pred_line) - _heading(gt_line))

    return FrameGuidanceScore(
        frame_id=frame_id, gt_status=gt.status, pred_status=pred.status,
        mean_deviation=mean_deviation, hit_rate=hit_rate, pred_coverage=pred_coverage,
        over_extension=over_extension, direction_error=direction_error,
        false_go=False, missed_path=False,
    )


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def evaluate_guidance_paths(pairs: list[tuple[str, GuidancePath, GuidancePath]]) -> dict[str, Any]:
    """Aggregate line-level metrics over (frame_id, gt, pred) triples."""
    frames = [score_frame(fid, gt, pred) for fid, gt, pred in pairs]
    both_ok = [f for f in frames if f.mean_deviation is not None]

    report: dict[str, Any] = {
        "frames": len(frames),
        "both_ok": len(both_ok),
        "false_go_frames": sum(1 for f in frames if f.false_go),
        "missed_path_frames": sum(1 for f in frames if f.missed_path),
        "mean_deviation": _mean([f.mean_deviation for f in both_ok]),
        "hit_rate": _mean([f.hit_rate for f in both_ok if f.hit_rate is not None]),
        "pred_coverage": _mean([f.pred_coverage for f in both_ok if f.pred_coverage is not None]),
        "over_extension": _mean([f.over_extension for f in both_ok if f.over_extension is not None]),
        "direction_error": _mean([f.direction_error for f in both_ok if f.direction_error is not None]),
        "per_frame": [f.to_dict() for f in frames],
    }
    return report


# Gate tolerances: a change worse than this (relative to baseline) blocks.
_EPS_DEVIATION = 0.01
_EPS_RATE = 0.02


def gate_guidance(current: dict[str, Any], baseline: dict[str, Any]) -> tuple[bool, list[str]]:
    """Return (passed, reasons). A regression in any safety-relevant metric fails.

    Safety-critical first: false_go and over_extension must NOT increase.
    """
    reasons: list[str] = []

    def worse_up(key: str, eps: float) -> None:
        cur, base = current.get(key), baseline.get(key)
        if cur is None or base is None:
            return
        if cur > base + eps:
            reasons.append(f"{key} regressed: {base:.4f} -> {cur:.4f}")

    def worse_down(key: str, eps: float) -> None:
        cur, base = current.get(key), baseline.get(key)
        if cur is None or base is None:
            return
        if cur < base - eps:
            reasons.append(f"{key} regressed: {base:.4f} -> {cur:.4f}")

    # Safety: predicting a path where truth has none must not increase.
    if current.get("false_go_frames", 0) > baseline.get("false_go_frames", 0):
        reasons.append(
            f"false_go_frames regressed: {baseline.get('false_go_frames', 0)} -> {current.get('false_go_frames', 0)}"
        )
    worse_up("over_extension", _EPS_RATE)
    worse_up("mean_deviation", _EPS_DEVIATION)
    worse_down("hit_rate", _EPS_RATE)

    return (len(reasons) == 0, reasons)
