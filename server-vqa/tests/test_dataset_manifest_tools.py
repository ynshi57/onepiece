import json
from pathlib import Path

import numpy as np
from PIL import Image

from server_vqa_path_import import import_tool


create_tool = import_tool("create_path_manifest_from_masks.py")


def test_create_path_manifest_from_masks_marks_blocked_near_path(tmp_path):
    images = tmp_path / "images"
    masks = tmp_path / "masks"
    images.mkdir()
    masks.mkdir()
    Image.new("RGB", (20, 20), "black").save(images / "frame1.jpg")
    mask = np.ones((20, 20), dtype=np.uint8) * 255
    # Near path ROI lower center becomes non-traversable.
    mask[8:20, 5:15] = 0
    Image.fromarray(mask).save(masks / "frame1.png")

    row = create_tool.row_for_image(
        image_path=images / "frame1.jpg",
        images_dir=images,
        mask_path=masks / "frame1.png",
        split="indoor",
        scene_tags=["test"],
        threshold=0.5,
    )

    assert row["ground_truth"]["near_path_status"] in {"blocked", "caution"}
    assert row["ground_truth_source"] == "traversability_mask"
    assert row["mask_coverage"]["near_path"] < 0.6
