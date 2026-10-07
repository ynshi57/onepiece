import XCTest
@testable import VQASee

final class IndoorCaptureTests: XCTestCase {
    private func manifest(id: UUID = UUID()) -> IndoorCaptureManifest {
        IndoorCaptureManifest(id: id, arSessionID: UUID(), objectName: "杯子", objectInstanceID: UUID(),
                              event: .removed, startedAt: Date(), startedUptime: 100)
    }
    private func packet(index: Int = 0) -> IndoorCapturePacket {
        let image = Data([0xFF, 0xD8, 0xFF, 0xD9]) // Opaque store test bytes, not a valid replay JPEG.
        let depth = Data([0, 0, 128, 63]), confidence = Data([2]) // 1 meter Float32 little endian.
        let k: [Float] = [500, 0, 0, 0, 500, 0, 320, 240, 1]
        let frame = IndoorCaptureFrame(id: index, timestamp: 101 + Double(index), tracking: "normal",
            sensorWidth: 640, sensorHeight: 480, imageWidth: 320, imageHeight: 240, interfaceOrientation: 1,
            cameraToWorld: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
            intrinsics: k, encodedIntrinsics: IndoorCaptureStore.scaledIntrinsics(k, x: 0.5, y: 0.5),
            imageSHA256: IndoorCaptureStore.hash(image), depthWidth: 1, depthHeight: 1,
            depthSHA256: IndoorCaptureStore.hash(depth), confidenceSHA256: IndoorCaptureStore.hash(confidence))
        return IndoorCapturePacket(metadata: frame, jpeg: image, depth: depth, confidence: confidence)
    }
    func testScalingIntrinsicsPreservesProjection() {
        let k: [Float] = [500, 0, 0, 0, 520, 0, 320, 240, 1]
        let scaled = IndoorCaptureStore.scaledIntrinsics(k, x: 0.5, y: 0.25)
        let x: Float = 0.2, y: Float = -0.3, z: Float = 2
        XCTAssertEqual((k[0] * x / z + k[6]) * 0.5, scaled[0] * x / z + scaled[6], accuracy: 0.0001)
        XCTAssertEqual((k[4] * y / z + k[7]) * 0.25, scaled[4] * y / z + scaled[7], accuracy: 0.0001)
        XCTAssertEqual(scaled[8], 1)
    }
    func testEventPlanDoesNotLabelBaselineAsRemoved() {
        var m = manifest()
        XCTAssertEqual(m.phase(at: 102), "基线 · 尚未开始变化")
        m.transitionStarted = 103; m.transitionCompleted = 105
        XCTAssertEqual(m.phase(at: 102), "基线")
        XCTAssertEqual(m.phase(at: 104), "变化过程 · 待标注")
        XCTAssertEqual(m.phase(at: 105), "事件后 · 仍需逐帧确认")
    }
    func testCaptureRoundTripCountersDepthAndDelete() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = IndoorCaptureStore(root: root), m = manifest(), p = packet()
        try await store.begin(m)
        _ = try await store.append(p, id: m.id)
        try await store.mark(id: m.id, time: 103, completed: false)
        try await store.mark(id: m.id, time: 105, completed: true)
        try await store.finish(id: m.id, reason: "进入后台", busy: 2, missing: 1, duplicates: 3, failures: 0)
        let read = try await store.readFrame(id: m.id, index: 0)
        XCTAssertEqual(read.depth, Data([0, 0, 128, 63]))
        XCTAssertEqual(read.confidence, p.confidence)
        let loaded = try await store.load(m.id)
        XCTAssertEqual(loaded.missedBusy, 2)
        XCTAssertEqual(loaded.transitionCompleted, 105)
        XCTAssertEqual(loaded.endReason, "进入后台")
        XCTAssertEqual(try root.resourceValues(forKeys: [.isExcludedFromBackupKey]).isExcludedFromBackup, true)
        try await store.delete(m.id)
        let entries = try await store.list()
        XCTAssertTrue(entries.isEmpty)
    }
    func testMissingReferencedFileFailsReplayInsteadOfShowingPreviousImage() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = IndoorCaptureStore(root: root), m = manifest()
        try await store.begin(m)
        _ = try await store.append(packet(), id: m.id)
        try FileManager.default.removeItem(at: root.appendingPathComponent(m.id.uuidString).appendingPathComponent("0.jpg"))
        do { _ = try await store.readFrame(id: m.id, index: 0); XCTFail("Missing evidence must fail visibly") }
        catch IndoorCaptureError.damaged { }
    }
    func testInterruptedClipIsListedAndBadDepthNeverCommitted() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = IndoorCaptureStore(root: root), m = manifest()
        try await store.begin(m)
        var invalid = packet(); invalid.depth = Data([0])
        do { _ = try await store.append(invalid, id: m.id); XCTFail("Depth layout is invalid") }
        catch IndoorCaptureError.invalidData { }
        let restoredStore = IndoorCaptureStore(root: root)
        let entries = try await restoredStore.list()
        XCTAssertEqual(entries.first?.manifest?.frames.count, 0)
        XCTAssertNotNil(entries.first?.issue)
    }
    func testOutOfOrderFramesCannotEnterDataset() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = IndoorCaptureStore(root: root), m = manifest()
        try await store.begin(m)
        _ = try await store.append(packet(), id: m.id)
        do { _ = try await store.append(packet(), id: m.id); XCTFail("Repeated frame must not be accepted") }
        catch IndoorCaptureError.invalidData { }
        let loaded = try await store.load(m.id)
        XCTAssertEqual(loaded.frames.count, 1)
    }
}
