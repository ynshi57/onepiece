# 小马技术情报：室内记忆的原生能力路线

日期：2026-10-04。类型：Apple 官方文档与当前代码核对。来源可信度高，产品适配性尚待真机实验。

## 当前能力与候选

- 已使用：ARKit世界跟踪、水平面 raycast、anchor 和屏幕投影。代码没有调用室内 VLM，没有生成完整房间模型。Codex 是开发工具，不是此模式运行时模型。
- 下一候选：ARWorldMap 保存空间与恢复锚点。采纳级别 L2 待实验，不把“加载成功”当“重定位成功”。
- 后续候选：Vision图像特征比较，建立手动框选、多视角物品匹配基线。L2待实验，不把类别识别当个体身份。
- 暂不引入：生成式世界模型或完整房间重建。目前没有证据证明它们是同会话桌面找回的必要条件。

## 来源

1. [Understanding World Tracking](https://developer.apple.com/documentation/arkit/understanding-world-tracking)：视觉与运动信息共同建立相对空间关系。
2. [Saving and loading world data](https://developer.apple.com/documentation/arkit/saving-and-loading-world-data)：ARWorldMap和恢复/重定位流程。
3. [VNGenerateImageFeaturePrintRequest](https://developer.apple.com/documentation/vision/vngenerateimagefeatureprintrequest)：图像特征提取候选基础能力。

## 角色学习与最小实验

- 乔布斯：把“记位置”“恢复空间”“认同一物品”分成不同验收目标。先完成v0.1实景闭环。
- 罗根：会话坐标有生命周期；跨启动必须重定位验证。对照最新构建测误差和可见延迟。
- 思余：名称/时间必须可读，历史位置与当前观察状态分开；恢复失败仍能看历史照片。
- 全麦：从相似杯子、移走、遮挡的负例构建基线，衡量误认与端上耗时。

失败退出：v0.1位置漂移/响应不达标则先定位系统或跟踪瓶颈，不用识别模型包装；ARWorldMap不能稳定恢复则停留在照片可查并保持“恢复未通过”；特征匹配误认同类物品则拒绝确认，不直接投入生产。

当前没有真机数据支持升级为生产采用。相关任务与门槛见 `docs/evolution/2026-10-04-indoor-memory-plan-and-demo.md`。无需修改团队skill或AGENTS。
