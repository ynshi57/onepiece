from app import worker_client
from app.worker_client import build_inference_result


SAMPLE_JPEG_BASE64 = "/9j/4AAQSkZJRgABAQAAAQABAAD/2w=="


def test_worker_builds_inference_result_from_frame_request():
    payload = build_inference_result(
        {
            "type": "inference_request",
            "request_id": "req-worker-001",
            "prompt": "road scene",
            "image_base64": SAMPLE_JPEG_BASE64,
            "gps": {"lat": 37.123, "lon": -122.456},
        }
    )

    assert payload["type"] == "inference_result"
    assert payload["request_id"] == "req-worker-001"
    assert payload["scene"] == "city street"
    assert payload["gps_location"] == {"lat": 37.123, "lon": -122.456}
    assert isinstance(payload["latency_ms"], (float, int))


def test_worker_rejects_invalid_frame_payload():
    payload = build_inference_result(
        {
            "type": "inference_request",
            "request_id": "req-worker-invalid",
            "image_base64": "",
        }
    )

    assert payload["type"] == "inference_error"
    assert payload["request_id"] == "req-worker-invalid"
    assert payload["reason"] == "invalid_frame_payload"


def test_worker_passes_model_override(monkeypatch):
    captured = {}

    def fake_run_vqa_from_frame(
        prompt: str,
        image_base64: str,
        model_override: str = "",
        incremental: bool = False,
        previous_image_base64: str = "",
        fast_response: bool = False,
    ):
        captured["prompt"] = prompt
        captured["model_override"] = model_override
        captured["incremental"] = incremental
        captured["previous_image_base64"] = previous_image_base64
        captured["fast_response"] = fast_response
        return {
            "objects": ["door"],
            "scene": "hallway",
            "vision_location": "indoor",
            "description": "正前方是一条走廊。",
        }

    monkeypatch.setattr(worker_client, "run_vqa_from_frame", fake_run_vqa_from_frame)
    payload = build_inference_result(
        {
            "type": "inference_request",
            "request_id": "req-worker-model",
            "prompt": "detail",
            "model": "qwen2.5vl:7b",
            "image_base64": SAMPLE_JPEG_BASE64,
        }
    )

    assert payload["type"] == "inference_result"
    assert captured == {
        "prompt": "detail",
        "model_override": "qwen2.5vl:7b",
        "incremental": False,
        "previous_image_base64": "",
        "fast_response": False,
    }


def test_worker_assembles_context_and_marks_incremental(monkeypatch):
    captured = {}

    def fake_run_vqa_from_frame(
        prompt: str,
        image_base64: str,
        model_override: str = "",
        incremental: bool = False,
        previous_image_base64: str = "",
        fast_response: bool = False,
    ):
        captured["prompt"] = prompt
        captured["incremental"] = incremental
        captured["previous_image_base64"] = previous_image_base64
        captured["fast_response"] = fast_response
        return {
            "objects": ["door"],
            "scene": "hallway",
            "vision_location": "indoor",
            "description": "无明显变化。",
            "change_significance": "none",
            "changes": "无明显变化",
        }

    monkeypatch.setattr(worker_client, "run_vqa_from_frame", fake_run_vqa_from_frame)
    payload = build_inference_result(
        {
            "type": "inference_request",
            "request_id": "req-worker-ctx",
            "mode": "surroundings",
            "client_ocr_text": "出口 EXIT",
            "previous_image_base64": SAMPLE_JPEG_BASE64,
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

    assert payload["type"] == "inference_result"
    # A context-bearing frame with no explicit question is an incremental frame.
    assert captured["incremental"] is True
    assert captured["fast_response"] is True
    assert captured["previous_image_base64"] == SAMPLE_JPEG_BASE64
    assert "客户端 OCR 文本" in captured["prompt"]
    assert "出口 EXIT" in captured["prompt"]
    # The continuity block must be appended to the mode prompt.
    assert "【连续观察上下文】" in captured["prompt"]
    assert "中关村南路附近" in captured["prompt"]
    # Continuity fields survive fusion back to the client.
    assert payload["change_significance"] == "none"
    assert payload["changes"] == "无明显变化"


def test_worker_saves_diagnostic_request(monkeypatch, tmp_path):
    monkeypatch.setenv("DIAGNOSTIC_CAPTURE_DIR", str(tmp_path))
    payload = worker_client.build_diagnostic_result(
        {
            "type": "diagnostic_request",
            "client_id": "relay-client",
            "image_base64": SAMPLE_JPEG_BASE64,
            "metadata_json": '{"diagnostic_session_id":"relay-diag","event":"sent_to_backend"}',
        }
    )

    assert payload["type"] == "diagnostic_result"
    assert payload["status"] == "ok"
    assert payload["session_id"] == "relay-diag"
    assert (tmp_path / "session-relay-diag" / "frames" / "frame-0001.jpg").is_file()
