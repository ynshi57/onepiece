"""CamVid official-K BEV: regular metric grid + dual-layer road structure.

Layer A = visible pavement (holes where cars/people occlude).
Layer B = CPU complete_road_structure (not primary go).
Does not change the iOS App. Metres are h-pinned, Boujou t is not metric.
"""
from __future__ import annotations

import argparse
import importlib.util
import math
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.lane_metrics import dilate
from app.guidance_path import (
    PATH_STATUS_INSUFFICIENT,
    PATH_STATUS_OK,
    GuidanceLine,
    GuidancePath,
    GuidancePoint,
    RiskSegment,
    centerline_from_mask,
)
from app.open_dataset_adapters import (
    CAMVID_LANE_COLORS,
    CAMVID_OBSTACLE_COLORS,
    CAMVID_ROAD_COLORS,
    CAMVID_SIDEWALK_COLORS,
)
from app.lane_rails import occupied_lane_rails, rail_to_points
from app.road_structure import (
    complete_road_structure,
    ego_lane_mask,
    extend_mids_toward_ego,
    follow_occupied_mids,
    heading_curvature_stats,
    pavement_edge_walls,
    smooth_mids,
    straight_occupied_centerline,
)

_STEM_RE = re.compile(r"^(?P<seq>0001TP|0006R0|0016E5|0005VD)_f?(?P<frame>\d+)$")


def parse_camvid_stem(stem: str) -> tuple[str, int]:
    match = _STEM_RE.match(stem)
    if not match:
        raise ValueError(f"unsupported CamVid stem: {stem!r}")
    return match.group("seq"), int(match.group("frame"))

_OFFICIAL = Path(__file__).with_name("render_camvid_frame_bev_official.py")
_spec = importlib.util.spec_from_file_location("bev_official", _OFFICIAL)
assert _spec and _spec.loader
bev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bev)

CAM_H_M = bev.CAM_H_M
BEV_X_MIN, BEV_X_MAX = bev.BEV_X_MIN, bev.BEV_X_MAX
BEV_Z_MIN, BEV_Z_MAX = bev.BEV_Z_MIN, bev.BEV_Z_MAX
BEV_RES = bev.BEV_RES

PAVEMENT = (168, 172, 180)
STRUCTURE = (110, 140, 168)
SIDEWALK = (46, 125, 80)
UNKNOWN = (36, 38, 44)
GRID = (58, 60, 68)
PRIMARY = (10, 132, 255)
LANE_YELLOW = (255, 214, 10)
RISK = (255, 69, 58)


def _font(size: int) -> ImageFont.ImageFont:
    return bev._font(size)


def _caption(image: Image.Image, text: str) -> Image.Image:
    canvas = Image.new("RGB", (image.width, image.height + 56), (12, 12, 16))
    canvas.paste(image, (0, 56))
    ImageDraw.Draw(canvas).text((14, 16), text, fill=(245, 245, 247), font=_font(16))
    return canvas


def _draw_metric_grid(draw: ImageDraw.ImageDraw, bev_w: int, bev_h: int) -> None:
    font = _font(15)
    for metre in range(int(math.floor(BEV_Z_MIN)), int(math.ceil(BEV_Z_MAX)) + 1):
        y = (BEV_Z_MAX - metre) / BEV_RES
        draw.line([(0, y), (bev_w, y)], fill=GRID, width=1)
        if metre % 2 == 0:
            draw.text((8, y + 2), f"{metre}m", fill=(180, 180, 186), font=font)
    x0 = (0.0 - BEV_X_MIN) / BEV_RES
    draw.line([(x0, 0), (x0, bev_h)], fill=(80, 82, 90), width=1)
    for metre in range(int(math.ceil(BEV_X_MIN)), int(math.floor(BEV_X_MAX)) + 1):
        x = (metre - BEV_X_MIN) / BEV_RES
        draw.line([(x, 0), (x, bev_h)], fill=GRID, width=1)


def _legend(
    draw: ImageDraw.ImageDraw,
    bev_h: int,
    lines: list[tuple[str, tuple[int, int, int]]],
    *,
    at: str = "bottom",
) -> None:
    font = _font(15)
    box_h = 18 + 18 * len(lines)
    top = 8 if at == "top" else bev_h - box_h - 8
    draw.rectangle([8, top, 360, top + box_h], fill=(12, 12, 16))
    y = top + 8
    for text, color in lines:
        draw.text((16, y), text, fill=color, font=font)
        y += 18


def _paint_mask(canvas: np.ndarray, mask: np.ndarray, color: tuple[int, int, int]) -> None:
    canvas[mask] = color


def _centerline_pixels(path, bev_w: int, bev_h: int) -> list[tuple[float, float]]:
    if path.status != PATH_STATUS_OK or not path.lines:
        return []
    points = []
    for point in path.lines[0].points:
        x = float(point.x) * bev_w
        y = (1.0 - float(point.y)) * bev_h
        points.append((x, y))
    return points


def _draw_polyline(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    color: tuple[int, int, int],
    *,
    width: int,
    dashed: bool = False,
    risk: set[int] | None = None,
    cap: bool = True,
) -> None:
    if len(points) < 2:
        return
    for index in range(len(points) - 1):
        if dashed and index % 2:
            continue
        stroke = RISK if (risk and index in risk) else color
        draw.line([points[index], points[index + 1]], fill=stroke, width=width)
    if cap:
        x, y = points[0]
        draw.ellipse([x - 5, y - 5, x + 5, y + 5], fill=color)


def _bev_xy_to_xz(col: float, row: float) -> tuple[float, float]:
    x_m = BEV_X_MIN + col * BEV_RES
    z_m = BEV_Z_MAX - row * BEV_RES
    return x_m, z_m


def _risk_indices(path, hole: np.ndarray, bev_w: int, bev_h: int) -> set[int]:
    if path.status != PATH_STATUS_OK or not path.lines:
        return set()
    risk: set[int] = set()
    points = path.lines[0].points
    for i, point in enumerate(points):
        col = int(round(float(point.x) * bev_w))
        row = int(round((1.0 - float(point.y)) * bev_h))
        if 0 <= row < bev_h and 0 <= col < bev_w and hole[row, col]:
            risk.add(max(0, i - 1))
            risk.add(i)
    return risk


def _attach_risk(path, hole: np.ndarray, bev_w: int, bev_h: int) -> None:
    if path.status != PATH_STATUS_OK or not path.lines:
        return
    indices = sorted(_risk_indices(path, hole, bev_w, bev_h))
    if not indices:
        return
    start = indices[0]
    prev = indices[0]
    segments: list[RiskSegment] = []
    for idx in indices[1:]:
        if idx == prev + 1:
            prev = idx
            continue
        segments.append(RiskSegment(from_index=start, to_index=prev, reason="through_obstacle"))
        start = prev = idx
    segments.append(RiskSegment(from_index=start, to_index=prev, reason="through_obstacle"))
    path.lines[0].risk_segments = segments
    path.lines[0].kind = "alternative"


def main(stem: str = "0001TP_008430", paint_source: str = "auto") -> None:
    sequence, frame_num = parse_camvid_stem(stem)
    repo = Path(__file__).resolve().parents[2]
    calib = repo / "dataset" / "camvid" / "calib"
    out = repo / "docs" / "model-lab" / "figures" / f"camvid-{stem}"
    out.mkdir(parents=True, exist_ok=True)
    rgb = np.asarray(Image.open(repo / "dataset" / "camvid" / "CamVid_RGB" / f"{stem}.png").convert("RGB"))
    label = np.asarray(
        Image.open(repo / "dataset" / "camvid" / "CamVid_Label" / f"{stem}_L.png").convert("RGB")
    )
    pose = bev.load_frame_pose(calib, frame_num, sequence=sequence)
    if pose is None:
        raise FileNotFoundError(f"no {sequence} Boujou pose for frame {frame_num}")
    k, r, _t = pose
    fx, fy, cx, cy = float(k[0, 0]), float(k[1, 1]), float(k[0, 2]), float(k[1, 2])
    pitch = bev.pitch_from_r(r)
    source = f"official {sequence} fx={fx:.0f} pitch={math.degrees(pitch):.1f}° h={CAM_H_M}m pinned"

    bev_w = int(round((BEV_X_MAX - BEV_X_MIN) / BEV_RES))
    bev_h = int(round((BEV_Z_MAX - BEV_Z_MIN) / BEV_RES))
    cols = np.arange(bev_w)
    rows = np.arange(bev_h)
    xs = BEV_X_MIN + (cols + 0.5) * BEV_RES
    zs = BEV_Z_MAX - (rows + 0.5) * BEV_RES
    xx, zz = np.meshgrid(xs, zs)
    u, v, ok = bev._uv_from_ground(xx, zz, fx, fy, cx, cy, CAM_H_M, pitch)

    road = bev._color_mask(label, CAMVID_ROAD_COLORS - CAMVID_LANE_COLORS)
    sidewalk = bev._color_mask(label, CAMVID_SIDEWALK_COLORS)
    lane = bev._color_mask(label, CAMVID_LANE_COLORS)
    pavement = road | lane
    bev_pavement = bev._warp(pavement.astype(np.uint8), u, v, ok).astype(bool)
    bev_sidewalk = bev._warp(sidewalk.astype(np.uint8), u, v, ok).astype(bool)
    bev_lane = bev._warp(lane.astype(np.uint8), u, v, ok).astype(bool)
    # Thin CamVid paint is ~1 BEV cell; dilate so lane geometry is readable.
    bev_lane_draw = dilate(bev_lane, max(2, int(round(0.12 / BEV_RES))))

    close_radius = max(1, int(round(1.05 / BEV_RES)))
    max_gap = max(1, int(round(4.5 / BEV_RES)))
    max_hole = max(1, int(round(10.0 / (BEV_RES * BEV_RES))))
    structure = complete_road_structure(
        bev_pavement,
        close_radius=close_radius,
        max_gap=max_gap,
        max_hole_area=max_hole,
        max_extend=max(1, int(round(2.2 / BEV_RES))),
        min_width=max(1, int(round(3.5 / BEV_RES))),
    )
    filled = structure & np.logical_not(bev_pavement)
    print("visible pavement cells", int(bev_pavement.sum()), "structure", int(structure.sum()), "filled", int(filled.sum()))

    horizon_v = cy + fy * math.tan(pitch)
    min_contact_v = horizon_v + 20.0
    footprints: list[tuple[np.ndarray, np.ndarray, tuple[int, int, int]]] = []
    for colors, paint in bev._OBS_LAYERS:
        foot = bev._lowest_in_column(bev._color_mask(label, colors))
        ox, oz = bev._unproject_mask(foot, fx, fy, cx, cy, pitch, min_contact_v)
        footprints.append((ox, oz, paint))

    def _base_canvas() -> np.ndarray:
        canvas = np.zeros((bev_h, bev_w, 3), dtype=np.uint8)
        canvas[:] = UNKNOWN
        return canvas

    def _stamp_feet(canvas: np.ndarray) -> None:
        for ox, oz, paint in footprints:
            bev._paint_disks(canvas, ox, oz, paint, radius=3)

    obs_mask = np.zeros((bev_h, bev_w), dtype=bool)
    for ox, oz, _paint in footprints:
        tmp = np.zeros((bev_h, bev_w, 3), dtype=np.uint8)
        bev._paint_disks(tmp, ox, oz, (255, 255, 255), radius=3)
        obs_mask |= tmp[:, :, 0] > 0
    blocked = dilate(obs_mask, max(1, int(round(0.6 / BEV_RES))))
    free = bev_pavement & np.logical_not(blocked)

    # --- 15 visible free space (irregular is the occlusion truth) ---
    vis = _base_canvas()
    _paint_mask(vis, bev_sidewalk, SIDEWALK)
    _paint_mask(vis, bev_pavement, PAVEMENT)
    _paint_mask(vis, bev_lane_draw, LANE_YELLOW)
    _stamp_feet(vis)
    img15 = Image.fromarray(vis)
    draw = ImageDraw.Draw(img15)
    _draw_metric_grid(draw, bev_w, bev_h)
    _caption(img15, f"15  Visible free space  {source}").save(out / "15-bev-visible-freespace.jpg", quality=92)

    # --- 16 structure completion ---
    st = _base_canvas()
    _paint_mask(st, bev_sidewalk, SIDEWALK)
    _paint_mask(st, structure, STRUCTURE)
    _paint_mask(st, bev_pavement, PAVEMENT)
    _paint_mask(st, bev_lane_draw, LANE_YELLOW)
    _stamp_feet(st)
    img16 = Image.fromarray(st)
    draw = ImageDraw.Draw(img16)
    _draw_metric_grid(draw, bev_w, bev_h)
    _caption(img16, f"16  Structure fill  {source}").save(out / "16-bev-structure-fill.jpg", quality=92)

    # --- 17 dual lines ---
    # Primary: occupied-lane center following rails (curve OK). Stop on
    # camera-label obstacles. Heading-only paint is not occupancy — add curb.
    ego_col = int(round((0.0 - BEV_X_MIN) / BEV_RES))
    ego_free = ego_lane_mask(
        free,
        bev_lane | pavement_edge_walls(bev_pavement),
        ego_col=ego_col,
        lane_width=max(4, int(round(3.2 / BEV_RES))),
    )
    bev_walls = dilate(bev_lane, 2) | pavement_edge_walls(bev_pavement)
    bev_mids = follow_occupied_mids(bev_walls, ego_col=ego_col, wall_gap=12)
    obstacle_cam = bev._color_mask(label, CAMVID_OBSTACLE_COLORS)
    img_h, img_w = rgb.shape[:2]
    rail_lane, rail_pavement, rail_sidewalk = lane, pavement, sidewalk
    rail_mask_source = "gt_row_anchor"
    ufld_model: object = "auto"
    twinlite_lane = None
    twinlite_da = None
    if paint_source == "twinlite":
        from app.twinlite_net import LAST_ERROR as TWINLITE_ERROR
        from app.twinlite_net import infer_twinlite_masks

        masks = infer_twinlite_masks(rgb)
        if masks is None:
            raise RuntimeError(f"TwinLiteNet failed: {TWINLITE_ERROR}")
        twinlite_lane, twinlite_da = masks
        rail_lane, rail_pavement, rail_sidewalk = twinlite_lane, twinlite_da, None
        rail_mask_source = "twinlite"
        ufld_model = False
    elif paint_source == "gt":
        ufld_model = False
    occupied = occupied_lane_rails(
        rail_lane,
        rail_pavement,
        sidewalk=rail_sidewalk,
        ego_col=int(round(cx)),
        obstacle=obstacle_cam,
        rgb=rgb,
        ufld_model=ufld_model,
        mask_source=rail_mask_source,
    )
    cam_mids, cam_lefts, cam_rights = occupied.mids, occupied.left, occupied.right
    g2 = occupied.stats
    print(
        "rails source",
        occupied.paint_source,
        occupied.left_kind,
        occupied.right_kind,
        "g2 n",
        int(g2.get("n", 0)),
        "ufld_error",
        g2.get("ufld_error", ""),
        "max_dpsi_deg",
        round(float(g2.get("max_dpsi_deg", float("nan"))), 3),
        "max_abs_kappa",
        round(float(g2.get("max_abs_kappa", float("nan"))), 5),
        "max_abs_dkappa",
        round(float(g2.get("max_abs_dkappa", float("nan"))), 5),
    )

    def _rail_hits(xs: np.ndarray, paint: np.ndarray, window: int = 6) -> tuple[int, int]:
        hits = 0
        n = 0
        _h, width = paint.shape
        for y, x in enumerate(xs):
            if not np.isfinite(x):
                continue
            n += 1
            a = int(np.clip(round(float(x)) - window, 0, width - 1))
            b = int(np.clip(round(float(x)) + window + 1, 0, width))
            if paint[y, a:b].any():
                hits += 1
        return hits, n

    near = slice(int(round(0.70 * img_h)), img_h)
    left_h, left_n = _rail_hits(cam_lefts, lane)
    right_h, right_n = _rail_hits(cam_rights, lane)
    near_left_h, near_left_n = _rail_hits(cam_lefts[near], lane[near])
    print(
        "rail hits left",
        f"{left_h}/{left_n}",
        "right",
        f"{right_h}/{right_n}",
        "near_left",
        f"{near_left_h}/{near_left_n}",
        "paint_source",
        occupied.paint_source,
    )
    bev_mids = extend_mids_toward_ego(bev_mids, ego_col=ego_col, hood_rows=4)
    bev_mids = smooth_mids(bev_mids, window=21, passes=3, max_du=1.5)
    cam_primary = list(occupied.points)
    if cam_primary:
        walked = np.full(img_h, np.nan, dtype=np.float64)
        for u, v in cam_primary:
            walked[int(np.clip(round(v), 0, img_h - 1))] = float(u)
        fig18_g2 = heading_curvature_stats(walked)
        print(
            "fig18 g2 n",
            int(fig18_g2["n"]),
            "max_dpsi_deg",
            round(fig18_g2["max_dpsi_deg"], 3),
            "max_abs_kappa",
            round(fig18_g2["max_abs_kappa"], 5),
            "max_abs_dkappa",
            round(fig18_g2["max_abs_dkappa"], 5),
        )
    bev_points: list[tuple[float, float]] = []
    for row in range(bev_h - 1, -1, -1):
        if not np.isfinite(bev_mids[row]):
            continue
        bev_points.append((float(bev_mids[row]), float(row)))
    if len(cam_primary) >= 3:
        pts = [
            GuidancePoint(x=col / bev_w, y=1.0 - (row + 0.5) / bev_h, half_width=0.05)
            for col, row in bev_points
        ]
        primary = GuidancePath(
            status=PATH_STATUS_OK if pts else PATH_STATUS_INSUFFICIENT,
            coverage=1.0 if pts else 0.0,
            lines=[GuidanceLine(points=pts, confidence=1.0, kind="primary")] if pts else [],
            source="occupied_lane_follow",
        )
    else:
        primary = straight_occupied_centerline(free, bev_walls, ego_col=ego_col)
        if not cam_primary:
            cam_primary = []
    structure_path = centerline_from_mask(structure, samples=24, horizon=0.88, source="layer_b_structure")
    hole_or_obs = filled | blocked
    _attach_risk(structure_path, hole_or_obs, bev_w, bev_h)
    print(
        "primary",
        primary.status,
        primary.coverage,
        "ego_col",
        ego_col,
        "ego_cells",
        int(ego_free.sum()),
        "structure",
        structure_path.status,
        structure_path.coverage,
    )
    if primary.lines:
        xs_m = [round(BEV_X_MIN + p.x * bev_w * BEV_RES, 2) for p in primary.lines[0].points]
        print("primary x_m", xs_m[:8], "...", xs_m[-4:])
    if cam_primary:
        print("fig18 u range", round(cam_primary[0][0], 1), round(cam_primary[-1][0], 1),
              "v range", round(cam_primary[0][1], 1), round(cam_primary[-1][1], 1))

    dual = np.array(st)
    img17 = Image.fromarray(dual)
    draw = ImageDraw.Draw(img17)
    _draw_metric_grid(draw, bev_w, bev_h)
    _draw_polyline(draw, _centerline_pixels(structure_path, bev_w, bev_h), (255, 255, 255), width=5, dashed=True, risk=_risk_indices(structure_path, hole_or_obs, bev_w, bev_h))
    _draw_polyline(draw, _centerline_pixels(primary, bev_w, bev_h), PRIMARY, width=5, dashed=False)
    _caption(img17, f"17  Dual lines  非用户画面  {source}").save(out / "17-bev-dual-lines.jpg", quality=92)

    # --- 18 product preview on camera (1:1). Yellow = all quality lane rails. ---
    overlay = rgb.copy()
    img_h, img_w = overlay.shape[:2]
    cam = Image.fromarray(overlay)
    draw = ImageDraw.Draw(cam)
    # Yellow = teacher paint polylines + occupied curb. Do not replace a long
    # paint rail with a tracking stub (01TP collapsed to 1–11 points).
    yellow_rails = list(occupied.paint_rails)
    if occupied.left_kind == "curb":
        yellow_rails.append(cam_lefts)
    if occupied.right_kind == "curb":
        yellow_rails.append(cam_rights)
    if not yellow_rails:
        yellow_rails = [cam_lefts, cam_rights]
    for rail in yellow_rails:
        _draw_polyline(draw, rail_to_points(rail), LANE_YELLOW, width=2, dashed=False, cap=False)

    def _line_to_camera(path) -> list[tuple[float, float]]:
        pixels = []
        for bev_col, bev_row in _centerline_pixels(path, bev_w, bev_h):
            x_m, z_m = _bev_xy_to_xz(bev_col, bev_row)
            cam_u, cam_v, good = bev._uv_from_ground(
                np.array(x_m), np.array(z_m), fx, fy, cx, cy, CAM_H_M, pitch
            )
            if bool(np.asarray(good).reshape(-1)[0]) and np.isfinite(cam_u) and np.isfinite(cam_v):
                pixels.append((float(cam_u), float(cam_v)))
        return pixels

    primary_px = cam_primary if cam_primary else _line_to_camera(primary)
    print("fig18 primary camera points", len(primary_px))
    # White halo so the line still reads on asphalt; no red, no structure dash.
    _draw_polyline(draw, primary_px, (255, 255, 255), width=11, dashed=False)
    _draw_polyline(draw, primary_px, PRIMARY, width=7, dashed=False)
    _legend(
        draw,
        img_h,
        [
            ("黄 = 车道线（涂料 + 占用牙子）", LANE_YELLOW),
            ("蓝实线 = 占用车道中线", PRIMARY),
        ],
        at="top",
    )
    cam_c = _caption(cam, f"18  产品预览  黄=车道线  蓝=占用中线  {occupied.paint_source}  {source}")
    orig = Image.fromarray(rgb)
    orig_c = _caption(orig, f"Original  {stem}")
    height = max(orig_c.height, cam_c.height)
    orig_r = orig_c.resize((int(orig_c.width * height / orig_c.height), height), Image.Resampling.LANCZOS)
    cam_r = cam_c.resize((int(cam_c.width * height / cam_c.height), height), Image.Resampling.LANCZOS)
    pair = Image.new("RGB", (orig_r.width + cam_r.width, height), (12, 12, 16))
    pair.paste(orig_r, (0, 0))
    pair.paste(cam_r, (orig_r.width, 0))
    pair.save(out / "18-persp-structure-reproject.jpg", quality=92)
    if twinlite_lane is not None and twinlite_da is not None:
        raw = rgb.copy().astype(np.float32)
        raw[twinlite_da] = raw[twinlite_da] * 0.55 + np.array([180.0, 40.0, 40.0])
        raw[twinlite_lane] = raw[twinlite_lane] * 0.35 + np.array([40.0, 220.0, 80.0])
        mask_img = Image.fromarray(np.clip(raw, 0, 255).astype(np.uint8))
        draw_c = ImageDraw.Draw(mask_img)
        if primary_px:
            _draw_polyline(draw_c, primary_px, (255, 255, 255), width=11, dashed=False)
            _draw_polyline(draw_c, primary_px, PRIMARY, width=7, dashed=False)
        _legend(
            draw_c,
            img_h,
            [
                ("红 = 可行驶区域", (220, 70, 70)),
                ("绿 = 车道线", (40, 220, 80)),
                ("蓝 = 占用车道引导线", PRIMARY),
            ],
            at="top",
        )
        mask_c = _caption(mask_img, f"18c  TwinLiteNet  红=可行驶  绿=车道  蓝=引导  {stem}")
        mask_c.save(out / "18c-twinlite-masks.jpg", quality=92)
        print("wrote", out / "18c-twinlite-masks.jpg")
    print("wrote", out / "15-bev-visible-freespace.jpg")
    print("wrote", out / "16-bev-structure-fill.jpg")
    print("wrote", out / "17-bev-dual-lines.jpg")
    print("wrote", out / "18-persp-structure-reproject.jpg")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stem", default="0001TP_008430")
    parser.add_argument(
        "--paint-source",
        default="auto",
        choices=("auto", "ufld", "gt", "twinlite"),
        help="auto/ufld uses UFLDv2 when weights exist; twinlite is unlabeled RGB.",
    )
    args = parser.parse_args()
    paint = "auto" if args.paint_source == "ufld" else args.paint_source
    main(stem=args.stem, paint_source=paint)
