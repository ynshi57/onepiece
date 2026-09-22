# 三区 ROI 残余清掉

## 结论

用户裁定「不要了，都删掉」。产品路径不再保留左/近/右三框：代码、测试、评测结果、701 帧 jsonl 旧字段、`path_roi.py`、`harness-config-*.json` 矩形全部移除。可走区评测只认 `traversable_grid`。UFLD polish 未动。

## 已执行

- Swift：`LocalPerception` / `LocalSegmentation` 回到 ROI-free；`PerceptionConfigWire` 不再解码 `roi` 与三区阈值。
- Python：删除 `server-vqa/app/path_roi.py`。预测器改为输出 `traversable_grid`。`evaluate_path_guidance` 不再打 `status_accuracy` / `focus_direction`。parity、regression gate、诊断台配置页、逐帧筛选都改锚到区域网格。
- 数据：CamVid jsonl 里的 `near_path_status` 等旧字段、`mask_coverage` 三区键、harness 预测里的三区状态、`harness-config-walk|drive.json` 的 `roi` 矩形都去掉。
- 评测结果：`camvid-ios-report.json` / `camvid-walk-v1.json` 里的三区准确率与 risk_miss 列表去掉；诊断台旧 case `camvid-manifest__risk_miss.json` / `__false_block.json` 已删。
- 文档：`CURRENT.md` 写清已删干净；`2026-08-25` 决策里「path_roi 仍留仓库」一句改成已删除。dated model-lab / evolution 实验笔记不改写。

## 验证

- 第一刀后：受影响测试绿。
- 第二刀后：`source .venv/bin/activate && pytest server-vqa/tests` → **388 passed**（少 2：删掉 `normalize_walking_roi` 两则契约测试，prompt 测试改成忽略 ROI）。App SwiftUI 仍需完整 Xcode。

## 第二刀：Qwen walking_roi ingest（用户「一起拆掉」）

- 删除 `normalize_walking_roi` / `_normalize_rect`。
- `signaling.py` / `worker_client.py` 不再读 `walking_roi`，不再把三框写进 Qwen prompt，不再报 `walking_roi_present`。
- 测试改为断言：旧客户端仍带 `walking_roi` 时，prompt 无 ROI 行、诊断指标无 `walking_roi_present`；`frame_quality` 短路测试保留。
- 不改 VQA `direction` 枚举，不改写 8/11 dated lab。

## 未做

- 不提交 git（用户未要求）。
- 不重跑 701 帧 harness（旧字段是从磁盘 jsonl 剥掉，不是重新推理）。
- 不删 8 月 lab 里描述当时 ROI 实验的段落。

## 审阅

GPT / Sonnet / Gemini 第二刀均为 VOTE agree，无必改项。

更新时间: 2026-09-22 15:32
