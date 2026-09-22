#!/usr/bin/env python3
"""Convert TwinLiteNet (BDD, 0.44M) to Core ML for the App road-surface adaptor.

Contract (must stay stable so swapping backends does not change Swift decode):
  input : image (1, 3, 360, 640) RGB, Core ML ImageType scale=1/255
  outputs: da_logits   (1, 2, 360, 640)  ch1 = drivable
           lane_logits (1, 2, 360, 640)  ch1 = lane marking

No ImageNet norm (matches TwinLiteNet official demo).
"""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

NET_H = 360
NET_W = 640


def _load_arch(repo: Path):
    path = repo / "model" / "TwinLite.py"
    spec = importlib.util.spec_from_file_location("vqasee_twinlite_arch_export", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot import TwinLite.py from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _strip_module(state: dict) -> dict:
    if state and all(key.startswith("module.") for key in state):
        return {key[len("module.") :]: value for key, value in state.items()}
    return state


class TwinLiteCoreMLWrapper(nn.Module):
    def __init__(self, net: nn.Module):
        super().__init__()
        self.net = net

    def forward(self, image: torch.Tensor):
        da_logits, lane_logits = self.net(image)
        return da_logits, lane_logits


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.home() / ".cache" / "vqasee" / "ext" / "TwinLiteNet",
    )
    parser.add_argument(
        "--ckpt",
        type=Path,
        default=Path.home() / ".cache" / "vqasee" / "models" / "twinlitenet_bdd.pth",
    )
    parser.add_argument(
        "--out-mlpackage",
        type=Path,
        default=Path.home() / ".cache" / "vqasee" / "models" / "VQASeeTwinLiteNet.mlpackage",
    )
    args = parser.parse_args()
    if not (args.repo / "model" / "TwinLite.py").is_file():
        raise SystemExit(f"TwinLite repo missing: {args.repo}")
    if not args.ckpt.is_file():
        raise SystemExit(f"weights missing: {args.ckpt}")

    import coremltools as ct

    mod = _load_arch(args.repo)
    net = mod.TwinLiteNet()
    state = torch.load(str(args.ckpt), map_location="cpu")
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    net.load_state_dict(_strip_module(state), strict=True)
    net.eval()
    wrapped = TwinLiteCoreMLWrapper(net).eval()

    example = torch.rand(1, 3, NET_H, NET_W)
    with torch.no_grad():
        da, lane = wrapped(example)
    if tuple(da.shape) != (1, 2, NET_H, NET_W) or tuple(lane.shape) != (1, 2, NET_H, NET_W):
        raise SystemExit(f"unexpected TwinLite shapes da={tuple(da.shape)} lane={tuple(lane.shape)}")

    traced = torch.jit.trace(wrapped, example, strict=False)
    with torch.no_grad():
        da_t, lane_t = traced(example)
    da_diff = float(np.max(np.abs(da.numpy() - da_t.numpy())))
    lane_diff = float(np.max(np.abs(lane.numpy() - lane_t.numpy())))
    print(f"trace vs eager max_abs_diff da={da_diff:.6g} lane={lane_diff:.6g}")

    ml = ct.convert(
        traced,
        inputs=[ct.ImageType(name="image", shape=example.shape, scale=1 / 255.0, bias=[0, 0, 0])],
        outputs=[
            ct.TensorType(name="da_logits"),
            ct.TensorType(name="lane_logits"),
        ],
        convert_to="mlprogram",
        minimum_deployment_target=ct.target.iOS16,
    )
    args.out_mlpackage.parent.mkdir(parents=True, exist_ok=True)
    if args.out_mlpackage.exists():
        import shutil

        shutil.rmtree(args.out_mlpackage)
    ml.save(str(args.out_mlpackage))
    print(f"saved Core ML -> {args.out_mlpackage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
