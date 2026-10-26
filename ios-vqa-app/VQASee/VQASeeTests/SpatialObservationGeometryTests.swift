import XCTest
import simd
@testable import VQASee

final class SpatialObservationGeometryTests: XCTestCase {
    func testPreparedCameraExactlyMatchesFrameGeometryAndRejectsBadCalibration() throws {
        let source=frame(x:0.3,rotation:0.21)
        let prepared=try XCTUnwrap(SpatialObservationGeometry.prepareCamera(frame:source))
        for pixel:[Float] in [[0,0],[0.23,0.71],[0.5,0.5],[1,1]] {
            let direct=try XCTUnwrap(SpatialObservationGeometry.bearing(pixel:pixel,frame:source))
            let cached=try XCTUnwrap(SpatialObservationGeometry.bearing(pixel:pixel,camera:prepared))
            XCTAssertEqual(direct.origin,cached.origin);XCTAssertEqual(direct.direction,cached.direction)
        }
        for p in [SpatialPoint(x:0,y:0,z:-2),SpatialPoint(x:1,y:-0.3,z:-4)] {
            let direct=try XCTUnwrap(SpatialObservationGeometry.project(position:p,frame:source))
            let cached=try XCTUnwrap(SpatialObservationGeometry.project(position:p,camera:prepared))
            XCTAssertEqual(direct.pixel,cached.pixel);XCTAssertEqual(direct.depth,cached.depth)
        }
        var invalid=source;invalid.intrinsics[0]=0
        XCTAssertNil(SpatialObservationGeometry.prepareCamera(frame:invalid))
    }
    private func frame(x: Float = 0, rotation: Float = 0, epoch: Int = 0) -> SpatialKeyframe {
        let r = simd_float4x4(simd_quatf(angle:rotation,axis:SIMD3(0,1,0)))
        var t = r; t.columns.3 = SIMD4(x,0,0,1)
        return SpatialKeyframe(id:UUID().uuidString,time:0,pose:SpatialPose(time:0,position:SpatialPoint(x:x,y:0,z:0),forward:SpatialPoint(x:-t.columns.2.x,y:-t.columns.2.y,z:-t.columns.2.z),epoch:epoch),intrinsics:[800,0,0,0,600,0,430,310,1],width:1000,height:800,imageFile:"frame.jpg",cameraToWorld:[t.columns.0,t.columns.1,t.columns.2,t.columns.3].flatMap{[$0.x,$0.y,$0.z,$0.w]})
    }
    private func pixel(_ p: SIMD3<Float>, in f: SpatialKeyframe) -> [Float] {
        let a = f.cameraToWorld
        let t = simd_float4x4(columns:(SIMD4(a[0],a[1],a[2],a[3]),SIMD4(a[4],a[5],a[6],a[7]),SIMD4(a[8],a[9],a[10],a[11]),SIMD4(a[12],a[13],a[14],a[15])))
        let q = t.inverse * SIMD4(p.x,p.y,p.z,1)
        return [(800*q.x / -q.z+430)/1000,(600 * -q.y / -q.z+310)/800]
    }
    func testAsymmetricIntrinsicsAndRotatedCameraRecoverSurface() throws {
        let a = frame(), b = frame(x:0.5,rotation:0.12), p = SIMD3<Float>(0.2,0.3,-3)
        let match = SpatialImageMatch(sourcePixel:pixel(p,in:a),targetPixel:pixel(p,in:b),cycleErrorPixels:0.2)
        let result = try XCTUnwrap(SpatialObservationGeometry.triangulate(match,source:a,target:b))
        XCTAssertEqual(result.position.x,p.x,accuracy:0.001)
        XCTAssertEqual(result.position.y,p.y,accuracy:0.001)
        XCTAssertEqual(result.position.z,p.z,accuracy:0.001)
        XCTAssertLessThan(result.reprojectionErrorPixels,0.01)
        XCTAssertEqual(result.cycleErrorPixels,0.2)
        let restored = try JSONDecoder().decode(SpatialSurfaceSupport.self, from: JSONEncoder().encode(result))
        XCTAssertEqual(restored, result)
        XCTAssertEqual(restored.cycleErrorPixels,0.2)
    }
    func testRotationHomographyMatchesNativeProjectionAndResizeCentres() throws {
        let source = frame(), target = frame(rotation:0.18)
        let h = try XCTUnwrap(SpatialObservationGeometry.rotationHomography(source:source,target:target))
        let p = SIMD3<Float>(0.2,0.3,-3)
        let a = pixel(p,in:source), b = pixel(p,in:target)
        let native = SIMD2(a[0]*1000,a[1]*800)
        let projected = try XCTUnwrap(SpatialObservationGeometry.mapPixel(native,homography:h))
        XCTAssertEqual(projected.x,b[0]*1000,accuracy:0.001)
        XCTAssertEqual(projected.y,b[1]*800,accuracy:0.001)
        let resize = try XCTUnwrap(SpatialObservationGeometry.resizePixelTransform(nativeWidth:1000,nativeHeight:800,width:384,height:307))
        let work = try XCTUnwrap(SpatialObservationGeometry.mapPixel(native,homography:resize))
        let compensated = try XCTUnwrap(SpatialObservationGeometry.mapPixel(work,homography:resize*h*resize.inverse))
        let restored = try XCTUnwrap(SpatialObservationGeometry.mapPixel(compensated,homography:resize.inverse))
        XCTAssertEqual(restored.x,projected.x,accuracy:0.001)
        XCTAssertEqual(restored.y,projected.y,accuracy:0.001)
        let half = try XCTUnwrap(SpatialObservationGeometry.resizePixelTransform(nativeWidth:1000,nativeHeight:800,width:500,height:400))
        let centre = try XCTUnwrap(SpatialObservationGeometry.mapPixel(SIMD2<Float>(20.5,30.5),homography:half))
        XCTAssertEqual(centre.x,10,accuracy:0.00001)
        XCTAssertEqual(centre.y,15,accuracy:0.00001)
        XCTAssertNil(SpatialObservationGeometry.rotationHomography(source:source,target:frame(epoch:1)))
    }

    func testBearingUsesNativeTopLeftAxes() throws {
        let ray = try XCTUnwrap(SpatialObservationGeometry.bearing(pixel:[0.43,0.3875],frame:frame()))
        XCTAssertEqual(ray.direction.z,-1,accuracy:0.00001)
        XCTAssertEqual(ray.direction.y,0,accuracy:0.00001)
        let lower = try XCTUnwrap(SpatialObservationGeometry.bearing(pixel:[0.43,0.6],frame:frame()))
        XCTAssertLessThan(lower.direction.y,0)
    }
    func testRejectsDegenerateAndInvalidEvidence() {
        let a = frame(), b = frame(x:0.5), p = SIMD3<Float>(0,0,-3)
        var m = SpatialImageMatch(sourcePixel:pixel(p,in:a),targetPixel:pixel(p,in:b),cycleErrorPixels:0)
        XCTAssertNil(SpatialObservationGeometry.triangulate(m,source:a,target:a))
        XCTAssertNil(SpatialObservationGeometry.triangulate(m,source:a,target:frame(x:0.5,epoch:1)))
        m.cycleErrorPixels = 2
        XCTAssertNil(SpatialObservationGeometry.triangulate(m,source:a,target:b))
        var bad = a; bad.intrinsics[0] = 0
        XCTAssertNil(SpatialObservationGeometry.bearing(pixel:[0.5,0.5],frame:bad))
        bad = a; bad.cameraToWorld[0] = 2
        XCTAssertNil(SpatialObservationGeometry.bearing(pixel:[0.5,0.5],frame:bad))
        XCTAssertNil(SpatialObservationGeometry.bearing(pixel:[Float.nan,0.5],frame:a))
    }
    func testTriangulationDiagnosticNamesOriginalGates() {
        let a = frame(), b = frame(x:0.5)
        let m = SpatialImageMatch(sourcePixel:[0.43,0.3875],targetPixel:[0.3,0.3875],cycleErrorPixels:0)
        XCTAssertEqual(SpatialObservationGeometry.diagnoseTriangulation(m,source:a,target:frame(epoch:1)).failure,.sourceEpoch)
        var cycle = m; cycle.cycleErrorPixels = 2
        XCTAssertEqual(SpatialObservationGeometry.diagnoseTriangulation(cycle,source:a,target:b).failure,.invalidCycle)
        let parallel = SpatialImageMatch(sourcePixel:m.sourcePixel,targetPixel:m.sourcePixel,cycleErrorPixels:0)
        XCTAssertEqual(SpatialObservationGeometry.diagnoseTriangulation(parallel,source:a,target:b).failure,.lowParallax)
    }

    func testRejectsBehindAndDistantIntersection() {
        let a = frame(), b = frame(x:0.5)
        for p in [SIMD3<Float>(0,0,3),SIMD3<Float>(0,0,-30)] {
            let m = SpatialImageMatch(sourcePixel:pixel(p,in:a),targetPixel:pixel(p,in:b),cycleErrorPixels:0)
            XCTAssertNil(SpatialObservationGeometry.triangulate(m,source:a,target:b))
        }
    }
}
