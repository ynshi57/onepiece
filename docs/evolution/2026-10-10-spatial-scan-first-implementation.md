# 无深度空间扫描：首轮实现与验收边界

## 实际改动

- SpatialScanController：独立 ARKit RGB+IMU 会话，仅 horizontal/vertical planeDetection，不请求 sceneDepth、RoomPlan 或 sceneReconstruction。10Hz 位姿观察，2cm/2度/0.2秒抽样，失跟后分段；关键帧1Hz、有界编码，240张/约100MB上限可见。
- SpatialScanModel/Geometry：米制点、位姿/epoch、观测面、相机内参与完整column-major cameraToWorld。跟踪缺口、时间逆序、异常速度断段，不捏造连接。
- SpatialScanScreen：实时摄像叠层、常驻俯视轨迹、扫描结束可旋转SceneKit结构、人工顶点修正、保存历史、完整包分享。轨迹批量 geometry；结构仅变化时重建。平面边界是已观测范围，不是完整墙面。
- SpatialScanStore：文件保护、不备份、原子保存、照片存在/矩阵/时间校验、未提交目录清理；逐图 ZIP＋SHA256清单。ZIP不压缩JPEG，避免重复压缩开销，CRC使用预计算表。
- ContentView：默认空间扫描，旧物品记忆保留菜单入口，相机按需切换。DeviceDebugEvidenceBridge：显式开启原始样本共享后上传结构与前三张参考JPEG；日志明确样本范围，完整包仍须主动导出。
- Mac spatial_scan_api/html：ZIP导入、校验清单、轨迹三视图/时间滑块、实际照片访问；仅本机访问，安全文件路径，128MB输入/解压限额，导入重任务在工作线程执行。main注册router、诊断主页加入入口。

## 角色审查与修复

乔布斯：目标不能退化为黑点或图片对照；把扫描、保存、重开、导出做成同一旅程。未观测结构不能补齐成事实。

罗根/全麦独立代码审查指出：导出缺JPEG、孤儿目录隐私问题、矩阵校验缺口、异步授权竞态、假轨迹连接、逐段节点性能问题；已分别修复。思余实现并修正首屏布局、轨迹批量渲染、明确空态、权限生命周期和未保存替换确认。小马的论文选型仍以10月9日设计文档为据，未安装或伪造任何学习SLAM/3DGS结果。

## 已验证与未验证

- iphoneos Debug generic build，签名关闭：通过。它证明真实设备架构编译，不证明已安装或手机UI功能通过。
- Swift独立编译当前Model+Store，真实1×1 JPEG保存/读取、重复ZIP导出、系统unzip检查、独立SHA256检查：通过。图像是协议样本，不能证明场景重建。
- Swift生成ZIP→Mac实际浏览器上传→JPEG decode：通过。Mac真实界面截图 `/tmp/vqasee-spatial-swift-mac.png`；无真实房间输入，只证明端到端文件消费。
- 后端空间导入、zip-slip、相机矩阵、清单校验、局域网访问拒绝测试：通过；设备调试接口回归通过。
- SpatialScanTests/SpatialScanUITests全部编译，执行受阻：模拟器Runner启动被SBMainWorkspace拒绝，截图Timeout waiting for screen surfaces。停止本轮卡住进程；不能标成XCTest通过。另修复原有轮廓测试中SIMD矩阵构造的编译错误，并更新旧默认入口UI断言。
- iPhone17实扫、用户首屏看图、尺度/漂移/长时延迟/温升：**未验证，未通过**。没有以fixture或构建替代。

后续真实设备尝试：发现已配对iPhone17，使用应用已有开发团队签名构建通过。首次设备服务/安装60秒超时，重试后成功安装 com.bayes.VQASee，保留原应用数据。真机XCTest尝试先发现测试targets缺开发团队，临时继承app开发团队后，UITests Runner因缺profile且Xcode账户登录失败(-1000)被阻塞；没有修改项目Team以绑定新账号，也没有称自动UI验收通过。需要恢复Xcode账户登录/Runner签名，或用户直接进行真实扫描操作。安装成功不等于真实场景验收通过。

相关后端测试最终39 passed（空间导入5项＋真机调试回归34项）；main OpenAPI确认新路由已注册。测试样本不代表真实房间能力。

真实iPhone17 `devicectl process launch` 成功返回应用已启动。未获取手机画面，启动成功不能代替界面验收。

## 变更影响面

默认入口、室内/户外切换、旧记录入口、调试状态与共享证据、ZIP协议、Mac导入/文件服务/平台导航、UI回归断言同步。旧物品JSON未改变；新空间schema独立为1，米/轴/矩阵/epoch在manifest明确。新增Apple系统framework，无第三方依赖变化；Mac导入使用标准库，无模型下载或新安装要求。README更新用法。

## 未完成，不能宣称全部设计已交付

自动稳定墙面合并、完整房间闭合、门窗识别及拓扑仍未实现；当前只保留ARKit观测面与人工修正。没有真实3DGS训练/渲染后端，Mac明确not_available。已提供完整同步输入包，但输入准备不是3DGS能力恢复。

进入下一算法阶段需要无深度iPhone17的真实多视角房间样本与测量对照，验证白墙/玻璃/暗光/视差和跟踪质量；当前阻塞的是实际场景验收，不是宣称技术不可能。首轮不是完整矢量房间产品发布。

## 最短真机操作

安装当前Xcode构建→打开空间扫描→开始扫描并允许相机→缓慢横向走动扫墙角和地面→结束→旋转查看/看俯视轨迹→保存→历史重开。需要Mac复盘时导出扫描（含照片），在闭环主页空间扫描入口导入ZIP。无需LiDAR、无需用户输入深度。拍摄图像仅默认本机保存，主动导出才分享。
