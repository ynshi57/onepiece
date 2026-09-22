# 决策：退役 near/left/right 三区 ROI 框，改用引导线 + 可走区域

- 日期：2026-08-25
- 主责：乔布斯（产品裁决）；实现罗根/思余/全麦；影响面按「变更影响面规则」
- 状态：2026-09-22 拍板深层删除纳入本轮（引擎 / OTA / case）。可见框阶段已完成。现行规定见 [`docs/CURRENT.md`](../CURRENT.md)。

> **2026-09-22：** 历史记录。评测计数已从本页拿掉。

## 背景 / 用户反馈

用户原话：「左，右，近 3 个矩形框和相关功能我觉得可以去掉，没什么用，对于 app 使用者或
app 引导线、可行走区域等并不会消费这三个框吧。」

这是产品裁决问题，不是纯 UI。裁决前先摸清这三个框（`near_path_status` /
`left_front_status` / `right_front_status` / `focus_direction`）的全仓生产者与消费者。

## 影响面地图（关键结论）

- **用户对**：设备端**即时语音**不读这三个状态（走 YOLO `object.direction`）；**引导线**
  `guidance_path` 与**可走区域** `traversable_grid` 都从整张分割 mask 独立算出，**不用**这三个
  ROI 矩形。
- **用户没注意到、但它还占着的两处**：
  1. 设备相机浮层**在画**左右注意区方框 + 近处状态 chips；
  2. 它是闭环评测的**主门禁**（`camvid-ios.json`，`status_accuracy≈0.44` /
     `focus_direction_accuracy≈0.20`）。引导线、区域各有独立 baseline。
- 另外 Qwen 语音间接吃到它的 `backendContext` 文案。
- 案例层（case_store）目前也按三区状态聚类 `risk_miss` / `false_block`。

裁决：**同意退役，P1**。这个离散三态信号准确率仅 0.44/0.20、粗糙重叠、不服务北极星
（「iPhone 感知出绿色可走区域」），留着当主门禁等于用破指标守质量。但它现在还是主门禁 +
设备在画，**必须按迁移做，不能裸删**（否则门禁静默塌陷 / 屏幕提示消失 = 静默失败）。

## 本轮已实现（可验证）

1. **诊断台逐帧视图**（`diagnostic_api._overlay_svg` + 卡片）：删掉三个 ROI 方框、三区状态
   对比表、focus 行；改为「可走线对比」文字判定（漏报路径/误报路径/双方有线/双方无线）。保留
   引导线（主）+ 绿色可走区域叠层 + 物体框 + 失败筛选（漏报/误阻挡仍可跳转复盘）。页面 legend
   同步去掉三框说明。
2. **harness**（`main.swift`）：不再输出 `roi` 字段（驱动那三个框的矩形）。保留 `prediction`
   里的 `*_status`，让案例层暂时仍能聚类（案例改锚为下一步）。`swift build` 通过。
3. **闭环主门禁**（`run_ios_harness_eval.py`）：主门禁改锚到**引导线 + 区域** gate；ROI
   `evaluate_path_guidance` 降为信息展示、不再 block；没有 guidance/region baseline 时**报错退出**
   （EXIT_NO_PREDICTIONS），不静默通过。退役并删除 `eval-baselines/camvid-ios.json`。
4. **设备浮层**（`CameraRiskOverlay.swift`）：删掉左右注意区方框 + 近处/关注方向状态 chips；
   保留引导走廊 + 道路/障碍提示。（app-only 文件，本机无完整 Xcode，未编译验证——见「未验证」。）
5. **测试**：`test_ios_harness_eval` 的两个 ROI 门禁用例改写为新的引导线门禁用例
   （阻断/通过/无 baseline 报错）；`test_perception_config_api` 逐帧视图断言改为「三区表必须消失」。

## 验证

- **pytest**：当时跑绿（具体计数已按 2026-09-22 拍板从决策正文删除）。
- `cd ios-vqa-app/perception-harness && swift build` → **Build complete**（覆盖 `LocalPerception` /
  `LocalVisionAnalyzer` / `main.swift` 真身感知核心）。
- **未验证**：`CameraRiskOverlay.swift` 是纯 App SwiftUI 文件，不在 harness 编译目标里，本机只有
  Command Line Tools，无法编译验证；需在有完整 Xcode 的机器 `bash deploy/ios/test.sh` 复核。

## 变更影响面结论

- 波及模块：诊断台逐帧视图 + eval 面板、harness 输出、闭环门禁、eval-baseline、设备浮层、相关测试。
- 已同步：门禁改锚 + baseline 退役 + harness 输出 + 诊断台三处（overlay/卡片/legend）+ 测试，全部一次改到位并跑绿。
- 覆盖用户消费路径：诊断台是用户真正看框的地方，已去框并 pytest 断言「三区表消失」；设备浮层已改但需 Xcode 复核。
- 消除重复：门禁不再同时依赖三套 baseline 的 ROI 那套。

## 2026-09-22 深层删除（本轮已做）

1. **案例层改锚**：`case_store` 按区域网格聚类 `region_miss` / `region_false_go`。`_frame_flags` 与测试同步。网格可在行根或 `ground_truth`/`prediction` 里。
2. **Swift 引擎字段**：`LocalPathGuidanceSignal` 不再带 `nearPathStatus` / `leftFrontStatus` / `rightFrontStatus` / `focusDirection`。障碍用 `blockedRegions`。`DiagnosticCaptureRecorder` 不再写三区状态。
3. **新 manifest 不再写三区真值**：`open_dataset_adapters` / `path_dataset_import` 只写 `traversable_grid`。磁盘上旧的 701 帧 jsonl 仍可能带旧字段，消费方不再当产品信号。**未重跑 701 帧。**
4. **OTA schema**：`perception_config.py` / `PerceptionConfig.swift` 去掉 ROI 矩形与三区阈值。加载忽略旧 `roi`；bump 带 `roi` 会 400。诊断台配置页不再编辑三框。

未验证：App SwiftUI（`CameraRiskOverlay` / `DiagnosticCaptureRecorder`）需完整 Xcode `bash deploy/ios/test.sh`。`path_roi.py` 仍留在仓库给旧 eval 信息展示，不再是门禁。

## 沉淀去向

- 本决策：本文件。
- 迭代记录：`docs/evolution/2026-08-24-iphone-traversable-region.md` 追加一节。
