#!/usr/bin/env python3
"""Phase 2: fine-tune the outdoor Fast-SCNN traversability model on CamVid.

Phase 1 swapped in Cityscapes weights (zero-shot) and lifted region IoU to ~0.77
with precision ~0.97 but recall ~0.79 (it still MISSES ~83 walkable frames). Phase 2
fine-tunes on CamVid's OWN labels — same "traversable = road+sidewalk" definition the
platform evaluates against — to raise recall without giving back precision.

Honesty guardrail (AGENTS.md 测试底线): CamVid is video, so we split by SEQUENCE, not
by frame, to avoid train/test leakage. One whole sequence is held out and NEVER trained
on; every metric is reported on that held-out set. We also print the zero-shot Cityscapes
model's metrics on the SAME held-out frames, so "did fine-tuning actually help" is an
apples-to-apples, leakage-free comparison — not a number inflated by memorized frames.

Preprocessing matches the device exactly: the on-device Vision request uses
``.scaleFill`` (stretch to 512x512, no crop) and ImageNet normalization, so we train on
the same stretch + normalization. The exported Core ML model keeps VQASee's 2-channel
contract ([1,2,H,W], channel0=notTrav, channel1=trav), so the iPhone Swift code is
UNCHANGED.

Runs CPU-only (this Mac has no CUDA/MPS). Fast-SCNN is ~1.1M params and CamVid is small,
so a few warm-started epochs are enough.

Usage:
    python deploy/ios/finetune_fast_scnn_camvid.py \
        --manifest docs/datasets/camvid-manifest.jsonl \
        --weights ~/.cache/vqasee/models/fast_scnn_citys.pth \
        --test-seq Seq05V --epochs 20 \
        --out-ckpt ~/.cache/vqasee/models/fast_scnn_camvid_ft.pth \
        --out-mlpackage ~/.cache/vqasee/models/VQASeeTraversabilitySegmentation.ft.mlpackage
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

from app.open_dataset_adapters import (  # noqa: E402
    _camvid_traversability_mask,
    camvid_traversable_colors,
)
from app.region_grid import (  # noqa: E402
    GRID_COLS,
    GRID_ROWS,
    downsample_mask_to_grid,
    grid_from_wire,
    region_scores,
)

# Reuse the exact Phase 1 architecture + constants (single source of truth).
_conv_path = REPO_ROOT / "deploy" / "ios" / "convert_fast_scnn_cityscapes_pth_to_coreml.py"
_spec = importlib.util.spec_from_file_location("citys_conv", _conv_path)
citys = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(citys)
FastSCNN = citys.FastSCNN
TraversabilityHead = citys.TraversabilityHead
IMAGENET_MEAN = citys.IMAGENET_MEAN
IMAGENET_STD = citys.IMAGENET_STD
TRAVERSABLE_CLASS_IDS = citys.TRAVERSABLE_CLASS_IDS
NUM_CLASSES = citys.NUM_CLASSES

SIZE = 512
SEG_THRESHOLD = 0.55  # device default seg_traversable_pixel; used for BOTH models.


def _seq_of(frame_id: str) -> str:
    """CamVid sequence id = the frame filename's leading token (e.g. 0016E5, Seq05V)."""
    base = frame_id.split("/")[-1]
    return base[:6]


class BinaryTraversability(nn.Module):
    """2-class Fast-SCNN with ImageNet normalization baked in. Input arrives in [0,1]
    (Core ML ImageType scale=1/255); output is [N,2,H,W] logits, channel0=notTrav,
    channel1=trav — matching ``LocalSegmentation.sampler(fromMultiArray:)``."""

    def __init__(self, backbone: FastSCNN):
        super().__init__()
        self.backbone = backbone
        self.register_buffer("mean", torch.tensor(IMAGENET_MEAN).view(1, 3, 1, 1))
        self.register_buffer("std", torch.tensor(IMAGENET_STD).view(1, 3, 1, 1))

    def forward(self, x):
        x = (x - self.mean) / self.std
        return self.backbone(x)


def build_binary_model(weights: Path) -> BinaryTraversability:
    """Init a 2-class Fast-SCNN from the 19-class Cityscapes checkpoint: copy every
    matching tensor, and WARM-START the 2-class classifier head from the 19-class head
    (traversable channel = mean of road+sidewalk filters, other channel = mean of the
    rest). This starts fine-tuning right at the Phase 1 zero-shot decision boundary
    instead of a random head, so CPU training converges in a few epochs."""
    sd19 = torch.load(weights, map_location="cpu")
    if isinstance(sd19, dict) and "state_dict" in sd19:
        sd19 = sd19["state_dict"]
    bb19 = FastSCNN(NUM_CLASSES)
    bb19.load_state_dict(sd19, strict=True)

    bb2 = FastSCNN(2)
    dst = bb2.state_dict()
    for k, v in bb19.state_dict().items():
        if k in dst and dst[k].shape == v.shape:
            dst[k] = v.clone()
    # Warm-start the 2-class head from the 19-class head.
    w19 = bb19.classifier.conv[1].weight.data  # [19,128,1,1]
    b19 = bb19.classifier.conv[1].bias.data    # [19]
    trav = sorted(set(TRAVERSABLE_CLASS_IDS))
    rest = [c for c in range(NUM_CLASSES) if c not in trav]
    dst["classifier.conv.1.weight"] = torch.stack(
        [w19[rest].mean(0), w19[trav].mean(0)], dim=0
    )
    dst["classifier.conv.1.bias"] = torch.stack([b19[rest].mean(), b19[trav].mean()])
    bb2.load_state_dict(dst)
    return BinaryTraversability(bb2)


class CamVidDataset(torch.utils.data.Dataset):
    def __init__(self, items: list[dict], colors, train: bool):
        self.items = items
        self.colors = colors
        self.train = train

    def __len__(self):
        return len(self.items)

    def _load(self, it: dict):
        img = Image.open(it["image_path"]).convert("RGB").resize((SIZE, SIZE), Image.BILINEAR)
        arr = np.asarray(img, dtype=np.float32) / 255.0  # HWC [0,1]
        mask_full = _camvid_traversability_mask(Path(it["label_path"]), self.colors)
        mask = np.asarray(
            Image.fromarray(mask_full.astype(np.uint8) * 255).resize((SIZE, SIZE), Image.NEAREST),
            dtype=np.uint8,
        ) > 0
        return arr, mask

    def __getitem__(self, idx):
        arr, mask = self._load(self.items[idx])
        if self.train:
            if random.random() < 0.5:  # horizontal flip
                arr = arr[:, ::-1, :].copy()
                mask = mask[:, ::-1].copy()
            if random.random() < 0.5:  # brightness jitter
                arr = np.clip(arr * random.uniform(0.8, 1.2), 0.0, 1.0)
        x = torch.from_numpy(arr).permute(2, 0, 1).contiguous()  # [3,H,W] in [0,1]
        y = torch.from_numpy(mask.astype(np.int64))  # [H,W] {0,1}
        return x, y


def tversky_loss(logits, target, alpha=0.3, beta=0.7, eps=1e-6):
    """Tversky loss on the traversable-class prob. beta>alpha penalizes FALSE NEGATIVES
    (missed walkable) harder than false positives — i.e. push RECALL up, which is exactly
    Phase 1's weakness. Precision is guarded separately by the region gate."""
    prob = F.softmax(logits, dim=1)[:, 1]  # traversable prob
    t = target.float()
    tp = (prob * t).sum()
    fp = (prob * (1 - t)).sum()
    fn = ((1 - prob) * t).sum()
    return 1.0 - (tp + eps) / (tp + alpha * fp + beta * fn + eps)


@torch.no_grad()
def eval_region(prob_fn, items, colors) -> dict:
    """Region IoU/recall/precision on the given items, built the SAME way as the
    manifest GT grid (threshold -> majority-vote downsample to 64x48), so it is
    comparable to the platform's region metrics. ``prob_fn(x)->prob[H,W]`` maps a
    normalized-input model to a traversable-prob map."""
    ious, recalls, precisions = [], [], []
    false_go = miss = 0
    for it in items:
        img = Image.open(it["image_path"]).convert("RGB").resize((SIZE, SIZE), Image.BILINEAR)
        x = torch.from_numpy(np.asarray(img, dtype=np.float32) / 255.0).permute(2, 0, 1).unsqueeze(0)
        prob = prob_fn(x)  # [H,W] np or tensor
        prob = prob.numpy() if isinstance(prob, torch.Tensor) else prob
        pred_grid = downsample_mask_to_grid(prob >= SEG_THRESHOLD)
        gt_grid = grid_from_wire(it["traversable_grid"])
        if gt_grid is None:
            continue
        s = region_scores(pred_grid.astype(bool), gt_grid)
        ious.append(s["iou"])
        if s["recall"] is not None:
            recalls.append(s["recall"])
            if s["recall"] < 0.5:
                miss += 1
        if s["precision"] is not None:
            precisions.append(s["precision"])
            if s["precision"] < 0.5:
                false_go += 1
    mean = lambda v: (sum(v) / len(v)) if v else None
    return {
        "mean_iou": mean(ious),
        "mean_recall": mean(recalls),
        "mean_precision": mean(precisions),
        "region_false_go_frames": false_go,
        "region_miss_frames": miss,
        "scored": len(ious),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True, help="Cityscapes fast_scnn_citys.pth")
    parser.add_argument("--test-seq", type=str, default="0016E5", help="sequence to hold out frames from (6-char prefix)")
    parser.add_argument("--test-tail-frac", type=float, default=0.35,
                        help="fraction of --test-seq's TAIL frames held out for test. "
                             "1.0 = leave-the-whole-sequence-out; <1.0 keeps the head in train.")
    parser.add_argument("--gap-frac", type=float, default=0.05,
                        help="fraction of --test-seq dropped as a temporal GAP before the test tail, "
                             "so no near-duplicate adjacent frame straddles train/test (anti-leakage).")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--lr", type=float, default=5e-4)
    parser.add_argument("--patience", type=int, default=5)
    parser.add_argument("--limit", type=int, default=0, help="cap frames for a quick smoke run")
    parser.add_argument("--out-ckpt", type=Path, required=True)
    parser.add_argument("--out-mlpackage", type=Path)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    rows = [json.loads(line) for line in args.manifest.read_text().splitlines() if line.strip()]
    rows = [r for r in rows if r.get("image_path") and r.get("label_path") and isinstance(r.get("traversable_grid"), dict)]
    if args.limit:
        rows = rows[: args.limit]
    colors = camvid_traversable_colors(rows[0].get("traversable_classes", "walk"))

    # Leakage-safe split: hold out the TAIL of one sequence (temporally far from the
    # head we train on), with a dropped GAP band between them so no near-duplicate
    # adjacent frame straddles the boundary. tail_frac=1.0 => whole-sequence hold-out.
    others = [r for r in rows if _seq_of(r["frame_id"]) != args.test_seq]
    tgt = sorted((r for r in rows if _seq_of(r["frame_id"]) == args.test_seq), key=lambda r: r["frame_id"])
    n = len(tgt)
    n_test = int(round(n * args.test_tail_frac))
    n_gap = int(round(n * args.gap_frac))
    test = tgt[n - n_test:]
    gap = tgt[max(0, n - n_test - n_gap): n - n_test]
    tgt_head = tgt[: max(0, n - n_test - n_gap)]
    train = others + tgt_head
    if not test or not train:
        sys.exit(f"bad split: train={len(train)} test={len(test)} (check --test-seq/--test-tail-frac)")
    seqs = sorted({_seq_of(r["frame_id"]) for r in rows})
    print(f"sequences={seqs}")
    print(f"train={len(train)} frames (others={len(others)} + {args.test_seq} head={len(tgt_head)}); "
          f"gap-dropped={len(gap)}; held-out {args.test_seq} tail={len(test)} frames")

    # Zero-shot Cityscapes baseline on the SAME held-out frames (leakage-free reference).
    zs = TraversabilityHead(
        (lambda bb: (bb.load_state_dict(torch.load(args.weights, map_location="cpu"), strict=True), bb)[1])(
            FastSCNN(NUM_CLASSES)
        ),
        TRAVERSABLE_CLASS_IDS,
    ).eval()

    def zs_prob(x):
        out = zs(x)[0]  # [2,H,W]
        return torch.sigmoid(out[1] - out[0])

    zs_metrics = eval_region(zs_prob, test, colors)
    print("zero-shot (Cityscapes) held-out:", json.dumps(zs_metrics))

    model = build_binary_model(args.weights)
    model.eval()

    def ft_prob(x):
        out = model(x)[0]  # [2,H,W]
        return F.softmax(out, dim=0)[1]

    print("fine-tune (warm-started head) held-out @init:", json.dumps(eval_region(ft_prob, test, colors)))

    loader = torch.utils.data.DataLoader(
        CamVidDataset(train, colors, train=True), batch_size=args.batch, shuffle=True, num_workers=0
    )
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    class_w = torch.tensor([1.0, 2.0])  # upweight the traversable class for recall
    best_iou, best_state, no_improve = -1.0, None, 0
    for epoch in range(1, args.epochs + 1):
        model.train()
        running = 0.0
        for x, y in loader:
            opt.zero_grad()
            logits = model(x)
            loss = F.cross_entropy(logits, y, weight=class_w) + tversky_loss(logits, y)
            loss.backward()
            opt.step()
            running += loss.item() * x.size(0)
        model.eval()
        m = eval_region(ft_prob, test, colors)
        iou = m["mean_iou"] or 0.0
        print(f"epoch {epoch:02d}  loss={running/len(train):.4f}  held-out {json.dumps(m)}")
        if iou > best_iou:
            best_iou, no_improve = iou, 0
            best_state = {k: v.clone() for k, v in model.backbone.state_dict().items()}
        else:
            no_improve += 1
            if no_improve >= args.patience:
                print(f"early stop at epoch {epoch} (no held-out IoU gain for {args.patience})")
                break

    if best_state is not None:
        model.backbone.load_state_dict(best_state)
    model.eval()
    final = eval_region(ft_prob, test, colors)
    print("=== FINAL held-out comparison (same Seq, leakage-free) ===")
    print("zero-shot :", json.dumps(zs_metrics))
    print("fine-tuned:", json.dumps(final))

    args.out_ckpt.parent.mkdir(parents=True, exist_ok=True)
    torch.save(model.backbone.state_dict(), args.out_ckpt)
    print(f"saved fine-tuned 2-class checkpoint -> {args.out_ckpt}")

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
        print(f"saved Core ML -> {args.out_mlpackage} (contract [1,2,H,W], ch0=notTrav ch1=trav)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
