#!/usr/bin/env python3
"""Analyze a VQASee iPhone diagnostic capture offline.

Input is a directory produced by the iOS DiagnosticCaptureRecorder:

    VQASeeDiagnostics/session-.../
      metadata.json
      manifest.jsonl
      frame-0001.jpg
      ...

By default this script summarizes local perception outputs (object counts,
road/depth cues, skipped/in-flight frames). With --run-qwen it also sends each
saved frame through the configured Qwen backend path using run_vqa_from_frame.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def load_manifest(session_dir: Path) -> list[dict[str, Any]]:
    manifest = session_dir / "manifest.jsonl"
    if not manifest.is_file():
        raise FileNotFoundError(f"manifest not found: {manifest}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON at {manifest}:{line_number}: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"manifest row {line_number} is not an object")
        rows.append(row)
    return rows


def load_labels(session_dir: Path) -> list[dict[str, Any]]:
    labels_path = session_dir / "labels.jsonl"
    if not labels_path.is_file():
        return []
    labels: list[dict[str, Any]] = []
    for line_number, line in enumerate(labels_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON at {labels_path}:{line_number}: {exc}") from exc
        if isinstance(row, dict):
            labels.append(row)
    return labels


def _safe_div(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def summarize_labels(labels: list[dict[str, Any]]) -> dict[str, Any]:
    label_counts: Counter[str] = Counter()
    frames_with_labels: set[str] = set()
    for label in labels:
        label_counts[str(label.get("label", "unknown"))] += 1
        frame = str(label.get("frame", ""))
        if frame:
            frames_with_labels.add(frame)

    # Coarse frame/label-level metrics, not pixel/box IoU metrics.
    # `correct` approximates true positives; false_positive/wrong_class/bad_box
    # approximate false positives; missed approximates false negatives.
    true_positive = label_counts.get("correct", 0)
    false_positive = (
        label_counts.get("false_positive", 0)
        + label_counts.get("wrong_class", 0)
        + label_counts.get("bad_box", 0)
    )
    false_negative = label_counts.get("missed", 0)
    precision = _safe_div(true_positive, true_positive + false_positive)
    recall = _safe_div(true_positive, true_positive + false_negative)
    f1 = None
    if precision is not None and recall is not None and precision + recall > 0:
        f1 = round(2 * precision * recall / (precision + recall), 4)

    return {
        "label_count": len(labels),
        "frames_with_labels": len(frames_with_labels),
        "labels": dict(label_counts),
        "coarse_metrics": {
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "note": "coarse label-level metrics; not bbox IoU or pixel segmentation metrics",
        },
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    object_counts: Counter[str] = Counter()
    direction_counts: Counter[str] = Counter()
    event_counts: Counter[str] = Counter()
    road_counts: Counter[str] = Counter()
    depth_counts: Counter[str] = Counter()
    model_status_counts: Counter[str] = Counter()

    for row in rows:
        event_counts[str(row.get("event", "unknown"))] += 1
        perception = row.get("perception") if isinstance(row.get("perception"), dict) else {}
        model_status_counts[str(perception.get("model_status", "unknown"))] += 1
        for obj in perception.get("objects", []) if isinstance(perception.get("objects"), list) else []:
            if not isinstance(obj, dict):
                continue
            kind = str(obj.get("kind", "unknown"))
            direction = str(obj.get("direction", "unknown"))
            object_counts[kind] += 1
            direction_counts[f"{kind}:{direction}"] += 1
        road = perception.get("road_cues") if isinstance(perception.get("road_cues"), dict) else {}
        for key, value in road.items():
            if value != "unknown":
                road_counts[f"{key}:{value}"] += 1
        depth = perception.get("depth_cues") if isinstance(perception.get("depth_cues"), dict) else {}
        for key, value in depth.items():
            if value != "unknown":
                depth_counts[f"{key}:{value}"] += 1

    return {
        "frame_count": len(rows),
        "events": dict(event_counts),
        "model_status": dict(model_status_counts),
        "objects": dict(object_counts),
        "objects_by_direction": dict(direction_counts),
        "road_cues": dict(road_counts),
        "depth_cues": dict(depth_counts),
    }


def run_qwen(session_dir: Path, rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    # Import lazily so summary mode has no backend dependencies beyond stdlib.
    repo_server = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_server))
    from app.vqa_service import run_vqa_from_frame  # pylint: disable=import-error

    results: list[dict[str, Any]] = []
    selected = rows[:limit] if limit > 0 else rows
    for row in selected:
        frame_name = str(row.get("frame", ""))
        frame_path = session_dir / frame_name
        if not frame_path.is_file():
            results.append({"frame": frame_name, "error": "missing_frame_file"})
            continue
        image_base64 = base64.b64encode(frame_path.read_bytes()).decode("ascii")
        mode = str(row.get("mode", "walking"))
        local_context = ""
        perception = row.get("perception") if isinstance(row.get("perception"), dict) else {}
        if perception:
            local_context = str(perception.get("backend_context", ""))
        prompt = (
            "模式=离线诊断。请分析这帧是否存在视觉风险，尤其检查本地模型是否误检/漏检。"
            "请用中文简短输出风险、可疑物体、边界/道路线索和不确定性。"
        )
        if local_context:
            prompt += f"\n本地模型当时输出：{local_context}"
        try:
            result = run_vqa_from_frame(
                prompt=prompt,
                image_base64=image_base64,
                model_override="",
                incremental=False,
                previous_image_base64="",
                fast_response=mode in {"walking", "surroundings"},
            )
        except Exception as exc:  # noqa: BLE001 - diagnostic script should keep going
            results.append({"frame": frame_name, "error": str(exc)})
            continue
        results.append({"frame": frame_name, "qwen": result})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze a VQASee diagnostic capture directory.")
    parser.add_argument("session_dir", type=Path)
    parser.add_argument("--run-qwen", action="store_true", help="Run configured Qwen backend over saved frames.")
    parser.add_argument("--limit", type=int, default=20, help="Max frames for --run-qwen; 0 means all frames.")
    parser.add_argument("--output", type=Path, help="Write JSON report to this path.")
    args = parser.parse_args()

    session_dir = args.session_dir.expanduser().resolve()
    rows = load_manifest(session_dir)
    labels = load_labels(session_dir)
    report: dict[str, Any] = {
        "session_dir": str(session_dir),
        "summary": summarize(rows),
        "label_summary": summarize_labels(labels),
    }
    if args.run_qwen:
        report["qwen_results"] = run_qwen(session_dir=session_dir, rows=rows, limit=args.limit)

    output = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
