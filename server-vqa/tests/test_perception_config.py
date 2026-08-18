"""Tests for the versioned perception config schema."""

from __future__ import annotations

import json

import pytest

from app import perception_config as pc


def test_defaults_match_ios_constants():
    config = pc.default_config()
    # These MUST equal LocalPathGuidanceEngine constants in LocalPerception.swift.
    assert (config.near_roi.x, config.near_roi.y, config.near_roi.w, config.near_roi.h) == (0.25, 0.0, 0.5, 0.58)
    assert (config.left_roi.x, config.left_roi.y, config.left_roi.w, config.left_roi.h) == (0.0, 0.05, 0.42, 0.62)
    assert (config.right_roi.x, config.right_roi.y, config.right_roi.w, config.right_roi.h) == (0.58, 0.05, 0.42, 0.62)
    t = config.thresholds
    assert t.near_blocked_area == 0.82
    assert t.side_blocked_area == 0.86
    assert t.seg_near_caution_ratio == 0.35
    assert t.seg_side_caution_ratio == 0.30
    assert t.seg_traversable_pixel == 0.55


def test_to_dict_has_stable_keys_and_hash():
    config = pc.default_config()
    payload = config.to_dict()
    assert set(payload.keys()) == {"version", "updated_at", "hash", "roi", "thresholds"}
    assert set(payload["roi"].keys()) == {"near", "left", "right"}
    assert set(payload["roi"]["near"].keys()) == {"x", "y", "w", "h"}
    assert set(payload["thresholds"].keys()) == {
        "near_blocked_area",
        "side_blocked_area",
        "seg_near_caution_ratio",
        "seg_side_caution_ratio",
        "seg_traversable_pixel",
    }
    # Hash is deterministic and metadata-independent.
    assert payload["hash"] == config.content_hash()
    other = pc.PerceptionConfig(version=99, updated_at="2020-01-01T00:00:00Z")
    assert other.content_hash() == config.content_hash()


def test_round_trip_from_dict():
    config = pc.default_config()
    restored = pc.config_from_dict(config.to_dict())
    assert restored.to_dict() == config.to_dict()


@pytest.mark.parametrize(
    "mutation",
    [
        {"roi": {"near": {"x": 1.2, "y": 0.0, "w": 0.5, "h": 0.58}}},  # x > 1
        {"roi": {"near": {"x": 0.8, "y": 0.0, "w": 0.5, "h": 0.58}}},  # x + w > 1
        {"roi": {"near": {"x": 0.25, "y": 0.0, "w": 0.0, "h": 0.58}}},  # zero width
        {"thresholds": {"near_blocked_area": 1.5}},  # out of range
        {"thresholds": {"bogus_key": 0.5}},  # unknown key
        {"version": 0},  # bad version
    ],
)
def test_invalid_configs_raise(mutation):
    base = pc.default_config().to_dict()
    if "roi" in mutation:
        base["roi"].update(mutation["roi"])
    if "thresholds" in mutation:
        base["thresholds"].update(mutation["thresholds"])
    if "version" in mutation:
        base["version"] = mutation["version"]
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

    updated = pc.bump_and_save({"thresholds": {"near_blocked_area": 0.75}})
    assert updated.version == 2
    assert updated.thresholds.near_blocked_area == 0.75

    on_disk = json.loads(store.read_text(encoding="utf-8"))
    assert on_disk["version"] == 2
    assert on_disk["thresholds"]["near_blocked_area"] == 0.75
    assert on_disk["updated_at"]  # stamped

    # A second bump continues from the persisted version.
    again = pc.bump_and_save({"roi": {"near": {"x": 0.2, "y": 0.0, "w": 0.5, "h": 0.5}}})
    assert again.version == 3
    assert again.near_roi.x == 0.2


def test_bump_and_save_rejects_invalid_without_writing(tmp_path, monkeypatch):
    store = tmp_path / "cfg.json"
    monkeypatch.setenv("VQASEE_PERCEPTION_CONFIG_PATH", str(store))
    with pytest.raises(pc.ConfigValidationError):
        pc.bump_and_save({"thresholds": {"near_blocked_area": 9.0}})
    assert not store.exists()
