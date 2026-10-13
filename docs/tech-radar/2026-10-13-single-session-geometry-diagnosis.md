# 单会话几何：冻结轨迹的嵌套残差竞争实验

2026-10-13，小马只读审查。支持先验证已有坐标、K、时间和姿态链，再修算法；反对先换模型或用训练拟合下降宣称根因。此轮只制定实验，不新增运行结果。

## 已有输入与实现缺口

原始书房 `/tmp/vqasee-spatial-browser/fa32c81d-1fe0-4b2d-92d0-a36bd603f102/document.json`：239关键帧，1798高频pose；实验仍用既定75训练/13开发分区，分区从 `/tmp/vqasee-lightglue-aligned-query/alignment.json`读取并校验，不按注册成功重新选择分母。`/tmp/vqasee-lightglue-pairs-2775/map_superpoint_lightglue/models/0` 是56注册训练视图单分量；其原特征与2775匹配在同父目录 features/matches，固定特征身份。`/tmp/vqasee-lightglue-aligned-query/document.json`及 alignment.json 保存训练限定的 AR 对齐；原始 K/c2w始终从原doc读取。

track_residual_diagnostics.py 做 frozen点的 row/radius 线性残差趋势并留track组验证，但点来自调用方、可能已用被评图三角化。row/radius趋势不是rolling shutter或畸变归因。temporal_pose_correction.py 插值训练锚点的SE3修正：有分量边界与外推标记，但不是时间偏移估计，也不证明地图连通。resect_training_views.py 硬编码0/1 SIFT分量且使用原SIFT身份；不能直接复用为LG单分量查询，须先参数化/map identity核对，不能混入SP descriptor。

Controller.captureKeyframe 把JPEG源buffer、K、c2w从同一个ARFrame在异步编码前捕获；当前没有“编码延迟使pose对应错图”的源码证据。1798高频poses仅position/forward/time/epoch，缺roll；严禁把它们重建的近似旋转当完整姿态作精确时差标定。旧239图也没有完整曝光/读取时长信息，rolling shutter不可直接确认。

## 一个最小实验：相同冻结轨迹上的嵌套竞争

无需重跑抽特征/匹配，也不调整3px几何门。先SHA冻结上述doc/map/features与分区。每个75训练视图都是一个目标，56注册与19未注册均保留；13开发图不参与拟合、超参数或假设挑选。

1. 数学自一致基线：从磁盘模型取stored points2D和同身份points3D，用模型PINHOLE K/W2C复投影，确认自实现与COLMAP投影一致；native与COLMAP转换要同时对K主点、点坐标减/加.5，c2w列主序、AR/OpenGL到CV轴转换明确。同一个模型的残差只是实现自检，不是相机准确度。
2. 对每个目标删除其2D观测，使用其他训练视图同track、固定SfM相机重三角化（至少两个不同训练视角）。整体相机BA过去用过目标图，因此这是“去目标观测控制”，不是严格leave-image-out SfM。缺基线/负深度/无track所有失败保留；不能只用较好点过滤后报改善。分组须整条track与近邻patch union，固定训练来源分组，禁止同一物点跨fit/score。
3. 一组完全相同的冻结评分观测竞争四个解释：原AR经训练限定Sim3对齐；全局低自由度K纠偏（fx/fy/cx/cy，固定坐标）；全局时间偏移控制；每图6DoF姿态纠偏。拟合观测取完整组固定一半，评分为另一半并排除定位patch重叠。K/时差在预定训练时间块上拟合，其他时间块验证，局部pose只能用各图fit组，不用score更新地图或pose。再交叉K+pose检查参数耦合。每种解释参数数、优化失败、SVD条件/覆盖、时间全保存。

时差只扫描预声明 ±0.1/±0.2秒，使用同epoch相邻完整关键帧旋转SO3/平移插值，禁止缺roll的高频pose补成GT；跨epoch/边界无覆盖记不可测。该粗控制足以反驳“大时间错位解释”，不足确认细小真实sensor offset。不得把每图任意时差拟合成姿态自由度替代物。

## 完整分母与判别门

每个75训练帧×每个解释均有record。输出源tracks数、所有观测数、可三角化数、定位fit/独立score数、画外/负深度/歧义/退化/求解失败数。各解释改善必须在同一可评分身份交集成对报告，并另列完整失败分母；不能跨方法用不同成功点均值比较。看图固定全75缩略图及残差箭头，不能选最好视图展示。

坐标错误：若相同stored模型投影无法复现COLMAP，先修转换，不准继续物理归因；若复现，则该路径转换错误被否证，但不证明原采集K正确。

标定：共享K纠偏需在未用于拟合的时间块及不同画面区域一致改善、参数不在边界、仍有足够视角；只训练下降或与pose强耦合则不支持标定根因。

时序：一个全局偏移需跨时间块同方向有效、与运动快慢相关且优于零偏移；不同帧需要不同偏移、静止也误差大或粗插值不可观测，则不支持单一时差解释。此数据缺完整高频旋转，细小时差保留缺证。

局部姿态漂移：在几何充分帧，6DoF fit对独立组稳定改善、修正沿时间平滑，而K/时差解释不能解释时，支持局部pose不一致；SfM点和相机仍非外部GT，不报告厘米漂移。

匹配退化：fit好但score冲突、空间覆盖不足/track重复/近平面条件差、多解敏感或多数帧不可定位，支持匹配/可观测性不足。不能删这类失败后宣称姿态已修。上述解释可能共存；未出现可分辨结果就报告不可识别，而不是挑最小训练残差。

## 一手理论

[OpenCV calib3d](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html)定义p=K[R|t]P、尺寸变化需同步K，并说明畸变校准的可逆性要求。[COLMAP camera models](https://colmap.github.io/cameras.html)提示复杂模型会过拟合、简单模型可能欠拟合，支持从最小K控制起而非任意畸变参数。

[ETH Kalibr官方](https://github.com/ethz-asl/kalibr/wiki/Camera-IMU-calibration)采用连续时间轨迹联合空间/时间校准，要求足够运动激励与同钟时间、原始IMU。它说明“时差可估”需要观测，不支持拿缺roll的ARpose替代真实IMU标定；当前借鉴实验原理，不安装Kalibr、不改生产依赖。

## 执行接口与本轮边界

实现输出建议 `frozen-input-receipt.json`、`projection-selfcheck.json`、`all-frame-controls.json`、`all-observations.jsonl`、全帧残差图；productAccepted=false。主代理可在现有诊断上补 controls，不替换匹配器；先过数学自检再运行竞争。此实验服务A单会话/B-C共同相机依赖，不评价跨次房间identity，不推导A恢复成功。仅此文档新增；其他研究代码、iOS与渲染未改，实验尚未执行。
