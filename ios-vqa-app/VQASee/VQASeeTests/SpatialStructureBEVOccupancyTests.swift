import simd
import XCTest
@testable import VQASee

final class SpatialStructureBEVOccupancyTests: XCTestCase {
    func testFilledRectangleOccupancyContourHasFourLongSides() {
        var points: [SIMD3<Float>] = []
        for x in stride(from: Float(0), through: 4, by: 0.04) {
            for z in stride(from: Float(0), through: 3, by: 0.04) {
                points.append(SIMD3(x, 0, z))
            }
        }
        let contour = SpatialStructureBEVOccupancy.simplify(
            SpatialStructureBEVOccupancy.walk(
                SpatialStructureBEVOccupancy.largest(SpatialStructureBEVOccupancy.grid(points)),
                floorHeight: 0
            ),
            epsilon: SpatialStructureBEVOccupancy.simplifyMeters
        )
        XCTAssertGreaterThanOrEqual(contour.count, 4)
        let closed = contour + [contour[0]]
        let long = zip(closed, closed.dropFirst()).map { simd_distance($0, $1) }.filter { $0 > 1 }
        XCTAssertGreaterThanOrEqual(long.count, 4, "BEV occupancy must recover the four room sides, not ARKit meshes")
    }

    func testAllFloorLabelsDoNotInventOccupancyWallsOnSyntheticRoom() {
        var doc = SpatialScanDocument()
        doc.surfaces = [
            SpatialSurface(id: "floor", kind: "horizontal", vertices: [
                SpatialPoint(x: 0, y: 0, z: 0), SpatialPoint(x: 4, y: 0, z: 0),
                SpatialPoint(x: 4, y: 0, z: 3), SpatialPoint(x: 0, y: 0, z: 3)
            ], epoch: 1),
            SpatialSurface(id: "west", kind: "vertical", vertices: [
                SpatialPoint(x: 0, y: 0, z: 0), SpatialPoint(x: 0, y: 2, z: 0),
                SpatialPoint(x: 0, y: 2, z: 3), SpatialPoint(x: 0, y: 0, z: 3)
            ], epoch: 1)
        ]
        doc.keyframes = (0..<4).map { i in
            SpatialKeyframe(
                id: "f\(i)", time: Double(i),
                pose: SpatialPose(time: Double(i), position: SpatialPoint(x: 2, y: 1, z: 1.5), forward: SpatialPoint(x: 0, y: 0, z: -1), epoch: 1),
                intrinsics: [80, 0, 0, 0, 80, 0, 50, 50, 1], width: 100, height: 100, imageFile: "f\(i).jpg",
                cameraToWorld: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 2, 1, 1.5, 1]
            )
        }
        doc.indoorSemanticObservations = doc.keyframes.map { frame in
            var row = SpatialIndoorSemanticObservation(frameID: frame.id, frameTime: frame.time, epoch: 1, modelID: "synthetic")
            row.status = .processed; row.width = 128; row.height = 128
            row.labels = Data(repeating: 3, count: 128 * 128)
            row.confidence = Data(repeating: 230, count: 128 * 128)
            row.taxonomy = SpatialSurfaceSemanticTaxonomy(
                configSHA256: String(repeating: "a", count: 64), wallLabelID: 0, floorLabelID: 3, verified: true
            )
            row.records = doc.surfaces.map { surface in
                var record = SpatialSurfaceSemanticRecord(
                    surfaceID: surface.id, surfaceVertices: surface.vertices, epoch: 1,
                    frameID: frame.id, frameTime: frame.time, modelID: "synthetic",
                    configSHA256: String(repeating: "a", count: 64)
                )
                record.status = .sampled; record.totalSamples = 20
                if surface.kind == "horizontal" { record.floorSupport = 20 } else { record.wallSupport = 20 }
                return record
            }
            return row
        }
        let map = SpatialStructureP0Builder.build(document: doc).map
        XCTAssertFalse(map.wallSegments.contains { $0.verticalTrackID.hasPrefix("occupancy-") })
        XCTAssertFalse(map.wallSegments.contains { $0.verticalTrackID.hasPrefix("stereo-") })
    }
}
