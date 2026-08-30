# T2 · 多类分割方案（把 81% 马路误当人行道压下去）— 待 review，未执行

- 日期：2026-08-26
- 主责：全麦（模型/训练/转换/评测）；配合：罗根（端上延迟预算，T4）、思余（叠加呈现，T6）
- 上游依据：`docs/decisions/2026-08-26-role-conditioned-traversability.md`
- 状态：**方案待裁决，代码未动、未训练**

## 0. 为什么做（T1 已量化的缺口）

T1 在 701 帧真实 CamVid 上量化：当前设备是二值「可走区」（马路+人行道混一块），
按行人角色真值衡量 **马路误当人行道 80.7%（700/701 帧）**、人行道召回 64%、车道线覆盖
72% 但**没有车道线通道**。根因不是引导线算法，是**模型只输出 2 类，分不出人行道/马路
边界与车道线**。T2 让设备输出多类，从源头把这条红线压下去。

## 1. 类别定义（N=5 —— 2026-08-26 裁决：先不做 crosswalk）

设备端分割输出 **5 类**，全部能由 CamVid 训出有效监督，能同时派生 walk / drive 两角色层，无需回读 RGB：

| id | 类 | CamVid 来源（已在 `open_dataset_adapters` 定义） | walk 角色 | drive 角色 |
|---|---|---|---|---|
| 0 | background/其它 | 其余颜色 | 非可走 | 非可走 |
| 1 | road 马路 | `CAMVID_ROAD_COLORS`（去掉车道线） | caution 慎行 | primary 可走 |
| 2 | sidewalk 人行道 | `CAMVID_SIDEWALK_COLORS` | **primary 可走** | obstacle |
| 3 | lane 车道线 | `CAMVID_LANE_COLORS` | caution | primary |
| 4 | obstacle 障碍 | `CAMVID_OBSTACLE_COLORS`（车/人/杆/栏…） | obstacle | obstacle |

派生规则（端上从 argmax 类图算，纯逻辑，无模型二次推理）：
- walk：`primary = (cls==2)`，`caution = (cls==1)|(cls==3)`，`obstacle = (cls==4)`
- drive：`primary = (cls==1)|(cls==3)`，`obstacle = (cls==4)|(cls==2)`

像素优先级（同像素多义时）：obstacle(4) > lane(3) > sidewalk(2) > road(1) > bg(0)。

### 1.1 crosswalk 延后（不阻塞本轮）

CamVid 的 32 类调色板**没有独立 crosswalk 类**（斑马线像素被涂成 Road / 车道线），所以监督式分割
在 CamVid 上**训不出 crosswalk**——这是数据标签缺失，不是模型能力问题。裁决：**本轮先不做 crosswalk**，
待引入带 crosswalk 标注的数据集（如 Mapillary Vistas，需注册+许可，本地无法自动下载）后，作为独立一轮
（T2b）扩到 N=6。届时 5→6 的端上契约与 UI 分色一并随重训/重发一次性改，不在本轮承担。

## 2. 架构选择：先 Fast-SCNN 换 N 类头（不换骨干）

| 方案 | 优点 | 风险 | 裁决 |
|---|---|---|---|
| **A. Fast-SCNN 2→5 类头**（暖启动 Phase 2 CamVid 权重） | 改动最小；骨干复用；端上延迟≈不变（末层 1×1 conv 128→5 vs 128→2）；转换/微调管线现成 | 车道线/人行道是小类，可能学不透 | **本轮选 A** |
| B. DeepLabV3+-MobileNetV3 | 精度更高 | 更大更慢；新转换路径；端上延迟需重估 | A 不达标再升级 |

理由（小步可验证 + 罗根延迟预算）：A 只改分割头最后一层通道数和 loss，骨干与前处理
（512×512 scaleFill + ImageNet norm）完全不变，端上算力增量可忽略；先用最小改动验证
「多类能否把 R-a-S 压下去」，不达标再上 B。

## 3. 训练数据与 loss

- **多类真值图**：新增 `_camvid_class_map(arr) -> np.ndarray[H,W]`（int 0–4），按 §1 优先级
  从 CamVid RGB 派生。复用现成 `_camvid_color_mask`，不新读文件。
- **防泄漏切分**：沿用 Phase 2 的**按序列**留出（`--test-seq`），整段序列永不训练，所有指标
  在留出集上报，并与暖启动前对比（apples-to-apples）。
- **类不均衡**：sidewalk 稀疏、lane ~0.67% 像素。用**类加权 CE + Tversky/Dice**，对 sidewalk、
  lane 提高召回权重；不追求 lane 完美（专用车道模型是后续 Phase D）。

## 4. 导出与端上契约（T2 只到"可离线验证"，端上切换属 T4）

- 导出 Core ML `[1,5,H,W]` logits（新 mlpackage，不覆盖旧 2 通道）。
- **T2 自验证不依赖端上改造**：用离线路径跑导出模型 → argmax → 派生 walk 角色层 →
  用 `run_ios_harness_eval.py --role-manifest` 对 `camvid-manifest-walk.jsonl` 打分，
  与 **T1 已存基线 `camvid-ios-role.json` 做前后对比**。
- 端上 2→N 通道消费（sampler/grid/harness/schema 改造 + 真机延迟实测）是 **T4**，单独一次影响面。

## 5. 验收红线（用 T1 基线做门禁，walk + drive 双角色）

留出序列上，暖启动前 vs 后。**裁决：walk 与 drive 都设硬门禁**（drive 需另生成
`camvid-manifest-drive.jsonl` 角色真值 + drive role 基线）：

walk 角色（现状基线 `camvid-ios-role.json`）：

| 指标 | 现状 | T2 目标 | 硬门禁 |
|---|---|---|---|
| `mean_road_as_primary_rate`（马路误当人行道） | 0.807 | **显著下降**，力争 < 0.20 红线 | 不得上升 |
| `mean_primary_recall`（人行道召回） | 0.639 | 上升 | 不得下降 |
| `region_false_go_frames`（冒进） | 0 | 0 | **必须仍为 0** |
| `mean_obstacle_overlap_rate` | 0.004 | ≤ 现状 | 不得恶化 |

drive 角色（本轮先建 drive role 基线，再前后对比）：

| 指标 | 目标 | 硬门禁 |
|---|---|---|
| `mean_road_as_primary_rate`（对 drive=人行道误当马路可走率） | 下降 | 不得上升 |
| `mean_primary_recall`（马路召回） | 上升 | 不得下降 |
| 冒进帧 | 0 | 必须仍为 0 |

任一硬门禁破 → 回滚，不发布（对齐"安全第一 / 不允许静默失败"）。
crosswalk 本轮不做（见 §1.1），T2b 补数据后再纳入。

## 6. 四角色 review

- **乔布斯（产品）**：这是让"区分人走人行道、车走马路"从 demo 变真能力的核心一步；验收锚定
  R-a-S 红线，用户在总览页能一眼看到前后对比。同意先做 A，最小闭环。
- **罗根（系统/性能）**：只改分割头末层 + 每类下采样 grid（5 张 vs 1 张，廉价），端上 p95 预算
  基本不变；但**真机实测归 T4**，T2 不擅自改端上契约。有条件同意：A 方案不得引入新前处理。
- **思余（UI）**：多类后总览页/逐帧页要能分色显示人行道(绿)/马路(琥珀)/车道线/障碍；属 T6，
  T2 先保证离线可视化能画出 argmax 类图即可。
- **全麦（模型）**：主责。承认 lane 是薄类、sidewalk 稀疏，用加权 loss + 只求"够用"；lane 完美
  留给专用模型（Phase D）。防泄漏按序列切分，杜绝自欺。

## 7. 变更影响面（blast radius）

- **新增不覆盖**：多类真值图、N 类权重、新 mlpackage 与旧 2 通道并存；旧 region/guidance
  基线口径不变（仍按二值），role 基线是独立口径。
- **端上契约不动**：T2 不碰 `LocalSegmentation.swift` 的 2 通道解析；那是 T4。
- **单一真源**：类别↔颜色映射只在 `open_dataset_adapters` 定义，训练/评测/端上派生共用。

## 8. 交付物与验证命令

1. `_camvid_class_map` + 单测（各类优先级、稀疏类不被吞）。
2. `deploy/ios/finetune_fast_scnn_camvid.py` 加 `--num-classes 5` 分支（或新脚本），复用切分/前处理。
3. 训练 → 导出 `[1,5,H,W]` mlpackage。
4. 离线派生 walk 角色层 → `run_ios_harness_eval.py --role-manifest` 前后对比，出 §5 表真实数字。
5. `setup_mac.sh` models profile 若新增权重来源/转换步骤 → 同步（环境依赖规则）。
6. 结果沉淀本 model-lab 文档 + 更新决策文档 T2 行。

验证：`pytest server-vqa/tests`（新类图单测）+ 留出集 role 指标前后对比（非 mock，真实 CamVid）。

## 8.5 执行记录（2026-08-26）

- **`camvid_class_map`（N=5 真值图）** 已实现于 `open_dataset_adapters.py`（优先级 obstacle>lane>
  sidewalk>road>bg，lane 不被 road 吞），单测 `test_camvid_class_map_assigns_five_classes_with_priority` 绿。
- **多类训练脚本** `deploy/ios/finetune_fast_scnn_camvid_multiclass.py`（新文件，2 类路径不动）：
  暖启动 5 类头（Cityscapes 19→5 分组均值），加权 CE（sidewalk×3、lane×4）+ 召回向 Tversky，
  按序列留出 + gap 防泄漏；模型选择指标 = 留出集 `primary_recall − road_as_primary_rate`。
- **Smoke（24 训练/14 留出，1 epoch）已跑通**：`road_as_primary` 0.256→0.075、误引帧 8→1，
  人行道召回保持 ~0.76 → 机制方向正确。
- **drive 角色真值** `docs/datasets/camvid-manifest-drive.jsonl`（701 帧）已生成，供 drive 门禁。
- **全量训练**（632 训练/60 留出 Seq05V 尾，20 epoch）进行中；@init 留出：road_as_primary=0.037、
  primary_recall=0.25（暖启动头保守，待训练提召回）。

### setup_mac 同步判断（罗根）

本轮**不改 `setup_mac.sh`**：models profile 安装的是**已上线的 2 类**设备模型；5 类模型仍在实验+门禁
阶段，端上未采用（2→N 切换属 T4）。按「环境与依赖同步规则」，安装面在 5 类真正上端（T4）时再同步，
现在改属过早。已在此显式记录，非遗漏。

## 9. 裁决记录（2026-08-26）

1. ✅ **类粒度 = N=5**（先不做 crosswalk）。CamVid 无 crosswalk 标注，crosswalk 延后到 T2b（引入
   Mapillary Vistas 等带 crosswalk 标注的数据集后）扩到 N=6（§1.1）。
2. ✅ **walk + drive 双角色都设硬门禁**（§5）；需新建 drive role 基线。
3. ✅ **lane best-effort**，正式车道能力等专用模型 Phase D。
