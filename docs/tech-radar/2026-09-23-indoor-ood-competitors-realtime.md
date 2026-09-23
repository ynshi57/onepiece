# 技术情报卡 · 室内失效 / 竞品形态 / 端上实时看路

- 日期：2026-09-23
- 触发：用户咖啡店实测 — 画面像不动、车道与可走区乱跳、延迟不达实时
- 主责：小马 → 乔布斯 / 罗根 / 全麦 / 思余
- 采纳等级：**L2（最小实验）→ 产品 L3 候选：户外域门闩 + 感知帧率拆分**

## 用户事实（乔布斯归因）

| 原话/现象 | 归因 |
| --- | --- |
| 咖啡店室内，车道/红色可走区总变 | TwinLite 训练域≈户外驾驶（BDD100K），室内是 **OOD 幻觉**，不是阈值微调 |
| 画面不动、叠层乱跳 | 相机预览连续；感知曾被 **2.0s** 节流 + 同步前向可能堵采集队列 |
| 达不到实时 | 感知≠视频流；旧上送节奏绑死本地叠层 |

## 帧率事实（代码）

- 预览：`AVCaptureSession` 连续视频层（~30 fps），**不是**感知输入。
- 感知：`FrameCaptureProxy` 从 `AVCaptureVideoDataOutput` 取**离散帧**，经 `minLocalPerceptionInterval`（现 **0.2s ≈ 目标 5 Hz**；忙则丢，latest-wins）。
- 远程 Qwen：另开 JPEG 编码；历史上与 `minFrameInterval=2.0s` 绑在一起；现拆为 `minRemoteUploadInterval=2.0s`。
- **不是**把整路 H.264/视频流送给 TwinLite。

## 竞品产品形态（给乔布斯）

| 产品 | 形态抓手 | 对 VQASee 的启示 |
| --- | --- | --- |
| [Seeing AI](https://www.microsoft.com/en-us/garage/wall-of-fame/seeing-ai/) | **Channel 模式**（短文本/文档/场景…），用户选任务，不假装全能导航 | 室内应用「读环境/找物」通道，**不要**硬画车道 |
| Envision / Be My AI | 识别与问答为主，导航不是默认 | 远程 VQA = 解释通道；本地叠层 = 户外看路通道，已对齐 |
| [GoodMaps](https://www.goodmaps.com/platform) | 室内靠 **地图数字孪生 + 定位 SDK**，不是通用可走区分割 | 室内导航另开产品线；勿用 TwinLite 冒充 |
| [Mobilio](https://www.nature.com/articles/s41551-026-01772-x)（Nature BME 2026） | 户外路径引导 + 障碍 + 个性化语音；分割实时叠在相机旁 | 户外才是 VQASee 主战场；成功指标含时间与碰撞↓ |
| Apple Live Recognition (visionOS 2025) | 描述/找物/读文档；**明确警告勿作导航依赖** | 诚实边界文案：辅助提醒 ≠ 可走承诺 |

**乔布斯可用的一句话形态：**  
「户外街景看路叠层」与「室内/问答解释」必须分通道；竞品从不在咖啡店画假车道。

## 技术候选（给全麦/罗根）

| 候选 | 来源 | 成熟度 | 建议 |
| --- | --- | --- | --- |
| TwinLiteNet / + | BDD 驾驶可走区+车道 | 户外可用；室内 OOD | 保持户外默认；加 OOD/室内门闩 |
| UFLDv2 / 轻量 row-anchor | 已有产品路线 | 仍是车道域 | 有权优先；不解决室内 |
| Domain routing / contrastive branch ([ICCVW 2025](https://openaccess.thecvf.com/content/ICCV2025W/2COOOL/papers/Khan_Adapt_But_Dont_Forget_Fine-Tuning_and_Contrastive_Routing_for_Lane_ICCVW_2025_paper.pdf)) | 分布路由 | 研究向 | L2：先做粗分类（室内/街景）再决定是否跑 TwinLite |
| 行人可走区 SOTA | 既有雷达卡 | — | 见 `2026-08-26-pedestrian-traversability-sota.md` |
| 感知异步 + 5 Hz | 本轮工程 | 已落地代码 | 真机量 p50/p95 |

## 分角色学习卡

- **乔布斯：** 场景边界写进 CURRENT；室内失败可见；下一步产品是「域门闩」不是「再训一轮胡说」。
- **罗根：** 预览与感知解耦；本地 0.2s / 远程 2s；真机测叠层更新 Hz 与卡顿。
- **思余：** 空闲提示写清户外；室内收叠层时用短状态而非假线。
- **全麦：** 不做室内 TwinLite 微调幻想；优先 outdoor confidence / scene gate 实验。

## 最小实验

1. 真机：户外步行 vs 咖啡店，记录叠层更新间隔与「假线」观感（乔布斯看图）。
2. OOD 门闩 PoC：简单室内启发（天花板灯/近景纹理）或低置信可走区 → **不画车道/可走**，只留 YOLO 人/物（若有）。
3. 罗根签字：本地感知 p95 与 UI 更新 Hz。

## 下一次优先搜索

- Places / scene classification on-device（Core ML）作域门闩
- Apple Accessibility Nutrition / Live Recognition 文案边界
- GoodMaps Dot SDK 是否值得「室内另开」评估（多半 L0 观察）
