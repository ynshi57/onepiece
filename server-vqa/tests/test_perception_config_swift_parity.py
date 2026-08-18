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


def test_swift_config_file_exists():
    assert SWIFT_CONFIG.is_file(), f"missing {SWIFT_CONFIG}"


def test_wire_threshold_keys_present_in_swift():
    swift = SWIFT_CONFIG.read_text(encoding="utf-8")
    from dataclasses import asdict

    for key in asdict(pc.Thresholds()).keys():
        assert key in swift, f"threshold wire key '{key}' missing from PerceptionConfig.swift"


def test_wire_roi_shape_present_in_swift():
    swift = SWIFT_CONFIG.read_text(encoding="utf-8")
    # ROIWire fields and the near/left/right set.
    for token in ("var x: Double", "var y: Double", "var w: Double", "var h: Double",
                  "var near:", "var left:", "var right:"):
        assert token in swift, f"ROI wire token '{token}' missing from PerceptionConfig.swift"


def test_default_threshold_values_match_swift_literals():
    swift = SWIFT_CONFIG.read_text(encoding="utf-8")
    t = pc.default_config().thresholds
    for literal in (str(t.near_blocked_area), str(t.side_blocked_area),
                    str(t.seg_near_caution_ratio), str(t.seg_side_caution_ratio),
                    str(t.seg_traversable_pixel)):
        assert literal in swift, f"default threshold literal '{literal}' not found in Swift defaults"


def test_default_roi_values_match_swift_engine_constants():
    """Swift default ROIs come from LocalPathGuidanceEngine constants; verify those
    literals equal the Python defaults so the two stay in lock-step."""
    engine = (REPO_ROOT / "ios-vqa-app" / "VQASee" / "VQASee" / "LocalPerception.swift").read_text(encoding="utf-8")
    cfg = pc.default_config()
    # near: x=0.25 y=0.00 w=0.50 h=0.58
    assert "CGRect(x: 0.25, y: 0.00, width: 0.50, height: 0.58)" in engine
    assert "CGRect(x: 0.00, y: 0.05, width: 0.42, height: 0.62)" in engine
    assert "CGRect(x: 0.58, y: 0.05, width: 0.42, height: 0.62)" in engine
    # And Python agrees.
    assert (cfg.near_roi.x, cfg.near_roi.w) == (0.25, 0.50)
    assert (cfg.left_roi.w, cfg.left_roi.h) == (0.42, 0.62)
    assert cfg.right_roi.x == 0.58
