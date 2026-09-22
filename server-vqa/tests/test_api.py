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
    assert "iPhone 本地感知能力总览" in ui_response.text
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
    assert eval_response.json()["frame_count"] == 1

    eval_ui_response = client.get("/diagnostics/sessions/path-export-session/path-eval/ui")
    assert eval_ui_response.status_code == 200
    assert "路径评估" in eval_ui_response.text


def test_extract_zip_flat_flattens_despite_zip_in_output_dir(tmp_path):
    # Reproduce the CamVid failure: a GitHub-style archive nests everything under
    # one top-level folder, and the zip lives in the same output dir. The zip
    # must not block the flatten (previously it did -> nested CamVid_RGB -> 500).
    import zipfile

    from app.diagnostic_api import _extract_zip_flat

    output_dir = tmp_path / "camvid"
    output_dir.mkdir()
    zip_path = output_dir / "camvid.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("CamVid-main/CamVid_RGB/frame.png", "img")
        archive.writestr("CamVid-main/CamVid_Label/frame.png", "lbl")

    _extract_zip_flat(zip_path, output_dir)

    assert (output_dir / "CamVid_RGB" / "frame.png").is_file()
    assert (output_dir / "CamVid_Label" / "frame.png").is_file()
    assert not (output_dir / "CamVid-main").exists()


def test_find_dataset_dir_locates_nested_directory(tmp_path):
    from app.diagnostic_api import _find_dataset_dir

    nested = tmp_path / "CamVid-main" / "CamVid_RGB"
    nested.mkdir(parents=True)
    assert _find_dataset_dir(tmp_path, "CamVid_RGB") == nested
    assert _find_dataset_dir(tmp_path, "DoesNotExist") is None


def test_normalize_camvid_layout_maps_official_ucl_folders(tmp_path):
    from app.diagnostic_api import _normalize_camvid_layout

    raw = tmp_path / "701_StillsRaw_full"
    raw.mkdir()
    (raw / "0001TP_006690.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "0001TP_006690_L.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    _normalize_camvid_layout(tmp_path)

    assert (tmp_path / "CamVid_RGB" / "0001TP_006690.png").is_file()
    assert (tmp_path / "CamVid_Label" / "0001TP_006690_L.png").is_file()
    assert not raw.exists()



def test_dir_has_images_treats_empty_dir_as_absent(tmp_path):
    """Regression: macOS purged /tmp left empty CamVid dirs, which the old check
    counted as 'downloaded' and so silently skipped re-download. An empty (or
    missing) dir must read as NOT-ready; only actual image files count."""
    from app.diagnostic_api import _dir_has_images

    empty = tmp_path / "CamVid_RGB"
    empty.mkdir()
    assert _dir_has_images(None) is False
    assert _dir_has_images(tmp_path / "does_not_exist") is False
    assert _dir_has_images(empty) is False  # exists but purged -> not ready

    (empty / "0001TP_006690.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    assert _dir_has_images(empty) is True

    # A dir with only non-image files still reads as not-ready.
    other = tmp_path / "meta"
    other.mkdir()
    (other / "readme.txt").write_text("x")
    assert _dir_has_images(other) is False


def test_allowed_local_roots_track_the_dataset_root(monkeypatch, tmp_path):
    """Regression: after moving the dataset root, the file-serving allowlist
    still only trusted cwd//tmp, so every image 403'd with file_not_allowed.
    The allowlist must derive from _open_dataset_root()."""
    import app.diagnostic_api as da

    sentinel = (tmp_path / "cache" / "vqasee" / "open-datasets").resolve()
    sentinel.mkdir(parents=True)
    monkeypatch.setattr(da, "_open_dataset_root", lambda: sentinel)
    assert sentinel in da._allowed_local_roots()


def test_local_file_serves_image_from_durable_dataset_root(monkeypatch, tmp_path):
    import app.diagnostic_api as da
    from PIL import Image

    root = (tmp_path / "durable-root").resolve()
    img_dir = root / "camvid" / "CamVid_RGB"
    img_dir.mkdir(parents=True)
    image_path = img_dir / "0001TP_006690.png"
    Image.new("RGB", (16, 16), "#202020").save(image_path)

    # Datasets live under `root`, which is neither cwd nor /tmp.
    monkeypatch.setattr(da, "_open_dataset_root", lambda: root)

    response = client.get(f"/diagnostics/local-file?path={image_path}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/")


def test_open_dataset_root_defaults_to_repo_dataset(monkeypatch):
    monkeypatch.delenv("VQASEE_DATASET_ROOT", raising=False)
    from app.dataset_paths import open_dataset_root, repo_root

    assert open_dataset_root() == (repo_root() / "dataset").resolve()


def test_local_file_remaps_stale_machine_camvid_path(monkeypatch, tmp_path):
    """Committed manifests used to bake /Users/bayes/.cache/... absolute paths.
    Serving must remap onto the current dataset root instead of 404."""
    import app.diagnostic_api as da
    from PIL import Image

    root = (tmp_path / "dataset").resolve()
    img_dir = root / "camvid" / "CamVid_RGB"
    img_dir.mkdir(parents=True)
    image_path = img_dir / "0001TP_006690.png"
    Image.new("RGB", (16, 16), "#202020").save(image_path)

    monkeypatch.setattr(da, "_open_dataset_root", lambda: root)
    monkeypatch.setattr(da, "_repo_root", lambda: tmp_path)

    stale = "/Users/bayes/.cache/vqasee/open-datasets/camvid/CamVid_RGB/0001TP_006690.png"
    response = client.get("/diagnostics/local-file", params={"path": stale})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/")


def test_detect_camvid_dirs_ignores_empty_purged_dirs(monkeypatch, tmp_path):
    """The Step-2 auto-fill must not point at emptied dirs after a /tmp purge."""
    from app.diagnostic_api import _detect_camvid_dirs

    monkeypatch.setenv("VQASEE_DATASET_ROOT", str(tmp_path))
    rgb = tmp_path / "camvid" / "CamVid_RGB"
    lbl = tmp_path / "camvid" / "CamVid_Label"
    rgb.mkdir(parents=True)
    lbl.mkdir(parents=True)

    # Empty dirs: detected as absent (None), so the UI won't claim readiness.
    assert _detect_camvid_dirs() == (None, None)

    # Once real images land, detection returns the populated dirs.
    (rgb / "frame.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (lbl / "frame_L.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    detected_rgb, detected_lbl = _detect_camvid_dirs()
    assert detected_rgb == rgb
    assert detected_lbl == lbl


def test_diagnostics_datasets_ui_and_evaluate():
    ui_response = client.get("/diagnostics/datasets/ui")
    assert ui_response.status_code == 200
    assert "开源/本地数据集评估" in ui_response.text

    eval_response = client.get("/diagnostics/datasets/evaluate?manifest=docs/datasets/path-guidance-manifest-example.jsonl")
    assert eval_response.status_code == 200
    assert "labeled_frames" in eval_response.json()


def test_ios_harness_run_rejects_predictions_file(tmp_path):
    # Pointing the harness at its own PREDICTIONS output (frame_id + prediction, no
    # image) must fail loud + actionable — not run into a cryptic missing_image=N.
    import json as _json

    pred = tmp_path / "foo-ios-harness.jsonl"
    pred.write_text(
        _json.dumps({"frame_id": "road/x", "prediction": {"prediction_source": "ios_coreml_offline_harness"}, "guidance_path": {}}) + "\n",
        encoding="utf-8",
    )
    resp = client.post(f"/diagnostics/datasets/ios-harness/run?manifest={pred}")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "error"
    assert payload["capability"] == "wrong_manifest"
    # The message must point the user at a real dataset manifest.
    assert "camvid-manifest.jsonl" in payload["reason"]


def test_harness_binary_stale_when_source_newer(tmp_path):
    import os

    from app.diagnostic_api import _harness_binary_stale

    harness_dir = tmp_path / "perception-harness"
    (harness_dir / "Sources" / "PerceptionHarness").mkdir(parents=True)
    pkg = harness_dir / "Package.swift"
    src = harness_dir / "Sources" / "PerceptionHarness" / "main.swift"
    pkg.write_text("// pkg\n", encoding="utf-8")
    src.write_text("let x = 1\n", encoding="utf-8")
    missing = tmp_path / "missing-bin"
    assert _harness_binary_stale(missing, harness_dir) is True

    bin_path = tmp_path / "PerceptionHarness"
    bin_path.write_bytes(b"bin")
    os.utime(bin_path, (1_000_000, 1_000_000))
    os.utime(src, (2_000_000, 2_000_000))
    os.utime(pkg, (1_000_000, 1_000_000))
    assert _harness_binary_stale(bin_path, harness_dir) is True
    os.utime(src, (500_000, 500_000))
    assert _harness_binary_stale(bin_path, harness_dir) is False


def test_harness_missing_images_reason_wrong_relative_join():
    from app.diagnostic_api import _harness_missing_images_reason

    stderr = (
        "image_not_found: /Users/x/onepiece/docs/datasets/dataset/camvid/CamVid_RGB/a.png\n"
        "harness done: predicted=0 missing_image=701\n"
    )
    reason = _harness_missing_images_reason(stderr)
    assert "docs/datasets" in reason
    assert "dataset/camvid" in reason
    assert "/tmp" not in reason


def test_harness_cache_empty_predictions_are_not_fresh(tmp_path):
    from app import diagnostic_api

    manifest = tmp_path / "m.jsonl"
    manifest.write_text('{"frame_id":"f1","image_path":"x.png"}\n', encoding="utf-8")
    out = diagnostic_api._harness_out_path(manifest)
    out.write_text("", encoding="utf-8")
    try:
        info = diagnostic_api._harness_cache_info(manifest)
        assert info["exists"] is True
        assert info["count"] == 0
        assert info["fresh"] is False
        assert any("未产出" in r for r in info["stale_reasons"])
    finally:
        out.unlink(missing_ok=True)


def test_read_manifest_eval_role_maps_drive_and_walk(tmp_path):
    from app.diagnostic_api import _overlay_harness_config_for_manifest, _read_manifest_eval_role

    drive = tmp_path / "camvid-manifest-drive.jsonl"
    drive.write_text('{"frame_id":"f","role":"drive","image_path":"x.png"}\n', encoding="utf-8")
    walk = tmp_path / "camvid-manifest-walk.jsonl"
    walk.write_text('{"frame_id":"f","role":"walk","image_path":"x.png"}\n', encoding="utf-8")
    plain = tmp_path / "other.jsonl"
    plain.write_text('{"frame_id":"f","image_path":"x.png"}\n', encoding="utf-8")
    assert _read_manifest_eval_role(drive) == "vehicle"
    assert _read_manifest_eval_role(walk) == "pedestrian"
    assert _read_manifest_eval_role(plain) is None

    from app.perception_config import default_config

    overlaid = _overlay_harness_config_for_manifest(default_config().to_dict(), "vehicle")
    assert overlaid["role"] == "vehicle"
    assert overlaid["use_multiclass_segmentation"] is False
    assert overlaid["road_backend"] == "twinlite"


def test_ios_harness_ui_drive_manifest_states_vehicle_role():
    resp = client.get(
        "/diagnostics/datasets/ios-harness/ui",
        params={"manifest": "docs/datasets/camvid-manifest-drive.jsonl"},
    )
    assert resp.status_code == 200
    assert "机动车评估" in resp.text
    assert "人行道不算可行驶" in resp.text
    assert "全量 701" in resp.text
    assert "camvid-manifest-drive-test.jsonl" in resp.text


def test_manifest_runnable_reason_accepts_dataset_flags_predictions(tmp_path):
    import json as _json

    from app.diagnostic_api import _manifest_runnable_reason

    ds = tmp_path / "mini-manifest.jsonl"
    ds.write_text(
        _json.dumps({"frame_id": "road/x", "image_path": str(tmp_path / "x.png"), "image": "x.png"}) + "\n",
        encoding="utf-8",
    )
    assert _manifest_runnable_reason(ds) is None

    pred = tmp_path / "p-ios-harness.jsonl"
    pred.write_text(_json.dumps({"frame_id": "road/x", "prediction": {"prediction_source": "ios_coreml_offline_harness"}}) + "\n", encoding="utf-8")
    reason = _manifest_runnable_reason(pred)
    assert reason is not None and "预测结果" in reason


def test_datasets_ui_marks_predictions_file_non_evaluable():
    # The dataset list enumerates every *.jsonl; the harness prediction output must be
    # shown but NOT offered the evaluate/harness links that would fail on it, and it
    # is grouped under the collapsed developer section (not mixed with truth datasets).
    resp = client.get("/diagnostics/datasets/ui")
    assert resp.status_code == 200
    text = resp.text
    assert "camvid-ios-harness.jsonl" in text
    # Prediction files live under the "预测结果 / 派生文件" developer section.
    assert "预测结果 / 派生文件" in text
    # Safety-critical: the prediction output is never offered an evaluate/harness link
    # (which would report a cryptic missing_image=N for every frame).
    assert "datasets/evaluate/ui?manifest=docs/datasets/camvid-ios-harness.jsonl" not in text
    assert "datasets/ios-harness/ui?manifest=docs/datasets/camvid-ios-harness.jsonl" not in text


def test_datasets_ui_recommends_camvid_test_split_over_full_701():
    resp = client.get("/diagnostics/datasets/ui")
    assert resp.status_code == 200
    text = resp.text
    assert "日常迭代（推荐）" in text
    assert "camvid-manifest-drive-test.jsonl" in text
    assert "camvid-manifest-walk-test.jsonl" in text
    assert "推荐迭代" in text
    assert "全量回归" in text
    drive_test_link = "datasets/ios-harness/ui?manifest=docs/datasets/camvid-manifest-drive-test.jsonl"
    assert drive_test_link in text


def test_ios_harness_ui_test_manifest_quotes_small_runtime_not_full_701():
    resp = client.get(
        "/diagnostics/datasets/ios-harness/ui",
        params={"manifest": "docs/datasets/camvid-manifest-drive-test.jsonl"},
    )
    assert resp.status_code == 200
    assert "机动车评估" in resp.text
    assert "日常迭代 test 集" in resp.text
    assert "TwinLiteNet" in resp.text
    assert "多类分割" not in resp.text
    assert "35–45 分钟" not in resp.text
    assert "701 帧在 Intel" not in resp.text


def test_dataset_evaluate_ui_surfaces_missing_predictions():
    response = client.get("/diagnostics/datasets/evaluate/ui?manifest=docs/datasets/path-guidance-manifest-example.jsonl")
    assert response.status_code == 200
    # The evaluate page must expose the prediction-coverage card and the run-predict step.
    assert ("缺预测帧" in response.text) or ("预测覆盖" in response.text)
    assert "运行预测" in response.text


def test_dataset_predict_reports_unsupported_without_model(monkeypatch, tmp_path):
    # No onnxruntime/model in test env: predict must say unsupported, not fake it.
    monkeypatch.setenv("VQASEE_TRAVERSABILITY_ONNX", str(tmp_path / "missing.onnx"))
    response = client.post(
        "/diagnostics/datasets/predict",
        params={"manifest": "docs/datasets/path-guidance-manifest-example.jsonl", "write_back": "false"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["capability"] == "unsupported"
    assert payload["predicted"] == 0
    assert payload["reason"]


def test_session_close_loop_saves_baseline(monkeypatch, tmp_path):
    monkeypatch.setenv("DIAGNOSTIC_CAPTURE_DIR", str(tmp_path / "captures"))
    monkeypatch.setenv("VQASEE_EVAL_BASELINE_DIR", str(tmp_path / "baselines"))
    from app.diagnostic_capture import save_diagnostic_frame

    save_diagnostic_frame(
        session_id="close-loop-session",
        image_base64="/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
        metadata={"event": "sent_to_backend", "mode": "walking", "frame": "frames/frame-0001.jpg"},
    )
    client.post(
        "/diagnostics/sessions/close-loop-session/labels",
        json={"frame": "frames/frame-0001.jpg", "label": "no_obvious_risk", "true_risks": "无明显风险"},
    )

    response = client.post("/diagnostics/sessions/close-loop-session/close-loop")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["baseline"] == "session-close-loop-session"
    assert (tmp_path / "baselines" / "session-close-loop-session.json").is_file()

    baselines = client.get("/diagnostics/baselines")
    assert baselines.status_code == 200
    names = {item["name"] for item in baselines.json()["baselines"]}
    assert "session-close-loop-session" in names


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


def test_manifest_browser_paginates_and_lazy_loads_thumbnails(monkeypatch, tmp_path):
    import json

    monkeypatch.setenv("VQASEE_DATASET_ROOT", str(tmp_path))
    from PIL import Image

    image_path = tmp_path / "frame.png"
    Image.new("RGB", (1280, 720), "black").save(image_path)
    manifest = tmp_path / "manifest.jsonl"
    lines = []
    for i in range(30):
        lines.append(
            json.dumps(
                {
                    "frame_id": f"road/frame-{i:03d}",
                    "image_path": str(image_path),
                    "ground_truth": {},
                },
                ensure_ascii=False,
            )
        )
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")

    page1 = client.get("/diagnostics/datasets/manifest/ui", params={"manifest": str(manifest)})
    assert page1.status_code == 200
    # Lazy-loaded thumbnails, not eager full-size images.
    assert "loading='lazy'" in page1.text
    assert "&w=480" in page1.text
    # Only one page's worth of frames rendered; page 2 link present.
    assert "frame-000" in page1.text
    assert "frame-024" not in page1.text
    assert "共 30 帧 · 第 1/2 页" in page1.text
    assert "page=2" in page1.text

    page2 = client.get("/diagnostics/datasets/manifest/ui", params={"manifest": str(manifest), "page": 2})
    assert page2.status_code == 200
    assert "frame-024" in page2.text
    assert "frame-000" not in page2.text

    # Thumbnail endpoint returns a downscaled JPEG far smaller than the source PNG.
    thumb = client.get("/diagnostics/local-file", params={"path": str(image_path), "w": 480})
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/jpeg"
    assert len(thumb.content) < image_path.stat().st_size


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
    assert "dataset/camvid" in ui_response.text
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


def test_diagnostics_create_open_camvid_autodetects_downloaded_dirs(monkeypatch, tmp_path):
    from PIL import Image
    import numpy as np

    monkeypatch.setenv("VQASEE_DATASET_ROOT", str(tmp_path))
    # Simulate the real download layout where dirs sit nested under CamVid-main.
    images = tmp_path / "camvid" / "CamVid-main" / "CamVid_RGB"
    labels = tmp_path / "camvid" / "CamVid-main" / "CamVid_Label"
    images.mkdir(parents=True)
    labels.mkdir(parents=True)
    Image.new("RGB", (40, 40), "black").save(images / "frame.png")
    label = np.zeros((40, 40, 3), dtype=np.uint8)
    label[18:40, 10:30] = np.array([128, 64, 128], dtype=np.uint8)
    Image.fromarray(label).save(labels / "frame.png")
    output = tmp_path / "camvid-manifest.jsonl"

    # UI must auto-fill the detected nested paths, not the old flat placeholder.
    ui_response = client.get("/diagnostics/datasets/create-open/ui")
    assert ui_response.status_code == 200
    assert str(images) in ui_response.text
    assert "已检测到本地 CamVid" in ui_response.text

    # Blank images/labels → auto-detect and still generate the manifest.
    response = client.get(
        "/diagnostics/datasets/create-open",
        params={"dataset": "camvid", "output": str(output), "as_json": "true"},
    )
    assert response.status_code == 200
    assert response.json()["rows"] == 1
    assert output.is_file()


def test_diagnostics_create_open_camvid_missing_returns_clear_error(monkeypatch, tmp_path):
    monkeypatch.setenv("VQASEE_DATASET_ROOT", str(tmp_path))
    output = tmp_path / "camvid-manifest.jsonl"
    response = client.get(
        "/diagnostics/datasets/create-open",
        params={"dataset": "camvid", "output": str(output), "as_json": "true"},
    )
    assert response.status_code == 404
    assert "camvid_not_found" in response.json()["detail"]
    assert not output.exists()


# --- Capability overview (single north-star scorecard) -----------------------
import json as _json


def _write_capability_baselines(
    baseline_dir,
    *,
    iou=0.77,
    recall=0.79,
    precision=0.97,
    region_false_go=0,
    region_miss=83,
    scored=701,
    include_role=False,
    road_as_primary=0.807,
    primary_recall=0.639,
    lane_coverage=0.718,
    road_as_primary_frames=700,
    obstacle_overlap_frames=5,
    include_lane=False,
    lane_recall_tol=0.982,
    lane_precision_tol=0.851,
    lane_iou=0.331,
    lane_miss_frames=0,
    lane_frames=60,
    lane_tol=3,
    lane_on_device=False,
    include_obstacle=False,
    obstacle_coverage_recall=0.62,
    obstacle_box_precision=0.44,
    obstacle_miss_frames=180,
    obstacle_frames=540,
    include_drive=False,
    drive_road_recall=0.811,
    drive_sidewalk_as_road_rate=0.006,
    drive_sidewalk_as_road_frames=0,
    drive_obstacle_overlap_frames=534,
):
    baseline_dir.mkdir(parents=True, exist_ok=True)
    if include_drive:
        (baseline_dir / "camvid-ios-drive.json").write_text(
            _json.dumps(
                {
                    "name": "camvid-ios-drive",
                    "source": "ios_coreml_offline_harness_role_drive",
                    "metrics": {
                        "mean_primary_recall": drive_road_recall,
                        "mean_road_as_primary_rate": drive_sidewalk_as_road_rate,
                        "road_as_primary_frames": drive_sidewalk_as_road_frames,
                        "obstacle_overlap_frames": drive_obstacle_overlap_frames,
                        "mean_lane_coverage": 0.718,
                        "mean_obstacle_overlap_rate": 0.163,
                        "scored": scored,
                    },
                }
            ),
            encoding="utf-8",
        )
    if include_obstacle:
        (baseline_dir / "camvid-ios-obstacle.json").write_text(
            _json.dumps(
                {
                    "name": "camvid-ios-obstacle",
                    "source": "ios_coreml_offline_harness_obstacle",
                    "metrics": {
                        "mean_coverage_recall": obstacle_coverage_recall,
                        "mean_box_precision": obstacle_box_precision,
                        "obstacle_miss_frames": obstacle_miss_frames,
                        "obstacle_frames": obstacle_frames,
                        "scored": scored,
                    },
                }
            ),
            encoding="utf-8",
        )
    if include_lane:
        (baseline_dir / "camvid-ios-lane.json").write_text(
            _json.dumps(
                {
                    "name": "camvid-ios-lane",
                    "source": "lane_seg_camvid_heldout",
                    "on_device": lane_on_device,
                    "metrics": {
                        "mean_recall_tol": lane_recall_tol,
                        "mean_precision_tol": lane_precision_tol,
                        "mean_iou": lane_iou,
                        "lane_miss_frames": lane_miss_frames,
                        "lane_frames": lane_frames,
                        "scored": lane_frames,
                        "tol": lane_tol,
                    },
                }
            ),
            encoding="utf-8",
        )
    if include_role:
        (baseline_dir / "camvid-ios-role.json").write_text(
            _json.dumps(
                {
                    "name": "camvid-ios-role",
                    "source": "ios_coreml_offline_harness_role",
                    "metrics": {
                        "mean_road_as_primary_rate": road_as_primary,
                        "mean_primary_recall": primary_recall,
                        "mean_lane_coverage": lane_coverage,
                        "mean_obstacle_overlap_rate": 0.004,
                        "road_as_primary_frames": road_as_primary_frames,
                        "obstacle_overlap_frames": obstacle_overlap_frames,
                        "scored": scored,
                    },
                }
            ),
            encoding="utf-8",
        )
    (baseline_dir / "camvid-ios-region.json").write_text(
        _json.dumps(
            {
                "name": "camvid-ios-region",
                "source": "ios_coreml_offline_harness_region",
                "metrics": {
                    "mean_iou": iou,
                    "mean_precision": precision,
                    "mean_recall": recall,
                    "region_false_go_frames": region_false_go,
                    "region_miss_frames": region_miss,
                    "scored": scored,
                },
            }
        ),
        encoding="utf-8",
    )
def test_diagnostics_overview_shows_capability_verdict(monkeypatch, tmp_path):
    baseline_dir = tmp_path / "baselines"
    _write_capability_baselines(baseline_dir)
    monkeypatch.setenv("VQASEE_EVAL_BASELINE_DIR", str(baseline_dir))

    response = client.get("/diagnostics/ui")
    assert response.status_code == 200
    text = response.text
    # Leads with the single capability verdict, not scattered eval cards.
    assert "iPhone 本地感知能力总览" in text
    assert "看得准" in text and "安全侧" in text
    assert "画得对" not in text
    assert "IoU 0.77" in text
    # Verdict must reflect the safe-but-conservative state and name the fix owner.
    assert "偏保守" in text
    assert "0 冒进帧" in text
    assert "全麦" in text
    # Detail pages are folded into drill-down, not spread on the landing.
    assert "下钻" in text


def test_diagnostics_overview_without_baseline_prompts_run(monkeypatch, tmp_path):
    # Empty baseline dir: must say "no baseline yet" and point to running eval,
    # never fabricate a score (no silent pass).
    monkeypatch.setenv("VQASEE_EVAL_BASELINE_DIR", str(tmp_path / "empty"))
    response = client.get("/diagnostics/ui")
    assert response.status_code == 200
    assert "尚无能力基线" in response.text
    assert "/diagnostics/datasets/ui" in response.text


def test_capability_snapshot_flags_regression_vs_baseline(monkeypatch, tmp_path):
    from app.diagnostic_api import _capability_snapshot

    baseline_dir = tmp_path / "baselines"
    _write_capability_baselines(baseline_dir)
    # A newer eval report whose region IoU dropped below baseline → trend "down".
    (baseline_dir / "camvid-ios-report.json").write_text(
        _json.dumps(
            {
                "region": {"mean_iou": 0.70},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("VQASEE_EVAL_BASELINE_DIR", str(baseline_dir))

    snap = _capability_snapshot()
    assert snap["available"] is True
    assert snap["trend"]["status"] == "down"
    assert "退步" in snap["trend"]["text"]


def test_capability_snapshot_flags_unsafe_when_false_go(monkeypatch, tmp_path):
    from app.diagnostic_api import _capability_snapshot

    baseline_dir = tmp_path / "baselines"
    _write_capability_baselines(baseline_dir, region_false_go=5)
    monkeypatch.setenv("VQASEE_EVAL_BASELINE_DIR", str(baseline_dir))

    snap = _capability_snapshot()
    assert snap["safe"] is False
    assert "冒进" in snap["summary"]


def test_capability_snapshot_role_section_absent_without_role_baseline(monkeypatch, tmp_path):
    """Role baseline is optional: when absent the snapshot stays available and just
    omits the role section (backwards compatible, no crash)."""
    from app.diagnostic_api import _capability_snapshot

    baseline_dir = tmp_path / "baselines"
    _write_capability_baselines(baseline_dir)  # no role
    monkeypatch.setenv("VQASEE_EVAL_BASELINE_DIR", str(baseline_dir))
    snap = _capability_snapshot()
    assert snap["available"] is True
    assert snap.get("role") is None


def test_capability_snapshot_lane_section_absent_without_lane_baseline(monkeypatch, tmp_path):
    """Lane baseline is optional: absent => snapshot stays available, lane is None
    (no fabricated lane capability)."""
    from app.diagnostic_api import _capability_snapshot

    baseline_dir = tmp_path / "baselines"
    _write_capability_baselines(baseline_dir)  # no lane
    monkeypatch.setenv("VQASEE_EVAL_BASELINE_DIR", str(baseline_dir))
    snap = _capability_snapshot()
    assert snap["available"] is True
    assert snap.get("lane") is None


def test_overview_surfaces_lane_capability_card(monkeypatch, tmp_path):
    """When a lane baseline exists, the overview must show lane as a first-class
    capability card: tolerance-band recall/precision, the thin-class IoU caveat,
    and the honest on-device status (offline-only until bundled)."""
    baseline_dir = tmp_path / "baselines"
    _write_capability_baselines(baseline_dir, include_lane=True)
    monkeypatch.setenv("VQASEE_EVAL_BASELINE_DIR", str(baseline_dir))

    response = client.get("/diagnostics/ui")
    assert response.status_code == 200
    text = response.text
    assert "车道线 · 找得到吗" in text
    assert "召回 98%" in text  # 0.982 tolerance-band recall
    assert "容差 3px" in text
    assert "尚未捆绑" in text  # honest: offline only until on-device


def test_capability_snapshot_obstacle_section_absent_without_baseline(monkeypatch, tmp_path):
    from app.diagnostic_api import _capability_snapshot

    baseline_dir = tmp_path / "baselines"
    _write_capability_baselines(baseline_dir)  # no obstacle
    monkeypatch.setenv("VQASEE_EVAL_BASELINE_DIR", str(baseline_dir))
    snap = _capability_snapshot()
    assert snap["available"] is True
    assert snap.get("obstacle") is None


def test_overview_surfaces_obstacle_capability_card(monkeypatch, tmp_path):
    """When an obstacle baseline exists, the overview must show obstacles as a
    first-class card: coverage recall, the 'proxy not mAP' caveat, and misses."""
    baseline_dir = tmp_path / "baselines"
    _write_capability_baselines(baseline_dir, include_obstacle=True)
    monkeypatch.setenv("VQASEE_EVAL_BASELINE_DIR", str(baseline_dir))

    response = client.get("/diagnostics/ui")
    assert response.status_code == 200
    text = response.text
    assert "障碍物 · 看得见吗" in text
    assert "覆盖召回 62%" in text
    assert "非检测 mAP" in text  # honest: coverage proxy, not mAP
    assert "180/540" in text


def test_overview_surfaces_drive_role_contrast(monkeypatch, tmp_path):
    """The driving-role card must make the '区分人车' contrast explicit: the SAME
    device grid is near-safe for a driver (0 sidewalk-as-road frames, high road
    recall) — which is exactly why role-conditioning is required."""
    baseline_dir = tmp_path / "baselines"
    _write_capability_baselines(baseline_dir, include_role=True, include_drive=True)
    monkeypatch.setenv("VQASEE_EVAL_BASELINE_DIR", str(baseline_dir))

    response = client.get("/diagnostics/ui")
    assert response.status_code == 200
    text = response.text
    # Both role cards present -> the contrast is visible on one page.
    assert "行人角色" in text and "驾驶角色" in text
    assert "人行道误当车道" in text
    assert "道路召回 81%" in text
    assert "必须区分人车" in text


def test_capability_snapshot_drive_section_absent_without_baseline(monkeypatch, tmp_path):
    from app.diagnostic_api import _capability_snapshot

    baseline_dir = tmp_path / "baselines"
    _write_capability_baselines(baseline_dir, include_role=True)  # walk only, no drive
    monkeypatch.setenv("VQASEE_EVAL_BASELINE_DIR", str(baseline_dir))
    snap = _capability_snapshot()
    assert snap["available"] is True
    assert snap.get("drive") is None


def test_overview_surfaces_role_road_as_sidewalk_redline(monkeypatch, tmp_path):
    """When a role baseline exists, the overview must loudly show the walk-role
    safety red-line (road misread as sidewalk) and point at the T2 fix, so the
    'lane / road-boundary not recognized' gap is visible on the landing page."""
    baseline_dir = tmp_path / "baselines"
    _write_capability_baselines(baseline_dir, include_role=True)
    monkeypatch.setenv("VQASEE_EVAL_BASELINE_DIR", str(baseline_dir))

    response = client.get("/diagnostics/ui")
    assert response.status_code == 200
    text = response.text
    assert "行人角色" in text
    assert "马路误当人行道" in text
    assert "81%" in text  # 0.807 rounded
    assert "700/701" in text
    assert "车道线" in text
    assert "T2" in text
