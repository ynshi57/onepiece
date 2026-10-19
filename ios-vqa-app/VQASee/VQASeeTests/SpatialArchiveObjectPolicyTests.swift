import XCTest
@testable import VQASee

final class SpatialArchiveObjectPolicyTests: XCTestCase {
    func testOnlySavedFramesEnterArchiveAndFailuresDoNotRetryAutomatically() {
        var document = SpatialScanDocument()
        XCTAssertNil(SpatialArchiveObjectPolicy.nextFrame(in: document))
        let frame = makeFrame("saved-photo", time: 4)
        document.keyframes = [frame]
        XCTAssertEqual(SpatialArchiveObjectPolicy.nextFrame(in: document), frame)
        let failed = SpatialObjectObservation(attemptID: UUID(), frameID: frame.id, epoch: frame.pose.epoch,
            frameTime: frame.time, status: .failed, failure: "模型不可用", elapsedSeconds: 0.3,
            detections: [], modelID: "YOLO11nObject")
        document.objectObservations = [failed]
        XCTAssertNil(SpatialArchiveObjectPolicy.nextFrame(in: document))
        XCTAssertEqual(document.objectObservations?.first, failed)
    }

    func testPumpKeepsOldFailureAndSelectsNextRealFrameInCaptureOrder() {
        var document = SpatialScanDocument()
        let first = makeFrame("one", time: 4), second = makeFrame("two", time: 5)
        document.keyframes = [first,second]
        let failure = SpatialObjectObservation(attemptID: UUID(), frameID: first.id, epoch: 2,
            frameTime: first.time, status: .failed, failure: "推理失败", elapsedSeconds: 1.2,
            detections: [], modelID: "YOLO11nObject")
        document.objectObservations = [failure]
        XCTAssertEqual(SpatialArchiveObjectPolicy.nextFrame(in: document), second)
        XCTAssertEqual(document.objectObservations, [failure])
    }

    func testPhotoCapacityDoesNotPreventDrainingExistingFrames() {
        var document = SpatialScanDocument()
        document.keyframes = (0..<240).map { makeFrame("frame-\($0)", time: Double($0)) }
        XCTAssertEqual(SpatialArchiveObjectPolicy.nextFrame(in: document)?.id, "frame-0")
        document.objectObservations = document.keyframes.dropLast().map {
            SpatialObjectObservation(attemptID: UUID(), frameID: $0.id, epoch: 2, frameTime: $0.time,
                status: .processed, elapsedSeconds: 0.1, detections: [], modelID: "YOLO11nObject")
        }
        XCTAssertEqual(SpatialArchiveObjectPolicy.nextFrame(in: document)?.id, "frame-239")
    }

    private func makeFrame(_ id: String, time: Double) -> SpatialKeyframe {
        SpatialKeyframe(id: id, time: time,
            pose: SpatialPose(time: time, position: SpatialPoint(x: 0,y: 0,z: 0), forward: SpatialPoint(x: 0,y: 0,z: -1), epoch: 2),
            intrinsics: [600,0,0,0,600,0,384,256,1], width: 768, height: 512, imageFile: "\(id).jpg",
            cameraToWorld: [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1])
    }
}
