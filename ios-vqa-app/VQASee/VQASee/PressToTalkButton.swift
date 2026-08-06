import SwiftUI
import UIKit

/// Press-and-hold "按住说话" control.
///
/// ## Why the gesture is UIKit, not SwiftUI
///
/// The obvious SwiftUI approach — a `DragGesture(minimumDistance: 0)` tracked with
/// `@GestureState` — does NOT reliably hold here, and that is the "无法按住 /
/// 按住后马上又变回按住说话" bug users kept hitting.
///
/// The moment the press begins, `onStart()` calls into the view model, which flips
/// several `@Published` values at once (`isRecording`, `speechStatusText`, …). That
/// republishes the parent `AssistanceScreen`, whose body *structurally inserts* a
/// status `Text` directly above this button (`if !speechStatusText.isEmpty { … }`).
/// A structural change to the gesture owner's siblings mid-press causes SwiftUI to
/// re-establish the gesture and can deliver an immediate end — firing `onStop()`
/// right away, so the button springs back before any audio is captured.
/// `@GestureState` is supposed to survive rebuilds, but it does not survive this.
///
/// A UIKit `UIControl` on a persistent `UIView` is immune: a SwiftUI republish
/// only calls `updateUIView` (we just refresh the callbacks), and never tears
/// down the live view or its in-flight touch tracking. This is the standard
/// walkie-talkie / push-to-talk pattern. The visuals, localization and
/// accessibility stay in SwiftUI; only the *touch tracking* moves to UIKit.
struct PressToTalkButton: View {
    let isRecording: Bool
    let inputLevel: Double
    let onStart: () -> Void
    let onStop: () -> Void

    var body: some View {
        ZStack(alignment: .bottomLeading) {
            Label(
                isRecording ? "松开结束" : "按住说话",
                systemImage: isRecording ? "waveform" : "mic.fill"
            )
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)

            if isRecording {
                GeometryReader { proxy in
                    RoundedRectangle(cornerRadius: 2)
                        .fill(.white.opacity(0.72))
                        .frame(width: max(8, proxy.size.width * min(1, max(0, inputLevel))), height: 4)
                        .frame(maxHeight: .infinity, alignment: .bottom)
                }
                .frame(height: 4)
                .padding(.horizontal, 12)
                .padding(.bottom, 6)
                .accessibilityHidden(true)
            }
        }
        .background(isRecording ? Color.red : Color.accentColor)
        .foregroundStyle(.white)
        .fontWeight(.semibold)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .contentShape(RoundedRectangle(cornerRadius: 12))
        // The recognizer lives on a transparent overlay filling the whole button.
        // It survives parent republishes, so a press is never cancelled by the
        // status text being inserted above us mid-gesture.
        .overlay {
            PressGestureRecognizerView(onStart: onStart, onStop: onStop)
                .accessibilityHidden(true)
        }
        .accessibilityLabel(isRecording ? "正在听，松开结束说话" : "按住说话")
        .accessibilityAddTraits(.isButton)
    }
}

/// Hosts a tiny `UIControl` and forwards touch-down / touch-up directly. This is
/// even less stateful than a gesture recognizer: no recognition delay, no
/// ScrollView/gesture-arena cancellation, and no repeated `.changed` callbacks.
/// Because the underlying `UIView` persists across SwiftUI updates, the press is
/// not interrupted when the parent re-renders.
private struct PressGestureRecognizerView: UIViewRepresentable {
    let onStart: () -> Void
    let onStop: () -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(onStart: onStart, onStop: onStop)
    }

    func makeUIView(context: Context) -> UIView {
        let view = PressTouchControl()
        view.backgroundColor = .clear
        view.addTarget(context.coordinator, action: #selector(Coordinator.touchDown), for: .touchDown)
        view.addTarget(
            context.coordinator,
            action: #selector(Coordinator.touchUp),
            for: [.touchUpInside, .touchUpOutside, .touchCancel]
        )
        return view
    }

    func updateUIView(_ uiView: UIView, context: Context) {
        // Refresh the callbacks so the recognizer always calls the latest closures
        // (which capture the current view model) without rebuilding the view.
        context.coordinator.onStart = onStart
        context.coordinator.onStop = onStop
    }

    final class Coordinator: NSObject {
        var onStart: () -> Void
        var onStop: () -> Void

        init(onStart: @escaping () -> Void, onStop: @escaping () -> Void) {
            self.onStart = onStart
            self.onStop = onStop
        }

        @objc func touchDown() {
            onStart()
        }

        @objc func touchUp() {
            onStop()
        }
    }
}

private final class PressTouchControl: UIControl {
    override var intrinsicContentSize: CGSize {
        CGSize(width: UIView.noIntrinsicMetric, height: UIView.noIntrinsicMetric)
    }
}
