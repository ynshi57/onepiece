import XCTest

final class SpatialScanUITests: XCTestCase {
    @MainActor
    func testDefaultScreenAndHistoryAreUsableWithoutCamera() throws {
        let app = XCUIApplication()
        app.launch()
        XCTAssertTrue(app.buttons["spatial.scan"].waitForExistence(timeout: 15))
        XCTAssertTrue(app.staticTexts["空间扫描"].exists)
        XCTAssertFalse(app.buttons["spatial.save"].isEnabled)
        XCTAssertTrue(app.otherElements["spatial.topView"].exists)
        let initial = XCTAttachment(screenshot: app.screenshot())
        initial.name = "Spatial scan real initial UI — no camera or geometry simulation"
        initial.lifetime = .keepAlways; add(initial)
        app.buttons["spatial.history"].tap()
        XCTAssertTrue(app.navigationBars["扫描记录"].waitForExistence(timeout: 5))
        app.buttons["完成"].tap()
        app.buttons["spatial.more"].tap()
        XCTAssertTrue(app.buttons["真机调试"].waitForExistence(timeout: 5))
        app.buttons["真机调试"].tap()
        XCTAssertTrue(app.buttons["device-debug-done"].waitForExistence(timeout: 5))
    }
}
