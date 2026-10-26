import simd
import XCTest
@testable import VQASee

final class SpatialStructureSemanticStereoTests: XCTestCase {
    func testRansacRecoversVerticalPlaneFromRGBPoints() {
        var points: [SIMD3<Float>] = []
        for z in stride(from: Float(0.1), through: 1.9, by: 0.15) {
            for y in stride(from: Float(0.4), through: 1.8, by: 0.2) {
                points.append(SIMD3(0.01 * sin(z * 9), y, z))
            }
        }
        let floor = SpatialPlaneTrackAssociation.Track(
            id: "floor", kind: "horizontal", normal: SIMD3(0, 1, 0), anchor: .zero, memberSurfaceIDs: ["floor"], epoch: 1
        )
        let planes = SpatialStructureSemanticStereo.ransacPlanes(points, floor: floor)
        XCTAssertGreaterThanOrEqual(planes.count, 1)
        XCTAssertGreaterThan(abs(planes[0].normal.x), 0.9)
        XCTAssertLessThan(abs(planes[0].normal.y), 0.1)
        XCTAssertGreaterThanOrEqual(planes[0].inliers.count, 8)
    }

    func testTwoViewWallPixelsTriangulateWithoutLiDAR() throws {
        let wallX: Float = 0
        func frame(id: String, z: Float) -> SpatialKeyframe {
            SpatialKeyframe(
                id: id, time: Double(z),
                pose: SpatialPose(time: Double(z), position: SpatialPoint(x: 2, y: 1, z: z), forward: SpatialPoint(x: -1, y: 0, z: 0), epoch: 1),
                intrinsics: [100, 0, 0, 0, 100, 0, 64, 64, 1], width: 128, height: 128, imageFile: "\(id).jpg",
                cameraToWorld: [0, 0, -1, 0, 0, 1, 0, 0, 1, 0, 0, 0, 2, 1, z, 1]
            )
        }
        let a = frame(id: "a", z: 0.6)
        let b = frame(id: "b", z: 1.3)
        XCTAssertNotNil(SpatialObservationGeometry.prepareCamera(frame: a))
        XCTAssertNotNil(SpatialObservationGeometry.prepareCamera(frame: b))
        func view(_ frame: SpatialKeyframe) throws -> SpatialStructureWallLineSupport.View {
            let camera = try XCTUnwrap(SpatialObservationGeometry.prepareCamera(frame: frame))
            var labels = Data(repeating: 3, count: 128 * 128)
            var confidence = Data(repeating: 230, count: 128 * 128)
            for y in 0..<128 {
                for x in 0..<128 {
                    let pixel: [Float] = [(Float(x) + 0.5) / 128, (Float(y) + 0.5) / 128]
                    guard let ray = SpatialObservationGeometry.bearing(pixel: pixel, frame: frame) else { continue }
                    let o = SIMD3(ray.origin.x, ray.origin.y, ray.origin.z)
                    let d = SIMD3(ray.direction.x, ray.direction.y, ray.direction.z)
                    guard abs(d.x) > 0.0001 else { continue }
                    let t = (wallX - o.x) / d.x
                    guard t > 0.2 else { continue }
                    let p = o + d * t
                    if p.y >= 0.3, p.y <= 2, p.z >= 0, p.z <= 2 {
                        labels[y * 128 + x] = 0
                    }
                }
            }
            return SpatialStructureWallLineSupport.View(
                frameID: frame.id, camera: camera, wallLabel: 0, floorLabel: 3,
                width: 128, height: 128, labels: labels, confidence: confidence
            )
        }
        let floor = SpatialPlaneTrackAssociation.Track(
            id: "floor", kind: "horizontal", normal: SIMD3(0, 1, 0), anchor: .zero, memberSurfaceIDs: ["floor"], epoch: 1
        )
        let va = try view(a), vb = try view(b)
        let boundary = SpatialStructureSemanticStereo.boundaryMask(va).filter { $0 }.count
        XCTAssertGreaterThan(boundary, 8, "synthetic wall must expose a wall-floor contour")
        let points = SpatialStructureSemanticStereo.wallPoints(frames: [a, b], views: [va, vb], floor: floor)
        XCTAssertGreaterThanOrEqual(points.count, 8, "iPhone 17 RGB stereo must lift wall-floor pixels without LiDAR")
        let medianX = points.map(\.x).sorted()[points.count / 2]
        XCTAssertEqual(medianX, wallX, accuracy: 0.12)
        let published = SpatialStructureSemanticStereo.publish(
            frames: [a, b], views: [va, vb], floor: floor, existing: [], startIndex: 0
        )
        XCTAssertFalse(published.segments.isEmpty)
        XCTAssertTrue(published.tracks.contains { abs($0.normal.x) > 0.8 })
    }
}
