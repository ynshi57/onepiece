# Walking 近处通行路径风险区间

日期：2026-08-11

## 乔布斯裁决

walking 模式不要让模型输出“3 米内”“1.5 米”等单目图像无法可靠支持的精确距离。用户真正需要的是：近处哪里可能有风险、该不该放慢、往哪个方向注意。

## 模型规则

- 只关注用户即将经过的近处通行路径，尤其是画面下半部、中心、左前方和右前方。
- 不输出具体米数。
- 使用枚举字段表达粗略区域：
  - `risk_zone`: `immediate | near | mid | far | unknown`
  - `direction`: `left | center | right | left_front | right_front | front | unknown`
  - `distance_confidence`: `none | low | medium | high`
- 不确定时使用 `unknown` / `none` / `low`，语音里说“疑似”“近处”“前方”“无法判断”。

## 为什么不是精确测距

Qwen/VQA 模型从单帧图像不能稳定推断真实米数。精确或半精确距离应来自 iOS 端相机内参、姿态、ARKit/LiDAR 或连续帧几何估计；模型字段只能作为风险排序和语音表达的粗略线索。

## 后续实验

- 用真实行走帧 A/B 当前 walking 与 near-path schema。
- 记录 `latency_ms` p50/p95、JSON 成功率、`risk_zone=unknown` 比例。
- 重点检查台阶、路沿、车辆、行人、小障碍的漏报率。
