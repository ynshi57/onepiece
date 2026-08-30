"""Unit tests for the guidance-path schema + centerline generator."""

from __future__ import annotations

import numpy as np
import pytest

from app.guidance_path import (
    GuidancePath,
    GuidancePathError,
    PATH_STATUS_INSUFFICIENT,
    PATH_STATUS_OK,
    centerline_from_mask,
)


def _corridor_mask(height=64, width=64, cx=0.5, halfw=0.15):
    """A vertical traversable corridor centered at cx with half-width halfw."""
    mask = np.zeros((height, width), dtype=bool)
    x0 = int((cx - halfw) * width)
    x1 = int((cx + halfw) * width)
    mask[:, x0:x1] = True
    return mask


def test_centerline_traces_straight_corridor():
    path = centerline_from_mask(_corridor_mask(cx=0.5), source="test")
    assert path.status == PATH_STATUS_OK
    assert path.primary is not None
    xs = [p.x for p in path.primary.points]
    # Centered corridor -> centers near 0.5.
    assert all(abs(x - 0.5) < 0.05 for x in xs)
    # Points ordered from feet (small y) forward (large y).
    ys = [p.y for p in path.primary.points]
    assert ys == sorted(ys)


def test_centerline_follows_offset_corridor():
    path = centerline_from_mask(_corridor_mask(cx=0.75), source="test")
    assert path.status == PATH_STATUS_OK
    xs = [p.x for p in path.primary.points]
    assert all(abs(x - 0.75) < 0.06 for x in xs)


def test_no_free_space_is_insufficient_not_fabricated():
    mask = np.zeros((64, 64), dtype=bool)  # nothing traversable
    path = centerline_from_mask(mask)
    assert path.status == PATH_STATUS_INSUFFICIENT
    assert path.lines == []


def test_skips_blocked_bottom_rows_like_a_car_hood():
    """A blocked immediate foreground (driving-frame hood) at the bottom must NOT
    kill an otherwise clear path above it: skip leading blocked rows, then trace."""
    h, w = 64, 64
    mask = np.zeros((h, w), dtype=bool)
    mask[0:43, 26:38] = True  # road in the upper part; rows 43..63 (bottom) blocked
    path = centerline_from_mask(mask)
    assert path.status == PATH_STATUS_OK
    ys = [p.y for p in path.primary.points]
    # Bottom (hood) rows were skipped, so the nearest traced point sits above it.
    assert min(ys) > 0.3


def test_interior_gap_stops_line_and_is_never_bridged():
    """An obstacle ahead (interior gap) must break the line — we must NOT bridge
    across it to the road beyond, which would draw a path through the obstacle."""
    h, w = 64, 64
    mask = np.zeros((h, w), dtype=bool)
    mask[44:64, 26:38] = True  # near road (bottom)
    mask[10:34, 26:38] = True  # far road (top), separated by a blocked gap rows 34..43
    path = centerline_from_mask(mask)
    assert path.status == PATH_STATUS_OK
    ys = [p.y for p in path.primary.points]
    # Every traced point stays in the NEAR band; none jumped over the gap.
    assert all(y < 0.45 for y in ys)


def _mean_primary_x(path: GuidancePath) -> float:
    assert path.primary is not None
    return sum(p.x for p in path.primary.points) / len(path.primary.points)


def _bimodal_mask(*, bottom_left_end: int, height=64, width=64) -> np.ndarray:
    """Two valid corridors split by an obstacle, with the LEFT component larger.

    ``bottom_left_end`` changes only the first sampled bottom row by a couple of
    columns. The old greedy nearest-run algorithm flips the whole line because it
    seeds each frame from x=0.5; the stable component algorithm should still keep
    the larger left corridor for both masks.
    """
    mask = np.zeros((height, width), dtype=bool)
    mask[:, 5:33] = True      # larger left corridor
    mask[:, 37:52] = True     # smaller right corridor
    mask[:, 29:39] = False    # obstacle separator
    mask[height - 1, 5:bottom_left_end] = True
    return mask


def test_centerline_prefers_largest_reachable_corridor_over_tiny_seed_perturbation():
    stable_left = centerline_from_mask(_bimodal_mask(bottom_left_end=32), source="a")
    perturbed = centerline_from_mask(_bimodal_mask(bottom_left_end=31), source="b")

    assert stable_left.status == PATH_STATUS_OK
    assert perturbed.status == PATH_STATUS_OK
    # Both should choose the larger left corridor; a tiny bottom-row perturbation
    # must not flip the whole path to the smaller right corridor.
    assert _mean_primary_x(stable_left) < 0.45
    assert _mean_primary_x(perturbed) < 0.45
    assert abs(_mean_primary_x(stable_left) - _mean_primary_x(perturbed)) < 0.08


def _sign_changes(xs: list[float], eps: float = 0.02) -> int:
    """Count direction reversals in a sequence (ignore tiny wobble)."""
    changes = 0
    prev_sign = 0
    for a, b in zip(xs, xs[1:]):
        d = b - a
        if abs(d) < eps:
            continue
        sign = 1 if d > 0 else -1
        if prev_sign != 0 and sign != prev_sign:
            changes += 1
        prev_sign = sign
    return changes


def test_centerline_does_not_zigzag_when_wide_base_splits_upward():
    """A merged wide base that splits into two comparable blocks upward must NOT
    produce a line that folds left-right each row. Within-frame continuity should
    keep it single-directional (regression guard for the 'widest run per row' fix
    that dropped continuity and made the GT line zigzag)."""
    h, w = 48, 64
    mask = np.zeros((h, w), dtype=bool)
    # Bottom anchor rows biased left (like a sidewalk on the left of frame).
    mask[44:48, 4:22] = True
    # A wide merged base a few rows up (road + sidewalk connected).
    mask[40:44, 4:60] = True
    # Upper rows split into a left and a right block, alternating which is wider
    # by a couple of columns -> old 'widest run' logic flips every row.
    for row in range(24, 40):
        if row % 2 == 0:
            mask[row, 4:22] = True   # left wider
            mask[row, 42:58] = True
        else:
            mask[row, 6:22] = True
            mask[row, 40:58] = True  # right wider
    path = centerline_from_mask(mask, source="zig")
    assert path.status == PATH_STATUS_OK
    xs = [p.x for p in path.primary.points]
    # The old logic reverses direction many times; a continuous trace reverses at
    # most once (the legitimate base->one-side transition).
    assert _sign_changes(xs) <= 1, xs


def test_roundtrip_serialization():
    path = centerline_from_mask(_corridor_mask(), source="rt")
    restored = GuidancePath.from_dict(path.to_dict())
    assert restored.status == path.status
    assert len(restored.primary.points) == len(path.primary.points)
    assert restored.primary.points[0].to_dict() == path.primary.points[0].to_dict()


def test_from_dict_rejects_out_of_range():
    with pytest.raises(GuidancePathError):
        GuidancePath.from_dict({
            "status": "ok",
            "coverage": 0.9,
            "lines": [{"kind": "primary", "confidence": 0.5,
                       "points": [{"x": 1.4, "y": 0.1, "half_width": 0.1}]}],
        })


def test_from_dict_rejects_unordered_points():
    with pytest.raises(GuidancePathError):
        GuidancePath.from_dict({
            "status": "ok",
            "coverage": 0.9,
            "lines": [{"kind": "primary", "confidence": 0.5, "points": [
                {"x": 0.5, "y": 0.8, "half_width": 0.1},
                {"x": 0.5, "y": 0.2, "half_width": 0.1},
            ]}],
        })
