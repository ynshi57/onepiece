import XCTest
@testable import VQASee

final class SpatialStructureWallLineSupportTests: XCTestCase {
    private func view(id:String, wallPixels:Bool) throws -> SpatialStructureWallLineSupport.View {
        let frame=SpatialKeyframe(id:id,time:0,pose:SpatialPose(time:0,position:.init(x:0,y:1,z:3),forward:.init(x:0,y:0,z:-1),epoch:0),
            intrinsics:[80,0,0,0,80,0,50,50,1],width:100,height:100,imageFile:"\(id).jpg",
            cameraToWorld:[1,0,0,0,0,1,0,0,0,0,1,0,0,1,3,1])
        let camera=try XCTUnwrap(SpatialObservationGeometry.prepareCamera(frame:frame))
        return .init(frameID:id,camera:camera,wallLabel:0,floorLabel:3,width:128,height:128,
                     labels:Data(repeating:wallPixels ? 0:5,count:128*128),confidence:Data(repeating:230,count:128*128))
    }
    func testTwoWallViewsConfirmIntervalAndOtherViewsDoNot() throws {
        let origin=SIMD3<Float>(0,0,0), direction=SIMD3<Float>(0,0,1), up=SIMD3<Float>(0,1,0)
        let wall=SpatialStructureWallLineSupport.intervals(origin:origin,direction:direction,up:up,lo:0,hi:1,
            views:[try view(id:"a",wallPixels:true),try view(id:"b",wallPixels:true)])
        XCTAssertEqual(wall.count,1)
        XCTAssertEqual(wall[0].hi-wall[0].lo,1,accuracy:0.001)
        XCTAssertEqual(Set(wall[0].frameIDs),["a","b"])
        let mixed=SpatialStructureWallLineSupport.intervals(origin:origin,direction:direction,up:up,lo:0,hi:1,
            views:[try view(id:"a",wallPixels:true),try view(id:"b",wallPixels:false)])
        XCTAssertTrue(mixed.isEmpty)
        let other=SpatialStructureWallLineSupport.intervals(origin:origin,direction:direction,up:up,lo:0,hi:1,
            views:[try view(id:"a",wallPixels:false),try view(id:"b",wallPixels:false)])
        XCTAssertTrue(other.isEmpty)
    }
    func testSingleWallViewIsNotEnough() throws {
        let origin=SIMD3<Float>(0,0,0)
        let result=SpatialStructureWallLineSupport.intervals(origin:origin,direction:SIMD3(0,0,1),up:SIMD3(0,1,0),lo:0,hi:1,
            views:[try view(id:"a",wallPixels:true)])
        XCTAssertTrue(result.isEmpty)
    }
}
