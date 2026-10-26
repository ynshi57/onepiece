# 带语义正例：本机可操作路径（Cursor 给 Codex）

目的：在**不冒充 149 帧真机**的前提下，给 `SpatialSceneHypothesis` 一条「有 mask → 有地面格、壳仍虚线」的验收链。

## 现状（2026-10-20 Cursor 盘库）

| 来源 | 语义 rows | 说明 |
|---|---:|---|
| `~/.cache/vqasee/spatial-scans/*/document.json` | **0** | 含咖啡店、模拟器导出的 10-18 |
| `/tmp/vqasee-scene-1018-metadata.json` | **0** | 仅结构回放 |
| `server-vqa/diagnostic-captures/spatial-20261013/8EE9D885-…` | **0**（导入前） | **gitignore**，226 帧原件在 Mac 上若存在则照片齐；需 App 内跑 SegFormer |

本机 **没有** 已持久化的 `indoorSemanticObservations` JSON；正例只能 **生成**，不能「只读打开」。

## 推荐旁路（同意 Codex 方向，补操作细节）

1. **导入** 226 帧包（FastAPI `/spatial-scans/import` 或 App 历史），确认 `document.json` + `keyframes/*/jpg` 可读。
2. **模拟器或真机** 打开该 scan → 触发 `SpatialIndoorSemanticAnalyzer`（扫描页已有 pipeline，不改阈值）。
3. **预算**：全 epoch 语义可能久；验收可先 **budget 6** 看路径附近是否有格，停扫后 **budget nil** 看全段（与产品一致）。
4. **导出** 更新后的 `document.json` 到固定路径，双方同 id 回放 UI / DEBUG PNG。
5. **深度**（可选第二轮）：同一帧跑 `depthObservations`，才能验 0.08 m 否决；无语义时可跳过。

## 不能当作什么

- 不能冒充 **149 帧** 或 **10-18 240 帧** 的同一 scan id。
- `deploy/spatial/research/segformer_masks.py` 产出 **研究 receipt**，不自动写回 iOS document schema；产品正例应走 **App 分析器**，与发布层一致。

## Cursor 可并行帮 Codex 的

- 导入脚本 / 模拟器容器路径对账（`monitor_codex_scene_handoff.sh` + 讨论文件贴 scan id）。
- 语义跑完后 Cursor 复跑 38 项 + 打开俯视 PNG 对照产品门第 3 节。
