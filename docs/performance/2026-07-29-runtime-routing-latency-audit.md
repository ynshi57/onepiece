# 性能审查：真实模型路由与端到端延迟闭环

## 背景

- 场景：iPhone nearby 连接 Mac，本地 Qwen 3B/7B 推理。
- 模式：walking / surrounding / detail / read_text。
- 用户感知问题：结果不够智能时会尝试 7B，但当前系统需要证明 7B 是否真的在线且被路由；walking 需要 p95 延迟可信。

## 链路分解

```text
iPhone camera → mode-aware JPEG encode → optional Apple Vision OCR → WebSocket/direct or relay → backend prompt/context assembly → model routing → llama-server inference → schema parse → fusion fallback → iOS state → speech gate → voice output
```

## 数据

- end-to-end：iOS 已显示拆分，但缺少持久化/汇总。
- encode：iOS 已计算 encodeMs。
- network + queue：iOS 根据 roundtrip - model 派生。
- model：后端返回 latency_ms。
- speech：未量化。
- timeout rate：iOS 有 watchdog，但缺少统计。
- reconnect success：已有自动重连逻辑，但缺少统计。

## 瓶颈判断

- 最可能瓶颈：模型 routing 不透明 + model prefill/decode 耗时。
- 证据：已有 direct llama-server 降低 image-min-tokens；但 3B/7B 同时可用性未显式暴露。
- 需要补充的日志：
  - backend 当前模型、端口、runtime、image_min_tokens；
  - 每帧选择的 requested_model / resolved_model；
  - p50/p95 model latency by mode/model；
  - timeout 和 reconnect 次数。

## 改动方案

- 最小修复：新增 `/runtime/status`，返回可用模型、当前 runtime、端口、image token 参数。
- 风险：如果直接启动双 runtime 内存不够，Mac 16GB 会 swap，反而变慢。
- 回滚方式：保留单 runtime，但 UI 只显示当前实际模型，隐藏不可用模型。

## 验证

- 命令：
  - `source .venv/bin/activate && pytest server-vqa/tests/test_local_runtime.py server-vqa/tests/test_vqa_service.py`
  - `source .venv/bin/activate && pytest server-vqa/tests relay-server/tests`
- 人工测试：
  - 启动 3B runtime，iOS 自动模式 walking 应显示/使用 3B；
  - 启动 7B runtime，surrounding/detail/read-text 应使用 7B；
  - 7B 不可用时 UI 不允许假选择。
- 结果：待下一轮实现。

## 结论

- 是否达标：未达标；缺少 runtime truth source。
- 下一步：罗根主责实现 runtime status；全麦配合 model route；思余配合 UI 隐藏不可用模型。

## 2026-07-29 执行追加

已实现最小 truth source：

- `GET /runtime/status` 返回：
  - `status`
  - `configured_model`
  - `resolved_model`
  - `dynamic_model_selection`
  - `available_models`
  - `routing_reason`
  - image token 和 max token 参数
- direct llama-server (`127.0.0.1:11435`) 被视为单模型 runtime：
  - 如果请求 `qwen2.5vl:7b` 但当前配置是 `qwen2.5vl:3b`，后端会继续用 3B，并标记 `model_routing_reason=single_runtime_ignored_override`。
- Ollama (`:11434`) 或其它动态 endpoint 仍可按每帧 model override 路由。

下一步：iOS 设置页应消费 `/runtime/status`，只把真实可用模型展示给用户。
