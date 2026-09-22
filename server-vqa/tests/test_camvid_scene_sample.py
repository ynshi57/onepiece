import json
from pathlib import Path

import numpy as np
from PIL import Image

from app.camvid_scene_sample import (
    CAMVID_SEQUENCES,
    CAMVID_TEST_PER_SCENE,
    catalog_from_frames,
    load_camvid_test_catalog,
    sample_camvid_test_frames,
    sample_three_spread,
    sequence_of,
    situation_of,
    subset_jsonl_by_stems,
    write_camvid_test_artifacts,
)
from app.open_dataset_adapters import CAMVID_ROAD_COLORS, CAMVID_SIDEWALK_COLORS


def test_sequence_of_maps_official_camvid_prefixes():
    assert sequence_of("0001TP_006690.png") == "0001TP"
    assert sequence_of("0006R0_f00990") == "0006R0"
    assert sequence_of("0016E5_08190") == "0016E5"
    assert sequence_of("Seq05VD_f01800.png") == "Seq05VD"
    assert sequence_of("unknown.png") == "other"


def test_sample_three_spread_picks_one_from_each_third():
    rows = [{"stem": f"f{i:03d}", "vehicle_near": 0.0, "lane": 0.0, "person": 0.0, "sidewalk_near": 0.0} for i in range(9)]
    rows[1]["vehicle_near"] = 0.4
    rows[4]["lane"] = 0.2
    rows[8]["person"] = 0.3
    picked = sample_three_spread(rows, k=3)
    assert [row["stem"] for row in picked] == ["f001", "f004", "f008"]


def test_sample_camvid_test_frames_takes_three_per_official_sequence():
    frames = []
    for seq in CAMVID_SEQUENCES:
        for i in range(6):
            frames.append(
                {
                    "stem": f"{seq}_{i:05d}",
                    "sequence": seq,
                    "vehicle_near": 0.01 * i,
                    "lane": 0.0,
                    "person": 0.0,
                    "sidewalk_near": 0.0,
                    "situation": "open_road",
                    "brightness": 100.0,
                    "road": 0.3,
                }
            )
    sampled = sample_camvid_test_frames(frames)
    assert len(sampled) == len(CAMVID_SEQUENCES) * CAMVID_TEST_PER_SCENE
    by_seq = {seq: [row["stem"] for row in sampled if row["sequence"] == seq] for seq in CAMVID_SEQUENCES}
    for seq, stems in by_seq.items():
        assert len(stems) == 3
        assert stems[0] != stems[1] != stems[2]


def test_situation_of_prefers_near_traffic_when_vehicles_fill_foreground():
    assert situation_of({
        "vehicle_near": 0.25,
        "lane": 0.002,
        "person": 0.001,
        "sidewalk_near": 0.05,
        "brightness": 110.0,
        "road": 0.2,
    }) == "near_traffic"


def test_subset_jsonl_copies_only_requested_stems_and_marks_test(tmp_path):
    src = tmp_path / "full.jsonl"
    rows = [
        {"frame_id": "road/0001TP_006690", "image": "0001TP_006690.png", "image_path": "dataset/camvid/CamVid_RGB/0001TP_006690.png", "scene_tags": ["camvid"]},
        {"frame_id": "road/0016E5_00000", "image": "0016E5_00000.png", "image_path": "dataset/camvid/CamVid_RGB/0016E5_00000.png", "scene_tags": ["camvid"]},
    ]
    src.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    dest = tmp_path / "test.jsonl"
    count = subset_jsonl_by_stems(src, dest, ["0001TP_006690"])
    assert count == 1
    out = json.loads(dest.read_text(encoding="utf-8").strip())
    assert out["image"] == "0001TP_006690.png"
    assert out["eval_split"] == "test"
    assert "camvid-test" in out["scene_tags"]
    assert "seq:0001TP" in out["scene_tags"]


def test_write_camvid_test_artifacts_from_tiny_dataset(tmp_path):
    dataset = tmp_path / "dataset"
    rgb = dataset / "camvid" / "CamVid_RGB"
    lab = dataset / "camvid" / "CamVid_Label"
    rgb.mkdir(parents=True)
    lab.mkdir(parents=True)
    road = next(iter(CAMVID_ROAD_COLORS))
    sidewalk = next(iter(CAMVID_SIDEWALK_COLORS))
    prefixes = list(CAMVID_SEQUENCES)
    for seq in prefixes:
        for i in range(3):
            stem = f"{seq}_{i:05d}"
            Image.new("RGB", (8, 8), (40 * i, 40 * i, 40 * i)).save(rgb / f"{stem}.png")
            arr = np.zeros((8, 8, 3), dtype=np.uint8)
            arr[:] = road
            arr[:, 4:] = sidewalk
            Image.fromarray(arr).save(lab / f"{stem}_L.png")
    docs = tmp_path / "docs" / "datasets"
    docs.mkdir(parents=True)
    stems_needed = []
    for seq in prefixes:
        for i in range(3):
            stems_needed.append(f"{seq}_{i:05d}")
    for name in ("camvid-manifest-drive.jsonl", "camvid-manifest-walk.jsonl", "camvid-manifest.jsonl"):
        lines = []
        for stem in stems_needed:
            lines.append(json.dumps({
                "frame_id": f"road/{stem}",
                "image": f"{stem}.png",
                "image_path": f"dataset/camvid/CamVid_RGB/{stem}.png",
                "role": "drive" if "drive" in name else "walk" if "walk" in name else None,
                "scene_tags": ["camvid"],
            }))
        (docs / name).write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = write_camvid_test_artifacts(repo=tmp_path, dataset_root=dataset, docs_dir=docs)
    assert len(result["stems"]) == 12
    catalog = json.loads((docs / "camvid-test-scenes.json").read_text(encoding="utf-8"))
    assert catalog["test_count"] == 12
    assert catalog["full_count"] == 12
    assert result["manifests"]["camvid-manifest-drive-test.jsonl"] == 12
    drive_test = (docs / "camvid-manifest-drive-test.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(drive_test) == 12


def test_load_camvid_test_catalog_derives_stems_from_scenes(tmp_path):
    docs = tmp_path / "docs" / "datasets"
    docs.mkdir(parents=True)
    (docs / "camvid-test-scenes.json").write_text(
        json.dumps(
            {
                "scenes": [
                    {"id": "0001TP", "samples": [{"stem": "0001TP_a"}, {"stem": "0001TP_b"}]},
                ]
            }
        ),
        encoding="utf-8",
    )
    catalog = load_camvid_test_catalog(tmp_path)
    assert catalog["stems"] == ["0001TP_a", "0001TP_b"]


def test_catalog_lists_four_official_scenes():
    frames = [
        {"stem": "0001TP_a", "sequence": "0001TP", "situation": "lowlight", "brightness": 60, "vehicle_near": 0.2, "sidewalk_near": 0.1, "lane": 0.01, "person": 0.01},
        {"stem": "0001TP_b", "sequence": "0001TP", "situation": "near_traffic", "brightness": 62, "vehicle_near": 0.3, "sidewalk_near": 0.1, "lane": 0.01, "person": 0.0},
    ]
    sampled = frames[:1]
    catalog = catalog_from_frames(frames, sampled)
    assert catalog["scenes"][0]["id"] == "0001TP"
    assert catalog["scenes"][0]["full_count"] == 2
    assert catalog["stems"] == ["0001TP_a"]
