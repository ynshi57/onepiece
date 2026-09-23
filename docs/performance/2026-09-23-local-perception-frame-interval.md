# 本地感知帧率拆分（罗根）

- 日期：2026-09-23
- 关联：`docs/tech-radar/2026-09-23-indoor-ood-competitors-realtime.md`、咖啡店实测

## 变更

| 常量 | 值 | 用途 |
| --- | --- | --- |
| `minLocalPerceptionInterval` | **0.20 s**（~5 Hz 目标） | TwinLite/YOLO 叠层 |
| `minRemoteUploadInterval` | 2.0 s | Qwen JPEG 上送节奏（别名 `minFrameInterval`） |

感知在独立 queue 异步跑；`encodesJPEGForRemote=false` 时不 JPEG。预览不再被 2s 同步前向拖成「画面不动」。

## 验证

- 真机更新 Hz / 卡顿：待测
- 编译：本轮 xcodebuild

更新时间: 2026-09-23 12:20
