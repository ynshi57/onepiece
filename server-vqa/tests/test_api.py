from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


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
