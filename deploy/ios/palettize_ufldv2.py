#!/usr/bin/env python3
"""Palettize the UFLDv2 Core ML model to shrink the huge dense MLP head so it can
ship in the iPhone app, and measure the accuracy (CamVid lane hit-rate) cost at
each bit-width. See docs/model-lab/2026-08-29-lane-line-detection-ufldv2.md (C3
on-device size blocker).

Root cause of the 394MB FP16 size: UFLDv2's classification head is a
Linear(2048 -> ~91k) whose weights dominate. Palettization (LUT + n-bit indices)
is the SOTA-recommended fix for exactly this kind of large, cluster-able head.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path

import coremltools as ct
import coremltools.optimize.coreml as cto
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import decode_ufldv2_lanes as D  # noqa: E402


def hit_rate(mlmodel, dataset: str, camvid_dir: Path, limit: int) -> tuple[float, float]:
    cfg = D.DECODE[dataset]
    imgs = sorted(glob.glob(str(camvid_dir / "*.png")))[:limit]
    hit = 0
    pts_sum = 0
    for p in imgs:
        pil = Image.open(p)
        w, h = pil.size
        pred = mlmodel.predict({"image": D.preprocess(pil, cfg)})
        lanes = D.decode(pred, w, h, cfg)
        if lanes:
            hit += 1
            pts_sum += sum(len(l) for l in lanes)
    n = max(len(imgs), 1)
    return hit / n, pts_sum / max(hit, 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in-mlpackage", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--dataset", default="culane", choices=list(D.DECODE))
    parser.add_argument("--camvid-dir", type=Path, required=True)
    parser.add_argument("--nbits", type=int, nargs="+", default=[6, 4, 2])
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args()

    base = ct.models.MLModel(str(args.in_mlpackage))
    base_hr, base_pts = hit_rate(base, args.dataset, args.camvid_dir, args.limit)
    base_mb = sum(f.stat().st_size for f in args.in_mlpackage.rglob("*")) / 1e6
    print(f"FP16 baseline: size={base_mb:.0f}MB  hit_rate={base_hr:.2f}  avg_pts={base_pts:.0f}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for nbits in args.nbits:
        op = cto.OpPalettizerConfig(nbits=nbits, mode="kmeans")
        cfg = cto.OptimizationConfig(global_config=op)
        comp = cto.palettize_weights(base, cfg)
        out = args.out_dir / f"VQASeeLaneUFLDv2_p{nbits}.mlpackage"
        comp.save(str(out))
        size_mb = sum(f.stat().st_size for f in out.rglob("*")) / 1e6
        hr, pts = hit_rate(comp, args.dataset, args.camvid_dir, args.limit)
        print(f"palette {nbits}-bit: size={size_mb:.0f}MB  hit_rate={hr:.2f}  avg_pts={pts:.0f}  -> {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
