# 02 — 分层架构

受众：工程、产品。现行规定见 [`docs/CURRENT.md`](../../CURRENT.md)。

```mermaid
flowchart TB
    L5[体验层 · 画面 overlay 为主 · 语音辅助]
    L4[策略层 · 何时开口 · 失败必须看得见]
    L3[感知层 · YOLO · TwinLite 路面/车道]
    L2[语义层 · Qwen · 以后再做]
    L1[采集传输 · 相机 / WebSocket / Relay]
    L0[学习闭环 · 诊断 / harness / 评测]

    L1 --> L3 --> L4 --> L5
    L2 --> L4
    L3 --> L0
    L0 -. 改进 .-> L3
```

| 层级 | 代码 |
|---|---|
| 采集 | `CameraCapture.swift` |
| 路面 | `RoadSurface.swift` · TwinLite 默认 |
| 障碍 | `LocalPerception.swift` · YOLO11n |
| Overlay | `CameraRiskOverlay.swift` |
| 诊断 | harness + `server-vqa/app/diagnostic_*` |
