# 2026-07-29 真机反馈闭环：定位、2s 目标、语音问题与读文字

## 反馈/问题

- 用户原话或现象：
  - 测试 1：不知道模式设置在哪，未出现 JSON，Latency 约 6s+，希望 2s 以内。
  - 测试 2：按住说话基本可用，但问“你知道今天是星期几吗”，App 回答“用户可能在室内，可能在电梯间附近”，明显跑题。
  - 测试 3：Read Text 对准药物说明书，只显示“这是一张药物单子”；用户期望识别所有文字并读出来。
  - 用户认为产品定位“面向弱视群体”可能过窄，应是正常人也可用的视觉辅助。
- 场景：iPhone 真机 + Mac 后端，本地视觉辅助。
- 模式：Surroundings、Voice Question、Read Text。
- 影响：核心体验仍不达预期；产品定位和模式承诺需要修正。

## 事实与证据

- 已知事实：
  - JSON 外露已修复。
  - Surroundings 延迟从 11s+ 降到约 6s+，但仍远高于用户期望 2s。
  - Push-to-talk 可以按住，但语音问题没有识别“非视觉/日期时间问题”的意图边界。
  - Read Text 没有按“全文阅读”产品承诺执行。
  - 用户找不到模式设置/模式切换，说明 UI 可发现性不足。
- 合理猜测：
  - 6s+ 仍来自模型推理：可能仍在跑高分辨率/7B/双图/长 schema，或 direct runtime 单模型与 UI 状态不一致。
  - Read Text 虽有 Apple Vision OCR，但 OCR 文本可能未进入 prompt、未展示，或模型没有被强约束“读全文”。
  - 非视觉语音问题被塞进 VQA prompt，模型被迫看图回答，导致胡乱回答场景。
- 需要验证：
  - 当前设置页实际模型显示，Surroundings 是否真实使用 3B。
  - Read Text 请求里 `client_ocr_text` 是否非空。
  - Voice question 的问题类型分类是否存在。

## 核心能力定位

- 看得准：Read Text 没有完成“读全文”。
- 反应快：Surroundings 6s+ 不达标。
- 说得对：语音问题跑题，能力边界不清。
- 用得住：用户找不到模式设置，模式可发现性不足。

## 乔布斯先定方向

### 初始方案

- 产品定位调整：VQASee 从“弱视专用”改成“语音优先的视觉辅助工具，低视力优先，普通人也能用”。
- 本轮优先级：
  1. P0：语音问题意图分类。非视觉问题（日期/常识/闲聊）不能硬套 VQA，应回复“我主要帮助你看画面”。
  2. P0：Read Text 必须承诺读文字，不是描述纸张。
  3. P1：Surroundings 目标从 6s 降到 2s 体验；若真实模型达不到 2s，产品必须改成“先快速播报粗略结果，稍后补充详细”。
  4. P1：模式切换可发现性提升。
- 指派：
  - 全麦主责：语音问题分类、Read Text prompt/OCR 强约束、模型策略。
  - 罗根主责：2s latency 拆解和快速/详细两阶段方案。
  - 思余主责：模式可发现性、Read Text 主 UI 文案、语音问题边界提示。
- 本轮不做：不承诺 Qwen 单帧 VLM 直接达到 2s；不继续把正常用户排除在定位外。

## 专家 review 乔布斯方案

### 罗根：系统 / 性能 / 架构

- 态度：有条件同意。
- 判断：2s 是正确产品目标，但当前 Qwen VLM 全量推理很可能做不到稳定 2s，尤其 7B/读文字/高分辨率。
- 实现困难：
  - 单帧 VLM prefill + decode 本身可能 2.5s+；6s 可能还包含 7B 或高图像 token。
  - Read Text 高分辨率 + OCR + VLM 会更慢。
- 替代方案：
  - 两阶段响应：先用 iOS 本地 OCR/轻量策略在 <1s 给“检测到文字/正在读”；再让 VLM 汇总。
  - Surroundings 默认 3B + 单图 + 更短 schema；Detailed 才用 7B。
  - 为每帧记录 resolved_model、mode、image size、previous image on/off、model latency。
- 最小可验证改动：
  - 在 UI/debug 和日志中显示 resolved_model + mode + image profile。
  - Surroundings 禁用双图、强制 3B、max_tokens 降低，验证 p50 是否接近 2-3s。

### 思余：UI / 交互 / 可访问性

- 态度：同意，但要求乔布斯修正文案和信息架构。
- 判断：用户找不到模式，说明当前 ModeBar 虽存在但视觉/语言不够明确；“Surroundings”英文标签也不适合中文用户。
- 实现困难：
  - 主界面空间有限，不能塞更多解释。
  - 低视力用户不适合读长文案或找隐藏设置。
- 替代方案：
  - 模式栏中文化并加短副标题或首次提示：`看周围 / 走路 / 读文字 / 详细看`。
  - Read Text 切换后主卡片显示明确指令：“把文字放到中央，我会读出来”。
  - 语音问题如果不是视觉问题，直接语音答：“我主要帮你看画面；日期时间请问 Siri。”
- 最小可验证改动：
  - ModeBar 文案改为中文动词；
  - Read Text 空状态改成“对准文字，开始读”；
  - Push-to-talk 回答边界文案。

### 全麦：模型 / Prompt / Qwen 3B/7B

- 态度：同意，但反对把所有问题都交给 VLM。
- 判断：用户问“今天星期几”是非视觉问题，VLM 不该看图回答；Read Text 应该优先 Apple Vision OCR，不应让 VLM“看图猜这是一张纸”。
- 实现困难：
  - Qwen 对药物说明书小字可能不稳定；VLM 读全文慢且可能漏字。
  - 当前 schema 偏视觉描述，不适合长 OCR 文本输出。
- 替代方案：
  - 语音问题先做本地 intent classification：visual_question / read_text / non_visual。
  - Read Text 走 OCR-first：有 OCR 文本时直接展示/播报 OCR；VLM 只负责总结/纠错/解释。
  - 对长说明书分段读，不一次塞进 spoken_text。
- 最小可验证改动：
  - 新增纯函数 `VoiceQuestionIntent.classify` 测试“今天星期几” → non_visual。
  - Read Text 若 OCR 非空，主 UI 优先显示 OCR 文本前几行，并语音播报 OCR 摘要或全文入口。

## 乔布斯修正后最终裁决

- 优先级：
  - P0：语音问题意图分类，非视觉问题不要胡答。
  - P0：Read Text 改为 OCR-first，必须读文字。
  - P1：Surroundings 2s 体验目标改成“两阶段”：先快反馈，再详细补充；短期目标 p50 <= 3s，长期目标 <= 2s。
  - P1：模式可发现性中文化。
- 本轮做：沉淀任务、接口、测试目标；下一轮先做 P0 intent + OCR-first。
- 本轮不做：不承诺 Qwen 7B 在所有模式 2s 内；不把 VQASee 限定为弱视专用。
- 主责：全麦主责 P0 intent/read-text；罗根主责 latency instrumentation；思余主责模式文案和状态。
- 成功标准：
  - “今天星期几”不再触发场景描述；
  - Read Text 对说明书能显示/播报 OCR 文本，而不是“这是一张单子”；
  - Surroundings 不出现 JSON，p50 明显低于 6s；
  - 用户能一眼知道怎么切模式。
- 失败转向：如果 OCR 长文播报体验差，改成“读摘要 / 逐段读 / 停止”三步。

## 任务拆解与执行看板追加

| ID | 优先级 | 状态 | 主责 | 配合 | 任务 | 交付物 | 验收标准 |
|---|---|---|---|---|---|---|---|
| OP-013 | P0 | Done | 全麦 | 思余 | 语音问题意图分类 | `VoiceQuestionIntent` + tests | “今天星期几”不进入 VQA 场景描述 |
| OP-014 | P0 | Done | 全麦 | 思余 | Read Text OCR-first | OCR 文本优先 UI/语音 | 说明书显示/播报 OCR 内容 |
| OP-015 | P1 | Ready | 罗根 | 全麦 | Surroundings latency audit | resolved_model/image profile/latency 日志 | p50 明显低于 6s，定位瓶颈 |
| OP-016 | P1 | Done | 思余 | 乔布斯 | 模式可发现性中文化 | ModeBar 中文动词 | 用户能找到/理解模式 |
| OP-017 | P2 | Ready | 乔布斯 | 全员 | 产品定位文案修正 | README/roadmap 定位 | “视觉辅助，低视力优先，普通人可用” |

## 协作接口

### Voice Intent

输入：用户语音转写文本。

输出：

```swift
enum VoiceQuestionIntent {
  case visualQuestion
  case readText
  case nonVisual
}
```

规则：

- 日期/时间/天气/闲聊/常识 → nonVisual。
- “读一下/上面写什么/说明书” → readText。
- “前方/左边/右边/这是什么/能不能走” → visualQuestion。

### Read Text OCR-first

- iOS OCR 非空：主 UI 优先显示 OCR 文本；语音优先读 OCR。
- VLM 可作为辅助：总结、解释、纠错，不替代 OCR。
- OCR 为空：提示靠近、对准、增加光线。

## 联调计划

1. 语音问题：
   - 说“你知道今天是星期几吗”。
   - 期望：回复“我主要帮你看画面，日期时间请问 Siri/系统”。
2. Read Text：
   - 对准药物说明书。
   - 期望：OCR 文本出现；不是“这是一张药物单子”。
3. Surroundings：
   - 室内卧室场景。
   - 记录 Latency、resolved_model、是否 previous image。
4. 模式可发现性：
   - 用户能指出“看周围/走路/读文字/详细看”在哪里。

## 验证

- 自动测试：下一轮新增 intent/OCR-first 纯逻辑测试。
- 人工测试：按用户反馈模板继续截图/录屏。


## 2026-07-29 执行追加：OP-013/014/016 完成

已实现：

- OP-013：新增 `VoiceQuestionIntent.classify`。日期/时间/天气/常识类问题不再进入 VQA；用户问“今天星期几”时，App 会说明自己主要帮助看画面。
- OP-014：Read Text 改为 OCR-first。iOS OCR 非空时，主卡片优先显示 OCR 文本，语音优先读 OCR；VLM 结果不再覆盖 OCR-first 主文案。
- OP-016：模式标签中文动词化：`看周围 / 走路 / 读文字 / 详细看`。

验证：

- `source .venv/bin/activate && pytest server-vqa/tests relay-server/tests` → 64 passed。
- iOS Xcode CLI 编译仍因本机 active developer directory 为 CommandLineTools 无法运行；需真机 Xcode Run 验证。

真机重点复测：

1. 说“你知道今天是星期几吗” → 不应描述室内场景。
2. Read Text 对准药物说明书 → 应显示/播报 OCR 文字，而不是“这是一张药物单子”。
3. 模式栏应显示中文动词。
