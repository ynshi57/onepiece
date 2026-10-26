import XCTest
import simd
@testable import VQASee

/// Synthetic cameras check the candidate contract, not tape-measured accuracy.
final class SpatialObjectLocationBuilderTests: XCTestCase {
    private func frame(id: String, x: Float, time: Double) -> SpatialKeyframe {
        SpatialKeyframe(id: id, time: time,
            pose: SpatialPose(time: time, position: SpatialPoint(x: x, y: 0, z: 0),
                              forward: SpatialPoint(x: 0, y: 0, z: -1), epoch: 0),
            intrinsics: [800, 0, 0, 0, 600, 0, 430, 310, 1], width: 1000, height: 800,
            imageFile: "\(id).jpg",
            cameraToWorld: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, 0, 0, 1])
    }

    private func pixel(_ p: SIMD3<Float>, in f: SpatialKeyframe) -> [Float] {
        let a = f.cameraToWorld
        let t = simd_float4x4(columns: (SIMD4(a[0], a[1], a[2], a[3]), SIMD4(a[4], a[5], a[6], a[7]),
                                        SIMD4(a[8], a[9], a[10], a[11]), SIMD4(a[12], a[13], a[14], a[15])))
        let q = t.inverse * SIMD4(p.x, p.y, p.z, 1)
        return [(800 * q.x / -q.z + 430) / 1000, (600 * -q.y / -q.z + 310) / 800]
    }

    private func attempt(source: SpatialKeyframe, target: SpatialKeyframe, attemptID: UUID,
                         points: [SIMD3<Float>], box: [Float]) throws -> SpatialObjectSupportAttempt {
        let supports = try points.map { p -> SpatialSurfaceSupport in
            let match = SpatialImageMatch(sourcePixel: pixel(p, in: source), targetPixel: pixel(p, in: target),
                                          cycleErrorPixels: 0.2)
            return try XCTUnwrap(SpatialObservationGeometry.triangulate(match, source: source, target: target))
        }
        for support in supports {
            XCTAssertGreaterThanOrEqual(support.sourcePixel[0], box[0])
            XCTAssertLessThanOrEqual(support.sourcePixel[0], box[2])
            XCTAssertGreaterThanOrEqual(support.sourcePixel[1], box[1])
            XCTAssertLessThanOrEqual(support.sourcePixel[1], box[3])
        }
        return SpatialObjectSupportAttempt(
            sourceFrameID: source.id, sourceAttemptID: attemptID, detectionIndex: 0,
            targetFrameID: target.id, epoch: 0, status: .measured,
            reason: "Synthetic visible-surface test only", elapsedSeconds: 0.2,
            pairGridCount: 150, pairMatchCount: supports.count, sampledMatchCount: supports.count,
            supports: supports)
    }

    func testThreeInBoxSupportsPublishVisibleSurfaceWidthAndHeight() throws {
        let source = frame(id: "source", x: 0, time: 1)
        let target = frame(id: "target", x: 0.5, time: 2)
        let box: [Float] = [0.2, 0.2, 0.8, 0.8]
        let attemptID = UUID()
        let observation = SpatialObjectObservation(
            attemptID: attemptID, frameID: source.id, epoch: 0, frameTime: source.time,
            status: .processed, elapsedSeconds: 0.1,
            detections: [SpatialObjectDetection(label: "cup", score: 0.8, boundingBox: box, rawBoundingBox: box)],
            modelID: "synthetic-test")
        let attempt = try attempt(source: source, target: target, attemptID: attemptID,
                                  points: [SIMD3(0, 0, -3), SIMD3(0.04, 0.02, -3), SIMD3(-0.03, -0.02, -3)],
                                  box: box)
        var document = SpatialScanDocument()
        document.keyframes = [source, target]
        document.objectObservations = [observation]
        document.objectSupportAttempts = [attempt]

        let rows = SpatialObjectLocationBuilder.candidates(document: document)
        XCTAssertEqual(rows.count, 1)
        let row = try XCTUnwrap(rows.first)
        XCTAssertEqual(row.label, "cup")
        XCTAssertEqual(row.supportCount, 3)
        XCTAssertFalse(row.isClipped)
        XCTAssertEqual(row.surfacePosition.x, 0, accuracy: 0.03)
        XCTAssertEqual(row.surfacePosition.z, -3, accuracy: 0.03)
        XCTAssertEqual(row.widthMeters, 2.25, accuracy: 0.05)
        XCTAssertEqual(row.heightMeters, 2.4, accuracy: 0.05)
        XCTAssertGreaterThan(row.spreadMeters, 0)
        XCTAssertLessThan(row.spreadMeters, 0.08)
        XCTAssertTrue(row.reason.contains("进深未知"))
        XCTAssertTrue(row.reason.contains("不是物品中心"))
    }

    func testFewerThanThreeSupportsStayUnpublished() throws {
        let source = frame(id: "source", x: 0, time: 1)
        let target = frame(id: "target", x: 0.5, time: 2)
        let box: [Float] = [0.2, 0.2, 0.8, 0.8]
        let attemptID = UUID()
        let observation = SpatialObjectObservation(
            attemptID: attemptID, frameID: source.id, epoch: 0, frameTime: source.time,
            status: .processed, elapsedSeconds: 0.1,
            detections: [SpatialObjectDetection(label: "chair", score: 0.7, boundingBox: box, rawBoundingBox: box)],
            modelID: "synthetic-test")
        let attempt = try attempt(source: source, target: target, attemptID: attemptID,
                                  points: [SIMD3(0, 0, -3), SIMD3(0.04, 0, -3)], box: box)
        var document = SpatialScanDocument()
        document.keyframes = [source, target]
        document.objectObservations = [observation]
        document.objectSupportAttempts = [attempt]
        XCTAssertTrue(SpatialObjectLocationBuilder.candidates(document: document).isEmpty)
    }

    func testLatestAttemptWinsAndPreviousObservationDoesNotPublish() throws {
        let source = frame(id: "source", x: 0, time: 1)
        let target = frame(id: "target", x: 0.5, time: 2)
        let box: [Float] = [0.2, 0.2, 0.8, 0.8]
        let oldID = UUID(), newID = UUID()
        let old = SpatialObjectObservation(
            attemptID: oldID, frameID: source.id, epoch: 0, frameTime: source.time,
            status: .processed, elapsedSeconds: 0.1,
            detections: [SpatialObjectDetection(label: "cup", score: 0.8, boundingBox: box, rawBoundingBox: box)],
            modelID: "synthetic-test")
        var current = old
        current.attemptID = newID
        current.previousAttempts = [old]
        let stale = try attempt(source: source, target: target, attemptID: oldID,
                                points: [SIMD3(0.2, 0, -3), SIMD3(0.24, 0, -3), SIMD3(0.16, 0, -3)], box: box)
        let fresh = try attempt(source: source, target: target, attemptID: newID,
                                points: [SIMD3(0, 0, -3), SIMD3(0.04, 0, -3), SIMD3(-0.04, 0, -3)], box: box)
        var document = SpatialScanDocument()
        document.keyframes = [source, target]
        document.objectObservations = [current]
        document.objectSupportAttempts = [stale, fresh]
        let rows = SpatialObjectLocationBuilder.candidates(document: document)
        XCTAssertEqual(rows.count, 1)
        XCTAssertEqual(rows.first?.sourceAttemptID, newID)
        XCTAssertEqual(rows.first?.surfacePosition.x ?? 99, 0, accuracy: 0.03)
    }
}
