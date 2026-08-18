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

## 追加：逐帧「看得见」识别效果（2026-08-18 补）

用户反馈：iPhone 真身评估页只有聚合指标卡，「没看到 CamVid 图片，也没看到感知层在图上识别的效果」。这违背 VQASee「视觉引导优先、用户能直观看到」的北极星——诊断台自己却只有数字。

改动：

- harness `main.swift` 逐帧额外吐出 `objects`（YOLO 检出物体：kind/中文 label/confidence/direction + Vision 归一化 box）与 `roi`（本帧实际使用的近/左/右 ROI，避免叠加层与决策漂移）。prediction 主体 schema 不变，评分/parity 无感。
- 新增 `GET /diagnostics/datasets/ios-harness/frames/ui`：分页在每张 CamVid 原图上用 SVG 叠加——蓝虚线=检出物体框，绿/黄/红/灰=近/左/右区域预测状态；右侧「真实答案 vs iPhone 预测」对照表并自动标「漏报/误阻挡」。Vision 坐标下-左原点，叠加时翻转 y 贴合 `<img>`。
- 无预测的帧显式提示「该帧没有对应预测」，不静默丢弃。iPhone 真身评估结果页加醒目入口。
- 测试：`test_perception_config_api.py` 增 2 例（叠加/对照渲染 + 漏报标记 + 缺文件 404）；全套 `pytest server-vqa/tests` → **165 passed**。

意义：`status_accuracy=0.38 / false_block=549` 从抽象数字变成可肉眼核查的画面——能直接看清真身把公交车、交通灯识别在哪、近处 ROI 为何在驾驶图上过度报「疑似占用」，为下一轮调阈值/换数据集提供证据。

**结果缓存 + 按需重跑（补）**：用户问「每次都要跑真身吗」。答案是否——预测跑完存成文件，评估/逐帧页直接读，无需每次重跑。`/run` 加 `force` 参数与新鲜度判断（`_harness_cache_info`）：数据集 manifest、harness 二进制、生效配置版本都没变时返回 `status=cached` 秒回，不重复跑；任一变化则在向导页明确提示「建议重跑」并列出原因，绝不静默复用过期结果。同时修一个闭环缺口——`/run` 现在把**当前生效的 perception config** 写临时文件用 `--config` 传给 harness，所以在 OTA 编辑器调完 ROI/阈值再重跑，结果才会真正变化（tune→rerun→gate→ship 闭环成立）。向导页 Step 1 有缓存时改为「直接查看评估（用缓存）」+「↻ 用当前配置重新跑」。测试：`test_ios_harness_run_reuses_fresh_cache_without_running`（断言有缓存时绝不 spawn 子进程）、`test_ios_harness_cache_marked_stale_after_config_bump`。

**内容指纹新鲜度（补）**：把新鲜度判定从「靠文件 mtime」升级为「靠内容指纹」。`/run` 成功后写一份 meta 边车 `/tmp/{stem}-ios-harness.meta.json`，记录 manifest 内容 sha256、配置行为哈希（`PerceptionConfig.content_hash`，只对 ROI/阈值取哈希）、harness 二进制哈希、生成时间与帧数。`_harness_cache_info` 优先按这三者比对（`fingerprint=content`）：即使字节变了但 mtime 未变也能识破；手动跑（无 meta）时降级为 mtime 近似（`fingerprint=mtime`），向导页显式标注判定依据。测试：`test_ios_harness_cache_uses_content_fingerprint_meta`（改 manifest 字节 / bump 配置行为哈希 → 判为过期）。全套 → **171 passed**。

**一键在本机跑真身（补）**：用户反馈手动 `swift build`+跑 harness+复制路径太繁琐。因为诊断台与 harness 同在一台 Mac，新增 `POST /diagnostics/datasets/ios-harness/run`：服务器直接 subprocess 调用已编译二进制（缺则 best-effort `swift build`），跑完把预测路径自动带入评估页。诚实能力报告、无静默失败：非 macOS→`unsupported/not_macos`；无 swift 工具链或编译失败→`needs_build`（附编译报错尾部）；harness 非零退出（如缺 YOLO 模型）→`error`（附 stderr 尾部）；产出为空也报错。向导页 Step 1 改为「▶ 一键在本机跑」按钮 + 手动命令折叠兜底。测试：`test_ios_harness_run_404_when_manifest_missing`、`test_ios_harness_run_reports_unsupported_off_macos`（monkeypatch 平台，断言绝不 spawn 子进程）。全套 → **168 passed**。

**结果筛选器（补）**：701 帧无法逐条看，逐帧页加 `filter` 参数与顶部选择器（全部/漏报/误阻挡/有分歧/全对/无预测），每类带**实时计数**，非法值回退「全部」不报错、不静默空。CamVid 上按帧计数：漏报 208、误阻挡 517、有分歧 684、全对 17、无预测 0（注意这是**按帧**去重计数，与报告里 `risk_miss_count/false_block_count` 的**按区域**计数口径不同）。用户可一键跳到最该复盘的坏例子。测试：`test_ios_harness_frames_ui_filters_by_result_category`。全套 `pytest server-vqa/tests` → **166 passed**。

## 角色评审

- 乔布斯：闭环成立——平台能用客观 GT 考 iPhone 真身，且调参能经门禁回流到设备；普通路径向导化。
- 全麦：harness 保真（真身 YOLO+分割）、prediction schema、config 语义、门禁指标一致。发现 CamVid 上 false_block 偏高，是真身在“驾驶数据集”上按“行走”阈值过度保守，属真实信号，进 model-lab 复盘。
- 罗根：harness/CI 用 SwiftPM CLI，OTA 走既有 HTTP 侧信道，失败回退可观测；无静默失败。
- 思余：设置页显示生效版本与回退态（橙色），OTA 失败对用户可见。
