import SwiftUI

// MARK: - AssistanceScreen
//
// The immersive, single-screen layout: the camera fills the whole screen as the
// hero, and status / answer / controls float on top as translucent glass. There is
// NO page ScrollView — the only thing that ever scrolls is the answer text inside
// AnswerPanel, capped at a fraction of the screen height so the fixed controls
// (mode bar, press-to-talk, start/stop) are always reachable without scrolling.
//
// Business logic is untouched: the start/stop button drives the same
// requestPermissions() + startStreaming()/stopStreaming() calls as before.

struct AssistanceScreen: View {
    @ObservedObject var viewModel: StreamingViewModel
    @Binding var showingSettings: Bool

    private var isActive: Bool {
        switch viewModel.streamStatus {
        case .streaming, .preparing:
            return true
        case .idle, .error:
            return false
        }
    }

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                CameraPreview(session: viewModel.captureSession)
                    .ignoresSafeArea()
                    // The raw preview layer carries no information for a VoiceOver
                    // user — the AnswerPanel conveys the scene — so skip it in the
                    // rotor instead of surfacing an unlabeled element.
                    .accessibilityHidden(true)

                CameraRiskOverlay(
                    signal: viewModel.localPerceptionSignal,
                    mode: viewModel.selectedMode,
                    isActive: isActive
                )
                .ignoresSafeArea()
            }
            .overlay(alignment: .top) {
                topBar
                    .padding(.horizontal, Theme.Spacing.lg)
                    .padding(.top, Theme.Spacing.sm)
            }
            .safeAreaInset(edge: .bottom) {
                bottomStack(maxAnswerHeight: proxy.size.height * 0.18)
                    .padding(.horizontal, Theme.Spacing.lg)
                    .padding(.bottom, Theme.Spacing.sm)
            }
        }
    }

    // MARK: Top bar — status pill + settings gear

    private var topBar: some View {
        HStack(alignment: .top) {
            StatusPill(
                status: viewModel.streamStatus,
                connectionText: viewModel.nearbyServerText
            )
            Spacer()
            Button {
                showingSettings = true
            } label: {
                Image(systemName: "gearshape.fill")
                    .font(.title3)
                    .padding(Theme.Spacing.md)
                    .background {
                        Circle().fill(.regularMaterial)
                    }
            }
            .buttonStyle(.plain)
            .accessibilityLabel("设置")
        }
    }

    // MARK: Bottom control cluster

    private func bottomStack(maxAnswerHeight: CGFloat) -> some View {
        VStack(spacing: Theme.Spacing.md) {
            AnswerPanel(
                summary: viewModel.summaryText,
                spatial: viewModel.spatialText,
                riskLevel: viewModel.currentRiskLevel,
                risk: viewModel.riskText,
                action: viewModel.actionText,
                latency: viewModel.latencyText,
                isProcessing: viewModel.isProcessing,
                maxContentHeight: maxAnswerHeight
            )

            if !viewModel.speechStatusText.isEmpty {
                HStack(spacing: Theme.Spacing.sm) {
                    Image(systemName: viewModel.isRecording ? "waveform" : "text.bubble")
                    Text(viewModel.speechStatusText)
                        .lineLimit(2)
                }
                    .font(viewModel.isRecording ? Theme.Typography.risk : Theme.Typography.caption)
                    .foregroundStyle(viewModel.isRecording ? .white : .secondary)
                    .padding(.horizontal, Theme.Spacing.md)
                    .padding(.vertical, Theme.Spacing.sm)
                    .background {
                        if viewModel.isRecording {
                            Capsule().fill(Theme.riskDanger.opacity(0.92))
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
            }

            // Press-to-talk. @GestureState lives inside the dedicated subview so the
            // press survives viewModel republishes (see PressToTalkButton for the
            // "按住后马上又变回按住说话" bug this avoids).
            PressToTalkButton(
                isRecording: viewModel.isRecording,
                inputLevel: viewModel.speechInputLevel,
                onStart: { viewModel.startVoiceQuestion() },
                onStop: { viewModel.stopVoiceQuestion() }
            )
            .accessibilityHint("按住说出你的问题，松开后优先回答")
            .accessibilityAddTraits(.startsMediaSession)

            startStopButton

            if let errorText = viewModel.errorText {
                Text(errorText)
                    .font(Theme.Typography.caption)
                    .foregroundStyle(Theme.riskDanger)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: .infinity)
            }
        }
    }

    @ViewBuilder
    private var startStopButton: some View {
        if isActive {
            Button {
                Task {
                    await viewModel.stopStreaming()
                }
            } label: {
                Label("停止", systemImage: "stop.fill")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(Theme.riskDanger)
            .controlSize(.large)
        } else {
            Button {
                viewModel.requestPermissions()
                Task {
                    await viewModel.startStreaming()
                }
            } label: {
                Label("开始观察", systemImage: "eye.fill")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
        }
    }
}
