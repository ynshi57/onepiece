from datetime import datetime, timezone
from typing import Optional


RISK_OBJECT_KEYWORDS = {
    "high": [
        "stairs",
        "stair",
        "step",
        "台阶",
        "楼梯",
        "hole",
        "坑",
        "cliff",
        "edge",
        "火",
        "fire",
    ],
    "medium": [
        "car",
        "vehicle",
        "bus",
        "truck",
        "bicycle",
        "bike",
        "traffic",
        "person",
        "people",
        "行人",
        "车辆",
        "自行车",
        "障碍",
        "obstacle",
    ],
}


def _normalize_objects(raw_objects: object) -> list:
    if not isinstance(raw_objects, list):
        return []
    return [str(item) for item in raw_objects if isinstance(item, (str, int, float))]


def _string_field(payload: dict, key: str, fallback: str = "") -> str:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _risk_level(payload: dict, fallback: str) -> str:
    value = _string_field(payload, "risk_level", fallback).lower()
    if value in {"low", "medium", "high"}:
        return value
    if value in {"低", "安全"}:
        return "low"
    if value in {"中", "注意"}:
        return "medium"
    if value in {"高", "危险"}:
        return "high"
    return fallback


def _change_significance(payload: dict) -> str:
    value = payload.get("change_significance")
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in {"none", "minor", "major"}:
            return candidate
    # No/invalid value -> treat as a significant frame so the client speaks it.
    return "major"


def _derive_assistance_payload(
    objects: list,
    scene: str,
    description: str,
    vision_location: str,
) -> dict:
    object_text = " ".join(objects).lower()
    combined_text = f"{object_text} {scene} {description} {vision_location}".lower()

    risk_level = "low"
    risk_message = "暂未发现明显危险。"
    suggested_action = "保持手机朝向前方，缓慢移动以获取更多信息。"

    for keyword in RISK_OBJECT_KEYWORDS["high"]:
        if keyword.lower() in combined_text:
            risk_level = "high"
            risk_message = "可能存在台阶、落差或其他高风险障碍。"
            suggested_action = "请先停下，缓慢调整手机方向确认周围环境。"
            break

    if risk_level == "low":
        for keyword in RISK_OBJECT_KEYWORDS["medium"]:
            if keyword.lower() in combined_text:
                risk_level = "medium"
                risk_message = "附近可能有人、车辆或障碍物，请注意避让。"
                suggested_action = "请放慢速度，并用手机扫视前方和两侧。"
                break

    if description and description != "no description":
        summary = description
    elif objects:
        summary = f"画面中可能有：{'、'.join(objects[:4])}。"
    elif scene != "unknown":
        summary = f"当前场景可能是：{scene}。"
    else:
        summary = "暂时无法可靠判断画面内容。"

    spoken_text = f"{summary} {risk_message}"

    return {
        "summary": summary,
        "spatial_description": "请缓慢移动手机，以便判断左侧、正前方和右侧的空间关系。",
        "risk_level": risk_level,
        "risk_message": risk_message,
        "suggested_action": suggested_action,
        "spoken_text": spoken_text,
        "ocr_text": "",
    }


def fuse_vqa_result(
    vision_payload: dict,
    gps_payload: Optional[dict],
    latency_ms: Optional[float] = None,
) -> dict:
    description = vision_payload.get("description")
    if not isinstance(description, str) or not description.strip():
        description = "no description"

    objects = _normalize_objects(vision_payload.get("objects", []))
    scene = vision_payload.get("scene", "unknown")
    if not isinstance(scene, str):
        scene = "unknown"
    vision_location = vision_payload.get("vision_location", "unknown")
    if not isinstance(vision_location, str):
        vision_location = "unknown"

    assistance_payload = _derive_assistance_payload(
        objects=objects,
        scene=scene,
        description=description,
        vision_location=vision_location,
    )
    summary = _string_field(vision_payload, "summary", assistance_payload["summary"])
    spatial_description = _string_field(
        vision_payload,
        "spatial_description",
        assistance_payload["spatial_description"],
    )
    risk_level = _risk_level(vision_payload, assistance_payload["risk_level"])
    risk_message = _string_field(vision_payload, "risk_message", assistance_payload["risk_message"])
    suggested_action = _string_field(
        vision_payload,
        "suggested_action",
        assistance_payload["suggested_action"],
    )
    spoken_text = _string_field(
        vision_payload,
        "spoken_text",
        f"{summary} {risk_message}",
    )
    ocr_text = _string_field(vision_payload, "ocr_text", assistance_payload["ocr_text"])

    # Continuity fields (scene-memory / incremental reporting). Absent when the
    # client sent no context or an older client is connected -> default to
    # "major" so the client speaks the result, matching pre-continuity behaviour.
    change_significance = _change_significance(vision_payload)
    changes = _string_field(vision_payload, "changes", "")

    return {
        "objects": objects,
        "scene": scene,
        "vision_location": vision_location,
        "description": description,
        "summary": summary,
        "spatial_description": spatial_description,
        "risk_level": risk_level,
        "risk_message": risk_message,
        "suggested_action": suggested_action,
        "spoken_text": spoken_text,
        "ocr_text": ocr_text,
        "change_significance": change_significance,
        "changes": changes,
        "gps_location": gps_payload,
        "latency_ms": latency_ms,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
