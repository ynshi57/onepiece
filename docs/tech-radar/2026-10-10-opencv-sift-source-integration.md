# OpenCV 4.11 SIFT 源码摘取：算法事实、依赖闭包与验收边界

- 日期：2026-10-10；主责：小马；相关角色：乔布斯、罗根、思余、全麦。
- 用户目标：把 OpenCV 稳定 C++ SIFT 源码及其支持依赖纳入 VQASee，由自己可检查、可编译的源码提取特征，不重新实现 SIFT。
- 小马结论：适合先做 **L2 Mac 离线实验**，服务真实扫描照片的旋转/尺度变化匹配与独立几何核对。完整支持依赖属于交付范围；仅复制两个算法文件不能形成独立可构建组件。
- 本文证据：已阅读官方 4.11.0 源码、接口声明、模块构建、许可和 Apple 构建入口；独立下载并核对算法文件 SHA256。**本文没有运行源码构建、特征对照或手机性能测试，不宣称已经 built 或生产验收通过。** 同轮实现、构建结果以实现任务的日志与测试为准。

## 一手来源与固定版本

| 来源 | 本次读取用途 | 可信度 |
| --- | --- | --- |
| [sift.dispatch.cpp，4.11.0](https://github.com/opencv/opencv/blob/4.11.0/modules/features2d/src/sift.dispatch.cpp) | 公共构造、图像预处理、金字塔、线程调度、KeyPoint 坐标还原 | 高：官方代码 |
| [sift.simd.hpp，4.11.0](https://github.com/opencv/opencv/blob/4.11.0/modules/features2d/src/sift.simd.hpp) | 极值、亚像素、方向、128 维描述子及 SIMD/标量路径 | 高：官方代码 |
| [features2d.hpp](https://github.com/opencv/opencv/blob/4.11.0/modules/features2d/include/opencv2/features2d.hpp)、[SIFT API 文档](https://docs.opencv.org/4.11.0/d7/d60/classcv_1_1SIFT.html) | 公开默认参数、描述类型与 L2 契约 | 高：官方接口 |
| [precomp.hpp](https://github.com/opencv/opencv/blob/4.11.0/modules/features2d/src/precomp.hpp)、[模块 CMake](https://github.com/opencv/opencv/blob/4.11.0/modules/features2d/CMakeLists.txt) | 必需头文件、模块依赖和 dispatch 生成入口 | 高：官方构建 |
| [core/private.hpp](https://github.com/opencv/opencv/blob/4.11.0/modules/core/include/opencv2/core/private.hpp)、[BufferArea](https://github.com/opencv/opencv/blob/4.11.0/modules/core/include/opencv2/core/utils/buffer_area.private.hpp)、[CPU 优化配置](https://github.com/opencv/opencv/blob/4.11.0/cmake/OpenCVCompilerOptimizations.cmake) | 私有构建定义、内存对齐与平台配置 | 高：官方支持层 |
| [feature2d.cpp](https://github.com/opencv/opencv/blob/4.11.0/modules/features2d/src/feature2d.cpp)、[keypoint.cpp](https://github.com/opencv/opencv/blob/4.11.0/modules/features2d/src/keypoint.cpp) | 基类实现和 KeyPointsFilter 的外部符号 | 高：官方代码 |
| [Lowe 2004 原论文](https://www.cs.ubc.ca/~lowe/papers/ijcv04.pdf) | 尺度/旋转不变性的原始依据及适用边界 | 高：作者论文 |
| [OpenCV LICENSE](https://github.com/opencv/opencv/blob/4.11.0/LICENSE) | Apache-2.0 与随附版权要求 | 高：官方许可 |
| [iOS framework 构建脚本](https://github.com/opencv/opencv/blob/4.11.0/platforms/ios/build_framework.py)、[Apple XCFramework 脚本](https://github.com/opencv/opencv/blob/4.11.0/platforms/apple/build_xcframework.py) | 未来 iOS 的平台/架构构建入口 | 高：官方工具 |

本轮仓库 `native/spatial_sift/SOURCE.json` 记录 tag `4.11.0`、commit `31b0eeea0b44b370fd0712312df4214d4ae1b158` 与 archive hash。小马独立从官方 tag 下载的两个算法文件，与该清单及仓库副本一致：

| 文件 | SHA256 |
| --- | --- |
| `sift.dispatch.cpp` | `c92b6016fcbdf8a5451bfdc4382a906263b058080880218f7ee28b89f7d1ffd6` |
| `sift.simd.hpp` | `07f5084ef8ef0776652aca0ffe2218447560e5bf6ebf73700fc721637675ae08` |

文件一致只证明来源完整性，不证明编译成功、链接到了该实现、输出与参考一致或真实场景有效。公开原始文件也可通过 [dispatch raw](https://raw.githubusercontent.com/opencv/opencv/4.11.0/modules/features2d/src/sift.dispatch.cpp) 和 [SIMD raw](https://raw.githubusercontent.com/opencv/opencv/4.11.0/modules/features2d/src/sift.simd.hpp) 复核。

## 从源码读出的完整算法链

以下描述是对固定版本源码的核对，集成工作应保留原实现，而不是照本文另写一套。

### 1. 输入与尺度金字塔

`detectAndCompute` 要求非空、8 位深度图像；mask 若提供必须是单通道 8 位。默认检测从 octave −1 开始，先把输入尺寸加倍，默认中间像素类型为 float（`DoG_TYPE_SHORT=0`）。彩色入口按 BGR 转灰度，因此 VQASee 桥接直接传明确的灰度 uint8 数据可避免 RGB/BGR 含义混淆。

每个 octave 保留 `s+3` 层 Gaussian 图像，默认 `s=nOctaveLayers=3`。相邻尺度比例 `k=2^(1/s)`；增量模糊由总方差减去上一层方差计算。下一 octave 取上一 octave 第 `s` 层再缩小一半，而不是从原图任意重新缩放。输入假定已有 `sigma=0.5` 的模糊，补足目标默认 `sigma=1.6`。具体图像路径见 [createInitialImage / buildGaussianPyramid](https://github.com/opencv/opencv/blob/4.11.0/modules/features2d/src/sift.dispatch.cpp)。

### 2. DoG 与候选极值

相邻 Gaussian 层相减得到每 octave `s+2` 层 DoG。内部有效层逐像素检查本层 8 邻居及上下尺度各 9 邻居，共 26 邻居；跳过默认 5 像素边界，并先做粗对比度筛选。实现中的极值比较包含大于等于/小于等于，不能擅自改成另一套严格极值规则。候选检查及后续筛选见 [findScaleSpaceExtrema](https://github.com/opencv/opencv/blob/4.11.0/modules/features2d/src/sift.simd.hpp)。

### 3. 亚像素定位与对比度筛除

`adjustLocalExtrema` 在 x/y/尺度三个方向计算梯度及 3×3 Hessian，用 LU 求解位置修正；偏移未收敛到各分量绝对值小于 0.5 时迁移到相邻采样点，最多迭代 5 次。越界、无法收敛或异常大偏移会失败。用插值后的 DoG 对比度再次筛除；源码检查 `abs(contrast)*s < contrastThreshold`，因此公开 `contrastThreshold` 不是未经层数折算的最终阈值。这部分不能简化为整数像素峰值或仅一次梯度求解。[官方实现](https://github.com/opencv/opencv/blob/4.11.0/modules/features2d/src/sift.simd.hpp)、[参数说明](https://docs.opencv.org/4.11.0/d7/d60/classcv_1_1SIFT.html)。

### 4. 边缘剔除

对空间 x/y 的 2×2 Hessian 使用 trace 与 determinant；`det<=0` 拒绝，曲率比例满足 `trace²*edgeThreshold >= (edgeThreshold+1)²*det` 也拒绝。默认 `edgeThreshold=10`；该参数越大，边缘筛选越宽松，不能与“对比度阈值越大、点越少”的方向混淆。亚像素与边缘结果一起形成位置、尺度、response、打包 octave/layer 元数据。[源码](https://github.com/opencv/opencv/blob/4.11.0/modules/features2d/src/sift.simd.hpp)。

### 5. 方向峰

在对应 Gaussian 层的尺度邻域取中心差分梯度，以空间 Gaussian 权重累加 36 个方向 bin；方向权重 sigma 是局部尺度的 1.5 倍，半径约 4.5 倍。环状直方图用 `[1,4,6,4,1]/16` 平滑，对局部峰做抛物线插值。达到最大峰 0.8 倍的额外方向峰也会产生 KeyPoint，因此同一位置/尺度可能有多条不同方向记录；不能按 x/y 去重掉它们。其坐标约定与 `360-angle` 转换必须和后续描述子一起保留。[calcOrientationHist 与峰值生成](https://github.com/opencv/opencv/blob/4.11.0/modules/features2d/src/sift.simd.hpp)。

### 6. 4×4×8 描述子

描述邻域按特征方向旋转、按尺度归一化；默认每空间 bin 宽度约为局部尺度的 3 倍。在 4×4 个空间 bin、每个 8 个方向 bin 中，以 Gaussian 加权梯度幅值进行 x/y/方向三线性插值，处理方向环绕后得到 128 维向量。缓冲区额外保留边界 bin，不应把内部工作数组误当作最终 128 维输出。`nfeatures` 是筛选保留数量，不能减少前面的尺度金字塔计算。`KeyPointsFilter::retainBest` 对响应分数并列的点可能保留超过目标数量；C ABI 必须检测实际点数与输出容量，明确失败或按公开且有记录的规则处理，不能越界写、静默截掉最后若干点。[calcSIFTDescriptor](https://github.com/opencv/opencv/blob/4.11.0/modules/features2d/src/sift.simd.hpp)、[retainBest](https://github.com/opencv/opencv/blob/4.11.0/modules/features2d/src/keypoint.cpp)。

### 7. 归一化、clamp 与输出的实际值

源码先求描述直方图 L2 范数，把每个分量裁到 `0.2*原范数`，再对裁后向量重新计算范数，以 512 为缩放因子，最后四舍五入并饱和到 `[0,255]`。**本版本的 CV_32F 输出也存放量化后的整数值，只是容器为 float；它不是单位 L2 范数的未量化向量。** CV_8U 也仍是梯度直方图描述子，匹配使用 L2，不是二进制位串的 Hamming 距离。源内另有被编译条件关闭的平方根归一化路径，不能把它标成当前默认 RootSIFT。[输出代码](https://github.com/opencv/opencv/blob/4.11.0/modules/features2d/src/sift.simd.hpp)、[defaultNorm](https://github.com/opencv/opencv/blob/4.11.0/modules/features2d/src/sift.dispatch.cpp)。

### 8. SIMD、线程与数值一致性

算法内部通过 universal intrinsics 处理极值比较、梯度直方图、插值及描述子归一化/输出，并保留标量尾部；外层使用 CPU dispatch、`parallel_for_`、TLS 累积。features2d CMake 为 SIFT 声明 SSE4.1、AVX2、AVX512_SKX 分发变体，实际编译/可运行集合由平台和配置决定，不能宣称每台机器全部启用。ARM 基线可由同一 universal intrinsics 支持层选择 NEON，Mac Intel 路径不能直接复制成 iPhone 路径。

源码有一段明确关闭的 AVX2 范数累计优化，注释说明求和顺序会造成轻微差异。这是为什么对照必须记录线程、优化能力和构建配置，而不能为了“更快”自行启用所有注释路径、开激进 fast-math，再用宽松阈值遮盖差异。[SIMD 实现](https://github.com/opencv/opencv/blob/4.11.0/modules/features2d/src/sift.simd.hpp)、[dispatch 声明](https://github.com/opencv/opencv/blob/4.11.0/modules/features2d/CMakeLists.txt)、[平台配置](https://github.com/opencv/opencv/blob/4.11.0/cmake/OpenCVCompilerOptimizations.cmake)。

### 9. 精确上采样的版本细节

公开 `SIFT::create` 默认 `enable_precise_upscale=false`；内部 `SIFT_Impl` 的声明默认值却是 true，公开入口会把其收到的值传进去。开启时用逆映射 `warpAffine`、0.5 对角映射、线性插值与反射边界，使原采样索引 x 对应扩大图的 2x；关闭时走 resize。4.11 源码日志指出关闭路径会带来定位偏差。两者不是可随意混用的等价预处理。

本轮桥接若显式 false，应先与 4.11 参考的 false 同参验证，再单独评估 true；不能把“官方支持 precise”写成当前运行路径已开启。对照固定 sigma/layers/threshold、图像缩放和坐标还原之后，才讨论它是否改善真实极线误差。[公开默认与含义](https://docs.opencv.org/4.11.0/d7/d60/classcv_1_1SIFT.html)、[实际上采样路径](https://github.com/opencv/opencv/blob/4.11.0/modules/features2d/src/sift.dispatch.cpp)。

## 依赖闭包：摘取的是算法与完整支持面

| 支持面 | 必需事实 | VQASee 集成边界 |
| --- | --- | --- |
| core | Mat/InputArray/OutputArray、Vec/Matx、LU 求解、转换、减法、分配、异常、HAL、线程、TLS、trace/logger | 必须保留同版本实现和生成配置；C++ 标准库不能代替这些符号 |
| imgproc | GaussianBlur、resize、warpAffine、cvtColor | 不能用占位函数或粗糙自写 blur 补链接，同时声称原始 SIFT |
| features2d 基础 | SIFT/Feature2D/Algorithm 继承契约、KeyPoint、KeyPointsFilter 去重/保留/掩码、基类方法 | 两个算法文件以外还有真实链接符号；重复编译两份 SIFT 会产生定义冲突 |
| SIMD/生成文件 | intrin.hpp、平台后端、`sift.simd_declarations.hpp`、CPU dispatch 与 cvconfig | `sift.simd_declarations.hpp` 由 OpenCV 构建生成，不是漏下载一个普通源码头 |
| 私有头/对齐 | precomp.hpp 引入 core/private.hpp、ocl.hpp；SIMD 使用 BufferArea | private.hpp 有内部构建 guard 和配置依赖；安装版公开 include 目录不足以保证可编译 |
| FLANN | 官方 features2d 模块声明 OPTIONAL flann | SIFT 提取自身没有 FLANN 查询；本轮同源支持构建保留 flann，不能把它的代价漏报 |
| 图像解码 | SIFT 吃像素而不读 JPEG | 本轮 C ABI 可接 Python/Pillow 解码后的灰度数组；若原生直接读 JPEG，再计入 imgcodecs 与实际启用的 codec 依赖 |
| 可选外部后端 | IPP/OpenCL/ITT/TBB 等取决于构建开关 | 禁用某后端可以减少依赖；禁用后应验证无自动下载/链接，不能由“看起来没调用”推断零依赖 |

源码依据：[precomp](https://github.com/opencv/opencv/blob/4.11.0/modules/features2d/src/precomp.hpp)、[private guard](https://github.com/opencv/opencv/blob/4.11.0/modules/core/include/opencv2/core/private.hpp)、[features2d CMake](https://github.com/opencv/opencv/blob/4.11.0/modules/features2d/CMakeLists.txt)、[核心配置说明](https://docs.opencv.org/4.11.0/db/d05/tutorial_config_reference.html)。

本轮本地 CMake 的静态检查显示保留 `core,imgproc,features2d,flann`，禁用 IPP/ITT/OpenCL/视频/GUI 等非目标后端；C ABI 桥接链接同源 `opencv_features2d`。这是“由自己纳入仓库的原始源码构建”，不是仅调用系统 OpenCV，也不是两个文件的无支持库组件。**CMake 中关闭开关不等于实际链接检查已完成；仍应审核构建日志、产物依赖及首次离线构建。** 不应把 FLANN 是否还能裁掉，变成本轮复写 SIFT 的理由。

需要保留来源清单、LICENSE、各第三方通知、CMake 平台/配置模板及真正编译到的支持模块；明确区分“仓库保留的第三方源码”和“实际链接进产物的第三方代码”。仅把系统 OpenCV dylib 链接到算法文件，会失去同版本私有 ABI 和可复现性保障。桥接只负责像素/缓冲区/错误/参数契约，不修改算法数值路径；C++ 异常必须转成有原因的失败，不能返回正常的零特征掩盖加载或执行失败。

## 许可与分发

OpenCV 4.11 顶层为 Apache-2.0；SIFT 文件还保留 Rob Hess、Willow Garage、Intel 的版权及原实现的 BSD 式通知。纳入源码需保留原文件头、Apache LICENSE、适用的第三方许可/NOTICE；修改文件时记录修改，二进制分发也携带所需版权与许可材料。保留支持模块意味着要检查其随附许可，不能只给两个 SIFT 文件加一句“Apache”就结束。[顶层许可](https://github.com/opencv/opencv/blob/4.11.0/LICENSE)、[SIFT 文件原始通知](https://github.com/opencv/opencv/blob/4.11.0/modules/features2d/src/sift.dispatch.cpp)。

源码头注明所引用的 US6711293 专利在 2020 年 3 月到期；这一出处可记录为该实现的历史信息，不能扩张为对所有法域、所有其他权利和所有新增支持库的全面无风险保证。本文只核对公开许可文本，不替代发行前对实际分发文件集合的核查。[官方源码头](https://github.com/opencv/opencv/blob/4.11.0/modules/features2d/src/sift.dispatch.cpp)。

## Mac 离线与未来 iOS 的代价

| 维度 | 本轮 Mac 离线 | 未来 iPhone 集成：尚未验收 |
| --- | --- | --- |
| 工具链 | CMake、Clang/C++ 标准库、同源支持模块；Python 图像解码/NumPy/ctypes 属于桥接运行依赖 | 完整 Xcode/iOS SDK，device 与 simulator 独立平台构建，静态库/合规 framework 或 XCFramework，Swift 的 C/ObjC++ 包装 |
| 产物 | 本机架构 dylib；检查支持库是否静态并入、是否意外链接系统 OpenCV | Mac dylib 不能直接装进 iOS；arm64 device 和 arm64 simulator 即使 CPU 相同也不是同一平台切片 |
| 计算 | 逐帧提取后缓存，离线批处理可量化耗时/峰值 RSS；禁止整套金字塔长期累积 | 金字塔与 DoG、工作缓冲会挤占相机/ARKit内存；需要实测 CPU、内存、热状态、掉帧及 p95延迟 |
| SIMD/调度 | Intel Mac/Apple Silicon配置不同；record build flags 和 dispatch能力 | 适配 ARM NEON、iOS调度；禁用无用后端，工作线程不能阻塞主线程/ARSession |
| 端上产品 | 只是质量核对，不进入实时看路链路 | 只有证据支持时才排入端上路线；必须与实时核心预算一起验收，不能据Mac耗时承诺手机实时 |
| 可复现与隐私 | 一旦全部源码/依赖就位，再证明断网构建和失败提示；本地扫描照片不外发 | 安装脚本、项目链接、资源和许可必须同步；图像仅在显式授权路径处理和存储 |

尺度金字塔的内存并不随最终 `nfeatures` 成比例下降。按默认 3 layers、float、首层图像加倍、无限几何级数粗估，Gaussian 加 DoG 的纯像素存储上界约 `4*(6+5)*(4/3)*4*W*H` 字节，尚未含临时缓冲、描述子、原图、分配开销与实际有限 octave 的差异。这是**从源码数组规模推导的预算估计，不是测量**；约 640×480 时约 69 MiB，不能把原生全分辨率 JPEG直接搬到手机上每帧运行而不测内存。官方 Apple 构建脚本证明存在支持入口，不证明本轮配置已经成功用于 iPhone。[iOS入口](https://github.com/opencv/opencv/blob/4.11.0/platforms/ios/build_framework.py)、[XCFramework入口](https://github.com/opencv/opencv/blob/4.11.0/platforms/apple/build_xcframework.py)。

## 独立建议与专家 review 请求

这些是小马提交给专家的建议，不伪称他们已经 review 或同意。

| 角色 | 小马建议的立场 | 本轮必须审查 | 该角色以后应多问的问题 |
| --- | --- | --- | --- |
| 乔布斯 | 有条件同意 Mac L2 | 把“提高可核对匹配覆盖”与“解决漂移/重建房间”分开验收；所有失败帧对在分母中 | 成功点变多是否带来真实几何证据？用户是否仍看见不可测与不确定性？ |
| 罗根 | 有条件同意完整支持依赖 | 从新机离线构建、ABI/C ABI边界、产物链接、内存缓存/并发、失败恢复复核 | 缺CMake/源码/库时有没有清晰恢复？source build是否不依赖系统OpenCV？ |
| 思余 | 有条件同意质量检查呈现 | 默认只展示覆盖/可测/失败理由及时间段；参数保持渐进披露 | 图像匹配失败和真实空间误差是否被不同状态明确表达？ |
| 全麦 | 有条件同意原始C++与参考对照 | 灰度字节、完整参数、坐标缩放、KeyPoint所有方向、L2描述符、ratio/mutual、独立几何 | 描述值是量化float还是单位向量？误匹配是否用AR预测自身“证明”？ |

协作接口：罗根交付源码构建与 C ABI 元数据；全麦交付同参参考和真实配对指标；思余消费包含失败项的结果 schema；乔布斯对同场景的准确度/耗时双验收裁定是否从 L2 升级。小马保留一手出处与未验证边界，不替代各角色的实测。

## 最小完整实验与退出条件

1. **来源与支持闭包**：清单核对原算法文件 hash、支持模块版本、许可及真实构建入口；在干净 build目录、无系统OpenCV的条件下构建，记录CMake/Clang/架构与产物依赖。缺依赖明确失败；源码缺失不临时下载另一个版本顶替。环境改动同步 `setup_mac.sh`、依赖清单和README。
2. **算法对照**：同一灰度字节，固定nfeatures/layers/contrast/edge/sigma/type/precise、线程和优化配置。以同版本官方构建为独立reference；比较点数、x/y/size/angle/response/packed octave及对应descriptor行，考虑TLS可能改变列表顺序，不能只比“有返回值”。同架构同配置优先严格比较；跨SIMD路径的容差须先解释来源、固定再评估。
3. **真实/近真实输入**：保留空白、低纹理、真实室内照片，以及已知旋转/尺度变换的控制样例；合成变换只能验证不变性，不能包装成房间精度。SIFT特征本身不做3D、metric scale、SLAM或语义分类。
4. **真实扫描闭环**：沿用同一真实扫描、同一选中帧对集合、同一独立F/H支持与原始K/T核对；报告总选中、可测、不可测及逐原因、匹配/内点、空间覆盖、几何误差分布、冷/热提取/匹配/几何时间和RSS。禁止只显示成功pair，禁止使用待验证AR姿态直接选出“符合它的matches”再证明它正确。
5. **双验收**：输出必须确实来自本轮编译的source产物；与参考同参一致性通过、旋转/尺度控制样例按已知变换核验、真实失败项完整且UI消费通过，才算“源码组件接入实验可用”。对于“覆盖是否提升”，提前冻结旧baseline和同pair分母；只有覆盖增加且独立几何证据/耗时可接受才成立。覆盖不增加也保留事实，不调低可靠性门槛换成功率。
6. **失败退出**：support缺失/混版本、hash不符、源码输出与reference无法解释的差异、内存失控、隐藏失败、只有AR自证或缺少真实样本均阻止升级。若原始照片低纹理/模糊/低基线导致不可测，应回到采集与真值建设；不重新实现SIFT，不把新增诊断记为漂移已修复。

推荐候选：固定4.11官方C++与同源core/imgproc/features2d/flann支持面；待验证：precise=true与false同场景对比、ARM/Intel数值与时延、未来iOS切片与热预算。暂不采用：手写简化SIFT、用Python wheel作为实际生产提取器却声称源码集成、绕开依赖闭包、以匹配数量代替房间/尺度真值。[SIFT原论文适用范围](https://www.cs.ubc.ca/~lowe/papers/ijcv04.pdf)。

## 本次沉淀与后续学习

本文是源码阅读情报卡；同轮实现/实验数据进入 `docs/evolution/` 与回归测试。现阶段不把未经本机/端上验证的性能数值写入CURRENT，不新增“无依赖SIFT”等长期规则，也不因本卡直接升级L4。四角色需吸收“源文件+支持闭包+同参参考+真实全分母+准确度/耗时”这一验收方式；它已与AGENTS的依赖同步、禁止掩盖缺陷、测试底线一致，本次无需改AGENTS或skill。下一次主动雷达聚焦低纹理室内匹配、rolling shutter与图像/位姿时间一致性、精确上采样对实际残差的影响；这些是后续研究主题，不是本卡已验证成果。
