"""Export VQASee diagnostic sessions into path-guidance dataset manifests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _frame_key(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value if value.startswith("frames/") else f"frames/{Path(value).name}"


def _has_human_label(label: dict[str, Any]) -> bool:
    for key in ("label", "true_risks", "false_positives", "missed_risks", "note"):
        if str(label.get(key, "")).strip():
            return True
    return False


def export_session_path_manifest(session_id: str, session_dir: Path) -> list[dict[str, Any]]:
    manifest_rows = _load_jsonl(session_dir / "manifest.jsonl")
    labels = _load_jsonl(session_dir / "labels.jsonl")
    labels_by_frame: dict[str, list[dict[str, Any]]] = {}
    for label in labels:
        frame = _frame_key(label.get("frame"))
        if frame:
            labels_by_frame.setdefault(frame, []).append(label)

    exported: list[dict[str, Any]] = []
    for row in manifest_rows:
        frame = _frame_key(row.get("backend_saved_frame") or row.get("frame"))
        if not frame:
            continue
        perception = row.get("perception") if isinstance(perception := row.get("perception"), dict) else {}
        path_guidance = perception.get("path_guidance") if isinstance(perception.get("path_guidance"), dict) else {}
        label_candidates = labels_by_frame.get(frame, [])
        labeled = any(_has_human_label(label) for label in label_candidates)
        if not labeled and not path_guidance:
            continue
        exported.append(
            {
                "frame_id": f"{session_id}/{frame}",
                "image": frame,
                "split": str(row.get("mode") or "diagnostic"),
                "scene_tags": _scene_tags(row, label_candidates),
                "ground_truth": {},
                "prediction": _prediction_from_path_guidance(path_guidance),
                "source_event": row.get("event", ""),
                "source_reason": row.get("reason", ""),
            }
        )
    return exported


KEEP_PREDICTION_KEYS = (
    "blocked_regions",
    "uncertain_regions",
    "guidance_corridor",
    "guidance_path",
    "depth_capability",
    "segmentation_capability",
    "traversable_ratio",
    "traversable_grid",
    "prediction_source",
)


def _prediction_from_path_guidance(path_guidance: dict[str, Any]) -> dict[str, Any]:
    if not path_guidance:
        return {}
    return {key: path_guidance[key] for key in KEEP_PREDICTION_KEYS if key in path_guidance}


def _scene_tags(row: dict[str, Any], labels: list[dict[str, Any]]) -> list[str]:
    tags = [str(row.get("mode") or "diagnostic")]
    for label in labels[:1]:
        true_scene = str(label.get("true_scene", ""))
        if "室内" in true_scene:
            tags.append("indoor")
        if "水桶" in true_scene:
            tags.append("water-bottle")
        if "走廊" in true_scene:
            tags.append("corridor")
    return sorted(set(tag for tag in tags if tag))


def manifest_to_jsonl(rows: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + ("\n" if rows else "")
