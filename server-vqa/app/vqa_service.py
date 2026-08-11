import base64
import json
import logging
import os
from time import perf_counter

import httpx


logger = logging.getLogger(__name__)
ALLOWED_MODEL_OVERRIDES = {"qwen2.5vl:3b", "qwen2.5vl:7b"}
ALLOWED_RISK_ZONES = {"immediate", "near", "mid", "far", "unknown"}
ALLOWED_DIRECTIONS = {"left", "center", "right", "left_front", "right_front", "front", "unknown"}
ALLOWED_DISTANCE_CONFIDENCE = {"none", "low", "medium", "high"}


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
            "risk_zone": "unknown",
            "direction": "front",
            "distance_confidence": "none",
        }

    # Generic fallback: we have no usable model output. Do NOT fabricate a
    # person (or any object) — telling a low-vision user "画面中可能有人" when
    # nothing was actually detected is a false positive that erodes trust and is
    # unsafe. Say plainly that the content could not be identified.
    # (CLAUDE.md: No Silent Failures.)
    return {
        "objects": [],
        "scene": "unknown",
        "vision_location": "unknown",
        "description": "暂时无法识别画面内容（本地模型未返回可用结果）。",
        "summary": "暂时无法识别画面内容。",
        "spatial_description": "无法判断空间方位，请稍后重试。",
        "risk_level": "low",
        "risk_message": "无法判断是否存在风险，请谨慎移动。",
        "suggested_action": "请缓慢移动手机重试；若持续无结果，请检查 Mac 后端模型是否正常。",
        "spoken_text": "暂时无法识别画面内容，请谨慎移动。",
        "risk_zone": "unknown",
        "direction": "unknown",
        "distance_confidence": "none",
    }


def _normalize_qwen_payload(payload: dict, fallback_prompt: str) -> dict:
    objects = payload.get("objects", [])
    if not isinstance(objects, list):
        objects = []
    deduped_objects = []
    seen_objects = set()
    for item in objects:
        if not isinstance(item, (str, int, float)):
            continue
        text = str(item).strip()
        if not text or text in seen_objects:
            continue
        seen_objects.add(text)
        deduped_objects.append(text)
        if len(deduped_objects) >= 12:
            break
    objects = deduped_objects

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

    risk_zone = payload.get("risk_zone")
    if isinstance(risk_zone, str):
        candidate = risk_zone.strip().lower()
        if candidate in ALLOWED_RISK_ZONES:
            normalized["risk_zone"] = candidate

    direction = payload.get("direction")
    if isinstance(direction, str):
        candidate = direction.strip().lower()
        if candidate in ALLOWED_DIRECTIONS:
            normalized["direction"] = candidate

    distance_confidence = payload.get("distance_confidence")
    if isinstance(distance_confidence, str):
        candidate = distance_confidence.strip().lower()
        if candidate in ALLOWED_DISTANCE_CONFIDENCE:
            normalized["distance_confidence"] = candidate

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

    # The model returned text but not the required JSON (common with a direct
    # llama-server, which does not enforce response_format). Do NOT fall back to
    # the fabricated heuristic ("画面中可能有人") — that invents objects the model
    # never reported. Instead surface the model's ACTUAL text as the description
    # and let fusion derive summary/risk from it, so the user sees the real
    # reason/content rather than a fake person. (CLAUDE.md: No Silent Failures.)
    logger.warning(
        "Qwen returned non-JSON content; surfacing raw text instead of heuristic person. len=%d",
        len(stripped),
    )
    looks_like_broken_json = stripped.startswith(("{", "[")) or '"objects"' in stripped
    if looks_like_broken_json:
        raw_preview = stripped[:500].rstrip()
        return {
            "objects": [],
            "scene": "unknown",
            "vision_location": "unknown",
            # Keep the diagnostic in debug/description, but never let raw JSON
            # become the user-facing summary/spoken text.
            "description": f"模型未按要求输出结构化结果（格式异常）：{raw_preview}",
            "summary": "模型输出异常，暂时无法可靠描述画面。",
            "spatial_description": "暂时无法判断左侧、正前方和右侧。",
            "risk_level": "low",
            "risk_message": "无法判断是否存在风险，请谨慎移动。",
            "suggested_action": "请稍微移动手机后重试；如果持续出现，请重启 Mac 后端。",
            "spoken_text": "模型输出异常，暂时无法可靠描述画面。请谨慎移动。",
            "risk_zone": "unknown",
            "direction": "unknown",
            "distance_confidence": "none",
            "change_significance": "major",
            "changes": "模型输出格式异常",
        }

    return {
        "objects": [],
        "scene": "unknown",
        "vision_location": "unknown",
        # Natural-language non-JSON can still be useful; let fusion surface it.
        "description": f"（模型未按要求输出结构化结果，以下为原始描述）{stripped}",
    }


def run_vqa(prompt: str) -> dict:
    return _heuristic_vqa(prompt=prompt)


def _default_qwen_model(qwen_api_base_url: str) -> str:
    qwen_model = os.getenv("QWEN_MODEL", "").strip()
    if qwen_model:
        return qwen_model

    if "127.0.0.1:11434" in qwen_api_base_url or "localhost:11434" in qwen_api_base_url:
        return "qwen2.5vl:3b"
    if "127.0.0.1:11435" in qwen_api_base_url or "localhost:11435" in qwen_api_base_url:
        return "qwen2.5vl:3b"
    return "Qwen/Qwen2.5-VL-3B-Instruct"


def _supports_dynamic_model_selection(qwen_api_base_url: str) -> bool:
    """Whether one API endpoint can honestly switch models per request.

    Our direct llama-server runtime (:11435 by default) loads exactly one model
    process. Sending a different `model` field to that endpoint does NOT switch
    weights, so honoring iOS' per-frame override there is misleading. Ollama
    (:11434) and cloud/OpenAI-compatible endpoints can route dynamically.
    """
    local_direct = (
        "127.0.0.1:11435" in qwen_api_base_url
        or "localhost:11435" in qwen_api_base_url
    )
    return not local_direct


def _resolve_qwen_model_info(qwen_api_base_url: str, model_override: str = "") -> dict:
    configured_model = _default_qwen_model(qwen_api_base_url)
    requested_model = model_override.strip()
    dynamic = _supports_dynamic_model_selection(qwen_api_base_url)

    if dynamic and requested_model in ALLOWED_MODEL_OVERRIDES:
        resolved_model = requested_model
        routing_reason = "override"
    else:
        resolved_model = configured_model
        if requested_model and requested_model != configured_model:
            routing_reason = "single_runtime_ignored_override" if not dynamic else "unsupported_override"
        else:
            routing_reason = "configured"

    return {
        "api_base_url": qwen_api_base_url,
        "configured_model": configured_model,
        "requested_model": requested_model,
        "resolved_model": resolved_model,
        "dynamic_model_selection": dynamic,
        "routing_reason": routing_reason,
        "allowed_overrides": sorted(ALLOWED_MODEL_OVERRIDES),
    }


def _resolve_qwen_model(qwen_api_base_url: str, model_override: str = "") -> str:
    return _resolve_qwen_model_info(
        qwen_api_base_url=qwen_api_base_url,
        model_override=model_override,
    )["resolved_model"]


def runtime_status() -> dict:
    qwen_api_base_url = os.getenv("QWEN_API_BASE_URL", "").rstrip("/")
    if not qwen_api_base_url:
        return {
            "status": "heuristic",
            "api_base_url": "",
            "configured_model": "",
            "resolved_model": "",
            "dynamic_model_selection": False,
            "available_models": [],
            "message": "QWEN_API_BASE_URL not set; backend uses heuristic fallback.",
        }

    info = _resolve_qwen_model_info(qwen_api_base_url=qwen_api_base_url)
    available_models = info["allowed_overrides"] if info["dynamic_model_selection"] else [info["resolved_model"]]
    return {
        "status": "qwen",
        "api_base_url": qwen_api_base_url,
        "configured_model": info["configured_model"],
        "resolved_model": info["resolved_model"],
        "dynamic_model_selection": info["dynamic_model_selection"],
        "available_models": available_models,
        "routing_reason": info["routing_reason"],
        "image_min_tokens": os.getenv("IMAGE_MIN_TOKENS", "256"),
        "image_max_tokens": os.getenv("IMAGE_MAX_TOKENS", "512"),
        "max_tokens_fast": _MAX_TOKENS_FAST,
        "max_tokens_incremental": _MAX_TOKENS_INCREMENTAL,
        "max_tokens_full": _MAX_TOKENS_FULL,
        "send_previous_image_in_incremental": os.getenv("QWEN_SEND_PREVIOUS_IMAGE_IN_INCREMENTAL", "0") == "1",
    }

# These are output CEILINGS, not fixed costs. The fast schema used by walking /
# surroundings frames emits fewer fields, so it can safely use a smaller ceiling.
# Keep enough margin for valid Chinese JSON; too small -> truncated JSON ->
# "模型未按要求输出" on every frame.
_MAX_TOKENS_FAST = _env_int("QWEN_MAX_TOKENS_FAST", 260)
_MAX_TOKENS_INCREMENTAL = _env_int("QWEN_MAX_TOKENS_INCREMENTAL", _MAX_TOKENS_FAST)
_MAX_TOKENS_FULL = _env_int("QWEN_MAX_TOKENS_FULL", 520)


# Slim output schema. The mode prompts in prompts.py are prose instructions
# ("先说场景类型，再说最重要的物体和位置") that fight a plain "output JSON" system
# message — the model follows the more specific prose and emits Markdown, so
# parsing fails on every frame. Enforcing this JSON Schema at the decode layer
# (llama-server grammar) makes valid JSON structurally guaranteed regardless of
# how the prompt is worded, and locks change_significance to the enum. Only the
# fields fusion.py cannot derive are required.
_VQA_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "objects": {"type": "array", "items": {"type": "string"}},
        "scene": {"type": "string"},
        "description": {"type": "string"},
        "summary": {"type": "string"},
        "spatial_description": {"type": "string"},
        "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
        "risk_message": {"type": "string"},
        "suggested_action": {"type": "string"},
        "spoken_text": {"type": "string"},
        "risk_zone": {"type": "string", "enum": ["immediate", "near", "mid", "far", "unknown"]},
        "direction": {"type": "string", "enum": ["left", "center", "right", "left_front", "right_front", "front", "unknown"]},
        "distance_confidence": {"type": "string", "enum": ["none", "low", "medium", "high"]},
        "ocr_text": {"type": "string"},
        "change_significance": {"type": "string", "enum": ["none", "minor", "major"]},
        "changes": {"type": "string"},
    },
    "required": [
        "objects",
        "scene",
        "description",
        "summary",
        "spatial_description",
        "risk_level",
        "risk_message",
        "suggested_action",
        "spoken_text",
        "ocr_text",
        "change_significance",
        "changes",
    ],
    "additionalProperties": False,
}


# Fast safety schema for walking / surroundings. It intentionally omits verbose
# diagnostic fields (`description`, `ocr_text`) that fusion can fill, while
# preserving the fields a low-vision user needs immediately: where, risk, and
# what to do next. Fewer required fields means fewer decode tokens on Qwen 3B.
_FAST_VQA_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "objects": {"type": "array", "items": {"type": "string"}},
        "scene": {"type": "string"},
        "summary": {"type": "string"},
        "spatial_description": {"type": "string"},
        "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
        "risk_message": {"type": "string"},
        "suggested_action": {"type": "string"},
        "spoken_text": {"type": "string"},
        "change_significance": {"type": "string", "enum": ["none", "minor", "major"]},
        "changes": {"type": "string"},
    },
    "required": [
        "objects",
        "scene",
        "summary",
        "spatial_description",
        "risk_level",
        "risk_message",
        "suggested_action",
        "spoken_text",
        "change_significance",
        "changes",
    ],
    "additionalProperties": False,
}


# Walking-specific fast schema. It adds coarse near-path fields without asking
# the model for fake meter-level distance. Keep it separate from surroundings so
# ordinary ambient descriptions do not pay extra decode tokens.
_WALKING_FAST_VQA_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        **_FAST_VQA_JSON_SCHEMA["properties"],
        "risk_zone": {"type": "string", "enum": ["immediate", "near", "mid", "far", "unknown"]},
        "direction": {"type": "string", "enum": ["left", "center", "right", "left_front", "right_front", "front", "unknown"]},
        "distance_confidence": {"type": "string", "enum": ["none", "low", "medium", "high"]},
    },
    "required": [
        *_FAST_VQA_JSON_SCHEMA["required"],
        "risk_zone",
        "direction",
        "distance_confidence",
    ],
    "additionalProperties": False,
}


def _uses_near_path_schema(prompt: str) -> bool:
    """Whether a fast request needs coarse risk-zone fields.

    User-visible modes are being removed, but the backend still needs a compact
    risk schema for both the old walking mode and the new unified risk observer.
    """
    return "模式=行走" in prompt or "模式=风险观察" in prompt


def _build_response_format(
    qwen_api_base_url: str,
    fast_response: bool = False,
    walking_fast_response: bool = False,
) -> dict:
    """Choose the strongest output constraint the runtime supports.

    Direct llama-server (:11435) supports `json_schema` with `strict`, which
    enforces the structure via a decode-time grammar — the reliable fix. Ollama
    (:11434, USE_OLLAMA=1) only understands `json_object`, so fall back to that
    there rather than sending a payload it would reject.
    """
    if "11434" in qwen_api_base_url:
        return {"type": "json_object"}
    if fast_response and walking_fast_response:
        schema = _WALKING_FAST_VQA_JSON_SCHEMA
        name = "vqa_walking_fast_result"
    elif fast_response:
        schema = _FAST_VQA_JSON_SCHEMA
        name = "vqa_fast_result"
    else:
        schema = _VQA_JSON_SCHEMA
        name = "vqa_result"
    return {
        "type": "json_schema",
        "json_schema": {"name": name, "strict": True, "schema": schema},
    }


def run_vqa_from_frame(
    prompt: str,
    image_base64: str,
    model_override: str = "",
    incremental: bool = False,
    previous_image_base64: str = "",
    fast_response: bool = False,
) -> dict:
    base64.b64decode(image_base64, validate=True)
    send_previous_image = (
        bool(previous_image_base64)
        and os.getenv("QWEN_SEND_PREVIOUS_IMAGE_IN_INCREMENTAL", "0") == "1"
    )
    if send_previous_image:
        base64.b64decode(previous_image_base64, validate=True)

    qwen_api_base_url = os.getenv("QWEN_API_BASE_URL", "").rstrip("/")
    if not qwen_api_base_url:
        fallback = _heuristic_vqa(prompt=prompt)
        fallback["diagnostic_metrics"] = {
            "qwen_http_ms": 0.0,
            "schema_name": "heuristic",
            "frame_base64_bytes": len(image_base64.encode("utf-8")),
            "fast_response": fast_response,
            "walking_fast_response": fast_response and _uses_near_path_schema(prompt),
        }
        return fallback
    model_info = _resolve_qwen_model_info(
        qwen_api_base_url=qwen_api_base_url,
        model_override=model_override,
    )
    qwen_model = model_info["resolved_model"]

    try:
        timeout_seconds = float(os.getenv("QWEN_TIMEOUT_SECONDS", "45"))
    except ValueError:
        timeout_seconds = 45.0

    use_fast_schema = fast_response
    walking_fast_response = use_fast_schema and _uses_near_path_schema(prompt)
    max_tokens = _MAX_TOKENS_FAST if use_fast_schema else _MAX_TOKENS_FULL

    user_content = [{"type": "text", "text": prompt or "Describe the scene in the image."}]
    if send_previous_image:
        user_content.append(
            {
                "type": "text",
                "text": "上一帧图像如下，仅用于比较变化，不要把上一帧当作当前事实：",
            }
        )
        user_content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{previous_image_base64}"},
            }
        )
        user_content.append({"type": "text", "text": "当前帧图像如下，请以当前帧为准："})
    user_content.append(
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"},
        }
    )

    response_format = _build_response_format(
        qwen_api_base_url,
        fast_response=use_fast_schema,
        walking_fast_response=walking_fast_response,
    )
    schema_name = response_format.get("json_schema", {}).get("name", response_format.get("type", "unknown"))

    request_payload = {
        "model": qwen_model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": response_format,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a visual-assistance VQA parser for blind or low-vision users. "
                    "Output strict JSON only, no Markdown fences. Required keys follow the supplied JSON schema. "
                    "objects must be a list of short Chinese strings, e.g. [\"行人\",\"台阶\"] — NEVER bounding boxes or dicts. "
                    "Use short Chinese values for scene, summary, spatial_description, risk_message, suggested_action, spoken_text, changes. "
                    "risk_level must be low, medium, or high; change_significance must be none, minor, or major. "
                    "Do not invent certainty: use 可能/疑似 when unsure. "
                    "spatial_description MUST mention 左侧、正前方、右侧 when visible; say 信息不足 when not visible. "
                    "suggested_action must be a direct action for the user, not a generic description. "
                    "If client OCR text is provided, use it for ocr_text and text-reading answers, but correct obvious OCR noise. "
                    "For walking safety, prioritize obstacles, people, vehicles, stairs, doors, curbs, holes, edges and the near path in the lower/center image. "
                    "Never estimate exact meters from a single image; use risk_zone immediate/near/mid/far/unknown, direction left/center/right/left_front/right_front/front/unknown, and distance_confidence none/low/medium/high. "
                    "If distance is uncertain, set distance_confidence to none or low and say 近处/前方/远处/无法判断 in Chinese. "
                    "When a 【连续观察上下文】 block is present, report only important changes: set "
                    "change_significance to none when nothing important changed (keep description short), "
                    "minor for small changes, major when the user must pay attention; put the delta in changes. "
                    "If previous and current images are both provided, compare them but answer using the current image. "
                    "Without context, set change_significance to major and leave changes empty."
                ),
            },
            {
                "role": "user",
                "content": user_content,
            },
        ],
    }

    qwen_http_ms = 0.0
    try:
        qwen_started_at = perf_counter()
        response = httpx.post(
            f"{qwen_api_base_url}/v1/chat/completions",
            json=request_payload,
            timeout=timeout_seconds,
        )
        qwen_http_ms = (perf_counter() - qwen_started_at) * 1000.0
        response.raise_for_status()
        response_data = response.json()
        content = response_data["choices"][0]["message"]["content"]
        parsed = _parse_qwen_content(content=content, fallback_prompt=prompt)
        parsed["requested_model"] = model_info["requested_model"]
        parsed["resolved_model"] = model_info["resolved_model"]
        parsed["model_routing_reason"] = model_info["routing_reason"]
        parsed["diagnostic_metrics"] = {
            "qwen_http_ms": qwen_http_ms,
            "schema_name": schema_name,
            "frame_base64_bytes": len(image_base64.encode("utf-8")),
            "fast_response": use_fast_schema,
            "walking_fast_response": walking_fast_response,
        }
        return parsed
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        if qwen_http_ms == 0.0:
            qwen_http_ms = (perf_counter() - qwen_started_at) * 1000.0 if "qwen_started_at" in locals() else 0.0
        logger.exception("Qwen inference failed; fallback to heuristic path: %s", exc)
        fallback = _heuristic_vqa(prompt=prompt)
        fallback["description"] = f"{fallback['description']} (fallback due to inference error)"
        fallback["requested_model"] = model_info["requested_model"]
        fallback["resolved_model"] = model_info["resolved_model"]
        fallback["model_routing_reason"] = model_info["routing_reason"]
        fallback["diagnostic_metrics"] = {
            "qwen_http_ms": qwen_http_ms,
            "schema_name": schema_name,
            "frame_base64_bytes": len(image_base64.encode("utf-8")),
            "fast_response": use_fast_schema,
            "walking_fast_response": walking_fast_response,
            "fallback_reason": "qwen_inference_failed",
        }
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
