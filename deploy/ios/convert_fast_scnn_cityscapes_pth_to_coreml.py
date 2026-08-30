#!/usr/bin/env python3
"""Convert an outdoor **Cityscapes** Fast-SCNN checkpoint to the VQASee Core ML
traversability model (`VQASeeTraversabilitySegmentation`).

Why this exists (Phase 1 of the traversable-region model swap)
--------------------------------------------------------------
The bundled model was ``Tanishjain9/fast-scnn-floor-segmentation`` — a Fast-SCNN
trained for *indoor floor* segmentation. On outdoor CamVid/road frames it fails
(e.g. calls the sky traversable), which is a DOMAIN mismatch, not an architecture
bug: Fast-SCNN was designed and benchmarked on Cityscapes (outdoor driving). This
script swaps in outdoor Cityscapes weights while keeping the on-device contract.

Two non-obvious things this script gets right (the floor converter did NOT):

1. **19 classes -> our 2-channel contract.** The Cityscapes checkpoint emits 19
   semantic classes. `LocalSegmentation.sampler(fromMultiArray:)` on device expects
   a ``[1, 2, H, W]`` tensor of logits where channel 0 = NOT-traversable and
   channel 1 = traversable, and computes ``sigmoid(trav - notTrav) = P(traversable)``.
   We add a fixed reduction head that groups the 19 class logits into
   {traversable = road+sidewalk} vs {everything else} via a numerically-stable
   log-sum-exp, so ``sigmoid`` of the 2-channel difference equals the true softmax
   probability of the traversable group. The device Swift code stays UNCHANGED.

2. **ImageNet normalization.** The Cityscapes weights were trained on inputs
   normalized with ImageNet mean/std. Core ML ``ImageType`` scales the raw 0-255
   image to [0,1] (scale = 1/255); this model then applies ``(x - mean) / std``
   INSIDE ``forward`` so the network sees the distribution it was trained on. The
   floor converter skipped this, which would silently wreck accuracy here.

The architecture below is copied verbatim from Tramac/Fast-SCNN-pytorch so the
checkpoint loads with ``strict=True`` — a shape/key mismatch raises loudly rather
than silently loading partial weights.

Usage:
    python convert_fast_scnn_cityscapes_pth_to_coreml.py fast_scnn_citys.pth \
        VQASeeTraversabilitySegmentation.mlpackage [--size 512]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import coremltools as ct
import torch
import torch.nn as nn
import torch.nn.functional as F

# Cityscapes 19 train-id classes considered walkable by VQASee. road=0, sidewalk=1.
# This is the single safety-relevant knob (see AGENTS.md: 乔布斯 裁决). Widen with
# care: terrain(9) raises recall but drags grass/dirt into "walkable".
TRAVERSABLE_CLASS_IDS = (0, 1)
NUM_CLASSES = 19
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


# --- Tramac/Fast-SCNN-pytorch architecture (verbatim, for strict weight load) ---
class _ConvBNReLU(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size=3, stride=1, padding=0, **kwargs):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(in_channels, out_channels, kernel_size, stride, padding, bias=False),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(True),
        )

    def forward(self, x):
        return self.conv(x)


class _DSConv(nn.Module):
    def __init__(self, dw_channels, out_channels, stride=1, **kwargs):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(dw_channels, dw_channels, 3, stride, 1, groups=dw_channels, bias=False),
            nn.BatchNorm2d(dw_channels),
            nn.ReLU(True),
            nn.Conv2d(dw_channels, out_channels, 1, bias=False),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(True),
        )

    def forward(self, x):
        return self.conv(x)


class _DWConv(nn.Module):
    def __init__(self, dw_channels, out_channels, stride=1, **kwargs):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(dw_channels, out_channels, 3, stride, 1, groups=dw_channels, bias=False),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(True),
        )

    def forward(self, x):
        return self.conv(x)


class LinearBottleneck(nn.Module):
    def __init__(self, in_channels, out_channels, t=6, stride=2, **kwargs):
        super().__init__()
        self.use_shortcut = stride == 1 and in_channels == out_channels
        self.block = nn.Sequential(
            _ConvBNReLU(in_channels, in_channels * t, 1),
            _DWConv(in_channels * t, in_channels * t, stride),
            nn.Conv2d(in_channels * t, out_channels, 1, bias=False),
            nn.BatchNorm2d(out_channels),
        )

    def forward(self, x):
        out = self.block(x)
        if self.use_shortcut:
            out = x + out
        return out


class PyramidPooling(nn.Module):
    def __init__(self, in_channels, out_channels, **kwargs):
        super().__init__()
        inter_channels = int(in_channels / 4)
        self.conv1 = _ConvBNReLU(in_channels, inter_channels, 1, **kwargs)
        self.conv2 = _ConvBNReLU(in_channels, inter_channels, 1, **kwargs)
        self.conv3 = _ConvBNReLU(in_channels, inter_channels, 1, **kwargs)
        self.conv4 = _ConvBNReLU(in_channels, inter_channels, 1, **kwargs)
        self.out = _ConvBNReLU(in_channels * 2, out_channels, 1)

    def pool(self, x, size):
        return nn.AdaptiveAvgPool2d(size)(x)

    def upsample(self, x, size):
        return F.interpolate(x, size, mode="bilinear", align_corners=True)

    def forward(self, x):
        size = x.size()[2:]
        feat1 = self.upsample(self.conv1(self.pool(x, 1)), size)
        feat2 = self.upsample(self.conv2(self.pool(x, 2)), size)
        feat3 = self.upsample(self.conv3(self.pool(x, 3)), size)
        feat4 = self.upsample(self.conv4(self.pool(x, 6)), size)
        x = torch.cat([x, feat1, feat2, feat3, feat4], dim=1)
        return self.out(x)


class LearningToDownsample(nn.Module):
    def __init__(self, dw_channels1=32, dw_channels2=48, out_channels=64, **kwargs):
        super().__init__()
        self.conv = _ConvBNReLU(3, dw_channels1, 3, 2)
        self.dsconv1 = _DSConv(dw_channels1, dw_channels2, 2)
        self.dsconv2 = _DSConv(dw_channels2, out_channels, 2)

    def forward(self, x):
        return self.dsconv2(self.dsconv1(self.conv(x)))


class GlobalFeatureExtractor(nn.Module):
    def __init__(self, in_channels=64, block_channels=(64, 96, 128),
                 out_channels=128, t=6, num_blocks=(3, 3, 3), **kwargs):
        super().__init__()
        self.bottleneck1 = self._make_layer(LinearBottleneck, in_channels, block_channels[0], num_blocks[0], t, 2)
        self.bottleneck2 = self._make_layer(LinearBottleneck, block_channels[0], block_channels[1], num_blocks[1], t, 2)
        self.bottleneck3 = self._make_layer(LinearBottleneck, block_channels[1], block_channels[2], num_blocks[2], t, 1)
        self.ppm = PyramidPooling(block_channels[2], out_channels)

    def _make_layer(self, block, inplanes, planes, blocks, t=6, stride=1):
        layers = [block(inplanes, planes, t, stride)]
        for _ in range(1, blocks):
            layers.append(block(planes, planes, t, 1))
        return nn.Sequential(*layers)

    def forward(self, x):
        return self.ppm(self.bottleneck3(self.bottleneck2(self.bottleneck1(x))))


class FeatureFusionModule(nn.Module):
    def __init__(self, highter_in_channels, lower_in_channels, out_channels, scale_factor=4, **kwargs):
        super().__init__()
        self.scale_factor = scale_factor
        self.dwconv = _DWConv(lower_in_channels, out_channels, 1)
        self.conv_lower_res = nn.Sequential(nn.Conv2d(out_channels, out_channels, 1), nn.BatchNorm2d(out_channels))
        self.conv_higher_res = nn.Sequential(nn.Conv2d(highter_in_channels, out_channels, 1), nn.BatchNorm2d(out_channels))
        self.relu = nn.ReLU(True)

    def forward(self, higher_res_feature, lower_res_feature):
        lower_res_feature = F.interpolate(lower_res_feature, scale_factor=4, mode="bilinear", align_corners=True)
        lower_res_feature = self.dwconv(lower_res_feature)
        lower_res_feature = self.conv_lower_res(lower_res_feature)
        higher_res_feature = self.conv_higher_res(higher_res_feature)
        return self.relu(higher_res_feature + lower_res_feature)


class Classifer(nn.Module):
    def __init__(self, dw_channels, num_classes, stride=1, **kwargs):
        super().__init__()
        self.dsconv1 = _DSConv(dw_channels, dw_channels, stride)
        self.dsconv2 = _DSConv(dw_channels, dw_channels, stride)
        self.conv = nn.Sequential(nn.Dropout(0.1), nn.Conv2d(dw_channels, num_classes, 1))

    def forward(self, x):
        return self.conv(self.dsconv2(self.dsconv1(x)))


class FastSCNN(nn.Module):
    def __init__(self, num_classes, aux=False, **kwargs):
        super().__init__()
        self.aux = aux
        self.learning_to_downsample = LearningToDownsample(32, 48, 64)
        self.global_feature_extractor = GlobalFeatureExtractor(64, [64, 96, 128], 128, 6, [3, 3, 3])
        self.feature_fusion = FeatureFusionModule(64, 128, 128)
        self.classifier = Classifer(128, num_classes)

    def forward(self, x):
        size = x.size()[2:]
        higher_res_features = self.learning_to_downsample(x)
        x = self.global_feature_extractor(higher_res_features)
        x = self.feature_fusion(higher_res_features, x)
        x = self.classifier(x)
        return F.interpolate(x, size, mode="bilinear", align_corners=True)


class TraversabilityHead(nn.Module):
    """Wrap the 19-class Cityscapes Fast-SCNN into VQASee's 2-channel contract.

    Bakes ImageNet normalization (Core ML feeds [0,1]) and reduces the 19 class
    logits to ``[notTrav, trav]`` via a numerically-stable log-sum-exp over the two
    class groups. ``sigmoid(trav - notTrav)`` then equals the softmax probability of
    the traversable group — exactly what the device sampler consumes.
    """

    def __init__(self, backbone: FastSCNN, traversable_ids):
        super().__init__()
        self.backbone = backbone
        self.register_buffer("mean", torch.tensor(IMAGENET_MEAN).view(1, 3, 1, 1))
        self.register_buffer("std", torch.tensor(IMAGENET_STD).view(1, 3, 1, 1))
        trav = sorted(set(traversable_ids))
        rest = [c for c in range(NUM_CLASSES) if c not in trav]
        self.register_buffer("trav_idx", torch.tensor(trav, dtype=torch.long))
        self.register_buffer("rest_idx", torch.tensor(rest, dtype=torch.long))

    @staticmethod
    def _logsumexp(x):
        # Stable LSE over the channel dim using only max/sub/exp/sum/log/add so it
        # converts cleanly to Core ML ops. keepdim -> [N, 1, H, W].
        m = x.max(dim=1, keepdim=True).values
        return m + torch.log(torch.exp(x - m).sum(dim=1, keepdim=True))

    def forward(self, x):
        x = (x - self.mean) / self.std
        logits = self.backbone(x)  # [N, 19, H, W]
        trav = self._logsumexp(logits.index_select(1, self.trav_idx))
        notrav = self._logsumexp(logits.index_select(1, self.rest_idx))
        # Channel order MUST be [notTrav, trav] to match Swift sampler(fromMultiArray:).
        return torch.cat([notrav, trav], dim=1)  # [N, 2, H, W]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint", type=Path, help="fast_scnn_citys.pth (Cityscapes, 19-class)")
    parser.add_argument("output", type=Path, help="output .mlpackage")
    parser.add_argument("--size", type=int, default=512)
    args = parser.parse_args()

    backbone = FastSCNN(NUM_CLASSES).eval()
    state_dict = torch.load(args.checkpoint, map_location="cpu")
    if isinstance(state_dict, dict) and "state_dict" in state_dict:
        state_dict = state_dict["state_dict"]
    # strict=True: a mismatched key/shape fails loudly rather than silently loading
    # partial (wrong-domain) weights.
    backbone.load_state_dict(state_dict, strict=True)

    model = TraversabilityHead(backbone, TRAVERSABLE_CLASS_IDS).eval()

    example = torch.rand(1, 3, args.size, args.size)
    with torch.no_grad():
        out = model(example)
    if tuple(out.shape) != (1, 2, args.size, args.size):
        raise SystemExit(f"unexpected head output shape {tuple(out.shape)}, expected (1,2,{args.size},{args.size})")

    traced = torch.jit.trace(model, example)
    mlmodel = ct.convert(
        traced,
        inputs=[ct.ImageType(name="image", shape=example.shape, scale=1 / 255.0, bias=[0, 0, 0])],
        convert_to="mlprogram",
        minimum_deployment_target=ct.target.iOS16,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    mlmodel.save(str(args.output))
    print(f"Saved {args.output}")
    print(f"  traversable class ids = {sorted(set(TRAVERSABLE_CLASS_IDS))} (road, sidewalk)")
    print("  output contract = [1, 2, H, W] logits, channel0=notTrav channel1=trav")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
