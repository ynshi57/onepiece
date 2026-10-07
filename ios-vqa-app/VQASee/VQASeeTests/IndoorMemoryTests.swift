import XCTest
import ARKit
@testable import VQASee

final class IndoorMemoryTests: XCTestCase {
    func testRestorationTimeoutDoesNotDependOnFrameAvailability() {
        XCTAssertFalse(IndoorMemoryPolicy.restorationExpired(startedAt: 100, now: 114.99))
        XCTAssertTrue(IndoorMemoryPolicy.restorationExpired(startedAt: 100, now: 115))
        XCTAssertTrue(IndoorMemoryPolicy.restorationExpired(startedAt: 100, now: 160))
        XCTAssertFalse(IndoorMemoryPolicy.restorationExpired(startedAt: nil, now: 160))
    }

    @MainActor
    func testCapturePausedReasonIsActionableAndDoesNotClaimSimulator() {
        let controller = IndoorMemoryController()
        controller.stop()
        XCTAssertEqual(controller.captureAvailability, .paused)
        XCTAssertFalse(controller.canStartCapture)
        XCTAssertEqual(controller.captureAvailability.message, "相机已暂停，请恢复相机后开始。")
    }

    @MainActor
    func testIndoorCameraNeverEnablesUserFaceTracking() {
        let configuration = IndoorMemoryController.makeIndoorConfiguration()
        XCTAssertFalse(configuration.userFaceTrackingEnabled)
        XCTAssertTrue(configuration.planeDetection.contains(.horizontal))
        XCTAssertTrue(configuration.planeDetection.contains(.vertical))
    }
    @MainActor
    func testRelaunchLoadsPhotoButDoesNotInventLivePosition() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = IndoorMemoryStore(directory: directory)
        let id = UUID()
        let stored = IndoorMemoryStoredRecord(id: id, name: "相机", recordedAt: Date(), sessionID: UUID(), photo: Data([1, 2, 3]))
        try await store.save(IndoorMemorySnapshot(records: [stored], worldMapData: nil))
        let relaunched = IndoorMemoryController(store: store)
        await relaunched.loadRecords()
        XCTAssertEqual(relaunched.selected?.id, id)
        XCTAssertEqual(relaunched.selected?.photo, stored.photo)
        XCTAssertNil(relaunched.marker, "Persisted photo alone must never restore spatial validity")
        XCTAssertFalse(relaunched.canCheckSelectedObject)
        relaunched.confirmSelectedObject(present: false)
        XCTAssertEqual(relaunched.checkStatus, .idle, "No current observation must not be confirmed absent")
    }

    @MainActor
    func testCorruptMapPreservesPhotoAndExposesError() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = IndoorMemoryStore(directory: directory)
        let stored = IndoorMemoryStoredRecord(id: UUID(), name: "物品", recordedAt: Date(), sessionID: UUID(), photo: Data([1]))
        try await store.save(IndoorMemorySnapshot(records: [stored], worldMapData: Data([0, 1, 2])))
        let controller = IndoorMemoryController(store: store)
        await controller.loadRecords()
        XCTAssertEqual(controller.records.count, 1)
        XCTAssertNotNil(controller.error)
        XCTAssertNil(controller.marker)
    }
    func testResetCannotProjectHistoricalCoordinatesEvenWithNormalTracking() {
        XCTAssertFalse(IndoorMemoryPolicy.canProject(recordSession: UUID(), currentSession: UUID(), trackingNormal: true, frameAge: 0.02))
    }
    func testTrackingLossCannotProjectFreshHistoricalCoordinate() {
        let session = UUID()
        XCTAssertFalse(IndoorMemoryPolicy.canProject(recordSession: session, currentSession: session, trackingNormal: false, frameAge: 0.02))
    }
    func testStaleAndInvalidFramesCannotProject() {
        let session = UUID()
        for age in [0.251, 10, -1, Double.nan, Double.infinity] {
            XCTAssertFalse(IndoorMemoryPolicy.canProject(recordSession: session, currentSession: session, trackingNormal: true, frameAge: age))
        }
        XCTAssertTrue(IndoorMemoryPolicy.canProject(recordSession: session, currentSession: session, trackingNormal: true, frameAge: 0.05))
    }
    func testBehindCameraNeverUsesInvertedLeftRightProjection() {
        let viewport = CGRect(x: 0, y: 0, width: 400, height: 800)
        for x: CGFloat in [-100, 800] {
            XCTAssertEqual(IndoorMemoryPolicy.turnHint(isBehind: true, projected: CGPoint(x: x, y: 400), visibleRect: viewport), "记录位置在身后，请原地转动手机")
        }
    }
    func testOffscreenDirectionsUseUIOrientedProjectionInPortraitAndLandscape() {
        for size in [CGSize(width: 400, height: 800), CGSize(width: 800, height: 400)] {
            let viewport = CGRect(origin: .zero, size: size)
            XCTAssertEqual(IndoorMemoryPolicy.turnHint(isBehind: false, projected: CGPoint(x: size.width + 100, y: size.height / 2), visibleRect: viewport), "向右转动手机")
            XCTAssertEqual(IndoorMemoryPolicy.turnHint(isBehind: false, projected: CGPoint(x: -100, y: size.height / 2), visibleRect: viewport), "向左转动手机")
            XCTAssertEqual(IndoorMemoryPolicy.turnHint(isBehind: false, projected: CGPoint(x: size.width / 2, y: -100), visibleRect: viewport), "抬高手机")
            XCTAssertEqual(IndoorMemoryPolicy.turnHint(isBehind: false, projected: CGPoint(x: size.width / 2, y: size.height + 100), visibleRect: viewport), "放低手机")
        }
    }
    func testUserNamingRejectsEmptyAndUnboundedInput() {
        XCTAssertNil(IndoorMemoryPolicy.validName(" \n "))
        XCTAssertNil(IndoorMemoryPolicy.validName(String(repeating: "杯", count: 31)))
        XCTAssertEqual(IndoorMemoryPolicy.validName(" 蓝色杯子 \n"), "蓝色杯子")
    }
    @MainActor
    func testStopInvalidatesDraftMarkerAndRecordAction() {
        let controller = IndoorMemoryController()
        controller.stop()
        XCTAssertNil(controller.marker)
        XCTAssertNil(controller.draft)
        XCTAssertFalse(controller.canRecord)
        XCTAssertEqual(controller.state, .paused("空间定位暂停"))
    }
    @MainActor
    func testMissingPlaneDoesNotCreateInventedLocation() {
        let controller = IndoorMemoryController()
        controller.beginRecord()
        XCTAssertNotNil(controller.error)
        XCTAssertNil(controller.draft)
        controller.save(name: "杯子")
        XCTAssertTrue(controller.records.isEmpty)
    }
}
