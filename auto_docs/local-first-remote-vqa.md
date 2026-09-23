# 本地感知默认 · 远程 VQA 设置开启

## 最终结论

「开始观察」默认不再连 Mac。本地 TwinLite/YOLO 叠层即可用。远程 Qwen 在设置「远程风险解释」打开后再 Bonjour/WebSocket。

## 方案要点

- `RemoteVQASessionPolicy` + `StreamingViewModel.isRemoteVQAEnabled`（UserDefaults，默认 false）
- 关：`startLocalOnlyStreaming`；`sendFrame` 只更新叠层与本地语音；停 Bonjour
- 开：保留原连接路径；关→开时尝试 connect，失败不关掉本地会话
- 关时按住提问可见拒绝；诊断上传依赖远程

## 已改文件

- `StreamingViewModel.swift`、`SettingsView.swift`、`AssistanceScreen.swift`、`PureHelpers.swift`
- `VQASeeTests.swift`、`docs/CURRENT.md`

## 验证

- `xcodebuild` simulator Debug：**BUILD SUCCEEDED**
- `testRemoteVQADefaultsOffWhenKeyMissing` + `testRemoteVQAGatesWhenEnabled`：**TEST SUCCEEDED**（iPhone 17 Simulator）

更新时间: 2026-09-23 11:50
