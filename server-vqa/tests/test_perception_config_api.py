"""API tests for the perception-config OTA endpoints and the iPhone-harness wizard."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Isolate the config store per test so bumps don't leak across tests / repo.
    monkeypatch.setenv("VQASEE_PERCEPTION_CONFIG_PATH", str(tmp_path / "perception_config.json"))
    return TestClient(app)


def test_runtime_perception_config_defaults_to_v1(client):
    resp = client.get("/runtime/perception-config")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["version"] == 1
    assert payload["thresholds"]["near_blocked_area"] == 0.82
    assert set(payload["roi"].keys()) == {"near", "left", "right"}


def test_bump_persists_and_runtime_reflects_it(client):
    resp = client.post(
        "/diagnostics/perception-config/bump",
        json={"thresholds": {"near_blocked_area": 0.7}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["config"]["version"] == 2
    assert body["config"]["thresholds"]["near_blocked_area"] == 0.7

    runtime = client.get("/runtime/perception-config").json()
    assert runtime["version"] == 2
    assert runtime["thresholds"]["near_blocked_area"] == 0.7


def test_invalid_bump_is_rejected_and_not_written(client):
    resp = client.post(
        "/diagnostics/perception-config/bump",
        json={"thresholds": {"near_blocked_area": 9.0}},
    )
    assert resp.status_code == 400
    # Nothing was written: still default v1.
    assert client.get("/runtime/perception-config").json()["version"] == 1


def test_invalid_roi_bump_rejected(client):
    resp = client.post(
        "/diagnostics/perception-config/bump",
        json={"roi": {"near": {"x": 0.9, "y": 0.0, "w": 0.5, "h": 0.5}}},  # x+w > 1
    )
    assert resp.status_code == 400
    assert client.get("/runtime/perception-config").json()["version"] == 1


def test_config_editor_ui_renders(client):
    resp = client.get("/diagnostics/perception-config/ui")
    assert resp.status_code == 200
    assert "保存并升级版本" in resp.text
    assert "near_blocked_area" in resp.text


# ---------------------------------------------------------------------------
# iPhone offline-harness wizard
# ---------------------------------------------------------------------------

def _write_manifest(path):
    rows = [
        {
            "frame_id": "f1",
            "image_path": "/does/not/matter.png",
            "ground_truth": {
                "near_path_status": "blocked",
                "left_front_status": "candidateOpen",
                "right_front_status": "candidateOpen",
                "focus_direction": "center",
            },
        },
        {
            "frame_id": "f2",
            "image_path": "/does/not/matter2.png",
            "ground_truth": {
                "near_path_status": "candidateOpen",
                "left_front_status": "candidateOpen",
                "right_front_status": "candidateOpen",
                "focus_direction": "unknown",
            },
        },
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_ios_harness_ui_shows_instructions_without_predictions(client, tmp_path):
    manifest = tmp_path / "m.jsonl"
    _write_manifest(manifest)
    resp = client.get("/diagnostics/datasets/ios-harness/ui", params={"manifest": str(manifest)})
    assert resp.status_code == 200
    assert "swift build" in resp.text
    assert "PerceptionHarness" in resp.text


def test_ios_harness_ui_reports_missing_prediction_file(client, tmp_path):
    manifest = tmp_path / "m.jsonl"
    _write_manifest(manifest)
    resp = client.get(
        "/diagnostics/datasets/ios-harness/ui",
        params={"manifest": str(manifest), "predictions": "/tmp/definitely-missing-preds.jsonl"},
    )
    assert resp.status_code == 200
    assert "找不到预测文件" in resp.text


def test_ios_harness_ui_scores_real_predictions(client, tmp_path):
    manifest = tmp_path / "m.jsonl"
    _write_manifest(manifest)
    # Harness predictions must live under an allowed root (/tmp); tmp_path is not.
    preds = "/tmp/vqasee-test-ios-harness-preds.jsonl"
    rows = [
        {"frame_id": "f1", "prediction": {"near_path_status": "blocked", "left_front_status": "candidateOpen", "right_front_status": "candidateOpen", "focus_direction": "center", "prediction_source": "ios_coreml_offline_harness"}},
        {"frame_id": "f2", "prediction": {"near_path_status": "candidateOpen", "left_front_status": "candidateOpen", "right_front_status": "candidateOpen", "focus_direction": "unknown", "prediction_source": "ios_coreml_offline_harness"}},
    ]
    with open(preds, "w", encoding="utf-8") as handle:
        handle.write("\n".join(json.dumps(r) for r in rows) + "\n")
    try:
        resp = client.get(
            "/diagnostics/datasets/ios-harness/ui",
            params={"manifest": str(manifest), "predictions": preds},
        )
        assert resp.status_code == 200
        assert "状态准确率" in resp.text
        assert "ios_coreml_offline_harness" in resp.text
    finally:
        import os
        os.remove(preds)


def test_ios_harness_parity_reports_unsupported_without_onnx(client, tmp_path):
    manifest = tmp_path / "m.jsonl"
    _write_manifest(manifest)
    preds = "/tmp/vqasee-test-ios-harness-preds2.jsonl"
    with open(preds, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"frame_id": "f1", "prediction": {"near_path_status": "blocked"}}) + "\n")
    try:
        resp = client.post(
            "/diagnostics/datasets/ios-harness/parity",
            params={"manifest": str(manifest), "predictions": preds},
        )
        assert resp.status_code == 200
        # onnxruntime is an optional dep; in this env parity is honestly unsupported.
        body = resp.json()
        assert body["status"] in {"ok", "unsupported"}
        if body["status"] == "unsupported":
            assert body["reason"]
    finally:
        import os
        os.remove(preds)
