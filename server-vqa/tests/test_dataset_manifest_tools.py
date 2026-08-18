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


def test_bdd100k_drivable_adapter_creates_path_manifest(tmp_path):
    from app.open_dataset_adapters import create_bdd100k_drivable_manifest

    images = tmp_path / "images"
    images.mkdir()
    Image.new("RGB", (100, 100), "black").save(images / "frame.jpg")
    labels = tmp_path / "drivable.json"
    labels.write_text(
        json.dumps(
            [
                {
                    "name": "frame.jpg",
                    "labels": [
                        {
                            "category": "drivable area",
                            "attributes": {"areaType": "direct"},
                            # Fill lower-center image area. Adapter should map it to near_path candidateOpen.
                            "poly2d": [[[25, 45], [75, 45], [75, 99], [25, 99]]],
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    output = tmp_path / "bdd-manifest.jsonl"

    rows = create_bdd100k_drivable_manifest(images_dir=images, labels_path=labels, output_path=output, limit=10)

    assert output.is_file()
    assert len(rows) == 1
    row = rows[0]
    assert row["dataset_source"] == "bdd100k_drivable_area"
    assert row["ground_truth_source"] == "bdd100k_drivable_area_poly2d"
    assert row["ground_truth"]["near_path_status"] == "candidateOpen"
    assert row["mask_coverage"]["near_path"] >= 0.6


def test_camvid_adapter_creates_path_manifest_from_rgb_labels(tmp_path):
    from app.open_dataset_adapters import create_camvid_manifest

    images = tmp_path / "CamVid_RGB"
    labels = tmp_path / "CamVid_Label"
    images.mkdir()
    labels.mkdir()
    Image.new("RGB", (40, 40), "black").save(images / "frame.png")
    label = np.zeros((40, 40, 3), dtype=np.uint8)
    # Road-colored lower center should become traversable near path.
    label[18:40, 10:30] = np.array([128, 64, 128], dtype=np.uint8)
    Image.fromarray(label).save(labels / "frame.png")
    output = tmp_path / "camvid-manifest.jsonl"

    rows = create_camvid_manifest(images_dir=images, labels_dir=labels, output_path=output)

    assert output.is_file()
    assert len(rows) == 1
    row = rows[0]
    assert row["dataset_source"] == "camvid_github"
    assert row["ground_truth_source"] == "camvid_rgb_semantic_label"
    assert row["ground_truth"]["near_path_status"] == "candidateOpen"
