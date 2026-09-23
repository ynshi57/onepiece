# camvid-manifest-drive-test 逐帧核验

## 最终结论

不是显示慢。12/12 都有预测行（筛选项「无预测=0」）。看起来「没有输出」的是：4 帧紫线故意不画（前方有车，`insufficient`）；拥堵帧绿可走区只剩引擎盖一条；黄线已改成折线但仍短/贴错边。唯一和加载有关的是 CamVid 真值绿罩懒加载（默认关），以及滚到最后一帧才加载底图。

## 看图事实

页：`/diagnostics/datasets/ios-harness/frames/ui?...camvid-manifest-drive-test.jsonl`  
预测：`/tmp/camvid-manifest-drive-test-ios-harness.jsonl`（重跑后，含 `twinlite_mask` 折线）  
对照：`docs/model-lab/figures/camvid-drive-test-frames/`

| 帧 | 紫线 | 黄线 | 绿 DA | 看图 |
|---|---|---|---|---|
| 0001TP_006690 | 无 insufficient | 2 条短，贴引擎盖/SEAT 旁 | 52 格贴引擎盖 | 公交车+SEAT 挡路，不画穿车假线 |
| 0001TP_008430 | 无 | 3 条短，未夹住占用车道 | 330 格贴近处 | 红厢式车堵左边 |
| 0001TP_009240 | 无 | 左轨斜上，不像右车道边 | 38 格贴引擎盖 | 前车贴脸 |
| 0006R0_f00960 | 有 twinlite_rails | 左右黄轨较完整 | 大片绿 | 空弯道，最像产品 |
| 0006R0_f02550 | 有，略右飘 | 几乎一条中线 | 停车场绿地毯 | 不像车道边 |
| 0006R0_f03450 | 有 | 有黄 | 有绿 | 近处有车 |
| 0016E5_04530 | 无 | 4 条，夹着前车 | 有绿 | 棕车挡路，不画紫是对的 |
| 0016E5_06630 | 有 ufld | UFLD 折线 | 有绿 | 城市场有线 |
| 0016E5_08047 | 有 ufld | UFLD | 有绿 | |
| Seq05VD_f01080 | 有 ufld | 双黄轨 | 有绿 | |
| Seq05VD_f02820 | 有 ufld | 双黄轨 | 有绿 | |
| Seq05VD_f04080 | 有 ufld | 双黄夹紫 | 有绿 | 最接近司机看见的两条边 |

漏报可走 12 / 全对 0：不是没跑，是 TwinLite DA 和 CamVid 真值逐格对不上。

## 已执行 / 待办

- 已看诊断台 + jsonl + 12 张叠图，排除「渲染延迟导致没输出」
- 几何门仍未过：0001TP 右轨、拥堵近场紫线（保持 insufficient）
- 未改算法。未提交对照图

更新时间: 2026-09-23 10:25
