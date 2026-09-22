# 技术雷达：用更小更快的开源 VLM 压 Qwen VQA 延迟

- 日期：2026-09-20
- 小马结论：L2 最小实验（可商用小模型对照）；FastVLM 仅 L1 研究、不可进 App
- 相关角色：乔布斯 / 罗根 / 思余 / 全麦

## 1. 来源与可信度

| 来源 | 类型 | 可信度 | 备注 |
| --- | --- | --- | --- |
| [InternVL3.5](https://arxiv.org/html/2508.18265) · [HF 1B](https://huggingface.co/OpenGVLab/InternVL3_5-1B-HF) | 论文 / 官方权重 | 高 | Apache-2.0；ViR 减视觉 token；宣称相对 InternVL3 最高 4.05× 推理加速 |
| [Qwen3-VL 系列](https://github.com/qwenlm/qwen3-vl) · 官方 2B/4B | GitHub / HF | 高 | 现网是 Qwen2.5-VL 3B/7B + llama-server；2B Instruct 是同族最小可换档 |
| [Apple FastVLM](https://github.com/apple/ml-fastvlm) · CVPR 2025 | 论文 / GitHub / iOS demo | 高（技术）低（可上架） | FastViTHD 少视觉 token；0.5B 有 iPhone demo。**权重研究许可，禁止商用产品** |
| [Gemma 3n](https://developers.googleblog.com/en/introducing-gemma-3n-developer-guide/) | 官方博客 / HF | 高 | 端侧 MatFormer + MobileNet-V5；E2B 约 2GB；Gemma 条款，非 Apache |
| [SmolVLM 256M/500M](https://github.com/huggingface/blog/blob/main/smolervlm.md) | HF 官方博客 | 高 | 极小、MLX/ONNX；街景风险语言能力未证明 |
| [Empirical recipes 2026](https://arxiv.org/html/2603.16987v1) | 论文 | 中高 | 小 VLM 的 TTFT 常被 CPU 预处理吃掉，不换模型也能快 |

检索说明（2026-09-20）：2026 年没有出现能直接替换、且已证明比上述更快更准的新开源街景 VLM。Qwen3-VL-30B-A3B 在 vLLM 上很快，但对 VQASee 的 Mac/iPhone 单帧路径过重。

## 2. 核心认知

- VQASee 当前 VQA 路径：`qwen2.5vl:3b/7b` → `llama-server`。延迟主要是**图像视觉 token 的 prefill**，其次才是 JSON decode（已有 fast schema / 不传上一帧）。
- 四项核心（车道线 / 人车可走区 / 障碍 / 实时）应继续走端上 YOLO+分割，**不要用换 VLM 来救看路实时性**。
- 真正该换模型的，是 surrounding / detail / 语音问画面 这条 VQA 旁路。
- 可商用、值得对照的小模型：**InternVL3.5-1B/2B**、**Qwen3-VL-2B-Instruct**。FastVLM 只可实验室对标，不能进 shipping。
- 换 runtime（MLX / 更少 image tokens）往往和换模型同一量级；Ollama 默认 `image-min-tokens=1024` 是已知慢点，direct llama-server 已绕开。

## 3. 对 VQASee 的机会

- 解决哪个瓶颈：**反应快**（VQA 旁路），不是四项核心的「看得准」。
- 可能收益：walking 以外的问画面，从数秒级降到亚秒～2 秒（需本机实测）。
- 适用场景：用户主动问「前面是什么」、周围描述；不适用于每帧引导线。

## 4. 风险与不确定性

- 技术风险：小模型更容易漏风险、胡编、JSON schema 不稳（现网 3B 已有 repeat 死循环问题）。
- 产品风险：把 FastVLM 权重塞进 App 会踩 Apple 研究许可。
- 系统风险：换族（InternVL / Gemma）要改 worker 协议、tokenizer、图像预处理，不是改一个 `QWEN_MODEL`。
- 数据/评测风险：通用 MMMU 高分 ≠ 户外风险提醒准。必须用 CamVid/现场街景 + 现有 schema 回归。

## 5. 分角色学习卡

### 乔布斯

- 产品影响：延迟投诉若来自「看路实时」，换 VLM 是错药；若来自「问画面太慢」，才值得实验。
- 路线图：四项核心不改；VQA 加速是 P2 旁路，实验成功再进 backlog 的「问画面」体验。
- 下一次要多问：用户说慢，是引导线慢还是问完没声音？

### 罗根

- 系统影响：优先量 `qwen_http_ms` / TTFT / 视觉 token 数 / 图尺寸，再决定换模还是砍 token。
- 可观测性：新 runtime 必须暴露 resolved_model、prefill_ms、decode_ms，失败可见。
- 下一次要多问：这是 llama.cpp 慢，还是模型大？

### 思余

- 体验：更快的第一句可以先出「正在看」+ 短风险，再补细节；不要等完整 JSON。
- 风险：小模型不确定时，UI 不能装成很有把握。
- 下一次要多问：用户有没有看到「还在想」而不是假死？

### 全麦

- 对照：`qwen2.5vl:3b`（现网） vs `Qwen3-VL-2B-Instruct` vs `InternVL3.5-1B`，同一 10 张 CamVid 街景 + walking schema。
- 指标：e2e p50/p95、TTFT、schema 成功率、risk 漏报/误报、spoken_text 是否短。
- 下一次要多问：视觉 token 能不能再降，而不换模型？

## 6. 最小实验

- 假设：InternVL3.5-1B 或 Qwen3-VL-2B 在相同 JPEG 与 max_tokens=260 下，e2e p50 ≤ 现网 3B 的 50%，且 schema 有效率 ≥ 现网。
- 改动范围：实验室脚本 + 10 张 CamVid 帧；不改 iOS、不换默认 runtime。
- Baseline：本机 `qwen2.5vl:3b` llama-server walking fast path。
- 成功：延迟达标且风险字段不比 3B 更漏。
- 失败退出：更快但乱报风险 / schema 碎 → 停用，回去砍 token 与端上感知。

## 7. 沉淀与后续

- 是否更新 AGENTS.md：否（未经验证）
- 是否更新 skill：否
- 是否进入 docs/decisions：否
- 是否进入 docs/model-lab / performance：实验跑完再写 model-lab
- 是否进入 roadmap：否；保持四项核心优先
- 下一次主动雷达主题：MLX vs llama.cpp 在 Intel/Apple Silicon Mac 上的 VLM TTFT
