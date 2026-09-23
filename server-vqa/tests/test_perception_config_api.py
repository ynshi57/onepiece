"""API tests for the perception-config OTA endpoints and the iPhone-harness wizard."""

from __future__ import annotations

import json
import os

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
    assert payload["thresholds"]["seg_traversable_pixel"] == 0.55
    assert "roi" not in payload


def test_bump_persists_and_runtime_reflects_it(client):
    resp = client.post(
        "/diagnostics/perception-config/bump",
        json={"thresholds": {"seg_traversable_pixel": 0.7}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["config"]["version"] == 2
    assert body["config"]["thresholds"]["seg_traversable_pixel"] == 0.7

    runtime = client.get("/runtime/perception-config").json()
    assert runtime["version"] == 2
    assert runtime["thresholds"]["seg_traversable_pixel"] == 0.7


def test_invalid_bump_is_rejected_and_not_written(client):
    resp = client.post(
        "/diagnostics/perception-config/bump",
        json={"thresholds": {"seg_traversable_pixel": 9.0}},
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
    assert "seg_traversable_pixel" in resp.text
    assert "near_blocked_area" not in resp.text
    assert "近处正前" not in resp.text
    assert "road_backend" in resp.text


# ---------------------------------------------------------------------------
# iPhone offline-harness wizard
# ---------------------------------------------------------------------------

def _write_manifest(path):
    rows = [
        {
            "frame_id": "f1",
            "image_path": "/does/not/matter.png",
            "ground_truth": {},
            "traversable_grid": {"cols": 2, "rows": 2, "cells": [1, 0, 0, 0]},
        },
        {
            "frame_id": "f2",
            "image_path": "/does/not/matter2.png",
            "ground_truth": {},
            "traversable_grid": {"cols": 2, "rows": 2, "cells": [1, 0, 0, 0]},
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
        {"frame_id": "f1", "prediction": {"prediction_source": "ios_coreml_offline_harness"}},
        {"frame_id": "f2", "prediction": {"prediction_source": "ios_coreml_offline_harness"}},
    ]
    with open(preds, "w", encoding="utf-8") as handle:
        handle.write("\n".join(json.dumps(r) for r in rows) + "\n")
    try:
        resp = client.get(
            "/diagnostics/datasets/ios-harness/ui",
            params={"manifest": str(manifest), "predictions": preds},
        )
        assert resp.status_code == 200
        assert "ios_coreml_offline_harness" in resp.text
    finally:
        import os
        os.remove(preds)


def _write_harness_predictions(path):
    rows = [
        {
            "frame_id": "f1",
            "prediction": {
                "prediction_source": "ios_coreml_offline_harness",
            },
            "traversable_grid": {"cols": 2, "rows": 2, "cells": [0, 0, 0, 0]},
            "objects": [
                {"kind": "bus", "label": "公交车", "confidence": 0.98, "direction": "center",
                 "box": {"x": 0.5, "y": 0.2, "w": 0.2, "h": 0.4}},
            ],
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
    # Image + SVG overlay with the detected object box (the three legacy ROI status
    # rectangles were removed — the guidance line + region layer are the signals).
    assert "frame-overlay" in text
    assert "<svg" in text
    assert "local-file" in text
    # Detected object label surfaces so the user sees what was recognized.
    assert "公交车" in text
    # The retired 3-region status table must be GONE, not just hidden.
    assert "真实答案" not in text
    assert "近/左/右三区" not in text
    # The failure triage filter still exists (line-level honesty preserved).
    assert "漏报" in text
    # Frame without a prediction is shown honestly, not silently dropped.
    assert "该帧没有对应预测" in text


def test_ios_harness_frames_ui_draws_predicted_guidance_only(client, tmp_path):
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
    manifest.write_text(json.dumps({
        "frame_id": "f1",
        "image_path": "/tmp/vqasee-nonexistent.png",
        "ground_truth": {},
    }) + "\n", encoding="utf-8")
    preds.write_text(json.dumps({
        "frame_id": "f1",
        "prediction": {"prediction_source": "ios_coreml_offline_harness"},
        "guidance_path": line,
    }) + "\n", encoding="utf-8")

    resp = client.get(
        "/diagnostics/datasets/ios-harness/frames/ui",
        params={"manifest": str(manifest), "predictions": str(preds)},
    )
    assert resp.status_code == 200
    text = resp.text
    assert "<polyline" in text
    assert "<polygon" in text
    assert "紫实线=iPhone 预测路径" in text
    assert "绿虚线=真值" not in text
    assert "线级/车道真值已退役" in text
    assert ">预测<" in text
    assert ">真值<" not in text


def test_camvid_mask_endpoint_renders_traversable_region(client, tmp_path, monkeypatch):
    """The GT-provenance overlay tints exactly the traversable pixels and reuses
    the same mask builder as the green line, so the two can't drift apart."""
    import io

    import numpy as np
    from PIL import Image

    from app.open_dataset_adapters import CAMVID_ROAD_COLORS

    monkeypatch.setenv("VQASEE_DATASET_ROOT", str(tmp_path))
    road = next(iter(CAMVID_ROAD_COLORS))
    arr = np.zeros((10, 8, 3), dtype=np.uint8)
    arr[5:, :] = road  # bottom half = road (traversable)
    label = tmp_path / "0001TP_x_L.png"
    Image.fromarray(arr).save(label)  # (H, W, 3) uint8 -> RGB inferred

    resp = client.get(
        "/diagnostics/camvid-mask",
        params={"label": str(label), "classes": "walk", "w": 0},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"

    pixels = np.asarray(Image.open(io.BytesIO(resp.content)).convert("RGBA"))
    assert (pixels[5:, :, 3] > 0).all()  # road rows tinted
    assert (pixels[:5, :, 3] == 0).all()  # non-road rows transparent
    assert tuple(pixels[9, 0, :3]) == (48, 209, 88)  # Apple green tint


def test_camvid_mask_rejects_path_outside_allowlist(client):
    # /etc/hosts exists but is not under any allowed root -> refused, not read.
    resp = client.get("/diagnostics/camvid-mask", params={"label": "/etc/hosts"})
    assert resp.status_code == 403


def test_camvid_mask_bad_classes_is_400(client, tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.setenv("VQASEE_DATASET_ROOT", str(tmp_path))
    label = tmp_path / "x_L.png"
    Image.new("RGB", (4, 4), (0, 0, 0)).save(label)
    resp = client.get(
        "/diagnostics/camvid-mask", params={"label": str(label), "classes": "fly"}
    )
    assert resp.status_code == 400


def test_ios_harness_frames_ui_overlays_camvid_gt_mask(client, tmp_path, monkeypatch):
    """Frames with a CamVid label carry a translucent GT-region layer + a toggle,
    so users can verify the green line sits on the labeled road/sidewalk."""
    from PIL import Image

    monkeypatch.setenv("VQASEE_DATASET_ROOT", str(tmp_path))
    label = tmp_path / "0001TP_x_L.png"
    Image.new("RGB", (8, 6), (128, 64, 128)).save(label)
    image = tmp_path / "0001TP_x.png"
    Image.new("RGB", (8, 6), "#202020").save(image)

    manifest = tmp_path / "m.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "frame_id": "f1",
                "image_path": str(image),
                "label_path": str(label),
                "traversable_classes": "walk",
                "ground_truth": {
                    },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    preds = tmp_path / "preds.jsonl"
    preds.write_text(
        json.dumps(
            {
                "frame_id": "f1",
                "prediction": {
                    "prediction_source": "ios_coreml_offline_harness",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    resp = client.get(
        "/diagnostics/datasets/ios-harness/frames/ui",
        params={"manifest": str(manifest), "predictions": str(preds)},
    )
    assert resp.status_code == 200
    text = resp.text
    assert "class='gt-mask'" in text
    assert "/diagnostics/camvid-mask?label=" in text
    assert "id='gtMaskToggle'" in text
    assert "真值可走区域" in text


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

    # region_miss: only f1 qualifies (truth walkable, pred empty).
    # Match the frame-card heading precisely — a loose "f2" substring collides with
    # color hex like #bf5af2 in the legend.
    rm = client.get("/diagnostics/datasets/ios-harness/frames/ui", params={**base, "filter": "region_miss"})
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
        {"frame_id": "f1", "prediction": {
 "config_version": config_version}},
        {"frame_id": "f2", "prediction": {
 "config_version": config_version}},
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
        bump = client.post("/diagnostics/perception-config/bump", json={"thresholds": {"seg_traversable_pixel": 0.7}})
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
        client.post("/diagnostics/perception-config/bump", json={"thresholds": {"seg_traversable_pixel": 0.71}})
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
        handle.write(json.dumps({"frame_id": "f1", "prediction": {"prediction_source": "ios_coreml_offline_harness"}}) + "\n")
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


# --- iPhone-perceived walkable region (traversable_grid) ---------------------

def _grid_wire(rows, cols, ones):
    cells = [0] * (rows * cols)
    for (r, c) in ones:
        cells[r * cols + c] = 1
    return {"cols": cols, "rows": rows, "cells": cells}


def test_traversable_grid_png_and_mask_helpers():
    from app.diagnostic_api import _traversable_grid_png_datauri, _traversable_grid_to_mask

    grid = _grid_wire(3, 4, [(0, 0), (0, 1)])
    mask = _traversable_grid_to_mask(grid)
    assert mask is not None and mask.shape == (3, 4)
    assert mask[0, 0] and mask[0, 1] and not mask[2, 3]
    uri = _traversable_grid_png_datauri(grid)
    assert uri and uri.startswith("data:image/png;base64,")
    # Malformed grids never render garbage — they return None (explicit "no region").
    assert _traversable_grid_to_mask({"cols": 2, "rows": 2, "cells": [1, 0, 1]}) is None
    assert _traversable_grid_png_datauri(None) is None


def test_lane_grid_png_datauri_renders_and_rejects_garbage():
    from app.diagnostic_api import _lane_grid_png_datauri

    grid = _grid_wire(3, 4, [(0, 0), (2, 3)])
    uri = _lane_grid_png_datauri(grid, (255, 214, 10, 210))
    assert uri and uri.startswith("data:image/png;base64,")
    # Same explicit "no lane" contract as the traversable grid: bad payload -> None.
    assert _lane_grid_png_datauri(None, (255, 214, 10, 210)) is None
    assert _lane_grid_png_datauri({"cols": 2, "rows": 2, "cells": [1]}, (0, 0, 0, 255)) is None


def test_optional_harness_model_flags_injects_lane_model(tmp_path, monkeypatch):
    from app.diagnostic_api import _optional_harness_model_flags

    monkeypatch.setenv("VQASEE_MODELS_DIR", str(tmp_path))
    # No compiled lane model yet -> no flag (harness falls back to no lane channel).
    assert _optional_harness_model_flags() == []
    # Once the old compiled pixel lane model exists, the run injects --lane-model so
    # predictions carry the debug lane_grid.
    lane = tmp_path / "VQASeeLaneSegmentation.mlmodelc"
    lane.mkdir()
    flags = _optional_harness_model_flags()
    assert flags == ["--lane-model", str(lane)]
    # The product lane-line path is UFLDv2 geometry. When that compiled model
    # exists, inject it too so predictions carry lane_polylines.
    ufld = tmp_path / "VQASeeLaneUFLDv2.mlmodelc"
    ufld.mkdir()
    flags = _optional_harness_model_flags()
    assert flags == [
        "--lane-model", str(lane),
        "--lane-polyline-model", str(ufld),
    ]


def test_optional_harness_model_flags_never_injects_seg5_for_role_eval(tmp_path, monkeypatch):
    from app.diagnostic_api import _optional_harness_model_flags, _seg5_model_path

    monkeypatch.setenv("VQASEE_MODELS_DIR", str(tmp_path))
    assert "--seg-model" not in _optional_harness_model_flags()
    flags = _optional_harness_model_flags(eval_role="vehicle")
    assert _seg5_model_path() is not None
    assert "--seg-model" not in flags


def test_harness_eta_for_drive_twinlite_stays_under_old_mc5_cap():
    from app.diagnostic_api import _format_duration, _harness_eta_seconds

    seconds = _harness_eta_seconds(701, "vehicle")
    assert seconds < 900
    assert "分钟" in _format_duration(seconds)


def test_finalize_dead_harness_records_completed_run(tmp_path, monkeypatch):
    from app import diagnostic_api

    manifest = tmp_path / "m.jsonl"
    manifest.write_text('{"frame_id":"f1","image_path":"x.png"}\n', encoding="utf-8")
    out = tmp_path / "out.jsonl"
    out.write_text('{"frame_id":"f1","prediction": {"prediction_source": "ios_coreml_offline_harness"}}\n', encoding="utf-8")
    exit_path = tmp_path / "exit"
    exit_path.write_text("0\n", encoding="utf-8")
    lock = tmp_path / "lock.json"
    meta = tmp_path / "meta.json"
    monkeypatch.setattr(diagnostic_api, "_harness_lock_path", lambda _m: lock)
    monkeypatch.setattr(diagnostic_api, "_harness_out_path", lambda _m: out)
    monkeypatch.setattr(diagnostic_api, "_harness_meta_path", lambda _m: meta)
    lock.write_text(json.dumps({
        "pid": 99999999,
        "manifest": str(manifest),
        "expected": 1,
        "config_version": 1,
        "config_hash": "abc",
        "exit_path": str(exit_path),
        "stderr_path": str(tmp_path / "no-stderr.log"),
    }), encoding="utf-8")
    result = diagnostic_api._finalize_dead_harness(manifest)
    assert result is not None
    assert result["status"] == "ok"
    assert result["predicted"] == 1
    assert not lock.exists()
    assert meta.is_file()


def test_harness_run_lock_reports_running_and_clears_stale(tmp_path, monkeypatch):
    from app import diagnostic_api

    manifest = tmp_path / "m.jsonl"
    manifest.write_text(json.dumps({"frame_id": "f1", "image_path": "/tmp/a.png"}) + "\n", encoding="utf-8")
    lock = tmp_path / "m.lock.json"
    monkeypatch.setattr(diagnostic_api, "_harness_lock_path", lambda _manifest: lock)

    lock.write_text(json.dumps({
        "pid": os.getpid(),
        "manifest": str(manifest),
        "started_at": "2026-08-30 10:00:00",
    }), encoding="utf-8")
    active = diagnostic_api._active_harness_run(manifest)
    assert active is not None
    assert active["pid"] == os.getpid()

    lock.write_text(json.dumps({
        "pid": 99999999,
        "manifest": str(manifest),
        "started_at": "2026-08-30 10:00:00",
    }), encoding="utf-8")
    assert diagnostic_api._active_harness_run(manifest) is None
    assert not lock.exists()


def test_pid_is_running_treats_zombie_as_dead(monkeypatch):
    from app import diagnostic_api

    monkeypatch.setattr(diagnostic_api, "_reap_pid", lambda _pid: False)
    monkeypatch.setattr(os, "kill", lambda _pid, _sig: None)
    monkeypatch.setattr(diagnostic_api, "_pid_state", lambda _pid: "Z")
    assert diagnostic_api._pid_is_running(40764) is False

    monkeypatch.setattr(diagnostic_api, "_pid_state", lambda _pid: "S")
    assert diagnostic_api._pid_is_running(40764) is True


def test_finalize_dead_harness_when_exit_file_exists_even_if_pid_looks_alive(tmp_path, monkeypatch):
    """Finished harness + zombie wrapper must not keep the UI at 12/12."""
    from app import diagnostic_api

    manifest = tmp_path / "m.jsonl"
    manifest.write_text('{"frame_id":"f1","image_path":"x.png"}\n', encoding="utf-8")
    out = tmp_path / "out.jsonl"
    out.write_text(
        '{"frame_id":"f1","prediction": {"prediction_source": "ios_coreml_offline_harness"}}\n',
        encoding="utf-8",
    )
    exit_path = tmp_path / "exit"
    exit_path.write_text("0\n", encoding="utf-8")
    lock = tmp_path / "lock.json"
    meta = tmp_path / "meta.json"
    monkeypatch.setattr(diagnostic_api, "_harness_lock_path", lambda _m: lock)
    monkeypatch.setattr(diagnostic_api, "_harness_out_path", lambda _m: out)
    monkeypatch.setattr(diagnostic_api, "_harness_meta_path", lambda _m: meta)
    monkeypatch.setattr(diagnostic_api, "_pid_is_running", lambda _pid: True)
    monkeypatch.setattr(diagnostic_api, "_reap_pid", lambda _pid: False)
    lock.write_text(json.dumps({
        "pid": os.getpid(),
        "manifest": str(manifest),
        "expected": 1,
        "config_version": 1,
        "config_hash": "abc",
        "exit_path": str(exit_path),
        "stderr_path": str(tmp_path / "no-stderr.log"),
    }), encoding="utf-8")
    result = diagnostic_api._finalize_dead_harness(manifest)
    assert result is not None
    assert result["status"] == "ok"
    assert result["predicted"] == 1
    assert not lock.exists()


def test_harness_progress_reason_complete_does_not_claim_one_minute(tmp_path, monkeypatch):
    from app import diagnostic_api

    out = tmp_path / "out.jsonl"
    out.write_text('{"frame_id":"a"}\n{"frame_id":"b"}\n', encoding="utf-8")
    monkeypatch.setattr(diagnostic_api, "_harness_out_path", lambda _m: out)
    reason = diagnostic_api._harness_progress_reason(
        tmp_path / "m.jsonl",
        {"pid": 1, "started_at": "now", "expected": 2, "eval_role": "vehicle"},
    )
    assert "已写完 2/2 帧" in reason
    assert "约 1 分钟" not in reason


def test_ios_harness_frames_ui_prefers_lane_polylines_and_demotes_grid_debug(client, tmp_path):
    """Product lane display is geometry-first: polylines are the yellow strokes.
    Pixel lane_grid may exist as a hidden debug layer, never the default overlay."""
    manifest = tmp_path / "m.jsonl"
    preds = tmp_path / "preds.jsonl"
    manifest.write_text(json.dumps({
        "frame_id": "f1",
        "image_path": "/tmp/vqasee-nonexistent.png",
        "ground_truth": {},
    }) + "\n", encoding="utf-8")
    preds.write_text(json.dumps({
        "frame_id": "f1",
        "prediction": {"prediction_source": "ios_coreml_offline_harness"},
        "lane_polylines": [
            {"lane_index": 1, "source": "rowAnchor",
             "points": [{"x": 0.45, "y": 0.42}, {"x": 0.50, "y": 0.70}, {"x": 0.55, "y": 0.98}]}
        ],
        "lane_grid": {"cols": 4, "rows": 3, "cells": [0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 1, 0]},
    }) + "\n", encoding="utf-8")

    resp = client.get(
        "/diagnostics/datasets/ios-harness/frames/ui",
        params={"manifest": str(manifest), "predictions": str(preds)},
    )
    assert resp.status_code == 200
    text = resp.text
    assert "iPhone 车道折线" in text
    assert "hide-lane-grid-debug" in text
    assert "lane-grid-debug" in text
    assert "已画出车道折线" in text
    assert "黄色实线=车道折线" in text
    assert "黄块=像素车道调试层" not in text
    assert "iPhone 感知车道线" not in text
    assert "CamVid 真值车道线" not in text
    assert "蓝色=真值车道线" not in text


def test_ios_harness_frames_ui_strokes_lane_grid_when_polylines_missing(client, tmp_path):
    """TwinLite frames without UFLD still get yellow strokes, not a chessboard."""
    from app.diagnostic_api import _lane_polylines_from_grid

    cols, rows = 8, 8
    cells = [0] * (cols * rows)
    for y in range(3, 8):
        cells[y * cols + 2] = 1
        cells[y * cols + 6] = 1
    derived = _lane_polylines_from_grid({"cols": cols, "rows": rows, "cells": cells})
    assert len(derived) == 2
    assert all(lane["source"] == "twinlite_mask" for lane in derived)
    assert all(len(lane["points"]) >= 3 for lane in derived)

    manifest = tmp_path / "m.jsonl"
    preds = tmp_path / "preds.jsonl"
    manifest.write_text(json.dumps({
        "frame_id": "f-grid",
        "image_path": "/tmp/vqasee-nonexistent.png",
        "ground_truth": {},
    }) + "\n", encoding="utf-8")
    preds.write_text(json.dumps({
        "frame_id": "f-grid",
        "prediction": {"prediction_source": "ios_coreml_offline_harness"},
        "lane_grid": {"cols": cols, "rows": rows, "cells": cells},
    }) + "\n", encoding="utf-8")

    resp = client.get(
        "/diagnostics/datasets/ios-harness/frames/ui",
        params={"manifest": str(manifest), "predictions": str(preds)},
    )
    assert resp.status_code == 200
    text = resp.text
    assert "class='lane-polylines'" in text or "lane-polylines" in text
    assert "hide-lane-grid-debug" in text
    assert "已画出车道折线" in text
    assert "本帧没有车道折线" not in text


def test_region_pairs_only_when_both_grids_present():
    from app.diagnostic_api import _region_pairs

    manifest = [
        {"frame_id": "f1", "traversable_grid": _grid_wire(2, 2, [(0, 0)])},
        {"frame_id": "f2", "traversable_grid": _grid_wire(2, 2, [(0, 0)])},  # pred missing
        {"frame_id": "f3"},  # GT missing
    ]
    preds = [
        {"frame_id": "f1", "traversable_grid": _grid_wire(2, 2, [(0, 0)])},
        {"frame_id": "f3", "traversable_grid": _grid_wire(2, 2, [(0, 0)])},
    ]
    pairs, dropped = _region_pairs(manifest, preds)
    assert [p[0] for p in pairs] == ["f1"]
    assert dropped == 2  # f2 (no pred grid) + f3 (no GT grid)


def test_frames_ui_shows_iphone_perceived_region_by_default(client, tmp_path, monkeypatch):
    """The per-frame page surfaces the iPhone-perceived walkable region as a green
    layer (default ON), the GT layer defaults OFF, so the user sees exactly the
    thing they asked for: what the device perceives as walkable."""
    from PIL import Image

    monkeypatch.setenv("VQASEE_DATASET_ROOT", str(tmp_path))
    label = tmp_path / "0001TP_x_L.png"
    Image.new("RGB", (8, 6), (128, 64, 128)).save(label)
    image = tmp_path / "0001TP_x.png"
    Image.new("RGB", (8, 6), "#202020").save(image)

    manifest = tmp_path / "m.jsonl"
    manifest.write_text(
        json.dumps({
            "frame_id": "f1",
            "image_path": str(image),
            "label_path": str(label),
            "traversable_classes": "walk",
            "traversable_grid": _grid_wire(3, 4, [(0, 0), (0, 1)]),
            "ground_truth": {
                },
        }) + "\n",
        encoding="utf-8",
    )
    preds = tmp_path / "preds.jsonl"
    preds.write_text(
        json.dumps({
            "frame_id": "f1",
            "prediction": {
                "prediction_source": "ios_coreml_offline_harness",
            },
            "traversable_grid": _grid_wire(3, 4, [(0, 0), (1, 1)]),
        }) + "\n",
        encoding="utf-8",
    )

    resp = client.get(
        "/diagnostics/datasets/ios-harness/frames/ui",
        params={"manifest": str(manifest), "predictions": str(preds)},
    )
    assert resp.status_code == 200
    text = resp.text
    assert "class='pred-mask'" in text
    assert "data:image/png;base64," in text
    assert "id='predMaskToggle'" in text and "id='predMaskToggle' checked" in text
    assert "iPhone 感知的可走区域" in text
    # GT region defaults OFF: framesWrap starts hidden and its toggle is unchecked.
    assert "id='framesWrap' class='hide-gt-mask'" in text
    assert "id='gtMaskToggle' checked" not in text


def test_ios_harness_ui_shows_region_iou_metrics(client, tmp_path):
    """The aggregate page scores region IoU/recall/precision from the stored GT
    grid + predicted grid — the quantitative form of "is the iPhone-perceived
    walkable region close to truth"."""
    manifest = tmp_path / "m.jsonl"
    manifest.write_text(
        "\n".join(
            json.dumps({
                "frame_id": fid,
                "traversable_grid": _grid_wire(2, 2, [(0, 0), (0, 1)]),
                "ground_truth": {
                    },
            })
            for fid in ("f1", "f2")
        ) + "\n",
        encoding="utf-8",
    )
    preds = tmp_path / "preds.jsonl"
    preds.write_text(
        "\n".join(
            json.dumps({
                "frame_id": fid,
                "prediction": {"prediction_source": "ios_coreml_offline_harness"},
                "traversable_grid": _grid_wire(2, 2, [(0, 0), (0, 1)]),
            })
            for fid in ("f1", "f2")
        ) + "\n",
        encoding="utf-8",
    )

    resp = client.get(
        "/diagnostics/datasets/ios-harness/ui",
        params={"manifest": str(manifest), "predictions": str(preds)},
    )
    assert resp.status_code == 200
    text = resp.text
    assert "可走区域指标" in text
    assert "区域 IoU" in text
    assert "覆盖率 recall" in text
    assert "准确率 precision" in text
    # Perfect overlap on both frames -> IoU 1.000 shown.
    assert "1.000" in text
