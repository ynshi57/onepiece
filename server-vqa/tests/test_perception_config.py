"""Tests for the versioned perception config schema."""

from __future__ import annotations

import json

import pytest

from app import perception_config as pc


def test_defaults_have_no_three_zone_roi():
    config = pc.default_config()
    payload = config.to_dict()
    assert "roi" not in payload
    assert payload["thresholds"] == {"seg_traversable_pixel": 0.55}
    assert payload["role"] == "pedestrian"
    assert payload["use_multiclass_segmentation"] is False
    assert payload["use_lane_segmentation"] is True
    assert payload["road_backend"] == "twinlite"


def test_to_dict_has_stable_keys_and_hash():
    config = pc.default_config()
    payload = config.to_dict()
    assert set(payload.keys()) == {
        "version",
        "updated_at",
        "hash",
        "thresholds",
        "role",
        "use_multiclass_segmentation",
        "use_lane_segmentation",
        "road_backend",
    }
    assert set(payload["thresholds"].keys()) == {"seg_traversable_pixel"}
    assert payload["hash"] == config.content_hash()
    other = pc.PerceptionConfig(version=99, updated_at="2020-01-01T00:00:00Z")
    assert other.content_hash() == config.content_hash()


def test_round_trip_from_dict():
    config = pc.default_config()
    restored = pc.config_from_dict(config.to_dict())
    assert restored.to_dict() == config.to_dict()


def test_load_ignores_leftover_roi_and_retired_thresholds():
    payload = pc.default_config().to_dict()
    payload["roi"] = {"near": {"x": 0.25, "y": 0.0, "w": 0.5, "h": 0.58}}
    payload["thresholds"]["near_blocked_area"] = 0.82
    restored = pc.config_from_dict(payload)
    assert "roi" not in restored.to_dict()
    assert restored.thresholds.seg_traversable_pixel == 0.55


@pytest.mark.parametrize(
    "mutation",
    [
        {"thresholds": {"seg_traversable_pixel": 1.5}},  # out of range
        {"thresholds": {"bogus_key": 0.5}},  # unknown key
        {"version": 0},  # bad version
        {"role": "walker"},  # not pedestrian|vehicle
        {"road_backend": "ufld"},  # not off|twinlite|mc5
    ],
)
def test_invalid_configs_raise(mutation):
    base = pc.default_config().to_dict()
    if "thresholds" in mutation:
        base["thresholds"].update(mutation["thresholds"])
    if "version" in mutation:
        base["version"] = mutation["version"]
    if "role" in mutation:
        base["role"] = mutation["role"]
    if "road_backend" in mutation:
        base["road_backend"] = mutation["road_backend"]
    with pytest.raises(pc.ConfigValidationError):
        pc.config_from_dict(base)


def test_load_active_config_defaults_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("VQASEE_PERCEPTION_CONFIG_PATH", str(tmp_path / "cfg.json"))
    assert pc.load_active_config().to_dict() == pc.default_config().to_dict()


def test_corrupt_store_raises_not_silent_default(tmp_path, monkeypatch):
    store = tmp_path / "cfg.json"
    store.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("VQASEE_PERCEPTION_CONFIG_PATH", str(store))
    with pytest.raises(pc.ConfigValidationError):
        pc.load_active_config()


def test_bump_and_save_increments_version_and_persists(tmp_path, monkeypatch):
    store = tmp_path / "cfg.json"
    monkeypatch.setenv("VQASEE_PERCEPTION_CONFIG_PATH", str(store))

    updated = pc.bump_and_save({"thresholds": {"seg_traversable_pixel": 0.75}})
    assert updated.version == 2
    assert updated.thresholds.seg_traversable_pixel == 0.75

    on_disk = json.loads(store.read_text(encoding="utf-8"))
    assert on_disk["version"] == 2
    assert on_disk["thresholds"]["seg_traversable_pixel"] == 0.75
    assert "roi" not in on_disk
    assert on_disk["updated_at"]  # stamped

    again = pc.bump_and_save({"road_backend": "mc5"})
    assert again.version == 3
    assert again.road_backend == "mc5"


def test_bump_rejects_retired_roi(tmp_path, monkeypatch):
    monkeypatch.setenv("VQASEE_PERCEPTION_CONFIG_PATH", str(tmp_path / "cfg.json"))
    with pytest.raises(pc.ConfigValidationError, match="roi retired"):
        pc.bump_and_save({"roi": {"near": {"x": 0.2, "y": 0.0, "w": 0.5, "h": 0.5}}})


def test_config_round_trip_preserves_vehicle_role():
    payload = pc.default_config().to_dict()
    payload["role"] = "vehicle"
    payload["use_multiclass_segmentation"] = True
    restored = pc.config_from_dict(payload)
    assert restored.role == "vehicle"
    assert restored.use_multiclass_segmentation is True
    assert restored.content_hash() != pc.default_config().content_hash()


def test_config_round_trip_preserves_road_backend():
    payload = pc.default_config().to_dict()
    payload["road_backend"] = "mc5"
    restored = pc.config_from_dict(payload)
    assert restored.road_backend == "mc5"
    assert restored.content_hash() != pc.default_config().content_hash()


def test_bump_and_save_road_backend(tmp_path, monkeypatch):
    store = tmp_path / "cfg.json"
    monkeypatch.setenv("VQASEE_PERCEPTION_CONFIG_PATH", str(store))
    updated = pc.bump_and_save({"road_backend": "off"})
    assert updated.version == 2
    assert updated.road_backend == "off"


def test_bump_and_save_rejects_invalid_without_writing(tmp_path, monkeypatch):
    store = tmp_path / "cfg.json"
    monkeypatch.setenv("VQASEE_PERCEPTION_CONFIG_PATH", str(store))
    with pytest.raises(pc.ConfigValidationError):
        pc.bump_and_save({"thresholds": {"seg_traversable_pixel": 9.0}})
    assert not store.exists()
