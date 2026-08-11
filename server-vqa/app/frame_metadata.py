"""Normalize client frame metadata used by walking safety fast paths.

The backend cannot reliably measure blur/exposure from a compressed JPEG without
an image-processing dependency, and a single frame cannot provide physical
meter-level distance. This module therefore treats client-provided quality/ROI
metadata as a safety hint: validate it, expose uncertainty, and never let it
silently hide risks.
"""

from __future__ import annotations

from typing import Optional


_ALLOWED_BLUR = {"ok", "blurry", "unknown"}
_ALLOWED_EXPOSURE = {"ok", "too_dark", "too_bright", "unknown"}
_ALLOWED_OCCLUSION = {"ok", "covered", "unknown"}
_ALLOWED_CONFIDENCE = {"low", "medium", "high"}
_MAX_QUALITY_REASON_CHARS = 120


def _clean_enum(value: object, allowed: set[str], default: str) -> str:
    if not isinstance(value, str):
        return default
    candidate = value.strip().lower()
    return candidate if candidate in allowed else default


def _clean_bool(value: object, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _clean_reason(value: object) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = value.strip()
    if len(cleaned) > _MAX_QUALITY_REASON_CHARS:
        cleaned = cleaned[:_MAX_QUALITY_REASON_CHARS].rstrip() + "…"
    return cleaned


def normalize_frame_quality(raw: object) -> dict:
    """Return a safe, bounded frame-quality hint.

    Missing or invalid metadata is not a failure: older clients simply get
    `unknown` quality so the model path remains unchanged.
    """
    if not isinstance(raw, dict):
        return {
            "blur": "unknown",
            "exposure": "unknown",
            "occlusion": "unknown",
            "usable_for_walking": True,
            "confidence": "low",
            "reason": "",
            "spoken_hint": "",
        }

    blur = _clean_enum(raw.get("blur"), _ALLOWED_BLUR, "unknown")
    exposure = _clean_enum(raw.get("exposure"), _ALLOWED_EXPOSURE, "unknown")
    occlusion = _clean_enum(raw.get("occlusion"), _ALLOWED_OCCLUSION, "unknown")
    confidence = _clean_enum(raw.get("confidence"), _ALLOWED_CONFIDENCE, "low")
    detected_unusable = blur == "blurry" or exposure in {"too_dark", "too_bright"} or occlusion == "covered"
    usable_for_walking = _clean_bool(raw.get("usable_for_walking"), not detected_unusable)
    if detected_unusable:
        usable_for_walking = False

    spoken_hint = _clean_reason(raw.get("spoken_hint"))
    if not spoken_hint and not usable_for_walking:
        if occlusion == "covered":
            spoken_hint = "镜头可能被挡住了。"
        elif exposure == "too_dark":
            spoken_hint = "光线太暗，我看不清前方。"
        elif exposure == "too_bright":
            spoken_hint = "光线太强，我看不清前方。"
        elif blur == "blurry":
            spoken_hint = "画面有些糊，请放慢。"
        else:
            spoken_hint = "前方信息不够清楚，请放慢确认。"

    return {
        "blur": blur,
        "exposure": exposure,
        "occlusion": occlusion,
        "usable_for_walking": usable_for_walking,
        "confidence": confidence,
        "reason": _clean_reason(raw.get("reason")),
        "spoken_hint": spoken_hint,
    }


def _normalize_rect(raw: object) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    try:
        x = float(raw["x"])
        y = float(raw["y"])
        w = float(raw["w"])
        h = float(raw["h"])
    except (KeyError, TypeError, ValueError):
        return None
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1.0 or y + h > 1.0:
        return None
    return {"x": round(x, 3), "y": round(y, 3), "w": round(w, 3), "h": round(h, 3)}


def normalize_walking_roi(raw: object) -> Optional[dict]:
    """Validate normalized-image walking ROI metadata from the client.

    Returns None for absent/invalid metadata so old clients remain compatible.
    """
    if not isinstance(raw, dict):
        return None
    coordinate_space = raw.get("coordinate_space", "normalized_image")
    if coordinate_space != "normalized_image":
        return None

    normalized: dict = {"coordinate_space": "normalized_image"}
    for key in ["near_path", "left_front", "right_front"]:
        rect = _normalize_rect(raw.get(key))
        if rect:
            normalized[key] = rect
    if len(normalized) == 1:
        return None
    return normalized


def should_short_circuit_quality(mode: str, question: str, frame_quality: dict) -> bool:
    """Whether walking can answer immediately with a quality warning.

    Only short-circuit high/medium-confidence quality failures in unattended
    walking frames. Explicit user questions still go to the model when possible.
    """
    return (
        mode in {"risk_observe", "walking"}
        and not question.strip()
        and frame_quality.get("usable_for_walking") is False
        and frame_quality.get("confidence") in {"medium", "high"}
    )


def quality_gate_vqa_payload(frame_quality: dict) -> dict:
    hint = frame_quality.get("spoken_hint") or "前方信息不够清楚，请放慢确认。"
    return {
        "objects": [],
        "scene": "unknown",
        "vision_location": "unknown",
        "description": hint,
        "summary": hint,
        "spatial_description": "画面质量不足，暂时无法可靠判断左侧、正前方和右侧。",
        "risk_level": "medium",
        "risk_message": "我暂时看不清前方，无法判断是否有风险。",
        "suggested_action": "请先放慢或停下，调整手机方向后重试。",
        "spoken_text": hint,
        "ocr_text": "",
        "risk_zone": "unknown",
        "direction": "unknown",
        "distance_confidence": "none",
        "change_significance": "major",
        "changes": "画面质量不足",
        "diagnostic_metrics": {
            "quality_gate": "short_circuit",
            "qwen_http_ms": 0.0,
        },
    }


def build_frame_metadata_prompt(
    *,
    mode: str,
    frame_quality: dict,
    walking_roi: Optional[dict],
) -> str:
    lines: list[str] = []
    if mode in {"risk_observe", "walking"}:
        lines.append("【行走帧元数据】")
        lines.append(
            "图像质量提示："
            f"blur={frame_quality.get('blur', 'unknown')}, "
            f"exposure={frame_quality.get('exposure', 'unknown')}, "
            f"occlusion={frame_quality.get('occlusion', 'unknown')}, "
            f"usable_for_walking={frame_quality.get('usable_for_walking', True)}, "
            f"confidence={frame_quality.get('confidence', 'low')}。"
        )
        if frame_quality.get("spoken_hint"):
            lines.append(f"若图像质量影响判断，优先提示用户：{frame_quality['spoken_hint']}")
        if walking_roi:
            near_path = walking_roi.get("near_path")
            left_front = walking_roi.get("left_front")
            right_front = walking_roi.get("right_front")
            if near_path:
                lines.append(f"near_path ROI={near_path}，代表画面中的近处通行路径。")
            if left_front:
                lines.append(f"left_front ROI={left_front}，代表左前方风险区域。")
            if right_front:
                lines.append(f"right_front ROI={right_front}，代表右前方风险区域。")
            lines.append("请重点关注 ROI，但不要忽略 ROI 外与安全相关的人、车辆、开门、台阶或路沿。")
    if not lines:
        return ""
    return "\n" + "\n".join(lines)
