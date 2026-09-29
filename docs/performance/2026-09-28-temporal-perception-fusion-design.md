# 连续视频时序融合设计

- 日期：2026-09-28
- 状态：**A+B+C 已实现代码与纯 Swift 语法检查**。A 使用单槽 mailbox、结果年龄与会话代际隔离；B 有对象确认/短预测/风险滞后；C 对新鲜且相邻一致的车道线与引导路径做低通。尚未完成真机视频逐帧验收，数值仍是实验门槛，不是真机签字结果。
- 主责：罗根（管线、延迟、可观测性）；配合：全麦（模型/OOD）、思余（呈现）、乔布斯（验收）
- 范围：iPhone 上 TwinLite 车道/道路、YOLO 障碍、引导线的连续输出；Qwen 仅作为低频语义层
- 实现指针：`TemporalPerceptionFusion.swift`、`CameraCapture.swift`（单槽 mailbox）、`auto_docs/temporal-fusion-ab-impl.md`

## 产品裁决

这项工作是 P0：它帮助户外行走/骑行/驾驶辅助用户在移动时看到稳定、最新且会诚实撤销的风险边界和障碍提示。它**不是**将 VQASee 改为自动驾驶，也不是以滤波掩盖模型错误。

室内是道路模型的域外场景。融合器必须收起不可信道路叠层，而不是让连续多帧的错误结果看起来更可信。

## 已知事实与缺口

当前 `FrameCaptureProxy` / `ARFrameCaptureProxy` 的预览与本地感知已经分离：本地感知在独立 queue、以 `minLocalPerceptionInterval = 0.20s` 节流、且只有一个进行中的任务。这避免了预览被前向推理阻塞，但忙时直接丢弃新帧。`LocalVisionAnalyzer` 每次返回独立的 `LocalPerceptionSignal`，而 `CameraRiskOverlay` 直接呈现它。因此最新帧不保证会被下一轮本地推理使用，且框、mask、车道和路径没有跨帧状态。

远程 Qwen 已有单请求 `latest-frame-wins`、前一结果上下文与超时释放。这只适用于语义路径，不能解决本地叠层的时序稳定。

Mac harness 的首次 8 帧测量为 YOLO p95 4.9ms、TwinLite/分割 p95 154.8ms、车道 p95 7.5ms、总 p95 169.3ms。它不是 iPhone 测量，但表明主分割是预算主导项。真机 p50/p95 和结果年龄尚不存在。

## 目标与非目标

### 目标

1. 相机预览持续运行；任何模型、融合或网络延迟不得冻结预览。
2. UI 只消费标有拍摄时间的融合快照；正常户外本地链路的**结果年龄**（显示时刻 - 图像拍摄时刻）p95 目标为 `<= 350ms`。
3. 近距离/进入引导走廊的新障碍与风险升级，p95 首次呈现目标 `<= 250ms`；不等待确认帧。
4. 稳定目标框、车道/路径不因单帧漏检或轻噪声跳变；丢失、OOD 与快速相机运动时明确撤销。
5. 不保留 FIFO 帧积压；本地和 Qwen 的队列深度都不大于一。

### 非目标

- 不把室内支持伪装成滤波问题；室内可走区域须等独立路由、数据和模型。
- 不把 Qwen 变为逐帧实时检测器。
- 不在这一阶段做多对象重识别、全局 SLAM、录像持久化或云端视频流。
- 不因平滑降低障碍、路沿、台阶、车辆和人等风险首次可见性。

## 架构

```text
AVCapture / ARFrame（30–60 Hz）
  ├─ preview layer ───────────────────────────────────→ 连续相机画面
  └─ LocalFrameMailbox（容量 1，最新覆盖旧）
       └─ LocalPerceptionWorker（最多一个 in-flight，3–8 Hz 自适应）
            └─ LocalVisionAnalyzer（YOLO + TwinLite + 可选 lane/depth）
                 └─ TemporalPerceptionFusion（串行、无 UI）
                      ├─ FusedSnapshot（带 captureTime / age / reason）
                      └─ telemetry / diagnostic

CameraRiskOverlay（每个显示帧读取最新 FusedSnapshot）

局部风险升级 / 显著变化 / 用户问题 / 心跳
  └─ Qwen latest-frame-wins（容量 1）→ 文字、语音；不得写几何叠层
```

### 两条时钟

- **显示时钟**：由 Camera preview 和 SwiftUI/Canvas 驱动，30–60 Hz。它只读取最近一次 `FusedSnapshot`，可做 120–200ms 视觉淡入/淡出，绝不做推理等待。
- **感知时钟**：在 worker 空闲时从 mailbox 取最新样本。它的频率由真机耗时、热状态和结果年龄决定，不由相机 FPS 决定。

### 最新帧 mailbox

以容量为 1 的 mailbox 取代“busy 时直接丢当前帧”的行为：

1. `captureOutput` 每次把包含像素缓冲深拷贝、`captureTime`、方向、AR pose、frame quality 的 `CapturedFrame` 写入 mailbox；若已有未消费样本，用新样本替换它并递增 `replacedBeforeInference`。
2. worker 空闲后 atomically 取出 mailbox 最新项，运行一次本地感知。
3. 推理完成后立即检查 mailbox；若有新项则直接处理，绝不回放旧项。
4. `FusedSnapshot` 发布前计算 `now - captureTime`。超过 `maxUsableResultAge`（初始 500ms）的正常几何结果不得覆盖画面；记录 `staleResultSuppressed`。风险候选仍可记录，但不能作为当前空间位置。

这样做允许当前推理完成，但不会让下一个计算浪费在过时画面上。它不是取消正在运行的 Core ML 请求，因为取消的资源/稳定性收益不确定。

## 数据契约

新增 `TemporalPerceptionFusion.swift`，由唯一串行 actor/queue 拥有可变状态。`LocalVisionAnalyzer` 保持产生原始推理；融合器的输入输出必须有明确时间语义。

```swift
struct CapturedFrame: Sendable {
    let pixelBuffer: CVPixelBuffer
    let captureTime: CFTimeInterval
    let orientation: CGImagePropertyOrientation
    let cameraPose: simd_float4x4?       // AR 可用时；无 pose 是合法降级
    let frameQuality: FrameQuality
}

struct RawPerceptionFrame: Sendable {
    let captured: CapturedFrame
    let signal: LocalPerceptionSignal
    let timings: PerceptionFrameTimings
    let completedAt: CFTimeInterval
}

struct FusedSnapshot: Sendable, Equatable {
    let captureTime: CFTimeInterval
    let publishedAt: CFTimeInterval
    let resultAgeMs: Double
    let objects: [FusedObject]
    let guidancePath: GuidancePath?
    let traversableGrid: TraversableGrid?
    let roadAvailability: RoadOverlayAvailability
    let risk: FusedRisk
    let provenance: FusionProvenance
}
```

`FusedSnapshot` 是 `CameraRiskOverlay` 的唯一输入。原 `LocalPerceptionSignal` 继续用于原始模型、诊断和远程 frame context，不应在 UI 中与融合数据混用。

`FusionProvenance` 至少包括：原始帧 ID / capture time、每一类信号的原始置信度、track ID、接受或拒绝原因、是否使用预测/重投影、age、OOD 状态。这样每个用户看到的叠层都可被回放解释。

## 四类时序算法

### 1. YOLO 障碍：关联、预测、确认、立即升级

每个 track：`id, kind, box, velocity, confidence, hits, misses, lastMeasuredAt, lastUpdatedAt, riskClass`。

- 匹配：同类别优先、预测框与当前框的 IoU（初始阈值 `>= 0.30`）加中心距离门；先匹配高置信度候选，避免一个测量关联多个 track。
- 位置：轻量 alpha-beta filter 或常速度 Kalman。只有测量可信且时间间隔合理时更新；未匹配时仅在 `<= 250ms` 内预测显示，之后撤销，不让旧框跟着用户走。
- 置信度：`c_t = α × measured + (1-α) × c_(t-1)`；`α` 初始 0.55，必须按帧间隔做时间归一化，不能把 5Hz 参数套到 2Hz。
- 可见状态：普通物体从 tentative 到 confirmed 需要两次匹配；confirmed 连续两次 miss 或 300ms 未测量后 hidden。
- **安全绕过**：框进入近场（底部区域 / 大面积）、进入当前引导走廊、`hasPriorityRiskObject`、类别升级或风险等级上升，第一帧就以 `candidateRisk` 显示和通知；后续仍进入 track。平滑永不阻止首报。

初版不应承诺“物理世界 identity”。它只用于消灭几百毫秒尺度的显示跳变；遮挡/快速相机转动时宁可失去 track，也不连接错误目标。

### 2. 车道与引导线：几何状态，而非像素残影

对每条 `LanePolyline` / `GuidancePath.primary` 先正规化到屏幕坐标，再按下部锚点、heading、曲线采样点平均距离匹配。不要按数组 index 假设相同车道。

- 对确认线取等弧长采样的 12–20 个点；对 x（或 Frenet 偏移）做随 `Δt` 归一的 EMA。若有 UFLD，优先平滑其几何线，而非 TwinLite mask 边界。
- 目标版的新线连续两帧匹配才作为稳定线；原 confirmed 线可在一次漏检时保持最多 250ms，使用虚线/降低透明度。超过窗口、急转导致 heading 差过大、线相交、或 road state 非 active 时立刻撤销。**当前 C 最小实现刻意不做确认/漏检保持**：只有相邻新鲜结果均存在且逐点位移小才插值；缺失或跳变立即使用当前结果。这一保守边界要等真实视频看图通过后，才考虑放宽为状态机。
- 引导路径只从**同一帧的融合后可信车道/可走区和障碍**生成。禁止把已过期 path 与新对象混合生成。
- 快速旋转、设备姿态不连续、曲线残差超过阈值时 reset lane tracks。错误地平滑到旧道路比短暂不画线更危险。

### 3. 可走区域：当前帧优先，重投影失败即不用历史

mask/grid 不能简单和前一帧逐像素 EMA：人转向、移动或镜头抖动时像素已不是同一个地面位置。

初版优先级：

1. 当前 TwinLite `traversableGrid` 为基础，并保留其原始置信度。
2. 仅在 `roadAvailability == active`、结果 age 小、相机角速度低、AR pose 有效且重投影误差低时，把前一份 grid/mask warp 到当前坐标，作为弱先验。
3. 以当前预测为主（初始 current 0.70 / warped history 0.30）；每格独立融合，并以形态学小孔填充限制在已确认道路连通区。
4. 无 pose 时只对网格级置信度做短时滞后（不能搬动像素区域）。遇到帧间变换大或 scene change 高则不使用历史。
5. 一旦 OOD、过期、低置信、或几何冲突，清空/收起道路区，并用 `uncertain` 状态而不是继续显示绿/红路面。

将来 ARKit depth 可用时可做 ground-plane 逆透视投影；这是第二阶段实验，不是初版依赖。必须对手机相机内参、姿态时间同步、滚动快门误差和人手持抖动做真机验证。

### 4. 风险状态和语音：上升快，下降慢，解释去重

`RiskLatch` 单独于几何融合维护：

- `low → medium/high`：立刻发布、立刻允许本地触觉/短语音。
- `high → lower`：连续 2 个原始感知帧或 500ms 无对应风险才降级。
- 同一对象/方向/风险在 cooldown 内不重复播报；但距离逼近、风险升档、出现新 object ID 时绕过 cooldown。
- 本地安全信号是源；Qwen 只能确认/补充，不得降低本地风险等级或删除本地框。

## OOD / 室内状态机

```text
unknown → outdoorCandidate → outdoorActive
       ↘ uncertain → indoorOrOOD
```

- `outdoorCandidate`：首次成功道路结果，不立即画强引导。
- `outdoorActive`：连续证据通过后展示车道、可走区、路径。
- `uncertain`：模型置信度、结构一致性、亮度/运动或候选室内分类器提示异常。停止更新路径；旧几何在很短过渡后撤销。
- `indoorOrOOD`：立刻关闭 TwinLite 车道/可走区/道路引导，只保留 YOLO 的泛障碍和“户外看路模型不适用于当前环境”的恢复路径。

进入 `uncertain` 可用多帧确认避免抖动；进入 `indoorOrOOD` 的安全降级允许一帧触发。返回户外必须连续恢复，避免在门口反复闪烁。

初始特征应为现有 road structural confidence、lane/path 残差、可走区连通性、连续帧一致性和 AR motion，不应先把 YOLO 检测到椅子当成唯一室内判断。是否加入专用室内/OOD 分类器必须由真实室内/室外视频评测决定。

## UI 行为

`CameraRiskOverlay` 渲染融合快照：

- confirmed 障碍为实线框；`candidateRisk` 以更克制的风险提示显示，但不要伪装为已确认类别。
- 预测状态不应显示为实测框；最多 250ms 低透明度/虚线，且不单独触发新的语音。
- path / lane 正常状态可使用 120–200ms 的 opacity/position transition；风险区域不可通过慢动画延迟出现。
- `uncertain` 收起道路引导并给一个安静、用户可理解的状态提示；不展示 IoU、帧率、EMA 等工程词。
- `indoorOrOOD` 明示“当前环境不使用户外道路引导”，保留相机与障碍提醒，避免用户误以为相机坏了。

## 性能控制

worker 以滑动 2 秒的真机 p95 调整目标频率：

| local inference p95 | 初始节奏 | 动作 |
| --- | --- | --- |
| `<= 125ms` | 8Hz | 完整本地链路；仍只取最新帧 |
| `125–200ms` | 5Hz | 默认目标 |
| `200–330ms` | 3Hz | 保持 YOLO；按风险和模型能力降重型几何频率 |
| `> 330ms` / 热/内存异常 | 安全降级 | 停止生成道路引导，不伪造路径；保留近期障碍和失败状态 |

具体“降级哪一个模型”必须由真实 iPhone 的每段耗时决定。已知 Mac 证据表明优先优化/降频 TwinLite/分割，不能以为 YOLO 是主瓶颈。

## 可观测性与诊断数据

在 `DiagnosticCaptureRecorder` 和可选本地 ring trace 增加：

- capture、enqueue、inferenceStart、inferenceEnd、fuse、publish 的单调时间；
- mailbox `replacedBeforeInference`、`staleResultSuppressed`、worker busy 时间；
- 每段模型耗时（已有 `PerceptionFrameTimings`）、结果年龄、温控/内存降级原因；
- object track 创建/匹配/丢失、lane/path 接受/撤销、mask 是否重投影；
- risk 首现到发布延迟、语音去重/绕过原因；
- OOD 状态转移和触发证据。

诊断录制继续遵守用户显式开启、最少持久化原则。默认只记聚合指标，视频/图像必须由用户选择录制。

## 实施任务卡与接口

### A. 可观测性和 mailbox（罗根，P0）

- 改动：`CameraCapture.swift`、`LocalVisionAnalyzer.swift`、`DiagnosticCaptureRecorder.swift`。
- 新增 `CapturedFrame` / 单槽 mailbox；不改变相机 preview 生命周期。
- 验收：连续回放中无 FIFO 增长；每个 snapshot 可计算 age；所有替换和过期抑制可见。

### B. 对象融合与风险闸门（罗根 + 全麦，P0）

- 新增 `TemporalPerceptionFusion.swift` 与纯 Swift 单元测试。
- 改动：`LocalPerception.swift` 增加非 UI 的原始置信度/时间数据；`StreamingViewModel.swift` 消费融合快照并保留 Qwen 分离。
- 验收：普通框稳定、首帧高风险不被确认门槛延迟、旧预测不会在年龄窗口后残留。

### C. 车道 / 路径融合（全麦 + 思余，P0）

- 接入现有 `GuidancePath`、`LanePolyline`；先只做几何轨迹平滑和撤销，不重训模型。
- 实现边界：只在结果年龄不超过 500ms、同一语义车道/路径的逐点最大位移小于 0.13/0.12 时，按 60% 最新观测插值；几何缺失、跳变或过期时直接撤销/直出，不保留旧道路画面。
- 验收：用真实连续户外片段逐帧导出 overlay。人工看图门：不出现 S 钩、漂移到相反车道、已无道路仍保留引导；不满足就继续调算法或撤销策略。

### D. 可走区时序与 OOD（全麦 + 罗根，P1）

- 初版 grid 置信度融合和 stale guard；AR pose 重投影作为 feature flag 实验。
- 新增 `RoadOverlayAvailability`，让 UI 明确区分 active / uncertain / unavailable / indoorOrOOD。
- 验收：咖啡店、商场和户外门口连续片段不产生道路引导假象；户外评测 IoU、障碍召回不回退。

### E. UI 与闭环平台（思余 + 乔布斯，P1）

- 改动：`CameraRiskOverlay.swift`、用户状态文案、perception harness 视频序列模式。
- 验收：显示“正在更新”而非清空叠层；不可用路径可理解；golden overlay 和指标报告可由诊断台生成。

## 测试与发布门

### 单元测试

- object association：交叉、遮挡、类别冲突、快速位移、一个测量不匹配多个 track；
- object lifecycle：tentative / confirmed / predicted / expired 与高风险立即绕过；
- lane association：车道顺序交换、漏一帧、急转、残差超界；
- result-age：过期结果不得替换新 snapshot；mailbox 永不多于一个；
- risk latch：升级立即、降级防抖、同风险去重；
- OOD：进入立即安全降级、返回需要连续证据。

### 视频序列回放

为 `perception-harness` 增加按 capture timestamps 回放序列而非独立图片的模式，输出每帧 raw / fused / overlay PNG 和 JSONL。至少建立：稳定直路、缓慢转向、快速转向、遮挡、行人/车辆进入、低光、咖啡店、商场入口 8 类脱敏片段。

自动指标：

- snapshot age p50 / p95 / p99；
- mailbox replacement、过期抑制、队列深度；
- 每类框 center jitter / area jitter（仅持续 confirmed track）；
- lane/path 连续性、heading jump、无效保留时间；
- 风险首现延迟、漏报与重复播报；
- 室内错误道路叠层帧数；
- 室外 CamVid/公开集 IoU、角色区分和障碍 recall 的回归。

### 真机发布门

发布前必须在至少一台目标 iPhone 上实测，而非用 Mac harness 代替：

1. 预览无可感知卡顿；
2. 本地结果年龄 p95、风险首现 p95 和内存/热状态有数据；
3. 连续视频逐帧看图通过 C 阶段视觉门；
4. 室内/OOD 不显示假道路引导；
5. 失败、过期、模型不可用都有用户可见恢复状态；
6. Qwen 慢、超时或断连不影响本地叠层和即时风险提示。

## 影响面、风险与回滚

影响面：相机采集调度、原始感知输出契约、SwiftUI overlay、语音风险 gate、诊断 schema、harness 与评测资产。必须在同一改动同步更新所有 `LocalPerceptionSignal` 的 UI 消费方；不得让某入口继续画 raw signal 而另一个入口画 fused signal。

主要风险：错误关联导致框粘到别的物体、会话切换让旧任务回写、pose 误差导致 mask 重投影错误、确认门压低首报、平滑看起来“顺”但隐藏域外失效、融合 CPU 反而恶化结果年龄。应对是：风险绕过且剥离旧道路几何、短预测 TTL、采集会话代际令牌、严格 stale guard、feature flags、每类融合独立可关闭、raw/fused 同时诊断输出。

回滚：默认保留 `TemporalFusionConfig.enabled`，可在 OTA 配置关闭几何融合并退回 raw local signal；安全风险 gate 与错误可见状态不能关闭。若 OOD 误判偏高，宁可收起道路引导，不回到室内假车道。

## 角色审查结论

- 乔布斯：同意，P0；验收不能是“画面比较顺”，而是用户不被陈旧或伪造引导误导。
- 罗根：有条件同意；先做 mailbox、时间戳、结果年龄和真机数据，否则不应承诺频率。
- 思余：同意；叠层过渡需安静，但“不确定/不可用”应明确、简短，且不能用动画延后风险。
- 全麦：有条件同意；融合可处理瞬时噪声，不能补齐 TwinLite 的室内域偏移、角色可走区与车道模型缺口；这些仍需数据/模型评测。

## 知识沉淀

本文件沉淀时序融合架构；实现后应补充 `docs/performance/` 的真机报告、`docs/model-lab/` 的室内/OOD样例与评测、`docs/ui-lab/` 的 overlay 视觉验收截图，并把失败片段纳入回归资产。
