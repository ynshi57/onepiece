from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_runtime_status_endpoint_returns_truth_source():
    response = client.get("/runtime/status")

    assert response.status_code == 200
    payload = response.json()
    assert "status" in payload
    assert "dynamic_model_selection" in payload
    assert "available_models" in payload


def test_vqa_endpoint_returns_fused_response():
    response = client.post(
        "/v1/vqa",
        json={
            "frame_id": "frame-001",
            "gps": {"lat": 39.9042, "lon": 116.4074},
            "prompt": "画面里有什么，什么场景",
        },
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["frame_id"] == "frame-001"
    assert isinstance(payload["objects"], list)
    assert "scene" in payload
    assert "gps_location" in payload
    assert "description" in payload
    assert "summary" in payload
    assert "spatial_description" in payload
    assert payload["risk_level"] in {"low", "medium", "high"}
    assert isinstance(payload["spoken_text"], str)
    assert isinstance(payload["suggested_action"], str)
    assert isinstance(payload["latency_ms"], (float, int))
    assert payload["latency_ms"] >= 0


def test_diagnostics_api_lists_sessions_and_serves_frame(monkeypatch, tmp_path):
    monkeypatch.setenv("DIAGNOSTIC_CAPTURE_DIR", str(tmp_path))
    from app.diagnostic_capture import save_diagnostic_frame

    save_diagnostic_frame(
        session_id="api-session",
        image_base64="/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
        metadata={"diagnostic_session_id": "api-session", "event": "sent_to_backend"},
    )

    sessions_response = client.get("/diagnostics/sessions")
    assert sessions_response.status_code == 200
    sessions = sessions_response.json()["sessions"]
    assert any(item["session_id"] == "api-session" for item in sessions)

    detail_response = client.get("/diagnostics/sessions/api-session")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["frame_count"] == 1
    assert detail["manifest_rows"] == 1
    assert detail["frames"] == ["frames/frame-0001.jpg"]

    frame_response = client.get("/diagnostics/sessions/api-session/frames/frame-0001.jpg")
    assert frame_response.status_code == 200
    assert frame_response.headers["content-type"].startswith("image/jpeg")


def test_diagnostics_annotation_ui_and_labels(monkeypatch, tmp_path):
    monkeypatch.setenv("DIAGNOSTIC_CAPTURE_DIR", str(tmp_path))
    from app.diagnostic_capture import save_diagnostic_frame

    save_diagnostic_frame(
        session_id="ui-session",
        image_base64="/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
        metadata={"diagnostic_session_id": "ui-session", "event": "sent_to_backend"},
    )

    ui_response = client.get("/diagnostics/ui")
    assert ui_response.status_code == 200
    assert "VQASee 诊断标注台" in ui_response.text
    assert "ui-session" in ui_response.text

    annotate_response = client.get("/diagnostics/sessions/ui-session/annotate")
    assert annotate_response.status_code == 200
    assert "保存标注" in annotate_response.text
    assert "frame-0001.jpg" in annotate_response.text

    label_response = client.post(
        "/diagnostics/sessions/ui-session/labels",
        json={"frame": "frames/frame-0001.jpg", "label": "false_positive", "note": "水桶误检成车"},
    )
    assert label_response.status_code == 200
    detail_response = client.get("/diagnostics/sessions/ui-session")
    labels = detail_response.json()["labels"]
    assert labels[0]["label"] == "false_positive"
    assert labels[0]["note"] == "水桶误检成车"


def test_diagnostics_delete_session(monkeypatch, tmp_path):
    monkeypatch.setenv("DIAGNOSTIC_CAPTURE_DIR", str(tmp_path))
    from app.diagnostic_capture import save_diagnostic_frame

    save_diagnostic_frame(
        session_id="delete-session",
        image_base64="/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
        metadata={"diagnostic_session_id": "delete-session", "event": "sent_to_backend"},
    )
    assert (tmp_path / "session-delete-session").is_dir()

    response = client.delete("/diagnostics/sessions/delete-session")
    assert response.status_code == 200
    assert response.json()["status"] == "deleted"
    assert not (tmp_path / "session-delete-session").exists()


def test_diagnostics_cleanup_old_sessions(monkeypatch, tmp_path):
    monkeypatch.setenv("DIAGNOSTIC_CAPTURE_DIR", str(tmp_path))
    old = tmp_path / "session-old"
    old.mkdir()
    (old / "metadata.json").write_text('{"created_at":"2000-01-01T00:00:00+00:00"}', encoding="utf-8")
    fresh = tmp_path / "session-fresh"
    fresh.mkdir()
    (fresh / "metadata.json").write_text('{"created_at":"2999-01-01T00:00:00+00:00"}', encoding="utf-8")

    response = client.post("/diagnostics/cleanup?older_than_days=7")
    assert response.status_code == 200
    assert "old" in response.json()["deleted"]
    assert not old.exists()
    assert fresh.exists()
