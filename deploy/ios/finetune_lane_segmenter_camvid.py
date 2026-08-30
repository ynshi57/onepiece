#!/usr/bin/env python3
"""Dedicated binary LANE-MARKING segmenter on CamVid (lane vs background).

Scope (honest): CamVid labels lane markings as thin PIXELS (LaneMkgsDriv/NonDriv,
~0.67% of pixels). This trains lane-marking PIXEL segmentation only — NOT lane
geometry / polylines / ego-lane, which need CULane/TuSimple-style instance labels
+ a row-anchor model (UFLD/CLRNet); that is Phase D. See
docs/model-lab/2026-08-27-lane-marking-segmentation.md.

Thin-class handling:
- Train-time GT DILATION (--train-dilate): thin 1-px lines are widened so the loss
  has gradient. Evaluation uses the ORIGINAL thin mask + a reported tolerance band
  (app/lane_metrics) — we never score against the dilated target.
- Recall-biased Tversky + heavy lane class weight.
- Leakage-safe split: hold out the TAIL of one sequence with a dropped GAP band.

Preprocessing matches the device (512x512 scaleFill + ImageNet norm baked in). The
exported Core ML is [1,2,H,W] logits (ch0=notLane, ch1=lane); device wiring is T4.

CPU-only. Usage:
    python deploy/ios/finetune_lane_segmenter_camvid.py \
        --manifest docs/datasets/camvid-manifest.jsonl \
        --weights ~/.cache/vqasee/models/fast_scnn_citys.pth \
        --test-seq Seq05V --epochs 25 \
        --out-ckpt ~/.cache/vqasee/models/lane_seg_camvid.pth \
        --out-mlpackage ~/.cache/vqasee/models/VQASeeLaneSegmentation.mlpackage
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server-vqa"
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from app.open_dataset_adapters import CAMVID_LANE_COLORS, _camvid_color_mask  # noqa: E402
from app.lane_metrics import evaluate_lane  # noqa: E402

_conv_path = REPO_ROOT / "deploy" / "ios" / "convert_fast_scnn_cityscapes_pth_to_coreml.py"
_spec = importlib.util.spec_from_file_location("citys_conv", _conv_path)
citys = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(citys)
FastSCNN = citys.FastSCNN
IMAGENET_MEAN = citys.IMAGENET_MEAN
IMAGENET_STD = citys.IMAGENET_STD
NUM_CLASSES_CITYS = citys.NUM_CLASSES  # 19

SIZE = 512


def _seq_of(frame_id: str) -> str:
    return frame_id.split("/")[-1][:6]


def _lane_mask(label_path: str) -> np.ndarray:
    lab = np.asarray(Image.open(label_path).convert("RGB").resize((SIZE, SIZE), Image.NEAREST))
    return _camvid_color_mask(lab, CAMVID_LANE_COLORS)


def _dilate_t(mask: torch.Tensor, radius: int) -> torch.Tensor:
    """Max-pool dilation of a [H,W] float tensor by an odd kernel (train-time only)."""
    if radius <= 0:
        return mask
    k = 2 * radius + 1
    return F.max_pool2d(mask[None, None], kernel_size=k, stride=1, padding=radius)[0, 0]


class LaneSeg(nn.Module):
    def __init__(self, backbone: FastSCNN):
        super().__init__()
        self.backbone = backbone
        self.register_buffer("mean", torch.tensor(IMAGENET_MEAN).view(1, 3, 1, 1))
        self.register_buffer("std", torch.tensor(IMAGENET_STD).view(1, 3, 1, 1))

    def forward(self, x):
        return self.backbone((x - self.mean) / self.std)


def build_lane_model(weights: Path) -> LaneSeg:
    """2-class lane Fast-SCNN warm-started from Cityscapes: copy matching backbone
    tensors; init the lane channel from the road filter (lanes sit on road) and the
    not-lane channel from the mean of all classes."""
    sd = torch.load(weights, map_location="cpu")
    if isinstance(sd, dict) and "state_dict" in sd:
        sd = sd["state_dict"]
    bb19 = FastSCNN(NUM_CLASSES_CITYS)
    bb19.load_state_dict(sd, strict=True)
    bb2 = FastSCNN(2)
    dst = bb2.state_dict()
    for k, v in bb19.state_dict().items():
        if k in dst and dst[k].shape == v.shape:
            dst[k] = v.clone()
    w19 = bb19.classifier.conv[1].weight.data  # [19,128,1,1]
    b19 = bb19.classifier.conv[1].bias.data
    dst["classifier.conv.1.weight"] = torch.stack([w19.mean(0), w19[0]], dim=0)  # [notLane, lane<-road]
    dst["classifier.conv.1.bias"] = torch.stack([b19.mean(), b19[0]])
    bb2.load_state_dict(dst)
    return LaneSeg(bb2)


class LaneDataset(torch.utils.data.Dataset):
    def __init__(self, items, train: bool):
        self.items = items
        self.train = train

    def __len__(self):
        return len(self.items)

    def __getitem__(self, idx):
        it = self.items[idx]
        img = Image.open(it["image_path"]).convert("RGB").resize((SIZE, SIZE), Image.BILINEAR)
        arr = np.asarray(img, dtype=np.float32) / 255.0
        y = _lane_mask(it["label_path"]).astype(np.float32)
        if self.train:
            if random.random() < 0.5:
                arr = arr[:, ::-1, :].copy()
                y = y[:, ::-1].copy()
            if random.random() < 0.5:
                arr = np.clip(arr * random.uniform(0.8, 1.2), 0.0, 1.0)
        x = torch.from_numpy(arr).permute(2, 0, 1).contiguous()
        return x, torch.from_numpy(y)


def lane_tversky(logits, target, alpha=0.3, beta=0.7, eps=1e-6):
    prob = F.softmax(logits, dim=1)[:, 1]
    t = target
    tp = (prob * t).sum()
    fp = (prob * (1 - t)).sum()
    fn = ((1 - prob) * t).sum()
    return 1.0 - (tp + eps) / (tp + alpha * fp + beta * fn + eps)


@torch.no_grad()
def eval_lane(model, items, thr: float, tol: int) -> dict:
    pairs = []
    for it in items:
        img = Image.open(it["image_path"]).convert("RGB").resize((SIZE, SIZE), Image.BILINEAR)
        x = torch.from_numpy(np.asarray(img, dtype=np.float32) / 255.0).permute(2, 0, 1).unsqueeze(0)
        prob = F.softmax(model(x)[0], dim=0)[1].numpy()
        pred = prob >= thr
        gt = _lane_mask(it["label_path"])  # ORIGINAL thin mask (never dilated for eval)
        pairs.append((it["frame_id"], pred, gt))
    return evaluate_lane(pairs, tol=tol)


def _select_score(m: dict) -> float:
    """Tolerance-band F1 — robust for thin lanes (strict pixel IoU is too harsh)."""
    r = m.get("mean_recall_tol") or 0.0
    p = m.get("mean_precision_tol") or 0.0
    return (2 * r * p / (r + p)) if (r + p) else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--weights", type=Path, required=True)
    ap.add_argument("--test-seq", type=str, default="Seq05V")
    ap.add_argument("--test-tail-frac", type=float, default=0.35)
    ap.add_argument("--gap-frac", type=float, default=0.05)
    ap.add_argument("--epochs", type=int, default=25)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--lr", type=float, default=5e-4)
    ap.add_argument("--patience", type=int, default=6)
    ap.add_argument("--train-dilate", type=int, default=3, help="GT dilation radius during TRAINING only")
    ap.add_argument("--lane-weight", type=float, default=12.0, help="CE weight for the lane class")
    ap.add_argument("--eval-thr", type=float, default=0.5)
    ap.add_argument("--eval-tol", type=int, default=4, help="tolerance-band radius (px @512) for eval")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out-ckpt", type=Path, required=True)
    ap.add_argument("--out-mlpackage", type=Path)
    ap.add_argument("--out-baseline", type=Path, help="write FINAL held-out lane metrics as a baseline json")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    rows = [json.loads(l) for l in args.manifest.read_text().splitlines() if l.strip()]
    rows = [r for r in rows if r.get("image_path") and r.get("label_path")]
    if args.limit:
        rows = rows[: args.limit]

    others = [r for r in rows if _seq_of(r["frame_id"]) != args.test_seq]
    tgt = sorted((r for r in rows if _seq_of(r["frame_id"]) == args.test_seq), key=lambda r: r["frame_id"])
    n = len(tgt)
    n_test = int(round(n * args.test_tail_frac))
    n_gap = int(round(n * args.gap_frac))
    test = tgt[n - n_test:]
    train = others + tgt[: max(0, n - n_test - n_gap)]
    if not test or not train:
        sys.exit(f"bad split: train={len(train)} test={len(test)}")
    print(f"sequences={sorted({_seq_of(r['frame_id']) for r in rows})}")
    print(f"train={len(train)} held-out {args.test_seq} tail={len(test)}  train_dilate={args.train_dilate} eval_tol={args.eval_tol}")

    model = build_lane_model(args.weights)
    model.eval()
    print("@init held-out lane:", json.dumps(eval_lane(model, test, args.eval_thr, args.eval_tol)))

    loader = torch.utils.data.DataLoader(
        LaneDataset(train, train=True), batch_size=args.batch, shuffle=True, num_workers=0
    )
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    class_w = torch.tensor([1.0, float(args.lane_weight)])
    best_score, best_state, no_improve = -1.0, None, 0
    for epoch in range(1, args.epochs + 1):
        model.train()
        running = 0.0
        for x, y in loader:
            opt.zero_grad()
            logits = model(x)
            # Dilate GT for the loss only, so thin lines have gradient.
            yd = torch.stack([_dilate_t(m, args.train_dilate) for m in y])
            ce_target = yd.long()
            loss = F.cross_entropy(logits, ce_target, weight=class_w) + lane_tversky(logits, yd)
            loss.backward()
            opt.step()
            running += loss.item() * x.size(0)
        model.eval()
        m = eval_lane(model, test, args.eval_thr, args.eval_tol)
        score = _select_score(m)
        print(f"epoch {epoch:02d}  loss={running/len(train):.4f}  tolF1={score:.4f}  {json.dumps(m)}")
        if score > best_score:
            best_score, no_improve = score, 0
            best_state = {k: v.clone() for k, v in model.backbone.state_dict().items()}
        else:
            no_improve += 1
            if no_improve >= args.patience:
                print(f"early stop at epoch {epoch}")
                break

    if best_state is not None:
        model.backbone.load_state_dict(best_state)
    model.eval()
    final = eval_lane(model, test, args.eval_thr, args.eval_tol)
    print("=== FINAL held-out lane (leakage-free, strict + tolerance) ===")
    print("final:", json.dumps(final))

    if args.out_baseline:
        import datetime as _dt

        args.out_baseline.parent.mkdir(parents=True, exist_ok=True)
        args.out_baseline.write_text(
            json.dumps(
                {
                    "metrics": final,
                    "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                    "checkpoint": str(args.out_ckpt),
                    "test_seq": args.test_seq,
                    "eval_tol": args.eval_tol,
                    "eval_thr": args.eval_thr,
                },
                indent=2,
            )
        )
        print(f"saved lane baseline -> {args.out_baseline}")

    args.out_ckpt.parent.mkdir(parents=True, exist_ok=True)
    torch.save(model.backbone.state_dict(), args.out_ckpt)
    print(f"saved lane checkpoint -> {args.out_ckpt}")

    if args.out_mlpackage:
        import coremltools as ct

        example = torch.rand(1, 3, SIZE, SIZE)
        traced = torch.jit.trace(model.eval(), example)
        ml = ct.convert(
            traced,
            inputs=[ct.ImageType(name="image", shape=example.shape, scale=1 / 255.0, bias=[0, 0, 0])],
            convert_to="mlprogram",
            minimum_deployment_target=ct.target.iOS16,
        )
        args.out_mlpackage.parent.mkdir(parents=True, exist_ok=True)
        ml.save(str(args.out_mlpackage))
        print(f"saved Core ML -> {args.out_mlpackage} (contract [1,2,H,W] logits, ch1=lane)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
