# VQASee Codex Agent Design v0.1

## 1. Goal
Build a Codex-assisted product team for OnePiece/VQASee: one product leader and three specialist engineers. The team should repeatedly improve VQASee toward an iPhone-like standard: useful, simple, reliable, beautiful, fast, and safe.

## 2. Recommended Codex surfaces

### AGENTS.md
Use `AGENTS.md` for repository-wide durable instructions: product principles, repo map, verification commands, code style, and role expectations. This repository now has a root `AGENTS.md` as the first version.

### Skills
Use skills for repeatable workflows that need a playbook, scripts, templates, or specialized references. Recommended future skills:
- `vqasee-product-review`: turn an idea into user stories, risks, metrics, and release criteria.
- `vqasee-ui-polish`: review SwiftUI screens for accessibility, iOS-native layout, copy, and state clarity.
- `vqasee-performance-audit`: measure and reason about encode/network/model/speech latency.
- `vqasee-model-prompt-lab`: design and test prompts, output schemas, model routing, and regression cases.

### MCP
Use MCP servers when Codex needs live external context/actions instead of static instructions. Recommended future MCPs:
- GitHub: issues, PRs, code review, release tracking.
- Xcode/build or local command tooling: structured build/test status if you later expose it safely.
- Product docs/Notion/Google Drive: roadmap, user interviews, specs.
- Observability/logs: latency traces, crash reports, backend metrics.
- Design assets/Figma: screens, components, product references.

### Hooks
Use hooks only for mechanical enforcement around lifecycle events, such as blocking commits with secrets or requiring tests after editing backend prompt code. Do not use hooks for product judgment.

## 3. Team decision model

乔布斯 is the final DRI. The other three roles advise from their domain:
- 罗根 can veto unsafe architecture/performance changes.
- 思余 can veto confusing or inaccessible UI.
- 全麦 can veto model behavior that is untestable, slow, verbose, or unsafe.

When tradeoffs conflict, decide in this order:
1. User safety and trust.
2. Core task success for low-vision users.
3. Latency and reliability.
4. Simplicity and beauty.
5. Engineering convenience.

## 4. First 4-week roadmap draft

### Week 1: Baseline and truth
- Define product metrics: time-to-first-use, p50/p95 latency, timeout rate, reconnect success, speech suppression precision.
- Create manual test checklist for nearby mode, relay mode, walking mode, read-text mode, backend crash, network switch.
- Audit current main screen states and settings complexity.

### Week 2: Reliability and latency
- Tighten connection/reconnect state machine.
- Add/verify latency breakdown visibility and logs.
- Establish model/image budget per mode and regression tests for prompt outputs.

### Week 3: UI simplification
- Make main flow calmer: one primary action, clear status, concise voice-friendly copy.
- Move advanced model/network controls behind clear advanced settings.
- Improve VoiceOver labels and dynamic type behavior.

### Week 4: Practical field release
- Field-test scripts for walking, indoor navigation, reading signs, asking one-shot questions.
- Create release criteria and known limitations.
- Package TestFlight-ready build checklist.

## 5. Prompt template for future Codex tasks

Use this when asking Codex to work on VQASee:

```text
请按 VQASee 团队方式工作：乔布斯先给产品判断，罗根审架构/性能，思余审 UI/可访问性，全麦审模型/后端。任务是：<你的任务>。

要求：
1. 先给简短计划和影响范围。
2. 只改必要文件。
3. 每个改动说明属于产品/系统/UI/模型哪类。
4. 运行最相关测试；不能运行就说明原因。
5. 最后给下一步建议。
```
