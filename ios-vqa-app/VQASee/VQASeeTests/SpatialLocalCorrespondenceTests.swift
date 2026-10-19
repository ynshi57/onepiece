import XCTest
import CoreGraphics
@testable import VQASee

final class SpatialLocalCorrespondenceTests: XCTestCase {
    /// Real revision-2 Vision network, no mocked flow. Raw raster rows increase downwards.
    func testLearnedFlowTranslationUsesNativeTopLeftCoordinatesInBothDirections() throws {
        let width = 768, height = 512, dx = 24, dy = 16
        let source = try texture(width: width, height: height, dx: 0, dy: 0)
        let target = try texture(width: width, height: height, dx: dx, dy: dy)
        for reverse in [false,true] {
            let matches = try SpatialLocalCorrespondence.matches(source: reverse ? target : source,
                target: reverse ? source : target, nativeWidth: width, nativeHeight: height)
            XCTAssertGreaterThan(matches.count, 15, "A known textured translation must yield usable network correspondences")
            XCTAssertLessThanOrEqual(matches.count, 150)
            let sign: Float = reverse ? -1 : 1
            let x = matches.map { ($0.targetPixel[0] - $0.sourcePixel[0]) * Float(width) }.sorted()
            let y = matches.map { ($0.targetPixel[1] - $0.sourcePixel[1]) * Float(height) }.sorted()
            guard !matches.isEmpty else { continue }
            XCTAssertEqual(x[x.count/2], sign * Float(dx), accuracy: 2, "Positive X means right in original JPEG pixels")
            XCTAssertEqual(y[y.count/2], sign * Float(dy), accuracy: 2, "Positive Y means down; scaled flow must map to native units")
            XCTAssertTrue(matches.allSatisfy { $0.cycleErrorPixels.isFinite && $0.cycleErrorPixels <= 1.5 })
            for match in matches {
                // Undo the native pixel-centre mapping before checking the work-image sampling grid.
                let workX = (match.sourcePixel[0] * 768 + 0.5) / 2 - 0.5
                let workY = (match.sourcePixel[1] * 512 + 0.5) / 2 - 0.5
                let gridX = workX * 16 / 383
                let gridY = workY * 11 / 255
                XCTAssertEqual(gridX, gridX.rounded(), accuracy: 0.0001)
                XCTAssertEqual(gridY, gridY.rounded(), accuracy: 0.0001)
            }
        }
    }

    func testResizePixelCentresMapToAbsoluteNativeKCoordinates() {
        let scaled = SpatialLocalCorrespondence.nativeCoordinate(pixelIndex: 10, workingExtent: 384, nativeExtent: 768)
        XCTAssertEqual(scaled * 768, 20.5, accuracy: 0.00001)
        let identity = SpatialLocalCorrespondence.nativeCoordinate(pixelIndex: 10, workingExtent: 384, nativeExtent: 384)
        XCTAssertEqual(identity * 384, 10, accuracy: 0.00001)
        // Known projection: fx=100, cx=20.5, pixel20.5 corresponds to the optical axis.
        XCTAssertEqual((scaled * 768 - 20.5) / 100, 0, accuracy: 0.000001)
        let origin = SpatialLocalCorrespondence.nativeCoordinate(pixelIndex: 0, workingExtent: 384, nativeExtent: 768)
        XCTAssertEqual(origin * 768, 0.5, accuracy: 0.00001)
    }

    func testSyntheticCalibrationCameraTranslationSignsRecoverKnownPlane() throws {
        let cameras = SpatialLocalCorrespondence.calibrationFrames()
        let match = SpatialImageMatch(sourcePixel: [384.0/768,256.0/512],
            targetPixel: [408.0/768,272.0/512], cycleErrorPixels: 0)
        let support = try XCTUnwrap(SpatialObservationGeometry.triangulate(match, source: cameras.source, target: cameras.target))
        XCTAssertEqual(support.position.x, 0, accuracy: 0.001)
        XCTAssertEqual(support.position.y, 0, accuracy: 0.001)
        XCTAssertEqual(support.position.z, -3, accuracy: 0.001)
        XCTAssertLessThan(support.reprojectionErrorPixels, 0.01)
    }

    func testActorCalibrationCachesVerifiedNetworkConvention() async throws {
        let matcher = SpatialLocalCorrespondence()
        let first = try await matcher.calibrate()
        let cached = try await matcher.calibrate()
        XCTAssertEqual(first.elapsedSeconds, cached.elapsedSeconds)
        XCTAssertGreaterThan(first.forwardMatchCount, 15)
        XCTAssertGreaterThan(first.reverseMatchCount, 15)
        XCTAssertGreaterThan(first.triangulatedSupportCount, 15)
        XCTAssertLessThanOrEqual(first.medianDepthErrorMeters, 0.15)
        XCTAssertEqual(first.triangulatedSupportCount, cached.triangulatedSupportCount)
        XCTAssertEqual(first.medianDepthErrorMeters, cached.medianDepthErrorMeters)
        XCTAssertEqual(first.forwardMedianPixels[0], 24, accuracy: 2)
        XCTAssertEqual(first.forwardMedianPixels[1], 16, accuracy: 2)
        XCTAssertEqual(first.reverseMedianPixels[0], -24, accuracy: 2)
        XCTAssertEqual(first.reverseMedianPixels[1], -16, accuracy: 2)
    }

    func testMismatchedRasterDimensionsFailExplicitly() throws {
        let a = try texture(width: 192, height: 128, dx: 0, dy: 0)
        let b = try texture(width: 128, height: 192, dx: 0, dy: 0)
        XCTAssertThrowsError(try SpatialLocalCorrespondence.matches(source: a, target: b, nativeWidth: 192, nativeHeight: 128))
    }

    private func texture(width: Int, height: Int, dx: Int, dy: Int) throws -> CGImage {
        var bytes = [UInt8](repeating: 0, count: width * height * 4)
        for y in 0..<height {
            for x in 0..<width {
                let sx = x-dx, sy = y-dy, offset = (y*width+x)*4
                if sx >= 0, sy >= 0, sx < width, sy < height {
                    // Stable asymmetric multiscale texture; translation is defined in native raster pixels.
                    let tile = (sx/23 * 73 + sy/31 * 151 + (sx/61)*(sy/47)*29) % 211
                    bytes[offset] = UInt8(20 + tile)
                    bytes[offset+1] = UInt8(20 + (tile*3 + sx/7) % 211)
                    bytes[offset+2] = UInt8(20 + (tile*7 + sy/9) % 211)
                }
                bytes[offset+3] = 255
            }
        }
        let provider = try XCTUnwrap(CGDataProvider(data: Data(bytes) as CFData))
        return try XCTUnwrap(CGImage(width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 32,
            bytesPerRow: width*4, space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.last.rawValue),
            provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent))
    }
}
