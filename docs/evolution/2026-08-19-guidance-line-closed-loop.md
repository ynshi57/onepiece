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

## 第二轮：中心线「跳底部空行」+ 门禁修复（2026-08-19 下午）

1. 反馈：`missed_path=353`（半数帧无线）。诊断：75% insufficient 帧 coverage=0，
   但 84% 近路是 candidateOpen/caution——中心线遇底部车头盖即 break，起点被判死。
2. 修改（Python 真值 + Swift 引擎同算法）：跳过底部起始空行找起点锚，保留中景遇空行
   break（绝不跨越前方障碍）。设备端顺带去掉每帧 width×height 全图拷贝（锁定期直读）+
   中心线单趟扫描（去 per-row 分配）。
3. 验证（701 帧真身）：`both_ok 347→588`、`missed_path 353→113(-68%)`、
   `false_go=0` 不变；`hit_rate 0.498→0.390`、`mean_deviation 0.292→0.322` 下滑
   **纯属新增 241 个更难帧的构成变化**（既有帧线逐位未变），非回归——已诚实记录。
4. 附带修复：发现 guidance 门禁**静默失效**（save 丢 guidance 指标 + gate 读错层级），
   恒过。修 `save_baseline(metric_keys=...)` + `gate_guidance` 解包 payload +
   全链路回归测试；`camvid-walk-v1-guidance` 首次持有真实指标，门禁真正生效。
5. 沉淀：`docs/model-lab/2026-08-19-centerline-skip-hood-fix.md`；基线前移到新已知良好。

## 第三轮：分割 2 通道采样 Bug（用户「紫线绿线不一致」→ 真身感知真因）

1. 反馈：逐帧图上紫线(预测)和绿线(真值)基本对不上。
2. 量化：预测线航向/横向摆动是真值的 ~2 倍 → 不是"走中间"是"乱摆"。
3. 归因：分割模型输出 `[1,2,512,512]`(2 类 logits)，但 Swift 采样器只读 channel 0 raw
   还和 0.5 比 → 读错类 + logit 当概率。ROI 与中心线共用采样器,一起被拖累。
4. 修复：2 类做 `sigmoid(l1-l0)` 取通行类；单通道直读；>2 类显式 nil 失败。
5. 闭环实测：`hit_rate 0.39→0.87`、`mean_deviation 0.32→0.11`、`over_extension 0.28→0.03`、
   `risk_miss 190→2`,`false_go=0` 不变；代价 `false_block 747→806`(更保守,需重标定阈值)。
6. 防护:guidance 基线前移,回退采样器会被 `gate_guidance` 因 hit_rate 骤降拦下。
7. 沉淀:`docs/model-lab/2026-08-19-segmentation-2channel-sampler-fix.md`。

## 经验

> 用户对"图看着不对"的直觉,往往比聚合指标先发现真 bug——要顺着直觉量化到根因,别用话术搪塞。
> 模型接入层(通道/尺度/坐标)错一处,评测全盘皆错;真身 harness 的价值就是让这种错暴露在数据里。
>
> 升级产出（框→线）之前，必须先确认真值可信；否则所有「变好/变差」都是错觉。
> 真值一旦错过两次，就不是 bug，而是系统没学会——已用调色板回归测试锁死。
>
> 平均指标下滑不必然是回归：先分清「既有样本变差」还是「新增更难样本拉低均值」。
> 门禁必须能真拦——一个恒过的 gate 比没有 gate 更危险，因为它假装在保护你。
