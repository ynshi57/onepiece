# Local Visual Guidance MVP：本地视觉引导与通行路径判断计划

Date: 2026-08-12

## 乔布斯先定方向

用户纠偏成立：VQASee 的主体验不能停留在语音提醒“请放慢、避让、确认”。普通用户需要在屏幕上直观看到 App 给出的通行路径、风险区域和不确定区域，然后语音再补充关键提醒。

本轮目标：先让本地层支持视觉上的通行路径判断和引导 overlay，再谈完整三层架构重构。

产品边界：

- 可以显示“建议关注的通行走廊 / 疑似可通行区域 / 被占用区域 / 不确定区域”。
- 不能显示或播报“可以走 / 可以开 / 安全通过”。
- 视觉引导线是风险辅助，不是导航或驾驶控制。

## 队员 Review

### 罗根：系统 / 性能

态度：有条件同意。

判断：本地视觉引导必须建立在 iPhone 端实时信号上，不能依赖 Qwen。需要先定义 `LocalPathGuidanceSignal`，确保 100～300ms 内输出，并且 overlay 与摄像头预览坐标一致。

风险：YOLO 框坐标、预览 `resizeAspectFill`、设备方向和 JPEG 方向可能不一致，导致引导线错位。

最小可验证改动：先做固定 ROI 的 local path occupancy，不做复杂路径规划。

### 思余：UI / 视觉引导

态度：同意。

判断：普通用户需要先看见“哪里可疑、哪里被挡、哪里不确定”，语音只是补充。引导线必须安静、明确、不像导航承诺。

风险：如果线画得太像“路线”，用户会误解为可以照线走。

最小可验证改动：用半透明风险区域 + 中心通行走廊状态，不用箭头式导航。

### 全麦：模型 / 本地感知

态度：有条件同意。

判断：YOLO 不能直接决定通行路径。需要后处理：室内 vehicle 降权、底部/边缘 person 小框降权、未确认物体统一成 obstacle_candidate。

风险：规则过强会漏掉真实车辆/人；需要诊断报告验证误报下降但召回不明显下降。

最小可验证改动：先基于已有 YOLO bbox + local vision quality + ROI 占用，输出 path status。

## 乔布斯最终裁决

采纳用户方向：优先做视觉引导和通行路径判断。语音不再是主体验，只是确认和补充。

采纳员工限制：第一版不做“可靠导航路线”，做“本地视觉通行路径 MVP”：

- near path 中心区域；
- left/right front 风险区域；
- 物体占用判断；
- 画面质量/不确定区域；
- overlay 可见；
- 语音跟随 overlay 状态。

## 任务卡

### T1：LocalPathGuidanceSignal 数据结构

- 主责：罗根
- 配合：全麦 / 思余
- 改动范围：`ios-vqa-app/VQASee/VQASee/LocalPerception.swift`、`LocalVisionAnalyzer.swift`
- 输出字段：

```swift
struct LocalPathGuidanceSignal {
    var nearPathStatus: PathStatus // clear/caution/blocked/unknown
    var leftFrontStatus: PathStatus
    var rightFrontStatus: PathStatus
    var confidence: Double
    var reasons: [PathReason]
    var guidanceCorridor: CGRect?
    var blockedRegions: [CGRect]
}
```

- 验收：不依赖 Qwen，能从每帧本地分析输出。

### T2：YOLO 后处理与通行区域占用

- 主责：全麦
- 配合：罗根
- 改动范围：`LocalPerception.swift`
- 规则：
  - bbox 与 near path ROI 重叠 → `caution/blocked`。
  - 室内/低速观察中 vehicle 类先降级为 `obstacle_candidate`，除非连续帧或高置信大框。
  - 画面底部/边缘小 person 框降权。
  - 水桶/椅子/箱子等未知物体统一为“物体候选”。
- 验收：用户上传的水桶场景不再显示“车辆引导风险”，而是“右前方物体候选/通行区域被占用候选”。

### T3：视觉引导 overlay

- 主责：思余
- 配合：罗根
- 改动范围：`CameraRiskOverlay.swift`
- 输出：
  - 中心通行走廊半透明区域；
  - blocked/caution/unknown 颜色区分；
  - 左前/右前风险区域；
  - 不使用箭头和“路线导航”样式。
- 验收：普通用户不听语音，也能看懂哪里需要注意。

### T4：语音跟随视觉状态

- 主责：思余 + 罗根
- 配合：全麦
- 改动范围：`PureHelpers.swift`、`StreamingViewModel.swift`
- 规则：
  - overlay 状态变化后才播；
  - 不重复播；
  - 不说“安全通过”；
  - 文案从 overlay 事实生成：如“右前方通行区域有物体，请注意”。

### T5：诊断评估闭环

- 主责：全麦
- 配合：乔布斯 / 罗根 / 思余
- 改动范围：diagnostic manifest + report
- 新增指标：
  - `path_guidance_status`；
  - `blocked_region_count`；
  - `vehicle_downgraded_to_obstacle_candidate`；
  - `overlay_mismatch_label`。
- 验收：诊断报告能评估视觉引导误报/漏报，而不只评估 YOLO 物体类别。

## 联调计划

1. 室内水桶场景：验证水桶不再显示为车辆，右前方风险区域可见。
2. 空走廊：中心通行走廊应为 clear/caution，不出现强风险色。
3. 人站正前方：near path caution/blocked。
4. 椅子/箱子挡路：near path blocked/caution。
5. 低光/遮挡：overlay 显示 unknown，看不清区域。

## 验证命令

后端：

```bash
source .venv/bin/activate && pytest server-vqa/tests
```

iOS：

```bash
bash deploy/ios/test.sh
```

当前 iOS build/test 已知阻塞：`YOLO11nObject.mlmodelc` 资源复制 duplicate `coremldata.bin`，需先修复工程资源打包。

## 2026-08-12 执行记录：视觉引导 overlay MVP

已完成第一版可视效果，先让用户看到：

- 中心近处通行区域：半透明走廊 + 虚线边界 + 中心参考线；
- 左前/右前关注区域：当对应方向有本地优先风险对象时高亮；
- 风险区域 overlay：对本地检测到的人/车/障碍候选画半透明风险框；
- 关注方向 chip：例如“关注正前方 / 左前方 / 右前方”；
- 通行区域状态 chip：`近处通行区域 / 近处需要注意 / 近处疑似被占用`。

改动文件：

- `ios-vqa-app/VQASee/VQASee/CameraRiskOverlay.swift`

当前实现边界：

- 这是视觉引导 MVP，不是最终通行路径算法；
- 仍基于已有 `LocalPerceptionSignal` / YOLO/Apple Vision 输出；
- 暂未实现 `LocalPathGuidanceSignal`、地面分割、深度、ARKit/LiDAR；
- 不显示“可以走/安全通过”，不使用导航箭头。

验证：

- Swift 业务源码 typecheck 通过，仅有既有 Swift 6 concurrency warnings。
- 后端回归通过：`source .venv/bin/activate && pytest server-vqa/tests` → 92 passed。

真机待验证：

1. 室内水桶场景是否显示右前方风险/物体区域；
2. 空走廊中心走廊是否安静、不误导；
3. 人站正前方时中心区域是否变为注意/占用；
4. overlay 是否与 camera preview 对齐。

## 2026-08-12 执行记录：LocalPathGuidanceSignal / Engine

根据用户反馈，撤销“UI 自己画固定假线”的思路，新增真实本地信号层：

- `LocalPathStatus`: `candidateOpen / caution / blocked / unknown`，避免使用 `safe/clear` 这类许可语义。
- `LocalPathGuidanceSignal`: 输出 near/left/right path 状态、关注方向、置信度、候选通行区域、blocked regions、uncertain regions、reasons、depth/segmentation capability。
- `LocalPathGuidanceEngine`: 当前第一版基于 YOLO/Apple Vision bbox + 画面质量 + 固定 ROI 占用判断，显式标记 `depthCapability=.unsupported`、`segmentationCapability=.unsupported`。
- `LocalVisionAnalyzer`: 每帧生成 `perception.pathGuidance`。
- `CameraRiskOverlay`: 不再自行判断 path status，而是消费 `signal.pathGuidance`。
- `DiagnosticCaptureRecorder`: 诊断 metadata 写入 `path_guidance`。
- `diagnostic_report`: 统计 `path_guidance_frames`、near path status、depth/segmentation capability，并在报告中暴露能力缺口。

当前真实能力边界：

- 已支持：bbox 与近处/左前/右前 ROI 的占用判断；低光/遮挡时输出 unknown；blocked/uncertain regions 可视化。
- 未支持：ARKit/LiDAR depth、地面/可通行区域分割、真实路径规划、台阶/落差可靠判断。
- 报告会明确指出 `path_guidance_capability_gap`，防止团队误以为本地层已经具备深度/分割能力。

验证：

- Swift 业务源码 typecheck 通过，仅有既有 Swift 6 concurrency warnings。
- 后端回归通过：`source .venv/bin/activate && pytest server-vqa/tests` → 92 passed。

下一步任务：

1. 修复 iOS 工程中 `YOLO11nObject.mlmodelc` 资源 duplicate output，恢复完整 build/test。
2. 真机验证 overlay 与 camera preview 对齐。
3. 接入 ARKit/LiDAR depth capability 检测；不支持 LiDAR 的设备明确展示/记录 depth unsupported。
4. 调研并评估轻量 floor/traversability segmentation 模型。

## 2026-08-12 用户真机反馈修复：蓝色矩形与报告入口

用户反馈：

- 真机可安装、可打开摄像头、可看到 overlay；
- overlay 很糟糕，只是一个蓝色矩形框；
- 标注页看不到诊断报告；
- 仍然很多 in-flight，不清楚我们做了什么。

事实分析：

- 最新 session `ios-2026-08-12T10-57-02Z-C213E648` 中，`path_guidance` 只有 `candidateOpen + yoloOnly + depthUnsupported + segmentationUnsupported`，没有任何目标检测对象；UI 却把 `guidance_corridor` 矩形大面积画成蓝框，这是错误的视觉呈现。
- latest-frame-wins 已生效，in-flight reason 从旧的 `backend request still in flight` 变为 `backend busy; latest frame retained for next send`；但它不会减少 Qwen 忙的事实，只是避免堆队列/纯丢最新帧。
- 报告入口只在 session 列表，不在 annotate 页，发现性不足。

已修复：

- `CameraRiskOverlay.swift`
  - 如果只有 `candidateOpen + yoloOnly` 且没有 blocked/uncertain regions，不再画大蓝色矩形。
  - 改为非常轻的中心参考线，避免假装已经识别出通行区域。
  - 只有 `caution/blocked/unknown` 或真实 uncertain/blocked regions 时才画明显 overlay。
  - corridor 显示从矩形改为收敛的梯形/走廊形状。
- `diagnostic_api.py`
  - 标注页顶部增加“查看评估报告”入口。

验证：

- Swift 业务源码 typecheck 通过，仅有既有 Swift 6 concurrency warnings。
- 后端回归通过：`source .venv/bin/activate && pytest server-vqa/tests` → 92 passed。
- 最新 session 离线报告可生成：发现 `high_in_flight_ratio`、`path_guidance_capability_gap`、`missing_qwen_raw_output`、`no_ground_truth_labels`。

下一步：

- in-flight 根因仍在：本地 Qwen 慢。需要 Qwen low-frequency policy、stale result ignore、raw output capture。
- overlay 下一步必须接地面/深度/分割能力；否则空场景只显示轻参考，不应显示强引导区域。

## 2026-08-12 执行记录：T1-T5 系统闭环

### T1：stale result ignore

已实现：

- `VqaDisplayResult` 增加 `frameID`。
- `SignalingResponseParser` 从 `frame_id` / `request_id` 解析结果所属帧。
- `StreamingViewModel` 增加 `inFlightFrameID`。
- 如果收到的 VQA 结果 `frameID` 与当前 in-flight frame 不一致，直接忽略，不更新 UI、不播报。

目的：旧 Qwen 结果不能覆盖新画面。

### T2：Qwen low-frequency policy

已调整 `WalkingFrameSendPolicy`：

- `heartbeatMs` 从 6s 提升到 12s。
- `minimumQwenIntervalMs = 8s`。
- 普通画面变化不再立即触发 Qwen；只有达到低频间隔后才复核。
- 本地风险对象/疑似人仍可触发发送，保证安全相关候选不被静默压制。

目的：减少 Qwen 满载时的无意义请求，把 Qwen 从实时主链路降级为低频复核。

### T3：Qwen raw/fused output 诊断记录

已实现：

- `FrameMessageBuilder` 支持发送 `diagnostic_session_id`。
- iOS 诊断上传开启时，普通 frame 请求会带上当前 diagnostic session id。
- 后端 `append_diagnostic_record()` 支持追加 metadata-only 诊断记录，不重复保存图片。
- direct websocket / relay worker 推理完成后追加 `backend_vqa_result` 记录。
- `vqa_service` 在 `diagnostic_metrics` 中加入 `qwen_raw_output_preview`、`schema_name`、`qwen_http_ms` 等信息。
- 诊断报告可统计 `qwen_result_frames`，不再只能看到 sent_to_backend 而缺模型结果。

目的：区分模型看错、JSON 截断、parser bug、后端超时和旧结果。

### T4：Depth capability 暴露

已实现第一步：

- `LocalPathGuidanceSignal` 中显式包含 `depthCapability`。
- 当前标记为 `unsupported`，并写入诊断 manifest。
- 诊断报告新增 `path_guidance_capability_gap`，显示 depth/segmentation 是否未接入。

尚未实现：

- ARKit / LiDAR `sceneDepth` 接入；
- non-LiDAR 设备的 fallback depth；
- 深度坐标与 preview overlay 对齐。

外部路线：Apple ARKit 支持 `sceneDepth` 和 scene reconstruction；是否可用取决于设备/配置能力。

### T5：Segmentation / traversability 路线

已实现第一步：

- `LocalPathGuidanceSignal` 中显式包含 `segmentationCapability`。
- 当前标记为 `unsupported`，并进入诊断报告。

技术路线：

1. 先接 lightweight floor/traversability segmentation Core ML 模型；
2. 输出 floor / obstacle / person / vehicle / unknown mask；
3. 与 depth / YOLO / quality 融合成 path guidance；
4. overlay 只显示模型和传感器共同支持的区域。

外部路线：Core ML 支持 image segmentation；机器人/导航领域通常用 semantic/geometric traversability，而不是单纯目标检测框。

### 验证

- Swift 业务源码 typecheck 通过，仅有既有 Swift 6 concurrency warnings。
- 后端回归通过：`source .venv/bin/activate && pytest server-vqa/tests` → 93 passed。

### 仍需真机验证

1. 打开诊断上传后，普通 frame 请求是否产生 `backend_vqa_result` metadata-only 记录。
2. 评估报告中的 `qwen_result_frames` 是否大于 0。
3. in-flight 比例是否因 low-frequency policy 下降。
4. 旧结果是否不再覆盖最新画面。

## 2026-08-12 追加：Depth capability 精细化

已补充：

- `LocalPathCapability` 从 `unsupported/available` 改为：
  - `unsupported`：设备/配置不支持；
  - `hardwareAvailableButInactive`：设备支持 ARKit sceneDepth/smoothedSceneDepth，但当前 VQASee 使用 AVCaptureSession，未启用 ARSession depth 管线；
  - `active`：未来真正接入 depth 后使用。
- `LocalDepthCapabilityDetector` 使用 ARKit 静态能力检测：
  - `ARWorldTrackingConfiguration.isSupported`
  - `supportsFrameSemantics(.sceneDepth/.smoothedSceneDepth)`
- Overlay 文案区分：
  - `深度不支持`
  - `深度未启用`
- 诊断报告将任何非 `active` 的 depth/segmentation 状态都视为 `path_guidance_capability_gap`。

这一步仍不启动 ARSession，避免和当前 AVCapture 摄像头链路冲突。下一步应设计 ARKit depth 采集管线或双管线切换方案。

## 2026-08-12 追加：YOLO 后处理降误报

已实现 `LocalPerceptionPostProcessor`：

- 小/中等、非宽形、边缘 vehicle 候选降级为 `.obstacle`，避免室内水桶/椅子/物体被直接播报或显示成“车辆/摩托车”。
- 画面底部或边缘的小 person 候选抑制，避免鞋尖/地面边缘被识别成人。
- 大且合理的 person 仍保留，避免完全牺牲召回。

新增测试：

- `testLocalPerceptionPostProcessorDowngradesSmallVehicleToObstacleCandidate`
- `testLocalPerceptionPostProcessorSuppressesBottomEdgeSmallPerson`
- `testLocalPerceptionPostProcessorKeepsLargePerson`

验证：

- Swift 业务源码 typecheck 通过，仅有既有 Swift 6 concurrency warnings。
- 后端回归通过：`source .venv/bin/activate && pytest server-vqa/tests` → 93 passed。

风险：

- 这是保守后处理，不是语义分割/深度。它会把一部分远处或小型真实车辆降级成“障碍候选”，但不会完全隐藏风险区域。后续需用真实诊断报告验证 vehicle 误报下降是否伴随真实车辆召回下降。

## 2026-08-12 追加：ARKit sceneDepth active 管线

已实现真实 depth 采集入口：

- 新增 `ARFrameCaptureProxy`：使用 `ARSessionDelegate` 接收 `ARFrame`。
- 支持设备判断：`ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth/.smoothedSceneDepth)`。
- 支持 sceneDepth 的设备：
  - 使用 `ARCameraPreview` / `ARSCNView` 作为预览；
  - 使用 `ARSession` 捕获 `ARFrame.capturedImage`；
  - 从 `frame.smoothedSceneDepth ?? frame.sceneDepth` 提取 `depthMap`；
  - `LocalVisionAnalyzer` 接收 `depthCues` 和 `depthCapability = .active`；
  - `LocalPathGuidanceEngine` 将 depth 近处障碍方向融合进 near/left/right path status。
- 不支持 sceneDepth 的设备：继续使用原 AVCapture 管线。

已实现 `ARDepthCueExtractor`：

- 在 depth map 的下半部/中部采样；
- 距离小于约 1.15m 的点作为近处障碍候选；
- 统计 left/center/right，输出 `LocalDepthCueSignal`。

安全边界：

- depth 只把路径状态提升到 `caution`，不输出“可通过/安全”；
- 当前未做完整 3D 重建、地面分割或路径规划；
- ARSession 与 AVCapture 不同时运行，避免摄像头资源冲突。

验证：

- Swift 业务源码 typecheck 通过，仅有既有 Swift 6 concurrency warnings。
- 后端回归通过：`source .venv/bin/activate && pytest server-vqa/tests` → 93 passed。

真机验证重点：

1. LiDAR/sceneDepth 设备上 overlay 是否显示 depth active，不再是“深度未启用”；
2. 近处 1m 左右放置物体，是否出现 path caution；
3. 非 LiDAR 设备是否 fallback 到 AVCapture 且不崩溃；
4. 诊断 manifest 中 `path_guidance.depth_capability` 是否为 `active`。

## Segmentation 仍未完成的真实原因

地面/通行区域 segmentation 需要模型资产（例如 floor/traversability segmentation Core ML 模型）和训练/评测样例。当前仓库没有该模型，iOS/Apple Vision 也没有通用“可通行地面分割”内置能力。下一步应：

1. 调研轻量 segmentation 模型并转 Core ML；
2. 定义 mask schema：floor / obstacle / person / vehicle / unknown；
3. 接入 `segmentationCapability = .active`；
4. 用诊断帧评估空走廊、水桶、椅子、台阶场景。

## 2026-08-12 追加：RGB-only Depth Anything runner

针对普通 iPhone 17（无 LiDAR），已加入可选单目深度 runner：

- 新增 `LocalMonocularDepthRunner`。
- 查找 bundle 中的 `DepthAnythingV2SmallF16.mlmodelc`。
- 如果模型存在：
  - 用 Vision/Core ML 跑 RGB-only monocular depth；
  - 从下半部 ROI 的相对深度图提取近处 depth cue；
  - 设置 `depthCapability = .active`；
  - 融合进 `LocalPathGuidanceSignal`。
- 如果模型不存在：
  - 不崩溃；
  - 保持 ARKit/YOLO fallback；
  - 诊断报告继续显示 depth/segmentation capability gap。

新增安装脚本：

```bash
bash deploy/ios/install_depth_anything_v2_small.sh
```

脚本从 Hugging Face `apple/coreml-depth-anything-v2-small` 下载 `DepthAnythingV2SmallF16.mlpackage` 到 iOS app 资源目录。模型目录已加入 `.gitignore`，避免误提交大模型资产。

安全边界：

- Depth Anything 是相对深度，不是安全认证的 metric depth；
- 当前只输出 caution/unknown 线索，不输出“可以走”；
- 如果深度语义方向与模型版本不一致，必须通过诊断报告和真机样例校准。

验证：

- Swift 业务源码 typecheck 通过，仅有既有 Swift 6 concurrency warnings。
- 后端回归通过：`source .venv/bin/activate && pytest server-vqa/tests` → 93 passed。

下一步：

- 用户本机运行安装脚本下载模型；
- Xcode 真机 Run；
- 检查 manifest 中 `path_guidance.depth_capability` 是否从 `unsupported` 变为 `active`；
- 近处水桶/椅子是否触发 path caution。

## 2026-08-12 追加：Traversability segmentation runner 接口

已实现可选 RGB-only traversability segmentation 接口：

- 新增 `LocalTraversabilitySegmentationRunner`。
- 查找 bundle 中的 `VQASeeTraversabilitySegmentation.mlmodelc`。
- 期望模型契约：
  - 单通道输出 mask（pixel buffer 或 MLMultiArray）；
  - 值越高表示越像 floor/traversable；
  - runner 只计算 near/left/right ROI 覆盖率，不输出安全许可。
- 如果模型存在：
  - `segmentationCapability = .active`；
  - coverage 低的 ROI 会把 path status 提升到 caution；
  - manifest 写入 `near_path_traversable_ratio` 等字段。
- 如果模型不存在：
  - 不崩溃；
  - 保持 `segmentationCapability = .unsupported`；
  - 诊断报告继续显示 capability gap。

新增脚本：

```bash
bash deploy/ios/install_traversability_segmentation_model.sh /path/to/model.mlpackage-or-mlmodel
```

该脚本会用 Xcode `coremlcompiler` 编译并安装为：

```text
ios-vqa-app/VQASee/VQASee/VQASeeTraversabilitySegmentation.mlmodelc
```

模型目录已加入 `.gitignore`，避免误提交大模型资产。

当前真实状态：

- Depth Anything V2 Small runner 已接入，但需要先下载模型。
- Traversability segmentation runner 已接入，但仓库没有训练好的 floor/traversability 模型。
- 后续任务从“写接口”转为“选择/训练/转换模型 + 真机评测”。

验证：

- Swift 业务源码 typecheck 通过，仅有既有 Swift 6 concurrency warnings。
- 后端回归通过：`source .venv/bin/activate && pytest server-vqa/tests` → 93 passed。
