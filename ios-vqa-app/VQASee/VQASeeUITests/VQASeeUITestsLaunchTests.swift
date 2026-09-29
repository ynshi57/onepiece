//
//  VQASeeUITestsLaunchTests.swift
//  VQASeeUITests
//
//  Created by Bayes on 2026/6/3.
//

import XCTest

final class VQASeeUITestsLaunchTests: XCTestCase {

    private func makeApp() -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["-ui-testing"]
        return app
    }

    override class var runsForEachTargetApplicationUIConfiguration: Bool {
        true
    }

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    @MainActor
    func testLaunch() throws {
        let app = makeApp()
        app.launch()

        // `app.screenshot()` asks CoreSimulator for a window-server snapshot. That
        // service can time out independently of the app (notably on a freshly
        // booted CI simulator), producing a false launch failure. Assert the
        // observable launch contract instead.
        XCTAssertEqual(app.state, .runningForeground)
    }
}
