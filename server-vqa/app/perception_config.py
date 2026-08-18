"""Versioned perception configuration — the closed-loop hub.

This is the single source of truth for the tunable knobs of the VQASee on-device
path-guidance layer: the three region-of-interest rectangles and the small set of
decision thresholds. The macOS offline harness ("test the iPhone") and the OTA
endpoint ("update the iPhone") both consume this exact schema, so a value tuned
on the platform can be validated by the harness and then shipped to the phone.

Defaults MUST equal the current compiled-in iOS constants
(``LocalPathGuidanceEngine`` in ``LocalPerception.swift``), so adopting the config
is a no-op until someone deliberately changes and bumps a version.

Safety stance: validation is strict and explicit. An out-of-range or malformed
config raises ``ConfigValidationError`` rather than being silently clamped, so a
bad OTA payload can never quietly widen a "blocked" call into "open".
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONFIG_SCHEMA_VERSION = 1


class ConfigValidationError(ValueError):
    """Raised when a perception config is out of range or malformed."""


@dataclass(frozen=True)
class ROI:
    """Normalized Vision-style rect (origin lower-left), all in 0..1."""

    x: float
    y: float
    w: float
    h: float

    def validate(self, name: str) -> None:
        for key, value in (("x", self.x), ("y", self.y), ("w", self.w), ("h", self.h)):
            if not isinstance(value, (int, float)):
                raise ConfigValidationError(f"roi.{name}.{key} must be a number, got {value!r}")
            if not (0.0 <= float(value) <= 1.0):
                raise ConfigValidationError(f"roi.{name}.{key}={value} out of range [0,1]")
        if self.w <= 0 or self.h <= 0:
            raise ConfigValidationError(f"roi.{name} width/height must be > 0")
        if self.x + self.w > 1.0 + 1e-6:
            raise ConfigValidationError(f"roi.{name} x+w={self.x + self.w} exceeds 1.0")
        if self.y + self.h > 1.0 + 1e-6:
            raise ConfigValidationError(f"roi.{name} y+h={self.y + self.h} exceeds 1.0")


@dataclass(frozen=True)
class Thresholds:
    near_blocked_area: float = 0.82
    side_blocked_area: float = 0.86
    seg_near_caution_ratio: float = 0.35
    seg_side_caution_ratio: float = 0.30
    seg_traversable_pixel: float = 0.55

    def validate(self) -> None:
        for key, value in asdict(self).items():
            if not isinstance(value, (int, float)):
                raise ConfigValidationError(f"thresholds.{key} must be a number, got {value!r}")
            if not (0.0 <= float(value) <= 1.0):
                raise ConfigValidationError(f"thresholds.{key}={value} out of range [0,1]")


# Defaults mirror LocalPathGuidanceEngine in ios-vqa-app/.../LocalPerception.swift.
DEFAULT_NEAR_ROI = ROI(x=0.25, y=0.00, w=0.50, h=0.58)
DEFAULT_LEFT_ROI = ROI(x=0.00, y=0.05, w=0.42, h=0.62)
DEFAULT_RIGHT_ROI = ROI(x=0.58, y=0.05, w=0.42, h=0.62)


@dataclass(frozen=True)
class PerceptionConfig:
    version: int = 1
    updated_at: str = ""
    near_roi: ROI = field(default_factory=lambda: DEFAULT_NEAR_ROI)
    left_roi: ROI = field(default_factory=lambda: DEFAULT_LEFT_ROI)
    right_roi: ROI = field(default_factory=lambda: DEFAULT_RIGHT_ROI)
    thresholds: Thresholds = field(default_factory=Thresholds)

    def validate(self) -> None:
        if not isinstance(self.version, int) or self.version < 1:
            raise ConfigValidationError(f"version must be an int >= 1, got {self.version!r}")
        self.near_roi.validate("near")
        self.left_roi.validate("left")
        self.right_roi.validate("right")
        self.thresholds.validate()

    def numeric_payload(self) -> dict[str, Any]:
        """Only the values that affect perception behavior (no metadata)."""
        return {
            "roi": {
                "near": asdict(self.near_roi),
                "left": asdict(self.left_roi),
                "right": asdict(self.right_roi),
            },
            "thresholds": asdict(self.thresholds),
        }

    def content_hash(self) -> str:
        blob = json.dumps(self.numeric_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        payload = self.numeric_payload()
        payload["version"] = self.version
        payload["updated_at"] = self.updated_at
        payload["hash"] = self.content_hash()
        return payload


def default_config() -> PerceptionConfig:
    return PerceptionConfig(version=1, updated_at="")


def _roi_from_dict(name: str, data: Any) -> ROI:
    if not isinstance(data, dict):
        raise ConfigValidationError(f"roi.{name} must be an object")
    try:
        return ROI(x=float(data["x"]), y=float(data["y"]), w=float(data["w"]), h=float(data["h"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigValidationError(f"roi.{name} malformed: {exc}") from exc


def config_from_dict(data: dict[str, Any]) -> PerceptionConfig:
    if not isinstance(data, dict):
        raise ConfigValidationError("config must be a JSON object")
    roi = data.get("roi") or {}
    thresholds_data = data.get("thresholds") or {}
    if not isinstance(thresholds_data, dict):
        raise ConfigValidationError("thresholds must be an object")
    default_thresholds = asdict(Thresholds())
    merged_thresholds = {**default_thresholds}
    for key, value in thresholds_data.items():
        if key not in default_thresholds:
            raise ConfigValidationError(f"unknown threshold key: {key}")
        merged_thresholds[key] = float(value)
    config = PerceptionConfig(
        version=int(data.get("version", 1)),
        updated_at=str(data.get("updated_at", "")),
        near_roi=_roi_from_dict("near", roi.get("near", asdict(DEFAULT_NEAR_ROI))),
        left_roi=_roi_from_dict("left", roi.get("left", asdict(DEFAULT_LEFT_ROI))),
        right_roi=_roi_from_dict("right", roi.get("right", asdict(DEFAULT_RIGHT_ROI))),
        thresholds=Thresholds(**merged_thresholds),
    )
    config.validate()
    return config


# ---------------------------------------------------------------------------
# Persistence (server single source of truth for the active config)
# ---------------------------------------------------------------------------

def config_store_path() -> Path:
    configured = os.getenv("VQASEE_PERCEPTION_CONFIG_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.cwd() / "data" / "perception_config.json"


def load_active_config() -> PerceptionConfig:
    """Return the stored active config, or the built-in default if none saved.

    A corrupt store is a hard error, not a silent fallback: we surface it so a
    bad file cannot masquerade as defaults.
    """
    path = config_store_path()
    if not path.is_file():
        return default_config()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigValidationError(f"stored perception config is not valid JSON: {path}: {exc}") from exc
    return config_from_dict(data)


def save_config(config: PerceptionConfig) -> Path:
    config.validate()
    stamped = PerceptionConfig(
        version=config.version,
        updated_at=datetime.now(timezone.utc).isoformat(),
        near_roi=config.near_roi,
        left_roi=config.left_roi,
        right_roi=config.right_roi,
        thresholds=config.thresholds,
    )
    path = config_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(stamped.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def bump_and_save(updates: dict[str, Any]) -> PerceptionConfig:
    """Apply partial updates to the active config, bump the version, save.

    ``updates`` may contain ``roi`` (any of near/left/right) and ``thresholds``
    (any subset). The new version is ``current.version + 1``. Validation runs
    before anything is written; on failure nothing is saved.
    """
    current = load_active_config()
    merged = current.to_dict()
    if "roi" in updates:
        if not isinstance(updates["roi"], dict):
            raise ConfigValidationError("updates.roi must be an object")
        for region, value in updates["roi"].items():
            if region not in ("near", "left", "right"):
                raise ConfigValidationError(f"unknown roi region: {region}")
            merged["roi"][region] = value
    if "thresholds" in updates:
        if not isinstance(updates["thresholds"], dict):
            raise ConfigValidationError("updates.thresholds must be an object")
        merged["thresholds"].update(updates["thresholds"])
    merged["version"] = current.version + 1
    new_config = config_from_dict(merged)
    save_config(new_config)
    return new_config
