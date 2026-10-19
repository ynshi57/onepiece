import XCTest
@testable import VQASee

final class SpatialHorizontalObservationTests: XCTestCase {
    private func point(_ x: Float, _ z: Float) -> SpatialPoint { SpatialPoint(x: x, y: 0.7, z: z) }
    func testConcaveRawBoundaryDoesNotFillMissingCorner() throws {
        let polygon = [point(0,0),point(3,0),point(3,1),point(1,1),point(1,3),point(0,3)]
        for vertices in [polygon, Array(polygon.reversed()), polygon + [polygon[0]]] {
            let triangles = try XCTUnwrap(SpatialHorizontalObservation.triangles(vertices))
            var area: Float = 0
            for i in stride(from: 0, to: triangles.count, by: 3) {
                let a = vertices[Int(triangles[i])], b = vertices[Int(triangles[i+1])], c = vertices[Int(triangles[i+2])]
                area += abs((b.x-a.x)*(c.z-a.z)-(b.z-a.z)*(c.x-a.x))/2
            }
            XCTAssertEqual(area, 5, accuracy: 0.00001)
        }
    }
    func testSharedConsumerKeepsEntireObservedPolygonWithoutTrajectory() {
        let raw = [point(-10,-8),point(15,-8),point(15,12),point(-10,12)]
        let measured = SpatialSurface(id: "horizontal", kind: "horizontal", vertices: raw)
        var unobserved = measured; unobserved.id = "unobserved"; unobserved.observed = false
        var wall = measured; wall.kind = "vertical"
        var invalid = measured; invalid.vertices[0].x = .nan
        let selected = SpatialHorizontalObservation.surfaces([measured,unobserved,wall,invalid])
        XCTAssertEqual(selected.map(\.id), ["horizontal"])
        XCTAssertEqual(selected.first?.vertices, raw)
        XCTAssertNotNil(SpatialHorizontalObservation.triangles(raw))
    }
    func testSelfIntersectingBoundaryKeepsOnlyOutline() {
        XCTAssertNil(SpatialHorizontalObservation.triangles([point(0,0),point(3,3),point(0,3),point(3,0),point(4,1)]))
    }
    func testDegenerateBoundaryCannotInventFilledArea() {
        XCTAssertNil(SpatialHorizontalObservation.triangles([point(0,0),point(1,0),point(2,0)]))
    }
}
