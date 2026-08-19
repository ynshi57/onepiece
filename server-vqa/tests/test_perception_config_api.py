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


def _write_harness_predictions(path):
    rows = [
        {
            "frame_id": "f1",
            "prediction": {
                "near_path_status": "candidateOpen",  # GT is blocked -> risk miss
                "left_front_status": "candidateOpen",
                "right_front_status": "candidateOpen",
                "focus_direction": "center",
                "prediction_source": "ios_coreml_offline_harness",
            },
            "objects": [
                {"kind": "bus", "label": "公交车", "confidence": 0.98, "direction": "center",
                 "box": {"x": 0.5, "y": 0.2, "w": 0.2, "h": 0.4}},
            ],
            "roi": {
                "near": {"x": 0.3, "y": 0.0, "w": 0.4, "h": 0.35},
                "left": {"x": 0.05, "y": 0.2, "w": 0.3, "h": 0.4},
                "right": {"x": 0.65, "y": 0.2, "w": 0.3, "h": 0.4},
            },
        },
        # f2 intentionally omitted to exercise the "no prediction for this frame" path.
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_ios_harness_frames_ui_draws_overlay_and_gt_comparison(client, tmp_path):
    manifest = tmp_path / "m.jsonl"
    _write_manifest(manifest)
    preds = tmp_path / "preds.jsonl"
    _write_harness_predictions(preds)

    resp = client.get(
        "/diagnostics/datasets/ios-harness/frames/ui",
        params={"manifest": str(manifest), "predictions": str(preds)},
    )
    assert resp.status_code == 200
    text = resp.text
    # Image + SVG overlay with both object box and ROI rects present.
    assert "frame-overlay" in text
    assert "<svg" in text
    assert "local-file" in text
    # Detected object label surfaces so the user sees what was recognized.
    assert "公交车" in text
    # GT vs prediction comparison, including the honest "漏报" flag for f1.
    assert "真实答案" in text
    assert "漏报" in text
    # Frame without a prediction is shown honestly, not silently dropped.
    assert "该帧没有对应预测" in text


def test_ios_harness_frames_ui_draws_guidance_lines(client, tmp_path):
    manifest = tmp_path / "m.jsonl"
    preds = tmp_path / "preds.jsonl"
    line = {
        "status": "ok", "coverage": 1.0, "source": "t",
        "lines": [{"kind": "primary", "confidence": 1.0, "risk_segments": [], "points": [
            {"x": 0.5, "y": 0.0, "half_width": 0.1},
            {"x": 0.52, "y": 0.3, "half_width": 0.1},
            {"x": 0.55, "y": 0.6, "half_width": 0.1},
        ]}],
    }
    # Manifest frame carries a GT guidance line; image_path under an allowed root.
    manifest.write_text(json.dumps({
        "frame_id": "f1",
        "image_path": "/tmp/vqasee-nonexistent.png",
        "ground_truth": {"near_path_status": "candidateOpen", "left_front_status": "candidateOpen",
                          "right_front_status": "candidateOpen", "focus_direction": "center"},
        "ground_truth_path": line,
    }) + "\n", encoding="utf-8")
    preds.write_text(json.dumps({
        "frame_id": "f1",
        "prediction": {"near_path_status": "candidateOpen", "left_front_status": "candidateOpen",
                       "right_front_status": "candidateOpen", "focus_direction": "center",
                       "prediction_source": "ios_coreml_offline_harness"},
        "roi": {"near": {"x": 0.3, "y": 0.0, "w": 0.4, "h": 0.35}},
        "guidance_path": line,
    }) + "\n", encoding="utf-8")

    resp = client.get(
        "/diagnostics/datasets/ios-harness/frames/ui",
        params={"manifest": str(manifest), "predictions": str(preds)},
    )
    assert resp.status_code == 200
    text = resp.text
    # Both the predicted line (polyline) and its corridor band (polygon) render,
    # plus the legend explaining the two lines as the primary signal.
    assert "<polyline" in text
    assert "<polygon" in text
    assert "紫实线=iPhone 预测路径" in text
    assert "绿虚线=真值路径" in text
    # On-image "预测"/"真值" labels make the line self-explanatory.
    assert ">预测<" in text
    assert ">真值<" in text


def test_ios_harness_frames_ui_filters_by_result_category(client, tmp_path):
    manifest = tmp_path / "m.jsonl"
    _write_manifest(manifest)  # f1 GT near=blocked, f2 GT all candidateOpen
    preds = tmp_path / "preds.jsonl"
    _write_harness_predictions(preds)  # f1 pred near=candidateOpen (risk miss), f2 absent

    base = {"manifest": str(manifest), "predictions": str(preds)}

    # Filter bar with counts is always present.
    all_resp = client.get("/diagnostics/datasets/ios-harness/frames/ui", params={**base, "filter": "all"})
    assert all_resp.status_code == 200
    assert "只看哪种结果" in all_resp.text
    assert "本类 2 帧" in all_resp.text

    # risk_miss: only f1 qualifies (blocked GT predicted candidateOpen).
    # Match the frame-card heading precisely — a loose "f2" substring collides with
    # color hex like #bf5af2 in the legend.
    rm = client.get("/diagnostics/datasets/ios-harness/frames/ui", params={**base, "filter": "risk_miss"})
    assert "本类 1 帧" in rm.text
    assert "<h2>f1</h2>" in rm.text and "<h2>f2</h2>" not in rm.text

    # no_prediction: only f2 (no harness row).
    npf = client.get("/diagnostics/datasets/ios-harness/frames/ui", params={**base, "filter": "no_prediction"})
    assert "本类 1 帧" in npf.text
    assert "<h2>f2</h2>" in npf.text and "<h2>f1</h2>" not in npf.text

    # Unknown filter value falls back to all (never 500 / never silently empty).
    bogus = client.get("/diagnostics/datasets/ios-harness/frames/ui", params={**base, "filter": "bogus"})
    assert bogus.status_code == 200
    assert "本类 2 帧" in bogus.text


def test_ios_harness_frames_ui_404s_on_missing_files(client, tmp_path):
    manifest = tmp_path / "m.jsonl"
    _write_manifest(manifest)
    preds = tmp_path / "preds.jsonl"
    _write_harness_predictions(preds)

    assert client.get(
        "/diagnostics/datasets/ios-harness/frames/ui",
        params={"manifest": "/tmp/nope-manifest.jsonl", "predictions": str(preds)},
    ).status_code == 404
    assert client.get(
        "/diagnostics/datasets/ios-harness/frames/ui",
        params={"manifest": str(manifest), "predictions": "/tmp/nope-preds.jsonl"},
    ).status_code == 404


def test_ios_harness_run_404_when_manifest_missing(client):
    resp = client.post(
        "/diagnostics/datasets/ios-harness/run",
        params={"manifest": "/tmp/definitely-missing-manifest.jsonl"},
    )
    assert resp.status_code == 404


def _write_cached_predictions(manifest_path, config_version=1):
    from app import diagnostic_api

    out = diagnostic_api._harness_out_path(manifest_path)
    rows = [
        {"frame_id": "f1", "prediction": {"near_path_status": "blocked", "config_version": config_version}},
        {"frame_id": "f2", "prediction": {"near_path_status": "candidateOpen", "config_version": config_version}},
    ]
    out.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return out


def test_ios_harness_run_reuses_fresh_cache_without_running(client, tmp_path, monkeypatch):
    from app import diagnostic_api

    manifest = tmp_path / "m.jsonl"
    _write_manifest(manifest)
    out = _write_cached_predictions(manifest, config_version=1)  # active default is v1

    def _fail_run(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("subprocess must not run when a fresh cache exists")

    monkeypatch.setattr(diagnostic_api.subprocess, "run", _fail_run)
    try:
        resp = client.post("/diagnostics/datasets/ios-harness/run", params={"manifest": str(manifest)})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "cached"
        assert body["predicted"] == 2
    finally:
        out.unlink(missing_ok=True)


def test_ios_harness_cache_marked_stale_after_config_bump(client, tmp_path):
    from app import diagnostic_api

    manifest = tmp_path / "m.jsonl"
    _write_manifest(manifest)
    out = _write_cached_predictions(manifest, config_version=1)
    try:
        # Fresh initially (cached config v1 == active v1).
        assert diagnostic_api._harness_cache_info(manifest)["fresh"] is True
        # Bump the active config -> cached v1 predictions become stale.
        bump = client.post("/diagnostics/perception-config/bump", json={"thresholds": {"near_blocked_area": 0.7}})
        assert bump.status_code == 200
        info = diagnostic_api._harness_cache_info(manifest)
        assert info["fresh"] is False
        assert any("配置" in r for r in info["stale_reasons"])
        # Wizard surfaces the "建议重跑" advice, not a silent stale reuse.
        page = client.get("/diagnostics/datasets/ios-harness/ui", params={"manifest": str(manifest)}).text
        assert "建议重跑" in page
    finally:
        out.unlink(missing_ok=True)


def test_ios_harness_cache_uses_content_fingerprint_meta(client, tmp_path):
    from app import diagnostic_api as da
    from app.perception_config import load_active_config

    manifest = tmp_path / "m.jsonl"
    _write_manifest(manifest)
    out = _write_cached_predictions(manifest, config_version=1)
    meta_path = da._harness_meta_path(manifest)
    active = load_active_config()
    bin_path = da._harness_bin()
    meta = {
        "manifest_hash": da._sha256_file(manifest),
        "config_version": active.version,
        "config_hash": active.content_hash(),
        "harness_hash": da._sha256_file(bin_path) if bin_path.is_file() else None,
        "generated_at": "2026-08-18 18:00:00",
        "count": 2,
        "predictions": str(out),
    }
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    try:
        info = da._harness_cache_info(manifest)
        assert info["fingerprint"] == "content"
        assert info["fresh"] is True

        # Mutate manifest bytes -> content hash mismatch -> stale (mtime would miss
        # this if bytes changed without advancing mtime; hashing catches it).
        with open(manifest, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"frame_id": "f3", "ground_truth": {}}) + "\n")
        stale = da._harness_cache_info(manifest)
        assert stale["fresh"] is False
        assert any("内容已变化" in r for r in stale["stale_reasons"])

        # Restore manifest; bump config behavior -> config hash mismatch -> stale.
        _write_manifest(manifest)
        meta["manifest_hash"] = da._sha256_file(manifest)
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        client.post("/diagnostics/perception-config/bump", json={"thresholds": {"near_blocked_area": 0.71}})
        cfg_stale = da._harness_cache_info(manifest)
        assert cfg_stale["fresh"] is False
        assert any("配置" in r for r in cfg_stale["stale_reasons"])
    finally:
        out.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)


def test_ios_harness_run_reports_unsupported_off_macos(client, tmp_path, monkeypatch):
    from app import diagnostic_api

    manifest = tmp_path / "m.jsonl"
    _write_manifest(manifest)
    # Simulate a non-macOS server: must honestly say unsupported, never spawn
    # a subprocess and never pretend success.
    monkeypatch.setattr(diagnostic_api.sys, "platform", "linux")

    def _fail_run(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("subprocess must not run when platform is unsupported")

    monkeypatch.setattr(diagnostic_api.subprocess, "run", _fail_run)

    resp = client.post(
        "/diagnostics/datasets/ios-harness/run",
        params={"manifest": str(manifest)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "unsupported"
    assert body["capability"] == "not_macos"
    assert body["reason"]


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
