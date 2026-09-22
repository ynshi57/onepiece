# 03 — 模型职责地图

受众：模型工程、产品。现行规定见 [`docs/CURRENT.md`](../../CURRENT.md)。

```mermaid
flowchart LR
    subgraph Live[现在 live]
      Y[YOLO11n · 人 / 车]
      T[TwinLiteNet · 可走区 + 车道图]
    end
    subgraph Staged[已 bundle 未作产品默认]
      M[mc5 角色可走 · 开关默认关]
    end
    subgraph Next[下一阶段]
      U[UFLDv2 · 几何折线]
      D[深度 / 边界 · 台阶路沿]
    end
    Y --> UI[Overlay]
    T --> UI
```

| 能力 | 现在谁负责 | 不要误会 |
|---|---|---|
| 人 / 车 | YOLO11n | 不是台阶 / 路沿 |
| 路面 + 车道图 | TwinLite | 不分人行道 vs 马路 |
| 角色可走 | mc5 代码在，live 关 | 承诺未兑现 |
| 几何车道线 | UFLDv2 未上 live | CamVid 像素不是产品线 |
| 解释 / 问答 | Qwen | 以后再做，不是主路径 |
