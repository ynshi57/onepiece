# SuperPoint + LightGlue：训练地图覆盖实验

本轮目标是补真实图像约束的地图覆盖，不是美化匹配图，也不是宣告恢复房间或墙体。

## 来源和使用边界

- [官方 LightGlue](https://github.com/cvg/LightGlue)：固定源码 `eb42fee2d71449efb0aa5c10549752b5d75384d8`，逐源文件 Git blob / SHA256 验证。LightGlue Apache-2.0。
- [官方 ETH-CVG 模型](https://huggingface.co/ETH-CVG/lightglue_superpoint)：revision `5f5f626efee99f37dbc9eafea879d24d297aeab8`；safetensors 55,016,112 bytes，SHA256 `320864fabf972cd00ce314e889cf89039e44c238e08d74d2b42d9cf2e80fc867`。通过镜像传输，仍使用固定官方版本和摘要。
- 使用官方 Transformers 转换映射反向转换到固定原源码，所有学习参数 strict load；24 个 SuperPoint 参数与 GitHub 下载权重逐 tensor 完全相等。仅 confidence thresholds 为源码确定性派生 buffer。
- [SuperPoint 原许可](https://github.com/magicleap/SuperGluePretrainedNetwork/blob/master/LICENSE)具有非商业内部研究限制。本实验隔离 `/tmp`，不能随产品分发；不能将整套栈标成 Apache。

## 冻结实验

75 张训练图；13 张固定开发验证图完全不进入重建。SuperPoint 缩放最大边640、最多1024 keypoints，原生中心坐标采用 `(p+.5)/scale-.5`，COLMAP keypoints 和 K 主点同时 +.5。LightGlue CPU 两线程，官方源码、固定权重，无 CUDA，无生产安装改动。

完整2775训练配对，3px RANSAC，COLMAP初始化16度/100内点保持默认。零特征、零匹配、未注册全部保留；数据库不伪造 SIFT descriptor，75图/2775匹配/2775两视图几何/0descriptor rows。旧SIFT与SP keypoint身份不能混用。

| 同预算结果 | SP + LG | 源码 SIFT |
| --- | --- | --- |
| ≥15几何内点配对 | 577 | 273 |
| 原始注册 | 56/75，单分量 | 47/75，两分量19+28 |
| 重建点 | 3206 | 921 + 1170 |
| 训练重投影均值 | 1.383px | .599 / .910px |

SP真正连接172、173到同一分量。SIFT第二分量对AR方向最大差98.75度，原始47不能视为可靠47；此前可靠筛选45属于另外结果，不能偷换。

全部注册中心Umeyama审计SP方向中位.928度/最大3.612度，中心残差中位.0152/最大.0629 AR map units。这是传感器先验一致性，不是独立精度。

训练AR对齐进一步使用方向medoid共识5度，并固定中心差最大.15 AR map unit实验门：56保留、19未注册。门未校准，只用于实验筛查；13开发图不参与拟合，原AR回退明确标注未验证。稀疏seed仅输出至少2个不同保留训练视图观测的3206点，不生成未观测墙。

## 查询协议和限制

`deploy/spatial/research/learned_map_query.py` 对固定地图使用相同SP抽取和LG匹配。完整3D track及训练8px邻域union分组，组按固定ID奇偶拆fit/scoring；重复同帧不多投票、冲突身份不多数表决掩盖。固定12 fit内点/3px；评分排除与定位query像素64px邻近，每group只算一次，至少12可评分/.8通过才列开发一致性候选。所有失败仍输出。

LightGlue attention利用整个特征集合，故上述空间与轨迹拆分不能宣称完整模型计算独立。SP没有SIFT scale，因此64px是保守实验屏蔽值，并非已校准感受野证明。13图已反复用于路线开发，不是最终holdout；PnP相机是推断，不是独立真值。训练地图文件SHA前后验证不变。

最窄验证：`python -m pytest server-vqa/tests/test_spatial_learned_feature_pairs.py server-vqa/tests/test_spatial_learned_map_query.py -q`。数学相似变换、90度错误注册、零baseline、关联身份冲突均覆盖；真实地图精度仍须后续墙/全RGB对照及看图。

实际产物：`/tmp/vqasee-lightglue-pairs-2775/map_superpoint_lightglue`；对齐/seed/开发查询证据：`/tmp/vqasee-lightglue-aligned-query`。研究缓存路径不可作为产品资源。

## 开发查询失败及全样本归因

13查询×56注册训练图，728次LG匹配全部保留，185.47秒。3 solver失败、5定位内点不足、5评分冲突，**0可靠候选**。原图叠图 `/tmp/vqasee-lightglue-aligned-query/development-query-montage.png` 已实际查看：明显错误向量，支持主要在家具/门框/屏幕，不代表稳定墙或房间identity。

新增 `learned_query_diagnosis.py` 不修改地图，保留17228 raw match votes、1525无歧义关联和1884跨点身份冲突query特征：

- 原AR pose：关联19/1525在3px内，所有关联分母含36负深度；有限投影误差median31.14px。
- 推断PnP：327/1525在3px内，含42无pose/30负深度；有限误差median8.645px。不是仅通过点统计。
- 663/1525仅单训练视图支持。≥4视图支持的348关联有限误差median2.803px，但仍只181个通过3px；不得依照此开发图数字调门宣称成功。
- 训练数学自一致性：12572 track投影median1.446/p953.063px；native archive 与 COLMAP stored-.5 最大差0。这排除明显像素半像素/convention错误，**不是独立产品精度**。
- 8px训练union后3206组均singleton，SP没有SIFT重复orientation；不是分组吞并多个3D身份造成错误。fit/scoring group交集为空，但模型attention仍关联。
- 154/231点集偏近平面（协方差最小/最大约.005/.011），203仅1点；其他多帧空间跨度足够仍大量错配，不能把一切归于几何退化。

官方 [HLoc localize_sfm.py](https://github.com/cvg/Hierarchical-Localization/blob/master/hloc/localize_sfm.py) 基于retrieval/covisibility的局部候选PnP，再按定位内点选候选；我们的对全部56训练视图匹配且要求全局身份一致可能让远视图错配否定真匹配。可以试训练地图预冻结局部covisibility邻域、仅fit组选择PnP、全部评分失败仍保留。官方示例默认12px及无结果回退最近相机不适用本产品，必须保留3px、显式失败，不能复制其宽阈值/最近相机回退。

## 冻结局部匹配控制（仍失败）

`learned_query_clusters.py` 复用728份匹配，训练track共视图冻结44个8-view邻域，仅even fit组定位内点最多、再fit中位误差最小选候选；odd评分不参与选择。3px/12inliers/12可评分/.8/64px门未变，不采用最近相机回退。13全保留，10.05秒：1弱、7评分冲突、4评分支持不足、1solver失败，**仍0可靠候选**。175/189/217/224 fit内点升至82/116/62/74，说明全局冲突匹配抑制真fit；但仅fit改善不能称查询相机可靠，也不能采用这些相机当评测真值。完整产物 `/tmp/vqasee-lightglue-query-clusters`；所有候选失败都保留在JSON，不能只选择最高fit并隐藏其他假设。
