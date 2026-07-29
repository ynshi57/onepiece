# 2026-07-29 VQASee 核心质量闭环：从“能跑”到“可信赖”

## 反馈/问题

- 用户原话或现象：目前还有哪些改进项没有实现；希望按 VQASee 自我进化方式复盘、分配任务并执行。
- 场景：iPhone + Mac 本地后端，视觉辅助 MVP 已具备 nearby、语音、Qwen 3B/7B、OCR、双帧输入等能力。
- 模式：行走 / 周围 / 详细 / 读文字 / 语音问题均受影响。
- 影响：产品已从 demo 进入可用原型，但距离“低视力用户可依赖的工具”还有关键缺口。

## 事实与证据

- 日志/截图/测试：后端与 relay 测试当前可通过 `source .venv/bin/activate && pytest server-vqa/tests relay-server/tests`。
- 相关文件：
  - iOS：`Models.swift`, `StreamingViewModel.swift`, `Networking.swift`, `CameraCapture.swift`, `SpeechRecognitionController.swift`, `PressToTalkButton.swift`, `OCRRecognition.swift`。
  - 后端：`vqa_service.py`, `prompts.py`, `scene_context.py`, `signaling.py`, `worker_client.py`, `fusion.py`。
  - Relay：`relay_app/main.py`。
- 已知事实：
  - 已有 mode-aware 图像质量、自动模型选择、Apple Vision OCR、双帧输入、结构化 JSON schema、scene memory 和 speech gate。
  - iOS 子工程是嵌套 Git/gitlink；外层仓库和 iOS 内层仓库需要分开提交。
  - `InfoPlist.xcstrings` 是权限文案的来源，硬编码 `INFOPLIST_KEY_NS*UsageDescription` 会破坏本地化一致性。
- 合理猜测：
  - “模型不够智能”主要不是 UI 问题，而是模型路由、视觉评估集、目标/空间结构化感知和真实多帧评估未闭环。
  - “按住说话不好使”可能还需要真机音频电平证据，而不仅是手势代码修复。
- 需要验证：
  - 前端选择 7B 时后端是否真的路由到 7B runtime。
  - 读文字模式 OCR + VLM 的真实准确率。
  - 行走模式 p95 端到端延迟、风险漏报率、timeout rate。
  - push-to-talk 真机录音链路：按钮、麦克风、SFSpeech 三者哪个环节失败。

## 四角色判断

### 乔布斯：产品

- 判断：VQASee 已经有很多能力，但产品核心不是“功能多”，而是用户在行走和读文字时是否信任它。
- 风险：如果继续暴露模型名、调试细节、长文本结果，产品会像工程 demo；如果模型错误无法被度量，用户不敢依赖。
- 建议：把下一轮聚焦为“信任闭环”：真实模型路由、行走安全状态、读文字可靠性、push-to-talk 可见反馈。
- 需要证据：现场测试视频/截图、每次识别的模式/模型/延迟/输出、失败样例。

### 罗根：系统/性能

- 判断：最大系统风险是“看起来选了某模型，但 runtime 未必真的加载该模型”；其次是缺少 p95 延迟和模型路由状态。
- 风险：用户选择 7B 但实际未运行 7B，会误判模型质量；行走模式若 p95 太高，会影响安全信任。
- 建议：新增 backend runtime status / model availability；iOS 只显示可用模型；采集 end-to-end、model、timeout、reconnect 指标。
- 需要证据：`/runtime/status` 返回、llama-server 端口/模型映射、真实 p50/p95 数据。

### 思余：UI/可访问性

- 判断：行走模式仍需要安全状态语言，而不是完整描述；push-to-talk 需要“正在听见你”的可视/触觉反馈。
- 风险：低视力用户无法判断 App 是没听见、没识别、没发送，还是模型没回答。
- 建议：主卡片增加三态：`可前行 / 放慢 / 停下`；push-to-talk 增加录音电平/波形和失败原因。
- 需要证据：真机按住说话时的录音电平、SFSpeech partial/final 回调、VoiceOver 读屏顺序。

### 全麦：模型/后端

- 判断：模型核心缺口是缺少固定评估集和真实路由；prompt/schema 改动目前没有可量化质量回归。
- 风险：每次调 prompt 都凭感觉，可能提升一个场景、破坏另一个场景；Qwen 3B/7B 的能力边界不清。
- 建议：建立 30 个视觉辅助评估样例；按 walking/surrounding/detail/read-text/voice question 定义 must-mention/risk/action 标准；记录到 model-lab。
- 需要证据：样例图、期望 JSON、3B/7B 输出对比、延迟对比、人工评分。

## 优先级

P0：真实模型路由 + push-to-talk 可诊断性。

理由：一个影响“看得准”的真实性，一个影响 voice-first 核心交互；两者不解决，后续模型/UI 讨论都会失真。

P1：视觉辅助评估集 + 行走安全状态 + 读文字模式产品化。

理由：决定核心任务能否持续变好。

## 最小实验

- 假设：如果后端显式暴露可用模型并让 iOS 只显示可用模型，则用户对 3B/7B 的质量判断会变得可信。
- 改动：新增 `/runtime/status`；后端支持 3B/7B runtime 映射；iOS 设置页用 status 驱动模型选择。
- 成功标准：iOS 选择 7B 时，后端日志/status 均显示实际使用 7B；7B 不可用时 UI 不显示或标记不可用。
- 失败处理：如果双 runtime 成本过高，退回“单 runtime + 明确当前模型”策略，不允许假自动路由。

## 实际改动

本轮执行的是知识沉淀和任务分配，不新增业务代码：

- 文件：
  - `docs/evolution/2026-07-29-vqasee-core-quality-closure.md`
  - `docs/model-lab/2026-07-29-model-quality-evaluation-plan.md`
  - `docs/performance/2026-07-29-runtime-routing-latency-audit.md`
  - `docs/ui-lab/2026-07-29-push-to-talk-and-walking-safety-ui.md`
  - `docs/roadmap.md`
- 内容：记录缺陷、四角色判断、P0/P1 backlog、最小实验和验证指标。

## 验证

- 自动测试：`source .venv/bin/activate && pytest server-vqa/tests relay-server/tests`
- 人工测试：下一轮需在 iPhone 真机验证 push-to-talk 电平、7B 路由、读文字 OCR。
- 结果：待本轮文档落地后运行自动测试。

## 沉淀

- 代码：本轮不改业务代码。
- 测试：下一轮优先新增 runtime status、model route、prompt eval tests。
- AGENTS.md：已有长期原则，无需本轮追加。
- Skill：已有自我进化和模型实验 skill，无需本轮追加。
- docs/decisions：已有记忆系统决策，无需新 ADR。
- docs/model-lab：新增模型评估计划。
- docs/ui-lab：新增 push-to-talk 与行走状态 UI 记录。
- docs/performance：新增 runtime/latency 审查记录。
- roadmap：新增 P0/P1 可执行 backlog。

## 下一轮进化

1. P0：实现后端 runtime status 和真实 3B/7B 路由。
2. P0：给 push-to-talk 增加录音电平/失败原因 UI。
3. P1：建立 30 个视觉辅助评估样例并接入测试。

## 2026-07-29 执行追加：P0 最小实现

### 已执行

- 罗根 + 全麦：后端新增 `/runtime/status` truth source；direct llama-server 单模型 runtime 不再接受每帧 model override 造成“假切换”。
- 全麦：`run_vqa_from_frame` 返回 `requested_model / resolved_model / model_routing_reason`，用于后续 UI/debug/日志确认。
- 思余 + 罗根：push-to-talk 增加麦克风输入电平；按钮按住时显示电平条；空识别时区分“没有检测到声音”和“没有听清”。

### 改动文件

- 后端：
  - `server-vqa/app/vqa_service.py`
  - `server-vqa/app/main.py`
  - `server-vqa/app/fusion.py`
  - `server-vqa/app/models.py`
  - `server-vqa/tests/test_api.py`
  - `server-vqa/tests/test_vqa_service.py`
- iOS：
  - `ios-vqa-app/VQASee/VQASee/SpeechRecognitionController.swift`
  - `ios-vqa-app/VQASee/VQASee/StreamingViewModel.swift`
  - `ios-vqa-app/VQASee/VQASee/PressToTalkButton.swift`
  - `ios-vqa-app/VQASee/VQASee/AssistanceScreen.swift`

### 新的产品规则

- Direct llama-server (`:11435`) 是单模型 runtime；不能让前端以为它能动态切换 3B/7B。
- Push-to-talk 失败不能只说“没听清”；必须尽可能区分麦克风无输入和语音识别失败。

### 仍需下一轮

- iOS 设置页读取 `/runtime/status` 并隐藏/禁用不可用模型。
- 真机验证麦克风电平条、语音识别权限、按住说话完成链路。
- 若要同时支持 3B/7B 真路由，需要双 runtime 或 Ollama/cloud dynamic routing。

## 2026-07-29 执行追加：看板与联调计划

已新增独立执行面板：

- `docs/evolution/2026-07-29-execution-board-collaboration-plan.md`

该文档包含：

- P0/P1/P2 执行看板；
- 乔布斯/罗根/思余/全麦的任务边界；
- iOS ↔ Backend ↔ Relay 的协作接口；
- Runtime Truth、Push-to-talk、Walking、Read-text 的联调步骤；
- 风险清单和验收清单。
