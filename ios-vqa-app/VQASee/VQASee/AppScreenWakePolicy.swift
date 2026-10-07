import SwiftUI
import UIKit

/// One app-level owner: camera modes and network state must not compete over
/// the system idle timer. Explicit lock/backgrounding remains under iOS control.
@MainActor
enum AppScreenWakePolicy {
    static func update(for phase: ScenePhase) {
        UIApplication.shared.isIdleTimerDisabled = phase == .active
    }
}
