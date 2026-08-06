# VQASee Road Boundary / Local Perception 开发测试计划

Date: 2026-08-06

## 乔布斯方向

VQASee 已从“低视力专用视觉辅助”升级为面向行人、骑行者、驾驶者和注意力可能分散场景的视觉风险辅助产品。下一阶段不能停留在 UI 假线，而要验证真实感知能力：本地 YOLO、道路边界、通行/行驶辅助走廊、深度/落差。

原则：

- 可以提示“疑似边界 / 可能有车辆 / 请放慢”；
- 不说“可以走 / 可以开 / 前方安全”；
- 先验证能力，再决定是否上线；
- 录屏、截图、延迟和误报都要沉淀。

## 当前已具备

- `YOLO11nObject.mlmodelc` 已加入 iOS App bundle。
- `LocalPerceptionSignal` 已支持对象、道路线索、深度线索。
- Camera overlay 已支持框、标签、辅助走廊、边界线、人行横道线、cue chips。
- iOS 单元测试通过。

## P0 任务：YOLO11nObject 真机验证

任务：验证本地 YOLO 在真机上能否识别人/车/自行车等目标并显示到画面。

主责：罗根 + 全麦  
配合：思余

测试场景：

- 人：左 / 中 / 右 / 远 / 近；
- 车：停放车辆 / 缓慢移动车辆；
- 自行车 / 电动车；
- 公交车 / 卡车；
- 狗 / 动物；
- 交通灯 / 标志牌。

验收标准：

- 画面出现框和标签；
- 框大致贴合目标；
- 方向 left / center / right 基本正确；
- App 不崩溃、不明显卡顿；
- 本地语音能提示“可能有车辆/人，我正在确认”。

记录指标：

- 机型；
- 场景；
- 是否出现框；
- 是否对齐；
- 误报/漏报；
- 延迟体感；
- 发热/耗电体感；
- 截图或录屏。

## P0 任务：Overlay 对齐测试

任务：确认 detection box / 边界线 / 辅助走廊与摄像头 preview 是否对齐。

主责：罗根  
配合：思余

测试步骤：

1. 人站在画面左侧、正中、右侧。
2. 缓慢移动手机，观察框是否跟随目标。
3. 对准车、自行车、标志牌。
4. 切换走路/看周围模式。

验收标准：

- 框不明显偏移；
- 辅助走廊位于画面中心；
- 目标移动时框不剧烈跳动；
- 方向判断与肉眼观察一致。

风险：

- SwiftUI overlay 和 `AVCaptureVideoPreviewLayer.resizeAspectFill` 存在 crop/scale 差异，可能需要专门的坐标映射函数。

## P0 任务：语音即时反馈测试

任务：确认本地感知不等 Qwen，也能先给短语音提示。

主责：思余 + 罗根  
配合：全麦

测试场景：

- 正前方有人；
- 正前方车辆；
- 镜头遮挡；
- 画面过暗；
- 画面明显变化。

验收标准：

- 能听到短语音提示；
- 不重复到烦；
- 不说“可以走/可以开”；
- Qwen 返回后可以补充解释，但不阻塞第一声提醒。

## P1 任务：Road Boundary Prototype on Mac

任务：验证真实道路边界模型，不再依赖 UI 假线。

主责：全麦  
配合：罗根 / 思余

候选模型：

- YOLOPv2；
- HybridNets；
- 语义分割模型；
- 后续可基于 Mapillary / BDD100K / Cityscapes 微调。

目标输出：

```json
{
  "frame_id": "...",
  "coordinate_space": "normalized_image",
  "road_boundary": {
    "left_polyline": [[0.1, 0.9], [0.3, 0.4]],
    "right_polyline": [[0.9, 0.9], [0.7, 0.4]],
    "confidence": 0.72
  },
  "crosswalk": {
    "polygons": [],
    "confidence": 0.61
  },
  "lane_markings": [],
  "guidance_corridor": {
    "centerline": [[0.5, 0.9], [0.5, 0.4]],
    "status": "caution"
  }
}
```

验收标准：

- 输入录制视频帧；
- 输出 mask / polyline / corridor；
- 能保存 overlay 图片；
- 人工评估是否贴合真实边界。

不做：

- 不直接上线；
- 不说“可行驶”；
- 不给驾驶控制建议。

## P1 任务：Depth / LiDAR Prototype

任务：验证台阶、坑洞、路沿、落差能否通过深度判断。

主责：罗根 + 全麦

候选：

- ARKit LiDAR depth；
- Depth Anything V2 Small / Metric Outdoor。

验收标准：

- 近处落差能标成风险区域；
- 不把普通地面纹理频繁误报为坑洞；
- 输出能融合到 `LocalDepthCueSignal`。

## 联调路径

```text
iPhone camera
→ LocalPerception / YOLO11nObject
→ CameraRiskOverlay
→ VoiceFeedbackPolicy
→ backend Qwen / future road boundary service
→ UI/speech/haptic
```

未来 road-boundary Mac prototype：

```text
recorded frames
→ road_boundary_service.py
→ mask/polyline/corridor JSON
→ overlay renderer
→ eval screenshots
```

## 真机反馈模板

```text
模式：走路 / 骑行 / 驾驶提醒
场景：室内 / 街道 / 路口 / 停车场
机型：
目标：人 / 车 / 自行车 / 路沿 / 人行横道 / 台阶 / 坑洞
现象：
- 框是否出现：
- 框是否对齐：
- 语音是否及时：
- 延迟：
- 误报/漏报：
- 是否卡顿/发热：
截图或录屏：
```

## 下一步执行顺序

1. 真机跑 YOLO11nObject：人/车/自行车。
2. 修 overlay 坐标偏差。
3. 验证语音即时反馈。
4. 开始 Mac road-boundary prototype。
5. 开始 Depth/LiDAR prototype。
