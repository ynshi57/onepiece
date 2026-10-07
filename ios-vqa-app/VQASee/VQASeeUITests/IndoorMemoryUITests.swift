import XCTest

final class IndoorMemoryUITests: XCTestCase {
    @MainActor
    func testLiveObservationExplainsUncertaintyAndKeepsComparisonAuxiliary() throws {
        let app = XCUIApplication()
        app.launchArguments = ["-ui-testing", "-indoor-layout-preview"]
        app.launch()
        XCTAssertTrue(app.staticTexts["indoor-live-observation"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["查看轮廓变化"].exists)
        XCTAssertFalse(app.staticTexts["还在原处"].exists, "UI fixture must not invent identity recognition")
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "Live observation uncertainty — UI fixture, not real depth"
        screenshot.lifetime = .keepAlways; add(screenshot)
    }
    @MainActor
    func testMenuActionsWhileCameraStatePublishesAtTwentyHertz() throws {
        let app = XCUIApplication()
        app.launchArguments = ["-ui-testing", "-indoor-layout-preview", "-indoor-ui-refresh-stress"]
        app.launch()
        XCTAssertTrue(app.buttons["物品记忆选项"].waitForExistence(timeout: 10))
        app.buttons["物品记忆选项"].tap()
        let capture = app.buttons["采集测试样本"]
        XCTAssertTrue(capture.waitForExistence(timeout: 3))
        capture.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        XCTAssertTrue(app.buttons["indoor-capture-start"].waitForExistence(timeout: 5), "The menu action must present its destination under live-rate state updates")
        let evidence = XCTAttachment(screenshot: app.screenshot())
        evidence.name = "Menu opened capture page under 20Hz UI fixture updates"
        evidence.lifetime = .keepAlways
        add(evidence)
        app.buttons["indoor-capture-done"].tap()
        app.buttons["物品记忆选项"].tap()
        app.buttons["真机调试"].tap()
        XCTAssertTrue(app.buttons["device-debug-done"].waitForExistence(timeout: 5), "Root-level sheet must also open under live-rate updates")
        app.buttons["device-debug-done"].tap()
        app.buttons["物品记忆选项"].tap()
        app.buttons["户外看路"].tap()
        XCTAssertTrue(app.buttons["返回物品记忆"].waitForExistence(timeout: 10), "Mode transition must remain operable")
        app.buttons["返回物品记忆"].tap()
        XCTAssertTrue(app.buttons["物品记忆选项"].waitForExistence(timeout: 5))
    }
    @MainActor
    func testCaptureReplayShowsHistoricalFixtureAndDisallowsFakeAcquisition() throws {
        let app = XCUIApplication()
        app.launchArguments = ["-ui-testing", "-indoor-layout-preview", "-indoor-capture-preview"]
        app.launch()
        app.buttons["物品记忆选项"].tap()
        app.buttons["采集测试样本"].tap()
        XCTAssertTrue(app.buttons["indoor-capture-start"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["indoor-capture-start"].isEnabled)
        XCTAssertTrue(app.staticTexts["indoor-capture-availability"].label.contains("当前是界面示例"))
        let sample = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "界面合成样本")).firstMatch
        XCTAssertTrue(sample.waitForExistence(timeout: 5))
        let setup = XCTAttachment(screenshot: app.screenshot())
        setup.name = "Capture setup — synthetic sample"; setup.lifetime = .keepAlways; add(setup)
        sample.tap()
        XCTAssertTrue(app.staticTexts["历史样本 · 每秒1张，非实时相机"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["基线 · 尚未开始变化"].exists)
        app.buttons["下一帧"].tap()
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "第2/2帧")).firstMatch.waitForExistence(timeout: 5))
        let replay = XCTAttachment(screenshot: app.screenshot())
        replay.name = "Capture replay — synthetic sample"; replay.lifetime = .keepAlways; add(replay)
    }
    @MainActor
    func testIndoorDefaultAndOutdoorRoundTrip() throws {
        let app = XCUIApplication()
        app.launchArguments = ["-ui-testing"]
        app.launch()
        XCTAssertTrue(app.staticTexts["物品记忆"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["indoor-record"].exists)
        XCTAssertFalse(app.buttons["indoor-record"].isEnabled, "Simulator has no AR tracking and must not permit fabricated anchors")
        let initial = XCTAttachment(screenshot: app.screenshot())
        initial.name = "Indoor unavailable — real simulator screen"
        initial.lifetime = .keepAlways
        add(initial)
        app.buttons["物品记忆选项"].tap()
        app.buttons["户外看路"].tap()
        XCTAssertTrue(app.buttons["返回物品记忆"].waitForExistence(timeout: 10))
        app.buttons["返回物品记忆"].tap()
        XCTAssertTrue(app.staticTexts["物品记忆"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.buttons["indoor-record"].isEnabled)
    }
    @MainActor
    func testExplicitLayoutFixtureNamingPhotoAndDeletion() throws {
        let app = XCUIApplication()
        app.launchArguments = ["-ui-testing", "-indoor-layout-preview"]
        app.launch()
        XCTAssertTrue(app.staticTexts["UI 布局示例 · 非真实定位"].waitForExistence(timeout: 10))
        let markerName = app.staticTexts["indoor-marker-name"]
        XCTAssertTrue(markerName.waitForExistence(timeout: 5))
        // XCTest reports text ink bounds (~68pt for this four-character name), not
        // its 220pt SwiftUI container. Validate horizontal readable text instead.
        XCTAssertGreaterThan(markerName.frame.width, markerName.frame.height * 2, "Marker name must remain a horizontal line")
        XCTAssertLessThan(markerName.frame.height, 30, "Normal-size marker name must not collapse into a vertical column")
        let layout = XCTAttachment(screenshot: app.screenshot())
        layout.name = "Indoor marker layout — explicit synthetic fixture"
        layout.lifetime = .keepAlways
        add(layout)
        app.buttons["indoor-record"].tap()
        let name = app.textFields["indoor-name"]
        XCTAssertTrue(name.waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["保存"].isEnabled, "Object naming must be optional")
        let selection = XCTAttachment(screenshot: app.screenshot())
        selection.name = "Next version target selection — explicit synthetic fixture"
        selection.lifetime = .keepAlways
        add(selection)
        name.tap()
        name.typeText("Book")
        app.buttons["保存"].tap()
        XCTAssertTrue(app.buttons["Book"].waitForExistence(timeout: 5))
        app.buttons["查看当时照片"].tap()
        XCTAssertTrue(app.buttons["删除这条记录"].waitForExistence(timeout: 5))
        app.buttons["删除这条记录"].tap()
        XCTAssertFalse(app.buttons["Book"].exists)
    }

    @MainActor
    func testSaveWithoutTypingAName() throws {
        let app = XCUIApplication()
        app.launchArguments = ["-ui-testing", "-indoor-layout-preview"]
        app.launch()
        XCTAssertTrue(app.buttons["indoor-record"].waitForExistence(timeout: 10))
        app.buttons["indoor-record"].tap()
        XCTAssertTrue(app.buttons["保存"].waitForExistence(timeout: 5))
        app.buttons["保存"].tap()
        XCTAssertTrue(app.buttons["未命名物品"].waitForExistence(timeout: 5))
    }

}
