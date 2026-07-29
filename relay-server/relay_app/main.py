import asyncio
import os
from collections import defaultdict, deque
from dataclasses import dataclass, field
from time import monotonic
from typing import Deque, Dict, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


PAIRING_TOKEN = os.getenv("RELAY_PAIRING_TOKEN", "dev-pairing-token")
MAX_FRAME_BASE64_BYTES = _env_int("MAX_FRAME_BASE64_BYTES", 900_000)
MAX_FRAMES_PER_MINUTE = _env_int("MAX_FRAMES_PER_MINUTE", 30)
MAX_INFLIGHT_PER_CLIENT = _env_int("MAX_INFLIGHT_PER_CLIENT", 1)
REQUEST_TIMEOUT_SECONDS = _env_int("RELAY_REQUEST_TIMEOUT_SECONDS", 30)


@dataclass
class RelayConnection:
    websocket: WebSocket
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send_json(self, payload: dict) -> None:
        async with self.send_lock:
            await self.websocket.send_json(payload)


@dataclass
class ClientConnection(RelayConnection):
    worker_id: str = ""


@dataclass
class PendingRequest:
    client_id: str
    worker_id: str
    started_at: float


@dataclass
class ExpiredRequest:
    request_id: str
    client_id: str


class RelayState:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.workers: Dict[str, RelayConnection] = {}
        self.clients: Dict[str, ClientConnection] = {}
        self.pending: Dict[str, PendingRequest] = {}
        self.client_inflight: Dict[str, int] = defaultdict(int)
        self.client_frame_times: Dict[str, Deque[float]] = defaultdict(deque)

    async def register_worker(self, worker_id: str, connection: RelayConnection) -> None:
        async with self.lock:
            self.workers[worker_id] = connection

    async def unregister_worker(self, worker_id: str, connection: RelayConnection) -> None:
        stale_clients = []
        async with self.lock:
            if self.workers.get(worker_id) is connection:
                self.workers.pop(worker_id, None)
                expired_request_ids = [
                    request_id
                    for request_id, pending in self.pending.items()
                    if pending.worker_id == worker_id
                ]
                for request_id in expired_request_ids:
                    pending = self.pending.pop(request_id)
                    self.client_inflight[pending.client_id] = max(
                        0, self.client_inflight[pending.client_id] - 1
                    )
                stale_clients = [
                    client
                    for client in self.clients.values()
                    if client.worker_id == worker_id
                ]

        for client in stale_clients:
            await _safe_send(
                client,
                {
                    "type": "worker_offline",
                    "worker_id": worker_id,
                },
            )

    async def register_client(
        self, client_id: str, worker_id: str, connection: ClientConnection
    ) -> bool:
        async with self.lock:
            connection.worker_id = worker_id
            self.clients[client_id] = connection
            return worker_id in self.workers

    async def unregister_client(self, client_id: str, connection: ClientConnection) -> None:
        async with self.lock:
            if self.clients.get(client_id) is connection:
                self.clients.pop(client_id, None)
                expired_request_ids = [
                    request_id
                    for request_id, pending in self.pending.items()
                    if pending.client_id == client_id
                ]
                for request_id in expired_request_ids:
                    self.pending.pop(request_id, None)
                self.client_inflight.pop(client_id, None)
                self.client_frame_times.pop(client_id, None)

    async def enqueue_frame(
        self,
        client_id: str,
        worker_id: str,
        request_id: str,
        payload: dict,
    ) -> tuple[bool, str, Optional[RelayConnection], list["ExpiredRequest"]]:
        now = monotonic()
        async with self.lock:
            expired = self._expire_old_requests_locked(now)

            worker = self.workers.get(worker_id)
            if worker is None:
                return False, "worker_offline", None, expired

            if request_id in self.pending:
                return False, "duplicate_request_id", None, expired

            if self.client_inflight[client_id] >= MAX_INFLIGHT_PER_CLIENT:
                return False, "too_many_inflight_requests", None, expired

            frame_times = self.client_frame_times[client_id]
            while frame_times and now - frame_times[0] > 60:
                frame_times.popleft()
            if len(frame_times) >= MAX_FRAMES_PER_MINUTE:
                return False, "rate_limited", None, expired

            frame_times.append(now)
            self.pending[request_id] = PendingRequest(
                client_id=client_id,
                worker_id=worker_id,
                started_at=now,
            )
            self.client_inflight[client_id] += 1
            return True, "ok", worker, expired

    async def complete_request(self, request_id: str) -> Optional[ClientConnection]:
        async with self.lock:
            pending = self.pending.pop(request_id, None)
            if pending is None:
                return None
            self.client_inflight[pending.client_id] = max(
                0, self.client_inflight[pending.client_id] - 1
            )
            return self.clients.get(pending.client_id)

    async def notify_expired(self, expired: list[ExpiredRequest]) -> None:
        """Tell clients their in-flight requests timed out. Called after the state
        lock is released so websocket sends never block state mutation."""
        if not expired:
            return
        async with self.lock:
            targets = [
                (entry.request_id, self.clients.get(entry.client_id))
                for entry in expired
            ]
        for request_id, client in targets:
            if client is None:
                continue
            await _safe_send(
                client,
                {
                    "type": "error",
                    "request_id": request_id,
                    "reason": "request_timeout",
                },
            )

    def _expire_old_requests_locked(self, now: float) -> list[ExpiredRequest]:
        expired_request_ids = [
            request_id
            for request_id, pending in self.pending.items()
            if now - pending.started_at > REQUEST_TIMEOUT_SECONDS
        ]
        expired: list[ExpiredRequest] = []
        for request_id in expired_request_ids:
            pending = self.pending.pop(request_id)
            self.client_inflight[pending.client_id] = max(
                0, self.client_inflight[pending.client_id] - 1
            )
            expired.append(
                ExpiredRequest(request_id=request_id, client_id=pending.client_id)
            )
        return expired


state = RelayState()
app = FastAPI(title="VQASee Relay Server", version="0.1.0")


def _is_authorized(payload: dict) -> bool:
    return isinstance(payload, dict) and payload.get("pairing_token") == PAIRING_TOKEN


async def _safe_send(connection: RelayConnection, payload: dict) -> None:
    try:
        await connection.send_json(payload)
    except Exception:
        return


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "max_frame_base64_bytes": MAX_FRAME_BASE64_BYTES,
        "max_frames_per_minute": MAX_FRAMES_PER_MINUTE,
        "max_inflight_per_client": MAX_INFLIGHT_PER_CLIENT,
    }


@app.websocket("/ws/worker")
async def worker_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    worker_id = ""
    connection = RelayConnection(websocket=websocket)

    try:
        register_message = await websocket.receive_json()
        if (
            not _is_authorized(register_message)
            or register_message.get("type") != "worker_register"
            or not isinstance(register_message.get("worker_id"), str)
            or not register_message["worker_id"].strip()
        ):
            await connection.send_json({"type": "error", "reason": "unauthorized"})
            await websocket.close(code=1008)
            return

        worker_id = register_message["worker_id"].strip()
        await state.register_worker(worker_id, connection)
        await connection.send_json({"type": "worker_registered", "worker_id": worker_id})

        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict):
                await connection.send_json({"type": "error", "reason": "invalid_message"})
                continue

            message_type = message.get("type")
            if message_type == "ping":
                await connection.send_json({"type": "pong"})
                continue

            if message_type in {"inference_result", "inference_error", "dropped"}:
                request_id = message.get("request_id")
                if not isinstance(request_id, str) or not request_id:
                    await connection.send_json({"type": "error", "reason": "missing_request_id"})
                    continue

                client = await state.complete_request(request_id)
                if client is None:
                    continue

                forward_payload = dict(message)
                if message_type == "inference_result":
                    forward_payload["type"] = "vqa_result"
                await _safe_send(client, forward_payload)
                continue

            await connection.send_json({"type": "error", "reason": "unsupported_message"})
    except WebSocketDisconnect:
        return
    finally:
        if worker_id:
            await state.unregister_worker(worker_id, connection)


@app.websocket("/ws/client")
async def client_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    client_id = ""
    worker_id = ""
    connection = ClientConnection(websocket=websocket)

    try:
        register_message = await websocket.receive_json()
        if (
            not _is_authorized(register_message)
            or register_message.get("type") != "client_register"
            or not isinstance(register_message.get("client_id"), str)
            or not register_message["client_id"].strip()
            or not isinstance(register_message.get("worker_id"), str)
            or not register_message["worker_id"].strip()
        ):
            await connection.send_json({"type": "error", "reason": "unauthorized"})
            await websocket.close(code=1008)
            return

        client_id = register_message["client_id"].strip()
        worker_id = register_message["worker_id"].strip()
        worker_online = await state.register_client(client_id, worker_id, connection)
        await connection.send_json(
            {
                "type": "client_registered",
                "client_id": client_id,
                "worker_id": worker_id,
                "worker_online": worker_online,
            }
        )

        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict):
                await connection.send_json({"type": "error", "reason": "invalid_message"})
                continue

            message_type = message.get("type")
            if message_type == "ping":
                await connection.send_json({"type": "pong"})
                continue

            if message_type != "frame_request":
                await connection.send_json({"type": "error", "reason": "unsupported_message"})
                continue

            request_id = message.get("request_id")
            image_base64 = message.get("image_base64")
            if not isinstance(request_id, str) or not request_id:
                await connection.send_json({"type": "error", "reason": "missing_request_id"})
                continue
            if not isinstance(image_base64, str) or not image_base64:
                await connection.send_json(
                    {
                        "type": "error",
                        "request_id": request_id,
                        "reason": "invalid_frame_payload",
                    }
                )
                continue
            if len(image_base64.encode("utf-8")) > MAX_FRAME_BASE64_BYTES:
                await connection.send_json(
                    {
                        "type": "error",
                        "request_id": request_id,
                        "reason": "frame_too_large",
                    }
                )
                continue
            previous_image_base64 = message.get("previous_image_base64", "")
            if isinstance(previous_image_base64, str) and len(previous_image_base64.encode("utf-8")) > MAX_FRAME_BASE64_BYTES:
                await connection.send_json(
                    {
                        "type": "error",
                        "request_id": request_id,
                        "reason": "previous_frame_too_large",
                    }
                )
                continue

            ok, reason, worker, expired = await state.enqueue_frame(
                client_id=client_id,
                worker_id=worker_id,
                request_id=request_id,
                payload=message,
            )
            # Notify any clients whose earlier requests we just expired, so they
            # don't hang forever waiting on a result that will never arrive.
            await state.notify_expired(expired)
            if not ok or worker is None:
                await connection.send_json(
                    {
                        "type": "error",
                        "request_id": request_id,
                        "reason": reason,
                    }
                )
                continue

            await worker.send_json(
                {
                    "type": "inference_request",
                    "request_id": request_id,
                    "client_id": client_id,
                    "prompt": str(message.get("prompt", "")),
                    "mode": str(message.get("mode", "")),
                    "question": str(message.get("question", "")),
                    "model": str(message.get("model", "")),
                    "image_base64": image_base64,
                    "previous_image_base64": str(message.get("previous_image_base64", "")),
                    "client_ocr_text": str(message.get("client_ocr_text", "")),
                    "gps": message.get("gps"),
                    "context": message.get("context"),
                }
            )
    except WebSocketDisconnect:
        return
    finally:
        if client_id:
            await state.unregister_client(client_id, connection)
