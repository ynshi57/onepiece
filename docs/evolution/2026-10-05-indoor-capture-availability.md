# 采样入口不可用与相机来源排查

用户反馈：采集测试样本点不了，怀疑前置摄像头开启。

## 事实与修复

- 菜单入口没有 disabled；采样页的开始按钮受 AR 会话状态约束。尚未确认用户点不了的是哪一层。
- 恢复空间位置的 15 秒超时原来位于新鲜相机帧检查之后。无帧或持续旧帧会阻止超时执行，导致恢复状态和禁用状态滞留。现在先处理超时，再读取画面。
- 禁用原因过去统一误报为模拟器限制。现在区分界面示例、不支持设备、权限、暂停、恢复位置、等待新画面，提供系统设置或重启相机入口。
- 采样页展示期间保留相机；后台与切换户外仍停止。采样绑定 AR 会话 ID，坐标重置后结束旧采样，避免混入不同坐标系。
- 室内使用 ARWorldTrackingConfiguration，统一配置显式关闭 userFaceTrackingEnabled；户外原本明确使用后置广角。没有找到主动开启前摄的路径，尚无真机证据确认前摄实际开启。绿色隐私指示灯不能区分前后摄像头。

## 改动与影响面

应用文件：IndoorMemoryModel.swift、IndoorMemoryController.swift、IndoorMemoryScreen.swift、IndoorCaptureScreen.swift、IndoorCaptureRecorder.swift。测试：IndoorMemoryTests.swift、IndoorMemoryUITests.swift。

共享配置覆盖首次启动和超时重置；采样帧入口同步传入 AR 会话 ID。变更影响室内相机会话、采样可用状态、采样弹窗生命周期及坐标重置后的终止行为；不改变存储 schema、模型和安装依赖。

## 审查与验收

- 产品：不可用必须说明真实原因并提供恢复操作，不能把诊断当作能力恢复。
- 系统独立审查：确认无帧时超时失效；弹窗生命周期是可能路径，未声称真机已复现。
- UI：检查导出的模拟器采样页截图，禁用原因完整可读。合成示例仍明确禁止真实采样。
- 模型边界：没有新增自动判断消失或移动能力，不把采集标签当模型结果。

验证命令：xcodebuild -project ios-vqa-app/VQASee/VQASee.xcodeproj -scheme VQASee -configuration Debug -destination 'platform=iOS Simulator,id=5B517ADD-4230-49EE-822B-8162E7F7B7B9' -parallel-testing-enabled NO -derivedDataPath /private/tmp/vqasee-next-build -resultBundlePath /private/tmp/vqasee-capture-gate-fixed.xcresult -only-testing:VQASeeTests/IndoorMemoryTests -only-testing:VQASeeTests/IndoorCaptureTests -only-testing:VQASeeUITests/IndoorMemoryUITests test

结果：19 项单元测试、4 项 UI 测试通过；git diff --check 通过。单元测试验证超时策略不依赖画面输入、暂停原因和禁用人脸跟踪配置；尚未覆盖真实 AR 硬件故障恢复。已查看采样页测试截图，未把合成图片当真机证据。

## 未闭环

尚未安装到用户手机，未完成真机采集验证。等待确认用户所指的按钮以及是否看见自拍画面；缺真实设备现场证据，不能断言用户问题完全解决。下一步需要更新 App 后复测，不需要用户准备模型或标注数据。
