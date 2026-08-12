# RGB-only Traversability：普通 iPhone 的本地通行路径模型路线

Date: 2026-08-12

## 背景

用户主测试设备是普通 iPhone 17，不支持 LiDAR/ARKit sceneDepth。VQASee 不能把通行路径能力押在 Pro / LiDAR 机型上。

主路径必须是 RGB-only：

```text
RGB camera
→ YOLO / Apple Vision
→ monocular depth or semantic segmentation
→ LocalPathGuidanceSignal
→ Path Guidance Overlay
```

LiDAR/ARKit sceneDepth 只作为 Pro 设备增强路径。

## 结论

下一步优先顺序：

1. **Depth Anything V2 Small Core ML**：先获得单目相对深度，补普通 iPhone 的近/远线索。
2. **DeepLabV3 / segmentation Core ML sample**：验证语义分割接入流程，但 VOC/COCO 语义不一定有 floor，需要评估标签是否适合通行区域。
3. **Fast-SCNN / custom floor-traversability segmentation**：长期更适合实时通行区域，但需要数据和转换/训练。

## 候选路线

### A. Depth Anything V2 Small Core ML

Apple Core ML Models 已集成 Depth Anything V2；Depth Anything V2 官方也说明 Apple Core ML integration 可用。优点是无需 LiDAR，可在普通 iPhone 上提供单目深度估计。缺点是它不是 metric-safe depth，不能直接输出“可以走”，只能用于 near/far、障碍候选和不确定性增强。

用途：

```text
relative depth map
→ near obstacle cue
→ LocalDepthCueSignal
→ path guidance caution / unknown
```

### B. Semantic segmentation Core ML

Apple Core ML 有 semantic image segmentation 示例，能把模型输出 mask overlay 到图像；Hugging Face 也有 Core ML semantic segmentation sample。优点是接入方式成熟。缺点是通用分割标签不一定包含 floor/traversable，必须评估标签集。

用途：

```text
segmentation mask
→ floor / obstacle / person / vehicle / unknown
→ traversability map
```

### C. Fast-SCNN / custom traversability segmentation

Fast-SCNN 这类模型面向嵌入式实时语义分割。长期更适合本地通行路径，但需要训练/转换/评测。不能直接拿来上线。

## 接入计划

### Phase 1：模型资产探测接口

新增 runner，但如果模型不存在，只输出：

```text
segmentationCapability = unsupported
monocularDepthCapability = unsupported
```

不能假装支持。

### Phase 2：Depth Anything V2 Small POC

- 下载/加入 Core ML 模型；
- 跑单帧相对深度；
- 从下半部/中心 ROI 提取 near obstacle cue；
- 写入诊断：`monocular_depth_capability=active`。

验收：

- 普通 iPhone 17 可运行；
- p95 本地推理可接受；
- 桶/椅子靠近时 near path 变 caution；
- 空地面不频繁 blocked。

### Phase 3：Segmentation POC

- 先接 Apple / Core ML segmentation sample 或 DeepLabV3；
- 验证 label 是否能表达 floor / obstacle；
- 如果不行，进入 custom floor/traversability 数据路线。

验收：

- 能输出 mask；
- mask 与预览对齐；
- 诊断报告能统计 floor/unknown/obstacle 区域比例。

## 安全原则

- 单目深度和分割都不能输出“安全通过”。
- 所有输出都是 candidate / caution / blocked / unknown。
- 视觉 overlay 必须显示不确定性。
- 语音只解释 overlay，不替代用户观察。

## 参考

- Apple Core ML Models includes Depth Anything V2 and semantic segmentation model resources.
- Apple Core ML semantic segmentation sample describes loading segmentation models and overlaying masks.
- Depth Anything V2 provides efficient monocular depth estimation for RGB-only devices.
- Fast-SCNN is designed for real-time semantic segmentation on embedded devices.
