# iOS 离线 harness 保真边界与首个 CamVid 结果

日期：2026-08-18
负责人：全麦
状态：harness 验证 / 真机未验证

## harness 是什么

[ios-vqa-app/perception-harness](../../ios-vqa-app/perception-harness) 是 macOS 原生 SwiftPM CLI，**符号链接复用 iPhone App 的真身感知源码**（非重写）：

- YOLO11n Core ML 目标检测（`LocalPerceptionCoreMLRunner`，真身 `.mlmodelc`）
- 通行性分割（`VQASeeTraversabilitySegmentation.mlmodelc`，真身，`segmentation_capability=active`）
- 通行区域引擎 `LocalPathGuidanceEngine.evaluate`（真身逻辑）
- 亮度/遮挡门 + Vision 人形检测（真身 `LocalVisionAnalyzer`）

## 保真边界（如实记录，不掩盖）

| 维度 | harness | 真机 | 说明 |
|---|---|---|---|
| YOLO 检测 | ✅ 真身 .mlmodelc | ✅ | 一致（导出脚本同源）|
| 通行性分割 | ✅ 真身 | ✅ | 一致 |
| LiDAR/ARKit 深度 | ❌ unsupported | 视机型 | macOS 无 ARKit；harness 反映“仅相机”分支 |
| 单目深度(DepthAnything) | ❌（模型未打包）| 可选 | 缺失即 fail open |
| 计算后端 | CPU（macOS，可能 GPU）| 可能 ANE | 数值可能有微小差异 |
| 图像朝向 | `.up`（数据集已正立）| `.right`（竖握相机）| harness 显式传 `.up` |

结论：harness 用于**功能级**评测（准确率/漏报/误挡）可接受；LiDAR 专有行为与 ANE 数值需真机（device_benchmark，未做，backlog）。任何结论都标注“harness 验证 / 真机未验证”。

## 首个 CamVid 701 帧结果（真跑，14s）

| 指标 | 值 |
|---|---|
| status_accuracy | 0.3828 |
| focus_direction_accuracy | 0.2468 |
| risk_miss_count | 233 |
| false_block_count | 549 |
| unknown_prediction_rate | 0.0014 |

复现：

```bash
(cd ios-vqa-app/perception-harness && swift build)
ios-vqa-app/perception-harness/.build/debug/PerceptionHarness \
  --manifest docs/datasets/camvid-manifest.jsonl --out /tmp/camvid-ios-harness.jsonl
python server-vqa/tools/run_ios_harness_eval.py \
  --manifest docs/datasets/camvid-manifest.jsonl --predictions /tmp/camvid-ios-harness.jsonl
```

## 归因（全麦）

- **false_block=549 偏高**是主信号：CamVid 是**车载驾驶**数据集，画面里到处是车/路，而 iPhone 通行判定的 ROI/阈值是按**行走**场景调的（`near_blocked_area=0.82` 等），于是把大量“对驾驶而言可走”的路面判为占用。这不是 harness 的 bug，是真身在“非目标域”上的真实表现。
- 对产品的意义：CamVid 适合暴露“过度保守/误挡”，但不是行走通行判定的理想 GT 域。行走域基准（人行道/路口/室内）应作为后续数据集建设重点。
- 下一步实验（最小可验证）：用感知配置编辑器上调 `near_blocked_area/side_blocked_area`、微调 ROI，`--config` 复跑 harness，看 `false_block` 下降而 `risk_miss` 不恶化；`--gate` 对基线把关，只有 `risk_miss` 不变差才允许 bump 版本并 OTA 下发。

## schema 契约

Python `server-vqa/app/perception_config.py` 与 Swift `PerceptionConfig.swift` 逐字段对齐，由 `tests/test_perception_config_swift_parity.py` 做文本级防漂移。跨语言已验证：Python 生成的默认 config 被 Swift harness 解码后逐帧结果不变。
