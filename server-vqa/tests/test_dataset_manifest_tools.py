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
    assert row["traversable_classes"] == "walk"
    # Every row now carries a ground-truth guidance line derived from the mask.
    from app.guidance_path import GuidancePath
    gt_path = GuidancePath.from_dict(row["ground_truth_path"])
    assert gt_path.status in {"ok", "insufficient"}


def test_camvid_sidewalk_is_traversable_in_walk_but_not_drive(tmp_path):
    """Regression guard for the palette bug: real CamVid Sidewalk color (0,0,192)
    must count as traversable for a pedestrian (walk) and must NOT for a vehicle
    (drive). The old code used a Cityscapes color that never matched CamVid, so
    sidewalks were silently dropped and ground truth was over-blocked."""
    from app.open_dataset_adapters import create_camvid_manifest

    images = tmp_path / "CamVid_RGB"
    labels = tmp_path / "CamVid_Label"
    images.mkdir()
    labels.mkdir()
    Image.new("RGB", (40, 40), "black").save(images / "frame.png")
    label = np.zeros((40, 40, 3), dtype=np.uint8)
    # Fill the near ROI region with CamVid Sidewalk color (not road).
    label[:, :] = np.array([0, 0, 192], dtype=np.uint8)
    Image.fromarray(label).save(labels / "frame.png")

    walk = create_camvid_manifest(
        images_dir=images, labels_dir=labels, output_path=tmp_path / "walk.jsonl",
        traversable_classes="walk",
    )[0]
    drive = create_camvid_manifest(
        images_dir=images, labels_dir=labels, output_path=tmp_path / "drive.jsonl",
        traversable_classes="drive",
    )[0]

    # Sidewalk everywhere -> walkable (candidateOpen) for pedestrians.
    assert walk["ground_truth"]["near_path_status"] == "candidateOpen"
    assert walk["mask_coverage"]["near_path"] >= 0.6
    # Sidewalk is not drivable -> blocked for the vehicle scene.
    assert drive["ground_truth"]["near_path_status"] == "blocked"


def test_camvid_traversable_colors_rejects_unknown_mode():
    from app.open_dataset_adapters import camvid_traversable_colors

    import pytest as _pytest

    with _pytest.raises(ValueError):
        camvid_traversable_colors("fly")


def test_role_policy_walk_favours_sidewalk_over_road():
    """A pedestrian's PRIMARY (green) surface is the sidewalk; the road is only
    caution (crossable, not preferred) and must never be primary. This is the
    core role-conditioned fix."""
    from app.open_dataset_adapters import role_traversability_policy

    walk = role_traversability_policy("walk")
    ROAD = (128, 64, 128)
    SIDEWALK = (0, 0, 192)
    assert SIDEWALK in walk.primary
    assert ROAD in walk.caution
    assert ROAD not in walk.primary          # road is NOT walkable-primary
    # Vehicles/pedestrians are obstacles for a walker.
    assert (64, 0, 128) in walk.obstacle     # Car
    assert (64, 64, 0) in walk.obstacle      # Pedestrian


def test_role_policy_drive_favours_road_and_blocks_sidewalk():
    from app.open_dataset_adapters import role_traversability_policy

    drive = role_traversability_policy("drive")
    ROAD = (128, 64, 128)
    SIDEWALK = (0, 0, 192)
    assert ROAD in drive.primary
    assert (128, 0, 192) in drive.primary    # LaneMkgsDriv drivable
    assert SIDEWALK not in drive.primary
    assert SIDEWALK in drive.obstacle        # sidewalk is not drivable


def test_role_policy_bike_aliases_walk_and_unknown_rejected():
    from app.open_dataset_adapters import role_traversability_policy

    import pytest as _pytest

    assert role_traversability_policy("bike").primary == role_traversability_policy("walk").primary
    with _pytest.raises(ValueError):
        role_traversability_policy("fly")


def test_role_policy_lane_layer_separated_from_road():
    """Lane markings are exposed as a distinct layer (previously lost inside road)."""
    from app.open_dataset_adapters import role_traversability_policy, CAMVID_LANE_COLORS

    walk = role_traversability_policy("walk")
    assert (128, 0, 192) in CAMVID_LANE_COLORS   # LaneMkgsDriv
    assert walk.lane == CAMVID_LANE_COLORS


def _write_camvid_pair(tmp_path, label_rgb):
    """Write a 1-frame CamVid image/label pair; return (images_dir, labels_dir)."""
    images = tmp_path / "CamVid_RGB"
    labels = tmp_path / "CamVid_Label"
    images.mkdir()
    labels.mkdir()
    h, w = label_rgb.shape[:2]
    Image.new("RGB", (w, h), "black").save(images / "frame.png")
    Image.fromarray(label_rgb).save(labels / "frame.png")
    return images, labels


def test_camvid_role_manifest_walk_emits_four_layers_and_does_not_overwrite(tmp_path):
    """A2: the role manifest is a SEPARATE artifact carrying primary/caution/lane/
    obstacle grids. For a walker, sidewalk -> primary, road -> caution, and a car
    -> obstacle. The legacy binary manifest must stay untouched."""
    from app.open_dataset_adapters import create_camvid_manifest, create_camvid_role_manifest
    from app.region_grid import grid_from_wire

    label = np.zeros((48, 64, 3), dtype=np.uint8)
    label[24:48, 0:32] = np.array([0, 0, 192], dtype=np.uint8)     # Sidewalk (left-bottom)
    label[24:48, 32:64] = np.array([128, 64, 128], dtype=np.uint8)  # Road (right-bottom)
    label[0:12, 40:56] = np.array([64, 0, 128], dtype=np.uint8)     # Car (top)
    images, labels = _write_camvid_pair(tmp_path, label)

    legacy = tmp_path / "camvid-manifest.jsonl"
    create_camvid_manifest(images_dir=images, labels_dir=labels, output_path=legacy)
    legacy_bytes = legacy.read_bytes()

    role_out = tmp_path / "camvid-manifest-walk.jsonl"
    rows = create_camvid_role_manifest(
        images_dir=images, labels_dir=labels, output_path=role_out, role="walk",
    )

    # Legacy file untouched (new-and-old coexist, no silent semantic change).
    assert legacy.read_bytes() == legacy_bytes
    assert role_out.is_file()
    assert len(rows) == 1
    row = rows[0]
    assert row["role"] == "walk"
    assert row["ground_truth_source"] == "camvid_rgb_semantic_role"
    grids = row["role_grids"]
    primary = grid_from_wire(grids["primary"])
    caution = grid_from_wire(grids["caution"])
    obstacle = grid_from_wire(grids["obstacle"])
    # Sidewalk became primary; road became caution (NOT merged into primary).
    assert primary[:, 0:32].any() and not primary[:, 40:64].any()
    assert caution[:, 40:64].any()
    assert not caution[:, 0:16].any()
    # Car cell is an obstacle, and is not primary.
    assert obstacle[0:12, 40:56].any()
    assert not primary[0:12, 40:56].any()
    # The shared traversable_grid mirrors the role primary (role-conditioned green).
    assert row["traversable_grid"] == grids["primary"]


def test_camvid_role_manifest_drive_makes_road_primary_and_sidewalk_obstacle(tmp_path):
    """A2: for a driver, road -> primary and sidewalk -> obstacle (not drivable)."""
    from app.open_dataset_adapters import create_camvid_role_manifest
    from app.region_grid import grid_from_wire

    label = np.zeros((48, 64, 3), dtype=np.uint8)
    label[24:48, 0:32] = np.array([0, 0, 192], dtype=np.uint8)      # Sidewalk (left)
    label[24:48, 32:64] = np.array([128, 64, 128], dtype=np.uint8)  # Road (right)
    images, labels = _write_camvid_pair(tmp_path, label)

    rows = create_camvid_role_manifest(
        images_dir=images, labels_dir=labels, output_path=tmp_path / "drive.jsonl", role="drive",
    )
    grids = rows[0]["role_grids"]
    primary = grid_from_wire(grids["primary"])
    obstacle = grid_from_wire(grids["obstacle"])
    assert primary[:, 40:64].any()          # road is primary for a vehicle
    assert not primary[:, 0:16].any()        # sidewalk is not primary
    assert obstacle[:, 0:16].any()           # sidewalk is a blocker for a vehicle


def test_camvid_class_map_assigns_five_classes_with_priority(tmp_path):
    """T2: the per-pixel N=5 class map must keep lane distinct from road, keep an
    obstacle over any surface as obstacle, and treat unlisted colors as background."""
    from app.open_dataset_adapters import (
        camvid_class_map,
        CAMVID_CLASS_BACKGROUND,
        CAMVID_CLASS_ROAD,
        CAMVID_CLASS_SIDEWALK,
        CAMVID_CLASS_LANE,
        CAMVID_CLASS_OBSTACLE,
        CAMVID_NUM_CLASSES,
    )

    assert CAMVID_NUM_CLASSES == 5
    label = np.zeros((10, 10, 3), dtype=np.uint8)
    label[0:5, 0:5] = np.array([128, 64, 128], dtype=np.uint8)   # Road
    label[0:5, 5:10] = np.array([0, 0, 192], dtype=np.uint8)     # Sidewalk
    label[5:10, 0:5] = np.array([128, 0, 192], dtype=np.uint8)   # LaneMkgsDriv (also in road set)
    label[5:10, 5:10] = np.array([64, 0, 128], dtype=np.uint8)   # Car (obstacle)
    label[0, 0] = np.array([70, 70, 70], dtype=np.uint8)          # unlisted -> background
    label[9, 9] = np.array([64, 64, 0], dtype=np.uint8)           # Pedestrian over-writes? it's obstacle region already

    cmap = camvid_class_map(label)
    assert cmap.shape == (10, 10)
    assert cmap[2, 2] == CAMVID_CLASS_ROAD
    assert cmap[2, 7] == CAMVID_CLASS_SIDEWALK
    # Lane color is a subset of the road set but must stay LANE, not road.
    assert cmap[7, 2] == CAMVID_CLASS_LANE
    assert cmap[7, 7] == CAMVID_CLASS_OBSTACLE
    assert cmap[0, 0] == CAMVID_CLASS_BACKGROUND
    assert set(np.unique(cmap)).issubset(set(range(CAMVID_NUM_CLASSES)))
