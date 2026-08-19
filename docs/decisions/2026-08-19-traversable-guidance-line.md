# 决策：从三区域状态升级到「可通行引导线」

- 日期：2026-08-19
- 状态：已采纳（Phase 1 落地，区域状态保留为兼容摘要）
- 主责：乔布斯（方向）/ 全麦（模型与真值）/ 罗根（系统）/ 思余（UI）
- 触发：用户明确「我对 LocalPathGuidanceEngine 的期望是输出一条或多条可通行的
  引导线，而不是框」，且要求「要做就做好」。

## 背景

原来的 `LocalPathGuidanceEngine` 只输出近/左/右三个区域的状态框
（candidateOpen/caution/blocked）。对一个「视觉引导优先」的行走产品，框无法回答
用户真正的问题：**我该往哪走**。同时在核对 CamVid 真值时发现真值调色板 Bug
（见 `docs/model-lab/2026-08-19-camvid-traversable-palette-fix.md`），说明升级前
必须先让「考卷答案」可信。

## 决策

引入统一的「可通行引导线」表示 `GuidancePath`：

- 归一化坐标（原点左下，y 向上，与 ROI/物体框一致）。
- 一条或多条 `GuidanceLine`：折线点 `{x, y, half_width}`（含走廊半宽）、
  `confidence`、`kind`、`risk_segments`。
- `status ∈ {ok, insufficient}`：自由空间破碎到无法成线时**显式降级**，
  绝不伪造一条直线（符合 AGENTS「不允许静默失败」）。

单一真源 `server-vqa/app/guidance_path.py`，Swift 镜像
`ios-vqa-app/VQASee/VQASee/GuidancePath.swift`，契约测试防漂移。

- **真值线**：服务端从修正后的可通行 mask 用中心线算法生成（`centerline_from_mask`）。
- **预测线**：设备端 `LocalTraversabilitySegmentation` 逐像素图 → 同一中心线算法
  （Swift `GuidancePathBuilder.centerline`）生成。
- 区域状态**保留**为兼容摘要，不删除已有决策链路，降低回归风险。

## 各角色意见

- 乔布斯：引导线才是「打开就懂往哪走」的闭环产出；先修真值再升级，接受本轮
  Phase 1（表示+真值+引擎+评测+UI），语音播报与设备端渲染进 backlog。
- 全麦：坚持真值先修；预测线口径与真值口径必须同算法；`false_go`（真值无路却报
  有路）是安全红线，必须单列并进门禁。
- 罗根：分割模型每帧只跑一次，cue 与引导线共用同一次推理输出（`analyzeDetailed`），
  不加帧预算；`insufficient` 走显式降级。
- 思余：逐帧页叠加「紫实线=预测（含走廊）/ 绿虚线=真值」，图例明确；框继续保留
  但语义从「结论」降为「摘要」。

## 影响

- 本轮为离线 harness + 服务端评测闭环；设备端实时渲染/语音在 backlog。
- 首个 walk 口径基线（701 帧）：`false_go=0`（安全）、`missed_path=353`、
  `hit_rate=0.498`、`mean_deviation=0.292`——暴露分割「自由空间」与语义「路+人行道」
  口径差异，作为下一轮模型/口径优化目标。

## 备选与放弃原因

- 继续用框：无法回答「往哪走」，放弃。
- 用最小方案（仅加一条直线朝向）：会掩盖真实自由空间形状与风险，违反安全原则，放弃。
