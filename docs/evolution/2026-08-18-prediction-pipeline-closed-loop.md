# Prediction Pipeline: Closing the Evaluation Loop

Date: 2026-08-18

## 乔布斯裁决

闭环实验平台此前是"半个闭环"：能采集、标注、算指标，但 manifest 里的 `prediction` 大多为空，开源数据集根本没有预测来源。价值只有在"环合上"时才成立——让"一份数据集 → 一键得到模型表现 → 一键回归"跑起来，把发布前判断从主观体验变成可复现证据。

优先级：先合上环（Phase 1 真机 + Phase 2 开源预测器），再上回归门禁（Phase 3）。UI 产品化、独立 PRD 排在其后。

## 问题定位（已验证）

产出 `near_path_status` 等预测的 `LocalPathGuidanceSignal` 只在 iOS Core ML 链路里（`LocalPerception.swift` / `LocalSegmentation.swift`），服务端 Python 没有"RGB 图 → path_guidance"的预测器。

- 真机 session：预测由 iPhone 现场算好、随 manifest 上传，`path_manifest_export._prediction_from_path_guidance` 已能填 → 可闭环。
- 开源数据集（CamVid/BDD100K）：`path_dataset_import` 只从 mask 生成 `ground_truth`，没有任何东西产生 `prediction` → 环断在这里。

关键防线：ground_truth 来自数据集 mask，prediction 来自模型输出，两者必须是不同来源，否则评估自证。

## What is implemented now

### Phase 1 — 真机数据先闭环 + 基线

- `server-vqa/app/eval_baseline.py`：保存/读取/列出回归基线（只存聚合指标 + 元数据，不存图片或绝对路径，安全可提交）。
- `POST /diagnostics/sessions/{id}/close-loop`：一键导出 manifest → 评估 → 存基线。
- `GET /diagnostics/baselines`：列出基线。
- 评估报告 UI（session path-eval + dataset evaluate）显著展示 `missing_prediction_count`，让"没预测的帧"可见，不被平均值掩盖。

### Phase 2 — 服务端 ONNX 通行性预测器（离线代理）

- `server-vqa/app/path_roi.py`：从 `path_dataset_import` 抽出共享 ROI（`NEAR/LEFT/RIGHT_ROI`、`roi_coverage`、`status_from_coverage`、`focus_direction`、`path_guidance_from_mask`）。import（GT）与 predictor（预测）共用，避免双实现漂移。
- `server-vqa/app/traversability_predictor.py`：加载 Fast-SCNN floor-seg ONNX → seg → ROI → path_guidance。onnxruntime 或模型缺失时返回 `capability=unsupported`，绝不假装、绝不产假预测。核心 `prediction_from_traversable_mask` 是纯 numpy，可脱离模型测试。
- `server-vqa/tools/predict_path_guidance_dataset.py`：批量产出 `predictions.jsonl`，直接喂 `evaluate_path_guidance(prediction_rows=...)`。预测器不可用时非零退出（EXIT 2）而非写空/假数据。
- `POST /diagnostics/datasets/predict`：平台向导式"运行预测"步骤，默认模型路径，工程参数进高级设置；不可用时明确告知。

### Phase 2 兜底 — Parity

- `server-vqa/app/path_parity.py` + `tools/parity_path_guidance.py`：在同时有 iOS 与服务端预测的真机帧上做逐字段一致性对比，量化漂移，超阈值告警（CLI EXIT 4）。离线代理不能当成 iPhone 真值。

### Phase 3 — 回归门禁

- `server-vqa/app/regression_gate.py`：安全优先阈值。`risk_miss_count` 零容忍不得增加；`status/direction accuracy` 不得跌超 epsilon；`unknown_rate`、`false_block` 有容忍上限。
- `server-vqa/tools/check_path_guidance_regression.py`：对比基线，回退时非零退出（EXIT 5 回退 / EXIT 6 无基线），可接 CI 作为发布/合并 gate。

## Commands / 验证

```bash
source .venv/bin/activate && pytest server-vqa/tests            # 128 passed
# 批量预测（需安装 onnxruntime + 提供模型；否则明确报 unsupported）
pip install -r server-vqa/requirements-predictor.txt
python server-vqa/tools/predict_path_guidance_dataset.py docs/datasets/camvid-manifest.jsonl --output docs/datasets/camvid-predictions.jsonl
python server-vqa/tools/evaluate_path_guidance_dataset.py docs/datasets/camvid-manifest.jsonl --predictions docs/datasets/camvid-predictions.jsonl
# 回归门禁
python server-vqa/tools/check_path_guidance_regression.py docs/datasets/camvid-manifest.jsonl --baseline camvid-manifest --predictions docs/datasets/camvid-predictions.jsonl
```

## 角色评审要点

- 乔布斯：闭环让发布前判断可复现；`missing_prediction` 必须可见。达成。
- 罗根：ROI 单一来源（`path_roi.py`）避免 Swift/Python 漂移无人管；parity + capability 探测兜底。达成。
- 全麦：ONNX 预测器标注为 offline proxy，非上线 on-device 模型；Qwen 结构化预测留作后续可选。达成。
- 思余：数据集评估新增向导式"运行预测"，指标卡区分"没预测"和"预测错"。达成。

## 剩余风险

- 模型资产/转换：onnxruntime 与 Fast-SCNN ONNX 需开发者本机安装/提供；当前环境二者皆缺，预测器如实报 unsupported。
- 漂移：服务端预测器是产品预测器的代理，指标是相对趋势参考，不是 iPhone 真值。
- 标签匹配：CamVid/BDD100K 语义标签是否覆盖 floor/traversable 需先验证（见 rgb-only 路线 Phase 3）。

## 沉淀去向

- 迭代记录 → 本文件。
- 模型经验 → `docs/model-lab/2026-08-18-offline-traversability-predictor.md`。
- 代码事实与回归 → `server-vqa/tests/test_traversability_predictor.py`、`test_path_parity.py`、`test_regression_gate.py`、`test_api.py`。
