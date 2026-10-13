"""Four-frame comic: how a keyframe JPEG is stuck onto an observed plane."""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "server-vqa"))
from app.spatial_reconstruction import camera, project  # noqa: E402

FIGURE = ROOT / "docs/evolution/figures/2026-10-12-projective-texture/look-gate.png"
W, H = 720, 540
K = [500.0, 0, 0, 0, 500.0, 0, 360.0, 270.0, 1.0]
IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
# Large enough that 上/下/左/右 sit inside the projected quad.
WALL = [(-0.55, -0.40, -1.0), (0.55, -0.40, -1.0), (0.55, 0.40, -1.0), (-0.55, 0.40, -1.0)]


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


def room_photo() -> Image.Image:
    """A fake indoor photo: window up, door left, floor down. Orientation is the point."""
    image = Image.new("RGB", (W, H), (232, 224, 208))
    d = ImageDraw.Draw(image)
    d.rectangle([0, int(H * 0.72), W, H], fill=(168, 132, 96))
    d.rectangle([int(W * 0.28), 36, int(W * 0.78), int(H * 0.42)], fill=(120, 168, 210), outline=(90, 90, 90), width=8)
    d.rectangle([int(W * 0.46), int(H * 0.42) - 8, int(W * 0.60), int(H * 0.42)], fill=(90, 90, 90))
    d.rectangle([28, int(H * 0.22), int(W * 0.22), int(H * 0.72)], fill=(118, 72, 48), outline=(70, 40, 24), width=6)
    d.ellipse([int(W * 0.14) - 8, int(H * 0.46), int(W * 0.14) + 8, int(H * 0.50)], fill=(210, 180, 80))
    d.ellipse([int(W * 0.82), 48, int(W * 0.94), 120], fill=(40, 40, 40), outline=(20, 20, 20), width=4)
    d.line([(int(W * 0.88), 84), (int(W * 0.88), 58)], fill=(230, 230, 230), width=3)
    d.polygon([(int(W * 0.70), int(H * 0.72)), (int(W * 0.86), int(H * 0.72)), (int(W * 0.78), int(H * 0.48))], fill=(46, 120, 62))
    f = font(34)
    d.text((W / 2, 8), "上", fill=(200, 30, 30), font=f, anchor="mt")
    d.text((W / 2, H - 8), "下", fill=(20, 110, 50), font=f, anchor="mb")
    d.text((8, H / 2), "左", fill=(200, 140, 20), font=f, anchor="lm")
    d.text((W - 8, H / 2), "右", fill=(200, 140, 20), font=f, anchor="rm")
    return image


def cam():
    rotation = np.asarray(IDENTITY, dtype=float).reshape(4, 4, order="F")
    return camera({
        "intrinsics": K, "cameraToWorld": IDENTITY, "time": 1.0, "width": W, "height": H,
        "pose": {"time": 1.0, "epoch": 1,
                 "position": dict(zip("xyz", rotation[:3, 3].tolist())),
                 "forward": dict(zip("xyz", (-rotation[:3, 2]).tolist()))},
    })


def jpeg_xy(uvs):
    return [(uv[0] * (W - 1), (1 - uv[1]) * (H - 1)) for uv in uvs]


def project_uvs(c, vertices):
    uvs = []
    for vertex in vertices:
        pixel, depth = project(c, np.asarray(vertex, dtype=float))
        if depth <= 0.05:
            raise ValueError("behind")
        uvs.append((float(pixel[0] / c.width), 1.0 - float(pixel[1] / c.height)))
    return uvs


def warp(source, dest, uvs, quad):
    src = np.float32(jpeg_xy(uvs))
    dst = np.float32(quad)
    matrix = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(np.asarray(source), matrix, dest.size)
    mask = np.zeros(dest.size[::-1], np.uint8)
    cv2.fillConvexPoly(mask, dst.astype(np.int32), 255)
    base = np.asarray(dest).copy()
    base[mask > 0] = warped[mask > 0]
    dest.paste(Image.fromarray(base))
    ImageDraw.Draw(dest).line([tuple(p) for p in quad] + [tuple(quad[0])], fill=(255, 255, 255), width=4)


def banner(step: str, title: str, body: Image.Image) -> Image.Image:
    canvas = Image.new("RGB", (body.width, body.height + 88), (18, 18, 20))
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, 0, body.width, 88], fill=(28, 28, 32))
    d.text((16, 14), step, fill=(255, 196, 72), font=font(20))
    d.text((16, 44), title, fill=(245, 245, 245), font=font(26))
    canvas.paste(body, (0, 88))
    return canvas


def frame_camera_and_wall() -> Image.Image:
    image = Image.new("RGB", (W, H), (36, 38, 44))
    d = ImageDraw.Draw(image)
    d.rectangle([0, int(H * 0.78), W, H], fill=(48, 52, 58))
    wall = [(420, 80), (680, 80), (680, 400), (420, 400)]
    d.polygon(wall, fill=(232, 224, 208), outline=(255, 255, 255), width=3)
    d.rectangle([470, 110, 630, 220], fill=(120, 168, 210), outline=(90, 90, 90), width=4)
    d.text((550, 96), "上", fill=(200, 30, 30), font=font(22), anchor="mt")
    d.polygon([(80, 250), (80, 310), (150, 280)], fill=(80, 180, 220), outline=(255, 255, 255), width=2)
    d.text((80, 330), "手机", fill=(220, 220, 220), font=font(22))
    for y in (110, 200, 300, 380):
        d.line([(150, 280), (420, y)], fill=(255, 90, 90), width=2)
    d.text((200, 40), "对着墙拍一张", fill=(240, 240, 240), font=font(24))
    return image


def frame_photo(photo, uvs) -> Image.Image:
    marked = photo.copy()
    d = ImageDraw.Draw(marked)
    pts = jpeg_xy(uvs)
    d.line(pts + pts[:1], fill=(255, 40, 40), width=5)
    for i, p in enumerate(pts):
        d.ellipse([p[0] - 7, p[1] - 7, p[0] + 7, p[1] + 7], fill=(255, 40, 40))
        d.text((p[0] + 10, p[1] - 18), str(i), fill=(255, 40, 40), font=font(22))
    d.text((24, 70), "红框 = 这面墙落到照片上的位置", fill=(40, 20, 20), font=font(22))
    return marked


def frame_oblique(photo, uvs) -> Image.Image:
    dest = Image.new("RGB", (W, H), (36, 38, 44))
    ImageDraw.Draw(dest).rectangle([0, int(H * 0.78), W, H], fill=(48, 52, 58))
    quad = [(160, 400), (560, 400), (670, 90), (70, 90)]
    warp(photo, dest, uvs, quad)
    ImageDraw.Draw(dest).text((24, 460), "走到旁边看：窗还在上，门还在左", fill=(240, 240, 240), font=font(22))
    return dest


def frame_behind() -> Image.Image:
    image = Image.new("RGB", (W, H), (36, 38, 44))
    d = ImageDraw.Draw(image)
    d.polygon([(80, 250), (80, 310), (150, 280)], fill=(80, 180, 220), outline=(255, 255, 255), width=2)
    d.text((60, 330), "手机朝这边", fill=(220, 220, 220), font=font(20))
    d.line([(40, 280), (200, 280)], fill=(80, 180, 220), width=3)
    d.polygon([(430, 90), (680, 90), (680, 400), (430, 400)], outline=(160, 110, 220), width=4)
    d.text((470, 220), "墙在身后\n没拍到\n不贴照片", fill=(210, 190, 230), font=font(26), align="center")
    return image


def main():
    photo = room_photo()
    c = cam()
    uvs = project_uvs(c, WALL)
    center = np.mean(uvs, axis=0)
    assert abs(center[0] - 0.5) < 0.05 and abs(center[1] - 0.5) < 0.05, center

    p1 = banner("第 1 格 · What", "先拍一张。墙在相机前面。", frame_camera_and_wall())
    p2 = banner("第 2 格 · How", "墙上四个角落到这张照片里。", frame_photo(photo, uvs))
    p3 = banner("第 3 格 · How", "回头斜着看，墙上贴的还是这张照片。", frame_oblique(photo, uvs))
    p4 = banner("第 4 格 · Why 有拒绝", "拍不到的墙不贴，避免贴错房间。", frame_behind())
    sheet = Image.new("RGB", (p1.width * 2, p1.height * 2), (8, 8, 10))
    sheet.paste(p1, (0, 0))
    sheet.paste(p2, (p1.width, 0))
    sheet.paste(p3, (0, p1.height))
    sheet.paste(p4, (p1.width, p1.height))
    FIGURE.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(FIGURE)
    print(FIGURE, "center", center.tolist())


if __name__ == "__main__":
    main()
