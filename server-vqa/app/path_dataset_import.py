"""Import local image/mask folders into VQASee path-guidance manifests."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
NEAR_ROI = (0.25, 0.00, 0.50, 0.58)
LEFT_ROI = (0.00, 0.05, 0.42, 0.62)
RIGHT_ROI = (0.58, 0.05, 0.42, 0.62)


def iter_images(images_dir: Path) -> Iterable[Path]:
    for path in sorted(images_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            yield path


def find_mask(masks_dir: Path, image_path: Path) -> Path | None:
    for suffix in [".png", ".jpg", ".jpeg", ".webp"]:
        candidate = masks_dir / f"{image_path.stem}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def load_mask(mask_path: Path, threshold: float) -> np.ndarray:
    image = Image.open(mask_path).convert("L")
    arr = np.asarray(image, dtype=np.float32) / 255.0
    return arr >= threshold


def roi_coverage(mask: np.ndarray, roi: tuple[float, float, float, float]) -> float | None:
    height, width = mask.shape[:2]
    x, y, w, h = roi
    x0 = max(0, min(width - 1, int(x * width)))
    x1 = max(x0 + 1, min(width, int((x + w) * width)))
    y_top = 1.0 - y - h
    y_bottom = 1.0 - y
    y0 = max(0, min(height - 1, int(y_top * height)))
    y1 = max(y0 + 1, min(height, int(y_bottom * height)))
    crop = mask[y0:y1, x0:x1]
    if crop.size == 0:
        return None
    return round(float(crop.mean()), 4)


def status_from_coverage(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value >= 0.60:
        return "candidateOpen"
    if value >= 0.28:
        return "caution"
    return "blocked"


def focus_direction(near: str, left: str, right: str) -> str:
    if near in {"blocked", "caution"}:
        return "center"
    if left in {"blocked", "caution"} and right not in {"blocked", "caution"}:
        return "left"
    if right in {"blocked", "caution"} and left not in {"blocked", "caution"}:
        return "right"
    if left in {"blocked", "caution"} and right in {"blocked", "caution"}:
        return "left"
    return "unknown"


def row_for_image(*, image_path: Path, images_dir: Path, mask_path: Path | None, split: str, scene_tags: list[str], threshold: float) -> dict:
    relative_image = image_path.relative_to(images_dir).as_posix()
    frame_id = f"{split}/{image_path.stem}"
    row = {
        "frame_id": frame_id,
        "image": relative_image,
        "image_path": str(image_path.resolve()),
        "split": split,
        "scene_tags": scene_tags,
    }
    if mask_path is None:
        row["ground_truth"] = {"near_path_status": "unknown", "left_front_status": "unknown", "right_front_status": "unknown", "focus_direction": "unknown"}
        row["ground_truth_source"] = "missing_mask"
        return row
    mask = load_mask(mask_path, threshold=threshold)
    near_cov = roi_coverage(mask, NEAR_ROI)
    left_cov = roi_coverage(mask, LEFT_ROI)
    right_cov = roi_coverage(mask, RIGHT_ROI)
    near_status = status_from_coverage(near_cov)
    left_status = status_from_coverage(left_cov)
    right_status = status_from_coverage(right_cov)
    row["ground_truth"] = {
        "near_path_status": near_status,
        "left_front_status": left_status,
        "right_front_status": right_status,
        "focus_direction": focus_direction(near_status, left_status, right_status),
    }
    row["ground_truth_source"] = "traversability_mask"
    row["mask"] = mask_path.name
    row["mask_path"] = str(mask_path.resolve())
    row["mask_coverage"] = {"near_path": near_cov, "left_front": left_cov, "right_front": right_cov}
    return row


def create_manifest_from_folders(*, images_dir: Path, masks_dir: Path | None, output_path: Path, split: str, scene_tags: list[str], threshold: float, limit: int = 0) -> list[dict]:
    if not images_dir.is_dir():
        raise FileNotFoundError(f"images dir not found: {images_dir}")
    if masks_dir and not masks_dir.is_dir():
        raise FileNotFoundError(f"masks dir not found: {masks_dir}")
    rows = []
    for image_path in iter_images(images_dir):
        mask_path = find_mask(masks_dir, image_path) if masks_dir else None
        rows.append(row_for_image(image_path=image_path, images_dir=images_dir, mask_path=mask_path, split=split, scene_tags=scene_tags, threshold=threshold))
        if limit and len(rows) >= limit:
            break
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + ("\n" if rows else ""), encoding="utf-8")
    return rows
