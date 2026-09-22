"""Render on-device perception steps for one CamVid drive-test frame."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.open_dataset_adapters import CAMVID_ROAD_COLORS, CAMVID_SIDEWALK_COLORS

FRAME_ID = "road/0001TP_008430"
STEM = "0001TP_008430"


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
    canvas = Image.new("RGB", (image.width, image.height + 48), (12, 12, 16))
    canvas.paste(image, (0, 48))
    draw = ImageDraw.Draw(canvas)
    draw.text((16, 10), text, fill=(245, 245, 247), font=_font(28))
    return canvas


def _overlay_mask(base: Image.Image, mask: np.ndarray, color: tuple[int, int, int], alpha: float = 0.45) -> Image.Image:
    arr = np.asarray(base).astype(np.float32)
    tint = np.zeros_like(arr)
    tint[mask] = color
    out = arr.copy()
    out[mask] = arr[mask] * (1 - alpha) + tint[mask] * alpha
    return Image.fromarray(out.astype(np.uint8))


def _color_mask(label: np.ndarray, colors: set[tuple[int, int, int]]) -> np.ndarray:
    mask = np.zeros(label.shape[:2], dtype=bool)
    for color in colors:
        mask |= np.all(label == np.asarray(color, dtype=np.uint8), axis=-1)
    return mask


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


def _vision_line(path: dict | None, width: int, height: int) -> list[tuple[float, float]]:
    if not path or path.get("status") != "ok":
        return []
    lines = path.get("lines") or []
    if not lines:
        return []
    points = []
    for point in lines[0].get("points") or []:
        points.append((float(point["x"]) * width, (1.0 - float(point["y"])) * height))
    return points


def _draw_line(draw: ImageDraw.ImageDraw, points: list[tuple[float, float]], color, width: int = 6, dashed: bool = False) -> None:
    if len(points) < 2:
        return
    if dashed:
        for index in range(len(points) - 1):
            if index % 2 == 0:
                draw.line([points[index], points[index + 1]], fill=color, width=width)
    else:
        draw.line(points, fill=color, width=width)
    radius = 8
    x, y = points[0]
    draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=color)


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    out = repo / "docs" / "model-lab" / "figures" / "camvid-0001TP_008430"
    out.mkdir(parents=True, exist_ok=True)
    rgb = Image.open(repo / "dataset" / "camvid" / "CamVid_RGB" / f"{STEM}.png").convert("RGB")
    label = np.asarray(Image.open(repo / "dataset" / "camvid" / "CamVid_Label" / f"{STEM}_L.png").convert("RGB"))
    width, height = rgb.size
    pred = _load_jsonl_row(repo / "docs" / "datasets" / "camvid-ios-mc5-drive.jsonl", FRAME_ID)
    truth = _load_jsonl_row(repo / "docs" / "datasets" / "camvid-manifest-drive-test.jsonl", FRAME_ID)
    walk_pred = _load_jsonl_row(repo / "docs" / "datasets" / "camvid-ios-mc5-walk.jsonl", FRAME_ID)
    road = _color_mask(label, CAMVID_ROAD_COLORS)
    sidewalk = _color_mask(label, CAMVID_SIDEWALK_COLORS)
    title = _font(20)

    _caption(rgb, "0 · 摄像头原图  0001TP_008430（drive test）").save(out / "00-rgb.jpg", quality=90)
    _caption(
        Image.open(repo / "dataset" / "camvid" / "CamVid_Label" / f"{STEM}_L.png").convert("RGB"),
        "1 · CamVid 语义标注（评测答案卡，不是手机输出）",
    ).save(out / "01-label.jpg", quality=90)
    _caption(_overlay_mask(rgb, road, (48, 209, 88)), "2 · 机动车真值可走区 = 马路+车道线（不含人行道）").save(
        out / "02-drive-gt-region.jpg", quality=90
    )
    _caption(_overlay_mask(rgb, sidewalk, (10, 132, 255)), "3 · 行人真值可走区（本帧人行道很少）").save(
        out / "03-walk-gt-region.jpg", quality=90
    )

    grid = pred["traversable_grid"]
    cells = np.asarray(grid["cells"], dtype=np.uint8).reshape(int(grid["rows"]), int(grid["cols"]))
    pred_mask = np.asarray(Image.fromarray(cells * 255, mode="L").resize((width, height), Image.NEAREST)) > 127
    _caption(_overlay_mask(rgb, pred_mask, (191, 90, 242)), "4 · 端上分割预测可走区（mc5 + 开车角色）").save(
        out / "04-pred-region.jpg", quality=90
    )

    walk_grid = walk_pred["traversable_grid"]
    walk_cells = np.asarray(walk_grid["cells"], dtype=np.uint8).reshape(
        int(walk_grid["rows"]), int(walk_grid["cols"])
    )
    walk_mask = np.asarray(Image.fromarray(walk_cells * 255, mode="L").resize((width, height), Image.NEAREST)) > 127
    _caption(
        _overlay_mask(rgb, walk_mask, (10, 132, 255)),
        "4w · 同一帧、走路角色：几乎没有人行道主表面",
    ).save(out / "04w-walk-pred-region.jpg", quality=90)

    compare = np.asarray(rgb).astype(np.float32)
    compare[road & ~pred_mask] = compare[road & ~pred_mask] * 0.45 + np.array([48, 209, 88]) * 0.55
    compare[pred_mask & ~road] = compare[pred_mask & ~road] * 0.45 + np.array([191, 90, 242]) * 0.55
    compare[road & pred_mask] = compare[road & pred_mask] * 0.4 + np.array([255, 214, 10]) * 0.6
    _caption(Image.fromarray(compare.astype(np.uint8)), "4b · 黄=重合  绿=真值漏了  紫=预测多画").save(
        out / "04b-region-compare.jpg", quality=90
    )

    lines = rgb.copy()
    draw = ImageDraw.Draw(lines)
    gt_pts = _vision_line(truth.get("ground_truth_path"), width, height)
    pred_pts = _vision_line(pred.get("guidance_path"), width, height)
    _draw_line(draw, gt_pts, (48, 209, 88), 7, dashed=True)
    _draw_line(draw, pred_pts, (191, 90, 242), 7)
    draw.text((20, height - 70), "green dashed = GT    purple = predicted", fill=(255, 255, 255), font=title)
    _caption(lines, "5 · 从可走区描中心线：脚下往远处串中点").save(out / "05-centerlines.jpg", quality=90)

    boxes = rgb.copy()
    draw = ImageDraw.Draw(boxes)
    colors = {"car": (255, 69, 58), "person": (255, 214, 10), "obstacle": (255, 159, 10)}
    for obj in pred.get("objects") or []:
        box = obj.get("box") or {}
        x = float(box.get("x", 0.0)) * width
        w = float(box.get("w", 0.0)) * width
        h = float(box.get("h", 0.0)) * height
        y = (1.0 - (float(box.get("y", 0.0)) + float(box.get("h", 0.0)))) * height
        color = colors.get(str(obj.get("kind")), (200, 200, 200))
        draw.rectangle([x, y, x + w, y + h], outline=color, width=4)
        draw.text((x + 4, max(4, y - 22)), f"{obj.get('label') or obj.get('kind')} {obj.get('confidence', 0):.2f}", fill=color, font=title)
    _caption(boxes, "6 · YOLO 障碍框（不挖可走区的洞）").save(out / "06-yolo.jpg", quality=90)

    compose = _overlay_mask(rgb, pred_mask, (191, 90, 242), 0.32)
    draw = ImageDraw.Draw(compose)
    _draw_line(draw, gt_pts, (48, 209, 88), 6, dashed=True)
    _draw_line(draw, pred_pts, (255, 255, 255), 8)
    _draw_line(draw, pred_pts, (191, 90, 242), 5)
    for obj in pred.get("objects") or []:
        if obj.get("kind") not in {"car", "person"}:
            continue
        box = obj.get("box") or {}
        x = float(box.get("x", 0.0)) * width
        w = float(box.get("w", 0.0)) * width
        h = float(box.get("h", 0.0)) * height
        y = (1.0 - (float(box.get("y", 0.0)) + float(box.get("h", 0.0)))) * height
        color = colors.get(str(obj.get("kind")), (200, 200, 200))
        draw.rectangle([x, y, x + w, y + h], outline=color, width=3)
    _caption(compose, "7 · 合成：紫区+紫线+人/车框（Qwen 不参与）").save(out / "07-compose.jpg", quality=90)

    inter = int((road & pred_mask).sum())
    union = int((road | pred_mask).sum())
    print("drive IoU", round(inter / union, 3) if union else None)
    print("drive pred cells", int(cells.sum()), "/", cells.size)
    print("walk pred cells", int(walk_cells.sum()), "/", walk_cells.size)
    print("drive guidance", pred.get("guidance_path", {}).get("status"), pred.get("guidance_path", {}).get("coverage"))
    print("walk guidance", walk_pred.get("guidance_path", {}).get("status"), walk_pred.get("guidance_path", {}).get("coverage"))
    print("gt guidance", truth.get("ground_truth_path", {}).get("status"), truth.get("ground_truth_path", {}).get("coverage"))
    print("objects", [(o.get("kind"), round(float(o.get("confidence") or 0), 3)) for o in pred.get("objects") or []])
    print("wrote", out)


if __name__ == "__main__":
    main()
