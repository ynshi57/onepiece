import XCTest

/// Exercises the saved-device preview, not room accuracy. Never captures a new scan.
final class SpatialSavedPreviewUITests: XCTestCase {
    @MainActor
    func testSavedPreviewShowsMap() throws {
        #if targetEnvironment(simulator)
        throw XCTSkip("Requires existing saved photographs on a physical iPhone")
        #else
        let app = XCUIApplication()
        app.launchArguments = ["-spatial-saved-preview-verify"]
        app.launch()
        XCTAssertTrue(app.staticTexts["已打开保存的空间预览"].waitForExistence(timeout:180), "Saved-scan verification must finish and leave its scan loaded")
        XCTAssertTrue(app.otherElements["spatial.topView"].exists)
        let map = XCTAttachment(screenshot:app.screenshot()); map.name = "Saved scan actual map preview"; map.lifetime = .keepAlways; add(map)
        #endif
    }
}
