import XCTest

final class SpatialScanUITests: XCTestCase {
    @MainActor
    func testDefaultScreenAndHistoryAreUsableWithoutCamera() throws {
        let app = XCUIApplication()
        app.launch()
        XCTAssertTrue(app.buttons["spatial.scan"].waitForExistence(timeout: 15))
        XCTAssertTrue(app.staticTexts["空间扫描"].exists)
        XCTAssertFalse(app.staticTexts["已观察结构"].exists)
        XCTAssertFalse(app.segmentedControls["spatial.layers"].exists)
        XCTAssertFalse(app.buttons["spatial.save"].isEnabled)
        XCTAssertTrue(app.otherElements["spatial.topView"].exists)
        let initial = XCTAttachment(screenshot: app.screenshot())
        initial.name = "Spatial scan real initial UI — no camera or geometry simulation"
        initial.lifetime = .keepAlways; add(initial)
        app.buttons["spatial.history"].tap()
        XCTAssertTrue(app.navigationBars["扫描记录"].waitForExistence(timeout: 5))
        app.buttons["spatial.history.done"].tap()
        app.buttons["spatial.more"].tap()
        XCTAssertTrue(app.buttons["真机调试"].waitForExistence(timeout: 5))
        app.buttons["真机调试"].tap()
        XCTAssertTrue(app.buttons["device-debug-done"].waitForExistence(timeout: 5))
    }
    @MainActor
    func testOnDeviceObjectObservationEntryShowsItsLimits() throws {
        let app = XCUIApplication()
        app.launch()
        let button = app.buttons["spatial.objects.analyze"]
        XCTAssertTrue(button.waitForExistence(timeout: 15))
        // The middle of this screen is an orbitable SceneKit view. Scroll from
        // the text area below it; disabled buttons are not tap targets.
        for _ in 0..<6 {
            if app.frame.contains(button.frame), button.frame.height > 0 { break }
            let start = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.92))
            let end = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.48))
            start.press(forDuration: 0.05, thenDragTo: end)
        }
        XCTAssertGreaterThan(button.frame.height, 0)
        XCTAssertTrue(app.frame.contains(button.frame))
        XCTAssertFalse(button.isEnabled) // No captured photographs; do not invent observations.
        XCTAssertTrue(app.buttons["spatial.objects.supports"].exists)
        XCTAssertFalse(app.buttons["spatial.objects.supports"].isEnabled)
        XCTAssertTrue(app.staticTexts["核对得到的是区域内表面点，可能来自背景；物品位置仍未确认。"].exists)
        XCTAssertTrue(app.staticTexts["检测类别有限，不包含纸箱和画架；没有检测框不代表不存在。"].exists)
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "On-device object observation entry — actual empty scan"
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    @MainActor
    func testExistingScanObjectObservationsOnDevice() throws {
        #if targetEnvironment(simulator)
        throw XCTSkip("Requires existing photographs on a physical iPhone")
        #else
        let app = XCUIApplication()
        func openHistory() {
            let history = app.buttons["spatial.history"]
            for _ in 0..<8 where !history.isHittable {
                app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.92)).press(forDuration: 0.05,
                    thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.48)))
            }
            history.tap()
            let record = app.buttons.matching(identifier: "spatial.history.open").firstMatch
            XCTAssertTrue(record.waitForExistence(timeout: 10), "Device needs an existing saved scan")
            record.tap()
        }
        func revealAnalysisButton() -> XCUIElement {
            let button = app.buttons["spatial.objects.analyze"]
            XCTAssertTrue(button.waitForExistence(timeout: 10))
            for _ in 0..<8 {
                if app.frame.contains(button.frame), button.frame.height > 0 { break }
                app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.92)).press(forDuration: 0.05,
                    thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.48)))
            }
            return button
        }
        app.launch()
        openHistory()
        let button = revealAnalysisButton()
        XCTAssertTrue(button.isEnabled, "Existing scan must include local photographs")
        button.tap()
        let progress = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "本次已识别")).firstMatch
        XCTAssertTrue(progress.waitForExistence(timeout: 10))
        XCTAssertTrue(XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: NSPredicate(format: "enabled == true"), object: button)], timeout: 180) == .completed)
        let summary = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "已有 ")).firstMatch
        XCTAssertTrue(summary.exists)
        let savedSummary = summary.label
        let detail = XCTAttachment(string: savedSummary)
        detail.name = "Physical iPhone observation completion counts"
        detail.lifetime = .keepAlways; add(detail)
        XCTAssertFalse(app.descendants(matching: .any).matching(identifier: "spatial.error").firstMatch.exists)
        app.terminate()
        app.launch()
        openHistory()
        _ = revealAnalysisButton()
        XCTAssertEqual(app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "已有 ")).firstMatch.label, savedSummary)
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "Physical iPhone observations after reload"
        screenshot.lifetime = .keepAlways; add(screenshot)
        #endif
    }

    @MainActor
    func testViewportPictureInPictureSwapsBothWays() throws {
        let app = XCUIApplication()
        app.launch()
        let swap = app.buttons["spatial.viewport.swap"]
        XCTAssertTrue(swap.waitForExistence(timeout: 15))
        XCTAssertEqual(swap.value as? String, "画面为主窗口")
        swap.tap()
        XCTAssertEqual(swap.value as? String, "俯视图为主窗口")
        let previewY = swap.frame.minY
        let viewport = app.otherElements["spatial.structure"]
        viewport.coordinate(withNormalizedOffset: CGVector(dx: 0.25, dy: 0.6))
            .press(forDuration: 0.1, thenDragTo: viewport.coordinate(withNormalizedOffset: CGVector(dx: 0.25, dy: 0.3)))
        XCTAssertEqual(swap.frame.minY, previewY, accuracy: 2, "地图拖动不应滚动页面")
        let mapMain = XCTAttachment(screenshot: app.screenshot())
        mapMain.name = "Top map primary with scene preview inset — actual empty scan"
        mapMain.lifetime = .keepAlways; add(mapMain)
        swap.tap()
        XCTAssertEqual(swap.value as? String, "画面为主窗口")
        let cameraMain = XCTAttachment(screenshot: app.screenshot())
        cameraMain.name = "Scene preview primary with top map inset — actual empty scan"
        cameraMain.lifetime = .keepAlways; add(cameraMain)
        XCTAssertFalse(app.buttons["spatial.save"].isEnabled)
        XCTAssertTrue(app.otherElements["spatial.topView"].exists)
    }

    @MainActor
    func testAuthorizedMetadataHorizontalSurfacesAreVisibleInBothViews() throws {
        guard ProcessInfo.processInfo.environment["VQASEE_METADATA_PREVIEW"] == "1" else {
            throw XCTSkip("Explicit local metadata replay only; no private fixture committed")
        }
        let app = XCUIApplication()
        app.launchArguments = ["-spatial-metadata-preview"]
        app.launch()
        XCTAssertTrue(app.staticTexts["结构回放（未载入照片）"].waitForExistence(timeout: 15))
        let scene = XCTAttachment(screenshot: app.screenshot())
        scene.name = "Authorized actual scan geometry — horizontal surfaces in 3D, no photos"
        scene.lifetime = .keepAlways; add(scene)
        app.buttons["spatial.viewport.swap"].tap()
        XCTAssertEqual(app.buttons["spatial.viewport.swap"].value as? String, "俯视图为主窗口")
        let top = XCTAttachment(screenshot: app.screenshot())
        top.name = "Authorized actual scan geometry — horizontal surfaces in top view, no photos"
        top.lifetime = .keepAlways; add(top)
        try verifySemanticSummary(app, walls:0, floors:0)
        XCTAssertTrue(app.staticTexts["水平观测，未确认地面"].exists)
    }

    @MainActor
    func testSyntheticSemanticWallAndFloorReachBothMapViews() throws {
        guard ProcessInfo.processInfo.environment["VQASEE_SEMANTIC_PREVIEW"] == "1" else {
            throw XCTSkip("Explicit synthetic semantic rendering fixture only")
        }
        let app = XCUIApplication()
        app.launchArguments = ["-spatial-metadata-preview"]
        app.launch()
        XCTAssertTrue(app.staticTexts["结构回放（未载入照片）"].waitForExistence(timeout:15))
        let scene = XCTAttachment(screenshot:app.screenshot())
        scene.name = "Synthetic only — semantic wall and floor in 3D"
        scene.lifetime = .keepAlways; add(scene)
        app.buttons["spatial.viewport.swap"].tap()
        XCTAssertEqual(app.buttons["spatial.viewport.swap"].value as? String,"俯视图为主窗口")
        let top = XCTAttachment(screenshot:app.screenshot())
        top.name = "Synthetic only — semantic wall and floor in top view"
        top.lifetime = .keepAlways; add(top)
        try verifySemanticSummary(app, walls:1, floors:1)
    }

    @MainActor
    private func verifySemanticSummary(_ app: XCUIApplication, walls: Int, floors: Int) throws {
        let details = app.buttons["扫描详情"]
        for _ in 0..<6 {
            if details.exists && details.isHittable { break }
            app.swipeUp()
        }
        XCTAssertTrue(details.waitForExistence(timeout:5))
        details.tap()
        let summary = app.staticTexts["墙面证据支持\(walls)面，地面证据支持\(floors)面，冲突0面；其余类别未知。全部原始观测保留。"]
        for _ in 0..<4 {
            if summary.exists { break }
            app.swipeUp()
        }
        XCTAssertTrue(summary.waitForExistence(timeout:5))
    }

}
