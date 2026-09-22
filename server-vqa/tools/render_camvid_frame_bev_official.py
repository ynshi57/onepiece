"""Official CamVid Boujou K/R → God's-eye BEV for one 01TP frame.

01TP's own `0001TP_v01_06690_10380.ban` has fx≈1149 (HFOV≈45°). Other
sequences export a different K (06R0 fx≈1885) — do not mix them.
Boujou t is reconstruction units, not metres; camera height is pinned.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.open_dataset_adapters import (
    CAMVID_LANE_COLORS,
    CAMVID_ROAD_COLORS,
    CAMVID_SIDEWALK_COLORS,
)

STEM = "0001TP_008430"
FRAME_NUM = 8430
CAM_H_M = 1.45  # dashboard camera; Boujou t is not metric
BEV_X_MIN, BEV_X_MAX = -5.0, 5.0
BEV_Z_MIN, BEV_Z_MAX = 4.2, 12.0
BEV_RES = 0.025


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
    ImageDraw.Draw(canvas).text((14, 14), text, fill=(245, 245, 247), font=_font(16))
    return canvas


def _color_mask(label: np.ndarray, colors: set[tuple[int, int, int]]) -> np.ndarray:
    mask = np.zeros(label.shape[:2], dtype=bool)
    for color in colors:
        mask |= np.all(label == np.asarray(color, dtype=np.uint8), axis=-1)
    return mask


_KEY_RE = re.compile(
    r"AddDecompCameraKey \{ Time (?P<time>-?\d+) K \[ (?P<k>[^\]]+) \]  "
    r"R \[ (?P<r>[^\]]+) \]  t \[ (?P<t>[^\]]+) \]"
)


def _floats(blob: str) -> list[float]:
    return [float(x) for x in blob.split()]


def parse_ban_text(text: str) -> dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    out: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for match in _KEY_RE.finditer(text):
        time = int(match.group("time"))
        k = np.asarray(_floats(match.group("k")), dtype=np.float64).reshape(3, 3)
        r = np.asarray(_floats(match.group("r")), dtype=np.float64).reshape(3, 3)
        t = np.asarray(_floats(match.group("t")), dtype=np.float64)
        out[time] = (k, r, t)
    return out


def _ban_token(sequence: str) -> str:
    """0001TP → 01TP, 0006R0 → 06R0, 0016E5 → 16E5."""
    seq = sequence.upper()
    if seq.startswith("000"):
        return seq[2:]
    if seq.startswith("00"):
        return seq[1:]
    return seq


def load_frame_pose(
    calib_dir: Path,
    frame_num: int,
    *,
    sequence: str = "0001TP",
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Return (K, R, t) for an absolute CamVid frame number.

    K is sequence-specific: 01TP Boujou exports fx≈1149; 06R0/16E5 export fx≈1885.
    """
    token = _ban_token(sequence)
    bans = sorted(calib_dir.rglob(f"*{token}*.ban"))
    candidates: list[tuple[int, Path]] = [(_range_start(path.name), path) for path in bans]
    eligible = [c for c in candidates if c[0] <= frame_num]
    if not eligible:
        return None
    start, path = max(eligible, key=lambda c: c[0])
    keys = parse_ban_text(path.read_text(errors="ignore"))
    if frame_num in keys:
        return keys[frame_num]
    return keys.get(frame_num - start)


def _range_start(name: str) -> int:
    numbers = [int(x) for x in re.findall(r"\d+", Path(name).stem)]
    if len(numbers) >= 2:
        return numbers[-2]
    return 0


def pitch_from_r(r: np.ndarray) -> float:
    """Optical-axis pitch vs horizontal, radians, positive = looking down.

    Camera Z in world is the third row of R when Xc = R (Xw - t).
    """
    axis = r[2]
    horiz = math.hypot(float(axis[0]), float(axis[2]))
    return math.atan2(-float(axis[1]), horiz) if horiz > 1e-6 else 0.0


def _ground_from_uv(u, v, fx, fy, cx, cy, h, pitch):
    x_n = (u - cx) / fx
    y_n = (cy - v) / fy
    s, c = math.sin(pitch), math.cos(pitch)
    denom = s - y_n * c
    z = np.full(u.shape, np.nan)
    x = np.full(u.shape, np.nan)
    ok = denom > 1e-4
    z[ok] = h * (y_n[ok] * s + c) / denom[ok]
    z_c = h * s + z * c
    ok &= (z > 0.5) & (z_c > 0.5)
    x[ok] = x_n[ok] * z_c[ok]
    z[~ok] = np.nan
    x[~ok] = np.nan
    return x, z, ok


def _uv_from_ground(x, z, fx, fy, cx, cy, h, pitch):
    s, c = math.sin(pitch), math.cos(pitch)
    y_c = -h * c + z * s
    z_c = h * s + z * c
    ok = z_c > 0.5
    u = np.full(np.asarray(x).shape, np.nan)
    v = np.full(np.asarray(x).shape, np.nan)
    u[ok] = fx * (np.asarray(x)[ok] / z_c[ok]) + cx
    v[ok] = cy - fy * (y_c[ok] / z_c[ok])
    return u, v, ok


def _warp(src, u, v, valid):
    h, w = src.shape[:2]
    ui = np.rint(u).astype(np.int32)
    vi = np.rint(v).astype(np.int32)
    inside = valid & (ui >= 0) & (ui < w) & (vi >= 0) & (vi < h)
    if src.ndim == 2:
        out = np.zeros(u.shape, dtype=src.dtype)
        out[inside] = src[vi[inside], ui[inside]]
        return out
    out = np.zeros(u.shape + (src.shape[2],), dtype=src.dtype)
    out[inside] = src[vi[inside], ui[inside]]
    return out


def _lowest_in_column(mask: np.ndarray) -> np.ndarray:
    """One ground-contact pixel per image column: the lowest obstacle pixel."""
    rows = np.arange(mask.shape[0])[:, None]
    lowest = np.where(mask, rows, -1).max(axis=0)
    contact = np.zeros_like(mask)
    cols = np.nonzero(lowest >= 0)[0]
    contact[lowest[cols], cols] = True
    return contact


def _paint_disks(god: np.ndarray, xs: np.ndarray, zs: np.ndarray, color: tuple[int, int, int], radius: int) -> None:
    height, width = god.shape[:2]
    cols = np.rint((xs - BEV_X_MIN) / BEV_RES).astype(np.int32)
    rows = np.rint((BEV_Z_MAX - zs) / BEV_RES).astype(np.int32)
    inside = (cols >= 0) & (cols < width) & (rows >= 0) & (rows < height)
    cols, rows = cols[inside], rows[inside]
    for row, col in zip(rows, cols, strict=True):
        r0, r1 = max(0, row - radius), min(height, row + radius + 1)
        c0, c1 = max(0, col - radius), min(width, col + radius + 1)
        god[r0:r1, c0:c1] = color


def _unproject_mask(mask: np.ndarray, fx, fy, cx, cy, pitch, min_v: float):
    vs, us = np.nonzero(mask)
    keep = vs.astype(np.float64) >= min_v
    if not np.any(keep):
        return np.zeros(0), np.zeros(0)
    xs, zs, ok = _ground_from_uv(
        us[keep].astype(np.float64), vs[keep].astype(np.float64), fx, fy, cx, cy, CAM_H_M, pitch
    )
    ok &= np.isfinite(xs) & np.isfinite(zs)
    return xs[ok], zs[ok]


# Bottom-contact colours: cars red, people pink, bikes orange, static gray.
_OBS_LAYERS = (
    ({(64, 0, 128), (64, 128, 192), (64, 0, 64)}, (255, 69, 58)),   # car / SUV / truck
    ({(64, 64, 0), (192, 128, 64)}, (255, 45, 85)),                 # pedestrian / child
    ({(0, 128, 192), (192, 0, 192)}, (255, 159, 10)),               # bicyclist / scooter
    ({(192, 192, 128), (64, 64, 128), (64, 128, 128), (64, 128, 64), (0, 0, 64), (64, 0, 192), (128, 64, 64)}, (142, 142, 147)),
)


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    calib = repo / "dataset" / "camvid" / "calib"
    out = repo / "docs" / "model-lab" / "figures" / "camvid-0001TP_008430"
    out.mkdir(parents=True, exist_ok=True)
    rgb = np.asarray(Image.open(repo / "dataset" / "camvid" / "CamVid_RGB" / f"{STEM}.png").convert("RGB"))
    label = np.asarray(Image.open(repo / "dataset" / "camvid" / "CamVid_Label" / f"{STEM}_L.png").convert("RGB"))
    pose = load_frame_pose(calib, FRAME_NUM)
    if pose is None:
        raise FileNotFoundError(f"no 01TP Boujou pose for frame {FRAME_NUM} under {calib}")
    k, r, t = pose
    fx, fy, cx, cy = float(k[0, 0]), float(k[1, 1]), float(k[0, 2]), float(k[1, 2])
    pitch = pitch_from_r(r)
    source = f"official 01TP  fx={fx:.0f}  pitch={math.degrees(pitch):.1f}°  h={CAM_H_M}m pinned"
    print("K fx,fy,cx,cy", fx, fy, cx, cy)
    print("R pitch deg", math.degrees(pitch), "t (Boujou units, not metres)", t)
    horizon_v = cy + fy * math.tan(pitch)
    min_contact_v = horizon_v + 20.0

    bev_w = int(round((BEV_X_MAX - BEV_X_MIN) / BEV_RES))
    bev_h = int(round((BEV_Z_MAX - BEV_Z_MIN) / BEV_RES))
    cols = np.arange(bev_w)
    rows = np.arange(bev_h)
    xs = BEV_X_MIN + (cols + 0.5) * BEV_RES
    zs = BEV_Z_MAX - (rows + 0.5) * BEV_RES
    xx, zz = np.meshgrid(xs, zs)
    u, v, ok = _uv_from_ground(xx, zz, fx, fy, cx, cy, CAM_H_M, pitch)
    road = _color_mask(label, CAMVID_ROAD_COLORS - CAMVID_LANE_COLORS)
    sidewalk = _color_mask(label, CAMVID_SIDEWALK_COLORS)
    lane = _color_mask(label, CAMVID_LANE_COLORS)
    ground = road | sidewalk | lane
    bev_rgb = _warp(rgb, u, v, ok)
    bev_ground = _warp(ground.astype(np.uint8), u, v, ok).astype(bool)
    bev_sidewalk = _warp(sidewalk.astype(np.uint8), u, v, ok).astype(bool)

    gx, gz, gok = _ground_from_uv(
        np.nonzero(ground)[1].astype(np.float64),
        np.nonzero(ground)[0].astype(np.float64),
        fx, fy, cx, cy, CAM_H_M, pitch,
    )
    print("ground Z range m (h-pinned)", float(np.nanmin(gz[gok])), float(np.nanmax(gz[gok])))

    god = np.zeros((bev_h, bev_w, 3), dtype=np.uint8)
    god[:] = (22, 24, 28)
    lit = np.clip(bev_rgb.astype(np.float32) * 1.85 + 18, 0, 255).astype(np.uint8)
    god[bev_ground] = lit[bev_ground]
    sidewalk_pix = bev_sidewalk & bev_ground
    tinted = 0.55 * god[sidewalk_pix].astype(np.float32) + np.array([40.0, 160.0, 80.0])
    god[sidewalk_pix] = np.clip(tinted, 0, 255).astype(np.uint8)
    lane_x, lane_z = _unproject_mask(lane, fx, fy, cx, cy, pitch, min_contact_v)
    _paint_disks(god, lane_x, lane_z, (255, 214, 10), radius=1)
    for colors, paint in _OBS_LAYERS:
        foot = _lowest_in_column(_color_mask(label, colors))
        ox, oz = _unproject_mask(foot, fx, fy, cx, cy, pitch, min_contact_v)
        _paint_disks(god, ox, oz, paint, radius=3)

    img = Image.fromarray(god)
    draw = ImageDraw.Draw(img)
    font = _font(16)
    for metre in range(int(BEV_Z_MIN), int(BEV_Z_MAX) + 1, 4):
        y = (BEV_Z_MAX - metre) / BEV_RES
        draw.line([(0, y), (bev_w, y)], fill=(60, 60, 68), width=1)
        draw.text((8, y + 2), f"{metre}m", fill=(180, 180, 186), font=font)
    x0 = (0.0 - BEV_X_MIN) / BEV_RES
    draw.line([(x0, 0), (x0, bev_h)], fill=(80, 80, 90), width=1)
    draw.rectangle([8, bev_h - 88, 248, bev_h - 8], fill=(12, 12, 16))
    draw.text((16, bev_h - 80), "yellow = lane", fill=(255, 214, 10), font=font)
    draw.text((16, bev_h - 62), "red = car  pink = person", fill=(255, 69, 58), font=font)
    draw.text((16, bev_h - 44), "orange = bike  gray = pole/wall", fill=(255, 159, 10), font=font)
    captioned = _caption(img, f"God-view  {source}")
    captioned.save(out / "12-bev-official-godview.jpg", quality=92)

    rgb_only = np.zeros_like(bev_rgb)
    rgb_only[bev_ground] = lit[bev_ground]
    _caption(Image.fromarray(rgb_only), f"Official-K IPM, ground only  {source}").save(
        out / "13-bev-official-ground-rgb.jpg", quality=92
    )

    persp = Image.open(repo / "docs" / "model-lab" / "figures" / "camvid-0001TP_008430" / "00-rgb.jpg").convert("RGB")
    right = captioned
    left = persp.resize((right.height * persp.width // persp.height, right.height), Image.Resampling.LANCZOS)
    pair = Image.new("RGB", (left.width + right.width, right.height), (12, 12, 16))
    pair.paste(left, (0, 0))
    pair.paste(right, (left.width, 0))
    pair.save(out / "14-persp-vs-official-godview.jpg", quality=92)
    print("wrote", out / "12-bev-official-godview.jpg")


if __name__ == "__main__":
    main()
