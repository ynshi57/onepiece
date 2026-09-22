"""TwinLiteNet diagnostic overlay: compose, cache, and gallery routes.

Does not run the 2.4s CPU model. Inference is mocked.
"""
from __future__ import annotations

import io

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.twinlite_preview import compose_twinlite_overlay, write_cached_overlay


client = TestClient(app)


def _fake_occupied():
    class Occupied:
        points = [(30.0, 38.0), (31.0, 20.0), (32.0, 8.0)]
        mids = np.full(40, np.nan)

    return Occupied()


def test_compose_tints_da_lane_and_draws_blue(monkeypatch):
    rgb = np.full((40, 60, 3), 80, dtype=np.uint8)
    lane = np.zeros((40, 60), dtype=bool)
    da = np.zeros((40, 60), dtype=bool)
    da[20:, 10:50] = True
    lane[:, 22] = True
    monkeypatch.setattr("app.twinlite_preview.infer_twinlite_masks", lambda _rgb: (lane, da))
    monkeypatch.setattr("app.twinlite_preview.occupied_lane_rails", lambda *_a, **_k: _fake_occupied())

    image = compose_twinlite_overlay(rgb, with_legend=False)
    pixels = np.asarray(image)
    da_pixel = pixels[35, 40]
    outside = pixels[5, 5]
    assert da_pixel[0] > outside[0]
    assert da_pixel[0] > da_pixel[1]
    lane_pixel = pixels[8, 22]
    assert lane_pixel[1] > lane_pixel[0]


def test_write_cached_overlay_reuses_file(monkeypatch, tmp_path):
    rgb = np.full((16, 16, 3), 40, dtype=np.uint8)
    calls = {"n": 0}

    def _compose(_rgb, **_kwargs):
        calls["n"] += 1
        return Image.new("RGB", (16, 16), (10, 20, 30))

    monkeypatch.setattr("app.twinlite_preview.preview_cache_dir", lambda: tmp_path)
    monkeypatch.setattr("app.twinlite_preview.compose_twinlite_overlay", _compose)
    monkeypatch.setattr("app.twinlite_preview.default_twinlite_weights", lambda: None)

    first = write_cached_overlay("frame_a", rgb)
    second = write_cached_overlay("frame_a", rgb)
    assert first == second
    assert first.is_file()
    assert calls["n"] == 1


def test_load_camvid_test_catalog_reads_stems(tmp_path):
    from app.camvid_scene_sample import load_camvid_test_catalog

    docs = tmp_path / "docs" / "datasets"
    docs.mkdir(parents=True)
    (docs / "camvid-test-scenes.json").write_text(
        '{"scenes":[{"id":"0001TP","samples":[{"stem":"0001TP_x"}]}],"stems":["0001TP_x"]}\n',
        encoding="utf-8",
    )
    catalog = load_camvid_test_catalog(tmp_path)
    assert catalog["stems"] == ["0001TP_x"]


def test_twinlite_ui_lists_test_split_and_not_app_default():
    resp = client.get("/diagnostics/twinlite/ui")
    assert resp.status_code == 200
    text = resp.text
    assert "不是 iPhone 默认" in text
    assert "0001TP_008430" in text
    assert "0006R0_f00960" in text
    assert "stem=0001TP_008430" in text
    assert "data-src=" in text


def test_datasets_and_overview_link_to_twinlite_gallery():
    datasets = client.get("/diagnostics/datasets/ui")
    overview = client.get("/diagnostics/ui")
    assert datasets.status_code == 200
    assert overview.status_code == 200
    assert "/diagnostics/twinlite/ui" in datasets.text
    assert "/diagnostics/twinlite/ui" in overview.text


def test_twinlite_frame_unknown_stem_is_404():
    resp = client.get("/diagnostics/twinlite/frame", params={"stem": "../etc/passwd"})
    assert resp.status_code == 404


def test_twinlite_frame_serves_cached_jpeg(monkeypatch, tmp_path):
    jpeg = tmp_path / "preview.jpg"
    Image.new("RGB", (8, 6), (200, 10, 10)).save(jpeg, format="JPEG")

    monkeypatch.setattr(
        "app.diagnostic_api._twinlite_allowed_stems",
        lambda: {"0001TP_008430"},
    )
    monkeypatch.setattr("app.diagnostic_api._twinlite_overlay_file", lambda stem: jpeg)

    resp = client.get("/diagnostics/twinlite/frame", params={"stem": "0001TP_008430"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/jpeg")
    pixels = np.asarray(Image.open(io.BytesIO(resp.content)))
    assert pixels.shape[0] == 6
    assert pixels[0, 0, 0] > 150


def test_twinlite_status_is_not_app_default():
    resp = client.get("/diagnostics/twinlite/status")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["app_default"] is False
    assert "0001TP_008430" in payload["stems"]
    assert len(payload["stems"]) == 12
