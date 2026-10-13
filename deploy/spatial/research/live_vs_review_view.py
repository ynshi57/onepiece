"""Live scan vs review orbit vs teaching figure. Teaching used the review camera on purpose."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / ".cursor-tmp-cap-2.png"
OUT = ROOT / "docs/evolution/figures/2026-10-12-projective-texture/live-vs-review.png"
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
    return sheet.crop((0, int(row) + 20, width // 3, int(row * 2))).resize((W, H), Image.Resampling.LANCZOS)


def banner(step: str, title: str, body: Image.Image) -> Image.Image:
    canvas = Image.new("RGB", (body.width, body.height + 92), (16, 16, 18))
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, 0, body.width, 92], fill=(28, 28, 32))
    d.text((16, 14), step, fill=(255, 196, 72), font=font(20))
    d.text((16, 46), title, fill=(245, 245, 245), font=font(26))
    canvas.paste(body, (0, 92))
    return canvas


def live(photo: Image.Image) -> Image.Image:
    image = photo.copy().convert("RGBA")
    box = [(40, 36), (600, 36), (600, 444), (40, 444)]
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(overlay).polygon(box, fill=(160, 80, 220, 64))
    image.alpha_composite(overlay)
    d = ImageDraw.Draw(image)
    d.line(box + box[:1], fill=(210, 130, 255), width=4)
    d.rectangle([16, 400, 624, 468], fill=(16, 16, 18))
    d.text((28, 412), "扫描时：实景还在，框贴在你看见的墙上", fill=(245, 245, 245), font=font(22))
    d.text((28, 440), "正对墙时看起来就是矩形，不是空房间里的梯形", fill=(210, 210, 220), font=font(20))
    return image.convert("RGB")


def review(photo: Image.Image) -> Image.Image:
    image = Image.new("RGB", (W, H), (38, 40, 46))
    ImageDraw.Draw(image).rectangle([0, 400, W, H], fill=(52, 54, 60))
    src = np.float32([(40, 36), (600, 36), (600, 444), (40, 444)])
    dst = np.float32([(50, 70), (610, 70), (520, 400), (90, 400)])
    matrix = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(np.asarray(photo), matrix, (W, H))
    mask = np.zeros((H, W), np.uint8)
    cv2.fillConvexPoly(mask, dst.astype(np.int32), 255)
    base = np.asarray(image).copy()
    base[mask > 0] = warped[mask > 0]
    image = Image.fromarray(base)
    d = ImageDraw.Draw(image)
    d.line([tuple(map(int, p)) for p in dst] + [tuple(map(int, dst[0]))], fill=(210, 130, 255), width=4)
    d.rectangle([16, 412, 624, 468], fill=(16, 16, 18))
    d.text((28, 424), "扫完回看、拖着转：才会看到近宽远窄的梯形", fill=(245, 245, 245), font=font(20))
    return image


def teaching() -> Image.Image:
    image = Image.new("RGB", (W, H), (38, 40, 46))
    d = ImageDraw.Draw(image)
    trap = [(50, 70), (610, 70), (520, 400), (90, 400)]
    d.polygon(trap, fill=(120, 70, 180), outline=(210, 130, 255), width=4)
    d.rectangle([16, 360, 624, 468], fill=(16, 16, 18))
    d.text((28, 376), "上一张讲解图的中间格，用的是回看机位", fill=(245, 245, 245), font=font(22))
    d.text((28, 412), "我把「相机」挪到旁边，方便看出这是一块3D板", fill=(210, 210, 220), font=font(20))
    d.text((28, 444), "不是扫描时系统把墙旋转了", fill=(255, 196, 72), font=font(20))
    return image


def main():
    photo = crop_door()
    p1 = banner("真机扫描（示意）", "摄像头画面 + 叠在墙上的框", live(photo))
    p2 = banner("扫完回看（示意）", "你用手把模型转开，墙才变梯形", review(photo))
    p3 = banner("讲解图中间格", "故意用了回看机位，不是扫描截屏", teaching())
    sheet = Image.new("RGB", (p1.width * 3, p1.height), (8, 8, 10))
    sheet.paste(p1, (0, 0))
    sheet.paste(p2, (p1.width, 0))
    sheet.paste(p3, (p1.width * 2, 0))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
