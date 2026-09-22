"""CPU road-structure completion for a boolean pavement mask.

CamVid (and on-device seg) does not label pavement under cars. Layer B fills
holes that are enclosed by pavement or small enough to close morphologically.
The filled cells are structure, not free space — callers must keep layer A as
the primary guidance mask.

No scipy: dilation is the numpy 8-neighbour OR already used by lane_metrics.
"""
from __future__ import annotations

from collections import deque

import numpy as np

from app.guidance_path import (
    MIN_COVERAGE,
    MIN_POINTS,
    PATH_STATUS_INSUFFICIENT,
    PATH_STATUS_OK,
    GuidanceLine,
    GuidancePath,
    GuidancePoint,
)
from app.lane_metrics import dilate


def erode(mask: np.ndarray, radius: int) -> np.ndarray:
    """Binary erosion by ``radius`` (dilate the complement)."""
    mask = np.asarray(mask, dtype=bool)
    if radius <= 0:
        return mask.copy()
    return np.logical_not(dilate(np.logical_not(mask), radius))


def morphological_close(mask: np.ndarray, radius: int) -> np.ndarray:
    """Close gaps smaller than roughly 2*radius cells without growing the hull.

    Pad so dilation cannot stick to the array border (otherwise erode cannot
    restore the original outer edge).
    """
    mask = np.asarray(mask, dtype=bool)
    if radius <= 0:
        return mask.copy()
    padded = np.pad(mask, radius, mode="constant", constant_values=False)
    closed = erode(dilate(padded, radius), radius)
    return closed[radius:-radius, radius:-radius]


def fill_enclosed_holes(mask: np.ndarray, *, max_area: int) -> np.ndarray:
    """Fill background components that do not touch the border, with an area cap.

    Border-connected background (buildings, sky, canvas unknown) is left empty.
    Components larger than ``max_area`` are also left empty so a courtyard is
    not painted as road.
    """
    mask = np.asarray(mask, dtype=bool)
    height, width = mask.shape
    background = np.logical_not(mask)
    reachable = np.zeros_like(background)
    queue: deque[tuple[int, int]] = deque()

    def _seed(y: int, x: int) -> None:
        if background[y, x] and not reachable[y, x]:
            reachable[y, x] = True
            queue.append((y, x))

    for x in range(width):
        _seed(0, x)
        _seed(height - 1, x)
    for y in range(height):
        _seed(y, 0)
        _seed(y, width - 1)

    while queue:
        y, x = queue.popleft()
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= ny < height and 0 <= nx < width and background[ny, nx] and not reachable[ny, nx]:
                reachable[ny, nx] = True
                queue.append((ny, nx))

    holes = background & np.logical_not(reachable)
    if not holes.any():
        return mask.copy()

    kept = np.zeros_like(holes)
    visited = np.zeros_like(holes)
    for y, x in zip(*np.nonzero(holes), strict=True):
        if visited[y, x]:
            continue
        stack = [(int(y), int(x))]
        visited[y, x] = True
        pixels: list[tuple[int, int]] = []
        while stack:
            cy, cx = stack.pop()
            pixels.append((cy, cx))
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if 0 <= ny < height and 0 <= nx < width and holes[ny, nx] and not visited[ny, nx]:
                    visited[ny, nx] = True
                    stack.append((ny, nx))
        if len(pixels) <= max_area:
            for py, px in pixels:
                kept[py, px] = True
    return mask | kept


def fill_internal_row_gaps(mask: np.ndarray, *, max_gap: int) -> np.ndarray:
    """Fill short False runs that sit between two True runs on the same row.

    This is the driving-corridor prior: a car in the middle of the lane is a
    gap on a constant-Z row, not a border bite into a building.
    """
    mask = np.asarray(mask, dtype=bool)
    if max_gap <= 0:
        return mask.copy()
    out = mask.copy()
    height, width = out.shape
    for y in range(height):
        row = out[y]
        start = None
        runs: list[tuple[int, int]] = []
        for x in range(width):
            if row[x] and start is None:
                start = x
            elif not row[x] and start is not None:
                runs.append((start, x))
                start = None
        if start is not None:
            runs.append((start, width))
        for i in range(len(runs) - 1):
            gap_start = runs[i][1]
            gap_end = runs[i + 1][0]
            if 0 < (gap_end - gap_start) <= max_gap:
                out[y, gap_start:gap_end] = True
    return out


def complete_by_width_prior(
    pavement: np.ndarray,
    *,
    max_extend: int,
    min_width: int,
) -> np.ndarray:
    """Fill each pavement row out to the median corridor width, clipped per row.

    Side bites (a van eating the left edge) are concavities, not enclosed holes.
    Extending at most ``max_extend`` cells past the visible edge keeps buildings
    out; using the median width of wide rows restores a coherent trapezoid.
    """
    pavement = np.asarray(pavement, dtype=bool)
    height, width = pavement.shape
    left = np.full(height, -1, dtype=int)
    right = np.full(height, -1, dtype=int)
    for y in range(height):
        xs = np.flatnonzero(pavement[y])
        if xs.size:
            left[y] = int(xs[0])
            right[y] = int(xs[-1])
    valid = right >= 0
    widths = right[valid] - left[valid] + 1
    wide = widths >= min_width
    if not np.any(wide):
        return pavement.copy()
    typical = int(np.median(widths[wide]))
    half = typical // 2
    out = pavement.copy()
    for y in np.flatnonzero(valid):
        vis_w = int(right[y] - left[y] + 1)
        if vis_w < max(min_width // 2, 4):
            continue
        center = (left[y] + right[y]) / 2.0
        new_l = int(round(center - half))
        new_r = int(round(center + half))
        new_l = max(0, max(new_l, int(left[y]) - max_extend))
        new_r = min(width - 1, min(new_r, int(right[y]) + max_extend))
        if new_r > new_l:
            out[y, new_l : new_r + 1] = True
    return out


def complete_road_structure(
    pavement: np.ndarray,
    *,
    close_radius: int,
    max_gap: int,
    max_hole_area: int,
    max_extend: int = 0,
    min_width: int = 0,
) -> np.ndarray:
    """Layer B: morph-close + row-gap fill + width prior + enclosed-hole fill.

    ``pavement`` should be the role's carriageway (road+lane), not sidewalk.
    """
    pavement = np.asarray(pavement, dtype=bool)
    closed = morphological_close(pavement, close_radius)
    gapped = fill_internal_row_gaps(closed | pavement, max_gap=max_gap)
    if max_extend > 0 and min_width > 0:
        gapped = complete_by_width_prior(gapped, max_extend=max_extend, min_width=min_width)
    return fill_enclosed_holes(gapped, max_area=max_hole_area)


def _cluster_columns(xs: np.ndarray, *, gap: int) -> list[tuple[int, int]]:
    """Inclusive [start, end] clusters of column indices split by ``gap``."""
    if xs.size == 0:
        return []
    clusters: list[tuple[int, int]] = []
    start = prev = int(xs[0])
    for raw in xs[1:]:
        col = int(raw)
        if col - prev > gap:
            clusters.append((start, prev))
            start = col
        prev = col
    clusters.append((start, prev))
    return clusters


def ego_lane_mask(
    free: np.ndarray,
    walls: np.ndarray,
    *,
    ego_col: int,
    lane_width: int,
    wall_gap: int = 8,
) -> np.ndarray:
    """Keep free space in the lane the vehicle currently occupies.

    Occupancy is not a fixed left/right lane: at the nearest row that has
    lane-marking rails, the occupied lane is the interval that contains the
    vehicle heading (``ego_col``). Farther rows track that same interval by
    nearest midpoint so a wider adjacent/oncoming lane cannot steal the line.
    Leading rows that only have a skinny sliver against a rail are skipped so
    the centerline does not start glued to the lane edge.
    """
    free = np.asarray(free, dtype=bool)
    walls = np.asarray(walls, dtype=bool)
    height, width = free.shape
    out = np.zeros_like(free)
    ego_col = int(np.clip(ego_col, 0, width - 1))
    lane_width = max(4, int(lane_width))
    left = ego_col - lane_width // 2
    right = ego_col + lane_width // 2
    occupied_mid: float | None = None
    started = False
    min_start_cells = max(4, lane_width // 6)

    def _intervals(clusters: list[tuple[int, int]]) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        for i in range(len(clusters) - 1):
            rail_l = clusters[i][1]
            rail_r = clusters[i + 1][0]
            if rail_r - rail_l >= 4:
                spans.append((rail_l, rail_r))
        return spans

    for y in range(height - 1, -1, -1):
        clusters = _cluster_columns(np.flatnonzero(walls[y]), gap=wall_gap)
        spans = _intervals(clusters)
        if occupied_mid is None:
            hit = [span for span in spans if span[0] <= ego_col <= span[1]]
            if not hit:
                continue
            left, right = hit[0]
            occupied_mid = 0.5 * (left + right)
        elif spans:
            best = min(spans, key=lambda span: abs(0.5 * (span[0] + span[1]) - occupied_mid))
            best_mid = 0.5 * (best[0] + best[1])
            if abs(best_mid - occupied_mid) <= lane_width:
                left, right = best
                occupied_mid = best_mid
        lo = max(0, int(round(left)))
        hi = min(width, int(round(right)) + 1)
        if hi > lo:
            out[y, lo:hi] = free[y, lo:hi]
        if not started:
            if int(out[y].sum()) <= min_start_cells:
                out[y] = False
            elif bool(out[y].any()):
                started = True
    return out


def _lane_intervals(walls_row: np.ndarray, *, gap: int) -> list[tuple[int, int]]:
    clusters = _cluster_columns(np.flatnonzero(walls_row), gap=gap)
    spans: list[tuple[int, int]] = []
    for i in range(len(clusters) - 1):
        rail_l = clusters[i][1]
        rail_r = clusters[i + 1][0]
        if rail_r - rail_l >= 4:
            spans.append((rail_l, rail_r))
    return spans


def pavement_edge_walls(pavement: np.ndarray) -> np.ndarray:
    """Left/right pavement borders. Lane paint alone is not occupancy on a curb."""
    pavement = np.asarray(pavement, dtype=bool)
    if pavement.ndim != 2 or pavement.shape[1] < 2:
        return np.zeros_like(pavement, dtype=bool)
    left = pavement & np.logical_not(np.roll(pavement, 1, axis=1))
    right = pavement & np.logical_not(np.roll(pavement, -1, axis=1))
    left[:, 0] = pavement[:, 0]
    right[:, -1] = pavement[:, -1]
    return left | right


def lock_occupied_lane_mid(
    walls: np.ndarray,
    *,
    ego_col: int,
    wall_gap: int = 8,
    max_span_ratio: float | None = None,
) -> float | None:
    """Column of the occupied-lane center: heading sits between two markings."""
    walls = np.asarray(walls, dtype=bool)
    height, width = walls.shape
    ego_col = int(np.clip(ego_col, 0, width - 1))
    max_span = width if max_span_ratio is None else max(4, int(round(max_span_ratio * width)))
    for y in range(height - 1, -1, -1):
        hit = [
            span
            for span in _lane_intervals(walls[y], gap=wall_gap)
            if span[0] <= ego_col <= span[1] and (span[1] - span[0]) <= max_span
        ]
        if hit:
            return 0.5 * (hit[0][0] + hit[0][1])
    return None


def complete_lane_markings(
    lane: np.ndarray,
    *,
    associate_dy: int = 90,
    associate_dx: float = 72.0,
    merge_dx: float = 28.0,
    thickness: int = 1,
) -> np.ndarray:
    """Connect dashed CamVid lane paint into continuous rails.

    Dashes of one line are associated near-to-far and interpolated. A driving
    lane between two parallel lines is not filled. The completed rails are
    boundary constraints for occupancy, not a reason to crop the guidance line.
    """
    lane = np.asarray(lane, dtype=bool)
    if lane.ndim != 2 or not lane.any():
        return lane.copy()
    height, width = lane.shape
    src = dilate(lane, 1)
    rails: list[dict[int, tuple[float, float]]] = []
    last: list[tuple[int, float, float]] = []  # y, x, vx per rail

    def _pred(idx: int, y: int) -> float:
        ly, lx, vx = last[idx]
        return lx + vx * float(y - ly)

    for y in range(height - 1, -1, -1):
        clusters = _cluster_columns(np.flatnonzero(src[y]), gap=6)
        if not clusters:
            continue
        used = [False] * len(clusters)
        assigned: dict[int, int] = {}
        order = sorted(range(len(rails)), key=lambda i: -last[i][0])
        for idx in order:
            ly, _lx, _vx = last[idx]
            if ly - y <= 0 or ly - y > associate_dy:
                continue
            pred = _pred(idx, y)
            best_i = -1
            best_d = associate_dx
            for i, (lo, hi) in enumerate(clusters):
                if used[i]:
                    continue
                dist = abs(0.5 * (lo + hi) - pred)
                if dist < best_d:
                    best_d = dist
                    best_i = i
            if best_i < 0:
                continue
            used[best_i] = True
            assigned[idx] = best_i
        for idx, ci in assigned.items():
            lo, hi = clusters[ci]
            pred = _pred(idx, y)
            for i, (a, b) in enumerate(clusters):
                if used[i]:
                    continue
                mid = 0.5 * (a + b)
                if abs(mid - pred) > merge_dx:
                    continue
                other = min(
                    (abs(mid - _pred(j, y)) for j in assigned if j != idx),
                    default=merge_dx + 1,
                )
                if abs(mid - pred) <= other:
                    lo = min(lo, a)
                    hi = max(hi, b)
                    used[i] = True
            rails[idx][y] = (float(lo), float(hi))
            x_mid = 0.5 * (lo + hi)
            ly = last[idx][0]
            dy = float(y - ly)
            vx = last[idx][2]
            if dy != 0:
                vx = (x_mid - last[idx][1]) / dy
            last[idx] = (y, x_mid, vx)
        for i, (lo, hi) in enumerate(clusters):
            if used[i]:
                continue
            rails.append({y: (float(lo), float(hi))})
            last.append((y, 0.5 * (lo + hi), 0.0))

    _stitch_fragmented_rails(rails, dy_max=28, dx_max=48)
    out = lane.copy()
    half = max(0.0, (float(thickness) - 1.0) / 2.0)
    for rail in rails:
        ys = sorted(rail)
        if not ys:
            continue
        _extend_rail_along_heading(rail, height=height, toward_ego=True)
        ys = sorted(rail)
        for i in range(len(ys) - 1):
            y0, y1 = ys[i], ys[i + 1]
            lo0, hi0 = rail[y0]
            lo1, hi1 = rail[y1]
            span = float(max(1, y1 - y0))
            for y in range(y0, y1 + 1):
                t = (y - y0) / span
                lo = lo0 + t * (lo1 - lo0)
                hi = hi0 + t * (hi1 - hi0)
                x = 0.5 * (lo + hi)
                a = int(np.clip(round(x - half), 0, width - 1))
                b = int(np.clip(round(x + half), 0, width - 1))
                out[y, a : b + 1] = True
        for y, (lo, hi) in rail.items():
            x = 0.5 * (lo + hi)
            a = int(np.clip(round(x - half), 0, width - 1))
            b = int(np.clip(round(x + half), 0, width - 1))
            out[y, a : b + 1] = True
    return out


def _stitch_fragmented_rails(
    rails: list[dict[int, tuple[float, float]]],
    *,
    dy_max: int,
    dx_max: float,
) -> None:
    """Join two fragments of the same dashed line; never merge parallel lanes."""
    if len(rails) < 2:
        return
    used = [False] * len(rails)
    for i, a in enumerate(rails):
        if used[i] or not a:
            continue
        while True:
            a_ys = sorted(a)
            a_hi, a_lo_y = a_ys[-1], a_ys[0]
            a_x = 0.5 * (a[a_hi][0] + a[a_hi][1])
            best_j = -1
            best_dy = dy_max + 1
            for j, b in enumerate(rails):
                if j == i or used[j] or not b:
                    continue
                b_ys = sorted(b)
                b_near = b_ys[0]
                dy = b_near - a_hi
                if dy <= 1 or dy > dy_max:
                    continue
                b_x = 0.5 * (b[b_near][0] + b[b_near][1])
                if abs(b_x - a_x) > dx_max:
                    continue
                if dy < best_dy:
                    best_dy = dy
                    best_j = j
            if best_j < 0:
                break
            a.update(rails[best_j])
            used[best_j] = True
            rails[best_j] = {}


def _extend_rail_along_heading(
    rail: dict[int, tuple[float, float]],
    *,
    height: int,
    toward_ego: bool,
    max_rows: int = 220,
) -> None:
    """Grow a rail toward the bumper using the heading at its nearest dash."""
    ys = sorted(rail)
    if len(ys) < 3 or ys[-1] - ys[0] < 24:
        return
    if ys[-1] >= height - 24:
        return
    tail_n = min(12, len(ys))
    tail = ys[-tail_n:]
    yy = np.asarray(tail, dtype=np.float64)
    if float(yy[-1] - yy[0]) < 1:
        return
    lo1, hi1 = rail[ys[-1]]
    vlo = float(np.clip(np.polyfit(yy, np.array([rail[y][0] for y in tail], dtype=np.float64), 1)[0], -1.2, 1.2))
    vhi = float(np.clip(np.polyfit(yy, np.array([rail[y][1] for y in tail], dtype=np.float64), 1)[0], -1.2, 1.2))
    half_w = 0.5 * (hi1 - lo1)
    y = int(ys[-1]) + 1
    steps = 0
    y1 = float(ys[-1])
    while toward_ego and y < height and steps < max_rows:
        lo = lo1 + vlo * (y - y1)
        hi = hi1 + vhi * (y - y1)
        if hi < lo:
            lo, hi = hi, lo
        if hi - lo < 1:
            lo, hi = lo - half_w, hi + half_w
        rail[y] = (float(lo), float(hi))
        y += 1
        steps += 1


def constrain_mids_to_occupied_lane(
    mids: np.ndarray,
    walls: np.ndarray,
    *,
    ego_col: int,
    margin: int = 10,
    wall_gap: int = 8,
    max_span_ratio: float = 0.45,
) -> np.ndarray:
    """Clip guidance to the interior of the occupied lane. Rails are hard bounds."""
    mids = np.asarray(mids, dtype=np.float64)
    tracked = follow_occupied_mids(
        walls, ego_col=ego_col, wall_gap=wall_gap, max_span_ratio=max_span_ratio
    )
    out = mids.copy()
    height = np.asarray(walls, dtype=bool).shape[0]
    for y in range(height):
        if not np.isfinite(out[y]) or not np.isfinite(tracked[y]):
            continue
        spans = _lane_intervals(walls[y], gap=wall_gap)
        hit = [span for span in spans if span[0] <= tracked[y] <= span[1]]
        if not hit:
            out[y] = float(tracked[y])
            continue
        left, right = hit[0]
        lo = float(left) + float(margin)
        hi = float(right) - float(margin)
        if hi <= lo:
            out[y] = 0.5 * (left + right)
        else:
            out[y] = float(np.clip(out[y], lo, hi))
    return out


def follow_occupied_mids(
    walls: np.ndarray,
    *,
    ego_col: int,
    wall_gap: int = 8,
    max_span_ratio: float = 0.45,
) -> np.ndarray:
    mids, _lefts, _rights = follow_occupied_bounds(
        walls, ego_col=ego_col, wall_gap=wall_gap, max_span_ratio=max_span_ratio
    )
    return mids


def follow_occupied_bounds(
    walls: np.ndarray,
    *,
    ego_col: int,
    wall_gap: int = 8,
    max_span_ratio: float = 0.45,
    min_span_ratio: float = 0.08,
    max_lane_ratio: float = 0.22,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Occupied-lane midpoint and left/right rails.

    First lock must be a plausible driving-lane width, not dash-to-opposite-curb.
    After lock, track both toward the horizon and back to the bumper. If the
    two-rail interval is gone, rebuild from the nearer rail plus home width.
    """
    walls = np.asarray(walls, dtype=bool)
    height, width = walls.shape
    ego_col = int(np.clip(ego_col, 0, width - 1))
    max_span = max(4, int(round(max_span_ratio * width)))
    min_span0 = max(8, int(round(min_span_ratio * width)))
    max_lane0 = max(40, int(round(max_lane_ratio * width)))
    mids = np.full(height, np.nan, dtype=np.float64)
    lefts = np.full(height, np.nan, dtype=np.float64)
    rights = np.full(height, np.nan, dtype=np.float64)

    lock: tuple[int, float, float, float, float] | None = None
    for y in range(height - 1, -1, -1):
        hit = []
        for span in _lane_intervals(walls[y], gap=wall_gap):
            if not (span[0] <= ego_col <= span[1]):
                continue
            span_w = span[1] - span[0]
            if not (min_span0 <= span_w <= min(max_span, max_lane0)):
                continue
            interior = min(ego_col - span[0], span[1] - ego_col)
            if interior < max(0.015 * width, 0.12 * span_w):
                continue
            hit.append(span)
        if not hit:
            continue
        best = max(hit, key=lambda span: min(ego_col - span[0], span[1] - ego_col))
        home_width = float(max(best[1] - best[0], 8.0))
        lock = (y, float(best[0]), float(best[1]), 0.5 * (best[0] + best[1]), home_width)
        break
    if lock is None:
        return mids, lefts, rights

    lock_y, prev_left, prev_right, occupied_mid, home_width = lock

    def advance(y: int, prev_left: float, prev_right: float, occupied_mid: float) -> tuple[float, float, float]:
        spans = _lane_intervals(walls[y], gap=wall_gap)
        overlap = []
        min_overlap = 0.35 * home_width
        min_span = 0.5 * home_width
        max_span_now = 1.6 * home_width
        for span in spans:
            span_w = span[1] - span[0]
            if span_w < min_span or span_w > max_span_now:
                continue
            overlap_px = min(span[1], prev_right) - max(span[0], prev_left)
            if overlap_px >= min_overlap:
                overlap.append(span)
        if overlap:
            best = min(overlap, key=lambda span: abs(0.5 * (span[0] + span[1]) - occupied_mid))
            left, right = float(best[0]), float(best[1])
            return left, right, 0.5 * (left + right)
        xs = np.flatnonzero(walls[y])
        if xs.size == 0:
            return prev_left, prev_right, occupied_mid
        anchor = occupied_mid
        left_cands = xs[xs <= int(round(anchor))]
        right_cands = xs[xs >= int(round(anchor))]
        left_wall = int(left_cands[-1]) if left_cands.size else None
        right_wall = int(right_cands[0]) if right_cands.size else None
        if left_wall is not None and (
            right_wall is None or (anchor - left_wall) <= (right_wall - anchor)
        ):
            left = float(left_wall)
            right = left + home_width
        elif right_wall is not None:
            right = float(right_wall)
            left = right - home_width
        else:
            return prev_left, prev_right, occupied_mid
        new_mid = 0.5 * (left + right)
        if abs(new_mid - occupied_mid) > 0.5 * home_width:
            return prev_left, prev_right, occupied_mid
        return left, right, new_mid

    def fill_row(y: int, left: float, right: float, mid: float) -> None:
        mids[y] = mid
        lefts[y] = left
        rights[y] = right

    fill_row(lock_y, prev_left, prev_right, occupied_mid)
    far_left, far_right, far_mid = prev_left, prev_right, occupied_mid
    for y in range(lock_y - 1, -1, -1):
        far_left, far_right, far_mid = advance(y, far_left, far_right, far_mid)
        fill_row(y, far_left, far_right, far_mid)
    near_left, near_right, near_mid = prev_left, prev_right, occupied_mid
    for y in range(lock_y + 1, height):
        near_left, near_right, near_mid = advance(y, near_left, near_right, near_mid)
        fill_row(y, near_left, near_right, near_mid)
    return mids, lefts, rights


def _strip_from_anchor(
    pavement_row: np.ndarray,
    walls_row: np.ndarray,
    anchor: int,
) -> tuple[float, float] | None:
    """Pavement run containing ``anchor``, stopped by walls (paint or curb)."""
    pavement_row = np.asarray(pavement_row, dtype=bool)
    walls_row = np.asarray(walls_row, dtype=bool)
    n = int(pavement_row.size)
    if n == 0:
        return None
    a = int(np.clip(anchor, 0, n - 1))
    if walls_row[a]:
        if a + 1 < n and pavement_row[a + 1] and not walls_row[a + 1]:
            a = a + 1
        elif a - 1 >= 0 and pavement_row[a - 1] and not walls_row[a - 1]:
            a = a - 1
    if not pavement_row[a]:
        xs = np.flatnonzero(pavement_row)
        if xs.size == 0:
            return None
        a = int(xs[int(np.argmin(np.abs(xs.astype(np.float64) - float(a))))])
    left = a
    while left > 0 and pavement_row[left - 1] and not walls_row[left - 1]:
        left -= 1
    right = a
    while right < n - 1 and pavement_row[right + 1] and not walls_row[right + 1]:
        right += 1
    return float(left), float(right)


def follow_occupied_region(
    pavement: np.ndarray,
    walls: np.ndarray,
    *,
    ego_col: int,
    max_span_ratio: float = 0.45,
    max_lane_ratio: float = 0.28,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Occupied asphalt strip: heading sits in pavement, walls (paint/curb) clip it.

    The centerline is the strip midpoint, not the average of two painted rails.
    Missing paint is fine when a curb still closes the band. A vanished divider
    that would swallow the adjacent lane is clamped with the locked home width.
    """
    pavement = np.asarray(pavement, dtype=bool)
    walls = np.asarray(walls, dtype=bool)
    height, width = pavement.shape
    ego_col = int(np.clip(ego_col, 0, width - 1))
    max_span = max(4, int(round(max_span_ratio * width)))
    max_lane0 = max(40, int(round(max_lane_ratio * width)))
    mids = np.full(height, np.nan, dtype=np.float64)
    lefts = np.full(height, np.nan, dtype=np.float64)
    rights = np.full(height, np.nan, dtype=np.float64)

    lock: tuple[int, float, float, float, float] | None = None
    for y in range(height - 1, -1, -1):
        strip = _strip_from_anchor(pavement[y], walls[y], ego_col)
        if strip is None:
            continue
        left, right = strip
        span_w = right - left
        if span_w < 8 or span_w > min(max_span, max_lane0):
            continue
        interior = min(ego_col - left, right - ego_col)
        if interior < max(0.015 * width, 0.12 * span_w):
            continue
        home_width = float(max(span_w, 8.0))
        lock = (y, left, right, 0.5 * (left + right), home_width)
        break
    if lock is None:
        return mids, lefts, rights

    lock_y, prev_left, prev_right, occupied_mid, home_width = lock

    def advance(y: int, prev_left: float, prev_right: float, occupied_mid: float) -> tuple[float, float, float]:
        strip = _strip_from_anchor(pavement[y], walls[y], int(round(occupied_mid)))
        if strip is not None:
            left, right = strip
            span_w = right - left
            if 0.5 * home_width <= span_w <= 1.6 * home_width:
                return left, right, 0.5 * (left + right)
        xs = np.flatnonzero(walls[y])
        anchor = occupied_mid
        left_wall = None
        right_wall = None
        if xs.size:
            left_cands = xs[xs <= int(round(anchor))]
            right_cands = xs[xs >= int(round(anchor))]
            if left_cands.size:
                left_wall = float(left_cands[-1])
            if right_cands.size:
                right_wall = float(right_cands[0])
        if right_wall is not None and (left_wall is None or (right_wall - anchor) <= (anchor - left_wall)):
            right = right_wall
            left = right - home_width
        elif left_wall is not None:
            left = left_wall
            right = left + home_width
        else:
            return prev_left, prev_right, occupied_mid
        pav = np.flatnonzero(pavement[y])
        if pav.size:
            left = float(np.clip(left, pav[0], pav[-1]))
            right = float(np.clip(right, pav[0], pav[-1]))
        if right <= left:
            return prev_left, prev_right, occupied_mid
        return left, right, 0.5 * (left + right)

    def fill_row(y: int, left: float, right: float, mid: float) -> None:
        mids[y] = mid
        lefts[y] = left
        rights[y] = right

    fill_row(lock_y, prev_left, prev_right, occupied_mid)
    far_left, far_right, far_mid = prev_left, prev_right, occupied_mid
    for y in range(lock_y - 1, -1, -1):
        far_left, far_right, far_mid = advance(y, far_left, far_right, far_mid)
        fill_row(y, far_left, far_right, far_mid)
    near_left, near_right, near_mid = prev_left, prev_right, occupied_mid
    for y in range(lock_y + 1, height):
        near_left, near_right, near_mid = advance(y, near_left, near_right, near_mid)
        fill_row(y, near_left, near_right, near_mid)
    return mids, lefts, rights


def extend_mids_toward_ego(
    mids: np.ndarray,
    *,
    ego_col: int,
    hood_rows: int = 0,
    pavement: np.ndarray | None = None,
) -> np.ndarray:
    """Fill from the first lock back toward the camera so the line does not start mid-road.

    Occupancy still waits for a lane-width split (dashes). Near the bumper those
    dashes are often missing, so we hold the locked lane center back to a small
    hood margin instead of leaving a gap or S-bending toward image center.
    """
    del ego_col  # occupancy is the locked lane, not optical-axis heading
    mids = np.asarray(mids, dtype=np.float64)
    out = mids.copy()
    height = out.shape[0]
    finite = np.flatnonzero(np.isfinite(out))
    if finite.size == 0 or height < 2:
        return out
    lock_y = int(finite.max())
    start_y = height - 1 - max(0, int(hood_rows))
    start_y = max(0, min(start_y, height - 1))
    if start_y <= lock_y:
        return out
    lock_u = float(out[lock_y])
    width = None if pavement is None else pavement.shape[1]
    for y in range(lock_y + 1, start_y + 1):
        u = lock_u
        if pavement is not None and width is not None:
            ui = int(np.clip(round(u), 0, width - 1))
            if not bool(pavement[y, ui]):
                xs = np.flatnonzero(pavement[y])
                if xs.size == 0:
                    continue
                u = float(xs[int(np.argmin(np.abs(xs.astype(np.float64) - u)))])
        out[y] = u
    return out


def extend_bounds_toward_ego(
    mids: np.ndarray,
    lefts: np.ndarray,
    rights: np.ndarray,
    *,
    hood_rows: int = 0,
    pavement: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Hold the locked corridor back to the hood, matching ``extend_mids_toward_ego``."""
    mids_out = extend_mids_toward_ego(
        mids, ego_col=0, hood_rows=hood_rows, pavement=pavement
    )
    lefts_out = np.asarray(lefts, dtype=np.float64).copy()
    rights_out = np.asarray(rights, dtype=np.float64).copy()
    finite = np.flatnonzero(np.isfinite(np.asarray(mids, dtype=np.float64)))
    if finite.size == 0:
        return mids_out, lefts_out, rights_out
    lock_y = int(finite.max())
    height = mids_out.shape[0]
    start_y = height - 1 - max(0, int(hood_rows))
    start_y = max(0, min(start_y, height - 1))
    lock_l = float(lefts_out[lock_y]) if np.isfinite(lefts_out[lock_y]) else float("nan")
    lock_r = float(rights_out[lock_y]) if np.isfinite(rights_out[lock_y]) else float("nan")
    for y in range(lock_y + 1, start_y + 1):
        if np.isfinite(mids_out[y]):
            if np.isfinite(lock_l):
                lefts_out[y] = lock_l
            if np.isfinite(lock_r):
                rights_out[y] = lock_r
    return mids_out, lefts_out, rights_out


def _finite_runs(values: np.ndarray) -> list[slice]:
    finite = np.isfinite(values)
    start: int | None = None
    runs: list[slice] = []
    for i, ok in enumerate(finite.tolist()):
        if ok and start is None:
            start = i
        elif (not ok) and start is not None:
            runs.append(slice(start, i))
            start = None
    if start is not None:
        runs.append(slice(start, finite.size))
    return runs


def _second_diff_matrix(n: int) -> np.ndarray:
    d2 = np.zeros((n - 2, n), dtype=np.float64)
    for i in range(n - 2):
        d2[i, i] = 1.0
        d2[i, i + 1] = -2.0
        d2[i, i + 2] = 1.0
    return d2


def _solve_smooth_d2(target: np.ndarray, weights: np.ndarray, lam: float) -> np.ndarray:
    """Discrete smoothing spline: (W + λ D2ᵀ D2) u = W t."""
    n = int(target.size)
    if n < 3 or lam <= 0:
        return np.asarray(target, dtype=np.float64).copy()
    d2 = _second_diff_matrix(n)
    a = np.diag(np.asarray(weights, dtype=np.float64)) + float(lam) * (d2.T @ d2)
    b = np.asarray(weights, dtype=np.float64) * np.asarray(target, dtype=np.float64)
    try:
        return np.linalg.solve(a, b)
    except np.linalg.LinAlgError:
        return np.asarray(target, dtype=np.float64).copy()


def fit_guidance_g2(
    mids: np.ndarray,
    lefts: np.ndarray,
    rights: np.ndarray,
    *,
    lam: float = 80000.0,
    margin: float = 10.0,
    max_iter: int = 1,
) -> np.ndarray:
    """Fit a G2 centerline as the midpoint of smoothing-splined occupied rails.

    Do not clip after a box filter: that is what produced the 10°+ heading jump.
    ``mids`` only marks the finite run. ``margin`` keeps a small interior gap
    after the rails are smoothed, without per-row yank.
    """
    del max_iter
    mids = np.asarray(mids, dtype=np.float64)
    lefts = np.asarray(lefts, dtype=np.float64)
    rights = np.asarray(rights, dtype=np.float64)
    out = mids.copy()
    for run in _finite_runs(out):
        n = run.stop - run.start
        if n < 3:
            continue
        left = lefts[run].copy()
        right = rights[run].copy()
        mid = out[run].copy()
        half = np.where(np.isfinite(left) & np.isfinite(right), 0.5 * (right - left), np.nan)
        half = np.where(np.isfinite(half) & (half > 1.0), half, 8.0)
        left = np.where(np.isfinite(left), left, mid - half)
        right = np.where(np.isfinite(right), right, mid + half)
        weights = np.ones(n, dtype=np.float64)
        left_s = _solve_smooth_d2(left, weights, lam)
        right_s = _solve_smooth_d2(right, weights, lam)
        swap = left_s > right_s
        left_s[swap], right_s[swap] = right_s[swap], left_s[swap]
        fitted = 0.5 * (left_s + right_s)
        out[run] = fitted
    return out


def heading_curvature_stats(mids: np.ndarray) -> dict[str, float]:
    """max |Δψ| (deg), max |κ|, max |κ′| on the longest finite run. Δv = 1 row."""
    mids = np.asarray(mids, dtype=np.float64)
    runs = _finite_runs(mids)
    empty = {
        "n": 0.0,
        "max_dpsi_deg": float("nan"),
        "max_abs_kappa": float("nan"),
        "max_abs_dkappa": float("nan"),
    }
    if not runs:
        return empty
    run = max(runs, key=lambda item: item.stop - item.start)
    u = mids[run]
    n = int(u.size)
    if n < 3:
        return {"n": float(n), "max_dpsi_deg": 0.0, "max_abs_kappa": 0.0, "max_abs_dkappa": 0.0}
    du = np.diff(u)
    dpsi = np.diff(np.arctan(du))
    u2 = np.diff(u, n=2)
    u1_mid = 0.5 * (du[:-1] + du[1:])
    kappa = u2 / np.power(1.0 + u1_mid * u1_mid, 1.5)
    dkappa = np.diff(kappa)
    return {
        "n": float(n),
        "max_dpsi_deg": float(np.degrees(np.max(np.abs(dpsi)))) if dpsi.size else 0.0,
        "max_abs_kappa": float(np.max(np.abs(kappa))) if kappa.size else 0.0,
        "max_abs_dkappa": float(np.max(np.abs(dkappa))) if dkappa.size else 0.0,
    }


def smooth_mids(
    mids: np.ndarray,
    *,
    window: int = 31,
    passes: int = 3,
    max_du: float = 2.2,
) -> np.ndarray:
    """Low-pass the occupied mid so heading/curvature is not a per-row polyline kink.

    Box filter repeated ≈ Gaussian, per contiguous finite run. Then clamp |Δu|
    so a fragmented wall cannot yank heading harder than a gentle lane curve.
    """
    mids = np.asarray(mids, dtype=np.float64)
    out = mids.copy()
    finite = np.isfinite(out)
    start: int | None = None
    runs: list[slice] = []
    for i, ok in enumerate(finite.tolist()):
        if ok and start is None:
            start = i
        elif (not ok) and start is not None:
            runs.append(slice(start, i))
            start = None
    if start is not None:
        runs.append(slice(start, finite.size))
    for run in runs:
        vals = out[run].copy()
        if vals.size < 5:
            continue
        odd = window if window % 2 else window - 1
        odd = max(5, min(odd, (vals.size | 1)))
        if odd % 2 == 0:
            odd -= 1
        kernel = np.ones(odd, dtype=np.float64) / float(odd)
        pad = odd // 2
        for _ in range(max(1, int(passes))):
            padded = np.pad(vals, pad, mode="edge")
            vals = np.convolve(padded, kernel, mode="valid")
        if max_du > 0:
            for i in range(1, vals.size):
                du = vals[i] - vals[i - 1]
                if du > max_du:
                    vals[i] = vals[i - 1] + max_du
                elif du < -max_du:
                    vals[i] = vals[i - 1] - max_du
        out[run] = vals
    return out


def straight_occupied_centerline(
    free: np.ndarray,
    walls: np.ndarray,
    *,
    ego_col: int,
    samples: int = 24,
    horizon: float = 0.62,
    wall_gap: int = 8,
    obstacle: np.ndarray | None = None,
    source: str = "occupied_lane_straight",
) -> GuidancePath:
    """Straight line down the occupied-lane center; stop when that center is blocked.

    Product target for a driver preview: not the per-row centroid of leftover
    free pixels (that zigzags when a person/cyclist bites one side).
    If ``obstacle`` is given, the line may cross pavement holes and only stops
    when the lane center hits an obstacle footprint.
    """
    free = np.asarray(free, dtype=bool)
    height, width = free.shape
    mid = lock_occupied_lane_mid(walls, ego_col=ego_col, wall_gap=wall_gap, max_span_ratio=0.45)
    if mid is None or height < 2 or width < 2:
        return GuidancePath(status=PATH_STATUS_INSUFFICIENT, coverage=0.0, source=source)
    mid_col = int(np.clip(round(mid), 0, width - 1))
    top_row = int(round(height * (1.0 - min(max(horizon, 0.05), 1.0))))
    top_row = max(0, min(top_row, height - 2))
    x_norm = mid / width
    if obstacle is None:
        blocked = np.logical_not(free)
    else:
        blocked = np.asarray(obstacle, dtype=bool)
        if blocked.shape != free.shape:
            raise ValueError("obstacle mask must match free mask shape")
    used_rows: list[int] = []
    started = False
    for img_row in range(height - 1, top_row - 1, -1):
        hit = bool(blocked[img_row, max(0, mid_col - 1) : min(width, mid_col + 2)].any())
        if hit:
            if not started:
                continue
            break
        started = True
        used_rows.append(img_row)
    if len(used_rows) > samples:
        pick = np.linspace(0, len(used_rows) - 1, num=samples).round().astype(int)
        used_rows = [used_rows[int(i)] for i in pick]
    points = [
        GuidancePoint(x=x_norm, y=1.0 - (float(img_row) + 0.5) / height, half_width=0.05)
        for img_row in used_rows
    ]
    coverage = len(points) / float(max(2, samples))
    if len(points) < MIN_POINTS or coverage < MIN_COVERAGE:
        return GuidancePath(
            status=PATH_STATUS_INSUFFICIENT, coverage=round(coverage, 5), source=source
        )
    line = GuidanceLine(points=points, confidence=round(min(1.0, coverage), 5), kind="primary")
    return GuidancePath(
        status=PATH_STATUS_OK, coverage=round(coverage, 5), lines=[line], source=source
    )
