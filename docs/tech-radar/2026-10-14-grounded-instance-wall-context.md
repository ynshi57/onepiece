# 开放词汇物体与实例边界：识别画架冲突，仍不能测出建筑墙

小马主责。承接B0/B2把画架木杆、背板作wall的失败，固定原120/B2的190墙标签地标身份，取union211。使用原六张问题训练图185/187/188/226/230/158；没有使用13开发图调模型或删除候选。

## 固定一手来源与硬件

[官方GroundingDINO](https://github.com/IDEA-Research/GroundingDINO)及[官方HF Tiny](https://huggingface.co/IDEA-Research/grounding-dino-tiny)，revision `a2bb814dd30d776dcf7e30523b00659f4f141c71`，safetensors 689,359,096 bytes，SHA256 `1a2412ef99bd74bcd3c2a246fa1e48581f8889a1300c9051974741314fc042f3`。固定config/tokenizer逐官方Gitblob验证，全模型SHA通过才执行。沿现有隔离Transformers4.44.2，CPU PyTorch deformable attention，disable_custom_kernels=True，不安装CUDA或升级生产venv。

[官方MobileSAM](https://github.com/ChaoningZhang/MobileSAM)固定commit `f706ad9c4eb7f219c00d9050e46328518ffb65d2`；必要源码16文件及权重逐Gitblob校验。40,728,226 bytes权重Gitblob `7ef2d090979fd9853adf17b7a99c8b94a1c5a6a7`；本地记录SHA256 `6dbb90523a35330fedd7f1d3dfc66f995213d81b29a5ca8108dbcdd4e37d6c2f`，没有把它冒称官方发布的SHA256。raw下载卡住后实际改用官方Gitblob raw media接口成功，没有停在网络建议。

MobileSAM需要timm0.9.16，按官方PyPI wheel SHA验证后仅解压到/tmp/python-deps。GroundingDINO/MobileSAM官方Apache-2.0可作为商业许可候选；本实验SP地标和此前SegFormer仍有研究许可限制，不能因此称整套地图管线可商业分发。

GDINO safetensors省略66个共享参数alias。新脚本仅在Parameter对象身份完全相同时补别名，再strict load；真正缺学习参数或同alias冲突均拒绝，有测试。不是用strict=False掩盖缺权重。

## 冻结最小控制

固定文本 `wooden easel . cardboard box . chair . wall .`，box .35/text .25，640短边/960长边，CPU1线程；输入AR gravity quarter-turn和mask回转保持native。全部900 raw检测logits/boxes及所有通过固定门的21检测框保留。没有按地标结果调整文本、阈值或框。

GDINO六图都检测到画架（.487–.871）和纸箱（.499–.922），部分椅子。SAM三alternative原生mask全部保存，预测mask IoU是模型分数，不是实测IoU。首先用三alternative包含该点的一致性作为保守模型证据：211地标中26家具冲突、185未知、0多视图建筑上下文；六图总125.76秒。

随后同21冻结框仅追加single-box模式，不重跑检测、不改门、不删三alternative。MobileSAM predictor文档说明非歧义提示下single-mask模式可能更好；这是适配box的控制假设，不是质量保证。若single与模型IoU选定的multi mask都包含点，列家具冲突模型证据；边界或类别分歧仍未知。建筑上下文要求wall的全部mask同意且无任何家具mask覆盖，至少3不同训练视图一致，但仍不是测量。

single-box控制结果：**57家具冲突、154未知、0建筑支持**。所有211身份及原三mask/原分类都保留，没有将删除错误点称B改进。已实际看两个candidate-context-montage：家具红点明显沿画架木杆与箱椅边，但蓝色单图wall模型上下文仍误落木杆/家具，不能宣称墙测量或A稳定房间identity。

## 接口与验证

新增 `instance_wall_context.py` 和 `instance_wall_box_control.py`，每个固定pointId输出原B0/B2归属、每图native像素、检测phrase/score、全部alternative/single成员关系、模型上下文、physicalWallVerified=False。未在六图出现的原地标仍未知。没有检测到家具不能证明建筑墙，bbox包含点不能替代实例mask。

测试包含：漏检测不认证墙、家具/边界冲突优先、不同视图票、混合phrase未知、共享alias身份/冲突、native点精确转向、single/multi模型一致。最窄命令：`python -m pytest server-vqa/tests/test_spatial_instance_wall_context.py -q`。

产物 `/tmp/vqasee-instance-wall-context-six-v3`、`/tmp/vqasee-instance-wall-single-box-six`。旧初始化失败和六图size-tokenizer参数失败记录也保留，没有计作成功。新实例上下文不能填未观测墙；后续测量仍需要独立多视图表面/边界几何，B尚未通过。旧脚本/root API/iOS和生产安装面未改。
