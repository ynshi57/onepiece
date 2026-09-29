# iOS Core ML 资源与模拟器恢复记录

## 现象

- `deploy/ios/test.sh` 因本机没有可用 iOS Simulator runtime 而无法运行。
- Xcode 在 Swift 编译前复制多个 `.mlmodelc` 时，因包内同名的 `coremldata.bin`、`metadata.json` 等文件发生输出冲突。

## 根因与修复

项目的 source synchronized group 递归纳入了 `VQASee/` 下的五个编译后 Core ML 包。它们不是普通资源目录，不能作为同步组中的散文件复制。

1. 将五个 `.mlmodelc` 包移动到 `ios-vqa-app/VQASee/ModelResources/`。
2. 在 Xcode 项目中将它们以 folder reference 显式加入 Resources build phase；资源包名保持不变，因此 `Bundle.url(forResource:withExtension:)` 的调用方无需修改。
3. 同步更新模型安装、导出与 perception harness 的默认路径，以及 `setup_mac.sh` 和 README。
4. 安装 iOS 26.5 Simulator runtime；恢复后使用 iPhone 17 验证。

## 验证与遗留限制

- Xcode 的 `CpResource` 已逐个复制五个完整模型包，不再出现内部同名文件冲突。
- iPhone 17 / iOS 26.5 上的 7 个关键时序融合、车道与引导单测全部通过。
- UI 测试不再调用易在新模拟器上超时的窗口截图服务，改为验证 App 进入前台；`-ui-testing` 启动参数跳过摄像头和语音隐私硬件初始化。
- 首屏截图已人工检查，显示 VQASee 本地看路首页，无隐私授权弹窗。

本机 XCTest 在 UI runner 完成后偶发清理滞留；这是 Xcode/CoreSimulator 基础设施问题，仍需在 CI 或另一台 Mac 运行完整 `bash deploy/ios/test.sh` 作为发布前最终检查。
