"""Guard against schema drift between the Python perception config and the Swift
struct that the iOS app + macOS harness decode.

This is a text-level contract test: it does not compile Swift (no Xcode in CI),
but it fails loudly if the wire keys or default values diverge between the two
sources of truth. If you intentionally change the schema, update BOTH sides and
this test.
"""

from __future__ import annotations

from pathlib import Path

from app import perception_config as pc

REPO_ROOT = Path(__file__).resolve().parents[2]
SWIFT_CONFIG = REPO_ROOT / "ios-vqa-app" / "VQASee" / "VQASee" / "PerceptionConfig.swift"
SWIFT_ENGINE = REPO_ROOT / "ios-vqa-app" / "VQASee" / "VQASee" / "LocalPerception.swift"


def test_swift_config_file_exists():
    assert SWIFT_CONFIG.is_file(), f"missing {SWIFT_CONFIG}"


def test_wire_threshold_keys_present_in_swift():
    swift = SWIFT_CONFIG.read_text(encoding="utf-8")
    from dataclasses import asdict

    for key in asdict(pc.Thresholds()).keys():
        assert key in swift, f"threshold wire key '{key}' missing from PerceptionConfig.swift"


def test_python_payload_has_no_roi_product_key():
    payload = pc.default_config().to_dict()
    assert "roi" not in payload
    swift = SWIFT_CONFIG.read_text(encoding="utf-8")
    assert "var roi: ROISet?" in swift
    engine = SWIFT_ENGINE.read_text(encoding="utf-8")
    assert "nearPathROI" not in engine
    assert "nearPathStatus" not in engine


def test_wire_role_and_model_switches_present_in_swift():
    swift = SWIFT_CONFIG.read_text(encoding="utf-8")
    for token in ("var role: String?", "use_multiclass_segmentation", "use_lane_segmentation", "road_backend"):
        assert token in swift, f"role/model-switch wire token '{token}' missing from PerceptionConfig.swift"


def test_default_threshold_values_match_swift_literals():
    swift = SWIFT_CONFIG.read_text(encoding="utf-8")
    t = pc.default_config().thresholds
    assert str(t.seg_traversable_pixel) in swift
