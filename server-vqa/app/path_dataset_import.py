"""Import local image/mask folders into VQASee path-guidance manifests."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image

from app.region_grid import downsample_mask_to_grid, grid_to_wire

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

__all__ = [
    "IMAGE_EXTENSIONS",
    "iter_images",
    "find_mask",
    "load_mask",
    "row_for_image",
    "create_manifest_from_folders",
]


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
        row["ground_truth"] = {}
        row["ground_truth_source"] = "missing_mask"
        return row
    mask = load_mask(mask_path, threshold=threshold)
    gt_cells = downsample_mask_to_grid(np.asarray(mask, dtype=bool))
    coverage = float(np.mean(np.asarray(mask, dtype=bool)))
    row["ground_truth"] = {}
    row["ground_truth_source"] = "traversability_mask"
    row["traversable_grid"] = grid_to_wire(gt_cells)
    row["mask"] = mask_path.name
    row["mask_path"] = str(mask_path.resolve())
    row["mask_coverage"] = {"frame": round(coverage, 4)}
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
