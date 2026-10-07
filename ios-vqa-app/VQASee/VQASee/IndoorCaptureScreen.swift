import SwiftUI

struct IndoorCaptureStatusBar: View {
    @EnvironmentObject private var deviceDebug: DeviceDebugController
    @ObservedObject var recorder: IndoorCaptureRecorder
    var body: some View {
        if recorder.state != .idle {
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text(recorder.state == .recording ? "正在采样 · 剩余 \(recorder.remaining) 秒" : "正在准备或保存…").font(.headline)
                    Spacer()
                    Button("停止") {
                        deviceDebug.recordEvent("capture_stop_pressed")
                        Task { await recorder.finish() }
                    }.disabled(recorder.state != .recording)
                }
                Text("每秒1张 · 非视频 · 已存\(recorder.savedCount)张 · 未采样\(recorder.missedCount)次").font(.caption)
                if recorder.state == .recording, recorder.phase < 2 {
                    Button(recorder.phase == 0 ? "开始变化" : "变化已完成") {
                        deviceDebug.recordEvent("capture_transition_pressed", details: ["phase": String(recorder.phase)])
                        Task { await recorder.markTransition() }
                    }
                }
                Text(recorder.message).font(.caption)
            }.padding(12).background(.red.opacity(0.2), in: RoundedRectangle(cornerRadius: 16))
        } else if let error = recorder.error {
            Text(error).font(.caption).padding(12).background(.orange.opacity(0.2), in: RoundedRectangle(cornerRadius: 12))
        }
    }
}

struct IndoorCaptureScreen: View {
    @EnvironmentObject private var deviceDebug: DeviceDebugController
    @ObservedObject var controller: IndoorMemoryController
    @ObservedObject var recorder: IndoorCaptureRecorder
    @State private var event: IndoorCaptureEvent = .unchanged
    @State private var name = ""
    @State private var deleting: UUID?
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            List {
                Section("记录一次变化实验") {
                    Text(deviceDebug.isActive && deviceDebug.shareOriginalSamples
                         ? "本次已允许向 Mac 共享原始样本。照片、相机位置与可用深度将保存在手机并发送到配对的 Mac。"
                         : "仅保存在这台手机，可能拍到周围的人与环境。保存照片、相机位置与可用深度，不上传。")
                        .font(.subheadline)
                    Picker("计划观察什么", selection: $event) {
                        ForEach(IndoorCaptureEvent.allCases) { Text($0.rawValue).tag($0) }
                    }
                    TextField("物品名字（可选）", text: $name)
                    Text("每秒1张，最多30秒。先观察原位，再点“开始变化”和“变化已完成”。事件标签不是算法判断。")
                        .font(.caption).foregroundStyle(.secondary)
                    TimelineView(.periodic(from: .now, by: 0.5)) { _ in
                        VStack(alignment: .leading, spacing: 10) {
                            Button("开始采样") {
                                deviceDebug.recordEvent("capture_start_pressed", details: ["availability": controller.captureAvailability.message])
                                Task {
                                    await controller.beginCapture(event: event, name: name)
                                    deviceDebug.recordEvent("capture_start_result", details: ["state": String(describing: recorder.state), "error": recorder.error ?? ""])
                                    if recorder.state == .recording { dismiss() }
                                }
                            }.disabled(!controller.canStartCapture || recorder.state != .idle)
                                .accessibilityIdentifier("indoor-capture-start")
                            Text(controller.captureAvailability.message)
                                .font(.caption).foregroundStyle(.secondary)
                                .accessibilityIdentifier("indoor-capture-availability")
                            if controller.captureAvailability == .permissionRequired {
                                Button("打开系统设置") {
                                    if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                                }
                            } else if [.paused, .waitingForFrame, .restoring].contains(controller.captureAvailability) {
                                Button("重新启动相机") { Task { await controller.restartCaptureCamera() } }
                                    .disabled(recorder.state != .idle)
                                    .accessibilityIdentifier("indoor-capture-restart")
                            }
                        }
                    }
                    if let error = recorder.error { Text(error).foregroundStyle(.orange) }
                }
                Section("本机样本") {
                    if recorder.entries.isEmpty { Text("尚无样本").foregroundStyle(.secondary) }
                    ForEach(recorder.entries) { entry in
                        if let manifest = entry.manifest {
                            NavigationLink {
                                IndoorCaptureReplay(manifest: manifest, issue: entry.issue, store: recorder.store)
                            } label: {
                                VStack(alignment: .leading) {
                                    Text("\(manifest.objectName) · \(manifest.event.rawValue)")
                                    Text("\(manifest.startedAt.formatted(date: .abbreviated, time: .shortened)) · \(manifest.frames.count)帧").font(.caption)
                                    Text(entry.issue ?? manifest.endReason ?? "采集中").font(.caption).foregroundStyle(.secondary)
                                }
                            }.swipeActions { Button("删除", role: .destructive) { deleting = entry.id } }
                        } else {
                            VStack(alignment: .leading) {
                                Text("无法读取的样本")
                                Text(entry.issue ?? "格式错误").font(.caption).foregroundStyle(.orange)
                                Button("删除损坏样本", role: .destructive) { deleting = entry.id }
                            }
                        }
                    }
                }
            }
            .navigationTitle("采集与回放")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() }.accessibilityIdentifier("indoor-capture-done") } }
            .task { await controller.prepareCapturePreview(); await recorder.reload() }
            .confirmationDialog("删除这一段的全部照片、深度和标注？", isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } })) {
                Button("删除样本", role: .destructive) {
                    if let id = deleting { Task { await recorder.delete(id) } }
                    deleting = nil
                }
            }
        }
    }
}

struct IndoorCaptureReplay: View {
    @EnvironmentObject private var deviceDebug: DeviceDebugController
    @State private var resending = false
    @State private var resendStatus = ""
    let manifest: IndoorCaptureManifest
    let issue: String?
    let store: IndoorCaptureStore
    @State private var index = 0
    @State private var image: UIImage?
    @State private var error: String?
    @State private var verified = false
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("历史样本 · 每秒1张，非实时相机").font(.headline)
                if deviceDebug.isActive && deviceDebug.shareOriginalSamples {
                    Button(resending ? "正在发送整段样本…" : "将这段样本发送到 Mac") {
                        resending = true
                        Task {
                            let success = await DeviceDebugEvidenceBridge.resend(manifest, store: store, debug: deviceDebug)
                            resendStatus = success ? "整段样本与清单已送达 Mac" : "未完整送达，本机样本仍保留；请检查共享连接后重试"
                            resending = false
                        }
                    }.disabled(resending || deviceDebug.uploadingAttachment)
                }
                if !resendStatus.isEmpty { Text(resendStatus).font(.caption) }
                Text("计划事件：\(manifest.event.rawValue)；不是逐帧真值").font(.subheadline)
                if let issue { Text(issue).foregroundStyle(.orange) }
                Text("结束：\(manifest.endReason ?? "未正常结束") · 忙碌未采\(manifest.missedBusy) · 无帧\(manifest.missingFrames) · 旧帧\(manifest.duplicateFrames) · 失败\(manifest.writeFailures)").font(.caption)
                if manifest.frames.isEmpty { Text("本段没有成功保存的帧，不能用于评测。") }
                else {
                    let frame = manifest.frames[index]
                    if let image { Image(uiImage: image).resizable().scaledToFit() }
                    if let error { Text(error).foregroundStyle(.orange) }
                    Text("第\(index + 1)/\(manifest.frames.count)帧 · +\(String(format: "%.2f", frame.timestamp - manifest.startedUptime))秒")
                    Text(manifest.phase(at: frame.timestamp)).font(.headline)
                    HStack {
                        Button("上一帧") { index -= 1 }.disabled(index == 0)
                        Spacer()
                        Button("下一帧") { index += 1 }.disabled(index + 1 == manifest.frames.count)
                    }
                    DisclosureGroup("数据完整性与定位详情") {
                        VStack(alignment: .leading, spacing: 8) {
                            Text(verified ? "本帧文件校验通过（不代表内容正确）" : "尚未通过完整性检查")
                            Text("跟踪：\(frame.tracking)；非 normal 位姿不可当作可靠定位")
                            Text("照片：\(frame.imageWidth)×\(frame.imageHeight)，传感器原方向")
                            Text(frame.depthSHA256 == nil ? "深度不可用：\(frame.depthUnavailableReason ?? "未提供")" : "深度已保存，单位米；置信图\(frame.confidenceSHA256 == nil ? "未提供" : "已保存")")
                            Text("内参：\(frame.intrinsics.count)项；位姿：\(frame.cameraToWorld.count)项；尚未标注实例轮廓和可见性真值")
                        }.font(.caption)
                    }
                }
            }.padding(20)
        }
        .navigationTitle("样本回放")
        .task(id: index) {
            image = nil; error = nil; verified = false
            guard !manifest.frames.isEmpty else { return }
            do {
                let packet = try await store.readFrame(id: manifest.id, index: index)
                guard !Task.isCancelled else { return }
                guard let decoded = UIImage(data: packet.jpeg) else { throw IndoorCaptureError.damaged("照片无法解码") }
                // Stored pixels stay in sensor coordinates. Display orientation is explicit.
                let orientation: UIImage.Orientation
                switch packet.metadata.interfaceOrientation {
                case UIInterfaceOrientation.landscapeLeft.rawValue: orientation = .up
                case UIInterfaceOrientation.landscapeRight.rawValue: orientation = .down
                case UIInterfaceOrientation.portraitUpsideDown.rawValue: orientation = .left
                default: orientation = .right
                }
                if let cg = decoded.cgImage { image = UIImage(cgImage: cg, scale: 1, orientation: orientation) }
                verified = true
            } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
        }
    }
}
