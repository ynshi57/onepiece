# 退役 CamVid 线级 / 车道像素真值

## 最终结论

乔布斯裁决（2026-09-22）：**彻底删除** CamVid 派生的 `ground_truth_path`（绿虚线真值路径）与 `lane_grid_fine`（蓝真值车道层）——UI、manifest 生成、.harness 线级/车道闭环评估一并下线。

**保留**：`traversable_grid` 区域 IoU（iPhone 绿叠层 vs CamVid 可走区像素）；iPhone 预测 `guidance_path` / `lane_grid` / TwinLite 画廊。

## 原因

- 绿虚线 = `centerline_from_mask` 从语义可走区抽的中线，与 TwinLite BDD-DA 中线**语义不对齐**，制造假「漏报路径」。
- 蓝车道层 = CamVid 像素格，与产品 UFLD 折线目标不一致。

## 已执行

- `open_dataset_adapters.py`：新 manifest 不再写入两字段
- `diagnostic_api.py`：逐帧页无绿虚线/蓝车道/可走线对比；评估页无引导线指标；能力总览仅 region 门禁
- `run_ios_harness_eval.py`：移除 guidance_line / lane_loop 评估与 gate
- `sweep_seg_threshold.py`：改为 region IoU sweep
- `docs/datasets/camvid*.jsonl`：批量 strip 旧字段
- 相关 pytest 更新

## 待办

- 历史 `eval-baselines/camvid-ios-guidance.json`、`camvid-ios-lane-loop.json` 仍留档，不再被 UI 消费
- 全量 701 帧若需干净 manifest，用诊断台或 `create_camvid_role_manifest` 重生成

更新时间: 2026-09-22 10:05
