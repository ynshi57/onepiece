# TwinLiteNet 上 App + 路面模型 adaptor

## 最终结论

可以先上 App 看效果，但是实验叠图，不是产品默认可走区。

TwinLiteNet 12 帧没过视觉门，DA 是 BDD 驾驶可走区域，不是行人行道。上 App 是为了真机看红区/绿线/蓝引导线，并量 ANE 延迟。

工程 adaptor 已经做成换模型点：analyzer 和 overlay 只消费 `RoadSurfaceMaps`。以后换方案加一个 `RoadSurfaceBackend` + `road_backend` 枚举值，不改感知架构。

## 共识

- live 默认 `road_backend=twinlite`（实验）
- walk/drive 评测显式 `road_backend=mc5`，不被 TwinLite 污染
- 红色 = 驾驶 DA，绿色 = 车道像素，蓝色 = DA 中线；有真实线就不画假梯形
- 行人可走区仍未解决；OTA 改 backend 要下次启动才加载模型

## 投票

GPT / Sonnet 有条件赞成，Gemini / 乔布斯赞成。子代理额度不足，审阅按代码清单写出。

## 已执行

- Core ML：`VQASeeTwinLiteNet.mlmodelc`（~1MB）
- Swift：`RoadSurface.swift` 协议、TwinLite / mc5 两个 backend；`off` 就是 off
- App overlay：DA + 车道 + `GuidancePath`；有真线不画假梯形
- Python/OTA：`road_backend`；诊断台可切换
- harness-config-walk/drive 锁定 mc5
- 验证：PerceptionHarness `swift build` 绿；相关 pytest 112 passed

## 待办 / 阻塞

- App 未用完整 Xcode 编译真机画面（未验证 / 待 Xcode 复核）
- ANE 延迟未签；Intel Mac 墙钟不能当 iPhone 结论
- TwinLite 仍不是产品信任默认；0001TP 类窄缝中线仍可能弯

## 关键路径

- `ios-vqa-app/VQASee/VQASee/RoadSurface.swift`
- `ios-vqa-app/VQASee/VQASee/LocalVisionAnalyzer.swift`
- `ios-vqa-app/VQASee/VQASee/CameraRiskOverlay.swift`
- `server-vqa/app/perception_config.py`
- `deploy/ios/convert_twinlite_coreml.py`

## 风险与回滚

OTA 或诊断台把 `road_backend` 设为 `off`（无路面模型）或 `mc5`（人/车可走区，需 bundled Seg5）。不要把 TwinLite DA 说成可以走。

更新时间: 2026-09-21 12:42
