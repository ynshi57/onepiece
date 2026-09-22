# 同 Wi‑Fi Mac 后端自动发现

## 结论

用户无需手动填写 `ws://` 地址。iPhone 与 Mac 连同一 Wi‑Fi 时，点「开始视觉辅助」会自动发现 Mac 后端并连接。

## 机制（双通道并行）

1. **Bonjour**（`_vqasee._tcp`）：Mac 启动 `bash ./start_backend.sh` 后广播；iPhone App 启动即开始 browse。
2. **/24 健康探测**：根据 iPhone 在 `en0` 上的 IPv4 扫描同网段，对 `http://<host>:9000/health` 并发探测。

点「开始」时两条通道**并行**跑最多约 10 秒，谁先找到有效后端就自动填入地址。

## 本轮修复

| 问题 | 修复 |
|------|------|
| Bonjour 只等 ~3s 再扫网，经常超时 | 并行等待 Bonjour + 子网扫描，Bonjour 等待 10s |
| Mac VPN 导致广播错误 IP | `_get_lan_ip()` 优先 `en0`（Wi‑Fi），TXT 增加 `ip=` |
| 子网扫到地址后误触发「用户手动固定」 | 改用 `applyDiscoveredURL`，不 pin |
| `/health` 0.35s 超时偏短 | 提到 0.8s |

## 用户验证步骤

1. Mac：`bash ./start_backend.sh`，日志应含 `Bonjour advertised: ... ip=192.168.x.x`
2. iPhone：设置 → 隐私 → 本地网络 → 允许 VQASee
3. 同一 Wi‑Fi，打开 App → 开始视觉辅助
4. 状态应显示「已发现 Mac 后端：…」并连接成功

## 仍可能失败的情况

- 路由器 **AP 隔离**（禁止设备互访）：Bonjour 与 TCP 扫描均无效，需关隔离或改用手动 IP / relay
- Mac 防火墙拦截 9000 端口
- 未授予本地网络权限

## 变更文件

- `server-vqa/app/discovery.py`
- `ios-vqa-app/VQASee/VQASee/PureHelpers.swift`
- `ios-vqa-app/VQASee/VQASee/BonjourDiscovery.swift`
- `ios-vqa-app/VQASee/VQASee/StreamingViewModel.swift`
- `ios-vqa-app/VQASee/VQASee/SettingsView.swift`
- 测试：`server-vqa/tests/test_discovery.py`、`VQASeeTests.swift`

更新时间: 2026-09-21 18:10
