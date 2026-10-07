# 真机调试：附近 Mac 连接与用户验收

## 用户任务与裁决

用户反馈：密钥、复制手机配对信息、粘贴到手机和方向不明确，无法自然完成连接。
乔布斯裁决：Mac 页面打开即可等待；手机选择附近 Mac，首次 Mac 核对数字并允许，手机主动开始共享。每次迭代亲自操作真实界面；单测、合成样本和预填参数不能代替真实旅程。

## 本轮实现与影响面

- iPhone：`DeviceDebugConnection.swift` 独立 Bonjour 发现、确认轮询、Keychain 记住授权、取消请求和临时断网重试。`DeviceDebugView.swift` 删除粘贴入口，授权前禁用开始共享；录屏仍由 `DeviceDebugController` 显式触发。
- Mac：`device_debug.html` 自动取得仅本机可读取的管理凭据，显示待确认请求、允许/拒绝、设备撤销。保留会话、画面、事件、样本与 Codex 报告。
- 服务：`device_debug_pairing.py` 分离 Mac 管理凭据与设备上传凭据；设备只可上传自己创建的会话。授权存哈希，文件权限600；拒绝、过期、取消和撤销均显式返回。
- 发现：`device_debug_discovery.py` 使用 ComputerName 与 LocalHostName，不从可能为 IP 的 socket hostname 推导 `.local`。
- iOS 安装声明：新增项目根 `AppInfo.plist`，由 Debug/Release 同一文件提供两个 Bonjour 类型及本地网络 ATS 声明。实查旧 INFOPLIST_KEY 设置未写入产物；本次消除无效副本，并验证最终 App Info.plist。
- 启动器、README 已同步普通连接步骤。CLI 仍从本机权限600的 `build/device-debug/pairing.json` 读取管理凭据；凭据不显示给普通用户。
- 依赖沿用已有 zeroconf、Keychain/Bonjour 系统 API；无新增包。执行 setup core profile 验证：沙箱首轮 harness 失败；完整本地权限重跑成功，426 项后端测试及 harness 编译通过（`/private/tmp/vqasee-simple-connect-setup-final.log`）。

## 角色审查与任务

| 角色 | 结论/主责 | 验收 |
|---|---|---|
| 乔布斯 | 取消密钥搬运，实际看页面并操作请求拒绝 | 用户入口无需地址/密钥；真机旅程保留未验收 |
| 罗根 | 有条件同意；发现取消占满请求槽、单次网络错误放弃整轮 | 已补请求取消、pending与频率分离、短暂断网重试；凭据作用域测试 |
| 思余 | 连接、确认、录屏应有明确状态与操作 | Mac 实际页面与 iOS UI 场景复核 |
| 全麦 | 本轮无模型修改；接口合成数据不证明模型或真机能力 | Codex 报告仍需真实证据，确认根因必须引用证据 |

联调接口：Bonjour `_vqasee-debug._tcp` → 公共 pairing request/secret轮询 → Mac管理批准 → 设备凭据 → session创建与上传 → Mac/CLI读取。数据未到达时保留未知；收到资料不自动表示根因确认。

## 本轮实际发现并修复的问题

1. Mac 展示名称为 IP，且可能派生错误 `.local` 主机；修正系统名称来源后，实际页面显示电脑名。
2. 构建产物没有 NSBonjourServices；改用显式 plist 并核对真实设备 App 内数组与 ATS。
3. 已决定/取消请求仍占 pending 配额；增加持请求 secret 的取消接口、独立短窗频率限制。
4. 取消与请求创建并发时可能遗留请求；手机发现已取消后发送服务端删除。
5. 一次临时轮询网络错误会丢掉已批准机会；有效期内重试，界面显示连接中断。
6. 思余二次审查补齐：无结果10秒说明恢复步骤；提供本地网络设置入口；每次开始前重新验证授权，撤销则清Keychain；共享中重开页面显示实际接收Mac；同一Mac保留“复测上次场景”开关并传入previous_session_id（当前App进程内上次会话）。

## 验收记录

| 场景 | 环境与输入来源 | 状态 |
|---|---|---|
| 管理bootstrap、跨站拒绝、设备作用域、撤销、拒绝/过期/取消、持久哈希 | FastAPI TestClient，协议合成输入 | 34 项相关接口/CLI测试通过；不是双端真机 |
| 打开等待页、电脑名、收到请求、点击拒绝、请求消失 | 实际9001页面；标注“接口测试设备（非iPhone真机）”的HTTP客户端 | 浏览器功能验收通过；截图 `/private/tmp/vqasee-simple-connect-mac.png` |
| iPhone App构建、签名、Bonjour数组、ATS | Xcode iphoneos实际产物 | 构建通过；最终日志 `/private/tmp/vqasee-simple-connect-device-final.log` |
| 无粘贴框、授权前禁用共享、样本默认关闭、返回菜单 | 模拟器XCUITest；明确布局fixture | 首轮runner启动失败，保留失败；重启后单实例及最终复测通过。最终结果 `/private/tmp/vqasee-simple-connect-ui-final.xcresult`，仅证明界面fixture场景 |
| 真机发现→允许→开始录屏→收到画面/事件→后台停止→重新连接 | 用户iPhone17与Mac | 待用户安装实测；不得标为通过 |
| Codex读取真实会话→分析回写→修复→同场景复测 | 真实会话 | 尚无本轮真机证据，仍未闭环 |

## 用户最短验证路径

1. 在 Xcode 安装当前工作区 App。Mac运行 `bash start_device_debug.sh`（已运行时直接打开9001页面）。两台设备同一Wi-Fi。
2. 手机菜单→真机调试，允许本地网络，选择电脑名称。
3. Mac核对两边数字，点击允许连接；手机点击开始共享 App 屏幕，并同意系统提示。
4. 完成关闭设置页、打开菜单、记住位置或采集样本等实际操作；Mac应显示画面和事件。样本需单独打开原始测试样本共享。
5. 切到后台应停止共享；回到App重新开始。下次选同一Mac应记住授权。
6. 告诉Codex“已测试”或大致操作时间，Codex读取本机会话；无需复制密钥或导出资料。

当前限制：可信局域网HTTP未加密；Mac改名会重新请求授权。尚无扫码兜底或USB专用传输。暂无自主后台Codex进程，需当前Codex会话读取并回写分析。

经验已写入 `.agents/skills/vqasee-product-review/SKILL.md` 的“乔布斯亲自执行用户验收”；真实产物检查必须覆盖配置消费者，构建设置看似存在不表示产物有效。
