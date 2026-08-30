# 车道线识别落地 · 专用二值车道线分割(CamVid 真值)

- 日期：2026-08-27
- 主责：全麦（模型）；乔布斯裁决产品形态与验收；罗根评端上延迟(T4)
- 关联：`docs/decisions/2026-08-26-role-conditioned-traversability.md`（车道线/障碍物写入 VQASee 能力）、`docs/model-lab/2026-08-26-t2-multiclass-segmentation-plan.md`
- 状态：离线能力 **已实现并出真实数字**；端上通道/延迟(T4)、UI 呈现(T6)、车道几何(Phase D) 未做

## 1. 用户诉求

> “我需要你今天把车道线识别要实现出来，拿 CamVid 数据集作为真值，车道线模型怎么定的？”

必须交付一个**能测量的车道线识别能力**，真值来自 CamVid，并说清模型是怎么定的。

## 2. 模型怎么定的（全麦裁决 + 诚实约束）

路线图里乔布斯选了“dedicated 专用车道模型（UFLD/CLRNet）”。落地时有一个决定形态的硬约束：

| 方案 | 需要的标注 | CamVid 能否供给 | 今天可行 |
|---|---|---|---|
| UFLD / CLRNet（行锚，输出车道折线/车道 ID/自车道几何） | CULane/TuSimple 式**折线实例**标注 | ❌ 只有逐像素颜色，没有折线实例 | 否 |
| **专用二值车道线分割（lane mask）** | 逐像素车道线掩码 | ✅ `LaneMkgsDriv`+`LaneMkgsNonDriv` 颜色 | **是** |

**裁决**：今天做**专用二值车道线分割模型**（尊重“dedicated”——独立模型，不埋进多类头），输出 lane mask。
**诚实边界**：这是车道线**像素分割**，不是车道**几何/折线/自车道**。几何拟合需要折线实例标注 + 行锚模型，属 **Phase D**（拿到 CULane 类标注再上 UFLD/CLRNet）。今天不 over-promise 几何。

### 为什么不用 T2 多类头的 lane 通道？
T2 5 类头里 lane 只占 ~0.67% 像素，与 road/sidewalk 争特征，极易被淹没（T2 阶段甚至没单独量过 lane）。专用二值模型能**独占容量 + 真值膨胀 + 召回向 loss** 专攻这个细类，拿到干净可比的数字。端上是否合并成一个模型（省一次前向）由罗根在 T4 按“精度×延迟”定，不在今天。

## 3. 细类（0.67% 像素）三招

1. **训练期真值膨胀**（`--train-dilate 2`）：1px 细线加粗，让 loss 有梯度；**评测用原始细掩码**，绝不拿膨胀真值刷分。
2. **召回向 Tversky(α0.3/β0.7) + 重类权**（`--lane-weight 6`）：初期调太狠(weight12/β0.8)会反向坍塌成“满图都是车道线”（smoke 实测 precision 0.015），调平衡后精度回升。
3. **防泄漏切分**：留出一个序列(`Seq05V`)尾段 + 丢弃 GAP 带，避免相邻近重复帧跨越 train/test。

评测在 512×512 上算：严格逐像素 `IoU/recall/precision`，外加**容差带**(`--eval-tol 3`) `recall_tol/precision_tol`（细结构检测惯例，`tol` 明确写进指标，不静默注水）。指标与门禁：`server-vqa/app/lane_metrics.py`。

## 4. 交付物

| 类型 | 路径 |
|---|---|
| 车道线指标 + 门禁 | `server-vqa/app/lane_metrics.py` |
| 指标单测(TDD) | `server-vqa/tests/test_lane_metrics.py` |
| 专用训练脚本 | `deploy/ios/finetune_lane_segmenter_camvid.py` |
| 权重(沙箱外) | `~/.cache/vqasee/models/lane_seg_camvid.pth` |
| Core ML(契约 [1,2,H,W]，ch1=lane) | `~/.cache/vqasee/models/VQASeeLaneSegmentation.mlpackage` |
| 基线 | `server-vqa/eval-baselines/camvid-ios-lane.json` |

## 5. 留出集真实数字（Seq05V 尾段 60 帧，防泄漏）

训练 632 帧，早停于 epoch16、恢复最佳 epoch10；Core ML 已导出。基线：`server-vqa/eval-baselines/camvid-ios-lane.json`。

| 指标 | @init(暖启动) | epoch3(过程) | **FINAL(采纳)** |
|---|---|---|---|
| 严格 IoU | 0.023 | 0.30 | **0.331** |
| 严格召回 | 1.0(泛滥) | 0.96 | **0.965** |
| 严格精度 | 0.023 | 0.30 | **0.336** |
| 容差召回(tol3) | 1.0 | 0.98 | **0.982** |
| 容差精度(tol3) | 0.068 | 0.80 | **0.851** |
| 漏车道帧(/60) | 0 | 0 | **0** |

读法：严格逐像素 IoU 0.33 低是**细结构固有**（1px 横向偏移即判全错），所以主看**容差带**——
**容差召回 0.982** = 几乎找全所有车道线（60 帧无一漏）；**容差精度 0.851** = 85% 的预测落在真值 3px 内。
对比 @init(暖启动泛滥,精度 0.068)，训练把精度从 0.068→0.851 拉起，同时召回保住≈0.98。这是**真实可用**的车道线像素识别能力。

## 6. T4 端上车道线通道（已实现，harness 验证）

日期：2026-08-27。把训练好的 lane 模型接进**端上感知契约**并在 macOS harness 上验证（本机无完整 Xcode，能真正 `swift build`+run 的是 harness，那就是今天的可验证落点；iOS App 打包/UI 见下方未做）。

改动（Swift 真身源码，App 与 harness 共用符号链接）：
- `ios-vqa-app/VQASee/VQASee/LocalPerception.swift`：新增 `LaneGrid`（128×96 细栅格，行主序 row0=顶部）+ `LocalPerceptionSignal.laneGrid` 字段。lane≠可走，独立类型。
- `ios-vqa-app/VQASee/VQASee/LocalLaneSegmentation.swift`（新）：`LocalLaneSegmentationRunner`，读 `[1,2,H,W]` 的 ch1 车道类，`sigmoid(l1-l0)≥0.5` 判 lane；记录 `lastInferenceMs`（延迟预算用）；支持 bundle（设备）或直接 `.mlmodelc` URL（harness）加载。
- `ios-vqa-app/VQASee/VQASee/LocalVisionAnalyzer.swift`：可注入 `laneRunner`（设备默认 nil=不加第二次前向；harness 注入）→ 跑出 `perception.laneGrid`。
- `ios-vqa-app/perception-harness/.../main.swift`：`--lane-model <mlmodelc>` 加载（缺失/加载失败**直接 fail 不静默**）、输出 `lane_grid`、末尾打印**车道线延迟预算**。

**验证（真跑，非 mock）**：`swift build` 通过；`--lane-model` 跑 CamVid 30 帧：
- **lane_grid 产出 30/30 帧**，覆盖 min 0.5% / mean 2.9% / max 8.8%（细线稀疏合理，既非空也非泛滥）。
- **车道线延迟预算(n=30)：mean 6.6ms / p50 6.6ms / p95 7.0ms / max 7.2ms**（Mac 单次前向）。→ 第二次前向很便宜，**支持端上独立 lane 模型**；是否合并进多类头由罗根按真机 ANE 实测再定。

## 7. 未做 / 下一步（诚实标注）

- **iOS App 打包 + 消费**：把 `VQASeeLaneSegmentation.mlmodelc` 打进 App bundle、`laneGrid` 接入引导/风险决策与语音——**本机无完整 Xcode 无法构建 App**，需人工在 Xcode 中加模型资源并联调。当前 `laneGrid` 仅被 harness 产出用于评测/可视化，**引导逻辑尚未消费它**。
- **闭环评分**：`run_ios_harness_eval` 尚未对 `lane_grid` 打分（可用 `app/lane_metrics` 对 walk manifest 的 GT lane 层做栅格容差评分）——additive，未接。
- **T5**：lane 阈值(现硬编码 0.5)走 `PerceptionConfig` OTA。
- **T6**：车道线叠加 UI（思余）。
- **Phase D**：车道**几何/折线/自车道**（UFLD/CLRNet + CULane 类折线标注），CamVid 无法供给，需引入数据集。
- `setup_mac.sh` models profile：新增 lane 权重/导出/`coremltools compile_model`（本机 `coremlcompiler` 不可用，改用 `MLModel.get_compiled_model_path()`）步骤，待全量产物稳定后同步（环境依赖同步规则）。

变更影响面：新增 `LocalPerceptionSignal.laneGrid`（App+harness 共用结构，可选 nil，App 侧暂不消费=不破坏）；wire 新增 `lane_grid` key（平台侧渲染/评估暂未读取=additive）。设备默认不注入 laneRunner，**现网 App 行为不变**（无额外前向）。
