# 决策/模型实验：车道线检测走「真·线」路线（UFLDv2 预训练 → Core ML）

- 日期：2026-08-29
- 主责：小马（SOTA 选型）× 全麦（模型/转换/评测）；乔布斯裁决产品方向；罗根真机延迟；思余端上画线
- 触发：用户看到逐帧页车道叠层是**过喷的黄色块**（本帧真值车道 1.26%、预测 5.4%，误报为主），提出「车道线不应该是一条线吗？CamVid 能训车道线吗？」并裁定走 **C（换正道）**。
- 关联：`docs/model-lab/2026-08-27-lane-marking-segmentation.md`（旧像素分割方案，本决策将其降级）、`docs/decisions/2026-08-27-core-capabilities-plan.md`

## 根因（为什么旧方案出不了「线」）

1. **方法错**：旧方案是**二值像素分割**（CamVid `LaneMkgsDriv/NonDriv` 像素 → 128×96 mask）。输出是「车道像素区域」，不是几何线；再粗格化 → 散块。
2. **数据弱**：CamVid 车道像素仅 ~0.67–1.3%（极稀疏细类）、仅 701 帧单一剑桥街景、是**像素标注不是车道线实例**→ 训不出稳、干净、可泛化、分左右车道的「线」。严格像素精度只有 0.33（大量误报）。

> 结论：CamVid **能**做车道像素分割/管线验证，但**不适合**做产品级车道线。要「线」必须换**车道线检测**范式 + **车道线数据集**。

## SOTA 选型（小马）

| 方案 | 输出 | 预训练 | ANE 友好 | 取舍 |
|---|---|---|---|---|
| **UFLDv2（选它）** | 行+列锚点分类 → 车道线**实例点→曲线** | CULane/TuSimple/CurveLanes 均有 | 好（纯卷积+分类头） | 快、可转 Core ML、de-facto 标准 |
| CLRerNet | anchor 回归 | 有 | 差（anchor 管线在 ANE 触发 CPU 回退） | 更准但不适合端上实时 |
| CondLaneNet/LaneFormer | 动态卷积/transformer | 有 | 差 | 复杂、端上难 |

- **模型**：UFLDv2 ResNet18（速度优先；34 更准备选）。
- **权重**：CULane（4 车道、场景多、泛化好，输入 320×1600）优先；TuSimple（高速、干净、320×800）备选。仓库 `cfzd/Ultra-Fast-Lane-Detection-v2` 提供预训练 pth；PINTO_model_zoo 有预转 ONNX。
- **转换**：pth →（trace）→ coremltools `ct.convert` → mlprogram/FP16；输出 `loc_row/loc_col/exist_row/exist_col`。
- **端上解码**：Core ML 出锚点 logits → **Swift 解码成每条车道的点序列 → 画折线/曲线**（真正的「线」，替换像素块叠层）。

## 数据集角色调整（重要）

- **CULane/TuSimple = 车道线训练/真值**（自带车道线实例标注）。
- **CamVid 降级**：不再当车道**唯一真值**；保留作**域验证/可视化对照**（把 UFLDv2 输出的线叠到 CamVid 帧上肉眼看），以及行人/可通行区/障碍物的既有真值来源。
- 旧的像素车道分割器（`VQASeeLaneSegmentation`）：保留离线粗评管线，但**端上显示改用 UFLDv2 的线**；像素 mask 叠层退役或仅调试可见。

## 分阶段任务卡

### C1 · 取预训练 + 转 Core ML + Python 数值验证（全麦）
- clone UFLDv2、取 ResNet18 CULane 预训练权重、trace→Core ML（FP16/mlprogram，显式输入尺寸与 `minimum_deployment_target`）。
- 转换后**先和 PyTorch 原模型同输入比对数值**，再压缩。
- 交付：`deploy/ios/convert_ufldv2_lane_coreml.py` + `~/.cache/vqasee/models/VQASeeLaneUFLDv2.mlpackage`。
- **阻塞风险**：预训练权重在 Google Drive/Baidu，下载可能需 gdown + 放行网络；沙箱内可能失败 → 需显式放行或用户协助。

### C2 · 解码 + CamVid 域验证（全麦）
- 实现锚点→车道点解码（Python 参考实现），把线叠到 CamVid 帧，肉眼确认是「干净的线」而非块；与 CamVid 车道像素做**宽容带**对照（非严格 IoU，因范式不同）。
- 交付：解码函数 + 对照图 + 一页评测记录。

### C3 · 端上落地（思余+罗根）
- Swift 侧：Core ML 跑 UFLDv2 → 解码车道点 → `CameraRiskOverlay` 画折线（替换像素块叠层）。
- bundle `.mlmodelc`、`use_lane_segmentation` 语义切到新模型；真机延迟由罗根签字。
- 交付：Swift 解码 + 画线 + 编译/测试；真机 p50/p95。

## 执行结果（2026-08-29）

### C1 · 取预训练 + 转 Core ML ✅
- 取 UFLDv2 **ResNet18 CULane** 预训练权重（`~/.cache/vqasee/models/ufldv2_culane_res18.pth`，787M 含优化器态，骨干本体小）。
- `deploy/ios/convert_ufldv2_lane_coreml.py`：构建 `parsingNet`（CPU/pretrained=False）→ 载权重 → `UFLDv2Wrapper`（内置 ImageNet 归一化 + tuple 输出）→ trace → `ct.convert`（mlprogram/FP16/iOS16，ImageType scale=1/255）。
- 训练依赖（DALI/tensorboard）在 import 期打桩绕过；`addict/pathspec` 为真实轻量依赖，已装。
- **数值验证（转换后 vs PyTorch 同输入）**：`loc_row max_abs_diff=0.0117`，**argmax 一致率 0.9965**（解码消费的正是 argmax）→ 转换无损可用。
- 产物：`~/.cache/vqasee/models/VQASeeLaneUFLDv2.mlpackage`，I/O 契约 `image(1,3,320,1600)` → `loc_row(1,200,72,4)/loc_col(1,100,81,4)/exist_row(1,2,72,4)/exist_col(1,2,81,4)`。

### C2 · 解码 + CamVid 域验证 ✅（含域差发现）
- `deploy/ios/decode_ufldv2_lanes.py`：镜像 repo `pred2coords`（CULane：`row_anchor=linspace(0.42,1,72)`、`col_anchor=linspace(0,1,81)`，row 车道 idx[1,2]、col 车道 idx[0,3]，局部 softmax 求期望格 → 点 → 折线）。
- 结果：**输出的是干净的车道线折线，不是散块**（主干道帧 col-lane 贴合右车道边界）。
- **量化域差（60 帧 CamVid）**：`hit_rate=0.45`，命中帧平均 69 点。即 CULane 模型在约一半城市帧（主干道/清晰漆线）出线，另一半（复杂城市/褪色线/路口）漏检；个别帧 row-lane 有横向抖动。
- **根因**：CULane 训练域＝高速/主干道、宽画幅（1640×590）；CamVid＝剑桥城市低速、4:3（960×720，resize 到 1600×320 有纵向压缩）。**范式与转换都对，差在训练域。**

### B 实验结果（换 CurveLanes 权重）→ 拒绝
- 扩展 `convert_ufldv2_lane_coreml.py` / `decode_ufldv2_lanes.py` 支持 CurveLanes（独立模型类 `model_curvelanes`、输入 800×1600、`num_col=41`、`num_lanes=10`、`row/col` 全迭代、存在门限 num_cls/4）。取 CurveLanes ResNet18 预训练 → 转 Core ML（`VQASeeLaneUFLDv2_curvelanes.mlpackage`，数值 max_abs_diff=0.045）。
- **60 帧 CamVid：hit_rate=1.00、平均 142 点** —— 但**是过检假象**：可视化显示路中央大量橙/品红/黄幻象车道乱穿，质量**比 CULane 更差**。
- **根因**：出域（城市+4:3 畸变）下，10 车道全迭代 + 松存在门限 → 到处开火。CurveLanes 的高召回在域内是优点，出域变噪声。
- **裁定**：拒绝 CurveLanes。对 VQASee「安全第一、宁可不报不可误报」，CULane 的保守稀疏更合适。**C3 端上落地用 CULane 权重。**
- 对照图：CULane `assets/ufldv2-camvid-lanes.png`（稀疏但干净）vs CurveLanes `assets/ufldv2-curvelanes-camvid.png`（密但幻象）。

### 已裁产品方向
- **C3 端上先上 CULane UFLDv2**：驾驶/主干道出干净真车道线；复杂城市降级为「少报/不报」（不误报，符合安全原则）。
- 城市级车道质量留作后续 **C（域内微调）** 阶段，不在本轮。

### C3 · 端上落地 — 发现严重体积阻塞（待乔布斯/罗根裁决）
把 CULane 模型编译为 `.mlmodelc` 准备 bundle 时发现 **FP16 就 394MB**，无法直接塞进 App。
- **根因**：UFLDv2 的密集分类头 `Linear(2048→91224)`≈186M 参数，是 UFLD 家族固有的重头（官方 issue #155 已记）。骨干（res18）很小，头占几乎全部体积。
- **压缩实验**（`deploy/ios/palettize_ufldv2.py`，CULane，40 帧 CamVid 命中率）：
  | 精度 | 体积 | 命中率 | 备注 |
  |---|---|---|---|
  | FP16 | 394MB | 0.45 | 太大 |
  | int8 linear | 207MB | 0.38 | 快 |
  | uniform 6-bit | **155MB** | 0.38 | 快方法最优 |
  | uniform 4-bit | 103MB | **0.00** | uniform 压垮，需 kmeans |
  | kmeans 4-bit（~93MB 目标） | 未完成 | — | CPU kmeans 对 186M 头太慢，需离线 GPU/时间 |
- **结论**：pipeline 全链路可行（转换/解码/出真线均已验证），但 UFLDv2 CULane 模型**天生重**。快压最优 155MB@6-bit（命中 0.38）；要 ~93MB 需离线 kmeans。

### C3 待用户裁决的两个真实阻塞
1. **体积/精度取舍（乔布斯）**：为「显示用车道线」上 155MB（6-bit）能否接受？还是投入离线 4-bit kmeans（~93MB）/ 换更轻车道检测架构（如 UFLDv1 更小头 / 分割式）/ 暂缓？
2. **真机 ANE 延迟（罗根，需用户 iPhone）**：320×1600 宽输入 + 186M 头在 ANE 上是否实时可用，本机无法测，必须真机签字。
> C3 的 Swift 折线解码器/叠层是纯新增代码、不阻塞，可先写；但**bundle 哪个模型**取决于上面裁决，避免把 394MB/155MB 反复塞进仓库。

### C3 进展（2026-08-30）· Swift 折线解码骨架 ✅
- `LocalLaneSegmentation.swift` 新增 `LanePolyline` + `UFLDv2LaneDecoder`，把 UFLDv2 的 row/col anchor logits 解成归一化折线点，镜像 `deploy/ios/decode_ufldv2_lanes.py` 的 CULane 解码语义（存在性门控 + argmax 周围局部 softmax 期望）。
- 新增 iOS 单测覆盖 row-anchor、col-anchor、存在性不足不造假车道三种行为；先红后绿。
- 验证：`bash deploy/ios/test.sh` 通过；`swift build --package-path ios-vqa-app/perception-harness` 通过。
- 诚实边界：这一步只落地**纯 Swift 解码层**，还没有把 UFLDv2 `.mlmodelc` runner 接入 live/harness，也没有解决 155MB/真机 ANE 延迟裁决。

### C3.1 进展（2026-08-30 09:55）· harness 折线输出与诊断台契约 ✅
- 产品契约修正：诊断台逐帧页优先消费 `lane_polylines` 并渲染为**黄色实线 iPhone 车道折线**；旧 `lane_grid` 只作为“旧像素车道调试层”显示，不再叫产品车道线，避免把黄色块误解为准确车道线。
- `LocalLanePolylineRunner` 接入 Core ML：显式加载 `VQASeeLaneUFLDv2.mlmodelc`，读取 `loc_row/loc_col/exist_row/exist_col`，复用 Swift `UFLDv2LaneDecoder` 输出 `[LanePolyline]`。
- `perception-harness` 新增 `--lane-polyline-model <VQASeeLaneUFLDv2.mlmodelc>`，当模型存在时 JSONL 输出 `lane_polylines`；平台 `_optional_harness_model_flags()` 会从 `~/.cache/vqasee/models` 或 `VQASEE_MODELS_DIR` 自动注入 `--lane-polyline-model`。
- 验证：`python -m pytest server-vqa/tests/test_perception_config_api.py -q -k lane` 通过；`bash deploy/ios/test.sh` 通过；`swift build --package-path ios-vqa-app/perception-harness` 通过。
- 外部雷达：2026 年移动端实时方案仍强调轻量 Core ML/ANE 与端到端延迟；Ultralytics iOS/YOLO26 类路线显示 nano 级 Core ML 可在新 iPhone 实时，但它是通用检测/分割生态，不直接替代车道线实例头。对 VQASee 的裁决：**本轮继续用 UFLDv2 打通闭环与真线可视化，同时把模型体积/真机延迟作为硬门；若 155MB 或 p95 不达标，下一轮做轻量替代/蒸馏实验，不在 UI 上掩盖。**

### C3.2 进展（2026-08-30 10:15）· harness 重跑防重入 ✅
- 现场问题：诊断台文案写“约 10–30 秒”，但 701 帧全量 + 旧 lane mask + UFLDv2 polyline 会跑数分钟；重复点击/刷新还会启动多个 `PerceptionHarness` 写同一个 `/tmp/*-ios-harness.jsonl`，造成 CPU 竞争和缓存污染风险。
- 修复：诊断台新增 harness 运行锁（child pid + manifest + started_at），已有同 manifest 运行中时返回 `already_running`，不启动第二个；同时扫描历史无锁 `PerceptionHarness --manifest ...` 进程，拦住本轮遗留任务。
- UI 文案修正：从“10–30 秒”改成“701 帧全量 + 车道模型可能需要数分钟”，避免用户误判为卡死。
- 验证：`python -m pytest server-vqa/tests/test_perception_config_api.py -q -k 'harness_run_lock or lane'` 通过；`ReadLints` 无新增错误。

### 历史岔路记录（B 已否，保留决策链）
- **A. CULane 权重按现状上端（C3）**：驾驶/主干道场景直接出真车道线，复杂城市降级为空（不误报，安全）。最快落端上。
- **B. 换 CurveLanes 权重**（F1 80、更多弯道/城市曲线，同 I/O 契约、转换脚本可复用）：便宜实验，可能抬升城市命中率。
- **C. 域内微调**（BDD100K-lane / CurveLanes / CamVid 车道转线标注）：城市覆盖最好，需 GPU + 数据工程，最重。
> I/O 契约与 C3 Swift 集成**不因权重更换而变**，A/B/C 任选都不浪费端上工作。

## 诚实边界

- **不在本机 CPU 从零训**（CULane 训练需 GPU-天）；走**预训练→转换**，与 Fast-SCNN Cityscapes 那次一致。
- 端上真机延迟/画线视觉**需用户 iPhone** 才能签字（罗根），本机只能编译+离线验证。
- UFLDv2 在 CULane 上是**车道场景**（机动车道），对行人/人行道边界不直接等价；行人可通行区仍走 mc5 角色分割，两条能力线不混。

## 验收口径

- C1：Core ML 输出与 PyTorch 数值一致（同输入 max abs diff 在容差内）。
- C2：UFLDv2 在 CamVid 帧上输出**成条的车道线**（肉眼 + 与真值车道方向一致），显著优于旧像素块。
- C3：端上画出车道线折线；真机 p50 在延迟预算内（罗根签字）。

## 图 18 接线（2026-09-20 · 离线 CamVid）

团队已裁定「要线不要像素块」，但图 18 一直走 CamVid Lane 涂色关联 + `pavement_edge_walls` 喷边。本轮把 **UFLD 同一套表示**接到产品预览，不新训、不 bundle、不改 App。

- 模块：`server-vqa/app/lane_rails.py`。涂料轨优先 UFLDv2 CULane ResNet18（`~/.cache/vqasee/models/ufldv2_culane_res18.pth`，PyTorch 推理，不再只认 Core ML）。解码走 `deploy/ios/decode_ufldv2_lanes.py`。官方 CULane `exist>num_row/2` 在 CamVid 上把 01TP 的 26 个行锚点整轨扔掉；离线图 18 用 `exist_min=8` 让模型点出线。一条 UFLD 实例若在行上急拐，只保留最长那段，丢掉跨路焊弦。
- 占用一对：优先 **涂料-涂料**（不再让牙子票数压过模型轨）。牙子仍补 UFLD 只出一根的弯道（06R0）。
- 对照帧（乔布斯看过图 18，**未过门**）：
  - `0001TP_008430`：`paint_source=ufld`。GT 教师的 Z 折没了。左黄只有近场一小段（模型在 y≈643 才稳住左轨，更远的 275px 跳点被丢掉）；右黄贴 SEAT 一侧；蓝线夹在两轨之间但很短。CULane 城市场域差，不是再调阈值能补到骑行者的。
  - `0006R0_f00960`：UFLD 只出近处右牙子；左虚线仍靠教师合并（`ufld+gt`）。蓝线沿右车道弯，看起来可用。左白实线模型没认出来。
- C3 仍阻塞：未 bundle、ANE 未测。离线已能跑 pth，不依赖 mlpackage。

## 无标注下一步（2026-09-21 · 乔布斯）

CULane 城市帧未过看图门后，不退回 GT，也不重跑已否的 CurveLanes。改用 TwinLiteNet BDD 预训练（`twinlite_net.py`，RGB only）。

01TP 图 18 / 18c **未过门**（近处可行驶红团，绿线斜杠）。06R0 弯道轨部分像，整体仍未过「各种街景」。本机无 CUDA/MPS，城市实例微调停在 GPU 阻塞。默认占用轨未切换。
