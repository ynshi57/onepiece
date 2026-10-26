import XCTest
@testable import VQASee

final class SpatialPlaneTrackAssociationTests: XCTestCase {
    private func quad(y: Float, id: String, kind: String, offset: Float = 0) -> SpatialSurface {
        SpatialSurface(
            id: id,
            kind: kind,
            vertices: [
                SpatialPoint(x: offset, y: y, z: 0),
                SpatialPoint(x: 1 + offset, y: y, z: 0),
                SpatialPoint(x: 1 + offset, y: y, z: 1),
                SpatialPoint(x: offset, y: y, z: 1),
            ],
            observed: true,
            epoch: 2
        )
    }

    private func wall(x: Float, z0: Float = 0, z1: Float = 1, id: String, reverse: Bool = false) -> SpatialSurface {
        let vertices = reverse
            ? [
                SpatialPoint(x: x, y: 0, z: z1),
                SpatialPoint(x: x, y: 1, z: z1),
                SpatialPoint(x: x, y: 1, z: z0),
                SpatialPoint(x: x, y: 0, z: z0),
            ]
            : [
                SpatialPoint(x: x, y: 0, z: z0),
                SpatialPoint(x: x, y: 1, z: z0),
                SpatialPoint(x: x, y: 1, z: z1),
                SpatialPoint(x: x, y: 0, z: z1),
            ]
        return SpatialSurface(id: id, kind: "vertical", vertices: vertices, observed: true, epoch: 2)
    }

    func testCoplanarVerticalPatchesMergeIntoOneTrack() {
        let surfaces = [wall(x: 0, id: "a"), wall(x: 0.05, id: "b")]
        let tracks = SpatialPlaneTrackAssociation.associate(surfaces: surfaces)
        XCTAssertEqual(tracks.filter { $0.kind == "vertical" }.count, 1)
        XCTAssertEqual(Set(tracks[0].memberSurfaceIDs), Set(["a", "b"]))
    }

    func testCoplanarPatches1_8mApartMergeIntoOneTrack() {
        let surfaces = [wall(x: 0, z0: 0, z1: 1, id: "near"), wall(x: 0, z0: 1.8, z1: 2.8, id: "far")]
        let tracks = SpatialPlaneTrackAssociation.associate(surfaces: surfaces)
        XCTAssertEqual(tracks.filter { $0.kind == "vertical" }.count, 1)
        XCTAssertEqual(Set(tracks[0].memberSurfaceIDs), Set(["near", "far"]))
    }

    func testNineCentimeterOffsetStaysTwoTracks() {
        let tracks = SpatialPlaneTrackAssociation.associate(surfaces: [wall(x: 0, id: "a"), wall(x: 0.09, id: "b")])
        XCTAssertEqual(tracks.filter { $0.kind == "vertical" }.count, 2)
    }

    func testParallelSameFacingWallsStaySeparate() {
        let tracks = SpatialPlaneTrackAssociation.associate(surfaces: [wall(x: 0, id: "west"), wall(x: 3, id: "east-same")])
        XCTAssertEqual(tracks.filter { $0.kind == "vertical" }.count, 2)
    }

    func testOppositeInwardWallsStaySeparate() {
        let tracks = SpatialPlaneTrackAssociation.associate(surfaces: [wall(x: 0, id: "west"), wall(x: 3, id: "east", reverse: true)])
        XCTAssertEqual(tracks.filter { $0.kind == "vertical" }.count, 2)
    }

    func testFloorAndWallStaySeparate() {
        let surfaces = [quad(y: 0, id: "floor", kind: "horizontal"), quad(y: 0, id: "wall", kind: "vertical")]
        let tracks = SpatialPlaneTrackAssociation.associate(surfaces: surfaces)
        XCTAssertEqual(tracks.count, 2)
    }

    func testCornerSolverUsesMergedWallTracksAndFloor() throws {
        let surfaces = [
            SpatialSurface(
                id: "wx",
                kind: "vertical",
                vertices: [
                    SpatialPoint(x: 0, y: 0, z: 0),
                    SpatialPoint(x: 0, y: 2, z: 0),
                    SpatialPoint(x: 0, y: 2, z: 2),
                    SpatialPoint(x: 0, y: 0, z: 2),
                ],
                observed: true,
                epoch: 2
            ),
            SpatialSurface(
                id: "wz",
                kind: "vertical",
                vertices: [
                    SpatialPoint(x: 0, y: 0, z: 0),
                    SpatialPoint(x: 2, y: 0, z: 0),
                    SpatialPoint(x: 2, y: 2, z: 0),
                    SpatialPoint(x: 0, y: 2, z: 0),
                ],
                observed: true,
                epoch: 2
            ),
            quad(y: 0, id: "floor", kind: "horizontal"),
        ]
        let tracks = SpatialPlaneTrackAssociation.associate(surfaces: surfaces)
        let verticals = tracks.filter { $0.kind == "vertical" }
        let floors = tracks.filter { $0.kind == "horizontal" }
        XCTAssertEqual(verticals.count, 2)
        XCTAssertEqual(floors.count, 1)
        let floorPlane = SpatialPlaneCornerSolver.Plane(normal: SIMD3(0, 1, 0), offset: 0)
        let wallX = SpatialPlaneCornerSolver.Plane(normal: SIMD3(1, 0, 0), offset: 0)
        let wallZ = SpatialPlaneCornerSolver.Plane(normal: SIMD3(0, 0, 1), offset: 0)
        let corner = try SpatialPlaneCornerSolver.intersect(wallX, floorPlane, wallZ)
        XCTAssertEqual(corner.x, 0, accuracy: 0.02)
        XCTAssertEqual(corner.y, 0, accuracy: 0.02)
        XCTAssertEqual(corner.z, 0, accuracy: 0.02)
    }
}
