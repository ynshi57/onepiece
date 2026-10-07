import SwiftUI

struct DeviceDebugView: View {
    @ObservedObject var controller: DeviceDebugController
    @Environment(\.dismiss) private var dismiss
    @StateObject private var connection = DeviceDebugConnection()
    @State private var linkRetest = false
    var body: some View {
        NavigationStack {
            Form {
                Section {
                    if controller.isLayoutPreview {
                        Text("UI 布局示例 · 未联网、未录屏").foregroundStyle(.orange)
                    }
                    Text("让 Mac 看见你在 VQASee 中看到的画面，帮助定位真机问题。")
                    Text("仅共享此 App 的画面，最多每秒一张，不是连续视频。不录音、不启用前置摄像头；系统弹窗与其他 App 可能不在画面中。")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Section("接收画面的 Mac") {
                    Text(controller.isActive ? "正在共享至 \(controller.receivingMacName)" : connection.message).accessibilityIdentifier("device-debug-connection")
                    if !connection.code.isEmpty {
                        Text(connection.code).font(.largeTitle.monospacedDigit()).accessibilityIdentifier("device-debug-code")
                    }
                    if controller.isActive {
                        Label(controller.receivingMacName, systemImage: "desktopcomputer")
                    } else if let mac = connection.selected, connection.credential != nil {
                        Label(mac.name, systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                    } else {
                        ForEach(connection.macs) { mac in
                            Button { connection.select(mac) } label: { Label(mac.name, systemImage: "desktopcomputer") }
                                .disabled(connection.waiting || controller.isActive)
                        }
                    }
                    if connection.waiting {
                        Button("取消连接") { connection.cancel() }
                    } else if !controller.isActive {
                        if connection.selected != nil { Button("重新选择 Mac") { connection.cancel(); connection.retry() } }
                        else { Button("重新查找") { connection.retry() } }
                    }
                    Text("Mac 打开真机调试页面，两台设备连接同一 Wi-Fi。首次连接只需在 Mac 允许一次。")
                        .font(.footnote).foregroundStyle(.secondary)
                    Text("仅在信任的 Wi-Fi 使用；当前局域网连接未加密。")
                        .font(.footnote).foregroundStyle(.secondary)
                    if !controller.isActive {
                        Button("打开本地网络权限设置") {
                            if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                        }
                    }
                }
                Section("共享范围") {
                    Toggle("同时允许发送原始测试样本", isOn: $controller.shareOriginalSamples)
                        .accessibilityIdentifier("device-debug-samples")
                        .onChange(of: controller.shareOriginalSamples) { _, enabled in
                            controller.recordEvent("sample_sharing_consent_changed", details: ["enabled": String(enabled)])
                        }
                    Text("开启后，会发送所选物品的参考照片与轮廓；测试采集可发送原始照片、位姿或深度资料到 Mac。屏幕画面和样本可能包含周围的人、环境及个人信息。关闭不会删除已经送达 Mac 的资料。")
                        .font(.footnote).foregroundStyle(.secondary)
                    Text("Mac 会保存收到的调试资料。停止共享后，可在 Mac 调试页面删除。")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Section("本次连接") {
                    Text(controller.status).accessibilityIdentifier("device-debug-status")
                    LabeledContent("已送达画面", value: "\(controller.uploadedFrames) 张")
                    if let date = controller.lastUploadAt {
                        LabeledContent("最近送达", value: date.formatted(date: .omitted, time: .standard))
                    }
                    if controller.eventFailures > 0 {
                        Text("\(controller.eventFailures) 条操作事件未送达，操作记录不完整")
                            .foregroundStyle(.orange)
                    }
                    if !controller.attachmentStatus.isEmpty { Text(controller.attachmentStatus).font(.footnote) }
                    if controller.attachmentFailures > 0 {
                        Text("\(controller.attachmentFailures) 份样本资料未送达，Mac 证据不完整")
                            .foregroundStyle(.orange)
                    }
                    if controller.isActive {
                        Button("停止共享", role: .destructive) { Task { await controller.stop() } }
                            .accessibilityIdentifier("device-debug-stop")
                    } else {
                        if controller.sessionID != nil, connection.selected?.url == controller.receivingMacURL {
                            Toggle("复测上次场景", isOn: $linkRetest)
                                .accessibilityIdentifier("device-debug-retest")
                        }
                        Button("开始共享 App 屏幕") {
                            guard let mac = connection.selected else { return }
                            let previous = linkRetest && mac.url == controller.receivingMacURL ? controller.sessionID : nil
                            Task {
                                guard let token = await connection.validatedCredential() else { return }
                                await controller.start(baseURL: mac.url, pairingToken: token, previousSessionID: previous, macName: mac.name)
                            }
                        }
                        .disabled(connection.credential == nil || connection.waiting || controller.state == .stopping)
                        .accessibilityIdentifier("device-debug-start")
                    }
                    Text("进入后台会自动停止；关闭这个设置页仍会继续共享，主画面会显示共享状态。")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            .task { if !controller.isLayoutPreview { connection.startDiscovery() } }
            .onDisappear { connection.stopDiscovery(); connection.cancel() }
            .scrollDismissesKeyboard(.interactively)
            .navigationTitle("共享屏幕到 Mac")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("完成") { dismiss() }.accessibilityIdentifier("device-debug-done")
                }
            }
        }
    }
}
