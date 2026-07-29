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
    return {
        "objects": [],
        "scene": "unknown",
        "vision_location": "unknown",
        # Prefix the diagnostic reason so debug views make the failure explicit,
        # while the readable model text still drives the spoken summary.
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
        "max_tokens_incremental": _MAX_TOKENS_INCREMENTAL,
        "max_tokens_full": _MAX_TOKENS_FULL,
    }

# These are output CEILINGS, not fixed costs: with the slim JSON schema below the
# model emits `finish_reason: stop` at ~70-130 tokens, so a higher ceiling adds
# no latency but prevents mid-JSON truncation (finish_reason: length -> invalid
# JSON -> "模型未按要求输出" on every frame). The old 96/160 values were BELOW the
# minimum size of a valid Chinese JSON object, so every response was truncated.
_MAX_TOKENS_INCREMENTAL = _env_int("QWEN_MAX_TOKENS_INCREMENTAL", 420)
_MAX_TOKENS_FULL = _env_int("QWEN_MAX_TOKENS_FULL", 640)


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


def _build_response_format(qwen_api_base_url: str) -> dict:
    """Choose the strongest output constraint the runtime supports.

    Direct llama-server (:11435) supports `json_schema` with `strict`, which
    enforces the structure via a decode-time grammar — the reliable fix. Ollama
    (:11434, USE_OLLAMA=1) only understands `json_object`, so fall back to that
    there rather than sending a payload it would reject.
    """
    if "11434" in qwen_api_base_url:
        return {"type": "json_object"}
    return {
        "type": "json_schema",
        "json_schema": {"name": "vqa_result", "strict": True, "schema": _VQA_JSON_SCHEMA},
    }


def run_vqa_from_frame(
    prompt: str,
    image_base64: str,
    model_override: str = "",
    incremental: bool = False,
    previous_image_base64: str = "",
) -> dict:
    base64.b64decode(image_base64, validate=True)
    if previous_image_base64:
        base64.b64decode(previous_image_base64, validate=True)

    qwen_api_base_url = os.getenv("QWEN_API_BASE_URL", "").rstrip("/")
    if not qwen_api_base_url:
        return _heuristic_vqa(prompt=prompt)
    model_info = _resolve_qwen_model_info(
        qwen_api_base_url=qwen_api_base_url,
        model_override=model_override,
    )
    qwen_model = model_info["resolved_model"]

    try:
        timeout_seconds = float(os.getenv("QWEN_TIMEOUT_SECONDS", "45"))
    except ValueError:
        timeout_seconds = 45.0

    max_tokens = _MAX_TOKENS_INCREMENTAL if incremental else _MAX_TOKENS_FULL

    user_content = [{"type": "text", "text": prompt or "Describe the scene in the image."}]
    if previous_image_base64:
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

    request_payload = {
        "model": qwen_model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": _build_response_format(qwen_api_base_url),
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a visual-assistance VQA parser for blind or low-vision users. "
                    "Output strict JSON only, no Markdown fences. Required keys: "
                    "objects(list of short Chinese strings, e.g. [\"行人\",\"台阶\"] — NEVER bounding boxes or dicts), "
                    "scene(str), description(str), summary(str), spatial_description(str), "
                    "risk_level(low|medium|high), risk_message(str), suggested_action(str), "
                    "spoken_text(str), ocr_text(str), change_significance(str: none|minor|major), changes(str). "
                    "Do not invent certainty: use 可能/疑似 when unsure. "
                    "spatial_description MUST mention 左侧、正前方、右侧 when visible; say 信息不足 when not visible. "
                    "suggested_action must be a direct action for the user, not a generic description. "
                    "If client OCR text is provided, use it for ocr_text and text-reading answers, but correct obvious OCR noise. "
                    "For walking safety, prioritize obstacles, people, vehicles, stairs, doors, edges. "
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

    try:
        response = httpx.post(
            f"{qwen_api_base_url}/v1/chat/completions",
            json=request_payload,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        response_data = response.json()
        content = response_data["choices"][0]["message"]["content"]
        parsed = _parse_qwen_content(content=content, fallback_prompt=prompt)
        parsed["requested_model"] = model_info["requested_model"]
        parsed["resolved_model"] = model_info["resolved_model"]
        parsed["model_routing_reason"] = model_info["routing_reason"]
        return parsed
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        logger.exception("Qwen inference failed; fallback to heuristic path: %s", exc)
        fallback = _heuristic_vqa(prompt=prompt)
        fallback["description"] = f"{fallback['description']} (fallback due to inference error)"
        fallback["requested_model"] = model_info["requested_model"]
        fallback["resolved_model"] = model_info["resolved_model"]
        fallback["model_routing_reason"] = model_info["routing_reason"]
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
