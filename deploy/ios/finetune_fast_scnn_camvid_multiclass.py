#!/usr/bin/env python3
"""T2: fine-tune an N=5 multi-class Fast-SCNN on CamVid so the device can tell the
SIDEWALK from the ROAD (and lane / obstacle) instead of one merged "walkable" blob.

Why (T1 quantified it): the 2-class model calls road+sidewalk one class, so for a
PEDESTRIAN it routes onto the carriageway 80.7% of the time (700/701 frames). A
single centerline through that merged region is semantically wrong. This model
outputs 5 classes; the device derives the role's walkable surface by argmax:

    0 background · 1 road · 2 sidewalk · 3 lane · 4 obstacle
    walk: primary = sidewalk;  drive: primary = road+lane   (see class map)

Honesty guardrails (AGENTS.md 测试底线):
- Leakage-safe split: hold out the TAIL of one CamVid SEQUENCE with a dropped GAP
  band, so no near-duplicate adjacent frame straddles train/test.
- The model-selection metric is the WALK-role safety pair on held-out frames:
  maximize (primary_recall - road_as_primary_rate). We also print the zero-shot
  and @init numbers so "did training actually help the pedestrian case" is an
  apples-to-apples, leakage-free comparison.
- Preprocessing matches the device exactly (512x512 scaleFill + ImageNet norm).
  Exported Core ML keeps a channel-per-class contract [1,5,H,W] logits; the device
  2->N change is a SEPARATE task (T4), so this script only trains + exports + scores
  offline against the role manifests / role baseline.

crosswalk (a 6th class) is deliberately NOT trained: CamVid has no crosswalk label
(see docs/model-lab/2026-08-26-t2-multiclass-segmentation-plan.md §1.1).

Runs CPU-only. Usage:
    python deploy/ios/finetune_fast_scnn_camvid_multiclass.py \
        --manifest docs/datasets/camvid-manifest.jsonl \
        --weights ~/.cache/vqasee/models/fast_scnn_citys.pth \
        --test-seq Seq05V --epochs 20 \
        --out-ckpt ~/.cache/vqasee/models/fast_scnn_camvid_mc5.pth \
        --out-mlpackage ~/.cache/vqasee/models/VQASeeTraversabilitySeg5.mlpackage
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
    CAMVID_CLASS_LANE,
    CAMVID_CLASS_OBSTACLE,
    CAMVID_CLASS_ROAD,
    CAMVID_CLASS_SIDEWALK,
    CAMVID_NUM_CLASSES,
    _camvid_color_mask,
    camvid_class_map,
    role_traversability_policy,
)
from app.region_grid import downsample_mask_to_grid  # noqa: E402
from app.role_metrics import role_region_scores  # noqa: E402

# Reuse the exact Phase 1 architecture + constants (single source of truth).
_conv_path = REPO_ROOT / "deploy" / "ios" / "convert_fast_scnn_cityscapes_pth_to_coreml.py"
_spec = importlib.util.spec_from_file_location("citys_conv", _conv_path)
citys = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(citys)
FastSCNN = citys.FastSCNN
IMAGENET_MEAN = citys.IMAGENET_MEAN
IMAGENET_STD = citys.IMAGENET_STD
NUM_CLASSES_CITYS = citys.NUM_CLASSES  # 19

SIZE = 512

# Warm-start map: our 5 classes <- Cityscapes 19 trainId groups.
# Cityscapes trainId: 0 road,1 sidewalk,2 building,3 wall,4 fence,5 pole,6 tlight,
# 7 tsign,8 veg,9 terrain,10 sky,11 person,12 rider,13 car,14 truck,15 bus,16 train,
# 17 motorcycle,18 bicycle.
CITYS_FOR_CLASS = {
    0: [2, 8, 9, 10, 6, 7],           # background <- building/veg/terrain/sky/signs
    CAMVID_CLASS_ROAD: [0],           # road <- road
    CAMVID_CLASS_SIDEWALK: [1],       # sidewalk <- sidewalk
    CAMVID_CLASS_LANE: [0],           # lane <- road (best-effort; lane sits on road)
    CAMVID_CLASS_OBSTACLE: [3, 4, 5, 11, 12, 13, 14, 15, 16, 17, 18],  # blockers
}


class MultiClassSeg(nn.Module):
    """N=5 Fast-SCNN with ImageNet normalization baked in. Input arrives in [0,1]
    (Core ML ImageType scale=1/255); output is [N,5,H,W] logits. The device takes
    argmax and derives the role's walkable surface — no threshold needed."""

    def __init__(self, backbone: FastSCNN):
        super().__init__()
        self.backbone = backbone
        self.register_buffer("mean", torch.tensor(IMAGENET_MEAN).view(1, 3, 1, 1))
        self.register_buffer("std", torch.tensor(IMAGENET_STD).view(1, 3, 1, 1))

    def forward(self, x):
        x = (x - self.mean) / self.std
        return self.backbone(x)


def build_multiclass_model(weights: Path) -> MultiClassSeg:
    """Init a 5-class Fast-SCNN from the 19-class Cityscapes checkpoint: copy every
    matching backbone tensor, and WARM-START each of the 5 classifier filters from the
    mean of the corresponding Cityscapes classes (see CITYS_FOR_CLASS). This starts
    training near a sensible outdoor decision boundary instead of a random head."""
    sd19 = torch.load(weights, map_location="cpu")
    if isinstance(sd19, dict) and "state_dict" in sd19:
        sd19 = sd19["state_dict"]
    bb19 = FastSCNN(NUM_CLASSES_CITYS)
    bb19.load_state_dict(sd19, strict=True)

    bb5 = FastSCNN(CAMVID_NUM_CLASSES)
    dst = bb5.state_dict()
    for k, v in bb19.state_dict().items():
        if k in dst and dst[k].shape == v.shape:
            dst[k] = v.clone()
    w19 = bb19.classifier.conv[1].weight.data  # [19,128,1,1]
    b19 = bb19.classifier.conv[1].bias.data    # [19]
    new_w = torch.stack([w19[CITYS_FOR_CLASS[c]].mean(0) for c in range(CAMVID_NUM_CLASSES)], dim=0)
    new_b = torch.stack([b19[CITYS_FOR_CLASS[c]].mean() for c in range(CAMVID_NUM_CLASSES)])
    dst["classifier.conv.1.weight"] = new_w
    dst["classifier.conv.1.bias"] = new_b
    bb5.load_state_dict(dst)
    return MultiClassSeg(bb5)


def _seq_of(frame_id: str) -> str:
    base = frame_id.split("/")[-1]
    return base[:6]


class CamVidClassDataset(torch.utils.data.Dataset):
    """Yields (image[3,H,W] in [0,1], class_map[H,W] in {0..4})."""

    def __init__(self, items: list[dict], train: bool):
        self.items = items
        self.train = train

    def __len__(self):
        return len(self.items)

    def __getitem__(self, idx):
        it = self.items[idx]
        img = Image.open(it["image_path"]).convert("RGB").resize((SIZE, SIZE), Image.BILINEAR)
        arr = np.asarray(img, dtype=np.float32) / 255.0
        lab = np.asarray(
            Image.open(it["label_path"]).convert("RGB").resize((SIZE, SIZE), Image.NEAREST)
        )
        y = camvid_class_map(lab).astype(np.int64)  # [H,W] {0..4}
        if self.train:
            if random.random() < 0.5:
                arr = arr[:, ::-1, :].copy()
                y = y[:, ::-1].copy()
            if random.random() < 0.5:
                arr = np.clip(arr * random.uniform(0.8, 1.2), 0.0, 1.0)
        x = torch.from_numpy(arr).permute(2, 0, 1).contiguous()
        return x, torch.from_numpy(y)


def class_tversky(logits, target, cls, alpha=0.3, beta=0.7, eps=1e-6):
    """Recall-favoring Tversky on ONE class prob (beta>alpha punishes missing it)."""
    prob = F.softmax(logits, dim=1)[:, cls]
    t = (target == cls).float()
    tp = (prob * t).sum()
    fp = (prob * (1 - t)).sum()
    fn = ((1 - prob) * t).sum()
    return 1.0 - (tp + eps) / (tp + alpha * fp + beta * fn + eps)


@torch.no_grad()
def eval_walk_role(model, items) -> dict:
    """Walk-role safety metrics on held-out frames: derive the predicted walkable
    surface = argmax==sidewalk, compare to GT role layers (from the label via the
    walk policy). Mirrors app/role_metrics so it is comparable to the role baseline."""
    policy = role_traversability_policy("walk")
    recalls, road_rates, obs_rates = [], [], []
    road_frames = obs_frames = scored = 0
    for it in items:
        img = Image.open(it["image_path"]).convert("RGB").resize((SIZE, SIZE), Image.BILINEAR)
        x = torch.from_numpy(np.asarray(img, dtype=np.float32) / 255.0).permute(2, 0, 1).unsqueeze(0)
        pred_cls = model(x)[0].argmax(0).numpy()  # [H,W]
        pred_primary = downsample_mask_to_grid(pred_cls == CAMVID_CLASS_SIDEWALK).astype(bool)
        lab = np.asarray(
            Image.open(it["label_path"]).convert("RGB").resize((SIZE, SIZE), Image.NEAREST)
        )
        gt = {
            "primary": downsample_mask_to_grid(_camvid_color_mask(lab, policy.primary)).astype(bool),
            "caution": downsample_mask_to_grid(_camvid_color_mask(lab, policy.caution)).astype(bool),
            "lane": downsample_mask_to_grid(_camvid_color_mask(lab, policy.lane)).astype(bool),
            "obstacle": downsample_mask_to_grid(_camvid_color_mask(lab, policy.obstacle)).astype(bool),
        }
        s = role_region_scores(pred_primary, **gt)
        scored += 1
        if s["primary_recall"] is not None:
            recalls.append(s["primary_recall"])
        if s["road_as_primary_rate"] is not None:
            road_rates.append(s["road_as_primary_rate"])
            if s["road_as_primary_rate"] > 0.20:
                road_frames += 1
        if s["obstacle_overlap_rate"] is not None:
            obs_rates.append(s["obstacle_overlap_rate"])
            if s["obstacle_overlap_rate"] > 0.05:
                obs_frames += 1
    mean = lambda v: (sum(v) / len(v)) if v else None
    return {
        "mean_primary_recall": mean(recalls),
        "mean_road_as_primary_rate": mean(road_rates),
        "mean_obstacle_overlap_rate": mean(obs_rates),
        "road_as_primary_frames": road_frames,
        "obstacle_overlap_frames": obs_frames,
        "scored": scored,
    }


def _select_score(m: dict) -> float:
    """Higher is better: reward sidewalk recall, punish routing onto the road."""
    recall = m.get("mean_primary_recall") or 0.0
    road = m.get("mean_road_as_primary_rate") or 0.0
    return recall - road


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True, help="Cityscapes fast_scnn_citys.pth")
    parser.add_argument("--test-seq", type=str, default="Seq05V")
    parser.add_argument("--test-tail-frac", type=float, default=0.35)
    parser.add_argument("--gap-frac", type=float, default=0.05)
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
    rows = [r for r in rows if r.get("image_path") and r.get("label_path")]
    if args.limit:
        rows = rows[: args.limit]

    others = [r for r in rows if _seq_of(r["frame_id"]) != args.test_seq]
    tgt = sorted((r for r in rows if _seq_of(r["frame_id"]) == args.test_seq), key=lambda r: r["frame_id"])
    n = len(tgt)
    n_test = int(round(n * args.test_tail_frac))
    n_gap = int(round(n * args.gap_frac))
    test = tgt[n - n_test:]
    tgt_head = tgt[: max(0, n - n_test - n_gap)]
    train = others + tgt_head
    if not test or not train:
        sys.exit(f"bad split: train={len(train)} test={len(test)}")
    print(f"sequences={sorted({_seq_of(r['frame_id']) for r in rows})}")
    print(f"train={len(train)} held-out {args.test_seq} tail={len(test)}")

    model = build_multiclass_model(args.weights)
    model.eval()
    print("@init held-out walk-role:", json.dumps(eval_walk_role(model, test)))

    loader = torch.utils.data.DataLoader(
        CamVidClassDataset(train, train=True), batch_size=args.batch, shuffle=True, num_workers=0
    )
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    # Upweight rare/important classes: sidewalk (the pedestrian surface) and lane.
    class_w = torch.tensor([0.5, 1.0, 3.0, 4.0, 1.5])
    best_score, best_state, no_improve = -1e9, None, 0
    for epoch in range(1, args.epochs + 1):
        model.train()
        running = 0.0
        for x, y in loader:
            opt.zero_grad()
            logits = model(x)
            loss = (
                F.cross_entropy(logits, y, weight=class_w)
                + class_tversky(logits, y, CAMVID_CLASS_SIDEWALK)
                + 0.5 * class_tversky(logits, y, CAMVID_CLASS_ROAD)
            )
            loss.backward()
            opt.step()
            running += loss.item() * x.size(0)
        model.eval()
        m = eval_walk_role(model, test)
        score = _select_score(m)
        print(f"epoch {epoch:02d}  loss={running/len(train):.4f}  score={score:.4f}  {json.dumps(m)}")
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
    final = eval_walk_role(model, test)
    print("=== FINAL held-out walk-role (leakage-free) ===")
    print("final:", json.dumps(final))

    args.out_ckpt.parent.mkdir(parents=True, exist_ok=True)
    torch.save(model.backbone.state_dict(), args.out_ckpt)
    print(f"saved 5-class checkpoint -> {args.out_ckpt}")

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
        print(f"saved Core ML -> {args.out_mlpackage} (contract [1,5,H,W] logits, argmax on device)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
