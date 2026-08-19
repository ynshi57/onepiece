# 引导线闭环：首个 walk 口径基线（CamVid 701 帧）

- 日期：2026-08-19 · 主责：全麦
- 预测：设备真身 harness（YOLO11n + LocalTraversabilitySegmentation，camera-only）
- 真值：CamVid 语义标注 → 修正后 walk 口径可通行 mask → 中心线
- 命令：
  ```bash
  ios-vqa-app/perception-harness/.build/debug/PerceptionHarness \
    --manifest docs/datasets/camvid-manifest.jsonl \
    --model-dir ios-vqa-app/VQASee/VQASee \
    --out /tmp/camvid-manifest-ios-harness.jsonl
  python server-vqa/tools/run_ios_harness_eval.py \
    --manifest docs/datasets/camvid-manifest.jsonl \
    --predictions /tmp/camvid-manifest-ios-harness.jsonl \
    --baseline camvid-walk-v1
  ```

## 线级指标（both_ok=347 / 701）

| 指标 | 值 | 解读 |
|---|---|---|
| false_go_frames | **0** | 真值无路却预测有路 = 0，安全红线未破 |
| missed_path_frames | 353 | 真值有路、设备分割 `insufficient`，约半数帧没画出线 |
| hit_rate | 0.498 | 可比点落入真值走廊比例 |
| mean_deviation | 0.292 | 横向偏差大 |
| over_extension | 0.279 | 预测线越过真值自由空间的比例 |
| direction_error | 0.350 | 朝向差 |
| pred_coverage | 0.902 | 在成线帧里，预测覆盖了真值前向跨度的 90% |

## 诚实结论（已验证，非 mock）

1. **安全侧良好**：`false_go=0`，设备从不在真值无路处编造通路。
2. **主要差距是「口径 + 召回」**：
   - 分割模型的「自由空间」与 CamVid「路+人行道」语义定义不同 → `mean_deviation`
     偏大、`hit_rate` 仅约 0.5；
   - `missed_path=353`：约半数帧设备分割成不了线（可能阈值 `seg_traversable_pixel`
     偏高，或模型对该 driving 场景弱）。
3. 这些是**下一轮优化目标**，不是可以用 UI 文案掩盖的问题（符合 AGENTS 模型工程分析要求）。

## 下一轮候选实验（backlog，全麦）

- 调 `seg_traversable_pixel` 阈值扫描，看 `missed_path` vs `false_go` 的取舍曲线。
- walk vs drive 口径分别建基线，避免用 driving 数据评 walking 模型时口径错配。
- 引入更贴近行走视角的样例集（人行道/路沿/台阶），减少 driving 数据偏置。
- 线级平滑与走廊半宽校准，降低 `mean_deviation`。

门禁：`camvid-walk-v1-guidance` 已保存为基线；后续候选用
`--gate camvid-walk-v1` 时会同时对 `false_go/over_extension/mean_deviation/hit_rate`
做回归拦截。
