# docs 知识记忆审计

## 最终结论

`docs/` 曾把 8 月中旬的语音优先、Qwen 主叙事、二值 Fast-SCNN、三区 ROI 写成「现在」。用户逐条拍板后，本轮已把活文档改到 `docs/CURRENT.md` 宪法，并完成三区深层删除。

8/10 那次项目概览是当时的 Qwen 中心快照，不是现行知识。

## 投票或共识

三审曾反对「CURRENT 当宪法」。用户拍板否决，CURRENT 优于 dated 决策。十条拍板后用户确认 `execute_all`。

## 已执行

文档：

- `docs/CURRENT.md` 宪法（视觉优先、四项核心、TwinLite 产品默认、尺子拆开）
- `AGENTS.md` / `docs/decisions/0001-vqasee-memory-system.md` 指针
- `docs/roadmap.md` / `README.md` 去掉「语音优先」现在时；P0 只留四项核心 + 坏了必须看得见
- `docs/architecture/` 设计包按视觉优先 + TwinLite + 四项核心重写
- dated decisions 加横幅，现状数字从 `docs/decisions` 拿掉
- draft skills 禁止调度；tech-radar index 补 08-26 pedestrian

三区 ROI 深层删除（第 5 条）：

- 引擎：`LocalPathGuidanceSignal` 不再有 near/left/right/focus；障碍用 YOLO `blockedRegions`
- OTA：`perception_config.py` / `PerceptionConfig.swift` 去掉 ROI 矩形和三区阈值；加载忽略旧 `roi`；bump 带 `roi` 返回 400
- case：按 `traversable_grid` 聚类 `region_miss` / `region_false_go`；行根或嵌套网格都能读到
- 新 manifest 不再写三区真值；诊断台配置页不再编辑三框
- 未重跑 701 帧旧 jsonl

## 验证

- `source .venv/bin/activate && pytest server-vqa/tests` → **388 passed**
- `cd ios-vqa-app/perception-harness && swift build` → **Build complete**
- **未验证**：App-only SwiftUI（`CameraRiskOverlay` / `DiagnosticCaptureRecorder`）需完整 Xcode `bash deploy/ios/test.sh`

## 待办

- TwinLite 仍不分人/车，记在 CURRENT，本轮不改模型。

## 关键路径：10 组问题（已拍板）

| # | 拍板 |
|---|---|
| 1 | 维持视觉引导优先 |
| 2 | 当前只四项核心；安全和稳定留下 |
| 3 | TwinLite 就是产品默认 |
| 4 | 产品车道 TwinLite；UFLDv2 下一阶段 |
| 5 | 三区深层删除本轮做完 |
| 6 | 台阶/路沿仍是意图；YOLO 不加类 |
| 7 | CURRENT 当宪法 |
| 8 | architecture 本轮重写 |
| 9 | 尺子拆开 |
| 10 | decisions 删数字 |

## 风险与回滚

- App 浮层/诊断上传未在 Xcode 编译；若字段引用漏删，需在有完整 Xcode 的机器修。
- 回滚 CURRENT 会让 dated 决策重新当宪法。OTA 若仍发旧 `roi` JSON，新解码会忽略矩形，不再当产品信号。

更新时间: 2026-09-22 12:20
