#!/usr/bin/env python3
"""Convert Fast-SCNN floor segmentation PyTorch checkpoint to Core ML.

Expected checkpoint: Tanishjain9/fast-scnn-floor-segmentation
Output: VQASeeTraversabilitySegmentation.mlpackage
"""
from __future__ import annotations

import argparse
from pathlib import Path

import coremltools as ct
import torch
import torch.nn as nn
import torch.nn.functional as F


class _ConvBNReLU(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size=3, stride=1, padding=1):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(in_channels, out_channels, kernel_size, stride, padding, bias=False),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True),
        )

    def forward(self, x):
        return self.conv(x)


class _DSConv(nn.Module):
    def __init__(self, in_channels, out_channels, stride=1):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(in_channels, in_channels, 3, stride, 1, groups=in_channels, bias=False),
            nn.BatchNorm2d(in_channels),
            nn.ReLU(inplace=True),
            nn.Conv2d(in_channels, out_channels, 1, 1, 0, bias=False),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True),
        )

    def forward(self, x):
        return self.conv(x)


class _DWConv(nn.Module):
    def __init__(self, channels, stride=1):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(channels, channels, 3, stride, 1, groups=channels, bias=False),
            nn.BatchNorm2d(channels),
            nn.ReLU(inplace=True),
        )

    def forward(self, x):
        return self.conv(x)


class LinearBottleneck(nn.Module):
    def __init__(self, in_channels, out_channels, t=6, stride=2):
        super().__init__()
        mid = in_channels * t
        self.use_shortcut = stride == 1 and in_channels == out_channels
        self.block = nn.Sequential(
            _ConvBNReLU(in_channels, mid, 1, 1, 0),
            _ConvBNReLU(mid, mid, 3, stride, 1),
            nn.Conv2d(mid, out_channels, 1, 1, 0, bias=False),
            nn.BatchNorm2d(out_channels),
        )
        self.block[1].conv[0] = nn.Conv2d(mid, mid, 3, stride, 1, groups=mid, bias=False)

    def forward(self, x):
        out = self.block(x)
        return x + out if self.use_shortcut else out


class LearningToDownsample(nn.Module):
    def __init__(self):
        super().__init__()
        self.conv = _ConvBNReLU(3, 32, 3, 2, 1)
        self.dsconv1 = _DSConv(32, 48, 2)
        self.dsconv2 = _DSConv(48, 64, 2)

    def forward(self, x):
        return self.dsconv2(self.dsconv1(self.conv(x)))


class PyramidPooling(nn.Module):
    def __init__(self, in_channels=128, out_channels=128):
        super().__init__()
        inter = in_channels // 4
        self.conv1 = _ConvBNReLU(in_channels, inter, 1, 1, 0)
        self.conv2 = _ConvBNReLU(in_channels, inter, 1, 1, 0)
        self.conv3 = _ConvBNReLU(in_channels, inter, 1, 1, 0)
        self.conv4 = _ConvBNReLU(in_channels, inter, 1, 1, 0)
        self.out = _ConvBNReLU(in_channels * 2, out_channels, 1, 1, 0)

    def _pool(self, x, size, conv):
        height, width = x.shape[2:]
        pooled = F.adaptive_avg_pool2d(x, size)
        pooled = conv(pooled)
        return F.interpolate(pooled, size=(height, width), mode="bilinear", align_corners=True)

    def forward(self, x):
        return self.out(
            torch.cat(
                [
                    x,
                    self._pool(x, 1, self.conv1),
                    self._pool(x, 2, self.conv2),
                    self._pool(x, 3, self.conv3),
                    self._pool(x, 6, self.conv4),
                ],
                dim=1,
            )
        )


class GlobalFeatureExtractor(nn.Module):
    def __init__(self):
        super().__init__()
        self.bottleneck1 = nn.Sequential(
            LinearBottleneck(64, 64, 6, 2),
            LinearBottleneck(64, 64, 6, 1),
            LinearBottleneck(64, 64, 6, 1),
        )
        self.bottleneck2 = nn.Sequential(
            LinearBottleneck(64, 96, 6, 2),
            LinearBottleneck(96, 96, 6, 1),
            LinearBottleneck(96, 96, 6, 1),
        )
        self.bottleneck3 = nn.Sequential(
            LinearBottleneck(96, 128, 6, 1),
            LinearBottleneck(128, 128, 6, 1),
            LinearBottleneck(128, 128, 6, 1),
        )
        self.ppm = PyramidPooling(128, 128)

    def forward(self, x):
        return self.ppm(self.bottleneck3(self.bottleneck2(self.bottleneck1(x))))


class FeatureFusionModule(nn.Module):
    def __init__(self):
        super().__init__()
        self.dwconv = _DWConv(128, 1)
        self.conv_lower_res = nn.Sequential(nn.Conv2d(128, 128, 1, 1, 0, bias=True), nn.BatchNorm2d(128))
        self.conv_higher_res = nn.Sequential(nn.Conv2d(64, 128, 1, 1, 0, bias=True), nn.BatchNorm2d(128))
        self.relu = nn.ReLU(inplace=True)

    def forward(self, higher_res, lower_res):
        lower_res = F.interpolate(lower_res, size=higher_res.shape[2:], mode="bilinear", align_corners=True)
        lower_res = self.dwconv(lower_res)
        lower_res = self.conv_lower_res(lower_res)
        higher_res = self.conv_higher_res(higher_res)
        return self.relu(lower_res + higher_res)


class Classifier(nn.Module):
    def __init__(self, num_classes=2):
        super().__init__()
        self.dsconv1 = _DSConv(128, 128, 1)
        self.dsconv2 = _DSConv(128, 128, 1)
        self.conv = nn.Sequential(nn.Dropout(0.1), nn.Conv2d(128, num_classes, 1))

    def forward(self, x):
        return self.conv(self.dsconv2(self.dsconv1(x)))


class FastSCNN(nn.Module):
    def __init__(self, num_classes=2):
        super().__init__()
        self.learning_to_downsample = LearningToDownsample()
        self.global_feature_extractor = GlobalFeatureExtractor()
        self.feature_fusion = FeatureFusionModule()
        self.classifier = Classifier(num_classes)

    def forward(self, x):
        size = x.shape[2:]
        higher = self.learning_to_downsample(x)
        lower = self.global_feature_extractor(higher)
        fused = self.feature_fusion(higher, lower)
        out = self.classifier(fused)
        return F.interpolate(out, size=size, mode="bilinear", align_corners=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--size", type=int, default=512)
    args = parser.parse_args()

    model = FastSCNN(2).eval()
    state_dict = torch.load(args.checkpoint, map_location="cpu")
    missing, unexpected = model.load_state_dict(state_dict, strict=False)
    if missing or unexpected:
        raise SystemExit(f"Checkpoint mismatch. missing={missing}, unexpected={unexpected[:10]}")

    example = torch.rand(1, 3, args.size, args.size)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
