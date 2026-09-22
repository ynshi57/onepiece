"""Tests for CPU road-structure hole fill (layer B, not primary free space)."""

from __future__ import annotations

import numpy as np

from app.guidance_path import PATH_STATUS_OK, centerline_from_mask
from app.road_structure import (
    complete_by_width_prior,
    complete_lane_markings,
    complete_road_structure,
    constrain_mids_to_occupied_lane,
    ego_lane_mask,
    extend_mids_toward_ego,
    fill_enclosed_holes,
    fill_internal_row_gaps,
    fit_guidance_g2,
    follow_occupied_mids,
    follow_occupied_region,
    heading_curvature_stats,
    pavement_edge_walls,
    smooth_mids,
    straight_occupied_centerline,
)


def test_fill_enclosed_holes_fills_island_not_border():
    mask = np.ones((12, 16), dtype=bool)
    mask[0, :] = False
    mask[-1, :] = False
    mask[:, 0] = False
    mask[:, -1] = False
    mask[4:7, 6:10] = False  # enclosed hole
    mask[1:4, 1] = False  # border-connected bite, must stay empty
    filled = fill_enclosed_holes(mask, max_area=20)
    assert filled[5, 8]
    assert not filled[2, 1]
    assert not filled[0, 8]


def test_fill_enclosed_holes_area_cap_skips_courtyard():
    mask = np.ones((20, 20), dtype=bool)
    mask[0, :] = mask[-1, :] = mask[:, 0] = mask[:, -1] = False
    mask[4:16, 4:16] = False  # 12x12 = 144 cells
    filled = fill_enclosed_holes(mask, max_area=20)
    assert not filled[10, 10]
    filled_small = fill_enclosed_holes(mask, max_area=200)
    assert filled_small[10, 10]


def test_row_gaps_fill_car_not_shoulder():
    mask = np.zeros((5, 20), dtype=bool)
    mask[2, 2:8] = True
    mask[2, 12:18] = True  # 4-cell gap = car
    out = fill_internal_row_gaps(mask, max_gap=5)
    assert out[2, 9]
    # left shoulder (x=0..1) stays empty — not between two runs
    assert not out[2, 0]


def test_complete_road_closes_small_bite():
    pavement = np.zeros((24, 40), dtype=bool)
    pavement[4:20, 8:32] = True
    pavement[10:16, 8:12] = False  # 4-cell left bite, smaller than close_radius
    done = complete_road_structure(pavement, close_radius=5, max_gap=10, max_hole_area=80)
    assert done[13, 10]
    assert not done[2, 20]  # outside the band
    assert pavement.sum() < done.sum()


def test_width_prior_restores_side_bite_not_border():
    pavement = np.zeros((20, 40), dtype=bool)
    pavement[:, 10:30] = True  # 20-wide corridor
    pavement[8:14, 10:16] = False  # left bite
    out = complete_by_width_prior(pavement, max_extend=8, min_width=12)
    assert out[10, 12]
    assert not out[10, 2]


def test_width_prior_skips_skinny_spike():
    pavement = np.zeros((12, 40), dtype=bool)
    pavement[:, 10:30] = True
    pavement[0:3, :] = False
    pavement[0:3, 19:21] = True  # 2-cell far spike
    out = complete_by_width_prior(pavement, max_extend=8, min_width=12)
    assert not out[1, 12]


def test_ego_lane_mask_keeps_occupied_lane_not_adjacent():
    """A wide adjacent/oncoming blob must not pull the occupied corridor sideways."""
    h, w = 24, 80
    free = np.zeros((h, w), dtype=bool)
    free[:, 8:72] = True
    walls = np.zeros((h, w), dtype=bool)
    walls[:, 24:26] = True
    walls[:, 48:50] = True
    ego_col = 36
    ego = ego_lane_mask(free, walls, ego_col=ego_col, lane_width=30)
    assert ego[12, 36]
    assert not ego[12, 60]
    path = centerline_from_mask(ego, samples=12, horizon=0.9, source="ego")
    xs = [p.x for p in path.primary.points]
    assert all(abs(x - ego_col / w) < 0.08 for x in xs)


def test_ego_lane_follows_whichever_lane_contains_vehicle():
    """Occupancy is the interval containing heading, not a hardcoded left lane."""
    h, w = 24, 80
    free = np.ones((h, w), dtype=bool)
    walls = np.zeros((h, w), dtype=bool)
    for col in (10, 30, 50, 70):
        walls[:, col] = True
    left = ego_lane_mask(free, walls, ego_col=20, lane_width=20)
    right = ego_lane_mask(free, walls, ego_col=60, lane_width=20)
    assert left[12, 20] and not left[12, 60]
    assert right[12, 60] and not right[12, 20]


def test_ego_lane_mask_does_not_bridge_interior_block():
    h, w = 24, 80
    free = np.zeros((h, w), dtype=bool)
    free[:, 20:50] = True
    free[8:12, :] = False  # obstacle band
    walls = np.zeros((h, w), dtype=bool)
    walls[:, 20] = True
    walls[:, 49] = True
    ego = ego_lane_mask(free, walls, ego_col=35, lane_width=30)
    path = centerline_from_mask(ego, samples=12, horizon=0.9, source="ego")
    ys = [p.y for p in path.primary.points]
    assert all(y < 0.55 for y in ys)


def test_ego_lane_does_not_lock_on_a_single_rail():
    """One nearby marking is not occupancy; wait until heading sits between two rails."""
    h, w = 24, 80
    free = np.zeros((h, w), dtype=bool)
    free[:, 8:72] = True
    walls = np.zeros((h, w), dtype=bool)
    walls[18:24, 12] = True  # only a left rail at the bottom
    walls[0:18, 24] = True
    walls[0:18, 48] = True
    ego = ego_lane_mask(free, walls, ego_col=36, lane_width=30)
    assert not ego[20, 12]
    assert ego[10, 36]
    assert not ego[10, 60]


def test_ego_lane_skips_skinny_leading_sliver():
    """A 2-cell sliver against the right rail at the bottom must not become the start."""
    h, w = 24, 80
    free = np.zeros((h, w), dtype=bool)
    free[0:16, 24:50] = True  # full occupied lane farther away
    free[16:24, 47:50] = True  # skinny near sliver on the right rail
    walls = np.zeros((h, w), dtype=bool)
    walls[:, 24] = True
    walls[:, 50] = True
    ego = ego_lane_mask(free, walls, ego_col=36, lane_width=24)
    assert not ego[20, 48]
    assert ego[10, 36]


def test_straight_occupied_centerline_is_constant_x_and_stops():
    h, w = 40, 80
    free = np.zeros((h, w), dtype=bool)
    free[12:40, 20:50] = True
    free[0:8, 20:50] = True
    free[8:12, :] = False
    walls = np.zeros((h, w), dtype=bool)
    walls[:, 20] = True
    walls[:, 50] = True
    path = straight_occupied_centerline(free, walls, ego_col=35, samples=16, horizon=0.95)
    assert path.status == PATH_STATUS_OK
    xs = [p.x for p in path.primary.points]
    assert max(xs) - min(xs) < 1e-6
    assert 0.40 < xs[0] < 0.50
    ys = [p.y for p in path.primary.points]
    assert all(y < 0.75 for y in ys)


def test_straight_line_does_not_follow_side_bite_centroid():
    h, w = 32, 80
    free = np.zeros((h, w), dtype=bool)
    free[:, 24:50] = True
    free[20:32, 24:36] = False
    walls = np.zeros((h, w), dtype=bool)
    walls[:, 24] = True
    walls[:, 50] = True
    path = straight_occupied_centerline(free, walls, ego_col=37, samples=16, horizon=0.95)
    xs = [p.x for p in path.primary.points]
    assert max(xs) - min(xs) < 1e-6
    assert abs(xs[0] - 37 / w) < 0.05


def test_whole_carriageway_is_not_occupied_lane():
    h, w = 20, 80
    walls = np.zeros((h, w), dtype=bool)
    walls[:, 2] = True
    walls[:, 77] = True
    mids = follow_occupied_mids(walls, ego_col=40)
    assert np.isnan(mids).all()


def test_curb_and_dash_lock_whichever_lane_has_heading():
    h, w = 24, 80
    pavement = np.zeros((h, w), dtype=bool)
    pavement[:, 10:70] = True
    lane = np.zeros((h, w), dtype=bool)
    lane[:, 40] = True
    walls = lane | pavement_edge_walls(pavement)
    left = follow_occupied_mids(walls, ego_col=25)
    right = follow_occupied_mids(walls, ego_col=55)
    assert 10 < left[12] < 40
    assert 40 < right[12] < 70


def test_follow_mids_bends_with_curving_rails():
    h, w = 30, 80
    walls = np.zeros((h, w), dtype=bool)
    for y in range(h):
        shift = (h - 1 - y) // 2
        walls[y, 20 - shift] = True
        walls[y, 50 - shift] = True
    mids = follow_occupied_mids(walls, ego_col=35)
    assert np.isfinite(mids[-2]) and np.isfinite(mids[2])
    assert mids[2] < mids[-2] - 2


def test_follow_mids_ignores_merged_wide_span():
    """A dashed gap that briefly merges two lanes must not yank occupancy sideways."""
    h, w = 16, 80
    walls = np.zeros((h, w), dtype=bool)
    walls[:, 20] = True
    walls[:, 40] = True
    walls[:, 60] = True
    walls[8, 40] = False  # one row loses the divider
    mids = follow_occupied_mids(walls, ego_col=50)
    assert 40 < mids[12] < 60
    assert 40 < mids[8] < 60
    assert 40 < mids[4] < 60


def test_dashed_gap_is_not_occupied_lane():
    """A short gap between two marking strokes is paint, not a driving lane."""
    h, w = 24, 80
    walls = np.zeros((h, w), dtype=bool)
    walls[:, 10] = True
    walls[:, 50] = True
    walls[0:10, 18] = True
    walls[0:10, 26] = True
    mids = follow_occupied_mids(walls, ego_col=30)
    assert 10 < mids[4] < 50
    assert not (18 <= mids[4] <= 26)


def test_follow_mids_ignores_one_pixel_sidewalk_overlap():
    h, w = 12, 80
    walls = np.zeros((h, w), dtype=bool)
    walls[:, 20] = True
    walls[:, 50] = True
    # farther row: occupied (20,50) barely touches a right-hand span
    walls[2, 20] = True
    walls[2, 49:52] = True
    walls[2, 78] = True
    mids = follow_occupied_mids(walls, ego_col=35)
    assert 20 < mids[8] < 50
    assert 20 < mids[2] < 50


def test_extend_mids_starts_near_ego():
    mids = np.full(40, np.nan)
    mids[8:20] = 50.0
    out = extend_mids_toward_ego(mids, ego_col=30, hood_rows=2)
    assert np.isfinite(out[-3])
    assert abs(out[-3] - 50) < 1
    assert abs(out[19] - 50) < 1
    assert np.isnan(out[4])


def test_smooth_mids_reduces_heading_jitter():
    mids = np.full(48, np.nan)
    base = np.linspace(20.0, 40.0, 30)
    jitter = np.array([2.5 if i % 2 == 0 else -2.5 for i in range(30)])
    mids[8:38] = base + jitter
    raw_du = float(np.max(np.abs(np.diff(mids[8:38]))))
    out = smooth_mids(mids, window=9, passes=3, max_du=2.2)
    sm_du = float(np.max(np.abs(np.diff(out[8:38]))))
    assert sm_du < raw_du
    assert sm_du <= 2.2 + 1e-6


def test_complete_lane_fills_dashed_gaps_not_the_lane():
    lane = np.zeros((40, 80), dtype=bool)
    for y, x in ((36, 40), (28, 38), (20, 36), (12, 34)):
        lane[y : y + 2, x : x + 3] = True
    done = complete_lane_markings(lane, associate_dy=12, associate_dx=8, merge_dx=10, thickness=2)
    assert done[24].any()
    assert done[16].any()
    parallel = np.zeros((20, 80), dtype=bool)
    parallel[:, 18:21] = True
    parallel[:, 50:53] = True
    kept = complete_lane_markings(parallel, thickness=1)
    assert not kept[10, 35]


def test_constrain_keeps_mid_inside_rails():
    h, w = 20, 80
    walls = np.zeros((h, w), dtype=bool)
    walls[:, 20] = True
    walls[:, 50] = True
    mids = np.full(h, 35.0)
    mids[5] = 12.0  # crossed the left rail
    out = constrain_mids_to_occupied_lane(mids, walls, ego_col=35, margin=4)
    assert out[5] >= 24
    assert out[10] == 35.0


def test_complete_lane_extends_toward_ego():
    lane = np.zeros((80, 60), dtype=bool)
    for y in (10, 18, 26, 34):
        lane[y : y + 2, 30:33] = True
    done = complete_lane_markings(lane, associate_dy=20, associate_dx=8, merge_dx=10, thickness=2)
    assert done[24].any()
    assert done[70, 28:35].any()


def test_complete_lane_stitches_split_fragments():
    lane = np.zeros((80, 60), dtype=bool)
    lane[12:14, 30:33] = True
    lane[38:40, 31:34] = True
    done = complete_lane_markings(lane, associate_dy=8, associate_dx=8, merge_dx=10, thickness=2)
    assert done[24, 28:36].any()


def test_fit_guidance_g2_caps_heading_jump_and_stays_between_rails():
    n = 80
    mids = np.full(n, np.nan)
    lefts = np.full(n, np.nan)
    rights = np.full(n, np.nan)
    mids[10:70] = np.linspace(40.0, 30.0, 60)
    mids[68] = 10.0
    lefts[10:70] = 20.0
    rights[10:70] = 50.0
    out = fit_guidance_g2(mids, lefts, rights, lam=80000.0, margin=4.0)
    stats = heading_curvature_stats(out)
    assert stats["max_dpsi_deg"] < 10.0
    assert np.all(out[10:70] >= 24.0 - 1e-6)
    assert np.all(out[10:70] <= 46.0 + 1e-6)


def test_fit_guidance_g2_follows_curve_between_rails():
    h = 40
    mids = np.full(h, np.nan)
    lefts = np.full(h, np.nan)
    rights = np.full(h, np.nan)
    for y in range(5, 38):
        shift = (37 - y) * 0.4
        lefts[y] = 20.0 - shift
        rights[y] = 50.0 - shift
        mids[y] = 35.0 - shift
    out = fit_guidance_g2(mids, lefts, rights, lam=400.0, margin=4.0)
    assert out[6] < out[36] - 2
    assert np.all(out[5:38] > lefts[5:38] + 3)
    assert np.all(out[5:38] < rights[5:38] - 3)


def test_wide_near_span_does_not_steal_lock():
    """Dash-to-opposite-curb at the bumper is not the occupied lane."""
    h, w = 24, 80
    walls = np.zeros((h, w), dtype=bool)
    walls[20:24, 10] = True
    walls[20:24, 70] = True
    walls[0:20, 20] = True
    walls[0:20, 50] = True
    mids = follow_occupied_mids(walls, ego_col=35)
    assert 20 < mids[10] < 50
    assert 20 < mids[22] < 50


def test_region_uses_curb_when_right_paint_missing():
    h, w = 24, 80
    pavement = np.zeros((h, w), dtype=bool)
    pavement[:, 10:70] = True
    walls = np.zeros((h, w), dtype=bool)
    walls[:, 40] = True
    walls |= pavement_edge_walls(pavement)
    mids, lefts, rights = follow_occupied_region(pavement, walls, ego_col=55)
    assert 40 < mids[12] < 70
    assert abs(lefts[12] - 40) <= 2
    assert abs(rights[12] - 69) <= 2


def test_region_holds_lane_when_near_divider_vanishes():
    h, w = 24, 80
    pavement = np.zeros((h, w), dtype=bool)
    pavement[:, 10:70] = True
    walls = np.zeros((h, w), dtype=bool)
    walls[0:20, 40] = True
    walls |= pavement_edge_walls(pavement)
    mids, _lefts, rights = follow_occupied_region(pavement, walls, ego_col=55)
    assert 40 < mids[10] < 70
    assert 40 < mids[22] < 70
    assert rights[22] > 55


def test_complete_lane_paint_is_not_fatter_than_thickness():
    lane = np.zeros((20, 40), dtype=bool)
    lane[:, 20] = True
    done = complete_lane_markings(lane, thickness=1)
    widths = [int(np.sum(done[y])) for y in range(20)]
    assert max(widths) <= 3
