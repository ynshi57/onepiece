# 诊断标注台首次现场反馈闭环

Date: 2026-08-11

## 反馈/问题

用户重新安装 VQASee 后，在“看周围”和“走路”模式打开上传诊断帧，诊断台出现两组数据。现场反馈：

1. 走路模式引导线不能实时输出，感觉无法预测或给出用户引导轨迹。
2. 室内办公环境被识别出摩托车，视觉识别误差大。
3. 总是输出“模型输出异常，暂时无法可靠描述画面”。
4. 诊断标注台看不懂，“已有标注”不知道是什么意思、需要填写什么。
5. Mac 后端执行 `bash ./start_local_vqa.sh` 后电脑明显卡顿。

## 事实与证据

- 诊断目录：
  - `server-vqa/diagnostic-captures/session-ios-2026-08-11T08-01-07Z-203B1D3C`
  - `server-vqa/diagnostic-captures/session-ios-2026-08-11T08-06-44Z-C7AAFB38`
- 看周围 session：30 帧，`sent_to_backend=7`，`captured_while_in_flight=23`。
- 走路 session：17 帧，`sent_to_backend=5`，`captured_while_in_flight=12`。
- 本地模型统计：看周围中出现 `motorcycle=2`，实际从图像看是室内办公场景，疑似蓝色椅子/物体误检。
- 诊断 JPEG 当前保存为横向/旋转 90 度；iOS Vision 分析使用 `.right` 方向，后端/Qwen 看到的图像方向和本地 Vision 不一致。
- 当前诊断 manifest 只有帧与本地感知 metadata，没有保存 Qwen 原始输出/最终 fused result，因此只能间接判断“模型输出异常”的原因。

## 四角色判断

### 乔布斯：产品

先解决用户看得见、能标注、能复现的问题。诊断台必须让非工程用户知道要填什么；App 不能把“引导线”伪装成真实导航轨迹。

### 罗根：系统/性能

核心问题之一是图像方向链路不一致：本地 Vision 用 upright 方向，但发送给后端/Qwen 的 JPEG 是 sideways。这会降低模型质量，也让诊断台误导判断。Mac 卡顿来自本地 Qwen/llama-server 加载和推理，需降低默认资源占用并让用户知道如何停止。

### 思余：UI/可访问性

“已有标注”无解释是不合格 UI。应显示“暂无标注”和填写说明，并展示本地模型当时输出，方便用户判断“误检/漏检/类别错误”。

### 全麦：模型/后端

“模型输出异常”通常说明 Qwen 返回了截断或非法 JSON。可能原因：旧后端未启用 strict schema、Ollama json_object 不强约束、max_tokens 不够、图像方向错误导致模型输出发散。诊断数据目前不能直接分析 Qwen 能力，因为没有保存 Qwen response。

## 已执行的最小修复

- `ios-vqa-app/VQASee/VQASee/Networking.swift`
  - `FrameJPEGEncoder` 输出 JPEG 前使用 `.oriented(.right)`，让发给后端/Qwen/诊断台的图像方向与本地 Vision 一致。
- `server-vqa/app/diagnostic_api.py`
  - 标注页增加填写说明。
  - “已有标注”为空时显示“暂无标注”。
  - 每帧显示 mode/event/reason、本地快速感知、本地模型输出。
  - 标注选项改成用户可理解文案。

## 验证

- 后端：`source .venv/bin/activate && pytest server-vqa/tests` → 86 passed。
- iOS：`bash deploy/ios/test.sh` 未通过，原因是本机/沙箱环境无法找到 `iPhone 17` simulator 且 CoreSimulatorService 不可用；需要用户在 Xcode 或可用模拟器/真机上验证。

## 下一步

1. 重新启动后端，重新安装/运行 iOS，上传 5～10 帧，确认诊断台图片方向是否变成正向。
2. 用诊断台给误检帧打标：`类别错误`，备注“室内办公区，蓝色椅子/物体被识别成摩托车”。
3. 增加 Qwen 原始输出/最终结果到 diagnostic manifest，才能准确分析“模型输出异常”。
4. 调整本地 YOLO 策略：室内模式/低置信或小框车辆降权；车辆类不要直接用高置信播报，先说“疑似”。
5. 增加低资源启动模式，降低 Mac 卡顿。

## 2026-08-11 追加：标注删除与 Mac 卡顿裁决

### 标注删除

已新增：

- 已有标注旁的“删除这条标注”按钮。
- `DELETE /diagnostics/sessions/{session_id}/labels/{label_index}`。
- 后端测试覆盖新增删除 API。

验证：

```bash
source .venv/bin/activate && pytest server-vqa/tests
```

结果：87 passed。

### Mac 卡顿：不是临时参数问题

乔布斯裁决：本地 Mac Qwen 只能作为开发/实验 worker，不能作为最终产品默认实时路径。真正解法是产品架构调整：

1. walking 连续反馈默认由 iPhone 本地感知 + 质量门控 + 近处风险规则完成。
2. Qwen 只做低频复核、看周围/详细/用户主动提问。
3. 本地 Mac Qwen worker 要加资源隔离、空闲卸载、性能仪表盘和可停止入口。
4. 长期产品路径应是远端 GPU / 更轻模型 / iPhone 端专用小模型，而不是让普通 Mac 长时间跑 3B VLM 实时流。

## 2026-08-11 追加：标注类型重构与 ground truth 可用性

现场反馈：用户认为标注类型混乱，没有“我看到的真实情况”这种选项；`画面变化明显` 与 `本地模型输出：无` 看起来矛盾。

事实分析：

- 最新 session：`ios-2026-08-11T11-48-50Z-B433CCC9`。
- `frame-0001` 真实画面为室内地面 + 右前方蓝色水桶；本地模型误检为正前方人(98%)、车辆(92%)。
- `frame-0002` 显示“画面变化明显、本地模型输出：无”。这不是同一类信号：前者是亮度/纹理变化检测，后者是目标检测为空。不是算法矛盾，但 UI 表达造成误解。

已完成修复：

- `server-vqa/app/diagnostic_api.py`
  - 标注类型改成 VQASee 任务导向：真实画面记录、无明显风险、误报、类别错误、漏报、方向错误、模型输出异常、旧结果/处理中、图像质量问题、其他。
  - 增加结构化 ground truth 字段：`true_scene`、`true_risks`、`false_positives`、`missed_risks`。
  - 页面把“本地快速感知”改成“画面变化检测”，把“本地模型输出”改成“目标检测结果”。
  - 增加说明：画面变化明显只表示亮度/纹理变化，不代表识别到物体。
- `server-vqa/tests/test_api.py`
  - 覆盖结构化标注保存和 UI 文案。

验证：

```bash
source .venv/bin/activate && pytest server-vqa/tests
```

结果：91 passed。

Ground truth 使用原则：

- `true_scene` 用于建立真实场景描述。
- `true_risks` 用于风险召回评估。
- `false_positives` 用于误报统计，例如“水桶→车辆”。
- `missed_risks` 用于漏报统计，例如“漏报台阶/路沿”。
- 只有结构化字段稳定后，才适合做自动评估；纯自然语言备注只能辅助人工复盘。

## 2026-08-11 追加：诊断评估报告 MVP

目标：评估报告不给普通用户看，而是给乔布斯 / 罗根 / 思余 / 全麦看，用于把 session 自动归因成指标、问题和任务建议。

已完成：

- 新增 `server-vqa/app/diagnostic_report.py`
  - 输入：manifest rows + structured labels。
  - 输出：核心结论、关键指标、自动发现的问题、任务建议。
  - 当前可发现：
    - `high_in_flight_ratio`：后端实时链路跟不上。
    - `indoor_vehicle_false_positive`：室内 vehicle 类误报候选。
    - `person_false_positive`：person 类误报候选。
    - `missed_risk`：用户标注的漏报风险。
    - `model_output_error`：用户标注模型输出异常。
    - `missing_qwen_raw_output`：诊断数据缺少 Qwen 原始/最终输出。
    - `unstructured_labels`：标注缺少结构化 ground truth。
- 新增 API：
  - `GET /diagnostics/sessions/{session_id}/report`
  - `GET /diagnostics/sessions/{session_id}/report/ui`
- 诊断台 session 列表增加“评估报告”入口。
- `server-vqa/tools/analyze_diagnostic_capture.py` 增加 `evaluation_report` 字段。
- 测试覆盖报告 API、HTML 入口、问题发现和任务建议。

用真实 session `ios-2026-08-11T11-48-50Z-B433CCC9` 跑出的自动结论：

- 核心问题：后端实时链路跟不上采集节奏。
- 指标：36 帧中 26 帧为 `captured_while_in_flight`，比例 72%。
- 自动发现：
  - 后端 in-flight 过高。
  - 室内 vehicle 类误报候选。
  - 缺少 Qwen raw/fused output，无法准确分析模型输出异常。
  - 现有标注是旧备注型标注，缺少 `true_scene/true_risks`。

验证：

```bash
source .venv/bin/activate && pytest server-vqa/tests
```

结果：92 passed。

下一步：把 Qwen raw output、schema_name、qwen_http_ms、fused result 写入 diagnostic manifest，让报告能真正区分模型输出异常、parser bug 和延迟瓶颈。
