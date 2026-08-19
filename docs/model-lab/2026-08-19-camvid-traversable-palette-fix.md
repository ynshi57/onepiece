# CamVid 真值可通行调色板修正（真值 Bug）

日期：2026-08-19 · 主责：全麦 · 触发：把「引擎输出引导线」接入闭环前核对真值

## 问题

生成 CamVid 真值时，`CAMVID_TRAVERSABLE_COLORS` 里「人行道」用的是 Cityscapes 的
`(244,35,232)`——该颜色在 CamVid 调色板里**根本不存在**。对照数据集自带的
`camvid_data.py` 官方 32 类映射，CamVid 真实颜色为：

- Road: `(128,64,128)`, `(128,0,192)`, `(192,0,64)`
- Sidewalk: `(0,0,192)`, `(64,192,128)`, `(128,128,192)`

后果：**人行道像素被静默当作不可通行**，对一个「行走优先」的产品，真值系统性
偏向 `blocked/caution`。这不是引擎错，是**考卷答案错**——违反 AGENTS「真值不能
自欺」。

## 证据（701 帧 × 3 区域，累计状态计数）

| 口径 | blocked | caution | candidateOpen |
|---|---|---|---|
| 修正前（Bug） | 486 | 1052 | 565 |
| 修正后 walk（路+人行道） | **90** | 1207 | 806 |
| drive（仅路面） | 486 | 1052 | 565 |

- `drive` 与「修正前」完全一致 → 证明旧的错误调色板等价于「仅路面」的驾驶口径。
- 修正后 `blocked` 486→90（−81%）：大量原判「占用」的帧其实是可走人行道。
- 单帧 `0001TP_006690`：可通行像素 9.3%→16.2%（补回约 7% 人行道）。

## 修改

- `server-vqa/app/open_dataset_adapters.py`：
  - 用官方 Road/Sidewalk 颜色重建 `CAMVID_ROAD_COLORS` / `CAMVID_SIDEWALK_COLORS`；
  - 新增 `camvid_traversable_colors(mode)`：`walk`=路+人行道（默认），`drive`=仅路面；
  - `create_camvid_manifest(..., traversable_classes="walk")`，并在每行记录
    `traversable_classes`，来源可追溯、非静默猜测。
- 重生成 `docs/datasets/camvid-manifest.jsonl`（walk 口径）。
- 测试：`test_camvid_sidewalk_is_traversable_in_walk_but_not_drive`、
  `test_camvid_traversable_colors_rejects_unknown_mode`。

## 影响与后续

- 之前 `误阻挡 517` 的一部分来自真值错，需在修正真值后重新评估引擎，不能直接归因引擎。
- 场景相关的「可通行」定义（行走 vs 驾驶）现在是显式参数，供后续引导线真值与
  评测统一口径。
