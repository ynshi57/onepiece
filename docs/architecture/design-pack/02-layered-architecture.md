# 02 — 分层架构

受众：工程、产品、设计。

## 分层模型

```mermaid
flowchart TB
    L5[体验层\n语音 / 触觉 / 画面 Overlay / 极简文字]
    L4[反馈策略层\n该不该说? 显示什么? 抑制什么?]
    L3[感知层\nApple Vision / YOLO / OCR / 未来道路边界 + 深度]
    L2[语义推理层\nQwen / Prompt / 上下文 / 融合]
    L1[采集与传输层\n摄像头 / 编码 / WebSocket / Relay / 后端]
    L0[学习闭环\n诊断录制 / 离线评估 / 回归测试]

    L1 --> L3
    L3 --> L4
    L2 --> L4
    L4 --> L5
    L3 --> L0
    L2 --> L0
    L0 -. 改进 .-> L3
    L0 -. 改进 .-> L2
```

## 为什么不能只靠一个大模型

- 本地感知快，但会误检/漏检。
- Qwen 表达能力强，但慢。
- UI 和语音不能在有明显风险时等待慢模型。
- 诊断闭环让系统从真实失败中学习。

## 当前实现对应代码

| 层级 | 代码位置 |
|---|---|
| 采集 | `CameraCapture.swift`, `FrameJPEGEncoder` |
| 本地感知 | `LocalVisionAnalyzer.swift`, `LocalPerception.swift` |
| 画面 Overlay | `CameraRiskOverlay.swift` |
| 语音策略 | `PureHelpers.swift`, `StreamingViewModel.swift` |
| 后端 VQA | `server-vqa/app/vqa_service.py` |
| 诊断闭环 | `DiagnosticCaptureRecorder.swift`, `server-vqa/tools/analyze_diagnostic_capture.py` |
