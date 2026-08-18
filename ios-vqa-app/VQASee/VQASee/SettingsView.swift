import SwiftUI

// MARK: - SettingsView
//
// The settings sheet. Everything that isn't part of the immersive main screen
// lives here: voice-broadcast toggle, model picker, multi-backend picker, the
// advanced server configuration (the four text fields moved verbatim from the old
// ContentView, together with their @FocusState / keyboard "完成" toolbar logic),
// debug text, and the hotspot help. Being the only place with text input, it's
// also the only place that has to manage the keyboard.

struct SettingsView: View {
    @ObservedObject var viewModel: StreamingViewModel

    /// Which advanced-settings text field currently owns the keyboard. Set to nil
    /// to dismiss the keyboard (via "完成", the return key, or an interactive drag).
    private enum Field: Hashable {
        case server
        case pairingToken
        case workerID
        case clientID
    }
    @FocusState private var focusedField: Field?

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section("语音与模型") {
                    Toggle("语音播报", isOn: $viewModel.isVoiceEnabled)

                    Text("播报状态：\(viewModel.voiceStatusText)")
                        .font(Theme.Typography.caption)
                        .foregroundStyle(.secondary)

                    HStack {
                        Text("本地模型")
                        Spacer()
                        if viewModel.isRefreshingRuntimeStatus {
                            ProgressView()
                        } else {
                            Button("刷新") {
                                viewModel.refreshRuntimeStatus()
                            }
                        }
                    }

                    Text(viewModel.runtimeStatusText)
                        .font(Theme.Typography.caption)
                        .foregroundStyle(.secondary)

                    HStack {
                        Text("感知配置版本")
                        Spacer()
                        Text("v\(viewModel.perceptionConfigVersion)")
                            .foregroundStyle(viewModel.perceptionConfigUsingFallback ? Color.orange : .secondary)
                    }
                    Text(viewModel.perceptionConfigText)
                        .font(Theme.Typography.caption)
                        .foregroundStyle(viewModel.perceptionConfigUsingFallback ? Color.orange : .secondary)

                    let modelOptions = viewModel.selectableModelOptions
                    if modelOptions.count > 1 {
                        Picker("模型", selection: $viewModel.selectedModel) {
                            ForEach(modelOptions) { model in
                                Text(model.title).tag(model)
                            }
                        }
                        .pickerStyle(.segmented)

                        Text(viewModel.selectedModel.hint)
                            .font(Theme.Typography.caption)
                            .foregroundStyle(.secondary)
                    } else if let onlyModel = modelOptions.first {
                        LabeledContent("当前实际模型", value: onlyModel.title)
                    } else {
                        Text("本地模型不可用，当前结果仅适合连通性测试。")
                            .font(Theme.Typography.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                if viewModel.showServerPicker && viewModel.discoveredServers.count >= 2 {
                    Section("选择 Mac 后端") {
                        ServerPickerView(
                            servers: viewModel.discoveredServers,
                            selectedURLString: viewModel.serverURLInput,
                            onSelect: { viewModel.selectServer($0) }
                        )
                    }
                }

                Section {
                    Text("请先开启 iPhone 个人热点，并让 Mac 连接该热点；Mac 上运行后端后，App 会自动发现并连接。")
                        .font(Theme.Typography.caption)
                        .foregroundStyle(.secondary)
                }

                Section("诊断上传") {
                    Toggle("上传诊断帧", isOn: $viewModel.isDiagnosticRecordingEnabled)
                    Text(viewModel.diagnosticRecordingText)
                        .font(Theme.Typography.caption)
                        .foregroundStyle(.secondary)
                    Text("会把压缩画面和本地模型输出发送到当前连接的 Mac 后端，用于分析误检、漏检和 overlay 对齐。仅测试时开启。")
                        .font(Theme.Typography.caption)
                        .foregroundStyle(.secondary)
                }

                Section("高级设置") {
                    Text("通常不需要填写。自动发现失败时才手动输入。")
                        .font(Theme.Typography.caption)
                        .foregroundStyle(.secondary)

                    TextField("ws://mac-host.local:9000/ws/signaling", text: $viewModel.serverURLInput)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .focused($focusedField, equals: .server)
                        .submitLabel(.done)
                        .onSubmit { focusedField = nil }

                    TextField("relay pairing token (only for Relay mode)", text: $viewModel.pairingTokenInput)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .focused($focusedField, equals: .pairingToken)
                        .submitLabel(.done)
                        .onSubmit { focusedField = nil }

                    TextField("worker id", text: $viewModel.workerIDInput)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .focused($focusedField, equals: .workerID)
                        .submitLabel(.done)
                        .onSubmit { focusedField = nil }

                    TextField("client id", text: $viewModel.clientIDInput)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .focused($focusedField, equals: .clientID)
                        .submitLabel(.done)
                        .onSubmit { focusedField = nil }

                    Text("调试结果：\(viewModel.debugText)")
                        .font(Theme.Typography.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .scrollDismissesKeyboard(.interactively)
            .navigationTitle("设置")
            .navigationBarTitleDisplayMode(.inline)
            .onAppear {
                viewModel.refreshRuntimeStatus()
            }
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("完成") {
                        focusedField = nil
                        dismiss()
                    }
                }
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("完成") {
                        focusedField = nil
                    }
                }
            }
        }
    }
}
