import XCTest
import CoreGraphics
import CoreVideo
@testable import VQASee

final class SpatialLiveObjectTests: XCTestCase {
    private let viewport = CGSize(width:400,height:800)

    func testFreshnessIncludesCaptureAgeRatherThanCompletionAge() {
        XCTAssertTrue(SpatialLiveObjectPresentation.isFresh(capturedAt:10,now:10))
        XCTAssertTrue(SpatialLiveObjectPresentation.isFresh(capturedAt:10,now:10.34))
        XCTAssertFalse(SpatialLiveObjectPresentation.isFresh(capturedAt:10,now:10.36))
        XCTAssertFalse(SpatialLiveObjectPresentation.isFresh(capturedAt:11,now:10))
        XCTAssertFalse(SpatialLiveObjectPresentation.isFresh(capturedAt:.nan,now:10))
        XCTAssertFalse(SpatialLiveObjectPresentation.isFresh(capturedAt:10,now:.infinity))
    }

    func testIdentityAndQuarterTurnUseAllFourCorners() throws {
        let box: [Float] = [0.1,0.2,0.4,0.6]
        let identity = try XCTUnwrap(SpatialLiveObjectPresentation.viewportBox(box,transform:[1,0,0,1,0,0],recordedViewport:[400,800],viewport:viewport))
        XCTAssertEqual(identity.minX,40,accuracy:0.001)
        XCTAssertEqual(identity.minY,160,accuracy:0.001)
        XCTAssertEqual(identity.width,120,accuracy:0.001)
        XCTAssertEqual(identity.height,320,accuracy:0.001)
        let rotated = try XCTUnwrap(SpatialLiveObjectPresentation.viewportBox(box,transform:[0,1,-1,0,1,0],recordedViewport:[400,800],viewport:viewport))
        XCTAssertEqual(rotated.minX,160,accuracy:0.001)
        XCTAssertEqual(rotated.minY,80,accuracy:0.001)
        XCTAssertEqual(rotated.width,160,accuracy:0.001)
        XCTAssertEqual(rotated.height,240,accuracy:0.001)
        // Shear needs off-diagonal corners: transformed opposite corners alone lose the x extent.
        let shear = try XCTUnwrap(SpatialLiveObjectPresentation.viewportBox(box,transform:[1,0,-1,1,0.5,0],recordedViewport:[400,800],viewport:viewport))
        XCTAssertEqual(shear.minX,0,accuracy:0.001)
        XCTAssertEqual(shear.maxX,280,accuracy:0.001)
    }

    func testClipsOnlyVisibleIntersection() throws {
        let clipped = try XCTUnwrap(SpatialLiveObjectPresentation.viewportBox([0.1,0.2,0.4,0.6],transform:[1,0,0,1,-0.2,0],recordedViewport:[400,800],viewport:viewport))
        XCTAssertEqual(clipped.minX,0,accuracy:0.001)
        XCTAssertEqual(clipped.maxX,80,accuracy:0.001)
        XCTAssertNil(SpatialLiveObjectPresentation.viewportBox([0.1,0.2,0.4,0.6],transform:[1,0,0,1,2,0],recordedViewport:[400,800],viewport:viewport))
    }

    func testRejectsViewportChangesAndInvalidMapping() {
        let b: [Float] = [0.1,0.2,0.4,0.6], identity: [Float] = [1,0,0,1,0,0]
        XCTAssertNil(SpatialLiveObjectPresentation.viewportBox(b,transform:identity,recordedViewport:[800,400],viewport:viewport))
        XCTAssertNil(SpatialLiveObjectPresentation.viewportBox(b,transform:nil,recordedViewport:[400,800],viewport:viewport))
        XCTAssertNil(SpatialLiveObjectPresentation.viewportBox(b,transform:identity,recordedViewport:nil,viewport:viewport))
        for invalid: [Float] in [[1,0,1,0,0,0],[1,0,0,1,.nan,0],[1,0]] {
            XCTAssertNil(SpatialLiveObjectPresentation.viewportBox(b,transform:invalid,recordedViewport:[400,800],viewport:viewport))
        }
        XCTAssertNil(SpatialLiveObjectPresentation.viewportBox([0.4,0.2,0.1,0.6],transform:identity,recordedViewport:[400,800],viewport:viewport))
        XCTAssertNil(SpatialLiveObjectPresentation.viewportBox(b,transform:identity,recordedViewport:[400,800],viewport:.zero))
    }

    func testMissingModelFailureRetainsCaptureContextAndTiming() async throws {
        var buffer: CVPixelBuffer?
        XCTAssertEqual(CVPixelBufferCreate(kCFAllocatorDefault,16,12,kCVPixelFormatType_32BGRA,nil,&buffer),kCVReturnSuccess)
        let image = try XCTUnwrap(buffer), id = UUID()
        let began = ProcessInfo.processInfo.systemUptime
        let detector = SpatialLiveObjectDetector(modelURL:nil)
        let input = SpatialLiveObjectInput(image:image,scanID:id,epoch:7,capturedAt:began,
            cameraToWorld:[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1],nativeToViewport:[1,0,0,1,0,0],viewportSize:[400,800])
        let result = await detector.analyze(input)
        XCTAssertNotNil(result.failure)
        XCTAssertTrue(result.detections.isEmpty)
        XCTAssertEqual(result.scanID,id)
        XCTAssertEqual(result.epoch,7)
        XCTAssertEqual(result.capturedAt,began)
        XCTAssertEqual(result.viewportSize,[400,800])
        XCTAssertEqual(result.nativeToViewport,[1,0,0,1,0,0])
        XCTAssertEqual(result.frameWidth,16)
        XCTAssertEqual(result.frameHeight,12)
        XCTAssertTrue(result.elapsedSeconds.isFinite)
        XCTAssertGreaterThanOrEqual(result.elapsedSeconds,0)
        XCTAssertGreaterThanOrEqual(result.completedAt,began)
    }
}
