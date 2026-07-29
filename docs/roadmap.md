# VQASee Roadmap

## 北极星

把 VQASee 做成低视力用户愿意每天使用的 iPhone 视觉辅助产品：能用、好用、实用、可靠、自然。

## 当前阶段

Phase 1：iPhone 实时视觉辅助 MVP。

核心能力：

- iPhone 摄像头实时取帧；
- 本地 Mac VQA backend；
- nearby 自动发现；
- relay 跨网络连接；
- 行走、周围、详细、读文字模式；
- 语音优先交互；
- 场景记忆和变化播报。

## 路线图原则

1. 先让真实用户能稳定完成核心任务，再增加功能。
2. 每个功能必须有失败状态和恢复路径。
3. 每次迭代都要沉淀：测试、文档、规则或 skill。
4. 不把技术复杂度暴露给普通用户。

## 近期方向

### P0：真实模型路由与可用性

- iOS 设置页读取 `/runtime/status`，只展示真实可用模型。
- direct llama-server 单模型 runtime 下，不允许 UI 暗示可动态切换 3B/7B。
- VQA 结果 debug 中显示 `requested_model / resolved_model / model_routing_reason`。
- 联调记录见：`docs/evolution/2026-07-29-execution-board-collaboration-plan.md`。

### P0：按住说话可诊断性

- push-to-talk 显示麦克风电平/波形。
- 区分失败原因：未授权、没检测到声音、语音识别失败、后端未回答。
- 真机调参电平阈值，避免“明明说话却提示没声音”。

### P0：安全和稳定

- walking 模式风险提醒可靠性；
- timeout 后状态恢复；
- backend/relay 断开后的可见反馈和自动恢复；
- 不因 speech suppression 隐藏安全变化。

### P1：首次使用体验

- nearby mode 默认路径；
- 减少高级设置依赖；
- 清晰的连接、识别、超时、重连状态；
- 第一次听到有效反馈的时间优化。

### P1：模型和 prompt

- 建立 30 个视觉辅助评估样例，覆盖 walking、surrounding、detail、read-text、voice question。
- 每个样例包含 mode、期望 risk/action/must-mention、3B/7B 输出和延迟记录。
- walking 风险优先；
- read-text 稳定读文字；
- 周围模式空间布局清晰；
- voice question 单次问题准确回答；
- prompt regression tests。

### P2：Apple 级 UI 打磨

- 主界面降噪；
- VoiceOver 和 Dynamic Type；
- 用户文案自然化；
- 高级设置重新组织。

### P2：可观测性

- 端到端 latency 记录；
- p50/p95 指标；
- timeout rate；
- reconnect success rate；
- 模型耗时和错误分类。

## 已确认的长期方向

- 语音优先；
- 低视力用户优先；
- 安全变化不能被隐藏；
- 高级设置作为 fallback，不作为主流程；
- 每个重复问题必须变成测试、规则或文档沉淀。
