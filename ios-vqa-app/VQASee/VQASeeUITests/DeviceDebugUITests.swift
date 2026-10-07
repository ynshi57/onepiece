import XCTest

final class DeviceDebugUITests: XCTestCase {
    @MainActor
    func testDisconnectedConsentAndReturnWithoutSharing() throws {
        let app = XCUIApplication()
        app.launchArguments = ["-ui-testing", "-device-debug-layout-preview"]
        app.launch()
        XCTAssertTrue(app.navigationBars["共享屏幕到 Mac"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["UI 布局示例 · 未联网、未录屏"].exists)
        XCTAssertFalse(app.secureTextFields["device-debug-pairing"].exists, "Normal flow must not require copying credentials")
        XCTAssertTrue(app.buttons["重新查找"].exists)
        let initial = XCTAttachment(screenshot: app.screenshot())
        initial.name = "Device debug configuration — explicit UI fixture, not live sharing"
        initial.lifetime = .keepAlways
        add(initial)

        let sampleToggle = app.switches["device-debug-samples"]
        if !sampleToggle.isHittable { app.swipeUp() }
        // SwiftUI exposes both the labelled row and its native switch. The row's
        // center is its text, not the actual thumb; target the observed child.
        let nativeSwitch = sampleToggle.switches.firstMatch
        XCTAssertTrue(nativeSwitch.exists)
        XCTAssertEqual(sampleToggle.value as? String, "0", "Original samples require separate explicit consent")
        nativeSwitch.tap()
        XCTAssertEqual(sampleToggle.value as? String, "1")
        nativeSwitch.tap()
        XCTAssertEqual(sampleToggle.value as? String, "0")

        app.swipeUp()
        let start = app.buttons["device-debug-start"]
        for _ in 0..<4 where !start.isHittable { app.swipeUp() }
        XCTAssertFalse(start.isEnabled, "Cannot share before Mac authorizes connection")
        XCTAssertFalse(app.buttons["device-debug-stop"].exists)
        let result = XCTAttachment(screenshot: app.screenshot())
        result.name = "Device debug preview remains disconnected after start"
        result.lifetime = .keepAlways
        add(result)
        app.buttons["device-debug-done"].tap()
        XCTAssertTrue(app.staticTexts["物品记忆"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.navigationBars["共享屏幕到 Mac"].exists)
        app.buttons["物品记忆选项"].tap()
        XCTAssertTrue(app.buttons["真机调试"].waitForExistence(timeout: 3))
        app.buttons["真机调试"].tap()
        XCTAssertTrue(app.navigationBars["共享屏幕到 Mac"].waitForExistence(timeout: 3), "Returning must leave the real main-screen menu operable")
    }
}
