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

### 新扫描全序列与非相邻连接控制

`replay_saved_scan.py --data <扫描目录> --resources <已核验资源目录> --epoch <段号> --output <新目录>` 保留该段全部照片，匹配时间偏移1/2/4/7并重建；没有独立留出评估，不能用样本内误差验收。

`nonlocal_pair_control.py --data <同一扫描目录> --baseline <上述result.json> --resources <同资源目录> --output <新目录>` 保留原配对，使用AR距离/朝向提出跨时间候选，再独立验证匹配和重建。只比较连通性及计算成本，不把AR当真实尺度或降低失败门槛；输出不自动替换App或平台默认结果。真实226图实验见 `docs/evolution/2026-10-13-new-scan-nonlocal-connectivity.md`。

### 已知相机下的高斯外观对照

`gaussian_camera_control.py --data <同一扫描目录> --model <nonlocal/map/models/0> --baseline <replay/result.json> --binary <已核验的brush_app> --output <新目录> --epoch 2`

复用现有 geometry / Brush 可选安装环境，不引入新运行库。核验地图来源、原照片和相机内参，再用共同照片、同一随机初始化 PLY、相同步数与容量比较原始 ARKit / SfM 相机方案。默认每臂 2000 步、256、SH0、20000 上限，仅用于初筛。Metal 需要当前执行环境能够访问 GPU；不可见时显式失败，不回退成另一种训练算法。

所有照片已参与 SfM，因此这是已知相机条件下的外观开发实验；留出 RGB 不参与外观损失，但不是独立端到端验收。未注册照片和无法评估的预定视角保留在分母，逐图对照为原照片 / ARKit 渲染 / SfM 渲染。产物包含私人照片，保存在本地忽略目录，不提交。解释边界见 `docs/tech-radar/2026-10-14-gaussian-camera-control.md`。

`--initialization-control-parent <已完成相机对照目录>` 固定该次226输入、175训练/27可评估视角、SfM相机和预算，对比原随机初始化与至少两个训练视图支持的稀疏点初始化。点位置、颜色、数量和邻域尺度同时变化，不能称纯几何消融。训练RGB采样不读取评估照片；SfM几何仍使用全图，不是独立几何测试。父实验的相机/分区/照片字节、实际预算命令、二进制身份与27渲染指标全部重验；旧receipt无预算摘要时以完整实际命令核验，不伪造历史字段。预检或训练失败落盘，已有输出目录不可覆盖。

研究函数`train_arm`与`verify_completed_arm`的显式`max_splats`/`sh_degree`参数用于单变量容量和外观对照；默认20k/SH0保持不变。训练命令、PLY SH验证、后验command核验必须使用同一配置，不能用SH0父结果替代SH3。

### 场景语义观察入库（2026-10-16，M1研究原型）

`scene_memory_control.py` 复用已有CPU SegFormer缓存/隔离runtime，将固定真实照片的类别区域存为可重载查询的sidecar，并生成原图叠图。区域不等于物体实例，未知3D明确为空；模型分数不是准确度，单像素区域与逐帧失败均保留。主App/API未接入，不改生产模型；研究依赖使用独立可选安装 profile。

```bash
.venv/bin/python deploy/spatial/research/scene_memory_control.py --scan <已有扫描目录> --model <已有SegFormer-B2目录> --runtime <安装摘要输出的完整runtime路径> --output <新的研究输出目录>
.venv/bin/python -m pytest server-vqa/tests/test_spatial_scene_memory.py server-vqa/tests/test_scene_memory_control.py -q
```

先运行 `bash setup_mac.sh --skip-core --skip-models --skip-qwen --skip-ios --with-spatial-memory`。
此可选 profile 检查现有 NumPy/Pillow/SciPy、PyTorch、packaging 和 curl；缺失时明确指向 core / models / geometry 对应安装入口。
固定研究包来自 `server-vqa/requirements-spatial-memory.txt`，安装器校验官方 wheel 并隔离安装到按 Python 版本派生的持久目录。
将 setup 摘要输出路径显式传给 runner 的 `--runtime`，不复制旧临时目录。模型仍须提供已核验缓存；推理不安装或下载，缺失保留失败，不静默替换模型。设计与完整能力边界见 `docs/evolution/2026-10-05-indoor-memory-capture-design.md`。


`scene_inventory_control.py --scan <扫描> --output <新目录>` 调用已启动的本机Qwen服务，逐帧保存候选、证据、失败与耗时；截断输出拒绝入库。`frame_inventory_memory.py --help` 提供转入v2帧级观察与查询入口；不生成框、概率、物体数量或3D。

`semantic_surface_support.py --scan <扫描> --sfm <已有地图> --config <分割config> --labels <语义输出目录...> --output <新目录>` 是独立M2纸箱表面点候选实验。仅注册相机参与，全部冲突保留；历史输入未绑定、像素约定未独立验证时对应verified=false。禁止将这些SfM局部点当物体中心、可信世界位置或导航依据。

### 可选 Grounding DINO 安装

`bash setup_mac.sh --skip-core --skip-models --skip-qwen --skip-ios --with-spatial-grounding`
同时启用已有 spatial-memory 隔离运行库。安装器 `deploy/spatial/install_grounding_model.py`
读取官方 `IDEA-Research/grounding-dino-tiny` API 当前 revision 后固定全部下载，
保存配置/processor/tokenizer/权重，权重核对官方 LFS SHA256；revision 与逐文件摘要写入 installation.json。
模型与运行库完整持久目录由 setup 摘要输出，不引用临时目录；权重单次≤180秒、其他请求≤120秒、总预算420秒，不重试、不切镜像。
失败保留 receipt 并返回非零。此流程不读取私照、不改变生产模型或默认值，无新增 pip 依赖。
仅 CPU 二维候选定位研究，不接 App，不证明实例身份、三维位置或产品准确度/实时性。
