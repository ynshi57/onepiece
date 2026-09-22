# CamVid 迭代 test 集（四场景 × 3 张）

## 最终结论

CamVid 有 **4 种街道场景**（官方四段行车录像）。每种抽 3 张、拉开间隔，得到 12 帧 test 集。诊断台日常迭代跑 test，全量 701 留给发布回归。日常 harness 用 TwinLiteNet（~1s/帧），mc5 多类分割已退出默认路径。

## 方案要点

- 场景 = `0001TP` 低光城市 / `0006R0` 开阔明亮车道 / `0016E5` 混合街区 / `Seq05VD` 人行道偏多
- 每段头、中、尾各 1 张，优先信息量大的帧，避免 `006690`/`006720` 这种连帧
- walk / drive / legacy 三份 `*-test.jsonl`，真值网格从全量拷贝
- 数据集页把 test 放在「日常迭代（推荐）」；打开全量 701 会提示改用 test

## 投票 / 共识

用户明确要求分析场景并抽样 3 张作为 test，平台改识别 test。按此执行。

## 已执行

- `server-vqa/app/camvid_scene_sample.py` 统计 + 抽样 + 写 jsonl
- 诊断台分组与 ETA 按帧数计算
- 测试覆盖抽样逻辑、便携路径、UI 推荐

## 待办 / 阻塞

- 诊断台当前未在跑：打开 `/diagnostics/datasets/ui`，点 **camvid-manifest-drive-test.jsonl** 的「iPhone 真身评估」即可
- 日常 harness：TwinLiteNet bundled Core ML（mc5 仅手动 `--seg-model` 回归）
- 全量 701 仍可用于发布前回归，不要当日常迭代集

## 关键路径

```text
docs/datasets/camvid-test-scenes.json
  → docs/datasets/camvid-manifest-drive-test.jsonl（12 帧）
  → /diagnostics/datasets/ios-harness/ui?manifest=docs/datasets/camvid-manifest-drive-test.jsonl
```

## 风险与回滚

- 12 帧不能代表 701 的指标数字；发布门仍看全量
- 重抽样：`cd server-vqa && PYTHONPATH=. ../.venv/bin/python -m app.camvid_scene_sample`
- 回滚：诊断台仍可打开 `camvid-manifest-drive.jsonl`

更新时间: 2026-09-11 11:00
更新时间: 2026-09-11 11:52

更新时间: 2026-09-22 09:30
