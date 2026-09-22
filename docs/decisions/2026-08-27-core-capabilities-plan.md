# 决策：VQASee 现阶段四项核心能力设计与研发计划（乔布斯 × 小马 联合）

- 日期：2026-08-27
- 主责：乔布斯（产品裁决、验收口径）× 小马（SOTA / 技术路线）
- 状态：四项核心仍写入 AGENTS；**live 现状以 [`docs/CURRENT.md`](../CURRENT.md) 为准**（TwinLite 产品默认、角色可走未兑现）。
- 关联：`docs/decisions/2026-08-26-role-conditioned-traversability.md`、
  `docs/decisions/2026-08-25-retire-three-roi-boxes.md`

> **2026-09-22：** 历史记录。本页原「现状快照」和评测数字已按拍板删除，避免再被当成现在。

## 用户裁定（原话）

> 现阶段的核心是车道线，可通行区域(区分人和车)和障碍物的识别以及实时性，这些是核心，最重要。一定要实现这个目标。

## 乔布斯 · 产品判断

- **客户/场景**：户外街景下的步行者、骑行者、驾驶辅助用户。
- **真实问题**：产品要收敛，不能功能涣散。四项核心能力=看得准（可走区分人车 + 车道线 + 障碍物）+ 反应快（实时性），是"能用、敢信"的地基。
- **优先级 P0/P1**：四项都是必须闭环的目标，不是"建议/未来"。
- **验收口径（硬指标，退役粗三区 ROI）**：
  1. 车道线：CamVid 留出集 容差带召回/精度 + 漏车道帧；端上延迟。
  2. 可通行区域(分人车)：逐像素区域 IoU/召回/精度 + **马路误当人行道率**（行人角色安全红线）+ 驾驶角色对称指标。
  3. 障碍物：检测召回/精度（评测环）+ 可走区∩障碍真值重叠帧（安全）。
  4. 实时性：端上 YOLO+分割(+车道) 整合 p50/p95 延迟预算，慢即不合格。
- **MVP 边界**：先把每项能力做到**可评测、可上记分卡、可门禁**；端上真机上线（多类模型捆绑、车道模型捆绑）在有完整 Xcode 的机器分阶段验证发布。
- **不做/降级**：任何不服务这四项或其闭环验证的功能进 backlog。

## 小马 · SOTA / 技术路线（每项给"用什么、为什么、最小实验"）

1. **车道线**：CamVid 只有车道**像素**标注（无实例/几何）。→ 现阶段用**专用二值车道分割**（Fast-SCNN，Cityscapes 暖启动）；细线用**容差带**评分，不吹严格 IoU。车道**几何/自车道**（UFLD/CLRNet 行锚模型）需 CULane/TuSimple 类标注，列 Phase D。
2. **可通行区域(分人车)**：单一"可走"不够，SOTA 是**多类语义分割 + 角色策略**：行人角色 primary=人行道、caution=马路；机动车角色反之。→ **N=5 CamVid 微调**（background/road/sidewalk/lane/obstacle）+ 端上按角色派生 primary/caution。真值已有 walk/drive 角色 manifest。
3. **障碍物**：端上已有 YOLO11n（COCO 类）实时检测。缺**评测环**。→ 先建**障碍检测评测**（召回/精度/误阻挡）+ 与可走区融合的安全指标；台阶/路沿等 COCO 缺失类留分割/后续数据。
4. **实时性**：SOTA 端侧要有**明确延迟预算**。→ harness 给每个模型（YOLO/分割/车道/深度）单独计时 + 整合 p50/p95；端上真机复核由罗根签字后开启第二次前向。

## 现状快照

已删除。2026-09-22 起以 [`docs/CURRENT.md`](../CURRENT.md) 为准。

## 任务分发（主责 / 可验证性 / 阻塞）

### 本轮已完成（可验证）

- **A · AGENTS.md 固化四项核心能力**（乔布斯）。
- **B · 车道线上记分卡**（全麦/思余）：记分卡与真值渲染当时通过。pytest 计数已删。

### 本轮已完成（可验证）· 第二批

- **D · 障碍物评测环** ✅（全麦）：`obstacle_metrics.py`（覆盖召回/落点精度 + 漏障碍帧 + 门禁）、`run_ios_harness_eval.py` 接入（`--role-manifest` 的 obstacle 层 vs 预测 objects）、真实基线 `camvid-ios-obstacle.json`、记分卡"障碍物·看得见吗"卡。单测含坐标翻转。
- **E · 驾驶角色基线 + 记分卡对称** ✅（全麦）：`run_ios_harness_eval.py` 同一 role_manifest 同时评估 drive 角色，真实基线 `camvid-ios-drive.json`，记分卡"驾驶角色"卡与行人卡并排，凸显区分人车。
- **F · harness 整合延迟预算** ✅（罗根）：`LocalVisionAnalyzer` 分阶段计时（`PerceptionFrameTimings`，不接产品决策）、`main.swift` 打印整合 p50/p95、`swift build` 通过、沉淀 `docs/performance/2026-08-27-on-device-perception-latency-budget.md`。

### 本轮已完成（可验证）· 第三批

- **C · 车道线接入闭环评测** ✅（全麦）：
  - **根因确认**：manifest `role_grids.lane` 用多数投票降到 64×48 会抹掉细线（单测 `test_lane_presence_grid_preserves_thin_lines_that_majority_erases` 复现）。
  - **落地**：`region_grid.lane_presence_grid`（128×96、任意像素命中，对齐 Swift `LaneGrid` 128×96 中心采样）；`create_camvid_role_manifest` 每行加 **新字段** `lane_grid_fine`（旧 `role_grids.lane` 不动）；`run_ios_harness_eval._lane_pairs` 用 GT `lane_grid_fine` vs 预测 `lane_grid` → `evaluate_lane` → 存/门禁 **`camvid-ios-lane-loop`**（与留出集 `camvid-ios-lane` 分名并存，度量口径不同）。
  - **真实基线**：评测数字已按 2026-09-22 拍板删除。门禁当时通过。
  - **影响面**：manifest 仅新增字段。pytest 计数已删。
  - **诚实边界**：闭环数是同分布；泛化以留出集为准。

### 本轮已完成（可验证）· 第四批 · G 端上多类+角色派生

- **G · N=5 多类模型上设备**（全麦+罗根）✅（端上代码 + 模型 + 闭环评测；**未 bundle**，见下）：
  - **端上派生**：`PerceptionRole{pedestrian,vehicle}` + `PerceptionConfig.role`；`LocalSegmentation.sampler` 原对 `classCount>2` 返回 nil，现支持 N≥3 类**按角色 softmax 派生可走概率**（行人=sidewalk、机动车=road+lane，`SegClass` 类序单一真源对齐训练脚本）；抽出可单测 `traversableProbability(fromClassLogits:role:)`（非有限/无有效 primary 类→nil，失败可见）。
  - **模型**：`finetune_fast_scnn_camvid_multiclass.py` 训 mc5。评测数字已删。导出 Core ML 并编译。
  - **harness 接入**：新增 `--seg-model`（仿 `--lane-model`，不动 shipping 包）；role 经 `--config docs/datasets/harness-config-{walk,drive}.json` 注入；真身评测见上表 G 更新数字，基线 `camvid-ios-mc5-walk/-drive`。
  - **顺带修真 bug**：`LocalPerception.swift` 的 ARKit 段守卫 `#if canImport(ARKit)` 在完整 Xcode 的 macOS SDK 上为真、但 `ARWorldTrackingConfiguration` 仅 iOS → harness 编译失败；改为 `canImport(ARKit) && os(iOS)`（iOS 行为不变，macOS 走相机-only）。**blast radius**：共享源码同被 App + harness 消费，iOS 测试 + macOS `swift build` 双绿验证。
  - **验证**：iOS `** TEST SUCCEEDED **`（含 role/多类单测）；harness `swift build` 通过；walk/drive 各 701 帧真身评测通过。
  - **诚实边界**：mc5 **当时尚未作为 live 默认**（开关默认 false）。2026-09-22 起产品默认是 TwinLite，见 [`docs/CURRENT.md`](../CURRENT.md)；记分卡不把 mc5 数字写成已上线。端上真延迟待罗根真机签。

### 本轮已完成（可验证）· 第五批 · 实时性 P0 + H' 分级捆绑

- **P0 · mc5 分辨率曲线**（乔布斯 + 全麦/罗根）✅：全卷积重导出多档分辨率。评测数字已删。
- **H' · mc5 捆绑进 App（staged）**（罗根）✅：`VQASeeTraversabilitySeg5.mlmodelc`（384²）入 `VQASee/VQASee/` 同步组 + `project.pbxproj` explicitFileTypes；`PerceptionConfig.useMulticlassSegmentation` 开关**默认 false**（当时 live 不跑 mc5），置 true 即加载 mc5 按 role 派生。2026-09-22 起产品路面默认是 TwinLite，见 [`docs/CURRENT.md`](../CURRENT.md)。

### 本轮已完成（可验证）· 第六批 · H 模型上设备 + 平台默认带车道 + 端上真车道 overlay

- **平台侧根因修复**（罗根）✅：诊断台"运行预测"拼的 harness 命令漏了 `--lane-model` → 预测无 `lane_grid` → 逐帧页画不出车道。改为 `_optional_harness_model_flags()`（存在编译模型即注入 `--lane-model`，单一真源），并覆盖重跑用户缓存预测。逐帧视图新增车道层（🟡iPhone 车道 `lane_grid` + 🔵真值 `lane_grid_fine`）+ 图例；`_lane_grid_png_datauri` 渲染。测试全绿。
- **H · 车道模型上设备（shipped on）+ mc5 bundled（staged off）**（罗根+思余）✅：
  - `VQASeeLaneSegmentation.mlmodelc` + `VQASeeTraversabilitySeg5.mlmodelc` 均入 `VQASee/VQASee/` 同步组 + pbxproj explicitFileTypes。
  - `PerceptionConfig.useLaneSegmentation`（**默认 true**，便宜 + 仅呈现、不 gate 安全决策）；默认 analyzer 加载 bundled 车道 runner → 端上产出真实 `laneGrid`。
  - `CameraRiskOverlay.laneMarkingOverlay`：画**真实**车道格（黄），**替换**旧的硬编码斜线假车道（`roadCueOverlay` 的 laneMarking 分支已下线，仅留 curb 边界）。
  - App 带两模型 **编译 + 92+ 测试双绿**；harness 共享源码重建通过。
- **诚实边界**：端上车道显示已可编译验证,但**真机帧率/延迟未测**（本机无设备）；`useLaneSegmentation` 可 OTA 关，罗根真机签字端上预算。mc5 仍 staged off，flip 待真机延迟签字。

### 需完整 Xcode / 真机 / UI（验证阻塞，分阶段发布）

- **I · 端上角色选择器 UI**（思余+罗根）：`PerceptionConfig.role` + `useMulticlassSegmentation` 已就绪，缺 SwiftUI 入口（行人/机动车切换 + mc5 呈现）；需可视化迭代，最好配真机。
- **OTA flip 机制**（全麦/罗根）：`server-vqa/app/perception_config.py` 的 `to_dict()` 目前只发 roi/thresholds；要远程翻转 `role`/`use_multiclass_segmentation` 需给服务端 schema 加这两字段（端上已容忍其缺省）。
- **罗根真机 ANE 延迟签字**（阻塞：需用户 iPhone）：mc5@384 Mac p50 586ms，端上 ANE 真值未知；签字后决定最终 flip 到 256/384/512 哪一档并开 `use_multiclass_segmentation`。

## 成功指标

- 车道线：留出集容差召回 ≥0.95（现 0.982）、漏车道帧 =0；接入闭环后有门禁。
- 可通行区(分人车)：行人角色 马路误当人行道率从 0.807 显著下降（多类模型上设备后）；驾驶角色对称达标。→ **G 已达成**：mc5 真身评测 0.807→**0.096**、驾驶角色道路召回 **0.97**；剩发布门 H'（bundle）+ I（角色选择器）。
- 障碍物：检测召回/精度有基线且门禁；可走区∩障碍重叠帧不增。
- 实时性：端上感知栈 p95 有签字预算（罗根），不达标不发。
- 平台：`pytest server-vqa/tests` 全绿；harness `swift build` 通过；能力总览页四项能力可见。

## 沉淀

- 本计划：本文件。
- 规则：`AGENTS.md`「核心能力」+ 新增「持续执行规则（不轻易停下）」。
- 实时性预算：`docs/performance/2026-08-27-on-device-perception-latency-budget.md`（分割 p95 170ms 是头号瓶颈）。
- 记分卡（四能力卡 + 双角色卡）：`server-vqa/app/diagnostic_api.py`。
- 障碍物评测：`server-vqa/app/obstacle_metrics.py` + `tests/test_obstacle_metrics.py`。
- 车道闭环真值：`server-vqa/app/region_grid.py`（`lane_presence_grid`）+ `open_dataset_adapters.py`（`lane_grid_fine`）+ `tools/run_ios_harness_eval.py`（`_lane_pairs`）。
- 端上计时：`ios-vqa-app/VQASee/VQASee/LocalVisionAnalyzer.swift`（`PerceptionFrameTimings`）+ `perception-harness/Sources/PerceptionHarness/main.swift`。
- 真实基线：`server-vqa/eval-baselines/`（`camvid-ios-obstacle`、`camvid-ios-drive`、`camvid-ios-lane-loop`、**`camvid-ios-mc5-walk/-drive`（G）**）。
- G 端上多类+角色派生：`PerceptionConfig.swift`（`PerceptionRole`/`role`）+ `LocalSegmentation.swift`（`SegClass`、多类 sampler、`traversableProbability`、`compiledModelURL` init）+ `LocalVisionAnalyzer.swift`（可注入 segRunner）+ `VQASeeTests.swift`（role/多类单测）。
- G harness 接入：`perception-harness/.../main.swift`（`--seg-model`）+ `LocalPerception.swift`（ARKit `&& os(iOS)` 守卫修复）+ `docs/datasets/harness-config-{walk,drive}.json`。
- G 模型：`deploy/ios/finetune_fast_scnn_camvid_multiclass.py`；`setup_mac.sh` models profile 加 mc5 候选 INFO。
- 测试：`pytest server-vqa/tests` **281 passed**；iOS `** TEST SUCCEEDED **`（92 单测）；harness `swift build` 通过。
