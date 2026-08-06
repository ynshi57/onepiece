# 2026-07-29 VQASee 执行看板 / 协作接口 / 联调计划

## 闭环结论

VQASee 下一轮不是继续堆功能，而是把“可信赖视觉辅助”拆成 3 条可并行主线：

1. **真实模型能力**：用户选到的模型必须是真实运行的模型，模型质量必须可评估。
2. **可靠交互**：按住说话必须可诊断，行走模式必须先给安全状态。
3. **可观测联调**：端到端延迟、模型路由、OCR、超时和失败原因必须能被看到。

## 事实与证据

- 已知事实：
  - 后端已有 `/runtime/status`，可暴露 runtime truth source。
  - direct llama-server `:11435` 是单模型 runtime，不能假装每帧动态切 3B/7B。
  - iOS 已有 push-to-talk 音频电平、OCR、mode-aware encoding、双帧输入。
  - 后端/relay 自动测试当前通过：`63 passed`。
- 合理猜测：
  - 当前体验风险主要来自“用户以为在用 7B，但实际未必”；“用户以为 App 在听，但无法判断麦克风是否进音”。
  - 读文字和行走风险的效果需要评估集，不然 prompt/model 迭代没有方向。
- 需要验证：
  - 真机上 push-to-talk 电平条是否有反应，阈值 `0.08` 是否合理。
  - iOS 是否能读取 `/runtime/status` 并隐藏/禁用不可用模型。
  - 3B/7B 在同一批视觉辅助样例上的质量/延迟对比。

## 核心能力定位

- 看得准：模型评估集、结构化空间/风险输出、OCR + VLM 融合。
- 反应快：runtime status、端到端 latency、3B/7B 路由和 image/token budget。
- 说得对：walking 安全状态优先，voice question 优先回答问题。
- 用得住：push-to-talk 可诊断，连接/模型/权限状态可见。

## 乔布斯先定方向

- 产品判断：VQASee 的下一步是“建立信任”，不是增加更多设置。
- 本轮目标：把任务拆成可执行看板，明确每项协作接口和联调步骤。
- 指派：
  - 罗根主责：runtime status、模型路由、性能指标、联调脚本。
  - 全麦主责：模型评估集、prompt/schema、3B/7B 质量对比。
  - 思余主责：push-to-talk 可诊断 UI、walking 安全状态、设置页降噪。
- 本轮不做：不引入新云模型；不做 Mac companion app；不做完整用户系统。

## 员工反馈

### 罗根：系统 / 性能 / 架构

- 判断：必须先让系统知道“实际运行模型”和“每帧耗时”。
- 风险：如果没有 truth source，模型体验讨论全是错觉。
- 建议：iOS 消费 `/runtime/status`；后端记录 requested/resolved model；建立联调 checklist。
- 需要证据：runtime status 截图/JSON、p50/p95 latency、timeout 数。

### 思余：UI / 交互 / 可访问性

- 判断：主界面应该隐藏工程模型名；设置页也只能展示真实可用能力。
- 风险：用户误选不可用 7B、按住说话无反馈，会破坏信任。
- 建议：模型显示改成“自动/当前：快速/清晰”；push-to-talk 电平条 + 触觉反馈；walking 三态。
- 需要证据：真机录屏、VoiceOver 顺序、Dynamic Type 下按钮可触达。

### 全麦：模型 / Prompt / Qwen 3B/7B

- 判断：模型质量需要固定评估集；读文字、行走、周围、详细、语音问题要分开评估。
- 风险：prompt 调优没有回归，可能越调越偏。
- 建议：先建 30 个样例的 schema 和人工期望；不要求立即自动跑真实模型，但必须能记录输出。
- 需要证据：每个样例的 input/mode/model/output/latency/人工评分。

## 乔布斯最终裁决

- 优先级：
  - P0：iOS 消费 `/runtime/status`，真实模型可用性展示。
  - P0：push-to-talk 电平真机验证与失败原因闭环。
  - P1：30 样例模型评估集。
  - P1：walking 安全状态卡。
  - P1：read-text OCR + VLM 结果展示与失败引导。
- 本轮做：任务拆解、协作接口、联调计划、roadmap 更新。
- 本轮不做：不继续扩 schema；不新增大型架构；不强推双 runtime。
- 主责：罗根统筹联调；全麦负责模型质量；思余负责用户体验。
- 成功标准：每个任务有输入/输出/验收/验证命令/人工联调步骤。
- 失败转向：如果双 runtime 不现实，产品改为“当前实际模型”而不是“模型选择器”。

## 执行看板

| ID | 优先级 | 状态 | 主责 | 配合 | 任务 | 交付物 | 验收标准 | 验证 |
|---|---|---|---|---|---|---|---|---|
| OP-001 | P0 | Done | 罗根 | 全麦/思余 | iOS 读取 `/runtime/status`，显示真实可用模型 | `RuntimeStatus` client + Settings UI | direct runtime 只显示当前模型；dynamic endpoint 才显示 3B/7B | 后端测试通过；iOS 需真机确认 |
| OP-002 | P0 | Ready | 思余 | 罗根 | push-to-talk 真机电平验证与阈值调参 | 电平条、触觉反馈、失败原因文案 | 按住 200ms 内状态变化；无声音提示“没有检测到声音” | iPhone 真机录屏 |
| OP-003 | P1 | Ready | 全麦 | 乔布斯 | 建立 30 个视觉辅助评估样例结构 | `docs/model-lab/eval-set-v1/` 或 JSONL | 每个样例有 mode、期望 risk/action/must-mention | pytest 校验样例格式 |
| OP-004 | P1 | Ready | 全麦 | 罗根 | 3B/7B 输出与延迟对比 runner | eval runner 脚本/文档 | 同样例记录 resolved_model、latency、输出 | 手动模型跑批 |
| OP-005 | P1 | Ready | 思余 | 全麦 | walking 安全状态卡 | `可前行/放慢/停下` UI | 主卡片第一行先读安全状态 | VoiceOver + 真机 |
| OP-006 | P1 | Ready | 思余 | 全麦 | read-text 结果产品化 | OCR 文本展示/播报/失败引导 | OCR 为空时提示靠近/对准/增光 | 真机文字样例 |
| OP-007 | P2 | Backlog | 罗根 | 全麦 | 双 runtime 可行性评估 | 3B/7B 同时运行内存/延迟报告 | 16GB Mac 不明显 swap 才推进 | performance lab |
| OP-008 | P2 | Backlog | 罗根 | 思余 | 持久化 latency 指标 | 本地 session metrics | p50/p95 可查看 | 人工使用 10 分钟 |
| OP-009 | P2 | Backlog | 乔布斯 | 全员 | 首次使用引导 | 简短 onboarding | 用户知道如何启动 Mac/拿手机 | 人工走查 |

## 协作接口

### iOS → Backend：Runtime Status

```http
GET /runtime/status
```

期望响应：

```json
{
  "status": "qwen",
  "api_base_url": "http://127.0.0.1:11435",
  "configured_model": "qwen2.5vl:3b",
  "resolved_model": "qwen2.5vl:3b",
  "dynamic_model_selection": false,
  "available_models": ["qwen2.5vl:3b"],
  "routing_reason": "configured",
  "image_min_tokens": "256",
  "image_max_tokens": "512",
  "max_tokens_incremental": 420,
  "max_tokens_full": 640
}
```

UI 规则：

- `dynamic_model_selection=false`：隐藏 3B/7B 切换，只显示“当前模型：快速 3B/更准 7B”。
- `available_models` 不包含某模型：不可选择，不用灰色长解释吓用户。
- `status=heuristic`：主界面/设置页提示“本地模型未启用，结果仅用于测试”。

### iOS → Backend：Frame Request

现有字段继续使用：

```json
{
  "type": "frame",
  "frame_id": "frame-123",
  "mode": "walking",
  "model": "qwen2.5vl:3b",
  "image_base64": "...",
  "previous_image_base64": "...",
  "client_ocr_text": "...",
  "context": {...},
  "question": "右边有什么"
}
```

协作约定：

- 后端必须回传 `requested_model/resolved_model/model_routing_reason`。
- iOS debug 可显示 resolved_model；主 UI 不显示工程细节。

### Speech Controller → UI

现有/新增接口：

```swift
onAudioLevel: (Double) -> Void // 0...1
onPartialText: (String) -> Void
onFinalText: (String?) -> Void
onStateChanged: (SpeechInputState) -> Void
```

UI 规则：

- recording：显示电平条和“正在听”。
- finalizing：显示“识别中”。
- final nil + peak low：显示“没有检测到声音”。
- final nil + peak non-low：显示“没有听清”。

### Backend → iOS：VQA Result

必须保留：

```json
{
  "summary": "...",
  "spatial_description": "...",
  "risk_level": "low|medium|high",
  "risk_message": "...",
  "suggested_action": "...",
  "spoken_text": "...",
  "ocr_text": "...",
  "change_significance": "none|minor|major",
  "changes": "...",
  "latency_ms": 1234,
  "requested_model": "qwen2.5vl:7b",
  "resolved_model": "qwen2.5vl:3b",
  "model_routing_reason": "single_runtime_ignored_override"
}
```

## 联调计划

### 阶段 0：准备

- Mac：启动后端。

```bash
bash ./start_local_vqa.sh
```

- iPhone：重新安装 App。
- 网络：iPhone 热点或同 Wi-Fi。
- 验证后端：

```bash
curl http://127.0.0.1:9000/runtime/status
```

### 阶段 1：Runtime Truth 联调

1. 启动 3B direct runtime。
2. 打开 iOS 设置页。
3. 期望：只显示当前实际模型，或至少明确“当前实际：3B”。
4. 若选择 7B 请求仍发出，后端返回 `resolved_model=3b`，debug 可见。
5. 验收：用户不会误以为 7B 已启用。

### 阶段 2：Push-to-talk 联调

1. 按住说话但不出声。
2. 期望：电平条几乎不动；松开后提示“没有检测到声音”。
3. 按住说“前方有什么”。
4. 期望：电平条明显变化；出现 partial 或 final；下一帧带 question。
5. 验收：能区分“没声音”和“没听清”。

### 阶段 3：Walking 安全联调

1. 模式：行走。
2. 模型：自动。
3. 场景：室内走廊/椅子/人/台阶。
4. 记录：risk_level、suggested_action、latencyText、spoken_text。
5. 验收：高风险必须播报；低风险不重复啰嗦。

### 阶段 4：Read-text 联调

1. 模式：读文字。
2. 对准一张纸/路牌/包装。
3. 记录：client OCR、ocr_text、spoken_text。
4. 验收：OCR 为空时给明确拍摄建议；有 OCR 时模型不胡编。

### 阶段 5：回归

自动测试：

```bash
source .venv/bin/activate && pytest server-vqa/tests relay-server/tests
```

iOS 手动：

- Xcode Run 真机。
- 检查权限弹窗/麦克风/语音识别/本地网络。

## 风险清单

| 风险 | 影响 | 负责人 | 缓解 |
|---|---|---|---|
| 7B 不可用但 UI 允许选择 | 用户误判模型质量 | 罗根/思余 | runtime status 驱动 UI |
| push-to-talk 电平阈值不准 | 误报没声音/没听清 | 思余/罗根 | 真机调参 |
| 读文字 OCR 与 VLM 冲突 | 播报错误文字 | 全麦 | prompt 明确 OCR 优先但图像校验 |
| 双帧输入增加延迟 | walking 变慢 | 罗根/全麦 | walking 可关闭 previous image 或降低尺寸 |
| 评估集缺图片资产 | 无法量化模型质量 | 全麦/乔布斯 | 先用人工记录 JSONL，再逐步补图 |

## 本轮验收清单

- [ ] `/runtime/status` 在 Mac 本地可访问。
- [x] iOS 设置页消费 `/runtime/status`（已实现，待真机确认）。
- [ ] Push-to-talk 真机有电平条。
- [ ] 无声音与听不清提示分开。
- [ ] 30 样例评估集结构建立。
- [ ] walking 主卡片第一行是安全状态。


## 2026-07-29 执行追加：OP-001 完成

已实现 iOS 端 runtime status 消费：

- `RuntimeStatus` Codable 模型。
- `RuntimeModelPolicy` 纯逻辑：
  - 无 status 时保留原有选项；
  - `dynamic_model_selection=false` 时只返回当前 `resolved_model`；
  - `dynamic_model_selection=true` 时返回 `自动 + available_models`。
- `StreamingViewModel.refreshRuntimeStatus()` 从当前 direct backend URL 推导 `/runtime/status`。
- `SettingsView` 显示“本地模型”状态和刷新按钮；单模型 runtime 下不显示会误导用户的分段模型选择器。
- 发送帧时使用 `RuntimeModelPolicy.modelID(...)`，单模型 runtime 直接发送实际模型 ID。

仍需真机确认：设置页打开时是否能正确刷新，网络切换后是否更新，文案是否足够自然。

## 2026-07-29 现场截图复盘追加

### 用户现场反馈

- 主结果卡展示原始 JSON/调试内容：`(模型未按要求输出结构化结果，以下为原始描述) { "objects": ... }`。
- `Surroundings` 模式端到端延迟约 11.5s，模型耗时约 12.3s。
- `Hold to talk` 可以按住，但反馈不明显；只看到低对比度 `Recognized: ...`。
- 用户质疑：这不符合乔布斯预期；任务看板在哪里；如果需要真机验证，需要清晰测试方法和反馈格式。

### 乔布斯裁决

这不符合 VQASee 预期。用户主界面不能显示 JSON、不能把高延迟当正常体验、不能让语音输入反馈像调试文本。

### 新增/更新任务

| ID | 优先级 | 状态 | 主责 | 配合 | 任务 | 交付物 | 验收标准 | 验证 |
|---|---|---|---|---|---|---|---|---|
| OP-010 | P0 | Done | 全麦 | 思余 | 破损 JSON 不进入主卡片 | 后端 parser/fusion fallback | 主卡片显示自然失败文案，不显示 `{ "objects"... }` | `test_broken_json_does_not_surface_raw_json_as_user_summary` |
| OP-011 | P0 | Done | 罗根/全麦 | 思余 | Surroundings 降低默认延迟 | 自动模型策略 + 禁用连续双帧 | 自动模式下 Surroundings 使用 3B；连续模式不发送 previous image | iOS 逻辑测试 + 真机延迟复测 |
| OP-012 | P0 | In Progress | 思余 | 罗根 | Hold to talk 反馈更明显 | 录音状态胶囊 + 电平条 | 按住时 200ms 内出现明显“正在听”反馈 | iPhone 真机录屏 |

### 用户真机反馈格式

请每次真机验证按下面格式反馈：

```text
测试时间：
App commit/安装时间：
Mac 后端启动命令：bash ./start_local_vqa.sh / 其他
网络：iPhone 热点 / 同 Wi-Fi / Relay
模式：Surroundings / Walking / Read Text / Details
模型设置页显示：当前实际模型 = ?
画面场景：例如卧室/走廊/文字/台阶
端到端延迟：截图里的 Latency 行
模型耗时：截图里的 模型xxx ms
是否出现 JSON/英文调试内容：是/否
Hold to talk：
  - 按住时按钮是否变色/有电平条：是/否
  - 是否出现“正在聆听”：是/否
  - 说了什么：
  - 识别成什么：
  - 是否回答了问题：
截图/录屏路径：
备注：
```
