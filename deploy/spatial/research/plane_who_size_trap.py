"""Who drew the purple box, why that size, why the middle panel is a trapezoid."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / ".cursor-tmp-cap-2.png"
OUT = ROOT / "docs/evolution/figures/2026-10-12-projective-texture/who-size-trapezoid.png"
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


def frame_who(photo: Image.Image) -> Image.Image:
    image = photo.copy()
    d = ImageDraw.Draw(image, "RGBA")
    box = [(36, 44), (604, 44), (604, 430), (36, 430)]
    d.polygon(box, fill=(160, 80, 220, 45))
    d.line(box + box[:1], fill=(210, 130, 255, 255), width=5)
    d.rectangle([16, 360, 430, 464], fill=(20, 20, 24, 210))
    d.text((28, 372), "真机：iPhone 上的 ARKit 认「这块是平的」", fill=(245, 245, 245), font=font(20))
    d.text((28, 408), "这张讲解图：我按整面墙画的示意框", fill=(220, 210, 230), font=font(20))
    d.text((28, 440), "不是另有一个「紫色识别器」", fill=(220, 210, 230), font=font(20))
    return image.convert("RGB")


def frame_size(photo: Image.Image) -> Image.Image:
    """Same wall, two observation extents: small glance vs longer scan."""
    image = photo.copy().convert("RGBA")
    d = ImageDraw.Draw(image, "RGBA")
    small = [(180, 70), (430, 70), (430, 250), (180, 250)]
    large = [(36, 44), (604, 44), (604, 430), (36, 430)]
    d.polygon(large, outline=(210, 130, 255, 255))
    d.line(large + large[:1], fill=(210, 130, 255, 180), width=3)
    d.polygon(small, fill=(80, 180, 220, 60))
    d.line(small + small[:1], fill=(80, 220, 255, 255), width=4)
    d.rectangle([16, 360, 620, 464], fill=(20, 20, 24, 210))
    d.text((28, 372), "蓝框：刚扫到门的一小块，平面就这么大", fill=(180, 230, 255), font=font(20))
    d.text((28, 408), "紫框：多看一会儿，平面跟着变大", fill=(230, 210, 255), font=font(20))
    d.text((28, 440), "所以 size = 目前观察到的范围，不是竣工墙宽", fill=(230, 230, 230), font=font(20))
    return image.convert("RGB")


def frame_trap() -> Image.Image:
    image = Image.new("RGB", (W, H), (38, 40, 46))
    d = ImageDraw.Draw(image)
    d.rectangle([0, 400, W, H], fill=(52, 54, 60))
    # Front view rectangle (left)
    front = [(40, 140), (210, 140), (210, 340), (40, 340)]
    d.polygon(front, fill=(160, 90, 220, ), outline=(210, 130, 255), width=3)
    d.text((125, 120), "正对：矩形", fill=(245, 245, 245), font=font(20), anchor="mt")
    for i, p in enumerate([(40, 340), (210, 340), (210, 140), (40, 140)]):
        d.ellipse([p[0] - 6, p[1] - 6, p[0] + 6, p[1] + 6], fill=(255, 80, 80))
        d.text((p[0] + 8, p[1] - 18), str(i), fill=(255, 80, 80), font=font(18))
    # Side view trapezoid (right) — same 4 corners, perspective
    trap = [(340, 360), (560, 360), (620, 80), (300, 80)]
    d.polygon(trap, fill=(160, 90, 220), outline=(210, 130, 255), width=3)
    d.text((460, 58), "走到旁边：还是这 4 个角", fill=(245, 245, 245), font=font(20), anchor="mt")
    corners = [(340, 360), (560, 360), (620, 80), (300, 80)]
    for i, p in enumerate(corners):
        d.ellipse([p[0] - 6, p[1] - 6, p[0] + 6, p[1] + 6], fill=(255, 80, 80))
        d.text((p[0] + 8, p[1] - 18), str(i), fill=(255, 80, 80), font=font(18))
    d.line([(230, 240), (290, 240)], fill=(200, 200, 200), width=2)
    d.polygon([(290, 232), (290, 248), (310, 240)], fill=(200, 200, 200))
    d.text((24, 420), "中间那格是 3D 里斜着看，墙没有被剪成梯形", fill=(230, 230, 230), font=font(20))
    return image


def main():
    photo = crop_door()
    p1 = banner("问：紫框是谁画的？", "真机是 ARKit；讲解图里是示意", frame_who(photo))
    p2 = banner("问：为什么是这个大小？", "扫到多大，框就多大", frame_size(photo))
    p3 = banner("问：中间为什么变成梯形？", "换角度看同一面墙，四个角没变", frame_trap())
    sheet = Image.new("RGB", (p1.width * 3, p1.height), (8, 8, 10))
    sheet.paste(p1, (0, 0))
    sheet.paste(p2, (p1.width, 0))
    sheet.paste(p3, (p1.width * 2, 0))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
