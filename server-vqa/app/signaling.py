import binascii
import os
from time import perf_counter
from typing import Dict, Optional
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect

from app.fusion import fuse_vqa_result
from app.prompts import resolve_prompt
from app.scene_context import build_contextual_prompt
from app.vqa_service import run_vqa_from_frame


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


MAX_FRAME_BASE64_BYTES = _env_int("MAX_FRAME_BASE64_BYTES", 300_000)


def normalize_gps(raw_gps: object) -> Optional[Dict[str, float]]:
    if not isinstance(raw_gps, dict):
        return None

    try:
        lat = float(raw_gps["lat"])
        lon = float(raw_gps["lon"])
    except (KeyError, TypeError, ValueError):
        return None

    return {"lat": lat, "lon": lon}


async def handle_signaling_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    session_id = str(uuid4())
    await websocket.send_json({"type": "server_ready", "session_id": session_id})

    try:
        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict):
                await websocket.send_json({"type": "error", "reason": "invalid_message"})
                continue

            message_type = message.get("type")
            if message_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if message_type == "location_update":
                await websocket.send_json({"type": "location_ack", "gps": message.get("gps")})
                continue

            if message_type == "stream_start":
                frame_id = str(message.get("frame_id", "frame-unknown"))

                await websocket.send_json(
                    {
                        "type": "stream_ack",
                        "frame_id": frame_id,
                        "session_id": session_id,
                    }
                )
                continue

            if message_type == "frame":
                frame_id = str(message.get("frame_id", "frame-unknown"))
                mode = str(message.get("mode", ""))
                question = str(message.get("question", ""))
                context = message.get("context")
                context = context if isinstance(context, dict) else None
                prompt = resolve_prompt(
                    mode=mode,
                    question=question,
                    legacy_prompt=str(message.get("prompt", "")),
                )
                prompt = build_contextual_prompt(prompt, mode=mode, context=context)
                # A follow-up frame with prior context and no explicit question is an
                # incremental "what changed" frame -> allow a shorter, faster answer.
                incremental = context is not None and not question.strip()
                model = str(message.get("model", ""))
                gps_payload = normalize_gps(message.get("gps"))
                image_base64 = message.get("image_base64")
                started_at = perf_counter()
                if not isinstance(image_base64, str) or not image_base64:
                    await websocket.send_json({"type": "error", "reason": "invalid_frame_payload"})
                    continue
                if len(image_base64.encode("utf-8")) > MAX_FRAME_BASE64_BYTES:
                    await websocket.send_json({"type": "error", "reason": "frame_too_large"})
                    continue

                try:
                    vision_payload = run_vqa_from_frame(
                        prompt=prompt,
                        image_base64=image_base64,
                        model_override=model,
                        incremental=incremental,
                    )
                except (ValueError, binascii.Error):
                    await websocket.send_json({"type": "error", "reason": "invalid_frame_payload"})
                    continue

                latency_ms = (perf_counter() - started_at) * 1000.0
                fused_result = fuse_vqa_result(
                    vision_payload=vision_payload,
                    gps_payload=gps_payload,
                    latency_ms=latency_ms,
                )
                await websocket.send_json(
                    {
                        "type": "vqa_result",
                        "frame_id": frame_id,
                        **fused_result,
                    }
                )
                continue

            if message_type == "stop":
                await websocket.send_json({"type": "stream_stopped", "session_id": session_id})
                break

            await websocket.send_json({"type": "error", "reason": "unsupported_message"})
    except WebSocketDisconnect:
        return
