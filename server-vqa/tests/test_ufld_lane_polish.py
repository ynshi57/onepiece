from app.ufld_lane_polish import polish_lane_points, polish_row_ego_lane


def test_polish_removes_isolated_x_spike():
    points = []
    for i in range(16):
        t = i / 15.0
        x = 0.30 + 0.10 * t
        if i == 8:
            x = 0.42
        points.append((x, 0.50 + 0.50 * t))
    out = polish_lane_points(points)
    assert len(out) >= 8
    assert all(abs(x - 0.42) >= 0.002 for x, _ in out)
    assert out[len(out) // 2][0] < 0.40


def test_polish_leaves_short_polyline():
    points = [(0.20, 0.50), (0.22, 0.70), (0.24, 0.90)]
    assert polish_lane_points(points) == points


def test_polish_skips_col_anchor():
    points = [(i / 15.0, 0.80 if i == 8 else 0.40) for i in range(16)]
    assert polish_row_ego_lane(points, lane_index=0, source="colAnchor") == points


def test_polish_keeps_longer_side_after_identity_jump():
    points = []
    for i in range(10):
        points.append((0.52, 0.55 + 0.015 * i))
    for i in range(24):
        points.append((0.32 - 0.004 * i, 0.70 + 0.012 * i))
    out = polish_lane_points(points)
    xs = [x for x, _ in out]
    assert max(xs) < 0.40
    assert min(xs) > 0.15
