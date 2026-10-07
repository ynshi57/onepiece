import SwiftUI

/// App root. Owns the single `StreamingViewModel` and presents the immersive
/// `AssistanceScreen`, with all configuration living in a `SettingsView` sheet.
/// Deliberately thin — layout lives in AssistanceScreen, controls in components.
struct OutdoorAssistanceRoot: View {
    let onIndoor: () -> Void
    @State private var isLeaving = false
    @StateObject private var viewModel = StreamingViewModel()
    @State private var showingSettings = false

    var body: some View {
        AssistanceScreen(viewModel: viewModel, showingSettings: $showingSettings)
            .safeAreaInset(edge: .top) {
                Button("返回物品记忆") {
                    isLeaving = true
                    Task {
                        await viewModel.suspendForIndoorMemory()
                        onIndoor()
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(isLeaving)
            }
            .sheet(isPresented: $showingSettings) {
                SettingsView(viewModel: viewModel)
            }
    }
}

/// Indoor and outdoor cameras are created on demand, never side by side.
struct ContentView: View {
    @State private var outdoor = false
    @StateObject private var indoorController = IndoorMemoryController()
    @StateObject private var deviceDebug = DeviceDebugController()
    @State private var showingDeviceDebug = false
    @State private var evidenceBridge: DeviceDebugEvidenceBridge?
    var body: some View {
        Group {
            if outdoor {
                OutdoorAssistanceRoot(onIndoor: { outdoor = false })
            } else {
                IndoorMemoryScreen(controller: indoorController, onOutdoor: { outdoor = true },
                                   onDeviceDebug: { showingDeviceDebug = true })
            }
        }
        .environmentObject(deviceDebug)
        .background(DeviceDebugTouchObserver(debug: deviceDebug).frame(width: 0, height: 0))
        .safeAreaInset(edge: .bottom) {
            if deviceDebug.isActive || deviceDebug.state == .failed || deviceDebug.state == .stopping {
                HStack {
                    Button { showingDeviceDebug = true } label: {
                        Label(deviceDebug.status, systemImage: "record.circle").font(.caption).lineLimit(2)
                    }
                    Spacer()
                    if deviceDebug.isActive {
                        Button("这里有问题") { deviceDebug.recordEvent("problem_marked", details: debugStatus) }
                            .font(.caption.bold())
                        Button("停止") { Task { await deviceDebug.stop() } }.font(.caption)
                    }
                }.padding(10).background(.regularMaterial)
            }
        }
        .sheet(isPresented: $showingDeviceDebug) { DeviceDebugView(controller: deviceDebug) }
        .task {
            let bridge = DeviceDebugEvidenceBridge(debug: deviceDebug)
            evidenceBridge = bridge
            indoorController.capture.onSavedPacket = { packet, manifest in bridge.packet(packet, manifest: manifest) }
            indoorController.capture.onFinishedManifest = { manifest in bridge.manifest(manifest) }
            if deviceDebug.isLayoutPreview { showingDeviceDebug = true }
            #if DEBUG
            if ProcessInfo.processInfo.environment["VQASEE_DEBUG_PAIRING"] != nil { showingDeviceDebug = true }
            #endif
            while !Task.isCancelled {
                if deviceDebug.isActive { deviceDebug.recordEvent("app_status", details: debugStatus) }
                try? await Task.sleep(for: .seconds(2))
            }
        }
        .onChange(of: outdoor) { _, value in deviceDebug.recordEvent("mode_changed", details: ["mode": value ? "outdoor" : "indoor"]) }
        .onChange(of: indoorController.selectedID) { _, _ in
            if let record = indoorController.selected { evidenceBridge?.reference(record) }
        }
        .onChange(of: deviceDebug.shareOriginalSamples) { _, enabled in
            if enabled, let record = indoorController.selected { evidenceBridge?.reference(record) }
        }
        .onChange(of: deviceDebug.state) { _, state in
            if state == .sharing, let record = indoorController.selected { evidenceBridge?.reference(record) }
        }
    }
    private var debugStatus: [String: String] {
        if outdoor {
            return ["mode": "outdoor", "camera_configuration": "rear_wide_angle",
                    "telemetry_scope": "indoor_diagnostics_not_applicable",
                    "event_failures": String(deviceDebug.eventFailures),
                    "attachment_failures": String(deviceDebug.attachmentFailures)]
        }
        return ["mode": "indoor", "camera_configuration": "rear_world_tracking_no_face_tracking",
         "screen_awake": String(UIApplication.shared.isIdleTimerDisabled),
         "capture_availability": indoorController.captureAvailability.message,
         "live_observation": indoorController.liveObservation.rawValue,
         "live_observation_message": indoorController.liveObservation.message,
         "live_appearance_message": indoorController.liveAppearanceMessage,
         "live_appearance_evidence": indoorController.liveAppearanceEvidence.description,
         "live_observation_evidence": indoorController.liveObservationEvidence.description,
         "spatial_source": indoorController.selected?.spatialSource ?? "none",
         "capture_state": String(describing: indoorController.capture.state),
         "saved_frames": String(indoorController.capture.savedCount),
         "missed_frames": String(indoorController.capture.missedCount),
         "event_failures": String(deviceDebug.eventFailures),
         "attachment_failures": String(deviceDebug.attachmentFailures),
         "diagnostics": indoorController.diagnosticSummary,
         "error": indoorController.error ?? indoorController.capture.error ?? "",
         "app_version": Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "unknown",
         "build": Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "unknown"]
    }
}

#Preview {
    ContentView()
}
