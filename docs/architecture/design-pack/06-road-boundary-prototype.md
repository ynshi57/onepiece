# 06 — 道路边界原型

受众：模型、后端、产品。

## 目标

准确识别道路边界、车道线、人行横道、路沿/人行道边界，并把候选通行/行驶走廊贴合到视频画面上。

## 原型流程

```mermaid
flowchart TB
    A[iPhone 录制帧] --> B[Mac RoadBoundary Service]
    B --> C[YOLOPv2 / HybridNets]
    B --> D[语义分割]
    B --> E[Depth / LiDAR / Depth Anything]
    C --> F[车道线 / 可行驶区域]
    D --> G[道路 / 人行道 / 路沿 / 人行横道]
    E --> H[落差 / 台阶 / 坑洞]
    F --> I[RoadBoundaryResult]
    G --> I
    H --> I
    I --> J[归一化图像坐标]
    J --> K[离线 overlay 渲染]
    K --> L[人工评估]
    L --> M{稳定且准确?}
    M -->|否| N[继续采集 / 调参 / 训练]
    M -->|是| O[考虑 iPhone Core ML 部署]
```

## 输出合同草案

```json
{
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
  "guidance_corridor": {
    "centerline": [[0.5, 0.9], [0.5, 0.4]],
    "status": "caution"
  }
}
```

## 产品边界

在满足以下条件前，不能把它作为“路线”上线：

- 视频坐标对齐已验证；
- 时序稳定性可接受；
- 误报/漏报率有记录；
- 语言始终谨慎：**疑似、请放慢、请自行确认**。
