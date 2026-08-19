# Model Lab：分割模型 2 通道 logits 采样 Bug（紫线乱摆的真因）

- 日期：2026-08-19
- 主责：全麦（模型接入/评测），触发：乔布斯从逐帧图发现「紫线(预测)和绿线(真值)基本不一致」
- 结论：**不是标注问题、不是坐标对齐问题，是设备端把 2 通道分割输出读错了通道**。

## 发现路径（用户直觉 → 量化 → 归因）

1. 用户看逐帧图：紫线(iPhone 预测引导线)和绿线(CamVid 真值线)对不上。
2. 量化全量 588 条预测线 vs 真值线的形状分布：
   - 预测线航向摆动 sd=0.354(真值 0.180)、横向摆幅 0.394(真值 0.261)、
     中心位置 sd=0.236(真值 0.084)。→ **预测线不是"走中间"，是"乱摆"**。
3. 查模型：`VQASeeTraversabilitySegmentation.mlmodelc` 真实存在，PyTorch 2.8 转 CoreML，
   输出 `MultiArray [1, 2, 512, 512]`——**2 通道语义分割 logits(2 类：不可通行 / 可通行)**。
4. 查 Swift 采样器 `sampler(fromMultiArray:)`：只取 `array[y*hStride + x*wStride]`，
   即 **channel 0 的原始 logit**，再拿去和阈值 0.5 比。

## 根因（两个错叠加）

1. **读错通道**：channel 0 通常是"非通行"类，方向反了。
2. **logit 当概率用**：raw logit 是无界实数，和 0.5 阈值比毫无意义。

喂给中心线和 ROI cue 的"可通行图"因此基本是噪声 → 引导线乱摆、三区状态漏报严重。
注意：ROI 三区状态与中心线**共用同一个采样器**，所以两套指标都被拖累。

## 修复

2 类 logits 做 softmax 取可通行类(channel 1)：`prob = sigmoid(logit₁ − logit₀)`，
落在 [0,1] 再和阈值比。单通道模型按概率直读；>2 类无已知通行类索引时**返回 nil 显式失败**，
绝不拿错通道糊一条路出来。改动仅在 `LocalSegmentation.swift::sampler(fromMultiArray:)`。

## 闭环实测（701 帧 CamVid walk，真身 Core ML harness）

| 引导线 | 修复前(读 ch0 raw) | 修复后(softmax ch1) |
|---|---|---|
| hit_rate 落廊率 | 0.390 | **0.873** |
| mean_deviation 横向误差 | 0.322 | **0.106** |
| over_extension 越界 | 0.279 | **0.026** |
| false_go 虚报路 | 0 | **0** |
| both_ok / missed_path | 588 / 113 | 583 / 114 |

| 三区状态 | 修复前 | 修复后 |
|---|---|---|
| risk_miss 漏报 | 190 | **2** |
| status_accuracy | 0.379 | 0.440 |
| false_block 误阻挡 | 747 | 806 |

## 诚实取舍与遗留

- **净安全大幅提升**：漏报 190→2、越界 0.279→0.026、落廊率 0.39→0.87。对"视觉引导优先"
  产品，这是决定性的正向。
- **代价**：`false_block` 747→806——修复后模型更保守(真值可走却报注意/占用)。误阻挡比
  漏报安全，但体验偏烦。根因很可能是 ROI 阈值(0.60/0.28)是在**旧的错误概率尺度**下拍的，
  概率分布已从"raw logit"变成"真 sigmoid"，需要**按新尺度重标定阈值**（下一轮全麦）。
- `status_accuracy=0.44` 仍不高、`focus_direction=0.20` 未动 → 三区口径本身噪声大，
  更坚定「用引导线取代三区框」的方向。

## 回归防护

不新增 Swift 测试(harness 无测试 target)，而是**靠闭环门禁兜底**：guidance 基线已前移到
`hit_rate=0.873 / mean_deviation=0.106 / over_extension=0.026`。若有人回退采样器，
`gate_guidance` 会因 hit_rate 骤降(>0.02)、deviation 骤升(>0.01)**直接拦下**——
端到端行为级防护,比断言单个函数更可信。

## 影响面

修复在**共享源码** `LocalSegmentation.swift`(设备 App 与 harness 符号链接同一份)，
所以 iPhone App 本体同样受益；harness 只是先一步用数据证明了它。
