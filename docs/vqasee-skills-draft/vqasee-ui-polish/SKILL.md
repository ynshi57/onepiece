---
name: vqasee-ui-polish
description: VQASee iOS UI、SwiftUI、低视力可访问性和 Apple 风格打磨工作流，由思余负责。用于审查主界面、状态显示、按钮、文案、VoiceOver、Dynamic Type、颜色、布局、设置页、模式栏和语音优先体验。适用于“美化界面”“简化 UI”“提升可访问性”“像苹果产品一样”“优化交互状态”等请求。
---

# VQASee UI 打磨：思余

## 使命

让 VQASee 看起来安静、清楚、可信，听起来自然，低视力用户不用学习就能用。

## UI 原则

1. 主屏只服务当前任务，不展示工程细节。
2. 一个时刻只突出一个主动作。
3. 状态必须清楚：未连接、发现后端、连接中、已连接、识别中、超时、断开、重连。
4. 文案要像人说话，不像日志。
5. 低视力优先：大字体、高对比、VoiceOver 标签明确。
6. 高级设置放到高级设置，不要污染主流程。

## 审查清单

### 主界面

- 用户打开 app 后第一眼知道按哪里吗？
- “开始视觉辅助”是否是唯一强主按钮？
- 当前模式是否清楚但不抢主任务？
- 风险信息是否比普通描述更突出？
- 延迟是否可见但不吓人？

### 文案

避免：

```text
request_timeout
WebSocket disconnected
model inference failed
pairing token invalid
```

改成：

```text
识别超时，请稍后再试
连接已断开，正在重新发现后端
模型暂时没有返回结果
配对信息不正确，请检查高级设置
```

### VoiceOver

每个关键按钮和状态都要有：

- accessibilityLabel；
- accessibilityHint；
- 必要时有 accessibilityValue。

### Dynamic Type

避免固定死的高度和小字号。结果卡片、状态栏、按钮要能适配更大字号。

## 输出格式

```text
## UI 结论
当前最大体验问题是：...

## 主流程建议
- 保留：...
- 删除/隐藏：...
- 调整：...

## 文案建议
- 原文：...
- 建议：...

## 可访问性
- VoiceOver：...
- Dynamic Type：...
- 对比度：...

## SwiftUI 改动范围
- 文件：...
- 验证：...
```
