import XCTest
@testable import VQASee

final class SpatialStructureDesignTests: XCTestCase {
    func testThreeOrthogonalPlanesMeetAtOrigin() throws {
        let floor = SpatialPlaneCornerSolver.Plane(normal: SIMD3(0, 1, 0), offset: 0)
        let wallX = SpatialPlaneCornerSolver.Plane(normal: SIMD3(1, 0, 0), offset: 0)
        let wallZ = SpatialPlaneCornerSolver.Plane(normal: SIMD3(0, 0, 1), offset: 0)
        let p = try SpatialPlaneCornerSolver.intersect(wallX, floor, wallZ)
        XCTAssertEqual(p.x, 0, accuracy: 0.001)
        XCTAssertEqual(p.y, 0, accuracy: 0.001)
        XCTAssertEqual(p.z, 0, accuracy: 0.001)
    }
}
