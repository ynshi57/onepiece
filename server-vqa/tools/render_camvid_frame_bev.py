"""Pseudo-BEV (assumed extrinsics) for one CamVid drive-test frame.

CamVid has no camera height / pitch / depth. This is a visualization only —
axes are labeled in metres under a fixed guess, not a metric ground truth.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.open_dataset_adapters import CAMVID_ROAD_COLORS

FRAME_ID = "road/0001TP_008430"
STEM = "0001TP_008430"

# Assumed CamVid vehicle camera. Printed on every figure.
CAM_H_M = 1.35
PITCH_DEG = 0.0  # CamVid hood-cam: labeled road on this frame only reaches ~8m
HFOV_DEG = 62.0
BEV_X_MIN, BEV_X_MAX = -5.0, 5.0
BEV_Z_MIN, BEV_Z_MAX = 2.0, 10.0
BEV_RES = 0.025  # assumed metres per pixel


def _font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ):
        try:
            return ImageFont.truetype(path, size, index=0)
        except OSError:
            continue
    return ImageFont.load_default()


def _caption(image: Image.Image, text: str) -> Image.Image:
    canvas = Image.new("RGB", (image.width, image.height + 52), (12, 12, 16))
    canvas.paste(image, (0, 52))
    draw = ImageDraw.Draw(canvas)
    draw.text((14, 12), text, fill=(245, 245, 247), font=_font(22))
    return canvas


def _load_jsonl_row(path: Path, frame_id: str) -> dict:
    stem = frame_id.rsplit("/", 1)[-1]
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if stem not in line:
                continue
            row = json.loads(line)
            if row.get("frame_id") == frame_id:
                return row
    raise FileNotFoundError(f"{frame_id} not in {path}")


def _color_mask(label: np.ndarray, colors: set[tuple[int, int, int]]) -> np.ndarray:
    mask = np.zeros(label.shape[:2], dtype=bool)
    for color in colors:
        mask |= np.all(label == np.asarray(color, dtype=np.uint8), axis=-1)
    return mask


def _intrinsics(width: int, height: int) -> tuple[float, float, float, float]:
    fx = (width / 2.0) / math.tan(math.radians(HFOV_DEG) / 2.0)
    fy = fx
    return fx, fy, width / 2.0, height / 2.0


def _ground_from_uv(
    u: np.ndarray,
    v: np.ndarray,
    fx: float,
    fy: float,
    cx: float,
    cy: float,
    h: float,
    pitch: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Image pixels → ground (X right, Z forward). Invalid samples are nan."""
    x_n = (u - cx) / fx
    y_n = (cy - v) / fy
    s = math.sin(pitch)
    c = math.cos(pitch)
    denom = s - y_n * c
    z = np.full(u.shape, np.nan, dtype=np.float64)
    x = np.full(u.shape, np.nan, dtype=np.float64)
    ok = denom > 1e-4
    z[ok] = h * (y_n[ok] * s + c) / denom[ok]
    z_c = h * s + z * c
    ok &= z > 0.3
    ok &= z_c > 0.3
    x[ok] = x_n[ok] * z_c[ok]
    z[~ok] = np.nan
    x[~ok] = np.nan
    return x, z, ok


def _uv_from_ground(
    x: np.ndarray,
    z: np.ndarray,
    fx: float,
    fy: float,
    cx: float,
    cy: float,
    h: float,
    pitch: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    s = math.sin(pitch)
    c = math.cos(pitch)
    y_c = -h * c + z * s
    z_c = h * s + z * c
    ok = z_c > 0.3
    u = np.full(np.asarray(x).shape, np.nan, dtype=np.float64)
    v = np.full(np.asarray(x).shape, np.nan, dtype=np.float64)
    u[ok] = fx * (np.asarray(x)[ok] / z_c[ok]) + cx
    v[ok] = cy - fy * (y_c[ok] / z_c[ok])
    return u, v, ok


def _bev_axes(bev_w: int, bev_h: int) -> tuple[np.ndarray, np.ndarray]:
    cols = np.arange(bev_w)
    rows = np.arange(bev_h)
    x = BEV_X_MIN + (cols + 0.5) * BEV_RES
    z = BEV_Z_MAX - (rows + 0.5) * BEV_RES
    return np.meshgrid(x, z)


def _warp_to_bev(src: np.ndarray, u: np.ndarray, v: np.ndarray, valid: np.ndarray) -> np.ndarray:
    height, width = src.shape[:2]
    ui = np.rint(u).astype(np.int32)
    vi = np.rint(v).astype(np.int32)
    inside = valid & (ui >= 0) & (ui < width) & (vi >= 0) & (vi < height)
    if src.ndim == 2:
        out = np.zeros(u.shape, dtype=src.dtype)
        out[inside] = src[vi[inside], ui[inside]]
        return out
    out = np.zeros(u.shape + (src.shape[2],), dtype=src.dtype)
    out[inside] = src[vi[inside], ui[inside]]
    return out


def _vision_points(path: dict | None) -> list[tuple[float, float]]:
    if not path or path.get("status") != "ok":
        return []
    lines = path.get("lines") or []
    if not lines:
        return []
    return [(float(p["x"]), float(p["y"])) for p in lines[0].get("points") or []]


def _project_vision_line(
    points: list[tuple[float, float]],
    width: int,
    height: int,
    fx: float,
    fy: float,
    cx: float,
    cy: float,
    pitch: float,
) -> list[tuple[float, float]]:
    if not points:
        return []
    u = np.array([p[0] * width for p in points], dtype=np.float64)
    v = np.array([(1.0 - p[1]) * height for p in points], dtype=np.float64)
    x, z, ok = _ground_from_uv(u, v, fx, fy, cx, cy, CAM_H_M, pitch)
    out = []
    for i, good in enumerate(ok):
        if not good or not np.isfinite(x[i]) or not np.isfinite(z[i]):
            continue
        px = (x[i] - BEV_X_MIN) / BEV_RES
        py = (BEV_Z_MAX - z[i]) / BEV_RES
        out.append((px, py))
    return out


def _draw_polyline(draw: ImageDraw.ImageDraw, points: list[tuple[float, float]], color, width: int, dashed: bool = False) -> None:
    if len(points) < 2:
        return
    if dashed:
        for index in range(len(points) - 1):
            if index % 2 == 0:
                draw.line([points[index], points[index + 1]], fill=color, width=width)
    else:
        draw.line(points, fill=color, width=width)
    x, y = points[0]
    draw.ellipse([x - 6, y - 6, x + 6, y + 6], fill=color)


def _draw_grid(draw: ImageDraw.ImageDraw, bev_w: int, bev_h: int, font) -> None:
    for metre in range(int(math.ceil(BEV_Z_MIN)), int(BEV_Z_MAX) + 1, 2):
        y = (BEV_Z_MAX - metre) / BEV_RES
        draw.line([(0, y), (bev_w, y)], fill=(55, 55, 62), width=1)
        draw.text((8, y + 2), f"{metre}m (assumed)", fill=(170, 170, 176), font=font)
    x0 = (0.0 - BEV_X_MIN) / BEV_RES
    draw.line([(x0, 0), (x0, bev_h)], fill=(80, 80, 88), width=1)


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    out = repo / "docs" / "model-lab" / "figures" / "camvid-0001TP_008430"
    out.mkdir(parents=True, exist_ok=True)
    rgb = np.asarray(Image.open(repo / "dataset" / "camvid" / "CamVid_RGB" / f"{STEM}.png").convert("RGB"))
    label = np.asarray(Image.open(repo / "dataset" / "camvid" / "CamVid_Label" / f"{STEM}_L.png").convert("RGB"))
    height, width = rgb.shape[:2]
    pred = _load_jsonl_row(repo / "docs" / "datasets" / "camvid-ios-mc5-drive.jsonl", FRAME_ID)
    truth = _load_jsonl_row(repo / "docs" / "datasets" / "camvid-manifest-drive-test.jsonl", FRAME_ID)
    road = _color_mask(label, CAMVID_ROAD_COLORS)
    grid = pred["traversable_grid"]
    cells = np.asarray(grid["cells"], dtype=np.uint8).reshape(int(grid["rows"]), int(grid["cols"]))
    pred_mask = np.asarray(Image.fromarray(cells * 255, mode="L").resize((width, height), Image.NEAREST)) > 127

    fx, fy, cx, cy = _intrinsics(width, height)
    pitch = math.radians(PITCH_DEG)
    bev_w = int(round((BEV_X_MAX - BEV_X_MIN) / BEV_RES))
    bev_h = int(round((BEV_Z_MAX - BEV_Z_MIN) / BEV_RES))
    xs, zs = _bev_axes(bev_w, bev_h)
    u, v, ok = _uv_from_ground(xs, zs, fx, fy, cx, cy, CAM_H_M, pitch)
    bev_rgb = _warp_to_bev(rgb, u, v, ok)
    bev_road = _warp_to_bev(road.astype(np.uint8), u, v, ok).astype(bool)
    bev_pred = _warp_to_bev(pred_mask.astype(np.uint8), u, v, ok).astype(bool)

    note = _font(16)
    title = _font(20)

    # Occupancy BEV (what the product would use). RGB IPM of sky is not useful.
    occupancy = np.zeros((bev_h, bev_w, 3), dtype=np.uint8)
    occupancy[:] = (18, 18, 22)
    occupancy[bev_road & ~bev_pred] = (48, 209, 88)
    occupancy[bev_pred & ~bev_road] = (191, 90, 242)
    occupancy[bev_road & bev_pred] = (255, 214, 10)
    # Keep a faint RGB hint only on ground cells so the street is recognizable.
    hint = bev_rgb.astype(np.float32)
    ground = bev_road | bev_pred
    occupancy_f = occupancy.astype(np.float32)
    occupancy_f[ground] = occupancy_f[ground] * 0.72 + hint[ground] * 0.28
    occupancy = occupancy_f.astype(np.uint8)

    rgb_img = Image.fromarray(np.where(ok[..., None], bev_rgb, 12).astype(np.uint8))
    draw = ImageDraw.Draw(rgb_img)
    _draw_grid(draw, bev_w, bev_h, note)
    _caption(rgb_img, "BEV · warped RGB  (assumed h=1.35m pitch=0, NOT metric)").save(
        out / "08-bev-rgb.jpg", quality=90
    )

    compare_img = Image.fromarray(occupancy)
    draw = ImageDraw.Draw(compare_img)
    _draw_grid(draw, bev_w, bev_h, note)
    _caption(compare_img, "BEV occupancy  yellow=overlap  green=GT  purple=pred  NOT metric").save(
        out / "09-bev-regions.jpg", quality=90
    )

    lines = Image.fromarray(occupancy.copy())
    draw = ImageDraw.Draw(lines)
    _draw_grid(draw, bev_w, bev_h, note)
    pred_pts = _project_vision_line(_vision_points(pred.get("guidance_path")), width, height, fx, fy, cx, cy, pitch)
    _draw_polyline(draw, pred_pts, (191, 90, 242), 5)
    for obj in pred.get("objects") or []:
        if obj.get("kind") not in {"car", "person"}:
            continue
        box = obj.get("box") or {}
        # Bottom-center of the box in vision coords (y-up).
        u_b = (float(box.get("x", 0.0)) + float(box.get("w", 0.0)) / 2.0) * width
        v_b = (1.0 - float(box.get("y", 0.0))) * height
        x, z, ok_pt = _ground_from_uv(
            np.array([u_b]), np.array([v_b]), fx, fy, cx, cy, CAM_H_M, pitch
        )
        if not bool(ok_pt[0]):
            continue
        px = (float(x[0]) - BEV_X_MIN) / BEV_RES
        py = (BEV_Z_MAX - float(z[0])) / BEV_RES
        color = (255, 69, 58) if obj.get("kind") == "car" else (255, 214, 10)
        r = 9 if obj.get("kind") == "person" else 14
        draw.ellipse([px - r, py - r, px + r, py + r], outline=color, width=3)
        draw.text((px + r + 2, py - 8), str(obj.get("kind")), fill=color, font=note)
    draw.text((12, bev_h - 28), "green dashed=GT   purple=pred   dots=YOLO foot", fill=(255, 255, 255), font=title)
    _caption(lines, "BEV · lines + obstacle feet  (pseudo-metric, assumed camera)").save(
        out / "10-bev-lines.jpg", quality=90
    )

    persp = Image.open(out / "07-compose.jpg") if (out / "07-compose.jpg").exists() else Image.fromarray(rgb)
    # Side-by-side: camera view vs BEV lines
    left = Image.fromarray(rgb).resize((480, 360))
    right = lines.resize((480, 360))
    pair = Image.new("RGB", (960, 360), (12, 12, 16))
    pair.paste(left, (0, 0))
    pair.paste(right, (480, 0))
    _caption(pair, "camera view  vs  assumed-extrinsic BEV  (not a metric score)").save(
        out / "11-persp-vs-bev.jpg", quality=90
    )

    print("assumed", {"h_m": CAM_H_M, "pitch_deg": PITCH_DEG, "hfov_deg": HFOV_DEG, "res_m": BEV_RES})
    print("bev size", bev_w, bev_h)
    print("gt points in bev", len(gt_pts), "pred points in bev", len(pred_pts))
    print("wrote", out)


if __name__ == "__main__":
    main()
