# OpenCV SIFT 源码接入与真实扫描对照

用户要求复用稳定实现而非自行重写。已纳入OpenCV4.11.0固定commit的SIFT及支持源码，算法文件原样，来源/hash/原始许可保存于native/spatial_sift。支持模块core/imgproc/features2d/flann及构建依赖约24MB，不宣称两文件零依赖；关闭无关GUI/视频/Python包装及网络下载。

## 角色复核与任务

乔布斯：先提高独立照片证据可靠性，不能宣称房间或漂移已修复。小马独立读官方源码并核对hash，交付tech-radar学习文档：DoG尺度极值、亚像素/边缘剔除、主方向、4×4×8描述、截断归一化与SIMD；确认precise_upscale默认值与描述子L2契约。罗根：固定源码、离线构建、显式ABI版本、输入与容量校验，避免系统OpenCV隐性依赖。全麦：同20帧对、相同支持门槛比较，失败全部保留。思余：Mac入口明示旋转不变匹配，不用可测数量代替准确度。

spatial_review负责native源码/C ABI及build脚本、两间房匹配图和对照；space_model以小马角色负责官方实现研究；主Agent负责ctypes客户端、NumPy匹配/独立几何、Mac页面、测试与安装面。

## 改动与影响面

- native/spatial_sift：原版源码、许可证、SOURCE.json、薄C++接口。可调特征预算、尺度层、对比度/边缘阈值、sigma、精确上采样；输出keypoint六字段与128维float描述子。
- deploy/spatial/build_sift.sh：CMake源码构建，产物git-ignore，不用Python cv2或系统OpenCV。setup_mac.sh/core安装面和README同步。
- app/spatial_sift_native.py：ctypes加载，ABI2版本校验，灰度uint8输入。OpenCV保留response并列特征，不能将nfeatures当硬容量；选择预算800与输出容量4000解耦。中间真实容量失败保留，超出最终容量仍显式失败，不截断。
- spatial_image_quality.py：默认source SIFT，经Pillow灰度/640缩略；坐标按像素中心映回原生尺寸。NumPy双向ratio L2匹配+独立F/H支持；不使用AR残差挑选成功点。patch仅显式实验基线，无静默回退。缺源码构建产物返回不可用，Mac中文显示。
- 相机原始JPEG/K/T/轨迹未改；未修改iOS执行路径，Mac库不能当作iPhone可用库。Python wheel依赖已撤回，既有Pillow/NumPy加CMake/C++构建即可。

## 真实对照

两次用户保存的不同房间记录独立分析，使用同样20对、相同测量门槛。

|扫描|旧patch|源码SIFT|耗时旧→新|
|---|---|---|---|
|198照片|5/20可测|13/20可测，7对不可判别保留|3.47→5.86秒|
|104照片|19/20可测|20/20可测|6.50→9.09秒|

耗时为这台Mac离线检查，不是端上延迟。可测比例不是准确率；没有尺度/漂移真值。旧第154帧对跨物体误匹配明显改善，源码SIFT独立支持16/19，姿态残差中位1.40像素。第134帧对独立支持106/128，但仍有38.70像素中位姿态残差，保留待核对；不能据此宣布定位漂移或修复。原始、中间容量失败、最终报告和匹配图仅存/tmp，不提交用户照片。

## 验证

源码真实编译、C ABI390特征调用和finite描述验证通过；28项相关Python测试通过且无skip，含25度旋转/0.85尺度的受控测试。受控样例不代表室内精度。真实Chrome操作104张历史记录→质量检查，HTTP200、算法明示vendored OpenCV4.11/C ABI、20/20结果且无页面错误；结果图和第154匹配图由主Agent亲看。源码hash经小马独立对照官方文件。安装core profile成功构建/加载并跑环境检查；日志保存于/tmp。

剩余：缺官方同版本预构建库逐点描述对照、缺真实尺度/静止真值与端上构建/性能；A转向噪声未修复，B房间语义/结构重建和C3DGS未完成。源码摘取与Mac接入成立，不等于产品整体验收完成。
