"""Contract test: keep the guidance-path wire schema in sync between the Python
source of truth (`app/guidance_path.py`) and its Swift mirror
(`ios-vqa-app/VQASee/VQASee/GuidancePath.swift`). Text-level checks so a rename
on one side fails loudly instead of silently drifting."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SWIFT = REPO_ROOT / "ios-vqa-app" / "VQASee" / "VQASee" / "GuidancePath.swift"


def test_swift_mirror_exists():
    assert SWIFT.is_file(), f"missing Swift mirror: {SWIFT}"


def test_wire_keys_present_in_both():
    swift = SWIFT.read_text(encoding="utf-8")
    # Wire keys emitted by Python to_dict must all appear in the Swift toWire().
    for key in ["status", "coverage", "source", "lines", "kind", "confidence",
                "points", "risk_segments", "half_width", "from_index", "to_index", "reason"]:
        assert f'"{key}"' in swift, f"wire key {key!r} missing from Swift GuidancePath"


def test_status_and_defaults_match():
    swift = SWIFT.read_text(encoding="utf-8")
    from app.guidance_path import MIN_COVERAGE, MIN_POINTS, PATH_STATUS_OK, PATH_STATUS_INSUFFICIENT

    assert f"case {PATH_STATUS_OK}" in swift
    assert f"case {PATH_STATUS_INSUFFICIENT}" in swift
    # Thresholds must match numerically so GT and device agree on "insufficient".
    assert f"minCoverage = {MIN_COVERAGE}" in swift
    assert f"minPoints = {MIN_POINTS}" in swift
