# iPhone端侧观察：实现与真机验证

用户裁决：最终运行在iPhone，照片设备内处理/保存，Mac仅开发与离线评测。乔布斯限定本轮闭环：停止扫描→全部采集照片依次端侧检测→原图框/失败可见→自动保存→Store重新读取。不是全场景识别或可靠物体位置验收。

实现：SpatialObjectAnalyzer.swift（串行actor、原照片方向/尺寸与bbox坐标）、SpatialObjectObservationView.swift（照片候选/失败/历史）、SpatialScanController.swift（进度、generation隔离、保存）、SpatialScanScreen.swift（入口与操作保护）、SpatialScanModel.swift/SpatialScanStore.swift（可选观察、raw框、partial、递归历史校验）。测试为SpatialScanTests.swift、SpatialObjectCoordinatesTests.swift、SpatialScanUITests.swift。复用现有YOLO11nObject、Vision/CoreML，无新增模型/依赖/资源路径，安装脚本无需改动。

影响面：扫描JSON在原schema1增加可选objectObservations、rawBoundingBox、previousAttempts和detectionFailures；旧档可读，顶层每帧唯一，逐次历史保留。保存/读取/导出/导入共用Store验证来源帧/epoch/time、finite分数/坐标和部分失败。Python研究消费方按keyframes读取，归档接口保留整个document，无新增网络调用。既有调试共享仍需其原有独立显式同意；本轮未启用共享。

真实回归：设备最新一份108帧扫描，不是前述226帧开发集；没有缩小分母或删除原扫描。首次75 processed/33 failed，定位为raw框略越界（如top=-0.000122、bottom=1.00122）。修复为raw全保留、显示框=raw与照片交集并派生isClipped；完全画外/退化/NaN仍失败。逐检测失败保留原始错误，有效候选继续保留为partial，不因一个错误抹掉整帧。旧失败留在previousAttempts，未降模型confidence/NMS门槛。

同机同108帧最终108 processed、0 partial、0 failed，42个越界框可追溯，保存后Store重新读取完全一致。全108帧逐帧处理p50约11.8ms、p95约13.9ms、最慢约786ms（首次加载）；这些是含照片读取、方向、模型和结果校验的存照批处理耗时，包含所有帧，不是实时相机/绘制/保存的端到端p95，也不是语义准确率。

验证：22项空间/坐标XCTest通过；模拟器真实UI入口测试通过并看过截图（第一轮滑动被SceneKit接收，修正测试从文字区域滑动，仍断言入口可见且空扫描禁用）。模拟器与签名真机构建通过，最终版已安装。真机XCTest UI runner两次均在测试前以code74断连，已停止重试；改用仅Debug显式-spatial-objects-verify入口调用同一analyze/save/loadAll流程，并记录有限阶段状态。此自检不能替代真机GUI交互自动化。

看图：同一照片组件在iPhone上用实际重载结果渲染，正常保存不产图。自动审批最初按设备本地隐私边界拒绝复制该图；用户随后明确允许仅这一张结果图到Mac验收，已遵守，仅复制这张衍生图和计数/耗时元数据，无原照片传输或其他模型服务调用。已看该图：方向与框坐标对应椅子/桌面/盆栽，但逐框类别还不够清楚，不能以此宣布完整识别通过。私密产物均在忽略目录spatial-20261017，不提交。

角色复审：系统修复了手动保存/恢复/删除与识别并行冲突、切换generation、尺寸不符；模型坚持原框追溯、逐候选失败与旧失败不丢失；UI明确候选、闭词表限制与未知位置；产品拒绝把108张处理成功当108张识别正确。最终代码/Store类型检查、diff检查及实机路径均有证据。

剩余：YOLO闭词表不能覆盖纸箱/画架等目标，语义误认仍存在；缺独立实例/位置真值及移动重访样例。此轮仅闭合iPhone二维观察与持久化增量，可靠3D/身份/跨次更新未完成，不能把相机位置当物体位置。下一阶段必须新增物体定位证据与对应评测，实时相机路径另测；不重开高斯或继续穷举prompt。
