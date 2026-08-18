"""Persist VQASee path-guidance evaluation baselines.

A baseline is a small JSON snapshot of the scalar metrics produced by
``evaluate_path_guidance`` for one dataset or diagnostic session. It lets the
platform compare a later run against a known-good point so quality regressions
become reproducible evidence instead of subjective memory.

Baselines intentionally store only aggregate metrics plus lightweight metadata
(name, timestamp, sample count, source). They never store images, absolute
image paths, or raw model output, so they are safe to commit.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Scalar metrics tracked in every baseline. Kept explicit so a schema change in
# the evaluator does not silently add noisy fields to committed baselines.
TRACKED_METRICS = (
    "frame_count",
    "labeled_frames",
    "status_accuracy",
    "focus_direction_accuracy",
    "unknown_prediction_rate",
    "risk_miss_count",
    "false_block_count",
    "missing_prediction_count",
)

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def baseline_root() -> Path:
    configured = os.getenv("VQASEE_EVAL_BASELINE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[1] / "eval-baselines"


def safe_baseline_name(name: str) -> str:
    cleaned = _SAFE_NAME.sub("-", name.strip()).strip("-.")
    if not cleaned:
        raise ValueError("baseline name is empty after sanitization")
    return cleaned


def metrics_from_report(report: dict[str, Any]) -> dict[str, Any]:
    return {key: report.get(key) for key in TRACKED_METRICS}


def save_baseline(name: str, report: dict[str, Any], *, source: str) -> Path:
    safe_name = safe_baseline_name(name)
    root = baseline_root()
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": safe_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "sample_count": int(report.get("labeled_frames") or 0),
        "metrics": metrics_from_report(report),
    }
    path = root / f"{safe_name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_baseline(name: str) -> dict[str, Any] | None:
    path = baseline_root() / f"{safe_baseline_name(name)}.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"corrupt baseline at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"baseline at {path} is not an object")
    return value


def list_baselines() -> list[dict[str, Any]]:
    root = baseline_root()
    if not root.is_dir():
        return []
    baselines: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            baselines.append(value)
    return baselines
