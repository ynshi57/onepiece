# 连续刷新下室内菜单点击无响应

用户反馈：展开菜单后所有按钮点击没有反应。

## 根因证据与修复

旧静态模拟器布局未覆盖真机每秒20次状态发布。新增 DEBUG 参数 `-indoor-ui-refresh-stress`，以0.05秒 common RunLoop定时器复用布局状态发布，不伪造真实AR数据。

旧实现下，`testMenuActionsWhileCameraStatePublishesAtTwentyHertz` 真实坐标点击采集菜单项后目的页未出现，截图显示菜单仍在（`/private/tmp/vqasee-menu-stress.xcresult`）。静态对照的3项测试通过。

将静态菜单提取为 `IndoorMemoryActionsMenu`，根据controller identity建立相等边界，防止高频状态发布反复重建菜单动作。保持20Hz刷新和全局触摸观察器，同一测试通过（`vqasee-menu-stable.xcresult`）。已查看失败和通过截图。该对照支持高频重建为本次可复现原因，不能替代用户真机复测。

思余独立审查同意：回调读取controller实时引用和SwiftUI State storage，无追踪快照冻结；7个菜单动作映射完整。未来动态菜单标题、disabled或回调语义变化必须扩展比较条件。乔布斯裁决：菜单可用属于闭环前置条件，本轮修复，不降低相机刷新频率。

## 影响面与验证

文件：新增 `IndoorMemoryActionsMenu.swift`；更新 `IndoorMemoryScreen.swift`、`IndoorMemoryController.swift`（仅DEBUG压力输入）、`IndoorCaptureScreen.swift`（完成按钮测试ID）、`IndoorMemoryUITests.swift`。

全部室内菜单入口共享稳定菜单：调试、采样、扫描、保存、清除、定位诊断、户外。模型、存储schema、网络协议及依赖未改变。最终 `vqasee-menu-regression.xcresult` 3项UI测试通过，覆盖20Hz下采样内层sheet、调试根层sheet、户外切换及返回，另覆盖配对授权与复测预填。未宣称7项动作均已真机实测。

真机Debug构建通过。Xcode自动续签后profile到期为2026-10-13 13:46:07 UTC；此前过期是真实独立安装问题。首次安装修复包因设备连接中断失败，后续安装结果需单独核实。尚未收到用户真机修复后的会话证据。

窄验证：`xcodebuild -project ios-vqa-app/VQASee/VQASee.xcodeproj -scheme VQASee -configuration Debug -destination 'platform=iOS Simulator,id=5B517ADD-4230-49EE-822B-8162E7F7B7B9' -parallel-testing-enabled NO -derivedDataPath /private/tmp/vqasee-next-build -only-testing:VQASeeUITests/IndoorMemoryUITests/testMenuActionsWhileCameraStatePublishesAtTwentyHertz -only-testing:VQASeeUITests/DeviceDebugUITests test`。

剩余闭环：安装修复版，手机解锁连接，在同场景操作并通过真机调试收取证据。不能把模拟器测试或成功构建称为真机已恢复。
