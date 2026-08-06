# Local Perception Model MVP

Date: 2026-08-06

## Decision

VQASee needs a local perception layer before Qwen. The MVP is a contract and pluggable Core ML runner, not a committed model weight.

## Implemented

- Bundled `YOLO11nObject.mlmodelc` exported from `yolo11n.pt` with Core ML NMS for object boxes.
- Added `deploy/ios/export_yolo11_coreml.sh` to reproduce the export.
- `LocalPerceptionSignal`: unified local model output for objects, road cues and depth cues.
- `LocalPerceptionObject`: person / car / truck / bus / motorcycle / bicycle / dog / traffic light / sign / obstacle.
- `LocalRoadCueSignal`: crosswalk / lane marking / curb as possible future outputs.
- `LocalDepthCueSignal`: near drop / nearest obstacle direction as possible future outputs.
- `LocalPerceptionCoreMLRunner`: optional Vision/Core ML runner looking first for `YOLO11nObject.mlmodelc`, then `YOLO11nSeg.mlmodelc` for later segmentation experiments.
- Existing Apple Vision human rectangle detection is merged into `LocalPerceptionSignal` so the contract works before YOLO is bundled.
- `WalkingFrameSendPolicy` sends a backend frame when local perception detects a priority risk object.
- `WalkingImmediateFeedbackPolicy` can immediately say “正前方可能有车辆，我正在确认。” before Qwen returns.

## Safety boundary

The local perception layer is a trigger and early warning system. It must not say:

- “可以走”
- “可以开”
- “前方安全”
- “按这条轨迹驾驶”

It may say:

- “正前方可能有车辆，我正在确认。”
- “右侧疑似边界，请放慢。”

## YOLO model integration

Current bundled resource:

```text
YOLO11nObject.mlmodelc
```

It is exported from `yolo11n.pt` with `nms=True`, so Vision can return recognized object boxes.

Reproduce with:

```bash
python -m pip install ultralytics coremltools
bash deploy/ios/export_yolo11_coreml.sh
```

Segmentation experiment output:

```text
YOLO11nSeg.mlmodelc
```

`yolo11n-seg.pt` exports raw segmentation outputs; Ultralytics forces `nms=False` for segmentation. To use it for masks/boundaries, VQASee must implement raw `MLMultiArray` decode + NMS + mask processing instead of expecting `VNRecognizedObjectObservation`.

Supported label mapping includes:

- person / human / pedestrian
- car / vehicle / taxi
- truck
- bus
- motorcycle / motorbike
- bicycle / bike / cyclist
- dog / animal / pet
- traffic light
- sign / stop sign
- obstacle / barrier / cone / box
- crosswalk / zebra / pedestrian crossing
- lane / road marking / line marking
- curb / kerb / sidewalk edge

## Next experiments

1. Measure p50/p95 local inference time of `YOLO11nObject.mlmodelc` on real iPhone.
2. Implement YOLO segmentation raw `MLMultiArray` decode + NMS if mask-level boundaries are needed.
3. Evaluate YOLOPv2/HybridNets on Mac for lane/crosswalk/curb cues before bringing any road-boundary model to iPhone.
4. Evaluate Depth Anything V2 Small or LiDAR depth for curb/stair/drop-off cues.
