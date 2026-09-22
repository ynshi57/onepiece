# 决策：户外可通行区必须区分「谁用」+ 车道线 + 障碍物写入 VQASee 能力

> **2026-09-22：** 历史记录。现行规定见 [`docs/CURRENT.md`](../CURRENT.md)。评测数字已按拍板从决策正文删除。


日期：2026-08-26 · 裁决：乔布斯 · 主责：全麦（模型/数据）+ 罗根（端上/系统）· 配合：思余（UI）、小马（SOTA）
状态：**已承诺（必须实现）· Phase A 进行中**

---

## 1. 用户诉求（原话归因）

> 户外场景的可行驶区域，应该区分人 / 自行车 / 机动车。用户是人、走路或骑车 → 可通行区应偏重**人行道**而不是马路；机动车 → 可行驶区是**马路**。所以户外街景下可通行区要区分**谁用**。车道线很重要，障碍物也很重要，这两个识别能力**必须写入 VQASee 能力**。

这是产品能力方向，不是单点改动。乔布斯裁决：**接受为承诺能力**。理由：把「马路∪人行道」合并成一个"可走"，对走路用户是**信任级缺陷**——会把机动车道说成能走，违反不可妥协原则 2（视觉引导优先）与 8（辅助而非接管）。

## 2. 用户拍板的三个选择（本次确认）

1. **角色范围**：行人 + 车**同时做**（不是行人优先）。
2. **车道线**：直接引入**专用车道检测模型**（UFLD / CLRNet 方向），不满足于分割类。
3. **启动**：**现在就做 Phase A**（离线可验证 + 决策/路线图文档）。

## 3. 现状事实（决策依据，来自代码 + 真实数据）

- **端上模型是「角色盲」二值分割**：Core ML 只出 2 通道（可走/不可走），`ios-vqa-app/VQASee/VQASee/LocalSegmentation.swift` 明确写「>2 类不支持、不敢瞎猜」。→ 端上**分不出**人行道/马路/车道线，必须换多类模型。
- **真值层已有 `walk`/`drive` 钩子但不完整**：`open_dataset_adapters.camvid_traversable_colors` 里 `walk` 仍把**马路算进可走**——正是本缺陷。
- **CamVid 原生有全部类，无需新标注**。从真实标注文件抽出的调色板（8 张标注采样，像素占比）已验证：

| 类 | RGB | 像素% | 用途 |
|---|---|---|---|
| Road | (128,64,128) | 11.4% | 车主可走 / 行人慎行 |
| Sidewalk | (0,0,192) | 8.4% | 行人主可走 |
| LaneMkgsDriv | (128,0,192) | **0.67%** | 车道线（细类，召回难 → 支持专用模型） |
| Car | (64,0,128) | 4.9% | 障碍物 |
| Pedestrian | (64,64,0) | 1.1% | 障碍物 |
| Column_Pole | (192,192,128) | 1.3% | 障碍物 |
| TrafficLight | (0,64,64) | 0.9% | 障碍物/标志 |
| SUVPickupTruck | (64,128,192) | 0.6% | 障碍物 |
| Bicyclist | (0,128,192) | 0.02% | 障碍物 |

注：另有 (192,128,192)≈12% 不在标准 32 类调色板中，暂记为场景背景（不参与主/障碍映射）。
- **障碍物已部分具备**：端上 YOLO 已检测公交车/人/交通灯（诊断图里的蓝框）。缺的是**融进可通行/引导决策 + 评测**。**车道线是真正的空白**（现在被并进"马路"丢掉）。

## 4. 能力 Schema（写入 VQASee）

三件能力，都进能力定义 + 进门禁：

### ① 角色条件可通行区（role-conditioned traversable）
每个角色一套语义：

| 角色 | 主可通行（primary，绿） | 慎行/次选（caution，琥珀） | 禁止（blocked） |
|---|---|---|---|
| 行人/骑行 `walk` | Sidewalk、ParkingBlock、RoadShoulder、人行横道 | Road（可穿越但非首选） | 障碍物、建筑、车道内 |
| 机动车 `drive` | Road、LaneMkgsDriv（可行车道） | RoadShoulder | Sidewalk、障碍物 |

关键变化：**行人的 primary = 人行道，马路降为 caution，不再算 candidateOpen**。引导线优先走 primary。

### ② 车道线（lane lines）
从 CamVid LaneMkgsDriv/NonDriv 抽真值几何；端上用**专用车道检测模型**（用户选定）。对车模式定义可行车道，对行人辅助判断"马路边界/何处穿越"。

### ③ 障碍物（obstacles）
CamVid 动态+物理阻挡类（Car/Pedestrian/Bicyclist/Truck_Bus/SUV/Pole/Cone/Fence…）；端上复用 YOLO，**硬融进可通行与引导**（占用即 block/caution），并纳入评测。

## 5. 四角色 review

- **乔布斯（方向）**：承诺能力；行人+车都做（用户裁决），但按 profile 组织，避免语义互相污染。
- **全麦（有条件同意）**：数据现成，可立即在真值层做角色条件 + 车道线 + 障碍物真值。端上需二值→多类分割，Core ML 契约变、Swift sampler 要改。车道线细类召回难，用户已选专用模型，需先验证其 Core ML 转换可行性（UFLD 结构较友好）。复用 Phase 2 微调管线。
- **罗根（有条件同意）**：输出契约 2→N 通道，波及 sampler/grid/harness/manifest schema/eval，**影响面大，必须分阶段**；障碍物复用 YOLO 不重复造；角色作为 `PerceptionConfig` 运行时字段走 OTA；多类分割 + 专用车道模型的端上延迟/内存需实测预算。
- **思余（同意）**：人行道主绿、马路琥珀"可穿越非首选"、车道线画线、障碍物沿用框；保持安静分层，不堆砌。
- **小马（同意 + 情报）**：端侧实时多类分割候选 PP-LiteSeg / SegFormer-B0；车道线 UFLD（row-anchor，轻、易转 Core ML）优先于 CLRNet（更重）。落 `docs/tech-radar/` 待补。

## 6. 分阶段路线图（每步可验证）

- **Phase A（离线，不重训，进行中）**：真值/评测层落地角色条件语义（行人:人行道主、马路 caution）+ 从 CamVid 抽车道线真值 + 障碍物真值入 manifest + 新增 4 指标：**人行道召回 / 马路被误当首选率 / 车道线覆盖 / 障碍物重叠**。→ 用 701 帧量化「当前二值模型对行人有多离谱」。全离线可验证。
- **Phase B（模型）**：CamVid 微调**多类** Fast-SCNN（road/sidewalk/lane）+ 专用车道模型（UFLD）转 Core ML；改 N 类契约 + Swift 按 config 角色选类；转换→harness→重评。
- **Phase C（端上融合+OTA）**：YOLO 障碍物硬融进引导 + 画车道线 + 角色走 OTA + UI 打磨。
- **Phase D（小马 SOTA 复核后增补，见 `docs/tech-radar/2026-08-26-pedestrian-traversability-sota.md`）**：
  - **数据视角转向**：从 CamVid 车载引擎盖视角 → iPhone 胸高**行人视角**帧 + pedestrian-view 数据集（SENSATION-DS 9 类 / SANPO）。
  - **深度融合**：iPhone 原生 ARKit/LiDAR 深度 + 语义，补 curb/台阶/立体障碍（纯语义漏；对标 CVPR 2026 WalkGPT）。
  - **SAM2 自动标注**：Grounded-SAM2 / SAM2Auto 按 prompt 造角色条件真值，突破 CamVid 固定类，蒸馏到端上小模型。
  - **指标对齐**：A3 的「马路被误当首选率」= SOTA 的 **Road-as-Sidewalk Error Rate**（false-safe 安全指标）。

## 6.5 无原生深度（ARKit/LiDAR 不可用）约束下的裁决与任务卡

**约束**：当前设备不支持 ARKit/LiDAR，原生深度不可用。澄清：**深度 ≠ ARKit**——单目深度是从 RGB 学出来的模型（Depth Anything V3 / 端侧 ZipDepth/METER 蒸馏到 Core ML），不依赖传感器。所以深度作为「可选、后置、离线先验证」保留，不进 MVP。

### 小马推荐的 SOTA 方案（RGB-only，不依赖任何深度传感器）

**「RGB 安全导向多类分割（端上核心）+ Road-as-Sidewalk 安全门禁 + SAM2 离线自动标注造角色数据」** —— 即 SENSATION-DS（arXiv 2607.21137, 2026）已在手机端**无深度传感器**实证 ~7 FPS 可跑的路线。

组成：
- **端上核心**：多类分割（road / sidewalk / lane / hazard），角色策略选 primary。候选架构 DeepLabV3+-MobileNetV3（Road-as-Sidewalk 错误最低 0.079）/ SegFormer-B0 / 现有 Fast-SCNN 扩类。
- **安全门禁**：Road-as-Sidewalk Error Rate 作 false-safe 指标 + 保守化（宁可标 caution 不标 primary）。
- **数据**：Grounded-SAM2 / SAM2Auto 离线按 prompt（sidewalk/crosswalk/curb）自动标注**真机行人视角**帧，蒸馏到端上小模型；突破 CamVid 车载视角 + 固定类。
- **障碍物**：复用现有 YOLO，融进 guidance。
- **车道线**：用户选定的专用模型（UFLD）staged 到车模式；行人 MVP 先用分割 lane 类做「马路边界/穿越点」提示。
- **深度**：无 ARKit → 单目深度模型作**可选第二信号，离线先验证 curb 价值再决定上端**，不进 MVP；短期 curb 用「分割 + 几何启发（近处底部行 + 消失点）」兜底并显式标不确定。

### 小马评估

| 维度 | 判断 |
|---|---|
| 可行性 | **高**：RGB-only，SENSATION-DS 已在手机端实证可部署 |
| 契合度 | **高**：正是 VQASee 行人可通行场景 |
| 风险 | 细类（lane）召回难；CamVid 车载视角域差；无深度→curb/台阶弱 |
| 缓解 | SAM2 扩行人视角数据；curb 用几何启发兜底 + 单目深度 spike 后置 |
| 成本 | 复用现有 Fast-SCNN 微调管线 + 现有 YOLO；SAM2 为离线造数工具 |
| SOTA 对标 | SENSATION-DS（同题）、WalkGPT（CVPR2026，可选深度形态） |

### 乔布斯裁决（产品方案）

1. **承诺能力不变**：户外角色条件可通行 + 车道线 + 障碍物。
2. **MVP = RGB 多类角色感知分割 + Road-as-Sidewalk 安全门禁**（不依赖深度）。
3. **深度降级为后续可选实验**（单目模型，不依赖 ARKit，不进 MVP）；curb 短期几何启发兜底 + 显式标不确定（不可妥协原则 4/8）。
4. **角色**：GT/评测双角色（行人+车），**端上先发行人**（北极星）。
5. **车道线**：专用模型 staged 到车模式；行人 MVP 先用分割 lane 类提示马路边界/穿越点。
6. **数据**：立即启动 SAM2 离线自动标注 spike（真机行人帧），验证是否建管线。
7. **验收红线**：Road-as-Sidewalk Error Rate 为安全指标；把马路标成 primary 的帧占比不得超过阈值（阈值待 A3 首轮数据定）。

### 开发任务卡

| 编号 | 主责 | 交付物 | 验收标准 |
|---|---|---|---|
| T1 | 全麦 | ✅ A2/A3：角色条件真值（新旧并存）+ Road-as-Sidewalk Error Rate 指标（离线，701 帧） | **已完成**：R-a-S=0.807（700/701 帧误引马路）已量化，见 §7.5；`--role-manifest` 已接入 eval+门禁；24 passed。剩 A4（总览页接 role 段）见 §9 |
| T2 | 全麦 | Phase B：多类分割（Fast-SCNN 扩类 / DeepLabV3+-MobileNetV3）CamVid 微调 → Core ML N 类 | R-a-S 错误率较二值下降；region false_go=0 不回归 |
| T3 | 全麦 | SAM2 自动标注 spike：10–20 真机行人帧 prompt sidewalk/crosswalk/curb | 多数掩码可用 → 出「建/不建管线」决策 |
| T4 | 罗根 | 端上多类契约（2→N 通道）改造 sampler/grid/harness/manifest schema + 延迟预算 | 端到端 p95 延迟预算达标；无静默失败 |
| T4·lane | 全麦/罗根 | ✅ **端上车道线通道（已实现，harness 验证 2026-08-27）**：`LaneGrid`+`LocalLaneSegmentationRunner`(ch1 lane)+analyzer 注入+harness `--lane-model`/emit `lane_grid`/延迟预算 | **已完成**：`swift build` 通过；30 帧 lane_grid 30/30，覆盖均值 2.9%；**车道线延迟 mean 6.6ms/p95 7.0ms**（Mac 单前向，端上独立模型可承受）。未做：iOS App 打包+引导消费（无 Xcode）、闭环评分、阈值走 OTA。见 `docs/model-lab/2026-08-27-lane-marking-segmentation.md` §6–7 |
| T5 | 罗根 | `role` 作为 `PerceptionConfig` OTA 字段 | 可远程切 walk/drive，端上生效 |
| T6 | 思余 | 角色叠加呈现：人行道主绿 / 马路琥珀 caution / 障碍框 / 车道线；curb 不确定显式标注 | 一眼可辨主可走 vs 慎行；不确定不伪装确定 |
| T7 | 小马 | 单目深度 Core ML 可行性 + curb 价值离线评估（不依赖 ARKit） | 出「深度是否值得上端」结论 + 数据 |
| T8 | 小马 | pedestrian-view 数据集（SENSATION-DS / SANPO）许可与可得性调研 | 出可用数据清单或替代方案 |

### 跨角色接口（单一真源在本决策文档）

- **manifest schema**：`role` / `primary` / `caution` / `lane` / `obstacle` 掩码字段（全麦定义，罗根 harness、思余 UI 消费）。
- **Core ML N 类契约**：类索引 → 语义映射（全麦出，罗根 Swift sampler 消费）。
- **`PerceptionConfig.role` OTA 字段**（罗根定义，全麦/思余消费）。
- **Road-as-Sidewalk Error Rate 指标口径**（全麦定义，进门禁 + 总览页）。

联调计划：T1 先行（离线基线）→ T2/T4 并行（模型 + 端上契约）→ T5/T6 集成 → T3/T7/T8 spike 并行喂 roadmap。

## 7. 变更影响面（blast radius）

- **语义变更（primary 的定义）是跨模块共享的**：真值生成（`open_dataset_adapters`）、引导线派生（`centerline_from_mask`）、区域 grid、harness 预测、eval、门禁、总览页定级——全部依赖"什么算可走"。改语义必须同步所有消费方，且**旧 baseline 口径失效**（角色语义变了，不能直接和旧 region/guidance baseline 比），需重建 baseline 并在 evolution 标注"口径变更非退步"。
- **不 silently 覆盖旧 manifest**：Phase A 生成**角色专属**的真值/评测产物，与旧二值 GT 并存以便量化差距。
- **端上契约（2→N 通道）**：Phase B 才动，属另一次影响面，届时单独评估。

## 7.5 T1 首轮离线结果（2026-08-26，701 帧真实 CamVid 标注）

角色真值已落地：`docs/datasets/camvid-manifest-walk.jsonl`（人行道=primary、马路=caution、车道线=lane、障碍物=obstacle 四层来自 CamVid mark）。GT 覆盖：人行道 681/701、车道线 671/701、障碍物 701/701——**车道线和人行道信息在标注里一直存在**，缺的是设备端的识别通道。

用 `run_ios_harness_eval.py --role-manifest` 在 701 帧上评估**当前二值设备预测**（`traversable_grid`，仍是"马路+人行道混一块"）：

| 指标 | 数值 | 含义 |
| --- | --- | --- |
| `mean_road_as_primary_rate` | **0.807** | 设备判为"可走"的格子里 **80.7% 其实是马路**（Road-as-Sidewalk，安全红线） |
| `road_as_primary_frames` | **700 / 701** | 几乎每一帧都会把行人引到马路上 |
| `mean_primary_recall` | 0.639 | 只覆盖 64% 的真实人行道 |
| `mean_lane_coverage` | 0.718 | 72% 的车道线落在设备"可走区"内——设备**没有车道线通道**，直接从车道线上走过 |
| `mean_obstacle_overlap_rate` | 0.004（5 帧） | 障碍物规避尚可 |

**结论（回应"车道线、马路边界没识别出来"）**：这不是引导线算法的问题，而是**设备端根本没有这两条通道**——当前 Fast-SCNN 二值输出既不区分人行道/马路边界，也没有车道线类。80.7% 的马路误当人行道，就是"没有边界识别"的量化证据。修复路径是 **T2（多类分割 CamVid 微调 → Core ML N 类）+ 专用车道模型**，本轮已把口径、真值、指标、基线（`server-vqa/eval-baselines/camvid-ios-role.json`）备好，供 T2 前后对比与门禁。

## 8. 验证

- Phase A：`pytest server-vqa/tests` 全绿；新增角色策略 + 4 指标的单测（含行人场景马路应为 caution 的断言）；用 701 帧真实标注跑出指标基线。
- T1 离线四指标：`run_ios_harness_eval.py --role-manifest docs/datasets/camvid-manifest-walk.jsonl`，见 §7.5；`pytest server-vqa/tests/test_ios_harness_eval.py test_role_metrics.py test_dataset_manifest_tools.py` 24 passed。
- 端到端：新指标必须出现在能力总览页，能一眼看出"当前模型对行人的可走区有多不合适"。（总览页接入 role 段属 T1 尾/A4，未完成，见 §9）

## 9. 待办 / 风险

- ✅ **A4（总览页接 role 段）已完成**：能力总览页 `/diagnostics/ui` 新增「行人角色 · 可走区谁说了算」红线卡片，直显"马路误当人行道 81%（700/701 帧）· 人行道召回 64% · 车道线覆盖 72%"，并指向 T2 修复路径。role 基线缺失时卡片自动省略（向后兼容）。测试：`test_api.py` 39 passed（含 `test_overview_surfaces_role_road_as_sidewalk_redline`）。
- ✅ **车道线识别（离线能力）已实现（2026-08-27）**：UFLD/CLRNet 需 CULane 类**折线实例**标注，CamVid 只有逐像素颜色 → 无法训 UFLD。故落地为**专用二值车道线分割**（尊重"dedicated"，独立模型不埋多类头），CamVid 车道线颜色作真值。留出集 Seq05V 尾段 60 帧（防泄漏）：**容差召回 0.982 / 容差精度 0.851 / 严格 IoU 0.331 / 漏车道帧 0**。产物：`deploy/ios/finetune_lane_segmenter_camvid.py`、`app/lane_metrics.py`(+7 单测)、权重 `~/.cache/vqasee/models/lane_seg_camvid.pth`、Core ML `VQASeeLaneSegmentation.mlpackage`、基线 `server-vqa/eval-baselines/camvid-ios-lane.json`。详见 `docs/model-lab/2026-08-27-lane-marking-segmentation.md`。**未做**：端上通道/延迟(T4)、UI 叠加(T6)、车道**几何/折线/自车道**(Phase D，需引入 CULane 类标注)。
- (192,128,192) 未知类占比 12%，需查清是否为本 mirror 的某类（不影响主/障碍映射，但影响"背景"完整性）。
- 多类分割 + 专用车道模型端上延迟预算待罗根实测。
