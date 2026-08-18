import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket
from time import perf_counter

from app.diagnostic_api import router as diagnostic_router
from app.discovery import BonjourAdvertiser
from app.fusion import fuse_vqa_result
from app.models import VqaRequest, VqaResponse
from app.perception_config import ConfigValidationError, load_active_config
from app.signaling import handle_signaling_websocket
from app.vqa_service import run_vqa, runtime_status, warmup_model


logger = logging.getLogger(__name__)
bonjour_advertiser = BonjourAdvertiser()


def _warmup_enabled() -> bool:
    return os.getenv("QWEN_WARMUP_ON_STARTUP", "1") != "0"


def _backend_port() -> int:
    try:
        return int(os.getenv("VQASEE_SERVICE_PORT", os.getenv("PORT", "9000")))
    except ValueError:
        return 9000


@asynccontextmanager
async def lifespan(app: FastAPI):
    bonjour_advertiser.start(port=_backend_port())
    warmup_task = None
    if _warmup_enabled():
        # Prime the model off the event loop so the first real frame is fast.
        # Best-effort: warmup_model never raises, and we don't await it here so a
        # slow model load can't delay the server accepting connections.
        warmup_task = asyncio.create_task(asyncio.to_thread(warmup_model))
    try:
        yield
    finally:
        if warmup_task is not None:
            warmup_task.cancel()
        bonjour_advertiser.stop()


app = FastAPI(title="Local VQA Server", version="0.1.0", lifespan=lifespan)
app.include_router(diagnostic_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/runtime/status")
def runtime_status_endpoint() -> dict:
    return runtime_status()


@app.get("/runtime/perception-config")
def runtime_perception_config() -> dict:
    """OTA read endpoint: the iPhone fetches the active versioned perception
    config after connecting and applies it if valid (else falls back to its
    compiled-in defaults, visibly). Served over the same HTTP side-channel as
    /runtime/status. A corrupt server-side store is surfaced as a 500 rather
    than silently masquerading as defaults.
    """
    try:
        return load_active_config().to_dict()
    except ConfigValidationError as exc:
        raise HTTPException(status_code=500, detail=f"perception_config_invalid: {exc}") from exc


@app.post("/v1/vqa", response_model=VqaResponse)
def vqa(request: VqaRequest) -> VqaResponse:
    started_at = perf_counter()
    vision_payload = run_vqa(prompt=request.prompt)
    latency_ms = (perf_counter() - started_at) * 1000.0
    gps_payload = request.gps.model_dump() if request.gps else None
    fused_result = fuse_vqa_result(
        vision_payload=vision_payload,
        gps_payload=gps_payload,
        latency_ms=latency_ms,
    )
    return VqaResponse(frame_id=request.frame_id, **fused_result)


@app.websocket("/ws/signaling")
async def ws_signaling(websocket: WebSocket) -> None:
    await handle_signaling_websocket(websocket)
