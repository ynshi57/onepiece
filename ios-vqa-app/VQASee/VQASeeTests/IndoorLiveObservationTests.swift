import XCTest
@testable import VQASee

final class IndoorLiveObservationTests: XCTestCase {
    func testRepeatedFrameDoesNotAccumulateEvidence() {
        var filter = IndoorDepthObservationFilter()
        for _ in 0..<20 { XCTAssertEqual(filter.offer(expected: 1, samples: Array(repeating: 1.3, count: 9), timestamp: 1), .observing) }
    }
    func testStableGeometrySeparatesOcclusionFromMissingSurface() {
        for (depth, expectedStatus) in [(Float(0.7), IndoorLiveObservation.obstructionEvidence), (1.3, .originalSurfaceNotObserved), (1, .surfaceConsistent)] {
            var filter = IndoorDepthObservationFilter()
            var result = IndoorLiveObservation.observing
            for index in 0...5 { result = filter.offer(expected: 1, samples: Array(repeating: depth, count: 9), timestamp: 1 + Double(index)*0.2) }
            XCTAssertEqual(result, expectedStatus)
            XCTAssertEqual(filter.offer(expected: 1, samples: [], timestamp: 3), .noDepth)
            XCTAssertEqual(filter.offer(expected: 1, samples: Array(repeating: depth, count: 9), timestamp: 3.2), .observing)
        }
    }
    func testInvalidOrMixedDepthNeverMeansAbsent() {
        var filter = IndoorDepthObservationFilter()
        XCTAssertEqual(filter.offer(expected: 1, samples: Array(repeating: .nan, count: 9), timestamp: 1), .noDepth)
        XCTAssertEqual(filter.offer(expected: 1, samples: [0.5,0.5,0.5,1,1,2,2,2,2], timestamp: 2), .noDepth)
        XCTAssertEqual(filter.offer(expected: -1, samples: Array(repeating: 2, count: 9), timestamp: 3), .noDepth)
    }
}
