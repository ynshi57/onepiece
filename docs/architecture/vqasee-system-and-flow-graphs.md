# VQASee 架构图与场景流程图

Date: 2026-08-07

> 产品定位：VQASee 是语音优先的视觉风险辅助系统，服务行人、骑行者、驾驶者、低视力用户和注意力可能分散的人。它提醒风险、边界、物体和不确定性；不承诺“可以走/可以开”，不替代用户观察或驾驶责任。

## 1. 总体系统架构

```mermaid
graph TD
    U[用户/行人/骑行者/驾驶者] --> IOS[iPhone VQASee App]

    subgraph IOS_APP[iPhone 端]
        CAM[AVFoundation Camera]
        PREVIEW[全屏 CameraPreview]
        LOCAL[LocalVisionAnalyzer]
        YOLO[YOLO11nObject Core ML]
        VISION[Apple Vision<br/>人形检测/OCR]
        SIGNAL[LocalPerceptionSignal]
        OVERLAY[CameraRiskOverlay<br/>框/标签/边界 cue]
        VOICE[VoiceFeedbackPolicy<br/>即时语音/抑制原因]
        REC[DiagnosticCaptureRecorder<br/>本机诊断录制]
        WS[WebSocket Transport]
    end

    subgraph MAC[Mac 本地后端]
        API[FastAPI / WebSocket]
        PROMPT[Prompt + Context Builder]
        QWEN[Qwen / llama-server]
        FUSION[Fusion + Response Schema]
        OFFLINE[Offline Diagnostic Analyzer]
        ROAD[RoadBoundary Prototype<br/>未来: YOLOPv2/HybridNets/Depth]
    end

    CAM --> PREVIEW
    CAM --> LOCAL
    LOCAL --> VISION
    LOCAL --> YOLO
    VISION --> SIGNAL
    YOLO --> SIGNAL
    SIGNAL --> OVERLAY
    SIGNAL --> VOICE
    SIGNAL --> REC
    SIGNAL --> WS
    WS --> API
    API --> PROMPT
    PROMPT --> QWEN
    QWEN --> FUSION
    FUSION --> WS
    WS --> IOS
    REC --> OFFLINE
    OFFLINE --> ROAD
```

### 当前已经实现

- iPhone 摄像头取帧；
- Apple Vision 人形检测和 OCR；
- `YOLO11nObject.mlmodelc` 本地 Core ML 目标检测；
- `LocalPerceptionSignal` 统一本地感知输出；
- `CameraRiskOverlay` 显示检测框、标签、疑似道路 cue；
- `VoiceFeedbackPolicy` 和 `WalkingImmediateFeedbackPolicy`；
- WebSocket 到 Mac 后端；
- Qwen 本地 VQA；
- 诊断录制和 Mac 离线分析脚本。

### 当前未真正解决

- 精准道路边界；
- 准确人行横道 / 车道线 / 路沿；
- 台阶 / 坑洞 / 落差可靠识别；
- 真正可行走/可行驶轨迹预测；
- overlay 与 `resizeAspectFill` 的严格几何校准。

---

## 2. iPhone 实时帧处理流程

```mermaid
graph TD
    A[摄像头输出 CMSampleBuffer] --> B[FrameCaptureProxy 节流]
    B --> C[LocalVisionAnalyzer]
    C --> D[亮度/遮挡/变化分数]
    C --> E[Apple Vision 人形检测]
    C --> F[YOLO11nObject Core ML]
    E --> G[LocalPerceptionSignal]
    F --> G
    D --> G

    G --> H[更新 CameraRiskOverlay]
    G --> I[WalkingFrameSendPolicy]
    G --> J[WalkingImmediateFeedbackPolicy]
    G --> K{诊断录制开启?}

    J --> L{需要本地即时播报?}
    L -->|是| M[语音: 可能有车辆/人/边界 请放慢]
    L -->|否| N[记录未播原因]

    I --> O{是否发送后端?}
    O -->|skip| P[不发 Qwen<br/>记录 skipped_before_backend]
    O -->|send| Q[JPEG 编码]
    Q --> R[OCRRecognition 视模式运行]
    R --> S[WebSocket sendFrame]

    K -->|是| T[保存 JPEG + manifest.jsonl]
    K -->|否| U[不保存]
```

### 关键设计

- 本地感知先于 Qwen；
- 本地语音不等 Qwen；
- 后端慢时，用户仍可能先听到“可能有车/人，请放慢”；
- 诊断录制只在用户手动开启时保存到本机 Documents；
- 固定蓝色假轨迹线已下线，避免误导。

---

## 3. WebSocket / 后端 Qwen 流程

```mermaid
graph TD
    A[iPhone sendFrame] --> B[FastAPI /ws/signaling]
    B --> C[解析 mode/question/context/OCR]
    C --> D[resolve_prompt]
    D --> E[build_contextual_prompt]
    E --> F[run_vqa_from_frame]

    F --> G{QWEN_API_BASE_URL?}
    G -->|无| H[heuristic fallback]
    G -->|有| I[OpenAI-compatible /v1/chat/completions]
    I --> J[llama-server / Qwen]
    J --> K[parse JSON / normalize]
    K --> L[fuse_vqa_result]
    H --> L
    L --> M[vqa_result]
    M --> N[iPhone handleTransportEvent]
    N --> O[更新文字/UI/语音策略]
```

### 后端关键点

- walking / surroundings 可使用 fast response schema；
- continuous modes 默认不再发送 previous image，避免双图 prefill；
- Qwen 输出异常会显式暴露，不静默失败；
- `latency_ms` 是后端模型处理时间，iOS 端会组合端到端延迟。

---

## 4. UI Overlay 流程

```mermaid
graph TD
    A[LocalPerceptionSignal] --> B[objects]
    A --> C[roadCues]
    A --> D[depthCues]

    B --> E[objectOverlay]
    E --> F[框 + 标签 + 置信度]

    C --> G[roadCueOverlay]
    G --> H[疑似人行横道横线]
    G --> I[疑似车道线/路沿边界线]

    D --> J[cueChips]
    C --> J
    J --> K[顶部 chip: 疑似边界/人行横道/落差]
```

### UI 语义

| UI 元素 | 当前含义 | 是否可靠 |
|---|---|---|
| 橙色框 | YOLO 识别车辆/自行车等 | 初步可用，需真机评估 |
| 黄色框 | 人/动物 | 初步可用，需评估 |
| 红色框 | 障碍物/台阶/坑洞/路沿 | 通道已通，模型能力未验证 |
| 白色横线 | 疑似人行横道 | 通道已通，模型能力未验证 |
| 黄色边界线 | 疑似路沿/车道线 | 通道已通，模型能力未验证 |
| 蓝色固定线 | 已移除 | 之前是误导性 debug 线 |

---

## 5. 语音反馈流程

```mermaid
graph TD
    A[LocalPerceptionSignal] --> B[WalkingImmediateFeedbackPolicy]
    B --> C{是否需要即时播报?}
    C -->|是| D[VoiceFeedbackDecision.speak]
    C -->|否| E[VoiceFeedbackDecision.silent]

    F[Qwen vqa_result] --> G[VoiceFeedbackPolicy]
    G --> H{是否播报?}
    H -->|首次/风险升高/重大变化/提问| I[AVSpeechSynthesizer]
    H -->|无重要变化/重复/关闭| J[记录 suppressed reason]

    D --> I
    I --> K[voiceStatusText]
    J --> K
```

### 语音原则

- 第一条视觉结果必须播；
- 本地风险可先播，不等 Qwen；
- 重复内容会抑制；
- 设置页显示播报状态；
- 不说“可以走/可以开/前方安全”。

---

## 6. 各场景处理逻辑

### 6.1 走路模式 walking

```mermaid
graph TD
    A[walking mode] --> B[本地感知每帧运行]
    B --> C{本地风险?}
    C -->|人/车/自行车/障碍| D[即时语音提醒]
    C -->|无风险| E{变化/心跳/首帧?}
    E -->|是| F[发送 Qwen]
    E -->|否| G[跳过后端]
    F --> H[Qwen 输出风险/建议]
    H --> I[VoiceFeedbackPolicy 决定是否播]
    B --> J[Overlay 显示框/cue]
```

处理重点：

- 优先本地风险提醒；
- Qwen 做确认和解释；
- 不把蓝色假线当路线；
- 后续需 RoadBoundary prototype 生成真实边界/走廊。

### 6.2 看周围 surroundings

```mermaid
graph TD
    A[surroundings mode] --> B[较低频连续观察]
    B --> C[本地感知 + Qwen]
    C --> D[场景/左中右/重要变化]
    D --> E{重要变化?}
    E -->|是| F[语音播报]
    E -->|否| G[保持安静]
```

处理重点：

- 空间布局；
- 重要变化；
- 避免重复播报。

### 6.3 读文字 readText

```mermaid
graph TD
    A[readText mode] --> B[Apple Vision OCR]
    B --> C{读到文字?}
    C -->|是| D[本地直接展示/播报]
    C -->|否| E[提示靠近/对准/增加光线]
    D --> F[可选 Qwen 结合图像确认]
```

处理重点：

- OCR 优先；
- 不让 Qwen 猜“这是一张纸”；
- 文字模式可单次识别。

### 6.4 详细看 detail

```mermaid
graph TD
    A[detail mode] --> B[高分辨率 JPEG]
    B --> C[可发送 previous frame]
    C --> D[Qwen 详细描述]
    D --> E[场景/空间/文字/风险/建议]
```

处理重点：

- 允许更慢；
- 追求完整描述；
- 不适合行走实时场景。

### 6.5 语音提问 voice question

```mermaid
graph TD
    A[按住说话] --> B[SpeechRecognitionController]
    B --> C[VoiceQuestionIntent]
    C --> D{问题类型}
    D -->|视觉问题| E[下一帧强制发送]
    D -->|读文字| F[切 readText + OCR]
    D -->|非视觉| G[提示问 Siri/系统]
    E --> H[Qwen 回答]
    F --> I[本地 OCR 回答]
```

处理重点：

- 非视觉问题不进 VQA；
- 语音问题单次回答，不粘到后续每帧。

---

## 7. 诊断录制与离线分析闭环

```mermaid
graph TD
    A[Settings 打开 保存诊断帧] --> B[DiagnosticCaptureRecorder]
    B --> C[保存 frame-0001.jpg]
    B --> D[写 manifest.jsonl]
    D --> E[objects / bbox / confidence]
    D --> F[event: sent/skipped/in-flight]
    D --> G[local_vision / road_cues / depth_cues]

    C --> H[导出到 Mac]
    D --> H
    H --> I[analyze_diagnostic_capture.py]
    I --> J[统计误检/漏检/方向]
    I --> K{--run-qwen?}
    K -->|是| L[离线重跑 Qwen]
    K -->|否| M[只统计本地感知]
    J --> N[模型改进 / 评估集]
    L --> N
```

### 诊断目录格式

```text
VQASeeDiagnostics/session-.../
  metadata.json
  manifest.jsonl
  frame-0001.jpg
  frame-0002.jpg
```

Mac 分析命令：

```bash
python server-vqa/tools/analyze_diagnostic_capture.py /path/to/session
```

重跑 Qwen：

```bash
QWEN_API_BASE_URL=http://127.0.0.1:11435 \
QWEN_MODEL=qwen2.5vl:3b \
python server-vqa/tools/analyze_diagnostic_capture.py /path/to/session --run-qwen --limit 20
```

---

## 8. RoadBoundaryPerception 未来原型

```mermaid
graph TD
    A[录制视频帧] --> B[Mac RoadBoundary Service]
    B --> C[YOLOPv2 / HybridNets]
    B --> D[Semantic Segmentation]
    B --> E[Depth / LiDAR / Depth Anything]

    C --> F[lane lines / drivable area]
    D --> G[road / sidewalk / curb / crosswalk]
    E --> H[drop / stairs / pothole]

    F --> I[RoadBoundaryResult]
    G --> I
    H --> I
    I --> J[normalized image coordinates]
    J --> K[offline overlay renderer]
    K --> L[人工评估]
    L --> M{稳定且准确?}
    M -->|否| N[继续采集/训练/调参]
    M -->|是| O[考虑 Core ML / iPhone 化]
```

### RoadBoundaryResult 草案

```json
{
  "frame_id": "frame-0001",
  "coordinate_space": "normalized_image",
  "road_boundary": {
    "left_polyline": [[0.12, 0.88], [0.32, 0.38]],
    "right_polyline": [[0.88, 0.88], [0.68, 0.38]],
    "confidence": 0.72
  },
  "crosswalk": {
    "polygons": [],
    "confidence": 0.61
  },
  "lane_markings": [],
  "guidance_corridor": {
    "centerline": [[0.5, 0.9], [0.5, 0.4]],
    "status": "caution"
  }
}
```

---

## 9. 当前主要风险

| 风险 | 当前状态 | 下一步 |
|---|---|---|
| 水桶误检成车 | 已观察到 | 诊断录制 + 离线分析 + 模型评估 |
| 固定蓝线误导 | 已移除默认显示 | 未来只显示模型驱动路线/边界 |
| 道路边界不准 | 未真正解决 | Mac RoadBoundary prototype |
| Qwen 延迟高 | 仍存在 | 本地即时反馈 + ring buffer/latest-frame-wins |
| Overlay 坐标偏差 | 需真机验证 | 建立 preview transform 校准 |
| 台阶/坑洞 | 需要深度 | LiDAR / Depth Anything 实验 |

---

## 10. 乔布斯最终边界

VQASee 可以逐步成为“视觉风险辅助系统”，但只有在模型、时序、坐标对齐和评估集都验证后，才能显示真正的通行/行驶辅助路线。

当前版本允许：

- 显示检测框；
- 显示疑似边界/人行横道 cue；
- 本地即时提醒；
- 诊断录制；
- 离线分析。

当前版本不允许：

- 显示固定假轨迹并暗示可走；
- 说“可以走/可以开”；
- 把 YOLO11n 的 COCO 误检当道路理解能力；
- 让 Qwen 负责像素级道路边界。
