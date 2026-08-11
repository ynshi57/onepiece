# Unified Risk Observation 开发与验证计划

Date: 2026-08-11

## 乔布斯先定方向

本轮产品目标：把 VQASee 从“多模式 VQA App”改成“统一视觉风险观察 App”。

用户不再选择模式；系统自动判断当前任务。核心默认体验是：

> 看路、看周围障碍和危险，并用短语音提醒用户放慢、注意、确认。

本轮不追求一次删完所有内部 mode，而是分两层推进：

```text
用户层：无模式。
系统层：有自动 route。
```

## 员工 Review

### 罗根：系统 / 性能

态度：有条件同意。

判断：取消用户可见模式是对的，但不能删除内部 mode/schema。否则 Qwen 会重新变成大而慢的通用描述器。

风险：

- UI 改了但 backend 仍依赖 `AssistanceMode`，可能破坏发送策略。
- `readText` 如果没有触发入口，读文字能力会消失。
- 自动路由若不可观测，调试会更难。

最小可验证改动：新增内部 `ObservationRoute`，先把 UI 默认固定为风险观察，同时继续向后端发送兼容 mode。

需要证据：统一入口后端到端延迟、route 命中率、读文字触发率。

### 思余：UI / 可访问性

态度：同意。

判断：模式栏让产品像 demo。用户需要一个清楚主按钮和状态。

风险：

- 如果语音问题入口不明显，读文字/详细问题会变难。
- 如果状态只写“观察中”，用户不知道是在看不清、处理中还是断开。

最小可验证改动：主界面隐藏 ModeBar，改为 `开始观察`、`正在观察风险`，保留长按/语音问题入口。

需要证据：真机 VoiceOver 可用性、低光/断线/超时状态文案。

### 全麦：模型 / 后端

态度：有条件同意。

判断：统一产品体验不能对应统一大 prompt。模型仍需要窄问题：near path、traffic risk、read text、question。

风险：

- 默认 prompt 如果太宽，会导致“模型输出异常”、延迟升高、回答啰嗦。
- 驾驶场景必须谨慎，只能提醒风险，不能指导驾驶动作。

最小可验证改动：后端新增 `risk_observe` 模板，作为默认 mode；保留 walking fast schema 用于近处风险。

需要证据：30～50 张诊断样例 A/B，确认 risk_observe 不漏台阶、人、车、路沿。

## 乔布斯最终裁决

采纳：

- 罗根：内部路由必须保留，不能简单删除 mode。
- 思余：主界面要去模式化，语音问题入口必须保留。
- 全麦：默认 prompt 必须是风险观察，不是通用描述。

拒绝：

- 不接受“把所有模式合并成一个万能 prompt”。理由：会牺牲延迟和安全召回。
- 不接受“驾驶如何行驶”的输出。理由：VQASee 是风险辅助，不是驾驶决策系统。

## 任务卡

### T1：主界面去模式化

- 主责：思余
- 配合：罗根
- 目标：用户不再看到“看周围 / 走路 / 详细 / 读文字”。
- 改动范围：
  - `ios-vqa-app/VQASee/VQASee/AssistanceScreen.swift`
  - `ios-vqa-app/VQASee/VQASee/ModeBar.swift`
  - `ios-vqa-app/VQASee/VQASee/StreamingViewModel.swift`
  - `ios-vqa-app/VQASee/VQASee/Localizable.xcstrings`
- 交付物：
  - 主按钮：`开始观察` / `停止观察`
  - 主状态：`正在观察风险`
  - 语音问题入口保留
  - ModeBar 从主流程隐藏；可保留 debug/开发入口
- 验收标准：
  - 新用户无需理解模式即可开始。
  - VoiceOver 不再读“Walking mode / Surroundings mode”。
  - Dynamic Type 下主按钮不被挤压。
- 验证：
  - `bash deploy/ios/test.sh`
  - 真机手测：启动、停止、语音提问、诊断上传。

### T2：内部自动路由模型

- 主责：罗根
- 配合：全麦
- 目标：用户层无模式，但系统层明确 route。
- 改动范围：
  - `ios-vqa-app/VQASee/VQASee/Models.swift`
  - `ios-vqa-app/VQASee/VQASee/Networking.swift`
  - `ios-vqa-app/VQASee/VQASee/StreamingViewModel.swift`
  - `server-vqa/app/prompts.py`
  - `server-vqa/app/worker_client.py`
  - `server-vqa/app/signaling.py`
- 交付物：
  - 新增内部 route：`risk_observe`。
  - 旧 mode 兼容：旧客户端传 walking/surroundings 仍可用。
  - 默认路由规则：
    - 无问题：`risk_observe` / near-path fast。
    - 有文字问题：`readText`。
    - 有用户问题：`question`。
    - 用户要求详细：`detail`。
- 验收标准：
  - route 可在 diagnostic metadata 中看到。
  - 旧测试不破坏。
  - 不增加默认 walking/risk latency。
- 验证：
  - `source .venv/bin/activate && pytest server-vqa/tests`
  - `bash deploy/ios/test.sh`

### T3：默认风险观察 Prompt / Schema

- 主责：全麦
- 配合：罗根 / 思余
- 目标：默认模型输出不再像“看周围描述”，而是风险观察。
- 改动范围：
  - `server-vqa/app/prompts.py`
  - `server-vqa/app/vqa_service.py`
  - `server-vqa/tests/test_prompts.py`
  - `server-vqa/tests/test_vqa_service.py`
- 默认 prompt 要求：
  - 优先近处通行风险、障碍、人、车、台阶、路沿、边界、开门、画面质量。
  - 不输出精确米数。
  - 不说“可以走/可以开”。
  - 不确定就说不确定。
- 验收标准：
  - 输出包含 `risk_level/risk_zone/direction/distance_confidence/spoken_text`。
  - 默认 spoken_text 短，不做完整风景描述。
  - 无 “可以走 / 可以开 / 安全通过”。
- 验证：
  - `source .venv/bin/activate && pytest server-vqa/tests/test_prompts.py server-vqa/tests/test_vqa_service.py server-vqa/tests/test_fusion.py`

### T4：诊断与 A/B 验证

- 主责：全麦
- 配合：乔布斯 / 罗根 / 思余
- 目标：用真实诊断帧验证统一入口是否更好。
- 改动范围：
  - `server-vqa/tools/analyze_diagnostic_capture.py`
  - `docs/model-lab/`
  - `docs/performance/`
- 样例要求：
  - 室内办公区；
  - 走廊；
  - 台阶/门槛；
  - 人/椅子/箱子/玻璃门；
  - 车辆/自行车/路沿室外样例；
  - 文字标志。
- 指标：
  - p50/p95 latency；
  - false positive：如椅子识别成摩托车；
  - false negative：漏台阶/人/车；
  - 模型输出异常率；
  - spoken_text 平均长度；
  - route 命中率。
- 验收标准：
  - 统一入口不增加高风险漏报；
  - 模型输出异常率下降或不变；
  - 用户手动选择次数归零。

### T5：产品文案与发布标准

- 主责：乔布斯 + 思余
- 配合：罗根 / 全麦
- 目标：统一对外文案，避免“模式”和“驾驶指令”。
- 文案候选：
  - App 主标题：`VQASee`
  - 主按钮：`开始观察`
  - 状态：`正在观察风险`
  - 弱网/慢模型：`我还在确认，请先放慢。`
  - 看不清：`画面不够清楚，请放慢确认。`
- 禁用文案：
  - `可以走`
  - `可以开`
  - `安全通过`
  - `自动驾驶`
  - `导航路线`

## 协作接口

```text
思余 → 罗根：主界面不再选择模式，但需要 start/stop/question/debug 状态接口。
罗根 → 全麦：发送 internal route、mode 兼容字段、diagnostic route metadata。
全麦 → 思余：返回风险等级、不确定性、短 spoken_text、quality warning。
罗根 → 乔布斯：提供延迟、timeout、route 命中率。
乔布斯 → 全员：发布前不允许出现驾驶/行走许可文案。
```

## 联调计划

路径：

```text
iPhone camera
→ local perception / quality gate
→ internal route resolver
→ backend risk_observe prompt/schema
→ fusion
→ UI / speech / diagnostic capture
→ annotation platform
```

步骤：

1. Debug 构建中显示 internal route chip，仅开发可见。
2. 真机启动：用户不选模式，直接开始观察。
3. 室内办公区采集 10 秒，检查是否还出现摩托车误检。
4. 走廊/台阶采集 10 秒，检查 near-path 风险。
5. 语音问“这是什么字”，确认自动转 read_text/question。
6. 断开 Mac 后端，确认 UI 显示恢复路径。

失败定位：

- UI 不显示状态 → 思余。
- route 错 / 请求不发 → 罗根。
- Qwen 输出异常 / 太啰嗦 → 全麦。
- 用户仍困惑 → 乔布斯回炉。

## 最小开发顺序

1. T3 后端新增 `risk_observe` prompt，保持旧 UI 可用。
2. T2 iOS 增加 internal route resolver，但仍可用旧 selectedMode。
3. T1 隐藏 ModeBar，主界面改“开始观察”。
4. T4 用诊断帧 A/B。
5. T5 文案和发布验收。

## 验证命令

后端：

```bash
source .venv/bin/activate && pytest server-vqa/tests
```

iOS：

```bash
bash deploy/ios/test.sh
```

真机：

```text
1. 开始观察，不选模式。
2. 室内办公区 10 秒。
3. 走廊/台阶 10 秒。
4. 语音问文字。
5. 上传诊断帧并打标错误样例。
```

## 2026-08-11 T3 执行记录：后端 risk_observe

已完成第一阶段后端改造：

- `server-vqa/app/prompts.py`
  - 新增 `risk_observe` 模板。
  - `DEFAULT_MODE` 从 `surroundings` 改为 `risk_observe`。
  - 默认 prompt 聚焦风险观察：近处通行路径、人、车、台阶、路沿、坑洼、开门、边缘、不确定性。
- `server-vqa/app/vqa_service.py`
  - `risk_observe` fast request 使用带 `risk_zone/direction/distance_confidence` 的紧凑风险 schema。
  - 旧 `walking` fast schema 行为保持兼容。
- `server-vqa/app/worker_client.py` / `server-vqa/app/signaling.py`
  - 新增 `effective_mode`：无 mode 且无 legacy prompt 时默认 `risk_observe`。
  - 无 mode 但带旧 legacy prompt 的请求不被强行改成风险观察。
  - `risk_observe` 进入 fast response。
- `server-vqa/app/frame_metadata.py`
  - 质量门控适用于 `risk_observe` 和旧 `walking`。

新增/更新测试：

- 默认 prompt 是 `risk_observe`。
- 默认风险 prompt 包含障碍、台阶、车辆、禁止精确米数与许可性表达规则。
- `risk_observe` fast request 使用风险区间 schema。
- worker 无 mode 时默认进入风险观察 fast path。
- legacy prompt 仍不被污染。

下一步：T2 iOS internal route resolver，然后 T1 隐藏 ModeBar。

## 2026-08-11 T1/T2 执行记录：iOS 用户层去模式化基础版

已完成基础代码改造：

- `ios-vqa-app/VQASee/VQASee/AssistanceScreen.swift`
  - 主流程隐藏 `ModeBar`，用户不再选择“看周围/走路/读文字/详细看”。
  - 主按钮文案从“开始视觉辅助”改为“开始观察”。
- `ios-vqa-app/VQASee/VQASee/Models.swift`
  - 新增内部 `ObservationRoute`：`riskObserve / readText / question / detail`。
  - `readText/detail` 不再作为用户入口，而是由语音问题 intent 自动触发。
- `ios-vqa-app/VQASee/VQASee/StreamingViewModel.swift`
  - 默认内部兼容 mode 改为 `.walking`，用于风险观察路径。
  - 语音识别到读文字时不再 `selectMode(.readText)`，只设置 internal route。
  - 发送帧时按 `ObservationRoute` 决定 backend mode、prompt、编码档、是否单次请求、是否发送上一帧。
  - 默认连续观察发送 `risk_observe`，prompt 留空，让后端使用默认风险观察模板。
- `ios-vqa-app/VQASee/VQASeeTests/VQASeeTests.swift`
  - 增加 `ObservationRoute` 纯逻辑测试，覆盖默认风险观察、读文字意图、详细意图和普通视觉问题。

验证：

- Swift 业务源文件 typecheck 通过（排除 `ContentView.swift` 的 `#Preview` 宏和 `VQASeeApp.swift` App 入口）：

```bash
SDK=/Applications/Xcode.app/Contents/Developer/Platforms/iPhoneSimulator.platform/Developer/SDKs/iPhoneSimulator26.2.sdk
find ios-vqa-app/VQASee/VQASee -maxdepth 1 -name '*.swift' ! -name 'ContentView.swift' ! -name 'VQASeeApp.swift' -print0 \
  | xargs -0 /Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/swiftc \
      -typecheck -sdk "$SDK" -target arm64-apple-ios18.0-simulator \
      -module-cache-path /private/tmp/vqasee-swift-module-cache
```

结果：通过，仅有既有 Swift 6 并发 warning。

- 后端回归：

```bash
source .venv/bin/activate && pytest server-vqa/tests
```

结果：91 passed。

未完成/阻塞：

- `xcodebuild build/test` 在当前环境仍被 CoreSimulator/资源配置阻塞：
  - CoreSimulatorService 不可用；
  - `YOLO11nObject.mlmodelc` 作为目录资源复制时出现多个 `coremldata.bin` duplicate output。
- 需要在 Xcode/真机环境验证 UI 是否确实无模式栏，以及语音“帮我读一下”是否走 internal `readText` route。

下一步：修复 YOLO11nObject 资源打包方式，恢复完整 iOS build/test；然后真机验证统一观察入口。
