"""TwinLiteNet RGB overlay for diagnostic review (not the iPhone default)."""
from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.lane_rails import occupied_lane_rails, rail_to_points
from app.twinlite_net import LAST_ERROR, default_twinlite_weights, infer_twinlite_masks

PRIMARY = (10, 132, 255)
DA_TINT = np.array([180.0, 40.0, 40.0])
LANE_TINT = np.array([40.0, 220.0, 80.0])
_CACHE_LOCK = threading.Lock()


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _draw_polyline(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    color: tuple[int, int, int],
    *,
    width: int,
    cap: bool = True,
) -> None:
    if len(points) < 2:
        return
    for index in range(len(points) - 1):
        draw.line([points[index], points[index + 1]], fill=color, width=width)
    if cap:
        x, y = points[0]
        draw.ellipse([x - 5, y - 5, x + 5, y + 5], fill=color)


def compose_twinlite_overlay(
    rgb: np.ndarray,
    *,
    obstacle: np.ndarray | None = None,
    with_legend: bool = True,
) -> Image.Image:
    """Red = predicted drivable area, green = predicted lanes, blue = G2."""
    masks = infer_twinlite_masks(rgb)
    if masks is None:
        raise RuntimeError(LAST_ERROR or "twinlite-failed")
    lane, da = masks
    occupied = occupied_lane_rails(
        lane,
        da,
        sidewalk=None,
        ego_col=int(rgb.shape[1] // 2),
        obstacle=obstacle,
        rgb=rgb,
        ufld_model=False,
        mask_source="twinlite",
    )
    raw = np.asarray(rgb).astype(np.float32).copy()
    raw[da] = raw[da] * 0.55 + DA_TINT
    raw[lane] = raw[lane] * 0.35 + LANE_TINT
    image = Image.fromarray(np.clip(raw, 0, 255).astype(np.uint8))
    draw = ImageDraw.Draw(image)
    points = list(occupied.points)
    if len(points) < 2:
        points = rail_to_points(occupied.mids)
    if len(points) >= 2:
        _draw_polyline(draw, points, (255, 255, 255), width=11, cap=False)
        _draw_polyline(draw, points, PRIMARY, width=7, cap=True)
    if with_legend:
        draw.rectangle([8, 8, 360, 68], fill=(12, 12, 16))
        font = _font(15)
        draw.text((16, 14), "红 = 可行驶区域", fill=(220, 70, 70), font=font)
        draw.text((16, 32), "绿 = 车道线", fill=(40, 220, 80), font=font)
        draw.text((16, 50), "蓝 = 占用车道引导线", fill=PRIMARY, font=font)
    return image


def preview_cache_dir() -> Path:
    return Path.home() / ".cache" / "vqasee" / "twinlite-preview"


def cached_overlay_path(stem: str) -> Path:
    weights = default_twinlite_weights()
    stamp = "noweights"
    if weights is not None:
        stamp = str(int(weights.stat().st_mtime))
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in stem)
    return preview_cache_dir() / f"{safe}-{stamp}.jpg"


def write_cached_overlay(
    stem: str,
    rgb: np.ndarray,
    *,
    obstacle: np.ndarray | None = None,
) -> Path:
    dest = cached_overlay_path(stem)
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    with _CACHE_LOCK:
        if dest.is_file() and dest.stat().st_size > 0:
            return dest
        image = compose_twinlite_overlay(rgb, obstacle=obstacle)
        tmp = dest.with_suffix(".tmp.jpg")
        image.save(tmp, format="JPEG", quality=88)
        tmp.replace(dest)
    return dest
