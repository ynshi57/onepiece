# iPhone 本地物体区域的多视角证据（L2 实验）

目标是记住物体在哪里；检测框、类别相同、相机位置都不足以提供物体位置或身份。

一手依据：
- [Apple Vision 光流](https://developer.apple.com/documentation/vision/vngenerateopticalflowrequest)：同尺寸图像、串行请求、资源开销需测量。当前SDK明确revision 2为网络版本，本机Mac不支持，禁止悄悄改成revision 1。
- [ConceptGraphs](https://concept-graphs.github.io/)：以带位姿的RGB-D观测构建物体图。其代码和GPU模型不能直接当作iPhone部署方案；当前扫描没有深度，不能照搬其输入假设。
- [Apple sceneDepth](https://developer.apple.com/documentation/arkit/arconfiguration/framesemantics-swift.struct/scenedepth)：需设备能力检查，LiDAR限制使它不能覆盖现有RGB+IMU路线。

采纳：先对Apple学习型双向光流做已知平移自校准，按原生K/T三角化，检查视差、正深度、重投影及射线间距；保留具体图像点和来源尝试。区域内点可能是背景，暂不生成物体中心、实体合并或重访更新。此为实验门槛，不是位置准确度承诺。

角色学习：乔布斯以真实物体归属和位置误差验收；罗根把首次网络成本与失败计入，保持离线串行、有界图像；全麦分开“光流对应正确/几何一致/物体归属正确”三种证据；思余让未定位与失败继续可见，禁止把相机拍摄点画成物体点。

最小实验：合成非对称平移核对网络方向/Y/单位，独立缩放像素中心控制，再测真实同场景对应及几何支持数量、全部失败、首帧与稳态耗时。连续两次同一假设失败则停止该假设，不延长工具链。下一关是前景归属与真实位置真值；未经此关不能进入物体地图。

不新增训练、远程服务或额外下载依赖；不更新长期AGENTS/Skill，待真实证据稳定后再判断。
