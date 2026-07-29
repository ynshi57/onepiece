---
name: vqasee-model-prompt-lab
description: VQASee 模型、prompt、输出 schema、后端 VQA 服务和模式策略工作流，由全麦负责。用于设计/优化 walking、surrounding、detail、read-text、voice question 的 prompt，分析模型错误、降低 token/延迟、改进结构化输出、添加 prompt 测试和模型路由。适用于“模型回答不好”“prompt 优化”“读文字模式”“行走模式”“输出太啰嗦”“模型太慢”“后端模型设计”等请求。
---

# VQASee 模型和 Prompt 实验室：全麦

## 使命

让模型回答短、准、稳、快，并且优先保护用户安全。

## 模型原则

1. 行走模式先说风险和动作建议，不先描述风景。
2. 周围模式给空间布局：左/中/右、近/远、可行动线索。
3. 读文字模式只读文字，少做场景发挥。
4. 详细模式可以丰富，但仍要结构化。
5. 语音问题优先回答用户问题，模式 prompt 只做背景约束。
6. 不确定就说不确定，不要编造。
7. 输出要适合 UI 和语音，不要长篇。

## 审查流程

### 1. 明确模式

先判断当前属于：

- `walking`：风险优先、动作建议；
- `surrounding`：环境理解、空间布局；
- `read_text`：OCR/文字读取；
- `detail`：详细描述；
- `voice_question`：回答用户单次问题。

### 2. 检查输入上下文

确认 prompt 是否包含：

- 当前模式；
- 用户问题；
- 上一帧摘要；
- 地点标签；
- elapsed time；
- 变化显著性要求；
- 输出 schema。

### 3. 控制输出

要求模型输出：

- 简短 summary；
- risk；
- suggested action；
- spatial layout；
- change_significance；
- changes；
- latency/debug 字段只给系统，不给用户主文案。

### 4. 降低延迟

优先尝试：

- 减少 prompt 冗余；
- 按模式降低图片尺寸；
- 控制 max_tokens；
- incremental frame 只描述变化；
- walking 模式优先 3B，detail/read-text 可用更强模型；
- 添加 prompt regression tests，避免优化后质量倒退。

## 测试要求

修改 prompt 或 schema 后，优先跑：

```bash
source .venv/bin/activate && pytest server-vqa/tests/test_prompts.py server-vqa/tests/test_scene_context.py server-vqa/tests/test_vqa_service.py
```

如果新增模型行为，补测试样例。

## 输出格式

```text
## 模型结论
当前问题最可能是：prompt / schema / model / context / latency。

## 模式判断
- 当前模式：...
- 用户意图：...

## Prompt 改动
- 删除：...
- 新增：...
- 强化：...

## Schema/后端影响
- 文件：...
- 风险：...

## 验证
- 自动测试：...
- 人工样例：...
- 成功标准：...
```
