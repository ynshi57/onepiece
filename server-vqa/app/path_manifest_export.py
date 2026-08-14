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


def _label_ground_truth(label: dict[str, Any]) -> dict[str, str] | None:
    label_type = str(label.get("label", ""))
    true_risks = str(label.get("true_risks", ""))
    false_positives = str(label.get("false_positives", ""))
    missed_risks = str(label.get("missed_risks", ""))
    note = str(label.get("note", ""))
    combined = "\n".join([label_type, true_risks, false_positives, missed_risks, note]).lower()

    # Conservative mapping: only mark candidateOpen when user explicitly says no
    # obvious risk. Otherwise prefer caution so releases optimize for recall.
    if label_type in {"no_obvious_risk", "scene_truth"} and any(text in combined for text in ["无明显风险", "无风险", "no obvious", "no risk"]):
        status = "candidateOpen"
        focus = "unknown"
    elif label_type in {"missed_risk", "wrong_direction"} or missed_risks.strip():
        status = "caution"
        focus = _direction_from_text(combined)
    elif label_type in {"false_positive", "wrong_class"} or false_positives.strip():
        # If system over-triggered, the ground truth often has no risk; but when
        # the true_risks text mentions a real obstacle, keep caution.
        status = "candidateOpen" if not _has_risk_text(true_risks) else "caution"
        focus = _direction_from_text(combined)
    elif label_type == "image_quality_issue":
        status = "unknown"
        focus = "unknown"
    elif label_type == "output_error":
        status = "unknown"
        focus = "unknown"
    else:
        return None

    return {
        "near_path_status": status,
        "left_front_status": "candidateOpen" if status == "candidateOpen" else "unknown",
        "right_front_status": "candidateOpen" if status == "candidateOpen" else "unknown",
        "focus_direction": focus,
    }


def _has_risk_text(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in ["风险", "障碍", "台阶", "水桶", "椅子", "车", "人", "blocked", "obstacle"])


def _direction_from_text(text: str) -> str:
    if any(word in text for word in ["右", "right"]):
        return "right"
    if any(word in text for word in ["左", "left"]):
        return "left"
    if any(word in text for word in ["前", "正前", "center", "front"]):
        return "center"
    return "unknown"


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
        perception = row.get("perception") if isinstance(row.get("perception"), dict) else {}
        path_guidance = perception.get("path_guidance") if isinstance(perception.get("path_guidance"), dict) else {}
        label_candidates = labels_by_frame.get(frame, [])
        ground_truth = None
        for label in label_candidates:
            ground_truth = _label_ground_truth(label)
            if ground_truth:
                break
        if not ground_truth and not path_guidance:
            continue
        exported.append(
            {
                "frame_id": f"{session_id}/{frame}",
                "image": frame,
                "split": str(row.get("mode") or "diagnostic"),
                "scene_tags": _scene_tags(row, label_candidates),
                "ground_truth": ground_truth or {},
                "prediction": _prediction_from_path_guidance(path_guidance),
                "source_event": row.get("event", ""),
                "source_reason": row.get("reason", ""),
            }
        )
    return exported


def _prediction_from_path_guidance(path_guidance: dict[str, Any]) -> dict[str, Any]:
    if not path_guidance:
        return {}
    return {
        "near_path_status": path_guidance.get("near_path_status", "unknown"),
        "left_front_status": path_guidance.get("left_front_status", "unknown"),
        "right_front_status": path_guidance.get("right_front_status", "unknown"),
        "focus_direction": path_guidance.get("focus_direction", "unknown"),
    }


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
