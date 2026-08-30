#!/usr/bin/env python3
"""Re-export the trained N=5 CamVid multiclass Fast-SCNN (mc5) checkpoint to Core ML
at an ARBITRARY input size — WITHOUT retraining.

Why (乔布斯 P0, real-time): mc5 at 512x512 costs ~1.1s p50 on the Mac harness
(CPU wall-clock, not iPhone ANE). Fast-SCNN is fully convolutional, so the SAME
weights run at a smaller input; cost scales ~with pixel area (512->256 ≈ 4x
cheaper). This script lets us produce 256/384 variants so the harness can measure
the latency×accuracy trade-off and 罗根 can size the on-device budget with data
instead of guessing. No weights change, so this is a pure export, not a new model.

The exported contract matches the trainer exactly: input `image` (Core ML
ImageType, scale=1/255), output `[1,5,H,W]` logits; the device takes argmax /
role-primary softmax (see LocalSegmentation.swift).

Usage:
    python deploy/ios/export_mc5_coreml.py \
        --ckpt ~/.cache/vqasee/models/fast_scnn_camvid_mc5.pth \
        --size 256 \
        --out-mlpackage ~/.cache/vqasee/models/VQASeeTraversabilitySeg5_256.mlpackage
"""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import coremltools as ct
import torch

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_finetune_module():
    """Import the multiclass finetune script as a module to reuse FastSCNN +
    MultiClassSeg + CAMVID_NUM_CLASSES (single source of truth for the arch)."""
    path = REPO_ROOT / "deploy" / "ios" / "finetune_fast_scnn_camvid_multiclass.py"
    spec = importlib.util.spec_from_file_location("mc5_finetune", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ckpt", type=Path, required=True, help="fast_scnn_camvid_mc5.pth (backbone state_dict)")
    parser.add_argument("--size", type=int, default=256, help="square input size for the exported model")
    parser.add_argument("--out-mlpackage", type=Path, required=True)
    args = parser.parse_args()

    mc = _load_finetune_module()
    backbone = mc.FastSCNN(mc.CAMVID_NUM_CLASSES)
    state = torch.load(args.ckpt, map_location="cpu")
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    # strict=True: a key/shape mismatch fails loud rather than loading partial weights.
    backbone.load_state_dict(state, strict=True)
    model = mc.MultiClassSeg(backbone).eval()

    example = torch.rand(1, 3, args.size, args.size)
    with torch.no_grad():
        out = model(example)
    if tuple(out.shape) != (1, mc.CAMVID_NUM_CLASSES, args.size, args.size):
        raise SystemExit(f"unexpected output shape {tuple(out.shape)} at size {args.size}")

    traced = torch.jit.trace(model, example)
    ml = ct.convert(
        traced,
        inputs=[ct.ImageType(name="image", shape=example.shape, scale=1 / 255.0, bias=[0, 0, 0])],
        convert_to="mlprogram",
        minimum_deployment_target=ct.target.iOS16,
    )
    args.out_mlpackage.parent.mkdir(parents=True, exist_ok=True)
    ml.save(str(args.out_mlpackage))
    print(f"saved Core ML -> {args.out_mlpackage} (size={args.size}, contract [1,5,{args.size},{args.size}] logits)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
