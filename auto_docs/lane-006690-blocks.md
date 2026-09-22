# road/0001TP_006690 黄线方格与右边不准

## 最终结论

用户看见的「黄色方格子」不是折线画坏了，是诊断台把 TwinLite 车道**像素网格**当车道线默认叠上去。本帧没有 `lane_polylines`。右侧不准是因为这帧车道通道几乎只在引擎盖喷了 40 个格子，右边那串不在司机看见的占用车道右边。

## 看图事实（诊断台 + harness jsonl）

- 页：`camvid-manifest-drive-test.jsonl` 逐帧，`road/0001TP_006690`
- 黄块：128×96 `lane_grid`，占用 40/12288；y∈[0.78,0.97]，其中 37 格在画面底部 20%
- 左串：沿路沿/牙子的楼梯格；右串：SEAT 右后角沥青，到不了公交车左侧那条边
- `lane_polylines`：无
- 引导：`twinlite_rails` / `insufficient`（可走区 52 格，几乎贴引擎盖）
- 同 12 帧：UFLD 折线只出现在 Seq05VD 与部分 0016E5；0001TP 三帧全 0

对照图：`docs/model-lab/figures/camvid-0001TP_006690/jobs-lane-grid-yellow.jpg`

## 方案分层

- **P0 诚实呈现**：黄块调试层默认关；有折线才画黄实线；没有就明示「没有车道折线」。App 同样不要用格子冒充车道。
- **P1 导出轨**：`TwinLiteOccupiedRails` 已有 paint/curb 折线，失败时也写入 `lane_polylines`。拥堵近场右轨用占用带右缘/障碍左边，虚线，不许把喷斑当边。
- **P2 模型**：0001TP 作为 UFLD 城市场失败样例进评测。不在本帧堆阈值。不新训除非 P1 仍过不了门。

## 投票 / 共识

GPT / Sonnet / Gemini：**有条件同意**（approve_with_changes）。

吸收的条件：
- P0 可以先做：黄块从绿 DA 的 `.pred-mask` 拆开，默认关；无折线写「本帧没有车道折线」；改掉「默认必须画黄块」的测试。
- App 停绿格子必须和折线渲染或空态同船，否则真机仍是棋盘。
- P1 不得把 TwinLite 喷斑直接写成产品 `lane_polylines` / `rowAnchor`（会抢 UFLD 引导、把错误轨画成真黄线）。新 Source；喷斑不出黄实线；不得宣称 006690 右黄过门。
- 蓝线 insufficient 可接受（coverage 0.057 低于 0.30；SEAT 就在眼前）。

## 已执行与待办

用户裁定「先改画法」。已改：
- 诊断台默认不叠黄块；把 `lane_grid` 收成 `twinlite_mask` 折线（有 UFLD 折线时仍用 UFLD）。
- App overlay 画折线，不再填 `laneGrid` 绿格子。TwinLite 轨 source=`twinlite_mask`，不抢 `rowAnchor` 引导。
- 看过 006690：黄的是线段，不是棋盘。左沿路沿，右仍是 SEAT 旁短线——几何门未过。
- `pytest server-vqa/tests/test_perception_config_api.py` → 34 passed。App Swift 未用 Xcode 编译。

对照图：`docs/model-lab/figures/camvid-0001TP_006690/jobs-lane-strokes.jpg`（改画法后）vs `jobs-lane-grid-yellow.jpg`（改前格子）。

未做：右边贴上公交车（模型阶段）。不提交。

更新时间: 2026-09-22 15:55

## 风险与回滚

- 关掉黄块后，没有折线的帧会「看起来没车道」——这是真话，不是退步。
- 把 TwinLite mask 骨架化仍可能几何错；P1 导出轨不等于过视觉门。

更新时间: 2026-09-22 15:45
