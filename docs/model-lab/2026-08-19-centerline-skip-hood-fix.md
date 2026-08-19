# Model Lab：中心线「跳底部空行」修复 + 引导线门禁静默失效修复

- 日期：2026-08-19
- 主责：全麦（算法/评测），配合 罗根（设备帧预算/性能）、乔布斯（召回 vs 精度取舍裁决）
- 触发：`missed_path_frames=353`（约半数帧「没有引导线」）。诊断发现 75% 的
  `insufficient` 帧 coverage=0，但其中 84% 的近路状态是 candidateOpen/caution
  ——即「明明有可通行像素，中心线却一条都没画出来」。

## 归因

旧 `centerline_from_mask` / Swift `GuidancePathBuilder.centerline` 从画面最底行
（用户脚下）向上逐行采样，**遇到第一条没有可通行像素的行就 break**。在 driving 视角
里，最底部常是车头盖 / 近景遮挡，于是整条线在起点即被判死，哪怕上方就是清晰的路。

## 修改（Python 真值 + Swift 引擎，同一算法）

- **跳过底部起始空行**：还没锚定起点时遇到空行 → `continue`（跳过车头盖 / 近景遮挡），
  直到找到第一条可通行行作为起点。
- **保留中景 break**：一旦开始画线，遇到空行仍然 `break`——绝不跨越前方障碍把线连过去
  （否则等于把路画穿障碍物，安全红线）。

单测锁死两个方向（`test_guidance_path.py`）：
- `test_skips_blocked_bottom_rows_like_a_car_hood`：底部封死、上方有路 → 成线。
- `test_interior_gap_stops_line_and_is_never_bridged`：近路+远路中间隔障碍 →
  线停在近路，绝不跳到远路。

## 性能优化（设备端实时路径）

1. **Swift 中心线单趟扫描**：去掉每采样行 `[Bool]` 掩码分配 + 独立 `runs()`，
   改为一次 O(width) 扫描内联挑选「距上一中心最近的可通行段」，O(1) 额外空间。
2. **`LocalSegmentation` 去掉全图拷贝**：原实现每帧把整张 width×height 分割图拷成
   `[Double]`（512² ≈ 26 万个 Double / 帧）。cue 只采粗网格、中心线只采 ≤16 行，
   绝大多数像素根本用不到。改为**锁定期直读** base 指针，按需取样，零全图分配。

## 评测（701 帧 CamVid walk，真身 Core ML harness）

| 指标 | before | after | 说明 |
|---|---|---|---|
| both_ok | 347 | **588** | 有效对比帧 +241 |
| missed_path_frames | 353 | **113** | 「没有引导线」减少 240（-68%）|
| false_go_frames | 0 | **0** | 安全：从不在真值无路处宣称有路 |
| hit_rate | 0.498 | 0.390 | ↓（见下「诚实取舍」）|
| mean_deviation | 0.292 | 0.322 | ↑（见下）|
| over_extension | —（基线缺失）| 0.279 | 新建立的可观测量，下一轮重点 |
| 区域 status_accuracy | 0.379 | 0.379 | 不变（区域状态不依赖中心线）|
| 区域 risk_miss / false_block | 190 / 747 | 190 / 747 | 不变 |

## 诚实取舍（不粉饰）

- `hit_rate` 下降、`mean_deviation` 上升**不是既有帧变差**：算法在「无底部空行」时与旧版
  逐位一致，原 347 个 both_ok 帧的线**完全没变**（GT 仅恢复 1 帧）。变化 100% 来自
  **新增的 241 个更难帧**拉低了 both_ok 集合的平均——是构成变化，不是回归。
- 换言之：我们在 240 个「原本一条线都没有」的帧上给出了引导线，代价是这些更难帧本身
  精度更低。对「视觉引导优先」的产品，这是 recall 优先的合理取舍，且 `false_go=0`。
- **待观察**：`over_extension=0.279`（约 28% 的预测线尾部越过真值自由空间）。帧级
  `false_go=0`，但线尾越界仍是下一轮的安全相关重点。

## 附带修复：引导线门禁此前是「静默失效」

沉淀本轮时发现 `run_ios_harness_eval` 的 guidance 门禁形同虚设，违反「不允许静默失败」：

1. `save_baseline` 硬编码只存区域 `TRACKED_METRICS`，把 guidance 指标全丢成 `null`。
2. `gate_guidance` 读的是基线 payload 外层，而指标嵌在 `metrics` 下——层级错位。

两者叠加导致门禁始终拿 `None` 比较、恒过。修复：

- `save_baseline` 增加 `metric_keys` 参数，guidance 基线按 `GUIDANCE_BASELINE_KEYS` 快照。
- `gate_guidance` 自动解包 `{"metrics": {...}}` payload（与区域门禁一致）。
- 新增 `test_saved_guidance_baseline_actually_gates`：存→取→gate 全链路验证 false_go
  回归确实被拦（守住这个 bug 不再复发）。

此后 `camvid-walk-v1-guidance` 基线首次持有**真实** guidance 指标，门禁真正生效。

## 验证命令

```bash
source .venv/bin/activate && pytest server-vqa/tests            # 196 passed
cd ios-vqa-app/perception-harness && swift build               # 通过
# 全量重跑 + 评测（macOS，真身 Core ML）
python server-vqa/tools/run_ios_harness_eval.py \
  --manifest docs/datasets/camvid-manifest.jsonl \
  --predictions /tmp/camvid-manifest-ios-harness.jsonl --gate camvid-walk-v1
```

## 下一轮 backlog

- `over_extension` 收敛：线尾越界的风险段标注 + 惩罚。
- 「自由空间」口径 vs 语义「路/人行道」的系统性偏差（walking 数据集）。
- 设备端实时渲染 + 「向左半步」语音（思余/罗根）。
