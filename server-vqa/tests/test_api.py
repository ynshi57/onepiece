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
    assert payload["risk_zone"] in {"immediate", "near", "mid", "far", "unknown"}
    assert payload["direction"] in {"left", "center", "right", "left_front", "right_front", "front", "unknown"}
    assert payload["distance_confidence"] in {"none", "low", "medium", "high"}
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
    assert "VQASee 闭环实验平台" in ui_response.text
    assert "ui-session" in ui_response.text

    annotate_response = client.get("/diagnostics/sessions/ui-session/annotate")
    assert annotate_response.status_code == 200
    assert "保存标注" in annotate_response.text
    assert "frame-0001.jpg" in annotate_response.text
    assert "真实画面记录" in annotate_response.text
    assert "真实画面" in annotate_response.text
    assert "误报内容" in annotate_response.text
    assert "画面变化检测" in annotate_response.text

    label_response = client.post(
        "/diagnostics/sessions/ui-session/labels",
        json={
            "frame": "frames/frame-0001.jpg",
            "label": "wrong_class",
            "true_scene": "室内走廊，右前方有蓝色水桶",
            "true_risks": "无明显风险",
            "false_positives": "水桶被误检成车",
            "missed_risks": "",
            "note": "测试结构化标注",
        },
    )
    assert label_response.status_code == 200
    detail_response = client.get("/diagnostics/sessions/ui-session")
    labels = detail_response.json()["labels"]
    assert labels[0]["label"] == "wrong_class"
    assert labels[0]["true_scene"] == "室内走廊，右前方有蓝色水桶"
    assert labels[0]["true_risks"] == "无明显风险"
    assert labels[0]["false_positives"] == "水桶被误检成车"
    assert labels[0]["note"] == "测试结构化标注"


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


def test_diagnostics_delete_label(monkeypatch, tmp_path):
    monkeypatch.setenv("DIAGNOSTIC_CAPTURE_DIR", str(tmp_path))
    from app.diagnostic_capture import save_diagnostic_frame

    save_diagnostic_frame(
        session_id="label-delete-session",
        image_base64="/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
        metadata={"diagnostic_session_id": "label-delete-session", "event": "sent_to_backend"},
    )
    response = client.post(
        "/diagnostics/sessions/label-delete-session/labels",
        json={"frame": "frames/frame-0001.jpg", "label": "wrong_class", "note": "椅子被识别成摩托车"},
    )
    assert response.status_code == 200
    detail_response = client.get("/diagnostics/sessions/label-delete-session")
    assert detail_response.json()["labels"][0]["_index"] == 0

    delete_response = client.delete("/diagnostics/sessions/label-delete-session/labels/0")

    assert delete_response.status_code == 200
    assert delete_response.json()["status"] == "deleted"
    detail_after = client.get("/diagnostics/sessions/label-delete-session")
    assert detail_after.json()["labels"] == []


def test_diagnostics_report_finds_evolution_tasks(monkeypatch, tmp_path):
    monkeypatch.setenv("DIAGNOSTIC_CAPTURE_DIR", str(tmp_path))
    from app.diagnostic_capture import save_diagnostic_frame

    save_diagnostic_frame(
        session_id="report-session",
        image_base64="/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
        metadata={
            "diagnostic_session_id": "report-session",
            "event": "sent_to_backend",
            "mode": "walking",
            "perception": {
                "model_status": "loaded",
                "objects": [
                    {"kind": "car", "label": "车辆", "direction": "center", "confidence": 0.92}
                ],
                "road_cues": {},
                "depth_cues": {},
            },
        },
    )
    for _ in range(3):
        save_diagnostic_frame(
            session_id="report-session",
            image_base64="/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
            metadata={
                "diagnostic_session_id": "report-session",
                "event": "captured_while_in_flight",
                "mode": "walking",
                "perception": {"model_status": "loaded", "objects": [], "road_cues": {}, "depth_cues": {}},
            },
        )
    client.post(
        "/diagnostics/sessions/report-session/labels",
        json={
            "frame": "frames/frame-0001.jpg",
            "label": "wrong_class",
            "true_scene": "室内走廊，右侧有蓝色水桶",
            "true_risks": "无明显风险",
            "false_positives": "水桶被误识别成车辆",
            "missed_risks": "",
        },
    )

    response = client.get("/diagnostics/sessions/report-session/report")

    assert response.status_code == 200
    report = response.json()
    codes = {item["code"] for item in report["findings"]}
    assert "high_in_flight_ratio" in codes
    assert "indoor_vehicle_false_positive" in codes
    assert "missing_qwen_raw_output" in codes
    assert report["metrics"]["captured_while_in_flight"] == 3
    assert report["metrics"]["vehicle_false_positive_labels"] == 1
    assert any(task["primary"] in {"罗根", "全麦"} for task in report["task_suggestions"])

    html_response = client.get("/diagnostics/sessions/report-session/report/ui")
    assert html_response.status_code == 200
    assert "评估报告" in html_response.text
    assert "自动发现的问题" in html_response.text


def test_diagnostics_report_counts_backend_vqa_result(monkeypatch, tmp_path):
    monkeypatch.setenv("DIAGNOSTIC_CAPTURE_DIR", str(tmp_path))
    from app.diagnostic_capture import append_diagnostic_record, save_diagnostic_frame

    save_diagnostic_frame(
        session_id="raw-session",
        image_base64="/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
        metadata={"diagnostic_session_id": "raw-session", "event": "sent_to_backend"},
    )
    append_diagnostic_record(
        "raw-session",
        {
            "diagnostic_session_id": "raw-session",
            "event": "backend_vqa_result",
            "frame_id": "frame-1",
            "vqa_result": {"summary": "ok"},
            "diagnostic_metrics": {"qwen_raw_output_preview": "{...}", "schema_name": "vqa_walking_fast_result"},
        },
    )

    response = client.get("/diagnostics/sessions/raw-session/report")

    assert response.status_code == 200
    report = response.json()
    assert report["metrics"]["qwen_result_frames"] == 1
    assert "missing_qwen_raw_output" not in {finding["code"] for finding in report["findings"]}


def test_diagnostics_path_guidance_visualization_ui(monkeypatch, tmp_path):
    monkeypatch.setenv("DIAGNOSTIC_CAPTURE_DIR", str(tmp_path))
    from app.diagnostic_capture import save_diagnostic_frame

    save_diagnostic_frame(
        session_id="path-ui-session",
        image_base64="/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
        metadata={
            "diagnostic_session_id": "path-ui-session",
            "event": "sent_to_backend",
            "perception": {
                "path_guidance": {
                    "near_path_status": "caution",
                    "focus_direction": "right",
                    "guidance_corridor": {"x": 0.25, "y": 0, "width": 0.5, "height": 0.58},
                    "blocked_regions": [{"x": 0.62, "y": 0.2, "width": 0.2, "height": 0.2}],
                    "depth_capability": "unsupported",
                    "segmentation_capability": "active",
                }
            },
        },
    )

    response = client.get("/diagnostics/sessions/path-ui-session/path-guidance/ui")

    assert response.status_code == 200
    assert "引导层可视化" in response.text
    assert "path_guidance" in response.text
    assert "<svg" in response.text
    assert "frame-0001.jpg" in response.text


def test_diagnostics_session_path_manifest_and_eval_ui(monkeypatch, tmp_path):
    monkeypatch.setenv("DIAGNOSTIC_CAPTURE_DIR", str(tmp_path))
    from app.diagnostic_capture import save_diagnostic_frame

    save_diagnostic_frame(
        session_id="path-export-session",
        image_base64="/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
        metadata={
            "diagnostic_session_id": "path-export-session",
            "event": "sent_to_backend",
            "perception": {
                "path_guidance": {
                    "near_path_status": "caution",
                    "left_front_status": "candidateOpen",
                    "right_front_status": "candidateOpen",
                    "focus_direction": "center",
                }
            },
        },
    )
    client.post(
        "/diagnostics/sessions/path-export-session/labels",
        json={
            "frame": "frames/frame-0001.jpg",
            "label": "no_obvious_risk",
            "true_scene": "室内走廊",
            "true_risks": "无明显风险",
        },
    )

    manifest_response = client.get("/diagnostics/sessions/path-export-session/path-manifest")
    assert manifest_response.status_code == 200
    assert "path-export-session/frames/frame-0001.jpg" in manifest_response.text

    eval_response = client.get("/diagnostics/sessions/path-export-session/path-eval")
    assert eval_response.status_code == 200
    assert eval_response.json()["labeled_frames"] == 1

    eval_ui_response = client.get("/diagnostics/sessions/path-export-session/path-eval/ui")
    assert eval_ui_response.status_code == 200
    assert "路径评估" in eval_ui_response.text


def test_diagnostics_datasets_ui_and_evaluate():
    ui_response = client.get("/diagnostics/datasets/ui")
    assert ui_response.status_code == 200
    assert "开源/本地数据集评估" in ui_response.text

    eval_response = client.get("/diagnostics/datasets/evaluate?manifest=docs/datasets/path-guidance-manifest-example.jsonl")
    assert eval_response.status_code == 200
    assert "status_accuracy" in eval_response.json()


def test_datasets_create_and_manifest_browser(monkeypatch, tmp_path):
    monkeypatch.setenv("VQASEE_DATASET_ROOT", str(tmp_path))
    from PIL import Image

    images = tmp_path / "images"
    masks = tmp_path / "masks"
    images.mkdir()
    masks.mkdir()
    Image.new("RGB", (20, 20), "black").save(images / "sample.jpg")
    Image.new("L", (20, 20), 255).save(masks / "sample.png")
    output = tmp_path / "manifest.jsonl"

    response = client.get(
        "/diagnostics/datasets/create",
        params={
            "images": str(images),
            "masks": str(masks),
            "output": str(output),
            "split": "indoor",
            "tags": "office,floor",
            "as_json": "true",
        },
    )

    assert response.status_code == 200
    assert response.json()["rows"] == 1
    assert output.is_file()

    browse = client.get("/diagnostics/datasets/manifest/ui", params={"manifest": str(output)})
    assert browse.status_code == 200
    assert "Manifest 浏览" in browse.text
    assert "sample.jpg" in browse.text or "indoor/sample" in browse.text

    eval_response = client.get("/diagnostics/datasets/evaluate", params={"manifest": str(output)})
    assert eval_response.status_code == 200
    assert eval_response.json()["labeled_frames"] == 1


def test_datasets_create_uses_wizard_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("VQASEE_DATASET_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    from PIL import Image

    images = tmp_path / "images"
    masks = tmp_path / "masks"
    images.mkdir()
    masks.mkdir()
    Image.new("RGB", (20, 20), "black").save(images / "sample.jpg")
    Image.new("L", (20, 20), 255).save(masks / "sample.png")

    response = client.get(
        "/diagnostics/datasets/create",
        params={"images": str(images), "masks": str(masks), "dataset_type": "indoor", "as_json": "true"},
    )

    assert response.status_code == 200
    manifest = tmp_path / response.json()["manifest"]
    assert manifest.is_file()
    text = manifest.read_text(encoding="utf-8")
    assert "indoor" in text
    assert "office" in text


def test_diagnostics_create_open_bdd100k_dataset(monkeypatch, tmp_path):
    from PIL import Image
    import json

    monkeypatch.setenv("VQASEE_DATASET_ROOT", str(tmp_path))
    images = tmp_path / "bdd" / "images"
    images.mkdir(parents=True)
    Image.new("RGB", (100, 100), "black").save(images / "frame.jpg")
    labels = tmp_path / "bdd" / "drivable.json"
    labels.write_text(
        json.dumps([
            {
                "name": "frame.jpg",
                "labels": [
                    {
                        "category": "drivable area",
                        "attributes": {"areaType": "direct"},
                        "poly2d": [[[25, 45], [75, 45], [75, 99], [25, 99]]],
                    }
                ],
            }
        ]),
        encoding="utf-8",
    )
    output = tmp_path / "bdd-manifest.jsonl"

    ui_response = client.get("/diagnostics/datasets/create-open/ui")
    assert ui_response.status_code == 200
    assert "BDD100K" in ui_response.text

    response = client.get(
        "/diagnostics/datasets/create-open",
        params={
            "dataset": "bdd100k_drivable",
            "images": str(images),
            "labels": str(labels),
            "output": str(output),
            "as_json": "true",
        },
    )

    assert response.status_code == 200
    assert response.json()["rows"] == 1
    assert output.is_file()


def test_diagnostics_open_dataset_demo_flow():
    from pathlib import Path

    ui_response = client.get("/diagnostics/datasets/create-open/ui")
    assert ui_response.status_code == 200
    assert "一键下载 CamVid GitHub 数据" in ui_response.text
    assert "一键下载 CamVid GitHub 数据" in ui_response.text
    assert "VQASEE_DATASET_ROOT" in ui_response.text
    assert "高级：接入 BDD100K 大数据集" in ui_response.text
    assert "downloadCamvid()" in ui_response.text
    assert "downloadStatus" in ui_response.text

    demo_response = client.get("/diagnostics/datasets/create-open-demo?as_json=true")
    assert demo_response.status_code == 200
    payload = demo_response.json()
    assert payload["rows"] == 1
    assert payload["manifest"] == "docs/datasets/bdd100k-demo-manifest.jsonl"
    Path(payload["manifest"]).unlink(missing_ok=True)


def test_diagnostics_create_open_camvid_dataset(monkeypatch, tmp_path):
    from PIL import Image
    import numpy as np

    monkeypatch.setenv("VQASEE_DATASET_ROOT", str(tmp_path))
    images = tmp_path / "camvid" / "CamVid_RGB"
    labels = tmp_path / "camvid" / "CamVid_Label"
    images.mkdir(parents=True)
    labels.mkdir(parents=True)
    Image.new("RGB", (40, 40), "black").save(images / "frame.png")
    label = np.zeros((40, 40, 3), dtype=np.uint8)
    label[18:40, 10:30] = np.array([128, 64, 128], dtype=np.uint8)
    Image.fromarray(label).save(labels / "frame.png")
    output = tmp_path / "camvid-manifest.jsonl"

    response = client.get(
        "/diagnostics/datasets/create-open",
        params={
            "dataset": "camvid",
            "images": str(images),
            "labels": str(labels),
            "output": str(output),
            "as_json": "true",
        },
    )

    assert response.status_code == 200
    assert response.json()["rows"] == 1
    assert output.is_file()
