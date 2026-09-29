# Jev / 决策型模型 vs TwinLite（小马）

## 结论

**Jev 不是更快的 TwinLite。** TwinLite = 稠密感知几何（mask/车道）；Jev / Visual Jev = 给定选项上的结构化选择 + 概率。VQASee 户外叠层主航道仍是 TwinLite + YOLO + 规则；Jev 族最多作远程/低频「选项+概率」出口实验。

## 共识

| 项 | 裁定 |
| --- | --- |
| 能否替换画车道 | **否** |
| 「快」含义 | TwinLite：小 CNN/ANE；Jev：少 decode、共享视觉前缀 |
| 端上真要快决策 | 先场景门闩 + YOLO/深度，不上 4B VLM 当 5 Hz 主引擎 |
| 三层谱系 | ① Jev 族接口 ② 感知→规则（现状）③ E2E 规划（勿默认） |

## 已执行

- 情报卡：`docs/tech-radar/2026-09-29-jev-vs-perception-decision-models.md`
- 索引：`docs/tech-radar/index.md` 已挂条目（L1）
- **未改** App / 模型代码

## 待办 / 可选

- 远程 typed decision schema 最小实验（全麦）：固定选项集准确率 + 延迟对照；非本轮必做
- 场景门闩 on-device（与室内 OOD 卡衔接）

## 关键路径

- 情报卡路径见上
- 关联：`docs/tech-radar/2026-09-23-indoor-ood-competitors-realtime.md`、`docs/CURRENT.md`

## 风险与回滚

- 风险：把决策概率读成「可以走」；或误上 VLM 当实时主引擎
- 回滚：本轮仅文档；无代码回滚面

更新时间: 2026-09-29 11:33
