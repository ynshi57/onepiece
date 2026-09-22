"""CamVid street-scene inventory and a small iteration test split.

CamVid is four car-mounted sequences, not dozens of cities. Full 701-frame
role eval is a release gate (~3s/frame with multiclass on Intel Mac). Daily
algorithm work uses 3 temporally spread frames per sequence (12 total).
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image

from app.dataset_paths import open_dataset_root, repo_root
from app.open_dataset_adapters import (
    CAMVID_LANE_COLORS,
    CAMVID_ROAD_COLORS,
    CAMVID_SIDEWALK_COLORS,
)

CAMVID_TEST_PER_SCENE = 3

# Dataset-native street scenes: the four official CamVid driving sequences.
CAMVID_SEQUENCES: dict[str, dict[str, Any]] = {
    "0001TP": {
        "label": "低光城市街道",
        "summary": "最暗、近处车辆最多，城市峡谷，引导线容易被大车挤走人行道。",
    },
    "0006R0": {
        "label": "开阔明亮车道",
        "summary": "最亮、路面占比最高，适合看畅通车道和标线。",
    },
    "0016E5": {
        "label": "混合街区",
        "summary": "帧数最多，行人出现最频繁，街道更挤。",
    },
    "Seq05VD": {
        "label": "人行道偏多的街道",
        "summary": "人行道/路沿占比最高、近处车辆最少，适合看路沿界面。",
    },
}

CAMVID_VEHICLE_COLORS = {
    (64, 0, 128),     # Car
    (64, 0, 64),      # Truck_Bus
    (64, 128, 192),   # SUVPickupTruck
    (192, 0, 192),    # MotorcycleScooter
    (128, 64, 64),    # OtherMoving
}
CAMVID_PERSON_COLORS = {
    (64, 64, 0),      # Pedestrian
    (0, 128, 192),    # Bicyclist
    (192, 128, 64),   # Child
}

SITUATION_LABELS = {
    "near_traffic": "近处车辆占道",
    "open_road": "开阔车道",
    "curb_sidewalk": "路沿/人行道",
    "junction_lanes": "路口/车道标线",
    "pedestrians": "行人混行",
    "lowlight": "低光",
}

TEST_SCENE_TAG = "camvid-test"


def sequence_of(name: str) -> str:
    stem = Path(name).stem
    for prefix in CAMVID_SEQUENCES:
        if stem.startswith(prefix):
            return prefix
    return "other"


def _pack_colors(colors: Iterable[tuple[int, int, int]]) -> np.ndarray:
    return np.array([(c[0] << 16) | (c[1] << 8) | c[2] for c in colors], dtype=np.uint32)


_PACKED = {
    "road": _pack_colors(CAMVID_ROAD_COLORS),
    "lane": _pack_colors(CAMVID_LANE_COLORS),
    "sidewalk": _pack_colors(CAMVID_SIDEWALK_COLORS),
    "vehicle": _pack_colors(CAMVID_VEHICLE_COLORS),
    "person": _pack_colors(CAMVID_PERSON_COLORS),
}


def _frac(packed: np.ndarray, colors: np.ndarray) -> float:
    if packed.size == 0 or colors.size == 0:
        return 0.0
    return float(np.isin(packed, colors).mean())


def frame_stats(image_path: Path, label_path: Path) -> dict[str, Any]:
    rgb = np.asarray(Image.open(image_path).convert("RGB"))
    label = np.asarray(Image.open(label_path).convert("RGB"), dtype=np.uint32)
    packed = (label[:, :, 0] << 16) | (label[:, :, 1] << 8) | label[:, :, 2]
    lower = packed[int(packed.shape[0] * 0.55) :, :]
    stats = {
        "stem": image_path.stem,
        "sequence": sequence_of(image_path.name),
        "brightness": float(rgb.mean()),
        "road": _frac(packed, _PACKED["road"]),
        "lane": _frac(packed, _PACKED["lane"]),
        "sidewalk": _frac(packed, _PACKED["sidewalk"]),
        "vehicle": _frac(packed, _PACKED["vehicle"]),
        "person": _frac(packed, _PACKED["person"]),
        "vehicle_near": _frac(lower, _PACKED["vehicle"]),
        "sidewalk_near": _frac(lower, _PACKED["sidewalk"]),
    }
    stats["situation"] = situation_of(stats)
    return stats


def situation_of(stats: dict[str, Any]) -> str:
    """Dominant street situation for display — not a second sampling axis.

    Near-field vehicles win first: that is the failure mode that shoved the
    drive centerline onto the sidewalk. Sequence already encodes lighting.
    """
    vehicle_near = float(stats["vehicle_near"])
    person = float(stats["person"])
    lane = float(stats["lane"])
    sidewalk_near = float(stats["sidewalk_near"])
    brightness = float(stats["brightness"])
    road = float(stats["road"])
    if vehicle_near >= 0.12:
        return "near_traffic"
    if person >= 0.015:
        return "pedestrians"
    if lane >= 0.02:
        return "junction_lanes"
    if sidewalk_near >= 0.18:
        return "curb_sidewalk"
    if brightness < 80:
        return "lowlight"
    if road >= 0.25 and vehicle_near < 0.05:
        return "open_road"
    return "open_road"


def _interestingness(stats: dict[str, Any]) -> float:
    return (
        float(stats["vehicle_near"]) * 2.0
        + float(stats["lane"])
        + float(stats["person"])
        + float(stats["sidewalk_near"]) * 0.5
    )


def sample_three_spread(rows: list[dict[str, Any]], k: int = CAMVID_TEST_PER_SCENE) -> list[dict[str, Any]]:
    """Pick k frames from a sorted sequence: one from each temporal third.

    Inside a third, prefer the most situation-rich frame so start/mid/end are
    not three near-duplicates from a slow crawl.
    """
    ordered = sorted(rows, key=lambda row: str(row.get("stem", "")))
    if len(ordered) <= k:
        return ordered
    bounds = [0]
    for i in range(1, k):
        bounds.append(int(round(i * len(ordered) / k)))
    bounds.append(len(ordered))
    picked: list[dict[str, Any]] = []
    for start, end in zip(bounds, bounds[1:]):
        chunk = ordered[start:end] or ordered[start : start + 1]
        picked.append(max(chunk, key=_interestingness))
    return picked


def camvid_test_catalog_path(repo: Path | None = None) -> Path:
    root = repo if repo is not None else repo_root()
    return root / "docs" / "datasets" / "camvid-test-scenes.json"


def load_camvid_test_catalog(repo: Path | None = None) -> dict[str, Any]:
    path = camvid_test_catalog_path(repo)
    if not path.is_file():
        return {"scenes": [], "stems": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {"scenes": [], "stems": []}
    scenes = payload.get("scenes") or []
    stems = payload.get("stems") or []
    if not stems:
        stems = [
            str(sample.get("stem"))
            for scene in scenes
            if isinstance(scene, dict)
            for sample in (scene.get("samples") or [])
            if isinstance(sample, dict) and sample.get("stem")
        ]
        payload["stems"] = stems
    return payload


def camvid_rgb_dir(dataset_root: Path | None = None) -> Path:
    root = dataset_root if dataset_root is not None else open_dataset_root()
    return Path(root) / "camvid" / "CamVid_RGB"


def camvid_label_dir(dataset_root: Path | None = None) -> Path:
    root = dataset_root if dataset_root is not None else open_dataset_root()
    return Path(root) / "camvid" / "CamVid_Label"


def _label_for_rgb(label_dir: Path, stem: str) -> Path | None:
    for name in (f"{stem}_L.png", f"{stem}.png"):
        candidate = label_dir / name
        if candidate.is_file():
            return candidate
    return None


def inventory_camvid_frames(dataset_root: Path | None = None) -> list[dict[str, Any]]:
    rgb_dir = camvid_rgb_dir(dataset_root)
    label_dir = camvid_label_dir(dataset_root)
    if not rgb_dir.is_dir() or not label_dir.is_dir():
        raise FileNotFoundError(f"CamVid RGB/Label missing under {rgb_dir.parent}")
    rows: list[dict[str, Any]] = []
    for image_path in sorted(rgb_dir.glob("*.png")):
        label_path = _label_for_rgb(label_dir, image_path.stem)
        if label_path is None:
            continue
        rows.append(frame_stats(image_path, label_path))
    return rows


def sample_camvid_test_frames(frames: list[dict[str, Any]], k: int = CAMVID_TEST_PER_SCENE) -> list[dict[str, Any]]:
    sampled: list[dict[str, Any]] = []
    for sequence in CAMVID_SEQUENCES:
        group = [row for row in frames if row["sequence"] == sequence]
        sampled.extend(sample_three_spread(group, k=k))
    return sampled


def catalog_from_frames(all_frames: list[dict[str, Any]], sampled: list[dict[str, Any]]) -> dict[str, Any]:
    by_seq = Counter(row["sequence"] for row in all_frames)
    scenes = []
    for sequence, meta in CAMVID_SEQUENCES.items():
        chosen = [row for row in sampled if row["sequence"] == sequence]
        scenes.append(
            {
                "id": sequence,
                "label": meta["label"],
                "summary": meta["summary"],
                "full_count": by_seq.get(sequence, 0),
                "samples": [
                    {
                        "stem": row["stem"],
                        "situation": row["situation"],
                        "situation_label": SITUATION_LABELS.get(row["situation"], row["situation"]),
                        "brightness": round(float(row["brightness"]), 1),
                        "vehicle_near": round(float(row["vehicle_near"]), 3),
                        "sidewalk_near": round(float(row["sidewalk_near"]), 3),
                        "lane": round(float(row["lane"]), 3),
                        "person": round(float(row["person"]), 3),
                    }
                    for row in chosen
                ],
            }
        )
    return {
        "dataset": "camvid",
        "purpose": "iteration-test",
        "note": "四种官方行车序列各抽 3 张（头/中/尾时段里最有信息量的一帧），用于日常算法迭代。全量 701 仍是发布回归门。",
        "full_count": len(all_frames),
        "test_count": len(sampled),
        "per_scene": CAMVID_TEST_PER_SCENE,
        "scenes": scenes,
        "stems": [row["stem"] for row in sampled],
    }


def subset_jsonl_by_stems(
    source: Path,
    dest: Path,
    stems: Iterable[str],
    extra_tags: list[str] | None = None,
) -> int:
    """Copy matching manifest rows, preserving grids. Adds test scene tags."""
    wanted = set(stems)
    tags = extra_tags or []
    written = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    with source.open(encoding="utf-8") as src, dest.open("w", encoding="utf-8") as out:
        for line in src:
            if not line.strip():
                continue
            if not any(f"{stem}.png" in line for stem in wanted):
                continue
            row = json.loads(line)
            image = str(row.get("image") or row.get("image_path") or "")
            stem = Path(image).stem
            if stem not in wanted:
                continue
            existing = [str(tag) for tag in (row.get("scene_tags") or [])]
            for tag in (f"seq:{sequence_of(stem)}", TEST_SCENE_TAG, *tags):
                if tag not in existing:
                    existing.append(tag)
            row["scene_tags"] = existing
            row["eval_split"] = "test"
            out.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            written += 1
    return written


def write_camvid_test_artifacts(
    *,
    repo: Path | None = None,
    dataset_root: Path | None = None,
    docs_dir: Path | None = None,
) -> dict[str, Any]:
    root = repo if repo is not None else repo_root()
    docs = docs_dir if docs_dir is not None else root / "docs" / "datasets"
    frames = inventory_camvid_frames(dataset_root)
    sampled = sample_camvid_test_frames(frames)
    catalog = catalog_from_frames(frames, sampled)
    catalog_path = docs / "camvid-test-scenes.json"
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    stems = catalog["stems"]
    written = {
        "catalog": str(catalog_path),
        "stems": stems,
        "manifests": {},
    }
    pairs = (
        ("camvid-manifest-drive.jsonl", "camvid-manifest-drive-test.jsonl"),
        ("camvid-manifest-walk.jsonl", "camvid-manifest-walk-test.jsonl"),
        ("camvid-manifest.jsonl", "camvid-manifest-test.jsonl"),
    )
    for src_name, dest_name in pairs:
        src = docs / src_name
        dest = docs / dest_name
        if not src.is_file():
            continue
        count = subset_jsonl_by_stems(src, dest, stems)
        written["manifests"][dest_name] = count
    return written


if __name__ == "__main__":
    result = write_camvid_test_artifacts()
    print(json.dumps(result, ensure_ascii=False, indent=2))
