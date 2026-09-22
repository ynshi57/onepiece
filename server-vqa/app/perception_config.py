"""Versioned perception configuration — the closed-loop hub.

This is the single source of truth for the tunable knobs of the VQASee on-device
path-guidance layer. The macOS offline harness ("test the iPhone") and the OTA
endpoint ("update the iPhone") both consume this exact schema, so a value tuned
on the platform can be validated by the harness and then shipped to the phone.

Three-zone ROI rectangles (near/left/right) were retired as a product signal on
2026-09-22. Leftover ``roi`` keys in old stored payloads are ignored on load.
Posting ``roi`` on bump is a hard error so a stale editor cannot ship boxes.

Safety stance: validation is strict and explicit. An out-of-range or malformed
config raises ``ConfigValidationError`` rather than being silently clamped.
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
VALID_ROLES = ("pedestrian", "vehicle")
VALID_ROAD_BACKENDS = ("off", "twinlite", "mc5")
# Three-zone thresholds retired with the ROI boxes. Old files may still contain
# them; load ignores, bump with these keys is rejected as unknown.
RETIRED_THRESHOLD_KEYS = frozenset(
    {
        "near_blocked_area",
        "side_blocked_area",
        "seg_near_caution_ratio",
        "seg_side_caution_ratio",
    }
)


class ConfigValidationError(ValueError):
    """Raised when a perception config is out of range or malformed."""


@dataclass(frozen=True)
class Thresholds:
    seg_traversable_pixel: float = 0.55

    def validate(self) -> None:
        for key, value in asdict(self).items():
            if not isinstance(value, (int, float)):
                raise ConfigValidationError(f"thresholds.{key} must be a number, got {value!r}")
            if not (0.0 <= float(value) <= 1.0):
                raise ConfigValidationError(f"thresholds.{key}={value} out of range [0,1]")


@dataclass(frozen=True)
class PerceptionConfig:
    version: int = 1
    updated_at: str = ""
    thresholds: Thresholds = field(default_factory=Thresholds)
    # Wire keys match PerceptionConfig.swift: pedestrian|vehicle. Live default
    # does not run the retired binary segmenter; mc5 only when the flag is on.
    role: str = "pedestrian"
    use_multiclass_segmentation: bool = False
    use_lane_segmentation: bool = True
    # Swap-point for the on-device road-surface model. Live App default is TwinLiteNet
    # (product default as of 2026-09-22). mc5 is CamVid role-conditioned traversable.
    # off runs neither. Three ROI rectangles are not a product signal.
    road_backend: str = "twinlite"

    def validate(self) -> None:
        if not isinstance(self.version, int) or self.version < 1:
            raise ConfigValidationError(f"version must be an int >= 1, got {self.version!r}")
        if self.role not in VALID_ROLES:
            raise ConfigValidationError(
                f"role={self.role!r} is not one of {'|'.join(VALID_ROLES)}"
            )
        if not isinstance(self.use_multiclass_segmentation, bool):
            raise ConfigValidationError("use_multiclass_segmentation must be a boolean")
        if not isinstance(self.use_lane_segmentation, bool):
            raise ConfigValidationError("use_lane_segmentation must be a boolean")
        if self.road_backend not in VALID_ROAD_BACKENDS:
            raise ConfigValidationError(
                f"road_backend={self.road_backend!r} is not one of {'|'.join(VALID_ROAD_BACKENDS)}"
            )
        self.thresholds.validate()

    def numeric_payload(self) -> dict[str, Any]:
        """Only the values that affect perception behavior (no metadata)."""
        return {
            "thresholds": asdict(self.thresholds),
            "role": self.role,
            "use_multiclass_segmentation": self.use_multiclass_segmentation,
            "use_lane_segmentation": self.use_lane_segmentation,
            "road_backend": self.road_backend,
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


def config_from_dict(data: dict[str, Any]) -> PerceptionConfig:
    if not isinstance(data, dict):
        raise ConfigValidationError("config must be a JSON object")
    thresholds_data = data.get("thresholds") or {}
    if not isinstance(thresholds_data, dict):
        raise ConfigValidationError("thresholds must be an object")
    default_thresholds = asdict(Thresholds())
    merged_thresholds = {**default_thresholds}
    for key, value in thresholds_data.items():
        if key in RETIRED_THRESHOLD_KEYS:
            continue
        if key not in default_thresholds:
            raise ConfigValidationError(f"unknown threshold key: {key}")
        merged_thresholds[key] = float(value)
    role = data.get("role", "pedestrian")
    if not isinstance(role, str):
        raise ConfigValidationError(f"role must be a string, got {role!r}")
    use_mc = data.get("use_multiclass_segmentation", False)
    use_lane = data.get("use_lane_segmentation", True)
    road_backend = data.get("road_backend", "twinlite")
    if not isinstance(road_backend, str):
        raise ConfigValidationError(f"road_backend must be a string, got {road_backend!r}")
    config = PerceptionConfig(
        version=int(data.get("version", 1)),
        updated_at=str(data.get("updated_at", "")),
        thresholds=Thresholds(**merged_thresholds),
        role=role,
        use_multiclass_segmentation=use_mc,
        use_lane_segmentation=use_lane,
        road_backend=road_backend,
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
    bad file cannot masquerade as defaults. Leftover ``roi`` keys are ignored.
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
        thresholds=config.thresholds,
        role=config.role,
        use_multiclass_segmentation=config.use_multiclass_segmentation,
        use_lane_segmentation=config.use_lane_segmentation,
        road_backend=config.road_backend,
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

    ``updates`` may contain ``thresholds`` (live keys only), ``role``,
    ``use_multiclass_segmentation``, ``use_lane_segmentation``, and
    ``road_backend``. Posting ``roi`` is rejected: three-zone boxes are not a
    product signal. The new version is ``current.version + 1``. Validation runs
    before anything is written; on failure nothing is saved.
    """
    if "roi" in updates:
        raise ConfigValidationError(
            "roi retired: three-zone near/left/right boxes are not a product signal"
        )
    current = load_active_config()
    merged = current.to_dict()
    if "thresholds" in updates:
        if not isinstance(updates["thresholds"], dict):
            raise ConfigValidationError("updates.thresholds must be an object")
        for key in updates["thresholds"]:
            if key in RETIRED_THRESHOLD_KEYS:
                raise ConfigValidationError(
                    f"threshold {key!r} retired with three-zone ROI"
                )
        merged["thresholds"].update(updates["thresholds"])
    for key in ("role", "use_multiclass_segmentation", "use_lane_segmentation", "road_backend"):
        if key in updates:
            merged[key] = updates[key]
    merged["version"] = current.version + 1
    new_config = config_from_dict(merged)
    save_config(new_config)
    return new_config
