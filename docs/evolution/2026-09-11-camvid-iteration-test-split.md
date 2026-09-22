# 2026-09-11 CamVid 四种街道场景抽样为迭代 test 集

## 问题

全量 701 帧真身感知（机动车多类分割）在 Intel Mac 上约 35 分钟。准确度还不够时，这个周期没法支撑「改一点、看一眼」。

## 场景分析

CamVid 不是很多座城市，是剑桥 **4 段车载录像**（701 张静帧）：

| 序列 | 帧数 | 街道场景 | 标注侧写 |
|---|---|---|---|
| 0001TP | 124 | 低光城市街道 | 最暗（亮度约 61），近处车辆最多 |
| 0006R0 | 101 | 开阔明亮车道 | 最亮（约 138），路面占比最高 |
| 0016E5 | 305 | 混合街区 | 最长，行人出现最频繁 |
| Seq05VD | 171 | 人行道偏多的街道 | 人行道/路沿最高，近处车辆最少 |

同序列里相邻帧几乎一样（例如 `0001TP_006690` 与 `0001TP_006720`），所以每种场景抽 3 张时取头/中/尾时段里信息量最大的一帧，而不是连着三张。

## 方案

- 每序列 3 张，共 **12 帧**（约 1 分钟出结果）
- 从现有 walk/drive/legacy jsonl **拷贝原行**（含网格真值），不重新生成
- 诊断台默认推荐 test 集；全量 701 收到「全量回归」折叠里
- 全量仍是发布门，不替换

## 产物

- `docs/datasets/camvid-test-scenes.json` 目录
- `docs/datasets/camvid-manifest-drive-test.jsonl`
- `docs/datasets/camvid-manifest-walk-test.jsonl`
- `docs/datasets/camvid-manifest-test.jsonl`
- 重抽样：`PYTHONPATH=server-vqa python -m app.camvid_scene_sample`

## 影响面

- 消费方：诊断台数据集列表、iPhone 真身评估页 ETA 文案
- 未改 harness 协议、未改 `bayes-iphone`、未改 701 全量 manifest
- 验证走到「打开数据集页 / 打开 drive-test 真身评估页」，不只是写出了 12 行

## 验证

- `pytest server-vqa/tests/test_camvid_scene_sample.py server-vqa/tests/test_dataset_paths.py server-vqa/tests/test_api.py -k "camvid or datasets_ui or harness_ui"`
