import XCTest
import SwiftUI
import UIKit
@testable import VQASee

final class AppScreenWakePolicyTests: XCTestCase {
    @MainActor
    func testForegroundKeepsScreenAwakeWithoutStartingOutdoorCapture() {
        let previous = UIApplication.shared.isIdleTimerDisabled
        defer { UIApplication.shared.isIdleTimerDisabled = previous }
        AppScreenWakePolicy.update(for: .active)
        XCTAssertTrue(UIApplication.shared.isIdleTimerDisabled)
        AppScreenWakePolicy.update(for: .inactive)
        XCTAssertFalse(UIApplication.shared.isIdleTimerDisabled)
        AppScreenWakePolicy.update(for: .active)
        XCTAssertTrue(UIApplication.shared.isIdleTimerDisabled)
        AppScreenWakePolicy.update(for: .background)
        XCTAssertFalse(UIApplication.shared.isIdleTimerDisabled)
        AppScreenWakePolicy.update(for: .active)
        XCTAssertTrue(UIApplication.shared.isIdleTimerDisabled)
    }
}
