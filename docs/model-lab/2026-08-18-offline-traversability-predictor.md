# Offline Traversability Predictor (Server-side Proxy)

Date: 2026-08-18

## 背景

开源数据集（CamVid、BDD100K）只有图片 + mask，没有 VQASee 预测。真实产品预测器 `LocalPathGuidanceSignal` 只在 iPhone 上通过 Core ML 运行，无法在 Mac 上批量跑几千帧。要在离线阶段合上评估闭环，需要一个服务端预测器。

## 决策：offline proxy predictor

用同源的 Fast-SCNN floor-segmentation 模型（`deploy/ios/convert_floor_segmentation_onnx_to_coreml.sh` 转换的同一模型族），导出为 ONNX，在服务端跑推理得到通行性 mask，再用共享 ROI 逻辑映射成 path-guidance 字段。

```text
RGB image
→ Fast-SCNN floor-seg ONNX (server, onnxruntime CPU)
→ traversable mask
→ path_roi.path_guidance_from_mask (NEAR/LEFT/RIGHT ROI 覆盖率 → status)
→ {near/left/right_front_status, focus_direction}
```

选它而不是 Qwen 结构化预测的原因：
1. 与平台"不启 Qwen"设计一致，快、可批量。
2. 贴近产品的通行性判断逻辑（同一 ROI 阈值）。
3. Qwen 自由文本转结构有解析幻觉风险，违背"暴露不确定性"原则；留作后续可选增强，且必须先定输出契约。

## 诚实原则（对齐 rgb-only 路线 Phase 1）

- 这是 **offline proxy**，不是上线的 on-device 模型；其指标是相对趋势信号，不是 iPhone 真值。
- onnxruntime 未安装或模型缺失 → `capability=unsupported` + 明确原因；绝不假装、绝不产假预测、绝不静默跳过帧。
- 与 iOS 端预测做 parity 对比（`app/path_parity.py`），漂移超阈值告警，界定离线指标可信度。

## 关键实现

- `server-vqa/app/path_roi.py`：单一来源的 ROI 与阈值（`CANDIDATE_OPEN_MIN=0.60`、`CAUTION_MIN=0.28`）。GT（数据集 mask）与预测（模型 seg）共用，避免双实现漂移。
- `server-vqa/app/traversability_predictor.py`：
  - `probe_capability()` / `Capability`：能力探测。
  - `prediction_from_traversable_mask(mask)`：纯 numpy 核心，可脱离模型/onnxruntime 测试。
  - `TraversabilityPredictor`：懒加载 ONNX session；`_mask_from_logits` 支持多类 argmax（class 0=floor）与单通道概率输出。
  - `predict_manifest(rows, predictor)`：批量，缺 image_path / 缺文件都记为显式 error。

## 关键防线：非自证

Ground truth 来自数据集 mask（`path_dataset_import` / open dataset adapters），prediction 来自模型 seg 输出（本预测器）。两者输入不同，评估才有意义。ROI 逻辑共享不等于来源相同。

## 模型资产状态

- 转换脚本此前遇到 Hugging Face 域名解析失败、`.venv` 缺 `onnx`、coremltools 9.0 不再暴露 legacy onnx 转换等问题（见 `2026-08-12-rgb-only-traversability-model-route.md`）。
- 因此本预测器默认查找 `server-vqa/models/vqasee_traversability_segmentation.onnx` 或环境变量 `VQASEE_TRAVERSABILITY_ONNX`；缺失即 unsupported。
- 依赖装在可选文件 `server-vqa/requirements-predictor.txt`（onnxruntime + numpy + Pillow）。

## 验证

```bash
source .venv/bin/activate && pytest server-vqa/tests/test_traversability_predictor.py server-vqa/tests/test_path_parity.py -q
```

纯核心（ROI→prediction）、能力探测、多类 logits 解析、批量 error 处理、parity 漂移告警均有单测覆盖，且不依赖真实模型/onnxruntime。

## 下一步

- 开发者本机提供/转换 ONNX 模型后，跑 CamVid 全量 predict → eval → 存基线 → 回归门禁，验证真实指标与延迟。
- 收集 iOS 与服务端预测的 parity 数据，量化漂移，决定离线指标的可信区间。
- 评估 CamVid/BDD100K 语义标签是否真的覆盖 floor/traversable。
