"""Post-decode polish for UFLDv2 row-anchor ego lanes (indices 1 and 2).

Mirrors ``UFLDv2LanePolisher`` in LocalLaneSegmentation.swift. Raw pred2coords
stays unchanged; call this after decode on normalized (x, y) in 0...1.
"""
from __future__ import annotations

import numpy as np

MIN_POINTS = 8
MAX_SPIKE_DEV = 0.035
MAX_IDENTITY_JUMP = 0.06
SMOOTH_LAMBDA = 800.0
ROW_EGO_INDICES = (1, 2)


def _reject_spikes(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if len(points) < 3:
        return list(points)
    kept: list[tuple[float, float]] = []
    last = len(points) - 1
    for i, point in enumerate(points):
        if i == 0 or i == last:
            kept.append(point)
            continue
        pred = 0.5 * (points[i - 1][0] + points[i + 1][0])
        if abs(point[0] - pred) <= MAX_SPIKE_DEV:
            kept.append(point)
    return kept


def _longest_identity_fragment(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Keep one identity. A |Δx| > 0.06 jump is a different line, not a dent."""
    if len(points) < 2:
        return list(points)
    fragments: list[list[tuple[float, float]]] = [[points[0]]]
    for prev, point in zip(points, points[1:]):
        if abs(point[0] - prev[0]) > MAX_IDENTITY_JUMP:
            fragments.append([point])
        else:
            fragments[-1].append(point)
    return max(fragments, key=len)


def _smooth_x(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    n = len(points)
    if n < MIN_POINTS or SMOOTH_LAMBDA <= 0:
        return list(points)
    x = np.array([p[0] for p in points], dtype=np.float64)
    y = np.array([p[1] for p in points], dtype=np.float64)
    a = np.eye(n)
    for i in range(n - 2):
        coeffs = ((i, 1.0), (i + 1, -2.0), (i + 2, 1.0))
        for j, vj in coeffs:
            for k, vk in coeffs:
                a[j, k] += SMOOTH_LAMBDA * vj * vk
    xs = np.linalg.solve(a, x)
    return [(float(min(max(xi, 0.0), 1.0)), float(yi)) for xi, yi in zip(xs, y)]


def polish_lane_points(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Normalize-space polish. ``n < 8`` is returned unchanged."""
    if len(points) < MIN_POINTS:
        return list(points)
    ordered = sorted(points, key=lambda p: p[1])
    fragment = _longest_identity_fragment(_reject_spikes(ordered))
    if len(fragment) < MIN_POINTS:
        return fragment if len(fragment) >= 2 else list(points)
    smoothed = _smooth_x(fragment)
    return smoothed if len(smoothed) >= 2 else list(points)


def polish_row_ego_lane(
    points: list[tuple[float, float]],
    *,
    lane_index: int | None,
    source: str,
) -> list[tuple[float, float]]:
    if source != "rowAnchor" or lane_index not in ROW_EGO_INDICES:
        return list(points)
    polished = polish_lane_points(points)
    return polished if len(polished) >= 2 else list(points)
