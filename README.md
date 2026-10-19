<!-- Language switch -->
[English](README.en.md) · **简体中文**

# VQASee — 面向 iPhone 的视觉优先风险与通行路径辅助

> 拿起手机就能*看见*周围的通行路径、障碍、边界和风险——语音用于确认，而不是
> 替代你自己的观察。

VQASee 是一款面向 iPhone 的视觉优先风险辅助与通行路径提示应用。它服务行走、
骑行、驾驶、通勤、看路、读标志或注意力可能分散、需要额外视觉提醒的人。目标是
做到**能用、好用、实用、可信**——提醒风险、边界和不确定性，但绝不承诺"可以走"，
也绝不替代用户自己的判断。

本仓库不只是 App：它还包含一个**闭环进化平台**，把真实使用、模型评测、延迟数据和
代码验证，转化为产品的下一轮迭代。

---

## 目录

- [仓库包含什么](#仓库包含什么)
- [系统架构](#系统架构)
- [两条运行链路（nearby 与 relay）](#两条运行链路nearby-与-relay)
- [闭环进化平台](#闭环进化平台)
- [可通行引导线](#可通行引导线)
- [端侧实时体验](#端侧实时体验)
- [目录结构](#目录结构)
- [快速开始](#快速开始)
- [本地 Qwen 运行时](#本地-qwen-运行时)
- [测试](#测试)
- [知识库](#知识库)
- [原则](#原则)

---

## 仓库包含什么

| 层 | 作用 | 位置 |
|---|---|---|
| **iOS App** | SwiftUI 摄像头应用、视觉引导 overlay、端侧感知（YOLO11n + TwinLite 路面）、引导线、模式栏 | `ios-vqa-app/VQASee` |
| **VQA 后端** | FastAPI 服务：WebSocket 信令、prompt/schema、场景记忆、经 `llama-server` 的 Qwen 3B/7B、fusion 兜底 | `server-vqa/app` |
| **闭环平台** | 诊断采集、数据集导入、标注、评测、对拍、回归门禁、感知配置 OTA | `server-vqa/app/diagnostic_*` |
| **离线 harness** | macOS SwiftPM CLI，用**真身** App 感知源码跑基准数据集 | `ios-vqa-app/perception-harness` |
| **Relay** | 公共 WSS relay，让走蜂窝网的 iPhone 也能连到 Wi-Fi 上的 Mac worker | `relay-server` |
| **iOS 自动化** | 构建 / 测试 / 归档 / TestFlight 脚本 | `deploy/ios` |

当前产品能力：nearby 自动发现、跨网络 relay、端侧 TwinLite 路面与车道、YOLO 障碍框、
可通行引导线。读文字 / 周围 / 详细问答以后再做。语音用于确认，不是主体验。

## 系统架构

```mermaid
flowchart TB
  subgraph Device["iPhone · VQASee (SwiftUI)"]
    CAM["摄像头帧"]
    LP["端侧感知<br/>YOLO11n · TwinLite 路面"]
    GL["引导线 + 车道图"]
    UI["视觉 overlay<br/>语音辅助确认"]
    CAM --> LP --> GL --> UI
  end

  subgraph Backend["Mac · server-vqa (FastAPI)"]
    SIG["WebSocket 信令"]
    VQA["VQA 服务<br/>prompt · JSON schema · 场景记忆 · fusion"]
    QWEN["本地 Qwen 3B/7B<br/>llama-server"]
    SIG --> VQA --> QWEN
  end

  subgraph Relay["relay-server (公共 WSS)"]
    R["client ⇄ worker 中转"]
  end

  subgraph Platform["闭环平台 (/diagnostics)"]
    CAP["采集 · 数据集导入 · 标注"]
    EVAL["评测 · 对拍 · 回归门禁"]
    CFG["感知配置 OTA"]
  end

  Device -- "nearby Wi-Fi / 热点" --> Backend
  Device -- "蜂窝网" --> Relay --> Backend
  Device -- "诊断帧" --> Platform
  Platform -- "OTA 感知配置" --> Device
  Backend -. "同一 FastAPI app" .- Platform
```

## 两条运行链路（nearby 与 relay）

iPhone 连到推理的两种方式，**都不需要路由器端口转发**。

```mermaid
flowchart LR
  subgraph Nearby["nearby（同 Wi-Fi / iPhone 热点）"]
    P1["iPhone"] -- "ws://mac-lan-ip:9000" --> M1["Mac 后端"]
  end
  subgraph Xnet["跨网络（蜂窝 ↔ 另一网络）"]
    P2["iPhone"] -- "wss client" --> RL["公共 relay :9100"]
    W["Mac worker"] -- "wss worker（主动外连）" --> RL
  end
```

- **nearby**：Bonjour `_vqasee._tcp` 自动发现（优先数字 IPv4），自动填地址；仍需
  你点 **开始视觉辅助**——摄像头绝不自动推流。切换网络（热点 ↔ Wi-Fi）会清掉过期
  IP 并重新发现，而不是钉死在失效地址。
- **relay**：两端都主动外连到带同一配对 token 的公共 relay，于是 4G/5G 上的手机
  也能连到 Wi-Fi 上的 Mac。

<details>
<summary>Relay MVP 限制与配置</summary>

- 单帧 Base64 上限 `900000` · 每客户端每分钟 `30` 帧 · 每客户端在途 `1` 个
- iOS 默认帧间隔 `2s`；帧质量随模式：
  行走 448px/120KB · 周围 640px/220KB · 详细 768px/320KB · 读文字 1024px/520KB

```bash
# 1) 公共主机（或本地测试）
export RELAY_PAIRING_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
bash ./start_relay.sh
# 2) 跑推理的 Mac worker
export RELAY_WORKER_URL=ws://<relay-host>:9100/ws/worker
export RELAY_PAIRING_TOKEN=<同一 token>
export WORKER_ID=local-mac-worker
bash ./start_worker.sh
# 3) iOS App：Server URL 填 ws(s)://<relay-host>:9100/ws/client，同 token + worker id
```
</details>

## 闭环进化平台

### iPhone 真机调试

运行 `bash start_device_debug.sh`，Mac 会打开真机调试页。手机 VQASee 菜单 → 真机调试 → 选择附近的 Mac；首次在 Mac 页面核对数字并点击“允许连接”，然后在手机点击“开始共享 App 屏幕”。下次连接会记住授权，无需复制地址或密钥。手机与 Mac 需在同一可信局域网；如需原始采样，显式打开“同时允许发送原始测试样本”。本功能共享 App 屏幕（最多 1 Hz），不启用前摄或录音，不保证包含系统弹窗。新 iOS 代码须先构建安装。

调试服务默认 9001，仅暴露配对调试 API，与原平台 9000 的模型/管理接口隔离。启动器每次生成新密钥，写入被 Git 忽略的 `build/device-debug/pairing.json`（权限 600）；证据位于 `build/device-debug/sessions/`。不要提交或分享该目录。Mac 页面显示屏幕、事件、样本、Codex 报告和复测关联，断流标注历史状态。

Codex 本地读取：`.venv/bin/python server-vqa/tools/device_debug_codex.py --pairing-file build/device-debug/pairing.json list`。使用 `inspect`、`download`、`watch`、`report-submit` 等命令读取和回写证据分析（实际命令见 `--help`）。工具不会自动生成根因；需要运行中的 Codex 读取真实证据、审查代码、提交报告并复测。详见 [真机闭环设计](docs/evolution/2026-10-06-device-debug-loop.md)。安装依赖沿用 `setup_mac.sh` core profile，无新增第三方包。

每个功能都必须**形成闭环**，而不是"做完一个页面"。平台把这个闭环变得具体、可检查，
入口在 `http://127.0.0.1:9000/diagnostics/ui`。

```mermaid
flowchart LR
  U["用户反馈 / 现场问题"] --> A["归因"]
  A --> PC["产品判断"]
  PC --> T["技术拆解"]
  T --> E["最小实验"]
  E --> MG["指标门禁"]
  MG --> S["发布 / 回滚"]
  S --> DT["文档 + 测试"]
  DT --> NX["下一轮评测样例"]
  NX --> U
```

感知子闭环让平台能在开源数据集上测试 iPhone 的**端侧**感知（YOLO + 分割 +
引导引擎），并把调好的配置推回设备——且**不需要**完整 Xcode：

```mermaid
flowchart LR
  DS["开源数据集<br/>(CamVid 语义标注)"] --> GT["真值<br/>可通行引导线"]
  subgraph Harness["macOS 离线 harness"]
    H["真身 App 源码<br/>YOLO + 分割 → 预测引导线"]
  end
  DS --> H
  GT --> EV["线级评测<br/>deviation · hit_rate · false_go"]
  H --> EV
  EV --> G{"回归门禁"}
  G -- 通过 --> CFG["PerceptionConfig 版本 +1"]
  CFG -- "OTA /runtime/perception-config" --> APP["iPhone 引擎"]
  APP -. "共享源码（符号链接）" .- H
```

- 可调 ROI + 阈值的**单一真源**：`server-vqa/app/perception_config.py`（Python），
  由 `PerceptionConfig.swift` 镜像，契约测试防漂移。
- **诚实能力探针**：predictor 会以原因上报 `unsupported`，而不是静默失败
  （例如缺 `onnxruntime`）。
- **回归门禁**：安全关键指标（如 `risk_miss`、`false_go`）相对基线不得变差，
  否则拦截候选。

## 可通行引导线

感知引擎输出**一条或多条可通行引导线**（带走廊半宽、置信度和风险段的折线），
而不是框。当自由空间破碎到无法成线时，显式降级为 `insufficient`，**绝不伪造**
一条直线。

```text
 图像帧（底边 = 你的脚下）
 ┌─────────────────────────────────────┐
 │                 · · · 地平线          │
 │                 ╱                    │
 │                ╱   ← 预测引导线       │
 │              ┆╱┆      （含走廊）      │
 │              ┆ ┆                     │
 │             ╱   ╲   ← 真值线          │
 │            ●  你                     │
 └─────────────────────────────────────┘
   紫色实线 = 设备预测（含走廊带）
   绿色虚线 = 真值可通行引导线（来自语义 mask）
```

预测线与真值线共用同一 schema（`app/guidance_path.py` ↔ `GuidancePath.swift`），
闭环才能公平打分。逐帧叠加对照见
`/diagnostics/datasets/ios-harness/frames/ui`。

## 端侧实时体验

用户真正看到的画面——安静的状态栏、叠在实时相机上的引导线、附近风险的提示条，
以及一句简短语音。语音只做确认，绝不承诺"可以走"。

```text
        ┌─────────────────────────────┐
        │  ● 已连接    行走模式   ⏱1.2s │   ← 状态：连接 · 模式 · 延迟
        │                             │
        │         (实时相机)           │
        │             ╱               │
        │            ╱  ← 引导线        │
        │          ┆╱┆     +走廊        │
        │          ┆ ┆                 │
        │         ╱   ╲               │
        │   ⚠ 右前 行人                │   ← 风险提示条（附近危险）
        │        ●  你                 │
        │                             │
        │  “前方可走，注意右前行人”      │   ← spoken_text / 摘要
        │        [  按住说话  ]         │   ← 按住说话提问
        └─────────────────────────────┘
```

- **状态明确**：发现中 / 已连接 / 推流中 / 处理中 / 超时 / 已断开 / 重连中——
  绝不静默卡住。
- **语音门控，但帧不丢**：每帧都会刷新屏幕；`SpeechGate` 只决定要不要*出声*，
  所以画面永不发呆，同时避免重复播报。
- **按住说话**：单轮提问，随下一帧作答。

## 目录结构

```text
onepiece/
├── ios-vqa-app/
│   ├── VQASee/VQASee/            # SwiftUI App + 端侧感知
│   │   ├── LocalPerception.swift LocalSegmentation.swift LocalVisionAnalyzer.swift
│   │   ├── GuidancePath.swift    PerceptionConfig.swift   CameraCapture.swift
│   │   └── StreamingViewModel.swift SettingsView.swift ...
│   └── perception-harness/       # 用真身 App 源码的 macOS SwiftPM CLI
├── server-vqa/
│   ├── app/                      # FastAPI 后端 + 闭环平台
│   │   ├── main.py signaling.py vqa_service.py prompts.py scene_context.py
│   │   ├── diagnostic_api.py diagnostic_capture.py     # 平台 UI/API
│   │   ├── perception_config.py guidance_path.py guidance_path_eval.py
│   │   ├── open_dataset_adapters.py path_* traversability_predictor.py
│   │   └── eval_baseline.py regression_gate.py
│   ├── tools/                    # run_ios_harness_eval.py, ...
│   └── tests/
├── relay-server/                 # 公共 WSS relay MVP
├── deploy/ios/                   # 构建 / 测试 / 归档 / TestFlight
├── docs/                         # decisions · evolution · model-lab · ui-lab · ...
├── AGENTS.md                     # 团队角色与工作协议
└── start_*.sh                    # backend / diagnostics / qwen / relay / worker
```

## 快速开始

### 在新 Mac 上一键安装

已 clone 仓库后，用 `setup_mac.sh` 一次装好所需依赖、模型和工具（幂等，可重复跑）。任一摘要出现 `FAIL` 时最终返回非零；仍继续检查其余已选择的模块，方便一次看清失败项：

```bash
bash setup_mac.sh                    # 默认装四块：core + models + qwen + ios
bash setup_mac.sh --skip-qwen --skip-ios   # 只装闭环平台 + harness + 模型
bash setup_mac.sh --with-depth       # 额外装可选深度模型
HF_ENDPOINT=https://hf-mirror.com bash setup_mac.sh --hf-mirror   # HF 走镜像
```

四个 profile（默认全开，`--skip-<name>` 单独跳过）：

- `core`：Python venv + 后端/闭环平台依赖 + 离线感知 harness（`swift build`，无需 Xcode）。
- `models`：Core ML 模型。通行分割模型从 Hugging Face 下载并转换，**需完整 Xcode.app 的 `coremlcompiler`**；缺失会明确跳过并提示（不静默失败）。
- `qwen`：本地 Qwen 运行时（Ollama + `qwen2.5vl:3b`，数 GB）。
- `ios`：完整 iOS 构建工具链（**需完整 Xcode + Ruby ≥ 3.2**）；签名与 TestFlight 需 Apple 账号，人工完成。

室内墙地面分割由 models profile 安装 `VQASeeIndoorSemantics.mlmodelc` 与标签契约。来源为 NVIDIA SegFormer-B0 ADE20K，转换依赖固定于 `server-vqa/requirements-indoor-semantics-export.txt`，使用独立运行环境；需完整 Xcode。单独安装：`python3 deploy/ios/install_indoor_semantics_model.py`；离线核验：同命令加 `--check`。照片推理在 iPhone 本地执行。模型许可需在分发前另行审核。

脚本结尾会打印每项「已装 / 跳过 / 失败」摘要和下一步命令。App 模型资源统一位于 `ios-vqa-app/VQASee/ModelResources/`；仓库已自带 `YOLO11nObject.mlmodelc`，分割模型、深度模型、服务器 ONNX 预测器在 `.gitignore` 中，由上面步骤按需安装。

可选离线场景语义观察研究环境（默认关闭）：
`bash setup_mac.sh --skip-core --skip-models --skip-qwen --skip-ios --with-spatial-memory`。
需现有 core 的 Pillow/packaging、models 的 PyTorch、`--with-spatial-geometry` 的 NumPy/SciPy 和 curl；缺失时会明确失败并指向对应 profile。
固定研究包见 `server-vqa/requirements-spatial-memory.txt`，官方 wheel 校验后安装到按 Python 版本派生的本地持久隔离目录，不覆盖 `.venv` 包、不下载模型。
运行研究脚本时将安装摘要中的完整路径显式传给 `--runtime`；模型目录须另行提供已核验缓存。
这只支持二维语义观察的保存/重载查询，未接 App，不代表物体实例、三维位置、准确度或实时性通过。详见 [研究说明](deploy/spatial/research/README.md)。

### 手动分步

```bash
# 0) 一次性创建虚拟环境
python3 -m venv .venv && source .venv/bin/activate
pip install -r server-vqa/requirements-dev.txt

# 1) 后端（推理/信令）
bash ./start_backend.sh                       # ws://localhost:9000/ws/signaling
# 或：HOST=127.0.0.1 PORT=9000 bash ./start_backend.sh

# 2) 只启动闭环平台（不做 Qwen 预热）
bash ./start_diagnostics_platform.sh          # 自动打开 /diagnostics/ui

# 3) 完整本地栈（后端 + 本地 Qwen）
bash ./start_local_vqa.sh

# 4) 离线感知 harness（macOS）
cd ios-vqa-app/perception-harness && swift build
./.build/debug/PerceptionHarness \
  --manifest ../../docs/datasets/camvid-manifest.jsonl \
  --model-dir ../VQASee/VQASee --out /tmp/camvid-ios-harness.jsonl
```

<details>
<summary>iOS 构建与发布</summary>

1. 在 `ios-vqa-app/VQASee` 用 Xcode 创建 `VQASee.xcodeproj`（签名/team/bundle id；
   相机/定位/本地网络权限；安装 iOS 平台运行时）。
2. `cp deploy/ios/ExportOptions.plist.template deploy/ios/ExportOptions.plist`
3. 自动化：
   ```bash
   bash deploy/ios/preflight.sh
   bash deploy/ios/build.sh                 # 设备/发布
   SDK=iphonesimulator CONFIGURATION=Debug bash deploy/ios/build.sh
   bash deploy/ios/test.sh
   bash deploy/ios/install_on_device.sh     # DEVICE_ID=<udid> 指定设备
   bash deploy/ios/archive.sh
   bash deploy/ios/release_testflight.sh
   ```
</details>

## 本地 Qwen 运行时

`start_qwen_local.sh` **直接启动 `llama-server`**（`Ollama.app` 内置的二进制），
而不是让 Ollama 托管——因为 Ollama 把 `qwen2.5vl` 的 `--image-min-tokens` 锁死在
**1024**，这主导了 prefill 成本。自己跑 server 就能传 `--image-min-tokens 256`。

同一张 448px 帧上实测（M4 Air, 16GB）：

| `image-min-tokens` | prompt tokens | **prefill** | decode |
|---|---|---|---|
| 1024（Ollama 默认） | 1048 | **~5.0 s** | ~1.4 s |
| 256（本运行时） | 280 | **~1.3 s** | ~1.2 s |

```bash
bash ./start_qwen_local.sh                    # http://127.0.0.1:11435 (start|stop|status|supervise)
QWEN_API_BASE_URL=http://127.0.0.1:11435 QWEN_MODEL=qwen2.5vl:3b bash ./start_backend.sh
MODEL=qwen2.5vl:7b bash ./start_qwen_local.sh # 选 7B 前先在 Mac 上拉一次
```

<details>
<summary>运行时参数与诚实的延迟预期</summary>

- 环境变量：`LLAMA_PORT` (11435) · `IMAGE_MIN_TOKENS` (256) · `IMAGE_MAX_TOKENS`
  (512) · `LLAMA_SERVER_BIN` · `OLLAMA_MODELS_DIR` · `MODEL` (`qwen2.5vl:3b`)。
- `USE_OLLAMA=1` 回退到 Ollama 托管（锁死 1024，API `:11434`）。
- `supervise` 在崩溃时重启 `llama-server` 并记录每次重启（不静默失败）。
- decode 长度：快速安全 schema `QWEN_MAX_TOKENS_FAST`（260）；完整描述
  `QWEN_MAX_TOKENS_FULL`（520）。
- 连续帧默认只带当前图 + 文本场景上下文；
  `QWEN_SEND_PREVIOUS_IMAGE_IN_INCREMENTAL=1` 才启用双图对比。

> 单张 3B 帧在 16GB Mac 上约 2.5 s（prefill ~1.3 s + decode ~1.2 s）——完整帧推理
> **达不到** 1s。亚秒级的*体感*来自场景记忆门控：静止帧既不重复推理也不重复播报。
</details>

## 测试

```bash
source .venv/bin/activate
pytest server-vqa/tests            # 后端 + 闭环平台
pytest relay-server/tests          # relay
cd ios-vqa-app/perception-harness && swift build   # 编译真身 App 感知源码
bash deploy/ios/test.sh            # iOS（需完整 Xcode）
```

测试底线（见 `AGENTS.md`）：mock 要接近真实输入/错误/schema；断言必须覆盖安全、
失败恢复、超时、不确定性和用户可见状态；绝不为掩盖 bug 删测试。

## 知识库

产品在 `docs/` 下自我沉淀：

- `docs/README.md` — 先读哪两页，其余是证据日志
- `docs/CURRENT.md` — **现行产品现状（宪法页）**
- `docs/SPATIAL_HANDOFF.md` — 室内建图现状，开工只读这一页
- `docs/decisions/` — 历史产品/架构决策记录
- `docs/evolution/` — 迭代/闭环记录
- `docs/model-lab/` — 模型与评测经验（如 CamVid 调色板修正、引导线基线）
- `docs/ui-lab/` — UI 打磨经验
- `docs/performance/` — 延迟与系统经验
- `docs/tech-radar/` — 外部 SOTA 技术情报
- `docs/roadmap.md` — 北极星与阶段

## 原则

1. **安全第一** — 不隐藏、不静默丢弃影响安全的视觉变化。
2. **视觉引导优先，语音辅助确认** — 用户应能*看见*路径、障碍和风险；语音用于补充
   和免手操作。
3. **延迟就是体验** — 编码 / 网络 / 模型耗时都是产品指标。
4. **不允许静默失败** — 失败要在 UI、语音、日志或测试中可见，并有清晰恢复路径。
5. **默认保护隐私** — 尽量少持久化图片/音频；任何远程路径都要说明。
6. **辅助而非接管** — VQASee 提醒风险、边界和不确定性；绝不承诺"可以走"，也绝不
   替代用户在行走、骑行、驾驶时的主动观察。

---

<sub>团队角色、工作协议和 skill 调度见 [`AGENTS.md`](AGENTS.md)。</sub>
## 室内空间扫描（2026-10-10 实验版本）

VQASee 默认入口为空间扫描：开始扫描，缓慢移动 iPhone 观察墙角与地面；结束后可旋转查看已观测结构、查看俯视运动轨迹、修正顶点、保存并从历史重开。此路径使用摄像头与 IMU，不要求 LiDAR，不请求深度。

“导出扫描（含照片）”生成完整 ZIP。Mac 后端重启加载新代码后，从闭环主页的“空间扫描”进入 `/spatial-scans/ui`，导入包查看轨迹、观测面和关键帧。资料页面仅允许本机访问。照片默认仅存本机，导出由用户主动选择。

页面的“检查扫描质量”使用项目内固定版本的 OpenCV SIFT 源码进行照片匹配，不依赖 Python `cv2` wheel 或系统 OpenCV。原版算法与必要支持模块保存在 `native/spatial_sift/`，来源、校验值与许可证随源码保存。参数和 C 接口可在项目内调整；独立几何校验仍使用可读的 NumPy 实现。它不上传照片，也不证明真实尺寸或漂移。

本机结构线实验复用同一固定 OpenCV 源码中的 LSD 检测器，不新增 `cv2` wheel。只读取明确选定的训练照片和相机位姿，保留全部线对及第三训练视图的支持/失败记录，输出局部三维线候选；线段重复出现不等于独立米制墙面真值或完整房间。源码库由上述 core 安装流程一并构建。

“建立真实稀疏重建”使用同一扫描的多视角照片与逐帧相机参数生成空间点，首次计算约一分钟（随数据和机器变化）。时间轴同步照片、位姿与视锥；三维视图支持拖动旋转和滚轮缩放。重建点与支持点轮廓明确为最终结果，不能当作逐帧历史。照片显示重投影及红色留出核对失败；通过者误差和全部核对误差分开统计，完整证据可下载。最多240帧、320个帧对、20000点；容量限制与拒绝原因可见，原扫描不改写。三视图核对失败说明几何尚不一致，不能把小幅移动当纯旋转误差或将点云轮廓称完整墙面。没有新增第三方依赖，仍使用上述源码构建与 NumPy/Pillow。

统一安装：`bash setup_mac.sh --skip-models --skip-qwen --skip-ios`。core profile 检查/安装 CMake、使用 C++ 编译器构建源码并验证本机库。单独重编译：`bash deploy/spatial/build_sift.sh`，需要 CMake 与 Xcode Command Line Tools；缺少编译产物时页面明确显示不可用，不静默退回旧算法。Mac 构建产物不适用于 iPhone；端上移植与实时预算需另行验收。

当前是已观测空间草图与采集闭环，尚非自动完整房间/门窗模型；3DGS 重建尚未实现。真机精度与实时性未验收，详见 [实现与验收记录](docs/evolution/2026-10-10-spatial-scan-first-implementation.md)。旧物品记录仍在菜单中。

空间高斯重建的 CPU 参考实验是可选依赖，可在 Python 3.9–3.12 环境运行：
`bash setup_mac.sh --skip-core --skip-models --skip-qwen --skip-ios --with-spatial-gs`。
训练和照片处理均在本机执行。该低分辨率研究实现具有真实高斯参数、透明度合成、
独立留出照片与 PLY 产物，但没有生产版密度控制或高阶外观，不能等同完整房间、
精确尺寸或实时 3DGS 已通过验收。训练状态、失败与留出渲染在空间采集页面显示。

保存照片的离线深度先验实验默认关闭，按需安装：
`bash setup_mac.sh --skip-core --skip-models --skip-qwen --skip-ios --with-spatial-depth`。
需已有仓库 `.venv`（先运行 core profile）；使用 Python 3.9–3.12、torch 2.2.2、
torchvision 0.17.2、NumPy<2、OpenCV headless 4.10.0.84 与 curl。
固定官方 Depth Anything V2 Small 源码和99.2MB权重，下载支持续传，完整SHA256通过后才可使用。
默认权重保存在已忽略的 `server-vqa/models/depth-anything-v2-small/`；
`VQASEE_SPATIAL_DEPTH_WEIGHTS=/本机/已验证权重.pth` 可指定已有文件（例如本轮临时下载文件），
安装器仍检查同一完整摘要。可用 `bash deploy/spatial/install_depth_prior.sh --check-only`
进行不联网、不安装的依赖与身份检查。来源与许可详见 [官方源码记录](native/depth-anything-v2/README.md)。
模型在本机处理照片；结果是模型估计的相对深度，不能当实测墙体或尺寸。
尺度对齐仅使用训练证据，留出照片用于独立核对；白墙未知区域、核对失败与CPU耗时须明确呈现。
安装通过不代表真实画质、精度或实时性通过验收。

官方 Brush 的本机离线训练实验默认关闭，安装入口：
`bash setup_mac.sh --skip-core --skip-models --skip-qwen --skip-ios --with-spatial-brush`。
需 Rust `rustc >= 1.88`、cargo、git、curl 和 Python 3；缺少 Rust 时安装器给出官方 rustup 修复指引，
不会运行未经校验的 `curl | sh`。安装器固定 Brush 提交、逐份核对 164 个官方 Git blob，
其中包含编译必需的 29.43 MB LPIPS 权重；随后只应用仓库记录的小图片读取补丁并校验补丁后源码。
默认源码和编译产物分别位于已忽略的 `server-vqa/models/spatial-brush/source/` 与
`server-vqa/models/spatial-brush/target/release/brush_app`，可用
`VQASEE_SPATIAL_BRUSH_BINARY` 指定同结构的 `target/release/brush_app` 路径。
`bash deploy/spatial/install_brush.sh --check-only` 只检查本机源码、补丁、Rust版本和二进制回执，
不联网、不安装。安装成功只证明训练器可用；训练失败、留出照片画质和真实房间几何仍须单独验收。
来源、补丁与限制见 [Brush 本地实验说明](native/brush/README.md)。
自由视角页面需要支持 WebGL2、浮点颜色缓冲及浮点混合的浏览器；缺失时明确提示无法预览，
原生训练器验证照片与模型下载仍可使用。网页预览匹配训练器不代表重建几何或画质已通过验收。

室内照片/相机几何离线诊断（可选，Python 3.10–3.12）：
`bash setup_mac.sh --skip-core --skip-models --skip-qwen --skip-ios --with-spatial-geometry`。
固定 NumPy/SciPy/pycolmap 版本位于 `server-vqa/requirements-spatial-geometry.txt`，安装到
仓库 `.venv`。不会自动更改扫描相机或安装手机模型。实验运行说明见
`deploy/spatial/research/README.md`；求解成功不等于房间尺寸或恢复精度通过。

可选开放词汇二维定位 CPU 研究：
`bash setup_mac.sh --skip-core --skip-models --skip-qwen --skip-ios --with-spatial-grounding`。
该选项同时启用 spatial-memory 隔离运行库；从官方 `IDEA-Research/grounding-dino-tiny` API 冻结当前 revision，下载必要配置/分词器/权重，使用官方 LFS SHA256 校验权重。
模型保存在本地忽略的持久目录，完整路径由安装摘要输出；权重单次下载最多180秒、其他请求最多120秒、下载流程总预算420秒，无重试，失败显式记录。
不读取或上传私照，不修改现有模型或默认值，不接 App；CPU耗时、定位准确度、实例与三维能力均需另行验证。
