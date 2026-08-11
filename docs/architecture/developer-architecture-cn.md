# VQASee 研发架构图

Date: 2026-08-07

面向受众：iOS、后端、模型服务、测试与部署研发人员。

## 1. 总体架构图

```mermaid
flowchart LR
    user["用户<br/>行走 / 周围观察 / 读文字 / 详细描述"] --> iphone["iPhone VQASee App"]

    subgraph ios["iOS 前端"]
        ui["体验与交互层<br/>SwiftUI<br/>ContentView / AssistanceScreen<br/>ModeBar / AnswerPanel / SettingsView"]
        capture["设备采集层<br/>AVFoundation CameraCapture<br/>CoreLocation GPS<br/>SpeechRecognition<br/>Apple Vision OCR"]
        localVision["本地感知层<br/>LocalVisionAnalyzer<br/>Apple Vision 人形检测<br/>YOLO11nObject Core ML<br/>LocalPerceptionSignal"]
        feedback["反馈策略层<br/>CameraRiskOverlay<br/>VoiceFeedbackPolicy / SpeechGate<br/>AVSpeechSynthesizer"]
        transport["传输与发现层<br/>Networking WebSocket<br/>BonjourDiscovery<br/>Direct WS / Relay WS"]
        recorder["诊断闭环<br/>DiagnosticCaptureRecorder<br/>JPEG + manifest.jsonl"]
    end

    subgraph mac["Mac 本地后端"]
        api["FastAPI 服务入口<br/>main.py<br/>/health<br/>/ws/signaling"]
        discovery["本地发现服务<br/>discovery.py<br/>Bonjour _vqasee._tcp"]
        signaling["实时请求处理<br/>signaling.py<br/>frame / stop / vqa_result"]
        prompt["Prompt 与上下文<br/>prompts.py<br/>scene_context.py"]
        vqa["模型适配层<br/>vqa_service.py<br/>OpenAI-compatible request<br/>JSON normalize / fallback"]
        fusion["结果融合层<br/>fusion.py<br/>GPS / latency / schema fallback"]
        worker["跨网络 Worker<br/>worker_client.py<br/>连接 Relay 接收任务"]
        runtime["本地模型运行时<br/>local_runtime.py<br/>start_qwen_local.sh"]
        offline["离线诊断分析<br/>tools/analyze_diagnostic_capture.py"]
    end

    subgraph relay["跨网络 Relay 可选层"]
        relayServer["relay-server<br/>FastAPI WebSocket<br/>/ws/client<br/>/ws/worker<br/>pairing token / worker id"]
    end

    subgraph model["Qwen + 模型服务"]
        qwenRuntime["llama-server<br/>OpenAI 兼容 /v1/chat/completions<br/>image-min-tokens 256<br/>image-max-tokens 512"]
        qwenModel["qwen2.5vl:3b / 7b<br/>视觉语言推理<br/>场景 / 目标 / 风险 / 建议动作"]
        ollamaStore["Ollama 模型仓库<br/>只负责模型下载与本地 blob 存储"]
    end

    iphone --> ui
    ui --> capture
    capture --> localVision
    localVision --> feedback
    localVision --> recorder
    localVision --> transport
    feedback --> ui

    transport -->|"同网段 / 热点<br/>ws://mac:9000/ws/signaling"| api
    transport -.->|"跨网络<br/>wss://relay/ws/client"| relayServer
    relayServer -.->|"worker outbound ws"| worker

    discovery --> transport
    api --> signaling
    signaling --> prompt
    prompt --> vqa
    worker --> prompt
    vqa --> qwenRuntime
    qwenRuntime --> qwenModel
    ollamaStore --> qwenRuntime
    vqa --> fusion
    fusion --> signaling
    signaling --> api
    api --> transport
    recorder --> offline
```

## 2. 分层职责

```mermaid
flowchart TB
    L6["用户体验层<br/>语音提醒 / 画面 Overlay / 模式切换 / 调试信息"]
    L5["策略层<br/>何时播报 / 何时静默 / 何时发后端 / 连续场景记忆"]
    L4["本地感知层<br/>Apple Vision / YOLO Core ML / OCR / 亮度与变化评分"]
    L3["语义推理层<br/>Prompt 模板 / 上下文拼接 / Qwen VLM / 结果结构化"]
    L2["传输层<br/>WebSocket direct / Relay / Bonjour 自动发现 / Pairing token"]
    L1["运行时层<br/>FastAPI / llama-server / Ollama model store / iOS 系统能力"]
    L0["诊断闭环<br/>本机录制 / Mac 离线分析 / 回归测试 / 模型与策略迭代"]

    L1 --> L2
    L2 --> L3
    L1 --> L4
    L4 --> L5
    L3 --> L5
    L5 --> L6
    L4 --> L0
    L3 --> L0
    L0 -. "数据驱动改进" .-> L4
    L0 -. "Prompt / 策略回归" .-> L3
```

## 3. 实时推理时序

```mermaid
sequenceDiagram
    participant User as 用户
    participant App as iPhone App
    participant Local as 本地感知
    participant Backend as Mac FastAPI
    participant Prompt as Prompt上下文
    participant Qwen as llama-server/Qwen
    participant Voice as 语音与Overlay

    User->>App: 点击开始视觉辅助
    App->>Backend: WebSocket stream_start
    Backend-->>App: stream_ack

    loop 按模式节流采样
        App->>Local: 摄像头帧 CMSampleBuffer
        Local-->>App: LocalPerceptionSignal
        App->>Voice: 本地即时风险提醒
        App->>Backend: frame + mode + gps + question + context + jpeg
        Backend->>Prompt: resolve_prompt + build_contextual_prompt
        Prompt->>Qwen: POST /v1/chat/completions
        Qwen-->>Prompt: JSON 结构化视觉理解
        Prompt-->>Backend: summary / spatial / risk / action / spoken_text
        Backend-->>App: vqa_result + latency + gps_location
        App->>Voice: 更新结果卡片与语音播报策略
    end

    User->>App: 停止
    App->>Backend: stop
    Backend-->>App: stream_stopped
```

## 4. 两种联网拓扑

```mermaid
flowchart TB
    subgraph nearby["近场模式：同 Wi-Fi 或 iPhone 热点"]
        iphoneA["iPhone App"] -->|"Bonjour 发现 / 直接 WebSocket"| macA["Mac FastAPI + Qwen Worker"]
        macA --> qwenA["本地 llama-server + Qwen"]
    end

    subgraph relayMode["跨网络模式：手机蜂窝 + Mac Wi-Fi"]
        iphoneB["iPhone App"] -->|"outbound WebSocket<br/>/ws/client"| relay["公网 Relay"]
        macB["Mac Worker"] -->|"outbound WebSocket<br/>/ws/worker"| relay
        macB --> qwenB["本地 llama-server + Qwen"]
        relay -->|"转发请求/响应"| iphoneB
        relay -->|"转发任务/结果"| macB
    end
```

### 拓扑选择

- **近场模式**：适合现场演示、研发联调。iPhone 与 Mac 在同一网络时，App 可以通过 Bonjour 自动发现 Mac 后端，不需要手输 IP。
- **跨网络模式**：适合 iPhone 走蜂窝、Mac 在公司 Wi-Fi 的情况。iPhone 和 Mac 都主动连公网 Relay，避免路由器端口转发和内网穿透依赖。
- **模型仍在 Mac**：当前 Qwen 3B/7B 由 Mac 本地 `llama-server` 承载，不随 App 安装到 iPhone。

## 5. 核心模块与代码位置

| 模块 | 主要职责 | 代码位置 |
|---|---|---|
| iOS 主界面 | 模式、结果卡片、设置入口、用户交互 | `ios-vqa-app/VQASee/VQASee/ContentView.swift`, `AssistanceScreen.swift`, `AnswerPanel.swift`, `ModeBar.swift`, `SettingsView.swift` |
| 摄像头采集 | 相机预览、帧采样、JPEG 编码 | `CameraCapture.swift`, `StreamingViewModel.swift` |
| 本地感知 | Apple Vision、YOLO Core ML、OCR、本地风险信号 | `LocalVisionAnalyzer.swift`, `LocalPerception.swift`, `OCRRecognition.swift` |
| 语音反馈 | 本地即时播报、Qwen 结果播报、重复抑制 | `PureHelpers.swift`, `PressToTalkButton.swift`, `SpeechRecognitionController.swift` |
| 传输发现 | WebSocket、Bonjour 自动发现、服务器选择 | `Networking.swift`, `BonjourDiscovery.swift`, `ServerPickerView.swift` |
| 后端入口 | FastAPI HTTP/WS、健康检查、信令入口 | `server-vqa/app/main.py`, `server-vqa/app/signaling.py` |
| Prompt 构建 | 模式化 prompt、连续场景上下文、问答上下文 | `server-vqa/app/prompts.py`, `server-vqa/app/scene_context.py` |
| VQA 调用 | OpenAI 兼容接口、Qwen 响应解析、fallback | `server-vqa/app/vqa_service.py`, `server-vqa/app/fusion.py` |
| 本地模型运行时 | 启动 llama-server、加载 Qwen 模型、调优视觉 token | `server-vqa/app/local_runtime.py`, `start_qwen_local.sh`, `start_local_vqa.sh` |
| Relay | 跨网络 client/worker 转发、限流、配对 token | `relay-server/relay_app/main.py`, `start_relay.sh`, `start_worker.sh` |
| 诊断闭环 | 本机诊断录制、Mac 离线分析 | `DiagnosticCaptureRecorder.swift`, `server-vqa/tools/analyze_diagnostic_capture.py` |

## 6. 当前研发重点

1. **低延迟体验**：本地风险先提示，Qwen 慢结果后解释；通过 frame gating、scene memory、speech suppression 减少重复推理和重复播报。
2. **结构化输出**：后端要求 Qwen 返回 `summary / spatial_description / risk_level / suggested_action / spoken_text` 等字段，`fusion.py` 只做兜底与融合。
3. **跨网络可用性**：近场用 Bonjour 自动发现；不同网段用 Relay。研发调试时要明确当前走的是 direct 还是 relay。
4. **安全边界**：VQASee 是视觉风险辅助，不输出“可以走/可以开/安全”等替代用户判断的结论。
5. **诊断驱动迭代**：真实误检、漏检、延迟异常应通过诊断录制进入离线分析与回归测试，而不是只调 prompt。
