# 小马雷达：开源小 VLM 能否压 Qwen VQA 延迟

## 最终结论

有，但分两层：看路实时不要换 VLM；问画面这条旁路，可商用首选 **InternVL3.5-1B/2B** 和 **Qwen3-VL-2B**。Apple FastVLM 最快但不能进产品。

## 方案要点

- L2 实验：10 张 CamVid + 现网 walking schema，对照 3B
- 不改 AGENTS / 不改默认 runtime，直到数字出来
- 四项核心仍走端上分割/检测

## 已执行

- `docs/tech-radar/2026-09-20-fast-open-vlm-for-qwen-latency.md`
- 更新 `docs/tech-radar/index.md`

## 待办 / 阻塞

- 未在本机跑 Qwen3-VL-2B / InternVL3.5 对照（需下载权重与全麦排期）
- FastVLM 仅允许研究，禁止 shipping

更新时间: 2026-09-20 10:40
