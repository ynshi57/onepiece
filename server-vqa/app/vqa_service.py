import base64
import json
import logging
import os

import httpx


logger = logging.getLogger(__name__)
ALLOWED_MODEL_OVERRIDES = {"qwen2.5vl:3b", "qwen2.5vl:7b"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _heuristic_vqa(prompt: str) -> dict:
    normalized_prompt = prompt.lower()
    if "道路" in prompt or "road" in normalized_prompt:
        return {
            "objects": ["car", "traffic_light"],
            "scene": "city street",
            "vision_location": "outdoor road",
            "description": "前方可能是道路场景，有车辆和交通灯。",
            "summary": "前方可能是道路场景。",
            "spatial_description": "画面正前方可能有道路元素，车辆和交通灯的具体方位需要真实模型确认。",
            "risk_level": "medium",
            "risk_message": "附近可能有车辆，请注意交通风险。",
            "suggested_action": "请先停下或放慢速度，确认左右来车后再移动。",
            "spoken_text": "前方可能是道路场景，附近可能有车辆，请注意交通风险。",
        }

    return {
        "objects": ["person"],
        "scene": "unknown",
        "vision_location": "unknown",
        "description": "画面中可能有人，但当前模型无法可靠判断更多细节。",
        "summary": "画面中可能有人。",
        "spatial_description": "无法可靠判断人在左侧、正前方还是右侧。",
        "risk_level": "medium",
        "risk_message": "附近可能有人，请注意保持距离。",
        "suggested_action": "请缓慢移动手机，扫视左侧、正前方和右侧。",
        "spoken_text": "画面中可能有人，请注意保持距离。",
    }


def _normalize_qwen_payload(payload: dict, fallback_prompt: str) -> dict:
    objects = payload.get("objects", [])
    if not isinstance(objects, list):
        objects = []
    objects = [str(item) for item in objects if isinstance(item, (str, int, float))]

    scene = payload.get("scene", "unknown")
    if not isinstance(scene, str):
        scene = "unknown"

    vision_location = payload.get("vision_location", "unknown")
    if not isinstance(vision_location, str):
        vision_location = "unknown"

    description = payload.get("description", "")
    if not isinstance(description, str) or not description.strip():
        description = f"qwen_vqa_response for prompt: {fallback_prompt}"

    normalized = {
        "objects": objects,
        "scene": scene,
        "vision_location": vision_location,
        "description": description,
    }
    for key in [
        "summary",
        "spatial_description",
        "risk_level",
        "risk_message",
        "suggested_action",
        "spoken_text",
        "ocr_text",
        "changes",
    ]:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            normalized[key] = value.strip()

    change_significance = payload.get("change_significance")
    if isinstance(change_significance, str):
        candidate = change_significance.strip().lower()
        if candidate in {"none", "minor", "major"}:
            normalized["change_significance"] = candidate

    return normalized


def _parse_qwen_content(content: object, fallback_prompt: str) -> dict:
    if isinstance(content, list):
        text_parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                text_parts.append(part["text"])
        content = "\n".join(text_parts)

    if not isinstance(content, str):
        return _heuristic_vqa(fallback_prompt)

    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    if not stripped:
        return _heuristic_vqa(fallback_prompt)

    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return _normalize_qwen_payload(parsed, fallback_prompt=fallback_prompt)
    except json.JSONDecodeError:
        pass

    fallback = _heuristic_vqa(fallback_prompt)
    fallback["description"] = stripped
    return fallback


def run_vqa(prompt: str) -> dict:
    return _heuristic_vqa(prompt=prompt)


def _resolve_qwen_model(qwen_api_base_url: str, model_override: str = "") -> str:
    requested_model = model_override.strip()
    if requested_model in ALLOWED_MODEL_OVERRIDES:
        return requested_model

    qwen_model = os.getenv("QWEN_MODEL", "").strip()
    if qwen_model:
        return qwen_model

    if "127.0.0.1:11434" in qwen_api_base_url or "localhost:11434" in qwen_api_base_url:
        return "qwen2.5vl:3b"
    return "Qwen/Qwen2.5-VL-3B-Instruct"


# Continuous surroundings/walking frames only need a short delta; single-shot
# modes (read-text, detailed) still want room for a fuller answer. Kept modest
# either way to cut decode time on the local 3B model.
_MAX_TOKENS_INCREMENTAL = _env_int("QWEN_MAX_TOKENS_INCREMENTAL", 96)
_MAX_TOKENS_FULL = _env_int("QWEN_MAX_TOKENS_FULL", 160)


def run_vqa_from_frame(
    prompt: str,
    image_base64: str,
    model_override: str = "",
    incremental: bool = False,
) -> dict:
    base64.b64decode(image_base64, validate=True)

    qwen_api_base_url = os.getenv("QWEN_API_BASE_URL", "").rstrip("/")
    if not qwen_api_base_url:
        return _heuristic_vqa(prompt=prompt)
    qwen_model = _resolve_qwen_model(
        qwen_api_base_url=qwen_api_base_url,
        model_override=model_override,
    )

    try:
        timeout_seconds = float(os.getenv("QWEN_TIMEOUT_SECONDS", "45"))
    except ValueError:
        timeout_seconds = 45.0

    max_tokens = _MAX_TOKENS_INCREMENTAL if incremental else _MAX_TOKENS_FULL

    request_payload = {
        "model": qwen_model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a visual-assistance VQA parser for blind or low-vision users. "
                    "Output strict JSON only. Required keys: "
                    "objects(list[str]), scene(str), vision_location(str), description(str), "
                    "summary(str), spatial_description(str), risk_level(str: low|medium|high), "
                    "risk_message(str), suggested_action(str), spoken_text(str), ocr_text(str), "
                    "change_significance(str: none|minor|major), changes(str). "
                    "Write Chinese. Use image-coordinate directions when visible: 左侧、正前方、右侧、近处、远处. "
                    "Do not invent certainty: use 可能/疑似 when unsure. "
                    "For walking safety, prioritize obstacles, people, vehicles, stairs, doors, edges, and clear next action. "
                    "When a 【连续观察上下文】 block is present, report only important changes: set "
                    "change_significance to none when nothing important changed (and keep spoken_text very short), "
                    "minor for small changes, major when the user must pay attention; put the delta in changes. "
                    "Without that block, set change_significance to major and leave changes empty."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt or "Describe the scene in the image."},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"},
                    },
                ],
            },
        ],
    }

    try:
        response = httpx.post(
            f"{qwen_api_base_url}/v1/chat/completions",
            json=request_payload,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        response_data = response.json()
        content = response_data["choices"][0]["message"]["content"]
        return _parse_qwen_content(content=content, fallback_prompt=prompt)
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        logger.exception("Qwen inference failed; fallback to heuristic path: %s", exc)
        fallback = _heuristic_vqa(prompt=prompt)
        fallback["description"] = f"{fallback['description']} (fallback due to inference error)"
        return fallback


# A 1x1 transparent PNG, base64-encoded. Used only to prime the model so real
# frames don't pay the cold-load cost.
_WARMUP_IMAGE_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def warmup_model() -> bool:
    """Prime the local model so the first real frame doesn't pay a cold reload.

    Best-effort by design: returns True on success, False (with a log line) on any
    failure or when no local Qwen endpoint is configured. Never raises, so a slow
    or missing model at startup cannot take the service down.
    """
    qwen_api_base_url = os.getenv("QWEN_API_BASE_URL", "").rstrip("/")
    if not qwen_api_base_url:
        logger.info("warmup_model skipped: QWEN_API_BASE_URL not set.")
        return False

    qwen_model = _resolve_qwen_model(qwen_api_base_url=qwen_api_base_url)
    try:
        warmup_timeout = float(os.getenv("QWEN_WARMUP_TIMEOUT_SECONDS", "120"))
    except ValueError:
        warmup_timeout = 120.0

    request_payload = {
        "model": qwen_model,
        "temperature": 0,
        "max_tokens": 1,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "ok"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{_WARMUP_IMAGE_BASE64}"
                        },
                    },
                ],
            }
        ],
    }

    try:
        response = httpx.post(
            f"{qwen_api_base_url}/v1/chat/completions",
            json=request_payload,
            timeout=warmup_timeout,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("warmup_model request failed (non-fatal): %s", exc)
        return False

    logger.info("warmup_model complete for model=%s", qwen_model)
    return True
