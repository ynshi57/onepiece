import XCTest
@testable import VQASee

final class SpatialStructureFloorRegionBuilderTests:XCTestCase {
    private let floor=SpatialSurface(id:"floor",kind:"horizontal",vertices:[SpatialPoint(x:-1,y:0,z:-1),SpatialPoint(x:12,y:0,z:-1),SpatialPoint(x:12,y:0,z:12),SpatialPoint(x:-1,y:0,z:12)])
    private func point(_ x:Float,_ z:Float)->SpatialPoint {.init(x:x,y:0,z:z)}
    private func edges(_ ring:[SpatialPoint],prefix:String="wall")->[SpatialStructureMap.WallSegment] {
        ring.indices.map {i in .init(id:"\(prefix)-\(i)",verticalTrackID:"track-\(prefix)-\(i)",start:ring[i],end:ring[(i+1)%ring.count],startProvenance:.candidate,endProvenance:.candidate,lengthMeters:SpatialScanGeometry.distance(ring[i],ring[(i+1)%ring.count]),sourceSurfaceIDs:["surface-\(prefix)-\(i)"],frameIDs:["frame"])}
    }
    func testFollowsExistingShuffledWallEdgesWithoutMakingAHull() {
        let ring=[point(0,0),point(3,0),point(3,1),point(1,1),point(1,3),point(0,3)]
        let e=edges(ring),result=SpatialStructureFloorRegionBuilder.build(wallSegments:[e[3],e[0],e[5],e[2],e[1],e[4]],floorSurfaces:[floor])
        XCTAssertEqual(result.regions.count,1)
        XCTAssertEqual(result.regions[0].areaMeters2,5,accuracy:0.0001)
        XCTAssertEqual(result.regions[0].outerRing.count,6)
        XCTAssertEqual(result.regions[0].provenance,.candidate)
        XCTAssertTrue(SpatialStructureFloorRegionBuilder.validate(region:result.regions[0]))
    }
    func testOpenLAndBranchDoNotBecomeFloor() {
        let e=edges([point(0,0),point(2,0),point(2,2),point(0,2)])
        let open=SpatialStructureFloorRegionBuilder.build(wallSegments:Array(e.prefix(2)),floorSurfaces:[floor])
        XCTAssertTrue(open.regions.isEmpty);XCTAssertEqual(open.gaps.count,2)
        XCTAssertTrue(open.gaps.allSatisfy {$0.start == $0.end && !$0.wallSegmentIDs.isEmpty})
        var branch=e[0];branch.id="branch";branch.end=point(-1,0)
        let branched=SpatialStructureFloorRegionBuilder.build(wallSegments:e+[branch],floorSurfaces:[floor])
        XCTAssertTrue(branched.regions.isEmpty);XCTAssertEqual(branched.rejections["branchedWallGraph"],1)
    }
    func testSelfIntersectionRejectedAndEndpointToleranceNeverExpands() {
        let bow=SpatialStructureFloorRegionBuilder.build(wallSegments:edges([point(0,0),point(2,2),point(0,2),point(2,0)]),floorSurfaces:[floor])
        XCTAssertTrue(bow.regions.isEmpty)
        var e=edges([point(0,0),point(2,0),point(2,2),point(0,2)])
        e[0].start=point(0.06,0)
        let close=SpatialStructureFloorRegionBuilder.build(wallSegments:e,floorSurfaces:[floor])
        XCTAssertEqual(close.regions.count,1);XCTAssertEqual(close.regions[0].maximumEndpointJoinMeters,0.08)
        e[0].start=point(0.09,0)
        XCTAssertTrue(SpatialStructureFloorRegionBuilder.build(wallSegments:e,floorSurfaces:[floor]).regions.isEmpty)
    }
    func testNestedClosedHoleAndDisconnectedIslandsAreExplicit() {
        let outer=edges([point(0,0),point(10,0),point(10,10),point(0,10)])
        let hole=edges([point(2,2),point(4,2),point(4,4),point(2,4)],prefix:"hole")
        let result=SpatialStructureFloorRegionBuilder.build(wallSegments:outer+hole,floorSurfaces:[floor],floorTrackID:"floor-track")
        XCTAssertEqual(result.regions.count,1);XCTAssertEqual(result.regions[0].holes.count,1)
        XCTAssertEqual(result.regions[0].areaMeters2,96,accuracy:0.0001)
        XCTAssertEqual(result.regions[0].wallSegmentIDs.count,8)
        var invalid=result.regions[0];invalid.holes=[invalid.outerRing]
        XCTAssertFalse(SpatialStructureFloorRegionBuilder.validate(region:invalid))
    }
    func testInvalidPlaneAndOffPlaneEndpointsRejected() {
        var elevated=edges([point(0,0),point(2,0),point(2,2),point(0,2)])
        elevated[0].start.y=1
        XCTAssertTrue(SpatialStructureFloorRegionBuilder.build(wallSegments:elevated,floorSurfaces:[floor]).regions.isEmpty)
        var vertical=floor;vertical.kind="vertical"
        XCTAssertTrue(SpatialStructureFloorRegionBuilder.build(wallSegments:edges([point(0,0),point(2,0),point(0,2)]),floorSurfaces:[vertical]).regions.isEmpty)
    }
    func testOldMapAndReceiptJSONWithoutFloorRegionsDecodeEmpty() throws {
        let map=SpatialStructureMap(scanID:UUID(),epoch:0,revision:1,poseRevision:0,state:.incomplete)
        var object=try XCTUnwrap(JSONSerialization.jsonObject(with:JSONEncoder().encode(map)) as? [String:Any]);object.removeValue(forKey:"floorRegions")
        XCTAssertTrue(try JSONDecoder().decode(SpatialStructureMap.self,from:JSONSerialization.data(withJSONObject:object)).floorRegions.isEmpty)
        let receipt=SpatialStructureP0Receipt(scanID:map.scanID,epoch:0,structureRevision:1,keyframeIDs:[],corners:[],wallSegments:[],builderMessage:"incomplete")
        var old=try XCTUnwrap(JSONSerialization.jsonObject(with:JSONEncoder().encode(receipt)) as? [String:Any]);old.removeValue(forKey:"floorRegions")
        XCTAssertTrue(try JSONDecoder().decode(SpatialStructureP0Receipt.self,from:JSONSerialization.data(withJSONObject:old)).floorRegions.isEmpty)
    }
    func testNearFloorSourceEndpointsProjectOntoOnePlane() {
        var e=edges([point(0,0),point(2,0),point(2,2),point(0,2)])
        for i in e.indices {e[i].start.y=0.06;e[i].end.y = -0.06}
        let result=SpatialStructureFloorRegionBuilder.build(wallSegments:e,floorSurfaces:[floor])
        XCTAssertEqual(result.regions.count,1)
        XCTAssertTrue(result.regions[0].outerRing.allSatisfy {abs($0.y)<0.0001})
        XCTAssertTrue(SpatialStructureFloorRegionBuilder.validate(region:result.regions[0],wallSegments:e,floorSurfaces:[floor]))
    }
    func testShapeAloneCannotAuthorizeMissingOrInventedClosingEdges() {
        let e=edges([point(0,0),point(2,0),point(2,2),point(0,2)])
        let region=SpatialStructureFloorRegionBuilder.build(wallSegments:e,floorSurfaces:[floor]).regions[0]
        XCTAssertTrue(SpatialStructureFloorRegionBuilder.validate(region:region,wallSegments:e,floorSurfaces:[floor]))
        XCTAssertFalse(SpatialStructureFloorRegionBuilder.validate(region:region,wallSegments:Array(e.dropLast()),floorSurfaces:[floor]))
        var forged=region;forged.outerRing[1].x+=0.3
        XCTAssertTrue(SpatialStructureFloorRegionBuilder.validate(region:forged))
        XCTAssertFalse(SpatialStructureFloorRegionBuilder.validate(region:forged,wallSegments:e,floorSurfaces:[floor]))
    }
}
