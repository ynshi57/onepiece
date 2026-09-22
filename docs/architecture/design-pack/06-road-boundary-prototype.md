# 06 — 路面与车道

受众：模型、产品。现行规定见 [`docs/CURRENT.md`](../../CURRENT.md)。

## 现在

live 默认 **TwinLiteNet**：一张图出可走区和车道图。这是产品路面，不是实验开关。

## 下一阶段

- UFLDv2：真·几何折线（decoder 已有，未接 live）。
- mc5：按人/车派生可走区（已 bundle，开关默认关）。
- 台阶 / 路沿：分割边界或深度，不是给 YOLO 加类名。

## 评测尺子

可走区 / 障碍可用 CamVid。产品车道线不以 CamVid 像素当唯一真值。
