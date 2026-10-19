import XCTest
@testable import VQASee

final class SpatialObjectCoordinatesTests: XCTestCase {
    func testAsymmetricBoundingBoxReturnsToNativeCoordinates() {
        let box: [Float] = [0.1,0.2,0.3,0.6]
        let expected: [[Float]] = [box,[0.4,0.1,0.8,0.3],[0.7,0.4,0.9,0.8],[0.2,0.7,0.6,0.9]]
        for turns in 0..<4 {
            let native = SpatialObjectCoordinates.nativeBox(box, turns: turns)
            for i in 0..<4 { XCTAssertEqual(native[i], expected[turns][i], accuracy: 0.00001) }
            let restored = SpatialObjectCoordinates.nativeBox(native, turns: (4-turns)%4)
            for i in 0..<4 { XCTAssertEqual(restored[i], box[i], accuracy: 0.00001) }
        }
    }
    func testRealDeviceEdgeOverflowPreservesVisibleIntersection() {
        let raw: [Float] = [0.5197754,-0.00012207031,0.99975586,0.47277832]
        XCTAssertEqual(SpatialObjectCoordinates.visibleBox(raw), [raw[0],0,raw[2],raw[3]])
        XCTAssertNil(SpatialObjectCoordinates.visibleBox([1.1,0.2,1.2,0.8]))
        XCTAssertNil(SpatialObjectCoordinates.visibleBox([0.1,0.2,0.1,0.8]))
        XCTAssertNil(SpatialObjectCoordinates.visibleBox([.nan,0,1,1]))
    }

    func testSurfaceEvidencePixelCentresDisplayInAllFourOrientations() throws {
        // Non-square native raster. Geometry stores pixel-index / raster extent.
        let expected: [[Float]] = [[0.1005,0.201],[0.201,0.8995],[0.8995,0.799],[0.799,0.1005]]
        for turns in 0..<4 {
            let point = try XCTUnwrap(SpatialObjectSupportPresentation.displayPixel([0.1,0.2], width: 1000, height: 500, turns: turns))
            XCTAssertEqual(point[0],expected[turns][0],accuracy:0.00001)
            XCTAssertEqual(point[1],expected[turns][1],accuracy:0.00001)
        }
    }

}
