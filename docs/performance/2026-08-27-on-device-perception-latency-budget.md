# 端上感知延迟预算：分阶段计时（F）

- 日期：2026-08-27
- 主责：罗根（系统/延迟）；配合全麦（模型）
- 关联：`docs/decisions/2026-08-27-core-capabilities-plan.md`（四项核心能力·实时性）
- 状态：harness 分阶段计时已落地并验证；端上真机预算待有完整 Xcode 的机器复核

## 背景

四项核心能力之一是**实时性**。此前只有车道线单模型在 Mac harness 上的计时，没有端上感知栈（YOLO + 分割 + 深度 + 车道）的**整合延迟预算**，无法判断实时瓶颈在哪。

## 做了什么

- `LocalVisionAnalyzer.analyze()` 内对每个阶段（YOLO/分割/深度/车道/总）打墙钟，存入 `lastTimings`（`PerceptionFrameTimings`）。**不接入任何产品决策**，纯遥测；不改 `LocalVisionSignal`、不影响 App 行为。
- `perception-harness/main.swift` 每帧读取 `analyzer.lastTimings`，汇总打印 mean/p50/p95/max；未运行的阶段显式标 `n=0 (did not run)`，不伪装成零成本。

## 首次测量（8 帧，MAC 墙钟，非 iPhone）

```
on-device perception latency budget (MAC wall-clock, not iPhone):
  yolo         n=8 mean=4.1ms  p50=3.9ms  p95=4.9ms  max=4.9ms
  segmentation n=8 mean=129.3ms p50=125.0ms p95=154.8ms max=154.8ms
  depth        n=8 mean=0.0ms  (无深度模型，立即返回 nil)
  lane         n=8 mean=6.9ms  p50=7.0ms  p95=7.5ms  max=7.5ms
  total        n=8 mean=144.9ms p50=141.2ms p95=169.3ms max=169.3ms
```

## 根因结论（实时性瓶颈）

- **分割是头号瓶颈：~129ms，占总延迟约 89%**。YOLO(4ms)、车道(7ms) 都很轻。
- 车道线作为第二次前向仅 +7ms，代价可接受；**真正要优化的是主分割前向**。
- 深度阶段 0ms：无单目深度模型时立即返回（诚实，不是零成本假象）。

## 诚实边界

- 这是 **Mac 墙钟**，不是 iPhone。iPhone 有 Neural Engine，绝对值会不同；但**阶段排序**（分割 ≫ YOLO/车道）在端上大概率成立。
- **签字的端上预算必须来自真机 run**（罗根），本机只有 Command Line Tools，无法编译 App 做真机测量。

## 下一步（服务实时性）

1. 优化分割前向：更小输入分辨率 / 量化 / 换更快 backbone / 降频（非每帧分割）。
2. 若上 N=5 多类分割，避免叠加成两次重前向——**车道并入多类头**而非独立第二模型（全麦）。
3. 真机测 p50/p95，罗根签字端上预算，作为实时性验收硬指标。

## 2026-08-29 · mc5 多类分割 分辨率×精度 曲线（乔布斯 P0）

mc5（N=5 角色分割）512² 在 Mac 墙钟 ~1.1s，是实时拦路石。Fast-SCNN 全卷积，用输入分辨率做**设备无关**杠杆重导出（不重训，`deploy/ios/export_mc5_coreml.py`），harness 全 701 帧 walk 角色评测：

| 输入 | 分割 p50(Mac 墙钟) | 分割 p95 | 行人上马路率 ↓ | sidewalk 召回 ↑ |
|---|---|---|---|---|
| 512² | 1037ms | 1384ms | **0.096** | 0.92 |
| **384²（裁定默认）** | **586ms** | 773ms | **0.131** | 0.90 |
| 256² | 270ms | 347ms | 0.183 | 0.84 |
| 旧二值 | 170ms | — | 0.807 | — |

- **三档都碾压旧二值**（0.807 → 最差 0.183，降 77%）；分割成本随像素面积近线性（512→256 ≈ 3.8× 提速）。
- **乔布斯裁定：shipping 默认 384²**——甜点位：上马路率 0.131（比旧降 84%、逼近 512 的 0.096），seg 比 512 省 ~44%。256 作已验证兜底，512 作精度上限。
- **诚实边界**：以上仍是 Mac 墙钟、非 iPhone ANE；相对排序可信，**绝对端上预算待罗根真机签字**再决定最终 flip 到哪一档。
- 已 bundle 384 版进 App（`VQASeeTraversabilitySeg5.mlmodelc`），`use_multiclass_segmentation` 开关默认关（live 仍二值），等真机延迟签字后翻转。
