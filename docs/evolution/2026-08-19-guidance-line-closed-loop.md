# 进化记录：可通行引导线闭环（真值修正 → 表示 → 引擎 → 评测 → UI）

- 日期：2026-08-19
- 触发：用户要求引导线而非框，且「要做就做好，先定方案 review 再执行」。
- 出场角色：乔布斯（方向）、全麦（真值/模型/评测）、罗根（系统/帧预算）、
  思余（UI 叠加）。

## 本轮闭环（用户反馈 → 归因 → 实现 → 验证 → 沉淀）

1. 用户反馈：期望输出「一条或多条可通行引导线」；追问真值是谁产的。
2. 归因：核对真值时发现 CamVid 可通行调色板 Bug（人行道被静默丢弃），
   真值系统性偏 blocked——**先修考卷**。
3. 实现（按序）：
   - Step 0 真值修正：官方 Road/Sidewalk 调色板 + walk/drive 口径参数，重生成 manifest。
   - 表示层：`GuidancePath`（Python 单一真源 + Swift 镜像 + 契约测试）。
   - 真值线：`centerline_from_mask` 从可通行 mask 生成，写入每行 `ground_truth_path`。
   - 引擎线：`LocalTraversabilitySegmentation.analyzeDetailed` 一次推理同时产 cue 与
     引导线；harness 透传 `guidance_path`；设备/harness 靠符号链接共享源码保证一致。
   - 评测：线级指标（deviation/hit_rate/over_extension/direction/false_go/missed_path）+
     门禁；`run_ios_harness_eval` 扩展并存/取 guidance 基线。
   - UI：逐帧页叠加预测线（紫，含走廊）vs 真值线（绿虚），图例说明。
4. 验证：
   - 后端 `pytest server-vqa/tests` 全绿（新增 guidance schema/eval/UI/manifest 测试）。
   - Swift harness `swift build` 通过；701 帧真跑产出 `guidance_path`。
   - 首个 walk 基线：`false_go=0`（安全）、`missed_path=353`、`hit_rate=0.498`、
     `mean_deviation=0.292`——诚实暴露口径与召回差距。
5. 沉淀：
   - 决策 `docs/decisions/2026-08-19-traversable-guidance-line.md`
   - 真值 Bug `docs/model-lab/2026-08-19-camvid-traversable-palette-fix.md`
   - 基线 `docs/model-lab/2026-08-19-guidance-line-baseline.md`
   - UI `docs/ui-lab/2026-08-19-guidance-line-overlay.md`
   - 本文（进化记录）

## 剩余风险 / 未闭环（进 backlog / roadmap）

- 模型：分割「自由空间」口径 ≠ 语义「路+人行道」，`missed_path` 高 → 阈值扫描/口径
  分离/更贴近行走的数据集（全麦，下一轮）。
- 设备端：实时渲染引导线 + 语音「向左半步」提示尚未做（思余/罗根，backlog）。
- 数据：CamVid 是 driving 视角，评 walking 模型有偏；需 walking 样例集（乔布斯定优先级）。

## 经验

> 升级产出（框→线）之前，必须先确认真值可信；否则所有「变好/变差」都是错觉。
> 真值一旦错过两次，就不是 bug，而是系统没学会——已用调色板回归测试锁死。
