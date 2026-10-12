# SegFormer B2 室内语义对照：实现一致，但墙体归因仍失败

小马主责；产品目标是防止画架/纸箱/椅边被当成房间墙体。实验只改变隔离研究模型，不改iOS、旧脚本、生产安装面或既有几何门。

## 官方来源与可复现性

- [NVIDIA B2 ADE512 权重](https://huggingface.co/nvidia/segformer-b2-finetuned-ade-512-512)，固定revision `de01bae28967510f9ddd496c60a969357195400c`，官方LFS `pytorch_model.bin` 110,000,905 bytes，SHA256 `187ca07bea003a5717c63d04ea90b07f33cd033c0ebf44b4b89fce5070d6c8f3`。经镜像直连curl分块下载，全文件摘要通过才加载；config/preprocessor各自Git blob校验。
- [官方 SegFormer](https://github.com/NVlabs/SegFormer)，B2作为较大骨干的同ADE20K控制，保持同512输入和同150类。没有声称实测数据集mIoU。
- [NVIDIA许可](https://github.com/NVlabs/SegFormer/blob/master/LICENSE)限制非商业研究/评估；隔离 `/tmp`，不得作为产品分发许可。
- [Transformers v4.44.2官方后处理](https://github.com/huggingface/transformers/blob/v4.44.2/src/transformers/models/segformer/image_processing_segformer.py)：bilinear logits上采样、align_corners=False，然后argmax。RGB归一化使用官方processor；reduce_labels只作用标注图，不是RGB或预测类别整体再减一。

## 实现核验：未发现足以解释错误的坐标或预处理bug

已有 `segformer_masks.py` 保持只读。真实JPEG 1920×1440无EXIF旋转；AR世界down投影native+x，turn3顺时针转到画面down符合相机OpenGL→图像y轴符号；反转90度是精确像素置换，native dimensions保持。

新 `semantic_model_control.py` 在CPU1线程重跑原B0六张问题训练图185/187/188/226/230/158，输出对旧B0共16,588,800像素：仅3个label像素不同，confidence最大差3.19e-6，.5门仅1像素不同。输入official/manual最大差2.38e-7。这些近tie数值差不能解释家具/画架大面积wall误判。

新脚本按class分块插值到native，流式softmax归一化避免一次构建150×native logits/probs占用数GB。CPU专用，不竞争Brush GPU。随机logits官方后处理对照、tie最先类、四方向label/float confidence精确回转、重力不可观测、非有限logits显式失败7项测试通过。GPU或NaN logits不会静默发布类0。

## 完整真实对照

75训练图完整保留，用时625.54s，CPU inference median7.51/p958.06s。13反复使用的开发图另目录，完整保留，用时109.60s，median7.64/p957.86s；与训练无交集。这13图不是最终holdout。每图labels/score/panel与status均存在，没有删失败样本。

固定同一56注册SP/LG地图、同3视图一致/.5研究门，将75训练B2 masks映射到3206地标：

- wall标签候选由B0的120变为190；原120仅99继续wall，21未知，新增91。
- box候选由2变为129，说明部分箱体类别有所改善。
- **数字增加不是墙能力提高。** 已实际查看 `/tmp/vqasee-lightglue-wall-overlay-b2-same-six.png`：185 wall点27→48、18721→38、18819→41、15819→47，多在画架木杆、后板和家具边；椅边误判仍在。
- `/tmp/vqasee-segformer-B0-B2-six-comparison.png` 同六图颜色对照也显示画架仍被wall填充。置信度降低不确定比例，不等于真实类别正确。

语义模型输出stride4，在512输入得到128×128 logits，1920 native宽度每logit约15px。薄杆/物体边界的稀疏特征可以落在被背景wall覆盖的预测区域，descriptor也会混合不同物面。ADE150没有单独easel类别，模型不能据wall标签证明后板附着房屋。此处是类别/尺度/物理归属问题，而非已发现的软件预处理bug。

不能把模型mask、≥3语义一致或高model score视为物理测量；也不能只排除这些错误点、隐藏被排除的证据后称墙恢复。下一几何实验必须保留全部地标及未知/家具风险，依靠冻结多视图投影和实体表面支持确认墙归属，空白墙没有纹理点时不能凭语义填出测量墙。

## 产物与验证

- 模型 `/tmp/vqasee-segformer-b2`，固定官方metadata与摘要均记录。
- 全训练 `/tmp/vqasee-segformer-b2-training`，13开发 `/tmp/vqasee-segformer-b2-development`。
- 同地图语义结果 `/tmp/vqasee-lightglue-semantic-landmarks-b2`，旧120的逐ID变化保留于B0-B2-point-comparison.json。
- B0实现对照 `/tmp/vqasee-segformer-b0-audit-control/existing-B0-comparison.json`。
- 最窄测试 `python -m pytest server-vqa/tests/test_spatial_semantic_model_control.py -q`：7 passed。

没有人工逐像素标注，不报告IoU；看图是失败审查，不是墙精度真值。B尚未通过。本次没有新增生产依赖、默认值、协议或资源位置，安装面不变。
