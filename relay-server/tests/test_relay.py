from fastapi.testclient import TestClient

import relay_app.main as relay_main
from relay_app.main import app


client = TestClient(app)
TOKEN = "dev-pairing-token"
SAMPLE_JPEG_BASE64 = "/9j/4AAQSkZJRgABAQAAAQABAAD/2w=="


def test_health_endpoint():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_relay_routes_frame_request_to_worker_and_result_to_client():
    worker_id = "worker-route-001"
    client_id = "client-route-001"

    with client.websocket_connect("/ws/worker") as worker_ws:
        worker_ws.send_json(
            {
                "type": "worker_register",
                "worker_id": worker_id,
                "pairing_token": TOKEN,
            }
        )
        assert worker_ws.receive_json() == {
            "type": "worker_registered",
            "worker_id": worker_id,
        }

        with client.websocket_connect("/ws/client") as client_ws:
            client_ws.send_json(
                {
                    "type": "client_register",
                    "client_id": client_id,
                    "worker_id": worker_id,
                    "pairing_token": TOKEN,
                }
            )
            assert client_ws.receive_json() == {
                "type": "client_registered",
                "client_id": client_id,
                "worker_id": worker_id,
                "worker_online": True,
            }

            client_ws.send_json(
                {
                    "type": "frame_request",
                    "request_id": "req-route-001",
                    "prompt": "road scene",
                    "mode": "walking",
                    "question": "前面是红灯还是绿灯？",
                    "model": "qwen2.5vl:7b",
                    "image_base64": SAMPLE_JPEG_BASE64,
                    "gps": {"lat": 37.123, "lon": -122.456},
                    "context": {
                        "prev_summary": "前方是路口。",
                        "prev_scene": "city street",
                        "place_label": "中关村南路附近",
                    },
                }
            )
            routed_message = worker_ws.receive_json()
            assert routed_message["type"] == "inference_request"
            assert routed_message["request_id"] == "req-route-001"
            assert routed_message["client_id"] == client_id
            assert routed_message["image_base64"] == SAMPLE_JPEG_BASE64
            assert routed_message["model"] == "qwen2.5vl:7b"
            # mode/question must survive the relay hop (previously dropped)
            assert routed_message["mode"] == "walking"
            assert routed_message["question"] == "前面是红灯还是绿灯？"
            # context (scene-continuity) must also survive the relay hop.
            assert routed_message["context"]["prev_scene"] == "city street"
            assert routed_message["context"]["place_label"] == "中关村南路附近"

            worker_ws.send_json(
                {
                    "type": "inference_result",
                    "request_id": "req-route-001",
                    "scene": "city street",
                    "objects": ["car"],
                    "vision_location": "outdoor road",
                    "description": "road scene",
                    "gps_location": {"lat": 37.123, "lon": -122.456},
                    "latency_ms": 25.0,
                    "timestamp": "2026-07-23T00:00:00+00:00",
                }
            )

            result_message = client_ws.receive_json()
            assert result_message["type"] == "vqa_result"
            assert result_message["request_id"] == "req-route-001"
            assert result_message["scene"] == "city street"
            assert result_message["objects"] == ["car"]


def test_relay_rejects_frame_that_is_too_large():
    worker_id = "worker-large-001"
    client_id = "client-large-001"

    with client.websocket_connect("/ws/worker") as worker_ws:
        worker_ws.send_json(
            {
                "type": "worker_register",
                "worker_id": worker_id,
                "pairing_token": TOKEN,
            }
        )
        worker_ws.receive_json()

        with client.websocket_connect("/ws/client") as client_ws:
            client_ws.send_json(
                {
                    "type": "client_register",
                    "client_id": client_id,
                    "worker_id": worker_id,
                    "pairing_token": TOKEN,
                }
            )
            client_ws.receive_json()

            client_ws.send_json(
                {
                    "type": "frame_request",
                    "request_id": "req-large-001",
                    "image_base64": "x" * 300_001,
                }
            )

            error_message = client_ws.receive_json()
            assert error_message["type"] == "error"
            assert error_message["request_id"] == "req-large-001"
            assert error_message["reason"] == "frame_too_large"


def test_relay_notifies_client_when_request_times_out(monkeypatch):
    # Force any pending request to be considered expired immediately.
    monkeypatch.setattr(relay_main, "REQUEST_TIMEOUT_SECONDS", 0)
    worker_id = "worker-timeout-001"
    client_id = "client-timeout-001"

    with client.websocket_connect("/ws/worker") as worker_ws:
        worker_ws.send_json(
            {
                "type": "worker_register",
                "worker_id": worker_id,
                "pairing_token": TOKEN,
            }
        )
        worker_ws.receive_json()

        with client.websocket_connect("/ws/client") as client_ws:
            client_ws.send_json(
                {
                    "type": "client_register",
                    "client_id": client_id,
                    "worker_id": worker_id,
                    "pairing_token": TOKEN,
                }
            )
            client_ws.receive_json()

            # First request is routed to the worker but never answered.
            client_ws.send_json(
                {
                    "type": "frame_request",
                    "request_id": "req-timeout-001",
                    "image_base64": SAMPLE_JPEG_BASE64,
                }
            )
            worker_ws.receive_json()

            # A later frame triggers expiry of the stale request; the client must be
            # told its earlier request timed out rather than hanging forever.
            client_ws.send_json(
                {
                    "type": "frame_request",
                    "request_id": "req-timeout-002",
                    "image_base64": SAMPLE_JPEG_BASE64,
                }
            )

            timeout_message = client_ws.receive_json()
            assert timeout_message["type"] == "error"
            assert timeout_message["request_id"] == "req-timeout-001"
            assert timeout_message["reason"] == "request_timeout"


def test_relay_allows_only_one_inflight_request_per_client():
    worker_id = "worker-inflight-001"
    client_id = "client-inflight-001"

    with client.websocket_connect("/ws/worker") as worker_ws:
        worker_ws.send_json(
            {
                "type": "worker_register",
                "worker_id": worker_id,
                "pairing_token": TOKEN,
            }
        )
        worker_ws.receive_json()

        with client.websocket_connect("/ws/client") as client_ws:
            client_ws.send_json(
                {
                    "type": "client_register",
                    "client_id": client_id,
                    "worker_id": worker_id,
                    "pairing_token": TOKEN,
                }
            )
            client_ws.receive_json()

            client_ws.send_json(
                {
                    "type": "frame_request",
                    "request_id": "req-inflight-001",
                    "image_base64": SAMPLE_JPEG_BASE64,
                }
            )
            worker_ws.receive_json()

            client_ws.send_json(
                {
                    "type": "frame_request",
                    "request_id": "req-inflight-002",
                    "image_base64": SAMPLE_JPEG_BASE64,
                }
            )

            error_message = client_ws.receive_json()
            assert error_message["type"] == "error"
            assert error_message["request_id"] == "req-inflight-002"
            assert error_message["reason"] == "too_many_inflight_requests"
