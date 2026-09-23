# Seq05VD_f04080 左侧黄线折线

## 最终结论

用户看见的 Z 横档是 UFLD 逐行锚点无行间几何。decode 后做 **去尖刺 + 切断换身份跳变 + 轻 G2**，本帧黄线过看图门：Z 没了，停车走廊那点弯还在。

## 看图

- 用户页：刷新 `/diagnostics/datasets/ios-harness/frames/ui` 的 `road/Seq05VD_f04080`
- 过门：`docs/model-lab/figures/twinlite-preview/Seq05VD_f04080-harness-polished.jpg`
- 回归：`Seq05VD_f02820-harness-polished.jpg`（真弯还在）、`0016E5_06630-harness-polished.jpg`

| 帧 | 左黄 raw | 左黄 polish | 门 |
|---|---|---|---|
| f04080 | 56 点，最大转角 165.6°，中段 Z | 54 点，27.9°，走廊弯 | 过 |
| f02820 | 已较干净 | 形状几乎不变 | 过 |
| 06630 | 楼梯折线 | 走廊线 | 过 |

## 方案（已落地）

- `UFLDv2LanePolisher`：`LocalLanePolylineRunner.analyze` 在 raw decode 之后调用
- 只打 row-anchor 1/2；col 0/3 不动
- 不重训；不拉成直线

## 验证

- `pytest server-vqa/tests/test_ufld_lane_polish.py` → 4 passed
- PerceptionHarness 12/12 unsandboxed
- App-only Xcode 未编译

## 风险

- 左黄在栗色车旁仍有一小段真弯/轻折，这是停车边，不是 Z
- 诊断台若还画 col-anchor，橙色/青色仍可能乱，不是产品左黄线
- Intel Mac 沙箱里 CoreML 会吐出跨帧相同的假折线；看图必须 unsandboxed harness

更新时间: 2026-09-22 14:10
