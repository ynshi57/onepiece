# 小马：物品变化轮廓的可立即实现方案

日期：2026-10-07。结论：有条件同意采用 Apple Vision 分割、轮廓检测和图像配准，先把真实轮廓叠加到用户查看的图片；不再把外观分类校准作为展示轮廓的前置条件。配准与分割不是身份或消失判定。

## 已核实的一手来源

- [Apple：Aligning Similar Images](https://developer.apple.com/documentation/vision/aligning-similar-images)：配准用于同场景、相近视角，画面内容越相似越有利；不承诺任意三维视角的对齐。
- [Apple：VNImageHomographicAlignmentObservation](https://developer.apple.com/documentation/vision/vnimagehomographicalignmentobservation)：warpTransform 是 floating image 到 reference image 的透视变换。
- [Apple：VNGenerateForegroundInstanceMaskRequest](https://developer.apple.com/documentation/vision/vngenerateforegroundinstancemaskrequest)：得到显著前景实例，不是任意类别物品检测器。
- [Apple：generateScaledMaskForImage](https://developer.apple.com/documentation/vision/vninstancemaskobservation/generatescaledmaskforimage(forinstances:from:))：选择实例后生成与源图对应的高分辨率 mask。
- [Apple：VNDetectContoursRequest](https://developer.apple.com/documentation/vision/vndetectcontoursrequest)：检测图像边缘轮廓；适合先输入二值实例 mask，避免杯子纹理和桌面边缘混入目标外轮廓。

可信度高，API 已可使用；本产品组合方案为 L2 最小实验，不因 API 成熟就宣称场景已验收。

## 代码事实与缺口

`IndoorTargetEvidence.maskPNG` 已保存全图实例 mask、归一化 bounds、原图尺寸。`IndoorTargetVision.extract` 已支持点击处实例分割。`checkSelectedObject` 已采集当前图、以同一 ARFrame 的 anchor 投影选点；其输出仍主要走未校准身份评估文案，因此用户看不到已经取得的几何证据。

无需先训练新模型即可把 mask 变成轮廓绘制，但必须分离“几何证据展示”和“身份/消失结论”。原有特征评估不可作为整张变化画面的 gate。分割应能独立返回 mask：特征提取失败不能把已成功的轮廓吞掉。

## 本轮最小完整接口

1. 冻结旧图、旧 mask、当前图、拍摄时间、所选 record ID 和 session ID，后台串行处理。
2. 二值 mask 检测轮廓，返回归一化 top-left 的 polyline/path。Vision path 使用不同原点时显式翻转一次；aspect-fit 的图片与轮廓必须共享同一矩形。
3. 旧图为 floating、当前图为 reference，估算配准；把旧轮廓变换到当前图片，显示带标签的虚线“记住时”。当前分割轮廓显示实线“当前轮廓”。无需宣称两个轮廓是同一物品。
4. 用户点选当前图片重新分割；选点也是有价值的显式输入，不伪装成自动识别。原位置分割失败时仍显示旧参考轮廓及未取得当前轮廓状态，不能画假当前轮廓。
5. 配准失败时，旧图上继续显示旧轮廓、当前图上显示当前轮廓，明确“视角差异较大，分别显示”；不能把无效旧轮廓贴到当前图。恢复入口是回到相近角度或点选当前目标。
6. 仅对通过配准质量门的几何轮廓计算 IoU、中心偏移、面积变化、对称差异区域；标为轮廓差异，不能直接写“物品移走”。旋转、透视、遮挡、分割抖动均会产生差异。

## 系统、模型审查：配准必须拒绝哪些结果

以下是本项目实验检查，不是 Apple 保证或已实测校准阈值：

- 矩阵全部有限、可逆，齐次分母在图角和目标轮廓点上不接近零；透视后不翻转，不出现自交四边形。
- 变换后目标/图片保有足够交叠；面积比例、尺度、位移不离谱。拒绝参数需记录而非偷偷放宽。
- 排除旧目标膨胀区域与当前候选区域，再比较背景。不能用目标 ROI 的匹配程度验证配准，否则物品变化本身会把配准带偏。
- 至少检查多块背景区域的一致性，不能让一块重复纹理、单一平面或目标主导全局。背景残差在配准后应比配准前改善；曝光变化可做亮度归一化，但不得把所有差异抹掉。
- 二次正反向配准的回环偏差和独立背景残差可用于拒绝错误矩阵。若本轮只做平移模型，应明确仅允许近似同视角；大视角不升级成任意 homography 而不验证。
- 大幅移动产生视差：桌面、杯子不同深度，一个全局 homography 无法全对齐。单点 AR anchor 只能确定位置，不能推导完整三维杯子轮廓。正确产品状态是分别显示旧/当前轮廓，直到有轮廓深度或多视角几何。
- 特征距离未校准不能充当身份分数；点击到手/瓶子时，该轮廓仍是当前选中候选，不得标成同一杯子。
- 结果绑定冻结图片，不绑定继续移动的实时画面；回调检查 record/session/frame ID，切换、取消或超时后禁止覆盖新图。
- 图像编码、分割、配准、轮廓提取分别记录耗时；单个 in-flight、防重复点击排队，超过预算给可见恢复路径。

## 分角色任务与验收

- 乔布斯：用户第一次打开变化页就能看到参考轮廓；取得当前图后能看到当前轮廓或可点击恢复入口。不能只有“无法判断”一段文字。确认几何差异与自动事件判断的能力边界。
- 罗根：固定坐标、矩阵方向、图像版本和回调生命周期。交付已知平移/缩放图的定位验证、失败恢复和真实耗时。
- 思余：虚线/实线同时带文字标识，不能只靠颜色；图片、轮廓共享缩放裁切；配准失败时仍有轮廓而非空白。
- 全麦：分割与特征解耦；记录错误候选、无前景、相似替换、遮挡样本。轮廓展示不依赖身份阈值，事件分类仍需独立数据与评测。

最小实验：杯子不动仅手机平移；杯子移动；手遮挡；杯子移走；相似杯替换；大视角；透明杯；配准失败。每个实验查看最终图片并保留截图，量测轮廓边界误差/配准残差/失败可见率/端到端耗时。合成几何验证只能说明坐标链正确，不代替真机分割验收。

成功标准：有真实 mask 就能看见真实轮廓；变化区域来自通过质量门的对齐 mask；失败保留参考且能恢复；没有伪造身份或移走结论。真机场景未测时只能称代码和几何链已验证。

## 候选与知识沉淀

立即采用候选：现有 Vision mask + 二值轮廓 + 相近视角配准 + 用户点选恢复。待验证：多块背景质量门、轮廓深度与多视角重建。暂不采用：为展示基本轮廓立即接入 SAM/DINO 远端服务；它会增加模型和网络依赖，不能替代坐标与渲染缺口。

本次认知沉淀本技术卡；现有 AGENTS 已有看图验收和不隐藏失败规则，无需新增重复长期规则。下一次雷达主题：遮挡下的可见轮廓、多视角实例对应与端侧交互式分割。
