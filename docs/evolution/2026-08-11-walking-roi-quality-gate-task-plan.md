# Walking ROI / 图像质量门控开发验证任务

Date: 2026-08-11

## 反馈/问题

- 用户原话或现象：Qwen 模型延迟高；已实现 walking 近处通行路径 `risk_zone`，但剩余风险是视觉编码仍慢、模型不能真实测距、缺少 ROI / 本地质量门控和真实 A/B 验证。
- 场景：行走 / 骑行 / 注意力分散的近处通行风险提醒。
- 模式：`walking` 优先；后续可扩展到 riding / driving-risk。
- 影响：如果每帧都把整图送 Qwen，端到端延迟仍可能高；如果只靠模型粗判 risk_zone，可能漏掉侧向车辆、行人、开门、台阶和路沿。

## 乔布斯先定方向

下一阶段只做一个产品闭环：**先在 walking 模式让用户更快知道“画面是否可用、近处哪里可能危险、是否需要放慢”。**

不做：

- 不承诺“3 米内”；
- 不输出精确米数；
- 不做大重构；
- 不把 ROI 裁剪变成静默丢弃风险。

做：

1. 图像质量可见：模糊、过暗、遮挡要先提示。
2. ROI 可控：walking 优先关注近处通行路径，但保留安全兜底。
3. 性能可量化：记录 p50/p95、timeout、drop、JSON 成功率。
4. 安全可验证：用真实/近真实样例查漏报。

## 四角色 review

### 罗根：系统/性能

有条件同意。`risk_zone` schema 只能减少输出发散，不能根治视觉 prefill。必须追加：

- iOS 端或后端质量检测；
- ROI 裁剪或 ROI 元数据；
- latest-frame-wins / timeout 指标；
- 忙时丢帧的安全验证。

质疑：如果 ROI 只裁中下区域，侧向风险可能被裁掉。因此 P0 先做 **质量门控 + ROI 元数据**，裁剪必须可开关；默认不静默裁掉整图安全信息。

### 思余：UI/可访问性

有条件同意。失败和不确定必须用户可见、语音可听：

- “画面有些糊，请放慢”；
- “光线太暗，我看不清前方”；
- “近处正前方疑似有障碍，请放慢”。

不允许出现技术化文案：`blur_score`、`ROI`、`timeout`。

### 全麦：模型/后端

同意。Qwen 负责语义风险和粗略区域，不负责真实测距。后端要保留：

- `risk_zone` / `direction` / `distance_confidence`；
- 不确定时 `unknown` / `none` / `low`；
- 不输出 `distance_m`。

模型评测必须看漏报，而不是只看回答是否漂亮。

### 乔布斯最终裁决

按两条并行线推进：

1. **P0 开发线**：质量门控 + ROI 元数据 + 后端指标，不先强制裁剪。
2. **P0 验证线**：30～50 张 walking 风险样例 A/B，先证明不会因为 near-path 策略漏掉关键风险。

## 任务卡

### T1：Walking 图像质量门控 MVP

- 主责：罗根
- 配合：思余 / 全麦
- 改动范围：优先 iOS 摄像头链路；如 iOS 当前不便，先在 `server-vqa` 增加后端 diagnostic helper 做离线验证。
- 目标：在送 Qwen 前判断画面是否明显不可用。
- 最小输出：

```json
{
  "frame_quality": {
    "blur": "ok|blurry|unknown",
    "exposure": "ok|too_dark|too_bright|unknown",
    "occlusion": "ok|covered|unknown",
    "usable_for_walking": true,
    "confidence": "low|medium|high"
  }
}
```

- 用户可见要求：
  - 模糊：`画面有些糊，请放慢。`
  - 过暗：`光线太暗，我看不清前方。`
  - 遮挡：`镜头可能被挡住了。`
- 验收标准：
  - 明显糊图 / 黑图 / 遮挡图能触发；
  - 不因轻微运动频繁打断用户；
  - 质量异常不静默失败；
  - 自动测试覆盖阈值和 fallback。
- 最窄验证：
  - 后端 helper：`source .venv/bin/activate && pytest server-vqa/tests/test_frame_quality.py`
  - iOS：`bash deploy/ios/test.sh`

### T2：Walking ROI 元数据，不默认硬裁剪

- 主责：罗根
- 配合：全麦 / 思余
- 改动范围：iOS frame message / backend prompt context / tests。
- 目标：让后端知道 walking 的近处通行路径区域，但第一版不强制只传 ROI。
- 最小输出：

```json
{
  "walking_roi": {
    "coordinate_space": "normalized_image",
    "near_path": {"x": 0.20, "y": 0.45, "w": 0.60, "h": 0.55},
    "left_front": {"x": 0.00, "y": 0.40, "w": 0.35, "h": 0.60},
    "right_front": {"x": 0.65, "y": 0.40, "w": 0.35, "h": 0.60}
  }
}
```

- 验收标准：
  - backend prompt 能加入 ROI 说明；
  - 模型输出仍允许报告 ROI 外但安全相关的人/车/开门；
  - 不因为 ROI 元数据导致旧客户端失败。
- 安全边界：
  - P0 只传元数据；
  - P1 才评估 ROI 裁剪；
  - 任何裁剪都必须有 A/B 漏报验证。
- 最窄验证：
  - `source .venv/bin/activate && pytest server-vqa/tests/test_worker_client.py server-vqa/tests/test_scene_context.py server-vqa/tests/test_vqa_service.py`
  - iOS message tests / `bash deploy/ios/test.sh`

### T3：后端性能指标分解

- 主责：罗根
- 配合：全麦
- 改动范围：`server-vqa/app/worker_client.py`、`server-vqa/app/vqa_service.py`，必要时 `fusion.py`。
- 目标：不要只看总 `latency_ms`，拆出关键耗时。
- 最小输出：

```json
{
  "latency_ms": 1234,
  "metrics": {
    "qwen_http_ms": 1000,
    "fusion_ms": 2,
    "frame_bytes": 345678,
    "schema_name": "vqa_walking_fast_result",
    "dropped_reason": "worker_busy|none"
  }
}
```

- 验收标准：
  - 不暴露到用户主文案；
  - diagnostic / logs 可见；
  - timeout 和 worker_busy 可统计；
  - 不破坏现有 response 兼容。
- 最窄验证：
  - `source .venv/bin/activate && pytest server-vqa/tests/test_worker_client.py server-vqa/tests/test_vqa_service.py server-vqa/tests/test_fusion.py`

### T4：Walking near-path A/B 样例集

- 主责：全麦
- 配合：罗根 / 思余
- 改动范围：`docs/model-lab/`、`docs/performance/`、测试 fixture 或 diagnostic capture manifest。
- 目标：用 30～50 张真实/近真实 walking 帧验证 near-path 策略。
- 样例必须覆盖：
  - 台阶 / 楼梯；
  - 路沿 / 坑洼 / 地面边缘；
  - 正前方人 / 侧向行人；
  - 车辆 / 自行车 / 电动车；
  - 开门 / 玻璃门；
  - 模糊 / 过暗 / 遮挡；
  - 文字标志但非 readText 场景。
- 记录字段：

```text
frame_id
mode
真实风险标签
期望 risk_level
期望 risk_zone
期望 direction
是否允许 unknown
当前 schema 输出
near-path schema 输出
latency_ms
是否漏报
备注
```

- 验收标准：
  - `risk_level=low` 但真实有风险的样例必须人工复盘；
  - 侧向车辆/行人不得因 near-path 被系统性忽略；
  - `spoken_text` 平均长度下降或不变；
  - JSON 成功率不下降。
- 最窄验证：
  - 先人工表格 + diagnostic capture；
  - 后续沉淀自动评测脚本到 `server-vqa/tools/`。

### T5：UI / 语音失败可见策略

- 主责：思余
- 配合：罗根 / 全麦
- 改动范围：iOS 状态显示、语音播报策略、VoiceOver 文案。
- 目标：质量异常、模型超时、距离不确定都可见，但不吓人、不刷屏。
- 文案原则：
  - 简短；
  - 不技术化；
  - 不说“安全”；
  - 不输出米数。
- 建议文案：
  - 模糊：`画面有些糊，请放慢。`
  - 过暗：`光线太暗，我看不清前方。`
  - 超时：`我还在确认，请先放慢。`
  - 距离不确定：`前方信息不够清楚，请放慢确认。`
- 验收标准：
  - VoiceOver 标签自然；
  - Dynamic Type 不挤压主按钮；
  - 不连续重复同一句超过策略阈值；
  - 用户能区分“处理中 / 看不清 / 已断开”。
- 最窄验证：
  - `bash deploy/ios/test.sh`
  - 真机听感检查：室内、街边、低光、走动。

## 跨角色接口

```text
iOS Camera Frame
→ frame_quality / walking_roi metadata
→ relay or direct websocket
→ backend resolve_prompt + scene_context
→ Qwen walking fast schema: risk_zone / direction / distance_confidence
→ fusion + metrics
→ iOS state / speech / haptic
→ diagnostic capture + A/B table
```

接口约定：

- `risk_zone` 不是米数；
- `distance_confidence=none|low` 时 UI/语音只能表达不确定；
- frame quality 异常可以短路语音提醒，但不能永久阻止后续帧重试；
- ROI 元数据不能让模型忽略 ROI 外的安全相关人/车/开门。

## 联调计划

1. 后端先接受并透传 `walking_roi` / `frame_quality`，旧客户端不传也不失败。
2. iOS 开启 debug overlay 显示 ROI 与质量状态，仅 debug 可见。
3. 用 diagnostic capture 录制 30～50 帧。
4. 跑 A/B：当前 full-frame near-path prompt vs ROI metadata prompt。
5. 复盘漏报，再决定是否进入 P1 ROI 裁剪。

## 优先级

- P0：T1、T2、T3、T4。
- P1：T5，可和 T1 并行，但上线前必须完成。
- P2：真正 ROI 裁剪、CoreMotion 地面距离粗估、ARKit/LiDAR 深度。

理由：先保证看不清和慢的问题可见、可量化；再谈更激进压缩。

## 成功标准

- 自动测试通过；
- walking 结果包含 `risk_zone/direction/distance_confidence`；
- 质量异常有用户可见路径；
- A/B 样例中没有新增系统性高风险漏报；
- p50/p95、timeout、worker_busy 可记录；
- 不出现“3 米 / 1.5 米 / 可以走 / 安全通过”。

## 失败处理

- 如果 near-path 导致侧向风险漏报：保留 full-frame，ROI 只做排序，不做裁剪。
- 如果质量门控误报太多：降低播报频率，只在连续异常后提示。
- 如果指标埋点增加响应体负担：只写 diagnostic/log，不进入用户响应主字段。
- 如果 Qwen 对 `risk_zone` 不稳定：保留字段但 UI 只消费 `risk_level/spoken_text`，继续收集样本。

## 沉淀

- 代码事实：后续进入 iOS / backend 代码和测试。
- 产品决策：不输出精确米数，已沉淀到 `docs/model-lab/2026-08-11-walking-near-path-risk-zone.md`。
- 本计划：`docs/evolution/2026-08-11-walking-roi-quality-gate-task-plan.md`。
- 性能经验：T3 完成后沉淀到 `docs/performance/`。
- UI 经验：T5 完成后沉淀到 `docs/ui-lab/`。

## 2026-08-11 执行记录

已完成后端 P0 子集：

- 新增 `server-vqa/app/frame_metadata.py`，规范化 `frame_quality` 与 `walking_roi`。
- direct WebSocket 与 relay worker 均可接收元数据。
- walking 高/中置信质量异常可短路 Qwen，返回自然语音提示。
- ROI 只作为 prompt 元数据，不裁剪图像。
- fused response 透传 `diagnostic_metrics` 便于统计延迟和 quality gate。
- 新增后端测试覆盖质量门控、ROI prompt、安全兜底和 metrics。

仍未完成：

- iOS 端真实 `frame_quality` 计算与 UI/语音策略联调。
- 真机 ROI overlay 校准。
- 30～50 张 walking A/B 样例集。
- p50/p95 真实设备统计。
