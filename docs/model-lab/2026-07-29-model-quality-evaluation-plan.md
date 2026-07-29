# 模型 / Prompt 实验：VQASee 视觉辅助质量评估集与模型路由

## 背景

- 模式：walking / surrounding / detail / read_text / voice_question
- 问题：用户感觉 video 图像结果“不够智能”，尤其方向感、场景感、风险判断、读文字和问题回答不稳定。
- 用户影响：如果模型质量无法量化，用户无法建立信任；prompt/schema/model 改动也无法判断是否进步。

## 原始行为

- 输入场景：iPhone 连续取帧，后端 Qwen 3B/7B 通过 local llama-server 推理。
- 原 prompt 或策略：模式 prompt + scene context + JSON schema；iOS 可自动模型选择。
- 原输出：summary / spatial_description / risk / suggested_action / spoken_text。
- 问题：缺少固定样例和评分；3B/7B 是否真正路由到对应 runtime 需要系统证据。

## 实验假设

如果：建立固定评估集，并让每次模型/prompt/schema 改动都跑 walking/read-text/surrounding/detail/voice question 样例；

那么：模型质量问题会从“感觉不好”变成可定位的风险遗漏、方向错误、OCR 错误、啰嗦、延迟超标；

因为：视觉辅助产品需要长期追踪“看得准、说得对、反应快”，不能只靠现场主观体验。

## 改动

- Prompt：保留现有模式化 prompt，但增加 eval 样例覆盖。
- Schema：验证 `spatial_description`, `risk_level`, `suggested_action`, `change_significance` 稳定性。
- 模型路由：新增真实路由验证，确认 3B/7B 与 runtime 一致。
- 图片/token 参数：按模式记录 image size / max_tokens / image-min-tokens。
- 后端代码：下一轮新增可脚本化 eval runner 或 pytest fixtures。

## 测试样例

### 样例 1：楼梯/台阶（walking）

输入：正前方近处有台阶或楼梯。

期望：

```json
{
  "risk_level": "high",
  "must_mention": ["正前方", "台阶"],
  "suggested_action_should_include": ["停下", "放慢"]
}
```

### 样例 2：文字/路牌（read_text）

输入：画面中央有中文/英文混合文字。

期望：

```json
{
  "ocr_text_not_empty": true,
  "summary_should_answer_text": true,
  "suggested_action_if_unclear": "靠近/对准/增加光线"
}
```

### 样例 3：室内走廊（surrounding）

输入：左侧墙、正前方走廊、右侧门。

期望：

```json
{
  "spatial_description_should_include": ["左侧", "正前方", "右侧"],
  "risk_level": "low"
}
```

### 样例 4：语音问题（voice_question）

输入：用户问“右边有什么？”且画面右侧有门/人/障碍。

期望：优先回答右侧，不先完整描述全局。

## 指标

- 延迟：walking p95 <= 3.5s；surrounding/detail/read_text 记录 p50/p95，不强行低于 walking。
- 输出长度：spoken_text <= 2 句；walking suggested_action <= 1 句。
- 风险遗漏：walking 高风险样例 0 漏报为目标。
- 误报：无明显风险样例不应频繁升到 high。
- 读文字准确率：先以人工标注文本粗略对比，后续再做 CER/WER。

## 结论

- 保留 / 回滚 / 继续实验：继续实验。
- 原因：当前能力已有基础，但缺少评估闭环；这是模型进化的前置条件。

## 应沉淀到测试的内容

- `server-vqa/tests/test_prompts.py`：模式 prompt 必须包含空间/风险/读文字要求。
- 新增 `server-vqa/tests/test_model_eval_contract.py`：验证样例格式、must-mention 规则和输出 schema。
- 后续可加离线 eval runner，不强制每次 CI 调真实模型。
