# iPhone 可走区域导出 + 区域 IoU 评测（2026-08-24）

主责：全麦（模型/评测）；配合：罗根（端上性能约束）、思余（可视化）；乔布斯定验收。

## 背景（用户真问题）

用户看着逐帧页问：淡色绿/黄/红三区方块“好无意义”，这是模型还是算法？并给出目标：

> 我的目标是 iPhone 感知出绿色可行走区域。

## 归因：是“模型 + 算法降维”，信息被扔了

- **底层是模型**：`VQASeeTraversabilitySegmentation`（Core ML 分割）对整图输出每像素可通行概率（2 通道 logits → `sigmoid(l1-l0)`）。这才是“感知可走区域”的真身。
- **三区方块是算法**：`LocalSegmentation.cueValue` 把每像素概率图用 ROI 覆盖率 + 阈值粗暴降维成近/左/右三个格子。空间信息几乎全丢——所以“无意义”是对的。
- 关键缺陷：分割模型**每帧都算了**每像素图，但 harness 导出时只留下降维后的三区状态 + 中心线，**把可走区域整张扔了**。

## 改动：把可走区域导出并评测

1. **Swift 导出（opt-in，护住端上帧预算）**
   - 新增 `TraversableGrid{cols,rows,cells}`（`LocalPerception.swift`），row-major，**row 0 = 图像顶部**，二值（1=分割概率≥阈值，端上判定可走）。分辨率 64×48（`LocalSegmentation.gridCols/gridRows`）。
   - `LocalSegmentation.analyzeDetailed(..., emitGrid:)` 从**同一个采样器**产出网格（不额外推理）。
   - `LocalVisionAnalyzer` 加 `emitTraversableGrid`（默认 false）；只有离线 harness 传 true。**在线 App 完全不受影响**——延续“只采 ROI + ≤16 行中心线，不物化整张网格”的实时预算纪律。
   - harness 把 `traversable_grid` 写进预测 JSON；分割不可用时**不写**（明确“无区域”，非静默）。

2. **真值网格（单一真源，DCL「算一次、存下来」）**
   - manifest 生成时（`open_dataset_adapters._row_from_mask`）把同一张语义可走 mask 用 `region_grid.downsample_mask_to_grid`（多数表决、向量化）降到同 64×48，存 `traversable_grid`。IoU 从此是纯算术，不必每次读 701 张 label PNG。

3. **区域评测 + 门禁（`app/region_grid.py`）**
   - `region_scores`：逐帧 IoU / recall（真值可走被覆盖比例，低=漏走）/ precision（判为可走里真可走比例，低=虚报可走=区域级 false-go）。分母为空返回 None，不编造。
   - `evaluate_region_grids`：均值 + 安全桶 `region_false_go_frames`（precision<0.5）、`region_miss_frames`（recall<0.5）。
   - `gate_region`：false_go 帧数不得增、三个均值不得掉；兼容 `{metrics:{...}}` baseline 解包（避免和 guidance 门禁同款“静默空转”）。
   - 接进 `tools/run_ios_harness_eval.py`：`--baseline` 存 `*-region`、`--gate` 用 `*-region` 门禁；诊断台聚合页新增“可走区域指标”卡组。

## 结果（CamVid 701 帧，配置 v1）

| 指标 | 值 | 含义 |
|---|---|---|
| mean IoU | **0.625** | 两块绿区整体重合度 |
| mean recall | **0.645** | 真值可走被 iPhone 覆盖比例 |
| mean precision | **0.804** | iPhone 判可走里真可走比例 |
| region_false_go_frames | 118 | precision<0.5（把不可走当可走） |
| region_miss_frames | 158 | recall<0.5（漏掉大半可走区） |

- 逐帧可看出真实差距：`road/0001TP_006690` 上 iPhone 132 格、真值 494 格、**IoU 0.00**——模型把**天空**判为可走、漏掉**路面**（该帧引导线也退化为 insufficient）。这正是“三区方块无意义/紫线绿线不一致”的根因：**分割模型在城市街景上偏弱**，不是可视化的问题。

## 诚实边界 / 下一步（喂回闭环）

- 本轮**没有**改模型本身：只是把它真实的感知**导出、可视化、量化、上门禁**。现在“iPhone 感知出绿色可走区域”既**看得见**也**评得出**。
- 真正抬高 IoU/recall 要动**模型**：换更强的可走/地面分割模型、或用 CamVid 这类样例做 SFT/蒸馏、或加 sky/vertical 抑制。已进 backlog（全麦主责），验收指标就用本轮的 region IoU / recall / false_go 门禁。
- 阈值 `segTraversablePixel` 扫描可作为最小实验先行（不改模型，只调阈值看 recall/precision 折中）。

## 验证

- `swift build` 通过；重跑 701 帧 `predicted=701`，全部带 `traversable_grid`。
- `pytest server-vqa/tests` 233 passed（新增 `test_region_grid.py` 13 + 4 个 API 测试）。
- row0=top 朝向经真图叠加核对（天空误判可见），与中心线坐标约定一致。
