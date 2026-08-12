"""Generate VQASee diagnostic evaluation reports.

The report is for the VQASee evolution team, not end users. It turns a local
capture session (manifest + structured labels) into product/system/model/UI
findings and concrete task suggestions.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any


VEHICLE_KINDS = {"car", "truck", "bus", "motorcycle", "bicycle"}
VEHICLE_WORDS = ["车", "车辆", "摩托", "自行车", "电动车", "car", "vehicle", "motorcycle", "bicycle"]
INDOOR_WORDS = ["室内", "办公室", "办公", "走廊", "地板", "水桶", "桶装水", "indoor", "office", "hallway"]
PERSON_WORDS = ["人", "行人", "person", "human"]


def _safe_div(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _contains_any(text: str, words: list[str]) -> bool:
    lowered = text.lower()
    return any(word.lower() in lowered for word in words)


def _label_frame(label: dict[str, Any]) -> str:
    return _text(label.get("frame"))


def _all_label_text(label: dict[str, Any]) -> str:
    return "\n".join(
        _text(label.get(key))
        for key in ["label", "note", "true_scene", "true_risks", "false_positives", "missed_risks"]
    )


def _manifest_objects(row: dict[str, Any]) -> list[dict[str, Any]]:
    perception = row.get("perception") if isinstance(row.get("perception"), dict) else {}
    objects = perception.get("objects") if isinstance(perception.get("objects"), list) else []
    return [obj for obj in objects if isinstance(obj, dict)]


def _has_qwen_result(row: dict[str, Any]) -> bool:
    # Future-proof: accept several likely keys once iOS/backend starts storing raw
    # or fused model outputs in diagnostic metadata.
    for key in ["qwen", "qwen_result", "qwen_raw", "vqa_result", "model_output", "backend_result"]:
        if key in row:
            return True
    return False


def _make_finding(
    *,
    code: str,
    severity: str,
    owner: str,
    title: str,
    evidence: str,
    recommendation: str,
) -> dict[str, str]:
    return {
        "code": code,
        "severity": severity,
        "owner": owner,
        "title": title,
        "evidence": evidence,
        "recommendation": recommendation,
    }


def _task_from_finding(finding: dict[str, str]) -> dict[str, str]:
    owner = finding["owner"]
    if owner == "model":
        primary = "全麦"
    elif owner == "system":
        primary = "罗根"
    elif owner == "ui":
        primary = "思余"
    else:
        primary = "乔布斯"
    return {
        "title": finding["recommendation"],
        "primary": primary,
        "evidence": finding["evidence"],
        "acceptance": f"下一轮诊断报告中 {finding['code']} 指标下降或证据消失。",
    }


def generate_diagnostic_report(
    *,
    session_id: str,
    rows: list[dict[str, Any]],
    labels: list[dict[str, Any]],
) -> dict[str, Any]:
    event_counts = Counter(str(row.get("event", "unknown")) for row in rows)
    label_counts = Counter(str(label.get("label", "unknown")) for label in labels)
    frames_with_labels = {_label_frame(label) for label in labels if _label_frame(label)}

    object_counts: Counter[str] = Counter()
    vehicle_detection_frames: set[str] = set()
    person_detection_frames: set[str] = set()
    qwen_result_frames = 0
    path_status_counts: Counter[str] = Counter()
    depth_capability_counts: Counter[str] = Counter()
    segmentation_capability_counts: Counter[str] = Counter()
    path_guidance_frames = 0
    for row in rows:
        frame = _text(row.get("backend_saved_frame")) or _text(row.get("frame"))
        if _has_qwen_result(row):
            qwen_result_frames += 1
        perception = row.get("perception") if isinstance(row.get("perception"), dict) else {}
        path_guidance = perception.get("path_guidance") if isinstance(perception.get("path_guidance"), dict) else {}
        if path_guidance:
            path_guidance_frames += 1
            path_status_counts[str(path_guidance.get("near_path_status", "unknown"))] += 1
            depth_capability_counts[str(path_guidance.get("depth_capability", "unknown"))] += 1
            segmentation_capability_counts[str(path_guidance.get("segmentation_capability", "unknown"))] += 1
        for obj in _manifest_objects(row):
            kind = _text(obj.get("kind")) or "unknown"
            object_counts[kind] += 1
            if kind in VEHICLE_KINDS and frame:
                vehicle_detection_frames.add(frame)
            if kind == "person" and frame:
                person_detection_frames.add(frame)

    false_positive_labels = [
        label for label in labels
        if str(label.get("label")) in {"false_positive", "wrong_class"}
        or bool(_text(label.get("false_positives")))
    ]
    missed_risk_labels = [
        label for label in labels
        if str(label.get("label")) in {"missed_risk", "missed"}
        or bool(_text(label.get("missed_risks")))
    ]
    output_error_labels = [label for label in labels if str(label.get("label")) == "output_error"]

    indoor_label_count = sum(
        1 for label in labels
        if _contains_any(_all_label_text(label), INDOOR_WORDS)
    )
    vehicle_false_positive_labels = [
        label for label in false_positive_labels
        if _contains_any(_all_label_text(label), VEHICLE_WORDS)
    ]
    person_false_positive_labels = [
        label for label in false_positive_labels
        if _contains_any(_all_label_text(label), PERSON_WORDS)
    ]

    total_frames = len(rows)
    sent_to_backend = event_counts.get("sent_to_backend", 0)
    in_flight = event_counts.get("captured_while_in_flight", 0)
    skipped = event_counts.get("skipped_before_backend", 0)

    findings: list[dict[str, str]] = []

    if total_frames and _safe_div(in_flight, total_frames) >= 0.5:
        findings.append(_make_finding(
            code="high_in_flight_ratio",
            severity="high",
            owner="system",
            title="后端实时链路跟不上采集节奏",
            evidence=f"总帧 {total_frames}，backend in-flight 帧 {in_flight}（{_safe_div(in_flight, total_frames):.0%}）。",
            recommendation="做 latest-frame-wins / Qwen 低频复核 / iPhone 本地即时提示。",
        ))

    if vehicle_false_positive_labels or (indoor_label_count > 0 and vehicle_detection_frames):
        evidence = f"vehicle 检测帧 {len(vehicle_detection_frames)}；vehicle 误报标注 {len(vehicle_false_positive_labels)}；室内相关标注 {indoor_label_count}。"
        findings.append(_make_finding(
            code="indoor_vehicle_false_positive",
            severity="high",
            owner="model",
            title="室内 vehicle 类误报候选",
            evidence=evidence,
            recommendation="室内/低速风险观察中对车辆类检测降权，并要求连续帧或 Qwen 二次确认。",
        ))

    if person_false_positive_labels:
        findings.append(_make_finding(
            code="person_false_positive",
            severity="medium",
            owner="model",
            title="person 类误报候选",
            evidence=f"person 误报/类别错误标注 {len(person_false_positive_labels)} 条；person 检测帧 {len(person_detection_frames)}。",
            recommendation="分析 person 误报位置和 bbox，画面边缘/底部小框应降权。",
        ))

    if missed_risk_labels:
        findings.append(_make_finding(
            code="missed_risk",
            severity="high",
            owner="model",
            title="存在用户标注的漏报风险",
            evidence=f"漏报标注 {len(missed_risk_labels)} 条。",
            recommendation="把漏报帧加入 model-lab 回归样例，优先修高风险类别召回。",
        ))

    if output_error_labels:
        findings.append(_make_finding(
            code="model_output_error",
            severity="high",
            owner="model",
            title="用户标注模型输出异常",
            evidence=f"模型输出异常标注 {len(output_error_labels)} 条。",
            recommendation="保存 Qwen raw output，区分 JSON 截断、parser bug 和模型发散。",
        ))

    if total_frames and path_guidance_frames == 0:
        findings.append(_make_finding(
            code="missing_path_guidance_signal",
            severity="medium",
            owner="system",
            title="诊断数据缺少本地通行路径信号",
            evidence=f"总帧 {total_frames}，带 path_guidance 的帧 {path_guidance_frames}。",
            recommendation="把 LocalPathGuidanceSignal 写入诊断 manifest，并让 overlay 完全由该 signal 驱动。",
        ))

    depth_not_active = sum(count for key, count in depth_capability_counts.items() if key != "active")
    segmentation_not_active = sum(count for key, count in segmentation_capability_counts.items() if key != "active")
    if path_guidance_frames and (depth_not_active or segmentation_not_active):
        findings.append(_make_finding(
            code="path_guidance_capability_gap",
            severity="medium",
            owner="system",
            title="本地通行路径仍缺少深度/分割能力",
            evidence=f"path_guidance 帧 {path_guidance_frames}；depth={dict(depth_capability_counts)}；segmentation={dict(segmentation_capability_counts)}。",
            recommendation="评估 ARKit/LiDAR depth、地面分割或可通行区域模型，逐步替换 YOLO-only 通行判断。",
        ))

    if sent_to_backend and qwen_result_frames == 0:
        findings.append(_make_finding(
            code="missing_qwen_raw_output",
            severity="medium",
            owner="system",
            title="诊断数据缺少 Qwen 原始/最终输出",
            evidence=f"sent_to_backend 帧 {sent_to_backend}，manifest 中带 Qwen/VQA 输出的帧 {qwen_result_frames}。",
            recommendation="把 Qwen raw output、schema_name、qwen_http_ms、fused result 写入 diagnostic manifest。",
        ))

    if labels and not any(_text(label.get("true_scene")) or _text(label.get("true_risks")) for label in labels):
        findings.append(_make_finding(
            code="unstructured_labels",
            severity="medium",
            owner="ui",
            title="标注缺少结构化 ground truth",
            evidence=f"已有标注 {len(labels)} 条，但 true_scene/true_risks 为空。",
            recommendation="引导用户优先填写真实画面和真实风险，减少只填备注。",
        ))

    if not labels:
        findings.append(_make_finding(
            code="no_ground_truth_labels",
            severity="low",
            owner="product",
            title="本 session 暂无人工 ground truth",
            evidence="没有 labels.jsonl 标注，报告只能基于本地模型输出做候选判断。",
            recommendation="先标注明显误报/漏报帧，再生成更可信报告。",
        ))

    headline = "未发现明确高优先级问题。"
    if findings:
        top = sorted(findings, key=lambda item: {"high": 0, "medium": 1, "low": 2}.get(item["severity"], 3))[0]
        headline = top["title"]

    return {
        "session_id": session_id,
        "headline": headline,
        "metrics": {
            "frame_count": total_frames,
            "sent_to_backend": sent_to_backend,
            "captured_while_in_flight": in_flight,
            "skipped_before_backend": skipped,
            "in_flight_ratio": _safe_div(in_flight, total_frames),
            "label_count": len(labels),
            "frames_with_labels": len(frames_with_labels),
            "qwen_result_frames": qwen_result_frames,
            "local_objects": dict(object_counts),
            "labels": dict(label_counts),
            "vehicle_detection_frames": len(vehicle_detection_frames),
            "person_detection_frames": len(person_detection_frames),
            "vehicle_false_positive_labels": len(vehicle_false_positive_labels),
            "missed_risk_labels": len(missed_risk_labels),
            "output_error_labels": len(output_error_labels),
            "path_guidance_frames": path_guidance_frames,
            "path_near_status": dict(path_status_counts),
            "path_depth_capability": dict(depth_capability_counts),
            "path_segmentation_capability": dict(segmentation_capability_counts),
        },
        "findings": findings,
        "task_suggestions": [_task_from_finding(finding) for finding in findings],
        "report_scope": "local_manifest_and_human_labels_only",
        "note": "This report supports VQASee evolution decisions; it is not shown to end users.",
    }


def generate_report_from_session_dir(session_id: str, session_dir: Path) -> dict[str, Any]:
    from app.diagnostic_api import _load_labels  # local import avoids startup cycle

    manifest_path = session_dir / "manifest.jsonl"
    rows: list[dict[str, Any]] = []
    if manifest_path.is_file():
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = __import__("json").loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    labels = _load_labels(session_dir)
    return generate_diagnostic_report(session_id=session_id, rows=rows, labels=labels)
