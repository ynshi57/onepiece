# VQASee Roadmap

## 北极星

把 VQASee 做成面向行人、骑行者、驾驶者、低视力用户和注意力可能分散场景的 iPhone 视觉风险辅助产品：能用、好用、实用、可靠、自然。

## 当前阶段

Phase 1：iPhone 实时视觉风险辅助 MVP。定位：视觉辅助工具，不是弱视专用；行走、骑行、驾驶风险、交通边界、读文字和环境理解优先。

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


### P0：语音问题边界与 Read Text

- 语音问题先做 intent classification：视觉问题 / 读文字 / 非视觉问题。
- 非视觉问题（日期、时间、天气、常识闲聊）不要硬套 VQA 场景描述，应提示“我主要帮你看画面”。
- Read Text 走 OCR-first：有 OCR 文本时先显示/播报文字，VLM 只做总结/解释/纠错。
- 药物说明书、路牌、包装等长文本场景必须有“逐段读/停止”的后续规划。

### P1：Surroundings 2s 体验目标

- 短期目标：Surroundings p50 明显低于 6s，优先接近 3s。
- 长期目标：2s 内给到第一句有用反馈。
- 允许两阶段：先快速粗略反馈，再补充详细描述。

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

### P3：闭环平台产品化（现场数据回流）

面向未来，把当前 demo 阶段的闭环实验平台升级为可承接真机现场事件的产品级闭环。架构参考与降维设计见
[`docs/tech-radar/2026-08-20-dcl-data-closed-loop-architecture.md`](tech-radar/2026-08-20-dcl-data-closed-loop-architecture.md)
（借鉴自动驾驶 DCL 的架构纪律，不引入 Kafka/Argo/ES 等重型基础设施）。

- 事件触发录制：设备端按「值得录」的条件（引导线 insufficient / 感知分歧 / 低置信 / 用户手动接管）触发 clip，
  触发规则复用 `PerceptionConfig` 下发；默认最小持久化、明确隐私同意。
- 前后端通信协议（一等公民）：版本化上行信封 `{protocol_version, clip_id, seq, config_version, kind, payload}`，
  `clip_id` 设备端确定性生成；`clip.open/chunk/close/ack` 消息族，服务端必须回写、不静默失败。**实现前先在 `docs/decisions` 立协议契约。**
- 断点续传：clip 分片 + 幂等 + chunk/整体双哈希校验 + `data_status` 状态机；弱网/切网恢复只补缺失片。
- case 层（**MVP 已落地 2026-08-20**）：eval 失败帧自动聚类成带确定性 id 和生命周期的 case，
  同一问题回归到超过验收帧数时自动重开。平台侧见 `server-vqa/app/case_store.py` 与
  `docs/evolution/2026-08-20-case-layer-mvp.md`；已回填两条真实 case（采样 bug 漏报 190→2 已验证、
  误阻挡 566 已分诊）。下一步：把 case 与设备端事件触发/上传协议打通。
- DCL 产品手册 V1.0 带来的增量（详见 tech-radar 卡片 §11）：retained「最后已知配置」真源语义、
  失败类型/标签**单一真源登记**、case **AutoTriage 回写疑似根因/优先级**、**现场帧与数据集帧分流**
  （防回流数据污染离线基线）。重型件（Kafka/EMQX/Mongo/ES/Argo）在万级 clip 前一律不引入。



- 语音优先；
- 可访问性优先，但不再限定为低视力用户；
- 安全变化不能被隐藏；
- 高级设置作为 fallback，不作为主流程；
- 每个重复问题必须变成测试、规则或文档沉淀。
