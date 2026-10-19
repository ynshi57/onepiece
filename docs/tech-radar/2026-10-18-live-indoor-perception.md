# 实时室内识别的产品缺口（L2）

用户反馈：目前主要看到轨迹和图片映射，物体识别与实时矢量建图缺失。代码核对属实：原识别仅处理停扫后的JPEG，实时AR预览未消费检测框。

一手来源：[Apple实时物体识别](https://developer.apple.com/documentation/vision/recognizing-objects-in-live-capture)、[ARKit物体标签示例](https://developer.apple.com/documentation/arkit/recognizing-and-labeling-arbitrary-objects)、[RoomPlan研究](https://machinelearning.apple.com/research/roomplan)。实时二维框可直接接入Vision/CoreML；识别与三维定位不能互相替代。RoomPlan的参数化结构/物体融合值得参考，但其传感器与类别前提不能冒充现有RGB路线已具备。

乔布斯：先部署已验证修复，再交付扫描时可见的类别框；诊断入口不算功能。全麦：直接像素buffer、模型常驻，单在途，不排无限任务。罗根：保留全部接收/提交/忙时未分析/失败/旧代丢弃计数和耗时，真机未测不宣称实时。思余：按实际相机viewport映射，大小窗交换重算，过期明确提示；二维框不成为物体地图。

最小实验：扫描→实时框→旋转/双窗交换→停止/重开，核对框方向、边缘、旧代污染及延迟。失败可见，不降低门槛。后续物体地图仍需前景归属、跨视图一致性及真实位置真值。当前设备不可达，实景验收不能被模拟器/构建替代。
