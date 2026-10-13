"""Simulate raw ARKit plane vs projective photo wrap using a real indoor crop."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / ".cursor-tmp-cap-2.png"
OUT = ROOT / "docs/evolution/figures/2026-10-12-projective-texture/plane-vs-texture.png"
W, H = 640, 480


def font(size: int):
    for path in (
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def crop_door() -> Image.Image:
    sheet = Image.open(SRC).convert("RGB")
    width, height = sheet.size
    row = height / 6
    # Contact-sheet row for frame 210, truth column.
    box = (0, int(row) + 20, width // 3, int(row * 2))
    return sheet.crop(box).resize((W, H), Image.Resampling.LANCZOS)


def banner(step: str, title: str, body: Image.Image) -> Image.Image:
    canvas = Image.new("RGB", (body.width, body.height + 92), (16, 16, 18))
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, 0, body.width, 92], fill=(28, 28, 32))
    d.text((16, 14), step, fill=(255, 196, 72), font=font(20))
    d.text((16, 46), title, fill=(245, 245, 245), font=font(26))
    canvas.paste(body, (0, 92))
    return canvas


def room() -> Image.Image:
    image = Image.new("RGB", (W, H), (38, 40, 46))
    ImageDraw.Draw(image).rectangle([0, 390, W, H], fill=(52, 54, 60))
    return image


def photo_quad():
    # Photo pixels, top-left origin: TL, TR, BR, BL.
    return [(36, 44), (604, 44), (604, 430), (36, 430)]


def wall_quad():
    # Same winding in the 3D view: far-left, far-right, near-right, near-left.
    return [(50, 70), (610, 70), (520, 400), (90, 400)]


def draw_wire(image: Image.Image, pts, color=(210, 130, 255), width=4):
    ImageDraw.Draw(image).line(pts + pts[:1], fill=color, width=width)


def photo_with_plane_outline(photo: Image.Image) -> Image.Image:
    marked = photo.copy()
    d = ImageDraw.Draw(marked, "RGBA")
    inset = photo_quad()
    d.polygon(inset, fill=(160, 80, 220, 40), outline=(210, 130, 255, 255))
    draw_wire(marked, inset, (210, 130, 255), 5)
    d.text((48, 54), "紫框：系统认为这是一面墙", fill=(40, 20, 40), font=font(22))
    return marked


def raw_plane() -> Image.Image:
    image = room()
    pts = wall_quad()
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(overlay).polygon(pts, fill=(160, 90, 220, 70))
    image = image.convert("RGBA")
    image.alpha_composite(overlay)
    image = image.convert("RGB")
    draw_wire(image, pts)
    ImageDraw.Draw(image).text((70, 430), "只有形状，像一块透明玻璃", fill=(230, 220, 240), font=font(22))
    return image


def textured(photo: Image.Image) -> Image.Image:
    image = room()
    pts = np.float32(wall_quad())
    src = np.float32(photo_quad())
    matrix = cv2.getPerspectiveTransform(src, pts)
    warped = cv2.warpPerspective(np.asarray(photo), matrix, (W, H))
    mask = np.zeros((H, W), np.uint8)
    cv2.fillConvexPoly(mask, pts.astype(np.int32), 255)
    base = np.asarray(image).copy()
    base[mask > 0] = warped[mask > 0]
    image = Image.fromarray(base)
    draw_wire(image, [tuple(map(int, p)) for p in pts])
    ImageDraw.Draw(image).text((70, 430), "玻璃上糊了刚才那张照片", fill=(240, 240, 240), font=font(22))
    return image


def main():
    photo = crop_door()
    p1 = banner("第 1 格 · 照片", "手机拍到的真实画面（白墙 + 门）", photo_with_plane_outline(photo))
    p2 = banner("第 2 格 · 原始平面", "系统只记下这块形状，还没有颜色", raw_plane())
    p3 = banner("第 3 格 · 贴图", "把第 1 格的照片按透视贴到这块形状上", textured(photo))
    sheet = Image.new("RGB", (p1.width * 3, p1.height), (8, 8, 10))
    sheet.paste(p1, (0, 0))
    sheet.paste(p2, (p1.width, 0))
    sheet.paste(p3, (p1.width * 2, 0))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
