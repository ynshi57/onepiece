from fastapi.testclient import TestClient

import app.signaling as signaling
from app.main import app


client = TestClient(app)
SAMPLE_JPEG_BASE64 = "/9j/4AAQSkZJRgABAQAAAQABAAD/2w=="


def test_signaling_websocket_stream_start_returns_ack_only():
    with client.websocket_connect("/ws/signaling") as websocket:
        ready_message = websocket.receive_json()
        assert ready_message["type"] == "server_ready"
        assert "session_id" in ready_message

        websocket.send_json(
            {
                "type": "stream_start",
                "frame_id": "frame-ws-001",
                "prompt": "road scene",
                "gps": {"lat": 37.33037, "lon": -122.02849},
            }
        )

        ack_message = websocket.receive_json()
        assert ack_message["type"] == "stream_ack"
        assert ack_message["frame_id"] == "frame-ws-001"

def test_signaling_websocket_unknown_message_returns_error():
    with client.websocket_connect("/ws/signaling") as websocket:
        websocket.receive_json()

        websocket.send_json({"type": "unknown"})
        error_message = websocket.receive_json()

        assert error_message["type"] == "error"
        assert error_message["reason"] == "unsupported_message"


def test_signaling_websocket_frame_message_returns_frame_level_vqa_result():
    with client.websocket_connect("/ws/signaling") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "stream_start", "frame_id": "bootstrap", "prompt": "road scene"})
        websocket.receive_json()

        websocket.send_json(
            {
                "type": "frame",
                "frame_id": "frame-002",
                "prompt": "road scene",
                "image_base64": SAMPLE_JPEG_BASE64,
                "gps": {"lat": 37.123, "lon": -122.456},
            }
        )
        vqa_message = websocket.receive_json()

        assert vqa_message["type"] == "vqa_result"
        assert vqa_message["frame_id"] == "frame-002"
        assert vqa_message["scene"] == "city street"
        assert vqa_message["gps_location"] == {"lat": 37.123, "lon": -122.456}
        assert isinstance(vqa_message["latency_ms"], (float, int))
        assert vqa_message["latency_ms"] >= 0
        assert isinstance(vqa_message["description"], str)


def test_signaling_frame_with_context_assembles_continuity_prompt(monkeypatch):
    captured = {}

    def fake_run_vqa_from_frame(prompt, image_base64, model_override="", incremental=False, previous_image_base64="", fast_response=False):
        captured["prompt"] = prompt
        captured["incremental"] = incremental
        captured["fast_response"] = fast_response
        return {
            "objects": ["door"],
            "scene": "hallway",
            "vision_location": "indoor",
            "description": "无明显变化。",
            "change_significance": "none",
            "changes": "无明显变化",
        }

    monkeypatch.setattr(signaling, "run_vqa_from_frame", fake_run_vqa_from_frame)

    with client.websocket_connect("/ws/signaling") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "stream_start", "frame_id": "bootstrap"})
        websocket.receive_json()

        websocket.send_json(
            {
                "type": "frame",
                "frame_id": "frame-ctx",
                "mode": "surroundings",
                "image_base64": SAMPLE_JPEG_BASE64,
                "context": {
                    "prev_summary": "正前方是一条走廊。",
                    "prev_scene": "hallway",
                    "prev_objects": ["door"],
                    "place_label": "中关村南路附近",
                    "elapsed_ms": 2000,
                },
            }
        )
        vqa_message = websocket.receive_json()

    assert "【连续观察上下文】" in captured["prompt"]
    assert "中关村南路附近" in captured["prompt"]
    # Context-bearing frame with no question -> incremental (shorter/faster) answer.
    assert captured["incremental"] is True
    assert captured["fast_response"] is True
    assert vqa_message["type"] == "vqa_result"
    assert vqa_message["change_significance"] == "none"
    assert vqa_message["changes"] == "无明显变化"


def test_signaling_websocket_rejects_oversized_frame():
    with client.websocket_connect("/ws/signaling") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "stream_start", "frame_id": "bootstrap", "prompt": "road scene"})
        websocket.receive_json()

        websocket.send_json(
            {
                "type": "frame",
                "frame_id": "frame-too-large",
                "prompt": "road scene",
                "image_base64": "x" * 900_001,
            }
        )
        error_message = websocket.receive_json()

        assert error_message["type"] == "error"
        assert error_message["reason"] == "frame_too_large"


def test_signaling_diagnostic_frame_upload_persists_on_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("DIAGNOSTIC_CAPTURE_DIR", str(tmp_path))
    metadata = {
        "diagnostic_session_id": "test-session",
        "frame": "diag-1.jpg",
        "mode": "walking",
        "event": "sent_to_backend",
        "perception": {
            "model_status": "loaded",
            "objects": [{"kind": "car", "direction": "center", "confidence": 0.86}],
            "road_cues": {"crosswalk": "unknown", "lane_marking": "unknown", "curb": "unknown"},
            "depth_cues": {"near_drop": "unknown", "nearest_obstacle_direction": "unknown"},
        },
    }

    with client.websocket_connect("/ws/signaling") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "diagnostic_frame",
                "image_base64": SAMPLE_JPEG_BASE64,
                "metadata_json": __import__("json").dumps(metadata),
            }
        )
        ack = websocket.receive_json()

    assert ack["type"] == "diagnostic_ack"
    assert ack["session_id"] == "test-session"
    session_dir = tmp_path / "session-test-session"
    assert (session_dir / "metadata.json").is_file()
    assert (session_dir / "manifest.jsonl").is_file()
    assert (session_dir / "frames" / "frame-0001.jpg").is_file()
    assert "frames/frame-0001.jpg" in (session_dir / "manifest.jsonl").read_text(encoding="utf-8")


def test_signaling_walking_quality_gate_returns_visible_warning(monkeypatch):
    def fail_run_vqa_from_frame(*args, **kwargs):
        raise AssertionError("quality gate should not call Qwen")

    monkeypatch.setattr(signaling, "run_vqa_from_frame", fail_run_vqa_from_frame)

    with client.websocket_connect("/ws/signaling") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "stream_start", "frame_id": "bootstrap"})
        websocket.receive_json()

        websocket.send_json(
            {
                "type": "frame",
                "frame_id": "frame-quality",
                "mode": "walking",
                "image_base64": SAMPLE_JPEG_BASE64,
                "frame_quality": {"exposure": "too_dark", "confidence": "high"},
            }
        )
        vqa_message = websocket.receive_json()

    assert vqa_message["type"] == "vqa_result"
    assert vqa_message["spoken_text"] == "光线太暗，我看不清前方。"
    assert vqa_message["risk_level"] == "medium"
    assert vqa_message["diagnostic_metrics"]["quality_gate"] == "short_circuit"
