# 无 LiDAR 室内矢量建图：平面生命周期与照片独立核验

小马技术审查，2026-10-12。结论：有条件支持保留原生平面观测并用点、线、面联合约束；反对继续把 ADE 的 wall 标签或更清晰高斯外观当建筑几何。ABC 当前未通过。本文是实现任务卡，不是产品验收。

## 官方 API 与代码事实

[Apple 分类支持说明](https://developer.apple.com/documentation/arkit/arplaneanchor/isclassificationsupported)限定 A12 或更新 GPU，不要求 LiDAR。iPhone 17 仍须记录实际 `ARPlaneAnchor.isClassificationSupported`；支持分类不等于分类正确。

本机 iPhoneOS26.5 SDK ARPlaneAnchor.h 及 Swift overlay 核查：分类 iOS12 起；Swift `classification` 的 `.none(Status)` 区分 notAvailable/undetermined/unknown，wall/floor 等具体类别隐含已分类。不要直接假定 Swift 有 classificationStatus getter。本 SDK 的 ARKit 头文件和 overlay 没有 `subsumedBy`；不要混入 ARCore/Unity 接口。参考 [Apple ARPlaneAnchor](https://developer.apple.com/documentation/arkit/arplaneanchor)。

SpatialScanController.swift 646–694 的 add/update 仅写 UUID、横/竖、世界边界、时间和 epoch，更新覆盖旧记录，remove 从 surfaces 删除。SpatialScanModel.swift 的 SpatialSurface 没有分类、状态、平面 transform/center/extent 或历史。这使已有记录无法回答该边界是否一直稳定、曾变成 furniture 类、被移除或跨恢复改变。不能在旧数据中补造缺失字段。

## 必须实现的最窄采集面

主责系统/iOS；模型与产品审查。新增 additive optional `PlaneObservation`：observationID、anchorID、sessionID/epoch、event(add/update/remove)、frameTimestamp、classificationSupported、classification/status、anchorToWorld、center、planeExtent、原生 boundary、关联 keyframeID。以 append-only 历史保留被移除 anchor；current surface 是历史派生状态。旧 schema 缺失明确 unknown。同一帧重复回调不是独立视角。

不提供伪 subsumption。合并只输出 candidate association：同 epoch 下共面、有限边界重叠、多帧照片支持；不把新 UUID 自动视为旧对象，不凭 AR 的正常 tracking 跨 epoch 合并。跨恢复坐标关联必须另有独立照片证据。

影响面必须枚举 Swift Codable/保存校验、导出包、后端 schema/import、研究解析、缓存版本、空间页状态。原始观测与推断结构分别保存，删除事件不能静默丢证据。首个验证是实际 iPhone17 录制包含墙、家具、玻璃、移开纸箱的短片，核对导出字段完整及历史 retained；这是采集验证，不是墙精度验收。

## 最窄几何实验

主责研究算法；先固定已有训练相机和训练图，开发查询不参与建图或门限选择。LSD 的所有原线保留；由跨视角点支持及线描述/外观互检建立有限 3D line tracks，记录所有匹配失败。重力只约束方向，不能固定不存在的墙面或强迫所有角为90度。

联合优化相机/点/线/平面时分别保存原 AR 对照、训练-only 解、观测残差和退化状态。平面归属需要实际多视图几何支持；语义 wall 仅候选，家具冲突保留。相交的两条非平行垂直面产生墙角候选，其有限高度必须来自观测端点，不补天花板/地板高度。门洞需要两侧边、过梁及多个视角可见证据；缺少 mask 或平面不能推导空洞，ARPlaneGeometry 也不是门洞真值。

冻结后投影至全部开发视角，同一像素阈值、完整样本分母与失败原因。记录可观察视角/基线、遮挡、边缘复投影、时间/内存；家具边、反射、重复书架为必要负例。单线通过、训练残差低、角度接近90°均不算完整墙或 B 通过。没有独立测量不能报告厘米误差；开发图重复使用不得称最终 holdout。

## 官方成熟路线与取舍

[LIMAP](https://github.com/cvg/limap)，CVG/ETH，BSD-3-Clause：官方支持已有相机的结构映射、点线定位及联合 SfM。源码路线能吸收有限线段多视角三角化及联合优化；它不能修复错误相机或从白墙无纹理生成观测。当前主分支 2.x 与 COLMAP4.2 集成，不能直接升级现有环境；先固定版本及 C++/Ceres/Eigen 依赖，在隔离 CPU 环境接已有点线，不引入 CUDA matcher。研究许可需逐依赖核对，尤其已有 SuperPoint 不可产品分发。

[VP Estimation with Prior Gravity](https://github.com/cvg/VP-Estimation-with-Prior-Gravity)，官方 ICCV2023，MIT：可先用 CPU LSD+重力做方向/退化控制；消失点不给平面距离，不能当矢量地图。

[PL-VIO](https://github.com/HeYijia/PL-VIO)，GPLv3，单目+原始 IMU；需要标定与时间同步，保存的 AR 相机姿态不替代原 IMU，因此不是当前 JPEG 的直接方案。[PL-SLAM](https://github.com/rubengooj/pl-slam)，GPLv3，立体点线系统；现有单 RGB 不满足输入。RoomPlan 依赖 LiDAR，本硬件路线不采用。

## 新场景 A 的完整事实

冻结输入 `/tmp/vqasee-a-photo-frozen/source-receipt.json`：179原照片（157参考、22恢复查询），182文件全SHA；不同房间旧书房22张独立负例不入参考地图。原始 App 文件未改。157参考构图614 pairs，119注册/38未注册，五分量；每个查询固定24参考，同预算全部1056查询匹配，44查询×5分量=220定位尝试，未重跑。

`result.json`：恢复22/22均未通过（110分量尝试82solver失败、16独立评分冲突、12弱支持）；负例22/22均未通过（100solver失败、10弱支持）。0负例误通过仅本次控制，不是普适误接受率。实际耗时1659.799秒，不能移作实时端上能力。

`original-AR-diagnosis.json` 另读冻结模型，不重新建图或匹配；训练-only AR 对齐只是传感器一致性先验。排除定位 patch 重叠后的所有可投影评分：原 AR 2143项 median74.60px/p956382.27px、7项≤3px；PnP1891项 median6.05px/p952842.58px、484项≤3px。两者样本数不同，不能把这个汇总当严格同分母改善量，全部原记录和失败保留。0恢复通过的结论不变。

发现 mapper 返回对象与持久化目录2/4的 metadata关联错误：磁盘真实 counts 为61/19/15/12/12。query实际读磁盘模型，几何结果有效；旧标题元数据不得当分量真源，后续消费必须读磁盘注册身份并生成显式校正 receipt，不静默改原 evidence。COLMAP不能读取这些原JPEG颜色但不影响已导入特征几何；派生训练PNG clone bitexact验证157/157，5模型坐标在重着色前后/落盘后完全不变，receipt位于reference-map-color-clone/receipt.json；它不是原始App出口。

## 角色结论与退出条件

产品：支持先恢复可信结构证据，ABC未过不得宣布完成。系统：必须补采集历史、完整 schema 和端到端导出；历史不能事后恢复。UI：原始/候选/验证失败状态清楚，不滞留旧模型冒充当前。模型：反对仅升级分类器，支持几何观测与家具冲突证据并存。小马：支持隔离点线联合实验，反对硬件不符的 RoomPlan/立体/原IMU替代承诺。

本轮只写技术卡和独立 SDK probe，不改 iOS/API/渲染、不重跑冻结 A。几何能力实现属于本轮目标必要项，由主代理继续；记录完整不等于产品已修好。
