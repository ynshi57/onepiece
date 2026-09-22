"""Row-anchor occupied rails: paint polylines + curb polylines, blue clamped inside."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from app.lane_rails import (
    clip_obstacle_far,
    curb_rails,
    densify_rail,
    guidance_between_rails,
    occupied_lane_rails,
    polyline_to_row_xs,
    rails_from_lane_mask,
    select_occupied_pair,
    split_sharp_turns,
    track_occupied_pair,
    trim_border_glued,
    try_ufld_paint_rails,
    drop_identity_jumps,
)


def _dashed(h: int, w: int, x: int, *, y0: int = 8, y1: int | None = None, on: int = 4, off: int = 6, thick: int = 3) -> np.ndarray:
    lane = np.zeros((h, w), dtype=bool)
    y = y0
    stop = h - 4 if y1 is None else y1
    while y < stop:
        lane[y : y + on, x : x + thick] = True
        y += on + off
    return lane


def test_row_anchors_cross_dashed_gaps():
    h, w = 80, 120
    lane = _dashed(h, w, 50, y0=10, y1=70)
    rails = rails_from_lane_mask(lane, min_rows=6, associate_dy=20, associate_dx=12)
    assert len(rails) >= 1
    rail = rails[0]
    gap_rows = [y for y in range(10, 70) if not lane[y].any()]
    assert gap_rows
    filled = sum(1 for y in gap_rows if np.isfinite(rail[y]))
    assert filled >= 8
    xs = rail[np.isfinite(rail)]
    assert abs(float(np.median(xs)) - 51.0) < 3.0


def test_parallel_rails_do_not_merge():
    h, w = 60, 160
    lane = _dashed(h, w, 40, y0=6, y1=54) | _dashed(h, w, 90, y0=6, y1=54)
    rails = rails_from_lane_mask(lane, min_rows=6, associate_dx=16, associate_dy=20)
    assert len(rails) >= 2
    meds = sorted(float(np.nanmedian(r)) for r in rails[:2])
    assert meds[1] - meds[0] > 30


def test_curb_is_polyline_not_scatter():
    h, w = 40, 100
    pavement = np.zeros((h, w), dtype=bool)
    sidewalk = np.zeros((h, w), dtype=bool)
    pavement[:, 20:70] = True
    sidewalk[:, 70:90] = True
    left, right = curb_rails(pavement, sidewalk, hood_rows=2)
    finite_y = np.flatnonzero(np.isfinite(right))
    assert finite_y.size >= 30
    # One x per row, not an edge spray: values hug the interface column.
    assert float(np.nanstd(right[finite_y])) < 1.5
    assert abs(float(np.nanmedian(right)) - 69.0) < 2.0
    assert abs(float(np.nanmedian(left)) - 20.0) < 2.0


def test_occupied_locks_between_dash_and_right_curb():
    h, w = 80, 160
    pavement = np.zeros((h, w), dtype=bool)
    sidewalk = np.zeros((h, w), dtype=bool)
    pavement[:, 20:110] = True
    sidewalk[:, 110:140] = True
    lane = _dashed(h, w, 50, y0=8, y1=72)
    occupied = occupied_lane_rails(
        lane,
        pavement,
        sidewalk=sidewalk,
        ego_col=75,
        ufld_model=False,
    )
    assert occupied.points
    assert occupied.paint_source == "gt_row_anchor"
    assert occupied.left_kind == "paint"
    assert occupied.right_kind == "curb"
    ys = [int(v) for _u, v in occupied.points]
    sample = ys[len(ys) // 2]
    assert abs(float(occupied.left[sample]) - 51.0) < 8.0
    assert abs(float(occupied.right[sample]) - 109.0) < 6.0
    # Must not lock dash-to-far-left-curb (whole carriageway).
    span = float(occupied.right[sample] - occupied.left[sample])
    assert 35.0 < span < 90.0


def test_guidance_points_hard_clamped_between_rails():
    h, w = 50, 80
    left = np.full(h, 20.0)
    right = np.full(h, 50.0)
    left[:4] = np.nan
    right[:4] = np.nan
    pavement = np.zeros((h, w), dtype=bool)
    pavement[:, 15:55] = True
    mids, points = guidance_between_rails(left, right, pavement=pavement, margin=1.0)
    assert points
    for u, v in points:
        y = int(v)
        assert left[y] <= u <= right[y]
    finite = np.isfinite(mids)
    assert np.all(mids[finite] >= 20.0)
    assert np.all(mids[finite] <= 50.0)


def test_mask_source_twinlite_does_not_fall_back_to_gt_name():
    h, w = 40, 80
    pavement = np.ones((h, w), dtype=bool)
    lane = _dashed(h, w, 30, y0=4, y1=36, on=3, off=4, thick=2)
    occupied = occupied_lane_rails(
        lane,
        pavement,
        ego_col=40,
        rgb=np.zeros((h, w, 3), dtype=np.uint8),
        ufld_model="auto",
        mask_source="twinlite",
    )
    assert occupied.paint_source == "twinlite"


def test_twinlite_missing_weights_returns_none():
    from app.twinlite_net import infer_twinlite_masks

    assert infer_twinlite_masks(None) is None


def test_ufld_missing_weights_falls_back_to_teacher():
    h, w = 40, 80
    pavement = np.ones((h, w), dtype=bool)
    lane = _dashed(h, w, 30, y0=4, y1=36, on=3, off=4, thick=2)
    occupied = occupied_lane_rails(lane, pavement, ego_col=40, rgb=None, ufld_model="auto")
    assert occupied.paint_source == "gt_row_anchor"
    assert try_ufld_paint_rails(None) is None
    assert try_ufld_paint_rails(np.zeros((8, 8, 3), dtype=np.uint8), model_path=Path("/no/such/ufld.pth")) is None


def test_ufld_decode_picks_dataset_from_pred_shape():
    from app.lane_rails import _import_ufld_decode, _rail_is_chord, _split_xy_points

    decode_mod = _import_ufld_decode()
    assert decode_mod is not None
    culane = decode_mod.cfg_from_pred({"loc_row": np.zeros((1, 200, 72, 4))})
    assert culane["net_w"] == 1600
    tusimple = decode_mod.cfg_from_pred({"loc_row": np.zeros((1, 100, 56, 4))})
    assert tusimple["net_w"] == 800
    curve = decode_mod.cfg_from_pred({"loc_row": np.zeros((1, 200, 72, 10))})
    assert curve["num_row"] == 72
    assert len(curve["row_lane_idx"]) == 10
    groups = _split_xy_points([(20.0, 10.0), (22.0, 40.0), (90.0, 42.0), (92.0, 70.0)])
    assert len(groups) >= 2
    chord = np.full(80, np.nan)
    chord[10:20] = np.linspace(5, 70, 10)
    assert _rail_is_chord(chord)
    lane = np.full(80, np.nan)
    lane[20:70] = 40.0 + 0.2 * np.arange(50)
    assert not _rail_is_chord(lane)


def test_ufld_polyline_resamples_to_one_x_per_row():
    height, width = 40, 80
    pts = [(10.0, 8.0), (12.0, 20.0), (14.0, 32.0)]
    xs = polyline_to_row_xs(pts, height, width)
    assert np.isfinite(xs[14])
    assert np.isfinite(xs[26])
    assert abs(float(xs[20]) - 12.0) < 1.5


def test_two_paint_rails_beat_curb_pair():
    h, w = 80, 200
    left_paint = np.full(h, 60.0)
    right_paint = np.full(h, 120.0)
    left_curb = np.full(h, 10.0)
    right_curb = np.full(h, 170.0)
    pair = select_occupied_pair(
        [
            (left_paint, "paint"),
            (right_paint, "paint"),
            (left_curb, "curb"),
            (right_curb, "curb"),
        ],
        ego_col=90,
        width=w,
        hood_rows=4,
        min_span_ratio=0.06,
        max_span_ratio=0.48,
    )
    assert pair is not None
    left, right, k_l, k_r = pair
    assert k_l == "paint" and k_r == "paint"
    assert abs(float(np.nanmedian(left)) - 60.0) < 0.5
    assert abs(float(np.nanmedian(right)) - 120.0) < 0.5


def test_select_pair_rejects_whole_carriageway():
    h, w = 30, 200
    dash = np.full(h, 70.0)
    left_curb = np.full(h, 5.0)
    right_curb = np.full(h, 120.0)
    pair = select_occupied_pair(
        [(dash, "paint"), (left_curb, "curb"), (right_curb, "curb")],
        ego_col=90,
        width=w,
        hood_rows=2,
        min_span_ratio=0.06,
        max_span_ratio=0.48,
    )
    assert pair is not None
    left, right, k_l, k_r = pair
    assert k_l == "paint"
    assert k_r == "curb"
    assert abs(float(np.nanmedian(left)) - 70.0) < 0.5
    assert abs(float(np.nanmedian(right)) - 120.0) < 0.5


def test_densify_fills_interior_nans():
    xs = np.full(40, np.nan)
    xs[8] = 10.0
    xs[16] = 12.0
    xs[24] = 14.0
    out = densify_rail(xs, width=80, hood_rows=2, degree=1)
    assert np.isfinite(out[12])
    assert np.isfinite(out[20])
    assert abs(float(out[16]) - 12.0) < 1.0


def test_densify_does_not_explode_to_image_edge():
    xs = np.full(80, np.nan)
    for y, x in ((20, 250.0), (30, 280.0), (40, 310.0), (50, 340.0)):
        xs[y] = x
    out = densify_rail(xs, width=960, hood_rows=8, extra_rows=20)
    assert np.isfinite(out[35])
    near = out[np.isfinite(out)]
    assert float(np.min(near)) > 200.0
    assert float(np.max(near)) < 400.0


def test_track_drops_far_island_jump():
    h, w = 80, 160
    left = np.full(h, 50.0)
    right = np.full(h, 110.0)
    right[:18] = 8.0
    pavement = np.zeros((h, w), dtype=bool)
    pavement[:, 20:130] = True
    pavement[:18, :] = False
    pavement[:18, :12] = True
    out_l, out_r = track_occupied_pair(
        left,
        right,
        pavement=pavement,
        left_kind="paint",
        right_kind="curb",
        ego_col=80,
        hood_rows=4,
        max_dx=24.0,
    )
    far = out_r[:18]
    far = far[np.isfinite(far)]
    assert far.size == 0 or float(np.min(far)) > 40.0
    assert np.isfinite(out_r[40])
    assert 90.0 < float(out_r[40]) < 135.0


def test_drop_identity_jumps_cuts_horizontal_chord():
    xs = np.full(40, np.nan)
    xs[:20] = 140.0
    xs[20:] = 290.0
    out = drop_identity_jumps(xs)
    assert not np.isfinite(out[10])
    assert np.isfinite(out[25])
    assert abs(float(out[25]) - 290.0) < 0.1


def test_split_sharp_turns_cuts_right_angle():
    h, w = 80, 160
    xs = np.full(h, np.nan)
    for y in range(10, 40):
        xs[y] = 40.0
    for y in range(40, 70):
        xs[y] = 40.0 + (y - 40) * 3.0
    parts = split_sharp_turns(xs, width=w)
    assert len(parts) >= 1
    for frag in parts:
        fin = np.flatnonzero(np.isfinite(frag))
        if fin.size < 12:
            continue
        win = 4
        for i in range(win, fin.size - win, 4):
            y0, y1, y2 = int(fin[i - win]), int(fin[i]), int(fin[i + win])
            v1 = (float(frag[y1] - frag[y0]), float(y1 - y0))
            v2 = (float(frag[y2] - frag[y1]), float(y2 - y1))
            a = np.array(v1)
            b = np.array(v2)
            na, nb = np.linalg.norm(a), np.linalg.norm(b)
            if na < 1e-6 or nb < 1e-6:
                continue
            ang = np.degrees(np.arccos(np.clip(np.dot(a, b) / (na * nb), -1, 1)))
            assert ang < 50.0


def test_track_near_curb_follows_widening_edge():
    h, w = 60, 160
    left = np.full(h, 40.0)
    right = np.full(h, np.nan)
    for y in range(20, 40):
        right[y] = 90.0
    pavement = np.zeros((h, w), dtype=bool)
    sidewalk = np.zeros((h, w), dtype=bool)
    for y in range(h):
        hi = min(w - 2, 90 + max(0, y - 30) * 2)
        pavement[y, 20 : hi + 1] = True
        sidewalk[y, hi + 1 :] = True
    out_l, out_r = track_occupied_pair(
        left,
        right,
        pavement=pavement,
        sidewalk=sidewalk,
        left_kind="paint",
        right_kind="curb",
        ego_col=70,
        hood_rows=2,
        max_dx=40.0,
    )
    ys = np.flatnonzero(np.isfinite(out_r))
    assert ys.size > 10
    assert float(out_r[ys.max()]) >= 90.0


def test_track_follows_curb_left_of_heading():
    """Left bend: far right curb sits left of ego+8 and must not freeze there."""
    h, w = 80, 160
    ego = 80
    left = np.full(h, 30.0)
    right = np.full(h, np.nan)
    pavement = np.zeros((h, w), dtype=bool)
    sidewalk = np.zeros((h, w), dtype=bool)
    for y in range(h):
        hi = 60 + int(round((y / (h - 1)) * 55))  # y=0 → 60, y=79 → 115
        right[y] = float(hi)
        pavement[y, 15 : hi + 1] = True
        sidewalk[y, hi + 1 :] = True
    out_l, out_r = track_occupied_pair(
        left,
        right,
        pavement=pavement,
        sidewalk=sidewalk,
        left_kind="paint",
        right_kind="curb",
        ego_col=ego,
        hood_rows=2,
        max_dx=40.0,
    )
    far = out_r[8:18]
    far = far[np.isfinite(far)]
    assert far.size >= 5
    # Must follow the curb (~65–70), not freeze on heading+8 (=88).
    assert float(np.median(far)) < ego
    assert abs(float(np.median(far)) - 88.0) > 8.0


def test_track_curb_does_not_climb_pole():
    h, w = 80, 160
    left = np.full(h, 40.0)
    right = np.full(h, 110.0)
    pavement = np.zeros((h, w), dtype=bool)
    sidewalk = np.zeros((h, w), dtype=bool)
    obstacle = np.zeros((h, w), dtype=bool)
    pavement[:, 20:111] = True
    sidewalk[:, 111:] = True
    # Lamp post on the far sidewalk, aligned with heading+8.
    obstacle[:28, 88] = True
    out_l, out_r = track_occupied_pair(
        left,
        right,
        pavement=pavement,
        sidewalk=sidewalk,
        left_kind="paint",
        right_kind="curb",
        ego_col=80,
        hood_rows=2,
        obstacle=obstacle,
    )
    for y in np.flatnonzero(np.isfinite(out_r)):
        xi = int(round(float(out_r[y])))
        assert not bool(obstacle[int(y), xi])


def test_clip_obstacle_far_drops_pole_column():
    xs = np.full(40, 88.0)
    obs = np.zeros((40, 120), dtype=bool)
    obs[:12, 88] = True
    out = clip_obstacle_far(xs, obs)
    assert not np.isfinite(out[:12]).any()
    assert np.isfinite(out[20])


def test_occupied_keeps_left_paint_rail():
    h, w = 80, 200
    pavement = np.zeros((h, w), dtype=bool)
    sidewalk = np.zeros((h, w), dtype=bool)
    pavement[:, 15:150] = True
    sidewalk[:, 150:180] = True
    lane = np.zeros((h, w), dtype=bool)
    lane[10:70, 22:25] = True
    lane |= _dashed(h, w, 70, y0=10, y1=70)
    occupied = occupied_lane_rails(
        lane,
        pavement,
        sidewalk=sidewalk,
        ego_col=95,
        ufld_model=False,
    )
    assert occupied.paint_rails
    meds = [float(np.nanmedian(r)) for r in occupied.paint_rails]
    assert any(abs(m - 23.0) < 6.0 for m in meds)
    # Occupied left stays on the dashes, not the left solid.
    ys = [int(v) for _u, v in occupied.points]
    sample = ys[len(ys) // 2]
    assert abs(float(occupied.left[sample]) - 71.0) < 10.0


def test_trim_border_glued_drops_x0_tail():
    xs = np.full(40, 40.0)
    xs[30:] = 0.0
    out = trim_border_glued(xs, width=80, border=3)
    assert np.isfinite(out[10])
    assert not np.isfinite(out[35])


def test_track_paint_skips_blob_beside_car():
    """Dashes must not walk onto a leftover paint blob that leads into a vehicle."""
    h, w = 80, 200
    left = np.full(h, np.nan)
    right = np.full(h, 150.0)
    for y in range(30, 70):
        left[y] = 90.0
    pavement = np.zeros((h, w), dtype=bool)
    sidewalk = np.zeros((h, w), dtype=bool)
    lane = np.zeros((h, w), dtype=bool)
    obstacle = np.zeros((h, w), dtype=bool)
    pavement[:, 20:151] = True
    sidewalk[:, 151:] = True
    lane[30:70, 88:93] = True
    # Far distractor next to a car, left of the dashes.
    lane[8:22, 48:53] = True
    obstacle[:18, 40:55] = True
    out_l, _out_r = track_occupied_pair(
        left,
        right,
        pavement=pavement,
        sidewalk=sidewalk,
        lane=lane,
        left_kind="paint",
        right_kind="curb",
        ego_col=120,
        hood_rows=2,
        obstacle=obstacle,
    )
    far = out_l[:22]
    far = far[np.isfinite(far)]
    if far.size:
        assert float(np.min(far)) > 70.0
        for y in np.flatnonzero(np.isfinite(out_l)):
            xi = int(round(float(out_l[y])))
            assert not bool(obstacle[int(y), xi])


def test_track_does_not_collapse_paint_in_traffic():
    """Vehicles beside paint must not reduce the occupied pair to a single point."""
    h, w = 80, 200
    left = np.full(h, 70.0)
    right = np.full(h, 120.0)
    pavement = np.ones((h, w), dtype=bool)
    lane = _dashed(h, w, 68, y0=8, y1=72) | _dashed(h, w, 118, y0=8, y1=72)
    obstacle = np.zeros((h, w), dtype=bool)
    obstacle[:, 90:110] = True  # cars between the rails
    out_l, out_r = track_occupied_pair(
        left,
        right,
        pavement=pavement,
        lane=lane,
        left_kind="paint",
        right_kind="paint",
        ego_col=95,
        hood_rows=2,
        obstacle=obstacle,
    )
    assert int(np.isfinite(out_l).sum()) >= 20
    assert int(np.isfinite(out_r).sum()) >= 20
