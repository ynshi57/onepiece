import SwiftUI

/// App root. Owns the single `StreamingViewModel` and presents the immersive
/// `AssistanceScreen`, with all configuration living in a `SettingsView` sheet.
/// Deliberately thin — layout lives in AssistanceScreen, controls in components.
struct ContentView: View {
    @StateObject private var viewModel = StreamingViewModel()
    @State private var showingSettings = false
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        AssistanceScreen(viewModel: viewModel, showingSettings: $showingSettings)
            .sheet(isPresented: $showingSettings) {
                SettingsView(viewModel: viewModel)
            }
            .onChange(of: scenePhase) { _, phase in
                // Re-assert wake policy after SceneDelegate transitions; release when backgrounded.
                viewModel.refreshScreenWakePolicy(isForeground: phase == .active)
            }
    }
}

#Preview {
    ContentView()
}
