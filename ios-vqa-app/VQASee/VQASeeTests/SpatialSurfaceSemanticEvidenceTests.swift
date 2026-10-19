import XCTest
@testable import VQASee

final class SpatialSurfaceSemanticEvidenceTests: XCTestCase {
    private func frame(_ id: String = "a", epoch: Int = 1) -> SpatialKeyframe {
        SpatialKeyframe(id:id,time:1,pose:SpatialPose(time:1,position:SpatialPoint(x:0,y:0,z:0),forward:SpatialPoint(x:0,y:0,z:-1),epoch:epoch),intrinsics:[100,0,0,0,100,0,49.5,49.5,1],width:100,height:100,imageFile:"test.jpg",cameraToWorld:[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1])
    }
    private var wall: SpatialSurface {
        SpatialSurface(id:"wall",kind:"vertical",vertices:[SpatialPoint(x:-2,y:-2,z:-2),SpatialPoint(x:2,y:-2,z:-2),SpatialPoint(x:2,y:2,z:-2),SpatialPoint(x:-2,y:2,z:-2)],epoch:1)
    }
    private func mask(_ id: String = "a", label: Int = 17) -> SpatialSurfaceSemanticMask {
        SpatialSurfaceSemanticMask(frameID:id,epoch:1,modelID:"fixture-only",taxonomy:SpatialSurfaceSemanticTaxonomy(configSHA256:String(repeating:"a",count:64),wallLabelID:17,floorLabelID:29,verified:true),width:24,height:24,labels:Array(repeating:label,count:576),scores:Array(repeating:0.9,count:576))
    }
    func testLargePlaneInteriorProjectsDespiteCornersOutsideImage() {
        let result = SpatialSurfaceSemanticEvidence.evaluate(surface:wall,frame:frame(),mask:mask())
        XCTAssertNil(result.failure)
        XCTAssertEqual(result.wallSupport,576)
        XCTAssertEqual(result.totalSamples,result.wallSupport+result.floorSupport+result.otherOpposition+result.uncertain+result.occluded+result.outsideBoundary+result.unprojectable)
        XCTAssertEqual(SpatialSurfaceSemanticEvidence.resolve(surface:wall,records:[result]),.unknown)
        XCTAssertEqual(SpatialSurfaceSemanticEvidence.resolve(surface:wall,records:[result,result]),.unknown,"Repeating a frame cannot establish multiple views")
        let second = SpatialSurfaceSemanticEvidence.evaluate(surface:wall,frame:frame("b"),mask:mask("b"))
        XCTAssertEqual(SpatialSurfaceSemanticEvidence.resolve(surface:wall,records:[result,second]),.wall)
    }
    func testOppositionIsNotOcclusionOrDeletion() {
        let before = wall
        let other = SpatialSurfaceSemanticEvidence.evaluate(surface:wall,frame:frame(),mask:mask(label:42))
        XCTAssertEqual(other.otherOpposition,576)
        XCTAssertEqual(other.occluded,0)
        XCTAssertEqual(before,wall)
        let wallRecord = SpatialSurfaceSemanticEvidence.evaluate(surface:wall,frame:frame("b"),mask:mask("b"))
        XCTAssertEqual(SpatialSurfaceSemanticEvidence.resolve(surface:wall,records:[other,wallRecord]),.unknown)
    }
    func testExplicitOcclusionAndUncertaintyRetainUnknown() {
        var input = mask(); input.occluded = Array(repeating:true,count:576)
        let occluded = SpatialSurfaceSemanticEvidence.evaluate(surface:wall,frame:frame(),mask:input)
        XCTAssertEqual(occluded.occluded,576)
        XCTAssertEqual(SpatialSurfaceSemanticEvidence.vote(occluded),.unknown)
        input.occluded = nil; input.scores = Array(repeating:0.2,count:576)
        XCTAssertEqual(SpatialSurfaceSemanticEvidence.evaluate(surface:wall,frame:frame(),mask:input).uncertain,576)
    }
    func testSourceTaxonomyAndGeometryCannotMigrate() {
        var input = mask(); input.taxonomy.verified = false
        XCTAssertNotNil(SpatialSurfaceSemanticEvidence.evaluate(surface:wall,frame:frame(),mask:input).failure)
        XCTAssertNotNil(SpatialSurfaceSemanticEvidence.evaluate(surface:wall,frame:frame(epoch:2),mask:mask()).failure)
        let a = SpatialSurfaceSemanticEvidence.evaluate(surface:wall,frame:frame(),mask:mask())
        let b = SpatialSurfaceSemanticEvidence.evaluate(surface:wall,frame:frame("b"),mask:mask("b"))
        var moved = wall; moved.vertices[0].x -= 0.1
        XCTAssertEqual(SpatialSurfaceSemanticEvidence.resolve(surface:moved,records:[a,b]),.unknown)
        var taxonomyChanged = b; taxonomyChanged.configSHA256 = String(repeating:"b",count:64)
        XCTAssertEqual(SpatialSurfaceSemanticEvidence.resolve(surface:wall,records:[a,taxonomyChanged]),.unknown)
    }
    func testNearestKnownPlaneOccludesRearWithoutDeletingIt() {
        var front = wall; front.id = "front"
        front.vertices = front.vertices.map { SpatialPoint(x:$0.x,y:$0.y,z:-1) }
        let results = SpatialSurfaceSemanticEvidence.evaluateAll(surfaces:[front,wall],frame:frame(),mask:mask())
        XCTAssertEqual(results.count,2)
        XCTAssertEqual(results[0].wallSupport,576)
        XCTAssertEqual(results[1].wallSupport,0)
        XCTAssertEqual(results[1].occluded,576)
        XCTAssertEqual(results[1].surfaceVertices,wall.vertices)
        XCTAssertEqual(SpatialSurfaceSemanticEvidence.vote(results[1]),.unknown)
        var moved = front
        moved.vertices = moved.vertices.map { SpatialPoint(x:$0.x+10,y:$0.y,z:$0.z) }
        let changed = SpatialSurfaceSemanticEvidence.evaluateAll(surfaces:[moved,wall],frame:frame(),mask:mask())
        XCTAssertEqual(changed[1].wallSupport,576,"Changed occluding geometry must be recomputed")
    }
    func testNearbyCoplanarDepthDoesNotOcclude() {
        var nearby = wall; nearby.id = "nearby"
        nearby.vertices = nearby.vertices.map { SpatialPoint(x:$0.x,y:$0.y,z:-1.99) }
        let results = SpatialSurfaceSemanticEvidence.evaluateAll(surfaces:[nearby,wall],frame:frame(),mask:mask())
        XCTAssertEqual(results.map(\.occluded),[0,0])
        XCTAssertEqual(results.map(\.wallSupport),[576,576])
    }
    func testBatchSingleSurfaceMatchesPublicSingleAPI() {
        let single = SpatialSurfaceSemanticEvidence.evaluate(surface:wall,frame:frame(),mask:mask())
        var batch = SpatialSurfaceSemanticEvidence.evaluateAll(surfaces:[wall],frame:frame(),mask:mask())[0]
        batch.elapsedSeconds = single.elapsedSeconds
        XCTAssertEqual(batch,single)
        XCTAssertTrue(SpatialSurfaceSemanticEvidence.evaluateAll(surfaces:[],frame:frame(),mask:mask()).isEmpty)
    }
    func testBehindCameraHasNoSemanticSupport() {
        var behind = wall
        behind.vertices = behind.vertices.map { SpatialPoint(x:$0.x,y:$0.y,z:2) }
        let result = SpatialSurfaceSemanticEvidence.evaluate(surface:behind,frame:frame(),mask:mask())
        XCTAssertEqual(result.wallSupport,0)
        XCTAssertEqual(result.unprojectable,576)
    }
}
