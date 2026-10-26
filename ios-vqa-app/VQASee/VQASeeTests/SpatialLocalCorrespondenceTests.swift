import XCTest
import CoreGraphics
import simd
@testable import VQASee

final class SpatialLocalCorrespondenceTests: XCTestCase {
    private func requireLearnedFlowRuntime() throws {
        #if targetEnvironment(simulator) && arch(x86_64)
        throw XCTSkip("Intel simulator lacks Vision cnn_moflow.espresso.weights; run the real-network device calibration on iPhone")
        #endif
    }

    /// Real revision-2 Vision network, no mocked flow. Raw raster rows increase downwards.
    func testLearnedFlowTranslationUsesNativeTopLeftCoordinatesInBothDirections() throws {
        try requireLearnedFlowRuntime()
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

    func testPhotometricRefinementCorrectsUnderestimatedProposalWithoutScalingFlow() throws {
        let source = try texture(width:384,height:256,dx:0,dy:0)
        let target = try texture(width:384,height:256,dx:12,dy:8)
        var originalCandidates: [(pixel:[Float],displacement:[Float]?)] = []
        for y in stride(from:40,through:200,by:40) { for x in stride(from:40,through:320,by:40) {
            let pixel: [Float] = [Float(x),Float(y)]
            let result = try SpatialLocalCorrespondence.refinedDisplacement(source:source,target:target,pixel:pixel,proposal:[7.2,5.3])
            originalCandidates.append((pixel,result))
        }}
        // Exact fixture analysis: 25 low-texture patches, 12 ambiguous peaks, 3 unique.
        // Retain all 40 original positions; demanding >4 would require accepting ambiguity.
        XCTAssertEqual(originalCandidates.count,40)
        let originalAccepted = originalCandidates.compactMap(\.displacement)
        XCTAssertEqual(originalAccepted.count,3)
        XCTAssertEqual(originalCandidates.filter { $0.displacement == nil }.count,37)
        for result in originalAccepted {
            XCTAssertEqual(result[0],12,accuracy:0.5)
            XCTAssertEqual(result[1],8,accuracy:0.5)
        }

        // The actual product grid covers the full native image, including rejected borders.
        // The fixture, proposal, photometric gates and accepted-point accuracy stay unchanged.
        var fullGrid: [(pixel:[Float],displacement:[Float]?)] = []
        for y in 0..<48 { for x in 0..<64 {
            let pixel: [Float] = [(Float(x)+0.5)*384/64-0.5,(Float(y)+0.5)*256/48-0.5]
            let result = try SpatialLocalCorrespondence.refinedDisplacement(source:source,target:target,pixel:pixel,proposal:[7.2,5.3])
            fullGrid.append((pixel,result))
        }}
        if let bad = fullGrid.first(where:{ candidate in
            guard let displacement = candidate.displacement else { return false }
            return abs(displacement[0]-12) > 0.5 || abs(displacement[1]-8) > 0.5
        }) {
            let trace = try SpatialLocalCorrespondence.refinementDebugScores(source:source,target:target,pixel:bad.pixel,proposal:[7.2,5.3],truth:[12,8])
            print("NCC_OBJECTIVE_TRACE",String(data:try JSONSerialization.data(withJSONObject:trace,options:.sortedKeys),encoding:.utf8) ?? "unavailable")
        }
        XCTAssertEqual(fullGrid.count,64*48)
        let gridAccepted = fullGrid.compactMap(\.displacement)
        XCTAssertGreaterThan(gridAccepted.count,4)
        XCTAssertEqual(gridAccepted.count+fullGrid.filter { $0.displacement == nil }.count,3072)
        for result in gridAccepted {
            XCTAssertEqual(result[0],12,accuracy:0.5)
            XCTAssertEqual(result[1],8,accuracy:0.5)
        }
    }

    func testRefinementRejectsTruncatedSearchBeforeAcceptingFalseEdgePeak() throws {
        let source = try texture(width:384,height:256,dx:0,dy:0)
        let target = try texture(width:384,height:256,dx:12,dy:8)
        // Previously accepted (10.46875,8) although true (12,8) patch was outside target.
        let result = try SpatialLocalCorrespondence.refinementOutcome(source:source,target:target,
            pixel:[368.5,140.8333],proposal:[7.2,5.3])
        XCTAssertNil(result.displacement)
        XCTAssertEqual(result.state,.outsideView)
        var mask = [Bool](repeating:true,count:384*256)
        mask[85*384+127] = false
        let hole = try SpatialLocalCorrespondence.refinementOutcome(source:source,target:target,
            pixel:[120,80],proposal:[7.2,5.3],targetValidMask:mask)
        XCTAssertNil(hole.displacement)
        XCTAssertEqual(hole.state,.outsideView,"A warp hole anywhere in the full search footprint invalidates peak uniqueness")
    }

    func testLowTextureReasonPrecedesTargetSearchBoundary() throws {
        let data = Data(repeating:255,count:384*256*4)
        let provider = try XCTUnwrap(CGDataProvider(data:data as CFData))
        let flat = try XCTUnwrap(CGImage(width:384,height:256,bitsPerComponent:8,bitsPerPixel:32,bytesPerRow:384*4,
            space:CGColorSpaceCreateDeviceRGB(),bitmapInfo:CGBitmapInfo(rawValue:CGImageAlphaInfo.last.rawValue),
            provider:provider,decode:nil,shouldInterpolate:false,intent:.defaultIntent))
        let result = try SpatialLocalCorrespondence.refinementOutcome(source:flat,target:flat,
            pixel:[368.5,140.8333],proposal:[7.2,5.3])
        XCTAssertNil(result.displacement)
        XCTAssertEqual(result.state,.lowTexture)
    }

    func testRotationWarpKeepsInvalidFootprintsUnknown() throws {
        let source = try texture(width:64,height:48,dx:0,dy:0)
        let h = simd_float3x3(columns:(SIMD3(1,0,0),SIMD3(0,1,0),SIMD3(5,0,1)))
        let warped = try SpatialLocalCorrespondence.rotationWarp(image:source,width:64,height:48,homography:h)
        XCTAssertEqual(warped.valid.count,64*48)
        XCTAssertEqual(warped.valid.filter { $0 }.count,58*47)
        XCTAssertTrue(warped.valid[10*64+10])
        XCTAssertFalse(warped.valid[10*64+58])
        XCTAssertFalse(warped.valid[47*64+10])
        let behind = simd_float3x3(diagonal:SIMD3<Float>(1,1,-1))
        let invalid = try SpatialLocalCorrespondence.rotationWarp(image:source,width:64,height:48,homography:behind)
        XCTAssertTrue(invalid.valid.allSatisfy { !$0 })
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
        try requireLearnedFlowRuntime()
        let matcher = SpatialLocalCorrespondence()
        let first = try await matcher.calibrate()
        let cached = try await matcher.calibrate()
        XCTAssertEqual(first.elapsedSeconds, cached.elapsedSeconds)
        XCTAssertGreaterThan(first.forwardMatchCount, 15)
        XCTAssertGreaterThan(first.reverseMatchCount, 15)
        XCTAssertGreaterThan(first.triangulatedSupportCount, 15)
        XCTAssertLessThanOrEqual(first.medianDepthErrorMeters, 0.15)
        XCTAssertEqual(first.depthGridCandidateCount,3072)
        XCTAssertGreaterThan(first.depthGridValidCount,15)
        XCTAssertEqual(first.depthGridReasonCounts.values.reduce(0,+),3072)
        XCTAssertGreaterThan(first.depthGridTriangulatedCount,15)
        XCTAssertLessThanOrEqual(first.depthGridMedianDepthErrorMeters,0.15)
        XCTAssertEqual(first.triangulatedSupportCount, cached.triangulatedSupportCount)
        XCTAssertEqual(first.medianDepthErrorMeters, cached.medianDepthErrorMeters)
        XCTAssertEqual(first.forwardMedianPixels[0], 24, accuracy: 2)
        XCTAssertEqual(first.forwardMedianPixels[1], 16, accuracy: 2)
        XCTAssertEqual(first.reverseMedianPixels[0], -24, accuracy: 2)
        XCTAssertEqual(first.reverseMedianPixels[1], -16, accuracy: 2)
    }

    func testDepthGridRetainsEveryCandidateIncludingRejectedBorders() throws {
        try requireLearnedFlowRuntime()
        let source = try texture(width:768,height:512,dx:0,dy:0)
        let target = try texture(width:768,height:512,dx:24,dy:16)
        let grid = try SpatialLocalCorrespondence.depthGrid(source:source,target:target,nativeWidth:768,nativeHeight:512)
        XCTAssertEqual(grid.count,64*48)
        XCTAssertGreaterThan(grid.filter { $0.state == .valid }.count,15)
        XCTAssertTrue(grid.allSatisfy { ($0.state == .valid) == ($0.match != nil) })
        XCTAssertTrue(grid.contains { $0.state != .valid }, "Translation moves some source cells outside the target")
        let matches = grid.compactMap(\.match)
        let dx = matches.map { ($0.targetPixel[0]-$0.sourcePixel[0])*768 }.sorted()
        let dy = matches.map { ($0.targetPixel[1]-$0.sourcePixel[1])*512 }.sorted()
        if !matches.isEmpty {
            XCTAssertEqual(dx[dx.count/2],24,accuracy:2)
            XCTAssertEqual(dy[dy.count/2],16,accuracy:2)
        }
        let cameras = SpatialLocalCorrespondence.calibrationFrames()
        let supports = matches.compactMap { SpatialObservationGeometry.triangulate($0,source:cameras.source,target:cameras.target) }
        XCTAssertGreaterThan(supports.count,15)
        let errors = supports.map { abs($0.position.z+3) }.sorted()
        if !errors.isEmpty { XCTAssertLessThanOrEqual(errors[errors.count/2],0.15) }
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
