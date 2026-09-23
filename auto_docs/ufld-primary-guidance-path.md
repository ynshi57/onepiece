# Phase B + P0：UFLD 主路径 + TwinLite occupied rails 回退

## 最终结论

产品引导线分层（用户批准 P0）：

1. **UFLD** 车道 1+2 中线 → `source=ufld`
2. **TwinLite occupied_lane_rails**（G2 中轨，非 DA 中心线）→ `source=twinlite_rails`
3. 全部失败 → `status=insufficient`（宁缺毋滥：不画假线）

## 已执行

- `TwinLiteOccupiedRails.swift`：Swift 移植 `occupied_lane_rails`（mask row-anchor + curb + pair + track + G2）
- `RoadSurface.swift`：掩码最近邻上采样到相机分辨率（对齐 Python `infer_twinlite_masks`）后跑 rails
- `LocalVisionAnalyzer`：融合 UFLD > twinlite_rails；DA 中心线退出产品路径
- 诊断台 frames UI：展示 `guidance_path.source`
- Harness 12 帧：`/tmp/camvid-manifest-drive-test-ios-harness-rails.jsonl`

## 12 帧 harness（drive-test）

| 结果 | 帧 |
|---|---|
| `ufld` ok (5) | 0016E5_06630, 0016E5_08047, Seq05VD_f01080, Seq05VD_f02820, Seq05VD_f04080 |
| `twinlite_rails` ok (3) | **0006R0_f00960, 0006R0_f02550, 0006R0_f03450** |
| insufficient (4) | 0001TP_006690, 0001TP_008430, 0001TP_009240, 0016E5_04530 |

**8/12** 有产品引导线（原 DA 回退 7/12 含锯齿；0006R0 三帧现由 rails 覆盖）。

## 看图验收

| 帧 | source | 说明 |
|---|---|---|
| 0006R0_f00960 | twinlite_rails | UFLD 漏检；rails 288 点，cov≈0.40 → `docs/model-lab/figures/twinlite-preview/0006R0_f00960-p0-rails.jpg` |
| Seq05VD_f02820 | ufld | max Δx≈0.01；对照 `Seq05VD_f02820-phase-b-ufld.jpg` |

## 待办

- P1：UFLD 域微调（drive-test 命中率 ≥80%）
- P2：UFLD 体积 / 真机 ANE 延迟
- 0001TP 三帧 rails 仍 insufficient（006690 大转角门控 intentional）
- App Xcode 真机编译未验证

更新时间: 2026-09-22 12:45
