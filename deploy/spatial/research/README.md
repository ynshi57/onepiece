# 室内语义研究实验

`segformer_masks.py` 只验证二维语义候选，既不恢复墙距，也不生成已验收墙体。
使用隔离环境安装 `requirements-segformer.txt`，不要覆盖生产 `.venv`。此研究工具不接入
`setup_mac.sh` 的生产 profile，不默认下载或发布模型。新增产品依赖时必须同步安装真源。

权重来自 NVIDIA `segformer-b0-finetuned-ade-512-512`，固定 revision
`489d5cd81a0b59fab9b7ea758d3548ebe99677da`。脚本严格校验官方 safetensors SHA256、
严格加载参数，按 AR 重力转正照片；输出原生像素标签、语义叠图与置信度不足区域。

运行参数：`--scan` 扫描目录（含 document.json），`--model` 权重与 config.json 目录，
`--output` 新输出目录，`--indices` 冻结照片索引，逗号分隔。全部帧保留，失败显式报错。
产物含私人照片，不提交到仓库。

[官方模型](https://huggingface.co/nvidia/segformer-b0-finetuned-ade-512-512)
及[官方许可](https://raw.githubusercontent.com/NVlabs/SegFormer/master/LICENSE)：
限研究/评估用途，不能因本实验成功就直接作为商业 App 依赖发布。
置信度不是实测准确率；未独立标注时不报告 IoU 或边界验收通过。

`semantic_landmarks.py` 用 `--sfm` 官方冻结地图目录、`--masks` 完整训练mask目录、
`--alignment` 训练姿态冲突记录、`--output` 新目录，对每个三维点逐条保留训练视图标签。
此脚本额外需要隔离研究依赖 `pycolmap==3.12.5`。代码拒绝验证照片混入，保留
未知和冲突。至少三个不同训练视角一致且各分数>=0.5仅作为语义支持候选，
不输出墙距、尺寸或完整面。这些阈值是诊断条件，不能当产品验收门槛。

`semantic_lines.py` 需设置 `PYTHONPATH=server-vqa` 并先构建仓库源码LSD桥。
输入 `--scan`、`--masks`、`--output`。所有源码线段保留，原±6像素的墙地两侧候选
保留为基线；±2/4/8/16像素多尺度窄带、中心第三类与低模型分数用于诊断。
多尺度一致仍不证明真实墙地界，需独立多视角几何和墙/地平面支持。

`track_residual_diagnostics.py --input frozen.json --output diagnostics.json` 检查冻结
训练地标的重投影残差是否随图像行或离主点距离变化。输入显式训练/验证集合、
原生K与worldToCamera、三维track及像素观察；按完整地标分组留一验证。
它不会修改相机，不将趋势认定为滚动快门或畸变根因。

`restore_photo_check.py manifest.json output-directory` 检查冻结训练照片区域在
假设平面和当前位姿预测下是否获得真实像素支持。原生坐标与半像素K同步，
每个搜索位移使用共同有效像素，完整保留低纹理、歧义和画外结果及叠图。
所有相关阈值尚未校准；局部照片支持不能证明房间身份，尤其区域位于家具时。
这两个脚本只依赖现有numpy/Pillow，不接入iOS恢复成功判定。

`resect_training_views.py` 只对未可靠注册的训练帧向冻结地图做重定位研究。
需要隔离研究pycolmap依赖和源码SIFT桥；验证照片不能进入匹配/求解。
独立地标组评分、重叠patch、弱求解和失败均保留，不修改原AR记录。

`pair_geometry_control.py` 与 `pair_geometry_hypotheses.py` 用冻结训练照片匹配比较原AR、
图像F/H、共享内参和局部姿态假设。全部相邻训练pair和失败保留；同一固定空间分组
拟合/评分。它们不修改原相机、不验证尺度，也不把逐对解作为SLAM轨迹。
先运行 `bash setup_mac.sh --skip-core --skip-models --skip-qwen --skip-ios --with-spatial-geometry`，
使用仓库 `.venv/bin/python`。依赖固定于 `server-vqa/requirements-spatial-geometry.txt`，
不安装其他感知模型。缺此profile时应按上述入口安装，不临时隐式安装包。
第一脚本参数 `--document document.json --pairs frozen-pairs/result.json --output control.json`；
第二脚本参数 `--document document.json --control control.json --output hypotheses.json`。
有界鲁棒最小二乘参见 [SciPy 1.13.1 官方说明](https://docs.scipy.org/doc/scipy-1.13.1/reference/generated/scipy.optimize.least_squares.html)。

### 未注册照片与分辨率诊断

`registration_failure_audit.py --pairs <冻结result.json> --map <COLMAP模型目录> --output <新JSON> [--document <原document.json>]` 核查特征索引并报告全部未注册帧的地图支持；可选PnP只是样本内诊断，不写地图、不代表定位通过。

`feature_resolution_probe.py --data <扫描目录> --resources <已核验LightGlue资源目录> --output <新目录> [--pairs 167:168,168:169]` 对同一组照片运行640/1280提取（均最多1024点）与3px几何检验，保留失败、缓存身份和耗时。默认是固定失败照片对。选择开发照片后只可作为诊断，不能继续称独立holdout；匹配数增加不等于准确度提高。SuperPoint原有非商业研究许可限制仍有效，这不是App模型引入。
