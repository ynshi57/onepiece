import XCTest
@testable import VQASee

final class SpatialSceneHypothesisTests: XCTestCase {
    private func fixture() -> SpatialScanDocument {
        let c: Float = sqrt(0.5), s: Float = -sqrt(0.5)
        var document = SpatialScanDocument()
        document.surfaces = [SpatialSurface(id: "shell", kind: "horizontal", vertices: [
            SpatialPoint(x: -5, y: 0, z: -5), SpatialPoint(x: 5, y: 0, z: -5),
            SpatialPoint(x: 5, y: 0, z: 5), SpatialPoint(x: -5, y: 0, z: 5),
        ], epoch: 0)]
        document.keyframes = (0 ..< 2).map { i in
            SpatialKeyframe(
                id: "f\(i)", time: Double(i),
                pose: SpatialPose(time: Double(i), position: SpatialPoint(x: 0, y: 1.5, z: 0), forward: SpatialPoint(x: 0, y: s, z: -c), epoch: 0),
                intrinsics: [500, 0, 0, 0, 500, 0, 160, 120, 1], width: 320, height: 240, imageFile: "f\(i).jpg",
                cameraToWorld: [1, 0, 0, 0, 0, c, s, 0, 0, -s, c, 0, 0, 1.5, 0, 1])
        }
        let surface = document.surfaces[0]
        document.indoorSemanticObservations = document.keyframes.map { frame in
            var row = SpatialIndoorSemanticObservation(frameID: frame.id, frameTime: frame.time, epoch: 0, modelID: "synthetic")
            row.status = .processed
            row.width = 32
            row.height = 24
            row.labels = Data(repeating: 3, count: 768)
            row.confidence = Data(repeating: 0, count: 768)
            for y in 18 ..< 21 { for x in 14 ..< 18 { row.confidence[y * 32 + x] = 230 } }
            row.taxonomy = SpatialSurfaceSemanticTaxonomy(configSHA256: String(repeating: "a", count: 64), wallLabelID: 0, floorLabelID: 3, verified: true)
            var record = SpatialSurfaceSemanticRecord(
                surfaceID: surface.id, surfaceVertices: surface.vertices, epoch: 0,
                frameID: frame.id, frameTime: frame.time, modelID: row.modelID, configSHA256: row.taxonomy!.configSHA256)
            record.status = .sampled
            record.floorSupport = 20
            record.totalSamples = 20
            row.records = [record]
            return row
        }
        return document
    }

    func testRebuildDoesNotPublishFloorTiles() {
        let document = fixture()
        let result = SpatialSceneHypothesis.rebuild(document: document)
        XCTAssertEqual(result.unknownHorizontals, document.surfaces)
        XCTAssertTrue(result.walls.isEmpty)
        XCTAssertTrue(result.publishedStatusLine.contains("StructureMap"))
    }

    func testNoSemanticsMeansUnknownShellOnly() {
        var document = fixture()
        document.indoorSemanticObservations = nil
        let result = SpatialSceneHypothesis.rebuild(document: document)
        XCTAssertEqual(result.unknownHorizontals, document.surfaces)
        XCTAssertTrue(result.walls.isEmpty)
    }

    func testLowVerticalCannotBePromotedAndShellDoesNotExtendWall() {
        var document = fixture()
        document.indoorSemanticObservations = nil
        let wall = SpatialSurface(
            id: "wall", kind: "vertical",
            vertices: [
                SpatialPoint(x: 0, y: 0, z: -2), SpatialPoint(x: 1, y: 0, z: -2),
                SpatialPoint(x: 1, y: 2, z: -2), SpatialPoint(x: 0, y: 2, z: -2),
            ], epoch: 0)
        var low = wall
        low.id = "low"
        low.vertices = low.vertices.map { SpatialPoint(x: $0.x + 3, y: $0.y / 4, z: $0.z) }
        document.surfaces += [wall, low]
        let withShell = SpatialSceneHypothesis.rebuild(document: document)
        document.surfaces.removeFirst()
        let withoutShell = SpatialSceneHypothesis.rebuild(document: document)
        XCTAssertEqual(withShell.walls, withoutShell.walls)
        XCTAssertTrue(withShell.walls.allSatisfy { !$0.confirmed })
        XCTAssertFalse(withShell.walls.contains { $0.surfaceIDs.contains("low") })
        XCTAssertTrue(withShell.objects.contains { $0.surfaceIDs.contains("low") })
    }
}
