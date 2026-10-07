# 室内入口遗漏防熄屏策略

用户反馈：打开VQASee后不操作仍会自动熄屏，之前已修过。

根因：此前唯一 `isIdleTimerDisabled` 写入位于户外 `StreamingViewModel.refreshScreenWakePolicy`，条件为户外推流激活且前台；默认入口改为室内后未接入。户外停止时还会将全局防熄屏关闭，影响返回室内。这是共享系统属性的所有权遗漏，不是用户设置错误。

修复：`VQASeeApp` 在App级观察聚合 `scenePhase`，首次也应用。唯一写入者 `AppScreenWakePolicy` 在active保持亮屏，inactive/background释放。删除 `StreamingViewModel` 与户外根界面的旧写入和生命周期调用，保留相机/推流状态独立。室内、户外、设置、回放与调试等前台页面统一适用；用户手动侧键锁屏不被阻止。

角色审查：罗根独立同意，强调删除所有旧写入、首次active即生效、后台返回恢复。产品取舍是App前台持续亮屏，以支持观察和真机调试；后台不持有亮屏。

修改文件：`VQASeeApp.swift`、`AppScreenWakePolicy.swift`、`StreamingViewModel.swift`、`ContentView.swift`、`AppScreenWakePolicyTests.swift`。调试状态新增 `screen_awake` 仅用于确认实际系统开关；没有更改存储schema或模型。所有写入已语义搜索，收敛为单一来源。

验证：单元测试直接检查UIApplication开关在active→inactive→active→background→active的值，且不创建户外模型；UI回归覆盖室内默认入口与户外往返。模拟器状态断言不能替代真机等待超过自动锁定时限的验证。真机验收需在室内/切换后分别闲置超过系统自动锁定时限，并检查后台返回及手动锁屏。

结果：生命周期单元测试与室内户外往返UI测试均通过；`/private/tmp/vqasee-wake-policy-fixed.log` 显示TEST SUCCEEDED。修复包（含菜单稳定性修复）已于2026-10-07 00:02成功安装并启动iPhone 17，见 `/private/tmp/vqasee-wake-install.log`。尚未取得真实长时间闲置证据，不宣称真机自动锁屏验收已完成。
