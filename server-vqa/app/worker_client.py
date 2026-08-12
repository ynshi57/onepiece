import asyncio
import base64
import binascii
import json
import os
from time import perf_counter
from typing import Optional

import websockets

from app.diagnostic_capture import append_diagnostic_record, save_diagnostic_frame
from app.frame_metadata import (
    build_frame_metadata_prompt,
    normalize_frame_quality,
    normalize_walking_roi,
    quality_gate_vqa_payload,
    should_short_circuit_quality,
)
from app.fusion import fuse_vqa_result
from app.prompts import resolve_prompt
from app.scene_context import build_contextual_prompt
from app.signaling import normalize_gps
from app.vqa_service import run_vqa_from_frame


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


RELAY_WORKER_URL = os.getenv("RELAY_WORKER_URL", "ws://127.0.0.1:9100/ws/worker")
PAIRING_TOKEN = os.getenv("RELAY_PAIRING_TOKEN", "dev-pairing-token")
WORKER_ID = os.getenv("WORKER_ID", "local-mac-worker")
MAX_FRAME_BASE64_BYTES = _env_int("MAX_FRAME_BASE64_BYTES", 900_000)
INFERENCE_TIMEOUT_SECONDS = _env_int("WORKER_INFERENCE_TIMEOUT_SECONDS", 20)
DROP_IF_BUSY = os.getenv("WORKER_DROP_IF_BUSY", "1") != "0"
RECONNECT_DELAY_SECONDS = _env_int("WORKER_RECONNECT_DELAY_SECONDS", 3)


async def _send_json(websocket, send_lock: asyncio.Lock, payload: dict) -> None:
    async with send_lock:
        await websocket.send(json.dumps(payload))


def build_inference_result(message: dict) -> dict:
    request_id = str(message.get("request_id", ""))
    mode = str(message.get("mode", ""))
    question = str(message.get("question", ""))
    context = message.get("context")
    context = context if isinstance(context, dict) else None
    legacy_prompt = str(message.get("prompt", ""))
    effective_mode = mode if mode.strip() else ("risk_observe" if not legacy_prompt.strip() else "")
    frame_quality = normalize_frame_quality(message.get("frame_quality"))
    walking_roi = normalize_walking_roi(message.get("walking_roi"))
    prompt = resolve_prompt(
        mode=mode,
        question=question,
        legacy_prompt=legacy_prompt,
    )
    client_ocr_text = str(message.get("client_ocr_text", "")).strip()
    if client_ocr_text:
        prompt = (
            f"{prompt}\n\n【客户端 OCR 文本】\n{client_ocr_text[:800]}\n"
            "如果用户在读文字，请优先利用这段 OCR 文本，并结合图像确认。"
        )
    prompt = build_contextual_prompt(prompt, mode=mode, context=context)
    prompt = prompt + build_frame_metadata_prompt(
        mode=effective_mode,
        frame_quality=frame_quality,
        walking_roi=walking_roi,
    )
    incremental = context is not None and not question.strip()
    fast_response = effective_mode in {"risk_observe", "walking", "surroundings"} and not question.strip()
    model = str(message.get("model", ""))
    image_base64 = message.get("image_base64")
    previous_image_base64 = message.get("previous_image_base64", "")
    if not request_id:
        return {"type": "inference_error", "reason": "missing_request_id"}
    if not isinstance(image_base64, str) or not image_base64:
        return {
            "type": "inference_error",
            "request_id": request_id,
            "reason": "invalid_frame_payload",
        }
    if len(image_base64.encode("utf-8")) > MAX_FRAME_BASE64_BYTES:
        return {
            "type": "inference_error",
            "request_id": request_id,
            "reason": "frame_too_large",
        }
    if isinstance(previous_image_base64, str) and len(previous_image_base64.encode("utf-8")) > MAX_FRAME_BASE64_BYTES:
        return {
            "type": "inference_error",
            "request_id": request_id,
            "reason": "previous_frame_too_large",
        }
    try:
        base64.b64decode(image_base64, validate=True)
    except (ValueError, binascii.Error):
        return {
            "type": "inference_error",
            "request_id": request_id,
            "reason": "invalid_frame_payload",
        }

    started_at = perf_counter()
    gps_payload = normalize_gps(message.get("gps"))
    if should_short_circuit_quality(mode=effective_mode, question=question, frame_quality=frame_quality):
        vision_payload = quality_gate_vqa_payload(frame_quality)
    else:
        vision_payload = run_vqa_from_frame(
            prompt=prompt,
            image_base64=image_base64,
            model_override=model,
            incremental=incremental,
            previous_image_base64=previous_image_base64 if isinstance(previous_image_base64, str) else "",
            fast_response=fast_response,
        )
    latency_ms = (perf_counter() - started_at) * 1000.0
    fused_result = fuse_vqa_result(
        vision_payload=vision_payload,
        gps_payload=gps_payload,
        latency_ms=latency_ms,
    )
    fused_result.setdefault("diagnostic_metrics", {})
    fused_result["diagnostic_metrics"].update(
        {
            "worker_total_ms": latency_ms,
            "frame_base64_bytes": len(image_base64.encode("utf-8")),
            "mode": mode,
            "fast_response": fast_response,
            "incremental": incremental,
            "quality": frame_quality,
            "walking_roi_present": walking_roi is not None,
        }
    )
    diagnostic_session_id = str(message.get("diagnostic_session_id", "")).strip()
    if diagnostic_session_id:
        append_diagnostic_record(
            diagnostic_session_id,
            {
                "diagnostic_session_id": diagnostic_session_id,
                "event": "backend_vqa_result",
                "frame_id": request_id,
                "mode": mode,
                "question": question,
                "vqa_result": fused_result,
                "diagnostic_metrics": fused_result.get("diagnostic_metrics", {}),
            },
        )
    return {
        "type": "inference_result",
        "request_id": request_id,
        **fused_result,
    }


def build_diagnostic_result(message: dict) -> dict:
    client_id = str(message.get("client_id", "relay-client"))
    image_base64 = message.get("image_base64")
    metadata_json = message.get("metadata_json", "{}")
    if not isinstance(image_base64, str) or not image_base64:
        return {"type": "diagnostic_result", "status": "error", "reason": "invalid_frame_payload"}
    if len(image_base64.encode("utf-8")) > MAX_FRAME_BASE64_BYTES:
        return {"type": "diagnostic_result", "status": "error", "reason": "frame_too_large"}
    try:
        metadata = json.loads(metadata_json) if isinstance(metadata_json, str) else {}
    except ValueError:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    try:
        saved = save_diagnostic_frame(
            session_id=client_id,
            image_base64=image_base64,
            metadata=metadata,
        )
    except ValueError as exc:
        return {"type": "diagnostic_result", "status": "error", "reason": str(exc)}
    return {"type": "diagnostic_result", "status": "ok", **saved}


async def _handle_inference_request(websocket, send_lock: asyncio.Lock, message: dict) -> None:
    request_id = str(message.get("request_id", ""))
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(build_inference_result, message),
            timeout=INFERENCE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        result = {
            "type": "inference_error",
            "request_id": request_id,
            "reason": "inference_timeout",
        }
    except Exception as exc:
        result = {
            "type": "inference_error",
            "request_id": request_id,
            "reason": "inference_failed",
            "detail": str(exc),
        }
    await _send_json(websocket, send_lock, result)


async def connect_worker_forever(
    relay_worker_url: str = RELAY_WORKER_URL,
    worker_id: str = WORKER_ID,
    pairing_token: str = PAIRING_TOKEN,
) -> None:
    while True:
        try:
            await run_worker_once(
                relay_worker_url=relay_worker_url,
                worker_id=worker_id,
                pairing_token=pairing_token,
            )
        except Exception as exc:
            print(f"Worker connection failed: {exc}; retrying in {RECONNECT_DELAY_SECONDS}s")
            await asyncio.sleep(RECONNECT_DELAY_SECONDS)


async def run_worker_once(
    relay_worker_url: str = RELAY_WORKER_URL,
    worker_id: str = WORKER_ID,
    pairing_token: str = PAIRING_TOKEN,
) -> None:
    send_lock = asyncio.Lock()
    active_task: Optional[asyncio.Task] = None

    async with websockets.connect(relay_worker_url, max_size=None) as websocket:
        await _send_json(
            websocket,
            send_lock,
            {
                "type": "worker_register",
                "worker_id": worker_id,
                "pairing_token": pairing_token,
            },
        )
        print(f"Connected to relay as worker_id={worker_id}")

        async for raw_message in websocket:
            try:
                message = json.loads(raw_message)
            except ValueError:
                await _send_json(websocket, send_lock, {"type": "error", "reason": "invalid_json"})
                continue

            if not isinstance(message, dict):
                await _send_json(websocket, send_lock, {"type": "error", "reason": "invalid_message"})
                continue

            message_type = message.get("type")
            if message_type in {"worker_registered", "pong"}:
                continue

            if message_type == "diagnostic_request":
                result = await asyncio.to_thread(build_diagnostic_result, message)
                await _send_json(websocket, send_lock, result)
                continue

            if message_type != "inference_request":
                continue

            request_id = str(message.get("request_id", ""))
            if active_task is not None and not active_task.done():
                if DROP_IF_BUSY:
                    await _send_json(
                        websocket,
                        send_lock,
                        {
                            "type": "dropped",
                            "request_id": request_id,
                            "reason": "worker_busy",
                        },
                    )
                continue

            active_task = asyncio.create_task(
                _handle_inference_request(websocket, send_lock, message)
            )


def main() -> None:
    asyncio.run(connect_worker_forever())


if __name__ == "__main__":
    main()
