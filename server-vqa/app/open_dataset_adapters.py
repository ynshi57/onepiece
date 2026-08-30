"""Adapters from open/local datasets into VQASee path manifests."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image, ImageDraw

from app.guidance_path import centerline_from_mask
from app.path_dataset_import import LEFT_ROI, NEAR_ROI, RIGHT_ROI, focus_direction, roi_coverage, status_from_coverage
from app.region_grid import downsample_mask_to_grid, grid_to_wire, lane_presence_grid


BDD_SCENE_TAGS = ["road", "driving", "drivable", "bdd100k"]
CAMVID_SCENE_TAGS = ["road", "outdoor", "driving", "camvid"]
# Authoritative CamVid class -> color mapping comes from the dataset's own
# camvid_data.py (32-class palette). Earlier we used a Cityscapes sidewalk color
# (244,35,232) that DOES NOT EXIST in CamVid, so sidewalk pixels were silently
# dropped from "traversable" and the ground truth was systematically over-blocked.
# These sets match the real CamVid palette.
CAMVID_ROAD_COLORS = {
    (128, 64, 128),  # Road
    (128, 0, 192),   # LaneMkgsDriv (drivable lane markings)
    (192, 0, 64),    # LaneMkgsNonDriv (kept as road surface)
}
CAMVID_SIDEWALK_COLORS = {
    (0, 0, 192),      # Sidewalk
    (64, 192, 128),   # ParkingBlock (walkable)
    (128, 128, 192),  # RoadShoulder (walkable)
}

# Default "walk" = road + sidewalk are traversable for a pedestrian.
CAMVID_TRAVERSABLE_COLORS = CAMVID_ROAD_COLORS | CAMVID_SIDEWALK_COLORS

# Lane markings as a first-class layer (previously folded into "road" and lost).
# Colors verified against real CamVid_Label pixels (LaneMkgsDriv is only ~0.67%
# of pixels — a thin class, which is why a dedicated lane detector is planned).
CAMVID_LANE_COLORS = {
    (128, 0, 192),  # LaneMkgsDriv (drivable lane markings)
    (192, 0, 64),   # LaneMkgsNonDriv
}

# Physical / dynamic blockers a walker or driver must not be routed through.
# Colors are the canonical CamVid 32-class palette, spot-checked against real
# CamVid_Label pixels (Car/Pedestrian/Column_Pole/TrafficLight/SUV/Bicyclist all
# present in sampled frames).
CAMVID_OBSTACLE_COLORS = {
    (64, 0, 128),     # Car
    (64, 64, 0),      # Pedestrian
    (0, 128, 192),    # Bicyclist
    (64, 0, 64),      # Truck_Bus
    (64, 128, 192),   # SUVPickupTruck
    (192, 0, 192),    # MotorcycleScooter
    (192, 128, 64),   # Child
    (64, 128, 64),    # Animal
    (64, 0, 192),     # CartLuggagePram
    (128, 64, 64),    # OtherMoving
    (0, 0, 64),       # TrafficCone
    (192, 192, 128),  # Column_Pole
    (64, 64, 128),    # Fence
    (64, 128, 128),   # Wall
}

VALID_ROLES = ("walk", "drive")


class RoleTraversabilityPolicy:
    """Role-conditioned traversability semantics for one outdoor user role.

    The core product fix: a *pedestrian's* traversable region must favour the
    SIDEWALK, not the road. Merging road+sidewalk into one "traversable" (the old
    binary semantics) tells a walker the carriageway is walkable — a trust-level
    defect. So each role splits classes into:

    - ``primary``  : the preferred, "green" traversable surface for this role.
    - ``caution``  : usable but not preferred (e.g. a walker crossing the road);
                     NEVER counted as primary/candidateOpen.
    - ``obstacle`` : physical blockers to route around.
    - ``lane``     : lane-marking geometry (a separate capability layer).
    """

    __slots__ = ("role", "primary", "caution", "obstacle", "lane")

    def __init__(self, role, primary, caution, obstacle, lane):
        self.role = role
        self.primary = frozenset(primary)
        self.caution = frozenset(caution)
        self.obstacle = frozenset(obstacle)
        self.lane = frozenset(lane)

    def as_dict(self) -> dict[str, list[tuple[int, int, int]]]:
        return {
            "role": self.role,
            "primary": sorted(self.primary),
            "caution": sorted(self.caution),
            "obstacle": sorted(self.obstacle),
            "lane": sorted(self.lane),
        }


def role_traversability_policy(role: str) -> RoleTraversabilityPolicy:
    """Resolve the class-to-semantics policy for an outdoor role.

    ``role``:
    - "walk"  (pedestrian / cyclist): primary = sidewalk; road = caution.
    - "drive" (motor vehicle): primary = road + drivable lane; sidewalk = blocked.

    Unknown roles raise (no silent guess); "bike" is an alias for "walk".
    """
    key = (role or "walk").strip().lower()
    if key == "bike":
        key = "walk"
    if key == "walk":
        return RoleTraversabilityPolicy(
            role="walk",
            primary=CAMVID_SIDEWALK_COLORS,          # sidewalk / parking block / shoulder
            caution=CAMVID_ROAD_COLORS,              # road (crossable, not preferred)
            obstacle=CAMVID_OBSTACLE_COLORS,
            lane=CAMVID_LANE_COLORS,
        )
    if key == "drive":
        return RoleTraversabilityPolicy(
            role="drive",
            primary=CAMVID_ROAD_COLORS,              # carriageway + drivable lane markings
            caution={(128, 128, 192)},               # RoadShoulder: usable, not preferred
            # Sidewalk is NOT drivable -> a blocker for a vehicle.
            obstacle=CAMVID_OBSTACLE_COLORS | {(0, 0, 192), (64, 192, 128)},
            lane=CAMVID_LANE_COLORS,
        )
    raise ValueError(f"unknown role: {role!r} (use one of {VALID_ROLES} or 'bike')")


def camvid_traversable_colors(traversable_classes: str) -> set[tuple[int, int, int]]:
    """Resolve which CamVid palette colors count as traversable for a scene.

    "walk"  -> road + sidewalk (pedestrian / default)
    "drive" -> road only (vehicle: sidewalk is NOT drivable)
    """
    mode = (traversable_classes or "walk").lower()
    if mode == "drive":
        return set(CAMVID_ROAD_COLORS)
    if mode == "walk":
        return set(CAMVID_TRAVERSABLE_COLORS)
    raise ValueError(f"unknown traversable_classes: {traversable_classes!r} (use 'walk' or 'drive')")


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON at {path}: {exc}") from exc


def _bdd_records(labels_path: Path) -> list[dict[str, Any]]:
    value = _load_json(labels_path)
    if isinstance(value, list):
        records = value
    elif isinstance(value, dict) and isinstance(value.get("frames"), list):
        records = value["frames"]
    elif isinstance(value, dict) and isinstance(value.get("items"), list):
        records = value["items"]
    else:
        raise ValueError("BDD100K labels must be a JSON list or an object with frames/items")
    if not all(isinstance(item, dict) for item in records):
        raise ValueError("BDD100K label records must be objects")
    return records


def _bdd_image_name(record: dict[str, Any]) -> str:
    for key in ["name", "frame", "image", "image_name"]:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return Path(value).name
    raise ValueError("BDD100K record missing image name")


def _labels(record: dict[str, Any]) -> Iterable[dict[str, Any]]:
    labels = record.get("labels")
    return labels if isinstance(labels, list) else []


def _polygon_points(poly2d: Any) -> list[tuple[float, float]]:
    if not isinstance(poly2d, list):
        return []
    points: list[tuple[float, float]] = []
    for point in poly2d:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            try:
                points.append((float(point[0]), float(point[1])))
            except (TypeError, ValueError):
                continue
        elif isinstance(point, dict):
            try:
                points.append((float(point["x"]), float(point["y"])))
            except (KeyError, TypeError, ValueError):
                continue
    return points


def _is_direct_drivable(label: dict[str, Any]) -> bool:
    category = str(label.get("category") or label.get("name") or "").lower()
    if "drivable" not in category:
        return False
    attributes = label.get("attributes") if isinstance(label.get("attributes"), dict) else {}
    area_type = str(attributes.get("areaType") or attributes.get("area_type") or "").lower()
    # BDD100K commonly marks directly reachable drivable area as "direct".
    # If areaType is absent, keep the polygon: older/converted exports may omit it.
    return not area_type or area_type == "direct"


def _rasterize_bdd_drivable_mask(image_path: Path, record: dict[str, Any]) -> np.ndarray:
    with Image.open(image_path) as image:
        width, height = image.size
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    for label in _labels(record):
        if not isinstance(label, dict) or not _is_direct_drivable(label):
            continue
        polygons = label.get("poly2d")
        # Some exports nest poly2d under a shape object.
        if polygons is None and isinstance(label.get("shape"), dict):
            polygons = label["shape"].get("poly2d")
        for polygon in polygons if isinstance(polygons, list) else []:
            points = _polygon_points(polygon.get("vertices") if isinstance(polygon, dict) else polygon)
            if len(points) >= 3:
                draw.polygon(points, fill=255)
    return np.asarray(mask, dtype=np.float32) / 255.0 >= 0.5


def _row_from_mask(*, image_path: Path, images_dir: Path, mask: np.ndarray, split: str, scene_tags: list[str]) -> dict[str, Any]:
    near_cov = roi_coverage(mask, NEAR_ROI)
    left_cov = roi_coverage(mask, LEFT_ROI)
    right_cov = roi_coverage(mask, RIGHT_ROI)
    near_status = status_from_coverage(near_cov)
    left_status = status_from_coverage(left_cov)
    right_status = status_from_coverage(right_cov)
    rel = image_path.relative_to(images_dir).as_posix()
    # Ground-truth walkable-region grid (same coarse raster the iPhone harness
    # emits), so the loop can score region IoU without re-reading label images.
    gt_cells = downsample_mask_to_grid(np.asarray(mask, dtype=bool))
    # Derive the line from the same 64x48 grid consumed by the iPhone/evaluator,
    # not from full-resolution labels. This keeps semantics aligned and avoids
    # full-res connected-component work when manifests are regenerated.
    gt_path = centerline_from_mask(gt_cells.astype(bool), source="dataset_mask")
    gt_grid = grid_to_wire(gt_cells)
    return {
        "frame_id": f"{split}/{image_path.stem}",
        "image": rel,
        "image_path": str(image_path.resolve()),
        "split": split,
        "scene_tags": scene_tags,
        "dataset_source": scene_tags[-1] if scene_tags else "open_dataset",
        "ground_truth_source": "semantic_traversability_mask",
        "ground_truth": {
            "near_path_status": near_status,
            "left_front_status": left_status,
            "right_front_status": right_status,
            "focus_direction": focus_direction(near_status, left_status, right_status),
        },
        "ground_truth_path": gt_path.to_dict(),
        "traversable_grid": gt_grid,
        "mask_coverage": {"near_path": near_cov, "left_front": left_cov, "right_front": right_cov},
    }


def create_bdd100k_drivable_manifest(
    *,
    images_dir: Path,
    labels_path: Path,
    output_path: Path,
    split: str = "road",
    scene_tags: list[str] | None = None,
    limit: int = 0,
) -> list[dict[str, Any]]:
    """Create a path-guidance manifest from BDD100K drivable-area labels.

    Expected local inputs:
    - `images_dir`: directory containing BDD100K images for one split.
    - `labels_path`: JSON list with records that include image `name` and
      `labels[].category == "drivable area"` polygons in `poly2d`.

    Only direct drivable polygons are used when `attributes.areaType` exists.
    """
    if not images_dir.is_dir():
        raise FileNotFoundError(f"images dir not found: {images_dir}")
    if not labels_path.is_file():
        raise FileNotFoundError(f"BDD100K labels file not found: {labels_path}")
    tags = scene_tags or BDD_SCENE_TAGS
    rows: list[dict[str, Any]] = []
    for record in _bdd_records(labels_path):
        image_name = _bdd_image_name(record)
        image_path = images_dir / image_name
        if not image_path.is_file():
            continue
        mask = _rasterize_bdd_drivable_mask(image_path, record)
        row = _row_from_mask(image_path=image_path, images_dir=images_dir, mask=mask, split=split, scene_tags=tags)
        row["dataset_source"] = "bdd100k_drivable_area"
        row["ground_truth_source"] = "bdd100k_drivable_area_poly2d"
        rows.append(row)
        if limit and len(rows) >= limit:
            break
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + ("\n" if rows else ""), encoding="utf-8")
    return rows


def _camvid_color_mask(arr: np.ndarray, colors: Iterable[tuple[int, int, int]]) -> np.ndarray:
    """Boolean mask of pixels whose RGB matches any color in ``colors``."""
    mask = np.zeros(arr.shape[:2], dtype=bool)
    for color in colors:
        rgb = np.asarray(color, dtype=np.uint8)
        mask |= np.all(arr == rgb, axis=-1)
    return mask


# T2 multi-class taxonomy (N=5). Single source of truth for the class ids the
# on-device segmentation model outputs and everything (training, eval, on-device
# role derivation) derives from. crosswalk deliberately NOT included this round
# (CamVid has no crosswalk label — see docs/model-lab/2026-08-26-t2-...-plan.md).
CAMVID_CLASS_BACKGROUND = 0
CAMVID_CLASS_ROAD = 1
CAMVID_CLASS_SIDEWALK = 2
CAMVID_CLASS_LANE = 3
CAMVID_CLASS_OBSTACLE = 4
CAMVID_NUM_CLASSES = 5
# "road (carriageway) only" = road colors minus the lane-marking colors, so lane
# gets its own class rather than being swallowed by road.
CAMVID_ROAD_ONLY_COLORS = CAMVID_ROAD_COLORS - CAMVID_LANE_COLORS


def camvid_class_map(arr: np.ndarray) -> np.ndarray:
    """Per-pixel int class map (H,W) for the N=5 T2 taxonomy from a CamVid RGB label.

    Painted by ASCENDING priority so higher-priority classes overwrite lower ones
    where the (rare) overlap of color sets would otherwise be ambiguous:
    background(0) < road(1) < sidewalk(2) < lane(3) < obstacle(4).
    A lane-marking pixel therefore stays lane (not road); an obstacle over any
    surface stays obstacle. This mirrors the on-device argmax semantics.
    """
    h, w = arr.shape[:2]
    class_map = np.full((h, w), CAMVID_CLASS_BACKGROUND, dtype=np.uint8)
    class_map[_camvid_color_mask(arr, CAMVID_ROAD_ONLY_COLORS)] = CAMVID_CLASS_ROAD
    class_map[_camvid_color_mask(arr, CAMVID_SIDEWALK_COLORS)] = CAMVID_CLASS_SIDEWALK
    class_map[_camvid_color_mask(arr, CAMVID_LANE_COLORS)] = CAMVID_CLASS_LANE
    class_map[_camvid_color_mask(arr, CAMVID_OBSTACLE_COLORS)] = CAMVID_CLASS_OBSTACLE
    return class_map


def _role_row_from_masks(
    *,
    image_path: Path,
    images_dir: Path,
    role: str,
    primary: np.ndarray,
    caution: np.ndarray,
    lane: np.ndarray,
    obstacle: np.ndarray,
    split: str,
    scene_tags: list[str],
) -> dict[str, Any]:
    """Build a role-conditioned manifest row.

    The role's PRIMARY surface (e.g. sidewalk for a walker) is the "green" target:
    the GT guidance line and the shared ``traversable_grid`` derive from it, so the
    loop scores the iPhone against the surface this role should actually use — not a
    road+sidewalk merge. ``caution`` / ``lane`` / ``obstacle`` are exposed as their
    own coarse grids so A3 can quantify role-specific defects (e.g. routing a walker
    onto the carriageway) without re-reading label images."""
    rel = image_path.relative_to(images_dir).as_posix()
    primary_cells = downsample_mask_to_grid(np.asarray(primary, dtype=bool))
    gt_path = centerline_from_mask(primary_cells.astype(bool), source="dataset_mask")
    primary_grid = grid_to_wire(primary_cells)
    caution_grid = grid_to_wire(downsample_mask_to_grid(np.asarray(caution, dtype=bool)))
    lane_grid = grid_to_wire(downsample_mask_to_grid(np.asarray(lane, dtype=bool)))
    obstacle_grid = grid_to_wire(downsample_mask_to_grid(np.asarray(obstacle, dtype=bool)))
    # Finer, any-pixel lane raster (128x96) so thin lanes survive for closed-loop
    # scoring against the on-device lane grid. Separate NEW field: the coarse
    # role_grids.lane above is unchanged so existing role consumers are untouched.
    lane_grid_fine = grid_to_wire(lane_presence_grid(np.asarray(lane, dtype=bool)))
    return {
        "frame_id": f"{split}/{image_path.stem}",
        "image": rel,
        "image_path": str(image_path.resolve()),
        "split": split,
        "scene_tags": scene_tags,
        "dataset_source": "camvid_github",
        "ground_truth_source": "camvid_rgb_semantic_role",
        "role": role,
        "ground_truth_path": gt_path.to_dict(),
        # Role primary = the surface the iPhone should perceive as walkable/drivable.
        "traversable_grid": primary_grid,
        "role_grids": {
            "primary": primary_grid,
            "caution": caution_grid,
            "lane": lane_grid,
            "obstacle": obstacle_grid,
        },
        # Fine lane truth (128x96, any-pixel) for closed-loop lane scoring.
        "lane_grid_fine": lane_grid_fine,
    }


def create_camvid_role_manifest(
    *,
    images_dir: Path,
    labels_dir: Path,
    output_path: Path,
    role: str,
    split: str = "road",
    scene_tags: list[str] | None = None,
    limit: int = 0,
) -> list[dict[str, Any]]:
    """Create a ROLE-CONDITIONED CamVid manifest (walk / drive).

    This is deliberately a SEPARATE artifact from ``create_camvid_manifest`` — it
    writes to its own ``output_path`` (e.g. ``camvid-manifest-walk.jsonl``) and does
    NOT overwrite the legacy binary manifest. Keeping both lets us (a) quantify the
    defect of the old road+sidewalk-merged truth by comparing side by side, (b)
    avoid silently changing semantics for existing consumers.

    Each row carries four role layers as coarse grids (primary/caution/lane/
    obstacle) so downstream metrics never re-read the RGB labels.
    """
    if not images_dir.is_dir():
        raise FileNotFoundError(f"images dir not found: {images_dir}")
    if not labels_dir.is_dir():
        raise FileNotFoundError(f"CamVid labels dir not found: {labels_dir}")
    policy = role_traversability_policy(role)
    tags = scene_tags or CAMVID_SCENE_TAGS
    rows: list[dict[str, Any]] = []
    for image_path in _iter_images(images_dir):
        label_path = _find_camvid_label(labels_dir, image_path)
        if label_path is None:
            continue
        arr = np.asarray(Image.open(label_path).convert("RGB"), dtype=np.uint8)
        row = _role_row_from_masks(
            image_path=image_path,
            images_dir=images_dir,
            role=policy.role,
            primary=_camvid_color_mask(arr, policy.primary),
            caution=_camvid_color_mask(arr, policy.caution),
            lane=_camvid_color_mask(arr, policy.lane),
            obstacle=_camvid_color_mask(arr, policy.obstacle),
            split=split,
            scene_tags=tags,
        )
        row["label_path"] = str(label_path.resolve())
        rows.append(row)
        if limit and len(rows) >= limit:
            break
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )
    return rows


def _iter_images(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
            yield path


def _find_camvid_label(labels_dir: Path, image_path: Path) -> Path | None:
    stems = [image_path.stem, f"{image_path.stem}_L", image_path.stem.replace("_leftImg8bit", "_gtFine_color")]
    for stem in stems:
        for suffix in [".png", ".jpg", ".jpeg"]:
            candidate = labels_dir / f"{stem}{suffix}"
            if candidate.is_file():
                return candidate
    for path in labels_dir.rglob(f"{image_path.stem}*"):
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            return path
    return None


def _camvid_traversability_mask(
    label_path: Path, colors: set[tuple[int, int, int]] | None = None
) -> np.ndarray:
    arr = np.asarray(Image.open(label_path).convert("RGB"), dtype=np.uint8)
    mask = np.zeros(arr.shape[:2], dtype=bool)
    for color in colors if colors is not None else CAMVID_TRAVERSABLE_COLORS:
        rgb = np.asarray(color, dtype=np.uint8)
        mask |= np.all(arr == rgb, axis=-1)
    return mask


def create_camvid_manifest(
    *,
    images_dir: Path,
    labels_dir: Path,
    output_path: Path,
    split: str = "road",
    scene_tags: list[str] | None = None,
    limit: int = 0,
    traversable_classes: str = "walk",
) -> list[dict[str, Any]]:
    """Create a path-guidance manifest from CamVid-style RGB semantic labels.

    Expected inputs:
    - `images_dir`: RGB images.
    - `labels_dir`: RGB semantic labels with matching filenames or `_L` suffix.

    `traversable_classes` selects which CamVid classes count as walkable ground
    truth: "walk" (road + sidewalk, default) or "drive" (road only). This is a
    deliberate, recorded choice — not a silent palette guess.
    """
    if not images_dir.is_dir():
        raise FileNotFoundError(f"images dir not found: {images_dir}")
    if not labels_dir.is_dir():
        raise FileNotFoundError(f"CamVid labels dir not found: {labels_dir}")
    tags = scene_tags or CAMVID_SCENE_TAGS
    colors = camvid_traversable_colors(traversable_classes)
    rows: list[dict[str, Any]] = []
    for image_path in _iter_images(images_dir):
        label_path = _find_camvid_label(labels_dir, image_path)
        if label_path is None:
            continue
        mask = _camvid_traversability_mask(label_path, colors)
        row = _row_from_mask(image_path=image_path, images_dir=images_dir, mask=mask, split=split, scene_tags=tags)
        row["dataset_source"] = "camvid_github"
        row["ground_truth_source"] = "camvid_rgb_semantic_label"
        row["traversable_classes"] = traversable_classes
        row["label_path"] = str(label_path.resolve())
        rows.append(row)
        if limit and len(rows) >= limit:
            break
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + ("\n" if rows else ""), encoding="utf-8")
    return rows
