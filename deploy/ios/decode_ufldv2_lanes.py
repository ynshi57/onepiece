#!/usr/bin/env python3
"""Decode UFLDv2 Core ML lane outputs into per-lane point lists and draw them as
polylines on an image. This is the "C2" reference decode for
docs/model-lab/2026-08-29-lane-line-detection-ufldv2.md — it proves the row/col
ordinal-classification head yields clean lane *lines* (not the blob mask the old
pixel segmenter produced), and is the spec the Swift on-device decoder mirrors.

Decode logic mirrors the UFLDv2 repo demo.py `pred2coords` for CULane:
  - row lanes (indices 1,2): for each valid anchor row, expected grid cell via a
    local softmax around the argmax -> x; y = row_anchor[k] * H.
  - col lanes (indices 0,3): symmetric, x = col_anchor[k] * W; y = expected cell.
CULane anchors: row_anchor = linspace(0.42,1,72), col_anchor = linspace(0,1,81).

Usage:
  python deploy/ios/decode_ufldv2_lanes.py \
    --model ~/.cache/vqasee/models/VQASeeLaneUFLDv2.mlpackage \
    --images 0001TP_006990.png 0006R0_f00930.png \
    --camvid-dir dataset/camvid/CamVid_RGB \
    --out /tmp/ufldv2_camvid_lanes.png
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

# Decode constants per dataset (configs/*_res18.py + utils/common.py + eval_wrapper.py).
# CULane: 4 lanes, ego pair on row idx [1,2] + col idx [0,3], row existence gate /2.
# CurveLanes: 10 lanes, all iterated, row+col existence gate /4, taller input.
DECODE = {
    "culane": dict(
        num_grid_row=200, num_grid_col=100, num_row=72, num_col=81,
        row_anchor=np.linspace(0.42, 1.0, 72), col_anchor=np.linspace(0.0, 1.0, 81),
        row_lane_idx=[1, 2], col_lane_idx=[0, 3],
        row_gate_div=2.0, col_gate_div=4.0,
        net_h=320, net_w=1600, crop_ratio=0.6,
    ),
    "curvelanes": dict(
        num_grid_row=200, num_grid_col=100, num_row=72, num_col=41,
        row_anchor=np.linspace(0.4, 1.0, 72), col_anchor=np.linspace(0.0, 1.0, 41),
        row_lane_idx=list(range(10)), col_lane_idx=list(range(10)),
        row_gate_div=4.0, col_gate_div=4.0,
        net_h=800, net_w=1600, crop_ratio=0.8,
    ),
}
LANE_COLORS = [(255, 60, 60), (60, 200, 255), (60, 255, 120), (255, 214, 10),
               (200, 60, 255), (255, 140, 40), (40, 255, 220), (255, 90, 160),
               (150, 255, 60), (100, 160, 255)]


def preprocess(pil: Image.Image, cfg: dict) -> Image.Image:
    """Resize to (H/crop_ratio, W) then keep the bottom net_h rows (drops sky),
    exactly as the UFLDv2 test transform does."""
    net_h, net_w = cfg["net_h"], cfg["net_w"]
    resized = pil.convert("RGB").resize((net_w, int(net_h / cfg["crop_ratio"])))
    top = resized.height - net_h
    return resized.crop((0, top, net_w, resized.height))


def _softmax(x: np.ndarray, axis: int) -> np.ndarray:
    e = np.exp(x - x.max(axis=axis, keepdims=True))
    return e / e.sum(axis=axis, keepdims=True)


def decode(pred: dict, img_w: int, img_h: int, cfg: dict, local_width: int = 1) -> list[list[tuple[int, int]]]:
    loc_row = pred["loc_row"][0]      # (grid_row, num_row, lanes)
    loc_col = pred["loc_col"][0]      # (grid_col, num_col, lanes)
    exist_row = pred["exist_row"][0]  # (2, num_row, lanes)
    exist_col = pred["exist_col"][0]  # (2, num_col, lanes)

    num_grid_row, num_row = cfg["num_grid_row"], cfg["num_row"]
    num_grid_col, num_col = cfg["num_grid_col"], cfg["num_col"]
    row_anchor, col_anchor = cfg["row_anchor"], cfg["col_anchor"]

    max_idx_row = loc_row.argmax(0)   # (num_row, lanes)
    valid_row = exist_row.argmax(0)   # (num_row, lanes)
    max_idx_col = loc_col.argmax(0)
    valid_col = exist_col.argmax(0)

    lanes: list[list[tuple[int, int]]] = []

    for i in cfg["row_lane_idx"]:
        pts: list[tuple[int, int]] = []
        if valid_row[:, i].sum() > num_row / cfg["row_gate_div"]:
            for k in range(num_row):
                if valid_row[k, i]:
                    lo = max(0, max_idx_row[k, i] - local_width)
                    hi = min(num_grid_row - 1, max_idx_row[k, i] + local_width) + 1
                    idx = np.arange(lo, hi)
                    prob = _softmax(loc_row[idx, k, i], axis=0)
                    loc = (prob * idx).sum() + 0.5
                    x = int(loc / (num_grid_row - 1) * img_w)
                    y = int(row_anchor[k] * img_h)
                    pts.append((x, y))
        if pts:
            lanes.append(pts)

    for i in cfg["col_lane_idx"]:
        pts = []
        if valid_col[:, i].sum() > num_col / cfg["col_gate_div"]:
            for k in range(num_col):
                if valid_col[k, i]:
                    lo = max(0, max_idx_col[k, i] - local_width)
                    hi = min(num_grid_col - 1, max_idx_col[k, i] + local_width) + 1
                    idx = np.arange(lo, hi)
                    prob = _softmax(loc_col[idx, k, i], axis=0)
                    loc = (prob * idx).sum() + 0.5
                    x = int(col_anchor[k] * img_w)
                    y = int(loc / (num_grid_col - 1) * img_h)
                    pts.append((x, y))
        if pts:
            lanes.append(pts)

    return lanes


def draw(pil: Image.Image, lanes: list[list[tuple[int, int]]]) -> Image.Image:
    out = pil.convert("RGB").copy()
    d = ImageDraw.Draw(out)
    for li, pts in enumerate(lanes):
        color = LANE_COLORS[li % len(LANE_COLORS)]
        if len(pts) >= 2:
            d.line(pts, fill=color, width=6, joint="curve")
        for (x, y) in pts:
            d.ellipse([x - 4, y - 4, x + 4, y + 4], fill=color)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--images", nargs="+", required=True)
    parser.add_argument("--camvid-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dataset", default="culane", choices=list(DECODE))
    args = parser.parse_args()

    import coremltools as ct

    cfg = DECODE[args.dataset]
    ml = ct.models.MLModel(str(args.model))

    tiles = []
    for name in args.images:
        src = args.camvid_dir / name
        pil = Image.open(src)
        img_w, img_h = pil.size
        net_in = preprocess(pil, cfg)
        pred = ml.predict({"image": net_in})
        lanes = decode(pred, img_w, img_h, cfg)
        n_pts = sum(len(l) for l in lanes)
        print(f"{name}: {len(lanes)} lanes, {n_pts} points")
        tiles.append(draw(pil, lanes))

    w = max(t.width for t in tiles)
    h = sum(t.height for t in tiles)
    canvas = Image.new("RGB", (w, h), (0, 0, 0))
    y = 0
    for t in tiles:
        canvas.paste(t, (0, y))
        y += t.height
    args.out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(args.out)
    print(f"saved -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
