# Phase 2：在 CamVid 上微调 Fast-SCNN，验证"采集→微调→修复漏报"闭环

日期：2026-08-25　主责：全麦（模型）　配合：罗根（端上契约/导出保真）　裁决：乔布斯（是否上真机默认）

## 目标

Phase 1 换成 Cityscapes 户外权重后，region IoU~0.77、precision~0.97，但 recall~0.79，
仍**漏报**约 83 帧可走区域。Phase 2 用 CamVid 自己的标注微调，把 recall 抬上去，验证
"采集难场景数据 → 微调 → 该场景漏报被修复"这条产品闭环真的能跑通。

## 无泄漏实验设计（AGENTS 测试底线）

CamVid 是视频，相邻帧近重复，**按帧随机切分会泄漏**。先按序列统计 zero-shot 难度：

| 序列 | n | IoU | recall | miss |
|---|---|---|---|---|
| 0016E5 | 305 | 0.667 | 0.671 | **73** ← 漏报集中在此 |
| 0001TP | 124 | 0.667 | 0.718 | 13 |
| 0006R0 | 101 | 0.890 | 0.899 | 6 |
| Seq05V | 171 | 0.935 | 0.957 | 0（太简单，不能当留出集） |

据此定**带间隔的时序留出**：0016E5 **尾段 35%（107 帧）留出做测试**，中间丢 5%（15 帧）
gap 段避免边界相邻帧泄漏，0016E5 头段 + 其余 3 序列全进训练（train=579）。留出帧**永不参与训练**。

## 方法（`deploy/ios/finetune_fast_scnn_camvid.py`）

- 模型：Tramac FastSCNN backbone **从 Cityscapes 权重初始化**，分类头换 2 类；
  **warm-start**：2 类头由 19 类头按 {road+sidewalk} / {其余} 求均值初始化，
  起点直接在 Phase 1 决策面附近，CPU 几个 epoch 即收敛。
- 预处理与端上一致：`.scaleFill` 拉伸到 512×512 + ImageNet 归一化（烘进 forward）。
- 损失：加权 CE + **Tversky(α=0.3,β=0.7)**，β>α 重罚漏报（FN）→ 专抬 recall；precision 由 region gate 另行守。
- 增广：水平翻转 + 亮度抖动。早停：按留出 IoU，patience=3。
- 输出契约不变：`[1,2,H,W]`，ch0=notTrav、ch1=trav，**端上 Swift 零改动**。
- 环境：本机无 CUDA/MPS，纯 CPU；FastSCNN ~1.1M 参数、CamVid 小，8 epoch 内早停于 epoch 6。

## 结果（0016E5 尾段 107 帧，无泄漏，apples-to-apples）

| 指标 | zero-shot（Cityscapes） | 微调（CamVid） |
|---|---|---|
| mean_iou | 0.495 | **0.968** |
| mean_recall | 0.496 | **0.992** |
| mean_precision | 0.996 | 0.976 |
| region_miss_frames | 50 | **0** |
| region_false_go_frames | 0 | 0 |

漏报 50→0、recall 0.50→0.99、IoU 翻倍，precision 仅 0.996→0.976（false_go 仍 0）。
**闭环有效被证明，且数字来自永不参与训练的留出帧。**

## 验证

- 训练脚本端到端跑通，early stop @epoch6。
- **导出保真**：微调 Core ML vs torch，留出 30 帧 `max|Δprob|=0.0149`（fp16 级），train→export→CoreML 整链无走样。
- 产物：`~/.cache/vqasee/models/fast_scnn_camvid_ft.pth` 与 `VQASeeTraversabilitySegmentation.ft.mlpackage`。

## 诚实边界：专化 vs 泛化（**为什么没有直接把它设为真机默认**）

这个模型是在 **579 帧 CamVid** 上微调的，对 CamVid 域高度专化。我们的评测集也是 CamVid，
所以它在评测上大胜——但这**不等于**它在**真实 iPhone 户外帧**（不同相机、不同城市/光照）上一定更好。
Phase 1 的 Cityscapes 广域模型泛化面更宽。因此：

- **本轮交付的是"闭环配方"被证明有效**，不是"CamVid 专化模型应当上真机"。
- 是否把微调模型设为端上默认，取决于近期使用/评测域是否 CamVid-like（Demo/CamVid 评测→可装；
  真实世界泛化→保留 Cityscapes 广域模型）。
- **真正的下一步**：当闭环采集到**真机 iPhone 帧**后，用同一脚本在其上微调——那才是对准部署域的正解。

## 影响面

- 未改端上默认模型（避免静默把 CamVid 专化模型推给真实用户）；当前 App 仍是 Phase 1 Cityscapes 模型。
- 端上 2 通道契约未变；新增训练脚本无新外部依赖（torch/coremltools/PIL/numpy 均已在 models profile）。
- 微调产物放 `~/.cache`，未污染仓库；旧 floor 备份仍在。

## 下一步

- 乔布斯裁决：是否 (a) 装微调模型为默认、(b) 保留 Cityscapes 广域默认、(c) 在全 701 帧重训后再装。
- Phase 2b（可选）：引入 BDD100K drivable/语义分割做广域微调，兼顾泛化。
- 采集真机 iPhone 帧 → 同脚本微调 → 对准真实部署域（真正闭环）。
