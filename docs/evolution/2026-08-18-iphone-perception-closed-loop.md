# iPhone 本地感知层接入闭环实验平台（harness + 配置 OTA）

日期：2026-08-18
状态：已实现并本地验证（后端 pytest + macOS harness 真跑；iOS 端代码已写，待 `deploy/ios/test.sh` 在 Xcode 环境验证）

## 背景与缺口

上一轮（见 [2026-08-18-prediction-pipeline-closed-loop.md](2026-08-18-prediction-pipeline-closed-loop.md)）打通了服务器 ONNX 代理预测器。但两个真实缺口未闭合：

- 缺口 A（测 iPhone）：iPhone 感知（YOLO11n Core ML + `LocalPathGuidanceEngine`）只跑实时相机帧，无法用 CamVid 这类**有客观 GT** 的基准集考 iPhone 真身。服务器 ONNX 只是代理，不是真身。
- 缺口 B（更新回 iPhone）：ROI/阈值全是编译期常量，没有任何运行时下发通道。

## 本轮做了什么

用一份**版本化 PerceptionConfig 作为闭环枢纽**，让“测真身”和“下发”共用同一 schema 与默认值：

```
CamVid(GT) → macOS harness(=iPhone 真身: YOLO+分割+通行引擎) → prediction JSONL
          → 平台 evaluate + parity + 回归门禁 → 调参 bump version
          → GET /runtime/perception-config → iPhone 连接后拉取并应用
```

### Phase 1 — macOS 离线 harness（真身可测）

- 因本机只有 Command Line Tools（无完整 Xcode/simctl），**采用方案 1b：macOS 原生 SwiftPM CLI**（计划里的备选，环境所迫，非 1a Simulator）。
- 新增 [ios-vqa-app/perception-harness/](../../ios-vqa-app/perception-harness/)，通过**符号链接**复用 App 真身源码（`LocalPerception/LocalVisionAnalyzer/LocalSegmentation/LocalMonocularDepth/PerceptionConfig`），不是重写。单一真源，改 App 即改 harness。
- `LocalPerception.swift` 的 `import ARKit` 用 `#if canImport(ARKit)` 守卫；macOS 无 ARKit → 深度 `unsupported`（iOS 行为不变）。
- `LocalVisionAnalyzer` 加 `init(modelBundle:config:)` 与 `analyze(orientation:)`（附加参数，默认保持 App 行为），使 harness 能注入 App 的 Core ML 模型、按数据集朝向 `.up` 跑。
- harness 读平台 manifest 的 `image_path` → 解码为 32BGRA `CVPixelBuffer` → 跑真身 → 输出 `prediction_source=ios_coreml_offline_harness`、`config_version`、`depth_capability` 的 JSONL。**模型没加载则 fail loud**（避免空检测器把一切判为可走）。
- 新增 [server-vqa/tools/run_ios_harness_eval.py](../../server-vqa/tools/run_ios_harness_eval.py)：harness JSONL 当 prediction 跑 `evaluate_path_guidance` + 与服务器 ONNX 代理 `compute_parity` + 可选 `--baseline`/`--gate`。
- 诊断台新增“iPhone 真身评估”向导（`/diagnostics/datasets/ios-harness/ui`）：普通路径只问“选哪个数据集 + 贴预测文件路径”，给出 build/run 命令；坏路径、代理不可用都显式报错，不静默。

### Phase 2 — 引擎配置化（统一可调项）

- 新增 [server-vqa/app/perception_config.py](../../server-vqa/app/perception_config.py)：`PerceptionConfig`（version + 3 个 ROI + 5 个阈值），严格范围校验（越界/ROI 越框/未知键→硬报错，不静默 clamp），版本单调、内容 hash。
- 新增 Swift [PerceptionConfig.swift](../../ios-vqa-app/VQASee/VQASee/PerceptionConfig.swift)：运行时结构 + `.default`（ROI 复用引擎常量，单一默认源）+ 与 Python 逐字段对齐的 `PerceptionConfigWire: Codable` + 镜像的范围校验。
- 重构 `LocalPathGuidanceEngine.evaluate` 与分割 cue 读取，从 `config` 取 ROI/阈值（默认值 = 原常量，**行为不变**）；harness 支持 `--config`。

### Phase 3 — 配置 OTA 回 iPhone（轻量、安全）

- 后端 `GET /runtime/perception-config` 返回版本化配置（与 `/runtime/status` 同款 HTTP 侧信道）。
- 诊断台“感知配置”编辑器 `/diagnostics/perception-config/ui`：改数值 → `POST /diagnostics/perception-config/bump` 校验并 bump version；非法值 400 且不落盘。
- iOS：`StreamingViewModel` 连接后 `refreshPerceptionConfig()` 拉取→解码→校验→应用到两个采集代理的分析器；失败**回退编译期默认并在设置页可见**（`感知配置版本` 变橙色 + 文案）。OTA 只下发数值，不下发代码/模型。

### Phase 4 — 门禁、测试、文档

- `run_ios_harness_eval.py --gate <baseline>` 接入 `regression_gate`：`risk_miss` 变差则非零退出（4），阻止 bump/下发。
- 测试：`test_perception_config.py`(13) + `test_perception_config_api.py` + `test_perception_config_swift_parity.py`（防 Python↔Swift schema 漂移）+ `test_ios_harness_eval.py`（评分 + 门禁）。

## 验证（已跑）

- 后端：`pytest server-vqa/tests` → **163 passed**。
- harness 真跑 CamVid 701 帧（14s）：`status_accuracy=0.3828`、`focus_direction_accuracy=0.2468`、`risk_miss=233`、`false_block=549`、`unknown_rate=0.0014`。详见 model-lab 卡。
- 跨语言：Python 生成的默认 config JSON 喂给 Swift harness `--config`，输出与无 config **逐帧一致**（行为不变）；改 `near_blocked_area=1.0` 后近处 `blocked→caution`、`config_version` 透传；越界 config **fail loud**（rc=1）。
- OTA：`GET /runtime/perception-config` v1 默认 → bump v2 → runtime 反映 → 非法 400 不落盘。

## 未做 / backlog（明确）

- iOS 端 App 无法在本机编译（无 Xcode）；`StreamingViewModel/CameraCapture/SettingsView` 改动需人工跑 `bash deploy/ios/test.sh` 验证。
- device_benchmark（真机含 LiDAR 深度）未做，留 backlog：harness 只覆盖“仅相机”分支。
- 模型 OTA（下发 .mlmodelc）未做（按选择只做配置 OTA）。
- 自动扫描候选配置（评测驱动自动 bump）未做，当前为手动调参 + 门禁把关。

## 角色评审

- 乔布斯：闭环成立——平台能用客观 GT 考 iPhone 真身，且调参能经门禁回流到设备；普通路径向导化。
- 全麦：harness 保真（真身 YOLO+分割）、prediction schema、config 语义、门禁指标一致。发现 CamVid 上 false_block 偏高，是真身在“驾驶数据集”上按“行走”阈值过度保守，属真实信号，进 model-lab 复盘。
- 罗根：harness/CI 用 SwiftPM CLI，OTA 走既有 HTTP 侧信道，失败回退可观测；无静默失败。
- 思余：设置页显示生效版本与回退态（橙色），OTA 失败对用户可见。
