# ESPNet-C 室内复用：架构候选，非现成室内能力

来源：TwinLiteNet 官方代码 https://github.com/chequanghuy/TwinLiteNet/blob/main/model/TwinLite.py 、官方训练说明 https://github.com/chequanghuy/TwinLiteNet 、ESPNet 官方项目 https://github.com/sacmehta/ESPNet 。可信度高；采纳级别 L1，待 L2 实验。已核对本机缓存架构与 deploy/ios/convert_twinlite_coreml.py，未运行室内评测。

ESPNet-C 在 TwinLite 中输出低分辨率特征，随后位置/通道注意力和两套解码头输出可行驶区域、车道二分类。App 转换封装仅导出这两个任务输出，未导出通用室内语义或实例掩码。BDD100K 权重的类别和训练域不能直接解释为地板、桌面、杯子。

可复用架构实现、Core ML 部署经验和帧处理；室内需要对应数据、任务头及训练。共用代码、共用结构、共用同一组权重是三个不同决策。先独立室内权重验证，再决定是否联合训练共享权重。原户外注意力与输出可保留；注意力也可用于室内，应通过消融决定而非按场景禁止。

语义分割可区分杯子类和桌子类，不能天然区分同类两个杯子或确认跨帧身份。物品记忆仍需实例/目标分割、匹配、空间对齐与遮挡推理。

最小实验：定义地板、墙、桌面、家具等室内结构任务，选标签匹配的公开室内数据与独立家庭/书店/咖啡店留出集；训练 ESPNet-C+室内解码头，与至少一个轻量室内基线同分辨率比较。报告逐类 IoU、边界质量、小目标失败、实图叠层和 iPhone p50/p95 延迟；物品任务另测实例边界与相同物品匹配，不能用场景 mIoU 代替。

角色判断：乔布斯有条件同意，以实际能力选择室内产品；全麦要求室内标签和留出评测，不把道路权重当室内模型；罗根先共用推理基础设施，联合模型必须测内存/延迟及户外回归；思余要求真实掩码可视化，错误区域可见；小马继续比较轻量室内编码器。以上是本次分析视角，未声称独立子代理评审或实验通过。

产品候选：室内空间结构、摆放区域变化与通行空间变化；精确杯子记忆不能只凭语义分割确定可行。无生产代码、默认模型或依赖变更，不需安装面同步。本次仅文档核查，不新增功能测试。

## 公开权重检索补充

2026-10-08 检查 ESPNet 官方 pretrained、ESPNetv2 和后继 EdgeNets：ESPNet 官方明确 Cityscapes 权重；EdgeNets 展示 Cityscapes/PASCAL VOC 分割模型。未核实到 ESPNet/ESPNet-C 在 NYUv2 或 SUNRGB-D 的可直接使用公开权重，不能据此断言从未有人训练。VOC 含室内常见物体但不是完整室内空间标签体系。

可用对照候选：NVIDIA SegFormer-B0 ADE20K 模型 https://huggingface.co/nvidia/segformer-b0-finetuned-ade-512-512 （RGB，ADE20K 混合室内外）；TUI-NICR ESANet https://github.com/TUI-NICR/ESANet （RGB-D，NYUv2/SUNRGB-D 权重入口）；EMSANet https://github.com/TUI-NICR/EMSANet （室内多任务含实例）。ESANet 名称相似但不是 ESPNet，权重不能装入 ESPNet-C。仅确认模型页和下载入口，尚未下载、校验权重或验证 iPhone 转换与性能。
